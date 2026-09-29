#!/usr/bin/env python3
"""Flask API server for tax-analysis-engine frontend."""
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

from tax_search import search_tax, detect_intent, resolve_tax_type
from tax_analyze import accounting_gap, accounting_note
from tax_analyze import legislative_stage, legislative_note
from tax_detail import fetch_detail, download_bytes, SXX_MAP, _parse_docx_from_bytes
from tax_web_search import search_chinatax
from tax_fgk import search_fgk
from tax_so360 import so360_search
from tax_shui5 import search_shui5
from tax_wechat import search_wechat
from tax_aggregator import aggregate_search, DEFAULT_SOURCES
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
    # 原先这两步各自裸调 tax_http，闸外发请求，网页端连点几次就能和检索撞车。
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
    size = min(data.get("size", 20), 50)
    sort = data.get("sort", "relevance")
    source = data.get("source", "npc")
    # province 只回填给调用方，不参与本接口的检索：NPC 库是全国性法规，没有
    # 省级维度；地方口径要靠 /api/interpretations 的 province 参数换站点。
    # 前端"省份"下拉因此对搜索结果没有过滤作用，别让它看起来是生效的。
    province = data.get("province", "")

    intent = detect_intent(keyword)
    tax_type_info = resolve_tax_type(keyword)
    parent_law = (tax_type_info or {}).get("parent_law") or ""
    authority = (tax_type_info or {}).get("authority", "npc")

    if source == "aggregated":
        # 聚合同样要换源：不换就会把用户原话丢给五源，NPC 侧取回的是含通用字的
        # 无关法规，总局侧又翻不到该专题的规范性文件。sta 专题改用条目自带的
        # search_term 并剔掉 NPC，npc 专题改用 parent_law 精确检索。
        agg_kw = keyword
        agg_sources = None
        if authority == "sta":
            agg_kw = (tax_type_info or {}).get("search_term") or keyword
            agg_sources = [s for s in DEFAULT_SOURCES if s != "npc"]
            result = aggregate_search(agg_kw, size=size, status=status,
                                      scope=scope, sources=agg_sources, sort=sort)
            result["_routed"] = f"{tax_type_info['type']}属总局专题，已改查法规库：{agg_kw}"
        else:
            if parent_law:
                agg_kw = parent_law
            result = aggregate_search(agg_kw, size=size, status=status, scope=scope,
                                      exact=bool(parent_law), sort=sort)
            if parent_law:
                result["_routed"] = f"按{tax_type_info['type']}的本体法检索：{parent_law}"
            else:
                result["_routed"] = UNROUTED_NOTE
    elif source == "chinatax":
        result = search_chinatax(keyword, size=size)
    elif source == "fgk":
        result = search_fgk(keyword, size=size, with_body=bool(data.get("body")))
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
            result = search_fgk(term, size=size, with_body=bool(data.get("body")))
            result["_routed"] = f"{tax_type_info['type']}属总局专题，已改查法规库：{term}"
        else:
            result = search_tax(
                keyword, scope=scope, search_type=search_type,
                status=status, date_from=date_from, date_to=date_to,
                size=size, sort=sort,
            )
            if parent_law:
                result["_routed"] = f"按{tax_type_info['type']}的本体法检索：{parent_law}"
            else:
                result["_routed"] = UNROUTED_NOTE

    # 会计口径缺口这句话由 tax_analyze 写成整句，前端只照抄——不在界面里自己
    # 判断准则能不能当依据（判据在 SKILL.md ③ 末）。按用户原话判，不看被改写后
    # 用来检索的 keyword。
    raw_keyword = data.get("keyword", "").strip()
    gap_note = accounting_note(accounting_gap(raw_keyword))
    # 点名的是草案/征求意见稿时同样只照抄：库里收的都是已公布文本，界面这一栏
    # 列出的同名文件是它的现行有效版本，不是草案内容（判据见 legislative_stage）。
    stage_note = legislative_note(legislative_stage(raw_keyword))

    return jsonify({
        "keyword": keyword,
        "user_keyword": data.get("keyword", "").strip(),
        "intent": intent,
        "province": province,
        "intent_label": INTENT_LABELS.get(intent, intent),
        "tax_type": tax_type_info["type"] if tax_type_info else None,
        "tax_type_aliases": tax_type_info["aliases"] if tax_type_info else [],
        "authority": (tax_type_info or {}).get("authority", "npc"),
        "accounting_note": gap_note,
        "legislative_note": stage_note,
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

    旧写法在这里自己去 npm 全局目录找一个写死的命令名并直接发起调用：谁打开
    网页点一下"AI 解读"就开始计费，界面上一个字都不提。现在闸门关着就返 503，
    错误文案里写清楚开哪两个环境变量；闸门开了但上游没钱也返 503，不重试。
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
    return jsonify([
        {"label": "增值税", "icon": "📊", "keyword": "增值税"},
        {"label": "企业所得税", "icon": "🏢", "keyword": "企业所得税"},
        {"label": "个人所得税", "icon": "👤", "keyword": "个人所得税"},
        {"label": "消费税", "icon": "🛒", "keyword": "消费税"},
        {"label": "关税", "icon": "🚢", "keyword": "关税"},
        {"label": "房产税", "icon": "🏠", "keyword": "房产税"},
        {"label": "印花税", "icon": "📝", "keyword": "印花税"},
        {"label": "契税", "icon": "🔑", "keyword": "契税"},
        {"label": "土地增值税", "icon": "🏗️", "keyword": "土地增值税"},
        {"label": "税收优惠", "icon": "🎁", "keyword": "税收优惠"},
        {"label": "发票管理", "icon": "🧾", "keyword": "发票管理办法"},
        {"label": "税收征管", "icon": "⚖️", "keyword": "税收征收管理法"},
    ])


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
    print("\n  [tax-analysis-engine] API Server")
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
