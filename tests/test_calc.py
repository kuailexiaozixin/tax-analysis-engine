#!/usr/bin/env python3
"""算式展开器 `scripts/tax_calc.py` 的离线用例。不联网、不调模型。

这一层的定位是"算式展开器，不是知识库"：骨架里只有四则运算的顺序，税率、
扣除标准、档界一律由调用方喂进来。所以本文件的用例分四组，各组盯的失效路径不同：

1. **零硬编码**：静态扫源码与骨架串，政策数值一旦被人写进引擎就报红。这一条
   是整套设计的成立前提——写死了，答案就说不清是从哪份文件来的。
2. **参数清单**：每个骨架要哪些参数，是 `run()` 报错与 ② 补问的共同依据，
   取值逐个钉住。清单漂了（少一项或多一项）就是"缺参数却猜了值"或"要了不存在的参数"。
3. **两式互验**：累进税的分段累加与速算扣除必须相等；把速算扣除数改成别行的值，
   引擎必须报错而不是静默给出其中一个数。
4. **已知答案的算例**：数值由测试自己喂（用条文里出现过的组合），保证引擎本身可回归。

用法：`python tests/test_calc.py`
"""

import ast
import io
import json
import contextlib
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import tax_calc  # noqa: E402

SRC = (ROOT / "scripts/tax_calc.py").read_text(encoding="utf-8")


# ── 测试自己喂的数值：全部取自公开条文里出现过的组合，只为算式对账 ────────────
IIT_TABLE = [  # 综合所得年度税率表（超额累进）
    {"上限": 36000, "税率": 0.03, "速算扣除数": 0},
    {"上限": 144000, "税率": 0.10, "速算扣除数": 2520},
    {"上限": 300000, "税率": 0.20, "速算扣除数": 16920},
    {"上限": 420000, "税率": 0.25, "速算扣除数": 31320},
    {"上限": 660000, "税率": 0.30, "速算扣除数": 52920},
    {"上限": 960000, "税率": 0.35, "速算扣除数": 85920},
    {"上限": None, "税率": 0.45, "速算扣除数": 181920},
]
LVAT_TABLE = [  # 土地增值税四级超率累进：档界是"增值额占扣除项目金额的倍数"，速算扣除是系数
    {"上限": 0.5, "税率": 0.30, "速算扣除系数": 0},
    {"上限": 1, "税率": 0.40, "速算扣除系数": 0.05},
    {"上限": 2, "税率": 0.50, "速算扣除系数": 0.15},
    {"上限": None, "税率": 0.60, "速算扣除系数": 0.35},
]


class TestNoHardcodedPolicyNumbers(unittest.TestCase):
    """引擎里不许出现政策数值。写死一个税率，答案就再也指不回文件。"""

    def test_skeleton_expressions_carry_no_policy_numbers(self):
        for name, sk in tax_calc.SKELETONS.items():
            # 「公式」是给人看的一行话，可以并列两条；分号切开逐条扫，
            # 免得有人把"税率 0.13"只写进说明行、不写进步骤就躲过检查。
            for expr in sk["步骤"] + sk["公式"].split("；"):
                # 走引擎自己的转换点：骨架里写的是排印符号，_ascii 之后才是 ast 认的算式
                for node in ast.walk(ast.parse(tax_calc._ascii(expr))):
                    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                        self.assertIn(node.value, tax_calc._ALLOWED_CONSTANTS,
                                      f"骨架「{name}」的算式里出现了数字常量 "
                                      f"{node.value}：{expr}")

    def test_module_has_no_tax_rate_literals(self):
        """整个模块（含表格注释、默认参数）都不许出现 0<x<1 的小数常量。

        只扫骨架不够：有人把"默认税率 0.13"写成函数默认值，骨架里干净、行为仍写死。
        """
        bad = []
        for node in ast.walk(ast.parse(SRC)):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if 0 < node.value < 1:
                    bad.append(node.value)
        self.assertFalse(bad, f"tax_calc.py 出现了比率型字面量 {bad}——政策数值只能由调用方给")

    def test_no_skeleton_has_default_params(self):
        for name in tax_calc.SKELETONS:
            with self.assertRaises(tax_calc.Missing,
                                   msg=f"骨架「{name}」不给参数就能跑，说明有人藏了默认值"):
                tax_calc.run(name, {})


