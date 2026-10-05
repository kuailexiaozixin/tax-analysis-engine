#!/usr/bin/env python3
"""稽查模拟模块 `scripts/tax_inspect.py` 的离线用例。不联网、不调模型。

守的是六类失效路径，每类都带一条"打不红就是用例空了"的变异自检：

1. **注册表的结构与红线在载入时判**：`POINT_KEYS` 那十格少一格、域代号跳号、要点代号
   跨域、整改动作写出补造或倒签、问询写出稽查结果预测——都要 `load()` 直接报错，
   而不是等输出里冒出来。
2. **词表只从要点派生**：风险指标与适用场景各只有一个定义处。域级再写一份触发信号
   就会漂（注册表初版两处清单在七个域上全部对不上，数字记在表里）。
3. **三类缺口各自判得动**：缺材料、材料矛盾、政策口径不清——给与不给必须翻转状态，
   单边有数不许写成"一致"，第四类缺口不许冒出来。
4. **没问、没答、答了但缺，是三件事**：覆盖判 `partial` 并逐个列出未询问要点；
   没作答写"未作答"而不是替企业编一句回答。
5. **输出不越红线**：渲染全文不含预测稽查结果的措辞，也不含补造历史的整改动作。
6. **文档与本表同源**：SKILL ⑥ 的形态清单、快速索引、README、风险框架那一节与本测试
   指向同一实现；门禁的离线组里也得点名本文件，否则这整份用例平时不跑。

用法：`python tests/test_inspect.py`
"""

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import tax_inspect        # noqa: E402

