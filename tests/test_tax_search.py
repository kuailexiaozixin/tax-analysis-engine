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


def test_search_sort_date():
    """sort=date 必须真的按发布时间降序——三条检索路径都要查。

    NPC 收到 orderByParam={order:-1,sort:gbrq} 并不按时间排（实测标题检索
    "增值税"回 2024-12-25、2011-01-08、1994-02-22、2025-12-25），所以排序在本地
    做。同时默认路径的首条仍必须是本体法，防止为了时间序把相关度排掉。
    """
    print("\n[Test] sort=date 在标题模糊 / 标题精确 / 正文三条路径都生效")
    cases = [
        ("增值税", "title", 2),
        ("税收优惠", "title", 2),
        ("增值税法", "title", 1),
        ("增值税", "fulltext", 2),
    ]
    passed = 0
    for kw, scope, st in cases:
        r = search_tax(kw, scope=scope, search_type=st, status=3, size=8, sort="date")
        dates = [it["publish_date"] or "" for it in r["results"]]
        assert dates, f"{kw}/{scope}/{st} 没取到条目，无法判排序"
        assert dates == sorted(dates, reverse=True), \
            f"{kw}/{scope}/{'exact' if st == 1 else 'fuzzy'} 未按时间降序：{dates}"
        passed += 1
        print(f"  [PASS] {kw}/{scope}/{'exact' if st == 1 else 'fuzzy'} → {dates[:3]}")
    # 默认排序仍要把本体法放首位
    r = search_tax("增值税", scope="title", search_type=2, status=3, size=8)
    assert r["results"][0]["title"] == "中华人民共和国增值税法", \
        f"默认相关度首位被换掉了：{r['results'][0]['title']}"
    print(f"  [PASS] 默认相关度首位仍是《{r['results'][0]['title']}》")
    return passed + 1, passed + 1


def test_search_with_cache():
    """Test cache functionality."""
    print("\n[Test] Cache: two sequential searches")
    import tax_search
    # 缓存的类名是 tax_cache.CacheManager，tax_search 只是把它转手导入；
    # 早先这里写的是已被删掉的 _CacheManager，用例直接 ImportError 挂掉。
    from tax_search import CacheManager

    # Clear any residual cache first
    CacheManager(enabled=True).clear()
    tax_search._cache = CacheManager(enabled=True)

    result1 = search_tax("契税", scope="title", status=3, size=3)
    assert not result1.get("_from_cache"), "First search should not be from cache"

    result2 = search_tax("契税", scope="title", status=3, size=3)
    assert result2.get("_from_cache"), "Second search should be from cache"

    tax_search._cache = CacheManager(enabled=False)
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


def test_so360_block_page():
    """离线：360 的"访问异常出错"页必须报成 _error，不能当成 0 条命中。

    这份页面是 HTTP 200 + 约 5KB，一张结果卡都没有。若不识别，
    /api/interpretations 会回空列表，前端据此写出"该法规可能暂无公开的政策
    解读文件"——把限流说成了法规属性。
    """
    import tax_so360
    saved = tax_so360.requests.get
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        text = ('<!doctype html><title>360</title>'
                '<div class="tip">访问异常出错</div>'
                '<script src="https://s5.ssl.qhres2.com/static/x.js"></script>')

    def fake_get(url, **kw):
        calls["n"] += 1
        return FakeResp()

    tax_so360.requests.get = fake_get
    try:
        r = tax_so360.so360_search("增值税法 政策解读", site="chinatax.gov.cn", size=5)
    finally:
        tax_so360.requests.get = saved

    assert r["total"] == 0 and not r["results"], "拦截页不该产出任何结果"
    assert r.get("_error"), "拦截页必须带 _error，否则上层无法与'真的没搜到'区分"
    assert "访问异常" in r["_error"], f"_error 要说明是被拦：{r.get('_error')}"
    print(f"  [PASS] 拦截页识别为 _error：{r['_error']}")
    print(f"  [PASS] 重试次数 {calls['n']}（<= MAX_RETRIES {tax_so360.MAX_RETRIES}）")
    assert calls["n"] <= tax_so360.MAX_RETRIES, "拦截页是 200，不该无限重试"
    return True


