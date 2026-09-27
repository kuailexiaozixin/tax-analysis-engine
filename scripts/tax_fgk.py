#!/usr/bin/env python3
"""
国家税务总局政策法规库 (fgk.chinatax.gov.cn) — 检索清单并取正文。

取正文的关键是编码，不是 JS 渲染。该站详情页不声明 charset，requests 按
HTTP 头的 ISO-8859-1 解码，于是中文全部变成乱码，看起来"正文不在 HTML 里"
（检索词在原文里 0 次命中）。显式按 UTF-8 解码字节流即可拿到完整正文，
容器是 <div class="zscont">（注释）与 <div class="arc_cont">（正文），
正文内的法规名带 <a href> 指向关联文件。

页面头部还带一组 meta：ArticleTitle、PubDate、ContentSource、articleId
（关联文件查询接口 queryManuscriptAssociation 的入参）。

检索要翻页，否则永远筛不出法规文件。该接口把 pageSize 卡在 10，传更大的值
也只回 10 条（实测传 20/60/100 一律返回 10 条），所以只能按页取。总局站里
法规库条目占比不高且排在后面：搜"转让定价"命中 173 条，第 1 页 10 条全是
经济日报、经合组织的外国税改新闻，法规文件在第 2、3、5、6 页，《特别纳税调整
实施办法》直到第 5 页才出现。只读第 1 页会把"库里没有"错报成"确实没有"。

Usage:
  python tax_fgk.py "研发费用" --size 10
  python tax_fgk.py "增值税" --size 5 --json
  python tax_fgk.py "资产评估减值" --size 1 --body
  python tax_fgk.py "转让定价" --size 3 --pages 8
"""

import argparse
import html as htmllib
import json
import re
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from tax_web_search import FGK_MARKER, search_chinatax

# 总局检索接口把 pageSize 卡在 10 条，传更大的值无效，只能按页翻。
PAGE_SIZE = 10
# 翻到第几页为止。实测第 6 页仍能翻出法规文件，再深的页收益极低。
MAX_PAGES = 6

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

_ZS_OPEN_RE = re.compile(r'<div class="zscont"[^>]*>', re.I)
_ARC_OPEN_RE = re.compile(r'<div class="arc_cont"[^>]*>', re.I)
# 导航栏里也含 arc_cont，按顺序取最后一处才是正文
_ZS_END_RE = re.compile(r'<div class="arc_cont"[^>]*>|</body>', re.I)
_ARC_END_RE = re.compile(
    r'<div class="(?:arc_cont|bot-btns-box)"[^>]*>|</body>', re.I)
