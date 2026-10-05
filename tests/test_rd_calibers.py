#!/usr/bin/env python3
"""研发子技能三套口径对照的离线用例。不联网、不调模型，只读 markdown。

三套口径指 会计核算／高新技术企业认定／研发费用加计扣除 三条归集规定，不是
`tax_evidence.LEGAL_RANK`（那根轴量的是发文机关层级，回答"谁能定"；口径量的是同一笔
费用按哪份规定归集，回答"算不算、算多少"）。同一笔研发费在三套口径下取值不同，是高企
稽查与加计核查里最高频的争议，所以这份对照值得当回归对象。

守的四类问题：

  1. **基准唯一**。科目级取值只住在 `references/rd-mgmt-methodology.md` 第 4.1 节，
     `deduction-guide.md` 第四节、`routing-table.md`、`workflow-evidence.md`、
     `audit-response.md` 都只指过去。分成两份就会各说各话——本轮修的正是这一类：
     旧 4.1 注写"高企与加计其他费用都限 10%"，而同文件第 6.3 节与 195 号原文都是 20%。
  2. **取值与原文一致**。表里的关键数值要能在本地政策原文里逐字找到：195 号文的 20%、
     委托外部研发 80%、科技人员 183 天、在用建筑物折旧，执行指引 2.0 的 2/3 境外委托
     限额与正列举，28 号公告的统一计算，40 号公告的股权激励与结转冲减。
     表漂了或原文换了说法，这里报红。
  3. **科目不缺行**。加计六类与高企八类取并集共八行，少一行就有一笔费用没人管。
     旧表缺的正是长期待摊费用与委托外部研发费用两行——高企可含而加计不含的两类差异。
  4. **隔离纪律在场**。加计与高企都是正列举、冲减只落在加计口径、跨口径不得外推，
     子技能 SKILL.md 红线与 `audit-response.md` 风险表各有一条在场。

`policy-docs/` 与 `book-*/`、`moc-*/` 是原文与转录件，不参与"自写文件不许漂"的扫描。
"""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SUB = ROOT / "subskills" / "rd-deduction-hitech"
REF = SUB / "references"
METHOD = REF / "rd-mgmt-methodology.md"
BASELINE = "第 4.1 节"

DOC_195 = REF / "policy-docs" / "高新技术企业认定管理工作指引-国科发火2016-195号.md"
GUIDE_2_0 = REF / "policy-docs" / "研发费用加计扣除政策执行指引2.0.md"
DOC_28 = REF / "policy-docs" / "税务总局公告2021-28号-进一步落实研发费用加计扣除政策.md"
DOC_40 = REF / "policy-docs" / "税务总局公告2017-40号-研发费用加计扣除归集范围.md"
DOC_119 = REF / "policy-docs" / "财税2015-119号-完善研究开发费用税前加计扣除政策.md"

# 子技能自己写的规则文件。判据只扫这些；原文与转录件不参与。
OWNED = ("SKILL.md", "NOTE.md", "references/rd-mgmt-methodology.md",
         "references/deduction-guide.md", "references/audit-response.md",
         "references/workflow-evidence.md", "references/routing-table.md",
         "references/regulatory-basis.md", "references/hitech-scoring.md")

# 只许指回基准、不许自己重写科目级取值的四份文件。
POINTER_FILES = ("references/deduction-guide.md", "references/routing-table.md",
                 "references/workflow-evidence.md", "references/audit-response.md")

# 加计六类 ∪ 高企八类
SUBJECTS = ("人员人工费用", "直接投入费用", "折旧费用", "长期待摊费用",
            "无形资产摊销", "设计试验等费用", "委托外部研发费用", "其他相关费用")

# 4.1 表的列序：0 费用项目 | 1 加计扣除 | 2 高企认定 | 3 会计 | 4 差异与争点
COL_DEDUCT, COL_HITECH, COL_ACCOUNT, COL_GAP = 1, 2, 3, 4
HEADER = "费用项目"

# 10% 只属于加计口径的限额；高企侧是 20%。散文里把两者连起来写就成了旧错误。
HITECH_WORDS = re.compile(r"高企|高新技术企业|高新认定")


def read(rel: str, texts: dict = None) -> str:
    if texts is not None:
        return texts[rel]
    return (SUB / rel).read_text(encoding="utf-8")


def section_41(text: str) -> str:
    """取 4.1 节正文，到下一个三级标题为止。"""
    start = text.index("### 4.1 ")
    rest = text[start:]
    end = rest.find("\n### ", 3)
    return rest if end < 0 else rest[:end]


