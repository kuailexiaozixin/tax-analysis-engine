#!/usr/bin/env python3
"""研发子技能口径对照的离线用例。不联网、不调模型，只读 markdown。

口径指 会计核算／高新技术企业认定／研发费用加计扣除 三条归集规定（另含 R&D 统计与
IPO 披露两套扩展口径），量的是同一笔费用按哪份规定归集，回答"算不算、算多少"。同一
笔研发费在不同口径下取值不同，是高企稽查与加计核查里最高频的争议，故作为回归对象。

本用例针对 `subskills/R&D-Expenses/`（新技能族）——科目级取值的唯一基准是
`subskills/rd-accounting-tax/references/subject-caliber-baseline.md`。守四类问题：

  1. **基准唯一**：科目级取值只住在基准文件里，注明"唯一基准"，且子技能入口指过去。
  2. **取值与原文一致**：表里关键数值能在本地政策原文逐字找到（80%、183 天、在用建筑物、
     2/3、20%/10%、统一计算、未分配不得加计、结转冲减）。
  3. **科目不缺行**：加计六类 ∪ 高企八类共八行，且每行三套口径都有取值，不许略写。
  4. **隔离纪律在场**：加计与高企都是正列举、冲减只落加计口径、跨口径不得外推。

原文（`policy/`）是转录件，不参与"自写文件不许漂"的扫描。
"""

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SUB = ROOT / "subskills" / "R&D-Expenses"
ACC = SUB / "subskills" / "rd-accounting-tax"
BASE = ACC / "references" / "subject-caliber-baseline.md"
SKILL = SUB / "SKILL.md"
ACC_SKILL = ACC / "SKILL.md"
POLICY = SUB / "policy"

BOOK = POLICY / "研发费用加计扣除与高新技术企业优惠实务及案例.md"
DOC_28 = POLICY / "税务总局公告2021-28号-进一步落实研发费用加计扣除政策.md"
DOC_40 = POLICY / "税务总局公告2017-40号-研发费用加计扣除归集范围.md"

# 基准表首格＝科目名。列序：0 费用项目 | 1 加计扣除 | 2 高企认定 | 3 会计 | 4 差异与争点
SUBJECTS = ("人员人工费用", "直接投入费用", "折旧费用", "长期待摊费用",
            "无形资产摊销", "设计试验等费用", "委托外部研发费用", "其他相关费用（限额）")
COL_DEDUCT, COL_HITECH, COL_ACCOUNT, COL_GAP = 1, 2, 3, 4
HEADER = "费用项目"
BASELINE_MARK = "唯一基准"


def base_text() -> str:
    return BASE.read_text(encoding="utf-8")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def table_rows(text: str) -> list:
    """[(首格, 整行格串)]，只取第一张连续表格块，跳过分隔行与表头。"""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip().startswith("|"):
        i += 1
    block = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        block.append(lines[i])
        i += 1
    rows = []
    for line in block:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or all(set(c) <= set("-: ") for c in cells):
            continue
        if cells[0] == HEADER:
            continue
        rows.append((cells[0], cells))
    return rows


def cell(rows, subject: str, col: int) -> str:
    for head, cells in rows:
        if head == subject:
            return cells[col] if col < len(cells) else ""
    raise AssertionError(f"基准表里没有「{subject}」这一行")


def empty_caliber_cells(rows) -> list:
    return [f"{head}:{col}" for head, cells in rows
            for col in (COL_DEDUCT, COL_HITECH, COL_ACCOUNT) if len(cells[col]) <= 1]


def shorthand_cells(rows) -> list:
    return [f"{head}:{col}" for head, cells in rows
            for col in (COL_DEDUCT, COL_HITECH, COL_ACCOUNT, COL_GAP)
            if "同加计" in cells[col]]


def pointer_missing(text: str) -> bool:
    return "subject-caliber-baseline" not in text


class TestSingleBaseline(unittest.TestCase):
    """科目级取值只住一处，且登记为唯一基准。"""

    def test_baseline_declares_itself_unique(self):
        self.assertGreaterEqual(base_text().count(BASELINE_MARK), 2,
                                "基准文件应标题与正文各写一次「唯一基准」")

    def test_subskill_entry_points_back(self):
        self.assertFalse(pointer_missing(read(ACC_SKILL)),
                         "rd-accounting-tax 的 SKILL.md 没有指回科目级口径基准")


