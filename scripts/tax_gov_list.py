#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""税务总局「政策文件库」分页列表源（getFileListByCodeId）—— tax_sync 的分页 target。

这是母技能五个在线检索源之外的一条**结构化清单直连**：plain HTTP POST 到
www.chinatax.gov.cn/getFileListByCodeId，按 channelId 分栏目、分页返回 JSON，
每条带 title/url/发布时间 与政策文件元数据（发文字号、效力等级、时效性、
成文日期、税费类型）。无需 cookie、无需 UA、实测 20 次连发不限流、不调模型。

它补的是检索源给不了的东西：**一个栏目下全部现行文件的封闭清单**，
每条自带官方"时效性"分类状态（全文有效/全文废止/已修改/部分失效/尚未生效）。
这份分类状态正是 scripts/tax_evidence.py 的 judge_validity 要的输入，
官方 url 又是 scripts/tax_cited.py 的文号→官方链接缓存要的落点。

同步复用 scripts/tax_sync.py 的 ListSynchronizer（分页爬全 → 集合 SHA1 diff
→ 变了才重建索引）。集合 SHA1 覆盖 url+时效性+发文字号+title，所以某文件
时效性翻转（同一 url、内容没换）也会被检出、触发重建。

用法：
    python tax_gov_list.py sync                 # 抓全指定栏目并重建索引
    python tax_gov_list.py sync --check         # 只比 total，不爬全
    python tax_gov_list.py sync --force         # 强制重建
    python tax_gov_list.py sync --channel 财税文件
    python tax_gov_list.py lookup 国家税务总局公告2026年第18号
    python tax_gov_list.py lookup 增值税 --aging 全文有效 -n 20
    python tax_gov_list.py stats
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

API = "https://www.chinatax.gov.cn/getFileListByCodeId"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 30
PAGE_SIZE = 100

# 栏目 channelId（实测 total 见注释；时效性只有"税务规范性文件/法律/行政法规/其他"
# 这几栏填得满，"财税文件"那一栏时效性恒空——但仍收进清单，靠发文字号/效力等级用）。
CHANNELS = {
    "税务规范性文件": "470b437b304f434396500a1e2edc7f28",   # c100012, total≈1924, 时效性填充
    "财税文件": "2cb303fdee614232b79552d52bb057d6",          # c102416, total≈1532, 时效性恒空
    "其他": "4c1a5be62f6d44d48f386f630dcebbc5",              # c100013, ≈488
    "法律": "d34fa7ad03f84f4caed12f5c2beae099",              # c100009, ≈75
    "行政法规": "e1cd1569d1ea4a25a11041248925a081",          # c100010, ≈65
}
DEFAULT_CHANNEL = "税务规范性文件"

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

    index = {
        "栏目": channel,
        "channelId": CHANNELS.get(channel, ""),
        "版本日期": version,
        "构建时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "条目数": len(recs),
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
    if as_json:
        print(json.dumps({"栏目": idx.get("栏目"), "版本": idx.get("版本日期"),
                          "条目数": idx.get("条目数"), "时效性分布": dict(c)},
                         ensure_ascii=False, indent=1))
        return
    print(f"栏目 {idx.get('栏目')} | 版本 {idx.get('版本日期')} | 条目 {idx.get('条目数')}")
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
    try:
        res = syn.sync(check_only=check, force=force)
    except Exception as e:
        res = {"source": syn.source, "成功": False, "动作": "异常", "错误": str(e)}
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(("[成功] " if res.get("成功") else "[失败] ") + res.get("动作", ""))
        for k in ("本地条目数", "远端总数", "条目数", "总数", "新版本日期", "集合SHA1", "错误"):
            if res.get(k):
                print(f"    {k}：{res[k]}")
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
