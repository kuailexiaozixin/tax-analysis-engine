#!/usr/bin/env python3
"""证据覆盖率注册表与 `scripts/tax_coverage.py` 的离线用例。不联网、不调模型。

这层要证明的是一句话：**"依据够不够"从人给的一句话，变成能从表推导的数**。
所以用例分五组，各组盯的失效路径不同：

1. **表与代码同源**：`项` 必须逐字等于 `tax_analyze.QUESTION_TYPES[型]["needs"]`，
   轴键必须落在 `CONTEXT_AXES` 里。两处一旦各写各的名字，覆盖率就在数一组对不上的条目。
2. **四种状态判得动**：`evidence=None`（还没检）与 `[]`（检了没有）必须分成
   待核与缺——混成一个，检索失败会被读成"这类题不需要依据"，那是本仓库反复修的
   静默降级。`不适用` 不进分母，否则政策查询题会因为没追问主体而被记成不充分。
3. **三处消费一张表**：前提状态来自 `detect_context_gaps`，参数清单来自
   `tax_calc.inputs`，都不是覆盖率层自己另立的一份。这两条各有一条"改了上游、
   下游跟着变"的用例。
4. **覆盖率的算法**：分子分母逐题钉住，含分母为 0 的出口。
5. **变异自检**：上面每条判据都要能被打红，打不红说明用例是空的。

用法：`python tests/test_evidence_coverage.py`
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import tax_analyze       # noqa: E402
import tax_calc          # noqa: E402
import tax_coverage      # noqa: E402

REG = tax_coverage.load()


def spec(reg, qtype):
    return reg["题型"][qtype]


def find(result, prefix):
    return [i for i in result["分项"] if i["项"].startswith(prefix)]


class TestTableAgreesWithCode(unittest.TestCase):
    """表里的名字全部引用代码里的符号集合，不各立一套。"""

    def test_registry_types_equal_question_types(self):
        self.assertEqual(sorted(REG["题型"]), sorted(tax_analyze.QUESTION_TYPES))

    def test_requirement_names_are_verbatim_from_needs(self):
        for name in tax_analyze.QUESTION_TYPES:
            self.assertEqual([i["项"] for i in spec(REG, name)["依据要件"]],
                             list(tax_analyze.QUESTION_TYPES[name]["needs"]),
                             f"题型「{name}」的要件名与 needs 不再是同一串字")

    def test_axis_keys_exist_and_cover_the_rule_in_2(self):
        """规则陈述型只走时点轴，其余四轴全走——这条规则在 ②，表必须与它一致。"""
        for name in tax_analyze.QUESTION_TYPES:
            axes = spec(REG, name)["前提轴"]
            self.assertTrue(set(axes) <= set(tax_analyze.CONTEXT_AXES),
                            f"题型「{name}」写了未知前提轴 {axes}")
            want = (list(tax_analyze.RULE_AXES)
                    if name in tax_analyze.RULE_STATEMENT_TYPES
                    else list(tax_analyze.CONTEXT_AXES))
            self.assertEqual(axes, want, f"题型「{name}」的前提轴与 ② 的处理规则不一致")

    def test_every_compute_flag_matches_a_real_skeleton_set(self):
        """只有"要出数"的两类挂算式项；挂了就必须有可选骨架，否则这项永远待核。"""
        for name, s in REG["题型"].items():
            if s["算式"]:
                self.assertTrue(tax_calc.SKELETONS, f"「{name}」要算式但没有骨架可选")

    def test_hit_words_are_not_empty(self):
        for name, s in REG["题型"].items():
            for it in s["依据要件"]:
                self.assertTrue(it["命中词"] and all(k.strip() for k in it["命中词"]),
                                f"「{name}」要件「{it['项']}」命中词为空，永远判缺")


class TestValidateCatchesDrift(unittest.TestCase):
    """载入即比对：表漂了要报错，不能读到一个对不上的名字就当没有这一项。"""

    def _drift(self, mutate):
        reg = copy.deepcopy(REG)
        mutate(reg)
        with self.assertRaises(ValueError):
            tax_coverage.validate(reg)

    def test_dropped_requirement_is_caught(self):
        self._drift(lambda r: r["题型"]["liability"]["依据要件"].pop())

    def test_renamed_requirement_is_caught(self):
        self._drift(lambda r: r["题型"]["liability"]["依据要件"][0].update(
            {"项": "改了个名字"}))

    def test_unknown_axis_is_caught(self):
        self._drift(lambda r: r["题型"]["liability"]["前提轴"].append("行业"))

    def test_empty_hit_words_are_caught(self):
        self._drift(lambda r: r["题型"]["liability"]["依据要件"][0].update(
            {"命中词": ["  "]}))

    def test_type_added_to_code_but_not_to_table_is_caught(self):
        with mock.patch.dict(tax_analyze.QUESTION_TYPES,
                             {"新增一类": {"needs": ["甲", "乙"]}}):
            with self.assertRaises(ValueError) as ctx:
                tax_coverage.validate(REG)
            self.assertIn("不一致", str(ctx.exception))


class TestFourStates(unittest.TestCase):
    def test_missing_evidence_is_not_the_same_as_empty_evidence(self):
        """没检=待核，检了没有=缺。混同就是把取数失败读成"这类题不需要依据"。"""
        none = tax_coverage.assess("liability", "2024年某公司销售额30万元")
        empty = tax_coverage.assess("liability", "2024年某公司销售额30万元", evidence=[])
        self.assertEqual([i["状态"] for i in find(none, "计税依据")], ["待核"])
        self.assertEqual([i["状态"] for i in find(empty, "计税依据")], ["缺"])
        # 只看依据那三项：前提轴的缺与算式项的待核是另两回事，混进计数就看不出差异
        self.assertEqual([i["状态"] for i in none["分项"] if i["类"] == "依据"],
                         ["待核", "待核", "待核"])
        self.assertEqual([i["状态"] for i in empty["分项"] if i["类"] == "依据"],
                         ["缺", "缺", "缺"])

    def test_hit_word_marks_the_requirement_satisfied(self):
        ev = [{"title": "关于小规模纳税人减免增值税问题的公告",
               "content": "合计月销售额未超过10万元的，免征增值税"}]
        out = tax_coverage.assess("liability", "2024年某个体户销售额", evidence=ev)
        item = find(out, "减免与加计")[0]
        self.assertEqual(item["状态"], "已满足")
        self.assertIn("免征", item["判据"])

    def test_rule_statement_types_do_not_ask_individual_axes(self):
        """政策查询题没写主体地区是常态：那三轴记不适用，不进分母。"""
        out = tax_coverage.assess("lookup", "个人养老金怎么扣")
        for axis in ("主体与身份", "地区", "金额与规模"):
            self.assertEqual(find(out, f"前提：{axis}")[0]["状态"], "不适用")
        self.assertEqual(len(out["分项"]), 6)        # 1 依据 + 4 轴 + 1 算式
        self.assertEqual(out["计数"]["不适用"], 4)     # 三轴 + 算式项都不进分母

    def test_evidence_fields_reuse_the_grading_layer(self):
        """字段名沿用 ⑧ 的 TOPIC_FIELDS：另定一份就会漏读正文。"""
        ev = [{"body": "本条例所称计税依据，是指纳税人销售货物的销售额"}]
        out = tax_coverage.assess("liability", "2024年某公司销售额30万元", evidence=ev)
        self.assertEqual(find(out, "计税依据")[0]["状态"], "已满足")


class TestOneTableThreeConsumers(unittest.TestCase):
    """② 的追问句与 ⑥ 的参数清单都不是覆盖率层自己抄的第二份。"""

    def test_probes_come_from_the_axis_table(self):
        out = tax_coverage.assess("entitlement", "某公司申请一项补助")
        want = [a["probe"] for a in tax_analyze.CONTEXT_AXES.values()
                if a["probe"] in out["补问"]]
        self.assertTrue(out["补问"])
        self.assertEqual(out["补问"], want, "追问句不是从 CONTEXT_AXES 取的")

    def test_axis_state_follows_detect_context_gaps(self):
        """上游判什么，这里就判什么：给 detect_context_gaps 打桩，分项必须跟着变。"""
        with mock.patch.object(tax_analyze, "detect_context_gaps",
                               lambda q: {"missing": ["entity"], "present": [],
                                          "probes": [], "blocking": True}):
            out = tax_coverage.assess("liability", "随便一句")
        self.assertEqual(find(out, "前提：主体与身份")[0]["状态"], "缺")
        self.assertEqual(find(out, "前提：时点")[0]["状态"], "已满足")

    def test_param_gap_comes_from_tax_calc_inputs(self):
        """给骨架加一个参数，注册表一个字没改，判据就要跟着列出它。"""
        extra = {"测试骨架": {"公式": "应纳税额 = 计税依据 × 税率",
                             "步骤": ["应纳税额 = 计税依据 × 税率 × 换算系数"],
                             "结果": ["应纳税额"]}}
        with mock.patch.dict(tax_calc.SKELETONS, extra):
            out = tax_coverage.assess(
                "liability", "2024年某公司销售额30万元",
                skeleton="测试骨架", params={"计税依据": 1, "税率": 2})
            item = find(out, "展开算式")[0]
            self.assertEqual(item["状态"], "缺")
            self.assertIn("换算系数", item["判据"], "参数清单不是从 tax_calc.inputs 现取的")
            full = tax_coverage.assess(
                "liability", "2024年某公司销售额30万元",
                skeleton="测试骨架", params={"计税依据": 1, "税率": 2, "换算系数": 3})
            self.assertEqual(find(full, "展开算式")[0]["状态"], "已满足")

    def test_no_skeleton_leaves_the_item_to_verify_not_satisfied(self):
        out = tax_coverage.assess("liability", "2024年某公司销售额30万元")
        item = find(out, "展开算式")[0]
        self.assertEqual(item["状态"], "待核")
        self.assertIn("未选定骨架", item["判据"])
        self.assertIn("SKELETONS", item["缺时动作"], "缺时动作要指向选骨架这一步，不能只说待定")


class TestCoverageMath(unittest.TestCase):
    def test_rate_is_satisfied_over_judgable(self):
        ev = [{"title": "减免公告：免征增值税", "content": "计税依据为销售额，税率 3%"}]
        with mock.patch.object(tax_analyze, "detect_context_gaps",
                               lambda q: {"missing": ["place"], "present": [],
                                          "probes": [], "blocking": True}):
            out = tax_coverage.assess("liability", "题面", evidence=ev,
                                      skeleton="从价计征",
                                      params={"计税依据": 1, "税率": 2})
        # 8 项判得动：依据 3 项里 2 项命中（起征点那项缺）、前提 4 轴里 3 项已满足、
        # 算式 1 项已满足 → (2+3+1)/8
        self.assertEqual(out["计数"], {"已满足": 6, "缺": 2, "不适用": 0, "待核": 0})
        self.assertEqual(out["覆盖率"], 0.75)
        self.assertEqual(out["分母说明"], "必备 8 项，不适用 0 项，判得动的 8 项，其中已满足 6 项")

    def test_not_applicable_leaves_the_denominator(self):
        out = tax_coverage.assess("lookup", "某规定是什么")
        self.assertEqual(len(out["分项"]), 6)          # 1 依据 + 4 轴 + 1 算式
        self.assertEqual(out["计数"]["不适用"], 4)      # 三轴 + 算式项都不进分母
        self.assertEqual(out["覆盖率"], 0.0)           # 依据项待核、时点轴缺，都进分母不算满足

    def test_zero_denominator_gives_none_not_one(self):
        """全项不适用时不能报 1.0——那等于"没什么可查的，所以充分"。"""
        reg = {"题型": {"lookup": {"中文名": "政策查询", "依据要件": [],
                                   "前提轴": [], "算式": False}}}
        with mock.patch.object(tax_coverage, "load", return_value=reg):
            out = tax_coverage.assess("lookup", "题面")
        self.assertIsNone(out["覆盖率"])
        self.assertIn("判得动的 0 项", out["分母说明"])

    def test_to_verify_never_counts_as_satisfied(self):
        out = tax_coverage.assess("liability", "")            # 没题面、没依据、没骨架
        self.assertEqual(out["计数"]["已满足"], 0)
        self.assertEqual(out["覆盖率"], 0.0)
        # 待办只列脚本给不出判据又必须有人补的：依据 3 项 + 算式 1 项。
        # 四条前提轴的"缺时动作"是转 ② 追问，那属于补问栏，不重复进待办。
        self.assertEqual(len(out["待办"]), 4)
        self.assertNotIn("前提：", "".join(out["待办"]))
        self.assertIn("展开算式", "".join(out["待办"]))


class TestCli(unittest.TestCase):
    def _run(self, argv):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = tax_coverage.main(argv)
        return code, buf.getvalue()

    def test_list_names_every_type(self):
        code, out = self._run(["--list"])
        self.assertEqual(code, 0)
        for name in tax_analyze.QUESTION_TYPES:
            self.assertIn(name, out)

    def test_assessment_is_json_and_has_the_rate(self):
        code, out = self._run(["liability", "--question", "2024年某公司销售额30万元"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(set(["覆盖率", "分项", "补问", "待办", "边界"]) - set(payload), set())

    def test_unknown_type_is_a_one_not_a_traceback(self):
        code, out = self._run(["不存在的类型"])
        self.assertEqual(code, 1)
        self.assertIn("没有这个题型", json.loads(out)["error"])

    def test_unknown_skeleton_is_a_one_not_a_traceback(self):
        code, out = self._run(["liability", "--question", "2024年销售额",
                              "--skeleton", "不存在骨架"])
        self.assertEqual(code, 1)
        self.assertIn("没有这个骨架", json.loads(out)["error"])

    def test_evidence_file_accepts_the_aggregator_shape(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ev.json"
            p.write_text(json.dumps({"results": [{"title": "免征增值税公告"}]}),
                         encoding="utf-8")
            code, out = self._run(["liability", "--question", "2024年销售额30万",
                                  "--evidence", str(p)])
        self.assertEqual(code, 0)
        got = [i for i in json.loads(out)["分项"]
               if i["项"] == "减免与加计政策"][0]
        self.assertEqual(got["状态"], "已满足")


class TestNothingSilentlyGreen(unittest.TestCase):
    """判据本身要能被证伪：把上游改掉，用例必须报红，而不是跟着"通过"。"""

    def test_rate_moves_when_a_hit_word_list_empties(self):
        reg = copy.deepcopy(REG)
        reg["题型"]["liability"]["依据要件"][0]["命中词"] = ["绝不可能出现的字样"]
        ev = [{"title": "计税依据与税率：销售额与税率", "content": "同上"}]
        full = tax_coverage.assess("liability", "2024年某公司销售额", evidence=ev,
                                   reg=reg, skeleton="从价计征",
                                   params={"计税依据": 1, "税率": 2})
        baseline = tax_coverage.assess("liability", "2024年某公司销售额", evidence=ev,
                                       skeleton="从价计征",
                                       params={"计税依据": 1, "税率": 2})
        self.assertEqual(find(baseline, "计税依据")[0]["状态"], "已满足")
        self.assertEqual(find(full, "计税依据")[0]["状态"], "缺")
        self.assertLess(full["覆盖率"], baseline["覆盖率"])

    def test_empty_hit_word_table_does_not_read_as_all_satisfied(self):
        """命中词全空时若按 any([]) 判定，每一项都会"命中"，覆盖率虚高到 1.0。"""
        reg = copy.deepcopy(REG)
        for it in reg["题型"]["liability"]["依据要件"]:
            it["命中词"] = []
        out = tax_coverage.assess("liability", "2024年某公司销售额", evidence=[{"title": "x"}],
                                  reg=reg)
        self.assertEqual([i["状态"] for i in out["分项"] if i["类"] == "依据"],
                         ["缺", "缺", "缺"],
                         "空命中词被判成命中，覆盖率就成了自评级")
        self.assertLess(out["覆盖率"], 1.0)


class TestEvalScorerReadsTheTable(unittest.TestCase):
    """`eval_analysis.score_sufficient` 的必备项清单来自这张表，不来自它自己的一串 if。

    这一格以前按题型硬写"需本体法/需逐条比对"，与 ① 表格、② 四轴是三份副本；
    改表不通知它、改它不通知表。用例把表改掉一份，打分输出必须跟着变。
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import eval_analysis as EA
        self.EA = EA

    def _score(self, question, reg=None, authority="", parent="中华人民共和国增值税暂行条例"):
        terms = {"topic": "增值税", "authority": authority, "fgk": "", "parent_law": parent}
        with mock.patch.object(self.EA.AN, "build_plan",
                               lambda q: {"type": {"type": "liability"}}), \
             mock.patch.object(self.EA.AN, "search_terms", lambda q: terms), \
             mock.patch.object(tax_coverage, "load",
                               lambda *a, **k: reg or REG):
            return self.EA.score_sufficient({"question": question}, {})

    def test_required_items_are_the_registry_list(self):
        out = self._score("2024年某公司销售额30万元，缴多少增值税")
        self.assertEqual(out["必备依据"],
                         [i["项"] for i in spec(REG, "liability")["依据要件"]])
        self.assertTrue(out["ok"])

    def test_editing_the_table_moves_the_scorer(self):
        """删掉表里一项，打分输出的清单就要少一项；不少说明它还留着自己那份。"""
        reg = copy.deepcopy(REG)
        reg["题型"]["liability"]["依据要件"].pop(1)
        out = self._score("2024年某公司销售额30万元，缴多少增值税", reg=reg)
        self.assertEqual(len(out["必备依据"]), 2)

    def test_offline_run_reports_requirements_as_to_verify(self):
        """离线没检依据：这三项记待核，绝不记缺——记缺就成了"这类题不需要依据"。"""
        out = self._score("2024年某公司销售额30万元，缴多少增值税")
        self.assertEqual(len(out["待核"]), 4)          # 3 项依据 + 1 项展开算式
        self.assertTrue(all(not i.startswith("前提：") for i in out["待核"]))
        for i in out["缺前提"]:
            self.assertTrue(i.startswith("前提："), f"缺项里混进了非前提条目：{i}")

    def test_sta_topic_judges_by_its_own_reachability(self):
        """总局专题没有本体法是设计如此：这分支的判据换成交换到 fgk 检索词。"""
        hit = self._score("2024年某跨境安排缴多少税", authority="sta", parent="")
        self.assertFalse(hit["ok"])                    # fgk 检索词为空
        self.assertIn("总局专题检索词", hit["note"])

    def test_unrouted_question_still_exits_without_a_score(self):
        """归不出税种就没有可比清单：这一格不给分，也不去查表。"""
        terms = {"topic": "", "authority": "", "fgk": "", "parent_law": ""}
        with mock.patch.object(self.EA.AN, "build_plan",
                               lambda q: {"type": {"type": "liability"}}), \
             mock.patch.object(self.EA.AN, "search_terms", lambda q: terms):
            out = self.EA.score_sufficient({"question": "一句归不出税种的话"}, {})
        self.assertIsNone(out["ok"])
        self.assertNotIn("必备依据", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
