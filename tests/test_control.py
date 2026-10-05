#!/usr/bin/env python3
"""内控生成模块 `scripts/tax_control.py` 的离线用例。不联网、不调模型。

守的是七类失效路径，每类都带一条"打不红就是用例空了"的变异自检：

1. **注册表的结构与红线在载入时判**：九要素少一格、环节代号跳号、活动代号挂错环节、
   清单里塞空条目、保存期限写成"按相关规定"、处置缺口冒出第四类、按域挂却去处置
   缺材料、整改动作写出把日期往前写——都让 `load()` 直接报错，而不是等输出里冒出来。
2. **表与代码同源**：`_说明` 里那四组名字（九要素／活动字段／三类缺口／环节下限）
   与 `tax_control.ELEMENT_KEYS`、`tax_control.ACTIVITY_KEYS`、
   `tax_inspect.GAP_TYPES`、`tax_control.LINK_FLOOR` 逐项等值。定义处改了名而注释不动，
   读表的人就按一套错的字段名去补条目。
3. **门槛两头都判**：正向是"没有诊断就没有处方"（`tax_control.NeedsDiagnosis`，
   回话给的是取诊断的命令而不是通用制度）；反向是覆盖底线——稽查层每个要点的三类缺口
   都要有对着它的活动，少一格载入即报错。只判一头的话，要么凭空生制度，要么拿着缺口
   开不出方子还看不出来。
4. **诊断输入的三种形态过同一段判据**：`tax_inspect.build()` 的整个输出、`{"缺口": [...]}`、
   裸清单。类别认不出、要点代号不在稽查层表里、要点与域不是同一个、缺对象——都报错
   列出可用取值，不静默丢掉那一条（丢掉的那条正是没人管的缺口）。
5. **匹配判得动**：类别不符不命中；点名要点的活动不被别的要点牵动；按域挂的活动接住该域
   任意要点的口径缺口；缺口没写范围时只能按类别粗配，并且要在「缺范围」里露出来。
6. **覆盖诚实**：控制活动数 + 未生成数 == 注册表活动总数（不重不漏）；本表没有制度可处置
   的缺口与被筛选范围排除的缺口分两格写，后者还带着"应在哪条活动"；下限环节本次没牵动
   要说明"不代表这些环节没有洞"。
7. **文档与本表同源**：SKILL 快速索引行、⑥ 那一段、⑦ 第 25 条、六段式里的「制度与流程」
   条件块、归属表符号、README 的能力行与目录树行与测试行、注册表『谁在读』的每个路径、
   门禁离线组点名本文件。

用法：`python tests/test_control.py`
"""

import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import tax_control  # noqa: E402
import tax_inspect  # noqa: E402
import red_line_map  # noqa: E402  ⑦ 红线的正文归属登记表（tests/ 目录本身就在检索路径上）

SKILL = ROOT / "SKILL.md"
TPL = ROOT / "references" / "output_templates.md"
DEFECTS = ROOT / "references" / "source_defects.md"
README = ROOT / "README.md"
REGISTRY = ROOT / "data" / "control_activities.json"

GAPS = list(tax_inspect.GAP_TYPES)


def reg_copy():
    return copy.deepcopy(tax_control.load())


def act_map(reg):
    return {a["代号"]: a for _l, a in tax_control.activities(reg)}


def find(reg, code):
    return act_map(reg)[code]


def gap(cls, obj="一份材料", point="", dom=""):
    return {"类": cls, "对象": obj, "要点": point, "域": dom}


def outside_fences(md: str) -> str:
    """去掉 ``` 围栏内的内容——模板里的示例骨架也写 `## [问题]`，不算小节标题。"""
    out, fence = [], False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            out.append(line)
    return "\n".join(out)


def h2_titles(md: str) -> list:
    return [ln[3:].strip() for ln in outside_fences(md).splitlines() if ln.startswith("## ")]


