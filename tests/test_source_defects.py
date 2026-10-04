#!/usr/bin/env python3
"""⑩「源缺陷」六条的离线用例。不联网、不开浏览器。

这六条是人肉规则——程序解决不了、用的时候要留神：

  1. NPC 有限流，检索串行跑；评测时不要并行别的 NPC 检索
  2. 360 被封时地方口径与税屋这一层是空的，要在答案里明说、不要拿别的源顶替
  3. fgk 翻得越深相关性越差，深页条目必须回 L1 核对
  4. 公众号依赖会话，并发加压会触发反爬
  5. `_reliability: medium` 说的是这条的召回方式，要落成一句具体提醒
  6. `_reliability: low` 同样只提醒核对，不折算成分数、不从挑选里剔除

"靠人记住"等于没有约束：第 5、6 条若只在输出里印一行、定级层不读它，标了 low
的条目照样能被挑成主依据、还打出"可作依据引用（法律）"。六条
都落进代码，这里逐条钉住，并附带自检（确认规则不是永远绿的）。
"""

import ast
import contextlib
import io
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_detail as DT      # noqa: E402
import tax_answer as ANS     # noqa: E402
import tax_evidence as E     # noqa: E402
import tax_fgk as FGK        # noqa: E402
import tax_formatter as FMT  # noqa: E402
import tax_http              # noqa: E402
import tax_search as T       # noqa: E402
import tax_wechat as W       # noqa: E402
import tax_aggregator as AGG  # noqa: E402
import tax_web_search as WS   # noqa: E402
from tax_web_search import FGK_MARKER  # noqa: E402

HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _fgk_hit(n: int, page_tag: str) -> list:
    return [{
        "title": f"财税文件{page_tag}-{i}",
        "url": f"https://fgk.chinatax.gov.cn/{FGK_MARKER}/{page_tag}{i}.htm",
        "date": "2025-01-0%d" % (i + 1),
        "document_number": f"财税〔2025〕{page_tag}{i}号",
        "publisher": "财政部",
        "snippet": "……",
    } for i in range(n)]


# ── ③ fgk 深页条目必须带页码与可靠性标记 ───────────────────────────────────
class TestFgkDeepPageMarker(unittest.TestCase):
    """翻得越深越松，深页条目只可用于定位。这件事不落进代码就靠人记页码。"""

    def _scan(self, pages):
        """pages: {页码: 该页法规库条目数}"""
        def fake_search(keyword, page=1, size=10, filters=None, **opts):
            n = pages.get(page, 0)
            return {"total": 99, "results": _fgk_hit(n, "p%d" % page)}

        with mock.patch.object(FGK, "search_chinatax", side_effect=fake_search):
            return FGK._scan_list("测试词", size=99, max_pages=max(pages),
                                  adaptive=False)

    def test_shallow_page_items_carry_page_and_no_marker(self):
        out = self._scan({1: 2})
        self.assertEqual(2, out["total"])
        for it in out["results"]:
            self.assertEqual(1, it["page"], "每条都要能看出取自第几页")
            self.assertNotIn("_reliability", it, "第 1 页是浅页，不该被降级")

    def test_deep_page_items_are_marked_locate_only(self):
        out = self._scan({1: 1, 2: 1})
        deep = [it for it in out["results"] if it["page"] == 2]
        self.assertEqual(1, len(deep), "第 2 页那条应该被收进结果")
        self.assertEqual("medium", deep[0]["_reliability"])
        self.assertIn("第 2 页", deep[0]["_reliability_note"])
        self.assertIn("核对上位法", deep[0]["_reliability_note"])

    def test_marker_threshold_is_configurable(self):
        """阈值是常量，不是散在代码里的魔法数。"""
        old = FGK.FGK_SHALLOW_PAGES
        try:
            FGK.FGK_SHALLOW_PAGES = 2
            out = self._scan({1: 1, 2: 1})
            self.assertTrue(all("_reliability" not in it for it in out["results"]),
                            "阈值放到 2 之后，前两页都不该被降级")
        finally:
            FGK.FGK_SHALLOW_PAGES = old


# ── ② 缺了哪一层由程序说，不靠人推断 ───────────────────────────────────────
class TestAggregatorGaps(unittest.TestCase):
    """源级失败要翻译成"缺了哪一层、能不能拿别的源顶"。"""

    def _agg(self, *, so360=None, shui5=None, wechat=None):
        ok = lambda n: {"total": n, "results": [{"title": "财税〔2025〕1号",
                                                 "url": "https://x.test/a"}] if n else []}
        with mock.patch.object(AGG, "search_tax", return_value=ok(1)), \
             mock.patch.object(AGG, "search_chinatax", return_value=ok(1)), \
             mock.patch.object(AGG, "so360_search",
                               return_value=so360 if so360 is not None else ok(1)), \
             mock.patch.object(AGG, "search_shui5",
                               return_value=shui5 if shui5 is not None else ok(0)), \
             mock.patch.object(AGG, "search_wechat",
                               return_value=wechat if wechat is not None else ok(0)):
            return AGG.aggregate_search("测试词", size=5)

    def test_no_gap_when_nothing_failed(self):
        r = self._agg()
        self.assertEqual([], r["gaps"])
        self.assertEqual("", r["degraded_note"])

    def test_blocked_source_yields_a_gap_with_impact_and_no_substitute(self):
        r = self._agg(so360={"_error": "360 被拦截（访问异常出错）", "results": []})
        gaps = {g["source"]: g for g in r["gaps"]}
        self.assertIn("so360", gaps)
        self.assertEqual("360 被拦截（访问异常出错）", gaps["so360"]["reason"])
        self.assertIn("地方口径", gaps["so360"]["impact"])
        self.assertTrue(gaps["so360"]["do_not_substitute"])
        # 断这句话的两个要点：不许拿别的源顶替 + 必须说明缺的是哪一层。
        # 别截半句去匹配——"不要用其他源顶替"在实现文案里并不连续
        # （实现写的是"不要用其他源的条目顶替缺失层"），断半句是形式审查。
        self.assertIn("不要用其他源的条目顶替", r["degraded_note"])
        self.assertIn("要明说缺的是哪一层", r["degraded_note"])

    def test_shui5_empty_is_attributed_to_so360_not_to_shui5(self):
        """税屋链接靠 360 的 site:shui5.cn 检索取——360 挂了，税屋的空是连带的。"""
        r = self._agg(so360={"_error": "360 被拦截", "results": []})
        shui5 = [g for g in r["gaps"] if g["source"] == "shui5"]
        self.assertEqual(1, len(shui5), "税屋该被单独标一条，说明它是被连带空掉的")
        self.assertEqual("so360", shui5[0]["blocked_by"])
        self.assertIn("不代表税屋没有内容", shui5[0]["impact"])

    def test_shui5_own_failure_is_not_attributed_to_so360(self):
        r = self._agg(shui5={"_error": "WAF 挑战未通过", "results": []})
        shui5 = [g for g in r["gaps"] if g["source"] == "shui5"]
        self.assertEqual(1, len(shui5))
        self.assertNotIn("blocked_by", shui5[0], "税屋自己报错时不该甩给 360")
        self.assertIn("WAF", shui5[0]["reason"])

    def test_note_counts_hits_and_names_the_layer(self):
        r = self._agg(so360={"_error": "360 被拦截", "results": []})
        self.assertIn("2/5", r["degraded_note"], r["degraded_note"])
        self.assertIn("税屋", r["degraded_note"])


# ── ②' 网页端要把缺口显示出来，不能显示成普通的 0 条 ──────────────────────
class TestFrontendShowsDegradedNote(unittest.TestCase):
    def test_render_results_shows_degraded_note(self):
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        self.assertIn("degraded_note", body)
        self.assertIn("esc(result.degraded_note)", body)

    def test_empty_state_keeps_the_causes_apart(self):
        """空清单有四种成因，页面要分流、原样显示，不得全塌成"未找到"。

        对应的缺陷：翻过末页、维度拼窄、接口没给清单是"没取到/取法不对"，
        取数失败（_fetch_failed）更是服务侧故障。都渲染成"未找到相关政策"就
        等于页面替用户说了"库里没有这份文件"——用户会换个词重搜，而真相是
        要么稍后再试、要么把维度放宽。分流顺序按"越靠近真相越先看"，取数失败
        排最前，不能和其余三种共用未找到那一句。
        """
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        # 取数失败走独立分支：有专门的标题，且这一句排在"未找到"之前
        self.assertIn("result._fetch_failed", body)
        self.assertIn("取数失败", body)
        self.assertLess(body.index("result._fetch_failed"), body.index("未找到相关政策"),
                        "取数失败被并进了未找到分支")
        # 其余三种成因仍逐字透出：filter_note / empty_reason / error 都进 why 链
        self.assertIn("result._filter_note", body)
        self.assertIn("result._empty_reason", body)
        # 透出的句子来自外部检索结果，必须走 esc() 才拼进 HTML
        self.assertIn("esc(why)", body)

    def test_render_results_shows_routing_note(self):
        """换源提示和"没归类"提示都由后端给整句，页面只照抄。

        对应的缺陷：归不出税种时后端拿原话做字面标题检索，实测回 687 条无关
        法条。页面不显示这句，一摞无关法条就看起来像按本题找出来的依据。
        """
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        self.assertIn("_routed", body)
        self.assertIn("esc(result._routed)", body)


