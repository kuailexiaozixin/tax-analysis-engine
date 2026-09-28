#!/usr/bin/env python3
"""
把多个公开财税题库里的税务题目归并成一套带出处、带时效标记的评测集。

产出 `tax_eval_set.jsonl`，每行一题：

    {"key","source","subset","question","options","answer","answer_type",
     "validity","flags"}

为什么要"时效标记"：这些题库取自注册会计师与税务师考试，考试按年度命题。
2016 年的真题在 2026 年看仍然"题干完整、答案唯一"，但它考的是当年的税率与
优惠口径，逐条拿它判分等于拿过期标准判现在的系统。所以不剔除，只打标记，
让评测脚本能分开报"全部"与"仅现行有效"两组数。

三档判定（规则见 VALIDITY_RULES，全部可从题面复算，不依赖人工记忆）：
    ok      题面没有指向已废止制度或过期年度的线索
    review  出现 2020-2022 年度，或出现国地税合并前的征管主体表述
    stale   出现 2019 年及更早年度、营业税、增值税旧税率档

时效之外还有一层"范围"判定（SCOPE_EXCLUDE）：题面问的是税制史 rather than
现行规定的题直接不收。这类题没有可检索、可定级的对象，留着只会把题库的
命题口径算成技能的能力分。

用法：
    python tests/build_eval_set.py --data-dir ../eval_data
    python tests/build_eval_set.py --data-dir ../eval_data --report
    python tests/build_eval_set.py --data-dir ../eval_data --out ../eval_data/tax_eval_set.jsonl
"""
import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 本地文件名 → (题库, 子集, 读取器)。文件名是固定的，题库改版要同步改这里。
# 上游路径写在 SOURCES 里，下载方式见 SKILL.md 的评测章节。
DATASET_FILES = {
    "ideafin_cpa_one_tax_law_val.csv": ("ideafin", "cpa_one", "csv_ideafin"),
    "ideafin_cpa_multi_tax_law_val.csv": ("ideafin", "cpa_multi", "csv_ideafin"),
    "financeiq_税务师_test.csv": ("financeiq", "shuiwushi", "csv_financeiq"),
    "fineval_tax_law_val.csv": ("fineval", "tax_law_val", "csv_fineval"),
    "fineval_tax_law_dev.csv": ("fineval", "tax_law_dev", "csv_fineval"),
}

# 兼容已下载文件的旧命名，构建时按新名归一
ALIASES = {
    "cpa_one_tax_law_val.csv": "ideafin_cpa_one_tax_law_val.csv",
    "cpa_multi_tax_law_val.csv": "ideafin_cpa_multi_tax_law_val.csv",
    "FinanceIQ__data_test_税务师.csv": "financeiq_税务师_test.csv",
    "FinEval__tax_law_val.csv": "fineval_tax_law_val.csv",
    "FinEval__tax_law_dev.csv": "fineval_tax_law_dev.csv",
}

SOURCES = {
    "ideafin": "DataArcTech/IDEAFinBench：datasets/cpa_one|cpa_multi/val/tax_law_val.csv",
    "financeiq": "Duxiaoman-DI/FinanceIQ：data/test/税务师.csv（CC BY-NC-SA-4.0）",
    "fineval": "SUFE-AIFLM-Lab/FinEval：tax_law 的 val 与 dev（CC BY-NC-SA-4.0）",
}

# 时效规则。每条 (代号, 判档, 扫描范围, 说明)。扫描范围 q=只问题面，
# all=连选项一起扫（税率与废止税种常出现在选项里）。
VALIDITY_RULES = [
    ("营业税", "stale", "all", "营业税 2016-05-01 起全面改征增值税，现行制度下不存在该税种"),
    ("增值税旧税率", "stale", "all", "17%/16%/11% 是 2019-04-01 前的档位，现行 13%/9%/6%"),
    ("过期年度", "stale", "q", "题面锁定 2019 年及更早年度，按当年口径命题"),
    ("次近年份", "review", "q", "题面锁定 2020-2022 年度，优惠政策可能已延续或调整"),
    ("合并前征管主体", "review", "all", "2018 年国税地税机构合并，不再单设地方税务局征管"),
    ("研发费用加计扣除旧比例", "review", "all", "75%/50% 的加计比例已被 100% 及之后的口径替代"),
]