def h2_body(text: str, prefix: str) -> str:
    """取某个二级标题小节的正文，到下一个二级标题或文件末尾。"""
    i = text.index(prefix)
    nxt = text.find("\n## ", i + len(prefix))
    return text[i:] if nxt < 0 else text[i:nxt]


def table_rows(section: str) -> list:
    """[(首格, 整行格串)]，跳过分隔行。"""
    rows = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append((cells[0], cells))
    return rows


def cell(rows, subject: str, col: int) -> str:
    for head, cells in rows:
        if head == subject:
            return cells[col] if col < len(cells) else ""
    raise AssertionError(f"4.1 表里没有「{subject}」这一行")


def subjects_in(rows) -> list:
    return [h for h, _ in rows if h != HEADER]


def empty_caliber_cells(rows) -> list:
    """三套口径列里有空格的 行:列。空格会被读成"这一口径不论"，而实际每格都有取值。"""
    return [f"{head}:{col}" for head, cells in rows if head != HEADER
            for col in (COL_DEDUCT, COL_HITECH, COL_ACCOUNT) if len(cells[col]) <= 1]


def shorthand_cells(rows) -> list:
    """出现"同加计扣除"式略写的 行:列——每格都要写实际取值，略写就是漂移点。"""
    return [f"{head}:{col}" for head, cells in rows if head != HEADER
            for col in (COL_DEDUCT, COL_HITECH, COL_ACCOUNT, COL_GAP)
            if "同加计" in cells[col]]


def missing_pointers(texts: dict) -> list:
    """返回没写基准指针的文件名。纯函数，变异用例直接喂内存副本。"""
    return [rel for rel in POINTER_FILES if BASELINE not in texts[rel]]


def wrong_cap_prose(texts: dict) -> list:
    """自写文件的散文里把高企其他费用上限写成 10% 的行（旧错误的具体形态）。

    只扫散文：4.1 表格里同一格的回归由 `TestValuesMatchOriginalText` 逐格盯，
    那里能精确到列，不必靠关键字猜。
    """
    bad = []
    for rel in OWNED:
        for no, line in enumerate(texts[rel].splitlines(), 1):
            if "10%" in line and "其他" in line and HITECH_WORDS.search(line) \
                    and "加计" not in line:
                bad.append(f"{rel}:{no}")
    return bad


def load_all() -> dict:
    return {rel: (SUB / rel).read_text(encoding="utf-8") for rel in OWNED}


class TestSingleBaseline(unittest.TestCase):
    """科目级取值只住一处，其余文件指过去。"""

    def test_41_declares_itself_the_baseline(self):
        self.assertIn("唯一基准", section_41(read("references/rd-mgmt-methodology.md")))

    def test_four_files_point_back(self):
        self.assertEqual([], missing_pointers(load_all()))

    def test_deduction_guide_section_four_keeps_only_extended_calibers(self):
        body = h2_body(read("references/deduction-guide.md"), "## 四、")
        self.assertIn("扩展口径对照", body)
        self.assertIn("R&D 统计", body)
        self.assertIn("IPO", body)
        self.assertNotIn("四套口径", body, "第四节标题又变回按数量指代口径")

    def test_deduction_guide_section_four_does_not_restate_the_rows(self):
        """§四 的表只装 R&D 统计与 IPO 披露两套扩展口径，不再抄八行科目取值。

        判据看表头与行首，不看正文——正文里"房屋折旧与长期待摊并入加计基数"
        这类指回基准的说法是允许的，把八行取值再列一遍才是要防的第二份基准。
        """
        body = h2_body(read("references/deduction-guide.md"), "## 四、")
        heads = [h for h, _ in table_rows(body)]
        self.assertEqual(["维度", "其他费用上限", "委托研发", "人员口径",
                          "资本化处理", "失败项目"], heads)
        for subject in SUBJECTS:
            self.assertNotIn(subject, heads, f"§四 又抄回了「{subject}」这一行的取值")


