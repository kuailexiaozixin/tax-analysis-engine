#!/usr/bin/env python3
"""案例通道实测探针 —— 给 `references/source_defects.md` 的「案例通道实测台账」产出证据。

为什么要有这个探针：类案检索（`scripts/tax_cases.py`）能走哪几维，全靠 search5
接口的几条现场行为撑着——栏目维可用、标签维对案例不可用、日期排序在新闻栏单调、
案例栏目没有直连清单页、子栏目代号判得出案例还是文件、同一条目会在两台主机各回
一遍。这六条都会随对方改版而变，写死在文档里就成了没人敢拆、也没人证明它还成立的
保守。本脚本一次跑完，按台账要的三字段回显：

    最近实测日期 / 实测方法（发了哪几条请求，逐条列出）/ 当时结果（逐条给数字）

要发的请求只有 `REQUESTS` 这一份清单：`--dry-run` 打印它，真跑也按它逐条执行，
所以"计划发几条"与"实发几条"不可能各说各话。判据全部可判定：命中条数、日期序列
是否单调、状态码、url 里出现的子栏目代号。任一条与台账记的不符，就是要改台账的那天。

用法：
    python tests/probe_case_channel.py --dry-run     # 只看要发哪几条，一条不发
    python tests/probe_case_channel.py               # 真发，回吐台账三字段
"""

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import requests  # noqa: E402
import urllib3  # noqa: E402

import tax_http  # noqa: E402
import tax_web_search as tws  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAX_REQUESTS = 24

# 台账里"全站能不能搜到案例"这一行的判据就是这四个词的命中数
CASE_WORDS = ("重大税收违法案件", "税收违法 曝光", "骗取出口退税", "虚开发票 查处")
# 新闻发布栏目号；"栏目维能收窄"这一行取同一检索词的两个数对照
NEWS_COLUMN = "5741"
COLUMN_WORDS = ("骗取出口退税", "重大税收违法案件")
# 三个案例子栏目代号，出现在结果 url 的路径里。c102435 是既有记录里宣称的
# 「重大税收违法案件信息公布」栏：本机 2026-10-04 复测时它一次都没出现，仍留在
# 清单里逐组计数——它哪天回来了，代号分布那行会自己说，不必改代码。
SUBTYPE_CODES = ("c102025", "c102435", "c102439")
# 标签维：发三个非十类文件名的 label，看接口回 0 还是回基线
BAD_LABELS = ("新闻", "稽查", "曝光台")
# 直连清单页：宣称 400 的那个入口，两种后缀各试一次
LIST_PAGES = tuple(
    f"https://www.chinatax.gov.cn/chinatax/n810215/c102435/common_list.{ext}"
    for ext in ("html", "shtml"))
# 检索词形态对照：前两条是"往案例词上加案例/公布这类字样"，后两条是
# "换成处理结果词"。台账那一行要说的是这两种写法差两个数量级。
WORD_SHAPE_QUERIES = (("骗取出口退税 案例", None), ("重大税收违法案件 公布", None),
                      ("虚开发票 依法查处 罚款", None), ("偷税 案件", NEWS_COLUMN))
# URL 路径里的栏目代号是 6 位（c100011／c102025／n810739），正文 id 是 7 位
# （c5172440），两者靠位数分开，不必猜。两头都用零宽断言：正则吃掉分隔斜杠的
# 写法会把相邻段隔一个漏一个（/n810219/c102025/c5172440/ 只能匹到首尾两段），
# 2026-10-04 那轮的"代号分布没出现 c102025、逐条计数却有 8 条"就是这么来的。
COL_CODE_RE = re.compile(r"(?<=/)(c\d{6}|n\d{6,7})(?=/)")
CONTENT_ID_RE = re.compile(r"(?<=/)c\d{7}(?=/content\.html)")


def requests_plan():
    """要发的每一条请求，(kind, 人话描述, 参数)。dry-run 与执行共用这一份。"""
    plan = []
    for w in CASE_WORDS:
        plan.append(("全站", f"search5 searchWord={w} label='' column=''",
                     {"keyword": w}))
    for w in COLUMN_WORDS:
        plan.append(("不限栏目", f"search5 searchWord={w}（作 column 收窄的基线）",
                     {"keyword": w}))
        plan.append(("栏目收窄",
                     f"search5 searchWord={w} column={NEWS_COLUMN}",
                     {"keyword": w, "column": NEWS_COLUMN}))
    for lb in BAD_LABELS:
        plan.append(("标签维", f"search5 searchWord=骗取出口退税 label={lb}",
                     {"keyword": "骗取出口退税", "raw_label": lb}))
    for p in (1, 2):
        plan.append(("日期序",
                     f"search5 searchWord=骗取出口退税 column={NEWS_COLUMN} "
                     f"orderBy=1 第 {p} 页",
                     {"keyword": "骗取出口退税", "column": NEWS_COLUMN,
                      "page": p, "order": "date_desc"}))
    for u in LIST_PAGES:
        plan.append(("直连清单", f"GET {u}", {"url": u}))
    for w, col in WORD_SHAPE_QUERIES:
        plan.append(("检索词形态",
                     f"search5 searchWord={w}"
                     + (f" column={col}" if col else ""),
                     {"keyword": w, "column": col}))
    return plan


def is_descending(seq):
    return len(seq) > 1 and all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))


def host_of(url):
    return "fgk" if tws.FGK_MARKER in url else "www"


def col_codes(url):
    return COL_CODE_RE.findall(url)


def content_id(url):
    m = CONTENT_ID_RE.search(url)
    return m.group(0) if m else ""


