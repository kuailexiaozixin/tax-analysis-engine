#!/usr/bin/env python3
"""判型探针：把"题面 → 判成哪一类、有多把握"打印出来，并统计整份评测集的分布。

与 `probe_routing.py` 同一种东西，量的是另一根轴：路由管"这题去哪个源查"，
这份管"这题被当成哪种问题"。两者都是确定性字符串匹配，所以纯离线就能跑，
不联网、不调模型，改前改后各跑一次直接 diff。

为什么要单独有一份：判型错不会报错，只会让后面的检索计划换一套依据，
而答出来的东西仍然像模像样。`confidence=0.30` 那一档是"词表一个信号都没抓到，
按政策查询兜底"，它是判型层唯一的失焦出口，所以这条数字要能被盯住。

用法：
    python tests/probe_classify.py            # 人读
    python tests/probe_classify.py --json     # 机器读，用来 diff
    python tests/probe_classify.py --real     # 附带真实提问面板（非考题写法）
    python tests/probe_classify.py --oracle   # 附带"选项全为数值⇒该题在算税"标尺

兜底率与标尺是两个方向相反的指标：前者低不代表判得准（把算税题判成填空也能
把兜底清零），后者才管"判成测算的题是不是真在算税"。两个要一起看。
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import tax_analyze as AA          # noqa: E402

# 题面清单：每条都带一句"为什么要盯它"，与 probe_routing.py 同一套攒法。
# 这一份只打印不断言，判对与否看"期望"那一行；要钉住行为请改
# tests/test_routing_terms.py 的 test_form_bonus_only_decides_when_content_is_silent。
PROBES = [
    ("刘某2022年2月15日从4S店购买小轿车一辆，支付含增值税价款47.46万元。"
     "该型号轿车车船税年税额840元，刘某2022年应缴纳车船税为（ ）。",
     "带留空的算税题。原表把「为/是」当列举动词，这题掉进零命中兜底档；"
     "解开以后形态分又把它拽成术语填空，词表收「应缴纳」之后内容分压过形态分，"
     "现应判 liability"),
    ("资本弱化特殊事项文档应当在关联交易发生年度次年6月30日之前准备完毕，"
     "应当自税务机关要求之日起的一定时间内提供，该时间限定是（ ）。",
     "留空前是系动词「是」，题面没有测算措辞，只有形态在说话 → fill_blank"),
    ("下列不属于印花税应税凭证的有（ ）。",
     "有「下列」引子且留空前是集合动词「有」，必须归选项判断而不是填空"),
    ("转让定价方法包括（ ）。",
     "留空前是集合动词「包括」，填的是方法清单，不许当术语填空"),
    ("小规模纳税人季度销售额30万元是否免征增值税",
     "真实提问写法。entitlement 原先只有「是否适用」，没有「是否免征」"),
    ("研发费用加计扣除比例是多少",
     "常态题面，动过形态分之后不能退步"),
]

# 真实提问：用户对技能会说的话，不是考题形态。判型词表在这些写法上的表现
# 才是它在本项目主线里的实际表现（评测集整体是考题形态）。
REAL_PANEL = [
    ("小规模纳税人季度销售额30万元是否免征增值税", "entitlement"),
    ("这笔退款要不要交增值税", "treatment"),
    ("公司转让股权未申报有什么风险", "risk"),
    ("研发费用加计扣除比例是多少", "lookup"),
    ("印花税的税目有哪些", "lookup"),
]


def probe_row(text: str) -> dict:
    r = AA.classify(text)
    return {"text": text, "type": r["type"], "label": r.get("label", ""),
            "confidence": r["confidence"],
            "alternatives": r.get("alternatives", []),
            "reason": r.get("reason", "")}


def eval_classify_stats() -> dict:
    """整份评测集走一遍判型，纯本地统计。读不到评测集就返回空统计。"""
    path = PROJECT_ROOT.parent / "eval_data" / "tax_eval_set.jsonl"
    if not path.exists():
        return {"missing": str(path)}
    questions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        q = (json.loads(line).get("question") or "").strip()
        if q:
            questions.append(q)
    types = collections.Counter()
    fallback = 0
    samples = []
    for q in questions:
        r = AA.classify(q)
        types[r["type"]] += 1
        # 0.30 只有"零信号兜底"这一条路径会得到，所以它唯一地标记失焦
        if r["confidence"] <= 0.3:
            fallback += 1
            if len(samples) < 40:
                samples.append(q[:60])
    n = len(questions)
    return {"n": n, "types": dict(types), "fallback": fallback,
            "fallback_rate": round(fallback / n * 100, 1) if n else 0,
            "fallback_samples": samples}


def real_panel() -> list:
    out = []
    for q, want in REAL_PANEL:
        r = AA.classify(q)
        out.append({"text": q, "want": want, "got": r["type"],
                    "confidence": r["confidence"], "ok": r["type"] == want})
    return out


# ── 判型的外部标尺：只看选项，不看题干 ────────────────────────────────────
# 判型没有标准答案，所以它一直只能被"数兜底题"间接盯着。选项文本给了一条独立
# 标尺：评测集全是选择题，四个选项都是数值的题一定在问"算多少"，
# 判成税负测算才算对；选项是一句说法的题不属这一档，判成测算就是抢题。
# 判据只吃选项、不吃题干，与 classify 用的词表完全无关，所以它能证伪词表。
_ORACLE_BARE = re.compile(
    r"^[\s\d,.，、;；%/／\-~～]*(?:万元|元|％|%|倍|小时|日|个月|年|吨|辆|台)?[\s]*$")


def _numeric_option_question(row: dict) -> bool:
    opts = [str(v) for v in (row.get("options") or {}).values()]
    if len(opts) < 2:
        return False
    return all(bool(_ORACLE_BARE.match(o.strip())) and any(c.isdigit() for c in o)
               for o in opts)


def oracle_stats() -> dict:
    """数值选项题判成 liability 的比例；判型词表改宽改窄都会动这个数。"""
    path = PROJECT_ROOT.parent / "eval_data" / "tax_eval_set.jsonl"
    if not path.exists():
        return {"missing": str(path)}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    num, non = [], []
    for row in rows:
        q = (row.get("question") or "").strip()
        if not q:
            continue
        (num if _numeric_option_question(row) else non).append(q)
    got_num = collections.Counter(AA.classify(q)["type"] for q in num)
    got_non = collections.Counter(AA.classify(q)["type"] for q in non)
    return {
        "n": len(rows), "numeric_n": len(num), "other_n": len(non),
        "numeric_types": dict(got_num),
        "numeric_hit": got_num.get("liability", 0),
        "numeric_hit_rate": round(got_num.get("liability", 0) / len(num) * 100, 1)
        if num else 0,
        # 反向指标：选项是一句说法的题被判成算税题，检索计划会去找税率而非法条
        "other_as_liability": got_non.get("liability", 0),
    }


def main():
    ap = argparse.ArgumentParser(description="判型探针（离线）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stats", action="store_true",
                    help="附带整份评测集的离线判型分布")
    ap.add_argument("--real", action="store_true", help="附带真实提问面板")
    ap.add_argument("--oracle", action="store_true",
                    help="附带'选项全为数值 ⇒ 该题在问算多少'这条独立标尺")
    args = ap.parse_args()

    rows = [probe_row(t) for t, _ in PROBES]
    panel = real_panel() if args.real else []
    stats = eval_classify_stats() if args.stats else {}
    oracle = oracle_stats() if args.oracle else {}

    if args.json:
        print(json.dumps({"probes": rows, "real_panel": panel,
                          "eval_classify_stats": stats, "oracle": oracle},
                         ensure_ascii=False, indent=1))
        return 0

    notes = {t: n for t, n in PROBES}
    for r in rows:
        print(f"\n题面：{r['text'][:56]}")
        print(f"  期望：{notes[r['text']]}")
        print(f"  判定：{r['type']}（{r['label']}） conf={r['confidence']}"
              f" 备选={r['alternatives'] or '—'}")
        print(f"  理由：{r['reason']}")
    if panel:
        hit = sum(1 for p in panel if p["ok"])
        print(f"\n真实提问面板：{hit}/{len(panel)} 判对")
        for p in panel:
            flag = "OK " if p["ok"] else "MISS"
            print(f"  {flag} 期望{p['want']:12s} 实得{p['got']:12s} "
                  f"conf={p['confidence']} | {p['text'][:34]}")
    if stats:
        if stats.get("missing"):
            print(f"\n评测集读不到：{stats['missing']}")
        else:
            print(f"\n整份评测集离线判型：{stats['n']} 题，"
                  f"零信号兜底 {stats['fallback']} 题（{stats['fallback_rate']}%）")
            for t, c in sorted(stats["types"].items(), key=lambda kv: -kv[1]):
                print(f"  {t}：{c}")
            print("  兜底题面前 12 条：")
            for s in stats["fallback_samples"][:12]:
                print(f"    · {s}")
    if oracle:
        if oracle.get("missing"):
            print(f"\n评测集读不到：{oracle['missing']}")
        else:
            print(f"\n外部标尺（只看选项）：{oracle['n']} 题里 "
                  f"{oracle['numeric_n']} 题四个选项全是数值，这些题在问\u201c算多少\u201d")
            print(f"  判成 liability：{oracle['numeric_hit']}/{oracle['numeric_n']}"
                  f"（{oracle['numeric_hit_rate']}%）")
            print(f"  选项是一句说法却判成 liability：{oracle['other_as_liability']}"
                  f"/{oracle['other_n']}")
            print("  数值题的判型分布：",
                  {k: v for k, v in sorted(oracle["numeric_types"].items(),
                                          key=lambda kv: -kv[1])})
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
