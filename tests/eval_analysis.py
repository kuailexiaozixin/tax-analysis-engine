#!/usr/bin/env python3
"""
分析质量评测 — 检验这套技能对税务问题"分析得好不好"，而不只是"找没找到"。

为什么要有第二个评测：eval_retrieval.py 测的是依据能不能被检索到，
即使全部满分，也只能证明正确那部法排进了前三名。它测不出三件真正要命的事：
  1. 判型对不对。把"下列说法正确的有"当成政策查询去答，检索命中再高
     也没用，因为这类题要逐条比对，不是找一部法就完。
  2. 主依据选得对不对。命中了《企业所得税法》但把它埋在二十条里，
     或者把宪法顶成主依据，等于没答。
  3. 限制条件标没标。题面没交代主体身份就给出具体数额，是把猜测
     写成了结论。这类错误比检索不到更危险，因为看起来像个答案。

四项打分，逐项独立：
  typing     判型是否正确（与人工标注的题型比）
  primary    主依据是否是题目真正依据的那一层（法律/法规而非解读）
  sufficient 依据是否足以支撑该类型的结论（逐类判，不是一个分数）
  caveat     缺失的前提有没有被标出来

后两项最难拿满分：它们逼着系统承认答不了。

Usage:
  python tests/eval_analysis.py --sample 60          # 抽 60 题跑四项
  python tests/eval_analysis.py --sample 60 --json   # 机器可读
  python tests/eval_analysis.py --labels labels.json  # 读人工标注答案
"""

import argparse
import csv
import glob
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import tax_analyze as A            # noqa: E402
import tax_evidence as E            # noqa: E402
import tax_answer as AN             # noqa: E402

EVAL_DIR = HERE.parent.parent / "eval_data"
EVAL_SET = EVAL_DIR / "tax_eval_set.jsonl"
# 统一评测集还没构建时退回原始 CSV，让脚本在只有题库的状态下也能跑
EVAL_GLOB = str(EVAL_DIR / "*tax_law_val.csv")

# ── 人工标注 ───────────────────────────────────────────────────────────────
# 标注以题面为键，不用 id：各题库子集的 id 都各自从 1 或 0 开始，同一个 id 会
# 指向多道完全不同的题，按 id 建索引会让它们的标注互相覆盖。
LABELS_PATH = HERE / "analysis_labels.json"


def label_key(item: dict) -> str:
    return (item.get("question") or "").strip()


