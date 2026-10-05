#!/usr/bin/env python3
"""账套巡检模块 `scripts/tax_ledger.py` 的离线用例。不联网、不调模型。

守的是七类失效路径，`validate()` 的每一处判据都配一次"把注册表改坏"的自检：
改坏了却不报红，就是用例空了。

1. **注册表的形状与词汇同源**：`_说明` 里那九张取值名字清单加上『不许出现数字的格子』
   共十处逐处等于代码常量；六条规则的
   代号、层、取数与判据都钉得住；注册表自称"谁在读"的那几份文件必须存在。
2. **骨架零数字**：算式、判据、取数、子集、账套字段、参数名与检索词、两张算子词表里
   出现任何数字，载入即报错；注册表少报一格也要报错；代码侧走同一判据
   （AST 里的数值常量只允许退码 0/1/2）。
3. **四层互斥的边界**：`row` 不许写取数、`calc` 不许带子集、`cross_row` 必须带子集；
   参数、取数、中间量、账套字段四类名字互不相交。
4. **两条红线与词汇不另立**：建议与查什么沿用 `tax_inspect` 那两份词表，四态引
   `tax_coverage.STATES`，缺口类型与动作引 `tax_inspect`——本层不建第二套。
5. **六种已知答案**：每条规则带的差额都按手算核对，命中行与通过行数逐个对上。
6. **判不动与"通过"是两件事**：三种成因各接各的动作与状态；空串与整格不给都算没交，
   数值零算交了零；缺记录不写成未发生；筛掉的范围与没判动的行都留名。
7. **输出不越红线**：渲染全文不含预测检查结果的措辞；改动历史事实那类动作禁在建议格
   （本层用『参数回填』说的是把 ③ 检回的现行值填进本次参数表，不是往历史账里补数）。

用法：`python tests/test_ledger.py`
"""

import ast
import copy
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

import tax_coverage    # noqa: E402
import tax_inspect     # noqa: E402
import tax_ledger      # noqa: E402
import red_line_map    # noqa: E402  ⑦ 红线的正文归属登记表
from test_doc_contract import h2_titles  # noqa: E402  数二级标题用同一把尺子

