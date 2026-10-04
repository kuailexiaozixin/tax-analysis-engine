#!/usr/bin/env python3
"""境外辖区专题（authority="overseas"）的离线用例。

这一类（支柱二与全球最低税）和 sta 那一路的分别只有一处：**中国的官方法规库里
没有对应文件**。所以它的失效路径也是独一份的——按 sta 配就是关掉文件类标签前
的 0 条空跑，按 npc 配就是"企业"两个字命中的无关法律顶住【主依据】。两边都
看着像正常出结果。本文件把这条路上每一处接线各自钉住，不联网：

  归类          支柱二/GloBE/BEPS2.0/补足税 归到 overseas；「最低税额」不抢环保税与资源税
  表的内建约束  每个 overseas 项必须带 note、adjacent、search_term，且不配 parent_law
  轮次          第 1 轮落全站层且不带 npc/fgk；补一轮相邻专题；空跑那轮改用专题词
  取数函数      whole_site 那一轮真把 file_only=False 发出去，并给每条带境外提醒
  服务端        聚合与非聚合两条路都换层，且都剔掉 NPC
  聚合层        file_only 落到 search_chinatax，其他四源不受牵连
  评测探针      按 authority 分派：overseas 走全站层，不进法规库也不进 NPC
  表面词表      覆盖新键，且"BEPS 2.0 支柱二"提得出全球最低税而不是反避税
  文档          references/tax_categories.md 里的 overseas 行与代码表一一对应

用法：python tests/test_overseas_topics.py
"""

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tax_aggregator as AGG      # noqa: E402
import tax_answer as AN           # noqa: E402
import tax_search as T            # noqa: E402
import tax_server                 # noqa: E402

KEY = "全球最低税"
QUESTION = "支柱二对跨国企业有什么影响"


def _ok(payload_total=1, items=None):
    return {"keyword": "x", "total": payload_total,
            "results": items if items is not None else [],
            "searched_at": "2026-10-04 00:00:00"}


def test_pillar_two_aliases_route_to_overseas():
    """各种口语说法都要落到 overseas 这一类，落空就等于按原话去检索。"""
    for text in ("支柱二对跨国企业有什么影响", "全球最低税什么时候生效",
                 "GloBE 规则的应税规则是什么", "BEPS2.0 的支柱二讲了什么",
                 "收入纳入规则和低税利润规则怎么配合",
                 "补足税是谁向谁征", "并行方案怎么落地"):
        info = T.resolve_tax_type(text) or {}
        assert info.get("type") == KEY, f"{text[:22]} 归到 {info.get('type')}，应为 {KEY}"
        assert info.get("authority") == "overseas", f"{text[:22]} 的 authority={info.get('authority')}"
    print("  [PASS] 归类：7 种说法均落 overseas")


def test_lowest_rate_questions_are_not_hijacked():
    """「最低税额」是环保税与资源税里的说法，不能被"全球最低税"抢走。

    收别名时裸「最低税」就是因为这一条没收：收了它，"环境保护税最低税额是多少"
    会被路由到支柱二，答出来的全是别国税改新闻。
    """
    for text, want in (("环境保护税的最低税额是多少", "环境保护税"),
                       ("资源税的最低税额标准怎么定", "资源税")):
        info = T.resolve_tax_type(text) or {}
        assert info.get("type") == want, f"{text} 归到 {info.get('type')}，应为 {want}"
    assert "最低税" not in T.TAX_TYPE_KEYWORDS[KEY]["aliases"], "裸「最低税」又被收回去了"
    assert "IIR" not in T.TAX_TYPE_KEYWORDS[KEY]["aliases"], \
        "裸缩写「IIR」又被收回去了：它在与非税收无关的文本里也成串出现"
    print("  [PASS] 边界：「最低税额」两类题没被抢，裸词没收进别名表")


