#!/usr/bin/env python3
"""
国家税务总局政策法规库 (fgk.chinatax.gov.cn) — 只出清单，不取正文。

边界说明（重要）：
  法规库列表页 /zcfgk/c100028/zcwj.html 的检索是前端 JS 发的，直连该页
  拿到的 HTML 里没有结果行（实测 43,838 字节，0 次出现检索词）。
  详情页 cXXXXXXX/content.html 同样是 JS 渲染，直连只有元信息。
  因此本模块走 search5 检索接口按域名筛出法规库条目，字段限于
  标题、文号、发文日期、发文机关、地址——正文取不到，不做假数据。

fgk 的条目在总局检索结果里以 fgk.chinatax.gov.cn 域名出现，
而总局检索服务已同时覆盖这两个站点，故共用 search5 接口再按 URL 过滤。

Usage:
  python tax_fgk.py "研发费用" --size 10
  python tax_fgk.py "增值税" --size 5 --json
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from tax_web_search import FGK_MARKER, search_chinatax

# 法规库结果在总局检索结果里的占比不高，多翻几页才够筛出 size 条
OVERFETCH = 4
PAGE_SIZE = 20


def search_fgk(keyword: str, size: int = 10) -> dict:
    """
    在税务总局法规库检索法规文件清单。

    Args:
        keyword: 检索词
        size: 返回条数上限

    Returns:
        {"keyword","total","results","searched_at","source","_error"?}
        每项含 title/document_number/date/publisher/url。
    """
    want = min(size * OVERFETCH, PAGE_SIZE)
    found = search_chinatax(keyword, size=want)

    results = []
    for item in found.get("results", []):
        if FGK_MARKER not in item.get("url", ""):
            continue
        results.append({
            "title": item["title"],
            "url": item["url"],
            "date": item.get("date", ""),
            "document_number": item.get("document_number", ""),
            "publisher": item.get("publisher", ""),
            "snippet": item.get("snippet", ""),
            "source": "税务总局法规库",
            "source_label": "税务总局法规库",
        })
        if len(results) >= size:
            break

    result = {
        "keyword": keyword,
        "total": len(results),
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "税务总局法规库 (fgk.chinatax.gov.cn)",
        "_from_cache": False,
    }
    # 检索本身失败要透出错误，不要和"库里没有"混为一谈
    if found.get("_error"):
        result["_error"] = found["_error"]
    elif not results:
        result["_error"] = "总局检索结果中未筛出法规库条目（该库条目在检索结果中占比偏低）"
    return result


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="国家税务总局政策法规库检索（只出清单）")
    p.add_argument("keyword", help="检索词")
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = search_fgk(args.keyword, size=args.size)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"🔍 税务总局法规库 \"{args.keyword}\" | {result['searched_at']}")
    if result.get("_error"):
        print(f"⚠️  {result['_error']}")
    print(f"共 {result['total']} 条\n")
    for item in result["results"]:
        print(f"  📋 {item['title']}")
        if item.get("document_number"):
            print(f"     文号: {item['document_number']}")
        if item.get("date"):
            print(f"     日期: {item['date']}")
        if item.get("publisher"):
            print(f"     发文机关: {item['publisher']}")
        print(f"     {item['url']}")
        print()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