REG = tax_ledger.load()
BY_CODE = {r["代号"]: r for r in REG["规则"]}
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
TEMPLATES = (ROOT / "references" / "output_templates.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
DEFECTS = (ROOT / "references" / "source_defects.md").read_text(encoding="utf-8")
SOURCE = (ROOT / "scripts" / "tax_ledger.py").read_text(encoding="utf-8")

#: 一本过得去的账：五条命中、四条通过、一行落在子集之外。
LEDGER = {"行": [
    {"行号": 1, "科目": "管理费用—业务招待费", "金额": 380000, "税前扣除额": 380000},
    {"行号": 2, "科目": "主营业务收入", "金额": 9000000},
    {"行号": 3, "科目": "应交税费—应交增值税", "业务类型": "销售货物",
     "金额": 1000000, "税额": 130000},
    {"行号": 4, "科目": "应交税费—应交增值税", "业务类型": "销售货物",
     "金额": 1000000, "税额": 90000},
    {"行号": 5, "所属期": "2024-06", "申报日期": "2024-07-20"},
    {"行号": 6, "所属期": "2024-07", "申报日期": "2024-08-12"},
    {"行号": 7, "科目": "应交税费—应交增值税", "计税方法": "简易计税", "进项税额": 5000},
    {"行号": 8, "科目": "应交税费—应交增值税", "计税方法": "一般计税", "进项税额": 0},
    {"行号": 9, "科目": "研发支出—费用化支出", "允许加计研发费用": 1200000,
     "加计扣除额": 1200000},
    {"行号": 10, "科目": "应交税费—应交增值税", "业务类型": "生活服务",
     "进项税额": 200000, "加计抵减额": 10000},
]}

#: 每个参数都带来源；不给来源等于没回填，落在『待核』而不是拿来算。
PARAMS = {
    "招待费科目关键词": [["业务招待费"], "http://a/43"],
    "收入科目关键词": [["主营业务收入"], "http://a/43"],
    "发生额扣除比例": [0.6, "http://a/43-1"],
    "收入比例限额": [0.005, "http://a/43-2"],
    "研发费科目关键词": [["研发支出"], "http://b/1"],
    "加计扣除比例": [1.0, "http://b/2"],
    "适用税率表": [[{"业务类型": "销售货物", "税率": 0.13},
                    {"业务类型": "生活服务", "税率": 0.06}], "http://c/2"],
    "申报期限表": [[{"所属期": "2024-06", "法定申报期限": "2024-07-15"},
                    {"所属期": "2024-07", "法定申报期限": "2024-08-15"}], "http://c/25"],
    "简易计税范围": [["简易计税"], "http://d/36-2"],
    "加计抵减行业范围": [["生活服务"], "http://e/1"],
    "加计抵减比例": [0.1, "http://e/2"],
}


def params(drop=(), sourceless=()):
    """造参数文件：`drop` 当作没检回，`sourceless` 检回了但不给来源。"""
    out = {k: {"值": v[0], "来源": "" if k in sourceless else v[1]}
           for k, v in PARAMS.items() if k not in drop}
    return tax_ledger._norm_params(out)


def run(ledger=None, values=None, codes=(), tol=0.0):
    return tax_ledger.scan(LEDGER if ledger is None else ledger,
                           params() if values is None else values,
                           codes, REG, tol)


def one(code, **kw):
    return next(r for r in run(**kw)["规则"] if r["代号"] == code)


def entry_of(code, out=None):
    out = run() if out is None else out
    got = [e for e in out["风险条目"] if e["规则代号"] == code]
    assert len(got) == 1, f"{code} 应当恰好命中一条，读到 {len(got)} 条"
    return got[0]


def mutate(fn):
    """改坏注册表再走 `validate()`：必须报错，并把报错文本交回调用方断言。"""
    reg = copy.deepcopy(REG)
    fn(reg)
    try:
        tax_ledger.validate(reg)
    except ValueError as e:
        return str(e)
    raise AssertionError("注册表被改坏了，validate 却没报错——这条判据是空的")


class TestRegistryShape(unittest.TestCase):
    """六条规则、四层检查，每一格都带齐"判得动"所需的来源。"""

    def test_mvp_six_rules_are_the_named_list(self):
        self.assertEqual(sorted(BY_CODE),
                         ["ENT01", "RD01", "VAT01", "VAT02", "VAT03", "VAT04"])
        self.assertEqual({c: BY_CODE[c]["层"] for c in BY_CODE},
                         {"ENT01": "cross_row", "RD01": "cross_row", "VAT01": "row",
                          "VAT02": "row", "VAT03": "row", "VAT04": "cross_row"})

    def test_layer_distribution_is_what_list_prints(self):
        dist = {k: sum(1 for r in REG["规则"] if r["层"] == k)
                for k in tax_ledger.CHECK_LAYERS}
        self.assertEqual(dist, {"row": 3, "calc": 0, "cross_row": 3, "global": 0},
                         "四层里只落了两层；另两层引擎实现了但表里没有，--list 要把零一起打出来")

    def test_every_rule_carries_the_fifteen_fields(self):
        for rule in REG["规则"]:
            for field in tax_ledger.RULE_KEYS:
                self.assertIn(field, rule, f"{rule['代号']} 缺『{field}』这一格")
            for field in ("名称", "税种", "场景", "查什么"):
                self.assertTrue(str(rule[field]).strip(),
                                f"{rule['代号']} 的『{field}』写空了")
            self.assertTrue(rule["参数"], f"{rule['代号']} 一个参数都不要，那它就是硬编码")
            self.assertTrue(rule["建议"], f"{rule['代号']} 判出风险却不给下一步动作")
            self.assertTrue(rule["依据"], f"{rule['代号']} 没有依据")

    def test_each_rule_declares_the_columns_it_reads(self):
        """取数要用的列、子集筛的列都得写进『账套字段』，否则跑起来读不到东西。"""
        for rule in REG["规则"]:
            declared = set(rule["账套字段"])
            for how in rule["取数"].values():
                column = str(how).partition(tax_ledger.SUBSET_MARK)[0]
                self.assertTrue(column.startswith(tax_ledger.TOTAL_MARK), column)
                self.assertIn(column[len(tax_ledger.TOTAL_MARK):], declared,
                              f"{rule['代号']} 要「{column}」这一列却没声明账套字段")
            for name, sel in rule["子集"].items():
                self.assertIn(sel["字段"], declared,
                              f"{rule['代号']} 的子集「{name}」筛的列没声明")

    def test_pinned_aggregates_and_criteria(self):
        """六条规则比哪两个数是设计里定下的，换掉要有人签字而不是顺手改。"""
        self.assertEqual(BY_CODE["ENT01"]["取数"],
                         {"账面发生额": "合计:金额|招待费行",
                          "账载扣除额": "合计:税前扣除额|招待费行",
                          "营业收入": "合计:金额|收入行"})
        self.assertEqual(BY_CODE["ENT01"]["判据"],
                         {"左": "账载扣除额", "算": "大于", "右": "允许扣除限额"})
        self.assertEqual(BY_CODE["RD01"]["判据"],
                         {"左": "申报加计额", "算": "不等于", "右": "测算加计扣除额"})
        self.assertEqual(BY_CODE["VAT01"]["判据"],
                         {"左": "实际税率", "算": "不等于", "右": "表内税率"})
        self.assertEqual(BY_CODE["VAT02"]["判据"],
                         {"左": "申报日期", "算": "晚于", "右": "法定申报期限"})
        self.assertEqual(BY_CODE["VAT03"]["判据"],
                         {"左": "进项税额", "算": "大于", "右": "零"})
        self.assertEqual(BY_CODE["VAT04"]["判据"],
                         {"左": "应计提加计抵减额", "算": "大于", "右": "已计提抵减"})

    def test_rd_rule_points_at_the_single_caliber_baseline(self):
        """研发三套口径的唯一基准在子技能那份文件里，本条只取加计扣除那一列。"""
        hint = " ".join(p["law_hint"] for p in BY_CODE["RD01"]["参数"])
        self.assertIn("rd-mgmt-methodology.md", hint)
        self.assertTrue((ROOT / "subskills" / "rd-deduction-hitech" / "references" /
                         "rd-mgmt-methodology.md").exists(),
                        "注册表指向的口径基准文件不存在")


class TestValidateCatchesDrift(unittest.TestCase):
    """`validate()` 每一处判据都改坏一次注册表，看它报不报红。"""

    def test_baseline_loads(self):
        tax_ledger.validate(copy.deepcopy(REG))

    def test_dropped_rule_field_is_caught(self):
        self.assertIn("查什么", mutate(lambda r: r["规则"][0].pop("查什么")))

    def test_note_constant_drift_is_caught(self):
        def poke(reg):
            reg["_说明"]["比较算子"] = ["大于", "不等于"]
        self.assertIn("与代码常量不一致", mutate(poke))

    def test_duplicate_code_is_caught(self):
        def poke(reg):
            reg["规则"][1]["代号"] = "ENT01"
        self.assertIn("规则代号重复", mutate(poke))

    def test_code_shape_is_caught(self):
        def poke(reg):
            reg["规则"][0]["代号"] = "E1"
        self.assertIn("税种字母＋序号", mutate(poke))

    def test_layer_outside_the_four_is_caught(self):
        def poke(reg):
            reg["规则"][0]["层"] = "cross"
        self.assertIn("不在四层里", mutate(poke))

    def test_level_outside_the_three_is_caught(self):
        def poke(reg):
            reg["规则"][0]["等级"] = "严重"
        self.assertIn("不在值域里", mutate(poke))

    def test_blank_cell_is_caught(self):
        def poke(reg):
            reg["规则"][0]["场景"] = "   "
        self.assertIn("是空的", mutate(poke))

    def test_empty_suggestion_is_caught(self):
        msg = mutate(lambda r: r["规则"][0].update(建议=[]))
        self.assertIn("落不了地", msg)

    def test_missing_judge_key_is_caught(self):
        def poke(reg):
            reg["规则"][0]["判据"].pop("算")
        self.assertIn("判据缺", mutate(poke))

    def test_unsupported_compare_op_is_caught(self):
        def poke(reg):
            reg["规则"][0]["判据"]["算"] = "不小于"
        self.assertIn("可选", mutate(poke))

    def test_param_missing_a_field_is_caught(self):
        def poke(reg):
            reg["规则"][0]["参数"][0].pop("law_hint")
        self.assertIn("参数缺『law_hint』", mutate(poke))

    def test_row_rule_with_aggregate_is_caught(self):
        def poke(reg):
            reg["规则"][2]["取数"] = {"税额合计": "合计:税额"}
        self.assertIn("row 层却写了取数", mutate(poke))

    def test_calc_rule_with_subset_is_pushed_to_cross_row(self):
        def poke(reg):
            reg["规则"][0]["层"] = "calc"
        self.assertIn("带子集", mutate(poke))

    def test_cross_row_without_subset_is_pushed_back_to_calc(self):
        def poke(reg):
            reg["规则"][0]["取数"] = {"账面发生额": "合计:金额"}
            reg["规则"][0]["子集"] = {}
            reg["规则"][0]["算式"] = [
                "发生额口径扣除 = 账面发生额 × 发生额扣除比例",
                "允许扣除限额 = 发生额口径扣除",
                "超出额 = 账载扣除额 − 允许扣除限额"]
        self.assertIn("没带子集", mutate(poke))

    def test_subset_field_not_declared_is_caught(self):
        def poke(reg):
            reg["规则"][0]["子集"]["招待费行"]["字段"] = "摘要"
        self.assertIn("账套字段没声明", mutate(poke))

    def test_subset_param_not_declared_is_caught(self):
        def poke(reg):
            reg["规则"][0]["子集"]["招待费行"]["对参数"] = "差旅费科目关键词"
        self.assertIn("但这条规则没声明它", mutate(poke))

    def test_has_number_selector_with_a_param_is_caught(self):
        def poke(reg):
            reg["规则"][2]["子集"]["有销项行"]["对参数"] = "零值下限"
            reg["规则"][2]["参数"].append({"名": "零值下限", "检索词": "进项税额 抵扣 下限",
                                          "law_hint": "《增值税暂行条例》第十条"})
        self.assertIn("这一档不看值只看那一格交没交", mutate(poke))

    def test_value_selector_without_a_param_is_caught(self):
        def poke(reg):
            reg["规则"][4]["子集"]["简易计税行"].pop("对参数")
        self.assertIn("缺『对参数』", mutate(poke))

    def test_unknown_selector_op_is_caught(self):
        def poke(reg):
            reg["规则"][4]["子集"]["简易计税行"]["算"] = "等于"
        self.assertIn("可选", mutate(poke))

    def test_row_layer_with_two_subsets_is_caught(self):
        def poke(reg):
            reg["规则"][2]["子集"]["第二份"] = {"字段": "金额", "算": "有数"}
        self.assertIn("逐行判只有一条作用行的口径", mutate(poke))

    def test_same_param_name_in_two_rules_is_caught(self):
        """回填的是一张扁平参数表，同名会共用一个值——两处要的不是一件事。"""
        def poke(reg):
            reg["规则"][1]["参数"][0]["名"] = "发生额扣除比例"
        self.assertIn("两处都用", mutate(poke))

    def test_aggregate_without_total_marker_is_caught(self):
        def poke(reg):
            reg["规则"][0]["取数"]["营业收入"] = "金额|收入行"
        self.assertIn("合计:", mutate(poke))

    def test_aggregate_column_not_declared_is_caught(self):
        def poke(reg):
            reg["规则"][0]["取数"]["营业收入"] = "合计:营业收入|收入行"
        self.assertIn("账套字段没声明", mutate(poke))

    def test_aggregate_naming_an_unknown_subset_is_caught(self):
        def poke(reg):
            reg["规则"][0]["取数"]["营业收入"] = "合计:金额|营业行"
        self.assertIn("没有的子集", mutate(poke))

    def test_expression_without_assignment_is_caught(self):
        def poke(reg):
            reg["规则"][0]["算式"].append("账载扣除额 减 允许扣除限额")
        self.assertIn("名字 = 表达式", mutate(poke))

    def test_expression_using_an_undeclared_name_is_caught(self):
        def poke(reg):
            reg["规则"][0]["算式"].append("凭空量 = 没声明过的名字 × 账面发生额")
        self.assertIn("不在参数、取数", mutate(poke))

    def test_expression_reading_a_later_step_is_caught(self):
        """算式按序求值：拿后面才算出来的中间量当输入，跑起来只会判不动。"""
        def poke(reg):
            reg["规则"][0]["算式"].insert(0, "先看 = 允许扣除限额")
        self.assertIn("前面算出的中间量", mutate(poke))

    def test_row_field_in_a_set_layer_expression_is_caught(self):
        """不逐行的层，算式里出现的字段一律要写成『合计:字段』，否则读的是哪一行的数？"""
        def poke(reg):
            reg["规则"][0]["算式"].append("混进来的 = 金额 × 发生额扣除比例")
        self.assertIn("不逐行的层取数要写", mutate(poke))

    def test_judge_naming_a_cell_that_is_not_there_is_caught(self):
        def poke(reg):
            reg["规则"][0]["判据"]["右"] = "扣除上限"
        self.assertIn("既不是参数、取数", mutate(poke))

    def test_name_used_in_two_pools_is_caught(self):
        """取数名与账套字段同名，聚合值会顶掉行上的值，判的就不是意图里那个数。"""
        def poke(reg):
            reg["规则"][0]["取数"]["金额"] = "合计:金额|招待费行"
        self.assertIn("既当『取数』又当『账套字段』", mutate(poke))

    def test_middle_quantity_shadowing_a_column_is_caught(self):
        def poke(reg):
            reg["规则"][0]["算式"].append("科目 = 账面发生额")
        self.assertIn("既当『中间量』又当『账套字段』", mutate(poke))

    def test_duplicate_middle_quantity_is_caught(self):
        def poke(reg):
            reg["规则"][0]["算式"].append("超出额 = 账面发生额")
        self.assertIn("同一个中间量名", mutate(poke))

    def test_fabrication_in_suggestion_is_caught(self):
        def poke(reg):
            reg["规则"][0]["建议"].append("把发票日期倒签到扣除年度内")
        self.assertIn("改动历史事实", mutate(poke))

    def test_prediction_in_what_to_check_is_caught(self):
        def poke(reg):
            reg["规则"][0]["查什么"] = "账面发生额与税前扣除额是否一致，不会被稽查"
        self.assertIn("检查结果预测", mutate(poke))

    def test_prediction_in_suggestion_is_caught(self):
        def poke(reg):
            reg["规则"][2]["建议"].append("这样改就不会被稽查")
        self.assertIn("检查结果预测", mutate(poke))

    def test_blank_basis_is_caught(self):
        def poke(reg):
            reg["规则"][0]["依据"] = ["   "]
        self.assertIn("依据", mutate(poke))

    def test_no_rules_at_all_is_caught(self):
        self.assertIn("没有『规则』", mutate(lambda r: r.update(规则=[])))


class TestNoPolicyNumbers(unittest.TestCase):
    """注册表与代码都不存政策数值：抄进去一次，就答不出这个数出自哪一号文。"""

    def test_digit_in_expression_is_caught(self):
        def poke(reg):
            reg["规则"][0]["算式"].append("加一段 = 账载扣除额 × 0.1")
        self.assertIn("数字", mutate(poke))

    def test_digit_in_selector_is_caught(self):
        def poke(reg):
            reg["规则"][2]["子集"]["有销项行"] = {"字段": "税额", "算": "有数", "门槛": 100}
        self.assertIn("数字", mutate(poke))

    def test_digit_in_search_terms_is_caught(self):
        def poke(reg):
            reg["规则"][0]["参数"][0]["检索词"] = "业务招待费 扣除比例 60%"
        self.assertIn("数字", mutate(poke))

    def test_digit_in_judge_is_caught(self):
        def poke(reg):
            reg["规则"][4]["判据"]["右"] = "5000"
        self.assertIn("数字", mutate(poke))

    def test_digit_in_param_name_is_caught(self):
        def poke(reg):
            reg["规则"][0]["参数"][2]["名"] = "发生额扣除比例2"
        self.assertIn("数字", mutate(poke))

    def test_digit_in_an_operator_name_is_caught(self):
        """算子里带门槛（「大于3万」）就把界写进了骨架。代码与注册表一起漂时
        『_说明』名字清单那道比对不会报，这一格只有禁数字扫描在管。"""
        both = tuple(tax_ledger.COMPARE_OPS) + ("大于3万",)
        with mock.patch.object(tax_ledger, "COMPARE_OPS", both):
            def poke(reg):
                reg["_说明"]["比较算子"] = list(both)
            self.assertIn("数字", mutate(poke))

    def test_a_claimed_digit_free_cell_cannot_be_dropped(self):
        """注册表少报一格，那一格就没人扫：清单本身由 `_说明` 与代码常量逐字比对钉住。"""
        def poke(reg):
            reg["_说明"]["不许出现数字的格子"] = \
                [c for c in reg["_说明"]["不许出现数字的格子"] if c != "参数.名"]
        self.assertIn("不许出现数字的格子", mutate(poke))

    def test_digit_in_ledger_column_is_caught(self):
        def poke(reg):
            reg["规则"][4]["账套字段"].append("进项税额2")
        self.assertIn("数字", mutate(poke))

    def test_source_holds_no_arithmetic_constants(self):
        """代码侧与 `tax_calc` 同一判据：除了 CLI 的退码，一个数值常量都不许有。"""
        tree = ast.parse(SOURCE)
        seen = sorted({n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant)
                       and isinstance(n.value, (int, float))
                       and not isinstance(n.value, bool)
                       and n.value not in (0, 1, 2)})
        self.assertEqual(seen, [], f"代码里出现了硬编码数值 {seen}：比例、限额、档界一律走参数")
        self.assertIn("return 2", SOURCE, "退码 2 是报错分支，不是政策数值")

    def test_exempted_cells_still_hold_their_own_line(self):
        """依据那一格写的是文号，不在禁数字的清单里；判据与算式一格都不带。"""
        self.assertNotIn("依据", REG["_说明"]["不许出现数字的格子"])
        for rule in REG["规则"]:
            for step in rule["算式"]:
                self.assertFalse(any(ch.isdigit() for ch in step), step)
            for side in rule["判据"].values():
                self.assertFalse(any(ch.isdigit() for ch in side), side)
            for name, how in rule["取数"].items():
                self.assertFalse(any(ch.isdigit() for ch in how), how)