class TestRegistryShape(unittest.TestCase):
    """结构、九要素、期限与两条红线：`load()` 必须逐格判得动。"""

    def assert_drift(self, mutator, needle):
        """把注册表改坏一处，断言 `validate()` 报的错里带着那句判据。"""
        reg = reg_copy()
        mutator(reg)
        with self.assertRaises(ValueError) as ctx:
            tax_control.validate(reg)
        self.assertIn(needle, str(ctx.exception))

    @staticmethod
    def drop_field(code, field):
        """删掉某一格——返回一个改坏注册表的函数。"""

        def _m(reg):
            del find(reg, code)[field]
        return _m

    @staticmethod
    def empty_list_item(code, field):
        def _m(reg):
            find(reg, code)[field] = ["有内容的一行", "   "]
        return _m

    def test_loads_clean(self):
        reg = tax_control.load()
        self.assertTrue(reg["控制环节"])
        self.assertGreaterEqual(len(tax_control.activities(reg)),
                                len(tax_control.LINK_FLOOR))

    def test_nine_elements_present_for_every_activity(self):
        reg = tax_control.load()
        for _l, act in tax_control.activities(reg):
            for k in tax_control.ELEMENT_KEYS:
                text = str(act.get(k) or "") if not isinstance(act.get(k), list) \
                    else " ".join(act[k])
                self.assertTrue(text.strip(), "%s 缺『%s』这一格" % (act["代号"], k))

    def test_every_link_has_activities(self):
        """空环节读起来像覆盖了，实际一条制度都生成不出来。"""
        self.assert_drift(
            lambda reg: reg["控制环节"][0]["控制活动"].clear(), "没有控制活动")

    def test_missing_link_list_is_caught(self):
        self.assert_drift(lambda reg: reg.__setitem__("控制环节", []),
                          "没有『控制环节』清单")

    def test_duplicate_link_name_is_caught(self):
        """环节名重复时 `--link <环节名>` 会一次选中两环，代号与名字必须一对一。

        取 H7 与 H11 这两环——都不在下限九名里，改名不会先把『环节下限缺』触发掉，
        报出来的才是重复这一处。
        """
        self.assert_drift(
            lambda reg: reg["控制环节"][10].__setitem__(
                "环节", reg["控制环节"][6]["环节"]),
            "环节名重复")

    def test_missing_element_is_caught(self):
        self.assert_drift(self.drop_field("H2-1", "责任岗位"), "缺『责任岗位』")

    def test_blank_scalar_element_is_caught(self):
        """格子在场但内容是空的——与删掉格子一样要报错，否则渲染出一行光标签。"""
        for field in ("目的", "责任岗位", "频率", "例外处理"):
            self.assert_drift(lambda reg, f=field: find(reg, "H2-1").__setitem__(f, "  "),
                              "是空的")

    def test_blank_list_item_is_caught(self):
        self.assert_drift(self.empty_list_item("H6-1", "复核点"), "第 2 项是空的")

    def test_retention_without_a_deadline_is_caught(self):
        """保存期限必须落到一个期限：写出年数，或长期／永久。"""
        self.assert_drift(
            lambda reg: find(reg, "H2-1").__setitem__("保存期限", "按相关规定保存"),
            "没写出期限")
        for ok in ("自相关纳税年度起 10 年", "永久"):
            reg = reg_copy()
            find(reg, "H2-1")["保存期限"] = ok
            tax_control.validate(reg)

    def test_link_codes_must_run_from_h1(self):
        self.assert_drift(lambda reg: reg["控制环节"][2].__setitem__("代号", "H9"),
                          "环节代号必须从 H1 连续排下来")

    def test_activity_code_must_sit_in_its_link(self):
        self.assert_drift(lambda reg: find(reg, "H1-1").__setitem__("代号", "H7-9"),
                          "不在所属环节")

    def test_duplicate_activity_code_is_caught(self):
        self.assert_drift(lambda reg: find(reg, "H1-2").__setitem__("代号", "H1-1"),
                          "重复")

    def test_link_floor_is_enforced(self):
        """环节下限九名逐个都在，少一环就是制度有洞。"""
        self.assert_drift(lambda reg: reg["控制环节"][0].__setitem__("环节", "甲乙丙"),
                          "环节下限缺")
        self.assertEqual(len(tax_control.LINK_FLOOR), 9)

    def test_fourth_gap_class_is_caught(self):
        self.assert_drift(
            lambda reg: find(reg, "H2-1")["处置缺口"].append("证据不足"),
            "第四类缺口")

    def test_unknown_domain_or_cross_domain_point_is_caught(self):
        self.assert_drift(lambda reg: find(reg, "H2-1").__setitem__("针对检查域", ["D8"]),
                          "不在稽查层注册表里")
        self.assert_drift(lambda reg: find(reg, "H2-1").__setitem__("针对要点", ["D6-1"]),
                          "不属于它列出的检查域")

    def test_domain_wide_activity_may_only_handle_caliber(self):
        """按域挂（要点留空）只接『政策口径不清』：另外两类必须点名要点。"""
        self.assertEqual(find(tax_control.load(), "H11-1")["针对要点"], [])
        self.assertEqual(find(tax_control.load(), "H11-1")["处置缺口"],
                         [tax_inspect.GAP_CALIBER])
        self.assert_drift(
            lambda reg: find(reg, "H11-1")["处置缺口"].append(tax_inspect.GAP_MISSING),
            "按域挂只允许用于")

    def test_red_line_remediation_word_is_caught(self):
        """制度文本不许写改动历史事实的动作——沿用稽查层那一份词表。"""
        self.assert_drift(
            lambda reg: find(reg, "H5-1")["操作步骤"].append(
                "付款前把合同日期倒签到付款日之前"),
            "越红线")
        self.assertIs(tax_control.tax_inspect.FORBIDDEN_REMEDIATION,
                      tax_inspect.FORBIDDEN_REMEDIATION,
                      "红线词表必须是稽查层那一份，不另立第二套")

    def test_prediction_word_is_caught(self):
        self.assert_drift(
            lambda reg: find(reg, "H8-1").__setitem__(
                "目的", "这样一查一定会被查上"), "结果预测")

    def test_registry_names_match_code(self):
        """`_说明` 那四组名字与代码常量逐项等值——注释漂走就等于字段名漂走。"""
        reg = tax_control.load()
        note = reg["_说明"]
        self.assertEqual(note["九要素"], list(tax_control.ELEMENT_KEYS))
        self.assertEqual(note["活动字段"], list(tax_control.ACTIVITY_KEYS))
        self.assertEqual(note["三类缺口"], GAPS)
        self.assertEqual(note["环节下限"], list(tax_control.LINK_FLOOR))
        for field in ("九要素", "活动字段", "三类缺口", "环节下限"):
            broken = reg_copy()
            broken["_说明"][field] = broken["_说明"][field][:-1]
            with self.assertRaises(ValueError, msg="%s 少一项却没报错" % field):
                tax_control.validate(broken)

    def test_activity_fields_and_elements_are_both_required(self):
        """管理字段与九要素合起来才是完整一格：少『处置缺口』也要报错。"""
        self.assert_drift(self.drop_field("H3-1", "处置缺口"), "缺『处置缺口』")
        self.assert_drift(self.drop_field("H3-1", "例外处理"), "缺『例外处理』")


