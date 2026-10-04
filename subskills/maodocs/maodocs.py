#!/usr/bin/env python3
"""
审计文库 (docs.maoyanqing.com) 取文工具 —— 纯标准库，不调模型、不装浏览器。

为什么不用税屋那套：税屋前置阿里云 WAF，正文要点浏览器实跑 JS 才放行；
MaoDocs 是 VuePress 静态站、EdgeOne 托管，直连 curl 即返 200 带全文，
两个准则页实测 WAF 挑战位均为 False。所以这里没有过 WAF、没有 Jina 兜底，
只有两种取法：

  1. search —— 抓分类"索引页"，其 HTML 里把该分类全部子条目的
     「全名 + .html 链接 + 文号」都 SSR 出来了，按关键词匹配标题即命中，
     不必依赖 360、也不必下载整份 sitemap 再逐页翻。
  2. fetch  —— 取某一页正文，切 vp-page-title 到 vp-page-meta 之间的区间。

sitemap.xml（约 1249 页，逐页 lastmod，changefreq=daily）留作 categories
列全域分类，以及需要全量枚举时用。

Usage:
  python maodocs.py categories
  python maodocs.py search cas "长期股权投资"
  python maodocs.py search csa "舞弊"
  python maodocs.py fetch /accounting/ent/cas/02.html
  python maodocs.py fetch https://docs.maoyanqing.com/auditing/csa/1141.html
"""

import html
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request

BASE = "https://docs.maoyanqing.com"
TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 分类索引页前缀：一级是域（accounting/auditing/control/appraisal/securities），
# 二级是系列缩写。search 用别名命中这个表里的完整前缀。
# 命名依据是站方自己的目录，不是本项目发明的。
CATEGORIES = {
    # 会计
    "cas":   "accounting/ent/cas",     # 企业会计准则（具体准则）
    "casg":  "accounting/ent/casg",    # 企业会计准则应用指南
    "casi":  "accounting/ent/casi",    # 企业会计准则解释
    "gas":   "accounting/gov/gas",     # 政府会计准则
    "cass":  "accounting/se/cass",     # 小企业会计准则（财会〔2011〕17号）
    # 审计
    "csa":   "auditing/csa",           # 中国注册会计师审计准则
    "csag":  "auditing/csag",          # 审计准则应用指南
    "csaq":  "auditing/csaq",          # 审计准则问题解答
    "csce":  "auditing/csce",          # 注册会计师法律责任/执业
    # 内控
    "icn":   "control/ent/icn",        # 企业内部控制规范体系
    # 评估
    "aas":   "appraisal/aas",          # 资产评估准则
    # 证券监管
    "garr":  "securities/garr",        # 监管规则适用指引（会计类/减持等）
}

_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(?:p|div|li|tr|h[1-6]|blockquote)\s*>|<br\s*/?>", re.I)
# 索引页里的子条目链接：<a href="/域/系列/NN.html" ...>全名</a>
_LINK = re.compile(r'<a[^>]*href="(/[^"]+\.html)"[^>]*>(.*?)</a>', re.S | re.I)


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        return r.read().decode("utf-8", errors="replace")


def _to_text(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style|nav|aside)\b.*?</\1>", "", fragment)
    fragment = _BLOCK_END.sub("\n", fragment)
    fragment = _TAG.sub("", fragment)
    fragment = html.unescape(fragment)
    lines = [ln.strip() for ln in fragment.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def categories() -> list:
    """列出已收录的分类别名与完整前缀，供 search 用。"""
    return [{"alias": a, "path": p, "url": f"{BASE}/{p}/"}
            for a, p in sorted(CATEGORIES.items())]


def search(alias: str, keyword: str, size: int = 10) -> dict:
    """在某个分类索引页里按关键词匹配条目标题。

    Args:
        alias: CATEGORIES 的键（cas/csa/gas/aas/icn/garr ...）
        keyword: 要在标题里找的词，子串匹配（中文不需要分词）
        size: 返回上限

    Returns:
        {"alias","path","keyword","total","results":[{"title","url","href"}]}
        匹配不到时 results 为空、total=0；索引页取不回时带 _error。
    """
    path = CATEGORIES.get(alias)
    if not path:
        return {"alias": alias, "_error":
                f"未知分类 {alias}；可选：{', '.join(sorted(CATEGORIES))}"}
    idx_url = f"{BASE}/{path}/"
    try:
        page = _get(idx_url)
    except Exception as e:
        return {"alias": alias, "path": path, "keyword": keyword,
                "total": 0, "results": [], "_error": f"索引页取回失败：{e}"}

    seen, results = set(), []
    native = "/" + path + "/"
    for href, raw in _LINK.findall(page):
        # 只认本分类前缀下的条目：索引页里还挂着站点全局导航（会计法/证券法
        # 等），不滤掉的话"准则""法"这类词会把导航项当命中带回。
        if not href.startswith(native):
            continue
        title = _to_text(raw).replace("　", " ").strip()
        if not title or href in seen:
            continue
        seen.add(href)
        if keyword in title:
            results.append({"title": title, "href": href, "url": BASE + href})
            if len(results) >= size:
                break
    return {"alias": alias, "path": path, "keyword": keyword,
            "total": len(results), "results": results}


def _title_of(page: str) -> str:
    m = re.search(r"<title>(.*?)\s*\|\s*审计文库", page, re.S)
    if m:
        return _to_text(m.group(1)).strip()
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    return _to_text(m.group(1)).strip() if m else ""


def fetch(target: str) -> dict:
    """取一页正文。

    Args:
        target: 站内相对路径（/accounting/ent/cas/02.html）或完整 URL。

    Returns:
        {"url","title","content","chars"} 或带 _error。
    """
    url = target if target.startswith("http") else BASE + (
        target if target.startswith("/") else "/" + target)
    try:
        page = _get(url)
    except Exception as e:
        return {"url": url, "_error": f"取回失败：{e}"}

    # WAF 挑战位检测：与税屋不同，这里预期恒为 False，留着是给同步时报警——
    # 一旦站方将来上了前置防护，本工具要能在正文缺失时说清是拦截不是空页。
    if "arg1" in page and "renderData" in page:
        return {"url": url, "_error": "出现 WAF 挑战页（这一向不设前置防护，取到这一页说明是被拦而非空页）"}

    # 正文区间：从标题容器之后到 vp-page-meta（发文信息/字数）之前。
    # 先按 class 属性名定位，再把起点顶到那个 '>' 之后，避免把
    # vp-page-title"> 这段类名残片混进正文第一行。
    m1 = re.search(r'class="vp-page-title"', page)
    m2 = page.find("vp-page-meta")
    if m1 and m2 > m1.end():
        start = page.find(">", m1.end()) + 1
        seg = page[start:m2]
    else:
        m = re.search(r"(?is)<main.*?</main>", page)
        seg = m.group(0) if m else page
    content = _to_text(seg)
    return {"url": url, "title": _title_of(page), "content": content,
            "chars": len(content)}


def main():
    try:
        for s in (sys.stdout, sys.stderr):
            s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    argv = sys.argv[1:]
    cmd = argv[0] if argv else "help"
    if cmd == "categories":
        print(json.dumps(categories(), ensure_ascii=False, indent=2))
    elif cmd == "search" and len(argv) >= 3:
        size = int(argv[3]) if len(argv) > 3 else 10
        print(json.dumps(search(argv[1], argv[2], size), ensure_ascii=False, indent=2))
    elif cmd == "fetch" and len(argv) >= 2:
        print(json.dumps(fetch(argv[1]), ensure_ascii=False, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
