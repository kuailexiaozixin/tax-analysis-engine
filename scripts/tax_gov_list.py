#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""税务总局「政策文件库」分页列表源（getFileListByCodeId）—— tax_sync 的分页 target。

这是母技能五个在线检索源之外的一条**结构化清单直连**：plain HTTP POST 到
www.chinatax.gov.cn/getFileListByCodeId，按 channelId 分栏目、分页返回 JSON，
每条带 title/url/发布时间 与政策文件元数据（发文字号、效力等级、时效性、
成文日期、税费类型）。无需 cookie、无需 UA、实测 20 次连发不限流、不调模型。

它补的是检索源给不了的东西：**一个栏目下全部现行文件的封闭清单**，
每条自带官方"时效性"分类状态。七栏实测出现的取值只有 全文有效／已修改／
全文废止／全文失效／尚未生效 五种（逐栏分布数字见 CHANNELS 注释），与
tax_web_search.AGING_VALUES 逐项相同，也都在 tax_evidence.judge_validity
认得的范围内——加新栏前先按这条核，取值域没登记就不收。
这份分类状态正是 scripts/tax_evidence.py 的 judge_validity 要的输入，
官方 url 又是 scripts/tax_cited.py 的文号→官方链接缓存要的落点。

它不在 scripts/tax_aggregator.py 的五源聚合里（`DEFAULT_SOURCES` 没有这一路）：
检索源回答"跟这个词相关的文件"，这一路回答"这一栏全部的文件"，两者不互相顶替。
索引顶部另带该栏的官方栏目页直链（`CHANNEL_PAGES`，`stats` 印出来给读者自查条数）。

同步复用 scripts/tax_sync.py 的 ListSynchronizer（分页爬全 → 集合 SHA1 diff
→ 变了才重建索引）。集合 SHA1 覆盖 url+时效性+发文字号+title，所以某文件
时效性翻转（同一 url、内容没换）也会被检出、触发重建。重建时 build_index 会
自检元数据覆盖率，把「发文字号缺失 / 时效性缺失 N 条」落进索引并在 sync 回显——
官方元数据键（writtentext/aging）改版导致整列变空，在写入侧就报出，不必等检索
召回下滑才察觉。注意时效性缺失对"财税文件""其他文件"两栏是常态（官方本就不填，
2026-10-04 整栏翻到底：1532 条与 488 条 0 条真值），只如实记录、不报警；
文号栏缺失才作改版预警。

用法：
    python tax_gov_list.py sync                 # 抓全指定栏目并重建索引
    python tax_gov_list.py sync --check         # 只比 total，不爬全
    python tax_gov_list.py sync --force         # 强制重建
    python tax_gov_list.py sync --channel 财税文件
    python tax_gov_list.py lookup 国家税务总局公告2026年第18号
    python tax_gov_list.py lookup 增值税 --aging 全文有效 -n 20
    python tax_gov_list.py stats                # 栏目/版本/条目/覆盖率/时效性分布
    python tax_gov_list.py seed-cache --only-missing   # 用清单批量预热文号→官方链接缓存
    python tax_gov_list.py missing                     # 列出有官方 url 却未进缓存的待补条目
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import tax_http  # noqa: E402
from tax_web_search import AGING_VALUES, aging_of  # noqa: E402  时效性的取值域与占位串空值口径都只有一套

API = "https://www.chinatax.gov.cn/getFileListByCodeId"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 30
PAGE_SIZE = 100

