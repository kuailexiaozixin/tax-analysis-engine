#!/usr/bin/env python3
"""多合同受理层 `scripts/tax_intake.py` 的离线用例。不联网、不调模型。

守的是九类失效路径，`validate()` 的每一处判据都配一次"把注册表改坏"的自检：
改坏了却不报红，就是用例空了。

1. **模板的形状与词汇同源**：`_说明` 里那十九张名字清单逐张等于代码常量；九维度代号连续、
   要素不重名、落点在值域里、风险指标落点指得到 `tax_risk_framework.md` 的某一节，
   历史称谓每一条的动作都带一个时间锚。
2. **骨架零数字**：维度名、要素名、为什么、抽取线索、落点里出现任何政策数值，载入即报错；
   注册表少报一格禁数字也要报错；代码侧走同一判据（AST 数值常量只允许退码 0/1/2）。
3. **抽取的值必须带可定位出处**：出处三格缺一格→整条『待核』不进比对；整格不给→受理时报错；
   填表的值不要求出处但标成来源『填表』；只给出处不给值算没交这一条。
4. **合同链只做到候选**：状态默认『待用户确认』，未确认的替代边不作废任何条款；确认后按要素
   落位，整份合同不作废；互相替代、指向不存在的合同、抽取来的边没出处都在受理时挡住。
5. **五种成因各有活路径**：条款未提供／履行记录缺失／口径不可比／多份合同各写一个值／
   出处定不到位，逐个能产出一条目，结论名与缺口类别名与注册表那句话对上。
6. **判不动与通过是两件事**：比对没做成的条目不进『一致』，缺记录不写成未发生，
   没比的维度留名，不适用的那一格必须带理由。
7. **名字不另立第二套**：核对四值、缺口类别与动作、覆盖四态、两条红线词表、② 的追问句、
   现行税种名都引上游那几处。
8. **输出与命令行**：渲染把差异条目十一格逐格打出来，全文不含预测检查结果的措辞；
   报错走退码 2 且不带 Traceback。
9. **文档挂线**：SKILL 快速索引那两行的时点、② 的触发时点句、⑥ 那段与 ⑦ 第 27 条、
   模板条件块（十一格与四值结论与五种成因逐字在场、缺料时那句"本块未执行"还在、
   围栏外仍是九个小节）、README 与归属表与边界段、`tax_categories.md` 那一张历史称谓表
   逐格等于注册表、注册表『谁在读』的每个路径都存在、门禁离线组点名本文件。
   文档里改一格而用例不报红，等于那一格没人核对。

用法：`python tests/test_intake.py`
"""

import ast
import contextlib
import copy
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import tax_analyze   # noqa: E402  ② 的追问句只有一处：CONTEXT_AXES
import tax_coverage  # noqa: E402  覆盖四态沿用覆盖率层那串名字
import tax_inspect   # noqa: E402  核对四值、缺口类别与动作、两条红线词表都取自这一处
import tax_intake    # noqa: E402
import tax_search    # noqa: E402  历史称谓不得撞上现行税种名

