#!/usr/bin/env python3
"""End-to-end tests for tax-policy-search skill."""
import json
import os
import sys
import time
from pathlib import Path

# Fix Windows console encoding
os.environ["PYTHONIOENCODING"] = "utf-8"

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from tax_search import (search_tax, resolve_tax_type, detect_intent,
                         TAX_TYPE_KEYWORDS, _is_challenge_page)
from tax_detail import fetch_detail
from tax_web_search import search_chinatax
from tax_formatter import format_search_response

PASS = "PASS"
FAIL = "FAIL"

def test_detect_intent():
    """Test intent classifier."""
    tests = [
        ("增值税税率多少", "policy_lookup"),
        ("怎么申报企业所得税汇算清缴", "filing_guide"),
        ("金税四期会查什么风险", "risk_check"),
        ("我公司能享受小微企业优惠吗", "eligibility"),
        ("增值税专用发票丢了怎么办", "invoice"),
        ("研发费用加计扣除比例", "policy_lookup"),
        ("会不会被税务稽查", "risk_check"),
    ]
    passed = 0
    for query, expected in tests:
        result = detect_intent(query)
        ok = result == expected
        status = PASS if ok else f"{FAIL} (expected {expected})"
        print(f"  [{status}] '{query}' -> {result}")
        if ok:
            passed += 1
    return passed, len(tests)


def test_resolve_tax_type():
    """Test tax type resolver."""
    tests = [
        ("增值税的一般税率是多少", "增值税"),
        ("企业所得税汇算清缴", "企业所得税"),
        ("个人所得税专项附加扣除怎么报", "个人所得税"),
        ("小微企业有什么税收优惠", "企业所得税"),
        ("房产税怎么计算", "房产税"),
        ("发票丢失怎么处理", "税收征管"),
    ]
    passed = 0
    for query, expected in tests:
        result = resolve_tax_type(query)
        ok = result and result["type"] == expected
        got = result['type'] if result else 'None'
        status = PASS if ok else f"{FAIL} (got {got}, expected {expected})"
        print(f"  [{status}] '{query}' -> {got}")
        if ok:
            passed += 1
    return passed, len(tests)


def test_search_title():
    """Test title search with NPC API."""
    print("\n[Test] Title search: VAT")
    result = search_tax("增值税", scope="title", status=3, size=5)
    assert result["total"] > 0, f"Expected >0 results, got {result['total']}"
    assert result["keyword"] == "增值税"
    assert result["scope"] == "title"
    assert result["searched_at"]
    assert len(result["results"]) > 0
    item = result["results"][0]
    assert "id" in item
    assert "title" in item
    assert "status_code" in item
    assert "status" in item
    assert "publish_date" in item
    print(f"  [PASS] Total: {result['total']}, searched_at: {result['searched_at']}")
    return result


def test_search_fulltext():
    """Test fulltext search."""
    print("\n[Test] Fulltext search: tax preference")
    result = search_tax("税收优惠", scope="fulltext", status=3, size=5)
    assert result["total"] > 0, "Expected >0 results"
    print(f"  [PASS] Total: {result['total']}")
    return result


def test_search_exact():
    """Test exact title search."""
    print("\n[Test] Exact search: VAT Law")
    result = search_tax("中华人民共和国增值税法", scope="title", search_type=1, status=3, size=5)
    assert result["total"] > 0, "Exact search should find VAT law"
    print(f"  [PASS] Found {result['total']} matches")
    return result


def test_search_date_range():
    """Test search with date range filter."""
    print("\n[Test] Date range: CIT 2024-2026")
    result = search_tax("企业所得税", scope="fulltext", status=3,
                        date_from="2024-01-01", date_to="2026-12-31", size=5)
    assert result["total"] > 0
    for item in result["results"]:
        if item["publish_date"]:
            assert item["publish_date"] >= "2024-01-01", f"Date out of range: {item['publish_date']}"
    print(f"  [PASS] Total in 2024-2026: {result['total']}")
    return result