REG = tax_inspect.load()
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
TEMPLATES = (ROOT / "references" / "output_templates.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
FRAMEWORK = (ROOT / "references" / "tax_risk_framework.md").read_text(encoding="utf-8")

ALL_POINTS = tax_inspect.points(REG)
BY_CODE = {pt["代号"]: (d, pt) for d, pt in ALL_POINTS}


def one_point(pt: str) -> dict:
    """按要点代号取注册表里那一格（可读别名，避免测试正文写满字典路径）。"""
    return BY_CODE[pt][1]


def ans(code: str, materials=(), numbers=None, reply="", year="2024", basis="…"):
    return {code: {"材料": list(materials), "数值": numbers or {}, "回答": reply,
                   "口径年度": year, "口径依据": basis}}


def gaps_of(out: dict, code: str, kind: str) -> list:
    unit = next(u for u in out["问询"] if u["代号"] == code)
    return [g for g in unit["证据缺口"] if g["类"] == kind]


def pair_of(code: str, index: int = 0) -> tuple:
    p = BY_CODE[code][1]["一致性核对"][index]
    return p["项A"], p["项B"]


class TestRegistryShape(unittest.TestCase):
    """七个域、22 个要点，每一格都带齐生成六字段所需的来源。"""

    def test_seven_domains_named_by_the_users_own_examples(self):
        self.assertEqual([d["代号"] for d in REG["检查域"]],
                         [f"D{i}" for i in range(1, 8)])
        themes = " ".join(pt["主题"] for _d, pt in ALL_POINTS)
        for named in ("工时", "辅助账", "失败项目"):
            self.assertIn(named, themes, f"用户点名的「{named}」在表里没有落点")

    def test_point_count_is_pinned(self):
        self.assertEqual(len(ALL_POINTS), 22,
                         "要点数变了要同步 SKILL 与 README 里的计数；表可以长，但得有人签字")

    def test_every_point_carries_the_source_fields(self):
        for _d, pt in ALL_POINTS:
            for field in tax_inspect.POINT_KEYS:
                self.assertIn(field, pt, f"{pt['代号']} 缺『{field}』")
            for field in ("支撑材料", "可能追问", "整改动作", "风险指标"):
                self.assertTrue(pt[field], f"{pt['代号']} 的『{field}』是空清单")

    def test_domain_scenarios_are_declared_and_unique_inside_a_domain(self):
        for d in REG["检查域"]:
            self.assertTrue(d["适用"], f"{d['代号']} 没写适用场景，--scenario 就选不到它")
            self.assertEqual(len(d["适用"]), len(set(d["适用"])),
                             f"{d['代号']} 的适用场景有重复项")

    def test_check_pairs_name_two_sides_and_a_reason(self):
        for _d, pt in ALL_POINTS:
            for p in pt["一致性核对"]:
                self.assertNotEqual(p["项A"], p["项B"],
                                    f"{pt['代号']} 有一条自己跟自己比的核对项")
                self.assertTrue(p["说明"], f"{pt['代号']} 的核对项没写为什么要比")


class TestValidateCatchesDrift(unittest.TestCase):
    """结构缺陷与两条红线都由 `load()` 拦住；每条都有注入变异证明它真会报红。"""

    def setUp(self):
        self.clean = copy.deepcopy(REG)

    def _break(self, fn):
        reg = copy.deepcopy(self.clean)
        fn(reg)
        with self.assertRaises(ValueError) as ctx:
            tax_inspect.validate(reg)
        return str(ctx.exception)

    def test_baseline_validates(self):
        tax_inspect.validate(self.clean)

    def test_dropped_field_is_caught(self):
        def fn(reg):
            del reg["检查域"][0]["检查要点"][0]["支撑材料"]
        self.assertIn("支撑材料", self._break(fn))

    def test_empty_material_entry_is_caught(self):
        def fn(reg):
            reg["检查域"][0]["检查要点"][0]["支撑材料"][0] = "  "
        self.assertIn("空的", self._break(fn))

    def test_point_code_outside_its_domain_is_caught(self):
        def fn(reg):
            reg["检查域"][1]["检查要点"][0]["代号"] = "D9-1"
        self.assertIn("不在所属域", self._break(fn))

    def test_domain_gap_is_caught(self):
        def fn(reg):
            reg["检查域"][2]["代号"] = "D8"
        self.assertIn("连续", self._break(fn))

    def test_fabrication_in_remediation_is_caught(self):
        word = tax_inspect.FORBIDDEN_REMEDIATION[0]

        def fn(reg):
            reg["检查域"][0]["检查要点"][0]["整改动作"][0] = f"{word}三个月的工时表"
        msg = self._break(fn)
        self.assertIn(word, msg)

    def test_prediction_in_the_question_is_caught(self):
        phrase = tax_inspect.PREDICTION_PHRASES[0]

        def fn(reg):
            reg["检查域"][0]["检查要点"][0]["问询"] = f"这一项{phrase}，你怎么解释？"
        self.assertIn(phrase, self._break(fn))

    def test_restored_trigger_column_is_caught(self):
        """域级『触发信号』一旦回来，词表就变成两处各写一份，必须报错而不是照读。"""
        def fn(reg):
            reg["检查域"][0]["触发信号"] = ["随便一条"]
        self.assertIn("触发信号", self._break(fn))

    def test_missing_consent_side_of_a_pair_is_caught(self):
        def fn(reg):
            del reg["检查域"][0]["检查要点"][0]["一致性核对"][0]["项B"]
        self.assertIn("项B", self._break(fn))


class TestDerivedVocabulary(unittest.TestCase):
    """风险指标与适用场景只从要点／域派生一次，筛选取值域就是派生结果。"""

    def test_signals_are_the_union_of_point_indicators(self):
        want = []
        for _d, pt in ALL_POINTS:
            for r in pt["风险指标"]:
                if r not in want:
                    want.append(r)
        self.assertEqual(tax_inspect.signals(REG), want)

    def test_scenarios_are_the_union_of_domain_uses(self):
        want = []
        for d in REG["检查域"]:
            for s in d["适用"]:
                if s not in want:
                    want.append(s)
        self.assertEqual(tax_inspect.scenarios(REG), want)

    def test_no_indicator_is_orphaned_from_every_filter(self):
        """每个指标都至少被一个要点带，`--signal` 才不会筛出一片空。"""
        for s in tax_inspect.signals(REG):
            self.assertTrue(tax_inspect.select(REG, signal=s), f"{s} 选不出任何要点")

    def test_scenario_spans_more_than_the_two_preftext_uses(self):
        """全税种不是口号：四个不带研发字样的场景要选得到问，高企那条也在同一张表里。"""
        for scenario in ("增值税进项税额抵扣", "跨境支付代扣代缴", "个人所得税薪金申报",
                         "资产损失税前扣除", "高新技术企业资格存续"):
            self.assertIn(scenario, tax_inspect.scenarios(REG),
                          f"『{scenario}』没进适用场景，--scenario 选不到它")
            self.assertTrue(tax_inspect.select(REG, scenario=scenario))

    def test_unknown_selection_names_error_out_with_the_value_domain(self):
        for kwargs, token in (({"domains": ["D9"]}, "D9"),
                              ({"codes": ["D1-9"]}, "D1-9"),
                              ({"scenario": "车船税"}, "车船税"),
                              ({"signal": "根本没这条指标"}, "根本没这条指标")):
            with self.assertRaises(ValueError) as ctx:
                tax_inspect.select(REG, **kwargs)
            msg = str(ctx.exception)
            self.assertIn(token, msg)
            self.assertTrue("可选" in msg or "现有取值" in msg,
                            f"{kwargs} 的报错没列出可用取值，改一次猜一次")

    def test_filters_nest_instead_of_unioning(self):
        """域圈范围，场景在范围内再筛：D1 里没有增值税场景，就该是空。"""
        vat = "增值税进项税额抵扣"
        self.assertTrue(tax_inspect.select(REG, scenario=vat))
        self.assertEqual(tax_inspect.select(REG, domains=["D1"], scenario=vat), [])


class TestSixFieldsPerQuestion(unittest.TestCase):
    """输出的每一问都带齐六格，缺一格就退化成提示。"""

    def test_every_unit_carries_the_six_fields(self):
        out = tax_inspect.build()
        for u in out["问询"]:
            for f in tax_inspect.SIX_FIELDS:
                self.assertIn(f, u, f"{u['代号']} 少了『{f}』这一格")

    def test_missing_answer_is_recorded_not_invented(self):
        out = tax_inspect.build(codes=["D1-1"])
        unit = out["问询"][0]
        self.assertEqual(unit["现有回答"], tax_inspect.UNANSWERED)
        self.assertFalse(unit["是否作答"])

    def test_answer_is_passed_through_verbatim(self):
        reply = "工时按项目周报登记，月末签认"
        out = tax_inspect.build(ans("D1-1", reply=reply), codes=["D1-1"])
        self.assertEqual(out["问询"][0]["现有回答"], reply)
        self.assertTrue(out["问询"][0]["是否作答"])

    def test_remediation_lines_carry_no_forbidden_wording(self):
        for _d, pt in ALL_POINTS:
            for act in pt["整改动作"]:
                self.assertEqual(tax_inspect._bad_words(act,
                                                       tax_inspect.FORBIDDEN_REMEDIATION),
                                 [], f"{pt['代号']} 的整改动作越了红线：{act}")

    def test_gap_actions_never_write_the_evidence_for_them(self):
        for kind, action in tax_inspect.GAP_ACTION.items():
            self.assertTrue(action, f"{kind} 没有动作")
            self.assertFalse(tax_inspect._bad_words(action,
                                                   tax_inspect.FORBIDDEN_REMEDIATION))
        self.assertIn("不代写", tax_inspect.GAP_ACTION[tax_inspect.GAP_MISSING])
        self.assertIn("不由企业自选",
                      tax_inspect.GAP_ACTION[tax_inspect.GAP_CALIBER])


class TestMissingMaterials(unittest.TestCase):
    def test_unanswered_question_lacks_every_material(self):
        out = tax_inspect.build(codes=["D1-2"])
        unit = out["问询"][0]
        want = len(one_point("D1-2")["支撑材料"])
        self.assertEqual(len(gaps_of(out, "D1-2", tax_inspect.GAP_MISSING)), want)
        self.assertEqual(len(unit["支撑材料"]), want)

    def test_supplying_a_material_clears_its_gap_only(self):
        first = one_point("D1-2")["支撑材料"][0]
        out = tax_inspect.build({"D1-2": {"材料": [first]}}, codes=["D1-2"])
        left = gaps_of(out, "D1-2", tax_inspect.GAP_MISSING)
        self.assertEqual(len(left), len(one_point("D1-2")["支撑材料"]) - 1)
        self.assertNotIn(first, [g["对象"] for g in left])

    def test_short_label_does_not_count_as_coverage(self):
        """「表」这类短串不许把三张不同的表认成一张。"""
        self.assertEqual(tax_inspect.covered_by("差异调节表", ["表"]), "")
        self.assertEqual(tax_inspect.covered_by("差异调节表", ["个税"]), "")

    def test_parenthetical_qualifier_is_stripped_before_matching(self):
        required = one_point("D1-2")["支撑材料"][0]
        self.assertIn("（", required, "这条用例依赖带限定语的表样，表样改了要换要点")
        self.assertTrue(tax_inspect.covered_by(required, ["研发人员名册"]))

    def test_matching_is_two_way(self):
        required = "逐人逐项目的工时记录（含起止日期与工作内容）"
        self.assertTrue(tax_inspect.covered_by(required, ["逐人逐项目的工时记录"]))
        self.assertTrue(tax_inspect.covered_by("研发人员名册",
                                               ["研发人员名册（2024 版）"]))


class TestNumberConflicts(unittest.TestCase):
    def test_matching_numbers_report_no_conflict(self):
        a, b = pair_of("D1-3")
        out = tax_inspect.build({"D1-3": {"数值": {a: 100, b: 100}}}, codes=["D1-3"])
        self.assertEqual(gaps_of(out, "D1-3", tax_inspect.GAP_CONFLICT), [])
        self.assertEqual(out["问询"][0]["一致性核对"][0]["状态"],
                         tax_inspect.CHECK_AGREED)

    def test_differing_numbers_name_both_sides_and_the_difference(self):
        a, b = pair_of("D1-3")
        out = tax_inspect.build({"D1-3": {"数值": {a: 100, b: 80}}}, codes=["D1-3"])
        gap = gaps_of(out, "D1-3", tax_inspect.GAP_CONFLICT)[0]
        self.assertIn(a, gap["对象"])
        self.assertIn(b, gap["对象"])
        self.assertIn("20", gap["说明"])
        self.assertIn("20.00%", gap["说明"])

    def test_each_pair_is_judged_separately(self):
        """D1-2 有两对各判一次，不能只比第一对。"""
        (a1, b1), (a2, b2) = pair_of("D1-2", 0), pair_of("D1-2", 1)
        out = tax_inspect.build({"D1-2": {"数值": {a1: 10, b1: 10, a2: 5, b2: 9}}},
                                codes=["D1-2"])
        conflicts = gaps_of(out, "D1-2", tax_inspect.GAP_CONFLICT)
        self.assertEqual(len(conflicts), 1)
        self.assertIn(a2, conflicts[0]["对象"])

    def test_one_sided_number_is_not_recorded_as_agreement(self):
        a, b = pair_of("D5-1")
        out = tax_inspect.build({"D5-1": {"数值": {a: 120}}}, codes=["D5-1"])
        row = out["问询"][0]["一致性核对"][0]
        self.assertEqual(row["状态"], tax_inspect.CHECK_ONE_SIDED)
        self.assertEqual(gaps_of(out, "D5-1", tax_inspect.GAP_CONFLICT), [])

    def test_no_numbers_at_all_is_a_fourth_state(self):
        out = tax_inspect.build(codes=["D5-3"])
        self.assertEqual(out["问询"][0]["一致性核对"][0]["状态"],
                         tax_inspect.CHECK_NOT_TAKEN)

    def test_tolerance_tolerates_only_within_the_ratio(self):
        a, b = pair_of("D2-1")
        numbers = {"D2-1": {"数值": {a: 1000, b: 996}}}
        self.assertEqual(gaps_of(tax_inspect.build(numbers, codes=["D2-1"],
                                                   tolerance=0.005),
                                 "D2-1", tax_inspect.GAP_CONFLICT), [])
        self.assertEqual(len(gaps_of(tax_inspect.build(numbers, codes=["D2-1"]),
                                     "D2-1", tax_inspect.GAP_CONFLICT)), 1)

    def test_absurd_tolerance_is_rejected(self):
        """容差 1.2 等于把所有差异判成一致，那是放行不是核对。"""
        for bad in (1.0, 1.2, -0.1):
            with self.assertRaises(ValueError):
                tax_inspect.build(codes=["D2-1"], tolerance=bad)

    def test_non_numeric_value_names_the_item_instead_of_crashing(self):
        a, b = pair_of("D4-2")
        with self.assertRaises(ValueError) as ctx:
            tax_inspect.build({"D4-2": {"数值": {a: "约十万元", b: 1000}}}, codes=["D4-2"])
        self.assertIn(a, str(ctx.exception))

    def test_numbers_under_unknown_item_are_reported(self):
        a, _b = pair_of("D7-1")
        out = tax_inspect.build({"D7-1": {"数值": {a: 1, "打错了的核对项": 2}}},
                                codes=["D7-1"])
        self.assertIn("打错了的核对项", out["问询"][0]["核对未用"])


class TestCaliberUnclear(unittest.TestCase):
    def test_missing_year_and_basis_is_one_gap_naming_both(self):
        out = tax_inspect.build(codes=["D7-4"])
        gap = gaps_of(out, "D7-4", tax_inspect.GAP_CALIBER)[0]
        self.assertIn("口径年度", gap["说明"])
        self.assertIn("口径依据", gap["说明"])
        self.assertIn("⑧", gap["动作"])

    def test_supplying_only_the_year_still_leaves_the_basis(self):
        out = tax_inspect.build({"D7-4": {"口径年度": "2023"}}, codes=["D7-4"])
        gap = gaps_of(out, "D7-4", tax_inspect.GAP_CALIBER)[0]
        self.assertIn("口径依据", gap["说明"])
        self.assertNotIn("口径年度", gap["说明"])

    def test_both_fields_clear_the_gap(self):
        out = tax_inspect.build({"D7-4": {"口径年度": "2023",
                                          "口径依据": "国家税务总局公告2023年第XX号"}},
                                codes=["D7-4"])
        self.assertEqual(gaps_of(out, "D7-4", tax_inspect.GAP_CALIBER), [])

    def test_no_fourth_gap_class_appears(self):
        out = tax_inspect.build(codes=["D1-1", "D6-3", "D7-3"])
        for unit in out["问询"]:
            for g in unit["证据缺口"]:
                self.assertIn(g["类"], tax_inspect.GAP_TYPES,
                              f"{unit['代号']} 冒出了第四类缺口")


class TestCoverageIsHonest(unittest.TestCase):
    def test_full_registry_run_is_complete(self):
        out = tax_inspect.build()
        self.assertEqual(out["覆盖"]["状态"], tax_inspect.COVERAGE_COMPLETE)
        self.assertEqual(out["覆盖"]["未询问"], [])
        self.assertEqual(out["本次范围"]["问询数"], len(ALL_POINTS))

    def test_filtered_run_lists_every_unasked_point(self):
        out = tax_inspect.build(domains=["D1"])
        self.assertEqual(out["覆盖"]["状态"], tax_inspect.COVERAGE_PARTIAL)
        unasked = {x["代号"] for x in out["覆盖"]["未询问"]}
        asked = {u["代号"] for u in out["问询"]}
        self.assertEqual(unasked | asked, set(BY_CODE))
        self.assertFalse(unasked & asked)
        self.assertNotIn("没问题", out["覆盖"]["说明"])

    def test_empty_range_says_so_instead_of_looking_finished(self):
        out = tax_inspect.build(domains=["D1"], scenario="转让定价")
        self.assertEqual(out["问询"], [])
        self.assertIn("范围筛空", out["覆盖"]["说明"])
        self.assertEqual(out["覆盖"]["状态"], tax_inspect.COVERAGE_PARTIAL)

    def test_answers_for_a_typo_code_are_not_dropped_silently(self):
        out = tax_inspect.build({"D1-9": {"材料": ["随便一份"]},
                                 "D7-1": {"材料": ["年度纳税申报表及附表"]}},
                                domains=["D1"])
        self.assertIn("D1-9", out["未用的回答"]["代号不在注册表里"])
        self.assertIn("D7-1", out["未用的回答"]["本次没问这一格"])

    def test_unasked_plus_asked_is_the_whole_registry_for_every_domain(self):
        """逐域各跑一次：状态由未询问推出来，不是写死的一句话。"""
        total = len(ALL_POINTS)
        for d in REG["检查域"]:
            out = tax_inspect.build(domains=[d["代号"]])
            asked = len(out["问询"])
            self.assertEqual(asked + len(out["覆盖"]["未询问"]), total,
                             f"{d['代号']} 这一路的覆盖账对不上")
            self.assertEqual(out["覆盖"]["状态"],
                             tax_inspect.COVERAGE_PARTIAL if asked < total
                             else tax_inspect.COVERAGE_COMPLETE)

    def test_mutation_answering_with_fabricated_material_is_caught(self):
        """材料匹配一旦被改成"永远算对上"，缺材料就会整层消失。"""
        real = tax_inspect.covered_by
        tax_inspect.covered_by = lambda req, provided: (provided or ["?"])[0]
        try:
            out = tax_inspect.build(codes=["D1-1"])
            self.assertEqual(gaps_of(out, "D1-1", tax_inspect.GAP_MISSING), [],
                             "判据被改成永远命中却没有被检出")
        finally:
            tax_inspect.covered_by = real
        self.assertTrue(gaps_of(tax_inspect.build(codes=["D1-1"]),
                                "D1-1", tax_inspect.GAP_MISSING))


class TestRender(unittest.TestCase):
    def test_six_field_labels_are_all_printed(self):
        text = tax_inspect.render(tax_inspect.build(domains=["D1"]))
        for f in tax_inspect.SIX_FIELDS:
            self.assertIn(f"  {f}", text, f"渲染里没有『{f}』这一格")

    def test_render_never_predicts_an_inspection_result(self):
        text = tax_inspect.render(tax_inspect.build())
        for phrase in tax_inspect.PREDICTION_PHRASES:
            self.assertNotIn(phrase, text)
        self.assertIn("不预测稽查结果", text)

    def test_render_carries_the_unasked_list_and_the_deny_its_meaning(self):
        text = tax_inspect.render(tax_inspect.build(domains=["D7"]))
        self.assertIn("未询问", text)
        self.assertIn("不代表没问题", text)
        self.assertIn("D1-1", text)

    def test_render_marks_empty_range_as_not_finished(self):
        text = tax_inspect.render(tax_inspect.build(domains=["D1"], scenario="转让定价"))
        self.assertIn("范围筛空", text)

    def test_gap_lines_carry_both_the_object_and_the_action(self):
        text = tax_inspect.render(tax_inspect.build(codes=["D2-3"]))
        self.assertIn(f"[{tax_inspect.GAP_MISSING}]", text)
        self.assertIn("动作：", text)


class TestCli(unittest.TestCase):
    def run_cli(self, *argv):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "tax_inspect.py"),
                               *argv], capture_output=True, text=True, encoding="utf-8")

    def test_list_shows_domains_points_and_the_value_domains(self):
        r = self.run_cli("--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        for code in list(BY_CODE)[:3]:
            self.assertIn(code, r.stdout)
        self.assertIn("要点共 22 个", r.stdout)
        self.assertIn("转让定价", r.stdout)

    def test_json_shape_is_the_same_as_build(self):
        r = self.run_cli("--domain", "D1", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["本次范围"]["问询数"], 3)
        self.assertIn("检查目的", out["问询"][0])
        self.assertEqual(sorted(out["缺口统计"]), sorted(tax_inspect.GAP_TYPES))

    def test_unknown_domain_is_exit_two_with_the_list(self):
        r = self.run_cli("--domain", "D9")
        self.assertEqual(r.returncode, 2)
        self.assertIn("D9", r.stdout + r.stderr)

    def test_answers_file_of_wrong_shape_is_exit_two(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(["不是对象"], f, ensure_ascii=False)
            path = f.name
        try:
            r = self.run_cli("--domain", "D1", "--answers", path)
            self.assertEqual(r.returncode, 2)
            self.assertIn("要点代号", r.stdout)
        finally:
            Path(path).unlink()


class TestDocsShareTheImplementation(unittest.TestCase):
    """文档与本表说同一件事；漂了就报红，而不是各说各话。"""

    def test_skill_routes_to_the_module(self):
        self.assertIn("scripts/tax_inspect.py", SKILL)
        self.assertIn("稽查模拟问询式", SKILL)

    def test_output_templates_define_the_question_shape(self):
        self.assertIn("## 稽查模拟问询式", TEMPLATES)
        self.assertIn("- [稽查模拟问询式](#稽查模拟问询式)", TEMPLATES)
        body = TEMPLATES.split("## 稽查模拟问询式", 1)[1]
        for f in tax_inspect.SIX_FIELDS:
            self.assertIn(f, body, f"模板里少了『{f}』这一格")
        self.assertIn("未询问", body)
        # ⑥ 只留触发条件与命令，「未用的回答」这一格连成因一起搬进模板；两个成因的
        # 名字取自代码常量，改名或删格都要在这里报红，否则搬过去的细则会没人认领。
        self.assertIn("未用的回答", body)
        for cause in tax_inspect.UNUSED_HINTS:
            self.assertIn(cause, body, f"模板里少了『{cause}』这一成因")

    def test_mutation_unused_answer_cell_dropped_is_caught(self):
        """自检：把模板那一格整格删掉，上一条判据必须报红。"""
        body = TEMPLATES.split("## 稽查模拟问询式", 1)[1]
        broken = body.replace("**未用的回答**", "**答过的数**", 1)
        self.assertNotEqual(broken, body, "变异没落到那一格的标签上")
        self.assertIn("未用的回答", body)
        self.assertNotIn("未用的回答", broken, "删掉这一格后判据不会报红")

    def test_risk_framework_links_the_question_layer(self):
        self.assertIn("tax_inspect", FRAMEWORK)

    def test_readme_lists_the_module(self):
        self.assertIn("tax_inspect.py", README)

    def test_registry_readers_all_exist(self):
        """注册表自称"谁在读"，那几份文件就得真在——写下不存在的读者等于没写。"""
        for who in REG["_说明"]["谁在读"]:
            rel = who.split("（")[0].strip()
            self.assertTrue((ROOT / rel).exists(), f"注册表说 {rel} 在读这一格，文件却不在")
            self.assertTrue(Path(rel).suffix, f"{rel} 不像路径")

    def test_gap_classes_agree_between_registry_and_code(self):
        """三类缺口在注册表与代码里必须是同一串名字，不各写一份。"""
        self.assertEqual(sorted(REG["_说明"]["三类证据缺口"]), sorted(tax_inspect.GAP_TYPES))
        self.assertEqual(sorted(tax_inspect.GAP_ACTION), sorted(tax_inspect.GAP_TYPES))

    def test_six_field_names_match_between_table_and_code(self):
        listed = REG["_说明"]["六字段"]
        for f in tax_inspect.SIX_FIELDS:
            self.assertIn(f, listed, f"注册表『六字段』那格没写 {f}")

    def test_gate_actually_runs_this_file(self):
        """门禁里点名本文件——写了用例却没进门禁，等于没写。

        离线组第一项是在本进程里跑的编译函数，不是 argv，所以先过掉 callable。
        """
        sys.path.insert(0, str(ROOT / "tests"))
        import run_all
        listed = " ".join(" ".join(map(str, cmd))
                          for _name, cmd in run_all.OFFLINE_GROUP
                          if not callable(cmd))
        self.assertIn("test_inspect.py", listed,
                      "tests/run_all.py 的离线组没点名 test_inspect.py，"
                      "这一整份用例一次都不会跑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