# 栏目 channelId 与整栏条数（total 为 2026-10-04 本机逐栏取到底的实数）。
# 键名一律取接口自己报的 channelName（也等于该栏每条的 效力等级），与
# tax_web_search.EFFECT_LEVEL_VALUES 逐个同名——CLI 的 --channel、检索面的
# effect_level、索引行里的 channel 字段就只需要记一个名字。
#
# 时效性填得满的是这五栏：税务规范性文件/法律/行政法规/国务院文件/税务部门规章。
# 2026-10-04 本机逐栏翻到底的整栏分布（条数 = total，未抽样）：
#   税务规范性文件 1925 = 全文有效 808／全文废止 745／已修改 347／全文失效 23／尚未生效 2
#   税务部门规章 86 = 46／18（已修改）／22（全文废止）
#   法律 75 = 69／3（全文废止）／2（已修改）／1（全文失效）
#   行政法规 65 = 43／21（全文废止）／1（已修改）
#   国务院文件 35 = 33／1（全文失效）／1（已修改）
# 七栏出现的取值合起来正好是 tax_web_search.AGING_VALUES 那五种，tax_evidence
# .judge_validity 逐条不落 unknown——加新栏前按这条核，取值域没登记就不收。
# 恒空的是这两栏，仍收进清单，靠发文字号/效力等级用（整栏翻到底，0 条真值）：
#   财税文件 1532 条全空、其他文件 488 条全空。别把"其他文件"当成填得满的一栏，
#   整栏翻到底 0 条真值；"财税文件"整栏不填这件事与 references/source_defects.md
#   里 corroborate_validity_from_target 那条是同一件事的两侧。
# 空栏里混着占位写法：财税文件抽样 150 条（第 1/3/5 页）是 138 空串 + 12 条字符串
# "null"。字面 "null" 由 normalize_item 走 aging_of 归成空串，否则 stats 的时效性
# 分布多出一档 "null"、build_index 的「时效性缺失」也会少报。
#
# 两栏故意不收录，各自有账：
#   工作通知 c102424（total=813）：抽 25 条时效性 0/25 全空。栏目枚举这条路要的是
#   "整栏现行文件的横截面"，不填时效性就枚举不出可引用的那批，只剩一堆内部工作事项
#   与文库版本通知；按词命中已由 search5 的 FILE_LABELS（含"工作通知"）覆盖。
#   政策解读 c100015：解读件不是可援引依据，走 search5 的"文字政策解读"标签。
CHANNELS = {
    "税务规范性文件": "470b437b304f434396500a1e2edc7f28",  # c100012, total=1925, 时效性填充
    "财税文件": "2cb303fdee614232b79552d52bb057d6",         # c102416, total=1532, 时效性恒空
    "其他文件": "4c1a5be62f6d44d48f386f630dcebbc5",         # c100013, total=488, 时效性恒空
    "法律": "d34fa7ad03f84f4caed12f5c2beae099",             # c100009, total=75, 时效性填充
    "行政法规": "e1cd1569d1ea4a25a11041248925a081",         # c100010, total=65, 时效性填充
    "国务院文件": "fa1726b47078490fa0a4522194185e8d",       # c102440, total=35, 时效性填充
    "税务部门规章": "0ac34e96afbb4be28844f18eef412421",     # c100011, total=86, 时效性填充
}
DEFAULT_CHANNEL = "税务规范性文件"

# 栏目的可浏览页（政策法规库站点的栏目首页，读者自己核"这一栏都有什么"的入口）。
# 一页一条，路径写全而不是按模板拼——页面名并不统一：只有「税务部门规章」是
# list.html，其余六栏是 listflfg.html。2026-10-04 本机逐个 GET：七页全 200，
# 而按统一模板拼出来的 c100011/listflfg.html 回 404，所以这张表必须是数据不能是推导。
# 静态 HTML 里栏目名由 JS 渲染，页面自身只把该栏的 c 码写在导航与脚本里，
# 每张卡片的正文链接也落在同一个 c 码目录下。
CHANNEL_PAGES = {
    "税务规范性文件": "https://fgk.chinatax.gov.cn/zcfgk/c100012/listflfg.html",
    "财税文件": "https://fgk.chinatax.gov.cn/zcfgk/c102416/listflfg.html",
    "其他文件": "https://fgk.chinatax.gov.cn/zcfgk/c100013/listflfg.html",
    "法律": "https://fgk.chinatax.gov.cn/zcfgk/c100009/listflfg.html",
    "行政法规": "https://fgk.chinatax.gov.cn/zcfgk/c100010/listflfg.html",
    "国务院文件": "https://fgk.chinatax.gov.cn/zcfgk/c102440/listflfg.html",
    "税务部门规章": "https://fgk.chinatax.gov.cn/zcfgk/c100011/list.html",
}