def test_search_with_cache():
    """Test cache functionality."""
    print("\n[Test] Cache: two sequential searches")
    import tax_search
    from tax_search import _CacheManager

    # Clear any residual cache first
    _CacheManager(enabled=True).clear()
    tax_search._cache = _CacheManager(enabled=True)

    result1 = search_tax("契税", scope="title", status=3, size=3)
    assert not result1.get("_from_cache"), "First search should not be from cache"

    result2 = search_tax("契税", scope="title", status=3, size=3)
    assert result2.get("_from_cache"), "Second search should be from cache"

    tax_search._cache = _CacheManager(enabled=False)
    print(f"  [PASS] Cache works: first={result1['searched_at']}, second={result2['searched_at']}")
    return result2


def test_fetch_detail():
    """Test fetching law detail."""
    print("\n[Test] Fetch detail for a known law")
    result = search_tax("中华人民共和国增值税法", scope="title", search_type=1, status=3, size=1)
    assert len(result["results"]) > 0
    law_id = result["results"][0]["id"]

    detail = fetch_detail(law_id)
    assert detail["title"], "Detail should have a title"
    print(f"  [PASS] Detail: {detail['title'][:50]} | {detail.get('status', '?')}")
    return detail


def test_chinatax_search():
    """Test chinatax.gov.cn search via the search5 JSON interface."""
    print("\n[Test] chinatax.gov.cn search: 研发费用加计扣除")
    result = search_chinatax("研发费用加计扣除", size=5)
    assert not result.get("_error"), f"Chinatax search failed: {result.get('_error')}"
    # 之前只断言 key 存在，total=0 也算通过，属于假绿
    assert result["total"] > 0, "Chinatax should return hits for 研发费用加计扣除"
    assert len(result["results"]) > 0, "Chinatax should return parsed results"
    for item in result["results"]:
        assert item["url"].startswith("http"), f"Bad url: {item['url']}"
        assert item["title"], "Result must have a title"
    print(f"  [PASS] Total: {result['total']}, parsed: {len(result['results'])}")
    print(f"  [PASS] First: {result['results'][0]['title'][:60]}")
    return result


def test_fgk_search():
    """Test the STA regulation library (fgk.chinatax.gov.cn)."""
    print("\n[Test] fgk regulation library: 研发费用")
    from tax_fgk import search_fgk
    result = search_fgk("研发费用", size=4)
    assert result["total"] > 0, "fgk search should return regulation entries"
    for item in result["results"]:
        assert "fgk.chinatax.gov.cn" in item["url"], f"Not an fgk entry: {item['url']}"
    print(f"  [PASS] fgk entries: {result['total']}")
    print(f"  [PASS] First: {result['results'][0]['title'][:60]}")
    return result


def test_so360_search():
    """Test 360 site: search, which replaced the removed Bing channel."""
    print("\n[Test] 360 site search: chinatax.gov.cn")
    from tax_so360 import so360_search
    result = so360_search("研发费用加计扣除", site="chinatax.gov.cn", size=5)
    assert not result.get("_error"), f"360 search failed: {result.get('_error')}"
    assert result["total"] > 0, "360 should return chinatax links"
    for item in result["results"]:
        assert "chinatax.gov.cn" in item["url"], f"Off-site result: {item['url']}"
        assert item["title"], "Result must have a title"
    print(f"  [PASS] 360 hits: {result['total']}")
    print(f"  [PASS] First: {result['results'][0]['title'][:60]}")
    return result


def test_shui5_search():
    """Test shui5.cn (360 discovery + Jina Reader body)."""
    print("\n[Test] shui5.cn: 高新技术企业认定")
    from tax_shui5 import search_shui5
    result = search_shui5("高新技术企业认定", size=2)
    assert not result.get("_error"), f"shui5 search failed: {result.get('_error')}"
    assert result["total"] > 0, "shui5 should return article links"
    for item in result["results"]:
        assert "shui5.cn" in item["url"], f"Off-site result: {item['url']}"
    print(f"  [PASS] shui5 hits: {result['total']}")
    print(f"  [PASS] First: {result['results'][0]['title'][:60]}")
    return result


def test_fgk_body():
    """fgk 详情页能取正文——原先判断"JS 渲染取不到"是编码问题，已修。

    该站不声明 charset，requests 按 HTTP 头的 ISO-8859-1 解码会把中文变乱码，
    看起来像正文不在 HTML 里。显式按 UTF-8 解码即可，正文在 div.zscont/arc_cont。
    """
    print("\n[Test] fgk article body")
    from tax_fgk import fetch_fgk_body
    url = "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5207148/content.html"
    body = fetch_fgk_body(url)
    assert not body.get("_error"), f"取正文失败：{body.get('_error')}"
    content = body.get("content", "")
    assert len(content) > 1000, f"正文过短：{len(content)} 字符"
    assert "第一条" in content, "《增值税暂行条例实施细则》应含条文编号"
    assert body.get("title"), "应从 meta 取出标题"
    print(f"  [PASS] {body['title'][:36]}，正文 {len(content)} 字符，含条文编号")
    return content