def test_interpretations_distinguish_blocked_from_empty():
    """离线：search_interpretations 在全站被拦时要给 engine_error，且不进缓存。"""
    import tax_server

    saved = tax_server._search_one_source
    saved_cache = dict(tax_server._interp_cache)
    tax_server._interp_cache.clear()

    def blocked(site, query, n=5):
        return [], "360 返回访问异常页（本机 IP 被限流），0 条不代表没有匹配结果"

    tax_server._search_one_source = blocked
    try:
        r = tax_server.search_interpretations("中华人民共和国增值税法", "增值税")
        cached_after_block = dict(tax_server._interp_cache)
    finally:
        tax_server._search_one_source = saved

    assert r["total"] == 0
    assert r.get("engine_error"), "被拦时必须给出原因"
    assert r["queries_blocked"].endswith("/8"), f"要给出被拦次数/总查询数：{r.get('queries_blocked')}"
    assert not cached_after_block, "被拦的空结果不能进缓存，否则限流恢复后仍一直回空"
    print(f"  [PASS] engine_error={r['engine_error']} / 未写缓存")

    # 搜索引擎正常时同样允许"真的没有解读"，此时才该给空态文案
    tax_server._interp_cache.clear()
    tax_server._search_one_source = lambda site, query, n=5: ([], "")
    try:
        r2 = tax_server.search_interpretations("中华人民共和国增值税法", "增值税")
    finally:
        tax_server._search_one_source = saved
        tax_server._interp_cache.clear()
        tax_server._interp_cache.update(saved_cache)
    assert not r2.get("engine_error"), "正常返回 0 条时不应误报被拦"
    assert r2.get("total") == 0
    print("  [PASS] 正常 0 条不带 engine_error")
    return True


