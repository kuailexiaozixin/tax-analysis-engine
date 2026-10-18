#!/usr/bin/env python3
"""
税屋站点体检 — 把 SKILL.md 与 NOTE.md 里那些带日期的实测数字变成一条命令。

为什么需要它：税屋的可用面全部长在外网上——WAF 认哪个 cookie、站内搜索那张表单
还开不开、栏目目录有没有改版。这些事实写在文档里就成了会过期的声明。有了这个探针，
下一次怀疑文档过期时重跑一遍即可对照，不必靠"上次是谁说的"。

它只读、不调模型、不下载文件；全项约 30 个请求，逐篇之间留间隔（站内检索那几档
每次隔 `tax_shui5.SEARCH_MIN_GAP` = 3 秒，站点自己声明的是 2 秒）。
放行 cookie 只在本机内存里传递，不落盘。`--no-browser` 只跑不需要放行的第 1 项。

判据分四类，缺一项就报在哪一项上，不静默跳过：
  1. 挑战面（不需要浏览器）：无 cookie 时首页、robots.txt、文章页、一个不存在的路径
     返回的字节数是否还是同一份挑战页。
  2. 检索与清单入口：站内搜索（帝国 CMS tempid=2）、360 的 site: 检索、栏目列表页翻页。
  3. 正文形态：arcContent 能不能取到、有没有 $N 后续页、尾部有没有混进分享控件。
  4. 限定与取数入口：站内检索的栏目、发布日期区间、匹配域各收窄到哪一档、
     栏目表与页面是否同源、标签页 `&page=` 还翻不翻得动。

Usage:
  python subskills/shui5/probe_shui5.py                       # 全项体检
  python subskills/shui5/probe_shui5.py --no-browser           # 只跑不需要放行的挑战面
  python subskills/shui5/probe_shui5.py --keyword 留抵退税 --column ShiWuJiDu
  python subskills/shui5/probe_shui5.py --column 北京 --column2 深圳   # 换逗号多选那两栏
  python subskills/shui5/probe_shui5.py --tag 增值税法                 # 指名复测标签翻页
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

import tax_shui5 as SH                       # noqa: E402

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

# 检索表单、结果行形状、正文容器边界、两份提示页文案一律取发货代码（`tax_shui5`）：
# 探针的意义是"复测文档里那些数字还成不成立"，判据本身不该在这儿另立一份。
SEARCH_FORM = SH.SEARCH_FIELDS
TOTAL_RE = SH._TOTAL_RE

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
        rows = SH._search_rows(page)
        out["结果行数"] = len(rows)
        out["字段齐全行"] = sum(1 for x in rows
                               if x["date"] and x["column"] and x["snippet"])
        out["样例"] = rows[:3]
        out["全部链接"] = [x["url"] for x in rows]
        if IS_CHALLENGE(page):
            out["错误"] = "WAF 挑战未通过（这一步要先过浏览器放行）"
        elif SH.RATE_LIMIT_MARK in page:
            out["错误"] = ("限流：站点要求两次检索之间隔 2 秒以上，本轮落进提示页「%s」"
                          % SH.RATE_LIMIT_MARK)
        elif SH.NO_RESULT_MARK in page and not rows:
            out["错误"] = ("这一轮站内检索 0 条：站点回「%s」。这一页有两种成因——这一轮"
                          "确实没命中的文章，或提交的参数站点不接受（tempid、tbname 换值就落在这里），"
                          "按发出去的参数表反查；都不是\"这一站没有内容\"（词按整串匹配，一个概念一个词）"
                          % SH.NO_RESULT_MARK)
        elif not rows:
            out["错误"] = "结果页里没有 div.xwt2 行：容器改版或落点不对"
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
    # 地方栏的数法与发货代码同源：build_column_table 把首页所有 /article/<目录>/ 收进表，
    # 表里除 li.mainlevel 那 11 个之外的就是地方栏。按锚文本正则数会少数几个，
    # 于是探针报"地方栏 30 个"而表里 36 个，两边对不上账。
    dirs = {p.rstrip("/").split("/")[-1]
            for p in paths if re.match(r"^/article/[^/]+/?$", p)}
    navdirs = {x["目录"].rstrip("/").split("/")[-1] for x in nav}
    out["栏目目录总数"] = len(dirs)
    out["地方栏数（表内非 mainlevel）"] = len(dirs - navdirs)
    out["mainlevel 目录数"] = len(navdirs)
    out["地方栏目样例"] = sorted(dirs - navdirs)[:6]
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
    m = SH._ARC_RE.search(page)
    body = SH._to_text(m.group(1)) if m else ""
    out["arcContent 命中"] = bool(m)
    out["第1页正文长"] = len(body)
    pages = SH._own_pages(page, SH._article_id(url))
    out["后续页码"] = sorted(pages)
    out["续页会读到"] = "按本篇 ID 跟读到末页（fetch_shui5）" if pages else "本篇单页"
    # 分享控件是否混进了正文（终止容器没命中时的典型征状）
    out["尾部混入分享控件"] = "分享至好友和朋友圈" in body or "点赞（）" in body
    t = re.search(r"时间：\s*(20\d\d-\d\d-\d\d)", page)
    out["时间字段"] = t.group(1) if t else "未取到"
    out["附件名"] = len(set(re.findall(r'href="[^"]+\.(?:pdf|docx?|xlsx?|zip)"', page, re.I)))
    s.close()
    return out


# ── 9 站内过滤参数：栏目、发布日期区间、匹配域各收窄到哪一步 ──────────────────
def _raw_search(session: requests.Session, keyword: str, extra: dict) -> dict:
    """绕过 tax_shui5 的换算，把参数原样 POST 上去看站点自己认不认。

    只用来说"竖线、classid[] 这类写法站点吃不吃"——发货代码会先换算成逗号，
    所以这一路必须裸提交才看得到站点的原始判据。
    """
    time.sleep(SH.SEARCH_MIN_GAP)
    data = dict(SH.SEARCH_FIELDS, keyboard=keyword, show=SH.SHOW_BY_MATCH["broad"])
    data.update(extra)
    try:
        r = session.post(SH.SEARCH_FORM, data=data, timeout=TIMEOUT,
                         verify=False, allow_redirects=True)
    except requests.RequestException as e:
        return {"行数": None, "总数": None, "说明": f"请求失败：{type(e).__name__}"}
    page = r.content.decode("utf-8", errors="replace")
    m = SH._TOTAL_RE.search(page)
    return {"行数": len(SH._search_rows(page)),
            "总数": int(m.group(1)) if m else None,
            "字节": len(r.content),
            "说明": ("提示页" if SH.NO_RESULT_MARK in page else
                     "限流提示页" if SH.RATE_LIMIT_MARK in page else "结果页")}


def scoped_filters(keyword: str, column: str, column2: str,
                   session: requests.Session) -> dict:
    """站内检索的栏目、发布日期区间、匹配域三个维度，全部走 tax_shui5.search_native 提交。

    判据一律写成"这一档 vs 那一档"的自比，不硬编码某天的读数：
    总数会随站点内容涨，档位之间的关系不会。
    """
    got: dict = {}

    def run(key: str, **kw) -> dict:
        time.sleep(SH.SEARCH_MIN_GAP)
        got[key] = SH.search_native(keyword, size=1, session=session, **kw)
        return got[key]

    def total(key: str):
        return got[key].get("total")

    out: dict = {"关键词": keyword, "档位": {}}
    run("broad")
    run("title", match="title")
    out["档位"]["匹配域"] = {
        "三域（标题+关键词+摘要）": total("broad"), "只标题": total("title"),
        "判据": ("只标题确实收窄" if (total("title") or 0) < (total("broad") or 0)
                 else "只标题没有更窄：SHOW_BY_MATCH 与站点的 show 字段对不上了")}

    c1, c2 = SH.classid_of(column), SH.classid_of(column2)
    if not c1 or not c2:
        out["档位"]["栏目"] = {"跳过": f"栏目表里没有 {column} 或 {column2}，"
                                      "跑 python scripts/tax_shui5.py --update-columns"}
    else:
        run("c1", classid=c1)
        run("c2", classid=c2)
        run("c12", classid=f"{c1},{c2}")
        both_sum = (total("c1") or 0) + (total("c2") or 0)
        out["档位"]["栏目"] = {
            "单栏1": total("c1"), "单栏2": total("c2"), "逗号两栏": total("c12"),
            "判据": ("逗号是按栏求并集" if total("c12") == both_sum and both_sum
                     else f"逗号两栏 {total('c12')} 与两单栏之和 {both_sum} 不等，"
                          "逗号语义要重测")}
        pipe = _raw_search(session, keyword, {"classid": f"{c1}|{c2}"})
        bracket = _raw_search(session, keyword, {"classid[]": str(c1)})
        out["档位"]["栏目写法"] = {
            "竖线": pipe, "数组": bracket,
            "判据": ("竖线只取第一段（与单栏1 同为 %s 条）、数组形态整页不认 —— "
                     "所以只有逗号可用" % total("c1")
                     if pipe["总数"] == total("c1") and not bracket["行数"]
                     else "写法判定与发货代码的注释不一致，重看 classid_spec")}

    run("to_only", date_to=time.strftime("%Y-%m-%d"))
    run("from_only", date_from="2025-01-01")
    run("pair", date_from="2025-01-01", date_to="2025-12-31")
    out["档位"]["日期"] = {
        "不限": total("broad"), "只给止": total("to_only"),
        "只给起": total("from_only"), "成对": total("pair"),
        "判据": ("只给起不过滤（读数与不限相同）→ date_from 单独无效这条还成立"
                 if total("from_only") == total("broad")
                 else "只给起这一档开始起作用了：starttime 单独无效的结论已过期")
                + ("；只给止确实收窄、成对不宽于只给止"
                   if (total("to_only") or 0) < (total("broad") or 0)
                   and (total("pair") or 0) <= (total("to_only") or 0)
                   else "；日期这一组的关系不对，starttime/endtime 要重测")}
    out["档位"]["只给起的缺口有没有写出来"] = bool(got["from_only"].get("_filter_note"))
    out["原始读数"] = {k: {"total": v.get("total"), "status": v.get("_status"),
                            "note": v.get("_filter_note", "")}
                       for k, v in got.items()}
    return out


# ── 10 栏目表与标签翻页：表里的值还跟页面一致吗 ───────────────────────────────
def column_table_check(column: str, session: requests.Session, tag: str = "") -> dict:
    """栏目表的 classid/页数/条数逐栏回读页面核对，再加两页重合与标签翻页。"""
    row = SH.resolve_column(column)
    if not row:
        return {"跳过": f"栏目表里没有「{column}」"}
    out: dict = {"栏目": column, "表里的值": {
        "classid": row["classid"], "pages": row.get("pages"),
        "records": row.get("records")}}
    stem = f"{SITE}article/{row['dir']}/"
    p1, e1 = SH._read_page(session, stem)
    if e1:
        return {"错误": f"栏目首页取不到：{e1}"}
    ids = SH._COL_PAGE_LINK_RE.findall(p1)
    live_id = max(set(ids), key=ids.count) if ids else None
    ctrl = SH._col_control(p1)
    out["页面回读"] = {"classid": live_id,
                     "控件": f"共 {ctrl[0]} 页 {ctrl[1]} 条" if ctrl else "未读到",
                     "行内栏目名": sorted({x.get("column") for x in SH._search_rows(p1)
                                          if x.get("column")})[:4]}
    out["判据"] = {
        "表与页面同源": (f"classid {live_id} 与表一致、页数条数与控件一致"
                        if str(live_id) == str(row["classid"])
                        and ctrl == (row.get("pages"), row.get("records"))
                        else "表和页面对不上了：栏目表过期，跑 --update-columns 重建"),
        "父栏混子栏": len(out["页面回读"]["行内栏目名"]) > 1}
    time.sleep(SH.SEARCH_MIN_GAP)
    p2, e2 = SH._read_page(session, f"{stem}{row['classid']}_2.html")
    a = {x["url"] for x in SH._search_rows(p1)}
    b = {x["url"] for x in SH._search_rows(p2)} if not e2 else set()
    out["两页重合"] = {"第1页行数": len(a), "第2页行数": len(b), "重合": len(a & b),
                     "判据": ("div.xwt2 行判据翻页不漏不重" if not (a & b)
                              else "第 1、2 页有 %d 行相同：行判据或分页形态变了"
                           % len(a & b))}

    home, e3 = SH._read_page(session, SITE)
    if e3:
        out["标签"] = {"跳过": f"首页取不到：{e3}"}
        return out
    tags = SH.tags_of(home)
    if not tags:
        out["标签"] = {"跳过": "首页读不出标签名（_TAG_HREF_RE 与 /e/tags/ 链接形态要重测）"}
        return out
    # 只看首页第一个标签会误判：那个标签若本身只有一页，第 2 页自然回空行，
    # 读起来和"翻页参数失效"一模一样。逐个试到第一个能出第 2 页行为止。
    tried = []
    for t in ([tag] if tag else tags[:3]):
        q = quote(t)
        tp1, te1 = SH._read_page(session, f"{SH.TAG_RESULT}?tagname={q}")
        time.sleep(SH.SEARCH_MIN_GAP)
        tp2, te2 = SH._read_page(session, f"{SH.TAG_RESULT}?tagname={q}&page=2")
        a = {x["url"] for x in SH._search_rows(tp1)} if not te1 else set()
        b = {x["url"] for x in SH._search_rows(tp2)} if not te2 else set()
        tried.append({"标签": t, "第1页行": len(a), "第2页行": len(b), "重合": len(a & b)})
        if b:
            break
    hit = [x for x in tried if x["第2页行"]]
    if hit:
        judge = ("&page= 2 确实翻页（%s 第 2 页 %d 行、与首页重合 %d）"
                 % (hit[0]["标签"], hit[0]["第2页行"], hit[0]["重合"]))
    elif any(x["第1页行"] == 0 for x in tried):
        judge = ("有标签第 1 页就 0 行：这个标签名站点不认（不存在或写法变了），"
                 "翻页与否这一轮判不了")
    else:
        judge = ("试过的标签首页都有行、第 2 页全空——这些标签本身可能只有一页，"
                 "也可能翻页真失效了；用 --tag <已知多页的标签> 指名复测")
    out["标签"] = {"逐个试过": tried, "判据": judge}
    return out


def report(keyword: str, column: str, use_browser: bool,
           column2: str = "深圳", tag: str = "") -> dict:
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
    if cookies:
        s = SH._browser_session(cookies)
        try:
            out["9 站内过滤参数"] = scoped_filters(keyword, column, column2, s)
            out["10 栏目表与标签翻页"] = column_table_check(column, s, tag)
        finally:
            s.close()
    else:
        out["9 站内过滤参数"] = {"跳过": "无放行 cookie（--no-browser）"}
        out["10 栏目表与标签翻页"] = {"跳过": "无放行 cookie（--no-browser）"}
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
    ap.add_argument("--column2", default="深圳",
                    help="逗号多选那一项用的第二个栏目（目录名/导航名/短标签/classid 都行）")
    ap.add_argument("--tag", default="",
                    help="指名复测的标签；不填就用首页前三个标签逐个试")
    ap.add_argument("--no-browser", action="store_true", help="不过 WAF，只跑挑战面")
    ap.add_argument("--json", action="store_true", help="整体输出 JSON")
    ns = ap.parse_args()

    data = report(ns.keyword, ns.column, use_browser=not ns.no_browser,
                  column2=ns.column2, tag=ns.tag)
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