def test_shui5_direct_body():
    """税屋取正文：成功就断言有正文，失败必须报清原因，不许静默返回空。

    纯 HTTP 直连过不了 WAF（实测 5/5 被拦），正文靠 tax_browser 让本机已装
    浏览器跑一次挑战后转交 HTTP 会话。两者取不到时报错要指名是 WAF，
    不能返回空 content 当成功——那会让上层以为有正文可用。
    """
    print("\n[Test] shui5 direct body (WAF challenge solved or reported)")
    from tax_shui5 import fetch_shui5
    url = "https://www.shui5.cn/article/42/70138.html"
    body = fetch_shui5(url)
    if body.get("_error"):
        assert "WAF" in body["_error"], f"报错应说明是 WAF 拦截：{body['_error']}"
        print(f"  [PASS] 被 WAF 拦截，已明确报错：{body['_error'][:40]}…")
        return 0
    content = body.get("content", "")
    assert len(content) > 300, f"正文过短：{len(content)} 字符"
    assert body.get("title"), "应取出标题"
    assert body.get("date"), "应从 articleResource 取出日期"
    print(f"  [PASS] {body['title'][:36]}（{body['date']}），正文 {len(content)} 字符")
    return 1


def test_shui5_read_article():
    """read_article 依次走浏览器过 WAF、纯 HTTP 直连、Jina 兜底。

    三条路都断时报错要同时说明直连与 Jina 两条路，便于判断是站点问题
    还是 Jina 自身问题。
    """
    print("\n[Test] shui5 article body (direct first, Jina fallback)")
    from tax_shui5 import read_article
    url = "https://www.shui5.cn/article/90/40872.html"
    content, err = read_article(url)
    if err:
        assert "直连失败" in err and "Jina" in err, \
            f"报错应同时说明直连与 Jina 两条路：{err}"
        print(f"  [PASS] 两条路都断，已报清：{err[:60]}…")
        return 0
    body = content.split("Markdown Content:", 1)[-1]
    assert len(body) > 500, f"Body too short: {len(body)} chars"
    print(f"  [PASS] Body: {len(body)} chars")
    return 1


def test_wechat_search():
    """Test WeChat public-account search through Sogou."""
    print("\n[Test] WeChat (Sogou): 研发费用加计扣除")
    from tax_wechat import search_wechat
    result = search_wechat("研发费用加计扣除", size=3)
    assert not result.get("_error"), f"WeChat search failed: {result.get('_error')}"
    assert result["total"] > 0, "WeChat search should return articles"
    for item in result["results"]:
        assert item.get("url", "").startswith("https://mp.weixin.qq.com/"), \
            f"Link not restored: {item.get('url')}"
    print(f"  [PASS] WeChat hits: {result['total']}")
    print(f"  [PASS] First: {result['results'][0]['title'][:60]}")
    return result


def test_wechat_read_article():
    """Test reading a WeChat article body."""
    print("\n[Test] WeChat article body")
    from tax_wechat import search_wechat, read_article
    found = search_wechat("研发费用加计扣除", size=1)
    assert found["total"] > 0, "Need at least one article to read"
    url = found["results"][0]["url"]
    content, err = read_article(url)
    assert not err, f"Reading failed: {err}"
    assert len(content) > 200, f"Body too short: {len(content)} chars"
    print(f"  [PASS] Body: {len(content)} chars")
    return content


