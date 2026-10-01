#!/usr/bin/env python3
"""路由与检索词整形的离线用例。

这里每一条都只依赖字符串与打桩，不联网：改别名表、改排序、改降级规则之后
跑这一份就能知道有没有把已经修好的归类撞回去。联网那部分（检索词真不真能
翻出文件）由 test_tax_search.py 的 sta 专题可达性用例负责，两边不重复。

用法：python tests/test_routing_terms.py
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tax_answer as AN          # noqa: E402
import tax_analyze as AA         # noqa: E402
import tax_evidence as E         # noqa: E402
import tax_fgk                   # noqa: E402
import tax_search as T           # noqa: E402
import tax_terms as TT           # noqa: E402
import tax_web_search as W       # noqa: E402


def test_strip_options():
    # 选项里的干扰税种不能参与归类：题干问车船税，选项里有"扣缴义务人"
    stem = TT.strip_options(
        "下列各项中，符合车船税征收管理规定的是（ ）。　"
        "A.扣缴义务人代收代缴；B.纳税地点为登记地；C.次月发生义务；D.使用人为纳税人")
    assert "车船税" in stem and "扣缴义务人" not in stem, stem
    # 题干自己写着 A. 这种编号但不是选项（不足两个并列标记）时不截断
    plain = "增值税的税率 A.13% 是怎么规定的"
    assert TT.strip_options(plain) == plain, "单个 A. 不该被当成选项头"
    # 只有 A、B 两个标记也要截：两选项题真实存在
    two = TT.strip_options("契税的纳税人是谁？　A.卖方；B.买方")
    assert two == "契税的纳税人是谁？", two
    assert TT.strip_options("") == ""
    print("  [PASS] strip_options：两个以上并列标记才截，单字母编号不误伤")


def test_cited_documents():
    q = "查询、阅读、分析、解读《关于企业重组业务所得税处理有关征管问题的公告》（2026年第13号）"
    cited = TT.cited_documents(q)
    assert len(cited) == 1, cited
    assert cited[0]["doc_number"] == "2026年第13号", cited[0]
    assert cited[0]["core"].startswith("企业重组"), cited[0]
    # 财税〔2009〕59号 这种带方括号的写法也要认
    assert TT.doc_number_of("依据财税〔2009〕59号文件") == "财税〔2009〕59号"
    assert TT.doc_number_of("没文号") == ""
    # 没点名的题面不该产出点名结果
    assert TT.cited_documents("研发费用加计扣除比例是多少") == []
    print("  [PASS] cited_documents：书名号标题与两种文号写法都能取出")


def test_title_candidates():
    title = "国家税务总局关于企业重组业务所得税处理有关征管问题的公告"
    cands = TT.title_candidates(title)
    # 由长到短，且第一个短词必须落在"企业重组"这个真正的主题上
    assert cands[0].startswith("企业重组"), cands
    assert "企业重组" in cands, cands
    assert len(set(cands)) == len(cands), f"候选词有重复：{cands}"
    assert all(len(c) >= 2 for c in cands), cands
    # 短标题剥掉文种之后不该再产出碎词候选
    assert TT.title_candidates("增值税暂行条例") == ["增值税"], \
        TT.title_candidates("增值税暂行条例")
    print(f"  [PASS] title_candidates：{cands}")


def test_title_similarity_orders_document_first():
    want = title = "关于企业重组业务所得税处理有关征管问题的公告"
    doc = "国家税务总局关于企业重组业务所得税处理有关征管问题的公告"
    interp = "关于《国家税务总局关于企业重组业务所得税处理有关征管问题的公告》的解读"
    other = "国家税务总局关于非货币性资产投资企业所得税有关征管问题的公告"
    assert TT.title_similarity(want, doc) >= TT.title_similarity(want, other)
    # 解读与原文字面几乎并列，靠 tax_answer 里的排序惩罚分开，这里只验纯相似度
    assert TT.title_similarity(want, interp) > TT.title_similarity(want, other)
    assert TT.title_similarity("", "x") == 0.0
    print("  [PASS] title_similarity：目标文件高于同域其他文件")


# 点名「税收征管法」时，总局法规库按 tax_terms.title_candidates 召回的
# 六条标题（按字符命中率降序）。六条里没有一条是《中华人民共和国税收征收管理法》：
# 排第一那份只是在自己的标题里引用了这部法，字符命中率却是满分 1.0。
FGK_SIX_FOR_ZGL = [
    "国家税务总局关于农业税、牧业税、耕地占用税、契税征收管理暂参照"
    "《中华人民共和国税收征收管理法》执行的通知",
    "国家税务总局关于发布《税收减免管理办法》的公告",
    "深化税收征管体制改革持续加力",
    "第一届“一带一路”税收征管合作论坛",
    "国家税务总局关于印发全国统一税收执法文书式样的通知",
    "欠税公告办法（试行）",
]


def test_title_identity_tells_named_from_cited():
    """点名文件要判的是"是不是这一份"，字符命中率判不出这个区别。"""
    for t in FGK_SIX_FOR_ZGL:
        assert TT.title_similarity("税收征管法", t) > 0, t      # 老尺子照样给分
        assert TT.title_identity("税收征管法", t) == "", \
            f"标题里引用了这部法，被认成了它本身：{t}"
    # 该认的两档：多一个发文机关前缀算字面同一，漏字简称算缩略同一
    assert TT.title_identity(
        "关于企业重组业务企业所得税处理有关征管问题的公告",
        "国家税务总局关于企业重组业务企业所得税处理有关征管问题的公告") == "same"
    assert TT.title_identity("税收征管法",
                             "中华人民共和国税收征收管理法") == "abbrev"
    # 文种不同就不是同一份：点《增值税法》时不能拿《增值税法实施条例》顶上，
    # 也不能拿"增值税法施行后……的公告"顶上——后者的核心标题含住前者。
    assert TT.title_identity("增值税法", "中华人民共和国增值税法实施条例") == ""
    assert TT.title_identity("增值税法",
                             "国家税务总局关于增值税法施行后若干征管事项的公告") == ""
    assert TT.title_identity("", "中华人民共和国增值税法") == ""
    print("  [PASS] title_identity：引用式标题与邻近文种都不再冒充被点名的文件")


def test_locate_cited_document_needs_an_identity_match():
    """桩打在 fgk 检索入口：定位这一层的判据是"是不是这一份"，不是谁排前面。"""
    real = AN.FGK.search_fgk

    def stub(rows):
        AN.FGK.search_fgk = lambda kw, size=6: {
            "results": [{"title": t, "url": "u%d" % i}
                        for i, t in enumerate(rows)]}

    try:
        stub(FGK_SIX_FOR_ZGL)
        rows, tried = AN.locate_cited_document("税收征管法")
        assert tried and rows, (tried, rows)
        assert all(not r.get("_cited_identity") for r in rows), \
            [r.get("_cited_identity") for r in rows]

        # 真目标混在这一堆里时必须排到第一，哪怕别人的字面命中率一样高
        stub([FGK_SIX_FOR_ZGL[0], "中华人民共和国税收征收管理法"])
        rows, _ = AN.locate_cited_document("税收征管法")
        assert rows[0]["title"] == "中华人民共和国税收征收管理法", rows[0]
        assert rows[0]["_cited_identity"] == "abbrev", rows[0]
    finally:
        AN.FGK.search_fgk = real
    print("  [PASS] locate_cited_document：无同一性证据就不打点名标记")


def test_unlocated_cited_document_falls_back_with_a_note():
    """没定位到点名文件：主依据回到分层判据，并且把"没定位到"写进答案层。"""
    plan = AN.build_plan("请使用技能查询、阅读、分析、解读《税收征管法》修订草案")
    plan["terms"] = dict(plan.get("terms") or {}, cited=[{"title": "税收征管法"}])
    plan["cited_located"] = False
    plan["evidence"] = E.grade_all([
        {"title": FGK_SIX_FOR_ZGL[0], "url": "u110", "effect_level": "规范性文件",
         "status": "已废止", "document_number": "国税发〔2001〕110号"},
        {"title": "中华人民共和国税收征收管理法", "effect_level": "法律",
         "status": "现行有效"},
    ])
    a = AN.compose(plan)
    # 修复前这一格是那份已废止的 2001 年通知（19.2 分），90 分的现行法律被挤到并列位
    assert a["primary"]["title"] == "中华人民共和国税收征收管理法", a["primary"]
    assert "没有定位到同一份文件" in a["cited_note"], a["cited_note"]
    # 没点名的题不带这句，免得答案里凭空多一段不相关的提醒
    plain = AN.compose({k: v for k, v in plan.items() if k != "cited_located"})
    assert plain["cited_note"] == "", plain["cited_note"]
    print("  [PASS] cited_located=False：主依据回到分层，点名未命中如实标注")


def test_route_by_tax_type_not_cross_topic():
    cases = [
        ("企业重组业务中适用特殊性税务处理需要满足哪些条件", "企业所得税"),
        ("下列属于稿酬所得项目的是", "个人所得税"),
        ("国际重复征税产生的根本原因是什么", "税收协定"),
        # 横切专题的别名要长过税种别名两个字以上才允许抢占
        ("某公司进口货物被海关征收滞纳金，应按关税还是滞纳金计算", "关税"),
        ("个人所得税的专项附加扣除怎么填报", "个人所得税"),
    ]
    for text, want in cases:
        got = (T.resolve_tax_type(text) or {}).get("type", "")
        assert got == want, f"{text[:24]} 归到 {got}，应为 {want}"
    print(f"  [PASS] 归类：{len(cases)} 条按税种优先、别名补齐后各归其位")


def test_alias_table_pins_accepted_and_rejected_words():
    """别名表两类要钉住：本轮收进来的词必须归对，量过后没收的词不许回来。

    没收的理由不是"没测"，是实测会抢题（见 tax_search 里那段注释）：排序按别名
    长度定胜负，跨税种通用的长词会把整题从真正所属的税种抢走，所以它们要一直
    留在未路由那一边，由界面按字面检索如实报出来。
    """
    accepted = [
        ("中国籍船舶驶入国内港口，按吨位缴纳的船舶吨税怎么计算", "船舶吨税"),
        ("占用耕地建设厂房要不要缴纳耕地占用税", "耕地占用税"),
        ("企业发生的长期待摊费用，在企业所得税前如何分期摊销扣除", "企业所得税"),
        ("企业取得的资产损失，所得税前能不能扣除", "企业所得税"),
        # 日式字形「増」U+5827：NFKC 折不成「增」，只能靠表里收下这个写法
        ("土地増值税清算的条件有哪些", "土地增值税"),
        ("任职受雇取得的工资、薪金所得怎么预扣预缴", "个人所得税"),
        ("一般纳税人选择简易计税方法，征收率是多少", "增值税"),
    ]
    for text, want in accepted:
        got = (T.resolve_tax_type(text) or {}).get("type", "")
        assert got == want, f"{text[:24]} 归到 {got}，应为 {want}"

    # 本体法不许挂错：船舶吨税与车船税是两部法律，吨税不得挂在车船税法下
    assert (T.TAX_TYPE_KEYWORDS["船舶吨税"]["parent_law"]
            == "中华人民共和国船舶吨税法")
    assert "船舶吨税" not in T.TAX_TYPE_KEYWORDS["车船税"]["aliases"]

    rejected = [
        # 跨税种通用：收进企业所得税会抢走别的税种（应纳税所得额实测改判 13 题，
        # 业务招待费与广告费改判 6 题），所以这三个词一直没进表
        "企业取得的应纳税所得额如何计算",
        "业务招待费的扣除比例是多少",
        "广告费和业务宣传费的扣除限额是多少",
        # 归个税，不是企业所得税
        "个人转让限售股取得的所得怎么征收个人所得税",
    ]
    for text in rejected:
        info = T.resolve_tax_type(text) or {}
        if "限售股" in text:
            assert info.get("type") == "个人所得税", (text, info)
        else:
            assert info.get("type") != "企业所得税", \
                f"「{text[:20]}」又被企业所得税抢走了，这个别名被收回去过"
    print(f"  [PASS] 别名表：{len(accepted)} 个新收词归对，"
          f"{len(rejected)} 个量过后没收的词没有被重新收进来")


def test_route_prefers_stem_and_falls_back_to_options():
    # 题干有税种时，选项里的别的税种不抢
    hijack = T.resolve_tax_type(
        "下列各项中，符合车船税征收管理规定的是（ ）。　"
        "A.扣缴义务人代收代缴车船税；B.纳税地点；C.纳税义务发生时间；D.自行申报")
    assert hijack["type"] == "车船税", hijack
    assert hijack.get("matched_from", "stem") == "stem", hijack
    # 题干问的是"哪种税制"，税种名只出现在选项里——这时必须回退到全句才路由得到
    only_opts = T.resolve_tax_type(
        "关于哪一种税制实行了多次课征制，以下选项中正确的是（）。"
        "　A.车辆购置税 B.个人所得税 C.企业所得税 D.增值税")
    assert only_opts and only_opts.get("matched_from") == "options", only_opts
    print("  [PASS] 路由：题干优先，题干无信号时按选项兜底并标注来源")


def test_search_terms_uses_cited_title():
    terms = AN.search_terms(
        "查询、阅读、分析、解读《关于企业重组业务所得税处理有关征管问题的公告》（2026年第13号）")
    assert terms["topic"] == "企业所得税", terms
    # 法规库那一轮要找的是这份文件本身，不是本体法
    assert terms["fgk"].startswith("企业重组"), terms["fgk"]
    assert terms["npc"] == "中华人民共和国企业所得税法", terms["npc"]
    assert terms["cited"][0]["doc_number"] == "2026年第13号", terms
    print(f"  [PASS] 检索词装配：点名文件走标题，上位法留给 npc 轮 → fgk={terms['fgk']}")


def test_sta_search_terms_present_and_renamed():
    sta = {k: v for k, v in T.TAX_TYPE_KEYWORDS.items() if v.get("authority") == "sta"}
    missing = [k for k, v in sta.items() if not v.get("search_term")]
    assert not missing, f"sta 专题缺 search_term：{missing}"
    # "税收协定"这个检索词翻不出法规库条目了，表里必须换成能翻出来的那个
    assert sta["税收协定"]["search_term"] == "双重征税", sta["税收协定"]
    print(f"  [PASS] {len(sta)} 个 sta 专题均有 search_term，税收协定已换用「双重征税」")


class _Resp:
    """假的 requests 响应，只带 search_chinatax 真正用到的两个方法。"""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


def _payload(total, items):
    return {"searchResultAll": {"total": total, "searchTotal": items}}


_ITEM = {"title": "国家税务总局关于企业重组业务所得税处理有关征管问题的公告",
         "url": "http://fgk.chinatax.gov.cn/zcfgk/c100012/c5251155/content.html",
         "pubDate": "2026-07-08 00:00:00",
         "cwrq": "2026-07-08 00:00:00",
         "content": "现对企业重组业务所得税处理有关征管问题公告如下",
         "pubName": "国家税务总局",
         "govDoc": {"docNum": "国家税务总局公告2026年第13号"},
         "xxgk_aging": "全文有效",
         "xxgk_effectLevel": "税务规范性文件"}


def test_search5_page_index_is_zero_based():
    """翻页基准由本用例确认：search5 的 pageNum 从 0 起算，接口层减 1，对外 page 从 1 起算。

    这条用例对应的缺陷：按 1 起算发请求等于每轮都丢掉相关度最高的首屏，
    命中不足 10 条时首屏是唯一一页，取回空列表就被读成"库里没有这份文件"。
    """
    saved = W.requests.get
    sent = []

    def fake(url, params=None, **kw):
        sent.append(dict(params))
        return _Resp(_payload(3, [_ITEM]))

    try:
        W.requests.get = fake
        r1 = W.search_chinatax("企业重组业务所得税处理", page=1)
        r3 = W.search_chinatax("企业重组业务所得税处理", page=3)
    finally:
        W.requests.get = saved

    assert sent[0]["pageNum"] == 0, sent[0]
    assert sent[1]["pageNum"] == 2, sent[1]
    assert r1["total"] == 3 and len(r1["results"]) == 1, r1
    print("  [PASS] 检索接口翻页基准：对外 page 从 1 起算，上线 pageNum 从 0 起算")


def test_aging_and_doc_number_come_from_the_source():
    """时效与文号要能从接口录入项带下来，否则每条法规都只能报"时效未标明"。"""
    saved = W.requests.get

    def fake(url, params=None, **kw):
        return _Resp(_payload(1, [_ITEM]))

    try:
        W.requests.get = fake
        row = W.search_chinatax("企业重组业务所得税处理")["results"][0]
    finally:
        W.requests.get = saved

    assert row["document_number"] == "国家税务总局公告2026年第13号", row
    assert row["status"] == "全文有效", row
    assert row["effect_level"] == "税务规范性文件", row
    assert row["publish_date"] == "2026-07-08", row

    g = E.grade(row, at="2026-09-29")
    assert g["validity"] == "effective", g
    assert g["rank"] == "normative", g
    print("  [PASS] 文号/时效/效力级别随清单落下来，定级据此判出现行有效")


def test_out_of_range_page_and_missing_list_are_told_apart():
    """命中数大于 0 却是空页：翻页越界与首屏没给清单，两种成因的文案不同。"""
    saved = W.requests.get

    def fake(url, params=None, **kw):
        return _Resp(_payload(3, []))

    try:
        W.requests.get = fake
        beyond = W.search_chinatax("企业重组", page=2)      # 3 条只占 1 页
        first = W.search_chinatax("企业重组", page=1)       # 首屏就该有条目
    finally:
        W.requests.get = saved

    assert "已翻过末页" in beyond.get("_empty_reason", ""), beyond
    assert "没给条目清单" in first.get("_empty_reason", ""), first
    assert "_error" not in first, "接口空页不是请求失败"

    # 清单层把这个空页原样透出，不许写成"翻完 N 页未筛出法规库条目"
    saved_scan = tax_fgk.search_chinatax
    try:
        tax_fgk.search_chinatax = lambda keyword, page=1, size=10: first
        r = tax_fgk._scan_list("企业重组业务所得税处理", size=5, max_pages=3)
    finally:
        tax_fgk.search_chinatax = saved_scan
    assert r["total"] == 0, r
    assert "未筛出法规库条目" not in r["_error"], r["_error"]
    print("  [PASS] 翻页越界与首屏空列表分开报，都不读成库里没有")


def test_accounting_gap_flags_questions_keyed_on_accounting():
    """税法条款借用会计结果时，① 要登记会计口径缺口——五个税收源都不收准则条文。"""
    cases = {
        "企业合并中乙企业净资产账面价值1000万元，特殊性税务处理怎么适用":
            "损益落在哪一期、账面价值按什么计量",
        "以自产产品抵偿到期债务，达成债务重组协议，所得税怎么处理":
            "该交易在准则里按哪一类业务处理",
        "会计上作为固定资产核算并按5年计提折旧，税法最低年限是多少":
            "题目直接问会计口径，先取准则原文",
        "境外分支机构利润的收入确认时间是哪一天":
            "收入在会计上确认于哪一期、金额多少",
        "存货计提跌价准备后，可抵扣暂时性差异要不要确认递延所得税":
            "账面价值与计税基础差在哪一期、要不要确认递延所得税",
    }
    for q, want in cases.items():
        got = AA.accounting_gap(q)
        assert got, f"这道题的会计依赖被漏了：{q}"
        assert want in got, f"{q}\n  期望：{want}\n  实得：{got}"
    print(f"  [PASS] accounting_gap：{len(cases)} 类会计要件题都能登记缺口")


def test_accounting_gap_does_not_flag_pure_tax_wording():
    """这两道是拿公开题库逐题量出来的误伤样本，不收进词表就说不上"改得动"。

    "计税基础"是企业所得税法自己的用词，"财务、会计处理办法备案"问的是期限，
    两者都不需要查准则。误伤多认一次只是多一次检索，但把税法术语说成会计缺口
    会让答案去引准则作答，那是引错依据。
    """
    for q in ("企业收取的下列费用中，应计入消费税计税基础的有（ ）。",
              "固定资产大修理支出需达到取得固定资产时计税基础的50%以上，比例是多少",
              "从事生产经营的纳税人应自领取税务登记证件之日起30日内，将其采用的"
              "财务、会计制度和具体的财务、会计处理办法报送备案",
              "小规模纳税人季度销售额30万元是否免征增值税",
              "印花税的税目有哪些",
              # 词表外的写法：只描述业务事实、不点会计科目名。这一条是边界，不是缺陷，
              # 记在这里是为了让"词表认不到"这件事有实例可查（见 SKILL.md ⑩）。
              "这笔以货物抵偿到期债务的交易按什么性质处理"):
        assert AA.accounting_gap(q) == [], AA.accounting_gap(q)
    assert AA.accounting_gap("") == []
    print("  [PASS] accounting_gap：计税基础/会计处理办法备案这类税法措辞不标缺口")


def test_accounting_gap_reaches_the_answer_layer():
    """缺口必须在 --answer 的骨架里，不能只停在判型那一步就丢掉。"""
    plan = AN.build_plan("企业合并中乙企业净资产账面价值1000万元，特殊性税务处理怎么适用")
    assert plan["accounting_gap"], plan
    a = AN.compose(plan)
    assert a["accounting_gap"] == plan["accounting_gap"], a.keys()
    # 没有会计依赖的题不带这一栏，免得答案里凭空多一段不相关的提醒
    plain = AN.compose(AN.build_plan("小规模纳税人季度销售额30万元是否免征增值税"))
    assert plain["accounting_gap"] == [], plain
    print("  [PASS] accounting_gap：build_plan → compose 一路带到答案骨架")


def test_legislative_stage_flags_draft_wording():
    """点名立法过程文件时必须在 ① 认出来：五个税收源收的都是已公布文本。

    三组措辞各盯一条 `tax_analyze.LEGISLATIVE_STAGES` 判据。用户原话里
    "国务院常务会议审议并原则通过…决定将草案提请全国人大常委会审议"同时命中
    草案与提请审议两组，因为"审议通过"是国务院层面的动作，不等于文本已表决公布。
    """
    cases = {
        "2026年8月31日，国务院常务会议审议并原则通过新版《税收征管法》修订草案，"
        "决定将草案提请全国人大常委会审议。请解读《税收征管法》修订草案":
            ["草案阶段：文本尚未表决通过",
             "提请审议阶段：已通过后内部审议，尚未由立法机关表决公布"],
        "增值税法征求意见稿里新增了哪些内容": ["征求意见阶段：文本尚未定稿"],
        "企业所得税法修正稿的送审进展": ["草案阶段：文本尚未表决通过"],
    }
    for q, want in cases.items():
        got = AA.legislative_stage(q)
        for w in want:
            assert w in got, f"{q}\n  期望：{w}\n  实得：{got}"
    print(f"  [PASS] legislative_stage：{len(cases)} 类立法过程题都能标出阶段")


def test_legislative_stage_is_silent_for_promulgated_texts():
    """问已公布文本时不带这一栏，否则每条答案都凭空多一段"你这是草案"。

    最后一例是本轮实测过的原题变体：点名 2026 年第 13 号公告问的是已公布的文件。
    """
    for q in ("增值税的征税范围有哪些",
              "中华人民共和国增值税法什么时候施行",
              "小规模纳税人季度销售额30万元是否免征增值税",
              "请使用技能查询、阅读、分析、解读《关于企业重组业务所得税处理"
              "有关征管问题的公告》（2026年第13号）"):
        assert AA.legislative_stage(q) == [], q
    assert AA.legislative_note([]) == ""
    print("  [PASS] legislative_stage：已公布文本的提问不标阶段")


def test_legislative_note_reaches_the_answer_layer():
    """阶段判据要一路到答案骨架，跟 accounting_gap 同一条通道。

    只在判型那一步认出来不够：拿现行有效版当草案内容写进答案，界面上看不出
    任何异样——90 分的现行有效法律本身就是"看起来对"的东西。
    """
    plan = AN.build_plan("请解读《税收征管法》修订草案的主要变化")
    assert plan["legislative_stage"], plan
    a = AN.compose(plan)
    assert "立法过程文件" in a["legislative_note"], a["legislative_note"]
    assert "现行有效版本" in a["legislative_note"], a["legislative_note"]
    plain = AN.compose(AN.build_plan("小规模纳税人季度销售额30万元是否免征增值税"))
    assert plain["legislative_note"] == "", plain
    print("  [PASS] legislative_note：build_plan → compose 一路带到答案骨架")


def test_content_decides_and_form_only_breaks_ties():
    """判型表钉两组：内容信号压过填空形态，零内容时形态才说话。

    这两组分起来才解释得通兜底率，合起来写就会漏掉一半：
    - 填空题形态（留空、没有"下列"引子）不得无条件拿 +3：把带留空的算税题
      改写成"术语填空"，检索计划跟着从"计税依据与税率"换成"该术语的定义条款"；
      1001 题上这一步单独量出来是 137 题被形态抢走，而兜底数一题不变。
    - 留空前是系动词"是/为"时，不得被当成列举动词剥夺填空分；题面又没有
      "下列/以下"引子拿不到选项分，会落进 confidence=0.30 的兜底档。
    """
    cases = [
        # 带留空的算税题：题面给了完整数据，内容分必须赢过形态分
        ("刘某2022年2月15日从4S店购买小轿车一辆，支付含增值税价款47.46万元。"
         "该型号轿车车船税年税额840元，刘某2022年应缴纳车船税为（ ）。",
         "liability"),
        ("2019年12月大风公司进口一辆本单位自用小汽车，关税完税价格为200000元，"
         "关税税率为10%，消费税税率为5%。大风公司应纳车辆购置税（ ）元。",
         "liability"),
        # 零内容信号时形态才决定：留空前是系动词，填的是术语/期限
        ("资本弱化特殊事项文档应当在关联交易发生年度次年6月30日之前准备完毕，"
         "应当自税务机关要求之日起的一定时间内提供，该时间限定是（ ）。",
         "fill_blank"),
        # 集合动词收尾的留空要逐项列，归选项判断
        ("下列不属于印花税应税凭证的有（ ）。", "option_judge"),
        ("转让定价方法包括（ ）。", "option_judge"),
        # "下列…应缴纳…的有（ ）"不能被算税抢走：选项形态 +2 顶得住
        ("下列各项中，应缴纳增值税的有（ ）。", "option_judge"),
        # 不带"下列"引子的择一提问，不得整片落进兜底档
        ("在考察税收执法监督的主旨时，下面哪一项论述是正确的（ ）。",
         "option_judge"),
        # 真实提问写法：疑问句式而不是考题形态
        ("小规模纳税人季度销售额30万元是否免征增值税", "entitlement"),
        # "哪种…更划算"是方案比较，不许被选项判断抢走（所以没收到裸的"哪种"）
        ("个人转让股权，哪种计税方式更划算？", "compare"),
    ]
    for text, want in cases:
        got = AA.classify(text)
        assert got["type"] == want, f"{text[:30]} 判成 {got['type']}，应为 {want}"
    print(f"  [PASS] 判型：内容定胜负、形态只补零信号，{len(cases)} 条各归其位")


def test_classify_tables_reject_measured_harmful_words():
    """量过之后没收的词要一直没收：这几个写法都会把别的类的题抢进测算/资格判定。

    与 test_alias_table_pins_accepted_and_rejected_words 同一套路子——
    拒绝的理由不是"没必要"，是实测改判会把本来判对的题判错，所以必须留用例。
    """
    liab = AA.QUESTION_TYPES["liability"]["signals"]
    ent = AA.QUESTION_TYPES["entitlement"]["signals"]
    opt = AA.QUESTION_TYPES["option_judge"]["signals"]
    # 裸「缴纳」在现码上会把数值题判对数从 100 推到 134，同时把误抢从 11 涨到 22
    # （5 道选项判断、5 道事项定性、6 道流程时限被抢），净收益 34:11，比
    # 「应缴纳」的 40:2 差一个量级
    assert "缴纳" not in liab, "裸「缴纳」被收回来了，先重跑 probe_classify --oracle"
    assert "应缴纳" in liab
    # 裸「免征/减征」会把陈述句里的免税列举抓成资格判定
    assert "免征" not in ent and "减征" not in ent, "裸「免征/减征」被收回来了"
    assert "是否免征" in ent
    # 裸「哪种/哪个/哪些」是 compare 已经在量的轴，收进选项判断会抢方案比较题
    for w in ("哪种", "哪个", "哪些"):
        assert w not in opt, f"「{w}」被收进 option_judge，会和方案比较抢题"
    # 系动词不是列举动词
    assert "是" not in AA._ENUM_VERBS and "为" not in AA._ENUM_VERBS, \
        "系动词回到了 _ENUM_VERBS，兜底档会翻倍"
    print("  [PASS] 判型词表：四类量过没收的写法没有被重新收进来")


def test_intent_vocabularies_agree():
    """检索路的意图词表有三处主人，键集合必须一模一样。

    `tax_search.detect_intent` 出码，`tax_formatter.INTENT_HEADERS` 把它写成
    markdown 标题，`tax_server.INTENT_LABELS` 把它写成界面标签。两张表都是
    `.get(intent, 兜底)`：新增一档意图而忘了改表，程序不报错，界面上直接印出
    `risk_check` 这样的英文码，markdown 标题退成"查询结果"。

    同时钉住它和主线九类题型互斥——`--intent` 传不了 `liability` 这类主线键，
    两套词表量的是不同的轴：检索档只决定展示措辞，题型决定检索计划与结论形态。
    """
    import tax_formatter as FM
    import tax_server as SV
    assert set(T.INTENTS) == set(FM.INTENT_HEADERS) == set(SV.INTENT_LABELS), (
        f"取值域 {sorted(T.INTENTS)} / markdown {sorted(FM.INTENT_HEADERS)} / "
        f"界面 {sorted(SV.INTENT_LABELS)}")
    # 取值域里的每一档都要真的能被判出来，否则 INTENTS 就是在声明一个死值
    probed = {T.detect_intent(q) for q in [
        "增值税专用发票丢了怎么办", "金税四期会查什么风险", "年度汇算清缴怎么申报",
        "我公司能享受小微企业优惠吗", "增值税税率多少"]}
    assert probed == set(T.INTENTS), f"这些判不出来：{set(T.INTENTS) - probed}"
    assert not (set(T.INTENTS) & set(AA.QUESTION_TYPES)), \
        "检索意图与主线题型用了同一个键名，读代码的人会以为两者能互换"
    print("  [PASS] 意图词表：检索档三处键集合一致，且与主线九类互不混用")


def main():
    tests = [
        test_strip_options,
        test_cited_documents,
        test_title_candidates,
        test_title_similarity_orders_document_first,
        test_title_identity_tells_named_from_cited,
        test_locate_cited_document_needs_an_identity_match,
        test_unlocated_cited_document_falls_back_with_a_note,
        test_route_by_tax_type_not_cross_topic,
        test_alias_table_pins_accepted_and_rejected_words,
        test_route_prefers_stem_and_falls_back_to_options,
        test_content_decides_and_form_only_breaks_ties,
        test_classify_tables_reject_measured_harmful_words,
        test_search_terms_uses_cited_title,
        test_sta_search_terms_present_and_renamed,
        test_search5_page_index_is_zero_based,
        test_aging_and_doc_number_come_from_the_source,
        test_out_of_range_page_and_missing_list_are_told_apart,
        test_accounting_gap_flags_questions_keyed_on_accounting,
        test_accounting_gap_does_not_flag_pure_tax_wording,
        test_accounting_gap_reaches_the_answer_layer,
        test_legislative_stage_flags_draft_wording,
        test_legislative_stage_is_silent_for_promulgated_texts,
        test_legislative_note_reaches_the_answer_layer,
        test_intent_vocabularies_agree,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
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
