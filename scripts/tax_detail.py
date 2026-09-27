#!/usr/bin/env python3
"""
Tax Policy Detail — fetch metadata and download law documents from NPC API.

Usage:
  python tax_detail.py --info <bbbs_id>
  python tax_detail.py --download <bbbs_id> [--format docx|pdf] [output_path]
  python tax_detail.py --preview <bbbs_id>
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET
from io import BytesIO
import zipfile

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://flk.npc.gov.cn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://flk.npc.gov.cn/",
    "Accept": "application/json, text/plain, */*",
}
VERIFY_SSL = os.getenv("TAX_SEARCH_VERIFY_SSL", "0") == "1"
SXX_MAP = {1: "已废止", 2: "已修改", 3: "现行有效", 4: "尚未生效"}


# ── Lightweight cache for detail metadata (1h TTL) ──────────────────────────
class _DetailCache:
    def __init__(self):
        self.dir = Path.home() / ".cache" / "tax-policy-search"

    def _key(self, bbbs_id: str) -> str:
        return hashlib.sha256(f"detail|{bbbs_id}".encode()).hexdigest()[:16]

    def get(self, bbbs_id: str) -> Optional[dict]:
        p = self.dir / f"{self._key(bbbs_id)}.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - data.get("_cached_at", 0) > 3600:
                return None
            return data.get("payload")
        except Exception:
            return None

    def set(self, bbbs_id: str, payload: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / f"{self._key(bbbs_id)}.json"
        p.write_text(
            json.dumps({"_cached_at": time.time(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8"
        )

_detail_cache = _DetailCache()

# 详情接口与检索接口同一套限流策略，最小间隔与重试次数照抄 tax_search
_MIN_INTERVAL = 0.6
_last_request_at = 0.0


def _is_challenge_page(r) -> bool:
    """限流时对方回 HTTP 200 + 一份 HTML 挑战页（Please enable JavaScript）。"""
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "json" in ctype:
        return False
    head = r.text[:600].lower()
    return "please enable" in head or "<!doctype html" in head


def _request(url: str, max_retries: int = 4):
    """带节流与重试的 GET，覆盖 429、断连、5xx 与挑战页。"""
    global _last_request_at
    for attempt in range(max_retries):
        gap = _MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if gap > 0:
            time.sleep(gap)
        try:
            r = requests.get(url, headers=HEADERS, verify=VERIFY_SSL, timeout=15)
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
            continue
        finally:
            _last_request_at = time.monotonic()
        if r.status_code == 429 or r.status_code in {500, 502, 503}:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
        elif _is_challenge_page(r) and attempt < max_retries - 1:
            time.sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
        return r
    return r


def fetch_detail(bbbs_id: str) -> dict:
    """Get law detail metadata from NPC API.

    响应里 ossWordPath / ossPdfPath 嵌在 data.ossFile 对象下，不在 data 顶层，
    按顶层取会静默拿到空串——下载功能因此报"No download URL"之外的问题却
    毫无提示。content 是单个节点对象而非数组，取 body 时要按节点展开。
    """
    cached = _detail_cache.get(bbbs_id)
    if cached:
        return cached

    url = f"{BASE_URL}/law-search/search/flfgDetails?bbbs={bbbs_id}"
    r = _request(url)
    try:
        data = r.json()
    except ValueError as e:
        # 限流时对方回 HTTP 200 + 一份 HTML 挑战页，.json() 报的是
        # "Expecting value: line 1 column 1"，看不出真实原因。
        raise RuntimeError(
            f"NPC 详情接口返回的不是 JSON（HTTP {r.status_code}，"
            f"Content-Type {r.headers.get('Content-Type')}，"
            f"{len(r.content)} 字节）：{r.text[:120]!r}；原异常 {e}") from e
    detail = data.get("data", data)
    oss = detail.get("ossFile") or {}

    result = {
        "id": bbbs_id,
        "title": detail.get("title", ""),
        "category": detail.get("flxz", ""),
        "publish_date": detail.get("gbrq", ""),
        "effective_date": detail.get("sxrq", ""),
        "status_code": detail.get("sxx", 0),
        "status": SXX_MAP.get(detail.get("sxx", 0), "未知"),
        "issuing_authority": detail.get("zdjgName", ""),
        "oss_files": {
            "docx": oss.get("ossWordPath", ""),
            "pdf": oss.get("ossPdfPath", ""),
        },
        "related": {
            "amendments": detail.get("xgwj", []),
            "interpretation": detail.get("lsyg", []),
            "drafts": detail.get("xgzl", []),
            "legal_basis": detail.get("flfg", []),
        },
        "content_tree": _flatten_content(detail.get("content")),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    _detail_cache.set(bbbs_id, result)
    return result


def _flatten_content(node, out: Optional[list] = None) -> list:
    """把 content 节点树拍平成 [{level,title,content}...] 列表。

    接口返回的是单个根节点（带 children），不是数组。
    """
    if out is None:
        out = []
    if isinstance(node, list):
        for n in node:
            _flatten_content(n, out)
        return out
    if not isinstance(node, dict):
        return out
    title = node.get("title") or node.get("name") or ""
    text = node.get("content") or ""
    if title or text:
        out.append({
            "level": node.get("level", 0),
            "title": title,
            "content": text,
        })
    _flatten_content(node.get("children") or node.get("childList"), out)
    return out


def get_download_url(bbbs_id: str, fmt: str = "docx") -> Optional[str]:
    """Get a signed download URL for a law document."""
    url = f"{BASE_URL}/law-search/download/pc?format={fmt}&bbbs={bbbs_id}"
    r = requests.get(url, headers=HEADERS, verify=VERIFY_SSL, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("data", {}).get("url")


def download_file(bbbs_id: str, fmt: str = "docx", output_path: Optional[str] = None) -> str:
    """Download a law document and save to disk."""
    dl_url = get_download_url(bbbs_id, fmt)
    if not dl_url:
        raise ValueError(f"No download URL returned for {bbbs_id}")

    r = requests.get(dl_url, headers=HEADERS, verify=VERIFY_SSL, timeout=60)
    r.raise_for_status()

    detail = fetch_detail(bbbs_id)
    safe_title = re.sub(r"[^\w一-鿿]", "_", detail["title"])[:50]
    ext = "docx" if fmt == "docx" else "pdf"

    if not output_path:
        output_path = f"{safe_title}_{bbbs_id[:8]}.{ext}"

    with open(output_path, "wb") as f:
        f.write(r.content)

    return output_path


def _parse_docx_from_bytes(data: bytes) -> list[str]:
    """Parse DOCX bytes and return non-empty paragraph texts (stdlib, zero-dependency)."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            with z.open("word/document.xml") as f:
                tree = ET.parse(f)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for p in tree.findall(".//w:p", ns):
            texts = [t.text or "" for t in p.findall(".//w:t", ns)]
            para = "".join(texts).strip()
            if para:
                paragraphs.append(para)
        return paragraphs
    except Exception:
        return []