class TestValuesMatchOriginalText(unittest.TestCase):
    """表里的数值都能在本地政策原文逐字找到。"""

    @classmethod
    def setUpClass(cls):
        cls.doc_195 = DOC_195.read_text(encoding="utf-8")
        cls.guide_2_0 = GUIDE_2_0.read_text(encoding="utf-8")
        cls.doc_40 = DOC_40.read_text(encoding="utf-8")
        cls.rows = table_rows(section_41(
            read("references/rd-mgmt-methodology.md")))

    def test_hitech_other_fee_cap_is_20_percent_both_sides(self):
        self.assertIn("不得超过研究开发总费用的20%", self.doc_195)
        hitech = cell(self.rows, "其他相关费用", COL_HITECH)
        self.assertIn("20%", hitech)
        self.assertNotIn("10%", hitech)

    def test_deduction_other_fee_cap_is_10_percent_both_sides(self):
        self.assertIn("不得超过可加计扣除研发费用总额的 10%", self.guide_2_0)
        self.assertIn("10%", cell(self.rows, "其他相关费用", COL_DEDUCT))

    def test_limit_formula_is_the_28_announcement_one(self):
        self.assertIn("× 10% ÷ (1 − 10%)", section_41(
            read("references/rd-mgmt-methodology.md")))
        self.assertIn("统一计算全部研发项目", DOC_28.read_text(encoding="utf-8"))

    def test_entrusted_80_percent_applies_to_both_calibers(self):
        self.assertIn("按照实际发生额的80%计入委托方研发费用总额", self.doc_195)
        self.assertIn("80%", cell(self.rows, "委托外部研发费用", COL_DEDUCT))
        self.assertIn("80%", cell(self.rows, "委托外部研发费用", COL_HITECH))
        self.assertNotIn("按实际归集", read("references/deduction-guide.md"),
                         "委托研发的高企取值又写回了旧的说法")

    def test_overseas_two_thirds_and_domestic_60_percent(self):
        self.assertIn("境内符合条件的研发费用三分之二的部分", self.guide_2_0)
        self.assertIn("2/3", cell(self.rows, "委托外部研发费用", COL_DEDUCT))
        self.assertIn("60%", cell(self.rows, "委托外部研发费用", COL_HITECH))

    def test_183_days_is_a_hitech_gate(self):
        self.assertIn("累计实际工作时间在183天以上", self.doc_195)
        self.assertIn("183 天", cell(self.rows, "人员人工费用", COL_HITECH))

    def test_building_depreciation_only_in_hitech_and_accounting(self):
        self.assertIn("在用建筑物", self.doc_195)
        self.assertIn("在用建筑物", cell(self.rows, "折旧费用", COL_HITECH))
        self.assertNotIn("建筑物", cell(self.rows, "折旧费用", COL_DEDUCT))
        self.assertIn("房屋", cell(self.rows, "折旧费用", COL_ACCOUNT))

    def test_long_term_deferred_absent_from_deduction(self):
        self.assertIn("长期待摊费用", self.doc_195)
        self.assertIn("不在列举范围", cell(self.rows, "长期待摊费用", COL_DEDUCT))

    def test_ipr_vs_patent_amortisation_differs(self):
        self.assertIn("知识产权", cell(self.rows, "无形资产摊销", COL_HITECH))
        self.assertIn("专利权", cell(self.rows, "无形资产摊销", COL_DEDUCT))

    def test_stock_incentive_belongs_to_deduction_side(self):
        self.assertIn("股权激励", self.doc_40)
        self.assertIn("股权激励", cell(self.rows, "人员人工费用", COL_DEDUCT))

    def test_unallocated_shared_labor_cannot_be_deducted(self):
        self.assertIn("未分配的不得加计扣除", self.doc_40)
        self.assertIn("未分配的不得加计", cell(self.rows, "人员人工费用", COL_GAP))

    def test_cap_is_20_in_methodology_63_and_not_10_in_prose(self):
        self.assertIn("不超过研发费用总额的20%",
                      read("references/rd-mgmt-methodology.md"))
        self.assertEqual([], wrong_cap_prose(load_all()))

    def test_only_deduction_side_has_carryforward_offset(self):
        self.assertIn("结转以后年度继续冲减", self.doc_40)
        self.assertNotIn("结转以后年度继续冲减", self.doc_195)

    def test_deduction_guide_drops_the_wrong_carryforward_claim(self):
        guide = read("references/deduction-guide.md")
        self.assertNotIn("可以结转以后年度扣除", guide)
        self.assertIn("也没有结转以后年度重新计算的条款",
                      read("references/rd-mgmt-methodology.md"))


