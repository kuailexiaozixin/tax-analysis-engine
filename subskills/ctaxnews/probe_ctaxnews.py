#!/usr/bin/env python3
"""
中国税网（www.ctaxnews.com.cn）站点体检 — 把 `subskills/ctaxnews/SKILL.md` 与 `NOTE.md`
里那些带日期的实测数字变成一条命令。

为什么需要它：这一站的可用面全长在外网上——`Search.do` 的参数值域、检索行的 15 枚键、
栏目页的行容器、2023-02 前后的地址分界、正文页那两对 `enpcontent` 标记写几遍。
这些事实写进文档就成了会过期的声明；有了探针，怀疑文档过期时重跑一遍即可对照。

它只读、**不调模型、零额度消耗**；每一项自己声明发几次请求，末尾把总数吐出来，
免得读者按"跑了多少分钟"猜花了多少。栏目页与正文页一律走发货代码那一份判据
（`tax_ctax._page_rows`、`_own_region`、`_column_id_set`、`_address_state`、`fetch_ctax`），
探针不另立判据。`--fast` 跳过第 10 项（栏目表重建一轮：2026-10-19 实发 102 次，
随站点当期节点数与父栏目数浮动，声明值与实发可以两头差）。

各项之间靠 `_CACHE` 接力：第 4 项的检索行供第 6 项挑页型，第 5 项的政策叶栏行供
第 6 项凑政策页，第 7 项自己取老行尾段。单跑某一项时缺上游缓存的，会就地补一次检索。

Usage:
  python subskills/ctaxnews/probe_ctaxnews.py                    # 全项
  python subskills/ctaxnews/probe_ctaxnews.py --fast             # 不重建栏目表
  python subskills/ctaxnews/probe_ctaxnews.py --items 2,4,7      # 只跑指定项
  python subskills/ctaxnews/probe_ctaxnews.py --keyword 留抵退税 # 换位置参数那一项用的词
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "scripts"))

import tax_ctax as C                                  # noqa: E402

HEAD = {"请求数": 0}
_CACHE = {}                                          # 项间接力：行池、政策行、老行


def _r(params, session):
    """打一次 Search.do 并计数。返回 (json, 错误)。"""
    HEAD["请求数"] += 1
    return C._search_once(params, session=session)


def _get(url, session, params=None):
    HEAD["请求数"] += 1
    return C._http_get(url, params=params, timeout=C.TIMEOUT_READ, session=session)


def _post(url, session, data=None):
    """第 12 项要用站点自己的提交法（POST 表单）比一次字节，本层代码只发 GET。"""
    HEAD["请求数"] += 1
    return session.post(url, data=data, timeout=C.TIMEOUT_SEARCH)


def _col_ids(node_id, session):
    """`_column_id_set` 内部发一次空词 nodeID 检索，走的是站点函数不是本文件的 `_r`，
    不包一层就漏计进「实际发出」那一行。"""
    HEAD["请求数"] += 1
    return C._column_id_set(node_id, session=session)


def _q(word="", **kw):
    p = {"q": word, "pageNo": 1, "pageSize": C.SEARCH_PAGE_SIZE,
         "sort": C.SORT_ONLY, "siteID": C.SITE_ID, "channel": C.CHANNEL_WEB}
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def _line(text, value=""):
    print("  %-56s %s" % (text, value))


def _head_id(j):
    return ((j or {}).get("article") or [{}])[0].get("articleid")


# ---------------------------------------------------------------------------
# 1. 通路：域名与镜像同一份数
# ---------------------------------------------------------------------------
def item1(session, kw):
    print("[1] 两个落点回不回同一个数（词：%s）" % kw)
    out = []
    for endpoint in C.SEARCH_ENDPOINTS:
        j, err = _probe_endpoint(endpoint, _q(kw), session)
        out.append((endpoint.split("/")[2], (j or {}).get("foundNum"), err))
        time.sleep(C.SEARCH_MIN_GAP)
    for host, found, err in out:
        _line(host, "foundNum=%s%s" % (found, "（%s）" % err if err else ""))
    same = len({f for _, f, _ in out if f is not None}) <= 1
    print("  => 两路同数：%s" % ("是" if same else "否，镜像那一路已经不同数"))


def _probe_endpoint(endpoint, params, session):
    HEAD["请求数"] += 1
    try:
        r = C._http_get(endpoint, params=params, timeout=C.TIMEOUT_SEARCH,
                        session=session)
    except Exception as e:                                             # noqa: BLE001
        return None, "请求失败：%s" % e
    if r.status_code != 200:
        return None, "HTTP %s（%d 字节）" % (r.status_code, len(r.content))
    try:
        return r.json(), ""
    except Exception as e:                                             # noqa: BLE001
        return None, "回的不是 JSON：%s" % e


# ---------------------------------------------------------------------------
# 2. 翻页基准
# ---------------------------------------------------------------------------
def item2(session, kw):
    print("[2] 翻页只认 pageNo（别名会被静默忽略）")
    base, _ = _r(_q(kw, pageNo=1), session)
    time.sleep(C.SEARCH_MIN_GAP)
    first = _head_id(base)
    j2, _ = _r(_q(kw, pageNo=2), session)
    time.sleep(C.SEARCH_MIN_GAP)
    second = _head_id(j2)
    j0, _ = _r(_q(kw, pageNo=0), session)
    time.sleep(C.SEARCH_MIN_GAP)
    zero = _head_id(j0)
    _line("pageNo=1 首行 / pageNo=2 首行 / pageNo=0 首行",
          "%s / %s / %s" % (first, second, zero))
    print("  => 0 与 1 同页：%s；2 换页：%s"
          % (zero == first, second != first))
    alias_hit = []
    for name in ("start", "page", "pageNum", "p", "offset", "currentPage"):
        j, _ = _r(_q(kw, **{name: 2}), session)
        time.sleep(C.SEARCH_MIN_GAP)
        alias_hit.append("%s=%s" % (name, "仍第一页" if _head_id(j) == first else "换页了"))
    _line("六枚别名各自提交 2 之后", " ".join(alias_hit))
    print("  => 期望：六枚全写「仍第一页」。出现「换页了」说明站点改了参数名")


# ---------------------------------------------------------------------------
# 3. 值域越界的回法
# ---------------------------------------------------------------------------
# 每一档都同时取 foundNum 与首行 articleid：只报 foundNum 判不动 sort——
# 站点换排序时总数不变，只有首行那枚编号会变。
def item3(session, kw):
    print("[3] 越界取值是 500、空结构还是 0 条")
    cases = (
        ("sort=date（基准）", _q(kw)),
        ("sort=score", _q(kw, sort="score")),
        ("sort=relevance", _q(kw, sort="relevance")),
        ("startDate=毫秒", _q(kw, startDate="1700000000000")),
        ("startDate=斜杠形态", _q(kw, startDate="2025/01/01")),
        ("startDate+endDate 合法成对", _q(kw, startDate="2025-01-01",
                                            endDate="2025-12-31")),
        ("只给 startDate", _q(kw, startDate="2025-01-01")),
        ("只给 endDate", _q(kw, endDate="2025-12-31")),
        ("起止倒挂", _q(kw, startDate="2026-12-31", endDate="2026-01-01")),
        ("channel=0", _q(kw, channel=0)),
        ("channel=3", _q(kw, channel=3)),
        ("channel=20", _q(kw, channel=20)),
        ("channel=-1", _q(kw, channel=-1)),
        ("siteID=1", dict(_q(kw), siteID=1)),
        ("siteID=3", dict(_q(kw), siteID=3)),
        ("sort=空串", _q(kw, sort="")),
        ("sort=orderbypubtime", _q(kw, sort="orderbypubtime")),
        ("paperID=1", dict(_q(kw), paperID=1)),
        ("paperID=999", dict(_q(kw), paperID=999)))
    base_head = None
    for label, params in cases:
        HEAD["请求数"] += 1
        try:
            r = C._http_get(C.SEARCH_ENDPOINTS[0], params=params,
                            timeout=C.TIMEOUT_SEARCH, session=session)
            body = r.text
            size = len(body.encode("utf-8"))
            found = head = None
            try:
                j = json.loads(body)
                found = j.get("foundNum")
                head = ((j.get("article") or [{}])[0]).get("articleid")
            except Exception:                                          # noqa: BLE001
                pass
            if base_head is None:
                base_head = head
                _line(label, "HTTP %s，%d 字节，foundNum=%s，首行=%s"
                      % (r.status_code, size, found, head))
            else:
                _line(label, "HTTP %s，%d 字节，foundNum=%s，首行=%s%s"
                      % (r.status_code, size, found, head,
                         "" if head in (None, base_head) else "（与基准不同序）"))
        except Exception as e:                                         # noqa: BLE001
            _line(label, "请求失败：%s" % e)
        time.sleep(C.SEARCH_MIN_GAP)
    print("  => 期望：日期给毫秒或斜杠回 500；倒挂 foundNum=0；单侧日期照常收；"
          "channel 0/3/20/-1 与 siteID 1/3 回 37 字节空结构。sort 看「与基准不同序」——"
          "换序说明站点收这个值，「sort 只认 date」那条判据作废；只有回 500 才叫越界；"
          "空串与 orderbypubtime 是壳里注释掉的那个下拉框的两枚取值，纸面 ID 两档看是否收窄")


# ---------------------------------------------------------------------------
# 4. 位置参数各单发 + q 盖位置参数
# ---------------------------------------------------------------------------
def item4(session, kw):
    print("[4] 匹配域靠提交哪一枚参数名（词：%s）" % kw)
    counts = {}
    for name in ("q", "title", "content", "keyword", "author"):
        p = {name: kw, "pageNo": 1, "pageSize": C.SEARCH_PAGE_SIZE,
             "sort": C.SORT_ONLY, "siteID": C.SITE_ID, "channel": C.CHANNEL_WEB}
        j, _ = _r(p, session)
        counts[name] = (j or {}).get("foundNum")
        if name == "q":
            _CACHE["qrows"] = (j or {}).get("article") or []
        time.sleep(C.SEARCH_MIN_GAP)
    _line("五枚各自单发的 foundNum",
          " ".join("%s=%s" % (k, v) for k, v in counts.items()))
    both = dict(_q(kw))
    both["title"] = kw
    j, _ = _r(both, session)
    time.sleep(C.SEARCH_MIN_GAP)
    _line("各档 foundNum 的取样口径",
          "channel=%s 显式提交、pageSize=%d" % (C.CHANNEL_WEB, C.SEARCH_PAGE_SIZE))
    print("  => q 与 title 同发 foundNum=%s；q 单发=%s（相等说明 q 把位置参数盖掉）"
          % ((j or {}).get("foundNum"), counts.get("q")))
    # 「q 不分词、按多枚词各自命中后取交集」这条判据的可证伪形式，三发各打一处：
    #   粘一枚库里没有的词必须回 0（否则站点是按词取并）；
    #   两枚真词的正序与倒序必须同数（不同数说明空格那一段进了整串匹配，顺序敏感）；
    #   单发第二枚词作基准（交集不可能比它大；相等则说明第一枚没参与收窄）。
    second = "留抵退税"
    extra = {}
    for label, word in (("只发第二枚词「%s」" % second, second),
                        ("q 粘一枚库里没有的词", kw + "zzz不存在的检索词zzz"),
                        ("q 两枚真词，正序", "%s %s" % (kw, second)),
                        ("q 两枚真词，倒序", "%s %s" % (second, kw))):
        j2, _ = _r(_q(word), session)
        extra[label] = (j2 or {}).get("foundNum")
        time.sleep(C.SEARCH_MIN_GAP)
        _line(label, "foundNum=%s（「%s」单发=%s）" % (extra[label], kw, counts.get("q")))
    print("  => 读数分档：粘词回 0 = 不按词取并；正序 %s 与倒序 %s 同数 = 顺序不参与匹配，"
          "「整串匹配」的写法要改成「按词取交集」；两枚同现的数若等于单发「%s」那一档（%s），"
          "说明第一枚没参与收窄，多词限定在这一路其实无效"
          % (extra.get("q 两枚真词，正序"), extra.get("q 两枚真词，倒序"),
             second, extra.get("只发第二枚词「%s」" % second)))


# ---------------------------------------------------------------------------
# 5. 父节点与叶节点
# ---------------------------------------------------------------------------
def item5(session, kw):
    print("[5] nodeID 只按叶节点收，以及栏目限定与日期区间同发时两样都生效")
    node63_total = None
    for nid, want in (("24", "父（政策）应 0"), ("8210", "父（文章页面包屑里的「2026年（税种）」）应 0"),
                      ("672", "父（法规专题）应 0"), ("8196", "父（2026年重要财税法规合集）应 0"),
                      ("8215", "叶 有行"),
                      ("8030", "叶 增值税"), ("63", "叶 地方税收法规")):
        j, _ = _r(_q("", nodeID=nid, pageSize=10), session)
        rows = (j or {}).get("article") or []
        _line("nodeID=%s %s" % (nid, want),
              "foundNum=%s，取回 %d 行%s"
              % ((j or {}).get("foundNum"), len(rows),
                 "，首行 %s" % rows[0].get("nodesearchname") if rows else ""))
        if nid == "8215":
            _CACHE["policyrows"] = rows
        if nid == "63":
            node63_total = (j or {}).get("foundNum")
        time.sleep(C.SEARCH_MIN_GAP)
    print("  => 父节点回 0 但它下面挂着文章：这一条只能当"
          "「检索不按子树收」，不能读成「这一栏没有内容」")
    # 正控：栏目限定与日期区间同发时「回 0」有两种来路（过滤生效、这一窗里确实没行 /
    # 参数根本没生效、被换成整站）。只跟同一轮上面取到的同栏基准比，不引用本机副本。
    for label, d1, d2 in (("2024 全年", "2024-01-01", "2024-12-31"),
                          ("2016~2019", "2016-01-01", "2019-12-31")):
        j, _ = _r(_q("", nodeID="63", pageSize=10, startDate=d1, endDate=d2), session)
        time.sleep(C.SEARCH_MIN_GAP)
        _line("node_63 + %s" % label,
              "foundNum=%s（同栏不带日期的基准=%s）"
              % ((j or {}).get("foundNum"), node63_total))
    # 栏目表能不能捞到政策那一族的深层节点：先看 node_24.html 这一页铺出的链接，
    # 再对着本地栏目表数这一枚到底进没进表（读文件，不另发请求）。
    try:
        page = _get(C.column_page_url("24", dir_="policy"), session).text
        ids = sorted({m for m in re.findall(r"node_(\d+)(?:_\d+)?\.html", page)}, key=int)
        _line("node_24.html 里的 node_ 编号", "%d 枚：%s" % (len(ids), " ".join(ids)))
        have = {str(c.get("nodeid")) for c in C.load_columns()}
        _line("8215 / 8196 / 672 / 8210 在两处的归属", " ".join(
            "%s=本页%s、栏目表%s" % (x, "在" if x in ids else "不在", "有行" if x in have else "无行")
            for x in ("8215", "8196", "672", "8210")))
    except Exception as e:                                             # noqa: BLE001
        _line("node_24.html", "取不到：%s" % e)
    print("  => 期望：有行的那一窗回一个既非 0 也非基准的数（栏目与日期两样同时在收，"
          "且日期没被静默换成整栏），早窗那一发回 0；两发都回基准才说明日期这一档没生效。"
          "上面四枚空词全回 0 只说明检索不按子树收；它们进不进栏目表是另一件事——"
          "表是从首页导航加十枚父栏目页各展开一层拼出来的，同一枚编号可能从别的页进来"
          "（当期 8196 有行、但不是从 node_24 来的），所以「不在这一页的链接里」不能读成「表里没有」。")


# ---------------------------------------------------------------------------
# 6. 正文页两对标记写几遍、两份是否同字节
# ---------------------------------------------------------------------------
def item6(session, kw):
    print("[6] enpcontent / enpproperty 两对标记的块数与一致性")
    rows = list(_CACHE.get("policyrows") or []) + list(_CACHE.get("qrows") or [])
    if not rows:                                       # 单跑第 6 项：就地取一份行池
        j, _ = _r(_q(kw, pageSize=C.SEARCH_PAGE_SIZE), session)
        time.sleep(C.SEARCH_MIN_GAP)
        rows = (j or {}).get("article") or []
    picked, seen = [], set()
    for r in rows:                                     # 政策叶栏先来两篇
        u = r.get("url") or ""
        if (r.get("articleid") in seen or C._address_state(u) != "ok"
                or len(picked) >= 2):
            break
        if "政策" in (r.get("nodesearchname") or "") or "/policy/" in u:
            picked.append(r)
            seen.add(r.get("articleid"))
    for want in ("增值税", "企业所得税", "财税新闻", "政策解读", "zt"):
        for r in rows:
            u = r.get("url") or ""
            if r.get("articleid") in seen or C._address_state(u) != "ok":
                continue
            if want in (r.get("nodesearchname") or "") or want in u:
                picked.append(r)
                seen.add(r.get("articleid"))
                break
    _line("挑中的页型（栏目 · 目录段 · 编号）",
          " ".join("%s·%s·%s" % (r.get("nodesearchname"), r["url"].split("/")[3],
                                 r.get("articleid")) for r in picked[:6]))
    liab = []
    for r in picked[:6]:
        try:
            page = _get(r["url"], session).text
        except Exception as e:                                         # noqa: BLE001
            _line(r["url"][-40:], "取不到：%s" % e)
            continue
        blocks = C._BODY_RE.findall(page)
        props = C._PROP_RE.findall(page)
        same = "两份逐字节相同" if len(blocks) == 2 and blocks[0] == blocks[1] else (
            "两份不一致" if len(blocks) == 2 else "只有一块")
        # 页面层的两枚站点自标字段：按份数记，块数与标注有没有关系要在这行上读
        lv = re.findall(r"<liability>(.*?)</liability>", "".join(props))
        tg = re.findall(r"<tag>(.*?)</tag>", "".join(props))
        liab.append((r.get("nodesearchname"), lv))
        _line("%s（%s · %s）" % (r["nodesearchname"], r["url"].split("/")[3],
                                 re.search(r"content_(\d+)", r["url"]).group(1)),
              "正文块 %d 份、property %d 份、%d 字，%s；liability %d 份有值 %d、tag %d 份非空 %d"
              % (len(blocks), len(props), len(blocks[0]) if blocks else 0, same,
                 len(lv), sum(1 for x in lv if x.strip()),
                 len(tg), sum(1 for x in tg if x.strip())))
        time.sleep(C.READ_INTERVAL)
    _line("liability 有值与栏目名的对应",
          " ".join("%s=%s" % (n, "有" if any(x.strip() for x in v) else "空")
                   for n, v in liab) or "本轮没量到页")
    print("  => 期望：块数（1 份或 2 份逐字节相同）与栏目目录段没有对应关系，只能逐页量；"
          "出现「两份不一致」就把 SKILL.md 第五步那句「取第一份」改成需要判断的写法。"
          "liability 那一行是文档里「站点自标的时效标注」整节的复现处，"
          "有值的页型与空值的页型各写清楚，别按页型猜")


# ---------------------------------------------------------------------------
# 7. 老地址：原样取不到、目录段猜不中
# ---------------------------------------------------------------------------
def item7(session, kw):
    print("[7] 三段老地址能不能救回来")
    old = _find_old_row(session)
    if old is None:
        print("  跳过：这一轮在 node_63 尾段没取到 3 段老行"
              "（站点可能已补齐历史地址，此时该复核 NOTE.md 的分界表）")
        return
    url = old["url"]
    _line("行内地址", "%s（%s）" % (url, C._address_state(url)[:20]))
    for label, target in _guess_urls(url):
        try:
            r = _get(target, session)
            _line(label, "HTTP %s，%d 字节" % (r.status_code, len(r.content)))
        except Exception as e:                                         # noqa: BLE001
            _line(label, "请求失败：%s" % e)
        time.sleep(C.READ_INTERVAL)
    print("  => 期望：原地址与五枚目录名补段全回 500。任何一个回 200 "
          "就说明目录段可推，`_address_state` 那条判据要改")


def _find_old_row(session):
    """按日期倒序，3 段老行只在这一栏的尾段——所以直接取到末段，不翻第一页。

    node_63（地方税收法规）整栏 1,393 行、一半以上是 2023-02 前的老行，
    末段 pageNo 由 foundNum 除以 pageSize 向上取整得到。
    """
    j, _ = _r(_q("", nodeID="63", pageSize=C.SEARCH_PAGE_SIZE), session)
    time.sleep(C.SEARCH_MIN_GAP)
    total = (j or {}).get("foundNum")
    if not total:
        return None
    tail = -(-int(total) // C.SEARCH_PAGE_SIZE)
    j2, _ = _r(_q("", nodeID="63", pageSize=C.SEARCH_PAGE_SIZE, pageNo=tail), session)
    time.sleep(C.SEARCH_MIN_GAP)
    _line("尾段取样", "node_63 共 %s 行，取末段 pageNo=%d 的 %d 行"
          % (total, tail, len((j2 or {}).get("article") or [])))
    return next((r for r in ((j2 or {}).get("article") or [])
                 if C._address_state(r.get("url") or "") != "ok"), None)


def _guess_urls(url):
    path = re.sub(r"^https?://[^/]+", "", url)
    yield "原样（http 域）", "http://www.ctaxnews.com.cn" + path
    yield "原样改 https", "https://www.ctaxnews.com.cn" + path
    for d in ("policy", "news", "sssw", "sswfaj", "zt"):
        yield "补目录 %s" % d, "https://www.ctaxnews.com.cn/" + d + path


# ---------------------------------------------------------------------------
# 8. 栏目页行形态
# ---------------------------------------------------------------------------
# 候选类名清单里 `hsqj-list` 与 `tabList` 两枚来自更早一轮的读数，本轮按目录段全扫复量：
# 只要它们在任何一个目录下 0 处命中，文档里那一句就得改。
_ROW_FORMS = ("newsList-item", "onlyTitleList", "ztNews-list-main", "hsqj-list", "tabList")


def _row_counts(page):
    """一栏目页的四档行计数：`class="newsList-item"` 精确串、同串前缀（写成 clearfix 那种也算）、
    五枚候选类名出现在任一 class 属性里的次数、`_ARTICLE_HREF_RE` 的地址形状命中处数。"""
    tok = {k: len(re.findall(r'class="[^"]*\b%s\b[^"]*"' % k, page)) for k in _ROW_FORMS}
    return (page.count('class="newsList-item"'), page.count('class="newsList-item'),
            tok, len(C._ARTICLE_HREF_RE.findall(page)))


def item8(session, kw):
    print("[8] 栏目页的行容器、地址形状与翻页上限")
    hit_pages = {k: 0 for k in _ROW_FORMS}   # 该候选类名在几页上出现过
    sliced_pages = [0]                        # 能按 column-header-1 切出本栏段的页数
    declared = set()                          # 各页 `<ul pages='N'>` 控件报的 N
    laid = []                                 # 各页 `_page_rows` 交回的行数
    date_anchor_pages = []                    # 各页里标题与日期同处一枚 <a> 的行数
    covered = set()
    n_pages = 0

    def _measure(nid, dir_, name, page, page_no="1"):
        nonlocal n_pages
        exact, prefix, tok, shape = _row_counts(page)
        rows, off_col, sliced, _ = C._page_rows(page, dir_, name)
        m = C._PAGES_RE.search(page)
        n_pages += 1
        sliced_pages[0] += 1 if sliced else 0
        # `_row_title` 剔纯日期行的那一道在这批模板上到底用不用得上：数标题与日期
        # 写进同一枚 <a> 的行有几处。不另发请求，用的就是这一页的文本。
        date_anchor_pages.append(
            sum(1 for a in C._ANCHOR_RE.finditer(page)
                if any(C._is_date_line(ln)
                       for ln in C._to_text(a.group("text")).splitlines())))
        for k in _ROW_FORMS:
            if tok[k]:
                hit_pages[k] += 1
        if m:
            declared.add(m.group(1))
        _line("%s（/%s/node_%s%s）" % (name, dir_, nid,
                                       "" if page_note == "1" else "_%s" % page_note),
              "精确串 %d、前缀 %d、token %s、地址形状 %d 处、切出本栏段 %s、"
              "交回 %d 行、他栏 %d 行、控件 pages=%s、标题带日期行的锚 %d 处"
              % (exact, prefix,
                 " ".join("%s=%d" % (k, v) for k, v in tok.items() if v) or "五枚候选名全无",
                 shape, sliced, len(rows), off_col, m.group(1) if m else "无",
                 date_anchor_pages[-1]))
        laid.append(len(rows))

    for nid, dir_, name in (("8131", "zt", "合规案例"), ("31", "csll", "财税理论"),
                            ("23", "sswfaj", "税案通报")):
        pno = "2" if nid == "31" else "1"
        url = C.column_page_url(nid, page=int(pno), dir_=dir_)
        covered.add(dir_)
        try:
            _measure(nid, dir_, name, _get(url, session).text, pno)
        except Exception as e:                                         # noqa: BLE001
            _line(url[-30:], "取不到：%s" % e)
        time.sleep(C.READ_INTERVAL)
    try:
        r = _get(C.column_page_url("23", page=11, dir_="sswfaj"), session)
        _line("node_23_11.html（第 11 页）", "HTTP %s，%d 字节"
              % (r.status_code, len(r.content)))
    except Exception as e:                                             # noqa: BLE001
        _line("node_23_11.html", "请求失败：%s" % e)
    # 「行容器有几种形态」这条判据的复现处：本地栏目表里**每一枚目录段**各取一栏（定点三页
    # 已覆盖的目录段跳过），逐页量 `_row_counts` 那四档。按目录段取而不是按栏目取，
    # 是因为建站模板按目录段分配——覆盖全目录段才算把形态数尽。
    todo = {}
    for col in C.load_columns():
        d = (col.get("dir") or "").lower()
        if (d and d not in covered and not col.get("parent")
                and int(col.get("foundNum") or 0) > 0 and d not in todo):
            todo[d] = (col["nodeid"], col["name"])
    for d, (nid, name) in sorted(todo.items()):
        try:
            r = _get(C.column_page_url(nid, dir_=d), session)
            if r.status_code != 200:
                _line("%s（/%s/node_%s）" % (name, d, nid),
                      "HTTP %s，%d 字节" % (r.status_code, len(r.content)))
                continue
            _measure(nid, d, name, r.text)
        except Exception as e:                                         # noqa: BLE001
            _line("%s（/%s/node_%s）" % (name, d, nid), "取不到：%s" % e)
        time.sleep(C.READ_INTERVAL)
    _line("本轮量到的页 / 覆盖的目录段", "%d 页 / %d 枚（%s）"
          % (n_pages, len(covered) + len(todo),
             " ".join(sorted(covered | set(todo)))))
    _line("五枚候选类名各在几页出现",
          " ".join("%s=%d" % (k, v) for k, v in hit_pages.items()))
    _line("各页交回行数档位 / 控件 pages 档位",
          "%s / %s" % (" ".join("%s×%d" % (v, c) for v, c in
                                sorted(Counter(laid).items(), key=lambda x: -x[1])),
                       " ".join(sorted(declared)) or "全部页都数不到控件"))
    _line("能按 column-header-1 切出本栏段的页", "%d / %d" % (sliced_pages[0], n_pages))
    _line("标题与日期写在同一枚锚里的行",
          "本轮 %d 页合计 %d 处（逐页 %s）"
          % (n_pages, sum(date_anchor_pages),
             " ".join(str(v) for v in date_anchor_pages)))
    print("  => 期望：第 11 页 500（翻页控件 pages 最大 10）；容器数与交回行数不等是常态，"
          "取行按地址形状不按类名。精确串与「各页交回行数档位」「控件 pages 档位」三行是文档里"
          "「每页 10 行」「五种形态」两条说法的复现处——两档都随目录段浮动，取到几档就写几档。"
          "精确串随写法浮动——行容器写成 clearfix 那种就数不中，"
          "只当形态统计，不当取行判据。"
          "「标题与日期写在同一枚锚里的行」那一档是 `_row_title` 那道剔除的复现处：0 处说明"
          "这一道在本轮页面上没有活干，留着是因为站点按目录段换模板、没覆盖到的写法可能出现；"
          "%s"
          % ("候选名里有 %s 在全部页上 0 处命中，文档不能再把它们算成读到的形态"
             % "、".join("`%s`" % k for k, v in hit_pages.items() if not v)
             if any(not v for v in hit_pages.values()) else "五枚候选名全部有页对上"))


# ---------------------------------------------------------------------------
# 9. 栏目页行与本栏检索集相交
# ---------------------------------------------------------------------------
def item9(session, kw):
    print("[9] 切不出本栏标题时靠检索集收口")
    # 两栏各管一种失效：8030 是同目录兄弟栏的行混进来，13 是这一页铺的行全不属于本栏
    # （要闻栏检索集 334 行、页面 17 处行一枚都对不上，交回 0 行）。
    for node_id, dir_, name in (("8030", "policy", "增值税"), ("13", "news", "要闻")):
        page = _get(C.column_page_url(node_id, dir_=dir_), session).text
        time.sleep(C.READ_INTERVAL)
        rows, off_col, sliced, off_set = C._page_rows(page, dir_, name)
        ids, err = _col_ids(node_id, session)
        time.sleep(C.READ_INTERVAL)
        if err:
            _line("node_%s 检索集" % node_id, "取不到：%s" % err)
            continue
        in_set = [r for r in rows if C._article_id(r["url"]) in ids]
        _line("node_%s（%s）" % (node_id, name),
              "切出本栏段=%s、页面行=%d、他栏目录段=%d、本栏检索集=%d 行、"
              "相交后=%d（扣掉 %d）" % (sliced, len(rows), off_col, len(ids),
                                        len(in_set), len(rows) - len(in_set)))
        _line("  交回的行里目录段是本栏的", "%d / %d"
              % (sum(1 for r in in_set if C._ARTICLE_HREF_RE.search(r["url"])
                     and C._ARTICLE_HREF_RE.search(r["url"]).group("dir").lower() == dir_),
                 len(in_set)))
    print("  => 期望：8030 扣掉的行是同目录兄弟栏；要闻那一栏相交后为 0——"
          "栏目页 0 行只说明这一页没铺本栏的行，不说明这一栏没有内容（它检索集里有 334 行）。"
          "任何一式扣为 0 且页面行数等于检索集大小，交集那一道就只对那一栏没用上")


# ---------------------------------------------------------------------------
# 10. 栏目表重建
# ---------------------------------------------------------------------------
def item10(session, kw):
    print("[10] 栏目表重建一轮（声明 102 次 = 2026-10-19 那轮实发：首页 1 次 + 逐节点空词探测 "
          "91 次 + 父栏目页各一次 10 次；探测那一路受 max_nodes=120 封住、取页那一路不另封，"
          "所以换一轮建站点的父栏目数就换实发；--fast 跳过）")
    before = len(C.load_columns())
    cols = C.update_columns(session=session, max_nodes=120)
    parents = sum(1 for c in cols if c.get("parent"))
    dup = {}
    for c in cols:
        dup[c["name"]] = dup.get(c["name"], 0) + 1
    dup = {k: v for k, v in dup.items() if v > 1}
    _line("表内行数（重建前 / 重建后）", "%d / %d，其中父栏目 %d"
          % (before, len(cols), parents))
    _line("重名栏目", "、".join("%s×%d" % (k, v) for k, v in sorted(dup.items())) or "无")
    _line("column_lookup 对重名的判据", "%s"
          % " ".join("%s:%s" % (k, C.column_lookup(k)[0]) for k in sorted(dup)))
    _line("整栏行数前五", "、".join("%s %s" % (c["name"], c["foundNum"])
                                   for c in cols[:5]))


# ---------------------------------------------------------------------------
# 11. 整站规模与 pageSize 上限
# ---------------------------------------------------------------------------
def item11(session, kw):
    print("[11] 整站行数与 pageSize=5000 的末段")
    j, _ = _r(dict(_q(""), pageSize=C.PAGE_SIZE_MAX, pageNo=1), session)
    total = (j or {}).get("foundNum")
    rows1 = len((j or {}).get("article") or [])
    time.sleep(C.SEARCH_MIN_GAP)
    tail_no = -(-int(total) // C.PAGE_SIZE_MAX) if total else 0
    j2, _ = _r(dict(_q(""), pageSize=C.PAGE_SIZE_MAX, pageNo=tail_no), session)
    rows2 = len((j2 or {}).get("article") or [])
    expect2 = int(total) - (tail_no - 1) * C.PAGE_SIZE_MAX
    _line("空词全站 foundNum", "%s（pageSize=5000 首页取回 %d 行）" % (total, rows1))
    _line("末段 pageNo=%d 取回" % tail_no, "%d 行；总数减前 %d 段应得 %d 行，%s"
          % (rows2, tail_no - 1, expect2, "对得上" if rows2 == expect2 else "对不上"))
    dates = [r.get("date") for r in ((j2 or {}).get("article") or []) if r.get("date")]
    _line("末段日期跨度", "%s 至 %s" % (min(dates), max(dates)) if dates else "无")
    print("  => 期望：首页取满 5,000 行、末段取到零头，跨度起于 1980 年；"
          "pageNo 从 1 起算时末段位次等于总数除以 pageSize 向上取整")


# ---------------------------------------------------------------------------
# 12. 首页那一路：壳、壳里的 JS、POST 与 GET 是否同一份数
# ---------------------------------------------------------------------------
# 站点自己的代码只用 POST（`Search.js` 里那句 `$.post`），本层只用 GET。
# 这一项验的是"两种提交法回不回同一份字节"——不中则本层的 GET 那一读要重写。
SHELL_PATH = "/founder/SearchServlet.do"
JS_PATH = "/bootstrap/js/Search.js"


def item12(session, kw):
    print("[12] 首页那一路（词：%s）" % kw)
    wire = {"q": kw, "pageNo": 0, "pageSize": 20, "channel": C.CHANNEL_WEB,
            "sort": C.SORT_ONLY, "siteID": C.SITE_ID}
    p = _post(C.SEARCH_ENDPOINTS[0], session, wire)
    time.sleep(C.SEARCH_MIN_GAP)
    g = _get(C.SEARCH_ENDPOINTS[0], session, params=wire)
    _line("同一批参数 POST 与 GET 的响应",
          "POST HTTP %s %d 字节 / GET HTTP %s %d 字节 / %s"
          % (p.status_code, len(p.content), g.status_code, len(g.content),
             "逐字节相同" if p.content == g.content else "不同（本层沿用 GET 这条要复核）"))
    time.sleep(C.SEARCH_MIN_GAP)

    shell = C.CTAX_BASE + SHELL_PATH
    form = {"content": kw, "siteID": C.SITE_ID, "changeChannel": C.CHANNEL_WEB}
    s1 = _post(shell, session, form)
    time.sleep(C.SEARCH_MIN_GAP)
    s0 = _post(shell, session, dict(form, content=""))
    time.sleep(C.SEARCH_MIN_GAP)
    si = _post("http://120.132.80.188:8088" + SHELL_PATH, session, form)
    time.sleep(C.SEARCH_MIN_GAP)
    _line("壳（域名）有词 / 空词",
          "HTTP %s %d 字节 / HTTP %s %d 字节，差 %d 字节；回吐了词：%s"
          % (s1.status_code, len(s1.content), s0.status_code, len(s0.content),
             len(s1.content) - len(s0.content), kw.encode("utf-8") in s1.content))
    _line("壳（IP）有词", "HTTP %s %d 字节；与域名那份%s"
          % (si.status_code, len(si.content),
             "逐字节相同" if si.content == s1.content else "不同"))
    _line("壳的 Set-Cookie", s1.headers.get("Set-Cookie") or "（无）")

    for host in (C.CTAX_BASE, "http://120.132.80.188:8088"):
        r = _get(host + JS_PATH, session)
        _line("壳里的 %s（%s）" % (JS_PATH, host.split("//")[1].split("/")[0]),
              "HTTP %s，%d 字节" % (r.status_code, len(r.content)))
        time.sleep(C.SEARCH_MIN_GAP)
    home = _get(C.CTAX_BASE + "/", session)
    _line("首页 HTML 里的 per_submit",
          "HTTP %s，%d 字节（解出 %d 字符），调用 %d 处、定义 %d 处；"
          "「paper」字样 %d 处"
          % (home.status_code, len(home.content), len(home.text),
             home.text.count("per_submit"), home.text.count("function per_submit"),
             home.text.lower().count("paper")))
    srcs = []
    for m in re.findall(r'<script[^>]+src="([^"]+)"', home.text):
        u = m if m.startswith("http") else C.CTAX_BASE + "/" + m.lstrip("./")
        if u not in srcs:
            srcs.append(u)
    _line("首页外链脚本", "%d 份去重（标签 %d 处）" % (len(srcs), len(re.findall(r'<script[^>]+src="', home.text))))
    called = defined = missing = 0
    for u in srcs:
        r = _get(u, session)
        body = r.text if r.status_code == 200 else ""
        called += body.count("per_submit")
        defined += body.count("function per_submit")
        missing += 0 if r.status_code == 200 else 1
        _line("  %s" % u.replace(C.CTAX_BASE + "/", ""),
              "HTTP %s，%d 字节，per_submit 调用 %d、定义 %d"
              % (r.status_code, len(r.content), body.count("per_submit"),
                 body.count("function per_submit")))
        time.sleep(C.SEARCH_MIN_GAP)
    _line("%d 份合起来" % len(srcs), "调用 %d 处、定义 %d 处、取不到 %d 份" % (called, defined, missing))
    print("  => 期望：POST 与 GET 逐字节相同（本层沿用 GET 成立）；壳两式同尺寸且不回数据；"
          "壳内 JS 在域名与 IP 上字节相同。壳的尺寸随词长变，不是空词与有词同尺寸；"
          "首页只调用 per_submit 而不定义它——首页那个框点下去在提交前就抛错，"
          "外链脚本里也一处定义都没有（`defined` 必须是 0，任何一份给出定义就得改写这一条）；"
          "「paper」字样为 0 处时，首页上没有报纸版面那一路的入口，paperID 便无从枚举")


# ---------------------------------------------------------------------------
# 13. 触屏端那一路：行构成与两式地址
# ---------------------------------------------------------------------------
def item13(session, kw):
    print("[13] channel=2 那一档有多少行、行里两枚地址各能不能取")
    j, _ = _r(_q(kw, channel=C.CHANNEL_TOUCH, pageSize=C.SEARCH_PAGE_SIZE), session)
    time.sleep(C.SEARCH_MIN_GAP)
    rows = (j or {}).get("article") or []
    names = {}
    for r in rows:
        names[r.get("nodesearchname") or "(空)"] = names.get(r.get("nodesearchname") or "(空)", 0) + 1
    ds = sorted(r.get("date") or "" for r in rows)
    _line("foundNum / 取回行", "%s / %d 行" % ((j or {}).get("foundNum"), len(rows)))
    _line("行内栏目名构成", " ".join("%s %d 行" % (k, v) for k, v in sorted(names.items())))
    _line("日期跨度", "%s 至 %s" % (ds[0], ds[-1]) if rows else "（没有行）")
    _line("url 与 urlpad 的段数", "url %s；urlpad %s"
          % (sorted({len([x for x in re.sub(r"^https?://[^/]+", "", r.get("url") or "").split("/") if x])
                     for r in rows}),
             sorted({len([x for x in re.sub(r"^https?://[^/]+", "", r.get("urlpad") or "").split("/") if x])
                     for r in rows if (r.get("urlpad") or "") != "null"})))
    _line("urlpad 的目录段", " ".join(sorted({(r.get("urlpad") or "").split("/")[3]
                                              for r in rows if (r.get("urlpad") or "null") != "null"})))
    _line("urlpad 与 url 同址的行", "%d / %d" % (sum(1 for r in rows if r.get("urlpad") == r.get("url")), len(rows)))
    got = 0
    for r in rows:
        u = r.get("urlpad") or ""
        if u == "null" or got >= 2:
            continue
        got += 1
        for label, target in (("按 urlpad 原样", u), ("只换 https", u.replace("http://", "https://", 1))):
            try:
                t = _get(target, session)
                _line("%s %s" % (label, re.sub(r"^https?://[^/]+", "", u) or u[-24:]),
                      "HTTP %s，%d 字节" % (t.status_code, len(t.content)))
            except Exception as e:                                     # noqa: BLE001
                _line(label, "请求失败：%s" % e)
            time.sleep(C.READ_INTERVAL)
    print("  => 期望：urlpad 补的是 `news` 段、行行不同址，但 http 与 https 两式都回 500。"
          "任何一式回 200 就说明触屏端能接进取正文，`CHANNEL_TOUCH` 那条边界与 "
          "`search_ctax` 的 channel 说明要改")


# 第 6 项含单跑时补取行池的 1 次检索，第 7 项含尾段取样的 2 次检索。
# 第 5 项含日期区间两发正控与父栏目页 1 次；第 12 项含首页外链脚本逐份取回（7 份，按站点当期清单浮动）。
# 第 4 项含多词语义那四发正控；第 8 项含按目录段全扫的那一批（本地栏目表 14 枚目录段，
# 定点三页已覆盖 3 枚，其余 11 枚各取一栏；某目录下取不到合格叶栏时这一项会少发）；
# 第 9 项两栏各一次栏目页加一次检索集，`_column_id_set` 那发已由 `_col_ids` 计入。
COST = {1: 2, 2: 9, 3: 19, 4: 10, 5: 10, 6: 7, 7: 9, 8: 15, 9: 4, 10: 102, 11: 2, 12: 15, 13: 5}


def main():
    ap = argparse.ArgumentParser(description="中国税网站点体检（只读，不调模型）")
    ap.add_argument("--keyword", default="增值税", help="第 1、2、3、4 项用的检索词")
    ap.add_argument("--fast", action="store_true", help="跳过第 10 项栏目表重建")
    ap.add_argument("--items", default="", help="只跑指定项，逗号分隔，如 2,4,7")
    a = ap.parse_args()
    want = ([int(x) for x in re.findall(r"\d+", a.items)] if a.items
            else [n for n in sorted(COST) if not (a.fast and n == 10)])
    print("# 取证请求数：本段声明合计 %d 次 HTTP 请求（实发逐项打在最后；第 6、7、8、10、12 项"
          "按站点当期读数浮动，两头都可能差）；不调用任何模型、不消耗任何额度。"
          % sum(COST[n] for n in want))
    for n in want:
        print("  第 %d 项 %d 次" % (n, COST[n]))
    print("")
    session = C._make_session()
    fns = {1: item1, 2: item2, 3: item3, 4: item4, 5: item5, 6: item6,
           7: item7, 8: item8, 9: item9, 10: item10, 11: item11, 12: item12,
           13: item13}
    ran = {}
    for n in want:
        before = HEAD["请求数"]
        try:
            fns[n](session, a.keyword)
        except Exception as e:                                         # noqa: BLE001
            print("[%d] 这一项没跑完：%s" % (n, e))
        ran[n] = HEAD["请求数"] - before
        print("")
    print("实发 / 声明 逐项：%s" % "、".join(
        "第 %d 项 %d/%d（差 %+d，第 6、7、8、10、12 项按站点当期读数浮动）"
        % (n, ran[n], COST[n], ran[n] - COST[n])
        for n in want if ran[n] != COST[n]) or "全部对上")
    print("实际发出 %d 次请求（声明合计 %d 次）；"
          "第 6 项按行池能凑出几篇浮动、第 8 项按本地栏目表的目录段数浮动、"
          "第 10 项按站点节点数与父栏目数浮动、第 12 项按首页当期外链脚本次数浮动"
          % (HEAD["请求数"], sum(COST[n] for n in want)))
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