class TestCoverageFloor(unittest.TestCase):
    """反方向的门槛：有诊断就得有处方，逐格判在载入时。"""

    def points(self):
        return [pt["代号"] for _d, pt in tax_inspect.points()]

    def test_every_point_and_class_has_an_activity(self):
        """22 个要点 × 3 类缺口 = 66 格，逐格都有活动对着。"""
        reg = tax_control.load()
        insp = tax_inspect.load()
        acts = [a for _l, a in tax_control.activities(reg)]
        cells = 0
        for pt in self.points():
            dom = pt.split("-")[0]
            for cls in GAPS:
                hit = [a["代号"] for a in acts
                       if cls in a["处置缺口"]
                       and (pt in (a["针对要点"] or [])
                            or (not a["针对要点"] and dom in a["针对检查域"]))]
                self.assertTrue(hit, "%s × %s 没有对着它的控制活动" % (pt, cls))
                cells += 1
        self.assertEqual(cells, len(self.points()) * 3)
        self.assertEqual(cells, 66, "稽查层要点数变了——覆盖面要跟着重数")

    def test_hole_in_the_floor_is_caught(self):
        """H2-2 是 D1-2、D1-3 的『缺材料』唯一出处：撤掉它，载入必须点这两格。"""
        self.assertEqual(find(tax_control.load(), "H2-2")["处置缺口"],
                         [tax_inspect.GAP_CONFLICT, tax_inspect.GAP_MISSING])
        self.assert_drift_widely(
            lambda reg: find(reg, "H2-2").__setitem__("处置缺口", [tax_inspect.GAP_CONFLICT]),
            ("覆盖底线有", "D1-2×" + tax_inspect.GAP_MISSING,
             "D1-3×" + tax_inspect.GAP_MISSING))

    def assert_drift_widely(self, mutator, needles):
        reg = reg_copy()
        mutator(reg)
        with self.assertRaises(ValueError) as ctx:
            tax_control.validate(reg)
        for needle in needles:
            self.assertIn(needle, str(ctx.exception))

    def test_floor_check_reads_both_kinds_of_scope(self):
        """按域挂的那条一撤到只剩 D1，其余各域没有点名要点的口径活动就都空了。"""
        thin = reg_copy()
        find(thin, "H11-1")["针对检查域"] = ["D1"]
        with self.assertRaises(ValueError) as ctx:
            tax_control.validate(thin)
        msg = str(ctx.exception)
        self.assertIn("覆盖底线有", msg)
        for pt in ("D2-1", "D3-1", "D4-1"):
            self.assertIn(pt, msg, "撤掉域级挂点后该要点的口径缺口没被点名：%s" % pt)
        # 自己带口径活动的要点不受影响，说明报错是逐格的，不是一撤就全红
        for pt in ("D6-3", "D5-2", "D7-4"):
            self.assertNotIn(pt + "×" + tax_inspect.GAP_CALIBER, msg)


