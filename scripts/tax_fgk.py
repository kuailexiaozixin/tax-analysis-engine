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

检索要翻页，否则筛不出法规文件。该接口把 pageSize 卡在 10，传更大的值也只回
10 条（实测传 20/60/100 一律返回 10 条），所以只能按页取。翻页基准：总局
search5 的 pageNum 从 0 起算，收在 tax_web_search.search_chinatax 里（本模块
的 page 参数从 1 起算）。基准用错时实测很隐蔽——「特别纳税调整实施办法」
按 1 起算发出去，取回的是真实第二屏，目标文件落在第 5 屏；按 0 起算后它就是
第 1 屏的首条。

法规库条目在总局站里占比低，靠不靠前一屏要看检索词：窄词（「特别纳税调整
实施办法」命中 21 条）第 1 屏就有 5 条法规库条目；宽词（「转让定价」命中
174 条）第 1、2、5 屏各 0 条，法规文件散在第 3、4、6 屏。所以只读第 1 页
会把"库里没有"错报成"确实没有"，必须按页筛。

Usage:
  python tax_fgk.py "研发费用" --size 10
  python tax_fgk.py "增值税" --size 5 --json
  python tax_fgk.py "资产评估减值" --size 1 --body
  python tax_fgk.py "转让定价" --size 3 --pages 8       # 严格翻 8 页（关自适应）
  python tax_fgk.py "增值税" --size 10 --cache          # 清单缓存(TTL 1h)，正文仍现拉
  python tax_fgk.py --cache-stats / --cache-clear       # 查看 / 清空缓存

翻页约定：默认按需自适应——连续 IDLE_PAGE_LIMIT(3) 页没捞到新的法规库条目
就收尾，不再往后翻（总局站里法规库条目占比低、集中在靠前页，后面多是新闻）。
显式给 --pages 则关掉自适应，严格翻满该页数。