# ── 录入项带来的文号/时效/效力级别，页面不能继续显示"时效未标注" ──────────
class TestFrontendShowsSourceMetadata(unittest.TestCase):
    """NPC 条目给数字状态码，总局法规库条目给的是录入项文本。

    缺陷形态很具体：接口已经回 `xxgk_aging="全文有效"`，页面只读
    `status_code`，于是这份现行有效的文件在界面上显示成"⚪ 时效未标注"，
    和真的没标的条目长得一模一样。
    """

    OLD_BADGE = ("function statusBadge(item){\n"
                 "  const cls = statusMap[item.status_code] || 's-unknown';\n"
                 "  return `<span class=\"status-badge ${cls}\">"
                 "${statusNames[item.status_code] || '时效未标注'}</span>`;\n}\n")

    @staticmethod
    def _badge():
        return HTML[HTML.index("function statusBadge(item){"):
                    HTML.index("items.forEach((item,i)=>{")]

    @staticmethod
    def _card():
        return HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]

    def test_badge_reads_both_the_code_and_the_text(self):
        b = self._badge()
        self.assertIn("item.status_code", b)
        self.assertIn("item.status", b)
        # 文本要分四类，"全文有效"不能掉进未标注
        for word in ("废止", "尚未生效", "修改", "有效"):
            self.assertIn(word, b, word)

    def test_card_shows_document_number_and_effect_level(self):
        body = self._card()
        self.assertIn("item.document_number", body)
        self.assertIn("item.effect_level", body)
        self.assertIn("statusBadge(item)", body)

    def test_date_falls_back_to_the_source_field(self):
        """单源 chinatax/fgk 的条目用 `date`，聚合后才统一成 `publish_date`。"""
        self.assertIn("item.publish_date||item.date", self._card())

    def test_placeholder_status_is_not_shown_as_a_source_value(self):
        """NPC 缺 status 时程序自己拼"未知(None)"，那是缺省值不是法规写的时效。

        界面要把它换成"时效未标注"，否则读者的判断依据从"来源没给"变成了
        "来源给了一个叫未知的值"。
        """
        self.assertIn("/^未知/", self._badge())
        self.assertIn("'时效未标注'", self._badge())

    def test_rules_can_actually_fail(self):
        """把同一套断言作用在违规写法上，确认它拦得住。"""
        for word in ("废止", "尚未生效", "修改", "有效"):
            self.assertNotIn(word, self.OLD_BADGE, word)
        self.assertNotIn("item.status||", self.OLD_BADGE)
        self.assertNotIn("document_number", self.OLD_BADGE)


# ── ⑤⑥ 定级层读到 `_reliability` 后要给出可核对的提醒 ──────────────────────
class TestReliabilityBecomesAReminder(unittest.TestCase):
    """来源标记要说清"疑在哪一处、要核对什么"，不许再用降权代替核对。

    断言的是提醒，不是否决。"标了 low 就把分清零、整组不许挑主依据"是在限制材料
    的作用——读者只看到一个小标签，既不知道为什么，
    也无从下手。同样的来源信息写成逐条提醒，材料照常参与分层。
    """

    LAW = "中华人民共和国企业所得税法"

    def test_low_carries_a_checkable_reminder(self):
        g = E.grade({"title": self.LAW, "_reliability": "low"})
        self.assertEqual("low", g["reliability"])
        self.assertIn(E.RELIABILITY_CAVEAT["low"], g["caveats"])
        self.assertNotIn("不得作为依据引用", g["citation_hint"])

    def test_medium_reminder_is_the_sources_own_sentence(self):
        """同是 medium，三条来源各说各的原因，提醒必须用来源自己那句。

        `medium` 在本仓库有三个出处：NPC 正文检索按全文分词命中、总局法规库第 2
        页起排序变松、立法过程件不在五个源的收录范围内。定级层若按档位配一句
        固定话（"这一条来自清单靠后的页位"），三处里只有法规库那处对得上，另两处
        给的是假提醒——全文检索和站内检索没有"页位"这件事。

        变异自检：把 `tax_evidence._caveats` 里那句改回
        `RELIABILITY_CAVEAT[rel] + "；" + rel_note`，`assertEqual` 与
        `not in` 两组断言同时报红；把 `RELIABILITY_CAVEAT["medium"]` 改写成带
        某个具体机制的说法，最后一条断言报红。
        """
        fgk = E.grade({"title": self.LAW, "_reliability": "medium",
                       "_reliability_note": FGK.FGK_DEEP_NOTE.format(page=3)},
                      topic="企业所得税")
        npc = E.grade({"title": self.LAW, "_reliability": "medium",
                       "_reliability_note": T.RELIABILITY_NOTES["medium"]},
                      topic="企业所得税")
        self.assertEqual(FGK.FGK_DEEP_NOTE.format(page=3), fgk["caveats"][-1])
        self.assertEqual(T.RELIABILITY_NOTES["medium"], npc["caveats"][-1])
        for g in (fgk, npc):
            self.assertTrue(all(E.RELIABILITY_CAVEAT["medium"] not in c
                                for c in g["caveats"]), g["caveats"])
        self.assertNotIn("第 3 页", "；".join(npc["caveats"]),
                         "全文检索的条目不该被告知自己来自法规库的第 3 页")

    def test_marker_without_a_note_falls_back_to_a_neutral_reminder(self):
        """只有档位、没有原因说明时，兜底句不许断言某个具体机制。"""
        g = E.grade({"title": self.LAW, "_reliability": "medium"})
        self.assertIn(E.RELIABILITY_CAVEAT["medium"], g["caveats"])
        for word in ("页位", "分词", "页"):
            self.assertNotIn(word, E.RELIABILITY_CAVEAT["medium"], word)
            self.assertNotIn(word, E.RELIABILITY_CAVEAT["low"], word)

    def test_unmarked_item_gets_no_reliability_reminder(self):
        g = E.grade({"title": self.LAW, "status": "全文有效"}, topic="企业所得税")
        self.assertEqual("ok", g["reliability"])
        self.assertEqual([], [c for c in g["caveats"]
                              if c in (E.RELIABILITY_CAVEAT["low"],
                                       E.RELIABILITY_CAVEAT["medium"])])
        self.assertEqual("direct", g["role"])

    def test_reliability_does_not_change_the_pick(self):
        """标了 low 的《企业所得税法》不再被整组剔除，也不再因此让位。

        变异自检：把 `_reliability` 塞回 `_order_key`（例如 low 时 tier 取 0），
        这一条与下一条都会报红——那正是本文件不许回来的做法。
        """
        g = E.pick_primary([
            E.grade({"title": self.LAW, "_reliability": "low",
                     "_reliability_note": "全文检索偏题"}, topic="企业所得税"),
            E.grade({"title": "国家税务总局公告2018年第28号"}, topic="企业所得税"),
        ])
        self.assertEqual(self.LAW, g["title"])
        self.assertEqual("low", g["reliability"])
        self.assertIn("全文检索偏题", g["_why"])

    def test_pick_is_by_role_not_by_marker(self):
        """两条在能不能引、角色、时效、层级、主题对应上全相等时，标记不参与排队。

        先断言两条真的并列，否则这条测的是排队规则而不是标记——上一次它就用一份
        未定级的通知去比一份总局公告，角色本来就不同，结论说明不了任何事。

        变异自检：把 `_reliability` 塞进 `_order_key`（low 排后），两次断言里
        必有一次报红。
        """
        a = E.grade({"title": "国家税务总局公告2018年第28号",
                     "_reliability": "low"}, topic="企业重组")
        b = E.grade({"title": "国家税务总局公告2019年第11号"}, topic="企业重组")
        self.assertEqual(E._order_key(a), E._order_key(b),
                         (a["rank"], a["role"], a["validity"], a["on_topic"]))
        self.assertEqual("low", a["reliability"])
        self.assertEqual(a["title"], E.pick_primary([a, b])["title"])
        self.assertEqual(b["title"], E.pick_primary([b, a])["title"])

    def test_every_candidate_keeps_its_own_caveats(self):
        """全组都带标记时照样挑得出来，且落选项在 `_runners_up` 里带着提醒。"""
        best = E.pick_primary([E.grade({"title": self.LAW, "_reliability": "low"},
                                       topic="企业所得税")])
        self.assertEqual(self.LAW, best["title"])
        self.assertIn(E.RELIABILITY_CAVEAT["low"], best["_why"])
        run = E.pick_primary([
            E.grade({"title": self.LAW, "status": "全文有效"}, topic="企业所得税"),
            E.grade({"title": "中华人民共和国增值税暂行条例", "status": "全文有效",
                     "_reliability": "medium"}, topic="企业所得税"),
        ])["_runners_up"]
        self.assertIn(E.RELIABILITY_CAVEAT["medium"], run[0]["caveats"])