class TestParamInventory(unittest.TestCase):
    """参数清单逐个钉住：这是 ② 补问与计算取参共用的那张表。"""

    EXPECTED = {
        "增值税一般计税": ["不含税销售额", "适用税率", "进项税额", "进项税额转出", "上期留抵税额"],
        "城建税及附加": ["实缴增值税", "实缴消费税", "城建税税率", "教育费附加费率",
                         "地方教育附加费率"],
        "超额累进": ["计税基数", "税率表"],
        "综合所得年度汇算": ["收入总额", "费用减除", "专项扣除", "专项附加扣除", "税率表",
                             "已预缴税额"],
        "超率累进": ["转让收入", "扣除项目金额", "税率表"],
        "从价计征": ["计税依据", "税率"],
    }

    def test_inputs_per_skeleton(self):
        self.assertEqual(sorted(tax_calc.SKELETONS), sorted(self.EXPECTED),
                         "骨架清单与参数清单不同步：加了骨架没登记，或删了骨架没删表")
        for name, want in self.EXPECTED.items():
            self.assertEqual(tax_calc.inputs(name), want, f"骨架「{name}」的待给参数变了")

    def test_missing_names_are_the_exact_gap(self):
        """缺四个报四个，按算式里读到的先后排——② 的补问照这个名字列表问。"""
        with self.assertRaises(tax_calc.Missing) as ctx:
            tax_calc.run("增值税一般计税", {"不含税销售额": 1000000})
        self.assertEqual(ctx.exception.names,
                         ["适用税率", "进项税额", "进项税额转出", "上期留抵税额"])


class TestProgressiveCrossCheck(unittest.TestCase):
    def test_iit_120000_both_routes_agree(self):
        out = tax_calc.run("超额累进", {"计税基数": 120000, "税率表": IIT_TABLE})
        self.assertEqual(out["结果"]["应纳税额"], 9480.0)   # 36000×3% + 84000×10%
        # 分档明细挂在这条步骤上，不进结果：下一步算式要的只是那个数额
        self.assertEqual(out["步骤"][0]["分档"]["适用档"], 2)
        self.assertEqual(len(out["步骤"][0]["分档"]["逐档"]), 2)
        self.assertEqual(out["步骤"][0]["结果"], 9480.0)

    def test_wrong_deduction_is_caught_not_silenced(self):
        """速算扣除数抄成别行的值，两式必然分叉，引擎要报错而不是给其中一个数。"""
        bad = [dict(r) for r in IIT_TABLE]
        bad[1]["速算扣除数"] = 16920                      # 抄了第 3 档的扣除数
        with self.assertRaises(ValueError) as ctx:
            tax_calc.run("超额累进", {"计税基数": 120000, "税率表": bad})
        self.assertIn("不自洽", str(ctx.exception))

    def test_non_monotonic_bounds_are_caught(self):
        bad = [dict(r) for r in IIT_TABLE]
        bad[1]["上限"] = 36000                            # 第 2 档与第 1 档同界
        with self.assertRaises(ValueError) as ctx:
            tax_calc.run("超额累进", {"计税基数": 120000, "税率表": bad})
        self.assertIn("档界不连续", str(ctx.exception))

    def test_last_tier_must_be_open(self):
        bad = [dict(r) for r in IIT_TABLE]
        bad[-1]["上限"] = 2000000
        with self.assertRaises(ValueError) as ctx:
            tax_calc.run("超额累进", {"计税基数": 120000, "税率表": bad})
        self.assertIn("不封顶", str(ctx.exception))

    def test_null_before_last_tier_is_caught(self):
        bad = [dict(r) for r in IIT_TABLE]
        bad[2]["上限"] = None
        with self.assertRaises(ValueError) as ctx:
            tax_calc.run("超额累进", {"计税基数": 120000, "税率表": bad})
        self.assertIn("不是最后一档", str(ctx.exception))