def test_challenge_page_detection():
    """限流挑战页必须被识别出来，不能当 JSON 解析。

    NPC 触发限流时回 HTTP 200 + 一份 30~40 KB 的 HTML（Please enable
    JavaScript），不是 429。识别不了就直接 .json()，报出来的是
    "Expecting value: line 1 column 1"，看不出是限流还是代码坏了。
    """
    print("\n[Test] Challenge page detection")

    class FakeResp:
        def __init__(self, ctype, text):
            self.headers = {"Content-Type": ctype}
            self.text = text
            self.content = text.encode("utf-8")
            self.status_code = 200

    challenge = ("<!DOCTYPE HTML>\n<html>\n<head><meta charset=\"utf-8\">"
                 "</head>\n<body>\n<noscript>\n<h1><strong>Please enable "
                 "JavaScript to continue.</strong></h1>\n</noscript>\n"
                 "<script>var _0x=['a','b'];</script>\n</body></html>")
    assert _is_challenge_page(FakeResp("text/html", challenge)), \
        "限流挑战页应被识别"
    assert not _is_challenge_page(FakeResp("application/json", '{"total":45}')), \
        "正常 JSON 响应不应误判为挑战页"
    assert not _is_challenge_page(FakeResp("text/html", "<html><body>1</body></html>")), \
        "普通 HTML 不是挑战页"
    print("  [PASS] 挑战页识别正确，JSON 响应不误判")
    return 3


def test_fgk_paging():
    """总局检索接口把 pageSize 卡在 10 条，法规库条目排在后面的页上。

    只读第 1 页会把"库里没有"错报成"确实没有"：搜"转让定价"命中 173 条，
    第 1 页 10 条全是外国税改新闻，法规文件在第 2、3、5、6 页。
    """
    print("\n[Test] fgk paging reaches documents past page 1")
    from tax_fgk import search_fgk
    r = search_fgk("转让定价", size=3)
    assert r["pages_scanned"] >= 2, f"应至少翻 2 页，实际 {r['pages_scanned']}"
    assert r["total"] > 0, f"翻页后应取到法规文件，_error={r.get('_error')}"
    titles = [x["title"] for x in r["results"]]
    assert any("特别纳税调整" in t or "转让定价" in t for t in titles), \
        f"取到的应含转让定价实体文件，实际 {titles}"
    print(f"  [PASS] 翻 {r['pages_scanned']} 页取到 {r['total']} 条法规文件")
    return r


def test_sta_topics_reachable():
    """11 个 authority="sta" 的专题，每一个都要能取到依据。

    这些专题在 NPC 库里检索无效，检索词取自实测：键名不是检索词
    （"税收争议救济"当检索词 0 条命中），必须用表里的 search_term。
    """
    print("\n[Test] sta topics reachable via fgk")
    from tax_fgk import search_fgk
    sta = {k: v for k, v in TAX_TYPE_KEYWORDS.items()
           if v.get("authority") == "sta"}
    bad = []
    for key, info in sta.items():
        term = info.get("search_term")
        if not term:
            bad.append(f"{key}: 缺 search_term")
            continue
        r = search_fgk(term, size=3)
        if r["total"] == 0:
            bad.append(f"{key}: 检索词「{term}」取不到法规文件")
    assert not bad, "以下 sta 专题取不到依据：\n  " + "\n  ".join(bad)
    print(f"  [PASS] {len(sta)}/{len(sta)} 个 sta 专题均取到依据")
    return len(sta)


def test_npc_reliability_marker():
    """模糊全文检索标 medium（已排序但可能偏题），精确检索不标。"""
    print("\n[Test] NPC reliability marker")
    fuzzy = search_tax("研发费用 资本化", scope="fulltext", search_type=2, status=3, size=5)
    assert fuzzy.get("_reliability") == "medium", \
        f"fuzzy fulltext 应标 medium，实际 {fuzzy.get('_reliability')}"
    assert fuzzy.get("_reliability_note"), "标了等级必须同时给处置说明"
    single = search_tax("增值税", scope="fulltext", search_type=2, status=3, size=5)
    assert single.get("_reliability") == "medium"
    exact = search_tax("研发费用加计扣除", scope="fulltext", search_type=1, status=3, size=5)
    assert exact.get("_reliability") is None, "Exact search should not be marked"
    print("  [PASS] fuzzy fulltext marked medium, exact search unmarked")
    return fuzzy