REG = tax_intake.load()
IDX = tax_intake.index(REG)
SOURCE = (ROOT / "scripts" / "tax_intake.py").read_text(encoding="utf-8")
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
TEMPLATES = (ROOT / "references" / "output_templates.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
DEFECTS = (ROOT / "references" / "source_defects.md").read_text(encoding="utf-8")
CATEGORIES = (ROOT / "references" / "tax_categories.md").read_text(encoding="utf-8")

#: 两份合同、一条替代边、一个判断单元：条款与履行在四个维度上交错，五种成因里四种在这一份里就出现。
DOC = {
    "合同": [
        {"代号": "CT-1", "名称": "设备采购合同", "签订方": "本公司（甲）／某设备公司（乙）",
         "签订日期": "2025-12-10", "标的": "两台包装机",
         "条款": {
             "C1": {
                 "对价总额与价外费用": {
                     "来源": "抽取", "金额": "1130000", "口径": "含税",
                     "原文出处": {"文件": "CT-1", "位置": "第三条",
                                "摘录": "合同总价 1130000 元，含税；包装物押金另计"}},
                 "合同双方与纳税主体": {
                     "来源": "抽取", "口径": "", "金额": "", "比例": "", "期限": "",
                     "触发时点": "",
                     "原文出处": {"文件": "CT-1", "位置": "第一条",
                                "摘录": "甲方：本公司；乙方指定收款账户为丙方名下账户"}}},
             "C3": {
                 "付款期限与分期安排": {
                     "来源": "抽取", "期限": "2026-03-31",
                     "触发时点": "验收合格后 30 日内付清尾款",
                     "原文出处": {"文件": "CT-1", "位置": "第六条",
                                "摘录": "验收合格后 30 日内付清尾款，最迟不晚于 2026-03-31"}},
                 "预付款与质保金": {
                     "来源": "抽取", "金额": "50000", "期限": "质保期 12 个月",
                     "触发时点": "合同签订后 5 日内支付预付",
                     "原文出处": {"文件": "CT-1", "位置": "第七条",
                                "摘录": "预付 50000 元，质保期 12 个月届满后退还"}}},
             "C4": {
                 "发票种类与票面税率": {
                     "来源": "抽取", "比例": "13%",
                     "原文出处": {"文件": "CT-1", "位置": "第八条",
                                "摘录": "乙方开具增值税专用发票，税率为 13%"}},
                 "开票顺序与时点": {
                     "来源": "抽取", "触发时点": "先开票后付款",
                     "原文出处": {"文件": "CT-1", "位置": "第八条第二款",
                                "摘录": "乙方先开具发票；原合同项下的营业税由乙方承担"}}},
             "C5": {
                 "扣缴义务与申报期限": {
                     "来源": "抽取", "金额": "", "比例": "", "期限": "", "触发时点": "",
                     "原文出处": {"文件": "CT-1", "位置": "第十条",
                                "摘录": "双方各自承担法定税费"}}}},
         },
        {"代号": "CT-2", "名称": "付款条款补充协议", "签订方": "本公司（甲）／某设备公司（乙）",
         "签订日期": "2026-03-20", "标的": "顺延付款期限",
         "条款": {"C3": {"付款期限与分期安排": {
             "来源": "抽取", "期限": "2026-06-30",
             "原文出处": {"文件": "CT-2", "位置": "第一条",
                        "摘录": "第六条约定的付款期限变更为 2026-06-30，原条款不再执行"}}}}},
    ],
    "边": [{"代号": "E1", "在先合同": "CT-1", "在后合同": "CT-2", "类型": "替代",
            "来源": "抽取",
            "原文出处": {"文件": "CT-2", "位置": "第一条", "摘录": "原条款不再执行"}}],
    "不适用": {"C8": "本单采购无变更与解除事项，合同里没有任何补充或终止条款"},
    "判断单元": [{"代号": "U1", "主体": "本公司（一般纳税人）",
                 "具体应税交易": "采购两台包装机", "业务所属期": "2026 年第一季度",
                 "资格条件": "", "适用政策": "", "合同": ["CT-1", "CT-2"],
                 "履行": {
                     "C1": {"对价总额与价外费用": {
                         "来源": "抽取", "金额": "1000000", "口径": "不含税",
                         "原文出处": {"文件": "付款回单", "位置": "2026-07-15 电汇",
                                    "摘录": "实付 1000000 元（不含税价）"}}},
                     "C3": {"付款期限与分期安排": {
                         "来源": "抽取", "期限": "2026-07-15",
                         "原文出处": {"文件": "付款回单", "位置": "2026-07-15 电汇",
                                    "摘录": "2026-07-15 支付尾款"}},
                         "预付款与质保金": {
                             "来源": "抽取", "金额": "50000",
                             "原文出处": {"文件": "付款回单", "位置": "2025-12-15 电汇",
                                        "摘录": "支付预付 50000 元"}}},
                     "C4": {"发票种类与票面税率": {"来源": "填表", "比例": "13%"},
                            "开票顺序与时点": {
                                "来源": "抽取", "触发时点": "2026-07-01 开票",
                                "原文出处": {"文件": "发票记账联", "位置": "",
                                           "摘录": "开票日期 2026-07-01"}}}}}],
}


def with_doc(**over) -> dict:
    """改 DOC 的顶层某一格（边、不适用、判断单元……），其余原样。"""
    d = copy.deepcopy(DOC)
    d.update(copy.deepcopy(over))
    return d


def mutate(fn):
    """改坏注册表再走 `validate()`：必须报错，并把报错文本交回调用方断言。"""
    reg = copy.deepcopy(REG)
    fn(reg)
    try:
        tax_intake.validate(reg)
    except ValueError as e:
        return str(e)
    raise AssertionError("注册表被改坏了，validate 却没报错——这条判据是空的")


def pinned_pairs() -> tuple:
    """validate 逐张比对的那十九张名字清单。用例与 validate 共用这一份，
    所以『validate 里删掉一处比对』会被 `test_validate_itself_pins_each_name_list` 打红。"""
    return (("九维度", tuple(d["维度"] for d in REG["维度"])),
            ("维度字段", tax_intake.DIMENSION_KEYS),
            ("要素字段", tax_intake.ELEMENT_KEYS),
            ("要素五件套", tax_intake.ELEMENT_SLOTS),
            ("记录字段", tax_intake.RECORD_KEYS),
            ("合同链边字段", tax_intake.EDGE_KEYS),
            ("判断单元字段", tax_intake.UNIT_ROW_KEYS),
            ("原文出处的三格", tax_intake.SOURCE_CELLS),
            ("取值来源三态", tax_intake.SOURCES),
            ("合同链边类型", tax_intake.EDGE_KINDS),
            ("合同链确认状态", tax_intake.CHAIN_STATES),
            ("判断单元四要素", tax_intake.UNIT_KEYS),
            ("一致性核对四值", tax_intake.VERDICTS),
            ("比对结论的成因", tax_intake.CAUSES),
            ("差异条目字段", tax_intake.ENTRY_KEYS),
            ("落点值域", tax_intake.LANDINGS),
            ("历史税种称谓", tuple(r["称谓"] for r in REG["历史税种称谓对照"])),
            ("重查指针的说法", tax_intake.RECHECK_NEEDLES),
            ("不许出现数字的格子", tax_intake.DIGIT_FREE_CELLS))


def rows(out) -> list:
    return out["差异条目"] + out["无法确认条目"] + out["一致明细"]


def find(out, dim, ename, slot=None, verdict=None) -> list:
    return [r for r in rows(out) if r["维度代号"] == dim and r["要素"] == ename
            and (slot is None or r["槽"] == slot)
            and (verdict is None or r["核对结论"] == verdict)]


def causes(out) -> list:
    """这一份输出里出现过的成因（从差异说明尾注里取，本层不另存一份）。"""
    seen = []
    for r in out["差异条目"] + out["无法确认条目"]:
        for c in tax_intake.CAUSES:
            if c in r["差异说明"] and c not in seen:
                seen.append(c)
    return seen


class TestTemplateShape(unittest.TestCase):
    """九维度 × 要素 × 五件套，每一格都带齐"判得动"所需的线索。"""

    def test_nine_dimensions_are_c1_to_c9(self):
        self.assertEqual([d["代号"] for d in REG["维度"]],
                         [f"C{i}" for i in range(1, 10)])
        self.assertEqual(REG["_说明"]["九维度"],
                         [d["维度"] for d in REG["维度"]])

    def test_every_dimension_has_the_five_fields(self):
        for d in REG["维度"]:
            for field in tax_intake.DIMENSION_KEYS:
                self.assertIn(field, d, f"{d['代号']} 缺『{field}』")
            self.assertTrue(d["要素"], f"{d['代号']} 是空维度")
            for e in d["要素"]:
                for field in tax_intake.ELEMENT_KEYS:
                    self.assertIn(field, e, f"{d['代号']}／{e.get('要素')} 缺『{field}』")
                self.assertTrue(tax_inspect._nonempty_strs(e["抽取线索"], "", ""))

    def test_landing_points_are_all_in_the_value_domain(self):
        for _code, _name, e in tax_intake.elements():
            self.assertIn(e["落点"], tax_intake.LANDINGS)

    def test_risk_landing_points_point_at_a_framework_section(self):
        sections = tax_intake.framework_sections()
        for d in REG["维度"]:
            hit = [s for s in sections
                   if d["风险指标落点"] in s and "风险指标" in s]
            self.assertTrue(hit, f"{d['代号']} 的落点「{d['风险指标落点']}」指不到小节")

    def test_five_slots_are_the_documented_five(self):
        self.assertEqual(tax_intake.ELEMENT_SLOTS,
                         ("金额", "比例", "期限", "触发时点", "原文出处"))
        self.assertEqual(tax_intake.NUMERIC_SLOTS, ("金额", "比例"))

    def test_historical_table_has_seven_rows_with_four_cells(self):
        hist = REG["历史税种称谓对照"]
        self.assertEqual(len(hist), 7)
        for r in hist:
            for field in tax_intake.HISTORICAL_KEYS:
                self.assertIn(field, r, f"历史称谓 {r.get('称谓')} 缺『{field}』")
            self.assertTrue(r["检索线索"])

    def test_blank_form_is_fillable(self):
        b = tax_intake.blank(REG)
        self.assertEqual(b["边"], [], "空白表写一行空边会让受理停在「在后合同是空的」上")
        self.assertEqual(list(b["判断单元"][0]), list(tax_intake.UNIT_ROW_KEYS))
        for side in (b["合同"][0]["条款"], b["判断单元"][0]["履行"]):
            self.assertEqual(sorted(side), [d["代号"] for d in REG["维度"]])
            for dim, elems in side.items():
                self.assertEqual(sorted(elems), sorted(IDX[dim]["要素"]))

    def test_blank_records_count_as_not_submitted(self):
        """空白表整格没交：覆盖报『缺』而不是报错，也不产出一条目。"""
        out = tax_intake.intake({"合同": [{"代号": "CT-1", "名称": "空白试填",
                                          "条款": tax_intake._blank_side(REG)}],
                                 "判断单元": [{"代号": "U1", "主体": "甲", "具体应税交易": "乙",
                                             "业务所属期": "丙", "资格条件": "丁",
                                             "合同": ["CT-1"],
                                             "履行": tax_intake._blank_side(REG)}]})
        self.assertEqual(out["缺维度数"], 9)
        self.assertEqual(out["覆盖"]["状态"], tax_inspect.COVERAGE_PARTIAL)
        done = tax_intake.reconcile({"合同": out["合同"] and [
            {"代号": "CT-1", "名称": "空白试填", "条款": tax_intake._blank_side(REG)}],
            "判断单元": [{"代号": "U1", "主体": "甲", "具体应税交易": "乙",
                        "业务所属期": "丙", "资格条件": "丁", "合同": ["CT-1"],
                        "履行": tax_intake._blank_side(REG)}]})
        self.assertEqual(done["核对"]["总成对数"], 0,
                         "两侧都空也产条目，条数就随模板的要素条数增长")


class TestValidateCatchesDrift(unittest.TestCase):
    """`validate()` 每一处判据都改坏一次注册表，看它报不报红。"""

    def test_baseline_loads(self):
        tax_intake.validate(copy.deepcopy(REG))

    def test_every_name_list_is_pinned_against_code(self):
        note = REG["_说明"]
        pairs = pinned_pairs()
        self.assertEqual(len(pairs), 19, "钉住的名字清单数量变了，用例与 validate 得一起补")
        for field, const in pairs:
            self.assertEqual(note[field], list(const), f"『{field}』这张清单与代码不一致")

    def test_validate_itself_pins_each_name_list(self):
        """每一张清单都要 `validate()` 自己拦住：只在本用例里比对过，validate 少比一张没人报红。"""
        for field, _ in pinned_pairs():
            with self.subTest(清单=field):
                def poke(reg, name=field):
                    reg["_说明"][name] = list(reg["_说明"][name]) + ["一张没人比的清单"]
                self.assertIn(field, mutate(poke))

    def test_dropping_a_name_list_entry_is_caught(self):
        def poke(reg):
            reg["_说明"]["记录字段"] = [x for x in reg["_说明"]["记录字段"] if x != "口径"]
        self.assertIn("记录字段", mutate(poke))

    def test_reordering_a_name_list_is_caught(self):
        def poke(reg):
            reg["_说明"]["要素五件套"] = list(reversed(reg["_说明"]["要素五件套"]))
        self.assertIn("不同源", mutate(poke))

    def test_empty_dimension_list_is_caught(self):
        self.assertIn("没有『维度』", mutate(lambda r: r.update(维度=[])))

    def test_empty_historical_table_is_caught(self):
        self.assertIn("历史税种称谓对照", mutate(lambda r: r.update(历史税种称谓对照=[])))

    def test_non_contiguous_code_is_caught(self):
        def poke(reg):
            reg["维度"][2]["代号"] = "C20"
        self.assertIn("从 C1 连续", mutate(poke))

    def test_duplicate_element_name_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][1]["要素"] = reg["维度"][0]["要素"][0]["要素"]
        self.assertIn("要素名", mutate(poke))

    def test_missing_field_on_an_element_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][0].pop("抽取线索")
        self.assertIn("缺『抽取线索』", mutate(poke))

    def test_empty_clue_list_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][0]["抽取线索"] = ["  "]
        self.assertIn("抽取线索", mutate(poke))

    def test_landing_point_outside_the_domain_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][0]["落点"] = "税额"
        self.assertIn("不在取值域", mutate(poke))

    def test_risk_landing_point_that_names_no_section_is_caught(self):
        def poke(reg):
            reg["维度"][0]["风险指标落点"] = "现金流"
        self.assertIn("没有对应小节", mutate(poke))

    def test_cause_list_drift_is_caught(self):
        def poke(reg):
            reg["_说明"]["成因各自的动作"].pop("口径不可比")
        self.assertIn("成因不同源", mutate(poke))

    def test_cause_sentence_that_omits_the_verdict_is_caught(self):
        """成因那张表读起来像静默查表，所以那句话必须自己写出结论名与缺口类别名。"""
        def poke(reg):
            reg["_说明"]["成因各自的动作"]["口径不可比"] = "两侧口径不同，回去核口径。"
        msg = mutate(poke)
        self.assertIn("口径不可比", msg)
        self.assertIn("未取数", msg)

    def test_red_line_scan_cell_list_drift_is_caught(self):
        def poke(reg):
            reg["_说明"]["红线扫在哪几格"]["越红线词表只扫"] = ["动作"]
        self.assertIn("越红线词表只扫", mutate(poke))

    def test_historical_name_that_is_a_live_tax_is_caught(self):
        live = sorted(set(tax_search.TAX_TYPE_KEYWORDS))[0]
        def poke(reg):
            reg["历史税种称谓对照"][0]["称谓"] = live
            reg["_说明"]["历史税种称谓"][0] = live
        self.assertIn("现行税种名", mutate(poke))

    def test_historical_row_without_a_bridge_is_caught(self):
        def poke(reg):
            reg["历史税种称谓对照"][0]["现行"] = "  "
        self.assertIn("是空的", mutate(poke))

    def test_fabrication_in_a_historical_action_is_caught(self):
        def poke(reg):
            reg["历史税种称谓对照"][0]["动作"] += "；把发票倒签到改征之前"
        self.assertIn("改动历史事实", mutate(poke))

    def test_prediction_in_tax_meaning_is_caught(self):
        def poke(reg):
            reg["维度"][0]["税务含义"] += "，这样就不会被稽查"
        self.assertIn("稽查结果预测", mutate(poke))

    def test_prediction_in_why_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][0]["为什么"] += "，这样改不会被罚"
        self.assertIn("稽查结果预测", mutate(poke))