class TestDependencyGate(unittest.TestCase):
    """没有诊断就没有处方；只要通用模板时必须把假设写明。"""

    def test_no_diagnosis_no_assumption_refuses(self):
        with self.assertRaises(tax_control.NeedsDiagnosis) as ctx:
            tax_control.build()
        text = str(ctx.exception)
        self.assertIn("拒绝凭空生成", text)
        self.assertIn("tax_inspect.py", text, "回话要给出取诊断的那一步")
        self.assertIn("--assume", text)

    def test_empty_diagnosis_file_says_widen_instead(self):
        """给了诊断文件、里面 0 条缺口，与压根没给文件是两件事。"""
        with self.assertRaises(tax_control.NeedsDiagnosis) as ctx:
            tax_control.build({"问询": []})
        self.assertIn("放宽", str(ctx.exception))
        self.assertNotIn("先跑稽查模拟拿诊断", str(ctx.exception))

    def test_assumption_mode_is_labelled_generic(self):
        out = tax_control.build(assumptions=["单一一般纳税人，查账征收，2025 年度"])
        self.assertEqual(out["形态"], tax_control.MODE_GENERAL)
        self.assertEqual(out["假设"], ["单一一般纳税人，查账征收，2025 年度"])
        self.assertEqual(out["诊断"]["缺口数"], 0)
        self.assertTrue(out["控制活动"])

    def test_diagnosis_mode_is_labelled_targeted(self):
        out = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING,
                                              point="D1-1")]})
        self.assertEqual(out["形态"], tax_control.MODE_DIAGNOSED)
        self.assertTrue(all(i["触发缺口"] for i in out["控制活动"]),
                        "针对性模式下每条生成出来的活动都要有牵动它的缺口")

    def test_blank_assumption_is_caught(self):
        with self.assertRaises(ValueError):
            tax_control.build(assumptions=["   "])

    def test_refusal_is_not_a_silent_empty_output(self):
        """拒生成的判据确实能被诊断输入翻掉：同一范围给了缺口就有产出。"""
        with self.assertRaises(tax_control.NeedsDiagnosis):
            tax_control.build(links=["H2"])
        out = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING, point="D1-1")]},
                                links=["H2"])
        self.assertTrue(out["控制活动"])


class TestIntake(unittest.TestCase):
    """三种输入形态过同一段判据，认不出就报错列取值。"""

    def diagnosis(self):
        return tax_inspect.build(domains=["D1"])

    def test_full_inspect_output_is_taken_as_is(self):
        d = self.diagnosis()
        rows = tax_control.intake(d)
        self.assertEqual(len(rows),
                         sum(len(u.get("证据缺口") or []) for u in d["问询"]))
        for r in rows:
            self.assertIn(r["类"], GAPS)
            self.assertTrue(r["对象"] and r["要点"] and r["域"])
        self.assertEqual({r["域"] for r in rows}, {"D1"})

    def test_three_shapes_agree(self):
        rows = [{"类": tax_inspect.GAP_MISSING, "对象": "工时表", "要点": "D1-1"}]
        a = tax_control.intake(self.diagnosis())
        b = tax_control.intake({"缺口": rows})
        c = tax_control.intake(rows)
        self.assertEqual(b, c)
        self.assertEqual(b[0]["域"], "D1", "只给要点时域由注册表推出")
        self.assertTrue(a)

    def test_unknown_class_lists_the_three(self):
        with self.assertRaises(ValueError) as ctx:
            tax_control.intake({"缺口": [{"类": "口径不明", "对象": "合同"}]})
        for g in GAPS:
            self.assertIn(g, str(ctx.exception))

    def test_unknown_point_or_domain_is_caught(self):
        """认不出的代号报错列取值：要点列不出去、只说去哪查；域直接把七个代号摆出来。"""
        with self.assertRaises(ValueError) as ctx:
            tax_control.intake([{"类": GAPS[0], "对象": "台账", "要点": "D9-9"}])
        self.assertIn("检查要点", str(ctx.exception))
        self.assertIn("tax_inspect.py --list", str(ctx.exception),
                      "认不出的要点代号要给出查取值的那条命令")
        with self.assertRaises(ValueError) as ctx:
            tax_control.intake([{"类": GAPS[0], "对象": "台账", "域": "D9"}])
        msg = str(ctx.exception)
        self.assertIn("检查域", msg)
        for d in ("D1", "D7"):
            self.assertIn(d, msg, "报错没列出可用的域代号")

    def test_point_and_domain_must_agree(self):
        with self.assertRaises(ValueError) as ctx:
            tax_control.intake([{"类": GAPS[0], "对象": "台账",
                                 "要点": "D1-1", "域": "D2"}])
        text = str(ctx.exception)
        self.assertIn("范围串了", text)
        self.assertIn("D1-1 属于 D1", text, "回话要说清点的是哪个要点、真正属于哪个域")

    def test_missing_object_or_shape_is_caught(self):
        with self.assertRaises(ValueError):
            tax_control.intake([{"类": GAPS[0], "对象": "  "}])
        with self.assertRaises(ValueError):
            tax_control.intake(["工时表"])
        with self.assertRaises(ValueError):
            tax_control.intake({"缺孔": []})
        with self.assertRaises(ValueError):
            tax_control.intake("工时表")

    def test_no_gap_key_is_not_mistaken_for_empty(self):
        """空清单与形态不对是两件事：`[]` 是"没缺口"，`{"甲": []}` 是"读不出"。"""
        self.assertEqual(tax_control.intake([]), [])
        self.assertEqual(tax_control.intake({"问询": []}), [])
        self.assertEqual(tax_control.intake(None), [])
        self.assertEqual(tax_control.intake({}), [])


