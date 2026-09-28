#!/usr/bin/env python3
"""
Tax Policy Aggregator — concurrent multi-source search with dedup and ranking.

Data source priority (按权威性排序):
  1. NPC API (flk.npc.gov.cn)       — 法律、行政法规，权威性最高
  2. chinatax.gov.cn (search5)      — 税务总局公告、解读，覆盖 fgk 法规库
  3. 360 站内搜索 (m.so.com)        — 省局子站、地方文件
  4. 税屋 (shui5.cn)                — 实务解读
  5. 微信公众号 (搜狗微信)          — 实务解读

第 4、5 两源默认开启：二者补的是前三个源查不到的实操层内容。权威性低于
法规原文，输出里以 _authority_rank 排在后面。

税屋当前只能取链接、正文取不到（阿里云 WAF 不认可算出的 cookie，详见
tax_shui5 模块说明），它在这轮里仍然有用——检索本身稳定，能告诉你"这个话题
税屋上有几篇实务文章"，正文再另找渠道。

AnySearch 已移除：本机不存在其 CLI（~/.claude/skills/anysearch/scripts 下
没有 anysearch_cli.py），原实现永远静默返回空列表。

Usage:
  python tax_aggregator.py "增值税" --size 10
  python tax_aggregator.py "小微企业优惠" --size 10 --json
  python tax_aggregator.py "加计扣除" --sources npc,chinatax
  python tax_aggregator.py "资本化" --sources npc,chinatax,so360,shui5,wechat
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Import sibling modules
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from tax_search import search_tax
from tax_web_search import search_chinatax
from tax_so360 import so360_search
from tax_shui5 import search_shui5
from tax_wechat import search_wechat

# 权威性排名，数字越小越权威
SOURCE_RANK = {
    "npc": 0,
    "chinatax": 1,
    "so360": 2,
    "shui5": 3,
    "wechat": 4,
}
DEFAULT_SOURCES = ["npc", "chinatax", "so360", "shui5", "wechat"]
SOURCE_LABELS = {
    "npc": "📜 NPC法规库",
    "chinatax": "🏛️ 国家税务总局",
    "so360": "🔎 360站内搜索",
    "shui5": "🏠 税屋(实务解读)",
    "wechat": "💬 微信公众号(实务解读)",
}


def _jaccard_similarity(s1: str, s2: str) -> float:
    """Simple Jaccard similarity on character trigrams for title dedup."""
    if not s1 or not s2:
        return 0.0

    def trigrams(s):
        s = s.lower().strip()
        return {s[i:i+3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}

    t1 = trigrams(s1)
    t2 = trigrams(s2)
    if not t1 or not t2:
        return 0.0

    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union if union > 0 else 0.0


def deduplicate(results: list, threshold: float = 0.7) -> list:
    """Remove near-duplicate results based on title similarity."""
    deduped = []
    for item in results:
        title = item.get("title", "")
        is_dup = False
        for existing in deduped:
            sim = _jaccard_similarity(title, existing.get("title", ""))
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            deduped.append(item)
    return deduped


def aggregate_search(keyword: str, *,
                     size: int = 10,
                     sources: list = None,
                     status: int = 3,
                     scope: str = "title",
                     exact: bool = False) -> dict:
    """
    Concurrently search multiple data sources and return deduplicated, ranked results.

    Args:
        keyword: search term
        size: results per source
        sources: 见 DEFAULT_SOURCES；默认五源全开
        status: NPC status filter (default: 3 = effective)
        scope: NPC search scope (default: title；fulltext 已按相关度排序但可能偏题)
        exact: NPC 精确检索。检索词是本体法名时必须为 True，否则模糊检索
               按发布时间排，宪法会顶掉本该在首位的本体法。
    """
    if sources is None:
        sources = list(DEFAULT_SOURCES)

    results = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {}

        if "npc" in sources:
            futures["npc"] = pool.submit(
                search_tax, keyword, scope=scope, status=status, size=size,
                search_type=1 if exact else 2,
            )
        if "chinatax" in sources:
            futures["chinatax"] = pool.submit(
                search_chinatax, keyword, size=size
            )
        if "so360" in sources:
            # 全网检索，再由调用方按需收窄站点；此处不带 site:
            futures["so360"] = pool.submit(
                so360_search, keyword, site="", size=size
            )
        if "shui5" in sources:
            futures["shui5"] = pool.submit(
                search_shui5, keyword, min(size, 5)
            )
        if "wechat" in sources:
            futures["wechat"] = pool.submit(
                search_wechat, keyword, min(size, 5)
            )

        for source, future in futures.items():
            try:
                results[source] = future.result(timeout=30)
            except Exception as e:
                errors[source] = str(e)
                results[source] = None

    # Collect all results，按权威性排序
    all_items = []
    for source, data in results.items():
        if not data:
            continue
        rank = SOURCE_RANK[source]
        source_rel = data.get("_reliability")
        source_note = data.get("_reliability_note", "")
        for item in data.get("results", []):
            item["_source"] = source
            item["_authority_rank"] = rank
            # 各源日期字段不统一，统一到 publish_date 供排序用
            if item.get("date") and not item.get("publish_date"):
                item["publish_date"] = item["date"]
            # 整源被判低可靠时逐条带上，否则聚合输出里这条禁令会失效
            if source_rel:
                item["_reliability"] = source_rel
                if source_note:
                    item["_reliability_note"] = source_note
            all_items.append(item)

    # Deduplicate across sources
    all_items = deduplicate(all_items)

    # Sort: authority rank first, then by date
    all_items.sort(key=lambda x: (
        x.get("_authority_rank", 99),
        # Put items with dates before those without
        0 if x.get("publish_date") else 1,
    ))

    return {
        "keyword": keyword,
        "total_sources": len(sources),
        "total_items": len(all_items),
        "items": all_items[:size * 3],  # Cap total results
        "source_summary": {
            s: len(results[s].get("results", [])) if results.get(s) else 0
            for s in sources
        },
        "errors": errors,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="Multi-source tax policy search aggregator",
        epilog="""