def run_one(kind, spec):
    """发这一条，回 (给台账看的一句, 日期序列, 这一页的条目行)。

    日期序列单独返回，是为了让"orderBy=1 是不是单调"这个判据看着全部条目判，
    而不是只看每页的首尾两天——首尾两天递减、中间乱跳的情况在摘要里看不见。
    条目行返回给 main 汇总，因为台账里"哪个子栏目出现过""同一条目回了几遍"
    这两条都是跨请求的判据，逐条打印时看不见。
    """
    if kind == "直连清单":
        try:
            r = requests.get(spec["url"], headers=tws.HEADERS, timeout=tws.TIMEOUT,
                             verify=False)
            body = re.sub(r"\s+", " ", r.text).strip()[:70]
            return (f"HTTP {r.status_code}，{len(r.content)} 字节 | 响应起头 {body}", [], [])
        except Exception as e:  # noqa: BLE001 探针要如实回显任何形态
            return (f"请求异常 {tax_http.short_reason(e)}", [], [])

    if kind == "标签维":
        params = {"siteCode": tws.SITE_CODE, "searchWord": spec["keyword"], "type": "1",
                  "pageSize": 10, "pageNum": 0, "orderBy": "5", "column": "",
                  "label": spec["raw_label"]}
        try:
            r = requests.get(tws.SEARCH_URL, params=params, headers=tws.HEADERS,
                             timeout=tws.TIMEOUT, verify=False)
            if r.status_code != 200:
                return (f"HTTP {r.status_code}", [], [])
            block = r.json().get("searchResultAll") or {}
            return (f"HTTP 200，total={block.get('total')}，"
                    f"清单 {len(block.get('searchTotal') or [])} 条", [], [])
        except Exception as e:  # noqa: BLE001
            return (f"请求异常 {tax_http.short_reason(e)}", [], [])

    res = tws.search_chinatax(spec["keyword"], page=spec.get("page", 1), size=10,
                              file_only=False, order=spec.get("order", "relevance"),
                              column=spec.get("column"))
    if res.get("_error"):
        return (f"请求失败：{res['_error']}", [], [])
    rows = res["results"]
    urls = [x["url"] for x in rows]
    if kind == "日期序":
        seq = [x["date"] for x in rows if x["date"]]
        return (f"本页 {len(seq)} 个日期，页内单调递减={is_descending(seq)}；"
                f"{seq[0] if seq else '—'} … {seq[-1] if seq else '—'}", seq, rows)
    hit = {c: sum(1 for u in urls if c in u) for c in SUBTYPE_CODES}
    # 首屏里"看着就是案例处理"的比例：条目带案例子栏目代号，且没带文号
    case_ish = sum(1 for x in rows
                   if set(col_codes(x["url"])) & set(SUBTYPE_CODES)
                   and not x.get("document_number"))
    return (f"total={res.get('total')}，首屏 {len(urls)} 条，"
            f"子栏目 {hit}，首屏判为案例 {case_ish} 条，"
            f"带文号 {sum(1 for x in rows if x.get('document_number'))} 条", [], rows)


def tally(rows):
    """跨请求汇总：代号分布、同 id 多主机重复、带文号且落案例栏的行数。"""
    codes, ids, doc_num = {}, {}, 0
    for x in rows:
        u = x["url"]
        for c in col_codes(u):
            codes[c] = codes.get(c, 0) + 1
        cid = content_id(u)
        if cid:
            ids.setdefault(cid, set()).add(host_of(u))
        if x.get("document_number"):
            doc_num += 1
    dup = {k: sorted(v) for k, v in ids.items() if len(v) > 1}
    return {"读取行数": len(rows),
            "代号分布": dict(sorted(codes.items(), key=lambda kv: -kv[1])),
            "同一条目跨主机重复": dup,
            "带文号的行数": doc_num}


def main(argv=None):
    ap = argparse.ArgumentParser(description="案例通道实测（联网，手动跑）")
    ap.add_argument("--dry-run", action="store_true", help="只列要发的请求，一条不发")
    args = ap.parse_args(argv)

    plan = requests_plan()
    if len(plan) > MAX_REQUESTS:
        print(f"计划发 {len(plan)} 条，超过 MAX_REQUESTS={MAX_REQUESTS}；"
              f"要么分批跑，要么显式改这个常量并说明为什么")
        return 1
    print(f"最近实测日期：{date.today().isoformat()}")
    print(f"实测方法：共 {len(plan)} 条请求，逐条如下")
    for i, (kind, desc, _spec) in enumerate(plan, 1):
        print(f"  {i:2d}. [{kind}] {desc}")
    if args.dry_run:
        print("\n[dry-run] 一条未发，台账不得据此更新")
        return 0

    print("\n当时结果：")
    dates, rows = [], []
    for i, (kind, desc, spec) in enumerate(plan, 1):
        line, seq, got = run_one(kind, spec)
        dates += seq
        rows += got
        print(f"  {i:2d}. [{kind}] {desc}\n      → {line}")
    print(f"\n合并 {len(dates)} 个日期（两页连看）单调递减：{is_descending(dates)}；"
          f"序列 {dates}")
    t = tally(rows)
    print(f"\n跨 {len([1 for k, _, _ in plan if k not in ('直连清单', '标签维')])} 组检索"
          f"共读 {t['读取行数']} 行：")
    print(f"  栏目代号分布（次数由多到少）：{t['代号分布']}")
    print(f"  带文号的行数：{t['带文号的行数']}")
    print(f"  同一正文 id 在两台主机各回一遍：{t['同一条目跨主机重复'] or '本轮没有'}")
    print(f"  宣称的三个案例子栏目中未出现的：{[c for c in SUBTYPE_CODES if c not in t['代号分布']]}")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
