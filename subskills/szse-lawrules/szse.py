#!/usr/bin/env python3
"""
深圳证券交易所法律规则 (www.szse.cn/www/lawrules/) 检索工具 —— 查询式子技能。

对标 maodocs / chenyiwei-bbs / 税屋：把深交所"法律规则"站点的分类目录与规则清单
建成一份本地索引，之后按关键词离线查询，命中给出「规则名 + 发布日期 + 原文链接 +
所属分类」。不调用任何模型、不消费任何额度；直连公开 HTTP。

站点渲染分两种（本机 2026-09-29 实测）：

  1. 标准库直连可枚举（SSR）——这些分类页把每条规则以
         <li>… <script>var curHref='…'; var curTitle='…'</script> …
         <span class="time">YYYY-MM-DD</span> …</li>
     内联进 HTML，翻页用 index.html → index_1.html → index_2.html…。
     覆盖：法律 / 行政法规 / 司法解释 / 证监会规章 / 适用指引 / 规范性文件 /
     废止公告。这类是 build 的默认通道。

  2. 运行时模板渲染（需浏览器）——交易所"本所业务规则"（rule/stock、rule/bond…）
     的页面只挂 <template> 与 _.each(data,…)，data 由前端二次拉取，直连只返回空壳。
     这类走母技能 scripts/tax_browser.py 的 BrowserSession.read_html()（引擎既有能力，
     不新增依赖）。playwright 不在本机时 build 会跳过这些通道并在回显里写清原因。

另外：lawrules/index.html 落地页把"最新业务规则"122 条以内联 curTitle/curHref 混排
SSR 出来，stdlib 可直接取，作为交易所自律规则的快照索引（--snapshot）。

Usage:
  python szse.py categories
  python szse.py build                 # 建 SSR 通道索引到 szse_index.json
  python szse.py build --browser       # 额外用浏览器补"本所业务规则"通道（需 playwright）
  python szse.py build --snapshot      # 额外把落地页最新规则并入索引
  python szse.py query 内幕交易        # 离线查索引
  python szse.py query 回购 --channel bond
  python szse.py fetch http://www.szse.cn/www/lawrules/…/tXXXX_YYYY.html
"""

import argparse
import html as _html
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://www.szse.cn/www/lawrules/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 25
PAGE_GAP = 0.4            # 翻页礼貌间隔
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "szse_index.json")

# 分类通道表：alias -> (中文名, 相对路径, 渲染类型)
# 渲染类型 "static"=标准库可枚举；"browser"=运行时模板，需 tax_browser。
# 路径与中文名为站方左侧导航原有目录，本项目不另起名。
CATEGORIES = {
    # —— SSR，标准库可枚举 ——
    "law":        ("法律",           "rules/law/index.html",                 "static"),
    "regulation": ("行政法规",       "rules/regu/index.html",                "static"),
    "judicial":   ("司法解释",       "rules/judicial/index.html",            "static"),
    "csrc_rule":  ("证监会规章",     "csrcrules/command/index.html",         "static"),
    "csrc_guide": ("适用指引",       "csrcrules/guide/index.html",           "static"),
    "csrc_file":  ("规范性文件",     "csrcrules/notice/index.html",          "static"),
    "repeal":     ("废止公告",       "rule/repeal/announcement/index.html",  "static"),
    # —— 本所业务规则：运行时模板，需浏览器 ——
    "general":    ("本所通用规则",   "rule/all/index.html",                  "browser"),
    "stock":      ("股票业务规则",   "rule/stock/trade/index.html",          "browser"),
    "issue":      ("发行上市规则",   "rule/stock/issue/index.html",          "browser"),
    "review":     ("审核规则",       "rule/stock/audit/index.html",          "browser"),
    "supervise":  ("持续监管规则",   "rule/stock/supervision/currency/index.html", "browser"),
    "bond":       ("债券业务规则",   "rule/bond/bonds/trade/index.html",     "browser"),
    "fund":       ("基金业务规则",   "rule/fund/trade/index.html",           "browser"),
    "option":     ("衍生品业务规则", "rule/derivative/index.html",           "browser"),
    "reits":      ("REITs规则",      "rule/reits/index.html",                "browser"),
    "member":     ("会员管理规则",   "rule/memberty/index.html",             "browser"),
    "trade":      ("交易通则",       "rule/trade/current/index.html",        "browser"),
}

_TAG = re.compile(r"<[^>]+>")
# 每条 <li> 里：curHref 只出现一次；curTitle 有注释行(//var)与生效行，生效行无前导 //
_LI_SPLIT = re.compile(r"<li>", re.S)
_HREF = re.compile(r"var\s+curHref\s*=\s*'([^']*)'")
_TITLE_ACTIVE = re.compile(r"^\s*var\s+curTitle\s*=\s*'([^']*)'", re.M)
_TIME = re.compile(r'class="time">\s*([0-9]{4}-[0-9]{2}-[0-9]{2})')
_PAGES = re.compile(r"共\s*(\d+)\s*页")


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        return r.read().decode("utf-8", errors="replace")