_META_RE = re.compile(r'<meta\s+name="([^"]+)"\s+content="([^"]*)"', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_PARA_RE = re.compile(r"</(?:p|div|li|tr)\s*>|<br\s*/?>", re.I)


def _text_of(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", fragment)
    fragment = _PARA_RE.sub("\n", fragment)
    fragment = _TAG_RE.sub("", fragment)
    fragment = htmllib.unescape(fragment)
    fragment = fragment.replace(" ", " ").replace("　", " ")
    lines = [ln.strip() for ln in fragment.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def fetch_fgk_body(url: str) -> dict:
    """
    取法规库详情页正文。

    Returns:
        {"url","title","pub_date","content","meta"?,"_error"?}
    """
    out = {"url": url}
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
    except requests.RequestException as e:
        out["_error"] = f"请求失败：{e}"
        return out
    if r.status_code != 200:
        out["_error"] = f"HTTP {r.status_code}"
        return out

    # 不声明 charset，必须显式按 UTF-8 解码，否则中文全是乱码
    page = r.content.decode("utf-8", errors="replace")
    meta = {k.lower(): v for k, v in _META_RE.findall(page)}
    out["meta"] = meta
    out["title"] = meta.get("articletitle", "")

    # 按位置切，不用嵌套正则：zscont 内部嵌套 div，贪婪/懒惰都拿不准边界，
    # 且 arc_cont 在导航栏里也出现，必须取 zscont 之后的第一处。
    note = raw_body = ""
    zs = list(_ZS_OPEN_RE.finditer(page))
    arcs = list(_ARC_OPEN_RE.finditer(page))
    if zs and arcs:
        z = zs[-1]
        note = page[z.end():_ZS_END_RE.search(page, z.end()).start()]
        a = next((m for m in arcs if m.start() > z.start()), None)
        if a is not None:
            raw_body = page[a.end():_ARC_END_RE.search(page, a.end()).start()]
    elif arcs:
        a = arcs[-1]
        raw_body = page[a.end():_ARC_END_RE.search(page, a.end()).start()]

    if not raw_body:
        out["_error"] = "未取到正文（页面结构与预期不符）"
        return out

    body = _text_of(raw_body)
    # 税法小课堂等栏目的正文是视频/图片，容器取得到但没有文字
    if not body and re.search(r"<(video|img|audio)\b", raw_body, re.I):
        out["_error"] = "该条正文为视频/图片，无文字内容"
        return out

    out["content"] = "\n".join(x for x in (_text_of(note), body) if x)
    out["pub_date"] = (meta.get("pubdate", "") or "")[:10]
    if not out["content"]:
        out["_error"] = "正文容器为空"
    return out


def search_fgk(keyword: str, size: int = 10, with_body: bool = False,
               max_pages: int = MAX_PAGES) -> dict:
    """
    在税务总局法规库检索法规文件清单。

    Args:
        keyword: 检索词
        size: 返回条数上限
        with_body: 逐条取详情页正文（每条多一次请求）
        max_pages: 最多翻几页总局检索结果（每页固定 10 条）

    Returns:
        {"keyword","total","pages_scanned","results","searched_at","source","_error"?}
        每项含 title/document_number/date/publisher/url，with_body 时另有 body。
    """
    results = []
    seen = set()
    pages = 0
    first_error = ""
    total_hits = 0
    for page in range(1, max(1, max_pages) + 1):
        found = search_chinatax(keyword, page=page, size=PAGE_SIZE)
        if not first_error and found.get("_error"):
            first_error = found["_error"]
        if page == 1:
            total_hits = found.get("total", 0)
        page_items = found.get("results", [])
        pages += 1
        if not page_items:
            break
        for item in page_items:
            if FGK_MARKER not in item.get("url", ""):
                continue
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            entry = {
                "title": item["title"],
                "url": item["url"],
                "date": item.get("date", ""),
                "document_number": item.get("document_number", ""),
                "publisher": item.get("publisher", ""),
                "snippet": item.get("snippet", ""),
                "source": "税务总局法规库",
                "source_label": "税务总局法规库",
            }
            if with_body and item["url"]:
                body = fetch_fgk_body(item["url"])
                entry["body"] = body.get("content", "")
                if body.get("pub_date") and not entry["date"]:
                    entry["date"] = body["pub_date"]
                if body.get("_error"):
                    entry["body_error"] = body["_error"]
            results.append(entry)
            if len(results) >= size:
                break
        if len(results) >= size:
            break

    result = {
        "keyword": keyword,
        "total": len(results),
        "total_hits": total_hits,
        "pages_scanned": pages,
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "税务总局法规库 (fgk.chinatax.gov.cn)",
        "_from_cache": False,
    }
    # 检索本身失败要透出错误，不要和"库里没有"混为一谈
    if first_error:
        result["_error"] = first_error
    elif not results:
        result["_error"] = (f"翻完前 {pages} 页总局检索结果（共 {total_hits} 条命中）"
                            "未筛出法规库条目")
    return result


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="国家税务总局政策法规库检索")
    p.add_argument("keyword", help="检索词")
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--body", action="store_true",
                   help="同时取详情页正文（每条多一次请求）")
    p.add_argument("--pages", type=int, default=MAX_PAGES,
                   help=f"最多翻几页总局检索结果，每页固定 {PAGE_SIZE} 条"
                        f"（默认 {MAX_PAGES}）")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = search_fgk(args.keyword, size=args.size, with_body=args.body,
                        max_pages=args.pages)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"🔍 税务总局法规库 \"{args.keyword}\" | {result['searched_at']}")
    if result.get("_error"):
        print(f"⚠️  {result['_error']}")
    print(f"总局检索命中 {result['total_hits']} 条，翻了 {result['pages_scanned']} 页，"
          f"筛出法规文件 {result['total']} 条\n")
    for item in result["results"]:
        print(f"  📋 {item['title']}")
        if item.get("document_number"):
            print(f"     文号: {item['document_number']}")
        if item.get("date"):
            print(f"     日期: {item['date']}")
        if item.get("publisher"):
            print(f"     发文机关: {item['publisher']}")
        print(f"     {item['url']}")
        if item.get("body"):
            print(f"     正文 {len(item['body'])} 字:")
            for ln in item["body"].splitlines():
                print(f"       {ln}")
        if item.get("body_error"):
            print(f"     ⚠️ {item['body_error']}")
        print()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