class TestVocabularyIsShared(unittest.TestCase):
    """四态、两类缺口、两条红线词表都引上游那几份，本层不建第二套名字。"""

    def test_four_states_are_tax_coverage_own_tuple(self):
        self.assertIs(tax_ledger.STATES, tax_coverage.STATES)
        self.assertEqual(REG["_说明"]["状态值域"], list(tax_coverage.STATES))

    def test_gap_classes_and_actions_are_tax_inspect_own(self):
        self.assertIs(tax_ledger.GAP_ACTION, tax_inspect.GAP_ACTION)
        self.assertEqual((tax_ledger.GAP_MISSING, tax_ledger.GAP_CALIBER),
                         (tax_inspect.GAP_MISSING, tax_inspect.GAP_CALIBER))
        self.assertIs(tax_ledger.COVERAGE_COMPLETE, tax_inspect.COVERAGE_COMPLETE)

    def test_no_second_copy_of_the_red_line_wordlists(self):
        self.assertNotIn("补造", SOURCE.split("class NeedsLedger")[0],
                         "本层自己抄了一份红线词表，两份迟早各说各话")

    def test_registry_name_lists_equal_the_code_constants(self):
        note = REG["_说明"]
        for field, const in (("检查层", tax_ledger.CHECK_LAYERS),
                             ("风险条目八要素", tax_ledger.ENTRY_KEYS),
                             ("规则字段", tax_ledger.RULE_KEYS),
                             ("参数字段", tax_ledger.PARAM_KEYS),
                             ("判据字段", tax_ledger.JUDGE_KEYS),
                             ("比较算子", tax_ledger.COMPARE_OPS),
                             ("筛选算子", tax_ledger.SELECTOR_OPS),
                             ("等级值域", tax_ledger.LEVELS),
                             ("状态值域", tax_ledger.STATES)):
            self.assertEqual(note[field], list(const), f"『{field}』这张清单与代码不一致")

    def test_digit_free_columns_are_listed_and_enforced(self):
        self.assertEqual(REG["_说明"]["不许出现数字的格子"],
                         list(tax_ledger.DIGIT_FREE_CELLS))
        self.assertEqual(list(tax_ledger.NUMERIC_FREE)
                         + ["参数.名", "参数.检索词", "筛选算子", "比较算子"],
                         list(tax_ledger.DIGIT_FREE_CELLS),
                         "代码里禁数字的两份清单对不上，等于有一格没人扫")