def _get_retry(url: str, tries: int = 4) -> str:
    """502/连接抖动重试；SZSE 偶发 502 是站点侧抖动，不代表失败。"""
    last = None
    for i in range(tries):
        try:
            return _get(url)
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _abs(href: str) -> str:
    return urllib.parse.urljoin(BASE, href)


def _parse_static_page(page: str) -> list:
    """从内联 curHref/curTitle/time 的页面里提取 [{title,date,url}]。"""
    out = []
    for blk in _LI_SPLIT.split(page):
        if "curCmsDocType" not in blk:
            continue
        mh = _HREF.search(blk)
        mt = _TITLE_ACTIVE.findall(blk)
        md = _TIME.search(blk)
        if not mh or not mt:
            continue
        out.append({
            "title": _html.unescape(mt[-1]).strip(),
            "date": md.group(1) if md else "",
            "url": _abs(mh.group(1)),
        })
    return out


def _enumerate_static(rel: str, max_pages: int) -> list:
    """index.html → index_N.html 逐页取，直到 404 或空页。"""
    stem = rel.rsplit("/", 1)[0]
    items, seen = [], set()
    for pg in range(max_pages + 1):
        name = "index.html" if pg == 0 else f"index_{pg}.html"
        url = _abs(f"{stem}/{name}")
        try:
            page = _get_retry(url, tries=2)
        except Exception:
            break
        got = _parse_static_page(page)
        if not got:
            break
        for it in got:
            if it["url"] not in seen:
                seen.add(it["url"])
                items.append(it)
        tot = _PAGES.search(page)
        if tot and pg + 1 >= int(tot.group(1)):
            break
        time.sleep(PAGE_GAP)
    return items


def _strip_comments(page: str) -> str:
    return re.sub(r"<!--.*?-->", "", page, flags=re.S)


def _parse_rendered(page: str) -> list:
    """解析浏览器渲染后的页面：每条规则是 <a class=…art-list-link… href title>
    锚点，配同一条目里的 <span class="time">日期。模板注释残留要先剔掉，
    否则把注释里的占位锚点也当命中。"""
    clean = _strip_comments(page)
    out = []
    # 以 art-list-link 锚点为锚，向后取最近的 time 作为发布日期
    for m in re.finditer(
            r'<a class="[^"]*art-list-link[^"]*"\s+href="([^"]+)"\s+title="([^"]*)"',
            clean):
        tail = clean[m.end():m.end() + 400]
        md = re.search(r'class="time">\s*([0-9]{4}-[0-9]{2}-[0-9]{2})', tail)
        out.append({
            "title": _html.unescape(m.group(2)).strip(),
            "date": md.group(1) if md else "",
            "url": m.group(1),
        })
    return out


def _browser_session():
    """惰性起母技能浏览器会话；playwright 缺失时抛 ImportError，由调用方分诊。"""
    scripts_dir = os.path.join(os.path.dirname(HERE), "..", "scripts")
    sys.path.insert(0, os.path.abspath(scripts_dir))
    from tax_browser import BrowserSession  # noqa: E402
    return BrowserSession(warm_url=BASE)


def _enumerate_browser(rel: str, sess, max_pages: int) -> list:
    """浏览器逐页渲染并解析 art-list-link 锚点（index.html → index_N.html）。"""
    stem = rel.rsplit("/", 1)[0]
    items, seen = [], set()
    for pg in range(max_pages + 1):
        name = "index.html" if pg == 0 else f"index_{pg}.html"
        url = _abs(f"{stem}/{name}")
        try:
            page = sess.read_html(url)
        except Exception:
            break
        got = _parse_rendered(page)
        if not got:
            break
        new = 0
        for it in got:
            if it["url"] not in seen:
                seen.add(it["url"])
                it["_render"] = "browser"
                items.append(it)
                new += 1
        if new == 0:
            break
        time.sleep(PAGE_GAP)
    return items


