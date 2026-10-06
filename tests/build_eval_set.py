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
    python tests/build_eval_set.py                      # 读 data/eval/raw/，产出 data/eval/tax_eval_set.jsonl
    python tests/build_eval_set.py --report             # 只看构成，不落盘
    python tests/build_eval_set.py --make-manifest      # 同时写 data/eval/MANIFEST.json（入库的清单）
    python tests/build_eval_set.py --verify             # 核对本地数据与清单是否一致
"""
import argparse
import csv
import hashlib
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

# 评测数据的三个落点都在技能目录内，克隆后不需要在仓库旁边再摆一个同名目录。
# 原始 CSV 与构建产物受上游许可约束不入库（见 data/eval/MANIFEST.json 的 note），
# 入库的只有清单：清单记哈希与构成，不含任何题面文本。
REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "data" / "eval"
RAW_DIR = EVAL_DIR / "raw"
EVAL_SET = EVAL_DIR / "tax_eval_set.jsonl"
MANIFEST_PATH = EVAL_DIR / "MANIFEST.json"

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

# 清单里唯一一处许可说明。原始题面与构建产物都不入库，入库的只有这份清单，
# 所以"为什么不入库"必须写在清单能读到的地方，而不是只写在仓库外的目录里。
MANIFEST_NOTE = (
    "本文件只登记哈希与构成，不含任何题面文本。"
    "原始 CSV（data/eval/raw/）与构建产物（data/eval/tax_eval_set.jsonl）受上游许可约束不入仓库："
    "FinanceIQ 与 FinEval 为 CC BY-NC-SA-4.0（非商业、相同方式共享），"
    "IDEAFinBench 上游没有 LICENSE 文件——没有声明不等于允许再分发。"
    "拿到题面后跑 `python tests/build_eval_set.py --make-manifest` 重建，"
    "再用 `--verify` 比对本文件登记的哈希；哈希逐字一致即证明用的是同一批数据。"
)

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
    names = resolve_raw_names(data_dir)

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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_rel(path: Path) -> str:
    """技能内路径写成相对形式；--out/--data-dir 指到仓库外时退回绝对路径。"""
    try:
        return str(path.relative_to(REPO_ROOT).as_posix())
    except ValueError:
        return path.resolve().as_posix()


def resolve_raw_names(data_dir: Path):
    """原始目录里**登记过的** CSV：本地文件名（含旧命名）→ 路径。

    只认 DATASET_FILES 里的名字，目录里混进的别的文件（包括占位的 .gitkeep）一概
    不算——否则空目录会被当成"有数据"，走到构建那一步才失败，回吐的原因也是错的。
    """
    names = {}
    if data_dir.is_dir():
        for p in sorted(data_dir.iterdir()):
            name = ALIASES.get(p.name, p.name)
            if p.is_file() and name in DATASET_FILES:
                names[name] = p
    return names


def summarize(records, stats):
    """把构成压成不含题面的计数，供清单登记。"""
    def c(key):
        return dict(sorted(Counter(r[key] for r in records).items()))
    return {
        "records": len(records),
        "by_source": c("source"),
        "by_subset": c("subset"),
        "by_answer_type": c("answer_type"),
        "by_validity": {k: v for k, v in sorted(Counter(r["validity"] for r in records).items())},
        "rule_hits": dict(sorted((k[len("规则:"):], v)
                                 for k, v in stats["flag"].items() if k.startswith("规则:"))),
        "read_per_subset": dict(sorted(stats["read"].items())),
        "dropped": dict(sorted(stats["dropped"].items())),
        "cross_source_dups": stats["dup"],
    }


def write_manifest(out_path: Path, data_dir: Path, records, stats, manifest_path: Path):
    names = resolve_raw_names(data_dir)
    m = {
        "note": MANIFEST_NOTE,
        "generated_by": "python tests/build_eval_set.py --make-manifest",
        "eval_set": {"path": repo_rel(out_path),
                     "bytes": out_path.stat().st_size,
                     "sha256": sha256_file(out_path)},
        "inputs": {fname: {"path": repo_rel(p),
                           "bytes": p.stat().st_size,
                           "sha256": sha256_file(p),
                           "source": source,
                           "subset": subset}
                   for fname, (source, subset, _) in DATASET_FILES.items()
                   for p in [names.get(fname)] if p},
        "sources": SOURCES,
        "composition": summarize(records, stats),
    }
    manifest_path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"清单已写入 {manifest_path.resolve()}（{len(m['inputs'])}/{len(DATASET_FILES)} 个输入登记哈希）")
    return m


def verify(manifest_path: Path, data_dir: Path, out_path: Path):
    """核对本地数据与清单：缺什么、什么对不上，各自给下一步动作。"""
    if not manifest_path.exists():
        print(f"❌ 没有清单：{manifest_path.resolve()}\n"
              f"   拿到原始题面后跑 `python tests/build_eval_set.py --make-manifest` 生成。")
        return 1
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    bad = 0

    names = resolve_raw_names(data_dir)
    for fname, info in m.get("inputs", {}).items():
        p = names.get(fname)
        if p is None:
            print(f"  缺输入  {fname}  → 上游 {SOURCES.get(info['source'], info['source'])}")
            bad += 1
            continue
        got = sha256_file(p)
        if got != info["sha256"]:
            print(f"  哈希漂移 {fname}  清单 {info['sha256'][:12]}… 本地 {got[:12]}…")
            bad += 1
        else:
            print(f"  输入一致 {fname}  {info['bytes']} 字节")

    want = m.get("eval_set", {})
    if not out_path.exists():
        print(f"❌ 评测集不在：{out_path.resolve()}\n"
              f"   没有它，eval_answer / eval_analysis / eval_retrieval 三个评测脚本无题可跑。"
              f"\n   把 5 个原始 CSV 放进 {data_dir.resolve()} 后跑 "
              f"`python tests/build_eval_set.py`；重建后此处哈希应与清单逐字一致"
              f"（{want.get('sha256', '未登记')[:12]}…）。")
        return 1
    got = sha256_file(out_path)
    if got == want.get("sha256"):
        print(f"  评测集一致 {out_path.name}  {want['bytes']} 字节  sha256 {got[:12]}…")
    else:
        print(f"❌ 评测集与清单不符：本地 sha256 {got[:12]}…，清单 {want.get('sha256', '未登记')[:12]}…\n"
              f"   要么原始 CSV 换过版本（重跑 --make-manifest 并核对构成），"
              f"要么评测集被单独改过（删掉后重建）。")
        bad += 1
    return 1 if bad else 0


def missing_data_message(data_dir: Path) -> str:
    return (f"❌ 原始题面不在：{data_dir.resolve()}\n"
            f"   这一步不能省：评测集由 {len(DATASET_FILES)} 个公开题库的 CSV 归并而成，"
            f"上游许可（CC BY-NC-SA-4.0 两份；IDEAFinBench 无 LICENSE 文件）不允许把它们"
            f"随技能再分发，所以仓库里只有清单 {MANIFEST_PATH}。\n"
            f"   文件名见清单的 inputs，下载位置见 sources；放齐后跑 "
            f"`python tests/build_eval_set.py --verify` 对哈希，再构建。")


def main():
    p = argparse.ArgumentParser(description="归并公开财税题库为统一评测集")
    p.add_argument("--data-dir", default=str(RAW_DIR),
                   help="原始 CSV 所在目录，默认技能内的 data/eval/raw/")
    p.add_argument("--out", default=str(EVAL_SET),
                   help="评测集落点，默认技能内的 data/eval/tax_eval_set.jsonl")
    p.add_argument("--report", action="store_true", help="只打印构成，不写文件")
    p.add_argument("--make-manifest", action="store_true",
                   help="构建后把哈希与构成写进 data/eval/MANIFEST.json（入库）")
    p.add_argument("--verify", action="store_true",
                   help="只核对本地数据与 MANIFEST.json，不写任何文件")
    args = p.parse_args()

    data_dir = Path(args.data_dir)
    out_path = Path(args.out)

    if args.verify:
        return verify(MANIFEST_PATH, data_dir, out_path)

    if not resolve_raw_names(data_dir):
        print(missing_data_message(data_dir))
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

    if not records:
        print(f"❌ 读到了文件却一题都没收进来：{data_dir.resolve()}\n"
              f"   不写文件——空评测集到了下游会被读成「0 分」，而真实原因是没数据。\n"
              f"   上面「丢弃明细」那一栏列的就是每道题的去处；全是「空题面／选项缺失」"
              f"时，多半是上游改了 CSV 的列名，对照 read_csv() 的取值键查。")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n已写入 {out_path.resolve()}")

    if args.make_manifest:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_manifest(out_path, data_dir, records, stats, MANIFEST_PATH)
    else:
        print(f"提示：加 --make-manifest 可把本次哈希与构成写进 {MANIFEST_PATH.name}（入库的那份清单）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