# ── ⑤'' 聚合层带下来的标记必须说得出原因 ──────────────────────────────────
class TestAggregatorCarriesTheReminder(unittest.TestCase):
    """整源标记按定义就是"这一窗条目共同的取回方式"，逐条带上不算冤枉。

    它只在源自己的输出里印一次、不带进聚合的话，聚合清单上看不出这些条目来自
    全文检索，定级层与界面都收不到这句提醒。

    变异自检：把 `tax_aggregator` 里 `item["_reliability_note"] = source_note`
    那两行删掉，第一条断言报红；把命令行摘要改回按档位配一句固定话（低档写
    "不得作为权威依据引用"），第二条报红。
    """

    NPC_NOTE = "NPC 正文检索按全文分词命中，可能偏题；请回到标题检索确定条文归属"
    DEEP_NOTE = "取自总局检索第 3 页：深页条目可能只是沾了检索词"

    def _agg(self):
        npc = {"total": 1, "results": [{"title": "中华人民共和国增值税暂行条例",
                                        "url": "http://x/npc"}],
               "_reliability": "medium", "_reliability_note": self.NPC_NOTE}
        chinatax = {"total": 1, "results": [{"title": "国家税务总局公告2025年第3号",
                                             "url": "http://x/ct"}]}
        empty = {"total": 0, "results": []}
        with mock.patch.object(AGG, "search_tax", return_value=npc), \
             mock.patch.object(AGG, "search_chinatax", return_value=chinatax), \
             mock.patch.object(AGG, "so360_search", return_value=empty), \
             mock.patch.object(AGG, "search_shui5", return_value=empty), \
             mock.patch.object(AGG, "search_wechat", return_value=empty):
            return AGG.aggregate_search("增值税", size=5, scope="fulltext")

    def test_source_level_marker_reaches_each_item_with_its_note(self):
        r = self._agg()
        row = [i for i in r["items"] if i["_source"] == "npc"][0]
        self.assertEqual("medium", row["_reliability"])
        self.assertEqual(self.NPC_NOTE, row["_reliability_note"])
        # 没带标记的那一路不能被连坐
        self.assertNotIn("_reliability",
                         [i for i in r["items"] if i["_source"] == "chinatax"][0])

    def test_cli_prints_the_reminder_text_not_a_level_ban(self):
        """命令行摘要印每条自带的提醒原文；按档位配死一句禁令是旧做法。"""
        res = {"searched_at": "2026-10-03 00:00:00",
               "source_summary": {"npc": 1, "chinatax": 1},
               "items": [{"_source": "npc", "title": "A",
                          "_reliability": "medium", "_reliability_note": self.NPC_NOTE},
                         {"_source": "chinatax", "title": "B",
                          "_reliability": "medium", "_reliability_note": self.DEEP_NOTE},
                         {"_source": "npc", "title": "C",
                          "_reliability": "low"}],
               "gaps": [], "errors": {}}
        out = io.StringIO()
        with mock.patch.object(AGG, "aggregate_search", return_value=res), \
             mock.patch.object(sys, "argv", ["tax_aggregator.py", "增值税"]), \
             contextlib.redirect_stdout(out):
            AGG.main()
        text = out.getvalue()
        self.assertIn(self.NPC_NOTE, text)
        self.assertIn(self.DEEP_NOTE, text)
        self.assertIn("未写明存疑在哪一处", text, "只有档位没有说明时要明说缺的是什么")
        for ban in ("不得作为权威依据引用", "只能参考", "不得作为依据引用"):
            self.assertNotIn(ban, text, ban)


# ── ⑤' 显示层也不许把标记吞掉 ──────────────────────────────────────────────
class TestFormatterShowsReliability(unittest.TestCase):
    """标记进了数据、却印不出来，等于没标记。

    `tax_formatter.py --mode single` 必须读响应级的 `_reliability`：把 NPC 全文
    检索（恒带 medium）的结果灌进去，输出里一个字都不提"可能偏题"，读者只看到
    "共 N 条法规"，会当成能引用的清单。多源归并那条路径印了，单源这条不许漏。
    """

    def _render(self, **extra):
        """喂一份 tax_search 真实会返回的最小响应，返回渲染出的 markdown。"""
        resp = {
            "keyword": "研发费用加计扣除", "total": 8833,
            "scope": "fulltext", "search_type": "fuzzy",
            "results": [{"title": "中华人民共和国企业所得税法", "id": "x1",
                         "status_code": 3, "publish_date": "2018-12-29"}],
        }
        resp.update(extra)
        return FMT.format_search_response(resp, intent="policy_lookup")

    def test_medium_marker_reaches_the_markdown(self):
        md = self._render(_reliability="medium",
                          _reliability_note="结果已排序但可能偏题（全文分词匹配）")
        self.assertIn("可靠性 medium", md)
        self.assertIn("可能偏题", md)
        # 警告要出现在条目之前，不能等读者看完清单才看到
        self.assertLess(md.index("可靠性"), md.index("中华人民共和国企业所得税法"))

    def test_local_wording_is_the_fallback(self):
        """数据里没带说明时用本地兜底文案，而不是印出空的破折号。"""
        md = self._render(_reliability="medium")
        self.assertIn("可靠性 medium", md)
        self.assertIn(FMT._RELIABILITY_NOTE, md)

    def test_no_marker_no_warning(self):
        """回归：标题检索不带标记，就不能凭空多出一句警告。"""
        self.assertNotIn("可靠性", self._render())


# ── ④ 搜狗微信要进闸，不能靠"别并发"的提醒 ────────────────────────────────
class _FakeSession:
    """记录并发峰值的假会话，用来验证闸真的把并发压成 1。"""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def get(self, url, **kwargs):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            return object()
        finally:
            with self._lock:
                self.active -= 1