YEAR_STALE = re.compile(r"20(0\d|1[0-9])\s*年")
YEAR_REVIEW = re.compile(r"20(2[0-2])\s*年")
OLD_VAT_RATE = re.compile(r"(?<![\d.])(?:17|16|11)\s*%")

RULE_MATCH = {
    "营业税": lambda q, blob: "营业税" in blob and "改征" not in blob,
    "增值税旧税率": lambda q, blob: bool(OLD_VAT_RATE.search(blob)) and "增值税" in blob,
    "过期年度": lambda q, blob: bool(YEAR_STALE.search(q)),
    "次近年份": lambda q, blob: bool(YEAR_REVIEW.search(q)),
    "合并前征管主体": lambda q, blob: ("地方税务局" in blob or "地税机关" in blob
                                       or "国税机关" in blob),
    "研发费用加计扣除旧比例": lambda q, blob: ("加计扣除" in blob
                                                and bool(re.search(r"(?<![\d.])50\s*%", blob))),
}

PUNCT = re.compile(r"[，。、；：（）()《》\"'’‘“”【】\[\]{}<>？?！!\-—_%．.\s]")

# 出题范围不在现行税法里的题，一道都不收。
# 只扫题面、不扫选项：选项里出现"两税法""费改税"往往只是一句背景描述
# （实测车辆购置税那道的 D 项就是这么写的），题面问的还是现行税种；
# 反过来，题面一旦落在"税制史／税收历史进程"上，这道题要的是史实，
# 没有任何现行规范可以检索、可以定级，答错记的是题库的命题口径，不是技能能力。
SCOPE_EXCLUDE = [
    ("税制史", re.compile(r"税制史|税收历史|税法.{0,3}建立与发展|历史进程|沿革")),
]


def out_of_scope(question: str):
    """命中返回规则代号，否则 None。"""
    for name, pat in SCOPE_EXCLUDE:
        if pat.search(question):
            return name
    return None


def make_key(question: str, options: dict) -> str:
    """题面连四个选项一起归一，作为跨题库去重的键。

    必须带选项：CPA 题库里有大量"下列关于 X 的说法正确的是"这种通用题干，
    只比题干会把不同题并掉——实测 ideafin 单选第 113/114 条题干逐字相同，
    选项和答案完全不同。也不做长度截断，截断正是造成那次误并的原因。
    """
    return PUNCT.sub("", question + "".join(options.get(k, "") for k in "ABCD"))


def parse_answer(raw: str):
    """答案归一成字母列表。多选的写法是 'A,C,D' 或 'ACD'。"""
    letters = sorted(set(re.findall(r"[A-D]", (raw or "").upper())))
    return letters


def read_csv(path: Path, kind: str):
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = path.read_bytes().decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"编码无法识别：{path}")
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for i, r in enumerate(rows):
        if kind == "csv_ideafin":
            q, ans = r.get("question", ""), r.get("answer", "")
        elif kind == "csv_financeiq":
            q, ans = r.get("Question", ""), r.get("Answer", "")
        else:
            q, ans = r.get("question", ""), r.get("answer", "")
        out.append({"row": i, "question": (q or "").strip(),
                    "options": {k: (r.get(k) or "").strip() for k in "ABCD"},
                    "raw_answer": (ans or "").strip()})
    return out


SEVERITY = {"ok": 0, "review": 1, "stale": 2}
RULE_SEVERITY = {name: SEVERITY[level] for name, level, _, _ in VALIDITY_RULES}


def grade(record: dict):
    """按 VALIDITY_RULES 打时效标记，返回 (validity, flags)。"""
    q = record["question"]
    blob = q + " " + " ".join(record["options"].values())
    flags = [name for name in RULE_MATCH if RULE_MATCH[name](q, blob)]
    if not flags:
        return "ok", []
    worst = max(RULE_SEVERITY[f] for f in flags)
    validity = next(k for k, v in SEVERITY.items() if v == worst)
    return validity, flags


