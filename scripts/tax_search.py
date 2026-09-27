#!/usr/bin/env python3
"""
Tax Policy Search — search China tax laws/regulations via NPC API (flk.npc.gov.cn).

Usage:
  # Title search (default)
  python tax_search.py "增值税" --size 20
  # Exact title search
  python tax_search.py "中华人民共和国增值税法" --exact
  # Date range + sort by publish date
  python tax_search.py "企业所得税" --status 3 --from 2024-01-01 --sort date
  # Verbose output (includes article snippets)
  python tax_search.py "增值税" --status 3 --verbose
  # Enable cache (5min TTL)
  python tax_search.py "增值税" --cache
  # JSON output for piping
  python tax_search.py "个人所得税" --json
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

# Fix Windows console encoding
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Constants ───────────────────────────────────────────────────────────────
_CLEAN_HTML_RE = re.compile(r"<[^>]+>")
BASE_URL = "https://flk.npc.gov.cn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://flk.npc.gov.cn/",
    "Accept": "application/json, text/plain, */*",
}
VERIFY_SSL = os.getenv("TAX_SEARCH_VERIFY_SSL", "0") == "1"

# Status code mapping
SXX_MAP = {1: "已废止", 2: "已修改", 3: "现行有效", 4: "尚未生效"}
SXX_REVERSE = {v: k for k, v in SXX_MAP.items()}

# ── Cache (disabled by default, short TTL when enabled) ─────────────────────
class _CacheManager:
    """Lightweight JSON file cache. Disabled by default."""
    def __init__(self, enabled: bool = False):
        self._enabled = enabled
        self.dir = Path.home() / ".cache" / "tax-policy-search"

    def _key(self, *parts: str) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str, max_age: float = 300) -> Optional[dict]:
        if not self._enabled:
            return None
        p = self._path(key)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - data.get("_cached_at", 0) > max_age:
                return None
            return data.get("payload")
        except Exception:
            return None

    def set(self, key: str, payload: dict) -> None:
        if not self._enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self._path(key)
        p.write_text(
            json.dumps({"_cached_at": time.time(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8"
        )

    def clear(self) -> None:
        if self.dir.exists():
            for f in self.dir.glob("*.json"):
                f.unlink()

    def stats(self) -> dict:
        if not self.dir.exists():
            return {"entries": 0, "size_kb": 0}
        files = list(self.dir.glob("*.json"))
        return {
            "entries": len(files),
            "size_kb": round(sum(f.stat().st_size for f in files) / 1024, 1)
        }

_cache = _CacheManager(enabled=False)


# ── 18 Tax Types → Search Keyword Mapping ───────────────────────────────────
TAX_TYPE_KEYWORDS = {
    # 流转税
    "增值税": {
        "aliases": ["增值税", "VAT", "进项税", "销项税", "留抵退税", "增值税专用发票", "增值税普通发票"],
        "parent_law": "中华人民共和国增值税法",
        "priority": 1,
    },
    "消费税": {
        "aliases": ["消费税", "卷烟", "成品油", "汽车消费税"],
        # 消费税至今没有立法为"消费税法"，NPC 查"中华人民共和国消费税法"返回的是
        # 宪法等无关法规。实际依据是 2008 年修订、现行有效的暂行条例。
        "parent_law": "中华人民共和国消费税暂行条例",
        "priority": 2,
    },
    "关税": {
        "aliases": ["关税", "进出口税", "反倾销税", "保税", "海关"],
        "parent_law": "中华人民共和国关税法",
        "priority": 3,
    },
    # 所得税
    "企业所得税": {
        "aliases": ["企业所得税", "应税所得", "税前扣除", "加计扣除", "高新技术企业", "小微企业", "西部大开发"],
        "parent_law": "中华人民共和国企业所得税法",
        "priority": 1,
    },
    "个人所得税": {
        "aliases": ["个人所得税", "综合所得", "专项附加扣除", "年度汇算", "劳务报酬", "经营所得"],
        "parent_law": "中华人民共和国个人所得税法",
        "priority": 1,
    },
    # 财产行为税
    "房产税": {
        "aliases": ["房产税", "房地产税", "房屋租赁税"],
        "parent_law": "中华人民共和国房产税暂行条例",
        "priority": 4,
    },
    "土地增值税": {
        "aliases": ["土地增值税", "土增税", "清算"],
        "parent_law": "中华人民共和国土地增值税暂行条例",
        "priority": 4,
    },
    "契税": {
        "aliases": ["契税", "不动产登记"],
        "parent_law": "中华人民共和国契税法",
        "priority": 4,
    },
    "城镇土地使用税": {
        "aliases": ["城镇土地使用税", "土地使用税"],
        "parent_law": "中华人民共和国城镇土地使用税暂行条例",
        "priority": 4,
    },
    "车船税": {
        "aliases": ["车船税", "车船使用税", "船舶吨税"],
        "parent_law": "中华人民共和国车船税法",
        "priority": 5,
    },
    # 车辆购置税是独立税种、独立立法，不归入车船税：车船税法不含车辆购置税，
    # 两者是并列关系，混在一处会把"买车缴税"的问题指到错误的法上。
    "车辆购置税": {
        "aliases": ["车辆购置税", "车购税", "购车税", "新车购置税"],
        "parent_law": "中华人民共和国车辆购置税法",
        "priority": 5,
    },
    "印花税": {
        "aliases": ["印花税", "合同印花税", "账簿印花税"],
        "parent_law": "中华人民共和国印花税法",
        "priority": 4,
    },
    "城市维护建设税": {
        "aliases": ["城市维护建设税", "城建税", "教育费附加", "地方教育附加"],
        "parent_law": "中华人民共和国城市维护建设税法",
        "priority": 4,
    },
    # 资源环境税
    "资源税": {
        "aliases": ["资源税", "水资源税", "矿产资源税"],
        "parent_law": "中华人民共和国资源税法",
        "priority": 4,
    },
    "环境保护税": {
        "aliases": ["环境保护税", "环保税", "排污税"],
        "parent_law": "中华人民共和国环境保护税法",
        "priority": 4,
    },
    # 其他
    "税收征管": {
        "aliases": ["税收征管", "税收征收管理", "征管", "税务登记", "纳税申报",
                    "发票管理", "发票", "税务稽查", "金税四期"],
        "parent_law": "中华人民共和国税收征收管理法",
        "priority": 1,
    },
    "税收优惠": {
        "aliases": ["税收优惠", "减免税", "退税", "即征即退", "先征后退", "免税"],
        "parent_law": None,
        "priority": 1,
    },
}

TAX_RISK_KEYWORDS = [
    "虚开发票", "骗取留抵退税", "骗取出口退税", "偷税", "逃税", "避税",
    "关联交易", "转让定价", "税收风险", "税务合规", "金税四期指标",
    "两税收入差异", "长亏不倒", "税负率异常", "资金闭环回流",
    "进销项不匹配", "四流合一", "私户收款", "账外经营",
]

INVOICE_KEYWORDS = [
    "增值税发票", "专用发票", "普通发票", "电子发票", "全电发票",
    "发票管理办法", "发票领购", "发票开具", "发票红冲", "发票遗失",
    "发票抵扣", "发票认证", "数电票",
]


def resolve_tax_type(query: str) -> dict:
    """Resolve user query to best-matching tax type."""
    q = query.strip()
    best = None
    best_len = 0
    for tax_type, info in TAX_TYPE_KEYWORDS.items():
        for alias in info["aliases"]:
            if alias in q and len(alias) > best_len:
                best = {"type": tax_type, "matched_alias": alias, **info}
                best_len = len(alias)
    return best


def detect_intent(query: str) -> str:
    """
    Classify user intent to determine search strategy.
    Returns: 'policy_lookup' | 'filing_guide' | 'risk_check' | 'eligibility' | 'invoice'
    """
    q = query.strip()

    # Invoice-related
    if any(kw in q for kw in ["发票", "开票", "红冲", "抵扣认证", "发票遗失"]):
        return "invoice"

    # Risk check
    risk_signals = ["风险", "会不会被查", "预警", "金税", "合规", "稽查", "会被罚款", "合规吗", "违规"]
    if any(kw in q for kw in risk_signals):
        return "risk_check"

    # Filing guide
    filing_signals = ["申报", "汇算清缴", "截止日期", "怎么申报", "年度汇算", "预缴", "报送", "备案"]
    if any(kw in q for kw in filing_signals):
        return "filing_guide"

    # Eligibility check
    eligibility_signals = ["符合条件", "能不能享受", "符不符合", "是否适用",
                           "可以抵扣吗", "适用吗", "资格", "能享受", "可以享受"]
    if any(kw in q for kw in eligibility_signals):
        return "eligibility"

    # Default: policy lookup
    return "policy_lookup"


# ── NPC API Client ──────────────────────────────────────────────────────────
def _title_match_rank(title: str, keyword: str, parent_law: str = "") -> tuple:
    """给标题模糊检索的结果排序用，键越小越相关。

    NPC 标题模糊检索按发布时间排，返回的只是"标题里含检索词部分字"的法律。
    这里按三个维度重排：是否就是该税种的本体法、是否以检索词结尾（"XX法"
    才是用户要的）、检索词在标题里的位置。命中不到的排到最后。
    最后一位恒为 0：并列项保持接口返回的次序，不按标题字母排。

    parent_law 是 resolve_tax_type() 认出来的税种对应的本体法名，需要调用方
    传进来。"税收征管"这类查询靠关键词本身排不出来：本体法全名是"税收征收
    管理法"，标题里没有"税收征管"四字，而两高的司法解释标题里恰好含这四字，
    会被判成高分排在前面。有本体法兜底才排得对。
    """
    kw = (keyword or "").strip()
    t = (title or "").replace("中华人民共和国", "")
    if not kw:
        return (9, 9, 0)
    if parent_law and t == parent_law.replace("中华人民共和国", ""):
        return (0, 0, 0)
    pos = t.find(kw)
    if pos < 0:
        # 检索词被打散命中（"企业所得税" 命中"企业破产法"），排到最后
        return (3, 8, 0)
    ends_with = t.endswith(kw) or t.endswith(kw + "法")
    return (1 if ends_with else 2, pos, 0)


_MIN_INTERVAL = 0.6          # NPC 连续请求过快会直接断连，不回 429
_last_request_at = 0.0


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Wrapper with retry for 429, 5xx and connection drops."""
    global _last_request_at
    max_retries = 3
    last_exc = None
    for attempt in range(max_retries):
        gap = _MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if gap > 0:
            time.sleep(gap)
        try:
            r = requests.request(method, url, verify=VERIFY_SSL, headers=HEADERS,
                                 timeout=15, **kwargs)
        except requests.RequestException as e:
            # 断连与 429 一样是对方在限流，退避后重试
            last_exc = e
            time.sleep(2 ** (attempt + 1))
            continue
        finally:
            _last_request_at = time.monotonic()
        if r.status_code == 429:
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code in {500, 502, 503} and attempt < max_retries - 1:
            time.sleep(1)
            continue
        return r
    if last_exc is not None:
        raise last_exc
    return r