class TestValuesMatchOriginalText(unittest.TestCase):
    """表里的数值都能在本地政策原文逐字找到。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = table_rows(base_text())
        cls.book = read(BOOK)
        cls.doc_40 = read(DOC_40)

    def test_hitech_other_fee_cap_is_20_not_10(self):
        self.assertIn("研究开发总费用的20%", self.book)
        cap = cell(self.rows, "其他相关费用（限额）", COL_HITECH)
        self.assertIn("20%", cap)
        self.assertNotIn("10%", cap)

    def test_deduction_other_fee_cap_is_10(self):
        self.assertIn("10%", cell(self.rows, "其他相关费用（限额）", COL_DEDUCT))

    def test_limit_formula_present_and_28_unifies(self):
        self.assertIn("× 10% ÷ (1 − 10%)", base_text())
        self.assertIn("统一计算全部研发项目", read(DOC_28))

    def test_entrusted_80_percent_both_calibers(self):
        self.assertIn("按照实际发生额的80%计入委托方研发费用总额", self.book)
        self.assertIn("80%", cell(self.rows, "委托外部研发费用", COL_DEDUCT))
        self.assertIn("80%", cell(self.rows, "委托外部研发费用", COL_HITECH))

    def test_overseas_two_thirds_and_domestic_60(self):
        self.assertIn("境内符合条件的研发费用三分之二的部分", self.book)
        self.assertIn("2/3", cell(self.rows, "委托外部研发费用", COL_DEDUCT))
        self.assertIn("60%", cell(self.rows, "委托外部研发费用", COL_HITECH))

    def test_183_days_is_a_hitech_gate(self):
        self.assertIn("累计实际工作时间在183天以上", self.book)
        self.assertIn("183 天", cell(self.rows, "人员人工费用", COL_HITECH))

    def test_building_depreciation_only_in_hitech_and_accounting(self):
        self.assertIn("在用建筑物", cell(self.rows, "折旧费用", COL_HITECH))
        self.assertNotIn("建筑物", cell(self.rows, "折旧费用", COL_DEDUCT))
        self.assertIn("房屋", cell(self.rows, "折旧费用", COL_ACCOUNT))

    def test_long_term_deferred_absent_from_deduction(self):
        self.assertIn("不在列举范围", cell(self.rows, "长期待摊费用", COL_DEDUCT))

    def test_ipr_vs_patent_amortisation_differs(self):
        self.assertIn("知识产权", cell(self.rows, "无形资产摊销", COL_HITECH))
        self.assertIn("专利权", cell(self.rows, "无形资产摊销", COL_DEDUCT))

    def test_only_deduction_side_has_carryforward_offset(self):
        self.assertIn("结转以后年度继续冲减", self.doc_40)

    def test_unallocated_shared_labor_cannot_be_deducted(self):
        self.assertIn("未分配的不得加计扣除", self.doc_40)


class TestSubjectCoverage(unittest.TestCase):
    """八行科目都在，且每行三套口径都给了说法。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = table_rows(base_text())

    def test_eight_subjects_present(self):
        heads = [h for h, _ in self.rows]
        self.assertEqual(8, len(heads), f"基准表行数不是八：{heads}")
        for subject in SUBJECTS:
            self.assertIn(subject, heads, f"基准表缺「{subject}」：{heads}")

    def test_every_row_fills_three_calibers(self):
        self.assertEqual([], empty_caliber_cells(self.rows))

    def test_no_row_uses_shorthand(self):
        self.assertEqual([], shorthand_cells(self.rows))

    def test_differences_column_names_a_real_gap(self):
        for head, cells in self.rows:
            self.assertTrue(len(cells[COL_GAP]) > 4, f"「{head}」没写差异与争点")


class TestIsolationDiscipline(unittest.TestCase):
    """三套口径各自独立，不许互相外推。"""

    @classmethod
    def setUpClass(cls):
        cls.section = base_text()

    def test_positive_listing_rule_present(self):
        self.assertIn("正列举", self.section)
        self.assertIn("推不出", self.section)

    def test_offset_rule_confined_to_deduction_side(self):
        self.assertIn("冲减只发生在加计口径", self.section)

    def test_failed_and_capitalised_present(self):
        self.assertIn("失败", self.section)
        self.assertIn("资本化", self.section)

    def test_skill_redline_carries_the_rule(self):
        self.assertIn("跨口径不得外推", read(SKILL))


class TestMutations(unittest.TestCase):
    """每条判据都要能被注入的破坏打红，否则它只是一句装饰。"""

    def setUp(self):
        self.text = base_text()

    def test_mut_hitech_cap_back_to_10_goes_red(self):
        broken = self.text.replace("**一般 ≤ 研发总费用 20%**",
                                   "**一般 ≤ 研发总费用 10%**", 1)
        self.assertNotEqual(broken, self.text)
        cap = cell(table_rows(broken), "其他相关费用（限额）", COL_HITECH)
        with self.assertRaises(AssertionError):
            self.assertNotIn("10%", cap)   # 破坏后判据应报红

    def test_mut_long_term_deferred_row_removed_goes_red(self):
        broken = re.sub(r"\n\| 长期待摊费用[^\n]*", "", self.text, count=1)
        heads = [h for h, _ in table_rows(broken)]
        self.assertNotIn("长期待摊费用", heads)
        self.assertEqual(7, len(heads))

    def test_mut_pointer_removed_goes_red(self):
        broken = read(ACC_SKILL).replace("subject-caliber-baseline", "口径另表")
        self.assertTrue(pointer_missing(broken))


if __name__ == "__main__":
    unittest.main(verbosity=2)