class TestWhatCountsAsProvided(unittest.TestCase):
    """零是交了零，空串与整格不给都是没交——这一格判错，整套输出就不诚实。"""

    def test_zero_is_provided_but_empty_and_null_are_not(self):
        self.assertTrue(tax_ledger._given(0))
        self.assertTrue(tax_ledger._given("0"))
        self.assertFalse(tax_ledger._given(""))
        self.assertFalse(tax_ledger._given("   "))
        self.assertFalse(tax_ledger._given(None))

    def test_missing_ledger_is_refused_not_guessed(self):
        with self.assertRaises(tax_ledger.NeedsLedger):
            tax_ledger.scan(None, params())

    def test_ledger_shape_error_names_the_form(self):
        with self.assertRaises(ValueError) as ctx:
            tax_ledger._rows({"明细": []})
        self.assertIn('"行"', str(ctx.exception))

    def test_row_that_is_not_an_object_is_named_by_position(self):
        with self.assertRaises(ValueError) as ctx:
            tax_ledger._rows({"行": [{"行号": 1}, "不是对象"]})
        self.assertIn("第 2 行", str(ctx.exception))

    def test_bare_scalar_param_gets_an_empty_source_and_stays_unusable(self):
        got = tax_ledger._norm_params({"发生额扣除比例": 0.6})
        self.assertEqual(got["发生额扣除比例"], {"值": 0.6, "来源": ""})
        self.assertTrue(tax_ledger._pending_params(BY_CODE["ENT01"], got))

    def test_boolean_and_null_backfills_are_not_values(self):
        for bad in (True, False, None):
            p = tax_ledger._norm_params({"加计扣除比例": {"值": bad,
                                                         "来源": "http://b/2"}})
            self.assertIn(BY_CODE["RD01"]["参数"][1],
                          tax_ledger._pending_params(BY_CODE["RD01"], p),
                          f"回填成 {bad!r} 却被当成有值——它落到 Decimal 会变成 1 或 0")

    def test_param_without_source_is_pending(self):
        self.assertTrue(tax_ledger._pending_params(
            BY_CODE["VAT03"], params(sourceless=("简易计税范围",))))

    def test_json_float_times_decimal_aggregate_does_not_crash(self):
        """0.6 从 JSON 进来是 float，聚合数是 Decimal：这一路要判得动而不是抛异常。"""
        got = one("ENT01")
        self.assertEqual(got["状态"], tax_ledger.VERIFIED)
        self.assertIn("228000.00", entry_of("ENT01")["明细"])

    def test_text_in_a_numeric_cell_is_judged_not_crashing(self):
        row = {"行号": 4, "业务类型": "销售货物", "金额": "一百万", "税额": 90000}
        got = one("VAT01", ledger={"行": [row]})
        self.assertIn("不是可算的数值", got["判不动行"][0]["原因"])


