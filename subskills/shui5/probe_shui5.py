#!/usr/bin/env python3
"""
税屋站点体检 — 把 SKILL.md 与 NOTE.md 里那些带日期的实测数字变成一条命令。

为什么需要它：税屋的可用面全部长在外网上——WAF 认哪个 cookie、站内搜索那张表单
还开不开、栏目目录有没有改版。这些事实写在文档里就成了会过期的声明。有了这个探针，
下一次怀疑文档过期时重跑一遍即可对照，不必靠"上次是谁说的"。

它只读、不调模型、不下载文件；对站点的请求量控制在十几次以内，逐篇之间留间隔。
放行 cookie 只在本机内存里传递，不落盘。

判据分三类，缺一项就报在哪一项上，不静默跳过：
  1. 挑战面（不需要浏览器）：无 cookie 时首页、robots.txt、文章页、一个不存在的路径
     返回的字节数是否还是同一份挑战页。
  2. 三条检索通道：站内搜索（帝国 CMS tempid=2）、360 的 site: 检索、栏目翻页。
  3. 正文形态：arcContent 能不能取到、有没有 $N 后续页、尾部有没有混进分享控件。

Usage:
  python subskills/shui5/probe_shui5.py                       # 全项体检
  python subskills/shui5/probe_shui5.py --no-browser           # 只跑不需要放行的那几项
  python subskills/shui5/probe_shui5.py --keyword 留抵退税 --column ShiWuJiDu
"""

import argparse
import html as htmllib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
import urllib3

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "scripts"))

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE = "https://www.shui5.cn/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
TIMEOUT = 25
GAP = 1.0                                   # 逐篇之间的间隔，站点吃挑战对频率敏感
_QUIET = {"全部链接", "链接"}               # 打印时略去的长清单，比对仍在 report 里做

# 挑战页判据：阿里云 WAF 的弹回页里同时带 arg1 与 renderData，正文页不带。
IS_CHALLENGE = lambda page: "arg1" in page and "renderData" in page  # noqa: E731

# 站内搜索用税屋自己那张表单的参数。tempid=1 只回计数不回列表，
# show 只给 title 会掉进"信息提示"页——这两条都是实测踩出来的，改动前先看 NOTE.md。
SEARCH_FORM = {"tbname": "article", "show": "title,keyboard,smalltext",
               "tempid": "2", "Submit": "站内搜税"}

# 结果行：div.xwt2 里 xwt2_a 是标题链，xwt2_c 是摘要，xwt2_d 依次是 URL／日期／栏目
ROW_RE = re.compile(r'<div class="xwt2">(.*?)<div class="xwt2_e">', re.S)
ROW_TITLE_RE = re.compile(r'<div class="xwt2_a"><a\s+href="([^"]+)"\s+title="(.*?)"', re.S)
ROW_SNIPPET_RE = re.compile(r'<div class="xwt2_c">.*?<p class="p1">(.*?)</p>', re.S)
ROW_DATE_RE = re.compile(r'<p class="p3">(20\d\d-\d\d-\d\d)</p>')
ROW_COLUMN_RE = re.compile(r'<p class="p4"><a[^>]*>(.*?)</a>', re.S)
TOTAL_RE = re.compile(r'(\d+)\s*条关于')

ARC_RE = re.compile(r'<div class="arcContent"[^>]*>(.*?)(?=<div class="(?:bot-share|left2b|blank20)|\Z)',
                    re.S | re.I)
NAV_RE = re.compile(r'<li class="mainlevel"><a\s+href="([^"]+)"[^>]*>(.*?)</a></li>', re.S)
PAGEWRAP_RE = re.compile(r'class="pageWrap".*?<div class="pagelist[^"]*">(.*?)</div>', re.S)
A_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']')