def test_npc_fulltext_relevance():
    """正文检索加 sort=score 后必须按相关度返回，而不是按发文时间。

    这是本次修复的核心断言：加 sort 之前，"增值税" 首条是 1986 年的
    《外交特权与豁免条例》，因为默认按发文时间排。
    """
    print("\n[Test] NPC fulltext relevance ordering")
    cases = [
        ("增值税", "中华人民共和国增值税法"),
        ("虚开发票", "中华人民共和国发票管理办法"),
        ("加计扣除", "中华人民共和国企业所得税法"),
    ]
    for kw, expected in cases:
        r = search_tax(kw, scope="fulltext", search_type=2, status=3, size=5)
        assert r["results"], f"{kw} 无结果"
        top = r["results"][0]["title"]
        assert top == expected, f"{kw}: 期望首条 {expected}，实际 {top}"
    # 整段命中必须排在仅分词命中之前：《诉讼费用交纳办法》只命中"费用"
    r = search_tax("研发费用加计扣除", scope="fulltext", search_type=2, status=3, size=5)
    titles = [x["title"] for x in r["results"]]
    assert "中华人民共和国企业所得税法" in titles, \
        f"整段命中加计扣除的《企业所得税法》应进入前 5，实际 {titles}"
    assert titles.index("中华人民共和国企业所得税法") < \
        titles.index("诉讼费用交纳办法"), \
        f"整段命中应排在仅分词命中之前，实际 {titles}"
    print(f"  [PASS] {len(cases)} 个查询首条为对应法规，且整段命中优先于分词命中")
    return cases


def test_npc_title_ranking():
    """标题模糊检索须把本体法排到前面，而不是按发布时间。"""
    print("\n[Test] NPC title ranking")
    cases = [
        ("企业所得税", "中华人民共和国企业所得税法"),
        ("个人所得税", "中华人民共和国个人所得税法"),
        ("增值税", "中华人民共和国增值税法"),
        ("契税", "中华人民共和国契税法"),
    ]
    for kw, expected in cases:
        result = search_tax(kw, scope="title", search_type=2, status=3, size=20)
        assert result["results"], f"No results for {kw}"
        top = result["results"][0]["title"]
        assert top == expected, \
            f"{kw}: 期望首条 {expected}，实际 {top}"
    print(f"  [PASS] {len(cases)} 个税种首条均为本体法")
    return cases


def test_parent_law_authenticity():
    """每个 parent_law 必须真的存在于 NPC 且首条等于查询词。

    NPC 的精确检索不是严格匹配：不存在的名称也返回上千条，所以判据只能是
    首条标题等于查询词，不能用 total > 0。parent_law 写错一个字，重排就会
    把司法解释顶到本体法前面。
    """
    print("\n[Test] parent_law authenticity")
    bad = []
    n = 0
    for tax_type, info in TAX_TYPE_KEYWORDS.items():
        parent = info.get("parent_law")
        if not parent:
            continue
        n += 1
        r = search_tax(parent, scope="title", search_type=1, status=3, size=5)
        top = r["results"][0]["title"] if r["results"] else "<无结果>"
        if top != parent:
            bad.append(f"{tax_type}: 期望 {parent}，实际 {top}")
    assert not bad, "parent_law 与库中实际标题不符：\n  " + "\n  ".join(bad)
    print(f"  [PASS] {n}/{n} 个 parent_law 首条均等于查询词")
    return n


def test_ranking_size_insensitivity():
    """重排只在单页内做，所以先过取再排。size 变化不该改变首条是谁。"""
    print("\n[Test] Ranking size insensitivity")
    cases = ["企业所得税", "个人所得税", "增值税", "税收征管", "消费税"]
    sizes = [1, 3, 20]
    bad = []
    for kw in cases:
        tops = {}
        for size in sizes:
            r = search_tax(kw, scope="title", search_type=2, status=3, size=size)
            assert r["results"], f"{kw} size={size} 无结果"
            tops[size] = r["results"][0]["title"]
        if len(set(tops.values())) != 1:
            bad.append(f"{kw}: {tops}")
    assert not bad, "首条随 size 变化，说明重排没有在过取后的完整页面上做：\n  " \
        + "\n  ".join(bad)
    print(f"  [PASS] {len(cases)} 个查询在 size={sizes} 下首条一致")
    return cases


def test_formatter():
    """Test formatting search results."""
    print("\n[Test] Format search results to markdown")
    result = search_tax("增值税", scope="title", status=3, size=3)
    md = format_search_response(result, intent="policy_lookup")
    assert "增值税" in md
    assert "免责声明" in md
    assert "searched_at" in result
    assert len(md) > 200
    print(f"  [PASS] Output {len(md)} chars, contains disclaimer and timestamp")
    return md