class TestSixKnownAnswers(unittest.TestCase):
    def test_ent01_lower_of_two_limits(self):
        detail = entry_of("ENT01")["明细"]
        self.assertIn("发生额口径扣除 = 380000.00 × 0.6 = 228000.00", detail)
        self.assertIn("收入口径限额 = 9000000.00 × 0.005 = 45000.00", detail)
        self.assertIn("允许扣除限额 = min(228000.00, 45000.00) = 45000.00", detail)
        self.assertIn("超出额 = 380000.00 − 45000.00 = 335000.00", detail)

    def test_ent01_row_and_subject_are_the_aggregated_ones(self):
        e = entry_of("ENT01")
        self.assertEqual(e["行号"], [1, 2])
        self.assertEqual(e["科目"], ["管理费用—业务招待费", "主营业务收入"])

    def test_only_the_rows_that_entered_the_aggregation_count(self):
        got = one("ENT01")
        self.assertEqual((got["作用行数"], got["未作用行数"]), (2, 8),
                         "整表 10 行里只有 2 行进过取数，报 10 行就是把没查的行说成查过")

    def test_rd01_equal_numbers_pass_instead_of_being_left_blank(self):
        got = one("RD01")
        self.assertEqual((got["命中数"], got["通过次数"], got["状态"]),
                         (0, 1, tax_ledger.VERIFIED))

    def test_vat01_hits_the_row_whose_rate_is_off(self):
        got = one("VAT01")
        self.assertEqual([e["行号"] for e in got["风险条目"]], [[4]])
        self.assertIn("0.09 对 0.13", got["风险条目"][0]["明细"])
        self.assertEqual((got["作用行数"], got["通过次数"]), (2, 1))

    def test_vat02_compares_against_the_published_deadline(self):
        got = one("VAT02")
        self.assertEqual([e["行号"] for e in got["风险条目"]], [[5]])
        self.assertIn("2024-07-20 对 2024-07-15", got["风险条目"][0]["明细"])
        self.assertEqual(got["通过次数"], 1)

    def test_vat03_hits_only_the_simplified_method_row(self):
        got = one("VAT03")
        self.assertEqual([e["行号"] for e in got["风险条目"]], [[7]])
        self.assertEqual(got["作用行数"], 1, "一般计税那一行不在这条规则的作用范围里")

    def test_vat04_unclaimed_addition_is_a_hit_with_the_difference(self):
        detail = entry_of("VAT04")["明细"]
        self.assertIn("应计提加计抵减额 = 200000.00 × 0.1 = 20000.00", detail)
        self.assertIn("缺口 = 20000.00 − 10000.00 = 10000.00", detail)

    def test_five_hits_and_the_chain_carries_every_backfilled_value(self):
        out = run()
        self.assertEqual(len(out["风险条目"]), 5)
        chain = {x["项"]: x for x in entry_of("ENT01", out)["依据链"]}
        self.assertEqual(chain["发生额扣除比例"]["值"], "0.6")
        self.assertEqual(chain["发生额扣除比例"]["来源"], "http://a/43-1")
        self.assertIn("law_hint", json.dumps(list(chain.values()), ensure_ascii=False))
        self.assertIn("规则依据", chain, "依据链要把规则自带的那几条依据也带上")

    def test_eight_keys_are_present_in_every_entry(self):
        for e in run()["风险条目"]:
            self.assertEqual(list(e), list(tax_ledger.ENTRY_KEYS))

    def test_tolerance_only_loosens_the_not_equal_operator(self):
        far = one("VAT01", tol=0.005)
        self.assertEqual(far["命中数"], 1, "0.09 与 0.13 差四个百分点，半个百分点的容差不该放过")
        near = one("VAT01", ledger={"行": [dict(LEDGER["行"][2], 税额=129500)]}, tol=0.005)
        self.assertEqual((near["命中数"], near["通过次数"]), (0, 1),
                         "129500 对 130000 差千分之四以内，给了容差就该判一致")
        strict = one("VAT01", ledger={"行": [dict(LEDGER["行"][2], 税额=129500)]})
        self.assertEqual(strict["命中数"], 1, "默认容差 0：差一分也算不一致")

    def test_absurd_tolerance_is_rejected_before_it_blinds_the_check(self):
        for bad in (1.0, -0.1):
            with self.assertRaises(ValueError):
                run(tol=bad)

    def test_mutation_a_comparator_that_always_says_equal(self):
        """把比较改成永远不命中，五条风险就该整批消失——否则这一层是空转。"""
        real = tax_ledger._compare
        tax_ledger._compare = lambda *a, **k: (False, "打桩")
        try:
            self.assertEqual(len(run()["风险条目"]), 0)
        finally:
            tax_ledger._compare = real
        self.assertEqual(len(run()["风险条目"]), 5)

    def test_mutation_a_subset_that_admits_every_row(self):
        """子集判据空转（所有行都算命中子集）时，作用行数会变，输出也就不可信。"""
        real = tax_ledger._subset
        tax_ledger._subset = lambda rule, name, rows, values: list(rows)
        try:
            self.assertEqual(one("VAT03")["作用行数"], 10)
        finally:
            tax_ledger._subset = real
        self.assertEqual(one("VAT03")["作用行数"], 1)