class TestNoPolicyNumbers(unittest.TestCase):
    """模板与代码都不存政策数值：抄进去一次，就答不出这个数出自哪一号文。"""

    def test_digit_in_dimension_name_is_caught(self):
        def poke(reg):
            reg["维度"][0].update(维度="主体与对价3")
            reg["_说明"]["九维度"][0] = "主体与对价3"
        self.assertIn("数字", mutate(poke))

    def test_digit_in_tax_meaning_is_caught(self):
        def poke(reg):
            reg["维度"][0]["税务含义"] += "（按 13% 计）"
        self.assertIn("数字", mutate(poke))

    def test_digit_in_element_name_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][0]["要素"] = "对价总额与价外费用2"
        self.assertIn("数字", mutate(poke))

    def test_digit_in_clues_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][0]["抽取线索"].append("60%")
        self.assertIn("数字", mutate(poke))

    def test_digit_in_landing_point_is_caught(self):
        def poke(reg):
            reg["维度"][0]["要素"][0]["落点"] = "主体2"
        self.assertIn("数字", mutate(poke))

    def test_digit_in_slot_list_is_caught(self):
        both = tuple(tax_intake.ELEMENT_SLOTS) + ("金额3万",)
        with mock.patch.object(tax_intake, "ELEMENT_SLOTS", both):
            def poke(reg):
                reg["_说明"]["要素五件套"] = list(both)
            self.assertIn("数字", mutate(poke))

    def test_digit_in_landing_value_domain_is_caught(self):
        both = tuple(tax_intake.LANDINGS) + ("第3档",)
        with mock.patch.object(tax_intake, "LANDINGS", both):
            def poke(reg):
                reg["_说明"]["落点值域"] = list(both)
            self.assertIn("数字", mutate(poke))

    def test_a_claimed_digit_free_cell_cannot_be_dropped(self):
        def poke(reg):
            reg["_说明"]["不许出现数字的格子"] = \
                [c for c in reg["_说明"]["不许出现数字的格子"] if c != "抽取线索"]
        self.assertIn("不许出现数字的格子", mutate(poke))

    def test_source_holds_no_arithmetic_constants(self):
        """代码侧与巡检层同一判据：除了 CLI 的退码，一个数值常量都不许有。"""
        tree = ast.parse(SOURCE)
        seen = sorted({n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant)
                       and isinstance(n.value, (int, float))
                       and not isinstance(n.value, bool)
                       and n.value not in (0, 1, 2)})
        self.assertEqual(seen, [], f"代码里出现了硬编码数值 {seen}：税率、期限天数、门槛一律回 ⑧")
        self.assertIn("return 2", SOURCE, "退码 2 是报错分支，不是政策数值")

    def test_historical_rows_are_the_documented_exemption(self):
        """年份与文号只出现在历史称谓那一张表里，且只作沿革标识与检索线索。"""
        self.assertNotIn("历史税种称谓对照", REG["_说明"]["不许出现数字的格子"])
        note = REG["_说明"]["历史称谓这一列不算依据"]
        for needle in ("沿革标识", "回 ⑧", "已废止"):
            self.assertIn(needle, note, f"豁免那一段少了：{needle}")
        for r in REG["历史税种称谓对照"]:
            self.assertTrue(r["动作"].strip())


