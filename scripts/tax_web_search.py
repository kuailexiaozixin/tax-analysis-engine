#!/usr/bin/env python3
"""
Tax Web Search — 国家税务总局站点检索。

检索接口用的是 www.chinatax.gov.cn 的 search5 检索服务（JSON），
站点编号 siteCode=bm29000002，覆盖总局站点与 fgk 法规库
（结果 url 中出现 fgk.chinatax.gov.cn 即为法规库条目）。

旧的 /was5/web/search 接口已下线：实测返回 HTTP 404 并把首页 HTML
当成响应体（144,077 字节），无法据此解析结果，故不再使用。

Usage:
  python tax_web_search.py "增值税" --size 10
  python tax_web_search.py "小微企业优惠" --size 10 --json
"""

import argparse
import json
import re
import sys
import time
from html import unescape
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://www.chinatax.gov.cn"
SEARCH_URL = f"{BASE_URL}/search5/search/s"
# 总局站点编号，检索结果同时覆盖 fgk.chinatax.gov.cn
SITE_CODE = "bm29000002"
# 法规库条目标识，用于给结果分层
FGK_MARKER = "fgk.chinatax.gov.cn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": f"{BASE_URL}/",
}
TIMEOUT = 20

_DOC_NUM_RE = re.compile(
    r'(财政部[、\s]*税务总局公告\d{4}年第\d+号|'
    r'国家税务总局公告\d{4}年第\d+号|'
    r'财税\[\d{4}\]\d+号|'
    r'税总发\[\d{4}\]\d+号)'
)
_TAG_RE = re.compile(r"<[^>]+>")


def search_chinatax(keyword: str, page: int = 1, size: int = 10) -> dict:
    """
    检索国家税务总局站点。

    Args:
        keyword: 检索词
        page: 页码，从 1 开始
        size: 返回条数上限

    Returns:
        {"keyword","total","results","searched_at","source","_error"?,"_from_cache"}
        total 是检索命中的总条数（可能远大于 results 长度）；
        total 为 0 时若有 _error，说明是请求失败而非无结果。
    """
    params = {
        "siteCode": SITE_CODE,
        "searchWord": keyword,
        "type": "1",
        "pageSize": max(size, 10),
        "pageNum": page,
        "orderBy": "5",   # 相关度排序
        "column": "",
        "label": "",
    }

    try:
        r = requests.get(SEARCH_URL, params=params, headers=HEADERS,
                         timeout=TIMEOUT, verify=False)
    except requests.RequestException as e:
        return _empty_result(keyword, str(e))

    if r.status_code != 200:
        return _empty_result(keyword, f"HTTP {r.status_code}")

    try:
        payload = r.json()
    except ValueError as e:
        return _empty_result(keyword, f"响应不是 JSON: {e}")

    block = payload.get("searchResultAll") or {}
    items = block.get("searchTotal") or []
    total = block.get("total") or 0

    results = []
    for it in items[:size]:
        url = (it.get("url") or "").strip()
        title = _clean(it.get("title") or "")
        if not url or not title:
            continue
        content = _clean(it.get("content") or "")
        # 文号优先从标题取，取不到再从正文摘要取
        doc_num = _first_match(_DOC_NUM_RE, title) or _first_match(_DOC_NUM_RE, content)
        results.append({
            "title": title,
            "url": url,
            "date": (it.get("pubDate") or "")[:10],
            "document_number": doc_num,
            "snippet": content[:200],
            "publisher": it.get("pubName") or "",
            "source": "chinatax.gov.cn",
            "source_label": "税务总局法规库" if FGK_MARKER in url else "税务总局",
        })

    return {
        "keyword": keyword,
        "total": total,
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "chinatax.gov.cn",
        "_from_cache": False,
    }


def _clean(fragment: str) -> str:
    """去高亮标签、解实体、压空白。检索结果标题里带 <span> 标记命中词。"""
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub("", fragment))).strip()


def _first_match(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(1) if m else ""


def _empty_result(keyword: str, error: str = "") -> dict:
    return {
        "keyword": keyword,
        "total": 0,
        "results": [],
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "chinatax.gov.cn",
        "_error": error,
        "_from_cache": False,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="检索国家税务总局站点（总局 + 法规库）"
    )
    p.add_argument("keyword", help="检索词")
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--json", action="store_true", help="输出 JSON")

    args = p.parse_args()
    result = search_chinatax(args.keyword, size=args.size)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"🔍 chinatax.gov.cn 搜索 \"{args.keyword}\" | {result['searched_at']}")
    if result.get("_error"):
        print(f"⚠️  {result['_error']}")
    print(f"命中 {result['total']} 条，取回 {len(result['results'])} 条\n")

    for item in result.get("results", []):
        print(f"  📋 {item['title']}")
        if item.get("document_number"):
            print(f"     文号: {item['document_number']}")
        if item.get("date"):
            print(f"     日期: {item['date']}")
        if item.get("publisher"):
            print(f"     来源: {item['publisher']}")
        if item.get("snippet"):
            print(f"     摘要: {item['snippet'][:100]}")
        print(f"     {item['url']}")
        print()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
