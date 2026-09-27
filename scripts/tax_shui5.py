#!/usr/bin/env python3
"""
税屋 (www.shui5.cn) — 实务解读来源。

为什么需要这个模块：税屋的文章（专栏、实操指引、答疑）是税务总局公告与
NPC 法规库里查不到的实操层内容，是三源之外的必要补充。

税屋访问的两道门槛及本模块的处理：
  1. 检索：税屋站内搜索没有自有引擎，页面搜索框实际提交到
     zhannei.baidu.com/cse/site。百度对本机 IP 极不稳定
     （返回 1,488 字节"百度安全验证"），实测 4 次仅 1 次返回结果。
     → 改用 360 移动版 site:shui5.cn 检索，实测 3 组关键词均稳定返回 5 条。
  2. 阅读：税屋所有页面（含 robots.txt）直连都返回同一份 23,682 字节的
     阿里云 WAF arg1 挑战页（<meta name="aliyun_waf_aa">），无浏览器
     无法执行挑战 JS。本机 Edge 未开调试端口且技能禁止重启浏览器。
     → 改用 Jina Reader (r.jina.ai) 取正文，实测 9/9 篇全部成功返回完整
       Markdown 正文。

本模块据此实现：360 检索 + Jina 读正文。

Usage:
  python tax_shui5.py "研发费用加计扣除" --size 5
  python tax_shui5.py "高新技术企业认定" --size 5 --read   # 连正文一起取
  python tax_shui5.py "增值税起征点" --size 3 --json
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
JINA_READER = "https://r.jina.ai/"
# 一次检索后逐篇取正文，360/搜狗/Jina 都对高频请求敏感
READ_INTERVAL = 3.0
TIMEOUT_READ = 90


def search_shui5(keyword: str, size: int = 5, read_body: bool = False) -> dict:
    """
    在税屋检索政策实务文章。

    Args:
        keyword: 检索词
        size: 返回条数上限
        read_body: True 时再逐篇取正文（较慢，每篇之间等 READ_INTERVAL）

    Returns:
        {"keyword","total","results","searched_at","source","_error"?}
        results 每项含 title/url/snippet；read_body=True 时额外含 content。
    """
    found = so360_search(keyword, site=SHUI5_SITE, size=size)
    if found.get("_error"):
        return _empty(keyword, str(found["_error"]))

    results = []
    for item in found["results"]:
        row = {
            "title": _strip_site_suffix(item["title"]) or item["title"],
            "url": item["url"],
            "date": "",
            "snippet": item.get("snippet", "")[:200],
            "source": "税屋 (shui5.cn)",
            "source_label": "实务解读",
        }
        if read_body:
            content, err = read_article(item["url"])
            row["content"] = content
            if err:
                row["_error"] = err
            else:
                jina_title = re.search(r"^Title:\s*(.+)$", content, re.MULTILINE)
                if jina_title:
                    row["title"] = _strip_site_suffix(jina_title.group(1).strip())
        results.append(row)
        if read_body:
            time.sleep(READ_INTERVAL)

    return {
        "keyword": keyword,
        "total": len(results),
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "税屋 (shui5.cn)",
        "_from_cache": False,
    }


def read_article(url: str) -> tuple[str, str]:
    """
    通过 Jina Reader 取单篇文章正文（Markdown）。

    Args:
        url: 税屋文章地址

    Returns:
        (markdown 正文, 错误信息)。成功时错误信息为空串。
    """
    try:
        r = requests.get(JINA_READER + url, timeout=TIMEOUT_READ)
    except requests.RequestException as e:
        return "", str(e)

    if r.status_code != 200:
        return "", f"HTTP {r.status_code}"
    # Jina 的 403 是 Cloudflare 拦截页，正文里不会有 "Markdown Content:"
    if "Markdown Content:" not in r.text:
        return "", "Jina 未返回正文（可能被 Cloudflare 拦截）"
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


def _empty(keyword: str, error: str = "") -> dict:
    return {
        "keyword": keyword,
        "total": 0,
        "results": [],
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "税屋 (shui5.cn)",
        "_error": error,
        "_from_cache": False,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="税屋 (shui5.cn) 实务解读检索")
    p.add_argument("keyword", help="检索词")
    p.add_argument("--size", type=int, default=5)
    p.add_argument("--read", action="store_true", help="同时取回正文（较慢）")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = search_shui5(args.keyword, size=args.size, read_body=args.read)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"🔍 税屋检索 \"{args.keyword}\" | {result['searched_at']}")
    if result.get("_error"):
        print(f"⚠️  {result['_error']}")
    print(f"共 {result['total']} 条\n")
    for item in result["results"]:
        print(f"  {item['title'][:70]}")
        print(f"     {item['url']}")
        if item.get("_error"):
            print(f"     正文读取失败: {item['_error']}")
        elif item.get("content"):
            body = item["content"].split("Markdown Content:", 1)[-1].strip()
            print(f"     正文 {len(body)} 字，节选：{body[:100]}...")
        print()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