class TestVocabularyIsShared(unittest.TestCase):
    """四值、三类缺口与动作、四态、两条红线词表、② 的追问句都引上游，本层不建第二套。"""

    def test_verdicts_are_tax_inspect_own_four(self):
        self.assertEqual(tax_intake.VERDICTS, (tax_inspect.CHECK_AGREED,
                                               tax_inspect.CHECK_CONFLICT,
                                               tax_inspect.CHECK_ONE_SIDED,
                                               tax_inspect.CHECK_NOT_TAKEN))
        self.assertEqual(REG["_说明"]["一致性核对四值"], list(tax_intake.VERDICTS))

    def test_gap_classes_and_actions_are_tax_inspect_own(self):
        """缺口类别与动作一律取 `tax_inspect` 那一张表；只有两处本层改写，改写要有注册表那句话签字。"""
        overridden = {tax_intake.CAUSE_CONTRACTS_DISAGREE: tax_intake.CHAIN_ACTION,
                      tax_intake.CAUSE_UNLOCATED: tax_intake.PENDING_ACTION}
        for cause in tax_intake.CAUSES:
            gap = tax_intake.CAUSE_GAP[cause]
            self.assertIn(gap, tax_inspect.GAP_TYPES)
            want = overridden.get(cause, tax_inspect.GAP_ACTION[gap])
            self.assertEqual(tax_intake.CAUSE_ACTION[cause], want,
                             f"{cause} 的动作既不是那一类缺口在 `tax_inspect` 里的动作，"
                             f"也不是本层登记过的改写")
            if cause in overridden:
                sentence = REG["_说明"]["成因各自的动作"][cause]
                needles = ("确认", "--confirm") if cause == tax_intake.CAUSE_CONTRACTS_DISAGREE \
                    else ("回 ③", "原文")
                for needle in needles:
                    self.assertIn(needle, sentence,
                                  f"这一格的动作被本层改写了，注册表那句话里却没写出改写后的动作")

    def test_coverage_states_are_tax_coverage_own_tuple(self):
        self.assertIs(tax_intake.STATES, tax_coverage.STATES)
        for row_state in (tax_intake.SATISFIED, tax_intake.LACK,
                          tax_intake.NA, tax_intake.TO_VERIFY):
            self.assertIn(row_state, tax_coverage.STATES)

    def test_unit_probe_sentences_come_from_step_two(self):
        for key, axis in tax_intake.AXIS_OF_UNIT_KEY.items():
            self.assertIn(axis, tax_analyze.CONTEXT_AXES)
        out = tax_intake.reconcile(with_doc(判断单元=[
            {**DOC["判断单元"][0], "主体": "", "业务所属期": ""}]))
        got = {a["缺"]: a["追问"] for a in out["②要补问"]}
        self.assertEqual(got["主体"], tax_analyze.CONTEXT_AXES["entity"]["probe"])
        self.assertEqual(got["业务所属期"], tax_analyze.CONTEXT_AXES["time"]["probe"])

    def test_no_second_copy_of_the_red_line_wordlists(self):
        head = SOURCE.split("class NeedsContracts")[0]
        for word in ("补造", "不会被稽查"):
            self.assertNotIn(word, head, "本层自己抄了一份红线词表，两份迟早各说各话")

    def test_digit_free_cells_are_listed_and_enforced(self):
        self.assertEqual(REG["_说明"]["不许出现数字的格子"],
                         list(tax_intake.DIGIT_FREE_CELLS))
        for cell in tax_intake.DIGIT_FREE_CELLS:
            if cell in tax_intake.DIMENSION_KEYS:
                continue
            if cell in tax_intake.ELEMENT_KEYS:
                continue
            self.assertIn(cell, ("要素五件套", "落点值域"),
                          "禁数字清单里混进了没人扫的格子名")


class TestRecordSources(unittest.TestCase):
    """来源三态与出处三格：抽取的可定位，填表的不冒充，没交的不代写。"""

    def test_extracted_record_without_any_cite_is_refused(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                               "履行": {"C1": {"对价总额与价外费用":
                                            {"来源": "抽取", "金额": "1000000"}}}}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("原文出处", str(ctx.exception))

    def test_partial_cite_makes_the_whole_element_unverified(self):
        out = tax_intake.reconcile(DOC, confirmed=("E1",))
        hit = find(out, "C4", "开票顺序与时点")
        self.assertEqual(len(hit), 1, "出处缺格时整条只出一句，不按槽各出一句")
        self.assertEqual(hit[0]["核对结论"], tax_intake.CAUSE_VERDICT[tax_intake.CAUSE_UNLOCATED])
        self.assertIn("整条未参与比对", hit[0]["条款侧"])
        self.assertNotEqual(hit[0]["条款侧"], "未提供",
                            "值给了，标成『未提供』会让人去要一份根本不缺的合同")

    def test_cite_without_value_counts_as_not_submitted(self):
        out = tax_intake.intake(DOC)
        self.assertIn("C5／扣缴义务与申报期限", out["未填的要素"],
                      "只给了摘录没给值：那一条算没交")
        untr = [x for x in out["待核清单"] if x["要素"].startswith("C5")]
        self.assertEqual(untr, [], "没交的记录不该同时出现在待核里")

    def test_fill_source_needs_no_cite_but_is_labelled(self):
        out = tax_intake.reconcile(DOC)
        agreed = find(out, "C4", "发票种类与票面税率", "比例", tax_intake.CHECK_AGREED)
        self.assertEqual(len(agreed), 1, "填表的值照样参与比对，只是要标出来源")
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                              "履行": {"C6": {"税费承担与含税口径":
                                           {"来源": "填表", "期限": "买方承担"}}}}])
        got = find(tax_intake.reconcile(d), "C6", "税费承担与含税口径")
        self.assertTrue(got, "填表的值没比对")
        self.assertIn(tax_intake.SOURCE_TAG, "｜".join(got[0]["依据链"]),
                      "依据链里看不出这一格是填表来的还是要回原件核对的")
        self.assertIn("填表，未经抽取核对", tax_intake.render(tax_intake.reconcile(d)))

    def test_absent_source_with_a_value_is_refused(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                               "履行": {"C1": {"对价总额与价外费用":
                                            {"来源": "未提供", "金额": "1000000"}}}}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("两者说的相反", str(ctx.exception))

    def test_cite_pointing_at_another_contract_is_refused(self):
        d = copy.deepcopy(DOC)
        d["合同"][0]["条款"]["C1"]["对价总额与价外费用"]["原文出处"]["文件"] = "CT-2"
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("两份文件", str(ctx.exception))

    def test_unknown_slot_key_is_refused_not_silently_absent(self):
        d = copy.deepcopy(DOC)
        d["合同"][0]["条款"]["C1"]["对价总额与价外费用"]["金额（元）"] = "1130000"
        with self.assertRaises(ValueError) as ctx:
            tax_intake._contracts(d, REG)
        self.assertIn("认不出的键", str(ctx.exception))

    def test_unknown_dimension_or_element_in_input_is_named(self):
        with self.assertRaises(ValueError) as ctx:
            tax_intake._contracts({"合同": [{"代号": "C", "名称": "n",
                                           "条款": {"C10": {}}}]}, REG)
        self.assertIn("模板没有的维度代号 C10", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            tax_intake._contracts({"合同": [{"代号": "C", "名称": "n",
                                           "条款": {"C1": {"对价总额": {}}}}]}, REG)
        self.assertIn("不在模板里", str(ctx.exception))


class TestNumericSlots(unittest.TestCase):
    """金额与比例按数值判等：千分位与尾格百分号是写法，不是另一个数。"""

    def test_thousands_separators_are_the_same_amount(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                               "履行": {"C1": {"对价总额与价外费用":
                                            {"来源": "填表", "金额": "1,130,000",
                                             "口径": "含税"}}}}])
        got = find(tax_intake.reconcile(d), "C1", "对价总额与价外费用", "金额",
                   tax_intake.CHECK_AGREED)
        self.assertEqual(len(got), 1, got)

    def test_percent_sign_is_not_rescaled(self):
        self.assertEqual(tax_intake._num(" 13 % ", "测试", "比例"),
                         tax_intake._num("13", "测试", "比例"))
        self.assertEqual(str(tax_intake._num("1,130,000", "测试", "金额")), "1130000")

    def test_percent_and_its_decimal_form_are_reported_as_conflict(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                               "履行": {"C4": {"发票种类与票面税率":
                                            {"来源": "填表", "比例": "0.13"}}}}])
        got = find(tax_intake.reconcile(d), "C4", "发票种类与票面税率", "比例")
        self.assertEqual(got[0]["核对结论"], tax_intake.CHECK_CONFLICT)
        self.assertIn("13%", got[0]["条款侧"])
        self.assertIn("0.13", got[0]["履行侧"],
                      "两侧原值都要显示，让读的人回原文认那一个口径")

    def test_prose_in_a_numeric_cell_names_the_cell_and_the_form(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                               "履行": {"C1": {"对价总额与价外费用":
                                            {"来源": "填表", "金额": "详见附件"}}}}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        msg = str(ctx.exception)
        self.assertIn("详见附件", msg)
        self.assertIn("口径", msg, "报错要指出去处：单位与币种那一格才是说法该写的地方")

    def test_different_calibers_are_not_turned_into_one_number(self):
        out = tax_intake.reconcile(DOC)
        got = find(out, "C1", "对价总额与价外费用", "金额",
                   tax_intake.CAUSE_VERDICT[tax_intake.CAUSE_INCOMPARABLE])
        self.assertEqual(got[0]["缺口类别"], tax_inspect.GAP_CALIBER)
        self.assertIn("不折算", got[0]["差异说明"])

    def test_dates_are_ordered_rather_than_subtracted(self):
        out = tax_intake.reconcile(DOC, confirmed=("E1",))
        got = find(out, "C3", "付款期限与分期安排", "期限")
        self.assertEqual(got[0]["核对结论"], tax_intake.CHECK_CONFLICT)
        self.assertIn("早于", got[0]["差异说明"])

    def test_numbers_in_a_text_slot_are_compared_as_said(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                               "履行": {"C3": {"预付款与质保金":
                                            {"来源": "填表", "期限": "质保期 12 个月"}}}}])
        got = find(tax_intake.reconcile(d), "C3", "预付款与质保金", "期限",
                   tax_intake.CHECK_AGREED)
        self.assertEqual(len(got), 1)