def build(use_browser: bool, snapshot: bool, max_pages: int, only: str) -> dict:
    """建索引。SSR 通道恒建；本所业务规则通道在 --browser 时补建。"""
    index = {"source": BASE, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "channels": {}}
    notes = []
    aliases = [only] if only else list(CATEGORIES)
    browser_sess = None
    browser_failed = False

    for alias in aliases:
        cn, rel, kind = CATEGORIES[alias]
        if kind == "static":
            try:
                items = _enumerate_static(rel, max_pages)
                for it in items:
                    it["channel"] = alias
                    it["channel_cn"] = cn
                index["channels"][alias] = {"name": cn, "render": "static",
                                            "count": len(items), "items": items}
                notes.append(f"{alias}({cn}) static {len(items)} 条")
            except Exception as e:
                notes.append(f"{alias} 建索引失败：{str(e).splitlines()[0][:80]}")
        elif kind == "browser":
            if not use_browser:
                continue
            if browser_failed:
                index["channels"][alias] = {"name": cn, "render": "browser",
                                            "count": 0, "items": [],
                                            "_skipped": "playwright/浏览器不可用"}
                continue
            try:
                if browser_sess is None:
                    browser_sess = _browser_session()
                items = _enumerate_browser(rel, browser_sess, max_pages)
                for it in items:
                    it["channel"] = alias
                    it["channel_cn"] = cn
                index["channels"][alias] = {"name": cn, "render": "browser",
                                            "count": len(items), "items": items}
                notes.append(f"{alias}({cn}) browser {len(items)} 条")
            except ImportError:
                browser_failed = True
                index["channels"][alias] = {"name": cn, "render": "browser",
                                            "count": 0, "items": [],
                                            "_skipped": "playwright 未安装"}
                notes.append("浏览器通道不可用：playwright 未安装（本机 2026-09-29 属实）")
            except Exception as e:
                notes.append(f"{alias} 浏览器建索引失败：{str(e).splitlines()[0][:80]}")

    if snapshot:
        try:
            page = _get_retry(BASE)
            items = _parse_static_page(page)
            for it in items:
                it["channel"] = "snapshot"
                it["channel_cn"] = "落地页最新规则快照"
            index["channels"]["snapshot"] = {"name": "落地页最新规则快照",
                                             "render": "static", "count": len(items),
                                             "items": items}
            notes.append(f"snapshot {len(items)} 条")
        except Exception as e:
            notes.append(f"snapshot 失败：{str(e).splitlines()[0][:80]}")

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    total = sum(c["count"] for c in index["channels"].values())
    return {"index_path": INDEX_PATH, "total": total,
            "channels": {a: index["channels"][a]["count"] for a in index["channels"]},
            "notes": notes}


def load_index() -> dict:
    if not os.path.exists(INDEX_PATH):
        raise SystemExit("索引不存在，先运行：python szse.py build")
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def query(keyword: str, channel: str, limit: int) -> dict:
    idx = load_index()
    results = []
    chans = idx["channels"]
    scope = [channel] if channel else list(chans)
    for alias in scope:
        ch = chans.get(alias)
        if not ch:
            continue
        for it in ch["items"]:
            if keyword in it["title"]:
                results.append({"title": it["title"], "date": it["date"],
                                "url": it["url"], "channel": alias,
                                "channel_cn": it.get("channel_cn", ch["name"])})
    results.sort(key=lambda x: x["date"], reverse=True)
    return {"keyword": keyword, "channel": channel or "全部",
            "total": len(results), "results": results[:limit],
            "truncated": len(results) > limit}


def fetch(target: str) -> dict:
    """取某一规则详情页正文（SSR 直连即可；PDF 只给链接与提示）。"""
    url = target if target.startswith("http") else _abs(target)
    if url.lower().endswith(".pdf"):
        return {"url": url, "type": "pdf",
                "content": "", "_note": "该条为 PDF 附件，正文需下载后另行解析"}
    try:
        page = _get_retry(url)
    except Exception as e:
        return {"url": url, "_error": f"取回失败：{str(e).splitlines()[0][:100]}"}
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    title = _html.unescape(m.group(1)).strip() if m else ""
    body = ""
    mc = re.search(r"(?is)<div[^>]+class=\"[^\"]*(?:content|article)[^\"]*\"[^>]*>(.*?)</div>", page)
    if mc:
        seg = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", mc.group(1))
        seg = _TAG.sub("\n", seg)
        seg = _html.unescape(seg)
        body = "\n".join(ln.strip() for ln in seg.splitlines() if ln.strip())
    return {"url": url, "title": title, "content": body, "chars": len(body)}


def categories() -> list:
    return [{"alias": a, "name": v[0], "path": v[1], "render": v[2]}
            for a, v in sorted(CATEGORIES.items())]


def main():
    try:
        for s in (sys.stdout, sys.stderr):
            s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="深交所法律规则 查询式子技能")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("categories")
    b = sub.add_parser("build")
    b.add_argument("--browser", action="store_true", help="补建本所业务规则通道（需 playwright）")
    b.add_argument("--snapshot", action="store_true", help="并入落地页最新规则快照")
    b.add_argument("--max-pages", type=int, default=40)
    b.add_argument("--channel", default="", help="只建某一个通道")
    q = sub.add_parser("query")
    q.add_argument("keyword")
    q.add_argument("--channel", default="")
    q.add_argument("--limit", type=int, default=20)
    f = sub.add_parser("fetch")
    f.add_argument("target")
    a = ap.parse_args()

    if a.cmd == "categories":
        print(json.dumps(categories(), ensure_ascii=False, indent=2))
    elif a.cmd == "build":
        print(json.dumps(build(a.browser, a.snapshot, a.max_pages, a.channel),
                         ensure_ascii=False, indent=2))
    elif a.cmd == "query":
        print(json.dumps(query(a.keyword, a.channel, a.limit), ensure_ascii=False, indent=2))
    elif a.cmd == "fetch":
        print(json.dumps(fetch(a.target), ensure_ascii=False, indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