缓存约定：只缓存"检索清单"（标题/文号/日期/URL），**正文永不缓存**——
条文必须每次现拉，避免把已废止/被修订的旧条文当现行有效引用。
"""

import argparse
import copy
import html as htmllib
import json
import re
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import tax_http
from tax_cache import CacheManager
from tax_web_search import FGK_MARKER, search_chinatax

# 总局检索接口把 pageSize 卡在 10 条，传更大的值无效，只能按页翻。
PAGE_SIZE = 10
# 翻页上限。实测“转让定价”全量 174 条命中分布在约 18 页，第 6 页之后仍有
# 法规库条目（默认 6 页只会筛出约 28% 的 fgk 条目）；故放宽到 20 页以覆盖
# 完整法规集。命中量极大的关键词用 --pages 自行收敛即可。
MAX_PAGES = 20

# 清单缓存 TTL（秒）。只缓存检索清单，正文永远现拉。默认关闭，用 --cache 打开。
# 清单/元数据变动频率低（与手册“详情元数据 1 小时”一致），故 TTL 取 1 小时。
CACHE_TTL = 3600

# 按需自适应收尾：连续这么多页都没捞到新的法规库条目，就不再往后翻。
# 总局站里法规库条目占比低且集中在靠前的页，后面的页基本是新闻，继续翻
# 只是白烧请求。要严格翻满某个页数，用 --pages 显式指定（那时不做自适应）。
IDLE_PAGE_LIMIT = 3

# 第几页之内算"浅页"。总局检索按相关度排序，越往后越松：第 1 页基本都在主题
# 上，第 2 页起开始出现只沾一个词的条目。浅页之外的条目一律标 _reliability:
# medium（只可用于定位），这样聚合、定级、格式化三处既有机制会自动按"不能当
# 条文依据"处理——原先"深页要回 L1 核对上位法"只能靠人记住。
FGK_SHALLOW_PAGES = 1

FGK_DEEP_NOTE = (
    "取自总局检索第 {page} 页：翻得越深排序越松，深页条目可能只是沾了检索词。"
    "仅用于定位法规，引用具体条文前必须回上一级数据库核对上位法。")

# 清单缓存键的翻页基准版本号。总局接口的 pageNum 原是从 0 起算、我们按 1
# 起算发送，等于每轮检索都丢掉相关度最高的首屏；按 1 起算后，用旧基准写下的
# 清单缺的就是这一屏，必须让它整体失效重抓，所以把基准写进键里。
LIST_KEY_REV = "pn0"

_cache = CacheManager(enabled=False, namespace="fgk")

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
        # verify 显式传 True：本模块原先走的是 requests 的默认校验（tax_detail
        # 与 tax_search 用的则是默认关闭的 VERIFY_SSL），这一处保持原行为。
        r = tax_http.get(url, headers=HEADERS, timeout=25, verify=True)
    except requests.RequestException as e:
        out["_error"] = f"请求失败：{tax_http.short_reason(e)}"
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


def _scan_list(keyword: str, size: int, max_pages: int,
               adaptive: bool = True) -> dict:
    """只翻检索清单，不取正文（这一层才可缓存）。

    adaptive=True 时按需收尾：连续 IDLE_PAGE_LIMIT 页没捞到新的法规库条目
    就停。总局站里法规库条目占比低且集中在靠前页，后面的页多是新闻，继续
    翻只是白烧请求。要严格翻满就用 adaptive=False（CLI 显式给 --pages 时）。
    """
    results = []
    seen = set()
    pages = 0
    idle_pages = 0          # 连续多少页没新增法规库条目
    stopped_early = False   # 是否因自适应而提前收尾
    first_error = ""
    empty_reason = ""
    total_hits = 0
    for page in range(1, max(1, max_pages) + 1):
        found = search_chinatax(keyword, page=page, size=PAGE_SIZE)
        if not first_error and found.get("_error"):
            first_error = found["_error"]
        if page == 1:
            total_hits = found.get("total", 0)
            empty_reason = found.get("_empty_reason", "")
        page_items = found.get("results", [])
        pages += 1
        if not page_items:
            break
        before = len(results)
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
                # 记下取自第几页：下游要靠它判断"这条是主题命中还是深页凑数"
                "page": page,
            }
            # 文号、时效、效力级别都从接口的录入项带下来。缺了这三栏，法规库
            # 条目每条都只能报"时效未标明"，定级环节就没法把 2026 年新发的
            # 公告和已被废止的公告分开。
            for k in ("status", "status_from", "effect_level", "publish_date"):
                if item.get(k):
                    entry[k] = item[k]
            if entry.get("effect_level"):
                entry["category"] = entry["effect_level"]
            if page > FGK_SHALLOW_PAGES:
                entry["_reliability"] = "medium"
                entry["_reliability_note"] = FGK_DEEP_NOTE.format(page=page)
            results.append(entry)
            if len(results) >= size:
                break
        if len(results) >= size:
            break
        if adaptive:
            if len(results) == before:
                idle_pages += 1
                if idle_pages >= IDLE_PAGE_LIMIT:
                    stopped_early = True
                    break
            else:
                idle_pages = 0

    result = {
        "keyword": keyword,
        "total": len(results),
        "total_hits": total_hits,
        "pages_scanned": pages,
        "stopped_early": stopped_early,
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "税务总局法规库 (fgk.chinatax.gov.cn)",
        "_from_cache": False,
    }
    # 检索本身失败要透出错误，不要和"库里没有"混为一谈
    if first_error:
        result["_error"] = first_error
    elif empty_reason:
        # 接口给了命中数、这一页没给清单：不能写成"翻完 N 页未筛出法规库条目"，
        # 那是把接口的返回形态当成库的内容。
        result["_error"] = empty_reason
        result["_empty_reason"] = empty_reason
    elif not results:
        tail = (f"（连续 {idle_pages} 页无新法规库条目，已自适应收尾）"
                if stopped_early else "")
        result["_error"] = (f"翻完前 {pages} 页总局检索结果（共 {total_hits} 条命中）"
                            f"未筛出法规库条目{tail}")
    return result


def search_fgk(keyword: str, size: int = 10, with_body: bool = False,
               max_pages: int = MAX_PAGES, adaptive: bool = True) -> dict:
    """
    在税务总局法规库检索法规文件清单。

    缓存策略：**只缓存清单，正文永不缓存**。清单按
    (keyword, size, max_pages, adaptive) 缓存 CACHE_TTL 秒；命中缓存时直接
    返回清单，正文（with_body）仍逐条现拉。

    Args:
        keyword: 检索词
        size: 返回条数上限
        with_body: 逐条取详情页正文（每条多一次请求，正文不走缓存）
        max_pages: 最多翻几页总局检索结果（每页固定 10 条）
        adaptive: 连续 IDLE_PAGE_LIMIT 页无新法规库条目即收尾（默认开）；
                  要严格翻满 max_pages 就传 False

    Returns:
        {"keyword","total","total_hits","pages_scanned","stopped_early","results",
         "searched_at","source","_from_cache","_cache_age_s"?,"_error"?}
        每项含 title/document_number/date/publisher/url；with_body 时另有
        body（正文）。正文是视频/图片的条目另带 media_only=True——表示"本来
        就没有文字"，与取失败的 body_error 区分开，上层据此判断无需重试。
    """
    cache_key = _cache._key("fgk", LIST_KEY_REV, keyword, str(size),
                            str(max_pages), str(adaptive))
    result = _cache.get(cache_key, max_age=CACHE_TTL)
    if result is not None:
        # 深拷贝，避免下面写 body 时污染缓存文件
        result = copy.deepcopy(result)
        result["_from_cache"] = True
        cached_age = _cache.age(cache_key)
        if cached_age is not None:
            result["_cache_age_s"] = round(cached_age, 1)
    else:
        result = _scan_list(keyword, size, max_pages, adaptive)
        _cache.set(cache_key, result)  # 缓存的是"无正文"的清单

    # 正文永远现拉，绝不缓存（避免引用过期条文）
    if with_body:
        for entry in result["results"]:
            if not entry.get("url"):
                continue
            body = fetch_fgk_body(entry["url"])
            entry["body"] = body.get("content", "")
            if body.get("pub_date") and not entry["date"]:
                entry["date"] = body["pub_date"]
            if body.get("_error"):
                entry["body_error"] = body["_error"]
                # 视频/图片条目单独标出来：这不是"取失败"，是"本来就没有文字"。
                # 上层见到 media_only 就知道不该去引条文，也不必重试。
                if "视频/图片" in body["_error"]:
                    entry["media_only"] = True
    return result


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="国家税务总局政策法规库检索")
    p.add_argument("keyword", nargs="?", help="检索词")
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--body", action="store_true",
                   help="同时取详情页正文（每条多一次请求，正文不走缓存）")
    p.add_argument("--pages", type=int, default=None,
                   help=f"最多翻几页总局检索结果，每页固定 {PAGE_SIZE} 条。"
                        f"不指定时按需自适应（连续 {IDLE_PAGE_LIMIT} 页无新条目"
                        f"即收尾，上限 {MAX_PAGES} 页）；显式指定则严格翻满")
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true",
                   help=f"启用清单缓存（TTL {CACHE_TTL}s）；正文仍现拉")
    p.add_argument("--no-cache", action="store_true", help="禁用缓存（默认）")
    p.add_argument("--cache-stats", action="store_true", help="查看缓存统计")
    p.add_argument("--cache-clear", action="store_true", help="清空缓存")
    args = p.parse_args()

    global _cache
    if args.cache:
        _cache = CacheManager(enabled=True, namespace="fgk")

    if args.cache_stats:
        print(json.dumps({"cache": _cache.stats()}, ensure_ascii=False, indent=2))
        return
    if args.cache_clear:
        _cache.clear()
        print("Cache cleared.")
        return

    if not args.keyword:
        p.error("需要检索词（仅 --cache-stats / --cache-clear 可省略）")

    max_pages = args.pages if args.pages is not None else MAX_PAGES
    adaptive = args.pages is None      # 显式给了页数就别自作主张提前收尾

    result = search_fgk(args.keyword, size=args.size, with_body=args.body,
                        max_pages=max_pages, adaptive=adaptive)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    cache_tag = ""
    if result.get("_from_cache"):
        age = result.get("_cache_age_s")
        cache_tag = f" [清单缓存{' ' + str(int(age)) + 's 前' if age is not None else ''}]"
    print(f"🔍 税务总局法规库 \"{args.keyword}\" | {result['searched_at']}{cache_tag}")
    if result.get("_error"):
        print(f"⚠️  {result['_error']}")
    early = (f"（连续 {IDLE_PAGE_LIMIT} 页无新法规库条目，已自适应收尾）"
             if result.get("stopped_early") else "")
    print(f"总局检索命中 {result['total_hits']} 条，翻了 {result['pages_scanned']} 页{early}，"
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
        if item.get("media_only"):
            print("     🎬 该条正文是视频/图片，没有文字可引（不是取失败，重试也无用）")
        elif item.get("body_error"):
            print(f"     ⚠️ {item['body_error']}")
        print()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