class TestChainIsCandidateOnly(unittest.TestCase):
    """边只做候选：没确认不作废任何条款，确认了也只按要素落位。"""

    def test_edges_start_pending_and_carry_the_confirm_action(self):
        chain = tax_intake.build_chain(DOC, tax_intake._contracts(DOC, REG))
        self.assertEqual([e["状态"] for e in chain["边"]], [tax_intake.PENDING])
        self.assertEqual(len(chain["待用户确认"]), 1)
        self.assertIn("--confirm", chain["待用户确认"][0]["动作"])
        self.assertEqual(chain["已确认替代"], [])

    def test_confirmation_is_by_edge_code(self):
        chain = tax_intake.build_chain(DOC, tax_intake._contracts(DOC, REG), ("E1",))
        self.assertEqual(chain["边"][0]["状态"], tax_intake.CONFIRMED)
        self.assertEqual([e["代号"] for e in chain["已确认替代"]], ["E1"])

    def test_unconfirmed_replacement_keeps_both_clause_values(self):
        out = tax_intake.reconcile(DOC)
        got = find(out, "C3", "付款期限与分期安排", "期限")
        self.assertEqual(got[0]["核对结论"],
                         tax_intake.CAUSE_VERDICT[tax_intake.CAUSE_CONTRACTS_DISAGREE])
        self.assertIn("CT-1", got[0]["条款侧"])
        self.assertIn("CT-2", got[0]["条款侧"])
        self.assertEqual(got[0]["动作"], tax_intake.CHAIN_ACTION)

    def test_confirmed_replacement_drops_only_the_shared_element(self):
        out = tax_intake.reconcile(DOC, confirmed=("E1",))
        self.assertEqual(out["单元"][0]["被替代而未采信的条款"],
                         ["CT-1／C3 付款／付款期限与分期安排（由 CT-2 依边 E1 替代）"])
        self.assertEqual(out["范围"]["被替代而未采信的条款"],
                         out["单元"][0]["被替代而未采信的条款"])
        got = find(out, "C3", "付款期限与分期安排", "期限")
        self.assertEqual(got[0]["条款侧"], "2026-06-30",
                         "确认后条款侧只剩在后那一份的这一段")

    def test_other_clauses_of_the_replaced_contract_still_count(self):
        """整份作废会把没被改动的条款一起摘掉，报成『合同条款里没找到这一格』。"""
        out = tax_intake.reconcile(DOC, confirmed=("E1",))
        self.assertIn("合同 CT-1", find(out, "C1", "对价总额与价外费用", "金额")[0]["依据链"][0])
        self.assertIn("合同 CT-1", find(out, "C3", "预付款与质保金", "期限")[0]["依据链"][0])
        self.assertEqual([t["合同"] for t in out["触发时点清单"]],
                         ["CT-1", "CT-1"], "原合同其余条款的时点仍要交给 ②")
        self.assertNotIn("C3／付款期限与分期安排",
                         [t["要素"] for t in out["触发时点清单"]])

    def test_element_absent_from_the_later_contract_is_not_replaced(self):
        d = copy.deepcopy(DOC)
        d["合同"][1]["条款"]["C3"].pop("付款期限与分期安排")
        out = tax_intake.reconcile(d, confirmed=("E1",))
        self.assertEqual(out["单元"][0]["被替代而未采信的条款"], [],
                         "后一份没写这一条，旧条款没有被取代")

    def test_mutation_supersession_that_ignores_the_later_record(self):
        """把落位判据换成"只要在先就作废"，未采信的条款就会多到开票那一维。"""
        real = tax_intake.superseded_cells
        tax_intake.superseded_cells = lambda chain, contracts: {
            (a, dim, ename): {"由": chain["已确认替代"][0]["在后"],
                              "边": chain["已确认替代"][0]["代号"]}
            for e in chain["已确认替代"] for a in (e["在先"],)
            for dim, row in contracts[a]["条款"].items() for ename in row}
        try:
            wide = tax_intake.reconcile(DOC, confirmed=("E1",))
            self.assertGreater(len(wide["单元"][0]["被替代而未采信的条款"]), 1)
        finally:
            tax_intake.superseded_cells = real
        self.assertEqual(len(tax_intake.reconcile(
            DOC, confirmed=("E1",))["单元"][0]["被替代而未采信的条款"]), 1)

    def test_edge_to_a_missing_contract_is_refused(self):
        d = with_doc(边=[{**DOC["边"][0], "在后合同": "CT-9"}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("清单里没有", str(ctx.exception))

    def test_self_edge_is_refused(self):
        d = with_doc(边=[{**DOC["边"][0], "在先合同": "CT-1", "在后合同": "CT-1"}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("同一份合同", str(ctx.exception))

    def test_mutual_replacement_is_refused(self):
        d = with_doc(边=DOC["边"] + [{"代号": "E2", "在先合同": "CT-2", "在后合同": "CT-1",
                                    "类型": "替代", "来源": "填表", "原文出处": None}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d, confirmed=("E1", "E2"))
        self.assertIn("互相", str(ctx.exception).replace("替回", "互相替回", 1)
                      if False else str(ctx.exception))
        self.assertIn("条款未提供", str(ctx.exception))

    def test_extracted_edge_without_a_quote_is_refused(self):
        d = with_doc(边=[{**DOC["边"][0], "原文出处": None}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("这条边就只是猜", str(ctx.exception))

    def test_edge_kind_outside_the_four_is_refused(self):
        d = with_doc(边=[{**DOC["边"][0], "类型": "合并"}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("不在取值域", str(ctx.exception))

    def test_pending_edge_with_bad_cite_says_so(self):
        d = with_doc(边=[{**DOC["边"][0], "原文出处": {"文件": "CT-2", "位置": "",
                                                  "摘录": "原条款不再执行"}}])
        chain = tax_intake.build_chain(d, tax_intake._contracts(d, REG))
        self.assertFalse(chain["待用户确认"][0]["出处可用"])
        self.assertIn("先补齐", chain["待用户确认"][0]["动作"])


class TestFiveCausesAreReachable(unittest.TestCase):
    """注册表那五种成因，每一种都要有活路径产出一条目，且结论与缺口类别对上那张表。"""

    def test_all_five_appear_across_two_runs(self):
        out = tax_intake.reconcile(DOC)
        self.assertIn(tax_intake.CAUSE_NO_PERFORMANCE, causes(out))
        self.assertIn(tax_intake.CAUSE_INCOMPARABLE, causes(out))
        self.assertIn(tax_intake.CAUSE_CONTRACTS_DISAGREE, causes(out))
        self.assertIn(tax_intake.CAUSE_UNLOCATED, causes(out))
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                              "履行": {"C7": {"验收标准与验收时点":
                                           {"来源": "填表", "期限": "验收合格"}}}}])
        self.assertIn(tax_intake.CAUSE_NO_CLAUSE, causes(tax_intake.reconcile(d)))

    def test_cause_mapping_matches_the_two_tables(self):
        for cause in tax_intake.CAUSES:
            self.assertIn(tax_intake.CAUSE_VERDICT[cause], tax_intake.VERDICTS)
            self.assertIn(tax_intake.CAUSE_GAP[cause], tax_inspect.GAP_TYPES)
            sentence = REG["_说明"]["成因各自的动作"][cause]
            self.assertIn(tax_intake.CAUSE_VERDICT[cause], sentence)
            self.assertIn(tax_intake.CAUSE_GAP[cause], sentence)

    def test_missing_clause_side_is_one_sided_and_asks_for_the_paper(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0],
                              "履行": {"C7": {"验收标准与验收时点":
                                           {"来源": "填表", "期限": "验收合格"}}}}])
        got = find(tax_intake.reconcile(d), "C7", "验收标准与验收时点")
        self.assertEqual(got[0]["核对结论"], tax_intake.CHECK_ONE_SIDED)
        self.assertEqual(got[0]["缺口类别"], tax_inspect.GAP_MISSING)
        self.assertIn("合同条款里没找到这一格", got[0]["差异说明"])

    def test_missing_performance_side_carries_the_no_record_note(self):
        out = tax_intake.reconcile(DOC)
        got = find(out, "C3", "预付款与质保金", "触发时点")
        self.assertEqual(got[0]["核对结论"], tax_intake.CHECK_ONE_SIDED)
        self.assertIn(tax_intake.NO_RECORD_NOTE, got[0]["动作"])

    def test_both_empty_produces_no_entry(self):
        """两侧都没交不构成一对：那一条报在受理覆盖的『缺』里，不产条目。"""
        out = tax_intake.reconcile(DOC)
        self.assertEqual(find(out, "C9", "代垫费用与报销范围"), [])
        self.assertTrue(any(x.startswith("C9") for x in tax_intake.intake(DOC)["未填的要素"]))

    def test_the_five_causes_are_exactly_the_registered_ones(self):
        out = tax_intake.reconcile(DOC)
        for r in out["差异条目"] + out["无法确认条目"]:
            tags = [c for c in tax_intake.CAUSES if f"成因：{c}" in r["差异说明"]]
            self.assertTrue(len(tags) <= 1, r["差异说明"])

    def test_mutation_the_cause_table_would_leave_a_hole(self):
        """把『履行记录缺失』的结论改成『一致』，无法确认条目就会掉进一致里——用例要报红。

        注册表照常载入（`validate()` 那道同源检查管的是表与代码一起漂，这一处变异只改代码），
        所以这里显式把已经载入的表传进去，绕开表侧检查、只留输出侧的判据。
        """
        real = dict(tax_intake.CAUSE_VERDICT)
        tax_intake.CAUSE_VERDICT[tax_intake.CAUSE_NO_PERFORMANCE] = tax_intake.CHECK_AGREED
        try:
            out = tax_intake.reconcile(DOC, reg=REG)
            self.assertTrue(any("履行记录缺失" in r["差异说明"] for r in out["一致明细"]),
                            "改坏了结论表，『一致』里却没多出任何一条——这一判据是空的")
        finally:
            tax_intake.CAUSE_VERDICT.clear()
            tax_intake.CAUSE_VERDICT.update(real)
        out = tax_intake.reconcile(DOC)
        self.assertEqual(out["核对"][tax_intake.CHECK_AGREED], 2)


class TestUnitsAreHonest(unittest.TestCase):
    """判断单元按 ② 那四个名字拆；缺格要追问，不适用要带理由。"""

    def test_missing_cell_becomes_a_question_not_a_skip(self):
        out = tax_intake.reconcile(DOC)
        self.assertEqual([a["缺"] for a in out["②要补问"]], ["资格条件"])
        self.assertIn("归给谁", out["②要补问"][0]["追问"])
        self.assertTrue(out["核对"]["总成对数"], "缺格不是不比对的理由")

    def test_policy_is_a_value_not_a_fifth_knife(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0], "适用政策": "财税〔2016〕36号"},
                             {**DOC["判断单元"][0], "代号": "U2",
                              "适用政策": "国税总局公告2026年第1号"}])
        out = tax_intake.reconcile(d)
        self.assertEqual(out["判断单元数"], 2, "单元由调用方按四格定，本层不自动并也不自动拆")
        self.assertEqual([u["适用政策"] for u in out["单元"]],
                         ["财税〔2016〕36号", "国税总局公告2026年第1号"])

    def test_unknown_unit_key_is_refused(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0], "甲方": "本公司"}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("认不出的键", str(ctx.exception))

    def test_unit_without_contracts_is_refused(self):
        d = with_doc(判断单元=[{**DOC["判断单元"][0], "合同": []}])
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(d)
        self.assertIn("不知道由哪几份合同的条款来说", str(ctx.exception))

    def test_duplicate_codes_are_refused(self):
        for key, label in (("合同", "合同代号"), ("判断单元", "判断单元代号"),
                           ("边", "边代号")):
            d = with_doc(**{key: DOC[key] + copy.deepcopy([DOC[key][0]])})
            if key == "判断单元":
                d["合同"] = DOC["合同"]
            with self.assertRaises(ValueError) as ctx:
                tax_intake.reconcile(d)
            self.assertIn(label, str(ctx.exception))

    def test_absent_dimension_needs_a_reason_and_is_skipped(self):
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(with_doc(不适用={"C5": ""}))
        self.assertIn("是空的", str(ctx.exception))
        out = tax_intake.reconcile(with_doc(不适用={"C5": "本单没有代扣代缴事项"}))
        self.assertEqual(find(out, "C5", "扣缴义务与申报期限"), [])
        self.assertIn("C5", out["范围"]["不适用维度"])

    def test_nothing_submitted_is_a_worklist_not_an_empty_report(self):
        for empty in ({"合同": []}, {"合同": DOC["合同"], "判断单元": []}):
            with self.assertRaises(tax_intake.NeedsContracts) as ctx:
                tax_intake.reconcile(empty)
            self.assertIn("--blank", str(ctx.exception))


