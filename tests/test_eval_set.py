#!/usr/bin/env python3
"""
评测集构建脚本的离线用例：不打网络，只验规则本身。

时效规则里有几条在这批题库上一次都没命中（营业税、合并前征管主体、
研发费用加计扣除旧比例）。没命中不等于写对了——用合成题面把每条规则
各打一遍，才能确认规则改坏了会发现，而不是从此静默失效。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from build_eval_set import DATASET_FILES, grade, make_key, parse_answer
from eval_retrieval import STA_CORE_TERM, SURFACE_FORMS, extract_hints


def rec(question, *options):
    opts = {k: v for k, v in zip("ABCD", options)}
    for k in "ABCD":
        opts.setdefault(k, "占位选项")
    return {"question": question, "options": opts}


def test_validity_rules_fire():
    """每条时效规则都要能被一个合成题面打到，且档位正确。"""
    cases = [
        (rec("2015年甲公司销售房产应缴纳的营业税是多少？", "3万元"),
         "stale", ["营业税", "过期年度"]),
        (rec("2018年某企业进口货物，增值税税率是多少？", "17%"),
         "stale", ["增值税旧税率", "过期年度"]),
        (rec("2021年小规模纳税人适用征收率是多少？", "3%"),
         "review", ["次近年份"]),
        (rec("纳税人应向所在地地方税务局申报缴纳房产税。", "正确"),
         "review", ["合并前征管主体"]),
        (rec("企业研发费用未形成无形资产的，按实际发生额的50%加计扣除。", "正确"),
         "review", ["研发费用加计扣除旧比例"]),
        (rec("增值税一般纳税人的基本税率是多少？", "13%"),
         "ok", []),
    ]
    for r, want_validity, want_flags in cases:
        validity, flags = grade(r)
        assert validity == want_validity, f"{r['question'][:20]} 判成 {validity}，应为 {want_validity}（{flags}）"
        assert flags == want_flags, f"{r['question'][:20]} 命中 {flags}，应为 {want_flags}"


def test_abolished_tax_not_flagged_when_reform_context():
    """问"营业税改征增值税"的文件层级是现行有效的题，不能因出现营业税就判过期。"""
    r = rec("《营业税改征增值税试点实施办法》的法律级别是什么？", "部门规章")
    assert grade(r) == ("ok", []), grade(r)


def test_year_outside_question_not_scanned():
    """年份只扫题面：选项里的年份多是干扰项，扫进去会把现行题误判成过期。"""
    r = rec("增值税一般纳税人的基本税率是多少？", "2016年为17%", "13%", "9%", "6%")
    validity, _ = grade(r)
    assert validity == "stale", validity      # 选项里的 17% 仍要抓到
    r2 = rec("城市维护建设税的纳税人是谁？", "2015年设立的企业", "行政机关",
             "事业单位", "自然人")
    assert grade(r2) == ("ok", []), grade(r2)  # 选项里的年份不算


def test_dedup_key_includes_options():
    """题干相同、选项不同的两道题必须算两道题。"""
    a = rec("下列关于增值税专用发票的说法正确的是（ ）。", "甲", "乙", "丙", "丁")
    b = rec("下列关于增值税专用发票的说法正确的是（ ）。", "甲", "戊", "丙", "丁")
    assert make_key(a["question"], a["options"]) != make_key(b["question"], b["options"])
    c = rec("下列关于增值税专用发票的说法正确的是（）。", "甲", "乙", "丙", "丁")
    assert make_key(a["question"], a["options"]) == make_key(c["question"], c["options"])


def test_answer_parsing():
    assert parse_answer("A,C,D") == ["A", "C", "D"]
    assert parse_answer("acd") == ["A", "C", "D"]
    assert parse_answer("B") == ["B"]
    assert parse_answer("无法确定") == []


def test_hint_specificity_order():
    """具体税种不能被笼统税种吃掉：土地增值税不能同时提增值税。"""
    hints = extract_hints("土地增值税的扣除项目包括哪些？")
    assert hints == ["土地增值税"], hints
    assert extract_hints("增值税进项税额如何抵扣？") == ["增值税"]
    assert extract_hints("城市维护建设税的纳税人是谁？") == ["城市维护建设税"]
    assert "车辆购置税" in extract_hints("车辆购置税的计税价格如何确定？")
    assert extract_hints("下列各项中，属于税收征管法适用范围的是？") == ["税收征管"]


def test_surface_forms_cover_the_whole_taxonomy():
    """表面词表必须覆盖 TAX_TYPE_KEYWORDS 的每个键。

    漏键不会报错，只会把缺口藏起来——covered 反而更好看。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import tax_search as T
    missing = set(T.TAX_TYPE_KEYWORDS) - {k for k, _ in SURFACE_FORMS}
    assert not missing, f"SURFACE_FORMS 漏了这些键：{sorted(missing)}"


def test_every_sta_key_has_core_terms():
    """sta 类专题在总局侧判命中要靠核心词，缺了就会拿键名去比标题而永远不命中。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import tax_search as T
    missing = [k for k, _ in SURFACE_FORMS
               if (T.TAX_TYPE_KEYWORDS.get(k) or {}).get("authority") == "sta"
               and k not in STA_CORE_TERM]
    assert not missing, f"STA_CORE_TERM 缺：{missing}"


def test_surface_forms_keys_exist_in_taxonomy():
    """SURFACE_FORMS 的每个键都要能在税种表里查到，否则探针会拿空配置去打网络。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import tax_search as T
    missing = [k for k, _ in SURFACE_FORMS if k not in T.TAX_TYPE_KEYWORDS]
    assert not missing, f"SURFACE_FORMS 有键不在 TAX_TYPE_KEYWORDS：{missing}"


def test_dataset_manifest_files_are_distinct():
    names = list(DATASET_FILES)
    assert len(names) == len(set(names))
    subsets = [s for _, s, _ in DATASET_FILES.values()]
    assert len(subsets) == len(set(subsets)), "子集名重复会让去重统计串台"
    for _, _, kind in DATASET_FILES.values():
        assert kind in ("csv_ideafin", "csv_financeiq", "csv_fineval"), kind


def test_surface_forms_keys_exist_in_taxonomy():
    """SURFACE_FORMS 的每个键都要能在税种表里查到，否则探针会拿空配置去打网络。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import tax_search as T
    missing = [k for k, _ in SURFACE_FORMS if k not in T.TAX_TYPE_KEYWORDS]
    assert not missing, f"SURFACE_FORMS 有键不在 TAX_TYPE_KEYWORDS：{missing}"


TESTS = [
    ("Validity Rules Fire", test_validity_rules_fire),
    ("Reform Context Not Stale", test_abolished_tax_not_flagged_when_reform_context),
    ("Year Scan Scope", test_year_outside_question_not_scanned),
    ("Dedup Key Includes Options", test_dedup_key_includes_options),
    ("Answer Parsing", test_answer_parsing),
    ("Hint Specificity Order", test_hint_specificity_order),
    ("Surface Forms Cover Taxonomy", test_surface_forms_cover_the_whole_taxonomy),
    ("Sta Core Terms Complete", test_every_sta_key_has_core_terms),
    ("Dataset Manifest", test_dataset_manifest_files_are_distinct),
    ("Surface Forms Taxonomy", test_surface_forms_keys_exist_in_taxonomy),
]


def main():
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