# 政策文件元数据：按稳定的 key 取（resultList 内顺序不固定，且 name 也可能改）。
META_KEY_FIELDS = {
    "发文字号": "writtentext",
    "效力等级": "effectlevel",
    "时效性": "aging",
    "成文日期": "writtendate",
    "税费类型": "taxpolicy",
    "发文单位": "writtendepartment",
}

DATA_ROOT = _HERE.parent / "data" / "sync" / "chinatax-list"
INDEX_PATH = DATA_ROOT / "gov_list_index.json"


# ── 规范化：一条列表项 → 扁平 dict ─────────────────────────────────────────
def normalize_item(item: dict) -> dict:
    """把 getFileListByCodeId 的一条原始项压成扁平记录（含政策元数据）。

    元数据分散在 domainMetaList 的多个分组里，每组 resultList[] 是 {name,value,key}。
    这里按 key 收非空值，再映射到中文字段名——不依赖分组顺序，也不依赖 name。
    """
    meta = {}
    for grp in item.get("domainMetaList") or []:
        for m in grp.get("resultList") or []:
            k, v = m.get("key"), m.get("value")
            if k and v and str(v).strip():
                meta[k] = str(v).strip()
    rec = {
        "title": (item.get("title") or "").strip(),
        "url": (item.get("url") or "").strip(),
        "published": (item.get("publishedTimeStr") or "").strip(),
        "channel": (item.get("channelName") or "").strip(),
    }
    for cn, key in META_KEY_FIELDS.items():
        rec[cn] = meta.get(key, "")
    # 财税文件栏实测有 12/150 条把时效性写成字符串 "null"。不归一就会当成
    # "有标注"：stats 的分布多出一档 "null"，build_index 的「时效性缺失」少报，
    # 而这一栏本来就该记成"官方没填"。空值口径复用检索面的 aging_of，不另立一套。
    rec["时效性"] = aging_of(rec["时效性"])
    return rec


# ── 分页抓取（喂给 ListSynchronizer 的 fetch_page 回调）────────────────────
def _response_to_rows(payload: dict) -> dict:
    """解一层 results.data，返回 {rows:[规范化 dict], total:int}。"""
    data = (payload or {}).get("results", {}).get("data", {}) or {}
    raw = data.get("results") or []
    return {"rows": [normalize_item(it) for it in raw],
            "total": int(data.get("total") or 0)}


