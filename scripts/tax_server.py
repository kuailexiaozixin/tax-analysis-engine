#!/usr/bin/env python3
"""Flask API server for tax-analysis-skill frontend."""
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from tax_search import search_tax, detect_intent, resolve_tax_type, TAX_TYPE_KEYWORDS
from tax_analyze import accounting_gap, accounting_note
from tax_analyze import legislative_stage, legislative_note
from tax_detail import fetch_detail, download_bytes, SXX_MAP, _parse_docx_from_bytes
from tax_web_search import search_chinatax, build_filters
from tax_fgk import search_fgk
from tax_so360 import so360_search
from tax_shui5 import search_shui5
from tax_wechat import search_wechat
from tax_aggregator import aggregate_search, DEFAULT_SOURCES
import tax_calc
import tax_llm

from flask import Flask, request, jsonify, send_from_directory
import urllib3
urllib3.disable_warnings()

app = Flask(__name__, static_folder=None)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# 监听地址。默认只绑本机回环：这个服务会拿本机 IP 去抓 NPC / 总局 / 360 /
# 搜狗，暴露到局域网等于把本机配额借给别人用。
#   本机自用   默认 127.0.0.1
#   局域网共享 TAX_BIND=0.0.0.0
#   容器里跑   必须 TAX_BIND=0.0.0.0——`-p` 端口映射靠 DNAT 把包投给容器自己
#              的 IP，只监听回环时没有任何 socket 收得到（实测 WinError 10061）。
#              此时把暴露面收在宿主机侧： docker run -p 127.0.0.1:5080:5080
BIND_HOST = os.getenv("TAX_BIND", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("TAX_PORT", "5080"))

# 界面「数据源」那一栏的取值域，也就是 /api/search 认的四条分支。请求体里显式给了
# 这四个之外的值（包括空串）一律 400 并列出取值域：以前这样的值会落进 else 分支，
# 按 NPC 查完回 200，调用方以为按自己点的那一源查过了（实测 source=ctax 与
# source=aggregate 都回 200、走的是 search_tax）。不给 source 才是默认 NPC。
SEARCH_SOURCES = ("npc", "chinatax", "fgk", "aggregated")

# 界面「本轮条数」那一栏的取值域。上界不是随口定的：tax_search.search_tax 往下发的是
# min(size, 100)，比它大的值会被 NPC 自己截成 100，界面上写的条数与实际取回的条数
# 就成了两个数。这里宁可在闸门上就把上界说明白。
SIZE_DEFAULT = 20
SIZE_MAX = 100

# 没有页码维度的那两路：聚合是六源各取 size 条再合并截断，法规库是一次连扫若干页
# 按 size 截断（tax_fgk.search_fgk 只有 max_pages，没有 pageNum）。这两路上的页码
# 控件必须说清没生效，不能静默停在第一轮取回的那批条目上。
PAGELESS_LAYERS = ("aggregated", "fgk")


def _int_param(data: dict, name: str, default: int, lo: int, hi: int = None) -> int:
    """把请求体里的数值控件读成一个整数，坏值在这一层就报出去。

    size 与 page 直接进上游请求体的 pageSize 与 pageNum。改动前这两枚是 `min(data.get
    ("size", 20), 50)` 这样吃的：发成字符串时比较运算抛 TypeError，回的是 HTML 的
    500 页面，调用方看不出是自己多带了一个字段；发 0 时上游照样回 200 且清单为空，
    界面把它读成"库里没有"。与 source／aging／日期那三道闸同一口径。
    """
    raw = data.get(name)
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ValueError(f"{name} 要的是整数，收到的是 {raw!r}")
    if isinstance(raw, str):
        raw = raw.strip()
        if not re.fullmatch(r"\d+", raw):
            raise ValueError(f"{name} 要的是整数，收到的是 {raw!r}")
        value = int(raw)
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError(f"{name} 要的是整数，收到的是 {raw!r}")
        value = int(raw)
    else:
        raise ValueError(f"{name} 要的是整数，收到的是 {type(raw).__name__}")
    domain = f"{lo}~{hi}" if hi is not None else f"不小于 {lo}"
    if value < lo or (hi is not None and value > hi):
        raise ValueError(f"{name} 只认 {domain} 的整数，收到的是 {raw!r}")
    return value


# 检索意图在界面上显示成什么。取值域是 tax_search.INTENTS；这里的措辞比
# tax_formatter.INTENT_HEADERS 短，因为界面那一栏是个标签，不是 markdown 标题。
# 两张表各管一个界面，键集合由 tests/test_routing_terms.py::test_intent_vocabularies_agree 对齐。
INTENT_LABELS = {
    "policy_lookup": "政策查询",
    "filing_guide": "申报指导",
    "risk_check": "合规风险",
    "eligibility": "资格判定",
    "invoice": "发票处理",
}

# 归不出税种时检索词就是用户原话，NPC 的标题检索按字面匹配，会还给一大摞无关
# 法条（实测整句提问回 687 条、首条是证券投资基金法）。结果本身挑不出来，就把
# "没归类"这件事交给界面去说，不假装这一摞是按题找出来的。
# 两条分支（聚合 / 单查 NPC）共用同一句，措辞改一处就够，不会各说各话。
UNROUTED_NOTE = ("未能归类到税种或专题：以上是按原话字面检索标题的结果，"
                 "与本题是否相关需逐条核对")


def _ui_filters(scope: str, exact, date_from, date_to, aging: str = "") -> dict:
    """把界面上的「范围/匹配/日期/时效」四个控件翻成 search5 的收窄维度。

    走 search5 的源（chinatax／fgk，含 NPC 分支里被自动换源到法规库的 sta 专题）
    都调这一个，免得控件在某一条路径上又被静默丢掉。命令行开关与判据在
    references/commands.md，逐档取值域与单发读数在 subskills/chinatax/NOTE.md。

    aging 是「时效」控件在 search5 那一侧的取值（全文有效／已修改／全文失效／
    全文废止／尚未生效），与 NPC 的 status 码是两套值域，前端按数据源切换：
    NPC 发 status 码，search5 发 aging 文本。这里不替 aging 兜默认值——「增值税」
    × 文件类标签那一档里五个时效值合计只覆盖约四成条目，其余条目该栏为空（解读类
    与其他文件本来就不填）。默认收窄会把没录时效的现行文件一起筛掉，所以宁可让控件留空。

    Raises:
        ValueError: 日期不合法，或 aging 不在 search5 的五个时效值里——调用方要
            挡成 400：非法值发出去接口回的是 `total=0`，不回错误码，看着像这一维筛得很干净。
    """
    return build_filters(
        in_title=(scope == "title"),
        precise=bool(exact),
        cwrq_from=date_from or "",
        cwrq_to=date_to or "",
        aging=aging or "",
    )