def build(data_dir: Path):
    """读取全部子集，去重归并。返回 (records, stats)。"""
    names = {}
    for p in sorted(data_dir.iterdir()):
        if not p.is_file():
            continue
        names[ALIASES.get(p.name, p.name)] = p

    stats = {"read": Counter(), "dropped": Counter(), "flag": Counter(),
             "dup": 0}
    seen = {}
    records = []

    for fname, (source, subset, kind) in DATASET_FILES.items():
        path = names.get(fname)
        if path is None:
            stats["dropped"][f"缺文件 {fname}"] += 1
            continue
        rows = read_csv(path, kind)
        kept = 0
        for i, r in enumerate(rows, 1):
            stats["read"][subset] += 1
            if not (r["question"] or "").strip():
                stats["dropped"][f"{subset}: 空题面"] += 1
                continue
            if not all(r["options"].values()):
                stats["dropped"][f"{subset}: 选项缺失"] += 1
                continue
            scope = out_of_scope(r["question"])
            if scope:
                stats["dropped"][f"{subset}: 范围外（{scope}）"] += 1
                continue
            answer = parse_answer(r["raw_answer"])
            if not answer:
                stats["dropped"][f"{subset}: 无答案"] += 1
                continue
            key = make_key(r["question"], r["options"])
            if key in seen:
                stats["dup"] += 1
                stats["dropped"][f"与 {seen[key]} 重复"] += 1
                continue
            seen[key] = subset
            validity, flags = grade(r)
            stats["flag"][validity] += 1
            for f in flags:
                stats["flag"]["规则:" + f] += 1
            kept += 1
            records.append({
                "key": key,
                "source": source,
                "subset": subset,
                "ordinal": i,
                "question": r["question"],
                "options": r["options"],
                "answer": "".join(answer),
                "answer_type": "multiple" if len(answer) > 1 else "single",
                "validity": validity,
                "flags": flags,
            })
        print(f"  {subset:<18s} 读入 {len(rows):>4d}  收编 {kept:>4d}", file=sys.stderr)
    return records, stats


def main():
    p = argparse.ArgumentParser(description="归并公开财税题库为统一评测集")
    p.add_argument("--data-dir", default="../eval_data")
    p.add_argument("--out", default="", help="默认写到 <data-dir>/tax_eval_set.jsonl")
    p.add_argument("--report", action="store_true", help="只打印构成，不写文件")
    args = p.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"❌ 目录不存在：{data_dir.resolve()}")
        return 1

    print(f"读取 {data_dir.resolve()}", file=sys.stderr)
    records, stats = build(data_dir)

    by_source = Counter(r["source"] for r in records)
    by_valid = Counter(r["validity"] for r in records)
    by_type = Counter(r["answer_type"] for r in records)

    print("\n评测集构成")
    print(f"  总计 {len(records)} 题（原始读入 {sum(stats['read'].values())}，"
          f"跨库去重 {stats['dup']}）")
    print(f"  按题库: {dict(by_source)}")
    print(f"  按题型: {dict(by_type)}")
    print(f"  按时效: {dict((k, by_valid[k]) for k in ('ok', 'review', 'stale') if by_valid[k])}")

    print("\n时效规则命中")
    for name, level, _, why in VALIDITY_RULES:
        n = stats["flag"].get("规则:" + name, 0)
        print(f"  {name:<12s} {level:<6s} {n:>4d}  {why}")
    if stats["dropped"]:
        print("\n丢弃明细")
        for k, n in sorted(stats["dropped"].items(), key=lambda x: -x[1]):
            print(f"  {k:<40s} {n}")

    print("\n各题库来源")
    for s, desc in SOURCES.items():
        print(f"  {s:<10s} {desc}")

    if args.report:
        return 0

    out = Path(args.out) if args.out else data_dir / "tax_eval_set.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n已写入 {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