Examples:
  python tax_aggregator.py "增值税" --size 10
  python tax_aggregator.py "加计扣除" --sources npc,chinatax --json
        """
    )
    p.add_argument("keyword", help="Search keyword")
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                   help=f"Comma-separated source names (default: {','.join(DEFAULT_SOURCES)})")
    p.add_argument("--status", type=int, default=3,
                   help="NPC status filter (3=effective)")
    p.add_argument("--scope", choices=["title", "fulltext"], default="title",
                   help="NPC search scope (default: title)")
    p.add_argument("--json", action="store_true")

    args = p.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    result = aggregate_search(
        args.keyword,
        size=args.size,
        sources=sources,
        status=args.status,
        scope=args.scope,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"🔍 多源搜索 \"{args.keyword}\" | {result['searched_at']}")
    print(f"   数据源: {', '.join(sources)}")
    for src in sources:
        print(f"   {SOURCE_LABELS.get(src, src)}: {result['source_summary'].get(src, 0)} 条")
    if result.get("errors"):
        for src, err in result["errors"].items():
            print(f"   ⚠️ {src}: {err}")
    flagged = [i for i in result.get("items", []) if i.get("_reliability")]
    if flagged:
        for lvl in ("low", "medium"):
            n = sum(1 for i in flagged if i["_reliability"] == lvl)
            if n:
                hint = ("不得作为权威依据引用" if lvl == "low"
                        else "可用于定位法规，确定条文归属请改用标题检索")
                print(f"   ⚠️ {n} 条结果带 _reliability: {lvl}，{hint}")
    print()

    for item in result.get("items", [])[:20]:
        label = SOURCE_LABELS.get(item.get("_source", ""), "")
        flag = f"  [_reliability: {item['_reliability']}]" if item.get("_reliability") else ""
        print(f"  {label} {item.get('title', '')[:80]}{flag}")
        if item.get("publish_date"):
            print(f"     日期: {item['publish_date']}")
        if item.get("document_number"):
            print(f"     文号: {item['document_number']}")
        if item.get("id"):
            print(f"     NPC ID: {item['id']}")
        if item.get("url"):
            print(f"     {item['url']}")
        print()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