# 界面「排序」控件的取值 → search5 的 order 名。这个控件只对 NPC 那一路
# 生效时，数据源切到税务总局／法规库就被静默丢掉，与「范围/匹配/日期」是同一个缺陷。
SORT_TO_ORDER = {"relevance": "relevance", "date": "date_desc"}


def _stamp_source(result: dict, source: str) -> None:
    """给单源那三路取回的条目逐条标上真正的来源。

    聚合那一路由 tax_aggregator.aggregate_search 写 `_source`，卡面的来源角标与
    日期那一栏的读法都按它来；NPC／税务总局／法规库三条单源分支以前不写，于是
    界面只能按"用户点的那一源"给日期起名。而这两路里有自动换源：归类成总局专题
    （税收优惠、转让定价等）时按法规库取回，归类成境外辖区专题（支柱二等）时按
    税务总局全站层取回——这时条目是 search5 那一层的，它的日期是成文日期（cwrq），
    按 NPC 标就成了"公布日期"。引用时拿公布日去核，与文件上的成文日不是同一天。
    """
    for item in (result.get("results") or result.get("items") or []):
        if isinstance(item, dict):
            item.setdefault("_source", source)

def _count_note(result: dict, source: str, page: int) -> str:
    """把"库里有多少"与"这一轮给了多少"分开说，整句由后端给，前端只照抄。

    改动前界面那一栏只有一个数：`result.total || result.total_items || items.length`。
    单源那三路读的是上游的命中总数（实测「增值税」NPC 45 条、税务总局 1910 条），
    卡面却只有本轮取回那 20 张；聚合那一路读的是 total_items（六源去重后收全的条数），
    而 tax_aggregator 往下给的是 items[:size*3]，两个口径共用一个"📋 N条"，
    看页面的人会以为屏幕上这一摞就是全部。
    """
    items = result.get("results") or result.get("items") or []
    if source == "aggregated":
        lib, head = result.get("total_items"), "六源共收"
    else:
        lib, head = result.get("total"), "库内命中"
    tail = f" · 第 {page} 页" if page > 1 else ""
    if lib is None:
        return f"本轮给出 {len(items)} 条{tail}"
    if lib == len(items):
        return f"{head} {lib} 条（已全部给出）{tail}"
    return f"{head} {lib} 条 · 本轮给出 {len(items)} 条{tail}"


_text_cache = {}
_interp_cache = {}

# Trusted tax-practice WeChat public account sources for interpretation/web search
# ── Content filter: domains/keywords that indicate non-tax garbage results ──
_BLOCKED_DOMAINS = [
    "game", "4399", "7k7k", "xiaoyouxi", "youxi", "yxdown", "3dmgame",
    "gamersky", "duowan", "17173", "52pk", "sina.com.cn/game",
    "bilibili.com/video", "douyin.com", "kuaishou.com", "weibo.com",
    "zhihu.com/question", "tieba.baidu.com", "baike.baidu.com/item",
]
_BLOCKED_TITLE_WORDS = [
    "游戏", "小游戏", "手游", "网游", "页游", "棋牌", "捕鱼",
    "娱乐", "八卦", "明星", "综艺", "电影", "电视剧",
    "体育", "彩票", "竞彩", "博彩",
    "小说", "漫画", "动漫",
]

def _is_garbage_result(url: str = "", title: str = "") -> bool:
    """Filter out clearly irrelevant/non-tax content."""
    lower_url = url.lower()
    for kw in _BLOCKED_DOMAINS:
        if kw in lower_url:
            return True
    lower_title = title.lower()
    for kw in _BLOCKED_TITLE_WORDS:
        if kw in lower_title:
            return True
    return False

TAX_PRACTICE_SOURCES = [
    {"name": "小颖言税", "query_hint": "小颖言税"},
    {"name": "税海涛声", "query_hint": "税海涛声"},
    {"name": "会计网",   "query_hint": "会计网"},
    {"name": "税小课",   "query_hint": "税小课服务"},
    {"name": "朴税",     "query_hint": "朴税"},
    # 这两个是 /api/web-related 实际会直连的源，不列进来的话，
    # 税屋和公众号的条目会被打成"实务解读"以外的泛标签。
    {"name": "税屋",     "query_hint": "shui5.cn"},
    {"name": "微信公众号", "query_hint": "mp.weixin.qq.com"},
]
# 官方站点：这些是政策原文来源，不能标成"实务解读"
OFFICIAL_DOMAINS = [
    "chinatax.gov.cn", "mof.gov.cn", "gov.cn", "npc.gov.cn",
    "chinatax.cn", "chinatax.gov",
]
# Sources that should be tagged as "实务解读" even if not from .gov.cn
# 不要用 "tax" 这种裸子串：chinatax.gov.cn 含 tax，会把官方站点误判成实务解读。
PRACTICE_DOMAIN_KEYWORDS = [
    "小颖言税", "税海涛声", "会计网", "税小课", "朴税",
    "mp.weixin.qq.com", "zhuanlan.zhihu.com", "toutiao.com",
    "shui5.cn", "shuiwu", "kuaiji", "chinaacc", "shuilishi", "shuikuai",
]

# ── Interpretation Search Engine ─────────────────────────────────────────

