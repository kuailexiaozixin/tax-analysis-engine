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
探针不另立判据。`--fast` 跳过第 10 项（栏目表重建一轮约 118 次请求）。

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
        ("siteID=1", dict(_q(kw), siteID=1)),
        ("siteID=3", dict(_q(kw), siteID=3)))
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
          "channel 0/3 与 siteID 1/3 回 37 字节空结构。sort 两档看「与基准不同序」——"
          "换序说明站点收这个值，「sort 只认 date」那条判据作废；只有回 500 才叫越界")


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


# ---------------------------------------------------------------------------
# 5. 父节点与叶节点
# ---------------------------------------------------------------------------
def item5(session, kw):
    print("[5] nodeID 只按叶节点收")
    for nid, want in (("24", "父（政策）应 0"), ("8210", "父（年份簇）应 0"),
                      ("672", "父（法规专题）应 0"), ("8215", "叶 有行"),
                      ("8030", "叶 增值税"), ("63", "叶 地方税收法规")):
        j, _ = _r(_q("", nodeID=nid, pageSize=10), session)
        rows = (j or {}).get("article") or []
        _line("nodeID=%s %s" % (nid, want),
              "foundNum=%s，取回 %d 行%s"
              % ((j or {}).get("foundNum"), len(rows),
                 "，首行 %s" % rows[0].get("nodesearchname") if rows else ""))
        if nid == "8215":
            _CACHE["policyrows"] = rows
        time.sleep(C.SEARCH_MIN_GAP)
    print("  => 父节点回 0 但它下面挂着文章：这一条只能当"
          "「检索不按子树收」，不能读成「这一栏没有内容」")


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
    _line("挑中的页型（栏目 · 目录段）",
          " ".join("%s·%s" % (r.get("nodesearchname"), r["url"].split("/")[3])
                   for r in picked[:6]))
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
        _line("%s（%s）" % (r["nodesearchname"], r["url"].split("/")[3]),
              "正文块 %d 份、property %d 份、%d 字，%s"
              % (len(blocks), len(props), len(blocks[0]) if blocks else 0, same))
        time.sleep(C.READ_INTERVAL)
    print("  => 期望：政策页与专题页 2 份且同字节，新闻 1 份。出现「不一致」"
          "就把 SKILL.md 第五步那句「取第一份」改成需要判断的写法")


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
def item8(session, kw):
    print("[8] 栏目页的行容器、地址形状与翻页上限")
    for nid, dir_, name in (("8131", "zt", "合规案例"), ("31", "csll", "财税理论"),
                            ("23", "sswfaj", "税案通报")):
        url = C.column_page_url(nid, page=2 if nid == "31" else 1, dir_=dir_)
        try:
            page = _get(url, session).text
        except Exception as e:                                         # noqa: BLE001
            _line(url[-30:], "取不到：%s" % e)
            continue
        region, sliced = C._own_region(page)
        rows, off_col, sliced2, off_set = C._page_rows(page, dir_, name)
        _line("%s（node_%s）" % (name, url.split("node_")[1]),
              "容器 class %d 个、地址形状 %d 处、切出本栏段 %s、交回 %d 行、"
              "他栏 %d 行、没交过检索集"
              % (page.count('class="newsList-item'),
                 len(C._ARTICLE_HREF_RE.findall(page)), sliced, len(rows), off_col))
        time.sleep(C.READ_INTERVAL)
    try:
        r = _get(C.column_page_url("23", page=11, dir_="sswfaj"), session)
        _line("node_23_11.html（第 11 页）", "HTTP %s，%d 字节"
              % (r.status_code, len(r.content)))
    except Exception as e:                                             # noqa: BLE001
        _line("node_23_11.html", "请求失败：%s" % e)
    print("  => 期望：第 11 页 500（翻页控件 pages 最大 10）；容器数与交回行数不等"
          "是常态，取行按地址形状不按类名")


# ---------------------------------------------------------------------------
# 9. 栏目页行与本栏检索集相交
# ---------------------------------------------------------------------------
def item9(session, kw):
    print("[9] 切不出本栏标题时靠检索集收口")
    node_id, dir_, name = "8030", "policy", "增值税"
    page = _get(C.column_page_url(node_id, dir_=dir_), session).text
    rows, off_col, sliced, off_set = C._page_rows(page, dir_, name)
    ids, err = C._column_id_set(node_id, session=session)
    time.sleep(C.READ_INTERVAL)
    if err:
        _line("检索集", "取不到：%s" % err)
        return
    in_set = [r for r in rows if C._article_id(r["url"]) in ids]
    _line("node_%s（%s）" % (node_id, name),
          "切出本栏段=%s、页面行=%d、本栏检索集=%d 行、相交后=%d（扣掉 %d）"
          % (sliced, len(rows), len(ids), len(in_set), len(rows) - len(in_set)))
    print("  => 期望：扣掉的行是同目录兄弟栏。扣为 0 且页面行数==检索集大小时"
          "这条交集已经不需要，可考虑只留标题切分那一道")


# ---------------------------------------------------------------------------
# 10. 栏目表重建
# ---------------------------------------------------------------------------
def item10(session, kw):
    print("[10] 栏目表重建一轮（约 118 次请求：首页 1 次 + 91 次空词探测 + 父栏目页若干，--fast 跳过）")
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


# 第 6 项含单跑时补取行池的 1 次检索，第 7 项含尾段取样的 2 次检索。
COST = {1: 2, 2: 9, 3: 13, 4: 6, 5: 6, 6: 7, 7: 9, 8: 4, 9: 2, 10: 118, 11: 2}


def main():
    ap = argparse.ArgumentParser(description="中国税网站点体检（只读，不调模型）")
    ap.add_argument("--keyword", default="增值税", help="第 1、2、3、4 项用的检索词")
    ap.add_argument("--fast", action="store_true", help="跳过第 10 项栏目表重建")
    ap.add_argument("--items", default="", help="只跑指定项，逗号分隔，如 2,4,7")
    a = ap.parse_args()
    want = ([int(x) for x in re.findall(r"\d+", a.items)] if a.items
            else [n for n in sorted(COST) if not (a.fast and n == 10)])
    print("# 取证请求数：本段共 %d 次 HTTP 请求，逐项列在下面；不调用任何模型、不消耗任何额度。"
          % sum(COST[n] for n in want))
    for n in want:
        print("  第 %d 项 %d 次" % (n, COST[n]))
    print("")
    session = C._make_session()
    fns = {1: item1, 2: item2, 3: item3, 4: item4, 5: item5, 6: item6,
           7: item7, 8: item8, 9: item9, 10: item10, 11: item11}
    for n in want:
        try:
            fns[n](session, a.keyword)
        except Exception as e:                                         # noqa: BLE001
            print("[%d] 这一项没跑完：%s" % (n, e))
        print("")
    print("实际发出 %d 次请求（声明 %d 次；第 10 项按站点节点数浮动）"
          % (HEAD["请求数"], sum(COST[n] for n in want)))
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