class TestThreeCausesOfUnjudgeable(unittest.TestCase):
    def test_param_not_backfilled_stops_the_whole_rule(self):
        got = one("VAT03", values=params(drop=("简易计税范围",)))
        self.assertEqual((got["状态"], got["判不动成因"]),
                         (tax_ledger.TO_VERIFY, "参数未回填"))
        self.assertEqual(got["命中数"], 0)
        self.assertIn("检索词", got["原因"])
        self.assertEqual([g["类"] for g in got["缺口"]], [tax_ledger.GAP_CALIBER])

    def test_sourceless_backfill_is_the_same_cause(self):
        got = one("VAT04", values=params(sourceless=("加计抵减比例",)))
        self.assertEqual(got["状态"], tax_ledger.TO_VERIFY)
        self.assertIn("没给来源", got["原因"])

    def test_missing_cell_is_asked_from_the_enterprise(self):
        got = one("VAT03", ledger={"行": [{"行号": 7, "计税方法": "简易计税"}]})
        self.assertEqual((got["状态"], got["判不动成因"]),
                         (tax_ledger.LACK, "账套缺那一格"))
        self.assertEqual([g["类"] for g in got["缺口"]], [tax_ledger.GAP_MISSING])

    def test_tier_absent_from_the_retrieved_table_is_not_padded(self):
        row = {"行号": 4, "业务类型": "不动产租赁", "金额": 100000, "税额": 9000}
        got = one("VAT01", ledger={"行": [row]})
        self.assertEqual(got["判不动行"][0]["成因"], "表里没有那一档")
        self.assertIn("不借近档顶位", got["判不动行"][0]["原因"])
        self.assertEqual([g["类"] for g in got["缺口"]], [tax_ledger.GAP_CALIBER])

    def test_a_tier_present_but_its_column_blank_is_the_same_cause(self):
        values = params()
        values["适用税率表"] = {"值": [{"业务类型": "销售货物", "税率": ""}],
                              "来源": "http://c/2"}
        got = one("VAT01", ledger={"行": [LEDGER["行"][2]]}, values=values)
        self.assertIn("但那档的『税率』是空的", got["判不动行"][0]["原因"])

    def test_table_backfilled_as_a_scalar_is_a_retrieval_problem(self):
        values = params()
        values["适用税率表"] = {"值": 0.13, "来源": "http://c/2"}
        got = one("VAT01", values=values)
        self.assertEqual(got["状态"], tax_ledger.TO_VERIFY)
        self.assertIn("回 ③ 换词重取", got["判不动行"][0]["原因"])

    def test_cause_switches_the_state_and_the_gap_class_together(self):
        for cause, state, kind in (
                ("参数未回填", tax_ledger.TO_VERIFY, tax_ledger.GAP_CALIBER),
                ("账套缺那一格", tax_ledger.LACK, tax_ledger.GAP_MISSING),
                ("表里没有那一档", tax_ledger.LACK, tax_ledger.GAP_CALIBER)):
            self.assertEqual(tax_ledger._state_for(cause), state, cause)
            gap = tax_ledger._gap(cause, "对象", "")
            self.assertEqual(gap["类"], kind, cause)
            self.assertEqual(gap["动作"], tax_inspect.GAP_ACTION[kind], cause)

    def test_rule_level_gap_is_not_counted_twice(self):
        """逐行已经各有一条缺口了，整条规则那句不能再补一条。"""
        rows = [{"行号": 7, "计税方法": "简易计税"}, {"行号": 8, "计税方法": "简易计税"}]
        got = one("VAT03", ledger={"行": rows})
        self.assertEqual(len(got["缺口"]), 2, "两行判不动该是两条缺口，不是三条")

    def test_missing_denominator_is_not_read_as_zero(self):
        row = {"行号": 4, "业务类型": "销售货物", "金额": 0, "税额": 90000}
        got = one("VAT01", ledger={"行": [row]})
        self.assertIn("除数是零", got["判不动行"][0]["原因"])
        self.assertEqual(got["命中数"], 0)

    def test_date_that_is_not_iso_names_the_cell_and_the_form(self):
        row = {"行号": 5, "所属期": "2024-06", "申报日期": "2024/7/20"}
        got = one("VAT02", ledger={"行": [row]})
        self.assertIn("YYYY-MM-DD", got["判不动行"][0]["原因"])
        self.assertEqual(got["状态"], tax_ledger.LACK)

    def test_set_layer_missing_a_column_names_the_subset(self):
        rows = [{"行号": 1, "科目": "管理费用—业务招待费", "金额": 380000},
                {"行号": 2, "科目": "主营业务收入", "金额": 9000000}]
        got = one("ENT01", ledger={"行": rows})
        self.assertEqual(got["状态"], tax_ledger.LACK)
        self.assertIn("招待费行", got["原因"])