def _date_from_url(url: str) -> str:
    """从 URL 里的日期目录里取发布日期，取不到就返回空串。

    常见形态是 /201904/t20220313_xxx.html 或 /201904/816904caec....shtml。
    后者那串 32 位哈希紧跟在年份后面，直接正则抓 8 位数字会拼出 "2019-04-81"
    这种不存在的日期，所以抓到之后要按真实日历校验一遍。
    """
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", url)
    if not m:
        return ""
    y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        date(y, mo, d)
    except ValueError:
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _search_one_source(site: str, query: str, n: int = 5) -> tuple[list, str]:
    """Search a specific site for policy interpretations, via 360 site: search.

    返回 (结果, 拦截说明)。说明非空表示这一路根本没取到数据，调用方不能把
    0 条当成"该站点没有解读"。

    Bing 在此处已被移除：www.bing.com 对 site: 查询返回 0 个结果块，
    cn/m.bing.com 虽偶发返回 10 个 <li class="b_algo">，但内容与查询无关
    （查 chinatax.gov.cn 企业所得税法 返回"元气壁纸"），属于不可信降级。
    """
    found = so360_search(query, site=site, size=n)
    if found.get("_error"):
        return [], found["_error"]

    results = []
    for item in found["results"]:
        title = item.get("title", "")
        if len(title) < 5 or _is_garbage_result(item.get("url", ""), title):
            continue
        results.append({
            "title": title,
            "url": item["url"],
            "date": _date_from_url(item.get("url", "")),
            "source": site,
            "source_label": _source_label(site),
            "snippet": item.get("snippet", ""),
        })
    return results, ""


def _source_label(site: str) -> str:
    labels = {
        "fgk.chinatax.gov.cn": "税务法规库",
        "chinatax.gov.cn": "国家税务总局",
        "mof.gov.cn": "财政部",
        "npc.gov.cn": "全国人大",
        "gov.cn": "中国政府网",
    }
    for k, v in labels.items():
        if k in site:
            return v
    # Handle provincial subdomain: shanghai.chinatax.gov.cn → "上海税务"
    m = re.match(r"([a-z]+)\.chinatax\.gov\.cn", site)
    if m:
        province_map = {
            "beijing":"北京税务","shanghai":"上海税务","tianjin":"天津税务",
            "chongqing":"重庆税务","guangdong":"广东税务","shenzhen":"深圳税务",
            "zhejiang":"浙江税务","jiangsu":"江苏税务","shandong":"山东税务",
            "sichuan":"四川税务","hubei":"湖北税务","hunan":"湖南税务",
            "henan":"河南税务","hebei":"河北税务","fujian":"福建税务",
            "xiamen":"厦门税务","anhui":"安徽税务","liaoning":"辽宁税务",
            "dalian":"大连税务","jilin":"吉林税务","heilongjiang":"黑龙江税务",
            "jiangxi":"江西税务","shanxi":"山西税务","shaanxi":"陕西税务",
            "gansu":"甘肃税务","qinghai":"青海税务","yunnan":"云南税务",
            "guizhou":"贵州税务","guangxi":"广西税务","hainan":"海南税务",
            "neimenggu":"内蒙古税务","ningxia":"宁夏税务","xinjiang":"新疆税务",
            "xizang":"西藏税务","qingdao":"青岛税务","ningbo":"宁波税务",
        }
        return province_map.get(m.group(1), f"{m.group(1)}税务")
    return site


def _is_practice_source(label_or_domain: str) -> bool:
    """Check if a source label matches known tax practice outlets."""
    s = (label_or_domain or "").lower()
    # 官方站点优先排除：chinatax.gov.cn 里含 "tax"，不先排除会被误判
    for dom in OFFICIAL_DOMAINS:
        if dom in s:
            return False
    for kw in PRACTICE_DOMAIN_KEYWORDS:
        if kw.lower() in s:
            return True
    return False


def _practice_source_name(domain: str, title: str) -> str:
    """Return the practice source name if the domain/title matches, else ''."""
    combined = f"{domain} {title}".lower()
    for src in TAX_PRACTICE_SOURCES:
        if src["query_hint"] in combined:
            return src["name"]
    return ""


def _search_practice_sources(query: str, n: int = 3) -> list[dict]:
    """Practical-interpretation sources: www.shui5.cn + WeChat public accounts.

    Both were previously routed through Bing site: search, which no longer
    returns usable results. They are now queried through their own modules.
    """
    results = []
    seen = set()

    for name, data in [("税屋", search_shui5(query, size=n)),
                       ("微信公众号", search_wechat(query, size=n))]:
        for item in (data or {}).get("results", []):
            href = item.get("url", "")
            if not href or href in seen or _is_garbage_result(href, item.get("title", "")):
                continue
            seen.add(href)
            results.append({
                "title": item.get("title", ""),
                "url": href,
                "date": item.get("date", ""),
                "source": name,
                "source_label": name,
                "snippet": (item.get("snippet") or item.get("content", ""))[:180],
            })
    return results


