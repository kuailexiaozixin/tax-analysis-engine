#!/usr/bin/env python3
"""Flask API server for tax-policy-search frontend."""
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
from tax_detail import fetch_detail, get_download_url, SXX_MAP, _parse_docx_from_bytes
from tax_web_search import search_chinatax
from tax_fgk import search_fgk
from tax_so360 import so360_search
from tax_shui5 import search_shui5
from tax_wechat import search_wechat
from tax_formatter import format_search_response
from tax_aggregator import aggregate_search, DEFAULT_SOURCES

from flask import Flask, request, jsonify, send_from_directory
import requests as req
import urllib3
urllib3.disable_warnings()

app = Flask(__name__, static_folder=None)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

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


def _search_one_source(site: str, query: str, n: int = 5) -> list[dict]:
    """Search a specific site for policy interpretations, via 360 site: search.

    Bing was removed here: it returned zero result blocks for site: queries on
    www.bing.com, and on cn/m.bing.com it intermittently returned blocks whose
    content had nothing to do with the query (searching chinatax.gov.cn 企业所得税法
    yielded 元气壁纸 results). 360 returns real target-site links consistently.
    """
    found = so360_search(query, site=site, size=n)
    if found.get("_error"):
        return []

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
    return results


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
    """Practical-interpretation sources: tax.shui5.cn + WeChat public accounts.

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

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for site in sources:
            for q_text in official_queries[:3]:
                futures[(site, q_text)] = pool.submit(
                    _search_one_source, site, q_text, 4
                )

        for (site, q_text), future in futures.items():
            try:
                items = future.result(timeout=15)
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
    _interp_cache[cache_key] = result
    return result


def _download_and_extract(bbbs_id: str) -> list[str]:
    """Download DOCX from NPC and extract text paragraphs. Returns list of non-empty lines."""
    if bbbs_id in _text_cache:
        return _text_cache[bbbs_id]

    dl_url = get_download_url(bbbs_id, "docx")
    if not dl_url:
        return []

    resp = req.get(dl_url, verify=False, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://flk.npc.gov.cn/",
    })
    if resp.status_code != 200:
        return []

    paragraphs = _parse_docx_from_bytes(resp.content)
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
                                      scope=scope, sources=agg_sources)
            result["_routed"] = f"{tax_type_info['type']}属总局专题，已改查法规库：{agg_kw}"
        else:
            if parent_law:
                agg_kw = parent_law
            result = aggregate_search(agg_kw, size=size, status=status, scope=scope,
                                      exact=bool(parent_law))
            if parent_law:
                result["_routed"] = f"按{tax_type_info['type']}的本体法检索：{parent_law}"
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
        # sta 专题（转让定价、税收优惠、税收立法权等）在 NPC 库里检索无效，
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

    return jsonify({
        "keyword": keyword,
        "user_keyword": data.get("keyword", "").strip(),
        "intent": intent,
        "province": province,
        "intent_label": {
            "policy_lookup": "政策查询",
            "filing_guide": "申报指导",
            "risk_check": "合规风险",
            "eligibility": "资格判定",
            "invoice": "发票处理",
        }.get(intent, intent),
        "tax_type": tax_type_info["type"] if tax_type_info else None,
        "tax_type_aliases": tax_type_info["aliases"] if tax_type_info else [],
        "authority": (tax_type_info or {}).get("authority", "npc"),
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
    """AI-generated interpretation as fallback for a law."""
    keyword = request.args.get("keyword", "")
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

        import subprocess, tempfile
        # Write prompt to temp file, then use stdin redirect to claude
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          encoding="utf-8", delete=False) as pf:
            pf.write(prompt)
            prompt_file = pf.name

        npm_global = Path.home() / "AppData" / "Roaming" / "npm"
        claude_path = npm_global / "claude.cmd"
        if not claude_path.exists():
            claude_path = npm_global / "claude"

        try:
            result = subprocess.run(
                f'"{claude_path}" --print < "{prompt_file}"',
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=120,
                cwd=str(Path.home()),
                shell=True,
            )
        finally:
            os.unlink(prompt_file)

        if result.returncode != 0:
            return jsonify({"error": f"Claude error: {result.stderr[:200]}"}), 500

        return jsonify({
            "law_id": bbbs_id,
            "law_title": title,
            "keyword": keyword,
            "interpretation": result.stdout,
            "model": "Claude (AI 生成)",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "disclaimer": "AI 生成内容仅供参考，以官方政策文件和主管税务机关解释为准。",
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "AI generation timed out"}), 504
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
def _search_web_broad(query: str, n: int = 8) -> list[dict]:
    """Broader web search for practical tax-policy analysis.

    Engine is 360 (m.so.com). Bing was removed: it returned nothing for site:
    queries, and Baidu rate-limits this host to a 1,488-byte "百度安全验证"
    page on nearly every request.
    """
    found = so360_search(f"{query} 税收 政策解读", site="", size=n)
    if found.get("_error"):
        return []

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
    return results


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

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_practice = pool.submit(_search_practice_sources, search_query, 3)
            f_web = pool.submit(_search_web_broad, search_query, 8)

            for future in [f_practice, f_web]:
                try:
                    items = future.result(timeout=20)
                    for item in items:
                        if item.get("url"):
                            if item["url"] not in seen and not _is_garbage_result(item.get("url", ""), item.get("title", "")):
                                seen.add(item["url"])
                                all_sources.append(item)
                        elif isinstance(item, dict):
                            all_sources.append(item)
                except Exception:
                    pass

        return jsonify({
            "law_id": bbbs_id,
            "law_title": title,
            "keyword": keyword,
            "total": len(all_sources),
            "sources": all_sources,
            "searched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
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
    return jsonify({"status": "ok", "time": time.strftime("%Y-%m-%d %H:%M:%S")})


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print("\n  [tax-policy-search] API Server")
    print("  http://localhost:5080")
    print("  POST /api/search")
    print("  GET  /api/text/<id>")
    print("  GET  /api/interpretations/<id>")
    print()
    app.run(host="0.0.0.0", port=5080, debug=False)