class TestSubjectCoverage(unittest.TestCase):
    """八行科目都在，且每行三套口径都给了说法。"""

    @classmethod
    def setUpClass(cls):
        cls.text = read("references/rd-mgmt-methodology.md")
        cls.rows = table_rows(section_41(cls.text))

    def test_eight_subjects_present(self):
        heads = subjects_in(self.rows)
        self.assertEqual(8, len(heads), f"4.1 表行数不是八：{heads}")
        for subject in SUBJECTS:
            self.assertIn(subject, heads, f"4.1 表缺「{subject}」：{heads}")

    def test_every_row_fills_three_calibers(self):
        self.assertEqual([], empty_caliber_cells(self.rows))

    def test_no_row_uses_shorthand(self):
        self.assertEqual([], shorthand_cells(self.rows))

    def test_differences_column_names_a_real_gap(self):
        for head, cells in self.rows:
            if head == HEADER:
                continue
            self.assertTrue(len(cells[COL_GAP]) > 4, f"「{head}」没写差异与争点")


class TestIsolationDiscipline(unittest.TestCase):
    """三套口径各自独立，不许互相外推。"""

    @classmethod
    def setUpClass(cls):
        cls.section = section_41(read("references/rd-mgmt-methodology.md"))
        cls.guide_2_0 = GUIDE_2_0.read_text(encoding="utf-8")

    def test_positive_listing_rule_present(self):
        self.assertIn("正列举", self.section)
        self.assertIn("推不出", self.section)
        self.assertIn("正列举", self.guide_2_0)

    def test_offset_rule_confined_to_deduction_side(self):
        self.assertIn("冲减只发生在加计口径", self.section)

    def test_failed_and_capitalised_rows_present(self):
        self.assertIn("失败的研发活动所发生的研发费用也可加计扣除", self.guide_2_0)
        self.assertIn("失败", self.section)
        self.assertIn("资本化", self.section)

    def test_hitech_text_says_nothing_about_capitalisation_or_failure(self):
        doc = DOC_195.read_text(encoding="utf-8")
        self.assertNotIn("资本化", doc)
        self.assertNotIn("失败", doc)

    def test_skill_redline_carries_the_rule(self):
        self.assertIn("跨口径不得外推", read("SKILL.md"))

    def test_audit_risk_table_has_a_cross_caliber_row(self):
        self.assertIn("跨口径外推", read("references/audit-response.md"))


class TestBaselineCitations(unittest.TestCase):
    """4.1 末尾的出处清单逐个文件存在，且真含所引的说法。"""

    def setUp(self):
        self.section = section_41(read("references/rd-mgmt-methodology.md"))

    def test_every_cited_file_exists(self):
        names = set(re.findall(r"`([^`]+\.md)`", self.section))
        self.assertTrue(names, "4.1 没列任何出处文件")
        for name in names:
            self.assertTrue(list(REF.rglob(Path(name).name)),
                            f"出处指向不存在的文件：{name}")

    def test_cited_docs_contain_the_quoted_wording(self):
        pairs = [
            (GUIDE_2_0, ("表5", "研发费用归集口径比较", "正列举",
                         "境内符合条件的研发费用三分之二的部分")),
            (DOC_195, ("研究开发总费用的20%", "按照实际发生额的80%计入委托方研发费用总额",
                       "累计实际工作时间在183天以上", "在用建筑物", "通讯费",
                       "装备调试费用")),
            (DOC_119, ("10%", "其他相关费用")),
            (DOC_28, ("统一计算全部研发项目", "形成无形资产的年度")),
            (DOC_40, ("结转以后年度继续冲减", "股权激励", "未分配的不得加计扣除")),
        ]
        for path, phrases in pairs:
            self.assertTrue(path.exists(), f"原文不在位：{path.name}")
            text = path.read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertIn(phrase, text, f"{path.name} 里没有「{phrase}」")

    def test_citation_paragraph_lists_all_three_norms(self):
        for token in ("195", "119", "40"):
            self.assertIn(token, self.section)