def search_interpretations(law_title: str, keyword: str = "",
                           sources: list[str] = None,
                           province: str = "") -> dict:
    """Search **official** policy interpretations ONLY from .gov.cn sources.
    Practice/public-account/web results belong in /api/web-related, NOT here."""
    cache_key = f"{law_title}|{keyword}|{'-'.join(sources or [])}|{province}"
    if cache_key in _interp_cache:
        return _interp_cache[cache_key]

    if sources is None:
        if province:
            sources = [f"{province}.chinatax.gov.cn", "chinatax.gov.cn", "www.gov.cn"]
        else:
            sources = ["chinatax.gov.cn", "fgk.chinatax.gov.cn", "mof.gov.cn", "www.gov.cn"]

    title_short = law_title.replace("中华人民共和国", "").strip()

    # 只搜官方站点的解读：每站两条问法，避免 4 站 × 4 问 = 16 次请求
    official_queries = [
        f"{title_short} 政策解读",
        f"{keyword} 官方解读" if keyword and keyword != law_title else f"{title_short} 解读",
    ]

    all_results = []
    seen_urls = set()
    blocked = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for site in sources:
            for q_text in official_queries[:3]:
                futures[(site, q_text)] = pool.submit(
                    _search_one_source, site, q_text, 4
                )

        for (site, q_text), future in futures.items():
            try:
                items, why = future.result(timeout=15)
                if why:
                    blocked.append(why)
                for item in items:
                    if isinstance(item, dict) and item.get("url"):
                        if item["url"] not in seen_urls:
                            seen_urls.add(item["url"])
                            all_results.append(item)
            except Exception:
                pass

    result = {
        "law_title": law_title,
        "keyword": keyword,
        "total": len(all_results),
        "sources": all_results[:15],
        "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 一条站点都没取到时必须说清是"被拦"还是"确实没有"：前端空态文案
    # "该法规可能暂无公开的政策解读文件"只有在搜索引擎正常工作时才成立。
    # 被拦的这种结果不进缓存，否则限流恢复后仍会一直回空。
    if not all_results and blocked:
        result["engine_error"] = blocked[0]
        result["queries_blocked"] = f"{len(blocked)}/{len(futures)}"
        return result

    _interp_cache[cache_key] = result
    return result


def _download_and_extract(bbbs_id: str) -> list[str]:
    """Download DOCX from NPC and extract text paragraphs. Returns list of non-empty lines."""
    if bbbs_id in _text_cache:
        return _text_cache[bbbs_id]

    # 取下载地址与取文件都走 tax_detail 的同一套节流 + 串行闸：
    # 这两步各自裸调 tax_http 就是闸外发请求，网页端连点几次能和检索撞车。
    try:
        content = download_bytes(bbbs_id, "docx")
    except Exception:
        # 本层的契约是"取不到就当没有正文"：拿不到地址、上游非 200、网络异常
        # 都归为取不到，由调用方按"未能获取全文"呈现，不把底层异常抛到路由外。
        return []

    paragraphs = _parse_docx_from_bytes(content)
    if paragraphs:
        _text_cache[bbbs_id] = paragraphs
    return paragraphs


@app.route("/")
def index():
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json() or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "keyword required"}), 400

    scope = data.get("scope", "title")
    search_type = 1 if data.get("exact") else 2
    status = data.get("status", 3)
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    sort = data.get("sort", "relevance")
    source = data.get("source", "npc")
    if source not in SEARCH_SOURCES:
        return jsonify({"error": "source 只认 %s 这几个取值，收到的是 %r"
                                 % ("／".join(SEARCH_SOURCES), source)}), 400
    # 本轮条数与页码两道取值域闸。排在分支之前，是因为这两枚的值要带进下面每一条
    # 取数调用；坏值如果不在这儿拦掉，进到 min()/pageNum 就是 500 或空清单。
    try:
        size = _int_param(data, "size", SIZE_DEFAULT, 1, SIZE_MAX)
        page = _int_param(data, "page", 1, 1)
    except ValueError as e:
        return jsonify({"error": f"筛选参数不合法：{e}"}), 400
    # 真正取回这一批条目的那一层。用户点的是 source，但下面两处会换层（sta 专题
    # 换到法规库、境外专题换到税务总局全站层），换层时这一栏跟着改，条目的来源
    # 标注才不会停在用户点的那一源上。聚合那一路由 tax_aggregator 逐条写。
    effective = source
    # province 只回填给调用方，不参与本接口的检索：NPC 库是全国性法规，没有
    # 省级维度；地方口径要靠 /api/interpretations 的 province 参数换站点。
    # 前端"省份"下拉因此对搜索结果没有过滤作用，别让它看起来是生效的。
    province = data.get("province", "")

    intent = detect_intent(keyword)
    tax_type_info = resolve_tax_type(keyword)
    parent_law = (tax_type_info or {}).get("parent_law") or ""
    authority = (tax_type_info or {}).get("authority", "npc")

    if source == "aggregated":
        # 聚合同样要换源：不换就会把用户原话丢给六源，NPC 侧取回的是含通用字的
        # 无关法规，总局侧又翻不到该专题的规范性文件。sta 专题改用条目自带的
        # search_term 并剔掉 NPC，npc 专题改用 parent_law 精确检索。
        # 日期区间这一维也要交给聚合层：聚合层不吃这两个参数时，界面上的日期控件
        # 在这条路径上就是空转（2026-10-02 实测带与不带日期的 12 条一模一样）。
        # 各路怎么生效见 tax_aggregator._date_scope_note，回给前端的说明在
        # result._date_note。
        try:
            agg_kw = keyword
            agg_sources = None
            if authority == "sta":
                agg_kw = (tax_type_info or {}).get("search_term") or keyword
                agg_sources = [s for s in DEFAULT_SOURCES if s != "npc"]
                result = aggregate_search(agg_kw, size=size, status=status,
                                          scope=scope, sources=agg_sources,
                                          sort=sort, date_from=date_from,
                                          date_to=date_to)
                result["_routed"] = f"{tax_type_info['type']}属总局专题，已改查法规库：{agg_kw}"
            elif authority == "overseas":
                # 界面默认走聚合，这一路不接就和单源那一路同样的空转：税务总局在
                # 聚合层仍按文件类标签检索，支柱二三个检索词各 0 条，取回的全是
                # 其余五路的泛泛文章，界面显示的"没查到"其实是筛错了层。
                agg_kw = (tax_type_info or {}).get("search_term") or keyword
                agg_sources = [s for s in DEFAULT_SOURCES if s != "npc"]
                result = aggregate_search(agg_kw, size=size, status=status,
                                          scope=scope, sources=agg_sources,
                                          sort=sort, date_from=date_from,
                                          date_to=date_to, file_only=False)
                result["_routed"] = (
                    f"{tax_type_info['type']}的规则主体在境外辖区，已改查税务总局全站层"
                    f"（文件类标签之外）：{agg_kw}")
            else:
                if parent_law:
                    agg_kw = parent_law
                # 「匹配」这一栏在聚合里只约束走 NPC 的那一路（aggregate_search 往下
                # 只在 npc 那一条 future 上用 exact，其余五路没有这个参数）。改动前这
                # 一行写的是 exact=bool(parent_law)，请求体里的值从不读：归不出本体法时
                # 用户在界面上点"精确"是空转。现在两问合一——用户点精确就按精确，
                # 检索词是本体法名时仍强制精确（否则模糊按发布时间排，本体法被顶出首位）。
                result = aggregate_search(agg_kw, size=size, status=status,
                                          scope=scope,
                                          exact=bool(parent_law) or bool(data.get("exact")),
                                          sort=sort, date_from=date_from,
                                          date_to=date_to)
                if parent_law:
                    result["_routed"] = f"按{tax_type_info['type']}的本体法检索：{parent_law}"
                else:
                    result["_routed"] = UNROUTED_NOTE
        except ValueError as e:
            # aggregate_search 里 build_filters 会校验日期；非法日期在这一层报 400，
            # 不能让接口静默回一份没筛过的清单。
            return jsonify({"error": f"筛选参数不合法：{e}"}), 400
    elif source in ("chinatax", "fgk"):
        # 界面「范围/匹配/日期/时效/排序」控件只喂给 NPC 那一路时，数据源切到
        # 税务总局或法规库就被静默丢掉——控件看着是全局的、实际只对 NPC 生效。
        # 这里把它们翻成 search5 的收窄维度与检索选项透传下去（见 _ui_filters）。
        try:
            ui_filters = _ui_filters(scope, data.get("exact"), date_from, date_to,
                                     data.get("aging"))
        except ValueError as e:
            # 日期输入框正常给的是 YYYY-MM-DD；手改过的请求体给非法日期或非法时效时，
            # 接口对非法值回 total=0 且不发错误码，所以在这一层就报 400。
            return jsonify({"error": f"筛选参数不合法：{e}"}), 400
        ui_order = SORT_TO_ORDER.get(sort, "relevance")
        if authority == "overseas":
            # 界面把数据源点到税务总局或法规库时也要换层：这两路都在文件类标签
            # 里检索，而这一类在中国官方法规库里没有对应文件（实测见专题项的
            # note）。不换就是回一份空清单，读起来像"这件事没有文件"。换层的原因
            # 写进 _routed，与 sta 那一路把 NPC 换成法规库同形。
            term = (tax_type_info or {}).get("search_term") or keyword
            result = search_chinatax(term, page=page, size=size, filters=ui_filters,
                                     file_only=False, order=ui_order)
            effective = "chinatax"
            where = "法规库" if source == "fgk" else "税务总局的文件类标签"
            result["_routed"] = (
                f"{tax_type_info['type']}的规则主体在境外辖区，{where}里没有对应文件，"
                f"已改查税务总局全站层（文件类标签之外）：{term}")
        elif source == "chinatax":
            result = search_chinatax(keyword, page=page, size=size, filters=ui_filters,
                                     order=ui_order)
        else:
            result = search_fgk(keyword, size=size, with_body=bool(data.get("body")),
                                filters=ui_filters, order=ui_order)
    else:
        # 归类出税种就按本体法名查，不要拿用户原话去标题检索。原话里
        # "费用"这类通用字会把《诉讼费用交纳办法》《国家赔偿费用管理条例》
        # 顶到首位（实测"研发费用加计扣除"就是这样），归类后走
        # parent_law 才能保证首条就是答这题要依据的那部法。
        if parent_law:
            keyword = parent_law
            search_type = 1
        # sta 专题（转让定价、税收优惠、税收协定等）在 NPC 库里检索无效，
        # 搜"转让定价"命中的是国有土地使用权出让和转让暂行条例。这类题
        # 改查总局法规库，用条目自带的 search_term 而不是原话。
        if authority == "sta":
            term = (tax_type_info or {}).get("search_term") or keyword
            # 这条也走 search5，同样吃界面那几个控件——否则用户在 NPC 源下设了日期、
            # 命中却被自动换到法规库时，日期又会被静默丢掉（与上一条同源的空转缺陷）。
            try:
                ui_filters = _ui_filters(scope, data.get("exact"), date_from, date_to,
                                         data.get("aging"))
            except ValueError as e:
                return jsonify({"error": f"筛选参数不合法：{e}"}), 400
            result = search_fgk(term, size=size, with_body=bool(data.get("body")),
                                filters=ui_filters,
                                order=SORT_TO_ORDER.get(sort, "relevance"))
            effective = "fgk"
            result["_routed"] = f"{tax_type_info['type']}属总局专题，已改查法规库：{term}"
        elif authority == "overseas":
            # 支柱二与全球最低税这一类不按 sta 那一路查：文件类标签下三个检索词
            # 各 0 条（实测见 `tax_search.OVERSEAS_REGIME_NOTE`），整轮是空跑；
            # 按 NPC 那一路查"支柱二对跨国企业有什么影响"，顶回来的是《企业破产法》
            # 《合伙企业法》——靠"企业"两个字命中的无关法律会占住主依据那一栏。
            # 这一路关掉文件类标签，取全站层。
            term = (tax_type_info or {}).get("search_term") or keyword
            try:
                ui_filters = _ui_filters(scope, data.get("exact"), date_from, date_to,
                                         data.get("aging"))
            except ValueError as e:
                return jsonify({"error": f"筛选参数不合法：{e}"}), 400
            result = search_chinatax(term, page=page, size=size, filters=ui_filters,
                                     file_only=False,
                                     order=SORT_TO_ORDER.get(sort, "relevance"))
            effective = "chinatax"
            result["_routed"] = (
                f"{tax_type_info['type']}的规则主体在境外辖区，已改查税务总局全站层"
                f"（文件类标签之外）：{term}")
        else:
            try:
                result = search_tax(
                    keyword, scope=scope, search_type=search_type,
                    status=status, date_from=date_from, date_to=date_to,
                    page=page, size=size, sort=sort,
                )
            except ValueError as e:
                # NPC 这一路不吃日期校验时，非法格式发给接口是被静默忽略还是
                # 被当成区间，从结果上看不出来。与 search5 两路同一处理，报 400。
                return jsonify({"error": f"筛选参数不合法：{e}"}), 400
            if parent_law:
                result["_routed"] = f"按{tax_type_info['type']}的本体法检索：{parent_law}"
            else:
                result["_routed"] = UNROUTED_NOTE

    if source != "aggregated":
        _stamp_source(result, effective)

    # 页码这一维不是四条分支都有：聚合按 size×3 截断、法规库一次扫若干页再截断，
    # 两路都没有 pageNum。这两路上用户点了"下一页"，清单照样回 200、条数照样是有的，
    # 不写这一句就读成"第 2 页取回的是这些"。判定放在后端而不是让前端看数据源：
    # 用户点的是 NPC，归类成总局专题时条目其实来自法规库那一层。
    if page > 1 and (source == "aggregated" or effective == "fgk"):
        result["_page_note"] = (
            f"第 {page} 页没有生效：多源聚合没有页码这一维，六源各按本轮条数取回再合并、"
            "按权威度排序后截断，这份清单仍是本轮取回的那一批；要一次多看就把「本轮条数」调大"
            if source == "aggregated" else
            f"第 {page} 页没有生效：税务法规库这一层没有页码，它是一次连扫若干页再按本轮条数"
            "截断，这份清单仍是本轮扫到的那一批；要按页翻请切到 NPC 或税务总局")
    result["_count_note"] = _count_note(result, source, page)

    # 会计口径缺口这句话由 tax_analyze 写成整句，前端只照抄——不在界面里自己
    # 判断准则能不能当依据（判据在 SKILL.md ③ 末）。按用户原话判，不看被改写后
    # 用来检索的 keyword。
    raw_keyword = data.get("keyword", "").strip()
    gap_note = accounting_note(accounting_gap(raw_keyword))
    # 点名的是草案/征求意见稿时同样只照抄：库里收的都是已公布文本，界面这一栏
    # 列出的同名文件是它的现行有效版本，不是草案内容（判据见 legislative_stage）。
    stage_note = legislative_note(legislative_stage(raw_keyword))
    # 境外辖区专题那句限制住在专题项上（tax_search.TAX_TYPE_KEYWORDS 的 note），
    # 前端只照抄：判据是"文件类标签下 0 条、全站层取回的是别国立法"，不让界面
    # 自己区分哪一类算境外规则。
    overseas_note = (tax_type_info or {}).get("note", "")

    return jsonify({
        "keyword": keyword,
        "user_keyword": data.get("keyword", "").strip(),
        # 这一轮真正生效的两个数由后端回，界面不拿自己 DOM 里的值充数：
        # 请求体里的 size 可能被取值域闸门拦下、page 可能在没有这一维的那两路被丢掉。
        "size": size,
        "page": page,
        "intent": intent,
        "province": province,
        "intent_label": INTENT_LABELS.get(intent, intent),
        "tax_type": tax_type_info["type"] if tax_type_info else None,
        "tax_type_aliases": tax_type_info["aliases"] if tax_type_info else [],
        "authority": (tax_type_info or {}).get("authority", "npc"),
        "accounting_note": gap_note,
        "legislative_note": stage_note,
        "overseas_note": overseas_note,
        "result": result,
    })


