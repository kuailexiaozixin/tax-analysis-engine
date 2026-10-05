#!/usr/bin/env python3
"""类案检索（`scripts/tax_cases.py`）的离线用例。不联网：取数一律走注入的出口。

这一层要防的错法有六类，每类各有一组用例：

1. **检索式写法**。台账实测过：案情词配"案例/公布"字样把命中打到 1 条，配处理
   结果词给 508 条。所以三轮取词规则要能被证伪——本轮里任何时候出现"案例""公布"
   这两个字样的检索式，就是一条会静默丢案例的检索式。
2. **判"是文件还是案例"的规则**。既有记录说"URL 含 `/zcfgk/` 是文件不是案例"，
   2026-10-04 量为**不成立**（`www.chinatax.gov.cn/zcfgk/c102439/c5247913` 是曝光
   典型案件）。所以这里必须正反各钉一次：`/zcfgk/` 不作为排除理由，主机名与文号
   栏才是判据。URL 段的切法同样钉死——正则吃掉分隔斜杠时会隔一个漏一个，
   而隔一个漏一个在"URL 里有两段 6 位代号"这种形态下才看得见。
3. **判"这一条是谁发的"**（来源等级 A1/A2/C）。2026-10-04 实跑把「人民日报：研发费用
   加计扣除申报方式优化」判成 A2，而 A2 的话是"各地税务机关通报"——媒体署名和读不出
   署名的行都被 default 成了机关背书。所以三档各钉正反，并加一条快照全表检查：
   凡发布方是媒体的行都不许拿到 A1/A2。
4. **台账组装**。去重（同一起案件在两台主机各回一行）、排序（子类型权重压过日期）、
   时点过滤、零结果与取数失败分开——这四段只要不执行，写在源码里就等于没写，
   所以全部经 `collect(..., fetch=…)` 用现场快照跑真链路，不只看纯函数。
5. **台账文档与代码同源**。`references/source_defects.md` 那一节自称"每行带齐三字段、
   由本文件逐格查"，还自称"真发 19 条"。文档里写的 `tax_cases.X` 必须真存在、
   探针计划必须真等于那个条数，否则这句就是没人复查的保守。
6. **四处挂线在场且说的是同一件事**。脚本写好了却没人指得到它，等于没有这一层；
   指针指向不存在的小节、⑥ 把"支持层"写回"法定依据"、模板丢掉强度上限那一句，
   都是改了代码忘了文档那一类。这一组只查文档措辞，逐条带变异自检。

两根轴的分工是本层的地基（来源等级=谁发布，材料层级=它是什么，位阶=谁能定），
所以 `TestOrthogonality` 单独钉：案例既不进 `tax_evidence.LEGAL_RANK`，
也不 import 那个模块。

用法：`python tests/test_cases.py`（或经 `tests/run_all.py` 的离线门禁）。
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
TESTS_DIR = ROOT / "tests"
FIXTURE = TESTS_DIR / "_fixture" / "case_channel_rows.json"
LEDGER_DOC = ROOT / "references" / "source_defects.md"

sys.path.insert(0, str(SCRIPTS_DIR))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import tax_cases  # noqa: E402
import tax_evidence  # noqa: E402
import tax_web_search as tws  # noqa: E402

# ── 现场快照与打桩出口 ────────────────────────────────────────

with open(FIXTURE, encoding="utf-8") as _f:
    _doc = json.load(_f)
SNAPSHOT_AT = _doc["captured_at"]
ROWS = _doc["rows"]

#: 快照里真实存在的那几条检索词。fixture_fetch 只认它们，别的词回空表——
#: 于是"这一轮取到几条"完全由被测代码拼出来的检索式决定，拼错就回 0 条。
SNAPSHOT_WORDS = sorted({r["_query"] for r in ROWS})


def fixture_fetch(word, page=1, **kwargs):
    rows = [r for r in ROWS if r["_query"] == word]
    return {"keyword": word, "total": len(rows), "results": rows, "source": "chinatax"}


def row(**over):
    """造一条最小可判定的清单行；给 over 就是逐项覆盖。"""
    base = {"title": "某省税务局依法查处一起虚开发票案件",
            "url": "http://www.chinatax.gov.cn/chinatax/n810219/c102025/c5000001/content.html",
            "date": "2024-01-01", "document_number": "", "snippet": "经查，该企业虚开发票。",
            "publisher": "", "source": "chinatax.gov.cn", "source_label": "税务总局"}
    base.update(over)
    return base


class _Recording:
    """记下每一次取数调用，用来断言发出去的维度，而不是只看返回了什么。"""

    def __init__(self, results=None):
        self.calls = []
        self._results = results or {}

    def __call__(self, word, page=1, **kwargs):
        self.calls.append({"word": word, "page": page, **kwargs})
        rows = self._results.get((word, page), [])
        return {"keyword": word, "total": len(rows), "results": rows}


# ── 1 检索式构造 ─────────────────────────────────────────────

class TestQueryConstruction(unittest.TestCase):
    def test_three_rounds_are_all_issued_in_exhaustive(self):
        qs = tax_cases.build_queries("研发费用加计扣除", ["混岗工时", "辅助账"])
        by_round = {}
        for q in qs:
            by_round.setdefault(q["round"], []).append(q["word"])
        self.assertEqual(by_round[1], ["研发费用加计扣除 混岗工时 辅助账"],
                         "第一轮应是主题加前两个要素的全要素组合")
        self.assertEqual(by_round[2], ["研发费用加计扣除 混岗工时",
                                       "研发费用加计扣除 辅助账"],
                         "第二轮应是逐要素拆分，一个要素一条")
        self.assertEqual(by_round[3],
                         [f"研发费用加计扣除 {w}" for w in tax_cases.ADVERSE_WORDS],
                         "第三轮应把处理结果词逐个配在案情词后面")

    def test_recent_mode_omits_the_per_element_round(self):
        qs = tax_cases.build_queries("研发费用加计扣除", ["混岗工时", "辅助账"],
                                     mode="recent")
        self.assertNotIn(2, {q["round"] for q in qs},
                         "recent 要的是'最近有没有查过'，逐要素拆分那一轮是 exhaustive 的事")
        self.assertEqual([q["round"] for q in qs if q["round"] == 3].__len__(), 2,
                         "recent 只发两个结果词")

    def test_no_query_word_contains_the_words_case_or_announcement(self):
        """台账"检索词形态"那一行的落点：往检索词里加"案例"两字反而把案例筛掉。

        这一条不是风格检查。谁在取词表里加回"案例/公布/典型"，加的那一条就会
        稳定地召回 1 条而不是 508 条，而 1 条看起来像"官方没公布过"。
        """
        banned = ("案例", "公布", "典型")
        for topic in ("骗取出口退税", "研发费用加计扣除", "虚开发票"):
            for mode in ("exhaustive", "recent"):
                for q in tax_cases.build_queries(topic, ["辅助账"], mode=mode):
                    for word in banned:
                        self.assertNotIn(word, q["word"],
                                         f"{mode} 的检索式 {q['word']!r} 里出现了"
                                         f"「{word}」——这一维会把命中打到个位数")
        # 反向钉：结果词那一轮确实在发，不是把整轮删了才没有禁字
        self.assertTrue(all(w not in banned for w in tax_cases.ADVERSE_WORDS))
        self.assertGreaterEqual(len(tax_cases.ADVERSE_WORDS), 4)

    def test_adverse_words_are_the_measured_result_words(self):
        words = set(tax_cases.ADVERSE_WORDS)
        for w in ("追缴", "取消资格", "行政处罚", "依法查处"):
            self.assertIn(w, words, f"处理结果词表缺「{w}」")

    def test_topic_only_query_when_no_elements(self):
        qs = tax_cases.build_queries("重大税收违法案件", [])
        self.assertEqual(qs[0]["word"], "重大税收违法案件")
        self.assertIn("题面没给争议要素", qs[0]["why"])

    def test_blank_elements_are_dropped_not_joined(self):
        qs = tax_cases.build_queries("增值税", ["  ", "虚开", ""])
        self.assertEqual(qs[0]["word"], "增值税 虚开")
        self.assertNotIn("增值税  ", [q["word"] for q in qs])

    def test_empty_topic_and_bad_mode_raise(self):
        with self.assertRaises(ValueError):
            tax_cases.build_queries("   ")
        with self.assertRaises(ValueError):
            tax_cases.build_queries("增值税", mode="fast")

    def test_collect_rejects_a_non_padded_as_of_too(self):
        """命令行挡不住的路（别人 import 进来直接调 collect）在这里再挡一次。"""
        rec = _Recording()
        with self.assertRaises(ValueError):
            tax_cases.collect("虚开发票", as_of="2024-1-1", fetch=rec)
        self.assertEqual(rec.calls, [], "校验要发在请求之前")

    def test_bad_order_is_rejected_before_any_request(self):
        rec = _Recording()
        with self.assertRaises(ValueError):
            tax_cases.collect("增值税", order=" newest", fetch=rec)
        self.assertEqual(rec.calls, [], "校验要发在请求之前")


# ── 2 逐条判型与过滤 ─────────────────────────────────────────

class TestRowClassification(unittest.TestCase):
    def test_document_number_present_means_it_is_a_file(self):
        call = tax_cases.classify_row(row(document_number="国税发〔2010〕103号"))
        self.assertFalse(call["keep"])
        self.assertIn("文号", call["reason"])

    def test_title_shaped_like_a_document_number_is_excluded_too(self):
        """清单行常不带 docNum 栏，文号写在标题里也得判出来，否则文件顶掉案例。"""
        call = tax_cases.classify_row(row(title="国家税务总局公告2016年第24号",
                                          document_number=""))
        self.assertFalse(call["keep"], "标题里是文号形态却当案例收进台账")

    def test_fgk_host_without_a_case_column_is_excluded(self):
        call = tax_cases.classify_row(row(
            url="http://fgk.chinatax.gov.cn/zcfgk/c100012/c5194640/content.html"))
        self.assertFalse(call["keep"])
        self.assertIn("法规库", call["reason"])

    def test_path_zcfgk_is_not_a_discriminator(self):
        """既有记录那句"URL 含 /zcfgk/ 是文件不是案例"2026-10-04 量为不成立。

        三条都挂在同一段 `/zcfgk/` 路径下，落点却由栏目代号与文号决定：曝光典型栏
        的案例留下，法规栏那一份按文号出局，栏目体系外的一条按报道留着排最后。
        谁把 `/zcfgk/` 接回排除条件，第一条就会静默消失。
        """
        case = tax_cases.classify_row(row(
            url="http://www.chinatax.gov.cn/zcfgk/c102439/c5247913/content.html"))
        self.assertTrue(case["keep"], "/zcfgk/ 被当成排除理由，会静默丢掉曝光典型")
        self.assertEqual(case["subtype"], "曝光典型")

        file_ = tax_cases.classify_row(row(
            title="国家税务总局关于修订《重大税收违法案件信息公布办法（试行）》的公告",
            document_number="国家税务总局公告2016年第24号",
            url="http://www.chinatax.gov.cn/zcfgk/c100012/c5194640/content.html"))
        self.assertFalse(file_["keep"], "带文号的这一条该由文号栏出局")

        classroom = tax_cases.classify_row(row(
            title="合规小课堂：一笔运费的税前扣除凭证怎么开",
            url="http://www.chinatax.gov.cn/zcfgk/c103098/c5248283/content.html"))
        self.assertTrue(classroom["keep"],
                        "认不出子栏目不等于不是案例，删它就是把没认出来写成没公布过")
        self.assertEqual(classroom["subtype"], tax_cases.OTHER_SUBTYPE[0])

    def test_file_shaped_title_without_a_column_code_is_excluded(self):
        for title in ("关于进一步落实研发费用加计扣除政策的公告",
                      "税收政策问答：加计扣除办法解读",
                      "税务总局举行落实新的组合式税费支持政策专题新闻发布会"):
            call = tax_cases.classify_row(row(title=title, url="http://www.chinatax"
                                             ".gov.cn/chinatax/n810219/n810724/"
                                             "c5178361/content.html"))
            self.assertFalse(call["keep"], f"{title} 该判成文件")

    def test_unrecognised_column_without_file_shape_is_kept_as_other_news(self):
        """认不出子栏目 ≠ 官方没公布过：留在台账里排到最后，不删。"""
        call = tax_cases.classify_row(row(
            title="税务总局公布2起「黑名单」联合惩戒典型案例",
            url="http://www.chinatax.gov.cn/chinatax/n810219/n810744/c4284771/content.html"))
        self.assertTrue(call["keep"])
        self.assertEqual(call["subtype"], tax_cases.OTHER_SUBTYPE[0])
        self.assertEqual(call["weight"], 1)
        self.assertIn("待核", tax_cases.verify_note(row(), call["subtype"]))

    def test_the_three_measured_columns_each_get_their_subtype(self):
        for code, (name, weight) in tax_cases.SUBTYPES.items():
            call = tax_cases.classify_row(row(
                url=f"http://www.chinatax.gov.cn/chinatax/n810219/{code}/c5000001/content.html"))
            self.assertTrue(call["keep"], f"{code} 应该留")
            self.assertEqual(call["subtype"], name)
            self.assertEqual(call["weight"], weight)
        self.assertEqual({c for c in tax_cases.SUBTYPES},
                         {"c102025", "c102439", "c102435"},
                         "子栏目代号是实测出来的三个，改动要同步台账")

    def test_column_codes_find_every_six_digit_segment(self):
        """分隔斜杠被正则吃掉时，相邻段会隔一个漏一个。两段代号同在一行的形态才测得出。"""
        url = ("http://www.chinatax.gov.cn/chinatax/n810215/c102374/c102435/"
               "c1097699/content.html")
        self.assertEqual(tax_cases.column_codes(url), ["c102374", "c102435"])
        call = tax_cases.classify_row(row(url=url))
        self.assertEqual(call["subtype"], "违法公布",
                         "代号只取到前一段就会把这行判成认不出子栏目")

    def test_content_id_is_the_dedupe_key_across_hosts(self):
        a = "http://www.chinatax.gov.cn/zcfgk/c102439/c5245915/content.html"
        b = "http://fgk.chinatax.gov.cn/zcfgk/c102439/c5245915/content.html"
        self.assertEqual(tax_cases.content_id(a), tax_cases.content_id(b))
        self.assertEqual(tax_cases.content_id(a), "c5245915")
        # 认不出正文 id 的（清单页、附件）退回整条 URL，不能塌成空串互撞
        self.assertEqual(tax_cases.content_id("http://x/common_list.html"),
                         "http://x/common_list.html")
        self.assertNotEqual(tax_cases.content_id(""), tax_cases.content_id("http://y/"))


# ── 3 来源等级三档：这一条是谁发的 ───────────────────────────

class TestSourceGrade(unittest.TestCase):
    """A1 税务总局本级 / A2 各地税务机关 / C 不是税务机关。

    2026-10-04 实跑「研发费用加计扣除 混岗工时」时，「人民日报：研发费用加计扣除
    申报方式优化」与一条没有署名的河北案情条目都拿到 A2。A2 说的话是"各地税务机关
    通报"，而这两条一条是媒体稿、一条读不出发布方——判据把"猜不出来"写成了
    "有机关背书"。所以三档各钉正反，再整表扫一遍。
    """

    def test_media_named_publishers_are_c_even_from_a_case_column(self):
        for pub in ("人民日报", "经济日报", "新华社", "中国新闻社", "法制日报", "南宁日报"):
            g = tax_cases.source_grade(row(publisher=pub), "查处通报")
            self.assertEqual(g, "C",
                             f"「{pub}」是媒体。给 A2 就是替它说有税务机关为这条担保")

    def test_tax_paper_is_c_though_its_name_carries_tax(self):
        """正向机关字形的用处：「中国税务报」带"税务"两字，但它是报纸。

        谁把 `ORG_NAME_RE` 放宽成"署名里含'税务'就算机关"，这一条就翻成 A1——
        媒体署名拿到最高档，比拿到 A2 更难被发现。
        """
        self.assertEqual(tax_cases.source_grade(row(publisher="中国税务报"), "曝光典型"), "C")
        self.assertFalse(tax_cases.ORG_NAME_RE.search("中国税务报"))

    def test_head_office_is_a1_and_local_bureau_is_a2(self):
        for pub in ("国家税务总局办公厅", "国家税务总局", "税务总局新媒体"):
            self.assertEqual(tax_cases.source_grade(row(publisher=pub), "查处通报"), "A1",
                             f"{pub} 是本级")
        for pub in ("国家税务总局河北省税务局", "广西壮族自治区地方税务局", "北京市税务局"):
            self.assertEqual(tax_cases.source_grade(row(publisher=pub), "查处通报"), "A2",
                             f"{pub} 带行政区划，不是本级")

    def test_title_head_carries_the_issuer_when_publisher_is_blank(self):
        """接口只在 publisher 栏和标题冒号前那一段写发布方，两处都得用。"""
        self.assertEqual(tax_cases.source_grade(row(
            title="河南省税务部门查处一起虚开发票团伙骗取留抵退税案件",
            publisher=""), "查处通报"), "A2", "标题点名河南省税务部门，这一条不是本级发布")
        self.assertEqual(tax_cases.source_grade(row(
            title="税务总局曝光2起自然人纳税人偷逃税案件", publisher=""), "曝光典型"), "A1")
        self.assertEqual(tax_cases.issuer_of(row(title="灵活用工平台虚开发票被查处",
                                                 publisher="")),
                         "灵活用工平台虚开发票被查处",
                         "issuer_of 只回字样，判档由 source_grade 做——别在这里加筛子")

    def test_reprint_prefix_in_the_title_is_c(self):
        g = tax_cases.source_grade(row(
            title="[中国新闻社] 2023年中国税务部门查处涉嫌违法纳税人13.5万户",
            publisher=""), "查处通报")
        self.assertEqual(g, "C")
        # 转载前缀里写的是原发源，比 publisher 栏（实测里那条挂着「税务总局新媒体」）可信
        self.assertEqual(tax_cases.media_name(row(
            title="[中国政府网]税务部门加大税收违法“黑名单”公布力度",
            publisher="税务总局新媒体")), "中国政府网")

    def test_unnamed_row_gets_a1_only_inside_an_official_case_column(self):
        """读不出发布方：官方案例栏目按栏目名义公布，算本级；认不出子栏目落 C。"""
        r = row(title="灵活用工平台虚开发票被查处", publisher="")
        self.assertEqual(tax_cases.source_grade(r, "违法公布"), "A1")
        self.assertEqual(tax_cases.source_grade(r, tax_cases.OTHER_SUBTYPE[0]), "C",
                         "把读不出写成 A2，就是凭空给一条报道加上机关背书")
        self.assertEqual(tax_cases.CASE_SUBTYPE_NAMES,
                         {"查处通报", "曝光典型", "违法公布"},
                         "这个集合等于 SUBTYPES 的三档名，扩栏目要同步")

    def test_every_snapshot_row_is_graded_and_stays_consistent(self):
        """整表扫：媒体署名行不许进 A 档，且等级与核验状态两处判断必须同一口径。

        等级在 `make_entry` 算、核验状态在 `verify_note` 里另算一次，两处的 subtype
        参数只要漏传一个，台账就会出现「A1 + 发布方读不出」这种自相矛盾的行。
        """
        c_forms = ("发布方是媒体", "待核：发布方两处字段都读不出机关")
        media_rows = 0
        graded = 0
        for r in ROWS:
            info = tax_cases.classify_row(r)
            if not info["keep"]:
                continue
            graded += 1
            grade = tax_cases.source_grade(r, info["subtype"])
            note = tax_cases.verify_note(r, info["subtype"])
            self.assertIn(grade, ("A1", "A2", "C"))
            self.assertEqual(grade == "C", note.startswith(c_forms),
                             f"{r['title'][:24]} 等级 {grade} 与核验状态「{note[:16]}」对不上")
            if tax_cases.is_media(r):
                media_rows += 1
                self.assertEqual(grade, "C", f"媒体署名的 {r['title'][:24]} 进了 {grade} 档")
        self.assertGreaterEqual(graded, 40, "快照留行数骤减，这一组检查就空转了")
        self.assertGreaterEqual(media_rows, 8,
                                f"快照里媒体署名行只有 {media_rows} 条，不足以钉住判据")

    def test_ledger_rows_are_graded_with_their_own_subtype(self):
        """经 `collect` 跑真链路：无署名行落在违法公布栏，等级与说法都得是本级原文。"""
        target = row(title="灵活用工平台虚开发票被查处", publisher="",
                     url="http://www.chinatax.gov.cn/chinatax/n810219/c102435/"
                         "c5000099/content.html")

        def fake(word, page=1, **kwargs):
            return {"total": 1, "results": [target] if page == 1 else []}

        out = tax_cases.collect("灵活用工平台虚开发票", fetch=fake)
        entry = out["candidates"][0]
        self.assertEqual(entry["来源等级"], "A1")
        self.assertIn("官方站内原文", entry["核验状态"])
        self.assertEqual(entry["机关"], "未标明")


# ── 4 发出去的维度 ───────────────────────────────────────────

class TestDimensionsSent(unittest.TestCase):
    def test_every_request_sends_the_case_column_and_no_file_label(self):
        """台账两条落进请求形状：label 必须是空（非十类文件名的 label 硬回 0 条），
        column 必须是 5741（不收窄就被新闻与各地动态挤满）。"""
        rec = _Recording()
        tax_cases.collect("虚开发票", ["查处"], mode="recent", fetch=rec)
        self.assertTrue(rec.calls, "一条请求都没发")
        for c in rec.calls:
            self.assertEqual(c["column"], tax_cases.CASE_COLUMN)
            self.assertEqual(c["column"], "5741",
                             "栏目号是实测值，别改成变量名一样的占位")
            self.assertFalse(c["file_only"],
                             "file_only=True 会发十个文件类标签，案例一条也取不到")

    def test_pages_only_deepens_the_result_word_round(self):
        """翻页只加在结果词那一轮，且取到空页就停——空页继续翻是白跑。"""
        words = [f"虚开发票 {w}" for w in tax_cases.ADVERSE_WORDS[:2]]
        rec = _Recording({(word, page): [row(url=("http://www.chinatax.gov.cn/"
                                                 f"chinatax/n810219/c102025/c500{page}1/content.html"))]
                          for word in words for page in (1, 2)})
        tax_cases.collect("虚开发票", ["查处"], mode="recent", pages=2, fetch=rec)
        pages = {}
        for c in rec.calls:
            pages[c["page"]] = pages.get(c["page"], 0) + 1
        self.assertEqual(pages.get(2), 2,
                         "只有处理结果词那一轮翻第二页，其余轮翻不到")
        self.assertEqual(pages.get(1), 3, "全要素一轮加结果词两轮，各翻第一页")


# ── 5 台账组装（现场快照跑真链路）────────────────────────────

class TestLedgerFromSnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tax_cases.collect("税收违法", ["曝光"], mode="recent",
                                    fetch=fixture_fetch)
        cls.dups = tax_cases.collect("虚开发票 查处（全站，含双主机重复）",
                                     mode="recent", fetch=fixture_fetch)
        cls.files = tax_cases.collect("重大税收违法案件", mode="recent",
                                      fetch=fixture_fetch)

    def test_snapshot_covers_the_measured_queries(self):
        self.assertEqual(SNAPSHOT_AT, "2026-10-04")
        self.assertEqual(len(ROWS), 50, "快照应是 5 组检索词各 10 行")
        self.assertIn("虚开发票 查处", SNAPSHOT_WORDS)

    def test_ledger_row_fields_are_all_filled(self):
        need = {"标题", "链接", "日期", "机关", "子类型", "来源等级", "材料层级",
                "案号/文号", "事实摘要", "要素命中", "检索轮次", "检索式", "核验状态",
                "重复次数"}
        for e in self.out["candidates"]:
            self.assertEqual(set(e), need, f"台账字段变了：{sorted(set(e) ^ need)}")
            self.assertTrue(e["标题"] and e["链接"] and e["日期"])

    def test_missing_case_number_is_written_as_none_not_left_blank(self):
        """留空读起来像"还没查这一栏"，写"无"才是"看过并且没有"。"""
        vals = {e["案号/文号"] for e in self.out["candidates"]}
        self.assertIn("无", vals)
        self.assertNotIn("", vals, "案号/文号 不许留空串")

    def test_weight_beats_date_in_the_sort(self):
        """排序键是 要素命中数 × 子类型权重，日期只在同分时决胜。

        快照里 曝光典型（权重 3）那条是 2025-03-17，查处通报（权重 2）里有
        2026-07-29，其他新闻（权重 1）里有 2024-01-19。谁把日期提到第一键，
        这三条的先后就会反过来。
        """
        got = [(e["子类型"], e["日期"]) for e in self.out["candidates"]]
        self.assertEqual(got[0], ("曝光典型", "2025-03-17"), f"首位应是权重最高的案例：{got}")
        self.assertEqual(got[1], ("查处通报", "2026-07-29"))
        self.assertLess(got.index(("曝光典型", "2025-03-17")),
                        got.index(("其他新闻", "2024-01-19")))

    def test_sort_is_recomputable_from_the_two_visible_keys(self):
        scores = [(len(e["要素命中"]) * tax_cases.SUBTYPE_WEIGHT[e["子类型"]], e["日期"])
                  for e in self.out["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         "台账顺序必须能用这两个键重算出来，不掺模型打分")

    def test_element_hit_is_literal_and_auditable(self):
        """要素命中只能是该条目自己带过的字面，不是打分也不是推断。

        「事实摘要」截到 120 字，所以按快照原行复核；截断本身由
        `test_summary_is_truncated_for_the_ledger` 钉住。
        """
        by_id = {tax_cases.content_id(r["url"]): r for r in ROWS}
        for e in self.out["candidates"]:
            src = by_id[tax_cases.content_id(e["链接"])]
            hay = f"{src['title']} {src['snippet']}"
            for hit in e["要素命中"]:
                self.assertIn(hit, hay,
                              f"{e['标题'][:20]} 的要素命中 {hit!r} 复核不了")

    def test_summary_is_truncated_for_the_ledger(self):
        for e in self.out["candidates"]:
            self.assertLessEqual(len(e["事实摘要"]), 120)

    def test_cross_host_duplicate_collapses_into_one_row_with_a_count(self):
        """同一起案件在 fgk 与 www 各回一行：不去重就占掉两个候选位。"""
        dup = [e for e in self.dups["candidates"] if e["重复次数"] > 1]
        self.assertEqual(len(dup), 1, "快照里 c5245915 在两台主机各回一行")
        self.assertEqual(dup[0]["重复次数"], 2)
        self.assertEqual(dup[0]["标题"], "灵活用工平台虚开发票被查处")
        self.assertEqual(self.dups["candidate_count"], 9,
                         "10 行里有一对重复，候选应是 9 条不是 10 条")
        ids = [tax_cases.content_id(e["链接"]) for e in self.dups["candidates"]]
        self.assertEqual(len(ids), len(set(ids)), "去重后不该还有同一个正文 id")

    def test_exclusion_reason_is_one_of_three_deterministic_rules(self):
        for d in self.files["excluded"]:
            self.assertTrue(d["标题"] and d["理由"])
            self.assertTrue(("文号" in d["理由"] or "法规库" in d["理由"]
                             or "文件与解读" in d["理由"]),
                            f"排除理由不在三条规则里：{d['理由']}")
        self.assertEqual(self.files["candidate_count"], 3,
                         "这一组里只有汇总公布页与两条新闻是案例，其余是文件")

    def test_as_of_drops_later_cases_and_says_the_deadline(self):
        out = tax_cases.collect("虚开发票", ["查处"], mode="recent",
                                as_of="2023-12-31", fetch=fixture_fetch)
        self.assertEqual(out["candidate_count"], 8)
        self.assertEqual(len(out["excluded"]), 2)
        for d in out["excluded"]:
            self.assertIn("2023-12-31", d["理由"])
        for e in out["candidates"]:
            self.assertLessEqual(e["日期"], "2023-12-31")
        self.assertEqual(out["as_of"], "2023-12-31")

    def test_limit_truncates_the_list_but_not_the_count(self):
        out = tax_cases.collect("虚开发票", ["查处"], mode="recent",
                                limit=3, fetch=fixture_fetch)
        self.assertEqual(len(out["candidates"]), 3)
        self.assertEqual(out["candidate_count"], 10,
                         "条数要报全部候选，否则'共 3 条'会被读成官方只公布过 3 件")

    def test_round_report_keeps_total_and_fetched_apart(self):
        rounds = tax_cases.collect("虚开发票", ["查处"], mode="recent",
                                   fetch=fixture_fetch)["rounds"]
        first = [r for r in rounds if r["word"] == "虚开发票 查处"][0]
        self.assertEqual(first["reported_total"], 10)
        self.assertEqual(first["fetched"], 10)
        self.assertEqual(first["source"], "chinatax")
        # 空那两轮也留痕，零命中与没发是两件事
        empty = [r for r in rounds if r["word"].endswith("追缴")][0]
        self.assertEqual(empty["fetched"], 0)


class TestZeroResultAndFailure(unittest.TestCase):
    def test_no_hit_is_reported_as_no_support_with_the_queries_listed(self):
        out = tax_cases.collect("一条库里没有的说法", mode="recent",
                                fetch=fixture_fetch)
        self.assertEqual(out["case_support"], "无")
        self.assertEqual(out["candidates"], [])
        self.assertTrue(out["round_queries"], "零结果也要交出这一趟发了哪几条")
        self.assertEqual(out["fetch_failures"], [])
        self.assertIn("官方处理口径", " ".join(out["limitations"]))

    def test_fetch_failure_is_not_recorded_as_empty(self):
        """取数失败与库里没有分不开，就会把接口故障读成"这类案件官方没公布过"。"""
        def boom(word, page=1, **kw):
            return {"keyword": word, "results": [], "_error": "连接超时（已重试 2 次）"}

        out = tax_cases.collect("虚开发票", ["查处"], mode="recent", fetch=boom)
        self.assertEqual(out["case_support"], "无")
        self.assertEqual(len(out["fetch_failures"]), 3,
                         "三条检索式各失败一次，一次都不能少记")
        self.assertTrue(all(f["error"] for f in out["fetch_failures"]))
        self.assertNotIn("没有", " ".join(f["error"] for f in out["fetch_failures"]))

    def test_sources_and_limitations_survive_a_zero_result(self):
        out = tax_cases.collect("虚开发票", ["查处"], mode="recent",
                                fetch=fixture_fetch)
        self.assertEqual(out["sources"], ["chinatax.gov.cn search5（column=5741，label 空）"])
        self.assertGreaterEqual(len(out["limitations"]), 4)


# ── 6 第二路（税屋）：默认不发，发了要标明是转载 ──────────────

class TestShui5Leg(unittest.TestCase):
    JUDG = {"title": "某公司诉某市税务局稽查局行政处罚决定案判决书",
            "url": "https://www.shui5.cn/article/8/12345.html", "date": "2024-06-01",
            "snippet": "法院认为……", "publisher": "", "source_label": "实务解读"}
    PLAIN = {"title": "研发费用加计扣除常见风险点解析",
             "url": "https://www.shui5.cn/article/9/23456.html", "date": "2024-05-01",
             "snippet": "", "publisher": "", "source_label": "实务解读"}

    def _out(self, with_leg=True):
        def shui5(word):
            return {"results": [self.JUDG, self.PLAIN], "total": 2}
        return tax_cases.collect("研发费用加计扣除", ["混岗工时"], mode="recent",
                                 fetch=fixture_fetch,
                                 shui5_fetch=shui5 if with_leg else None)

    def test_off_by_default_says_the_leg_was_not_run(self):
        out = self._out(False)
        self.assertIn(tax_cases.SHUI5_OFF_NOTE, out["limitations"])
        self.assertEqual(out["sources"], [out["sources"][0]])
        self.assertNotIn("shui5", [r["source"] for r in out["rounds"]])

    def test_on_leg_is_graded_C_and_flagged_as_second_hand(self):
        out = self._out(True)
        entries = {e["标题"]: e for e in out["candidates"]}
        j = entries[self.JUDG["title"]]
        self.assertEqual(j["来源等级"], "C")
        self.assertEqual(j["子类型"], tax_cases.SHUI5_SUBTYPE)
        self.assertIn("C1/C3", j["材料层级"], "标题是判决书形态就该给到 C1/C3 那一档")
        self.assertIn("二手转载", j["核验状态"])
        p = entries[self.PLAIN["title"]]
        self.assertEqual(p["材料层级"], "C4 实务站转载")
        self.assertNotIn(tax_cases.SHUI5_OFF_NOTE, out["limitations"])
        self.assertEqual(len(out["sources"]), 2)

    def test_shui5_rows_do_not_outrank_official_columns(self):
        out = self._out(True)
        self.assertEqual(tax_cases.SUBTYPE_WEIGHT[tax_cases.SHUI5_SUBTYPE],
                         tax_cases.SUBTYPE_WEIGHT[tax_cases.OTHER_SUBTYPE[0]],
                         "实务站转载应与'其他新闻'同档，排到官方栏目件后面")

    def test_shui5_failure_is_attributed_to_that_leg(self):
        def down(word):
            return {"results": [], "_error": "360 返回访问异常页"}
        out = tax_cases.collect("虚开发票", ["查处"], mode="recent",
                               fetch=fixture_fetch, shui5_fetch=down)
        self.assertTrue(out["fetch_failures"])
        self.assertTrue(all(f["error"].startswith("税屋：")
                            for f in out["fetch_failures"]),
                        "360 那一路挂了不能报成总局没有案例")
        self.assertGreater(out["candidate_count"], 0,
                           "第二路失败不影响第一路台账")


# ── 7 输出层：边界那句话必须到读者眼前 ────────────────────────

class TestRender(unittest.TestCase):
    def test_render_names_the_strength_ceiling(self):
        """用户要的边界诚实声明：这一层给的是官方处理口径，不是司法裁判口径。"""
        out = tax_cases.collect("税收违法", ["曝光"], mode="recent",
                                fetch=fixture_fetch)
        text = tax_cases.render(out)
        self.assertIn("官方处理口径", text)
        self.assertIn("司法裁判口径", text)
        self.assertIn("不占 L1–L4 位次", text)
        self.assertIn("类案支持：有", text)
        self.assertIn("裁判文书网", text, "没覆盖判决这件事要说出口，不能只写在源码里")

    def test_render_zero_result_is_a_report_not_a_blank(self):
        out = tax_cases.collect("一条库里没有的说法", mode="recent",
                                fetch=fixture_fetch)
        text = tax_cases.render(out)
        self.assertIn("本轮没有留下候选案例", text)
        self.assertIn("第 1 轮", text)
        self.assertIn("局限：", text)

    def test_render_keeps_exclusion_reasons_and_failure_lines(self):
        out = tax_cases.collect("重大税收违法案件", mode="recent",
                                fetch=fixture_fetch)
        text = tax_cases.render(out)
        self.assertIn("排除 7 条", text)
        self.assertIn("带文号", text)

        def boom(word, page=1, **kw):
            return {"results": [], "_error": "HTTP 503"}
        text2 = tax_cases.render(tax_cases.collect("虚开发票", mode="recent", fetch=boom))
        self.assertIn("取数失败 3 轮", text2)
        self.assertIn("不是「库里没有」", text2)

    def test_json_shape_is_stable_for_the_template_layer(self):
        out = tax_cases.collect("虚开发票", ["查处"], mode="recent",
                                fetch=fixture_fetch)
        s = json.dumps(out, ensure_ascii=False)   # 台账直接进答案，不能编码失败
        self.assertEqual(json.loads(s)["candidate_count"], out["candidate_count"])
        for key in ("topic", "mode", "round_queries", "candidates", "candidate_count",
                    "excluded", "sources", "case_support", "limitations",
                    "fetch_failures", "columns"):
            self.assertIn(key, s)


class TestCommandLine(unittest.TestCase):
    """这一组不许碰网络：把默认取数出口换成会喊的桩。

    不这样做，一次参数校验漏网就变成真发出去的检索——测试挂在门禁里没人看见，
    用户却为此付了请求。上一版就是靠这条才发现 `--as-of 2024-1-1` 被放过、
    整条时点过滤静默失效（见 `check_as_of`）。
    """

    def setUp(self):
        self._real = tws.search_chinatax

        def no_network(word, **kw):
            raise AssertionError(f"用例发出了真请求：{word!r}")

        tws.search_chinatax = no_network       # collect 调用时才取这个属性

    def tearDown(self):
        tws.search_chinatax = self._real

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = tax_cases.main(argv)
        return code, buf.getvalue()

    def test_no_topic_prints_help_and_exits_two(self):
        code, text = self._run([])
        self.assertEqual(code, 2)
        self.assertIn("--element", text)

    def test_non_padded_or_impossible_as_of_exits_two(self):
        """`strptime` 收 2024-1-1，而这一栏要跟补零的条目日期做字符串比较：
        放过去就是过滤静默失效，比报错坏得多。"""
        for bad in ("2024-1-1", "2024-13-01", "2024-02-30", "昨天"):
            code, text = self._run(["虚开发票", "--as-of", bad])
            self.assertEqual(code, 2, f"--as-of {bad} 竟然通过了校验")
            self.assertIn("YYYY-MM-DD", text)

    def test_order_flag_is_checked_before_the_first_request(self):
        code, text = self._run(["虚开发票", "--order", "not-a-sort"])
        self.assertEqual(code, 1, "非法排序应报退码 1 并给出原因，不是抛栈")
        self.assertIn("order", text)


# ── 8 两根轴不许混用 ─────────────────────────────────────────

class TestOrthogonality(unittest.TestCase):
    def test_case_grades_are_not_legal_ranks(self):
        ranks = set(tax_evidence.LEGAL_RANK) | set(tax_evidence.RANK_LABEL)
        labels = set(tax_evidence.RANK_LABEL.values())
        used_levels = set(tax_cases.LEVEL_BY_SUBTYPE.values())
        for v in used_levels:
            self.assertNotIn(v, labels, f"材料层级 {v!r} 与位阶标签撞名，读的一方会混")
            self.assertNotIn(v, ranks)
        for key in ranks:
            self.assertNotIn("案例", str(key))
        self.assertFalse(any("案例" in v for v in labels),
                         "有人把案例加进了效力位阶——它和谁能定是两根轴")

    def test_c1_is_never_produced_by_the_chinatax_leg(self):
        """C1（判决/裁定）这一层只有税屋那一路可能给，且要按标题判、标'待核'。"""
        values = set(tax_cases.LEVEL_BY_SUBTYPE.values())
        self.assertFalse(any(v.startswith("C1") for v in values),
                         "总局站内通道给不出判决书，标了 C1 就是假装覆盖")
        self.assertTrue(all(v.startswith(("C2", "C3", "C4")) for v in values))

    def test_module_does_not_import_or_call_the_grading_layer(self):
        """判的是 import 与调用，不是提没提——docstring 里讲正交关系必须提名字。"""
        src = (SCRIPTS_DIR / "tax_cases.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*(import|from)\s+tax_evidence", src, re.M),
                          "案例层一旦 import 定级层，就会被塞进 pick_primary 排队")
        self.assertIsNone(re.search(r"pick_primary\s*\(", src),
                          "调了挑选主依据那个函数，案例就进了依据层")

    def test_fixture_rows_never_get_a_rank_label_as_their_grade(self):
        out = tax_cases.collect("税收违法", ["曝光"], mode="recent",
                                fetch=fixture_fetch)
        self.assertLessEqual({e["来源等级"] for e in out["candidates"]},
                             {"A1", "A2", "C"})
        self.assertLessEqual({e["材料层级"] for e in out["candidates"]},
                             set(tax_cases.LEVEL_BY_SUBTYPE.values()))


# ── 9 台账文档契约 ───────────────────────────────────────────

LEDGER_SECTION = "## 案例通道的实测台账"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COUNT_RE = re.compile(r"(×\s*\d+)|(\d+\s*(?:条|组|次|页|个|字节))")


def _ledger_rows():
    md = LEDGER_DOC.read_text(encoding="utf-8")
    start = md.index(LEDGER_SECTION)
    body = md[start + len(LEDGER_SECTION):]
    stop = body.find("\n## ")
    rows = []
    for line in body[:stop if stop >= 0 else None].splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 5 or cells[0].startswith("判据") or set(cells[0]) <= set("-: "):
            continue
        rows.append(cells)
    return md, rows


class LedgerContract(unittest.TestCase):
    """`source_defects.md` 那一节自称"每行带齐三字段、由本用例逐格查"，这里就是它自己。"""

    @classmethod
    def setUpClass(cls):
        cls.md, cls.rows = _ledger_rows()

    def test_section_is_registered_in_the_toc(self):
        self.assertIn(LEDGER_SECTION, self.md)
        self.assertIn("](#案例通道的实测台账)", self.md, "目录里那一条被删了")

    def test_columns_are_the_five_named_in_order(self):
        head = [ln for ln in self.md.splitlines() if ln.strip().startswith("| 判据（谁在用）")]
        self.assertEqual(len(head), 1, "台账表头丢了或重复了")
        self.assertEqual([c.strip() for c in head[0].strip("|").split("|")],
                         ["判据（谁在用）", "入口", "最近实测日期", "实测方法", "当时结果"])

    def test_at_least_six_rows_and_no_empty_cell(self):
        self.assertGreaterEqual(len(self.rows), 6,
                                f"台账只剩 {len(self.rows)} 行，通道判据不止这几条")
        for i, row in enumerate(self.rows, 1):
            for j, cell in enumerate(row):
                self.assertTrue(cell, f"台账第 {i} 行第 {j + 1} 列是空的")

    def test_every_row_is_dated_and_counts_by_shape(self):
        for row in self.rows:
            self.assertTrue(DATE_RE.match(row[2]),
                            f"「{row[0][:24]}」的实测日期格是 {row[2]!r}，不是 ISO 日期")
            self.assertTrue(COUNT_RE.search(row[4]),
                            f"「{row[0][:24]}」的当时结果没有逐项计数：{row[4][:60]!r}")

    def test_claims_that_were_not_remeasured_say_so(self):
        """既有记录里的旧说法：冒充本轮数据要报红，悄悄删掉也要报红。

        探针没有「税收政策栏／互动」那两条请求（栏目号没取到），所以那两个数字
        在任何一天都不可能是本轮量出来的。只写"含未复测的行必须解释"挡不住
        反向写法——把"未复测"三个字删了，那两个数就变成有日期的数据了。
        """
        for row in self.rows:
            if "未复测" not in row[4]:
                continue
            self.assertIn("没取到", row[4], f"「{row[0][:24]}」写了未复测却没写为什么没测")
        unmeasured = [r for r in self.rows if "税收政策栏" in r[4]]
        self.assertEqual(len(unmeasured), 1,
                         "「税收政策栏仅 7、互动 8」这条旧说法被删掉了：读者会以为"
                         "案例集中在新闻发布栏这件事被逐栏比过")
        self.assertIn("未复测", unmeasured[0][4],
                      "旧栏数没带「未复测」就成了有日期的本轮数据")
        self.assertIn("785", self.md, "记录值与本轮量值不一致时，两个数都要留下")
        self.assertIn("828", self.md)

    def test_symbol_names_in_the_ledger_exist(self):
        """文档只能引用可 grep 的本机符号；写成 `tax_cases.dedupe` 那种就没人复查得了。"""
        for m in re.finditer(r"`(tax_cases|tax_web_search)\.([A-Za-z_][A-Za-z0-9_]*)",
                             self.md):
            mod = {"tax_cases": tax_cases, "tax_web_search": tws}[m.group(1)]
            self.assertTrue(hasattr(mod, m.group(2)),
                            f"台账引用了不存在的 {m.group(1)}.{m.group(2)}")

    def test_column_and_subtype_constants_agree_with_the_ledger(self):
        row = [r for r in self.rows if "column" in r[0] or "5741" in r[4]
               or "栏目维" in r[0]]
        self.assertTrue(row, "台账里没有栏目维那一行")
        self.assertIn(tax_cases.CASE_COLUMN, " ".join(r[4] for r in row),
                      f"代码发的是 {tax_cases.CASE_COLUMN}，台账里的数不是它")
        sub = [r for r in self.rows if "SUBTYPES" in r[0]]
        self.assertEqual(len(sub), 1, "子栏目代号那一行应当恰好一条")
        for code in tax_cases.SUBTYPES:
            self.assertIn(code, sub[0][4], f"{code} 有代号没进台账")
        for code in re.findall(r"c\d{6}", sub[0][4]):
            self.assertIn(code, tax_cases.SUBTYPES,
                          f"台账列了 {code}，代码的子类型表里没有它")
        adv = [r for r in self.rows if "ADVERSE_WORDS" in r[0]]
        self.assertTrue(adv, "没有一行说明检索词取词的实测，取词表就成了没人证的约定")

    def test_probe_plan_is_the_count_the_ledger_claims(self):
        spec = importlib.util.spec_from_file_location(
            "probe_case_channel", str(TESTS_DIR / "probe_case_channel.py"))
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        plan = probe.requests_plan()
        m = re.search(r"真发\s*(\d+)\s*条", self.md)
        self.assertTrue(m, "引言里那句「真发 N 条」不见了")
        self.assertEqual(len(plan), int(m.group(1)),
                         f"台账说真发 {m.group(1)} 条，探针计划是 {len(plan)} 条")
        self.assertLessEqual(len(plan), probe.MAX_REQUESTS,
                             "计划超过自卡的上限，探针跑起来会自己拒绝，台账那句就成了空话")
        kinds = {k for k, _d, _s in plan}
        for need in ("全站", "栏目收窄", "标签维", "日期序", "直连清单", "检索词形态"):
            self.assertIn(need, kinds, f"探针不再量「{need}」这一维，台账那一行就该改")


# ── 10 四处挂线：文档得指得到这一层，且说的是同一件事 ───────────

SKILL_DOC = ROOT / "SKILL.md"
README_DOC = ROOT / "README.md"
REF_DIR = ROOT / "references"


def _section(md: str, head: str) -> str:
    """取某个二级标题到下一个真实二级标题之间的正文，标题按前缀匹配。

    围栏里的 `## [问题] — …` 是产出物的行、不是本文件的章节：按它切会把模板正文
    截掉，条件块那些行就永远查不到。所以切点只认围栏外的标题。
    """
    out, started, in_fence = [], False, False
    for ln in md.splitlines(True):
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            if started:
                out.append(ln)
            continue
        if not in_fence and ln.strip().startswith("## "):
            if ln.strip()[3:].strip().startswith(head):
                started = True
                out.append(ln)
                continue
            if started:
                break
        elif started:
            out.append(ln)
    return "".join(out)


def _missing(hay: str, tokens) -> list:
    """还缺哪几个字样。写成"缺哪些"而不是"在不在"，是为了让变异自检能比列表。"""
    return [t for t in tokens if t not in hay]


# 每一处挂线各钉哪几句。这些串都是"删掉或改写就会让下一步走错"的话，不是修辞：
# 索引行少了「不占 L1–L4」，读的人会把它当依据排队；⑥ 少了强度上限那句，答案里
# 就会出现"法院也是这么判的"；模板少了可类比/不可外推两行，台账就变成装饰。
HOOKS = {
    "索引行": ("scripts/tax_cases.py", "不占 L1–L4 位次"),
    "① 触发": ("⑥ 前追加", "`references/classification.md`「哪几类题要追加类案检索」"),
    "⑥ 定性": ("支持层不是法定依据", "不占 L1–L4 位次", "官方处理口径",
               "司法裁判口径", "按目标年度重验"),
}


class TestDocHooks(unittest.TestCase):
    """四处挂线：SKILL 索引与 ①⑥、判型下沉文档、定级文档、模板、README。"""

    @classmethod
    def setUpClass(cls):
        cls.skill = SKILL_DOC.read_text(encoding="utf-8")
        cls.cls_md = (REF_DIR / "classification.md").read_text(encoding="utf-8")
        cls.grade_md = (REF_DIR / "evidence_grading.md").read_text(encoding="utf-8")
        cls.tmpl = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        cls.readme = README_DOC.read_text(encoding="utf-8")

    def test_index_row_points_at_the_script_and_states_the_rank_rule(self):
        rows = [ln for ln in self.skill.splitlines()
                if ln.strip().startswith("|") and "tax_cases.py" in ln]
        self.assertEqual(len(rows), 1, f"快速索引里 tax_cases 有 {len(rows)} 行")
        self.assertEqual(_missing(rows[0], HOOKS["索引行"]), [])

    def test_1_names_the_three_types_and_points_at_the_trigger_doc(self):
        body = _section(self.skill, "①")
        self.assertEqual(_missing(body, HOOKS["① 触发"]), [])
        for t in ("liability", "entitlement", "compare"):
            self.assertIn(t, body, f"① 的触发条件没点 {t}")
        heads = [ln[3:].strip() for ln in self.cls_md.splitlines()
                 if ln.startswith("## ") and not ln.startswith("## 目录")]
        self.assertIn("哪几类题要追加类案检索", heads,
                      "① 指的这一节在 classification.md 里不存在，指针断了")

    def test_trigger_doc_lists_exactly_the_words_the_code_sends(self):
        """判型文档把"什么写法算争点"交给了取词表，那就必须逐项对得上。"""
        sec = _section(self.cls_md, "哪几类题要追加类案检索")
        self.assertTrue(sec)
        for w in tax_cases.ADVERSE_WORDS:
            self.assertIn(w, sec, f"代码发「{w}」这一轮，文档却没说这种写法算争点")
        self.assertIn("risk", sec, "没交代 risk 为什么不发这一轮，读的人会以为漏了")
        self.assertIn("不发这一轮", sec)

    def test_6_states_support_layer_ceiling_and_remap(self):
        self.assertEqual(_missing(_section(self.skill, "⑥"), HOOKS["⑥ 定性"]), [])

    def test_grading_doc_owns_the_section_and_names_real_symbols(self):
        sec = _section(self.grade_md, "案例材料不占位次")
        self.assertTrue(sec, "定级文档里「案例材料不占位次」这一节没了——两根轴的正交关系没人写")
        for sym in ("tax_cases.source_grade", "tax_cases.LEVEL_BY_SUBTYPE"):
            self.assertIn(sym, sec, f"这一节没引用 {sym}")
            self.assertTrue(hasattr(tax_cases, sym.split(".")[1]), f"{sym} 不存在")
        self.assertIn("不进 `LEGAL_RANK`", sec)
        for note in ("REMAP_NOTE", "LIMITATIONS"):
            self.assertIn(note, sec, f"旧案重验/覆盖边界没写 {note}，读者不知道这些话由谁给")
            self.assertTrue(hasattr(tax_cases, note))

    def test_template_carries_the_conditional_block(self):
        """模板正文也提"可类比点/不可外推点"，所以这里钉的是围栏里那两行的写法。

        只按词在不在文件里判就是假绿：把模板里的取值行删掉，散文那句还会让检查
        照样过（变异 K 第一次就是这么绿的）。
        """
        sec = _section(self.tmpl, "分析六段式")
        for tok in ("**类案支持**", "可类比点：[", "不可外推点：[", "零候选",
                    "覆盖了哪些源", "司法裁判口径", "tax_cases.collect"):
            self.assertIn(tok, sec, f"六段式里缺「{tok}」——台账进答案的形状没人定")

    def test_readme_registers_the_layer(self):
        row = [ln for ln in self.readme.splitlines()
               if ln.startswith("| **类案支持层**")]
        self.assertEqual(len(row), 1, "README 的代码构成表里没有这一层")
        for tok in ("tax_cases.py", "L1–L4", "`pick_primary`"):
            self.assertIn(tok, row[0])

    def test_mutation_dropping_the_ceiling_is_caught(self):
        """自检：把模板里强度上限那一行删掉，六段式那条判据必须报缺。"""
        md = self.tmpl
        broken = md.replace("- 强度上限：官方处理口径，不是司法裁判口径", "- 参考：同类案件", 1)
        self.assertNotEqual(broken, md, "变异没落到模板那一行上")
        self.assertEqual(_missing(_section(md, "分析六段式"),
                                  ("官方处理口径", "司法裁判口径")), [])
        self.assertEqual(_missing(_section(broken, "分析六段式"), ("司法裁判口径",)),
                         ["司法裁判口径"], "删掉强度上限后判据不会报红")

    def test_mutation_6_rewritten_as_legal_basis_is_caught(self):
        """自检：⑥ 把"支持层不是法定依据"改成"法定依据之一"，定性判据必须报缺。"""
        skill = self.skill
        broken = skill.replace("类案是**支持层不是法定依据**", "类案是**法定依据之一**", 1)
        self.assertNotEqual(broken, skill, "变异没落到 ⑥ 那句定性上")
        self.assertEqual(_missing(_section(skill, "⑥"), HOOKS["⑥ 定性"]), [])
        self.assertIn("支持层不是法定依据",
                      _missing(_section(broken, "⑥"), HOOKS["⑥ 定性"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