def test_shui5_search():
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
    """所有 authority="sta" 的专题，每一个都要能取到依据。

    这些专题在 NPC 库里检索无效，检索词取自实测：键名不是检索词
    （"税收争议救济"当检索词 0 条命中），必须用表里的 search_term。
    数量随 ⑨ 的映射表变，所以用例自己从表里数，不写死条数。
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


def test_answer_scoring_strict():
    """答题判分按考试口径：多选只有集合完全相等才算对，漏选与错选都得算错。

    判分口径一松，正确率就静默虚高——F1 0.8 的多选题看着"基本答对"，真实阅卷
    给 0 分。所以这里同时钉两件事：exact 只在集合相等时为真；F1 与错选/漏选
    另记一档，不参与正确率。
    """
    print("[Test] answer scoring strictness")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_answer as X
    multi = {"answer": "AD", "answer_type": "multiple"}
    cases = [
        ({"answer": "D", "answer_type": "single"}, "D", True, "全对"),
        ({"answer": "D", "answer_type": "single"}, "B", False, "答错"),
        ({"answer": "D", "answer_type": "single"}, "", False, "拒答"),
        ({"answer": "AD", "answer_type": "multiple"}, "AD", True, "全对"),
        ({"answer": "AD", "answer_type": "multiple"}, "DA", True, "全对"),   # 顺序无关
        ({"answer": "AD", "answer_type": "multiple"}, "A", False, "漏选"),   # 答全一半仍算错
        ({"answer": "AD", "answer_type": "multiple"}, "ABCD", False, "错选"),
        ({"answer": "AD", "answer_type": "multiple"}, "ABC", False, "错选+漏选"),
        ({"answer": "AD", "answer_type": "multiple"}, "AB", False, "错选+漏选"),
        ({"answer": "AD", "answer_type": "multiple"}, "D", False, "漏选"),
        ({"answer": "AD", "answer_type": "multiple"}, "", False, "拒答"),
    ]
    for item, pred, want_exact, want_verdict in cases:
        r = X.score_one(item, {"answer": pred})
        assert r["exact"] == want_exact, f"{pred} 对 {item['answer']} 应 exact={want_exact}"
        assert r["verdict"] == want_verdict, f"{pred} 判成 {r['verdict']}，应为 {want_verdict}"
    # 单选题不写"漏选"：没有少选可言，套多选的分解会指错修法
    assert "漏" not in X.score_one({"answer": "D"}, {"answer": "B"})["verdict"]
    # F1 只作第二档：漏选给部分分，但 exact 必须仍为假
    half = X.score_one(multi, {"answer": "A"})
    assert half["f1"] > 0 and not half["exact"], "部分分不得渗进正确率"
    assert X.score_one(multi, {"answer": "AB"})["wrong"] == ["B"]
    assert X.score_one(multi, {"answer": "A"})["missing"] == ["D"]
    print(f"  [PASS] {len(cases)} 条判分与考试口径一致，部分分与正确率互不污染")
    return 1


def test_model_choice_parsing():
    """模型输出的解析要三级降级，且兜底档不能把废话数成选项。

    要求模型只回一个 JSON，但它会加围栏、加开场白、干脆用中文写"答案：ABD"。
    没有兜底就取不到答案，全表按拒答算，正确率被压低还看不出原因；
    兜底太宽又会把"ABC 三个选项都相关"这种句子读成选了 ABC。所以解析命中的
    级别必须写进 parse 字段，判分口径本身不变。
    """
    print("[Test] model output parsing")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_answer as X
    got = X.parse_choice('好的。\n```json\n{"answer":"ABD","basis":"契税法","reasoning":"x"}\n```')
    assert (got["answer"], got["parse"]) == ("ABD", "json"), got
    got = X.parse_choice('{"answer": "D", "basis": "b", "reasoning": "r"} 以上。')
    assert (got["answer"], got["parse"]) == ("D", "json"), got
    got = X.parse_choice('经比对各选项，答案：AC')
    assert (got["answer"], got["parse"]) == ("AC", "regex"), got
    got = X.parse_choice('')
    assert got["answer"] == "", "空输出必须是拒答，不能猜一个"
    got = X.parse_choice('这题涉及 A 与 C 两项，我倾向于都选')
    assert got["parse"] == "none" and got["answer"] == "", \
        f"没有作答格式时不得把散字母拼成选项：{got}"
    # 多个 JSON 时取最后一个带 answer 的：模型常先给草稿再给正式答案
    got = X.parse_choice('{"reasoning":"先看这条"} {"answer":"B","basis":"","reasoning":""}')
    assert got["answer"] == "B", got
    # 调用失败必须判成调用失败，不进分母：CLI 的英文告警里有散大写元音字母，
    # 早先的兜底解析把它们拼成了选项，blind 组正确率从满格假降到两成。
    got = X.parse_choice('{"_error": "调用失败（重试 3 次）：\\"deepseek\\" is not a model'
                         ' this version of Claude Code recognizes"}')
    assert got["parse"] == X.CALL_FAILED and got["answer"] == "", got
    got = X.parse_choice('"deepseek-v4-flash" is not a model this version of'
                         ' Claude Code recognizes, so auto-compact will keep A C E')
    assert got["parse"] == X.CALL_FAILED, f"裸告警文本必须判调用失败：{got}"
    r = X.score_one({"answer": "AD", "answer_type": "multiple"}, got)
    assert r["verdict"] == X.CALL_FAILED and r["call_failed"] and not r["exact"]
    assert X.agg([{"score": r, "source": "s", "validity": "ok",
                   "answer_type": "multiple"}])["call_failed"] == 1, \
        "调用失败要被单列，不能混进分母"
    # 额度信号绝不能被读成"模型答错了"。CLI 把 "API Error: 402 Insufficient
    # Balance" 打在标准输出、把无关的模型名告警打在标准错误，所以判额度要两边
    # 都看；判出来必须往上抛，让调用方停掉整批，而不是留一条失败记录继续烧钱。
    import types
    import tax_llm as L

    def stub_run(returncode, stdout, stderr):
        def fake(cmd, **kw):
            calls.append(cmd)
            return types.SimpleNamespace(returncode=returncode, stdout=stdout,
                                         stderr=stderr)
        return fake

    saved = (L.subprocess.run, os.environ.get(L.ENABLE_ENV),
             os.environ.get(L.CMD_ENV))
    calls = []
    os.environ[L.ENABLE_ENV] = "1"
    os.environ[L.CMD_ENV] = sys.executable    # 只要求文件存在，闸门不真跑它
    try:
        L.subprocess.run = stub_run(1, "API Error: 402 Insufficient Balance (id: x)\n",
                                    '"deepseek-v4-flash" is not a model '
                                    "this version recognizes。" * 30)
        try:
            X.ask_model({"question": "q", "options": {"A": "x"}}, "", "blind", 5)
            raise AssertionError("额度耗尽必须抛出：落成 _error 就等于让整批继续调")
        except L.QuotaExhausted as e:
            assert "402" in str(e), f"报错正文丢了状态码：{e}"

        # 本机 CLI 自身的故障没打到上游，允许按 retries 重问
        calls.clear()
        L.subprocess.run = stub_run(1, "", '"deepseek-v4-flash" is not a model')
        msg = json.loads(X.ask_model({"question": "q", "options": {"A": "x"}},
                                     "", "blind", 5, retries=2))["_error"]
        assert len(calls) == 2 and "is not a model" in msg, (len(calls), msg)

        # 除此之外一次都不重问：一次非零退出可能已经在计费的请求上发生过
        calls.clear()
        L.subprocess.run = stub_run(1, "连接被重置", "")
        msg = json.loads(X.ask_model({"question": "q", "options": {"A": "x"}},
                                     "", "blind", 5, retries=3))["_error"]
        assert len(calls) == 1, f"非本地故障不得重试，重试可能再扣一笔：{len(calls)}"
        assert "连接被重置" in msg, msg
    finally:
        L.subprocess.run = saved[0]
        for k, v in ((L.ENABLE_ENV, saved[1]), (L.CMD_ENV, saved[2])):
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
    p = X.parse_choice(json.dumps({"_error": "调用失败：is not a model"},
                                  ensure_ascii=False))
    assert p["parse"] == X.CALL_FAILED and not p["answer"], \
        f"报错正文不得被读成选项：{p}"
    print("  [PASS] JSON／正则／未答三档解析正确，调用失败单独成档不进分母，"
          "额度耗尽抛出停批、非本地故障不重问")
    return 1


def test_paid_llm_gate():
    """外部模型调用必须默认关死，而且只认环境变量、不自动找本机 CLI。

    这条是整条付费路径的地基：技能会被别人装走，只要"探测到 CLI 就直接调"，
    使用者点任何一个按钮都在花他自己账号的钱，而且事前一个字都看不到。
    三种回绝理由要各不相同才查得清——开关没开、开了没给命令、给了命令但路径
    不存在，是三种不同的现场。额度信号则要带词边界：请求 id 里的散数字一旦被
    认成"没钱了"，好端端的一批题会被无故停掉。
    """
    print("[Test] paid model call gate")
    import inspect
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tax_llm as L
    import eval_answer as X
    import tax_server
    saved = (os.environ.get(L.ENABLE_ENV), os.environ.get(L.CMD_ENV))
    for k in (L.ENABLE_ENV, L.CMD_ENV):
        os.environ.pop(k, None)
    try:
        cmd, why = L.channel()
        assert cmd == "" and L.ENABLE_ENV in why and L.CMD_ENV in why, why
        try:
            X.ask_model({"question": "q", "options": {"A": "x"}}, "", "blind", 5)
            raise AssertionError("闸门关着还能发起调用")
        except L.SpendRefused:
            pass
        os.environ[L.ENABLE_ENV] = "1"
        cmd, why = L.channel()
        assert cmd == "" and L.CMD_ENV in why, why
        os.environ[L.CMD_ENV] = str(Path(sys.executable).parent / "没有这个cli命令")
        cmd, why = L.channel()
        assert cmd == "" and "不存在" in why, why

        assert L.quota_text("API Error: 402 Insufficient Balance"), "状态码要认"
        assert L.quota_text("提示：账户余额不足，请充值"), "中文回显要认"
        assert L.quota_text("request_id=req_4027ab88") == "", "散数字不算额度信号"
        assert L.local_failure('"x" is not a model this version recognizes')
        assert not L.local_failure("API Error: 402 Insufficient Balance")

        # 花费预告按缓存命中数出来，所以命中判断只能有一份实现：
        # 两处各写一遍，迟早一处算钱、一处算调用。
        item = {"key": "k1", "question": "问", "options": {"A": "x"}}
        fp = X.cache_fingerprint(item, "")
        cache = {("k1", "blind"): {"fp": fp, "raw": "答案 A"}}
        assert X.cached_raw(item, "blind", None, cache) == "答案 A"
        assert X.cached_raw(dict(item, question="问改一个字"), "blind", None, cache) == "", \
            "题面变一个字就不许复用旧回答"
        notice = L.cost_notice(37, arms="evidence,blind")
        assert "37" in notice and "evidence,blind" in notice, notice

        # 分发面检查：源码里再出现"自己拼 npm 目录找 CLI"就说明闸门被绕过了
        for mod in (tax_server, X):
            src = inspect.getsource(mod)
            assert "AppData" not in src and "claude.cmd" not in src, \
                f"{mod.__name__} 又绕开闸门去找 CLI 了"
        # 网页按钮那条路必须真的接在闸门上，并且把状态报给前端：
        # 前端自己猜环境变量，猜错的方向是"以为能用了"。
        assert "tax_llm.channel()" in inspect.getsource(tax_server.api_ai_interpret)
        assert "paid_llm" in inspect.getsource(tax_server.api_health)
    finally:
        for k, v in ((L.ENABLE_ENV, saved[0]), (L.CMD_ENV, saved[1])):
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
    print("  [PASS] 默认关死、三种回绝理由分明、额度与本地故障分档、花费按命中数计")
    return 1


def test_answer_stratified_sampling():
    """分层抽样必须每格都取到题，否则分题库正确率是噪声。

    题库三家、时效三档共九格，题量差到 488:50。纯随机抽样会让 financeiq 吃掉
    大半样本，ideafin 与 fineval 各剩一两题，报出来的分题库正确率就没有含义。
    """
    print("[Test] stratified sampling covers every cell")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_answer as X
    n_id = 0
    synth = []
    for s, n in (("financeiq", 30), ("ideafin", 6), ("fineval", 2)):
        for v, m in (("ok", 5), ("review", 3), ("stale", 1)):
            for _ in range(n * m):
                n_id += 1
                synth.append({"key": f"k{n_id}", "id": f"k{n_id}", "source": s,
                              "validity": v, "subset": "", "question": "q",
                              "options": {"A": "x"}, "answer": "A",
                              "answer_type": "single"})
    picked = X.stratified(synth, 9, 7)
    cells = {(r["source"], r["validity"]) for r in picked}
    assert len(picked) == 9, f"应取满 9 题，实取 {len(picked)}"
    assert len(cells) == 9, f"九格应各出一题，实出 {len(cells)} 格：{sorted(cells)}"
    # 样本量超过格数时不得越界取题，也不得重复
    more = X.stratified(synth, 40, 7)
    keys = [r["key"] for r in more]
    assert len(keys) == len(set(keys)), "抽样出现重复题"
    print(f"  [PASS] 九格各出一题；扩样到 {len(more)} 题仍不重复")
    return 1


def test_basis_health_bucket():
    """主依据健康度要分得开"取对了法"与"根本没路由"。

    答对率饱和之后（模型本来就会做这批题），这一档是唯一还能区分技能好坏的量：
    把《国际刑事司法协助法》顶成"国际重复征税"的主依据不会让答案变错，
    但真实用户拿到的就是一份没用的依据清单。四档必须互斥且能覆盖缺字段的情况。
    """
    print("[Test] primary-authority health bucket")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_answer as X
    cases = [
        ({"evidence_n": 8, "parent_law": "中华人民共和国契税法",
          "primary_title": "中华人民共和国契税法"}, "on_topic"),
        ({"evidence_n": 8, "parent_law": "中华人民共和国契税法",
          "primary_title": "中华人民共和国契税法实施条例"}, "on_topic"),
        # 路由对了但取回无关法：这才是真正要修排序的档
        ({"evidence_n": 8, "parent_law": "中华人民共和国契税暂行条例",
          "primary_title": "中华人民共和国农业税法"}, "off_topic"),
        ({"evidence_n": 28, "parent_law": "",
          "primary_title": "中华人民共和国国际刑事司法协助法"}, "unrouted"),
        ({"evidence_n": 0, "parent_law": "", "primary_title": ""}, "no_evidence"),
        ({}, "no_evidence"),
    ]
    bad = []
    for diag, want in cases:
        got = X.basis_health(diag)
        if got != want:
            bad.append(f"{diag} 判成 {got}，应为 {want}")
    assert not bad, "\n  ".join(bad)
    assert X.basis_health(None) == "no_evidence", "缺字段的旧批次不能抛异常"
    print(f"  [PASS] {len(cases)} 种诊断字段组合各归一档，四档互斥")
    return 1


def row(key, arm, exact, pred, failed=False):
    return {"key": key, "arm": arm,
            "score": {"exact": exact, "pred": pred, "call_failed": failed}}


def test_paired_excludes_call_failures():
    """净贡献交叉表只配两组都真拿到回答的题，并把配不上的题数报出来。

    agg 已把调用失败剔出分母，但交叉表原先按"两组都有行"来配：一条失败的
    blind 行算成"blind 答错"，于是每道 evidence 答对的题都被记成依据救回来的。
    实测有一批 100 题的 blind 组 78 题调用失败，交叉表照样报 gain=70、harm=0，
    读起来像依据有决定性作用，真相是上游配额耗尽。所以失败行必须退出配对，
    配不上的题数报成 n_unpaired——不然覆盖缺口只会让配对数悄悄变小。
    """
    print("[Test] paired net contribution excludes call failures")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_answer as X
    # 1 题两组都真答对；1 题 evidence 对、blind 真答错（合法的 gain）；
    # 2 题 evidence 对、blind 调用失败（原先被误记为 gain）；1 题只有 blind 行
    rows = [
        row("q1", "evidence", True, "AB"), row("q1", "blind", True, "AB"),
        row("q2", "evidence", True, "AC"), row("q2", "blind", False, "A"),
        row("q3", "evidence", True, "B"), row("q3", "blind", False, "", True),
        row("q4", "evidence", True, "D"), row("q4", "blind", False, "", True),
        row("q5", "blind", True, "A"),
    ]
    p = X.paired(rows)
    assert p["n_both"] == 2, f"只有两组都真答过的 2 题可配，实得 {p['n_both']}"
    assert p["n_keys"] == 5 and p["n_unpaired"] == 3, \
        f"配不上的应为 3（2 题一组失败 + 1 题缺行），实得 {p['n_unpaired']}"
    assert p["gain"] == 1 and p["gain_keys"] == ["q2"], \
        f"合法的由错转对只该有 q2，实得 {p['gain']}/{p['gain_keys']}"
    assert p["harm"] == 0 and p["same_right"] == 1 and p["same_wrong"] == 0
    assert p["choice_changed"] == 1, "失败行不该算'选项被依据改变'"
    # 两组都失败的批次：净贡献必须为空，而不是把空答案当答错
    allbad = [row("z", "evidence", False, "", True), row("z", "blind", False, "", True)]
    q = X.paired(allbad)
    assert q["n_both"] == 0 and q["gain"] == 0 and q["n_unpaired"] == 1, \
        f"两组都失败时净贡献必须为空，实得 {q}"
    # 只跑一组时没有对照意图，不该报配对缺口
    solo = X.paired([row("s", "evidence", True, "A"), row("t", "evidence", False, "B")])
    assert solo["n_both"] == 0 and solo["n_unpaired"] == 0, \
        f"单组批次不该报缺口，实得 {solo}"
    print(f"  [PASS] 配对 {p['n_both']}/{p['n_keys']} 题、{p['n_unpaired']} 题退出，"
          f"gain 只认 q2；两组皆失败为空，单组批次不报缺口")
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


def test_aggregated_exact_flag():
    """聚合检索传了本体法名时必须走精确检索，否则宪法会顶掉本体法。"""
    print("[Test] aggregated search honors exact flag")
    import inspect
    from tax_aggregator import aggregate_search
    src = inspect.getsource(aggregate_search)
    assert "search_type=1 if exact else 2" in src, \
        "聚合检索未把 exact 传给 NPC 源"
    assert "exact: bool = False" in src, "aggregate_search 缺 exact 形参"
    print("  [PASS] exact 参数已透传到 NPC 源")
    return 1


def test_aggregated_sort_date():
    """离线：sort=date 时聚合结果跨源按公布日期降序，不再按权威度分层。

    默认那层排序把 NPC 整源顶在前面，用户勾了"只看最新"仍会拿到
    2024 年的法律排在 2026 年的总局公告之前——时间序必须真的生效。
    """
    print("[Test] aggregated sort=date crosses sources")
    import tax_aggregator as agg

    npc_item = {"id": "n1", "title": "中华人民共和国增值税法", "publish_date": "2024-12-25"}
    sta_item = {"id": "s1", "title": "国家税务总局公告2026年第1号", "date": "2026-01-30",
                "url": "https://www.chinatax.gov.cn/x1"}
    web_item = {"id": "w1", "title": "某省税务局关于增值税征管有关事项的通告",
                "date": "2026-09-03", "url": "https://x.chinatax.gov.cn/y"}

    stubs = {
        "search_tax": lambda *a, **k: {"results": [dict(npc_item)]},
        "search_chinatax": lambda *a, **k: {"results": [dict(sta_item)]},
        "so360_search": lambda *a, **k: {"results": [dict(web_item)]},
        "search_shui5": lambda *a, **k: {"results": []},
        "search_wechat": lambda *a, **k: {"results": []},
    }
    saved = {name: getattr(agg, name) for name in stubs}
    seen_kwargs = {}
    try:
        for name, fn in stubs.items():
            setattr(agg, name, fn)

        r = agg.aggregate_search("增值税", sort="date")
        dates = [it.get("publish_date") or "" for it in r["items"]]
        assert len(dates) == 3, f"三条源各一条，应回 3 条：{dates}"
        assert dates == sorted(dates, reverse=True), f"未按时间降序：{dates}"
        print(f"  [PASS] sort=date → {dates}")

        r2 = agg.aggregate_search("增值税")
        assert r2["items"][0]["_source"] == "npc", \
            f"默认排序仍应按权威度把 NPC 放首位：{r2['items'][0]['_source']}"
        print("  [PASS] 默认路径仍按权威度分层，NPC 居首")

        def spy(*a, **k):
            seen_kwargs.update(k)
            return {"results": [dict(npc_item)]}
        agg.search_tax = spy
        agg.aggregate_search("增值税", sort="date")
        assert seen_kwargs.get("sort") == "date", \
            f"sort 没传给 NPC 那一路，取回的窗口仍是相关度序：{seen_kwargs}"
        print("  [PASS] sort 已透传给 NPC 检索，窗口按时间取")
        return 3, 3
    finally:
        for name, fn in saved.items():
            setattr(agg, name, fn)


def test_server_routing_all_sources():
    """网页每条检索路径都要按 authority 换源，不能只改默认那条。"""
    print("[Test] server routes every source by authority")
    import inspect
    import tax_server
    src = inspect.getsource(tax_server.api_search)
    # 聚合路径也必须换源：sta 改查法规库并剔掉 NPC，npc 走 parent_law 精确检索
    assert 'if source == "aggregated":' in src
    assert "exact=bool(parent_law)" in src, \
        "聚合路径未按 parent_law 走精确检索"
    assert 's for s in DEFAULT_SOURCES if s != "npc"' in src, \
        "sta 专题在聚合路径未剔掉 NPC 源"
    # 每条分支都要写明改查了什么
    assert src.count("_routed") >= 4, \
        f"应有四处 _routed 提示，实际 {src.count('_routed')} 处"
    print("  [PASS] npc / fgk / chinatax / aggregated 四条路径均已换源")
    return 1


def main():
    print("=" * 60)
    print("tax-policy-search: End-to-End Tests")
    print("=" * 60)

    tests = [
        ("Question Type Classification", test_analyze_question_types),
        ("Context Axis Detection", test_analyze_context_axes),
        ("Answer Scoring Strictness", test_answer_scoring_strict),
        ("Model Choice Parsing", test_model_choice_parsing),
        ("Paid Model Call Gate", test_paid_llm_gate),
        ("Answer Stratified Sampling", test_answer_stratified_sampling),
        ("Basis Health Bucket", test_basis_health_bucket),
        ("Paired Net Contribution Guard", test_paired_excludes_call_failures),
        ("Evidence Authority Ranking", test_evidence_rank),
        ("Evidence Validity and Primary", test_evidence_validity_and_primary),
        ("Analysis Orchestration Plan", test_answer_plan),
        ("Search Term Routing", test_search_terms_route),
        ("Aggregated Exact Flag", test_aggregated_exact_flag),
        ("Aggregated Sort Date", test_aggregated_sort_date),
        ("Server Routing All Sources", test_server_routing_all_sources),
        ("Installed Browser Detection", test_browser_detection),
        ("shui5 Batch Body Read", test_shui5_batch_read),
        ("Intent Detection", test_detect_intent),
        ("Tax Type Resolution", test_resolve_tax_type),
        ("Title Search (NPC API)", test_search_title),
        ("Fulltext Search (NPC API)", test_search_fulltext),
        ("Exact Search", test_search_exact),
        ("Date Range Filter", test_search_date_range),
        ("Sort By Date", test_search_sort_date),
        ("Cache", test_search_with_cache),
        ("Fetch Detail", test_fetch_detail),
        ("chinatax.gov.cn Search5", test_chinatax_search),
        ("fgk Regulation Library", test_fgk_search),
        ("fgk Article Body", test_fgk_body),
        ("360 Site Search", test_so360_search),
        ("360 Block Page Detection", test_so360_block_page),
        ("Interpretations Blocked vs Empty", test_interpretations_distinguish_blocked_from_empty),
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