@app.route("/api/detail/<bbbs_id>", methods=["GET"])
def api_detail(bbbs_id):
    try:
        detail = fetch_detail(bbbs_id)
        return jsonify({"detail": detail})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/text/<bbbs_id>", methods=["GET"])
def api_text(bbbs_id):
    """Download and return the full text of a law."""
    try:
        detail = fetch_detail(bbbs_id)
        paragraphs = _download_and_extract(bbbs_id)

        # Classify paragraphs: headings vs articles vs body text
        heading_pattern = re.compile(r"^第[一二三四五六七八九十百千]+[章节条]|^目[\s　]*[录录]")
        article_pattern = re.compile(r"^第[一二三四五六七八九十百千\d]+条\s")
        chapter_pattern = re.compile(r"^第[一二三四五六七八九十百千]+章")

        sections = []
        current_chapter = ""
        for para in paragraphs:
            if chapter_pattern.match(para):
                current_chapter = para
                sections.append({"type": "chapter", "text": para})
            elif article_pattern.match(para):
                sections.append({"type": "article", "text": para, "chapter": current_chapter})
            elif heading_pattern.match(para):
                sections.append({"type": "heading", "text": para})
            else:
                sections.append({"type": "body", "text": para})

        return jsonify({
            "detail": detail,
            "total_paragraphs": len(paragraphs),
            "article_count": sum(1 for s in sections if s["type"] == "article"),
            "sections": sections,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai-interpret/<bbbs_id>", methods=["GET"])
def api_ai_interpret(bbbs_id):
    """AI 解读——本接口会花钱，花的是使用者自己账号里的额度，所以先过付费闸门。

    通道由 `tax_llm.channel()` 决定：闸门关着就返 503，错误文案里写清楚要开哪两个
    环境变量；闸门开了但上游没钱也返 503，不重试。若绕过 `tax_llm.channel()` 直接
    去 npm 全局目录找一个写死的命令名发起调用，谁打开网页点一下"AI 解读"就开始
    计费，界面上一个字都不提。
    """
    keyword = request.args.get("keyword", "")
    cmd, why = tax_llm.channel()
    if not cmd:
        return jsonify({"error": why, "code": "paid_llm_disabled",
                        "paid": True, "enabled": False}), 503
    try:
        detail = fetch_detail(bbbs_id)
        title = detail.get("title", "")
        if not title:
            return jsonify({"error": "Law not found"}), 404

        # Get law text sections
        paragraphs = _download_and_extract(bbbs_id)
        # Build a condensed version: first 20 paragraphs + article headings
        article_re = re.compile(r"^第[一二三四五六七八九十百千\d]+条\s")
        articles = [p for p in paragraphs if article_re.match(p)]
        # Take first 30 articles + all chapter headings
        chapter_re = re.compile(r"^第[一二三四五六七八九十百千]+章")
        chapters = [p for p in paragraphs if chapter_re.match(p)]
        condensed = chapters + articles[:30]
        law_text = "\n".join(condensed)

        if not law_text:
            return jsonify({"error": "Could not extract law text"}), 500

        prompt = f"""你是中国资深税务专家。请用通俗易懂的语言为以下法规条文撰写政策解读。

法规名称：{title}
用户关注的关键词：{keyword or "无特定关键词"}

法规条文：
{law_text}

请按以下格式输出 Markdown（每个部分必须包含）：

## 适用主体
[一句话说明谁适用这个法规]

## 核心要点
- 要点1
- 要点2
- 要点3

## {keyword + "相关条款" if keyword else "关键条款"}
[与关键词最相关的 2-3 个条款的通俗解读]

## 注意事项
- [注意] 注意点1
- [注意] 注意点2

要求：
- 语言通俗易懂，面向企业主和财务人员，避免法律术语
- 不要直接重复法条原文，用自己的话解释
- 如涉及税率、金额、日期等关键数据，请准确引用
- 总数控制在 500 字以内"""

        try:
            text = tax_llm.ask(prompt)
        except tax_llm.QuotaExhausted as e:
            # 额度耗尽不重试：在没钱的账号上每重试一次都可能继续计费。
            return jsonify({"error": f"所用账号额度已用完：{e}",
                            "code": "quota_exhausted",
                            "paid": True, "enabled": True}), 503
        except Exception as e:
            return jsonify({"error": str(e)[:300], "code": "model_error",
                            "paid": True, "enabled": True}), 502

        return jsonify({
            "law_id": bbbs_id,
            "law_title": title,
            "keyword": keyword,
            "interpretation": text,
            "model": f"外部模型 CLI（{cmd}）生成的解读",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "disclaimer": "AI 生成内容仅供参考，以官方政策文件和主管税务机关解释为准。",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/interpretations/<bbbs_id>", methods=["GET"])
def api_interpretations(bbbs_id):
    """Search official policy interpretations for a law."""
    keyword = request.args.get("keyword", "")
    province = request.args.get("province", "")
    try:
        detail = fetch_detail(bbbs_id)
        title = detail.get("title", "")
        if not title:
            return jsonify({"error": "Law not found"}), 404

        result = search_interpretations(title, keyword, province=province)
        result["law_id"] = bbbs_id
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Web-related search (broader, more practical) ──────────────────────────
def _search_web_broad(query: str, n: int = 8) -> tuple[list, str]:
    """Broader web search for practical tax-policy analysis.

    引擎是 360（m.so.com），返回 (结果, 拦截说明)。Bing 已移除：它对 site:
    查询返回空结果块；百度也移除：本主机几乎每次都被打成"百度安全验证"页。
    """
    found = so360_search(f"{query} 税收 政策解读", site="", size=n)
    if found.get("_error"):
        return [], found["_error"]

    results = []
    for item in found["results"]:
        href = item.get("url", "")
        title = item.get("title", "")
        if not href or len(title) < 8 or _is_garbage_result(href, title):
            continue
        domain = re.search(r"https?://(?:www\.)?([^/]+)", href)
        domain_label = domain.group(1) if domain else ""
        results.append({
            "title": title,
            "url": href,
            "date": "",
            "source": domain_label,
            "source_label": "官方来源" if ("chinatax" in href or "gov.cn" in href) else (
                _practice_source_name(domain_label, title) or "实务解读"
            ),
            "snippet": item.get("snippet", ""),
        })
    # 必须带着空说明返回：调用方按 `items, why = ...` 解包（见 api_web_related），
    # 只 `return results` 时解包按列表长度成败——1 条与 3 条都抛 ValueError 被
    # `except Exception` 吞掉，等于全网这一路永远交白卷；正好 2 条时两个名字各
    # 接住一个 dict，随后 `list(dict)` 拿到键名，接口回 500。
    return results, ""


@app.route("/api/web-related/<bbbs_id>", methods=["GET"])
def api_web_related(bbbs_id):
    """Search practice sources (公众号) + broader web for practical tax-policy analysis."""
    keyword = request.args.get("keyword", "")
    try:
        detail = fetch_detail(bbbs_id)
        title = detail.get("title", "")
        if not title:
            return jsonify({"error": "Law not found"}), 404

        short_title = title.replace("中华人民共和国", "").strip()
        search_query = f"{short_title} {keyword}" if keyword else short_title

        # ── Concurrent: practice sources + broad web ──
        all_sources = []
        seen = set()
        web_error = ""

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_practice = pool.submit(_search_practice_sources, search_query, 3)
            f_web = pool.submit(_search_web_broad, search_query, 8)
            try:
                web_items, web_error = f_web.result(timeout=20)
            except Exception:
                web_items = []
            try:
                practice_items = f_practice.result(timeout=20)
            except Exception:
                practice_items = []

        for item in list(practice_items) + list(web_items):
            if not item.get("url"):
                continue
            if item["url"] in seen or _is_garbage_result(item["url"], item.get("title", "")):
                continue
            seen.add(item["url"])
            all_sources.append(item)

        payload = {
            "law_id": bbbs_id,
            "law_title": title,
            "keyword": keyword,
            "total": len(all_sources),
            "sources": all_sources,
            "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        # 全网这一路被拦时说明原因，避免空列表被读成"网上没有相关解读"
        if web_error:
            payload["engine_error"] = web_error
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/quick-tax-types", methods=["GET"])
def api_quick_tax_types():
    """首页税种栏与引导面板第二步共用的清单，整份由归类词表渲出。

    这一份不在路由里手写第二遍：归类与自动换源读的是 `tax_search.TAX_TYPE_KEYWORDS`
    （现有 33 项），另立清单永远比词表少一截——全球最低税、税收协定、转让定价这类
    专题界面上点不到，命令行却能走到，读界面的人会以为引擎只认清单里那几个。
    键序就是界面上按钮的先后：前端按回来的顺序渲，只铺前 QUICK_TAX_SHOWN 枚，其余收进
    "展开"。词表因此按"税种段在前、横切专题在后"排——前 18 键正是现行十八个税种
    （含烟叶税），第 19 键起是专题；段长与那一枚数由
    `tests/test_server_routes.py::test_default_row_covers_all_statutory_tax_types` 钉住。
    挪键序对归类的风险有限但不为零：`resolve_tax_type` 比的是（别名长度、有无本体法、
    别名长度），键序只在这三项打平时决定谁被取——本次把烟叶税从词表末尾挪进税种段，
    359 道标注题的归类结果逐题相同。
    keyword 一律取键名本身：点下去发的是专题名，接下来按哪部本体法检索由
    `resolve_tax_type` 归类和 `api_search` 的换源规则决定，界面不替它预判。
    原先的 icon 字段一并去掉——前端只读 label 与 keyword，发出去没人读的字段
    只会变成第二份要人维护、且迟早和词表对不上的清单。
    发票这一类不在归类词表的键里（它是 tax_search.detect_intent 的五个意图之一），
    界面上的入口在引导面板第三步的"发票问题"。
    """
    return jsonify([{"label": t, "keyword": t} for t in TAX_TYPE_KEYWORDS])


@app.route("/api/calc", methods=["GET"])
def api_calc_skeletons():
    """算式展开面板的骨架清单，整份由 tax_calc.SKELETONS 渲出。

    参数名与形态一律走引擎（tax_calc.inputs_spec：名与先后取 inputs()，税率表这一枚
    取步骤串里那两个累进函数的末位实参与 RATE_TABLE_COLUMNS），不在这份路由里手写
    第二遍：手写清单只认得写它那天用到的参数，后来往骨架里加一步就多一个框，界面
    上会算出"缺少参数"却没有任何地方能填。
    税率表要在界面上排成一行一档的编辑表，列名只能由引擎给——前端不知道超率累进
    那一档记的是"速算扣除系数"而不是"速算扣除数"，猜错的那一列会被 row.get 当没填。
    结果名直接取骨架的 结果 键——那是这一路要展示哪几个数的唯一出处。
    """
    return jsonify({
        "骨架": [{"名称": name, "公式": sk["公式"],
                  "参数": tax_calc.inputs_spec(name), "结果": sk["结果"]}
                 for name, sk in tax_calc.SKELETONS.items()],
        "限制": tax_calc.DISCLAIMER,
    })


@app.route("/api/calc", methods=["POST"])
def api_calc_run():
    """把一条骨架按给定参数值展开成算式。纯本地求值：不联网、不调外部模型。

    这一路只补入口，不加判据：tax_calc.py 一直在命令行里，网页上没有地方进，
    看界面的人不知道 ⑥ 那一步的算式能带着自己检回来的数复算一遍。

    三道校验都排在调用 run 之前，因为 run 自身不拒绝多余的键：
    1. 骨架名。run 对不存在的名字抛 KeyError，落到通用分支就成了 500，而这纯粹
       是选错了一项，得回 400 并把可选值一起带回去。
    2. 参数名。run 把调用方给的键并进求值命名空间（values.update(given)），于是
       多给一个名为 max 的键就把算式允许调用的 max 换成一个 Decimal，随后
       「应纳税额 = max(0, 一般计税应纳税额)」那步报
       "'decimal.Decimal' object is not callable"——看着像引擎坏了，实际是请求里
       多带了一个字段。实测：增值税一般计税 + {"max": 99} 其余参数齐全 → TypeError。
    3. 取值形态。七种由参数写法决定的坏值，2026-10-20 逐条实测各抛哪一类：
       非数值、布尔、空表、末档封顶、档界与速算扣除不自洽各抛 ValueError，
       表行写成列表抛 AttributeError，表行少给 "税率" 这一枚键抛 KeyError
       （str(KeyError) 只剩一个带引号的键名，取回时补上"税率表少给一列"这句原话）。
       这些都由参数写法决定，一律 400 并把引擎原话带回，不折成 500。骨架名已在
       第 1 道校验拦下，这里的 KeyError 只会来自请求体里的表行。
    缺参数回 400 并给出整份名单：Missing.names 一次列全，不按步骤逐条挤，
    否则用户要试好几轮才知道少填了什么。
    """
    data = request.get_json(silent=True) or {}
    name = str(data.get("骨架") or "").strip()
    if name not in tax_calc.SKELETONS:
        return jsonify({"error": f"没有这个骨架：{name}",
                        "可选": sorted(tax_calc.SKELETONS)}), 400
    params = data.get("参数")
    if not isinstance(params, dict):
        return jsonify({"error": "参数必须是对象：{参数名: 值}，"
                                 "或带出处的 {参数名: {值: …, 来源: …}}"}), 400
    need = tax_calc.inputs(name)
    extra = [k for k in params if k not in need]
    if extra:
        return jsonify({"error": f"「{name}」没有这些参数：{'、'.join(extra)}",
                        "需要的参数": need}), 400
    try:
        return jsonify(tax_calc.run(name, params))
    except tax_calc.Missing as e:
        return jsonify({"error": f"缺少参数：{'、'.join(e.names)}",
                        "缺的参数": e.names, "需要的参数": need}), 400
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        # KeyError 的 str() 只剩一个带引号的键名（'税率'），对着界面那一栏读不出
        # 是哪里缺；这一枚只可能来自表行，所以补一句归属，其余原话照抄。
        why = (f"税率表少给一列：{e.args[0]}"
               if isinstance(e, KeyError) and e.args else str(e))
        return jsonify({"error": why, "需要的参数": need}), 400


@app.route("/api/health", methods=["GET"])
def api_health():
    """健康检查顺带报出付费闸门状态。

    前端要在点按钮之前就知道"AI 解读"这次会不会花钱、能不能用，所以把闸门
    说明放在这里；否则只能等用户点下去、拿到 503 才知道功能没开。
    """
    cmd, why = tax_llm.channel()
    return jsonify({"status": "ok", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "paid_llm": {"enabled": bool(cmd), "explanation": why}})


def _force_utf8_console():
    """让 Windows 控制台跟着 Python 走 UTF-8。

    本服务的输出固定用 UTF-8（见下面 __main__ 里的 reconfigure）。但 Windows
    控制台默认代码页是 936，UTF-8 字节按 936 解释就是乱码——所以先把控制台
    代码页也切成 65001。

    好处是 `python scripts\\tax_server.py` 在哪个终端里跑都能正常显示中文，
    启动脚本不必再套一层 `cmd /k "chcp 65001 && ..."`（那种嵌套引号在 bat 里
    既难写又难验）。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass                      # 没有真实控制台（管道/重定向）时忽略


if __name__ == "__main__":
    _force_utf8_console()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass                      # 容器里 stdout 是管道时可能不支持重配
    print("\n  [tax-analysis-skill] API Server")
    print(f"  监听 {BIND_HOST}:{PORT}")
    if BIND_HOST in ("127.0.0.1", "localhost"):
        print(f"  打开 http://localhost:{PORT}")
    else:
        print(f"  本机 http://127.0.0.1:{PORT}    局域网 http://<本机IP>:{PORT}")
        print("  注意：已监听所有网卡，同网段设备都能访问，且会用本机 IP 去抓外部站点")
    print("  POST /api/search")
    print("  GET  /api/text/<id>")
    print("  GET  /api/interpretations/<id>")
    print()
    app.run(host=BIND_HOST, port=PORT, debug=False)