class TestKnownAnswers(unittest.TestCase):
    def test_vat_general(self):
        out = tax_calc.run("增值税一般计税", {
            "不含税销售额": 1000000, "适用税率": 0.13,
            "进项税额": 100000, "进项税额转出": 0, "上期留抵税额": 0})
        self.assertEqual(out["结果"]["应纳税额"], 30000.0)
        self.assertEqual(out["结果"]["期末留抵税额"], 0.0)

    def test_vat_negative_becomes_credit_not_negative_tax(self):
        out = tax_calc.run("增值税一般计税", {
            "不含税销售额": 1000000, "适用税率": 0.13,
            "进项税额": 200000, "进项税额转出": 0, "上期留抵税额": 0})
        self.assertEqual(out["结果"]["应纳税额"], 0.0)
        self.assertEqual(out["结果"]["期末留抵税额"], 70000.0)

    def test_urban_construction_and_surcharges(self):
        out = tax_calc.run("城建税及附加", {
            "实缴增值税": 100000, "实缴消费税": 0,
            "城建税税率": 0.07, "教育费附加费率": 0.03, "地方教育附加费率": 0.02})
        self.assertEqual(out["结果"]["城市维护建设税"], 7000.0)
        self.assertEqual(out["结果"]["应纳税费合计"], 12000.0)

    def test_land_value_add_tax_super_progressive(self):
        """超率累进：档界按扣除项目金额的倍数计，与速算扣除系数互验。"""
        out = tax_calc.run("超率累进", {
            "转让收入": 3500000, "扣除项目金额": 1000000, "税率表": LVAT_TABLE})
        self.assertEqual(out["结果"]["增值额"], 2500000.0)
        self.assertEqual(out["结果"]["增值率"], 2.5)
        # 档界换算成绝对额是 50万/100万/200万：50万×30% + 50万×40% + 100万×50% + 50万×60%
        self.assertEqual(out["结果"]["应纳税额"], 1150000.0)
        self.assertEqual(out["步骤"][2]["分档"]["适用档"], 4)
        self.assertEqual(out["步骤"][2]["分档"]["速算式"],
                         "2500000.00 × 0.6 − 350000.00")

    def test_iit_annual_settlement_keeps_the_base_visible(self):
        out = tax_calc.run("综合所得年度汇算", {
            "收入总额": 200000, "费用减除": 60000, "专项扣除": 20000,
            "专项附加扣除": 24000, "税率表": IIT_TABLE, "已预缴税额": 5000})
        self.assertEqual(out["结果"]["应纳税所得额"], 96000.0)
        self.assertEqual(out["结果"]["应纳税额"], 7080.0)      # 36000×3%+60000×10%
        self.assertEqual(out["结果"]["应补退税额"], 2080.0)
        # 减下去的那一步必须留在输出里：③ 检回的是"每年基本减除费用 X 元"这类条文，
        # 只有中间量在，读者才能核所得额是不是从这几项来的。
        self.assertEqual(out["步骤"][0]["代入"],
                         "200000.00 − 60000.00 − 20000.00 − 24000.00")

    def test_ad_valorem(self):
        out = tax_calc.run("从价计征", {"计税依据": 8000000, "税率": 0.012})
        self.assertEqual(out["结果"]["应纳税额"], 96000.0)