class TestMatching(unittest.TestCase):
    def test_class_and_range_both_gate(self):
        act = find(tax_control.load(), "H2-1")
        self.assertTrue(tax_control.matches(act, gap(tax_inspect.GAP_MISSING,
                                                     dom="D1", point="D1-1")))
        self.assertFalse(tax_control.matches(act, gap(tax_inspect.GAP_CONFLICT,
                                                      dom="D1", point="D1-2")))
        self.assertFalse(tax_control.matches(act, gap(tax_inspect.GAP_MISSING,
                                                      dom="D2", point="D2-1")))

    def test_named_point_does_not_pick_up_siblings(self):
        reg = tax_control.load()
        act = find(reg, "H2-1")
        self.assertNotIn("D1-2", act["针对要点"])
        self.assertFalse(tax_control.matches(act, gap(tax_inspect.GAP_MISSING,
                                                      dom="D1", point="D1-2")))

    def test_domain_wide_activity_picks_any_point_in_its_domains(self):
        act = find(tax_control.load(), "H11-1")
        self.assertTrue(tax_control.matches(
            act, gap(tax_inspect.GAP_CALIBER, dom="D4", point="D4-2")))
        self.assertFalse(tax_control.matches(
            act, gap(tax_inspect.GAP_MISSING, dom="D4", point="D4-2")))

    def test_domain_only_gap_falls_back_to_domain(self):
        act = find(tax_control.load(), "H2-1")
        self.assertTrue(tax_control.matches(act, gap(tax_inspect.GAP_MISSING, dom="D1")))
        self.assertFalse(tax_control.matches(act, gap(tax_inspect.GAP_MISSING)))

    def test_scope_of_two_kinds(self):
        reg = tax_control.load()
        insp = tax_inspect.load()
        by_domain = {d["代号"]: [p["代号"] for p in d["检查要点"]] for d in insp["检查域"]}
        self.assertEqual(tax_control.scope_of(find(reg, "H2-1"), by_domain), {"D1-1"})
        wide = tax_control.scope_of(find(reg, "H11-1"), by_domain)
        self.assertIn("D7-1", wide)
        self.assertEqual(len(wide), len(tax_inspect.points(insp)))