class TestCoverageIsHonest(unittest.TestCase):
    def test_states_are_counted_separately(self):
        out = tax_intake.intake(DOC)
        self.assertEqual((out["已满足维度数"], out["待核维度数"],
                          out["缺维度数"], out["不适用维度数"]), (3, 0, 5, 1))
        self.assertEqual(out["覆盖"]["状态"], tax_inspect.COVERAGE_PARTIAL)
        self.assertEqual(out["覆盖"]["按维度"][7]["状态"], tax_intake.NA)
        self.assertTrue(out["覆盖"]["按维度"][7]["不适用理由"])

    def test_clue_hit_without_submission_is_flagged(self):
        out = tax_intake.intake(DOC)
        got = [x for x in out["线索命中却没交"] if x["要素"].startswith("C7")]
        self.assertTrue(got, "验收那一维没交，但第六条的摘录里出现过『验收合格』")
        self.assertIn("验收合格", got[0]["命中"][0])

    def test_clue_hit_does_not_quote_the_element_against_itself(self):
        d = copy.deepcopy(DOC)
        row = d["合同"][0]["条款"]["C1"]["合同双方与纳税主体"]
        row["原文出处"]["摘录"] = "甲方：本公司；乙方指定收款账户为丙方名下账户"
        out = tax_intake.intake(d)
        got = [x for x in out["线索命中却没交"] if x["要素"] == "C1／合同双方与纳税主体"]
        self.assertTrue(all("C1／合同双方与纳税主体 的摘录" not in h for h in got[0]["命中"]),
                        "拿这一条自己的摘录跟自己那条线索相对，报出来的『别处有』是假话")

    def test_scoped_run_says_what_was_not_compared(self):
        out = tax_intake.reconcile(DOC, dims=("C3",))
        self.assertEqual(out["范围"]["比对维度"], ["C3"])
        self.assertEqual(out["范围"]["筛选条件"], "C3")
        self.assertNotIn("C4", out["核对"]["按维度"])
        text = tax_intake.render(out)
        self.assertIn("没比不等于没问题", text)

    def test_unknown_dimension_filter_is_rejected_with_the_domain(self):
        with self.assertRaises(ValueError) as ctx:
            tax_intake.reconcile(DOC, dims=("C10",))
        self.assertIn("C1", str(ctx.exception))

    def test_missing_record_is_never_written_as_a_finding_of_fact(self):
        banned = ("未付款", "未发生", "无需申报", "不涉及该税种", "没有发生")
        out = tax_intake.reconcile(DOC)
        text = (json.dumps(out, ensure_ascii=False) + tax_intake.render(out))
        text = text.replace(tax_intake.NO_RECORD_NOTE, "")
        for phrase in banned:
            self.assertNotIn(phrase, text, f"输出把缺记录写成了事实结论：{phrase}")

    def test_the_gap_list_is_classified_and_feedable(self):
        out = tax_intake.reconcile(DOC)
        self.assertEqual(sum(out["证据缺口按类"].values()), len(out["证据缺口"]))
        self.assertEqual(sorted(out["证据缺口按类"]), sorted(tax_inspect.GAP_TYPES))
        for g in out["证据缺口"]:
            self.assertIn("单元 U1", g["对象"])