def search_tax(keyword: str, *,
               scope: str = "title",
               search_type: int = 2,
               status: Optional[int] = 3,
               date_from: Optional[str] = None,
               date_to: Optional[str] = None,
               page: int = 1,
               size: int = 20,
               sort: str = "relevance") -> dict:
    """
    Search tax policies via NPC API.

    Args:
        keyword: search term
        scope: 'title' (searchRange=1) or 'fulltext' (searchRange=2). fulltext
            is NOT filtered by the search term; it is kept only so callers get an
            explicit _reliability=low marker. Use tax_web_search.py for real
            body-text search.
        search_type: 1=exact, 2=fuzzy
        status: None=all, 3=effective, or any sxx code
        date_from: ISO date string e.g. '2024-01-01'
        date_to: ISO date string e.g. '2026-12-31'
        page: page number
        size: results per page (max 100)
        sort: 'relevance' or 'date'
    """
    search_range = 1 if scope == "title" else 2
    sxx = [status] if status is not None else []
    gbrq = []
    if date_from and date_to:
        gbrq = [date_from, date_to]
    elif date_from:
        gbrq = [date_from, "2099-12-31"]

    sort_param = {"order": "", "sort": ""}
    if sort == "date":
        sort_param = {"order": "-1", "sort": "gbrq"}

    cache_key = _cache._key(
        "search", keyword, str(search_range), str(search_type),
        str(status), str(date_from), str(date_to), str(page), str(size), sort
    )
    cached = _cache.get(cache_key, max_age=300)
    if cached:
        cached["_from_cache"] = True
        return cached

    payload = {
        "searchRange": search_range,
        "searchType": search_type,
        "searchContent": keyword,
        "pageNum": page,
        "pageSize": min(size, 100),
        "orderByParam": sort_param,
        "flfgCodeId": [],
        "zdjgCodeId": [],
        "sxx": sxx,
        "gbrq": gbrq,
        "sxrq": [],
        "gbrqYear": [],
        "xgzlSearch": False,
    }

    r = _request("POST", f"{BASE_URL}/law-search/search/list", json=payload)
    r.raise_for_status()
    data = r.json()
    outer = data.get("data", data)
    total = outer.get("total", 0)
    rows = outer.get("rows", outer.get("list", []))

    # Clean HTML tags from names (uses module-level _CLEAN_HTML_RE)
    def clean_html(s):
        return _CLEAN_HTML_RE.sub("", s) if s else ""

    results = []
    for item in rows:
        sxx_code = item.get("sxx", 0)
        results.append({
            "id": item.get("bbbs", ""),
            "title": clean_html(item.get("flfgname", item.get("title", ""))),
            "publish_date": item.get("gbrq", ""),
            "effective_date": item.get("sxrq", ""),
            "status_code": sxx_code,
            "status": SXX_MAP.get(sxx_code, f"未知({sxx_code})"),
            "issuing_authority": item.get("zdjgName", ""),
            "category": item.get("flxz", ""),
        })

    # 标题模糊检索按发布时间排序，不按相关度："企业所得税" 首条是企业破产法，
    # 目标法落在第 5 位。命中的都是"含检索词部分字"的法律，所以按检索词连续
    # 出现在标题中的位置重排一次，把完整命中的排到前面。相同档次内保持接口
    # 给的次序（Python 的 sort 稳定），不改动并列项的相对顺序。
    #
    # 重排只对取回的这一页有效，页外的条目无论多相关都排不进来。实测 16 个
    # 税种里有 7 个在 size<=3 时首位是错的（消费税返回消费者权益保护法、
    # 税收征管返回两高司法解释），所以向接口多要一些再排，排完再截回调用方
    # 要的条数。取不满时说明总数本来就不足 size，不补。
    # 翻页（page>1）不做过取：第 2 页的语义是接口原序的第 21 条起，掺入第 1 页
    # 的条目会让翻页结果失真。
    if search_type == 2 and scope == "title" and page == 1:
        fetch_size = min(max(size * 3, 20), 100)
        if fetch_size != size:
            extra = _request("POST", f"{BASE_URL}/law-search/search/list",
                             json={**payload, "pageSize": fetch_size})
            extra.raise_for_status()
            extra_outer = extra.json()
            extra_outer = extra_outer.get("data", extra_outer)
            extra_rows = extra_outer.get("rows", extra_outer.get("list", []))
            have = {r.get("bbbs", "") for r in rows}
            for item in extra_rows:
                bbbs = item.get("bbbs", "")
                if bbbs in have:
                    continue
                have.add(bbbs)
                results.append({
                    "id": bbbs,
                    "title": clean_html(item.get("flfgname", item.get("title", ""))),
                    "publish_date": item.get("gbrq", ""),
                    "effective_date": item.get("sxrq", ""),
                    "status_code": item.get("sxx", 0),
                    "status": SXX_MAP.get(item.get("sxx", 0), f"未知({item.get('sxx', 0)})"),
                    "issuing_authority": item.get("zdjgName", ""),
                    "category": item.get("flxz", ""),
                })
        tax_info = resolve_tax_type(keyword)
        parent_law = (tax_info or {}).get("parent_law") or ""
        results.sort(key=lambda it: _title_match_rank(it["title"], keyword, parent_law))
        del results[size:]

    result = {
        "keyword": keyword,
        "scope": scope,
        "search_type": "exact" if search_type == 1 else "fuzzy",
        "total": total,
        "page": page,
        "page_size": size,
        "results": results,
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "_from_cache": False,
    }

    # 可靠性标记：NPC 的正文模糊检索不按检索词过滤，返回的是与查询无关的法规流
    # （结果实际按发文时间排列）。实测 "研发费用 资本化" 命中 5,964 条，排在前面的
    # 却是国防法、香港基本法、公司法；"增值税" 命中 1,070 条，首条是外交特权与豁免
    # 条例；连无意义词 "紫貂养殖" 都能命中民法典，说明接口根本没在检索。
    # 标题检索与精确检索不受此影响。
    if search_type == 2 and scope == "fulltext":
        result["_reliability"] = "low"
        result["_reliability_note"] = (
            "NPC 正文模糊检索不按检索词过滤，结果与查询无关（按发文时间排列）；"
            "请改用 --scope title 或 --exact 检索条文，需要实务解读请用税务总局/税屋/微信公众号"
        )

    _cache.set(cache_key, result)
    return result