class TestBuildShape(unittest.TestCase):
    """输出不重不漏、两类空落点分开写。"""

    def test_generated_plus_unasked_equals_total(self):
        reg = tax_control.load()
        total = len(tax_control.activities(reg))
        for kwargs in ({}, {"links": ["H2"]}, {"codes": ["H6-1"]}):
            out = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING, point="D1-1"),
                                              gap(tax_inspect.GAP_CALIBER, point="D6-3")]},
                                    **kwargs)
            codes = [i["代号"] for i in out["控制活动"]]
            unasked = [i["代号"] for i in out["未生成"]["清单"]]
            self.assertEqual(len(set(codes)), len(codes), "同一条活动生成了两遍")
            self.assertEqual(len(codes) + len(unasked), total,
                             "生成 + 未生成 != 注册表总数：%s" % kwargs)
            self.assertFalse(set(codes) & set(unasked))
            self.assertEqual(out["本次范围"]["活动数"], len(codes))
            self.assertEqual(out["本次范围"]["注册表活动总数"], total)

    def test_two_kinds_of_emptiness_stay_apart(self):
        """本表没制度可处置 vs 有制度但被范围排除，两格分开且后者带上应落在哪条。"""
        diag = {"缺口": [gap(tax_inspect.GAP_MISSING, "工时表", "D1-1"),
                         gap(tax_inspect.GAP_MISSING, "另一份材料", "D1-1")]}
        wide = tax_control.build(diag)
        self.assertEqual(wide["未被处置的缺口"], [])
        self.assertEqual(wide["本次范围外的缺口"], [])
        narrow = tax_control.build(diag, links=["H1"])
        self.assertEqual(narrow["未被处置的缺口"], [])
        out = narrow["本次范围外的缺口"]
        self.assertEqual(len(out), 2, "范围收窄后两条缺口都该落进『本次范围外』")
        self.assertTrue(all(g["应在"] for g in out))
        self.assertIn("H2-1", out[0]["应在"])

    def test_gap_without_range_goes_to_two_places(self):
        """缺口既没要点也没域：只能按类别粗配不到，且要在『缺范围』里露出来。"""
        out = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING, "一张说不清的表")]})
        self.assertEqual(len(out["诊断"]["缺范围"]), 1)
        self.assertEqual(len(out["未被处置的缺口"]), 1)
        self.assertEqual(out["未被处置的缺口"][0]["要点"], "")

    def test_floor_links_reported_as_not_touched(self):
        """牵动到的环节进覆盖清单，其余下限环节逐个列进『未牵动』。"""
        out = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING, point="D1-1")]})
        self.assertEqual(out["本次范围"]["覆盖环节"], ["H2"])
        self.assertNotIn(tax_control.LINK_FLOOR[1], out["下限环节未牵动"])
        self.assertIn(tax_control.LINK_FLOOR[8], out["下限环节未牵动"])
        self.assertEqual(len(out["下限环节未牵动"]), len(tax_control.LINK_FLOOR) - 1)

    def test_unknown_filter_value_lists_options(self):
        for kwargs, needle in (({"links": ["H99"]}, "环节代号或环节名"),
                               ({"codes": ["H1-9"]}, "活动代号"),
                               ({"links": ["不存在的环节名"]}, "环节代号或环节名")):
            with self.assertRaises(ValueError) as ctx:
                tax_control.build({"缺口": [gap(GAPS[0], point="D1-1")]}, **kwargs)
            self.assertIn(needle, str(ctx.exception))
            self.assertIn("H1", str(ctx.exception), "报错要列出可用取值")

    def test_by_name_and_by_code_pick_same_activity(self):
        diag = {"缺口": [gap(tax_inspect.GAP_MISSING, point="D1-1"),
                         gap(tax_inspect.GAP_CONFLICT, point="D1-2")]}
        by_code = tax_control.build(diag, links=["H2"])
        by_name = tax_control.build(diag, links=[tax_control.LINK_FLOOR[1]])
        self.assertEqual([i["代号"] for i in by_code["控制活动"]],
                         [i["代号"] for i in by_name["控制活动"]])

    def test_activity_code_filter_narrows_further(self):
        diag = {"缺口": [gap(tax_inspect.GAP_MISSING, point="D1-1"),
                         gap(tax_inspect.GAP_CONFLICT, point="D1-2")]}
        out = tax_control.build(diag, codes=["H2-1"])
        self.assertEqual([i["代号"] for i in out["控制活动"]], ["H2-1"])
        self.assertEqual(out["筛选"]["活动"], ["H2-1"])

    def test_boundary_sentence_travels(self):
        out = tax_control.build(assumptions=["一般纳税人"])
        for needle in ("不是整改清单", "管理下限", "⑧"):
            self.assertIn(needle, out["边界"])


class TestEveryDomainReachable(unittest.TestCase):
    """非研发的那几域也要摸得到制度——覆盖面不局限高企与加计。"""

    def test_one_gap_of_each_class_per_domain_builds(self):
        insp = tax_inspect.load()
        reg = tax_control.load()
        for d in insp["检查域"]:
            pt = d["检查要点"][0]["代号"]
            diag = {"缺口": [{"类": g, "对象": "一份材料", "要点": pt} for g in GAPS]}
            out = tax_control.build(diag, reg=reg)
            self.assertTrue(out["控制活动"], "%s 的第一个要点 %s 开不出制度" % (d["代号"], pt))
            self.assertEqual(out["未被处置的缺口"], [],
                             "%s／%s 有缺口拿不到处方" % (d["代号"], pt))

    def test_non_rd_domains_are_in_the_table(self):
        """表里既有点名研发的，也有只按跨税种场景铺的环节。"""
        reg = tax_control.load()
        acts = [a for _l, a in tax_control.activities(reg)]
        doms = {d for a in acts for d in a["针对检查域"]}
        self.assertEqual(doms, {d["代号"] for d in tax_inspect.load()["检查域"]},
                         "有检查域一条制度都没挂")
        names = {l["环节"] for l in reg["控制环节"]}
        self.assertIn("发票凭证与合同管理", names)
        self.assertIn("政策变更与口径跟踪", names)