def test_overseas_entries_carry_note_and_adjacent():
    """表的内建约束：这一类的每一项必须自带限制句与境内相邻专题。

    缺 note 会让路由与提示分手——界面取回境外动态却一个字不提它不是境内依据；
    缺 adjacent（或它指向的项没有检索词）就静默少一轮，答案里"境内怎么缴"那段
    凭空没有，读的人分不清是没查还是查不到。
    """
    overseas = {k: v for k, v in T.TAX_TYPE_KEYWORDS.items()
                if v.get("authority") == "overseas"}
    assert overseas, "表里没有 overseas 项，本文件全部用例就失去对象"
    for key, info in overseas.items():
        assert info.get("note"), f"{key}: 缺 note"
        assert info.get("search_term"), f"{key}: 缺 search_term"
        assert info.get("parent_law") is None, f"{key}: 这一类没有本体法，不许配 parent_law"
        adj = info.get("adjacent") or ""
        assert T.TAX_TYPE_KEYWORDS.get(adj), f"{key}: adjacent 指向不存在的专题「{adj}」"
        assert T.adjacent_search_term(info), f"{key}: 相邻专题「{adj}」没有检索词"
    print(f"  [PASS] 表约束：{len(overseas)} 项均带 note/adjacent/search_term")


def test_plan_rounds_for_overseas():
    """轮次：第 1 轮换全站层，法规库那一轮改用专题词，末尾补一轮相邻专题。"""
    plan = AN.build_plan(QUESTION)
    rounds = plan["rounds"]
    first = rounds[0]
    assert first["sources"] == ["whole_site", "shui5"], first
    assert "npc" not in first["sources"] and "fgk" not in first["sources"], first
    assert "file_only=False" in first["call"], first["call"]

    fgk_rounds = [r for r in rounds if "fgk" in r["sources"]]
    assert len(fgk_rounds) == 2, [r["sources"] for r in rounds]
    # 境内有无文件那一轮沿用模板轮次，但词要是专题检索词，不是用户原话整句
    check = fgk_rounds[0]
    assert "支柱二" in check["goal"], check["goal"]
    # 相邻专题那一轮由 term_key 指定词，否则会拿支柱二去查境外所得
    adjacent = fgk_rounds[1]
    assert adjacent.get("term_key") == "adjacent_fgk", adjacent
    assert "境外所得" in adjacent["goal"], adjacent["goal"]
    assert plan["overseas_note"], "plan 没带出那句境外限制"
    print("  [PASS] 轮次：全站层打头、专题词查境内、末尾补相邻专题")


def test_search_terms_for_overseas():
    """每源自己的词：全站层与法规库两轮都用专题检索词，相邻轮用相邻专题的词。

    第二道题是必需的：「支柱二对跨国企业有什么影响」命中的别名与专题检索词
    同为"支柱二"，就算法规库那一轮退回用别名，装配出来的词也一样，用例看不出来。
    换成"补足税"提问，别名（补足税）与检索词（支柱二）才分家，两者混用的写法会报红。
    """
    terms = AN.search_terms(QUESTION)
    assert terms["whole_site"] == "支柱二", terms
    assert terms["fgk"] == "支柱二", terms
    assert terms["adjacent_fgk"] == "境外所得", terms

    alt = AN.search_terms("补足税的计算规则是什么")
    assert alt["shui5"] == "补足税", alt
    assert alt["fgk"] == "支柱二", f"法规库那一轮退回了别名，没用专题检索词：{alt}"
    assert alt["whole_site"] == "支柱二", alt

    # 别的类不带这两个键，免得答案里凭空多出两条没人用的词
    other = AN.search_terms("增值税的征税范围有哪些")
    assert other["adjacent_fgk"] == "", other
    print("  [PASS] 检索词装配：whole_site/fgk/adjacent_fgk 各就各位")