class TestSourcesAndOutput(unittest.TestCase):
    def test_parameter_source_pairs_survive(self):
        out = tax_calc.run("从价计征", {
            "计税依据": {"值": 8000000, "来源": "https://example.gov.cn/a"},
            "税率": {"值": 0.012, "来源": ""}})
        self.assertEqual([p["来源"] for p in out["参数"]],
                         ["https://example.gov.cn/a", ""])
        self.assertEqual(out["无来源参数"], ["税率"],
                         "没给来源的参数必须点名列出，否则答案里那句"
                         "「参数均来自检回文件」就是假话")

    def test_substituted_expression_is_readable(self):
        """代入这一栏把参数名换成实际数字，读者照着它核文件。"""
        out = tax_calc.run("从价计征", {"计税依据": 8000000, "税率": 0.012})
        self.assertEqual(out["步骤"][0]["代入"], "8000000.00 × 0.012")

    def test_intermediate_and_rate_table_substitute(self):
        """中间量也要换得出来：只有第一步有数字、后面几步仍是名字，核到第二步就断了。"""
        out = tax_calc.run("增值税一般计税", {
            "不含税销售额": 1000000, "适用税率": 0.13,
            "进项税额": 200000, "进项税额转出": 0, "上期留抵税额": 0})
        self.assertEqual(out["步骤"][0]["代入"], "1000000.00 × 0.13")
        self.assertEqual(out["步骤"][2]["代入"], "130000.00 − 200000.00 − 0")
        out = tax_calc.run("超额累进", {"计税基数": 120000, "税率表": IIT_TABLE})
        self.assertEqual(out["步骤"][0]["代入"], "分段累进(120000.00, 7 档)")

    def test_output_is_json_serialisable(self):
        out = tax_calc.run("增值税一般计税", {
            "不含税销售额": 1000000, "适用税率": 0.13,
            "进项税额": 100000, "进项税额转出": 0, "上期留抵税额": 0})
        json.dumps(out, ensure_ascii=False)      # Decimal 混进去就会在这里炸

    def test_result_keys_are_declared_per_skeleton(self):
        """每个骨架的「结果」都要在步骤里定义过——否则输出里那一栏是拼出来的空话。"""
        for name, sk in tax_calc.SKELETONS.items():
            defined = {s.split("=", 1)[0].strip() for s in sk["步骤"]}
            for key in sk["结果"]:
                self.assertIn(key, defined, f"骨架「{name}」声明了结果 {key}，没有步骤产出它")


class TestCli(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = tax_calc.main(argv)
        return code, buf.getvalue()

    def test_list_exits_zero_and_names_inputs(self):
        code, out = self._run(["--list"])
        self.assertEqual(code, 0)
        for name in tax_calc.SKELETONS:
            self.assertIn(name, out)

    def test_missing_params_exit_two_and_names_the_gap(self):
        code, out = self._run(["增值税一般计税", "--set", "不含税销售额=1000000"])
        self.assertEqual(code, 2, "缺参数应当是 2（转 ② 补问），不是 1（引擎坏了）")
        payload = json.loads(out)
        self.assertEqual(payload["缺参数"],
                         ["适用税率", "进项税额", "进项税额转出", "上期留抵税额"])
        self.assertIn("② 前提补齐", payload["处置"])

    def test_rate_table_via_cli_json(self):
        """税率表这类复合参数从命令行走 JSON，否则累进骨架在 CLI 上根本跑不通。"""
        code, out = self._run([
            "超额累进", "--set", "计税基数=120000",
            "--set", "税率表=" + json.dumps(IIT_TABLE, ensure_ascii=False),
            "--src", "税率表=https://example.gov.cn/iit"])
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["结果"]["应纳税额"], 9480.0)
        self.assertEqual(payload["无来源参数"], ["计税基数"])

    def test_thousands_separator_is_not_silently_mangled(self):
        code, out = self._run(["从价计征", "--set", "计税依据=1,000,000",
                              "--set", "税率=0.012"])
        self.assertEqual(code, 1)
        self.assertIn("不是可解析的数值", json.loads(out)["error"])

    def test_set_and_src_pair_into_named_source(self):
        code, out = self._run(["从价计征",
                               "--set", "计税依据=8000000", "--src", "计税依据=https://x/1",
                               "--set", "税率=0.012", "--src", "税率=https://x/2"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["无来源参数"], [])
        self.assertEqual(payload["结果"]["应纳税额"], 96000.0)

    def test_bad_skeleton_name_is_error_one(self):
        code, out = self._run(["不存在", "--set", "a=1"])
        self.assertEqual(code, 1)
        self.assertIn("没有这个骨架", json.loads(out)["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
