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

# 每个源挂掉时"缺的到底是哪一层"。原先这件事写在文档里，要读的人自己把
# "某源 0 条"翻译成"哪一层没了、能不能拿别的源顶"。现在由程序说清楚：
# 缺哪层、什么后果、以及不要用其他源顶替。
SOURCE_IMPACT = {
    "npc": "全国性法律法规层（法律/行政法规/司法解释）缺失，没有别的源能覆盖它",
    "chinatax": "总局公告、部门规章与官方解读层缺失",
    "so360": "地方口径层与税屋的链接发现层缺失（税屋靠 360 的 site:shui5.cn 检索取链接）",
    "shui5": "税屋的实务解读层缺失",
    "wechat": "微信公众号的实务解读层缺失",
}


def _build_gaps(sources: list, errors: dict, source_summary: dict) -> list:
    """把源级失败翻译成"缺了哪一层 + 怎么处理"，供上层照抄，不用自己推断。

    两条判定：
      1. 该源自己报了错（被拦、接口异常）→ 记一条缺口；
      2. 税屋的链接靠 360 的 site:shui5.cn 检索取得，所以 360 被拦而税屋又
         0 条时，税屋那条空结果不是"税屋没有内容"，是被 360 连带的——必须
         标成 blocked_by，否则会被读成"该主题没有实务解读"。
    """
    gaps = []
    for src in sources:
        if src in errors:
            gaps.append({
                "source": src,
                "label": SOURCE_LABELS.get(src, src),
                "reason": errors[src],
                "impact": SOURCE_IMPACT.get(src, "该源本次没有取到内容"),
                "do_not_substitute": True,
            })

    if ("so360" in errors and "shui5" in sources
            and not source_summary.get("shui5")
            and not any(g["source"] == "shui5" for g in gaps)):
        gaps.append({
            "source": "shui5",
            "label": SOURCE_LABELS.get("shui5", "shui5"),
            "reason": "360 被拦，而税屋链接是靠 360 的 site:shui5.cn 检索取得的",
            "impact": "税屋这一层的空结果是被 360 连带的，不代表税屋没有内容",
            "blocked_by": "so360",
            "do_not_substitute": True,
        })
    return gaps


def _degraded_note(sources: list, gaps: list, source_summary: dict) -> str:
    """把缺口拼成一句可以直接抄进答案的话；没有缺口时给空串。

    这段话是给"答案里必须明说该层缺失"这条要求用的：原先要读文档的人自己
    组织措辞，现在程序给成句，照抄即可，也不必自己判断哪层算缺。
    """
    if not gaps:
        return ""
    hit = sum(1 for s in sources if source_summary.get(s))
    missing = "、".join(g["label"] for g in gaps)
    return (f"本次多源检索 {hit}/{len(sources)} 个源有命中，缺失：{missing}。"
            "缺失只说明这次没取到，不代表该层没有对应内容；"
            "不要用其他源的条目顶替缺失层，答案里要明说缺的是哪一层。")


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
                     exact: bool = False,
                     sort: str = "relevance") -> dict:
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
        sort: "relevance"（默认）按权威度分层；"date" 时跨源按公布日期降序，
              不再按权威度分层——"只看最新"要的正是时间序，把 NPC 整源顶在
              前面会让总局 2026 年的公告排在 NPC 2024 年的法律之后。
    """
    if sources is None:
        sources = list(DEFAULT_SOURCES)

    results = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {}

        if "npc" in sources:
            # sort 要传到 NPC 这一路：不传的话取回的窗口是按接口自己那套序排的，
            # 后面只在窗口内按日期重排，"最新"的那批根本没能进窗口。
            futures["npc"] = pool.submit(
                search_tax, keyword, scope=scope, status=status, size=size,
                search_type=1 if exact else 2, sort=sort,
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
        # 源自己回报"取不到"（被拦、接口异常）时要进 errors：
        # source_summary 里的 0 条只说明没拿到，不说明该源没有内容。
        if data.get("_error"):
            errors[source] = data["_error"]
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
    if sort == "date":
        all_items.sort(key=lambda x: x.get("publish_date") or "", reverse=True)
    else:
        all_items.sort(key=lambda x: (
            x.get("_authority_rank", 99),
            # Put items with dates before those without
            0 if x.get("publish_date") else 1,
        ))

    source_summary = {
        s: len(results[s].get("results", [])) if results.get(s) else 0
        for s in sources
    }
    gaps = _build_gaps(sources, errors, source_summary)
    return {
        "keyword": keyword,
        "total_sources": len(sources),
        "total_items": len(all_items),
        "items": all_items[:size * 3],  # Cap total results
        "source_summary": source_summary,
        "errors": errors,
        # 缺了哪一层、什么后果、要不要拿别的源顶 → 由程序说，不靠读者推断
        "gaps": gaps,
        "degraded_note": _degraded_note(sources, gaps, source_summary),
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
    p.add_argument("--sort", choices=["relevance", "date"], default="relevance",
                   help="relevance=按权威度分层；date=跨源按公布日期降序（不再分层）")
    p.add_argument("--json", action="store_true")

    args = p.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    result = aggregate_search(
        args.keyword,
        size=args.size,
        sources=sources,
        status=args.status,
        scope=args.scope,
        sort=args.sort,
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
    if result.get("gaps"):
        print(f"   ⚠️ 缺失的层 {len(result['gaps'])} 处（答案里要明说，不要用其他源顶替）：")
        for g in result["gaps"]:
            by = f"［被 {g['blocked_by']} 连带］" if g.get("blocked_by") else ""
            print(f"      - {g['label']}{by}：{g['impact']}")
        print(f"      {result['degraded_note']}")
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