def test_whole_site_fetcher_opens_the_label_layer():
    """取数函数必须真把 file_only=False 发出去，并给每条带境外提醒。

    这一条盯的是"接了线但没换参数"：路由改成 whole_site、函数里仍按文件类标签
    发请求，取回的还是 0 条，界面上看是"这一类确实没东西"。
    """
    item = {"title": "丹麦提交支柱二法案", "url": "http://www.chinatax.gov.cn/x.html"}
    with mock.patch.object(AN.W, "search_chinatax", return_value=_ok(1, [dict(item)])) as m:
        rows, err = AN._FETCHERS["whole_site"]("支柱二", 8)
    assert err == "", err
    assert m.call_args.kwargs["file_only"] is False, m.call_args
    assert rows[0]["_reliability_note"] and "境外辖区" in rows[0]["_reliability_note"], rows
    # 两种"没有"各留各的形：接口报失败要原样带出去，干净的 0 条不许被编成失败
    with mock.patch.object(AN.W, "search_chinatax", return_value=_ok(0, [])):
        assert AN._FETCHERS["whole_site"]("支柱二", 8)[1] == "", "干净的空轮被编成了取数失败"
    with mock.patch.object(AN.W, "search_chinatax",
                           return_value={"keyword": "x", "total": 0, "results": [],
                                         "_error": "响应不是 JSON"}):
        assert "响应不是 JSON" in AN._FETCHERS["whole_site"]("支柱二", 8)[1]
    print("  [PASS] whole_site 取数：file_only=False 发出，每条带境外提醒")


def test_server_aggregated_route_switches_layer_and_drops_npc():
    """界面默认的多源聚合也要换层：不接这一路，支柱二在默认视图里还是 0 条。"""
    with mock.patch.object(tax_server, "resolve_tax_type",
                           return_value=dict(T.TAX_TYPE_KEYWORDS[KEY], type=KEY)), \
         mock.patch.object(tax_server, "aggregate_search", return_value=_ok(3)) as m:
        r = tax_server.app.test_client().post(
            "/api/search", json={"keyword": QUESTION, "source": "aggregated"})
    assert r.status_code == 200, r.data
    kwargs = m.call_args.kwargs
    assert kwargs["file_only"] is False, kwargs
    assert "npc" not in kwargs["sources"], kwargs["sources"]
    assert m.call_args[0][0] == "支柱二", m.call_args
    routed = r.get_json()["result"]["_routed"]
    assert "境外辖区" in routed and "全站层" in routed, routed


def test_server_single_source_routes_reopen_the_layer():
    """数据源点名税务总局／法规库／默认(NPC) 三种点法都要换到全站层。

    只接默认那一路是不够的：界面上的数据源是用户点的，点到税务总局时若还按
    文件类标签检索，取回的还是空清单——空清单在这一类上是"筛错了层"，不是"没这东西"。
    """
    for src in ("chinatax", "fgk", "npc"):
        with mock.patch.object(tax_server, "resolve_tax_type",
                               return_value=dict(T.TAX_TYPE_KEYWORDS[KEY], type=KEY)), \
             mock.patch.object(tax_server, "search_chinatax", return_value=_ok(3)) as m, \
             mock.patch.object(tax_server, "search_fgk", return_value=_ok(3)) as f, \
             mock.patch.object(tax_server, "search_tax", return_value=_ok(3)) as n:
            r = tax_server.app.test_client().post(
                "/api/search", json={"keyword": QUESTION, "source": src})
        assert r.status_code == 200, (src, r.data)
        assert m.call_args.kwargs["file_only"] is False, (src, m.call_args)
        assert m.call_args[0][0] == "支柱二", (src, m.call_args)
        assert not f.called and not n.called, (src, "换层后还打了文件类标签那一层")
        payload = r.get_json()
        assert payload["authority"] == "overseas", payload["authority"]
        assert "境外辖区" in payload["overseas_note"], payload["overseas_note"]
        assert "全站层" in payload["result"]["_routed"], payload["result"]["_routed"]
    print("  [PASS] 服务端：三种数据源点法均换到全站层，且带出那句境外限制")


def test_aggregator_forwards_file_only_to_chinatax_only():
    """file_only 只落在税务总局那一路，其余四源不许被它牵连。"""
    for value, want in ((None, True), (False, False)):
        calls = {}

        def rec(name, fn_kw):
            def inner(*a, **kw):
                calls[name] = kw
                return _ok(0, [])
            return inner

        with mock.patch.object(AGG, "search_chinatax", rec("chinatax", None)), \
             mock.patch.object(AGG, "search_tax", rec("npc", None)):
            kwargs = {"sources": ["chinatax", "npc"]}
            if value is not None:
                kwargs["file_only"] = value
            AGG.aggregate_search("支柱二", **kwargs)
        assert calls["chinatax"]["file_only"] is want, calls
        assert "file_only" not in calls["npc"], calls["npc"]
    print("  [PASS] 聚合层：file_only 只发给 search_chinatax，默认仍是文件类标签")


