#!/usr/bin/env python3
"""
中国税网 (www.ctaxnews.com.cn) — 中国税务报社的媒体层来源。

为什么需要这个模块：这个站同时挂出两类别处取不到的东西——
  1. 政策原文的转载（政策栏目下的中央/地方财税法规，行里直接带文号与发文机关）；
  2. 税务报自己的报道、税案通报与政策解读（媒体口径，不是法定依据）。
母技能 ③ 的 L4 实务层原本只有税屋与公众号，这一源补上"报刊登载的政策转载"。

站点形态（决定下面的写法，逐项读数记在 subskills/ctaxnews/NOTE.md）：
  * 静态服务端渲染，内容页没有 XHR、没有 WAF、不校验 UA（默认 python-requests UA
    与浏览器 UA 取回同一份 28,309 字节页面）。所以这个站不需要浏览器，
    按 browser-automation 的规矩，静态站一律走纯 HTTP，不启任何浏览器。
  * 检索是一个开放 JSON 接口：GET https://www.ctaxnews.com.cn/xy/Search.do，
    首页表单提交到的 founder/SearchServlet.do 只是发这块 JSON 的那个壳
    （bootstrap/js/Search.js 里的地址就是 /xy/Search.do）。
  * 编码是个坑：正文页的响应头是 `Content-Type: text/html`，不带 charset，
    requests 于是按 ISO-8859-1 解，中文全是乱码且不报错。
    所以取 HTML 一律显式按 utf-8 解（_http_get）；
    Search.do 那一路带头声明 charset=UTF-8，不用管。

接口契约里三处会静默改变结果，判据都放在下面这几个常量与注释里：
  * 翻页只认 `pageNo`，且 0 与 1 都回第一页；start/page/pageNum/p/offset/currentPage
    这些别名一概不认，被忽略后落回 start=0，于是每一页都是第一页。
  * `sort` 给 `date` 按发布日期倒序，给 `score` 站点也收（总数不变、首行换序），
    给站点不认的值（如 relevance）HTTP 500；本层只提交 date，不接换序那一路。
    `channel` 只有 1(网站)/2(触屏) 有效，0 或 3 回 37 字节空结构；`siteID` 只有 2 有结果。
    日期只认 yyyy-mm-dd，毫秒或斜杠分隔都回 HTTP 500；区间两枚各自独立，只给一侧照常收。
  * 触屏端（channel=2）那 18 行是 2019 年的微信转载，行里 url 与 urlpad 两个地址
    12/12 取样全返回 HTTP 500，取不到正文。这一路只用来数条数，不接进取正文。

正文可达性有一条改版分界：行里 url 是三段路径（域名后直接日期、且是 http）的
一律 500——那是 2023-02 改版前的老行，站点没有留下可反推的目录段（对同一行试原址、
换 https、再补 policy、news、sssw、sswfaj、zt 五枚目录名，七路全回 500 加一份 572 字节错误页）。
取样 2,617 行：1,834 行属于这一类，日期止于
2023-01-03；余下 783 行是四段 https 路径，日期始于 2023-02-10，逐条可达。
所以 `_address_state` 只看行本身就能判定，不必先吃一个 500。

行里的摘要不能接进抽取：`enpcontent` 是正文截断（200 行取样里 195 行有值，
长度只有 120 与 123 两档，194 行带收尾截断标记），`Abstract` 是编辑写的摘要
（同批 174 行有值，长度 9~193 不等）。逐字段读数记在 subskills/ctaxnews/NOTE.md
「检索行的字段与值域」。两者都只作展示，字段一律回正文页取。

Usage:
  python tax_ctax.py "研发费用加计扣除" --size 5
  python tax_ctax.py "增值税" --column 税案通报 --size 10      # 限定栏目（叶节点）
  python tax_ctax.py "出口退税" --from 2025-01-01 --to 2026-10-01
  python tax_ctax.py "增值税" --match title --size 20                    # 只匹配标题
  python tax_ctax.py "增值税" --all --max-rows 300             # 按 foundNum 翻到底
  python tax_ctax.py "财政部 税务总局公告2026年第29号" --find-doc
  python tax_ctax.py --list 税案通报 --pages 2                 # 不检索，按栏目页取清单
  python tax_ctax.py "出口退税" --read                          # 检索后逐篇取正文
  python tax_ctax.py --columns                                 # 打印本地栏目表，不发请求
  python tax_ctax.py --update-columns                          # 重建栏目表（约 118 次请求）
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CTAX_SITE = "ctaxnews.com.cn"
CTAX_BASE = "https://www.ctaxnews.com.cn"
# 首页表单提交到 founder/SearchServlet.do，那只是发下面这块 JSON 的壳；
# 域名上的这一路与站点后端 120.132.80.188:8088 返回同一份结果（实测 foundNum 一字不差），
# 后端地址留作域名那一路打不通时的第二落点。
SEARCH_ENDPOINTS = (CTAX_BASE + "/xy/Search.do",
                    "http://120.132.80.188:8088/xy/Search.do")

# 实测值域，越界不是报错就是空，写清楚免得当成"这一站没有内容"：
#   sort=date 按发布日期倒序，站点还收 score（同数换序），陌生值如 relevance 回 HTTP 500
#   ——本层只提交 date，SORT_ONLY 说的是本层的取值不是站点的取值上限；
#   channel 只认 1/2（0/3 回 37 字节空响应）；
#   siteID 只有 2 出结果（1/3 回空）；pageSize 到 5000 都照数给（349,177 条全库
#   用 pageSize=5000、pageNo=70 一次取到末段 4,177 行，逐段累加与总数相等）。
SORT_ONLY = "date"
CHANNEL_WEB = 1
CHANNEL_TOUCH = 2
SITE_ID = 2
PAGE_SIZE_MAX = 5000
# 匹配域靠"提交哪一枚位置参数"表达，不给 q 加限定。站点认 q/title/content/keyword/author
# 五枚、各只单用；本层的 content 走 q=（全文档），站点那枚 content= 只匹配正文、比 q 窄，
# 本层没接，两档差异的计数记在 NOTE.md「Search.do 的参数值域」。
MATCH_POSITIONS = {"content": "q", "title": "title",
                   "keyword": "keyword", "author": "author"}
# 本层的 0 = 不提交这一枚（拿到两端并集）；站点侧 0、3、20、-1 四枚实测回同一份
# 37 字节空结构（逐字节读数见 NOTE.md「触屏端与整站规模」）。
CHANNELS_ACCEPTED = (CHANNEL_WEB, CHANNEL_TOUCH)
SEARCH_PAGE_SIZE = 50
# 检索连翻的页数上限。--all 按 foundNum 翻到底，防的是「增值税」那种 46,871 条的宽词。
SEARCH_MAX_PAGES = 40
# 栏目列表页最多只铺到第 10 页（node_<id>_11.html 起 HTTP 500），每页 10 行。
# 同一栏目用 nodeID 走检索能拿到 1,648 行，所以"按栏目取全"只能走检索，
# 栏目页只用来看这一栏最新有什么。
COLUMN_PAGE_MAX = 10
ROWS_PER_COLUMN_PAGE = 10
# 逐篇取正文的间隔。站点没有实测到的限流（14 次零间隔检索与 10 篇零间隔正文全 200），
# 这两个数是礼节性限速，不是站点逼出来的下限。
READ_INTERVAL = 1.0
SEARCH_MIN_GAP = 1.0
TIMEOUT_SEARCH = 30
TIMEOUT_READ = 30

COLUMNS_FILE = SCRIPT_DIR.parent / "data" / "ctax_columns.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 正文与元信息都夹在方正 CMS 的注释标记里：
#   <!--enpcontent-->…<!--/enpcontent--> 正文；<!--enpproperty<字段>值</字段>…--> 元信息。
# 政策页与专题页把这两对标记各写两遍（桌面版与移动版），2026-10-19 取样的 11 篇政策页
# 与 2 篇专题页两块都逐字节相同——所以取第一块是去重，不是漏读。
_BODY_RE = re.compile(r"<!--enpcontent-->(.*?)<!--/enpcontent-->", re.S)
_PROP_RE = re.compile(r"<!--enpproperty(.*?)-->", re.S)
_FIELD_RE = re.compile(r"<(\w+)>(.*?)</\w+>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_PARA_RE = re.compile(r"</(?:p|div|li|tr|table|h[1-6])\s*>|<br\s*/?>", re.I)

# 栏目页的行：div.newsList-item clearfix 里 p.title 给标题与地址、
# p.summary 给摘要、span.time 给发布日期（写法是 2026.10.19，点分）。
_ROW_RE = re.compile(
    r'<div class="newsList-item[^"]*">(.*?)</div>\s*</div>', re.S | re.I)
_ROW_TITLE_RE = re.compile(r'<p class="title">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                           re.S | re.I)
_ROW_SUMMARY_RE = re.compile(r'<p class="summary">\s*<a[^>]*>(.*?)</a>', re.S | re.I)
_ROW_TIME_RE = re.compile(r'<span class="time">\s*([0-9]{4})\.([0-9]{1,2})\.([0-9]{1,2})')
# 分页控件把总页数写在 ul 的属性上：<ul pages='10' current='1' class='pages'>
_PAGES_RE = re.compile(r"<ul\s+pages=['\"](\d+)['\"][^>]*>", re.I)
# 栏目页地址：/policy/node_24.html、/sswfaj/node_23_2.html
_NODE_LINK_RE = re.compile(r"""href=["']([^"']*?node_(\d+)(?:_(\d+))?\.html)["']""", re.I)


def _http_get(url: str, params: dict | None = None, timeout: int = TIMEOUT_SEARCH,
              session: requests.Session | None = None):
    """取一页。正文页不带 charset，requests 会按 ISO-8859-1 解出乱码且不报错，
    所以这里按响应头补：没声明 charset 的一律按 utf-8 解。"""
    s = session or requests.Session()
    if session is None:
        s.headers.update(HEADERS)
    r = s.get(url, params=params, timeout=timeout)
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "charset" not in ctype and (r.encoding or "").lower() in ("iso-8859-1", "latin-1"):
        r.encoding = "utf-8"
    return r


def _to_text(fragment: str) -> str:
    """剥标签、留段落：块级结束标签与 <br> 先换成换行，再剥剩下的标签，压掉空行。

    顺序不能颠倒：`_TAG_RE` 把 `</p>` 一起剥掉之后 `_PARA_RE` 就再也找不到东西，
    整篇正文会粘成一行。
    """
    txt = _TAG_RE.sub("", _PARA_RE.sub("\n", fragment or ""))
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    lines = [ln.strip() for ln in txt.split("\n")]
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------------------
# 检索这一档
# ---------------------------------------------------------------------------

def _address_state(url: str) -> str:
    """行里的地址能不能取到正文，只看形状就能判（见模块头那段改版分界）。

    返回 "ok" 或一段说明文字；调用方据此决定要不要发这个请求。
    """
    if not url or not url.startswith("http"):
        return "行里没有可用地址"
    path = re.sub(r"^https?://[^/]+", "", url)
    segs = [x for x in path.split("/") if x]
    if len(segs) < 4:
        return ("站点侧取不到这一行：地址缺栏目目录段（2023-02 改版前的老行，"
                "实测返回 HTTP 500，目录段无法反推）")
    return "ok"


def _row_from_json(a: dict) -> dict:
    """把 Search.do 的一行归一成母技能认的形状。

    站点字段与这一层的对应关系（值域见 NOTE.md）：
      subtitle  → doc_number 文号（政策行才有；检索行里就有，不用进正文页）
      introtitle→ agency 发文机关
      sourcename→ 登载来源（中国税务报 / 国家税务总局 / 空）
      nodesearchname → column 行内栏目名（一稿多发时每行挂一栏，见 dedup_rows）
      author    → draft_date 成文日期。站点把"2026年10月12日"这种成文日期塞在
                  author 字段里，不是作者名；非日期形态的 author 原样留成 author。
      date      → 发布日期（与成文日期是两件事，都交出去）
    """
    title = (a.get("title") or "").strip()
    url = (a.get("url") or "").strip()
    author = (a.get("author") or "").strip()
    draft = author if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", author) else ""
    row = {
        "title": title,
        "url": url,
        "date": (a.get("date") or "").strip(),
        "snippet": _to_text(a.get("enpcontent") or a.get("Abstract") or "")[:200],
        "source": "中国税网 (ctaxnews.com.cn)",
        "source_label": "媒体解读",
        "articleid": str(a.get("articleid") or ""),
    }
    if a.get("nodesearchname"):
        row["column"] = a["nodesearchname"].strip()
    if a.get("subtitle"):
        row["doc_number"] = a["subtitle"].strip()
    if a.get("introtitle"):
        row["agency"] = a["introtitle"].strip()
    if a.get("sourcename"):
        row["byline"] = a["sourcename"].strip()
    if draft:
        row["draft_date"] = draft
    elif author:
        row["author"] = author
    if a.get("Abstract"):
        row["abstract"] = a["Abstract"].strip()
    state = _address_state(url)
    if state != "ok":
        row["_url_state"] = state
    return row


def dedup_rows(rows: list) -> list:
    """一稿多发的行合成一条，其余栏目名收进 also_in_columns。

    政策文件会同时挂在好几个栏目下，检索按栏出行，同一篇就出好几行。
    2026-10-19 用一枚文号取样：「财政部 税务总局公告2026年第29号」foundNum=4，
    四行的 title/date/subtitle 全同，只有 nodesearchname 分别是
    其他税种、财政部 国家税务总局公告、增值税、中央税收法规。
    判据取「标题＋发布日期＋文号」三项同，不用 articleid——四行是四个 articleid，
    站点就是把同一份稿子中了一份四篇。
    """
    out, index = [], {}
    for row in rows:
        key = (row.get("title", ""), row.get("date", ""), row.get("doc_number", ""))
        if key in index:
            first = out[index[key]]
            col = row.get("column", "")
            if col and col not in first.setdefault("also_in_columns", []):
                first["also_in_columns"].append(col)
            continue
        index[key] = len(out)
        out.append(row)
    for row in out:
        if row.get("also_in_columns"):
            row["also_in_columns"] = sorted(set(row["also_in_columns"]))
    return out


def _search_once(params: dict, session: requests.Session | None = None):
    """打一次 Search.do，返回 (json, 错误说明)。两个落点都试完才算失败。"""
    last = ""
    for endpoint in SEARCH_ENDPOINTS:
        try:
            r = _http_get(endpoint, params=params, timeout=TIMEOUT_SEARCH,
                          session=session)
        except Exception as e:  # noqa: BLE001
            last = f"{endpoint.split('/')[2]} 请求失败：{e}"
            continue
        if r.status_code != 200:
            last = (f"{endpoint.split('/')[2]} 返回 HTTP {r.status_code}"
                    f"（{len(r.content)} 字节）；日期形态不是 yyyy-mm-dd、"
                    f"或 sort 给了站点不认的值时这一路报 500，不是限流")
            continue
        try:
            return r.json(), ""
        except Exception as e:  # noqa: BLE001
            last = f"{endpoint.split('/')[2]} 回的不是 JSON：{e}"
    return None, last


def search_ctax(keyword: str, size: int = 5, *, node=None, date_from: str = "",
                date_to: str = "", match: str = "content", channel: int = CHANNEL_WEB,
                fetch_all: bool = False, max_rows: int = 0,
                session: requests.Session | None = None) -> dict:
    """站内检索（开放 JSON 接口，不要凭据、不启浏览器）。

    参数与站点认的形态：
      node      栏目限定，收栏目名或 nodeID。**只认叶节点**：2026-10-19 实测
                nodeID=24（政策，父节点）空词回 0 条，nodeID=8215（其他税种，叶）
                回 23 条——父节点下面挂着文章，但检索不按子树收，
                所以给父栏目会整片报 0，_filter_note 里写明这一条。
      date_from/date_to  发布日期区间，只认 yyyy-mm-dd；毫秒或斜杠分隔报 HTTP 500。
                两枚各自独立，只给一侧也生效（「增值税」只给起始 2,537 行、只给截止
                45,739 行、成对取 2025 全年 1,405 行）。
                实测与 nodeID 同时提交时两样都生效（nodeID=63 配 2016~2019 回 0，
                而该栏发布年份实际只铺到 2020-2025，那个 0 是真的没有，不是过滤失效）。
      match     提交哪一枚位置参数：content→`q`（全文，默认）、title→只匹配标题、
                keyword→只匹配站点的关键词字段、author→只匹配 author（那一段装的是
                成文日期，实际用处有限）。这四枚之外的取值不发出去——本层过去会静默
                回落到 q，那份读数看着像"限定生效了"其实没有，所以拦在发出之前。
      channel   1=网站（默认）。2=触屏端只有 18 行 2019 年的微信转载且地址全失效，
                取不到正文；0 是不提交这一枚，拿到两端的并集（同一枚词 46,889 = 网站 46,871 + 触屏 18）。
                这三枚之外的值不发出去：站点回一份 37 字节的空结构，读起来跟"这一词没命中"一样。
      size      取回多少**篇**（按 dedup_rows 合并后的篇数收，不是站点给的行数）。
                不翻到底时必须是正整数，0 与负数不发请求。
      fetch_all 按站点报的 foundNum 翻到底；给了 max_rows 先到 max_rows 行收口，
                否则到 SEARCH_MAX_PAGES 页。这一档不看 size，所以 max_rows 之外不必给正数。

    返回 {"results","total","_status","_pages","_filter_note"?,"_error"?}；
    total 是站点报的这一词总条数。检索词的写法只有一条要注意：`q` 按整串匹配、
    不分词，多词之间没有 AND 语义，一个概念一枚词。
    """
    if not (keyword or "").strip() and not node:
        return {"results": [], "total": None,
                "_status": "没走成：检索词为空且没限定栏目（空词只在全站范围可用，"
                           "用来数整栏条数）"}
    notes = []
    node_id = node
    if node is not None and not str(node).isdigit():
        state, cand = column_lookup(node)
        if state != "ok":
            if state == "ambiguous":
                return {"results": [], "total": None,
                        "_status": f"没走成：栏目表里有 {len(cand)} 行对上「{node}」——"
                                   f"{'；'.join(cand)}。站点确实有重名栏目，不替读者挑一条，"
                                   f"请改按 nodeID 限定",
                        "_error": "ambiguous column"}
            return {"results": [], "total": None,
                    "_status": f"没走成：栏目表 data/ctax_columns.json 里没有「{node}」，"
                               f"没有把半截限定发出去；先跑 --update-columns 重建栏目表"}
        node_id = cand[0]
        col = get_column(node_id)
        if col and col.get("parent"):
            notes.append(f"「{node}」是父栏目（nodeID={node_id}），检索按叶节点收，"
                         f"父节点回 0 条；要限定这一类请指名子栏目")
    for label, d in (("起始", date_from), ("截止", date_to)):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            return {"results": [], "total": None,
                    "_status": f"没走成：{label}日期「{d}」不是 yyyy-mm-dd 形态，"
                               f"站点这一档越界直接回 HTTP 500"}
    if match not in MATCH_POSITIONS:
        return {"results": [], "total": None,
                "_status": f"没走成：match「{match}」不在站点认的四枚里"
                           f"（{'、'.join(MATCH_POSITIONS)}）；本层不把它换成 q 发出去，"
                           f"那看着像限定了匹配域、其实取的是全文"}
    if channel not in CHANNELS_ACCEPTED and channel != 0:
        return {"results": [], "total": None,
                "_status": f"没走成：channel={channel} 站点不认（只收 {CHANNEL_WEB}=网站、"
                           f"{CHANNEL_TOUCH}=触屏端，本层的 0 = 不提交、取两端并集）；"
                           f"越界值回一份 37 字节空结构，读起来跟这一词没命中一样"}
    if max_rows < 0:
        return {"results": [], "total": None,
                "_status": f"没走成：max_rows={max_rows} 是负数（0 = 不设行数上限）"}
    if not fetch_all and size < 1:
        return {"results": [], "total": None,
                "_status": f"没走成：size={size}，收满按篇数算，这个值一篇也不要；"
                           f"要按 foundNum 翻到底请给 fetch_all=True"}

    params = {"pageNo": 1, "pageSize": SEARCH_PAGE_SIZE,
              "sort": SORT_ONLY, "siteID": SITE_ID}
    # 匹配域是"提交哪一枚参数名"，不是给 q 加限定：站点认 q/title/content/keyword/author 五枚，
    # 各自单用。同一枚「增值税」在省略 channel 的并集口径下 q= 46,889、title= 11,911、
    # keyword= 908，显式 channel=1 那一路是 46,871 / 11,909 / 46,498 / 908 / 0（author 段空）。
    # 而 q= 与 title= 同时提交时 foundNum 仍是 q 单发那一份——**q 会把位置参数盖掉**，
    # 所以只匹配标题时必须只发 title=，不能 q= 与 title= 一起发。
    # 站点的 content= 比 q= 窄（同一枚「出口退税」q= 9,804、content= 9,746），本层不接：
    # match=content 走的是 q（全文档），差异读数与取样口径记在 NOTE.md。
    params[MATCH_POSITIONS[match]] = keyword or ""
    if channel:
        # 0 在本层当作"不限"：省掉这一枚是两端并集；站点侧直接给 channel=0 回空结构，
        # 把那个空结构当成"这一词没命中"是这一站最容易踩的读法。
        params["channel"] = channel
    if date_from:
        params["startDate"] = date_from
    if date_to:
        params["endDate"] = date_to
    if node_id:
        params["nodeID"] = str(node_id)

    # 两枚上限各管一头：size 收的是"篇"（一稿多发会把一页里的行合成几篇，
    # 只按行数收会让 --size 5 实际只回 2 篇），max_rows 收的是站点给的"行"。
    # 取小的那一份：max_rows 只能收紧，不能反过来抢过 size 多给行。
    want = size if not max_rows else min(size, max_rows)
    cap_rows = max_rows
    # 翻页上限不按 want 换算页数：按篇收满要有几页算几页，预先按行数算会让小的 want
    # 只翻一页就交出不够篇数的结果。
    # 正常一词仍在一到两次请求内退出（收满即 break，取到 total 也 break）。
    cap_pages = SEARCH_MAX_PAGES
    got, pages_read, total, status, stopped_by = [], 0, None, "", ""
    page = 1
    while page <= cap_pages:
        params["pageNo"] = page
        j, err = _search_once(params, session=session)
        if j is None:
            if page == 1:
                return {"results": [], "total": None, "_status": f"没走成：{err}",
                        "_error": err}
            status = f"第 {page} 页取不到（{err}），停在已取的 {len(got)} 条"
            break
        rows = j.get("article") or []
        total = j.get("foundNum")
        pages_read += 1
        if not rows:
            break
        got += [_row_from_json(a) for a in rows]
        page += 1
        # 行数上限按整页取到再裁到那一行，多取的一页不留下——cut 点是"要这么多行"
        # 这句的字面要求；同一篇挂在 cut 点之后的栏目行会跟着裁掉，所以合并数只算留下的。
        if cap_rows and len(got) >= cap_rows:
            got = got[:cap_rows]
            stopped_by = f"max_rows={cap_rows} 行的"
            break
        if not fetch_all and len(dedup_rows(got)) >= want:
            break
        if total is not None and len(got) >= int(total):
            break
        time.sleep(SEARCH_MIN_GAP)

    uniq = dedup_rows(got)
    if not fetch_all:
        uniq = uniq[:want]
    if not status:
        if stopped_by and total is not None and len(got) < int(total):
            status = (f"取到 {len(got)} 行、交回 {len(uniq)} 篇，这一词共 {total} 条；"
                      f"已到 {stopped_by}上限，不是这一词只有这些")
        elif fetch_all and total is not None and len(got) < int(total):
            status = (f"取到 {len(got)} 条，这一词共 {total} 条；已到页数上限 "
                      f"{cap_pages} 页（SEARCH_MAX_PAGES），不是这一词只有这些")
        elif total is not None and len(got) == 0:
            status = "0 条：这一词（连同本轮限定）在站内没有命中"
        else:
            status = f"取到 {len(uniq)} 条，站点报这一词共 {total} 条"
    merged = len(got) - len(dedup_rows(got))
    if merged:
        status += f"；合并同一篇挂多栏的行 {merged} 行（判据见 dedup_rows）"
    out = {"results": uniq, "total": total, "_status": status, "_pages": pages_read}
    if notes:
        out["_filter_note"] = "；".join(notes)
    return out


def find_by_doc_number(doc_number: str, session: requests.Session | None = None) -> dict:
    """只有一枚文号时回站内定位。

    文号在检索行里就是 `subtitle` 字段，所以**带 title= 的文号检索取不到**：
    2026-10-19 实测 `title="2026年第29号"` 回 0 条，而整串 `q="财政部 税务总局公告2026年第29号"`
    回 4 条且首行就是目标。`q` 按整串匹配，空白不影响（带空格与去掉空格两种写法
    都命中同一簇），只留编号尾段（如「2026年第29号」）也能命中。
    交出去之前先按 dedup_rows 合并一稿多发（那 4 行是同一篇挂四栏）。
    """
    doc = (doc_number or "").strip()
    if not doc:
        return {"doc_number": doc, "results": [], "total": None, "_status": "没走成：没给文号"}
    variants = [doc, re.sub(r"\s+", "", doc)]
    tried, last = [], None
    for q in dict.fromkeys(variants):
        tried.append(q)
        r = search_ctax(q, size=0, fetch_all=True, max_rows=200, session=session)
        last = r
        if r["results"]:
            break
    rows = dedup_rows(last["results"]) if last else []
    out = {"doc_number": doc, "_queries_tried": tried, "results": rows,
           "total": (last or {}).get("total"), "_status": (last or {}).get("_status", "没走成")}
    if rows:
        out["_caveat"] = ("这一档命中里含同稿多栏与地方转发同号文件；"
                          "站点行里的 subtitle 就是文号，逐条看 subtitle 与 agency 是否就是这份，"
                          "不要默认取第一条")
    return out


# ---------------------------------------------------------------------------
# 正文这一档
# ---------------------------------------------------------------------------

def fetch_ctax(url: str, session: requests.Session | None = None,
               fetch_interval: float = READ_INTERVAL) -> dict:
    """读回一篇正文与站点自己标的元信息。

    返回 {"url","title","content","published","draft_date","doc_number","agency",
          "column","nodeid","liability","tags","byline","_error"?,"_note"?}
    只有正文页有而检索行没有的字段是 liability（效力标注）、tag（税种标签，
    分号分隔）、nodeid 与 nodename。2026-10-19 取样的 11 篇政策文 liability 全是
    「全文有效」、tag 全非空；同批里的新闻与解读文这两个字段全为空——
    **字段为空读成"站点没标"，不读成"这篇没有依据"**。
    """
    state = _address_state(url)
    if state != "ok":
        return {"url": url, "_error": state}
    try:
        r = _http_get(url, timeout=TIMEOUT_READ, session=session)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "_error": f"请求失败：{e}"}
    if fetch_interval:
        time.sleep(fetch_interval)
    page = r.text
    if r.status_code != 200:
        return {"url": url,
                "_error": f"HTTP {r.status_code}（{len(r.content)} 字节）——"
                          f"这个站的错误地址回 500 而不是 404，读成地址不存在，不重试"}
    bodies = _BODY_RE.findall(page)
    if not bodies:
        return {"url": url, "_error": "没有取到 enpcontent 正文标记", "_raw_len": len(page)}
    props = _PROP_RE.findall(page)
    meta = {}
    if props:
        meta = {k: v.strip() for k, v in _FIELD_RE.findall(props[0])}
    author = meta.get("author", "")
    out = {
        "url": url,
        "title": (meta.get("title") or "").strip(),
        "content": _to_text(bodies[0]),
        "published": (meta.get("date") or "")[:10],
        "doc_number": meta.get("subtitle", "").strip(),
        "agency": meta.get("introtitle", "").strip(),
        "column": meta.get("nodename", "").strip(),
        "nodeid": meta.get("nodeid", "").strip(),
        "liability": meta.get("liability", "").strip(),
        "byline": meta.get("sourcename", "").strip(),
        "abstract": meta.get("abstract", "").strip(),
    }
    if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", author):
        out["draft_date"] = author
    elif author:
        out["author"] = author
    if meta.get("tag"):
        out["tags"] = [t.strip() for t in re.split(r"[；;，,]", meta["tag"]) if t.strip()]
    if len(bodies) > 1 and bodies[0] == bodies[1]:
        out["_note"] = "正文有桌面/移动两份，逐字节相同，取第一份"
    elif len(bodies) > 1:
        out["_note"] = (f"正文有 {len(bodies)} 份且两份不一致，只取第一份——"
                        f"这一页可能改版了，去 NOTE.md 复测第 6 项")
    if not out["content"]:
        out["_error"] = "enpcontent 标记存在但正文为空（图片型正文，取样里 图解税收 栏目 1/1 如此）"
    return out


def read_articles(rows: list, limit: int = 5, session: requests.Session | None = None) -> list:
    """对检索行逐篇取正文，取不到的把原因写进 _error 挂在行上，不丢行。"""
    s = session or _make_session()
    for row in rows[:limit]:
        art = fetch_ctax(row.get("url", ""), session=s, fetch_interval=0)
        if art.get("_error"):
            row["_error"] = art["_error"]
        else:
            row["content"] = art.get("content", "")
            for k in ("liability", "tags", "draft_date", "doc_number", "agency",
                      "nodeid", "column", "_note"):
                if art.get(k) and k not in row:
                    row[k] = art[k]
        time.sleep(READ_INTERVAL)
    return rows


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ---------------------------------------------------------------------------
# 栏目页这一档（不检索，直接翻清单）
# ---------------------------------------------------------------------------

def column_dir(node_id, session: requests.Session | None = None) -> str:
    """栏目 id → 目录段。栏目表里没有这一行时（表还没建、或站点新开了栏目），
    发一次空词检索，从行里的地址取——目录段猜不得，只能从站点给的地址里读。"""
    col = get_column(node_id) or {}
    if col.get("dir"):
        return col["dir"]
    j, _ = _search_once({"q": "", "nodeID": str(node_id), "pageNo": 1, "pageSize": 1,
                         "sort": SORT_ONLY, "siteID": SITE_ID,
                         "channel": CHANNEL_WEB}, session=session)
    for a in (j or {}).get("article") or []:
        seg = [x for x in re.sub(r"^https?://[^/]+", "", a.get("url") or "").split("/") if x]
        if len(seg) >= 4:
            return seg[0]
    return ""


def column_page_url(node_id, page: int = 1, dir_: str = "") -> str:
    stem = "node_%s" % node_id + ("" if page <= 1 else "_%d" % page)
    d = dir_ or (get_column(node_id) or {}).get("dir", "")
    return "%s/%s/%s.html" % (CTAX_BASE, d, stem) if d else "%s/%s.html" % (CTAX_BASE, stem)


# 栏目页的列表容器至少有五种形态（2026-10-19 九个取样栏目读到：div.newsList-item、
# ul.onlyTitleList、ul.ztNews-list-main、a.hsqj-list、ul.tabList），类名不统一，
# 但每一行的地址一律写成 /<目录>/<yyyy-mm>/<dd>/content_<id>.html——发布日期就在这条路径里
# （200 行检索样本里 URL 日期与行内 date 逐字符相同 200/200）。所以行只按地址形状取。
_ARTICLE_HREF_RE = re.compile(
    r"/(?P<dir>[a-z0-9_-]+)/(?P<y>\d{4})-(?P<m>\d{2})/(?P<d>\d{2})/content_(?P<aid>\d+)\.html",
    re.I)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href="(?P<href>[^"]*content_\d+\.html[^"]*)"[^>]*>'
                        r'(?P<text>.*?)</a>', re.S | re.I)
# 本栏自己那一段：从本栏标题 column-header-1 起，到侧栏 layout-380 或页脚 footer 之前。
# 切不出这一标题的页面（专题页与 tab 形态的栏目页）只能整页扫，靠目录段过滤。
_OWN_HEAD_RE = re.compile(r'class="column-header-1[^"]*"', re.I)
_SIDE_RE = re.compile(r'class="(?:layout-380|footer)[^"]*"', re.I)


def _own_region(page: str) -> tuple:
    """返回 (本栏那一段, 按没按标题切开)。"""
    h = _OWN_HEAD_RE.search(page)
    if not h:
        return page, False
    rest = page[h.end():]
    side = _SIDE_RE.search(rest)
    return (rest[:side.start()] if side else rest), True


def _is_date_line(ln: str) -> bool:
    """判「2026-06-22」「2026.6.22」这种整行只有日期的行：按分隔符切三段、每段全数字、首段四位。"""
    parts = re.split("[.-]", ln.strip())
    return len(parts) == 3 and len(parts[0]) == 4 and all(x.isdigit() for x in parts)


def _row_title(fragment: str) -> str:
    """行的可读标题。

    tab 形态那一类的 <a> 里，标题与发布日期写在同一枚锚的两三个 span 里，
    直接 _to_text 会把日期粘成标题的第二行；只把纯日期那几行剔掉，
    不砍第一行——真有两行标题的页面上线也不能被误伤。
    """
    lines = [ln for ln in _to_text(fragment).splitlines() if ln]
    keep = [ln for ln in lines if not _is_date_line(ln)]
    return " ".join(keep or lines)


def _article_id(href: str) -> str:
    m = re.search(r"content_([0-9]+)\.html", href)
    return m.group(1) if m else ""


def _page_rows(page: str, want_dir: str, col_name: str, only_ids: set | None = None) -> tuple:
    """取栏目页里属于本栏的行。返回 (行, 目录段不是本栏的行数, 按没按标题切开, 不在本栏检索集里的行数)。

    三道过滤各管一件事：先按本栏标题切出那一段（侧栏那几块是别的栏目的推荐位，
    「政策解读」那一页的侧栏就铺着图解税收、财税理论、税案通报三栏）；再按目录段扣一遍
    留在段内的外来行；切不出标题的那一类页面（tab 形态、专题页）目录段也不再能定性——
    同一目录下的兄弟栏长得一样，只能再拿本栏的检索集（`only_ids`）交一次。
    同一篇的配图链接与标题链接同址，按地址并成一行，标题取非空那一枚；
    并完仍没有文字的行丢掉（图片型行没有可读标题，不编造）。
    """
    region, sliced = _own_region(page)
    best, order, off_col, off_set = {}, [], 0, 0
    for m in _ANCHOR_RE.finditer(region):
        href = urljoin(CTAX_BASE, m.group("href"))
        a = _ARTICLE_HREF_RE.search(href)
        if not a:
            continue
        if a.group("dir").lower() != want_dir.lower():
            off_col += 1
            continue
        if only_ids is not None and _article_id(href) not in only_ids:
            off_set += 1
            continue
        if href not in best:
            best[href] = {
                "title": _row_title(m.group("text")), "url": href,
                "date": "%s-%s-%s" % (a.group("y"), a.group("m"), a.group("d")),
                "snippet": "", "column": col_name,
                "source": "中国税网 (ctaxnews.com.cn)", "source_label": "媒体解读"}
            order.append(href)
        elif m.group("text").strip() and not best[href]["title"]:
            best[href]["title"] = _row_title(m.group("text"))
    for chunk in _ROW_RE.finditer(region):
        t, sm = _ROW_TITLE_RE.search(chunk.group(1)), _ROW_SUMMARY_RE.search(chunk.group(1))
        if not t or not sm:
            continue
        key = urljoin(CTAX_BASE, t.group(1))
        if key in best:
            best[key]["snippet"] = _to_text(sm.group(1))[:200]
    rows = [best[k] for k in order if best[k]["title"]]
    return rows, off_col, sliced, off_set


def _column_id_set(node_id, session: requests.Session | None = None):
    """本栏最新的这一批 articleid。栏目页切不出本栏标题时，行的归属只能由它来判。

    返回 (集合, 错误说明)。pageSize 上限 5000 而本层只提交 sort=date，所以拿到的是
    **按发布日期倒序的最新 5000 行**；整栏超过这个数的栏目（取样里财税新闻 7,185 行）
    只可能漏掉老行，而栏目页铺出的正是最新那十几行，所以够用。
    """
    j, err = _search_once({"q": "", "nodeID": str(node_id), "pageNo": 1,
                           "pageSize": PAGE_SIZE_MAX, "sort": SORT_ONLY,
                           "siteID": SITE_ID, "channel": CHANNEL_WEB}, session=session)
    if j is None:
        return None, err
    return {str(a.get("articleid")) for a in (j.get("article") or [])}, ""


def list_column(column, pages: int = 1, session: requests.Session | None = None) -> dict:
    """按栏目列表页取清单：`/<目录>/node_<id>[_<页>].html`。

    每一页只铺 10 行，控件 `<ul pages='N' current='M'>` 报的 N **最大就是 10**：
    同一栏目用 nodeID 走检索有 1,648 行，栏目页第 11 页起 HTTP 500。
    所以这一档只回答"这一栏最新几篇是什么"，取全请回 search_ctax(node=..., fetch_all=True)。
    行按 `_page_rows` 取，不按某一种容器类名取——栏目页的列表容器至少有五种形态，
    只认 `div.newsList-item` 时九个取样栏目里有四个读出 0 行（读数见 NOTE.md「栏目页的行形态」）。
    父栏目页（如 /policy/node_24.html）整页只有子栏入口，0 行写在 _status 里，
    不静默报"这一栏是空的"。
    """
    if pages < 1:
        # 不收 max(pages, 1) 那一类回落：把"一页也不要"改成"取一页"，
        # 交出去的清单就不在请求里了。
        return {"results": [], "_status": f"没走成：pages={pages}，这个值一页也不要",
                "_error": "bad pages"}
    if str(column).isdigit():
        node_id = str(column)
    else:
        state, cand = column_lookup(str(column))
        if state == "ambiguous":
            return {"results": [],
                    "_status": f"没走成：栏目表里有 {len(cand)} 行对上「{column}」——"
                               f"{'；'.join(cand)}。站点确实有重名栏目，不替读者挑一条，"
                               f"请改按 nodeID 限定",
                    "_error": "ambiguous column"}
        if state != "ok":
            return {"results": [], "_status": f"没走成：栏目表里没有「{column}」，"
                                              f"没有把半截限定发出去；先跑 --update-columns",
                    "_error": "unknown column"}
        node_id = cand[0]
    s = session or _make_session()
    d = column_dir(node_id, session=s)
    if not d:
        return {"results": [], "_status": f"没走成：node_{node_id} 取不到目录段——"
                f"这一栏在检索里没有行（父栏目就是这种），栏目页只铺子栏入口",
                "_error": "no column dir"}
    rows, errs, declared = [], [], None
    unsliced, read, off_set_total = [], 0, 0
    allow = None
    col_name = (get_column(node_id) or {}).get("name") or f"node_{node_id}"
    for p in range(1, pages + 1):
        if p > COLUMN_PAGE_MAX:
            errs.append(f"第 {p} 页超出站点铺出的页数（栏目页最多 {COLUMN_PAGE_MAX} 页）")
            break
        url = column_page_url(node_id, p, dir_=d)
        try:
            r = _http_get(url, timeout=TIMEOUT_READ, session=s)
        except Exception as e:  # noqa: BLE001
            errs.append(f"第 {p} 页请求失败：{e}")
            break
        if r.status_code != 200:
            errs.append(f"第 {p} 页 HTTP {r.status_code}（{len(r.content)} 字节）")
            break
        page = r.text
        m = _PAGES_RE.search(page)
        if m:
            declared = int(m.group(1))
        found, off_col, sliced, off_set = _page_rows(page, d, col_name)
        if not sliced:
            if allow is None:
                ids, serr = _column_id_set(node_id, session=s)
                time.sleep(SEARCH_MIN_GAP)
                allow = ids if ids is not None else False
            if allow is False:
                errs.append(f"第 {p} 页切不出本栏标题，本栏的检索集也没取到（{serr}）——"
                            f"这一页的行定不了归属，整页不交")
                break
            found, off_col, sliced, off_set = _page_rows(page, d, col_name, allow)
            unsliced.append(p)
            off_set_total += off_set
        if off_col:
            errs.append(f"第 {p} 页丢掉目录段不是 {d} 的行 {off_col} 行（侧栏里其他栏目的推荐位）")
        rows += found
        read += 1
        if not found:
            errs.append(f"第 {p} 页没有取到行（父栏目页整页只有子栏入口就是这种；"
                        f"这一栏的清单请回检索那一路，命令行加 --column <nodeID>）")
            break
        time.sleep(READ_INTERVAL)
    status = f"取到 {len(rows)} 行，读了 {read} 页"
    if unsliced:
        status += (f"；第 {'、'.join(str(x) for x in unsliced)} 页切不出本栏标题"
                   f"（tab 形态与专题页是这种），已按本栏检索集交过一遍"
                   + (f"，扣掉不在集里的行 {off_set_total} 行" if off_set_total else ""))
    if declared is not None:
        status += f"；栏目页控件报 {declared} 页（站点栏目页最多铺 {COLUMN_PAGE_MAX} 页，" \
                  f"整栏行数回 search_ctax(node=…) 用 foundNum 看）"
    out = {"results": rows, "_status": status, "_pages": read, "_column": node_id}
    if errs:
        out["_page_errors"] = errs
    return out


# ---------------------------------------------------------------------------
# 栏目表
# ---------------------------------------------------------------------------

def load_columns() -> list:
    if not COLUMNS_FILE.exists():
        return []
    try:
        return json.loads(COLUMNS_FILE.read_text(encoding="utf-8")).get("columns", [])
    except Exception:  # noqa: BLE001
        return []


def get_column(node_id) -> dict | None:
    for c in load_columns():
        if str(c.get("nodeid")) == str(node_id):
            return c
    return None


def column_label(c: dict) -> str:
    return f"{c.get('name')}(node_{c.get('nodeid')}, {c.get('foundNum')} 篇)"


def column_lookup(name: str) -> tuple:
    """栏目名 → (判据, 候选)。三种判据分开交，因为「查不到」与「同名多栏」的回法不同。

    ok 一个命中，nodeID 在候选第一项；ambiguous 栏目表里有两个以上行对上——站点确实有
    重名栏目（2026-10-19 那次重建里「实务分析」「疑难解答」「申报指引」「税务行动」各两行），
    这是站点自己的挂法，不能替读者挑一条；missing 表里对不上。
    """
    want = (name or "").strip()
    if not want:
        return "missing", []
    cols = load_columns()
    hits = [c for c in cols if c.get("name") == want or c.get("dir") == want]
    if not hits:
        hits = [c for c in cols
                if want in (c.get("name") or "") or (c.get("name") or "") in want]
    if len(hits) == 1:
        return "ok", [str(hits[0]["nodeid"])]
    if len(hits) > 1:
        return "ambiguous", [column_label(c) for c in hits]
    return "missing", []


def nodeid_of(name: str) -> str:
    """栏目名 → nodeID；查不到或有歧义都返回空串（调用方整体报缺，不发半截限定）。"""
    state, ids = column_lookup(name)
    return ids[0] if state == "ok" else ""


def column_names() -> list:
    return [f"{c.get('name')}({c.get('dir')}/node_{c.get('nodeid')}, {c.get('foundNum')} 篇)"
            for c in load_columns()]


NAV_PLACEHOLDER_IDS = {"15"}
NAV_PLACEHOLDER_WORDS = {"更多", "more"}


def _is_nav_placeholder(name: str) -> bool:
    """首页与栏目页上「更多 >>」「|更多|」「more」这类入口链接不是栏目名。

    去掉空白、竖线与尖括号后正好等于 `NAV_PLACEHOLDER_WORDS` 里那两枚之一的才算占位；
    带着别号的（「更多 2024」）不判成占位，宁可多留一行也不能把真栏目删掉。
    """
    core = re.sub(r"[\s|<>]+", "", name or "").lower()
    return core in NAV_PLACEHOLDER_WORDS


def update_columns(session: requests.Session | None = None,
                   max_nodes: int = 120) -> list:
    """重建 data/ctax_columns.json：首页导航 → 每个 id 一次空词检索取行数与行内栏目名，
    父栏目（空词回 0 行）再进它的栏目页把子节点多捞一层。

    栏目名有两个来源，优先级是实测出来的：行内 `nodesearchname` 是站点自己的叫法，
    拿得到就用它；导航锚文本只作父栏目的退路（首页那串「政 策」带排版空格，
    「影像 | 」带分隔符，所以只压锚文本的空白、不压 `nodesearchname` 里的空格——
    「财政部 国家税务总局公告」中间那个空格就是站点的写法）。子栏的名字不从父栏继承：
    父栏叫「政策」不等于它下面每一栏都能叫政策，名字取不出的节点只进检索队列、不进表。
    空词检索 foundNum=0 记成父栏目（parent=True）——那**不是这一栏没有文章**，
    是检索只按叶节点收（2026-10-19 实测 nodeID=24 父节点 0、其子节点 8215 有 23）。
    这一类父栏目即使没名字也要展开：政策那一簇的叶栏（增值税、企业所得税）挂在
    空锚的父栏目页里，跳过它们就只剩十几个能按名限定的栏目。
    「更多 >>」那类占位链接按 `NAV_PLACEHOLDER_IDS` 与 `_is_nav_placeholder` 去掉。
    """
    s = session or _make_session()
    try:
        page = _http_get(CTAX_BASE + "/", timeout=TIMEOUT_READ, session=s).text
    except Exception as e:  # noqa: BLE001
        print("首页取不到，栏目表没有重建：%s" % e, file=sys.stderr)
        return load_columns()
    nav, navname = _nav_from_page(page)
    probed = {}
    spent = 0

    def probe(nid, dir_hint=""):
        j, err = _search_once({"q": "", "nodeID": nid, "pageNo": 1, "pageSize": 10,
                               "sort": SORT_ONLY, "siteID": SITE_ID,
                               "channel": CHANNEL_WEB}, session=s)
        if j is None:
            print("  node_%s: 取不到（%s），跳过" % (nid, err), file=sys.stderr)
            return None
        arts = j.get("article") or []
        name = (arts[0].get("nodesearchname") or "").strip() if arts else ""
        d = dir_hint
        if arts:
            seg = [x for x in re.sub(r"^https?://[^/]+", "",
                                     arts[0].get("url") or "").split("/") if x]
            if len(seg) >= 4:
                d = seg[0]
        return {"nodeid": nid, "name": name or navname.get(nid, ""), "dir": d,
                "foundNum": j.get("foundNum"), "parent": not arts}

    for nid in [n for n in nav if n not in NAV_PLACEHOLDER_IDS]:
        if spent >= max_nodes:
            break
        c = probe(nid, nav.get(nid, ""))
        spent += 1
        time.sleep(SEARCH_MIN_GAP)
        if c:
            probed[nid] = c

    # 父栏目往下捞一层：这一轮用展开前的快照，孙辈不再展开（政策那一簇两层就够）
    for nid in [n for n, c in probed.items() if c["parent"] and c["dir"]]:
        if spent >= max_nodes:
            break
        try:
            p2 = _http_get(column_page_url(nid, dir_=probed[nid]["dir"]),
                           timeout=TIMEOUT_READ, session=s).text
        except Exception:  # noqa: BLE001
            continue
        child_nav, child_name = _nav_from_page(p2)
        time.sleep(READ_INTERVAL)
        for cid in child_nav:
            if cid in probed or cid in NAV_PLACEHOLDER_IDS or spent >= max_nodes:
                continue
            cc = probe(cid, child_nav[cid])
            spent += 1
            time.sleep(SEARCH_MIN_GAP)
            if cc is None:
                continue
            cc["name"] = cc["name"] or child_name.get(cid, "")
            if probed[nid]["name"]:
                cc["parent_of"] = nid
            probed[cid] = cc

    cols = [c for c in probed.values()
            if c["name"] and not _is_nav_placeholder(c["name"])]
    drop_no_name = [c["nodeid"] for c in probed.values()
                    if not c["name"] or _is_nav_placeholder(c["name"])]
    out = sorted(cols, key=lambda c: (c["parent"], -int(c["foundNum"] or 0)))
    print("发出 %d 次探测、取回 %d 个节点，进表 %d 个；名字取不出或属占位链接的 %d 个"
          % (spent, len(probed), len(out), len(drop_no_name)), file=sys.stderr)
    if drop_no_name:
        print("  没进表的节点：" + "、".join(drop_no_name), file=sys.stderr)
    COLUMNS_FILE.parent.mkdir(exist_ok=True)
    payload = json.dumps(
        {"_note": "由 scripts/tax_ctax.py --update-columns 生成；parent=True 的行只能"
                  "从栏目页看入口，检索按叶节点收所以 foundNum=0；"
                  "name 优先取行内 nodesearchname，父栏目取导航锚文本",
         "columns": out}, ensure_ascii=False, indent=1)
    COLUMNS_FILE.write_text(payload + chr(10), encoding="utf-8")
    return out


def _nav_from_page(page: str) -> tuple:
    """从一页里取出 node_<id>.html 的目录段与锚文本（两样都只从页面链接里读，不猜）。

    锚文本取 `href` 之后第一个 `>` 到下一个 `<` 之间的那一段。排版导航习惯在两个汉字
    中间塞空格（首页「政 策」），栏目名里没有空格，所以只压导航锚文本的空白，
    行内 `nodesearchname` 原样保留（「财政部 国家税务总局公告」中间那个空格是站点写法）。
    """
    nav, navname = {}, {}
    for m in _NODE_LINK_RE.finditer(page):
        href, nid = m.group(1), m.group(2)
        if nid not in nav:
            seg = [x for x in re.sub(r"^https?://[^/]+", "",
                                     urljoin(CTAX_BASE, href)).split("/") if x]
            nav[nid] = seg[0] if len(seg) >= 2 else ""
        if nid not in navname:
            tail = page[m.end():m.end() + 300]
            gt = tail.find(">")
            if 0 <= gt < 200:
                body = tail[gt + 1:]
                lt = body.find("<")
                text = re.sub(r"\s+", "", _to_text(body[:lt if lt >= 0 else 60]))
                if text:
                    navname[nid] = text[:24]
    return nav, navname


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="中国税网 (ctaxnews.com.cn) 取数")
    ap.add_argument("keyword", nargs="?", default="", help="检索词（一个概念一枚词，站点不分词）")
    ap.add_argument("--size", type=int, default=5,
                    help="取回多少篇（按合并同稿后的篇数算，默认 5；正整数）")
    ap.add_argument("--all", action="store_true", help="按站点报的总条数翻到底")
    ap.add_argument("--max-rows", type=int, default=0,
                    help="行数上限：只收紧不放大（与 --size 取小的那一份），"
                         "配合 --all 决定翻到几页就停")
    ap.add_argument("--column", default="", help="限定栏目（栏目名或 nodeID，只认叶节点）")
    ap.add_argument("--from", dest="date_from", default="", help="发布日期起 yyyy-mm-dd")
    ap.add_argument("--to", dest="date_to", default="", help="发布日期止 yyyy-mm-dd")
    ap.add_argument("--match", choices=list(MATCH_POSITIONS), default="content",
                    help="匹配域：全文（默认）或只标题")
    ap.add_argument("--channel", type=int, default=CHANNEL_WEB,
                    help="1=网站（默认）；2=触屏端只有 2019 年转载且地址已失效；"
                         "0=不提交这一枚、取两端并集；其余值不发请求")
    ap.add_argument("--read", action="store_true", help="逐篇取回正文")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--find-doc", metavar="文号", help="按文号回站内定位（合并同稿多栏）")
    ap.add_argument("--list", metavar="栏目", help="不检索，按栏目列表页取清单")
    ap.add_argument("--pages", type=int, default=1, help="配合 --list：读几页（站点只铺 10 页）")
    ap.add_argument("--columns", action="store_true", help="打印本地栏目表，不发请求")
    ap.add_argument("--update-columns", action="store_true", help="重建栏目表并写盘")
    a = ap.parse_args(argv)

    s = _make_session()
    if a.columns:
        for c in load_columns():
            print(f"{c['nodeid']:>6}  {'父栏目' if c.get('parent') else '    '} "
                  f"{c.get('foundNum', 0):>7} 篇  {c.get('dir',''):<9} {c.get('name','')}")
        if not load_columns():
            print("栏目表是空的，先跑 --update-columns")
        return 0
    if a.update_columns:
        cols = update_columns(session=s)
        print(f"重建完成：{len(cols)} 个节点，其中 {sum(1 for c in cols if c.get('parent'))} 个父栏目")
        return 0

    if a.list:
        res = list_column(a.list, pages=a.pages, session=s)
    elif a.find_doc:
        res = find_by_doc_number(a.find_doc, session=s)
    else:
        res = search_ctax(a.keyword, size=a.size, node=a.column or None,
                          date_from=a.date_from, date_to=a.date_to, match=a.match,
                          channel=a.channel, fetch_all=a.all, max_rows=a.max_rows,
                          session=s)
    rows = res.get("results", [])
    if a.read and rows:
        rows = read_articles(rows, limit=min(a.size or 5, len(rows)), session=s)
    if a.json:
        print(json.dumps(res if not a.read else {"results": rows},
                         ensure_ascii=False, indent=1))
        return 0
    if "total" in res:
        print(f"站点报的总条数：{res.get('total')}；{res.get('_status','')}")
    else:
        print(res.get("_status", ""))
    for k in ("_filter_note", "_page_errors", "_queries_tried", "_caveat"):
        if res.get(k):
            print(f"{k}: {res[k]}")
    for i, r in enumerate(rows, 1):
        line = [f"{i:>3}. {r.get('title','')}"]
        meta = " | ".join(x for x in (r.get("date", ""), r.get("column", ""),
                                      r.get("doc_number", ""), r.get("agency", ""),
                                      r.get("byline", "")) if x)
        if meta:
            line.append("     " + meta)
        line.append("     " + r.get("url", ""))
        if r.get("also_in_columns"):
            line.append("     同稿还挂在：" + "、".join(r["also_in_columns"]))
        if r.get("_url_state"):
            line.append("     ⚠ " + r["_url_state"])
        if r.get("liability") or r.get("tags"):
            line.append("     站点标注：" + " ".join(
                x for x in (r.get("liability", ""),
                            "税种：" + "、".join(r["tags"]) if r.get("tags") else "") if x))
        if r.get("snippet"):
            line.append("     " + r["snippet"][:120])
        if r.get("content"):
            body = r["content"].replace("\n", " ")
            line.append(f"     正文 {len(r['content'])} 字：" + body[:300])
        if r.get("_error"):
            line.append("     取正文失败：" + r["_error"])
        print("\n".join(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