class TestCoverageIsHonest(unittest.TestCase):
    def test_full_run_says_how_many_were_judged(self):
        out = run()
        self.assertEqual(out["覆盖"]["状态"], tax_ledger.COVERAGE_COMPLETE)
        self.assertIn("6 条判得动，0 条整条没执行", out["覆盖"]["说明"])

    def test_stalled_rules_are_counted_separately_from_judged_ones(self):
        out = run(values=params(drop=("简易计税范围", "加计抵减比例")))
        self.assertIn("2 条整条没执行（参数未回填）", out["覆盖"]["说明"])

    def test_filtered_run_marks_partial_and_lists_the_rest(self):
        out = run(codes=("RD01",))
        self.assertEqual(out["覆盖"]["状态"], tax_ledger.COVERAGE_PARTIAL)
        self.assertEqual(len(out["覆盖"]["未巡检规则"]), 5)
        self.assertTrue(all(x["状态"] == tax_ledger.NA for x in out["覆盖"]["未巡检规则"]))

    def test_zero_rules_selected_says_filtered_empty_not_finished(self):
        out = run(codes=("ENT01",))
        self.assertEqual(out["本次范围"]["规则数"], 1)
        empty = tax_ledger.scan(LEDGER, params(), tuple(), REG)
        self.assertEqual(empty["覆盖"]["状态"], tax_ledger.COVERAGE_COMPLETE)

    def test_unknown_code_is_rejected_with_the_value_domain(self):
        with self.assertRaises(ValueError) as ctx:
            run(codes=("VAT09",))
        self.assertIn("VAT01", str(ctx.exception))

    def test_no_hits_still_says_this_only_covers_those_six_checks(self):
        clean = {"行": [LEDGER["行"][1], dict(LEDGER["行"][2]), dict(LEDGER["行"][7])]}
        out = run(ledger=clean)
        self.assertEqual(len(out["风险条目"]), 0)
        text = tax_ledger.render(out)
        self.assertIn("不等于没问题", text)
        self.assertIn("它只覆盖", text)

    def test_no_row_in_a_subset_is_reported_per_rule_not_assumed_clear(self):
        out = run(ledger={"行": [{"行号": 1, "科目": "销售费用", "金额": 10}]})
        stalled = [r["代号"] for r in out["规则"] if r["状态"] == tax_ledger.LACK]
        self.assertTrue(stalled, "没有一行落进子集时每条规则都要说自己判不动")
        for r in out["规则"]:
            if r["代号"] in stalled:
                self.assertIn("缺记录不等于未发生", r["原因"])

    def test_the_no_record_note_is_the_only_place_those_words_appear(self):
        banned = ("未计提", "未享受", "不涉及该税种", "没有发生", "无需申报")
        out = run(ledger={"行": [{"行号": 7, "计税方法": "简易计税"}]})
        text = json.dumps(out, ensure_ascii=False) + tax_ledger.render(out)
        text = text.replace(tax_ledger.NO_RECORD_NOTE, "")
        for phrase in banned:
            self.assertNotIn(phrase, text, f"输出把缺记录写成了事实结论：{phrase}")

    def test_gaps_are_counted_by_class_not_merged_into_a_total(self):
        out = run(values=params(drop=("简易计税范围",)))
        self.assertEqual(out["缺口统计"]["政策口径不清"], 1)
        self.assertEqual(out["缺口统计"]["缺材料"], 0)

    def test_unused_ledger_columns_are_listed_separately(self):
        out = run(ledger={"行": [dict(LEDGER["行"][0], 摘要="业务招待")]})
        self.assertIn("摘要", out["未用的账套字段"],
                      "账套给了却没规则用到的字段要留名，不然是静默丢弃")


class TestRender(unittest.TestCase):
    def test_state_and_hit_count_are_printed_side_by_side(self):
        text = tax_ledger.render(run())
        self.assertIn("状态 已满足｜命中 1 条", text,
                      "『已满足』说的是判得动，紧跟命中数才不会读成没问题")

    def test_eight_keys_are_all_printed(self):
        text = tax_ledger.render(run())
        for key in tax_ledger.ENTRY_KEYS:
            self.assertIn(f"{key}：", text, f"渲染里少了『{key}』这一格")

    def test_subjectless_cross_row_entry_prints_the_rows_it_used(self):
        text = tax_ledger.render(run(codes=("ENT01",)))
        self.assertIn("行号：1、2", text)

    def test_unjudged_rows_and_skipped_rules_are_printed(self):
        text = tax_ledger.render(run(codes=("VAT01",), values=params()))
        self.assertIn("未巡检 ENT01", text)
        self.assertIn("放宽 --rule 再跑", text)

    def test_row_without_a_number_is_still_locatable(self):
        """账套没给行号那一列时，输出要能指回那一条：写成『第 None 行』等于没指，
        光说『未给行号』也指不出是哪几条，所以按提交顺序补一个序号。"""
        rows = [{"行号": 9, "科目": "应交税费—应交增值税", "业务类型": "销售货物",
                 "金额": 1000000, "税额": 130000},
                {"科目": "应交税费—应交增值税", "业务类型": "销售货物",
                 "金额": 1000000, "税额": 90000}]
        text = tax_ledger.render(run(ledger={"行": rows}))
        self.assertIn("第 2 条（该行未给行号）", text)
        self.assertNotIn("第 None 行", text)
        self.assertNotIn("第 9 行（该行未给行号）", text)

    def test_subjectless_row_without_a_number_still_counts_as_one_row(self):
        out = run(ledger={"行": [{"科目": "应交税费—应交增值税", "业务类型": "销售货物",
                                  "金额": 1000000, "税额": 90000}]})
        got = next(r for r in out["规则"] if r["代号"] == "VAT01")
        self.assertEqual(got["风险条目"][0]["行号"], ["第 1 条（该行未给行号）"])
        self.assertEqual(got["作用行数"], 1)

    def test_render_carries_no_prediction(self):
        text = tax_ledger.render(run())
        for phrase in tax_inspect.PREDICTION_PHRASES:
            self.assertNotIn(phrase, text, f"渲染里出现检查结果预测 {phrase}")
        self.assertIn("不预测检查与处罚结果", text)

    def test_backfill_word_is_banned_in_suggestions_only(self):
        """『回填』既在禁改历史的词表里，又是本层对『把 ③ 检回的现行值填进本次参数表』
        的叫法。所以这条红线只扫建议格：写进建议报错，出现在成因与边界句里不算越线。"""
        def poke(reg):
            reg["规则"][0]["建议"].append("把缺失的凭证回填到当月账上")
        self.assertIn("改动历史事实", mutate(poke))
        self.assertIn("回填", tax_ledger.render(run()),
                      "边界句里的『参数没回填』被当成整改动作扫掉，等于把这一层自己的说法判死")

    def test_render_prints_the_boundary_and_the_gap_counts(self):
        text = tax_ledger.render(run())
        self.assertIn("缺口统计：缺材料 0｜政策口径不清 0", text)
        self.assertIn("本层按注册表的判据比账上的数", text)