class TestMutations(unittest.TestCase):
    """每条判据都要能被注入的破坏打红，否则它只是一句装饰。"""

    def setUp(self):
        self.text = METHOD.read_text(encoding="utf-8")
        self.section = section_41(self.text)

    def _sub(self, old, new):
        self.assertEqual(1, self.section.count(old), f"锚点出现次数不是 1：{old[:16]}")
        self.assertNotIn(new, self.section, "变体与原文相同，等于没变异")
        return self.text.replace(old, new, 1)

    def test_mut_hitech_cap_back_to_10_goes_red(self):
        rows = table_rows(section_41(self._sub(
            "一般不得超过研究开发总费用的 **20%**",
            "一般不得超过研究开发总费用的 **10%**")))
        with self.assertRaises(AssertionError):
            self.assertNotIn("10%", cell(rows, "其他相关费用", COL_HITECH))

    def test_mut_entrusted_80_dropped_goes_red(self):
        rows = table_rows(section_41(self._sub(
            "按独立交易原则以实际发生额的 **80%** 计入委托方研发费用总额",
            "按实际发生额计入委托方研发费用总额")))
        with self.assertRaises(AssertionError):
            self.assertIn("80%", cell(rows, "委托外部研发费用", COL_HITECH))

    def test_mut_long_term_deferred_row_removed_goes_red(self):
        bad = re.sub(r"\n\| 长期待摊费用[^\n]*", "", self.text, count=1)
        heads = subjects_in(table_rows(section_41(bad)))
        self.assertNotIn("长期待摊费用", heads)
        self.assertEqual(7, len(heads))

    def test_mut_baseline_wording_softened_goes_red(self):
        """标题与正文各写了一次"唯一基准"，两处都要改才算把基准地位抽掉。"""
        self.assertEqual(2, self.section.count("唯一基准"))
        bad = self.text.replace("唯一基准", "一处参考")
        self.assertNotIn("唯一基准", section_41(bad))

    def test_mut_row_shorthand_reinserted_goes_red(self):
        rows = table_rows(section_41(self._sub(
            "用于研究开发活动的仪器、设备**和在用建筑物**的折旧费",
            "同加计扣除")))
        self.assertEqual(["折旧费用:2"], shorthand_cells(rows))

    def test_mut_cell_emptied_goes_red(self):
        anchor = "按会计准则摊销计入"
        self.assertEqual(1, self.section.count(anchor))
        rows = table_rows(section_41(self.text.replace(anchor, "", 1)))
        self.assertEqual(["长期待摊费用:3"], empty_caliber_cells(rows))

    def test_mut_pointer_removed_goes_red(self):
        texts = load_all()
        texts["references/routing-table.md"] = texts["references/routing-table.md"].replace(
            BASELINE, "口径另表", 1)
        self.assertEqual(["references/routing-table.md"], missing_pointers(texts))

    def test_mut_wrong_cap_sentence_reinserted_goes_red(self):
        texts = load_all()
        texts["references/audit-response.md"] += "\n高企其他相关费用的上限同样是 10%。\n"
        hits = wrong_cap_prose(texts)
        self.assertEqual(1, len(hits))
        self.assertTrue(hits[0].startswith("references/audit-response.md:"))

    def test_mut_stock_incentive_removed_goes_red(self):
        rows = table_rows(section_41(self._sub(
            "工资薪金含按规定可以在税前扣除的对研发人员股权激励支出（2017 年第 40 号）",
            "工资薪金只算货币性部分")))
        with self.assertRaises(AssertionError):
            self.assertIn("股权激励", cell(rows, "人员人工费用", COL_DEDUCT))

    def test_mut_citation_file_typo_goes_red(self):
        bad = self.text.replace("policy-docs/高新技术企业认定管理工作指引-国科发火2016-195号.md",
                                "policy-docs/国科发火2016-195号-不存在.md", 1)
        names = {n for n in re.findall(r"`([^`]+\.md)`", section_41(bad))
                 if "不存在" in n}
        self.assertTrue(names)
        for name in names:
            self.assertEqual([], list(REF.rglob(Path(name).name)))

    def test_mut_redline_clause_removed_goes_red(self):
        lines = read("SKILL.md").splitlines()
        start = next(i for i, ln in enumerate(lines) if "跨口径不得外推" in ln)
        bad = "\n".join(lines[:start] + lines[start + 1:])
        self.assertNotIn("跨口径不得外推", bad)

    def test_mut_pointer_purged_from_guide_goes_red(self):
        """§四 与协同要点各写了一次指针，只删一处仍算指回来了。"""
        texts = load_all()
        guide = texts["references/deduction-guide.md"]
        self.assertTrue(guide.count(BASELINE) >= 2)
        texts["references/deduction-guide.md"] = guide.replace(BASELINE, "见同类资料")
        self.assertEqual(["references/deduction-guide.md"], missing_pointers(texts))

    def test_mutation_anchors_are_unique(self):
        """同一段落若被复制过，replace(count=1) 只改第一处，变异就是空的。"""
        for anchor in ("一般不得超过研究开发总费用的 **20%**",
                       "按独立交易原则以实际发生额的 **80%** 计入委托方研发费用总额",
                       "工资薪金含按规定可以在税前扣除的对研发人员股权激励支出（2017 年第 40 号）",
                       "用于研究开发活动的仪器、设备**和在用建筑物**的折旧费",
                       "本技能唯一基准"):
            self.assertEqual(1, self.text.count(anchor), f"锚点不唯一：{anchor[:20]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