class TestWechatSogouGate(unittest.TestCase):
    def test_sogou_requests_go_through_the_gate(self):
        src = (SCRIPTS_DIR / "tax_wechat.py").read_text(encoding="utf-8")
        for fn in ("def search_wechat", "def _restore_url"):
            body = src[src.index(fn):]
            body = body[:body.index("\ndef ")]
            self.assertIn("_sogou_get", body, "{} 没走闸".format(fn))
            self.assertNotIn("sess.get(", body, "{} 里还有绕开闸的裸 sess.get".format(fn))
        helper = src[src.index("def _sogou_get"):src.index("def search_wechat")]
        self.assertIn("with sogou_gate:", helper)

    def test_gate_is_the_shared_implementation_with_its_own_lock(self):
        self.assertIsInstance(W.sogou_gate, tax_http.SerialGate)
        self.assertIsInstance(T.npc_gate, tax_http.SerialGate)
        self.assertIsInstance(T.npc_gate, T.NpcSerialGate)
        self.assertNotEqual(
            W.sogou_gate.path, T.npc_gate.path,
            "两个站必须各用一把锁，共用一把会让不相关的请求互相排队甚至死等")

    def test_concurrent_callers_are_serialized(self):
        sess = _FakeSession()
        old = W._SOGOU_MIN_INTERVAL
        try:
            W._SOGOU_MIN_INTERVAL = 0.0      # 只验证闸，不验证限速
            threads = [threading.Thread(
                target=W._sogou_get,
                args=(sess, "https://weixin.sogou.com/weixin?type=2&query=x"))
                for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            W._SOGOU_MIN_INTERVAL = old
        self.assertEqual(1, sess.max_active,
                         "并发峰值 %d：闸没起作用" % sess.max_active)

    def test_min_interval_is_enforced(self):
        sess = _FakeSession()
        old_int, old_at = W._SOGOU_MIN_INTERVAL, W._last_sogou_at
        try:
            W._SOGOU_MIN_INTERVAL = 0.4
            W._last_sogou_at = 0.0
            t0 = time.monotonic()
            W._sogou_get(sess, "https://weixin.sogou.com/weixin?type=2&query=x")
            W._sogou_get(sess, "https://weixin.sogou.com/weixin?type=2&query=y")
            dt = time.monotonic() - t0
        finally:
            W._SOGOU_MIN_INTERVAL, W._last_sogou_at = old_int, old_at
        self.assertGreaterEqual(dt, 0.4, "两次请求之间没有按最小间隔让路")


# ── ① NPC 闸要包住下载路径 ────────────────────────────────────────────────
class TestNpcGateCoversDownload(unittest.TestCase):
    def test_get_download_url_uses_the_gated_request(self):
        src = (SCRIPTS_DIR / "tax_detail.py").read_text(encoding="utf-8")
        body = src[src.index("def get_download_url"):src.index("def download_bytes")]
        self.assertIn("_request(", body, "取下载地址没走带闸的 _request")
        self.assertNotIn("tax_http.get(", body, "取下载地址还在闸外裸调")

    def test_npc_gate_is_a_subclass_not_a_copy(self):
        """一份实现两个用户：闸本体在 tax_http，NPC 只是钉了默认值。"""
        self.assertTrue(issubclass(T.NpcSerialGate, tax_http.SerialGate))
        npc_body = (SCRIPTS_DIR / "tax_search.py").read_text(encoding="utf-8")
        npc_body = npc_body[npc_body.index("class NpcSerialGate"):
                            npc_body.index("npc_gate = NpcSerialGate()")]
        self.assertNotIn("msvcrt", npc_body,
                         "NPC 子类里又抄了一遍文件锁实现，说明没共用")

    def test_eval_and_server_both_use_download_bytes(self):
        for rel in ("scripts/tax_server.py", "tests/eval_answer.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("law-search/" + "download/pc", text,
                             "{} 自己拼了下载地址".format(rel))
            self.assertIn("download_bytes", text)


class TestGradingReadsFgkMetadata(unittest.TestCase):
    """法规库清单带来的三个定级口子：解读件、已修改、标题里的书名号。

    翻页基准修正后清单里同时出现了公告原文、公告的解读、以及公告正文援引的
    配套文件（《关于企业重组业务所得税处理有关征管问题的公告》，2026年第13号），
    定级层一照面就把解读按被解读文件的形态判成了规范性文件。
    """

    def test_interpretation_is_not_the_document_it_explains(self):
        t = "国家税务总局关于企业重组业务企业所得税征收管理若干问题的公告的解读"
        self.assertEqual("technical", E.rank_of(t)["rank"])
        # 带 effect_level 也不该被分类规则顶成原文的位阶
        self.assertEqual("technical",
                         E.rank_of(t, category="税务规范性文件")["rank"])

    def test_amended_document_still_in_force(self):
        v = E.judge_validity({"status": "已修改"}, "2026-09-29")
        self.assertEqual("effective", v["validity"])
        self.assertIn("修改后的版本", v["note"])
        # 认不出的状态仍然只能报未标明，不猜
        self.assertEqual("unknown",
                         E.judge_validity({"status": "待定"}, "2026-09-29")["validity"])

    def test_title_with_book_marks_still_ranks(self):
        t = "国家税务总局关于发布《企业重组业务企业所得税管理办法》的公告"
        r = E.rank_of(t)
        self.assertEqual("normative", r["rank"])
        self.assertEqual(55, r["tier"])
        # 判成"未定性"时层级掉到 0、角色掉到待核对线索——那才是这个用例要拦的
        # 回归，所以两条都断言，而不是断言掉了几分
        g = E.grade({"title": t}, topic="企业重组")
        self.assertEqual("normative", g["rank"])
        self.assertEqual(55, g["rank_tier"])
        self.assertEqual("direct", g["role"])
        # 主题词对不上时它仍是有层级的法定文件，落"上位依据与授权"：
        # "待核对线索"只留给层级与主题两头都空着的那类（见 `role_of`）。
        self.assertEqual("superior",
                         E.grade({"title": t}, topic="留抵退税")["role"])
        off = E.grade({"title": t}, topic="留抵退税")
        self.assertIs(False, off["on_topic"])
        self.assertIn("标题与正文里都没有本题的主题词", "；".join(off["caveats"]))


class TestNormativeIsABasisNotReference(unittest.TestCase):
    """总局公告是本题的直接规定，不该被任何一根"参考"线挡在依据之外。

    断言的是角色，不是"分数够不够 50 那条线"——那根线是把能不能引和该不该让位
    乘成一个分数的合成判据，两根轴各判各的才分得开。规范性文件在与本题对得上、
    且没废止时就是"本题的直接规定"，并且挑得主依据，压过层级更高的法律。
    """

    ANN = "国家税务总局关于企业重组业务所得税处理有关征管问题的公告"

    def test_current_announcement_is_a_direct_basis(self):
        g = E.grade({"title": self.ANN, "status": "全文有效"},
                    at="2026-09-29", topic="企业重组")
        self.assertEqual("effective", g["validity"])
        self.assertEqual("direct", g["role"])

    def test_a_higher_tier_law_does_not_steal_the_headline(self):
        """问企业重组的征管口径时，头条要给规定这件事的公告，不给《企业所得税法》。

        变异自检：把 `pick_primary` 的排序键换成单按 `rank_tier` 降序，这一条
        立刻报红——法律 90 压公告 55。旧的可引用性公式就是这个形状。
        """
        g = E.grade_all([
            {"title": "中华人民共和国企业所得税法", "status": "全文有效"},
            {"title": self.ANN, "status": "全文有效"},
        ], at="2026-09-29", topic="企业重组")
        best = E.pick_primary(g)
        self.assertEqual(self.ANN, best["title"])
        self.assertEqual("direct", best["role"])

    def test_validity_problem_becomes_a_reminder_not_a_ban(self):
        """时效那一栏照常是唯一带后果的轴，但后果写成"要核对什么"。"""
        for status, want in (("", "unknown"), ("尚未生效", "pending"),
                             ("全文废止", "repealed")):
            g = E.grade({"title": self.ANN, "status": status},
                        at="2026-09-29", topic="企业重组")
            self.assertEqual(want, g["validity"], status)
            self.assertIn(E.VALIDITY_CAVEAT[want], g["caveats"], status)
        # 已废止的那条角色转成政策沿革，仍在分层里列出，没有被删掉
        self.assertEqual("history",
                         E.grade({"title": self.ANN, "status": "全文废止"},
                                 at="2026-09-29", topic="企业重组")["role"])

    def test_practice_layer_keeps_its_own_role(self):
        """税屋、公众号与官方解读各归"执行口径"，不再被压成不能引用的那堆。

        判的次序是"废止 → 实务形态 → 主题对得上 → 层级未定"：一份层级判不出来
        的稿子只要字面上就在讲本题，它就是"本题的直接规定"，层级未定那件事由提醒
        那几句话说清；层级与主题都落空的才落"待核对线索"。
        """
        cases = (
            ({"title": "关于企业重组业务所得税处理有关征管问题的公告的解读"},
             "technical", "practice"),
            ({"title": "解读：企业重组特殊性税务处理怎么备案"},
             "technical", "practice"),
            ({"title": "六税两费减免的十个易错点", "source": "税屋 (shui5.cn)"},
             "interpretation", "practice"),
            ({"title": "实务问答：留抵退税的口径", "source": "微信公众号 (搜狗微信)"},
             "technical", "practice"),
            ({"title": "上海市税务局关于做好企业重组备案工作的通知"},
             "local_normative", "direct"),
            ({"title": "企业重组有关的几点提示"}, "unknown", "direct"),
            ({"title": "几点提示"}, "unknown", "unmatched"),
        )
        for item, want_rank, want_role in cases:
            g = E.grade(dict(item, status="全文有效"), at="2026-09-29",
                        topic="企业重组")
            self.assertEqual(want_rank, g["rank"], item["title"])
            self.assertEqual(want_role, g["role"], item["title"])
        # 层级未定但主题对得上的那条，提醒里必须写着"层级没判出来"这件事
        g = E.grade({"title": "企业重组有关的几点提示", "status": "全文有效"},
                    at="2026-09-29", topic="企业重组")
        self.assertEqual("标题形态与来源都不足以定级", g["rank_by"])

    def test_pending_without_effective_date_cannot_be_read_as_in_force(self):
        """判 --at 时"没查到施行日期"不等于"日期一定在过去"。

        时点分支若只要没取到 effective_date 就落回 effective，一份标着尚未生效
        的公告会在带 --at 的用法下直接顶成主依据；只有日期确认早于观察时点才
        允许转正。转正那一条另配一句"生效日不晚于观察时点"的常规交代，它不是
        提醒，所以 `qualified` 为假。
        """
        for it in ({"status": "尚未生效"},
                   {"status": "尚未生效", "publish_date": "2026-07-08"},
                   {"status": "未生效", "effective_date": ""}):
            g = E.grade(dict(it, title=self.ANN), at="2026-09-29", topic="企业重组")
            self.assertEqual("pending", g["validity"], it)
            self.assertIn(E.VALIDITY_CAVEAT["pending"], g["caveats"], it)
        g = E.grade({"title": self.ANN, "status": "尚未生效",
                     "effective_date": "2026-01-01"}, at="2026-09-29",
                    topic="企业重组")
        self.assertEqual("effective", g["validity"])
        self.assertNotIn("生效日 2026-01-01 不晚于观察时点 2026-09-29",
                         g["caveats"], "这是判据来源的交代，不该占一条提醒")
        # 不传 --at 时本来就是纯状态判定，pending 不受影响
        self.assertEqual("pending",
                         E.judge_validity({"status": "尚未生效"}, "")["validity"])


class TestConstitutionIsNotTheHeadline(unittest.TestCase):
    """宪法位阶最高，但不能顶当主依据——位阶×时效的乘法会给它 100×1.0 的全场最高分。"""

    def test_flagged_and_yielded_to_the_law(self):
        c = E.grade({"title": "中华人民共和国宪法", "category": "宪法"},
                    at="2026-09-29", topic="税收法定")
        self.assertEqual(100, c["rank_tier"])
        self.assertTrue(c["not_directly_quotable"])
        self.assertIn(E.CONSTITUTION_NOTE, c["caveats"])
        self.assertEqual(E.CONSTITUTION_NOTE, c["citation_hint"].split("；")[1])
        mixed = [c, E.grade({"title": "中华人民共和国税收征收管理法",
                             "status": "全文有效"}, at="2026-09-29",
                            topic="税收法定")]
        self.assertEqual("中华人民共和国税收征收管理法",
                         E.pick_primary(mixed)["title"])

    def test_alone_it_is_picked_but_told_to_go_down_to_law(self):
        """一组里只有宪法时仍要挑它，同时说明它对税务机关不构成征税依据。

        变异自检：若改成"宪法一律不挑"，这一条报红——那时候答案会连一条
        规定税收法定原则的文件都提不出来，而题问的正是这个原则。
        """
        only = [E.grade({"title": "中华人民共和国宪法", "category": "宪法"},
                        at="2026-09-29", topic="税收法定")]
        best = E.pick_primary(only)
        self.assertEqual("中华人民共和国宪法", best["title"])
        self.assertIn("不构成征税依据", best["_why"])


class TestNoSynthesisedCitationScore(unittest.TestCase):
    """不设可引用性合成分数：留一个字段名就会被下游重新拿去排队。"""

    def test_grade_exposes_the_axes_not_a_product(self):
        g = E.grade({"title": "中华人民共和国企业所得税法", "status": "全文有效"},
                    at="2026-09-29", topic="企业所得税")
        for key in ("rank_tier", "validity", "on_topic", "role", "caveats",
                    "not_directly_quotable"):
            self.assertIn(key, g)
        self.assertNotIn("score", g)
        self.assertNotIn("score", E.rank_of("中华人民共和国宪法"))

    def test_removed_symbols_stay_removed(self):
        for name in ("authority_score", "PRIMARY_THRESHOLD", "RELIABILITY_BLOCK"):
            self.assertFalse(hasattr(E, name), name)

    def test_no_consumer_reads_a_score(self):
        for rel in ("scripts/tax_answer.py", "scripts/tax_server.py",
                    "tests/eval_answer.py", "frontend/index.html"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn('get("score"', text, rel)
            self.assertNotIn("PRIMARY_THRESHOLD", text, rel)

    def test_frontend_prints_the_reminder_not_a_downweight_badge(self):
        """界面上"⚪ 仅参考"那个角标换成了一句看得见的提醒。

        角标只给一个记号：读者既不知道疑在哪一处，也无从核对，只能整条忽略。
        现在卡面直接印提醒，句子取自条目自带的 `_reliability_note`。界面不再存
        第二份文案：同一档位在不同来源里原因不同，按档位配死一句就会有一句是假的
        （判据与定级层同一处，见 `tax_evidence._caveats`）。

        变异自检：把角标那段 `${item._reliability==='medium'?...仅参考...}` 抄回
        index.html，第一条断言报红；把 `rel-note` 那一行删掉，第二、三条报红；
        在界面里另存一份按档位配的固定文案，第四条报红。
        """
        self.assertNotIn("仅参考", HTML, "降权标签不能留在界面上")
        self.assertIn('class="rel-note"', HTML, "提醒要印在卡面上，不能只挂在 title 里")
        self.assertIn("item._reliability_note", HTML, "提醒要用来源自己那句")
        self.assertNotIn("RELIABILITY_NOTE", HTML, "界面不另存一份按档位配的文案")


class TestValidityCorroboratedByCitation(unittest.TestCase):
    """「财税文件」那一栏根本不录时效，援引证据补这一格。

    59 号、109 号是点名公告正文列明的制定依据，却是这道题的上位规则；
    因为法规库这两条的时效录入项为空（财税〔2003〕16 号那个位置上是
    字符串 "null"），它们全被压在依据线以下，答案只能说"只能参考"。详情页也只有
    成文日期，没有时效栏可回填。
    """

    ANN = "国家税务总局关于企业重组业务所得税处理有关征管问题的公告"
    BODY = ("根据《中华人民共和国企业所得税法》及其实施条例、《财政部 国家税务总局关于"
            "企业重组业务企业所得税处理若干问题的通知》（财税〔2009〕59号）、《财政部 国家"
            "税务总局关于促进企业重组有关企业所得税处理问题的通知》（财税〔2014〕109号）"
            "等文件规定，现对企业重组业务所得税处理有关征管问题公告如下：\n"
            "一、对于企业合并、分立业务……")
    AT = "2026-09-29"

    def _rows(self):
        return [
            {"title": self.ANN, "status": "全文有效", "document_number": "国家税务总局公告2026年第13号",
             "url": "u13", "_cited_target": True, "body": self.BODY},
            {"title": "财政部 国家税务总局关于企业重组业务企业所得税处理若干问题的通知",
             "document_number": "财税〔2009〕59号", "url": "u59"},
            {"title": "财政部 国家税务总局关于促进企业重组有关企业所得税处理问题的通知",
             "document_number": "财税〔2014〕109号", "url": "u109"},
            {"title": "财政部 国家税务总局关于营业税若干政策问题的通知",
             "document_number": "财税〔2003〕16号", "url": "u16"},
        ]

    def test_cited_basis_docs_become_citable(self):
        rows = self._rows()
        tagged = ANS.corroborate_validity_from_target(rows, self.AT)
        self.assertEqual(["财政部 国家税务总局关于企业重组业务企业所得税处理若干问题的通知",
                          "财政部 国家税务总局关于促进企业重组有关企业所得税处理问题的通知"],
                         tagged)
        # 没被援引的那份不补，正文里出现的《企业所得税法》也不动（它本来就有效）
        self.assertNotIn("u16", [r["url"] for r in rows if r.get("corroborated_by")])
        for r in rows:
            if r.get("corroborated_by") != self.ANN:
                continue
            g = E.grade(r, at=self.AT, topic="企业重组")
            self.assertEqual("effective", g["validity"])
            self.assertIn("制定依据判定在效", g["validity_note"])
            # 这一条的"现行有效"是佐证出来的，不是法规库录的：必须占一条提醒，
            # 免得读的人把它当成源里的时效字段
            self.assertIn("引用前按该文自身的时效复核", "；".join(g["caveats"]),
                          g["caveats"])

    def test_explicit_status_beats_citation_evidence(self):
        """明文写着废止或未生效的，援引证据不能翻案。"""
        for status, want in (("全文废止", "repealed"), ("尚未生效", "pending")):
            it = {"title": "财政部 国家税务总局关于某事项的通知", "status": status,
                  "corroborated_by": self.ANN}
            self.assertEqual(want, E.judge_validity(it, self.AT)["validity"], status)

    def test_unusable_target_corrobs_nothing(self):
        """援引方自己时效不明、或是解读件，就不具备给别的文件作证的资格。"""
        rows = self._rows()
        rows[0]["status"] = ""
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))
        rows = self._rows()
        rows[0]["_is_interpretation"] = True
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))
        rows = self._rows()
        rows[0].pop("body")
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))

    def test_preamble_only_is_parsed(self):
        """只读"根据……规定"那一段，正文条款里援引的文件不算制定依据。"""
        rows = self._rows()
        rows[0]["body"] = ("根据《中华人民共和国企业所得税法》规定，现公告如下：\n"
                           "四、本公告自发布之日起施行，《财政部 国家税务总局关于企业重组"
                           "业务企业所得税处理若干问题的通知》（财税〔2009〕59号）同时废止。")
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))

    def test_null_placeholder_is_not_a_status(self):
        self.assertEqual("", WS.aging_of("null"))
        self.assertEqual("", WS.aging_of(" NULL "))
        self.assertEqual("", WS.aging_of(None))
        self.assertEqual("全文有效", WS.aging_of("全文有效"))


