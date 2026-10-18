#!/usr/bin/env python3
"""
税屋 (www.shui5.cn) — 实务解读来源。

为什么需要这个模块：税屋的文章（专栏、实操指引、答疑）是税务总局公告与
NPC 法规库里查不到的实操层内容，是四源之外的必要补充。

税屋访问的两道门槛及本模块的处理：
  1. 检索：两条通道，覆盖的是两种排序，不是彼此的备份。
     - 360 的 site:shui5.cn（`tax_so360`）：不要凭据、不启浏览器，每行只有
       标题＋链接＋摘要，没有日期。
     - 站内原生检索（帝国 CMS 的 `POST /e/search/index.php`）：每行带日期、
       摘要与所属栏目，页面还给总条数，但要先过一次 WAF（约 6 秒）。
     默认 auto：先走 360，它被拦或 0 条时才换站内，避免每次检索都启浏览器。
     2026-10-18 实测同一个词「研发费用加计扣除」两路首屏按文章 ID 归一后交集 0 条。
     注：首页可见的搜索框提交到 zhannei.baidu.com/cse/site，那是百度站点搜索，
     本机 IP 上极不稳定（实测 4 次仅 1 次返回结果），不作为通道。
  2. 阅读：税屋前置阿里云 WAF，无放行 cookie 时所有页面（含 robots.txt、
     一个不存在的路径）都返回同一份 23,682 字节挑战页，页面里带 arg1，
     所以"请求成功"与"取到内容"在这个站是两件事。
     → 纯 HTTP 客户端按公开算法算 acw_sc__v2 也不放行（2026-09-27 实测
       5/5 被拦），因为挑战链里有浏览器行为特征，补不出来。
     → 改用真浏览器跑一次挑战（见 tax_browser），拿到放行 cookie 后转交
       requests 逐篇读正文。实测浏览器过挑战 5.9~7.0 秒，之后每篇 0.2 秒级。

本模块的正文抓取顺序：
  浏览器过 WAF 拿 cookie → requests 连读（首选）
  → 纯 HTTP 直连解挑战（浏览器不可用时）
  → Jina Reader（最后兜底）

长文分页：文章页正文里有 <div class="pagebreak">「本文章更多内容」，
按本篇 ID 给出各续页地址（`<ID>$2.html`）。本模块按这些地址逐页读完再拼接，
不靠"往后一页是不是 404"试探——2026-10-18 实测只有三页的 article/42/70138.html
取 $4、$5 都返回 200 与同一份 571 字内容，页码列表反而每次都多列一项。
读到与已读正文重复的那一页即停：那说明页码已经越界，站点把请求钳回了已有内容。

Usage:
  python tax_shui5.py "研发费用加计扣除" --size 5
  python tax_shui5.py "高新技术企业认定" --size 5 --read   # 连正文一起取
  python tax_shui5.py "增值税起征点" --size 3 --json
  python tax_shui5.py "留抵退税" --channel native --size 10   # 只走站内检索
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
import urllib3

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from tax_so360 import so360_search

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SHUI5_SITE = "shui5.cn"
SHUI5_BASE = "https://www.shui5.cn/"
JINA_READER = "https://r.jina.ai/"
# 一次检索后逐篇取正文，360/搜狗/税屋都对高频请求敏感
READ_INTERVAL = 2.0
TIMEOUT_READ = 30

# 站内原生检索（帝国 CMS）。表单字段是结果页自己带出来的，2026-10-18 实测照抄可用。
SEARCH_FORM = SHUI5_BASE + "e/search/index.php"
SEARCH_RESULT = SHUI5_BASE + "e/search/result/index.php"
SEARCH_FIELDS = {"tbname": "article", "show": "title,keyboard,smalltext",
                 "tempid": "2", "Submit": "站内搜税"}
# 站点在提示页里明写的检索间隔，留 1 秒余量
SEARCH_MIN_GAP = 3.0
SEARCH_ROWS_PER_PAGE = 30
SEARCH_MAX_PAGES = 6
# 续页上限：正文页码列表按篇给出，正常长文不到这个数；上限只防站点页码异常时把连读拖长
MAX_CONTINUATION_PAGES = 12
# 提示页的两种文案，实测各对应一件事：这一轮没命中 vs 请求太密
NO_RESULT_MARK = "没有搜索到相关的内容"
RATE_LIMIT_MARK = "系统限制的搜索时间间隔"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 税屋前置阿里云 WAF：第一次请求一定返回 23,682 字节的挑战页，页面里
# <textarea id="renderData"> 带 arg1（40 位十六进制），浏览器据此算出
# acw_sc__v2 再请求才放行。
#
# 算法是公开的：固定置换表重排 + 固定掩码逐字节异或，置换表与掩码是该
# 挑战的常量，不随 arg1 变。但算对 cookie 服务端仍不认（实测 5/5 被拦），
# 因为服务端还校验挑战链里的浏览器行为痕迹。留这条路是为了浏览器不可用
# 时还有个不白跑的尝试，同时它也是判断挑战页 arg1 是否变化的取样点。
_UNBOX_POS = [15, 35, 29, 24, 33, 16, 1, 38, 10, 9, 19, 31, 40, 27, 22, 23,
              25, 13, 6, 11, 39, 18, 20, 8, 14, 21, 32, 26, 2, 30, 7, 4,
              17, 5, 3, 28, 34, 37, 12, 36]
_XOR_MASK = "3000176000856006061501533003690027800375"
_ARG1_RE = re.compile(r"arg1\s*=\s*'([0-9a-fA-F]+)'")

# 正文容器的收口。2026-10-18 抽样 12 篇实测：正文之后第一个兄弟容器是
# div.left2e（点赞/微信/微博/扫码分享块），旧边界里没有它，所以每一篇的正文
# 尾部都混入「点赞（）微信 微博 × 用微信扫码二维码 分享至好友和朋友圈」；
# left2b 与 blank20 排在它之后，只作站点改版时的第二、三道保险。
_ARC_RE = re.compile(r'<div class="arcContent"[^>]*>(.*?)'
                     r'(?=<div class="(?:left2e|bot-share|left2b|blank20)|\Z)', re.S | re.I)
_META_RES_RE = re.compile(r'<div class="articleResource">(.*?)</div>', re.S | re.I)
_META_DES_RE = re.compile(r'<div class="articleDes">(.*?)</div>', re.S | re.I)
_TIME_RE = re.compile(r"时间：\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")
_TAG_RE = re.compile(r"<[^>]+>")
_PARA_RE = re.compile(r"</(?:p|div|li|tr|h[1-6])\s*>|<br\s*/?>", re.I)

# 续页：只用本篇 ID 出现在 pagebreak 里的地址，不按"页面上有没有 $N 形态的链接"数——
# 相关阅读区混着别人文章的续页链接，按形态数会把单页文章也判成分页文章。
_ARTICLE_ID_RE = re.compile(r"/article/[^/]+/(\d+)(?:\$\d+)?\.html")
_PAGE_LINK_RE = re.compile(r'href="([^"]*?/(\d+)\$(\d+)\.html)"', re.I)

# 站内检索结果页的字段落点（2026-10-18 实测行容器 div.xwt2 内五处）
_ROW_SPLIT_RE = re.compile(r'<div class="xwt2"[^>]*>', re.I)
_TOTAL_RE = re.compile(r"(\d+)\s*条关于")
_ROW_A_RE = re.compile(r'<div class="xwt2_a">\s*<a\s+href="([^"]+)"[^>]*?title="([^"]*)"',
                       re.I | re.S)
_ROW_C_RE = re.compile(r'<div class="xwt2_c">\s*<p class="p1">(.*?)</p>', re.I | re.S)
_ROW_P3_RE = re.compile(r'<p class="p3">([^<]*)</p>', re.I)
_ROW_P4_RE = re.compile(r'<p class="p4">.*?<a[^>]*>(.*?)</a>', re.I | re.S)
_SEARCHID_RE = re.compile(r"searchid=(\d+)")


def _to_text(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", fragment)
    fragment = _PARA_RE.sub("\n", fragment)
    fragment = _TAG_RE.sub("", fragment)
    import html as htmllib
    fragment = htmllib.unescape(fragment)
    lines = [ln.replace("　", " ").strip() for ln in fragment.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _solve_waf_challenge(arg1: str) -> str:
    """按阿里云 WAF 的公开算法由 arg1 算出 acw_sc__v2。

    先按 _UNBOX_POS 重排 40 位十六进制串，再与 _XOR_MASK 逐字节异或。
    """
    reordered = "".join(arg1[i - 1] for i in _UNBOX_POS if i - 1 < len(arg1))
    return "".join(
        "%02x" % (int(reordered[i:i + 2], 16) ^ int(_XOR_MASK[i:i + 2], 16))
        for i in range(0, len(reordered) - 1, 2)
    )


def _is_waf_page(page: str) -> bool:
    return "arg1" in page and "renderData" in page


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    return s


def _parse_article(page: str, url: str) -> dict:
    """从已放行的 HTML 里切出标题、日期、摘要、正文。"""
    out = {"url": url}
    if _is_waf_page(page):
        out["_error"] = ("WAF 挑战未通过：浏览器已过挑战但仍被拦，"
                         "多半是访问过于频繁，稍后重试")
        return out
    m = _ARC_RE.search(page)
    if not m:
        out["_error"] = "未匹配到 arcContent 正文容器"
        return out
    h1 = re.search(r"<h1>(.*?)</h1>", page, re.S)
    out["title"] = _to_text(h1.group(1)) if h1 else ""
    res = _META_RES_RE.search(page)
    if res:
        t = _TIME_RE.search(res.group(1))
        out["date"] = t.group(1) if t else ""
    des = _META_DES_RE.search(page)
    if des:
        out["summary"] = _to_text(des.group(1))
    out["content"] = _to_text(m.group(1))
    if not out["content"]:
        out["_error"] = "正文容器为空"
    return out


def _browser_session(cookies: dict) -> requests.Session:
    """用浏览器导出的放行 cookie 装一个 requests 会话。"""
    s = _make_session()
    s.headers["Referer"] = SHUI5_BASE
    for name, value in cookies.items():
        s.cookies.set(name, value, domain=SHUI5_SITE, path="/")
    return s


def _pass_waf(warm_url: str = "") -> tuple[requests.Session | None, str]:
    """过一次 WAF，交回可直接连读的会话与这次是怎么过的。

    Returns:
        (会话, 说明)。浏览器不可用时会话为 None，说明里带失败原因；
        调用方据此退回纯 HTTP 那条路（多数会被拦）。
    """
    try:
        from tax_browser import BrowserSession
        b = BrowserSession(warm_url=warm_url or SHUI5_BASE)
        b.warm()
        s = _browser_session(b.cookies_for(SHUI5_SITE))
        how = f"浏览器 {b.channel} 过 WAF 后用 HTTP 连读"
        b.close()
        return s, how
    except Exception as e:                              # 没装浏览器/启动失败
        return None, f"无浏览器，改纯 HTTP 直连（{str(e).splitlines()[0][:70]}）"


def _read_page(session: requests.Session, url: str) -> tuple[str, str]:
    """取一页 HTML，返回 (正文, 错误)。错误非空时正文不可用。

    没有凭据时任何地址都回同一份挑战页且 HTTP 200，所以判"取到内容"要看页面
    是不是挑战页，不能只看状态码。
    """
    r = session.get(url, timeout=TIMEOUT_READ)
    page = r.content.decode("utf-8", errors="replace")
    if r.status_code != 200:
        return "", f"HTTP {r.status_code}"
    if _is_waf_page(page):
        m = _ARG1_RE.search(page)
        if m:
            session.cookies.set("acw_sc__v2", _solve_waf_challenge(m.group(1)))
            r = session.get(url, timeout=TIMEOUT_READ)
            page = r.content.decode("utf-8", errors="replace")
    if _is_waf_page(page):
        return "", ("WAF 挑战未通过：acw_sc__v2 已按公开算法算出，"
                    "但服务端仍要求浏览器执行完整 JS 挑战链")
    return page, ""


def _article_id(url: str) -> str:
    m = _ARTICLE_ID_RE.search(url)
    return m.group(1) if m else ""


def _page_url(url: str, n: int) -> str:
    """本篇第 n 页的地址：<ID>.html / <ID>$2.html。"""
    stem = re.sub(r"(\$\d+)?\.html$", "", url)
    return url if n <= 1 else f"{stem}${n}.html"


def _own_pages(html: str, article_id: str) -> set:
    """这一页里指向本篇续页的页码。

    全页扫描而不切 pagebreak 容器：那个 div 里还可能套别的标记，切早了会把页码
    链接切掉，宁可读一页多余的。归属靠本篇 ID 判——相关阅读区混着别人文章的 `$N`
    链接，按链接形态数会把单页文章判成分页文章。
    """
    if not article_id:
        return set()
    return {int(n) for _, aid, n in _PAGE_LINK_RE.findall(html) if aid == article_id}


def fetch_shui5(url: str, session: requests.Session | None = None,
                page_interval: float = READ_INTERVAL,
                max_pages: int = MAX_CONTINUATION_PAGES) -> dict:
    """
    取一篇文章正文，长文按本篇 pagebreak 给的续页读到末页。

    Args:
        url: 税屋文章地址
        session: 已过 WAF 的会话。传了就直接用（多篇连读时省掉重复握手），
                 传空则自建一个并自行解挑战。
        page_interval: 续页之间的间隔秒数，站点对高频请求敏感
        max_pages: 单篇最多读几页

    Returns:
        {"url","title"?,"date"?,"summary"?,"content"?,"pages",
         "_pages_error"?,"_error"?}
        pages 是实际读到的页数；某一页取不到时正文停在已读部分，
        原因写在 _pages_error，不静默当成一整篇没有。
    """
    own = session is None
    if own:
        session = _make_session()
    try:
        page, err = _read_page(session, url)
        if err:
            return {"url": url, "_error": err}
        art = _parse_article(page, url)
        if art.get("_error"):
            return art

        aid = _article_id(url)
        read = {1}
        todo = sorted(_own_pages(page, aid))
        extra = []
        texts = {art["content"]}
        while todo and len(read) < max_pages:
            n = todo.pop(0)
            time.sleep(page_interval)
            nxt_url = _page_url(url, n)
            page2, err2 = _read_page(session, nxt_url)
            if err2:
                art["_pages_error"] = f"第 {n} 页取不到（{err2}），正文止于第 {max(read)} 页"
                break
            a2 = _parse_article(page2, nxt_url)
            if a2.get("_error"):
                art["_pages_error"] = f"第 {n} 页取不到（{a2['_error']}），正文止于第 {max(read)} 页"
                break
            if a2["content"] in texts:
                # 页码越界时站点不报 404，而是钳回一份已有正文（实测 $4、$5 同为 571 字
                # 且 sha8 一致）。文本与已读页重合即到底，这一页不计页数、不拼正文。
                break
            read.add(n)
            texts.add(a2["content"])
            extra.append(a2["content"])
            # 页码列表可能是窗口式的（只列相邻几页），读一页就把新出现的页码并进来
            todo = sorted(set(todo) | (_own_pages(page2, aid) - read - set(todo)))
        if extra:
            art["content"] = "\n".join([art["content"], *extra])
        art["pages"] = len(read)
        return art
    except requests.RequestException as e:
        return {"url": url, "_error": f"请求失败：{e}"}
    finally:
        if own:
            session.close()


def _row_dict(title: str, url: str, date: str, snippet: str, column: str = "") -> dict:
    """两条检索通道共用的行形状，保证下游（去重、排序、界面）不分辨来源。"""
    row = {
        "title": title,
        "url": url,
        "date": date,
        "snippet": snippet[:200],
        "source": "税屋 (shui5.cn)",
        "source_label": "实务解读",
    }
    if column:
        row["column"] = column
    return row


def _search_rows(page: str) -> list:
    """解站内结果页的 div.xwt2 行：标题、链接、日期、摘要、所属栏目。

    标题取 a 的 title 属性（可见文本被检索词高亮标签切开），高亮标签一律剥掉。
    """
    rows = []
    for chunk in _ROW_SPLIT_RE.split(page)[1:]:
        a = _ROW_A_RE.search(chunk)
        if not a:
            continue
        url = a.group(1)
        if SHUI5_SITE not in url:
            continue
        c = _ROW_C_RE.search(chunk)
        p3 = _ROW_P3_RE.search(chunk)
        p4 = _ROW_P4_RE.search(chunk)
        rows.append(_row_dict(
            title=_to_text(a.group(2)),
            url=url,
            date=(p3.group(1).strip() if p3 else ""),
            snippet=_to_text(c.group(1)) if c else "",
            column=_to_text(p4.group(1)) if p4 else "",
        ))
    return rows


def search_native(keyword: str, size: int = 5, session: requests.Session | None = None,
                  gap: float = SEARCH_MIN_GAP) -> dict:
    """站内原生检索：POST 检索表单 → 302 落点 → 按 page 从 0 起翻页。

    这一路要一份放行 cookie（约 6 秒过一次 WAF），换来的是 360 给不出的三件事：
    每行带日期、带所属栏目，页面还给这一词的总条数（形如「420条关于 留抵退税 的搜索结果」）。

    提示页有两种，按文案分而不是按字节分（两者实测只差 31 字节）：
      「没有搜索到相关的内容」= 这一轮站内检索没命中，是 0 条；
      「系统限制的搜索时间间隔为 2 秒」= 请求太密，等过间隔重发一次。

    Args:
        keyword: 检索词。多词之间站点按 AND 收，词一窄就整页变成提示页，短词更稳。
        size: 返回条数上限，超过一页时按 page 继续取
        session: 已过 WAF 的会话；传空则自己过一次
        gap: 翻页与重试之间的间隔秒数，不低于站点自己声明的 2 秒

    Returns:
        {"results","total","_status"}；total 是站点报的这一词总条数，取不到为 None。
        _status 说明这一路本轮走到哪一步，供上层归因，不当成命中与否的判据。
    """
    own = session is None
    if own:
        session, how = _pass_waf()
        if session is None:
            return {"results": [], "total": None, "_status": f"没走成：{how}"}
    try:
        data = dict(SEARCH_FIELDS, keyboard=keyword)
        r = session.post(SEARCH_FORM, data=data, timeout=TIMEOUT_READ,
                         verify=False, allow_redirects=True)
        page = r.content.decode("utf-8", errors="replace")
        if _is_waf_page(page):
            return {"results": [], "total": None,
                    "_status": "没走成：WAF 挑战未通过（会话凭据失效）"}
        if RATE_LIMIT_MARK in page:
            time.sleep(gap)
            r = session.post(SEARCH_FORM, data=data, timeout=TIMEOUT_READ,
                             verify=False, allow_redirects=True)
            page = r.content.decode("utf-8", errors="replace")
            if RATE_LIMIT_MARK in page:
                return {"results": [], "total": None,
                        "_status": "没走成：站点提示两次检索须间隔 2 秒以上，重发一次仍被限"}
        if NO_RESULT_MARK in page:
            return {"results": [], "total": 0,
                    "_status": "0 条：站点回「没有搜索到相关的内容」"}

        m = _SEARCHID_RE.search(r.url)
        if not m:
            return {"results": [], "total": None,
                    "_status": "没走成：检索落点里没有 searchid"}
        sid = m.group(1)
        total = None
        t = _TOTAL_RE.search(page)
        if t:
            total = int(t.group(1))
        rows = _search_rows(page)
        seen = {x["url"] for x in rows}
        # 落点页就是 page=0（实测两页首条与行数一致），继续从 1 翻到够数或到上限
        pn = 1
        while len(rows) < size and pn <= SEARCH_MAX_PAGES:
            time.sleep(gap)
            rp = session.get(f"{SEARCH_RESULT}?page={pn}&searchid={sid}",
                             timeout=TIMEOUT_READ, verify=False)
            nxt = _search_rows(rp.content.decode("utf-8", "replace"))
            if not nxt:
                break
            for x in nxt:
                if x["url"] not in seen:
                    seen.add(x["url"])
                    rows.append(x)
            pn += 1
        return {"results": rows[:size], "total": total,
                "_status": f"命中 {len(rows[:size])} 条"
                           + (f"（这一词站内共 {total} 条）" if total else "")}
    except requests.RequestException as e:
        return {"results": [], "total": None, "_status": f"没走成：请求失败：{e}"}
    finally:
        if own:
            session.close()


def search_shui5(keyword: str, size: int = 5, read_body: bool = False,
                 channel: str = "auto") -> dict:
    """
    在税屋检索政策实务文章。

    Args:
        keyword: 检索词
        size: 返回条数上限
        read_body: True 时再逐篇取正文（较慢，每篇之间等 READ_INTERVAL）
        channel: "auto"（默认）先走 360，它被拦或 0 条时才换站内原生检索；
                 "so360" 只走 360（不要凭据、不启浏览器）；
                 "native" 只走站内（每行带日期与栏目，但要过一次 WAF）。
                 两条通道覆盖的是不同排序：2026-10-18 实测同一个词首屏交集 0 条，
                 所以"这一路 0 条"不能读成"另一路也是 0 条"。

    Returns:
        {"keyword","total","results","searched_at","source","_channel",
         "_channels","_error"?}
        results 每项含 title/url/date/snippet（站内那一路另有 column）；
        read_body=True 时额外含 content 与 pages。
        _channels 逐条写明这一轮每条通道走到哪一步——上层据此归因，
        不必再猜"税屋 0 条"是税屋空的还是 360 连带的。
    """
    channels = {"360": "未走（指定通道）", "站内": "未走（指定通道）"}
    results: list = []
    used = ""

    if channel in ("auto", "so360"):
        found = so360_search(keyword, site=SHUI5_SITE, size=size)
        if found.get("_error"):
            channels["360"] = f"被拦：{found['_error']}"
        else:
            for item in found["results"]:
                results.append(_row_dict(
                    title=_strip_site_suffix(item["title"]) or item["title"],
                    url=item["url"], date="",
                    snippet=item.get("snippet", "")))
            channels["360"] = f"命中 {len(results)} 条" if results else "0 条"
        used = "360" if results else ""

    if channel == "auto" and not results:
        nat = search_native(keyword, size=size)
        channels["站内"] = nat["_status"]
        results = nat["results"]
        used = "站内" if results else ""
    elif channel == "native":
        nat = search_native(keyword, size=size)
        channels["站内"] = nat["_status"]
        results = nat["results"]
        used = "站内" if results else ""

    err = ""
    if not results:
        failed = {k: v for k, v in channels.items() if v.startswith("没走成") or v.startswith("被拦")}
        if failed:
            err = "两条检索通道都没取到：" + "；".join(f"{k} {v}" for k, v in failed.items())

    how = ""
    if read_body and results:
        # 一次过 WAF、连读全部，不逐篇重启浏览器
        arts = read_articles([r["url"] for r in results])
        for row, art in zip(results, arts):
            row["content"] = art.get("content", "")
            row["pages"] = art.get("pages", 1)
            if art.get("title"):
                row["title"] = _strip_site_suffix(art["title"])
            row["date"] = art.get("date", "") or row["date"]
            if art.get("summary"):
                row["summary"] = art["summary"]
            if art.get("_error"):
                row["_error"] = art["_error"]
            if art.get("_pages_error"):
                row["_pages_error"] = art["_pages_error"]
        how = arts[-1].get("_how", "") if arts else ""

    out = {
        "keyword": keyword,
        "total": len(results),
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "税屋 (shui5.cn)",
        "_channel": used,
        "_channels": channels,
        "_from_cache": False,
    }
    if err:
        out["_error"] = err
    if how:
        out["_how"] = how
    return out


def read_articles(urls: list, read_interval: float = READ_INTERVAL) -> list:
    """
    连续取多篇正文，WAF 只过一次。

    这是批量取正文的正确入口：浏览器预热 5.9~7.0 秒过挑战，之后每篇 0.2 秒级；
    逐篇重启浏览器是把 6 秒乘以篇数。浏览器不可用时退回纯 HTTP 逐篇握手（多数会被拦）。
    每篇内部还有续页时一并读到末页（见 fetch_shui5），续页之间同样留间隔。

    Args:
        urls: 税屋文章地址列表
        read_interval: 逐篇之间的间隔秒数，站点对高频请求敏感

    Returns:
        与 urls 等长的 dict 列表，每项含 url/content/title/date/summary/pages，
        取不到正文的项带 _error，整体取正文的方式记在 _how。
    """
    if not urls:
        return []

    session, how = _pass_waf(urls[0])

    out = []
    try:
        for i, u in enumerate(urls):
            out.append(fetch_shui5(u, session=session, page_interval=read_interval))
            if i < len(urls) - 1:
                time.sleep(read_interval)
    finally:
        if session is not None:
            session.close()
    for row in out:
        row["_how"] = how
    return out


def read_article(url: str) -> tuple[str, str]:
    """
    取单篇文章正文。

    2026-09-27 实测：浏览器过 WAF 后正文 3/3 取到，3,026~9,858 字；
    纯 HTTP 按公开算法算 cookie 直接读 0/3 被拦；Jina Reader 连接超时。

    Args:
        url: 税屋文章地址

    Returns:
        (正文, 错误信息)。成功时错误信息为空串。
    """
    row = read_articles([url], read_interval=0)[0]
    if row.get("content") and not row.get("_error"):
        header = "".join(
            f"{k}: {row[k]}\n" for k in ("title", "date", "summary") if row.get(k))
        return f"{header}\n{row['content']}", ""

    direct_err = row.get("_error", "正文为空")
    try:
        r = requests.get(JINA_READER + url, timeout=TIMEOUT_READ)
    except requests.RequestException as e:
        return "", f"直连失败({direct_err})；Jina 请求失败：{e}"

    if r.status_code != 200:
        return "", f"直连失败({direct_err})；Jina HTTP {r.status_code}"
    # Jina 的 403 是 Cloudflare 拦截页，正文里不会有 "Markdown Content:"
    if "Markdown Content:" not in r.text:
        return "", (f"直连失败({direct_err})；"
                    "Jina 未返回正文（可能被 Cloudflare 拦截）")
    return r.text, ""


def _strip_site_suffix(title: str) -> str:
    """去掉标题里的税屋站点后缀。

    360 与 Jina 给的标题都带同一句站点标语，但"税 屋"两字之间空白不一致，
    且 360 版本会掉字（实测 "税屋——" 变成 " 屋 "），所以先按标语正文切，
    再清掉残留的站名字符。
    """
    if not title:
        return ""
    # 先按标语正文切掉后半段，再剥掉站名及其两侧连接符
    head = re.split(r"第一时间传递财税政策法规", title)[0]
    head = re.split(r"[_|!！·、]*\s*[-—]{0,2}\s*税\s*屋\s*[-—]{0,2}\s*$", head)[0]
    head = re.split(r"[_|!！·、]*\s*[-—]{0,2}\s*屋\s*$", head)[0]
    return head.strip(" _-—!！|·、") or title.strip()


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="税屋 (shui5.cn) 实务解读检索")
    p.add_argument("keyword", help="检索词")
    p.add_argument("--size", type=int, default=5)
    p.add_argument("--read", action="store_true", help="同时取回正文（较慢）")
    p.add_argument("--channel", choices=["auto", "so360", "native"], default="auto",
                   help="auto=先 360、被拦或 0 条时换站内；so360=只走 360；"
                        "native=只走站内原生检索（要过一次 WAF，每行带日期与栏目）")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = search_shui5(args.keyword, size=args.size, read_body=args.read,
                          channel=args.channel)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"税屋检索 \"{args.keyword}\" | {result['searched_at']}")
    print("通道：" + "，".join(f"{k} {v}" for k, v in result["_channels"].items()))
    if result.get("_error"):
        print(f"注意：{result['_error']}")
    print(f"共 {result['total']} 条\n")
    for item in result["results"]:
        print(f"  {item['title'][:70]}")
        print(f"     {item['url']}")
        meta = " ".join(x for x in (item.get("date", ""), item.get("column", "")) if x)
        if meta:
            print(f"     {meta}")
        if item.get("_error"):
            print(f"     正文读取失败: {item['_error']}")
        elif item.get("content"):
            body = item["content"].split("Markdown Content:", 1)[-1].strip()
            print(f"     正文 {len(body)} 字／{item.get('pages', 1)} 页，"
                  f"节选：{body[:100]}...")
            if item.get("_pages_error"):
                print(f"     {item['_pages_error']}")
        print()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
