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
  2. 阅读：税屋前置阿里云 WAF，无 acw_sc__v2 cookie 时所有页面（含 robots.txt）
     都返回同一份 23,682 字节挑战页，页面里带 arg1。
     → 本模块按公开算法（固定置换表 + 固定异或掩码）由 arg1 算出
       acw_sc__v2 并用同一 session 重试。正文容器是 div.arcContent#tupain。
       直连仍失败时退回 Jina Reader。

     2026-09-27 实测：算出的 cookie 服务端不再认可（连续 5 次同 session 重试
     全被拦），Jina Reader 也连接超时。**该站的正文当时取不到**，本模块
     实际只当链接发现源用，标题与地址照常返回。取正文靠 fgk / 税屋之外的
     解读渠道补，不要因为"检索有结果"就以为正文也能取到。

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
# 一次检索后逐篇取正文，360/搜狗/税屋都对高频请求敏感
READ_INTERVAL = 2.0
TIMEOUT_READ = 30

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 税屋前置阿里云 WAF：第一次请求一定返回 23,682 字节的挑战页，页面里
# <textarea id="renderData"> 带 arg1（40 位十六进制），浏览器据此算出
# acw_sc__v2 _cookie 再请求才放行。
#
# 算法是公开的：先用固定置换表把 arg1 重排，再与固定掩码逐字节异或。
# 置换表与掩码是该挑战的常量，不随 arg1 变。
#
# 注意：算对 cookie 仍不必然放行——服务端还会校验 acw_tc 等会话 cookie，
# 无浏览器执行不了完整的 JS 挑战链。所以直连失败是常态，会退回 Jina Reader。
# 2026-09 实测：算出的 cookie 服务端一律不认，直连 0/N 成功。
_UNBOX_POS = [15, 35, 29, 24, 33, 16, 1, 38, 10, 9, 19, 31, 40, 27, 22, 23,
              25, 13, 6, 11, 39, 18, 20, 8, 14, 21, 32, 26, 2, 30, 7, 4,
              17, 5, 3, 28, 34, 37, 12, 36]
_XOR_MASK = "3000176000856006061501533003690027800375"
_ARG1_RE = re.compile(r"arg1\s*=\s*'([0-9a-fA-F]+)'")

_ARC_RE = re.compile(r'<div class="arcContent"[^>]*>(.*?)'
                     r'(?=<div class="(?:bot-share|left2b|blank20)|\Z)', re.S | re.I)
_META_RES_RE = re.compile(r'<div class="articleResource">(.*?)</div>', re.S | re.I)
_META_DES_RE = re.compile(r'<div class="articleDes">(.*?)</div>', re.S | re.I)
_TIME_RE = re.compile(r"时间：\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")
_TAG_RE = re.compile(r"<[^>]+>")
_PARA_RE = re.compile(r"</(?:p|div|li|tr|h[1-6])\s*>|<br\s*/?>", re.I)


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


def _get_with_challenge(url: str, session: requests.Session) -> requests.Response:
    """请求页面，遇挑战页就地解一次并重试。

    用同一个 session 连做两步，让 acw_tc 之类的会话 cookie 一并带上。
    """
    r = session.get(url, timeout=TIMEOUT_READ)
    page = r.content.decode("utf-8", errors="replace")
    if _is_waf_page(page):
        m = _ARG1_RE.search(page)
        if m:
            session.cookies.set("acw_sc__v2", _solve_waf_challenge(m.group(1)))
            r = session.get(url, timeout=TIMEOUT_READ)
    return r


def fetch_shui5(url: str) -> dict:
    """
    直连税屋取单篇文章正文。

    Returns:
        {"url","title","date","summary","content","_error"?}
    """
    out = {"url": url}
    try:
        r = _get_with_challenge(url, _make_session())
    except requests.RequestException as e:
        out["_error"] = f"请求失败：{e}"
        return out
    if r.status_code != 200:
        out["_error"] = f"HTTP {r.status_code}"
        return out

    page = r.content.decode("utf-8", errors="replace")
    if _is_waf_page(page):
        out["_error"] = ("阿里云 WAF 挑战未通过：acw_sc__v2 已按公开算法算出，"
                         "但服务端仍要求浏览器执行完整 JS 挑战链")
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
    取单篇文章正文。优先直连税屋（自动解一次 WAF 挑战），失败再退回 Jina Reader。

    2026-09 实测两条路都不通：算对的 acw_sc__v2 服务端不认，Jina 连接超时。
    税屋此时只能当"链接发现源"用——标题与地址照常返回，正文靠别处补。

    Args:
        url: 税屋文章地址

    Returns:
        (正文, 错误信息)。成功时错误信息为空串。
    """
    direct = fetch_shui5(url)
    if direct.get("content") and not direct.get("_error"):
        header = "".join(
            f"{k}: {direct[k]}\n" for k in ("title", "date", "summary") if direct.get(k))
        return f"{header}\n{direct['content']}", ""

    jina_err = ""
    try:
        r = requests.get(JINA_READER + url, timeout=TIMEOUT_READ)
    except requests.RequestException as e:
        return "", f"直连失败({direct.get('_error')})；Jina 请求失败：{e}"

    if r.status_code != 200:
        return "", (f"直连失败({direct.get('_error')})；"
                    f"Jina HTTP {r.status_code}")
    # Jina 的 403 是 Cloudflare 拦截页，正文里不会有 "Markdown Content:"
    if "Markdown Content:" not in r.text:
        return "", (f"直连失败({direct.get('_error')})；"
                    f"Jina 未返回正文（可能被 Cloudflare 拦截）")
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