def to_text(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", fragment)
    fragment = re.sub(r"</(?:p|div|li|tr|h[1-6])\s*>|<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = htmllib.unescape(fragment)
    return re.sub(r"[ \t]+", " ", fragment).strip()


def _session(cookies: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.headers["Referer"] = SITE
    for k, v in (cookies or {}).items():
        s.cookies.set(k, v, domain="shui5.cn", path="/")
    return s


def _get(s: requests.Session, url: str) -> tuple[int, str]:
    try:
        r = s.get(url, timeout=TIMEOUT, verify=False)
    except requests.RequestException as e:
        return 0, f"请求失败：{type(e).__name__}"
    return r.status_code, r.content.decode("utf-8", errors="replace")


# ── 1 挑战面：不带 cookie 时整站是不是同一张弹回页 ────────────────────────────
def challenge_survey() -> list:
    s = _session()
    rows = []
    for label, url in (("首页", SITE),
                       ("robots.txt", SITE + "robots.txt"),
                       ("文章页", SITE + "article/90/40872.html"),
                       ("不存在的路径", SITE + "zzz-not-here.html")):
        code, page = _get(s, url)
        rows.append({"对象": label, "HTTP": code, "字节": len(page),
                     "判据": "挑战页" if IS_CHALLENGE(page) else "正文页"})
        time.sleep(GAP)
    sizes = {r["字节"] for r in rows if r["判据"] == "挑战页"}
    same = len(sizes) == 1
    rows.append({"对象": "结论", "HTTP": "", "字节": "",
                 "判据": (f"四类路径都回同一份 {sizes.pop() if same else ''} 字节挑战页"
                          if same else "各路径返回不一，挑战面不再是全站同页")})
    return rows


# ── 2 放行：浏览器过挑战，看哪枚 cookie 才是凭据 ──────────────────────────────
def warm_and_cookie(warm_url: str) -> tuple[dict, dict]:
    """过挑战拿 cookie，并逐枚试出放行凭据与各自主导路径的时效。

    Returns:
        (cookie 字典, {"通道":..., "耗时秒":..., "cookie": [{名称,到期,距今天分钟}]})
    """
    from tax_browser import BrowserSession

    t0 = time.time()
    b = BrowserSession(warm_url=warm_url)
    b.warm()
    ck = b.cookies_for("shui5.cn")
    info = {"通道": b.channel, "耗时秒": round(time.time() - t0, 1),
            "cookie": [{"名称": c["name"],
                        "到期": time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(c["expires"]))
                        if c.get("expires", -1) > 0 else "会话结束",
                        "距今天分钟": round((c["expires"] - time.time()) / 60, 1)
                        if c.get("expires", -1) > 0 else ""}
                       for c in b._ctx.cookies()                       # noqa: SLF001
                       if "shui5" in c["domain"]]}
    b.close()

    probe = {}
    for name in ck:
        solo = _session({name: ck[name]})
        code, page = _get(solo, SITE + "article/90/40872.html")
        probe[name] = "单独可放行" if not IS_CHALLENGE(page) else "单独不够"
        solo.close()
        time.sleep(GAP)
    info["凭据判定"] = probe
    return ck, info


# ── 3 通道一：站内搜索（税屋自己那张表单） ────────────────────────────────────
def native_search(keyword: str, cookies: dict, line: int = 10) -> dict:
    """POST 之后服务端 302 到 /e/search/result/?searchid=N，跟着跳即得结果页。"""
    s = _session(cookies)
    data = dict(SEARCH_FORM)
    data["keyboard"] = keyword
    data["line"] = str(line)
    out = {"通道": "站内搜索 /e/search/index.php (tempid=2)", "关键词": keyword}
    try:
        r = s.post(SITE + "e/search/index.php", data=data, timeout=TIMEOUT, verify=False)
    except requests.RequestException as e:
        r = None
        out["错误"] = f"请求失败：{type(e).__name__}"
    page = r.content.decode("utf-8", errors="replace") if r else ""
    if r is not None:
        out["HTTP"] = r.status_code
        out["落点"] = r.url
        out["字节"] = len(r.content)
        out["总数"] = (TOTAL_RE.search(to_text(page)).group(1)
                       if TOTAL_RE.search(to_text(page)) else "未取到")
        rows = []
        for frag in ROW_RE.findall(page):
            t = ROW_TITLE_RE.search(frag)
            if not t:
                continue
            sn = ROW_SNIPPET_RE.search(frag)
            rows.append({
                "标题": to_text(t.group(2)),
                "url": t.group(1),
                "日期": (ROW_DATE_RE.search(frag).group(1) if ROW_DATE_RE.search(frag) else ""),
                "栏目": to_text(ROW_COLUMN_RE.search(frag).group(1))
                        if ROW_COLUMN_RE.search(frag) else "",
                "摘要": (to_text(sn.group(1))[:60] if sn else ""),
            })
        out["结果行数"] = len(rows)
        out["字段齐全行"] = sum(1 for x in rows if x["日期"] and x["栏目"] and x["摘要"])
        out["样例"] = rows[:3]
        out["全部链接"] = [x["url"] for x in rows]
        if IS_CHALLENGE(page):
            out["错误"] = "WAF 挑战未通过（这一步需要 --browser 先放行）"
        elif "信息提示" in page and not rows:
            out["错误"] = "服务端回信息提示页：该关键词形态站内检索不接受（空格词最容易踩）"
    s.close()
    return out


# ── 4 通道二：360 的 site: 检索（脚本当前在用的一路） ──────────────────────────
def so360_channel(keyword: str, size: int = 10) -> dict:
    from tax_so360 import so360_search

    res = so360_search(keyword, site="shui5.cn", size=size)
    urls = sorted({x["url"] for x in res.get("results", [])})
    out = {"通道": "360 站内搜索 site:shui5.cn", "关键词": keyword,
           "条数": res.get("total", 0),
           "文章形态链接": len([u for u in urls if re.search(
               r"/article/[0-9a-zA-Z]+/\d+\.html", u)]),
           "链接": urls}
    if res.get("_error"):
        out["错误"] = res["_error"]
    return out


# ── 5 通道三：栏目地图与翻页 ──────────────────────────────────────────────────
def columns(cookies: dict) -> dict:
    s = _session(cookies)
    code, page = _get(s, SITE)
    out = {"通道": "栏目导航", "HTTP": code}
    if IS_CHALLENGE(page):
        out["错误"] = "WAF 挑战未通过"
        s.close()
        return out
    # 主栏目带名字，只在 li.mainlevel 那一块里；省级目录与专栏是页内其他位置的链接，
    # 按 li.mainlevel 找会得到"地方栏目 0 条"，所以分成两路取。
    nav = []
    for href, label in NAV_RE.findall(page):
        p = re.sub(r"^https?://[^/]+", "", urljoin(SITE, href)).split("?")[0]
        if re.match(r"^/article/[^/]+/?$", p):
            nav.append({"栏目": to_text(label), "目录": p})
    local = re.compile(r"(ShengCaiShui|ShiFaGui|ShiCaiShui|Zizhiqu|category)")
    out["导航项"] = len(nav)
    out["主栏目"] = [{"栏目": x["栏目"], "目录": x["目录"]} for x in nav
                     if not local.search(x["目录"])]
    paths = {re.sub(r"^https?://[^/]+", "", urljoin(SITE, h)).split("?")[0]
             for h in re.findall(r'href="([^"]+)"', page)}
    out["地方栏目数"] = len([p for p in paths if re.match(r"^/article/[^/]+/?$", p)
                             and local.search(p)])
    out["地方栏目样例"] = sorted(p for p in paths if local.search(p))[:6]
    out["专栏"] = sorted(p for p in paths if re.match(r"^/s/[^/]+/?$", p))
    s.close()
    return out


def column_page(column: str, cookies: dict) -> dict:
    """取栏目第一页，顺带把真实翻页链接读出来（形如 <classid>_<页>.html）。"""
    s = _session(cookies)
    url = f"{SITE}article/{column.strip('/')}/"
    code, page = _get(s, url)
    out = {"通道": f"栏目翻页 {column}", "HTTP": code, "字节": len(page)}
    if IS_CHALLENGE(page):
        out["错误"] = "WAF 挑战未通过"
        s.close()
        return out
    arts = set(re.findall(r"/article/[0-9a-zA-Z]+/\d+\.html", page))
    out["本页文章链接"] = len(arts)
    w = PAGEWRAP_RE.search(page)
    nxt = re.findall(r'href="([^"]+)"', w.group(1)) if w else []
    out["分页控件"] = to_text(w.group(1))[:70] if w else "未找到"
    out["下一页"] = [urljoin(url, h) for h in nxt if re.search(r"_\d+\.html", h)][:2]
    if out["下一页"]:
        code2, p2 = _get(s, out["下一页"][0])
        out["第二页文章链接"] = len(set(re.findall(r"/article/[0-9a-zA-Z]+/\d+\.html", p2)))
        out["与第一页相同"] = bool(out["本页文章链接"] == out.get("第二页文章链接")
                                   and out["本页文章链接"])
    s.close()
    return out


# ── 6 正文形态：一次读全了吗 ──────────────────────────────────────────────────
def article_shape(url: str, cookies: dict) -> dict:
    s = _session(cookies)
    code, page = _get(s, url)
    out = {"对象": url, "HTTP": code}
    if IS_CHALLENGE(page):
        out["错误"] = "WAF 挑战未通过"
        s.close()
        return out
    m = ARC_RE.search(page)
    body = to_text(m.group(1)) if m else ""
    out["arcContent 命中"] = bool(m)
    out["第1页正文长"] = len(body)
    tail = re.search(r"/(\d+)\.html", url)
    pages = set()
    if tail:
        pages = {p for p in re.findall(r"/article/[0-9a-zA-Z]+/" + tail.group(1) + r"\$(\d+)\.html", page)}
    out["后续页码"] = sorted(pages)[:6]
    out["截断风险"] = "有后续页但脚本只取第 1 页" if pages else "无"
    # 分享控件是否混进了正文（终止容器没命中时的典型征状）
    out["尾部混入分享控件"] = "分享至好友和朋友圈" in body or "点赞（）" in body
    t = re.search(r"时间：\s*(20\d\d-\d\d-\d\d)", page)
    out["时间字段"] = t.group(1) if t else "未取到"
    out["附件名"] = len(set(re.findall(r'href="[^"]+\.(?:pdf|docx?|xlsx?|zip)"', page, re.I)))
    s.close()
    return out


def report(keyword: str, column: str, use_browser: bool) -> dict:
    out: dict = {"取样时刻": time.strftime("%Y-%m-%d %H:%M:%S"), "站点": SITE}
    out["1 挑战面（无 cookie）"] = challenge_survey()
    cookies: dict = {}
    if use_browser:
        try:
            cookies, info = warm_and_cookie(SITE + "article/90/40872.html")
            out["2 放行与 cookie"] = info
        except Exception as e:
            out["2 放行与 cookie"] = {"错误": f"浏览器不可用：{str(e).splitlines()[0][:90]}"}
    else:
        out["2 放行与 cookie"] = {"跳过": "--no-browser，后面的通道项多半吃挑战"}
    out["3 站内搜索"] = native_search(keyword, cookies)
    out["4 360 site:"] = so360_channel(keyword)
    a = out["3 站内搜索"].get("全部链接") or []
    b = out["4 360 site:"]
    if a:
        # 只比同一关键词下两路各自首屏的链接集合，不比排序；按文章 ID 归一再比，
        # 因为 360 给的地址常带跳转参数、分片段也可能与站内给的不同
        def aid(u: str) -> str:
            m = re.search(r"/article/[^/]+/(\d+)", u)
            return m.group(1) if m else u
        mine = {aid(u) for u in a}
        theirs = {aid(u) for u in b.get("链接", [])}
        out["5 两路首屏交集"] = {
            "站内首屏篇数": len(mine), "360 首屏篇数": len(theirs),
            "同一篇的条数": len(mine & theirs),
            "只在一路出现的篇数": len(mine ^ theirs)}
    out["6 栏目地图"] = columns(cookies)
    out["7 栏目翻页"] = column_page(column, cookies)
    first = None
    for x in (out["3 站内搜索"].get("样例") or []):
        first = x["url"]
        break
    out["8 正文形态"] = article_shape(first, cookies) if first else {"跳过": "站内搜索没出结果"}
    return out


def _print(node, depth=0) -> None:
    pad = "  " * depth
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _QUIET:
                continue
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}:")
                _print(v, depth + 1)
            else:
                print(f"{pad}{k}: {v}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, (dict, list)):
                _print(v, depth)
                if i < len(node) - 1:
                    print(f"{pad}---")
            else:
                print(f"{pad}- {v}")


def main():
    ap = argparse.ArgumentParser(description="税屋站点体检（只读，不调模型）")
    ap.add_argument("--keyword", default="研发费用加计扣除", help="两条检索通道共用的关键词")
    ap.add_argument("--column", default="ShiWuJiDu", help="拿来验翻页的栏目目录名")
    ap.add_argument("--no-browser", action="store_true", help="不过 WAF，只跑挑战面")
    ap.add_argument("--json", action="store_true", help="整体输出 JSON")
    ns = ap.parse_args()

    data = report(ns.keyword, ns.column, use_browser=not ns.no_browser)
    if ns.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"税屋站点体检 | {data['取样时刻']} | {data['站点']}\n")
    _print(data)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