class TestTimePoints(unittest.TestCase):
    def test_points_are_collected_with_their_cites_and_source_state(self):
        out = tax_intake.intake(DOC)
        got = out["触发时点清单"]
        self.assertEqual({(t["合同"], t["要素"]) for t in got},
                         {("CT-1", "C3／付款期限与分期安排"),
                          ("CT-1", "C3／预付款与质保金"),
                          ("CT-1", "C4／开票顺序与时点")})
        by = {t["要素"]: t for t in got}
        self.assertEqual(by["C3／付款期限与分期安排"]["出处"], "第六条")
        self.assertTrue(by["C3／付款期限与分期安排"]["出处可用"])
        self.assertEqual(by["C4／开票顺序与时点"]["来源"], "抽取")

    def test_a_replaced_element_leaves_the_point_list(self):
        """确认替代之后，旧合同那一条付款节点不再是问 ② 的那一个；同一合同其余要素照常收。"""
        before = {(t["合同"], t["要素"]) for t in tax_intake.intake(DOC)["触发时点清单"]}
        after = {(t["合同"], t["要素"])
                 for t in tax_intake.reconcile(DOC, confirmed=("E1",))["触发时点清单"]}
        self.assertEqual(before - after, {("CT-1", "C3／付款期限与分期安排")})
        self.assertIn(("CT-1", "C3／预付款与质保金"), after)

    def test_fill_source_is_marked_as_unverified_extraction(self):
        """触发时点是填进来的而不是从合同抽的：清单上要标出来，并带『出处待核』。"""
        c1 = copy.deepcopy(DOC["合同"][0])
        c1["条款"]["C7"] = {"验收标准与验收时点": {"来源": "填表", "触发时点": "到货后验收"}}
        out = tax_intake.reconcile(with_doc(合同=[c1, copy.deepcopy(DOC["合同"][1])]))
        got = [t for t in out["触发时点清单"] if t["要素"] == "C7／验收标准与验收时点"]
        self.assertEqual([t["来源"] for t in got], ["填表"])
        self.assertEqual(got[0]["出处"], "", "填表没有原文可指")
        text = tax_intake.render(out)
        self.assertIn("填表，未经抽取核对", text)


class TestHistoricalNames(unittest.TestCase):
    def test_old_tax_name_in_a_quote_is_flagged_not_reclassified(self):
        out = tax_intake.intake(DOC)
        hits = {h["称谓"]: h for h in out["历史称谓提醒"]}
        self.assertIn("营业税", hits)
        self.assertIn("开票顺序与时点", hits["营业税"]["出现在"])
        self.assertNotIn("应纳税额", json.dumps(hits, ensure_ascii=False))

    def test_historical_names_do_not_collide_with_live_ones(self):
        live = set(tax_search.TAX_TYPE_KEYWORDS)
        for name in REG["_说明"]["历史税种称谓"]:
            self.assertNotIn(name, live, f"{name} 是现行税种名，登记成历史称谓会误提醒")

    def test_bridge_rows_point_at_an_observation_time(self):
        """每一条动作都要留一个时间锚：少了它，『归到车船税』会被读成按今天的税额表顶位。"""
        needles = REG["_说明"]["重查指针的说法"]
        self.assertEqual(needles, list(tax_intake.RECHECK_NEEDLES))
        for r in REG["历史税种称谓对照"]:
            self.assertTrue(r["现行"].strip())
            self.assertTrue(r["检索线索"])
            self.assertTrue([n for n in needles if n in r["动作"]],
                            f"{r['称谓']} 的『动作』没写出这一笔按哪一时点的规定判")

    def test_historical_action_without_a_time_anchor_is_caught(self):
        def poke(reg):
            reg["历史税种称谓对照"][2]["动作"] = "历史资产清单里出现这个称谓时归到车船税。"
        self.assertIn("哪一时点", mutate(poke))

    def test_digit_in_recheck_needles_is_caught(self):
        both = tuple(tax_intake.RECHECK_NEEDLES) + ("2008 年度",)
        with mock.patch.object(tax_intake, "RECHECK_NEEDLES", both):
            def poke(reg):
                reg["_说明"]["重查指针的说法"] = list(both)
            self.assertIn("数字", mutate(poke))

    def test_render_shows_the_three_cells(self):
        text = tax_intake.render_intake(tax_intake.intake(DOC))
        self.assertIn("[历史税种称谓] 营业税", text)
        self.assertIn("检索线索：", text)


class TestRender(unittest.TestCase):
    def test_eleven_entry_cells_are_printed(self):
        text = tax_intake.render(tax_intake.reconcile(DOC))
        for needle in ("核对结论分布", "条款侧", "履行侧", "差异说明", "动作：", "依据链："):
            self.assertIn(needle, text, f"渲染里少了这一格：{needle}")
        for r in tax_intake.reconcile(DOC)["差异条目"]:
            self.assertEqual(list(r), list(tax_intake.ENTRY_KEYS))

    def test_unit_block_prints_the_four_cells_and_the_replaced_clauses(self):
        text = tax_intake.render(tax_intake.reconcile(DOC, confirmed=("E1",)))
        self.assertIn("【单元 U1】", text)
        self.assertIn("资格未给", text)
        self.assertIn("被替代而未采信：CT-1／C3", text)

    def test_verdict_distribution_is_printed_not_just_a_pass_count(self):
        text = tax_intake.render(tax_intake.reconcile(DOC))
        self.assertIn(f"一致 {2}｜矛盾", text)
        self.assertNotIn("无风险", text)

    def test_render_carries_no_prediction(self):
        for out in (tax_intake.reconcile(DOC), tax_intake.intake(DOC)):
            text = (tax_intake.render(out) if "核对" in out
                    else tax_intake.render_intake(out))
            for phrase in tax_inspect.PREDICTION_PHRASES:
                self.assertNotIn(phrase, text, f"渲染里出现稽查结果预测 {phrase}")
        # 边界那一句自己也得过同一把尺子：写下『不预测会不会被稽查』本身就含着一句预测。
        self.assertIn("也不预测检查结果", tax_intake.render(tax_intake.reconcile(DOC)))

    def test_boundary_names_the_next_layer_and_the_ruling_this_layer_makes_not(self):
        text = tax_intake.render(tax_intake.reconcile(DOC))
        for needle in ("tax_risk_framework.md", "tax_cases.py", "tax_inspect.py",
                       "替代按要素落位", "风险等级落在"):
            self.assertIn(needle, text, f"边界那一段少了：{needle}")

    def test_scanned_cells_never_carry_forbidden_remediation(self):
        out = tax_intake.reconcile(DOC)
        for r in rows(out):
            for cell in tax_intake.RED_SCAN_CELLS:
                hit = tax_inspect._bad_words(r[cell] or "",
                                             tax_inspect.FORBIDDEN_REMEDIATION)
                self.assertEqual(hit, [], f"{r['要素']} 的『{cell}』写了 {hit}")