# ── 轮次取数失败与"这一轮 0 条"必须分得开 ──────────────────────────────────
class TestRoundFailureIsNotSilent(unittest.TestCase):
    """源挂了不许被记成 found=0，否则主依据会静默降级而输出看不出来。

    NPC 那一轮静默失败时，90 分的现行有效法律不见了，顶上【主依据】的是
    70 分的《个体工商户建账管理暂行办法》，而 `rounds_done` 只写着 found=0、
    `errors` 是空的——读的人无法区分"库里没有"和"这一轮没取回来"。
    """

    Q = "增值税小规模纳税人月销售额10万，应纳增值税多少"
    规章 = {"title": "个体工商户建账管理暂行办法", "category": "税务部门规章",
            "status": "现行有效", "url": "http://x/规章"}
    法律 = {"title": "中华人民共和国税收征收管理法", "category": "法律",
            "status": "现行有效", "url": "http://x/法律"}

    def _gather(self, fail=(), with_law=True):
        """把所有源换成打桩客户端：fail 里的源报"取数失败"，其余回条目。"""
        def make(src):
            def fn(term, size):
                if src in fail:
                    return [], "响应不是 JSON: Expecting value: line 1 column 1 (char 0)"
                return [dict(self.规章)] + ([dict(self.法律)] if with_law and src == "npc" else []), ""
            return fn
        with mock.patch.dict(ANS._FETCHERS,
                             {s: make(s) for s in ("npc", "fgk", "shui5", "wechat", "legis")}):
            return ANS.gather(self.Q, at="2026-09-29")

    def test_source_error_names_the_round_and_the_source(self):
        g = self._gather(fail=("npc",))
        self.assertTrue(any("第1轮 npc 取数失败" in e for e in g["errors"]), g["errors"])
        self.assertIn("npc", g["rounds_done"][0]["failed"], g["rounds_done"])
        a = ANS.compose(g)
        self.assertTrue(a["fetch_errors"] and a["fetch_note"], a["fetch_note"])
        # 这一趟只剩部门规章：主依据确实被降级，靠 fetch_note 才读得出来
        self.assertEqual(self.规章["title"], a["primary"]["title"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ANS._print_answer(a)
        self.assertIn("【取数失败】", out.getvalue())
        self.assertIn("不等于库里没有", out.getvalue())

    def test_clean_empty_round_stays_silent(self):
        """干净的空结果不许编出一条失败来——两种"没有"分开，也得各自成立。"""
        with mock.patch.dict(ANS._FETCHERS,
                             {s: (lambda t, n: ([], ""))
                              for s in ("npc", "fgk", "shui5", "wechat", "legis")}):
            g = ANS.gather(self.Q, at="2026-09-29")
        self.assertEqual([], g["errors"])
        self.assertEqual([], [f for r in g["rounds_done"] for f in r["failed"]])
        a = ANS.compose(g)
        self.assertEqual("", a["fetch_note"])
        self.assertTrue(a["evidence_gap"], "一条依据都没取到时该说证据缺口，不是取数失败")

    def test_legislation_lane_items_cannot_become_primary(self):
        """立法过程实位取回的条目带 `legislative_process`，定级层据此不挑它当主依据。

        人大网站的草案与审议公告是"找到文本的线索"，本身不是已公布的规定。
        这一条拒绝的理由是"它不是已公布的条文"这个事实，不是降权：
        `_reliability: medium` 只负责附带一句提醒，
        真正把这一层挡在头条之外的是 `legislative_process` 这个标记。
        """
        page = {"results": [{"title": "法律草案审议 中国人大网",
                             "url": "http://www.npc.gov.cn/npc/c2/x.html"}], "total": 1}
        with mock.patch.object(ANS.S360, "so360_search", return_value=page) as hit:
            rows, err = ANS._legis_round("税收征收管理法 修订草案", 8)
        self.assertEqual("", err)
        self.assertEqual("npc.gov.cn", hit.call_args.kwargs["site"])
        self.assertEqual("medium", rows[0]["_reliability"])
        self.assertTrue(rows[0]["legislative_process"])
        graded = [E.grade(r, at="2026-09-29") for r in rows]
        best = E.pick_primary(graded)
        self.assertEqual("", best.get("title", ""), "立法过程线索不能被选成主依据")
        self.assertIn("立法过程线索", best["_why"])
        # 掺进一条正常依据时，主依据让位给那条，线索仍不能靠兜底上位
        mixed = graded + [E.grade({"title": "中华人民共和国税收征收管理法",
                                   "status": "全文有效"}, at="2026-09-29")]
        self.assertNotEqual("法律草案审议 中国人大网",
                            E.pick_primary(mixed)["title"])
        # 但它没有被删掉：落选清单里仍列着这一条
        self.assertIn("法律草案审议 中国人大网",
                      [r["title"] for r in best["_runners_up"]])

    def test_draft_round_also_searches_shui5_and_wechat(self):
        """草案那一轮必须连带搜税屋与公众号：草案解读文章只活在这两个源里。

        草案轮若只排 sources=["legis"]（人大网站内），税屋与公众号一次都不查，
        会把最有料的实务解读整层漏掉：search_shui5("税收征管法 修订草案") 首条
        即国务院法制办征求意见稿通知、含"修订重点逐条解读"多篇。
        """
        plan = ANS.build_plan("请解读《税收征管法》修订草案，已提请全国人大常委会审议")
        rnd = [r for r in plan["rounds"] if "legis" in r["sources"]]
        self.assertEqual(1, len(rnd), "应有且仅有一轮立法取证")
        self.assertEqual({"legis", "shui5", "wechat"} & set(rnd[0]["sources"]),
                         {"legis", "shui5", "wechat"},
                         "立法取证轮要覆盖人大网+税屋+公众号三个源")
        self.assertEqual("legis", rnd[0].get("term_key"),
                         "这一轮三个源统一用草案词，不用各源的主题短词")

    def test_draft_round_uses_draft_term_not_short_term(self):
        """税屋/公众号在草案轮拿到的词必须含"草案"，否则搜回的是现行规定。

        打桩三个 fetcher，跑 gather，抓每源被调用时收到的检索词。
        """
        seen = {}

        def make(src):
            def fn(term, size):
                seen.setdefault(src, []).append(term)
                return [], ""
            return fn

        Q = "请解读《税收征管法》修订草案，已提请全国人大常委会审议"
        with mock.patch.dict(ANS._FETCHERS,
                             {s: make(s) for s in ("npc", "fgk", "shui5",
                                                   "wechat", "legis")}):
            ANS.gather(Q, at="2026-09-29")
        # 草案轮与非草案轮的 shui5 调用词并存，取含"草案"的那一次
        terms_used = seen.get("shui5", [])
        self.assertTrue(any("草案" in t for t in terms_used),
                        f"shui5 在草案轮应拿到含'草案'的词，实际 {terms_used}")
        self.assertTrue(any("草案" in t for t in seen.get("wechat", [])),
                        f"wechat 在草案轮应拿到含'草案'的词，实际 {seen.get('wechat')}")


# ── 返回形态守门：注解写了 tuple，每一条 return 就得真的给元组 ────────────────
def _tuple_contract_offenders(path):
    """扫一个文件，列出 `-> tuple[...]` 却返回别的形状的 [(函数名, 行号, 返回表达式)]。

    只看直接写在该函数体里的 return，嵌套函数与 lambda 内部的归它们自己的注解管，
    不然一个元组函数里写个返回字符串的闭包会被误报。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for fn in (n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        ann = fn.returns
        if not ann or not ast.unparse(ann).startswith("tuple["):
            continue
        arity = (len(ann.slice.elts)
                 if isinstance(ann, ast.Subscript) and isinstance(ann.slice, ast.Tuple)
                 else None)
        inner = set()
        for sub in ast.walk(fn):
            if sub is fn or not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                 ast.Lambda)):
                continue
            inner.update(id(x) for x in ast.walk(sub))
        for r in (n for n in ast.walk(fn)
                  if isinstance(n, ast.Return) and n.value is not None
                  and id(n) not in inner):
            is_tuple = isinstance(r.value, ast.Tuple)
            right_arity = arity is None or (is_tuple and len(r.value.elts) == arity)
            if not (is_tuple and right_arity):
                offenders.append((fn.name, r.lineno, ast.unparse(r.value)))
    return offenders


class TestTupleReturnContract(unittest.TestCase):
    """`-> tuple[list, str]` 这类函数，成功分支必须和失败分支给同样多的值。

    起因是真缺陷：`tax_server._search_web_broad` 注解与文档都写着返回
    (结果, 拦截说明)，失败分支给了二元组，成功分支却 `return results` 交回裸列表，
    调用方按二元组解包。列表长度说了算：1 条与 3 条都抛 ValueError，被调用方的
    `except Exception` 吞成空结果；正好 2 条时两个名字各接住一个 dict，接口回 500
    （报错原文 `'str' object has no attribute 'get'`）。
    路由用例若把桩打在 `_search_web_broad` 上、桩形用二元组，就会与被测函数的真实
    返回形态不一致，这条断路在测试里一直是绿的。

    这一条把它变成编译期就拦得住的形状：全项目扫一遍，一处不合规门禁就红。
    """

    def test_scripts_and_tests_honour_their_tuple_annotations(self):
        offenders = []
        for d in ("scripts", "tests"):
            for p in sorted((ROOT / d).glob("*.py")):
                for name, lineno, expr in _tuple_contract_offenders(p):
                    offenders.append("{}:{}  {} 返回 {}".format(p.name, lineno, name, expr))
        self.assertEqual(
            [], offenders,
            "注解是 tuple 却返回别的形状（调用方按元组解包会静默丢数据或报 500）：\n  "
            + "\n  ".join(offenders))

    def test_scanner_sees_the_shape_that_caused_the_bug(self):
        """自检：把当年那一行喂进扫描器，必须报出来；不报就说明守门是假的。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "bad.py"
            tmp.write_text(
                "def ok(q):\n"
                "    if q:\n"
                "        return [], '拦了'\n"
                "    return [], ''\n"
                "\n"
                "def bad(q):\n"
                "    results = []\n"
                "    return results\n",
                encoding="utf-8",
            )
            # 两份都注解成二元组，只有 bad 的返回形状不对
            src = tmp.read_text(encoding="utf-8").replace(
                "def ok(q):", "def ok(q) -> tuple[list, str]:").replace(
                "def bad(q):", "def bad(q) -> tuple[list, str]:")
            tmp.write_text(src, encoding="utf-8")
            offenders = _tuple_contract_offenders(tmp)
        self.assertEqual([("bad", 8, "results")], offenders)


class TestPracticeCitationCheck(unittest.TestCase):
    """实务材料的口径要追到一份现行有效的法定文件上，逐条核对。

    这一层替代的是"给税屋/公众号打 10 分、标成只能参考"：降权既不告诉读者哪条
    口径有文件托着，也不告诉读者哪条悬空。现在按文号回官方库查存在与时效。
    全部用例用打桩的库，不发真实检索。
    """

    @staticmethod
    def _文章(content="", source="税屋 (shui5.cn)"):
        row = {"title": "减征车辆购置税的执行口径", "source": source,
               "url": "https://www.shui5.cn/article/9/1.html"}
        if content:
            row["content"] = content
        return row

    def _核对(self, rows, 库里=None, 库报错="", 命中数=None, 响应=None, **kw):
        """跑一次核对，带回 (统计, 发出去查的检索词列表)。

        `库报错` 走 `_fetch_failed` 标记——真实接口零命中时也会写一句 `_error`
        当说明（`tax_fgk._scan_list`），本层认的是标记不是句子。要看接口原样
        回的东西就用 `响应`，它绕过上面两个参数。
        """
        calls = []

        def lookup(term):
            calls.append(term)
            if 响应 is not None:
                return dict(响应)
            if 库报错:
                return {"_error": 库报错, "_fetch_failed": True}
            return {"results": 库里 or [], "total_hits": 命中数}

        stats = ANS.check_practice_citations(rows, at="2026-09-29",
                                             lookup=lookup, **kw)
        return stats, calls

    有效件 = {"title": "财政部 税务总局关于减征车辆购置税的公告",
              "document_number": "财政部 税务总局公告2023年第19号",
              "status": "现行有效", "url": "http://x/19"}

    def test_citation_found_in_library_is_named_with_its_number(self):
        文 = self._文章("依据财政部 税务总局公告2023年第19号，新能源车免征购置税")
        stats, calls = self._核对([文], 库里=[dict(self.有效件)])
        # 发出去的是带机关的宽写法（实测裸短形在库里零命中），记下来的还是
        # 文章里那枚短形——两头各管一件事
        self.assertEqual(["财政部 税务总局公告2023年第19号"], calls)
        st = 文["official_status"]
        self.assertEqual("effective", st["outcome"], st)
        self.assertEqual("2023年第19号", st["doc_number"], "记的是文章援引的那个短形")
        self.assertEqual(self.有效件["title"], st["title"], "文件名要取库里的规范名")
        self.assertEqual("http://x/19", st["url"])
        self.assertEqual({"effective": 1}, stats["by_outcome"])
        self.assertEqual(1, stats["checked"])

    def test_reminder_lands_in_the_graded_caveats(self):
        """核对结果只写进字段不算交付；定级那一条必须把它说成人话。"""
        文 = self._文章("依据财政部 税务总局公告2023年第19号执行")
        self._核对([文], 库里=[dict(self.有效件)])
        g = E.grade(文, at="2026-09-29", topic="车辆购置税")
        self.assertTrue(any("2023年第19号" in c and "税务总局法规库" in c
                            for c in g["caveats"]), g["caveats"])

    def test_repealed_citation_says_reread_the_current_text(self):
        库 = dict(self.有效件, status="全文废止")
        文 = self._文章("按财政部 税务总局公告2023年第19号的规定免征")
        self._核对([文], 库里=[库])
        line = E.official_caveat(文["official_status"])
        self.assertEqual("repealed", 文["official_status"]["outcome"])
        self.assertIn("不再执行的规则", line)

    def test_library_miss_is_stated_as_a_miss_not_as_repealed(self):
        """库里没有这一份，与库里有但已废止，是两条完全不同的下一步动作。"""
        文 = self._文章("按国税发〔1999〕43号执行")
        stats, _ = self._核对([文], 库里=[])
        self.assertEqual("not_in_library", 文["official_status"]["outcome"], stats)
        self.assertIn("没有对上同一份文件", E.official_caveat(文["official_status"]))

    def test_zero_hit_with_an_explained_empty_is_a_miss_not_a_failure(self):
        """接口自己说"这次没命中"和"这轮没连上"，不能读成同一件事。

        真实 `search_fgk` 的形态（实测）：零命中时带一句 `_error` 但**不带**
        `_fetch_failed`；请求或解析失败才带 `_fetch_failed`。拿 `_error` 当故障，
        每一篇援引了生僻文号的文章都会被写成"连不上库、补一轮"，而补一百轮也
        还是零命中。反向弄错同样糟：真挂了报成"库里没有这一份"，读者会去怀疑
        自己抄错文号。所以两个方向都在这里钉住。
        """
        文 = self._文章("按财税〔1999〕43号执行")
        stats, _ = self._核对([文], 响应={"results": [], "total_hits": 0,
                                          "_error": "未检索到相关内容"})
        self.assertEqual("not_in_library", 文["official_status"]["outcome"], stats)
        self.assertIn("检索零命中", E.official_caveat(文["official_status"]))

        挂 = self._文章("按财税〔1999〕43号执行")
        self._核对([挂], 响应={"results": [], "_error": "连接超时",
                              "_fetch_failed": True})
        self.assertEqual("lookup_failed", 挂["official_status"]["outcome"])

    def test_retrieval_term_borrows_the_issuer_not_the_prose(self):
        """补前缀只能借原文里的机关名，借不到就用裸文号。

        喂进 `_citation_phrase` 的是标题+正文拼起来的一整段（实测形态），
        按字符宽度从右截会把"减征车辆购置税的执行口径 依据"一起当成检索词。
        这种词在库里一个也命中不了，于是"现行有效"被写成"库里没对上"——
        假结论还带一句看起来有据的说明。裸文号最坏是命中宽，不会指错文件。
        """
        self.assertEqual(
            "财政部 税务总局公告2023年第19号",
            ANS._citation_phrase("减征车辆购置税的执行口径 依据财政部 税务总局公告"
                                 "2023年第19号", "2023年第19号"))
        self.assertEqual(
            "国家税务总局公告2021年第5号",
            ANS._citation_phrase("国家税务总局公告2021年第5号", "2021年第5号"))
        # 本身就带机关名的写法原样返回；认不到机关名不猜前缀
        self.assertEqual("财税〔2016〕36号",
                         ANS._citation_phrase("依据财税〔2016〕36号附件",
                                              "财税〔2016〕36号"))
        self.assertEqual("2024年第1号",
                         ANS._citation_phrase("某单位关于优惠的公告2024年第1号",
                                              "2024年第1号"))
        # 原文里文号常被排版拆开（"2021 年第 5 号"），而 `tax_terms` 交回来的是
        # 去过空格的短形：按字面 find 会找不到，前缀也就补不上了
        self.assertEqual(
            "国家税务总局公告2021年第5号",
            ANS._citation_phrase("根据国家税务总局公告 2021 年第 5 号的规定",
                                 "2021年第5号"))

    def test_empty_library_document_number_does_not_count_as_a_match(self):
        """只认 want 是 got 的子串：库里那条没录文号时不能算命中，否则任何一篇
        正文里提到文号的文章都会匹配上这条空记录。"""
        文 = self._文章("按国税发〔1999〕43号执行")
        self._核对([文], 库里=[{"title": "某文件", "document_number": "",
                                "status": "现行有效"}])
        self.assertEqual("not_in_library", 文["official_status"]["outcome"])

    def test_lookup_failure_is_not_read_as_missing(self):
        文 = self._文章("按财税〔2016〕36号的规定")
        self._核对([文], 库报错="响应不是 JSON")
        st = 文["official_status"]
        self.assertEqual("lookup_failed", st["outcome"], st)
        line = E.official_caveat(st)
        self.assertIn("不能把「查不到」读成「库里没有」", line)
        self.assertNotIn("没有对上同一份文件", line)

    def test_missing_body_is_not_reported_as_missing_citation(self):
        """`--no-body` 那一趟只有标题：说"这篇没写文号"是假的，要说"没读正文"。"""
        无正文 = self._文章()
        有正文 = self._文章("这篇文章讲的是地方执行的口径细节，没引文号")
        stats, calls = self._核对([无正文, 有正文])
        self.assertEqual("no_body", 无正文["official_status"]["outcome"], stats)
        self.assertEqual("no_citation", 有正文["official_status"]["outcome"], stats)
        self.assertEqual([], calls)
        self.assertIn("没读正文", E.official_caveat(无正文["official_status"]))
        self.assertNotIn("没读正文", E.official_caveat(有正文["official_status"]))

    def test_limit_stops_further_requests_and_says_so(self):
        rows = [self._文章(f"依据国家税务总局公告202{i}年第5号执行") for i in range(1, 4)]
        stats, calls = self._核对(rows, 库里=[dict(self.有效件)], limit=2)
        self.assertEqual(2, len(calls), calls)
        self.assertEqual(1, stats["skipped"], stats)
        self.assertEqual("not_checked", rows[2]["official_status"]["outcome"])
        self.assertIn("上限", E.official_caveat(rows[2]["official_status"]))
        self.assertNotIn("没有对上同一份文件", E.official_caveat(rows[2]["official_status"]))

    def test_same_number_is_looked_up_once_for_all_articles(self):
        rows = [self._文章("按财政部 税务总局公告2023年第19号执行") for _ in range(3)]
        stats, calls = self._核对(rows, 库里=[dict(self.有效件)])
        self.assertEqual(1, len(calls), calls)
        self.assertEqual(1, stats["checked"], stats)
        self.assertEqual(3, stats["by_outcome"]["effective"], stats)
        self.assertEqual({r["official_status"]["title"] for r in rows},
                         {self.有效件["title"]})

    def test_statutory_rows_are_left_alone(self):
        """法定层自己有 status 字段可判时效，不该被这一层重复劳动一遍。"""
        法 = {"title": "中华人民共和国车辆购置税法", "category": "法律",
              "status": "现行有效", "url": "http://x/law"}
        stats, calls = self._核对([法, self._文章("按2023年第19号执行")],
                                  库里=[dict(self.有效件)])
        self.assertNotIn("official_status", 法)
        self.assertEqual(["2023年第19号"], calls)
        self.assertEqual({"effective": 1}, stats["by_outcome"], stats)

    def test_practice_recognition_shares_the_graders_vocabulary(self):
        """认源用 `tax_evidence.PRACTICE_SOURCES`：这里各写一套源名就会静默空转。

        实测过的三种真实形态都要认出来——税屋的标签、公众号的标签、360 回填的纯域名。
        """
        for src in ("税屋 (shui5.cn)", "微信公众号", "shui5.cn"):
            row = self._文章("按财税〔2016〕36号执行", source=src)
            self.assertTrue(ANS._is_practice_row(row), src)
        self.assertFalse(ANS._is_practice_row(
            {"title": "车辆购置税法", "source": "🏛️ 国家税务总局"}))

    def test_nine_outcomes_are_all_reachable_and_each_has_a_sentence(self):
        """九种结果逐一跑出来，一种都不许只在表里挂着。

        这是这套用例自己的覆盖率检查：`OFFICIAL_CAVEAT` 里多一条却没有场景能产出它，
        就说明本层的分支已经和文档对不上；少一条则这里直接红。
        """
        库 = {"results": [dict(self.有效件)]}
        seen = set()

        def 跑(rows, **kw):
            ANS.check_practice_citations(rows, at="2026-09-29", **kw)
            seen.update(r["official_status"]["outcome"] for r in rows
                        if r.get("official_status"))

        def 库内(status):
            return lambda t: {"results": [dict(self.有效件, status=status)]}

        # 库里查到了这一份的四种时效（结果名直接取 `judge_validity` 的取值）
        for status in ("现行有效", "全文废止", "尚未生效", ""):
            跑([self._文章("按财政部 税务总局公告2023年第19号执行")],
               lookup=库内(status))
        # 库里没有同一份 / 正文没写文号 / 没取正文 / 库没连上 / 轮次用满
        跑([self._文章("按财税〔1999〕43号执行")], lookup=lambda t: {"results": []})
        跑([self._文章("只讲口径，没引文号")], lookup=lambda t: 库)
        跑([self._文章()], lookup=lambda t: 库)
        跑([self._文章("按2023年第19号执行")],
           lookup=lambda t: {"_error": "超时", "_fetch_failed": True})
        跑([self._文章(f"依据国家税务总局公告202{i}年第5号执行") for i in range(1, 4)],
           lookup=lambda t: 库, limit=1)
        self.assertEqual(set(E.OFFICIAL_CAVEAT), seen,
                         f"用例产出的结果与提醒表不一致：{sorted(seen)}")
        self.assertEqual(set(E.OFFICIAL_CAVEAT), set(E.OFFICIAL_OUTCOME_LABEL),
                         "两张表必须同键，否则汇总句里会漏出英文键名")
        for key, tpl in E.OFFICIAL_CAVEAT.items():
            self.assertTrue(E.official_caveat({"outcome": key}),
                            f"{key} 的提醒句格式化后为空")

    def test_every_outcome_literal_in_the_producer_has_a_caveat(self):
        """新增核对结果却忘了配句子时，提醒会静默消失——这条拦字面量那一类漏配。

        不跑代码，只读源码：把 `check_practice_citations` 里所有写进字典值的
        小写英文字面量取出来（结果名都是这个形态），逐个要求在两张表里有条目。
        时效那四种结果名来自 `judge_validity` 的取值，不在这个函数里写字面量，
        由上面那条用例逐场景覆盖。
        """
        src = Path(ANS.__file__).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "check_practice_citations")
        emitted = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                for v in node.values:
                    for s in ([v.value] if isinstance(v, ast.Constant) else
                              ([v.body.value, v.orelse.value]
                               if isinstance(v, ast.IfExp)
                               and all(isinstance(x, ast.Constant)
                                       for x in (v.body, v.orelse)) else [])):
                        if isinstance(s, str) and re.fullmatch(r"[a-z][a-z_]{3,}", s):
                            emitted.add(s)
        self.assertTrue(emitted, "没从源码里取出任何结果字面量，用例失效了")
        self.assertEqual(set(), emitted - set(E.OFFICIAL_CAVEAT),
                         f"这些结果没有提醒句：{sorted(emitted - set(E.OFFICIAL_CAVEAT))}")

    def test_unknown_outcome_is_not_silently_dropped(self):
        """认不出的结果要报出来，不能和"没核对过"共用一个空串。"""
        line = E.official_caveat({"outcome": "someday", "doc_number": "2023年第19号"})
        self.assertIn("someday", line)
        self.assertEqual("", E.official_caveat({}))


class TestCitationCheckReachesTheAnswer(unittest.TestCase):
    """核对要一路走到答案输出，不能停在 gather 的字典里。"""

    # 判成 lookup 的问句只走 npc/fgk 两轮，永远碰不到税屋那一轮，这一层就没得核对；
    # 换成算税题，第 3 轮才是实务材料源。
    Q = "增值税小规模纳税人月销售额10万，应纳增值税多少"

    def _跑一趟(self, check_citations=True, with_practice=True):
        文 = {"title": "新能源车购置税免税口径", "source": "税屋 (shui5.cn)",
              "url": "https://www.shui5.cn/article/9/1.html",
              "content": "依据财政部 税务总局公告2023年第19号免征"}
        法 = {"title": "中华人民共和国车辆购置税法", "category": "法律",
              "status": "现行有效", "url": "http://x/law"}
        库 = {"results": [{"title": "财政部 税务总局关于减征车辆购置税的公告",
                          "document_number": "财政部 税务总局公告2023年第19号",
                          "status": "现行有效", "url": "http://x/19"}]}
        calls = []

        def fake_fgk(term, size=5, **rest):
            calls.append(term)
            return dict(库)

        def make(src):
            """按源发桩：返回 fetcher，取数契约是 (条目, 失败说明)。"""
            if src == "shui5":
                return lambda term, size: ([dict(文)] if with_practice else [], "")
            if src == "npc":
                return lambda term, size: ([dict(法)], "")
            return lambda term, size: ([], "")

        with mock.patch.dict(ANS._FETCHERS,
                             {s: make(s) for s in ("npc", "fgk", "shui5",
                                                   "wechat", "legis")}), \
             mock.patch.object(FGK, "search_fgk", fake_fgk):
            return ANS.gather(self.Q, at="2026-09-29",
                              check_citations=check_citations), calls

    def test_gather_records_progress_and_compose_says_it(self):
        plan, calls = self._跑一趟()
        self.assertEqual(["财政部 税务总局公告2023年第19号"], calls)
        self.assertEqual(1, plan["citation_check"]["checked"], plan["citation_check"])
        a = ANS.compose(plan)
        self.assertIn("税务总局法规库核对 1 个", a["citation_note"], a["citation_note"])
        self.assertIn("在库且现行有效 1 条", a["citation_note"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ANS._print_answer(a)
        self.assertIn("文号已回税务总局法规库核对", out.getvalue())

    def test_turning_the_check_off_leaves_a_visible_empty_not_a_fake_zero(self):
        plan, calls = self._跑一趟(check_citations=False)
        self.assertEqual([], calls)
        self.assertIsNone(plan["citation_check"])
        self.assertEqual("", ANS.compose(plan)["citation_note"])

    def test_no_practice_row_says_none_needed_instead_of_nothing_found(self):
        """一道纯法条题：这一栏要说"没有文号需要核对"，不是留空让人猜。"""
        plan, calls = self._跑一趟(with_practice=False)
        self.assertEqual([], calls)
        self.assertEqual({"checked": 0, "skipped": 0, "by_outcome": {}},
                         plan["citation_check"])
        note = ANS.compose(plan)["citation_note"]
        self.assertIn("没有取回税屋或公众号的文章", note)


# ── 用例自身的能力自检：规则不能永远绿 ─────────────────────────────────────
class TestRulesCanActuallyFail(unittest.TestCase):
    """把规则作用在构造的反例上，确认它真的拦得住。"""

    def test_degraded_note_is_empty_only_without_gaps(self):
        self.assertEqual("", AGG._degraded_note(["npc"], [], {"npc": 3}))
        note = AGG._degraded_note(["npc"], [{"source": "npc", "label": "NPC"}],
                                  {"npc": 3})
        self.assertTrue(note)

    def test_reliability_marker_normalizes_only_known_levels(self):
        """认不出的取值一律当"没有标记"，免得凭空多出一条提醒。"""
        self.assertEqual("", E._reliability_of({"_reliability": "HIGH"}))
        self.assertEqual("low", E._reliability_of({"_reliability": " LOW "}))
        self.assertEqual("", E._reliability_of({}))


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    r = unittest.main(verbosity=2, exit=False)
    print("\n结果：%d/%d 通过" % (r.result.testsRun - len(r.result.failures)
                                - len(r.result.errors), r.result.testsRun))
    sys.exit(0 if r.result.wasSuccessful() else 1)