def test_title_only_default():
    """Default scope must be title. NPC's body-text scope ignores the query, so
    no code path may fall back to it."""
    print("\n[Test] Title-only default")
    result = search_tax("增值税", status=3, size=5)
    assert result["scope"] == "title", f"expected title, got {result['scope']}"
    assert result["total"] > 0
    assert "_reliability" not in result
    print(f"  [PASS] scope=title, {result['total']} results, no reliability marker")
    return result


# ── 分析层：问题类型判定 ───────────────────────────────────────────
def test_analyze_question_types():
    """判型要能区分两类形态：集合填空与术语填空。

    这两类都含"（ ）"，但答案形式不同——集合要逐项列，术语要定位到条款。
    靠主题词分不开，只能靠句式。9 条全部对照人工判读结果。
    """
    print("[Test] question type classification")
    import tax_analyze as A
    cases = [
        ("转让定价方法包括（ ）。", "option_judge"),
        ("根据车船税法的规定，车船税的计税单位形式不包括（ ）。", "option_judge"),
        ("纳税人购买下列车辆时，需要缴纳车辆购置税的是（ ）。", "option_judge"),
        ("下列各项中，不属于印花税应税凭证的有（ ）。", "option_judge"),
        ("完税价格以（ ）作为计税依据。", "fill_blank"),
        ("税务机关有权（ ）。", "fill_blank"),
        ("外购商誉的支出，在（ ），准予在企业所得税税前扣除。", "fill_blank"),
        ("公司有500万研发费用，没有高新资质，能享受加计扣除吗", "entitlement"),
        ("增值税小规模纳税人月销售额10万，应纳增值税多少", "liability"),
    ]
    bad = []
    for q, want in cases:
        got = A.classify(q)["type"]
        if got != want:
            bad.append(f"{q[:26]} 判成 {got}，应为 {want}")
    assert not bad, "判型不符：\n  " + "\n  ".join(bad)
    print(f"  [PASS] {len(cases)} 条判型与人工判读一致")
    return 1


def test_analyze_context_axes():
    """前提轴要认得自然人称谓，否则会把已答完的问题报成缺失。"""
    print("[Test] context axis detection")
    import tax_analyze as A
    q = "白某于2020年7月购入某境内上市公司股票，2021年1月转让并分得红利4000元"
    gaps = A.detect_context_gaps(q)
    assert "entity" not in gaps["missing"],         f"白某是已确定的主体，不该报缺失：{gaps['missing']}"
    assert "time" in gaps["present"], "题面有年月，时点轴应已交代"

    bare = A.detect_context_gaps("研发费用加计扣除比例是多少")
    assert set(bare["missing"]) == {"time", "entity", "place", "scale"},         f"四根轴都该报缺失：{bare['missing']}"
    assert bare["probes"], "有缺失就该给出追问句"
    print("  [PASS] 主体识别正确，缺失轴与追问句均正常")
    return 1


# ── 分析层：依据定级 ───────────────────────────────────────────────
def test_evidence_rank():
    """效力位阶要分得开法律、行政法规、部门规章、规范性文件、解读。"""
    print("[Test] evidence authority ranking")
    import tax_evidence as E
    cases = [
        ("中华人民共和国企业所得税法", "law"),
        ("中华人民共和国增值税暂行条例", "admin_regulation"),
        ("中华人民共和国税收征收管理法实施细则", "department_rule"),
        ("国家税务总局公告2018年第28号", "normative"),
        ("国家税务总局关于进一步支持小型微利企业发展的税收政策的公告", "normative"),
        ("广东省地方税务局关于印花税若干事项问题的通知", "local_normative"),
        ("研发费用加计扣除政策执行指引", "technical"),
    ]
    bad = []
    for title, want in cases:
        got = E.rank_of(title)["rank"]
        if got != want:
            bad.append(f"{title[:30]} 判成 {got}，应为 {want}")
    assert not bad, "位阶判定不符：\n  " + "\n  ".join(bad)

    # NPC 的法律性质字段比按标题猜准，有它时优先用
    by_field = E.rank_of("某某规定", category="法律")
    assert by_field["rank"] == "law", f"应采信 category 字段：{by_field}"
    print(f"  [PASS] {len(cases)} 条位阶判定正确，且优先采信 NPC 分类字段")
    return 1