# ── CLI ─────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Search China tax policies via NPC National Laws Database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tax_search.py "增值税" --size 20
  python tax_search.py "中华人民共和国增值税法" --exact
  python tax_search.py "企业所得税" --status 3 --from 2024-01-01 --sort date
  python tax_search.py "增值税" --status 3 --verbose
  python tax_search.py "增值税" --cache
  python tax_search.py --cache-clear
  python tax_search.py --cache-stats
        """
    )
    p.add_argument("keyword", nargs="?", help="Search keyword")
    p.add_argument("--scope", choices=["title", "fulltext"], default="title",
                   help="Search scope (default: title). fulltext does not filter "
                        "by the query and is only useful for confirming that")
    p.add_argument("--exact", action="store_true",
                   help="Exact title match (default: fuzzy)")
    p.add_argument("--status", type=int, default=3,
                   help="Status filter: 1=abolished, 2=amended, 3=effective, 4=pending. Omit for all.")
    p.add_argument("--from", dest="date_from", help="Publish date from (YYYY-MM-DD)")
    p.add_argument("--to", dest="date_to", help="Publish date to (YYYY-MM-DD)")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=20)
    p.add_argument("--sort", choices=["relevance", "date"], default="relevance")
    p.add_argument("--json", action="store_true", help="Output JSON")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    p.add_argument("--cache", action="store_true", help="Enable cache (5min TTL)")
    p.add_argument("--no-cache", action="store_true", help="Disable cache")
    p.add_argument("--cache-stats", action="store_true", help="Show cache stats")
    p.add_argument("--cache-clear", action="store_true", help="Clear cache")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    global _cache

    # Cache management
    if args.cache:
        _cache = _CacheManager(enabled=True)
    elif args.no_cache:
        _cache = _CacheManager(enabled=False)

    if args.cache_stats:
        s = _cache.stats()
        print(json.dumps({"cache": s}, ensure_ascii=False, indent=2))
        return
    if args.cache_clear:
        _cache.clear()
        print("Cache cleared.")
        return

    if not args.keyword:
        parser.print_help()
        return

    result = search_tax(
        args.keyword,
        scope=args.scope if not args.exact else "title",
        search_type=1 if args.exact else 2,
        status=args.status,
        date_from=args.date_from,
        date_to=args.date_to,
        page=args.page,
        size=args.size,
        sort=args.sort,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Human-readable output
    status_icon = {1: "🔴", 2: "🟡", 3: "🟢", 4: "🔵"}
    cache_tag = " [缓存]" if result.get("_from_cache") else ""
    print(f"🔍 搜索 \"{args.keyword}\" | {args.scope}/{result['search_type']} | "
          f"共 {result['total']} 条 | {result['searched_at']}{cache_tag}")
    if result.get("_reliability") == "low":
        print(f"  ⚠️ _reliability: low — {result['_reliability_note']}")
    print()

    for item in result["results"]:
        icon = status_icon.get(item["status_code"], "❓")
        print(f"  {icon} [{item['status']}] {item['title']}")
        if args.verbose:
            print(f"     公布: {item['publish_date']}  施行: {item['effective_date']}")
            print(f"     发布机关: {item['issuing_authority']}")
            print(f"     分类: {item['category']}   ID: {item['id']}")
        else:
            print(f"     公布: {item['publish_date']}  ID: {item['id']}")
        print()

    if result["total"] > args.size:
        total_pages = (result["total"] + args.size - 1) // args.size
        print(f"  📄 第 {args.page}/{total_pages} 页，共 {result['total']} 条")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    main()