def test_eval_probe_dispatches_by_authority():
    """探针跟着路由走：overseas 的键送全站层，不许按法规库或 NPC 判命中。

    照 npc 判会得到"这一类检索不到依据"，把设计上的没有写成取数缺陷。
    """
    import eval_retrieval as ER

    sent = []
    with mock.patch.object(ER.W, "search_chinatax",
                           side_effect=lambda *a, **kw: sent.append(("chinatax", kw)) or _ok(
                               2, [{"title": "经合组织发布支柱二并行方案"}])), \
         mock.patch.object(ER.FGK, "search_fgk",
                           side_effect=lambda *a, **kw: sent.append(("fgk", kw)) or _ok(0, [])), \
         mock.patch.object(ER.T, "search_tax",
                           side_effect=lambda *a, **kw: sent.append(("npc", kw)) or _ok(0, [])):
        c = ER.probe(KEY, topn=3, pause=0)
    assert [s[0] for s in sent] == ["chinatax"], sent
    assert sent[0][1]["file_only"] is False, sent
    assert c["rank"] == 0 and c["authority"] == "overseas", c


def test_surface_forms_cover_the_new_key_and_shadow_correctly():
    """表面词表要有这个键，且笼统的"BEPS"不许先把题面吃掉。

    extract_hints 命中即遮蔽：「BEPS」住在"反避税"那一行。若把全球最低税排在它
    后面，"BEPS 2.0 支柱二"这类题就只提得出反避税，探针也就打不到全站层那一层。
    """
    import eval_retrieval as ER

    keys = [k for k, _ in ER.SURFACE_FORMS]
    assert KEY in keys, keys
    missing = set(T.TAX_TYPE_KEYWORDS) - set(keys)
    assert not missing, f"SURFACE_FORMS 漏了：{sorted(missing)}"
    assert ER.extract_hints("BEPS 2.0 支柱二的补足税怎么征") == [KEY], ER.extract_hints(
        "BEPS 2.0 支柱二的补足税怎么征")
    # 反避税那一路照旧提得出，不能被新键遮掉
    assert "反避税" in ER.extract_hints("BEPS 行动计划的税基侵蚀措施有哪些"), \
        ER.extract_hints("BEPS 行动计划的税基侵蚀措施有哪些")
    print("  [PASS] 表面词表：新键在册，BEPS 遮蔽顺序不改判反避税")


def test_note_reaches_the_answer_skeleton():
    """和 accounting_gap、legislative_note 同一条通道：判型认出来的限制要到答案骨架。"""
    plan = AN.build_plan(QUESTION)
    a = AN.compose(plan)
    assert "不构成中国的征税依据" in a["overseas_note"], a["overseas_note"]
    plain = AN.compose(AN.build_plan("小规模纳税人季度销售额30万元是否免征增值税"))
    assert plain["overseas_note"] == "", plain
    print("  [PASS] overseas_note：build_plan → compose 一路带到答案骨架")


def test_categories_doc_lists_every_overseas_topic():
    """文档与表一一对应：表里加了项、文档没写，界面就有查不到出处的一路。"""
    text = (ROOT / "references" / "tax_categories.md").read_text(encoding="utf-8")
    for key, info in T.TAX_TYPE_KEYWORDS.items():
        if info.get("authority") != "overseas":
            continue
        assert f"| {key} |" in text, f"文档缺 {key} 这一行"
        assert info["search_term"] in text, f"文档里 {key} 的检索词与表不一致"
        assert info["adjacent"] in text, f"文档没写 {key} 的境内相邻专题"
    print("  [PASS] 文档：tax_categories.md 的 overseas 行与表一致")


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  [OK] {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {e}")
    print("=" * 50)
    print(f"结果：{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
