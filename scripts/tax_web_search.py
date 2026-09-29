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
import math
import re
import sys
import time
from html import unescape
from typing import Optional

import requests
import urllib3

import tax_http

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
        page: 页码，从 1 开始（对应接口的 pageNum 从 0 开始，见下）
        size: 返回条数上限

    页码基准：search5 的 pageNum 从 0 起算。实测同一检索词「企业重组」
    发 pageNum=0 与 pageNum=1 各回 10 条、url 交集为空，且 pageNum=0 那组
    才是相关度最高的一页；发 pageNum=1 等于整轮检索永远丢掉首屏，命中数
    不足 10 条时（如「企业重组业务所得税处理」共 3 条）第一页就是唯一一页，
    取回的空列表会被上层读成"库里没有这份文件"。本函数对外仍按 1 起算，
    发请求时减 1。NPC 法规库那个接口（tax_search.py）经实测是从 1 起算，
    两边基准不同，不要照抄。

    Returns:
        {"keyword","total","results","searched_at","source","_error"?,"_from_cache"}
        total 是检索命中的总条数（可能远大于 results 长度）；
        total 为 0 时若有 _error，说明是请求失败而非无结果。
    """
    params = {
        "siteCode": SITE_CODE,
        "searchWord": keyword,
        "type": "1",
        # 接口这一维是从 0 起算的页码，本项目对外的 page 从 1 起算，
        # 所以发请求时要减回去。
        "pageSize": max(size, 10),
        "pageNum": max(page, 1) - 1,
        "orderBy": "5",   # 相关度排序
        "column": "",
        "label": "",
    }

    try:
        r = requests.get(SEARCH_URL, params=params, headers=HEADERS,
                         timeout=TIMEOUT, verify=False)
    except requests.RequestException as e:
        return _empty_result(keyword, tax_http.short_reason(e))

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
        # 文号：接口把结构化文号放在 govDoc 里，比从标题正则抠更可靠
        # （标题常不含文号，而 govDoc.docNum 是录入项）。实测 2026 年第 13 号
        # 公告的 docNum = "国家税务总局公告2026年第13号"。
        gov = it.get("govDoc") or {}
        doc_num = (gov.get("docNum") or "").strip() or \
            _first_match(_DOC_NUM_RE, title) or _first_match(_DOC_NUM_RE, content)
        row = {
            "title": title,
            "url": url,
            "date": (it.get("pubDate") or "")[:10],
            "document_number": doc_num,
            "snippet": content[:200],
            "publisher": it.get("pubName") or "",
            "source": "chinatax.gov.cn",
            "source_label": "税务总局法规库" if FGK_MARKER in url else "税务总局",
        }
        # 时效与效力级别是接口的录入项（xxgk_aging / xxgk_effectLevel），只有
        # 政策法规条目会填，解读和新闻这两栏为空。带上它们，法规库条目才有
        # 明文时效可判——不带的话每条都只能报"时效未标明"。
        aging = aging_of(it.get("xxgk_aging"))
        if aging:
            row["status"] = aging
            row["status_from"] = "法规库录入项 xxgk_aging"
        eff_level = (it.get("xxgk_effectLevel") or "").strip()
        if eff_level:
            row["effect_level"] = eff_level
        cwrq = (it.get("cwrq") or "")[:10]
        if cwrq:
            row["publish_date"] = cwrq
        results.append(row)

    out = {
        "keyword": keyword,
        "total": total,
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "chinatax.gov.cn",
        "_from_cache": False,
    }
    # 命中数大于 0 但这一页没有条目。两种成因要分开写，处置完全不同：
    # 页号越过末页（每页固定 10 条，命中 3 条时只有第 1 页）是正常收尾；
    # 第 1 页就空则是接口这一轮没给清单，不能读成"库里没有"。
    # 空页都不是请求失败，所以不进 _error。
    if total and not items:
        last_page = math.ceil(total / 10)
        if page > last_page:
            out["_empty_reason"] = (
                f"已翻过末页：命中 {total} 条只占 {last_page} 页，"
                f"第 {page} 页本来就空，按已取回的清单下结论即可")
        else:
            out["_empty_reason"] = (
                f"接口报告命中 {total} 条，第 {page} 页却没给条目清单"
                f"（该页在末页之内，不是翻页越界）；换一个检索词再取一轮"
                f"才有结论，不能据此说库里没有")
    return out


def _clean(fragment: str) -> str:
    """去高亮标签、解实体、压空白。检索结果标题里带 <span> 标记命中词。"""
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub("", fragment))).strip()


# 时效录入项里出现的"这一栏没填"写法。接口对没录时效的条目回的是字符串
# "null"（实测财税〔2003〕16 号），照原样带上下游会把 null 当时效文本读。
_AGING_BLANKS = {"", "null", "none", "nil", "-", "—", "/"}


def aging_of(raw) -> str:
    """把接口的 xxgk_aging 归一成状态文本或空串。"""
    text = str(raw or "").strip()
    return "" if text.lower() in _AGING_BLANKS else text


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