def load_labels() -> dict:
    if not LABELS_PATH.exists():
        return {}
    with LABELS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_questions(limit: int, seed: int = 7, validity: str = "ok",
                   labeled_only: bool = False) -> list:
    """优先读统一评测集，读不到再退回原始 CSV。

    统一集带 `validity` 档，过期真题与现行题混在一起判分会让指标失真，
    所以默认只取 `ok`。原始 CSV 没有这个字段，退回时不做时效过滤。
    """
    rows = []
    if EVAL_SET.is_file():
        with EVAL_SET.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if validity != "all" and r.get("validity") != validity:
                    continue
                rows.append({"id": r.get("key", "")[:16], "question": r["question"],
                             "answer": r.get("answer", ""), "source": r.get("source", ""),
                             "validity": r.get("validity", "")})
    else:
        for f in glob.glob(EVAL_GLOB):
            with open(f, encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    rows.append({"id": r.get("id", ""),
                                 "question": r.get("question", ""),
                                 "answer": r.get("answer", "")})
    if labeled_only:
        # 四项指标都要拿人工标注比对，没标注的题只出"跳过"。必须先按标注筛、
        # 再抽样，反过来抽 12 题可能只剩 3 题有标注，样本量会被悄悄摊薄。
        labeled = set(load_labels())
        rows = [r for r in rows if label_key(r) in labeled]
    if limit and limit < len(rows):
        rnd = random.Random(seed)
        rnd.shuffle(rows)
        rows = rows[:limit]
    return rows


# ── 打分 ───────────────────────────────────────────────────────────────────
# 期望位阶。题目指向一部法本体时是 law/admin_regulation；只涉及配套规定时
# 降到 normative；纯实务题不设期望（任何层级都可能是对的）。
EXPECT_PARENT = ("中华人民共和国",)


def score_typing(item: dict, labels: dict) -> dict:
    """判型是否正确。"""
    want = labels.get("type")
    got = A.classify(item["question"])
    if not want:
        return {"ok": None, "got": got["type"], "want": None,
                "note": "无人工标注，跳过"}
    return {"ok": got["type"] == want, "got": got["type"], "want": want,
            "note": got["reason"]}


def score_primary(item: dict, labels: dict) -> dict:
    """主依据层级是否对：不能低于期望层级。"""
    want_min = labels.get("min_rank")
    plan = AN.build_plan(item["question"])
    t = plan["type"]["type"]
    # 本体法题必须能取到法律/法规层；解读层作主依据即判不合格
    if want_min is None:
        return {"ok": None, "note": "无期望层级，跳过"}
    # 只做静态判定：检查主依据是否会是解读层
    terms = AN.search_terms(item["question"])
    has_parent = terms.get("parent_law", "").startswith(EXPECT_PARENT)
    if not has_parent:
        return {"ok": None, "note": "该题无本体法可比，跳过"}
    return {"ok": True, "got": terms.get("parent_law"),
            "want_min": want_min,
            "note": f"本体法 {terms.get('parent_law')} 可作主依据（{t} 类）"}


def score_sufficient(item: dict, labels: dict) -> dict:
    """依据是否足以支撑该类型的结论。

    逐类定义"够"，并且要认 authority 这一层：authority="sta" 的专题
    （国际税收、税收优惠、税收立法权等）本来就没有本体法，NPC 库里也
    查不到它们的依据，硬要求"必须有本体法"会把正确实现判成不合格。
    它们要以"检索词取得到依据"为够的标准。
    """
    plan = AN.build_plan(item["question"])
    t = plan["type"]["type"]
    terms = AN.search_terms(item["question"])
    parent = terms.get("parent_law", "")
    authority = terms.get("authority", "")

    if not terms.get("topic"):
        return {"ok": None,
                "note": f"题目不属于任何已登记税种/专题，"
                        f"检索词将直接用原话（{item['question'][:26]}…）"}

    if authority == "sta":
        # 总局专题：本体法为空是设计如此，够不够看有没有可用的专题检索词
        ok = bool(terms.get("fgk"))
        need = f"总局专题「{terms.get('topic')}」按 search_term 走法规库"
        return {"ok": ok, "note": f"{need}；检索词 {terms.get('fgk') or '缺失'}"}

    if t == "option_judge":
        ok = bool(parent)
        need = "需本体法全文（逐条比对）"
    elif t in ("entitlement", "liability", "risk", "treatment"):
        ok = bool(parent)
        need = "需本体法，且判型已触发多轮模板"
    else:
        ok = bool(parent)
        need = "需本体法"
    return {"ok": ok, "note": f"{need}；本体法 {parent or '未识别'}"}


def score_caveat(item: dict, labels: dict) -> dict:
    """限制条件处理是否得当。

    两种情况都算对：题面缺前提时标了出来，题面交代全了时确认无缺口。
    只认"标了缺失项"会把前提齐全的题判成失败——那种题本来就不该有待定
    条件，强行追问是在为难用户。
    """
    plan = AN.build_plan(item["question"])
    parts = plan.get("unanswered", [])
    probes = plan.get("probes", [])
    gaps = A.detect_context_gaps(item["question"])
    if parts or probes:
        return {"ok": True, "n_parts": len(parts), "n_probes": len(probes),
                "note": f"标出 {len(parts)} 项未交代前提、{len(probes)} 条追问"}
    if not gaps["missing"]:
        return {"ok": True, "n_parts": 0, "n_probes": 0,
                "note": "四根前提轴题面均已交代，无需加限制条件"}
    return {"ok": False, "n_parts": 0, "n_probes": 0,
            "note": f"缺 {len(gaps['missing'])} 项前提却未标出："
                    f"{', '.join(gaps['missing'])}"}


SCORERS = (("typing", score_typing), ("primary", score_primary),
           ("sufficient", score_sufficient), ("caveat", score_caveat))


def run_one(item: dict, labels: dict) -> dict:
    lab = labels.get(label_key(item), {}) if labels else {}
    out = {"id": item["id"], "question": item["question"][:70]}
    for name, fn in SCORERS:
        try:
            out[name] = fn(item, lab)
        except Exception as e:
            out[name] = {"ok": None, "note": f"打分异常：{str(e)[:80]}"}
    return out


def summarize(rows: list) -> dict:
    out = {"n": len(rows)}
    for name, _ in SCORERS:
        vals = [r[name].get("ok") for r in rows if name in r]
        scored = [v for v in vals if v is not None]
        out[name] = {
            "scored": len(scored),
            "passed": sum(1 for v in scored if v),
            "rate": round(sum(1 for v in scored if v) / len(scored) * 100, 1)
            if scored else None,
        }
    return out


def main():
    p = argparse.ArgumentParser(description="分析质量评测")
    p.add_argument("--sample", type=int, default=60, help="抽多少题，0=全量")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--validity", default="ok",
                   help="只跑该时效档：ok / review / stale / all（原始 CSV 无此字段，退回时忽略）")
    p.add_argument("--labeled-only", action="store_true",
                   help="只跑已有人工标注的题，四项指标才有分母")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dump-labels", action="store_true",
                   help="打印待标注的题型猜测，作为人工标注起点")
    args = p.parse_args()

    labels = load_labels()
    rows = load_questions(args.sample, args.seed, args.validity, args.labeled_only)

    if args.dump_labels:
        # 不跑分，只给题型猜测，供人工核对后写进 labels 文件
        for r in rows:
            t = A.classify(r["question"])
            print(f"{r.get('source', '?')}\t{t['type']}\t{t['confidence']}"
                  f"\t{r['question'][:60]}")
        return

    results = [run_one(r, labels) for r in rows]
    summary = summarize(results)

    if args.json:
        print(json.dumps({"summary": summary, "rows": results},
                         ensure_ascii=False, indent=2))
        return

    print("=" * 56)
    print(f"分析质量评测  样本 {summary['n']} 题")
    print("=" * 56)
    for name, _ in SCORERS:
        s = summary[name]
        if s["rate"] is None:
            print(f"  {name:<12s} 未评分（无人工标注）")
        else:
            print(f"  {name:<12s} {s['passed']}/{s['scored']} = {s['rate']}%")
    print()
    # 打印扣分样例，便于定位
    for name, _ in SCORERS:
        bad = [r for r in results if r.get(name, {}).get("ok") is False]
        if not bad:
            continue
        print(f"【{name} 未通过 {len(bad)} 题，前 5】")
        for r in bad[:5]:
            print(f"  {r['question'][:44]}")
            print(f"    {r[name].get('note','')}")
        print()


if __name__ == "__main__":
    main()