def test_evidence_validity_and_primary():
    """已废止的依据不能被选成主依据，哪怕它位阶更高。"""
    print("[Test] evidence validity and primary selection")
    import tax_evidence as E
    items = [
        {"title": "中华人民共和国企业所得税法", "category": "法律",
         "status": "有效", "status_code": 3},
        {"title": "国家税务总局公告2017年第40号", "status": "已废止",
         "status_code": 9},
        {"title": "研发费用加计扣除政策执行指引", "source": "shui5.cn"},
        {"title": "加计扣除政策的十个常见问题解答", "source": "shui5.cn"},
    ]
    graded = E.grade_all(items, at="2026-09-27")
    primary = E.pick_primary(graded)
    assert "企业所得税法" in primary["title"],         f"主依据应是现行有效的法律，选成了 {primary['title']}"

    repealed = [g for g in graded if g["validity"] == "repealed"]
    assert repealed, "已废止那条应被识别出来"
    assert "不能作为结论依据" in repealed[0]["citation_hint"],         "已废止依据的引用提示要写明不能支撑结论"
    # 解读类必须落进参考材料
    interp = [g for g in graded if g["rank"] == "interpretation"]
    assert interp and interp[0]["score"] < E.PRIMARY_THRESHOLD,         "解读文章不得达到可作主依据的分数"
    print("  [PASS] 时效与位阶合成正确，主依据未被废止或解读类占据")
    return 1


# ── 分析层：编排 ───────────────────────────────────────────────────
def test_answer_plan():
    """编排层要给不同的题不同的轮次，不能一律两轮 NPC。"""
    print("[Test] analysis orchestration plan")
    import tax_answer as AN
    judge = AN.build_plan("转让定价方法包括（ ）。")
    lookup = AN.build_plan("研发费用加计扣除比例是多少")
    assert judge["type"]["type"] == "option_judge"
    assert lookup["type"]["type"] == "lookup"
    assert len(judge["rounds"]) != len(lookup["rounds"]),         "不同题型应有不同轮次"

    # 本体法名已知时必须给精确检索词，不能把用户原话丢给 NPC
    terms = AN.search_terms("研发费用加计扣除比例是多少")
    assert terms["npc"] == "中华人民共和国企业所得税法",         f"npc 检索词应是本体法名，实际 {terms['npc']}"
    assert terms["npc"].startswith("中华人民共和国"),         "整句丢进标题检索会取回无关法规"
    # 解读源要用短词
    assert len(terms["shui5"]) < 15, f"解读源检索词过长：{terms['shui5']}"
    # sta 专题第 1 轮要改查总局：NPC 库里搜"转让定价"命中的是土地和矿产
    # 资源转让条例，先查 NPC 会整轮取回无关法规
    sta_r1 = AN.build_plan("关联申报表要准备什么资料？")["rounds"][0]
    npc_r1 = AN.build_plan("研发费用加计扣除比例是多少")["rounds"][0]
    assert sta_r1["sources"][0] == "fgk", f"sta 专题首轮应先查总局：{sta_r1['sources']}"
    assert npc_r1["sources"][0] == "npc", f"npc 专题首轮应查 NPC：{npc_r1['sources']}"
    print(f"  [PASS] 轮次随题型变化，检索词正确归类（npc={terms['npc']}），首轮按 authority 换源")
    return 1


def test_search_terms_route():
    """sta 专题要路由到法规库检索词，npc 专题要路由到本体法。"""
    print("[Test] search term routing by authority")
    import tax_answer as AN
    sta = AN.search_terms("转让定价方法包括哪些")
    assert sta["authority"] == "sta", f"转让定价应判为 sta：{sta}"
    assert not sta["parent_law"], "sta 专题本就没有本体法"

    npc = AN.search_terms("增值税小规模纳税人起征点")
    assert npc["authority"] == "npc", f"增值税应判为 npc：{npc}"
    assert npc["parent_law"] == "中华人民共和国增值税法"
    print("  [PASS] sta / npc 两类专题均路由正确")
    return 1