class TestCli(unittest.TestCase):
    def cli(self, *argv):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "tax_intake.py"),
                               *argv], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(ROOT))

    def _file(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(DOC, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_list_names_every_dimension_and_the_input_keys(self):
        r = self.cli("--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        for d in REG["维度"]:
            self.assertIn(f"{d['代号']} {d['维度']}", r.stdout)
        for field in ("一条记录认的键", "一条边认的键", "一个判断单元认的键", "成因"):
            self.assertIn(field, r.stdout, f"--list 少了 {field}：填表的人照不到键名")
        self.assertIn("营业税", r.stdout)

    def test_blank_is_the_fillable_form(self):
        r = self.cli("--blank")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got, tax_intake.blank(REG))

    def test_intake_and_reconcile_agree_with_the_in_process_call(self):
        path = self._file()
        try:
            r = self.cli("--reconcile", path, "--confirm", "E1", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout),
                             tax_intake.reconcile(DOC, confirmed=["E1"]))
            r2 = self.cli("--intake", path)
            self.assertEqual(r2.stdout, tax_intake.render_intake(tax_intake.intake(DOC)) + "\n")
        finally:
            Path(path).unlink()

    def test_dimension_flag_reaches_the_engine(self):
        path = self._file()
        try:
            r = self.cli("--reconcile", path, "--dimension", "C3", "--json")
            got = json.loads(r.stdout)
            self.assertEqual(got["范围"]["比对维度"], ["C3"])
            self.assertEqual(got, tax_intake.reconcile(DOC, dims=["C3"]))
        finally:
            Path(path).unlink()

    def test_no_input_returns_one_with_the_two_commands(self):
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("--blank", r.stdout)
        self.assertIn("--intake", r.stdout)

    def test_bad_json_exits_two_without_a_traceback(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        f.write('{"合同": [}')
        f.close()
        try:
            r = self.cli("--intake", f.name)
            self.assertEqual(r.returncode, 2)
            self.assertIn("error", r.stdout)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            Path(f.name).unlink()

    def test_a_code_error_is_labelled_with_its_type(self):
        """内部键缺失只回吐一个 '口径' 会把代码故障读成政策问题，所以带上类型名。

        在进程内跑 `main()`：子进程用的是另一份模块，改这里的函数打不进去。
        """
        path = self._file()
        buf = io.StringIO()
        try:
            with mock.patch.object(tax_intake, "time_points",
                                   lambda contracts, dropped=None: [{"缺这一格": 1}]):
                with contextlib.redirect_stdout(buf):
                    code = tax_intake.main(["--reconcile", path])
        finally:
            Path(path).unlink()
        self.assertEqual(code, 2)
        got = json.loads(buf.getvalue())
        self.assertIn("KeyError", got["error"],
                      f"只回吐『{got['error']}』看不出这是代码故障：{got}")


def bridge_table(md: str) -> list:
    """读 `tax_categories.md` 那张历史称谓表，回 [(称谓, 现行衔接, 检索词清单)]。

    表格行按竖线切；表头与分隔行认不出来就当数据行，宁可让上面那条比对报红，
    也不在这里静默跳行——静默跳行等于少查一行而报告说全查过了。
    """
    body = md.split("## 历史税种称谓到现行税种的衔接")[1].split("\n## ")[0]
    rows = []
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 3 or set(cells[0]) <= set("-: "):
            continue
        if cells[0] == "历史称谓":
            continue
        rows.append((cells[0], cells[1], cells[2].split("、")))
    return rows


class TestDocsShareTheImplementation(unittest.TestCase):
    """文档与本表说同一件事；漂了就报红，而不是各说各话。"""

    def test_skill_routes_to_the_module(self):
        self.assertIn("scripts/tax_intake.py", SKILL)
        self.assertIn("data/contract_intake_template.json", SKILL)

    def test_quick_index_has_the_two_rows(self):
        """两行各管一侧：受理那一行在 ② 前，对账那一行在 ⑥ 前。"""
        rows = {n: [ln for ln in SKILL.splitlines() if n in ln]
                for n in ("--blank/--intake", "--reconcile")}
        for needle, want_step in (("--blank/--intake", "② 前"), ("--reconcile", "⑥ 前")):
            self.assertEqual(len(rows[needle]), 1,
                             f"快速索引里 {needle} 那一行出现 {len(rows[needle])} 次")
            self.assertIn(want_step, rows[needle][0],
                          f"{needle} 那一行没标在 {want_step}，读者会走错时点")

    def test_skill_step_two_says_the_time_points_come_from_this_layer(self):
        body = SKILL.split("## ② 前提补齐")[1].split("## ③")[0]
        flat = re.sub(r"\s+", "", body)
        for needle in ("tax_intake.py", "触发时点", "时点清单", "不判那个时点上政策是什么"):
            self.assertIn(needle, flat, f"② 那一段少了：{needle}")

    def test_red_line_27_is_the_intake_one(self):
        body = SKILL.split("\n## ⑦ ")[1].split("\n## ")[0]
        nums = [int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", body, re.M)]
        self.assertEqual(nums, list(range(1, len(nums) + 1)), "⑦ 的红线编号不连续")
        item = re.search(r"(?ms)^27\. .*?(?=^\d+\. |\Z)", body).group(0)
        for needle in ("合同链", "生效顺序", "出处", "第 25 条"):
            self.assertIn(needle, item, f"第 27 条少了：{needle}")

    def test_step_six_names_the_layer_without_a_new_form(self):
        body = SKILL.split("## ⑥ 输出格式模板")[1].split("## ⑦")[0]
        for needle in ("tax_intake.py", "--confirm", "判断单元", "无法确认",
                       "九种输出形态", "不新增第 10 种形态", "要素落位"):
            self.assertIn(needle, body, f"⑥ 那一段少了：{needle}")

    def test_template_conditional_block_lives_inside_the_risk_section(self):
        body = TEMPLATES.split("## 风险自检专用输出")[1].split("## 稽查模拟问询式")[0]
        self.assertIn("多合同受理条件块", body)
        self.assertIn("tax_intake.py", body)
        for key in tax_intake.ENTRY_KEYS:
            self.assertIn(key, body, f"模板里少了『{key}』这一格")
        for v in tax_intake.VERDICTS:
            self.assertIn(v, body, f"模板里没写比对结论的这一档：{v}")
        for c in tax_intake.CAUSES:
            self.assertIn(c, body, f"模板里没写成因的这一种：{c}")
        self.assertIn("一份合同都没给，本块未执行", body, "条件块要交代缺口的写法，不能整块抹掉")

    def test_template_keeps_nine_forms_after_the_block(self):
        from test_doc_contract import h2_titles
        heads = [t for t in h2_titles(TEMPLATES) if t != "目录"]
        self.assertEqual(len(heads), 9,
                         f"多合同受理该挂在既有那一式里，现在却有 {len(heads)} 个小节")

    def test_ownership_table_and_boundary_are_recorded(self):
        for needle in ("tax_intake.intake", "tax_intake.NeedsContracts",
                       "tax_intake.DIGIT_FREE_CELLS", "多合同受理的覆盖面"):
            self.assertIn(needle, DEFECTS, f"归属表或边界那几段少了：{needle}")

    def test_readme_lists_the_layer_the_registry_and_the_gate(self):
        for needle in ("tax_intake.py", "contract_intake_template.json",
                       "多合同受理", "被替代而未采信的条款"):
            self.assertIn(needle, README, f"README 少了：{needle}")

    def test_categories_bridge_table_matches_the_registry(self):
        """`tax_categories.md` 那三列逐格等于注册表『历史税种称谓对照』。

        两处各写一套旧税名时，读文档的人会把文档那一份当成口径；这一判据只认逐字相等，
        所以两边一起改也躲不过——除非两边一起改回同一个值。
        """
        rows = bridge_table(CATEGORIES)
        want = list(tax_intake.historical(REG).items())
        self.assertEqual([r[0] for r in rows], [name for name, _ in want],
                         "文档里那一张表的称谓行与注册表不同序或少了一行")
        for (name, cur, clues), (_, reg_row) in zip(rows, want):
            self.assertEqual(cur, reg_row["现行"], f"{name} 的『现行衔接』与注册表不一致")
            self.assertEqual(clues, reg_row["检索线索"], f"{name} 的检索线索与注册表不一致")

    def test_mutation_a_drifted_bridge_cell_is_caught(self):
        """自检：把文档里一格『现行衔接』改了字，比对必须报红而不是各自绿。"""
        broken = CATEGORIES.replace("改征增值税；全面推开试点的文件是财税〔2016〕36号",
                                    "改征增值税；全面推开试点另发过文件", 1)
        drift = [(n, c) for n, c, _ in bridge_table(broken)
                 if c != tax_intake.historical(REG)[n]["现行"]]
        self.assertEqual([n for n, _ in drift], ["营业税"],
                         "改了文档那一格却检不出来，说明表格提取或比对是空的")

    def test_registry_readers_all_exist(self):
        """注册表自称"谁在读"，那几份文件就得真在——写下不存在的读者等于没写。"""
        readers = REG["_说明"]["谁在读"]
        self.assertGreaterEqual(len(readers), 5,
                                "『谁在读』少了就没东西可查，这一判据会静默通过")
        for who in readers:
            rel = who.split("（")[0].strip()
            self.assertTrue((ROOT / rel).exists(), f"注册表说 {rel} 在读这一格，文件却不在")
            self.assertTrue(Path(rel).suffix, f"{rel} 不像路径")

    def test_gate_actually_runs_this_file(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import run_all
        listed = " ".join(" ".join(map(str, cmd))
                          for _name, cmd in run_all.OFFLINE_GROUP if not callable(cmd))
        self.assertIn("test_intake.py", listed,
                      "tests/run_all.py 的离线组没点名 test_intake.py，这一整份用例一次都不会跑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