def extract_text_from_docx(filepath: str) -> list:
    """Extract paragraphs from a DOCX file using stdlib (ZIP + XML)."""
    with open(filepath, "rb") as f:
        return _parse_docx_from_bytes(f.read())


def preview_law(bbbs_id: str) -> dict:
    """Preview a law: title, article count, numbering pattern, first articles."""
    detail = fetch_detail(bbbs_id)
    if not detail["title"]:
        raise ValueError(f"Law not found: {bbbs_id}")

    # Try to download and parse
    docx_path = None
    articles = []
    numbering = "unknown"

    try:
        docx_path = download_file(bbbs_id, "docx")
        paragraphs = extract_text_from_docx(docx_path)

        # Detect article numbering
        chinese_nums = sum(1 for p in paragraphs if re.match(r"^第[一二三四五六七八九十百千]+条", p))
        arabic_nums = sum(1 for p in paragraphs if re.match(r"^第\d+条", p))
        numbering = "chinese" if chinese_nums > arabic_nums else "arabic"
        pattern = r"^第[一二三四五六七八九十百千]+条" if numbering == "chinese" else r"^第\d+条"

        articles = [p for p in paragraphs if re.match(pattern, p)]
    except Exception:
        pass
    finally:
        if docx_path and os.path.exists(docx_path):
            os.remove(docx_path)

    return {
        **detail,
        "article_count": len(articles),
        "numbering_pattern": numbering,
        "first_articles": articles[:10],
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Fetch tax law detail and download documents")
    p.add_argument("--info", help="Fetch detail metadata for a law ID")
    p.add_argument("--download", help="Download a law document by ID")
    p.add_argument("--preview", help="Preview a law: title, article count, numbering")
    p.add_argument("--format", choices=["docx", "pdf"], default="docx")
    p.add_argument("--output", "-o", help="Output file path")
    p.add_argument("--json", action="store_true", help="Output JSON")

    args = p.parse_args()

    if args.info:
        detail = fetch_detail(args.info)
        if args.json:
            print(json.dumps(detail, ensure_ascii=False, indent=2))
        else:
            print(f"📋 {detail['title']}")
            print(f"   分类: {detail['category']}")
            print(f"   状态: [{detail['status']}]")
            print(f"   公布: {detail['publish_date']}  施行: {detail['effective_date']}")
            print(f"   发布机关: {detail['issuing_authority']}")

    elif args.download:
        path = download_file(args.download, args.format, args.output)
        print(f"✅ 已下载: {path}")

    elif args.preview:
        preview = preview_law(args.preview)
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            print(f"📋 {preview['title']}")
            print(f"   状态: [{preview['status']}]")
            print(f"   法条数: {preview['article_count']} 条")
            print(f"   编号格式: {'中文数字' if preview['numbering_pattern'] == 'chinese' else '阿拉伯数字'}")
            if preview.get("first_articles"):
                print(f"   前 {min(5, len(preview['first_articles']))} 条:")
                for a in preview["first_articles"][:5]:
                    print(f"     {a[:100]}")

    else:
        p.print_help()


if __name__ == "__main__":
    # Windows 控制台默认 GBK，输出里的 emoji 与法规名会炸 UnicodeEncodeError
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
