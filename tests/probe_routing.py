#!/usr/bin/env python3
"""路由探针：把"题面 → 归到哪个专题 → 各源用什么检索词"打印出来。

不联网、不调模型，改前改后各跑一次同一份题面，diff 就能看出修复动了谁。
题面清单是攒出来的：每次评测或实测暴露的失焦题面都加进来，
所以这份清单本身就是回归集，不是随手举的例子。

用法：
    python tests/probe_routing.py            # 人读
    python tests/probe_routing.py --json     # 机器读，用来 diff
    python tests/probe_routing.py --cache    # 附带评测缓存里的失焦题面
"""

import argparse
import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import tax_answer as AN          # noqa: E402
import tax_search as T           # noqa: E402

# 每条都带一句"为什么要盯它"，改动了映射表之后要靠这些题面判 regress。
PROBES = [
    ("查询、阅读、分析、解读《关于企业重组业务所得税处理有关征管问题的公告》（2026年第13号）",
     "用户已给出文件全名与文号，税种是企业所得税，却被\"征管\"抢走"),
    ("企业重组业务中适用特殊性税务处理需要满足哪些条件",
     "重组类问题按税种归到企业所得税，别当横切专题"),
    ("某公司进口货物被海关征收滞纳金，滞纳金应按关税还是进口增值税算",
     "同时出现关税与滞纳金时，主体税种是关税"),
    ("下列属于稿酬所得项目的是",
     "稿酬是个税的所得项目，表里必须有这个别名"),
    ("国际重复征税产生的根本原因是什么",
     "重复征税要归到税收协定专题"),
    ("中外合资开采石油产品分成收入要不要缴企业所得税",
     "产品分成收入要有归属，不能直接拿原话去检索"),
    ("远洋船员一个纳税年度内在船上累计工作满183天，个税怎么算",
     "远洋船员是个税专项口径，必须路由到个税"),
    ("小微企业有什么税收优惠",
     "横切专题与税种同时命中时，先落到能给出条文的那个税种"),
    ("增值税小规模纳税人月销售额10万元，应纳增值税多少",
     "常态题面，动过排序之后不能退步"),
    ("研发费用加计扣除比例是多少",
     "通用字带偏的老案例，主依据必须是企业所得税法"),
    # ↓ 盯梢项：每一条都对应一处实测到的错归类
    ("中国籍船舶驶入国内港口，按吨位缴纳的船舶吨税怎么计算",
     "吨税有单独的船舶吨税法，本体法不得挂在车船税法下"),
    ("占用耕地建设厂房要不要缴纳耕地占用税",
     "表里必须有耕地占用税这一项，不能直接拿原话检索"),
    ("企业发生的长期待摊费用，在企业所得税前如何分期摊销扣除",
     "会计科目名也是企业所得税的实务口径，必须有人认领"),
    ("土地増值税清算的条件有哪些",
     "题面用的是日式字形「増」（U+5827），NFKC 折不成「增」，"
     "评测侧早就收了这种写法，路由表不能漏"),
    ("个人转让限售股取得的所得怎么征收个人所得税",
     "反向盯梢：限售股是个税的所得项目，不能被企业所得税的通用别名抢走"),
    ("企业取得的应纳税所得额如何计算",
     "反向盯梢：应纳税所得额跨税种通用，收进企业所得税别名会抢走个税与土增题"),
]


def probe_row(text: str) -> dict:
    info = T.resolve_tax_type(text) or {}
    terms = AN.search_terms(text)
    return {
        "text": text,
        "topic": info.get("type") or "",
        "alias": info.get("matched_alias") or "",
        "parent_law": info.get("parent_law") or "",
        "authority": info.get("authority") or "",
        "search_term": info.get("search_term") or "",
        "npc": terms["npc"],
        "fgk": terms["fgk"],
        "shui5": terms["shui5"],
        "wechat": terms["wechat"],
    }


def eval_route_stats() -> dict:
    """把整份评测集的题目逐条走一遍路由，纯本地统计，不联网。

    路由是确定性字符串匹配，所以"未路由"的比例可以在改前改后各量一次直接对比，
    不必重跑检索，更不必调模型。评测集读不到就返回空统计，探针仍然可用。
    """
    path = PROJECT_ROOT / "data" / "eval" / "tax_eval_set.jsonl"
    if not path.exists():
        return {"missing": str(path)}
    import eval_answer as EA
    items = EA.load_questions("all")
    routed, unrouted = 0, []
    topics = {}
    for it in items:
        text = EA.stem_with_options(it)
        info = T.resolve_tax_type(text) or {}
        if info:
            routed += 1
            topics[info.get("type", "")] = topics.get(info.get("type", ""), 0) + 1
        elif len(unrouted) < 40:
            unrouted.append(text[:60])
    return {"n": len(items), "routed": routed,
            "unrouted": len(items) - routed,
            "rate": round(routed / len(items) * 100, 1) if items else 0,
            "topics": topics, "unrouted_samples": unrouted}


def main():
    ap = argparse.ArgumentParser(description="路由探针（离线）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--stats", action="store_true",
                    help="附带整份评测集的离线路由覆盖率统计")
    args = ap.parse_args()

    rows = [probe_row(t) for t, _ in PROBES]
    notes = {t: n for t, n in PROBES}
    stats = eval_route_stats() if args.stats else {}

    if args.json:
        print(json.dumps({"probes": rows, "eval_route_stats": stats},
                         ensure_ascii=False, indent=1))
        return 0

    for r in rows:
        print(f"\n题面：{r['text']}")
        print(f"  期望：{notes[r['text']]}")
        print(f"  归类：{r['topic'] or '未路由'}（命中别名「{r['alias']}」）"
              f" 本体法：{r['parent_law'] or '—'} 依据源：{r['authority'] or '—'}")
        print(f"  检索词：npc={r['npc']} | fgk={r['fgk']} | shui5={r['shui5']}")
    if stats:
        if stats.get("missing"):
            print(f"\n评测集读不到：{stats['missing']}")
        else:
            print(f"\n整份评测集离线路由：{stats['routed']}/{stats['n']}"
                  f" 命中专题（{stats['rate']}%），未路由 {stats['unrouted']} 条")
            for t, c in sorted(stats["topics"].items(), key=lambda kv: -kv[1]):
                print(f"  {t}：{c}")
            print("  未路由题面前 12 条：")
            for s in stats["unrouted_samples"][:12]:
                print(f"    · {s}")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