# ── 税屋正文链路 ───────────────────────────────────────────────────
def test_browser_detection():
    """必须复用本机已装浏览器，且探测不到时不能偷偷去装。"""
    print("[Test] installed browser detection")
    import tax_browser as B
    found = B.find_installed_browsers()
    assert found, "本机装有浏览器，应能探测到"
    for b in found:
        assert os.path.isfile(b["path"]), f"探测到的路径不存在：{b['path']}"
    names = [b["name"] for b in found]
    assert names[0] in ("edge", "chrome", "brave", "360se", "firefox"),         f"探测顺序应按过 WAF 成功率排，实际 {names}"
    print(f"  [PASS] 探测到 {[b['name'] for b in found]}，均为已装路径")
    return 1


def test_shui5_batch_read():
    """批量取正文：WAF 只过一次，多篇都要拿到内容。"""
    print("[Test] shui5 batch body read (one WAF pass)")
    import tax_shui5 as S5
    found = S5.search_shui5("研发费用加计扣除", size=3, read_body=False)
    assert found.get("results"), f"检索本身失败：{found.get('_error')}"
    urls = [r["url"] for r in found["results"]]
    rows = S5.read_articles(urls, read_interval=1.0)
    assert len(rows) == len(urls), "返回条数应与请求一致"
    ok = [r for r in rows if r.get("content") and not r.get("_error")]
    assert ok, "应有取到正文的：\n  " + "\n  ".join(
        str(r.get("_error")) for r in rows)
    print(f"  [PASS] {len(ok)}/{len(rows)} 篇取到正文，方式：{rows[0].get('_how','')[:40]}")
    return 1



def main():
    print("=" * 60)
    print("tax-policy-search: End-to-End Tests")
    print("=" * 60)

    tests = [
        ("Question Type Classification", test_analyze_question_types),
        ("Context Axis Detection", test_analyze_context_axes),
        ("Evidence Authority Ranking", test_evidence_rank),
        ("Evidence Validity and Primary", test_evidence_validity_and_primary),
        ("Analysis Orchestration Plan", test_answer_plan),
        ("Search Term Routing", test_search_terms_route),
        ("Installed Browser Detection", test_browser_detection),
        ("shui5 Batch Body Read", test_shui5_batch_read),
        ("Intent Detection", test_detect_intent),
        ("Tax Type Resolution", test_resolve_tax_type),
        ("Title Search (NPC API)", test_search_title),
        ("Fulltext Search (NPC API)", test_search_fulltext),
        ("Exact Search", test_search_exact),
        ("Date Range Filter", test_search_date_range),
        ("Cache", test_search_with_cache),
        ("Fetch Detail", test_fetch_detail),
        ("chinatax.gov.cn Search5", test_chinatax_search),
        ("fgk Regulation Library", test_fgk_search),
        ("fgk Article Body", test_fgk_body),
        ("360 Site Search", test_so360_search),
        ("shui5.cn Search", test_shui5_search),
        ("shui5.cn Article Body", test_shui5_read_article),
        ("shui5.cn Direct Body", test_shui5_direct_body),
        ("WeChat (Sogou) Search", test_wechat_search),
        ("WeChat Article Body", test_wechat_read_article),
        ("NPC Reliability Marker", test_npc_reliability_marker),
        ("Challenge Page Detection", test_challenge_page_detection),
        ("fgk Paging", test_fgk_paging),
        ("sta Topics Reachable", test_sta_topics_reachable),
        ("NPC Fulltext Relevance", test_npc_fulltext_relevance),
        ("NPC Title Ranking", test_npc_title_ranking),
        ("parent_law Authenticity", test_parent_law_authenticity),
        ("Ranking Size Insensitivity", test_ranking_size_insensitivity),
        ("Markdown Formatter", test_formatter),
        ("Title-Only Default", test_title_only_default),
    ]

    all_passed = 0
    all_total = 0

    for name, fn in tests:
        try:
            print(f"\n{'─' * 50}")
            print(f"> {name}")
            result = fn()
            if isinstance(result, tuple) and len(result) == 2:
                p, t = result
                all_passed += p
                all_total += t
            else:
                all_passed += 1
                all_total += 1
        except Exception as e:
            print(f"  [FAIL] {e}")
            all_total += 1
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"Results: {all_passed}/{all_total} passed")
    print(f"{'=' * 60}")

    return 0 if all_passed == all_total else 1


if __name__ == "__main__":
    sys.exit(main())