def make_fetch_page(channel_name: str = DEFAULT_CHANNEL):
    """绑一个栏目，返回 fetch_page(page)->{rows,total}；POST 只走 tax_http。"""
    cid = CHANNELS.get(channel_name, channel_name)

    def fetch_page(page: int) -> dict:
        resp = tax_http.request(
            "POST", API,
            headers={"User-Agent": UA,
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT, verify=False,
            data={"codeId": "", "channelId": cid, "page": page, "size": PAGE_SIZE})
        if resp.status_code != 200:
            raise RuntimeError(f"getFileListByCodeId HTTP {resp.status_code}")
        payload = resp.json()
        out = _response_to_rows(payload)
        # 把请求栏目名带回行里，避免列表项 channelName 与本地频道别名不一致时无法归栏。
        for r in out["rows"]:
            r.setdefault("channel", channel_name)
        return out

    return fetch_page


# ── 建索引（喂给 ListSynchronizer 的 build 回调）───────────────────────────
def build_index(rows: list, *, channel: str = DEFAULT_CHANNEL) -> dict:
    """把抓全的行写成 gov_list_index.json，返回统计（含版本日期=最新成文/发布日）。"""
    # 去重：同 url 保留一条（分页重叠或重发时防御）。
    by_url, order = {}, []
    for r in rows:
        u = r.get("url") or r.get("title")
        if u not in by_url:
            by_url[u] = r
            order.append(u)
    recs = [by_url[u] for u in order]

    dates = [r.get("成文日期") or (r.get("published") or "")[:10] for r in recs]
    dates = [d for d in dates if re.match(r"\d{4}-\d{2}-\d{2}", d or "")]
    version = max(dates) if dates else ""

    # 建库覆盖率自检：官方元数据键（writtentext/aging）若在上游改名，normalize_item
    # 会静默把整列掏空——集合 SHA1 变了照样触发重建，却没人报"现在几乎全无文号"。
    # 把缺失计数落在索引、sync 时回显，让元数据改版在写入侧就被察觉（与 preference
    # 的"文号抽取失败"同源）。注意：时效性缺失只如实记录不加警报——"财税文件"栏目
    # 官方本就不填时效性（见 CHANNELS 注释），缺失是常态不是改版。
    n_total = len(recs)
    miss_doc = sum(1 for r in recs if not (r.get("发文字号") or "").strip())
    miss_aging = sum(1 for r in recs if not (r.get("时效性") or "").strip())

    index = {
        "栏目": channel,
        "channelId": CHANNELS.get(channel, ""),
        "栏目页": CHANNEL_PAGES.get(channel, ""),
        "版本日期": version,
        "构建时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "条目数": n_total,
        "发文字号缺失": miss_doc,
        "时效性缺失": miss_aging,
        "记录": recs,
    }
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(INDEX_PATH)
    return index


# ── 离线查询 ───────────────────────────────────────────────────────────────
def _load_index():
    if not INDEX_PATH.exists():
        raise SystemExit(f"[缺索引] 未找到 {INDEX_PATH.name}，先运行：python tax_gov_list.py sync")
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _match(rec, words):
    hay = " ".join([rec.get("title", ""), rec.get("发文字号", ""),
                    rec.get("税费类型", ""), rec.get("效力等级", "")])
    return all(w in hay for w in words)


def lookup(words, aging=None, channel=None, limit=20, as_json=False):
    idx = _load_index()
    # 时效性是精确等值比对，取值域外（"有效"、"全文 有效"）不是"这一栏没有这种状态"，
    # 而是这个词根本不在这五种里。与检索面 build_filters 同一口径：域外值在动手筛之前就报。
    if aging and aging not in AGING_VALUES:
        raise SystemExit(f"[取值域外] --aging 只认 {'、'.join(AGING_VALUES)}，收到的是 {aging!r}")
    pool = idx["记录"]
    if aging:
        pool = [r for r in pool if aging == (r.get("时效性") or "")]
    if channel:
        pool = [r for r in pool if channel in (r.get("channel") or "")]
    if words:
        pool = [r for r in pool if _match(r, words)]
    pool = pool[:limit]
    if as_json:
        print(json.dumps({"栏目": idx.get("栏目"), "版本": idx.get("版本日期"),
                          "命中": len(pool), "结果": pool},
                         ensure_ascii=False, indent=1))
        return pool
    print(f"命中 {len(pool)} 条（栏目 {idx.get('栏目')} / 版本 {idx.get('版本日期')}）\n")
    for r in pool:
        print(f"[{r.get('时效性') or '—'}] {r.get('title')}")
        print(f"    文号：{r.get('发文字号') or '—'} | 效力：{r.get('效力等级') or '—'} "
              f"| 成文：{r.get('成文日期') or '—'}")
        if r.get("url"):
            print(f"    {r['url']}")
    return pool


def stats(as_json=False):
    from collections import Counter
    idx = _load_index()
    c = Counter(r.get("时效性") or "未标注" for r in idx["记录"])
    cov = {"发文字号缺失": idx.get("发文字号缺失"),
           "时效性缺失": idx.get("时效性缺失")}
    if as_json:
        print(json.dumps({"栏目": idx.get("栏目"), "版本": idx.get("版本日期"),
                          "条目数": idx.get("条目数"), "覆盖率": cov,
                          "栏目页": idx.get("栏目页", ""),
                          "时效性分布": dict(c)},
                         ensure_ascii=False, indent=1))
        return
    _d = "—" if cov["发文字号缺失"] is None else cov["发文字号缺失"]
    _a = "—" if cov["时效性缺失"] is None else cov["时效性缺失"]
    print(f"栏目 {idx.get('栏目')} | 版本 {idx.get('版本日期')} | 条目 {idx.get('条目数')}"
          f" | 文号缺 {_d} / 时效性缺 {_a}")
    # 索引里没有这一格时（建库那一路没把它录上）印一句缺，不留空串——空串会被读成
    # "这一栏没有官方页"。补齐的动作就是重跑 sync。
    print(f"  栏目页：{idx.get('栏目页') or '—（这份索引没带栏目页，重跑 sync 即补齐）'}")
    for k, v in c.most_common():
        print(f"  {v:>5}  {k}")


# ── 同步 ───────────────────────────────────────────────────────────────────
def _synchronizer(channel=DEFAULT_CHANNEL):
    import tax_sync
    return tax_sync.ListSynchronizer(
        f"chinatax-list-{channel}",
        make_fetch_page(channel),
        lambda rows: build_index(rows, channel=channel),
        sig_fields=("url", "时效性", "发文字号", "title"),
        page_size=PAGE_SIZE, max_pages=200)


def sync(check=False, force=False, as_json=False, channel=DEFAULT_CHANNEL):
    import tax_sync
    syn = _synchronizer(channel)
    # 索引只有一份，落在哪一栏由上一次真正重建它的请求决定。切栏时若那一栏的
    # 集合恰好没变，ListSynchronizer 会走"无更新"分支跳过构建，磁盘上留的还是
    # 别的栏目那份——stats/lookup 读到的不是刚请求的那栏，而 sync 的回显只看
    # 条目数，看不出串了栏。索引栏目与本次请求不一致就当 force 处理。
    # （2026-10-04 实测：先 sync --channel 税务部门规章，再 sync --channel 行政法规
    #   报"集合未变"，索引里仍是 税务部门规章 86 条。）
    if not check and not force and INDEX_PATH.exists():
        try:
            on_disk = json.loads(INDEX_PATH.read_text(encoding="utf-8")).get("栏目")
        except (OSError, ValueError):
            on_disk = None
        if on_disk != channel:
            force = True
    try:
        res = syn.sync(check_only=check, force=force)
    except Exception as e:
        res = {"source": syn.source, "成功": False, "动作": "异常", "错误": str(e)}
    # 真正重建过时，把本次 build_index 落进索引的元数据覆盖率带回回显（同步时点暴露改版）
    if res.get("动作") == "已更新" and INDEX_PATH.exists():
        try:
            meta = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            res["发文字号缺失"] = meta.get("发文字号缺失")
            res["时效性缺失"] = meta.get("时效性缺失")
            res["总条数"] = meta.get("条目数")
        except Exception:
            pass
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(("[成功] " if res.get("成功") else "[失败] ") + res.get("动作", ""))
        for k in ("本地条目数", "远端总数", "条目数", "总数", "新版本日期",
                  "集合SHA1", "发文字号缺失", "时效性缺失", "错误"):
            if res.get(k) not in (None, ""):
                print(f"    {k}：{res[k]}")
        if res.get("动作") == "已更新" and (res.get("发文字号缺失") or 0) > 0:
            print(f"    ← 文号栏缺 {res['发文字号缺失']}/{res.get('总条数')} 条；"
                  f"若这一数较上次跳增，多半是官方元数据键(writtentext)改版，须核对 normalize_item")
    return 0 if res.get("成功") else 1


# ── 用清单批量预热文号→官方链接缓存 ───────────────────────────────────────
def seed_cited_cache(cache_path=None, index=None, *, only_missing=False) -> dict:
    """把本地清单索引里的 发文字号→官方 url 批量写入 tax_cited 缓存（纯离线）。

    这是清单源与文号缓存（tax_cited）的闭环：getFileListByCodeId 每行自带官方
    url 与发文字号，一次性种进缓存后，locate_cited_document 按文号命中即零网络，
    不必再靠运行时逐个检索。文号键用 tax_terms.doc_number_of 归一（与缓存读取端
    同一个正则），URL 仍过 tax_cited.is_official——非官方域不落。

    Args:
        cache_path: 缓存文件路径，默认 tax_cited.CITED_LINKS（测试指临时文件）。
        index: 已加载的索引 dict，默认读 INDEX_PATH。
        only_missing: True 时只补缓存里尚缺的文号，不覆盖已有条目。

    Returns: {"扫描":n,"写入":m,"跳过非官方":k,"已有":p}
    """
    import tax_cited as CITED
    import tax_terms as TT
    idx = index or _load_index()
    stat = {"扫描": 0, "写入": 0, "跳过非官方": 0, "已有": 0}
    for r in idx.get("记录", []):
        doc_raw = (r.get("发文字号") or "").strip()
        url = (r.get("url") or "").strip()
        dn = TT.doc_number_of(doc_raw)
        if not dn or not url:
            continue
        stat["扫描"] += 1
        if not CITED.is_official(url):
            stat["跳过非官方"] += 1
            continue
        if only_missing and CITED.get_cited_link(dn, cache_path):
            stat["已有"] += 1
            continue
        if CITED.put_cited_link(dn, url, cache_path):
            stat["写入"] += 1
    return stat


# ── 待补官方链接的工作清单（与 seed_cited_cache 互补）───────────────────────
def missing_cited(cache_path=None, index=None, limit=None) -> list:
    """列出清单里有官方 url、但其文号尚未进 tax_cited 缓存的条目（待补工作清单）。

    seed_cited_cache 是把 url 直接种进缓存；这里反过来查"哪些还没种"，给一份可核对
    的清单。文号同样经 doc_number_of 归一后比对缓存键。只有官方域 url 才计入——非官方
    url 即便列出来也种不进缓存（tax_cited 会拒收），没有核对价值。纯本地读，不联网。
    """
    import tax_cited as CITED
    import tax_terms as TT
    idx = index or _load_index()
    out = []
    for r in idx.get("记录", []):
        dn = TT.doc_number_of((r.get("发文字号") or "").strip())
        url = (r.get("url") or "").strip()
        if not dn or not url or not CITED.is_official(url):
            continue
        if CITED.get_cited_link(dn, cache_path):
            continue
        out.append({"文号": dn, "标题": r.get("title", ""), "url": url})
        if limit and len(out) >= limit:
            break
    return out


# ── 注册进 tax_sync CLI（--source chinatax-list 走默认栏目）────────────────
def _register():
    try:
        import tax_sync
        tax_sync.register_list(
            "chinatax-list",
            make_fetch_page(DEFAULT_CHANNEL),
            lambda rows: build_index(rows, channel=DEFAULT_CHANNEL),
            sig_fields=("url", "时效性", "发文字号", "title"),
            page_size=PAGE_SIZE, max_pages=200)
    except Exception:
        pass


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="税务总局政策文件库分页列表源")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("sync")
    p.add_argument("--check", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--channel", default=DEFAULT_CHANNEL, choices=list(CHANNELS))
    p.add_argument("--json", action="store_true")
    q = sub.add_parser("lookup")
    q.add_argument("words", nargs="*")
    q.add_argument("--aging")
    q.add_argument("--channel")
    q.add_argument("-n", "--limit", type=int, default=20)
    q.add_argument("--json", action="store_true")
    s = sub.add_parser("stats")
    s.add_argument("--json", action="store_true")
    sd = sub.add_parser("seed-cache", help="用清单索引批量预热文号→官方链接缓存（离线）")
    sd.add_argument("--only-missing", action="store_true", help="只补缓存里尚缺的文号")
    sd.add_argument("--json", action="store_true")
    ms = sub.add_parser("missing", help="列出清单里有官方 url、文号却未进缓存的待补条目（离线）")
    ms.add_argument("-n", "--limit", type=int, default=None)
    ms.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "sync":
        return sync(a.check, a.force, a.json, a.channel)
    if a.cmd == "lookup":
        lookup(a.words, aging=a.aging, channel=a.channel, limit=a.limit, as_json=a.json)
        return 0
    if a.cmd == "stats":
        stats(a.json)
        return 0
    if a.cmd == "seed-cache":
        stat = seed_cited_cache(only_missing=a.only_missing)
        if a.json:
            print(json.dumps(stat, ensure_ascii=False, indent=1))
        else:
            print(f"扫描 {stat['扫描']} → 写入 {stat['写入']}"
                  f"（跳过非官方 {stat['跳过非官方']}，已有 {stat['已有']}）")
        return 0
    if a.cmd == "missing":
        rows = missing_cited(limit=a.limit)
        if a.json:
            print(json.dumps({"待补": len(rows), "结果": rows},
                             ensure_ascii=False, indent=1))
        else:
            print(f"待补官方链接 {len(rows)} 条（清单里有 url，缓存里缺文号）")
            for r in rows:
                print(f"  {r['文号']} | {r['标题']}")
                print(f"    {r['url']}")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