class TestCli(unittest.TestCase):
    def cli(self, *argv):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "tax_ledger.py"),
                               *argv], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(ROOT))

    def _files(self):
        led = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(LEDGER, led, ensure_ascii=False)
        led.close()
        par = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({k: {"值": v[0], "来源": v[1]} for k, v in PARAMS.items()},
                  par, ensure_ascii=False)
        par.close()
        return led.name, par.name

    def test_list_names_every_rule_and_the_empty_layers(self):
        r = self.cli("--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        for code in BY_CODE:
            self.assertIn(code, r.stdout)
        self.assertIn("规则共 6 条", r.stdout)
        self.assertIn("calc 0", r.stdout)
        self.assertIn("待检回参数", r.stdout)

    def test_pending_is_the_worklist_for_step_three(self):
        r = self.cli("--pending")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["待检回参数数"], 11)
        self.assertTrue(all(x["检索词"] and x["law_hint"] for x in got["参数"]))
        self.assertIn("回填形态", got["参数"][0])

    def test_pending_shrinks_as_parameters_are_backfilled(self):
        led, par = self._files()
        try:
            r = self.cli("--ledger", led, "--params", par, "--pending")
            self.assertEqual(json.loads(r.stdout)["待检回参数数"], 0)
        finally:
            Path(led).unlink()
            Path(par).unlink()

    def test_scan_via_files_matches_the_in_process_run(self):
        led, par = self._files()
        try:
            r = self.cli("--ledger", led, "--params", par, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["风险条目"], run()["风险条目"])
        finally:
            Path(led).unlink()
            Path(par).unlink()

    def test_plain_output_is_the_rendered_form(self):
        led, par = self._files()
        try:
            r = self.cli("--ledger", led, "--params", par)
            self.assertEqual(r.stdout, tax_ledger.render(run()) + "\n",
                             "命令行那条路径就是 render 的文本，只多一个收尾换行")
        finally:
            Path(led).unlink()
            Path(par).unlink()

    def test_no_ledger_exits_two_with_the_two_commands(self):
        r = self.cli()
        self.assertEqual(r.returncode, 2)
        self.assertIn("--pending", r.stdout)
        self.assertIn("账套形态", r.stdout)

    def test_malformed_ledger_exits_two_without_a_traceback(self):
        bad = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        bad.write('{"行": [{"科目": ,}]}')
        bad.close()
        try:
            r = self.cli("--ledger", bad.name)
            self.assertEqual(r.returncode, 2)
            self.assertIn("error", r.stdout)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            Path(bad.name).unlink()

    def test_unknown_rule_code_exits_two_with_the_list(self):
        led, par = self._files()
        try:
            r = self.cli("--ledger", led, "--params", par, "--rule", "VAT09")
            self.assertEqual(r.returncode, 2)
            self.assertIn("VAT01", r.stdout)
        finally:
            Path(led).unlink()
            Path(par).unlink()

    def test_absurd_tolerance_exits_two(self):
        led, par = self._files()
        try:
            r = self.cli("--ledger", led, "--params", par, "--tolerance", "2")
            self.assertEqual(r.returncode, 2)
            self.assertIn("容差", r.stdout)
        finally:
            Path(led).unlink()
            Path(par).unlink()


class TestDocsShareTheImplementation(unittest.TestCase):
    """文档与本表说同一件事；漂了就报红，而不是各说各话。"""

    def test_skill_routes_to_the_module(self):
        self.assertIn("scripts/tax_ledger.py", SKILL)
        self.assertIn("data/ledger_rules.json", SKILL)

    def test_skill_step_two_says_the_ledger_is_an_input(self):
        body = SKILL.split("## ② 前提补齐")[1].split("## ③")[0]
        # SKILL 的正文按句换行，比对前把空白压掉，不然断行处会把一句完整的话拆成两段。
        flat = re.sub(r"\s+", "", body)
        self.assertIn("tax_ledger.py", flat)
        self.assertIn("不拿题面推断账上的数", flat)

    def test_red_line_26_is_the_ledger_one(self):
        # 第 26 条的正文住在 ⑥ 那一式的账套巡检条件块里，⑦ 只留一行指针；
        # 指针与锚对不对由 `test_doc_contract` 的登记表用例逐条判，这里只认本层那几句还在。
        self.assertEqual(red_line_map.holder(26),
                         ("references/output_templates.md", "风险自检专用输出"))
        item = red_line_map.body(26)
        self.assertIn("【⑦ 第 26 条正文】", TEMPLATES)
        for needle in ("判不动", "参数未回填", "账套缺那一格", "表里没有那一档",
                       "邻近档", "tax_ledger.validate"):
            self.assertIn(needle, item, f"第 26 条少了：{needle}")

    def test_step_six_points_at_the_layer_without_a_new_form(self):
        body = SKILL.split("## ⑥ 输出格式模板")[1].split("## ⑦")[0]
        for needle in ("tax_ledger.py", "--pending", "九种输出形态", "不新增第 10 种形态",
                       "参数没检回", "邻近档", "八格"):
            self.assertIn(needle, body, f"⑥ 那一段少了：{needle}")

    def test_template_conditional_block_lives_inside_the_risk_section(self):
        body = TEMPLATES.split("## 风险自检专用输出")[1].split("## 稽查模拟问询式")[0]
        self.assertIn("账套巡检条件块", body)
        self.assertIn("tax_ledger.py", body)
        for key in tax_ledger.ENTRY_KEYS:
            self.assertIn(key, body, f"模板里少了『{key}』这一格")
        for state in tax_ledger.STATES:
            self.assertIn(state, body, f"模板里没写状态值域的这一档：{state}")
        self.assertIn("未提供账套行，本块未执行", body, "条件块要交代缺口的写法，不能整块抹掉")

    def test_template_keeps_nine_forms_after_the_block(self):
        # 模板骨架里的 `## [问题] — …` 在 ``` 围栏内，是产出物的行而不是本文件的章节；
        # 数形态只数真实二级标题，这一点沿用 `test_doc_contract` 的同一把尺子。
        heads = [t for t in h2_titles(TEMPLATES) if t != "目录"]
        self.assertEqual(len(heads), 9,
                         f"账套巡检该挂在既有那一式里，现在却有 {len(heads)} 个小节")

    def test_ownership_table_and_boundary_are_recorded(self):
        self.assertIn("tax_ledger.scan", DEFECTS)
        self.assertIn("tax_ledger.NeedsLedger", DEFECTS)
        self.assertIn("账套巡检的覆盖面", DEFECTS)

    def test_readme_lists_the_layer_the_registry_and_the_gate(self):
        for needle in ("tax_ledger.py", "ledger_rules.json", "四层检查", "八要素风险条目"):
            self.assertIn(needle, README)

    def test_registry_readers_all_exist(self):
        """注册表自称"谁在读"，那几份文件就得真在——写下不存在的读者等于没写。"""
        for who in REG["_说明"]["谁在读"]:
            rel = who.split("（")[0].strip()
            self.assertTrue((ROOT / rel).exists(), f"注册表说 {rel} 在读这一格，文件却不在")
            self.assertTrue(Path(rel).suffix, f"{rel} 不像路径")

    def test_registry_names_the_layers_it_does_not_fill(self):
        self.assertIn("calc 与 global", REG["_说明"]["本表覆盖的层"])

    def test_gate_actually_runs_this_file(self):
        """门禁里点名本文件——写了用例却没进门禁，等于没写。

        离线组第一项是在本进程里跑的编译函数，不是 argv，所以先过掉 callable。
        """
        sys.path.insert(0, str(ROOT / "tests"))
        import run_all
        listed = " ".join(" ".join(map(str, cmd))
                          for _name, cmd in run_all.OFFLINE_GROUP
                          if not callable(cmd))
        self.assertIn("test_ledger.py", listed,
                      "tests/run_all.py 的离线组没点名 test_ledger.py，"
                      "这一整份用例一次都不会跑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
