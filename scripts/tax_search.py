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


# ── 税种与专题 → 检索词映射 ──────────────────────────────────────────────────
# authority 标明该专题的依据在哪一源：
#   "npc"  有本体法，直接按 title 检索该法
#   "sta"  没有本体法。转让定价、反避税这类专题的依据是税务总局的规范性文件
#          （特别纳税调整实施办法、转让定价同期资料文档管理办法等），NPC 库里
#          只有法律、行政法规，搜"转让定价"返回的是国有土地使用权出让和转让
#          暂行条例这类无关法规，搜"反避税"返回 0 条。见到 authority=sta 的
#          专题就改查税务总局，不要拿 NPC 的结果当依据。
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
    # 税收优惠没有本体法，但依据确实是总局文件（财税〔2008〕1号、各税种优惠
    # 公告等），按 npc 查会拿到税收征管法、实施细则这类无关法律，所以路由到 sta。
    "税收优惠": {
        "aliases": ["税收优惠", "减免税", "退税", "即征即退", "先征后退", "免税"],
        "search_term": "税收优惠",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    # ── 国际税收 ──
    # 这几项的依据全在税务总局规范性文件与税收协定公告里，NPC 库检索无效：
    # 搜"税收协定"命中 29 条全无关，搜"常设机构"命中 70 条全无关，
    # 搜"转让定价"命中 10 条全是土地/森林/矿产资源转让条例。
    # 每一项单列成一个专题，因为它们的依据是各自独立的文件，合在一起就没有
    # 一个能用的检索词——搜"国际税收"命中 988 条，头几条全是外国税改新闻。
    # search_term 是实测能翻出依据的检索词，不填就拿键名去搜。键名只是分类标签，
    # 不是检索词："税收争议救济"、"纳税担保与信用"当检索词都取不到依据。
    "税收协定": {
        "aliases": ["税收协定", "双重征税", "税收居民身份", "居民企业身份",
                    "税收条约"],
        "search_term": "税收协定",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "常设机构": {
        "aliases": ["常设机构", "营业场所", "固定场所", "工程场所", "服务机构"],
        "search_term": "常设机构",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "非居民企业": {
        "aliases": ["非居民企业", "非居民", "源泉扣缴", "预提所得税", "扣缴义务人",
                    "支付所得"],
        "search_term": "非居民企业",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "境外所得": {
        "aliases": ["境外所得", "境外投资", "境外股息", "境外权益", "递延纳税"],
        "search_term": "境外所得",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "税收抵免": {
        "aliases": ["税收抵免", "抵免限额", "抵免额", "分国不分项", "国别抵免"],
        "search_term": "税收抵免",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "受控外国企业": {
        "aliases": ["受控外国企业", "外国企业股息", "视同股息分配"],
        "search_term": "境外所得",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "转让定价": {
        "aliases": ["转让定价", "关联交易", "同期资料", "预约定价", "资本弱化",
                    "成本分摊", "国别报告", "特别纳税调整", "受控外国企业"],
        "search_term": "转让定价",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "反避税": {
        "aliases": ["反避税", "一般反避税", "特别纳税调整", "BEPS", "税基侵蚀"],
        # "反避税"本身命中的多是税收协定文本，"一般反避税管理办法"只有 4 条
        # 且 0 条来自法规库。改用"预约定价安排"（150 条），前 4 条里有
        #《特别纳税调整实施办法（试行）》与预约定价系列公告。
        "search_term": "预约定价安排",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    # ── 税收征管与救济 ──
    # 检索词都是实测出来的，不是按字面推的：
    #   "税收行政处罚" 命中 7 条且 0 条来自法规库，"行政处罚" 命中 1301 条
    #     且前 3 条就有《税务行政处罚"首违不罚"事项清单》公告。
    #   "税收行政复议" 命中 65 条但前 3 条是无关的查补税金文件，
    #     "行政复议" 命中 231 条，首条即《行政复议法》本身。
    #   "纳税担保" 命中 41 条、前 3 条无担保文件，"纳税信用评价与修复"
    #     命中 13 条且首条即《纳税信用评价与修复有关事项的公告》。
    "税务行政处罚": {
        "aliases": ["税务行政处罚", "行政处罚", "听证", "裁量权", "罚款", "滞纳金"],
        "search_term": "行政处罚",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "税收争议救济": {
        "aliases": ["行政复议", "行政诉讼", "纳税争议", "起诉期限", "复议前置"],
        "search_term": "行政复议",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    "纳税担保与信用": {
        "aliases": ["纳税担保", "纳税保证人", "纳税信用", "失信主体", "纳税信用修复"],
        "search_term": "纳税信用评价与修复",
        "parent_law": None,
        "authority": "sta",
        "priority": 1,
    },
    # ── 从业/凭证类 ──
    "烟叶税": {
        "aliases": ["烟叶税", "烟叶"],
        "parent_law": "中华人民共和国烟叶税法",
        "priority": 5,
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
    """Resolve user query to best-matching tax type.

    返回值多带三个字段：parent_law（该专题的本体法，无则 None）、
    authority（该专题的依据在哪一源，"npc" 或 "sta"）、search_term（该专题
    实测可用的检索词，authority="sta" 时拿它去查，别拿分类名去搜）。
    见到 authority="sta" 就不要再查 NPC，改为 tax_fgk.py 查税务总局。
    """
    q = query.strip()
    best = None
    best_len = 0
    for tax_type, info in TAX_TYPE_KEYWORDS.items():
        for alias in info["aliases"]:
            if alias in q and len(alias) > best_len:
                best = {"type": tax_type, "matched_alias": alias, **info}
                best_len = len(alias)
    if best is not None:
        best.setdefault("authority", "npc")
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


RELIABILITY_NOTES = {
    "low": "结果与查询无关，不得作为依据引用；请换检索方式或换数据源。",
    "medium": "结果已排序但可能偏题（全文分词匹配）；可用于定位法规，"
              "确定条文归属请回到标题检索。",
}

_MIN_INTERVAL = 0.6          # NPC 连续请求过快会直接断连，不回 429
_last_request_at = 0.0


def _fulltext_match_rank(title: str, keyword: str, parent_law: str, score: float) -> tuple:
    """给正文检索的结果重排用，键越小越相关。

    NPC 的 sort=score 是按全文分词打分，通用词会把无关法规拉高——搜"研发费用
    加计扣除"时《诉讼费用交纳办法》（只命中"费用"）排在《企业所得税法》前面。
    这里只认两种命中：检索词整段出现在标题里，或标题是该税种的本体法。
    两者都没有时按接口给的 score 排，让标题没提到但正文确实相关的法规仍能
    浮上来，score 相同则保持接口次序。
    """
    kw = (keyword or "").strip()
    t = (title or "").replace("中华人民共和国", "")
    if not kw:
        return (9, 0.0, 0)
    if kw in t:
        return (0, 0.0, 0)
    if parent_law and t == parent_law.replace("中华人民共和国", ""):
        return (0, 0.0, 0)
    return (1, -score, 0)


def _is_challenge_page(r: requests.Response) -> bool:
    """判断响应是不是反爬挑战页。

    NPC 触发限流时不回 429，而是回 HTTP 200 + 一份 38,499 字节的 HTML，
    内容是 <noscript><h1>Please enable JavaScript</h1></noscript> 加一段
    混淆 JS。直接 .json() 报的是"Expecting value: line 1 column 1"，
    跟限流看不出关系。并行跑多个检索脚本时必现。
    """
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "json" in ctype:
        return False
    head = r.text[:600].lower()
    return "please enable" in head or "<!doctype html" in head


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Wrapper with retry for 429, 5xx, connection drops and challenge pages."""
    global _last_request_at
    max_retries = 4
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
        if _is_challenge_page(r):
            # 挑战页说明这次没拿到数据。退避越久越容易过去，实测 2/4/8 秒有效。
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
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
    elif search_range == 2:
        # 正文检索不给排序参数时按发文时间返回："增值税"首条是《外交特权与
        # 豁免条例》（1986 年）。接口每条都带 score 字段并支持按它降序，
        # 加上后同一查询首条变成《土地增值税法》。
        sort_param = {"order": "-1", "sort": "score"}

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
    try:
        data = r.json()
    except ValueError as e:
        # 限流时对方会回空正文或 HTML 拦截页，HTTP 仍是 200，直接 .json() 报的是
        # "Expecting value: line 1 column 1"，看不出是哪一层坏了。实测并发跑
        # 多个检索脚本时就会触发，所以把真实状态带出来。
        raise RuntimeError(
            f"NPC 返回的不是 JSON（HTTP {r.status_code}，"
            f"Content-Type {r.headers.get('Content-Type')}，"
            f"{len(r.content)} 字节）：{r.text[:120]!r}；"
            f"原异常 {e}。多半是并发请求被限流，间隔几秒重试。") from e
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
    if search_type == 2 and scope == "fulltext" and page == 1:
        # 正文检索默认按发文时间排，"增值税" 首条是《外交特权与豁免条例》。
        # 接口其实算出了相关度（每条带 score 字段）并支持 sort=score 降序，
        # 加上后首条变成《土地增值税法》。这里再按"检索词整段出现在标题"重排
        # 一次：sort=score 是全文分词打分，"研发费用加计扣除" 会把命中"费用"
        # 的《诉讼费用交纳办法》顶到第一，而《企业所得税法》才真正规定加计扣除。
        scores = [src.get("score") or 0.0 for src in rows]
        parent_law = (resolve_tax_type(keyword) or {}).get("parent_law") or ""
        results = [it for _, it in sorted(
            zip(scores, results),
            key=lambda p: _fulltext_match_rank(p[1]["title"], keyword, parent_law, p[0]))]
        del results[size:]

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

    # 可靠性标记。NPC 正文检索确实是按词过滤的（"紫貂养殖" 2,415 条、"zzzqqq123"
    # 只有 7 条，两者条数不同），原先的判断是错的；真实缺陷是它默认按发文时间
    # 排序。加 sort=score 降序并按整段命中重排后已可用于取依据，但命中的是
    # 全文分词，短词仍会被通用词带偏（"研发费用加计扣除"会带到《诉讼费用
    # 交纳办法》），所以标记为 medium 而不是 high：能用，但查具体条文要回到
    # 标题检索。
    if search_type == 2 and scope == "fulltext":
        result["_reliability"] = "medium"
        result["_reliability_note"] = RELIABILITY_NOTES["medium"]

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
    if result.get("_reliability"):
        print(f"  ⚠️ _reliability: {result['_reliability']} — "
              f"{result['_reliability_note']}")
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