class TestRender(unittest.TestCase):
    def rendered(self, **kw):
        return tax_control.render(tax_control.build(**kw))

    def test_all_nine_element_labels_appear(self):
        text = self.rendered(assumptions=["一般纳税人"], links=["H6"])
        for k in tax_control.ELEMENT_KEYS:
            self.assertIn("%s：" % k, text, "渲染漏了九要素里的『%s』" % k)
        first = text.index("%s：" % tax_control.ELEMENT_KEYS[0])
        last = text.index("%s：" % tax_control.ELEMENT_KEYS[-1])
        self.assertLess(first, last, "九要素顺序与 ELEMENT_KEYS 不一致")

    def test_mode_and_assumptions_are_printed(self):
        text = self.rendered(assumptions=["查账征收", "2025 年度"])
        self.assertIn(tax_control.MODE_GENERAL, text)
        self.assertIn("· 查账征收", text)
        self.assertIn("通用模板不是本企业的制度", text)

    def test_honesty_lines(self):
        out = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING, point="D1-1")]},
                                links=["H1"])
        text = tax_control.render(out)
        self.assertIn("本次筛选范围外被排除的缺口", text)
        self.assertIn("不代表这些环节没有洞", text)
        self.assertIn("未生成", text)
        out2 = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING, "一张说不清的表")]})
        self.assertIn("缺口没写明检查要点或域", tax_control.render(out2))

    def test_render_carries_no_banned_word(self):
        """渲染出来的整段制度文本不许出现两条红线上的词（含注册表全部九格）。"""
        text = self.rendered(assumptions=["一般纳税人"])
        for words, why in ((tax_inspect.FORBIDDEN_REMEDIATION, "改动历史事实"),
                           (tax_inspect.PREDICTION_PHRASES, "预测结果")):
            hit = tax_inspect._bad_words(text, words)
            self.assertEqual(hit, [], "%s的措辞进了输出：%s" % (why, hit))

    def test_trigger_gap_lines_show_class_object_and_point(self):
        text = self.rendered(diagnosis={"缺口": [gap(tax_inspect.GAP_MISSING,
                                                    "工时记录表", "D1-1")]})
        self.assertIn("由这些缺口牵动", text)
        self.assertIn("[%s] 工时记录表（D1-1）" % tax_inspect.GAP_MISSING, text)

    def test_out_of_range_line_shows_the_activity_it_belongs_to(self):
        out = tax_control.build({"缺口": [gap(tax_inspect.GAP_MISSING, "工时表", "D1-1")]},
                                links=["H1"])
        text = tax_control.render(out)
        self.assertIn("→ 本表里对应 H2-1", text)


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "tax_control.py"),
                               *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(ROOT))

    def test_list(self):
        r = self.run_cli("--list")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("九要素", r.stdout)
        for code in ("H1", "H11-1"):
            self.assertIn(code, r.stdout)
        self.assertIn("环节共 11 个，活动共 22 条", r.stdout,
                      "--list 的计数与注册表不一致")

    def test_refusal_exits_2_with_the_route(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 2)
        self.assertIn("拒绝凭空生成", r.stdout)

    def test_diagnosis_file_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "gaps.json"
            p.write_text(json.dumps(tax_inspect.build(domains=["D6"]), ensure_ascii=False),
                         encoding="utf-8")
            r = self.run_cli("--diagnosis", str(p), "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            out = json.loads(r.stdout)
            self.assertEqual(out["形态"], tax_control.MODE_DIAGNOSED)
            self.assertEqual(out["未被处置的缺口"], [])
            self.assertTrue(any("D6" in i["针对检查域"] for i in out["控制活动"]))

    def test_bad_filter_exits_2(self):
        r = self.run_cli("--assume", "一般纳税人", "--link", "H99")
        self.assertEqual(r.returncode, 2)
        self.assertIn("环节代号或环节名", r.stdout)

    def test_assume_text_shows_generic_form(self):
        r = self.run_cli("--assume", "小规模纳税人", "--activity", "H7-2")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("通用模板", r.stdout)
        self.assertIn("H7-2", r.stdout)


class TestDocHooks(unittest.TestCase):
    """文档挂线：这一层的入口、门槛与落点在四份文档里都要读得到。"""

    def skill(self):
        return SKILL.read_text(encoding="utf-8")

    def test_quick_index_row(self):
        for line in self.skill().splitlines():
            if "tax_control.py" in line and line.startswith("|"):
                break
        else:
            self.fail("SKILL 快速索引里没有 `scripts/tax_control.py` 那一行")
        self.assertIn("data/control_activities.json", line)
        self.assertIn("⑥ 前", line)

    def test_section_6_names_the_module_and_no_new_form(self):
        skill = self.skill()
        body = skill.split("\n## ⑥ ")[1].split("\n## ")[0]
        for needle in ("tax_control.py", "--diagnosis", "NeedsDiagnosis",
                       "tax_control.validate", "不新增输出形态", "通用模板",
                       "保存期限", "覆盖底线"):
            self.assertIn(needle, body, "⑥ 那一段少了：%s" % needle)
        self.assertIn("九种输出形态", body)

    def test_red_line_25_is_the_control_one(self):
        # 第 25 条是跨层红线，全文留在 ⑦。编号连不连续、指针与锚对不对，由
        # `test_doc_contract` 的登记表用例逐条判；本层只认自己那几句还在。
        self.assertEqual(red_line_map.holder(25), ("SKILL.md", "⑦ 禁止行为清单"))
        item = red_line_map.body(25)
        for needle in ("没有记录", "没有发生", "无法确认"):
            self.assertIn(needle, item, "第 25 条少了『%s』这一句" % needle)

    def test_template_conditional_block(self):
        md = TPL.read_text(encoding="utf-8")
        span = md.split("## 分析六段式")[1].split("## 逐条比对式")[0]
        self.assertIn("**制度与流程**", span, "六段式里没有「制度与流程」条件块")
        for k in tax_control.ELEMENT_KEYS:
            self.assertIn(k, span, "条件块里没写九要素的『%s』" % k)
        self.assertIn("tax_control.py --diagnosis", span)
        self.assertIn("未被处置的缺口", span)
        self.assertIn("tax_control.NeedsDiagnosis", md)
        self.assertIn("tax_control.ELEMENT_KEYS", md)
        self.assertNotIn("## 制度与流程", md, "条件块不该升成一个输出形态")

    def test_output_forms_stay_nine(self):
        """⑥ 说的形态数与模板的二级标题数同源：模板多出一个小节就是第十种形态。"""
        titles = h2_titles(TPL.read_text(encoding="utf-8"))
        forms = [t for t in titles if t != "目录"]
        self.assertEqual(len(forms), 9, "模板小节数变了：%s" % forms)
        skill_body = self.skill().split("\n## ⑥ ")[1].split("\n## ")[0]
        self.assertIn("九种输出形态", skill_body)

    def test_insufficient_basis_discloses_layers(self):
        """「依据不足时」那两格字段化：已查层与结果、未查层与原因。"""
        md = TPL.read_text(encoding="utf-8")
        span = md.split("## 依据不足时")[1].split("## 风险自检专用输出")[0]
        self.assertIn("**已查层与结果**", span)
        self.assertIn("**未查层与原因**", span)
        for lab in ("L1", "L2", "L3", "L4"):
            self.assertIn(lab, span)
        self.assertIn("⑦ 第 24 条", span)

    def test_attribution_row_and_boundary_bullet(self):
        md = DEFECTS.read_text(encoding="utf-8")
        rows = [ln for ln in md.splitlines() if "tax_control.build" in ln]
        self.assertEqual(len(rows), 1, "归属表里这一层的行应当唯一，读到 %d 行" % len(rows))
        row = rows[0]
        for sym in ("intake", "matches", "pick", "validate", "NeedsDiagnosis",
                    "FORBIDDEN_REMEDIATION", "PREDICTION_PHRASES"):
            self.assertIn(sym, row, "归属行少了 %s" % sym)
        self.assertIn("data/control_activities.json", row)
        self.assertIn("tests/test_control.py", row)
        self.assertIn("tax_control.RETENTION_RE", md, "边界那一节没写保存期限的判据")
        self.assertIn("内控生成的覆盖面", md, "边界那一节没写这一层的覆盖面")

    def test_registry_readers_exist(self):
        reg = tax_control.load()
        for rel in reg["_说明"]["谁在读"]:
            self.assertTrue((ROOT / rel).exists(), "注册表说 %s 在读，这个路径不存在" % rel)

    def test_readme_rows(self):
        md = README.read_text(encoding="utf-8")
        self.assertIn("tax_control.py", md)
        self.assertIn("control_activities.json", md)
        self.assertIn("test_control.py", md)
        row = [ln for ln in md.splitlines() if "test_control.py" in ln]
        self.assertTrue(any("九要素" in ln for ln in row),
                        "README 的测试行没写这一层守的是什么")
        self.assertTrue(any(re.search(r"\d+ 条", ln) for ln in row),
                        "README 的测试行没写实测的用据条数")

    def test_gate_actually_runs_this_file(self):
        """门禁里点名本文件——写了用例却没进门禁，等于没写。

        离线组第一项是在本进程里跑的编译函数，不是 argv，所以先过掉 callable。
        """
        sys.path.insert(0, str(ROOT / "tests"))
        import run_all
        listed = " ".join(" ".join(map(str, cmd))
                          for _name, cmd in run_all.OFFLINE_GROUP
                          if not callable(cmd))
        self.assertIn("test_control.py", listed,
                      "离线门禁没点名 tests/test_control.py，这份用例平时不跑")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    unittest.main(verbosity=2)
