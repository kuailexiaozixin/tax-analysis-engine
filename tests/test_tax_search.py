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
                         TAX_TYPE_KEYWORDS)
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
    """税屋能直连读正文（WAF cookie 有效），不再必须经 Jina。"""
    print("\n[Test] shui5 direct body (WAF cookie)")
    from tax_shui5 import fetch_shui5
    url = "https://www.shui5.cn/article/42/70138.html"
    body = fetch_shui5(url)
    assert not body.get("_error"), f"直连失败：{body.get('_error')}"
    content = body.get("content", "")
    assert len(content) > 300, f"正文过短：{len(content)} 字符"
    assert body.get("title"), "应取出标题"
    assert body.get("date"), "应从 articleResource 取出日期"
    print(f"  [PASS] {body['title'][:36]}（{body['date']}），正文 {len(content)} 字符")
    return content


def test_shui5_read_article():
    """read_article 走直连优先、Jina 兜底，两条路都要能拿到正文。"""
    print("\n[Test] shui5 article body (direct first, Jina fallback)")
    from tax_shui5 import read_article
    url = "https://www.shui5.cn/article/90/40872.html"
    content, err = read_article(url)
    assert not err, f"Reading failed: {err}"
    body = content.split("Markdown Content:", 1)[-1]
    assert len(body) > 500, f"Body too short: {len(body)} chars"
    print(f"  [PASS] Body: {len(body)} chars")
    return body


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


def main():
    print("=" * 60)
    print("tax-policy-search: End-to-End Tests")
    print("=" * 60)

    tests = [
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
