#!/usr/bin/env python3
"""tax_fgk.py 的离线用例：翻页上限、正文档/视频条目、缓存边界。

与 test_tax_search.py 里的 test_fgk_* 分工不同：

  - 那边是**联网 e2e**，验的是"线上此刻真拿得到"；
  - 这边全部打桩，验的是**逻辑本身对不对**，不碰网络，可反复跑、结果稳定。

打桩点选在"网络出口"，不是被测函数内部：
  tax_fgk.search_chinatax  → 总局 search5 检索（清单层）
  tax_fgk.requests.get     → 详情页 HTML（正文层）
被测代码的翻页、去重、正文切分、缓存判断全部照常执行。

覆盖四件事：
  1. max_pages 是硬上限，且够 size 就提前收尾
  2. 文字正文取得出；视频/图片条目必须明确报错，不能返回空正文当成功
  3. 缓存里**绝不能**出现正文
  4. 命中缓存要有标记，且命中时不再打网络、缓存不被正文污染

所有缓存用例都把缓存目录指到临时目录，**不碰 ~/.cache/tax-analysis-engine**。
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import tax_fgk  # noqa: E402
from tax_fgk import (  # noqa: E402
    CACHE_TTL, MAX_PAGES, PAGE_SIZE, fetch_fgk_body, search_fgk,
)

PASS = "PASS"
FAIL = "FAIL"

# 含 FGK_MARKER 的法规库详情页 URL（下面有自检，防止 marker 变了用例失效）
FGK_URL = "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5207148/content.html"
NEWS_URL = "https://www.chinatax.gov.cn/chinatax/n810219/n810724/c1000/content.html"


# ── 打桩工具 ────────────────────────────────────────────────────────────────

class _Resp:
    """够 requests.get 用的最小响应对象（tax_fgk 只用 status_code 与 content）。"""

    def __init__(self, html: str, status_code: int = 200):
        self.status_code = status_code
        self.content = html.encode("utf-8")
        self.text = html
        self.headers = {"Content-Type": "text/html"}


def _detail_html(body_inner: str, note: str = "政策法规注释",
                 title: str = "测试法规", pub_date: str = "2026-01-02") -> str:
    """拼一份与真实详情页同构的最小 HTML。

    真实页面的结构：meta 里带 ArticleTitle/PubDate，正文在
    div.zscont（注释）与 div.arc_cont（正文）里。tax_fgk 按位置切这两块。
    """
    return (
        "<!DOCTYPE html><html><head>"
        f'<meta name="ArticleTitle" content="{title}">'
        f'<meta name="PubDate" content="{pub_date} 00:00:00">'
        "</head><body>"
        f'<div class="zscont">{note}</div>'
        f'<div class="arc_cont">{body_inner}</div>'
        "</body></html>"
    )


def _fgk_item(page: int, i: int) -> dict:
    """一条法规库条目（URL 含 FGK_MARKER，逐条唯一）。"""
    return {
        "title": f"关于印发测试办法的通知（P{page}-{i}）",
        "url": f"http://fgk.chinatax.gov.cn/zcfgk/c102416/c5{page:02d}{i:02d}/content.html",
        "date": "2026-01-01",
        "document_number": f"财税〔2026〕{page}{i}号",
        "publisher": "国家税务总局",
        "snippet": "测试用法规条目",
    }


def _news_item(page: int, i: int) -> dict:
    """一条非法规库的普通新闻（总局站里占多数，会排在前面）。"""
    return {
        "title": f"某国税改动向（P{page}-{i}）",
        "url": f"https://www.chinatax.gov.cn/chinatax/n810219/n810724/c{page}{i}/content.html",
        "date": "2026-01-01",
        "document_number": "",
        "publisher": "",
        "snippet": "税改新闻",
    }


def _make_fake_search(pages_fgk_count: dict, total_hits: int = 100):
    """造一个假的 search_chinatax。

    pages_fgk_count: {页码: 该页的法规库条目数}，其余位置补普通新闻。
    返回 (fake_fn, calls)，calls 记录被请求过的页码，用来断言翻页行为。
    """
    calls: list = []
    filters_seen: list = []

    def fake(keyword, page=1, size=PAGE_SIZE, filters=None):
        calls.append(page)
        filters_seen.append(filters)
        n_fgk = pages_fgk_count.get(page, 0)
        items = []
        for i in range(PAGE_SIZE):
            items.append(_fgk_item(page, i) if i < n_fgk else _news_item(page, i))
        return {"total": total_hits, "results": items}

    fake.filters_seen = filters_seen
    return fake, calls


def _resolve(root, dotted: str):
    """把 "requests.get" 拆成 (持有者, 属性名)；只写 "search_chinatax" 也行。"""
    parts = dotted.split(".")
    holder = root
    for p in parts[:-1]:
        holder = getattr(holder, p)
    return holder, parts[-1]


@contextlib.contextmanager
def _patched(**attrs):
    """临时替换属性（支持 "requests.get" 这类点路径），退出时原样恢复。

    注意 requests 是全局模块对象，patch "requests.get" 会短暂影响所有
    引用它的模块——try/finally 保证退出即还原，用例之间不会互相干扰。
    """
    saved = []
    for dotted, val in attrs.items():
        holder, name = _resolve(tax_fgk, dotted)
        saved.append((holder, name, getattr(holder, name)))
        setattr(holder, name, val)
    try:
        yield
    finally:
        for holder, name, old in saved:
            setattr(holder, name, old)


@contextlib.contextmanager
def _temp_cache():
    """把 tax_fgk 的缓存在临时目录里打开，不碰用户真实的 ~/.cache。"""
    with tempfile.TemporaryDirectory() as td:
        cm = tax_fgk.CacheManager(enabled=True)
        cm.dir = Path(td)          # dir 是普通实例属性，可直接改写
        with _patched(_cache=cm):
            yield Path(td)


# ── 用例 ────────────────────────────────────────────────────────────────────

def test_fgk_marker_selfcheck():
    """自检：用的 URL 必须能过 FGK_MARKER 判定，否则后续用例会假通过。"""
    assert tax_fgk.FGK_MARKER in FGK_URL, \
        f"FGK_MARKER={tax_fgk.FGK_MARKER!r} 匹配不上 FGK_URL，用例需同步更新"
    assert tax_fgk.FGK_MARKER not in NEWS_URL, "新闻 URL 不该被当成法规库条目"
    return True


def test_max_pages_respected():
    """max_pages 是硬上限；筛够 size 就提前收尾，不多翻一页。"""
    # ① 上限 1：法规库条目在第 5 页 → 1 页内筛不出，且只请求了第 1 页
    fake, calls = _make_fake_search({5: 10})
    with _patched(search_chinatax=fake):
        r = search_fgk("测试词", size=3, max_pages=1)
    assert calls == [1], f"max_pages=1 应只请求第 1 页，实际 {calls}"
    assert r["pages_scanned"] == 1, f"pages_scanned 应为 1，实际 {r['pages_scanned']}"
    assert r["total"] == 0, "第 1 页没有法规库条目，不该筛出结果"
    assert r.get("_error"), "筛不出必须报 _error——不能把'没翻到'说成'库里没有'"
    print(f"  [PASS] max_pages=1 只翻 1 页，筛不出时报错：{r['_error'][:32]}…")

    # ② 上限 3：法规库条目仍在第 5 页 → 请求 1/2/3 页后停
    fake, calls = _make_fake_search({5: 10})
    with _patched(search_chinatax=fake):
        r = search_fgk("测试词", size=3, max_pages=3)
    assert calls == [1, 2, 3], f"max_pages=3 应请求第 1/2/3 页，实际 {calls}"
    assert r["pages_scanned"] == 3
    assert r["total"] == 0
    print(f"  [PASS] max_pages=3 恰好翻 {r['pages_scanned']} 页")

    # ③ 提前收尾：第 1 页就够 size → 只翻 1 页，哪怕上限是 MAX_PAGES
    fake, calls = _make_fake_search({1: 10})
    with _patched(search_chinatax=fake):
        r = search_fgk("测试词", size=2, max_pages=MAX_PAGES)
    assert calls == [1], f"第 1 页已筛够 {2} 条，不该继续翻，实际 {calls}"
    assert r["total"] == 2, f"应取到 2 条，实际 {r['total']}"
    assert r["pages_scanned"] == 1
    print(f"  [PASS] 已筛够 size=2 即收尾，只翻 {r['pages_scanned']} 页")

    # ④ max_pages=0 / 负数要被兜成至少 1 页，不能一页都不翻
    fake, calls = _make_fake_search({1: 10})
    with _patched(search_chinatax=fake):
        r = search_fgk("测试词", size=2, max_pages=0)
    assert calls == [1], f"max_pages=0 应兜底成翻 1 页，实际 {calls}"
    print("  [PASS] max_pages=0 兜底为 1 页")
    return True


def test_adaptive_early_stop():
    """自适应收尾：连续 IDLE_PAGE_LIMIT 页无新条目就停；严格模式不漏。

    这个取舍要用例钉死两边：自适应用"少打请求"换"可能漏掉间隔很远的条目"，
    所以它必须真会早停（①），也必须默认能取到靠前的条目；确有远处条目时，
    显式 adaptive=False / --pages 必须能补救（②③）。另测"有进度的页会重置
    空闲计数"（④），否则中间只要有一个空页就会被误判成该收尾。
    """
    limit = tax_fgk.IDLE_PAGE_LIMIT

    # ① 全程没有法规库条目：翻 limit 页就停，绝不一路翻到 20 页
    fake, calls = _make_fake_search({})
    with _patched(search_chinatax=fake):
        r = search_fgk("测试词", size=5, max_pages=MAX_PAGES, adaptive=True)
    assert calls == list(range(1, limit + 1)), \
        f"应连续 {limit} 页无新条目后收尾，实际请求 {calls}"
    assert r["stopped_early"] is True, "应标 stopped_early=True"
    assert r["pages_scanned"] == limit, f"实际翻了 {r['pages_scanned']} 页"
    assert r["total"] == 0 and r.get("_error"), "一条没筛出时必须报错"
    assert "自适应收尾" in r["_error"], f"报错要说清是自适应收尾：{r['_error']}"
    print(f"  [PASS] 全程无条目：只翻 {r['pages_scanned']} 页即收尾（上限 {MAX_PAGES}）")

    # ② 条目在第 5 页、自适应开启：前 3 页空 → 收尾 → 取不到。
    #    这是自适应的已知代价，如实写进用例，别假装它能全能。
    fake, calls = _make_fake_search({5: 10})
    with _patched(search_chinatax=fake):
        r_ad = search_fgk("测试词", size=5, max_pages=MAX_PAGES, adaptive=True)
    assert r_ad["total"] == 0, "前 3 页空就该收尾，取不到第 5 页"
    assert r_ad["stopped_early"] is True
    assert "自适应收尾" in (r_ad.get("_error") or ""), "应说明是自适应收尾"
    print(f"  [PASS] 自适应确实会漏掉第 5 页的条目（只翻 {r_ad['pages_scanned']} 页）")

    # ③ 同一输入改严格模式：翻到第 5 页就能取满 size
    fake, calls = _make_fake_search({5: 10})
    with _patched(search_chinatax=fake):
        r_strict = search_fgk("测试词", size=5, max_pages=MAX_PAGES, adaptive=False)
    assert calls == [1, 2, 3, 4, 5], f"严格模式应翻到第 5 页取满即止，实际 {calls}"
    assert r_strict["pages_scanned"] == 5
    assert r_strict["total"] == 5, f"应取到 5 条，实际 {r_strict['total']}"
    assert r_strict["stopped_early"] is False, "严格模式不该标早停"
    print(f"  [PASS] 严格模式翻到第 {r_strict['pages_scanned']} 页，取满 {r_strict['total']} 条")

    # ④ 有进度的页会重置空闲计数：条目在第 2/4/6 页 → 第 6 页后
    #    再连续 limit 页无进度才收尾，即翻到 6+limit 页
    fake, calls = _make_fake_search({2: 10, 4: 10, 6: 10})
    with _patched(search_chinatax=fake):
        r4 = search_fgk("测试词", size=100, max_pages=MAX_PAGES, adaptive=True)
    assert r4["total"] == 30, f"三页各 10 条应共 30 条，实际 {r4['total']}"
    assert r4["pages_scanned"] == 6 + limit, \
        f"应翻到第 {6 + limit} 页（第 6 页后连续 {limit} 页无进度），实际 {r4['pages_scanned']}"
    assert r4["stopped_early"] is True
    print(f"  [PASS] 有进度的页重置计数，翻到第 {r4['pages_scanned']} 页取到 {r4['total']} 条")
    return True


def test_text_entry_ok():
    """正文是文字的条目，要正常取出标题、日期与条文，并带上注释。"""
    html = _detail_html("<p>第一条 为了规范测试行为，制定本办法。</p>"
                        "<p>第二条 本办法自公布之日起施行。</p>",
                        note="政策法规注释")
    with _patched(**{"requests.get": lambda url, **kw: _Resp(html)}):
        r = fetch_fgk_body(FGK_URL)

    assert not r.get("_error"), f"文字正文不该报错：{r.get('_error')}"
    assert r.get("title") == "测试法规", f"标题应从 meta 取出，实际 {r.get('title')!r}"
    assert r.get("pub_date") == "2026-01-02", f"日期应裁到 10 位，实际 {r.get('pub_date')!r}"
    content = r.get("content", "")
    assert "第一条" in content and "第二条" in content, f"正文缺失：{content!r}"
    assert "政策法规注释" in content, "zscont 里的注释也应带回来"
    print(f"  [PASS] 取到标题「{r['title']}」，正文 {len(content)} 字符，含注释")
    return True


def test_video_entry_reports_error():
    """正文是视频/图片的条目必须明确报错，不能返回空正文当成功。

    税法小课堂这类栏目正文是视频：容器取得到，但里面没有文字。若当成功返回，
    上层会以为拿到了正文，把"没有条文可引"错报成"该法规没内容"。
    """
    html = _detail_html('<video src="https://x.example/v.mp4"></video>',
                        note="税法小课堂")
    with _patched(**{"requests.get": lambda url, **kw: _Resp(html)}):
        r = fetch_fgk_body(FGK_URL)

    assert r.get("_error"), "视频条目必须带 _error"
    assert "视频" in r["_error"] and "图片" in r["_error"], \
        f"报错要指明是视频/图片：{r['_error']}"
    assert not r.get("content"), f"视频条目不该给出正文，实际 {r.get('content')!r}"
    print(f"  [PASS] 视频条目明确报错：{r['_error']}")

    # 图片条目同理
    html_img = _detail_html('<img src="https://x.example/a.png">')
    with _patched(**{"requests.get": lambda url, **kw: _Resp(html_img)}):
        r2 = fetch_fgk_body(FGK_URL)
    assert r2.get("_error") and "视频" in r2["_error"], \
        f"图片条目同样要报错：{r2.get('_error')}"
    print(f"  [PASS] 图片条目明确报错：{r2['_error']}")

    # 取不到正文容器时，报错要说清是页面结构问题，不是"没搜到"
    html_blank = "<html><body><div class=\"arc_cont\"></div></body></html>"
    with _patched(**{"requests.get": lambda url, **kw: _Resp(html_blank)}):
        r3 = fetch_fgk_body(FGK_URL)
    assert r3.get("_error"), "空正文容器必须报错"
    print(f"  [PASS] 空容器报错：{r3['_error']}")
    return True


def test_media_only_marked_in_results():
    """with_body 时，视频/图片条目要在结果里显式标 media_only。

    目的：把"本来就没有文字"和"取失败了"分开。前者重试无用，后者重试可能
    成功——上层若只看 body_error，会把两种情况混成一种，白白重试或误判为故障。
    """
    fake, _ = _make_fake_search({1: 2})
    text_url = _fgk_item(1, 0)["url"]
    video_url = _fgk_item(1, 1)["url"]

    def fake_get(url, **kw):
        if url == video_url:
            return _Resp(_detail_html('<video src="https://x.example/v.mp4"></video>',
                                      note="税法小课堂"))
        return _Resp(_detail_html("<p>第一条 测验正文。</p>"))

    with _temp_cache():
        with _patched(search_chinatax=fake, **{"requests.get": fake_get}):
            r = search_fgk("测试词", size=2, with_body=True)

    by_url = {e["url"]: e for e in r["results"]}
    assert len(by_url) == 2, f"应取到 2 条，实际 {len(by_url)}"

    text_entry = by_url[text_url]
    assert text_entry.get("body"), "文字条目应有正文"
    assert "media_only" not in text_entry, "文字条目不该带 media_only"
    assert "body_error" not in text_entry, "文字条目不该带 body_error"

    video_entry = by_url[video_url]
    assert video_entry.get("media_only") is True, "视频条目必须标 media_only=True"
    assert not video_entry.get("body"), "视频条目本来就没有正文"
    assert video_entry.get("body_error"), "仍要保留 body_error 说明原因"
    print("  [PASS] 视频条目带 media_only=True（与取失败区分），文字条目不带")
    return True


def test_cache_excludes_body():
    """缓存文件里绝不能出现正文——缓存旧条文会把已废止条文当现行有效引用。"""
    fake, _ = _make_fake_search({1: 3})
    html = _detail_html("<p>第一条 这是会被缓存的正文，绝不该落盘。</p>")

    with _temp_cache() as cache_dir:
        with _patched(search_chinatax=fake,
                      **{"requests.get": lambda url, **kw: _Resp(html)}):
            r = search_fgk("测试词", size=3, with_body=True)

        # 结果里有正文……
        assert r["total"] == 3, f"应取到 3 条，实际 {r['total']}"
        assert r["results"][0].get("body"), "with_body=True 应把正文带回来"

        # ……但缓存文件里一个正文字符都不能有
        files = list(cache_dir.glob("*.json"))
        assert len(files) == 1, f"应只落一份清单缓存，实际 {len(files)} 个"
        raw = files[0].read_text(encoding="utf-8")
        assert "第一条" not in raw, "缓存文件里出现了正文内容——正文被缓存了！"
        assert "body" not in raw, \
            f"缓存文件里出现了 body 字段（正文或草稿被缓存）：{raw[:200]}"

        payload = json.loads(raw)["payload"]
        assert payload["results"], "缓存里应保留清单条目"
        for entry in payload["results"]:
            bad = [k for k in entry if k.startswith("body")]
            assert not bad, f"缓存条目含正文字段 {bad}：{list(entry)}"
        print(f"  [PASS] 缓存 {files[0].name} 只含清单（{len(payload['results'])} 条），"
              f"正文 {len(r['results'][0]['body'])} 字符未落盘")
        return True


def test_cache_hit_marker():
    """第二次查同一关键词要标 _from_cache，且不再打网络、缓存不被正文污染。"""
    fake, calls = _make_fake_search({1: 5})
    html = _detail_html("<p>第一条 正文。</p>")
    get_calls = {"n": 0}

    def fake_get(url, **kw):
        get_calls["n"] += 1
        return _Resp(html)

    with _temp_cache() as cache_dir:
        with _patched(search_chinatax=fake, **{"requests.get": fake_get}):
            r1 = search_fgk("测试词", size=2)
            assert r1["_from_cache"] is False, "第一次不该标命中"
            n_first = len(calls)

            r2 = search_fgk("测试词", size=2)
            assert r2["_from_cache"] is True, "第二次应标 _from_cache"
            assert isinstance(r2.get("_cache_age_s"), float), \
                f"命中应带缓存年龄，实际 {r2.get('_cache_age_s')!r}"
            assert len(calls) == n_first, \
                f"命中缓存不该再打网络，检索页请求从 {n_first} 变成 {len(calls)}"
            print(f"  [PASS] 第二次命中缓存（{r2['_cache_age_s']}s 前），未再打网络")

            # 命中后再带正文：缓存的清单不能被写进 body（deepcopy 是否生效）
            r3 = search_fgk("测试词", size=2, with_body=True)
            assert r3["_from_cache"] is True, "第三次仍应命中缓存"
            assert r3["results"][0].get("body"), "带正文时结果里应有 body"
            assert "第一条" in r3["results"][0]["body"]

            r4 = search_fgk("测试词", size=2)
            assert "body" not in r4["results"][0], \
                "deepcopy 没生效：取正文时把 body 写进了缓存的清单"
            print("  [PASS] 取正文未污染缓存清单（deepcopy 生效）")

            # 缓存文件本身也不该因为取过正文而变化
            raw = list(cache_dir.glob("*.json"))[0].read_text(encoding="utf-8")
            assert "第一条" not in raw, "缓存文件被正文污染"
        assert get_calls["n"] > 0, "第三次取正文应真的打了详情页"
        print(f"  [PASS] 详情页现拉 {get_calls['n']} 次，正文不进缓存")
    return True


def test_cli_cache_tag():
    """CLI 命中缓存要打 [清单缓存 Ns 前]，便于人工识别结果是不是刚查的。

    同时确认 CLI 走 --cache 时用的是可注入目录的缓存实例（测试把
    CacheManager 换成落在临时目录的子类），不会写到用户真实缓存里。
    """
    fake, _ = _make_fake_search({1: 3})
    saved_argv = sys.argv

    with tempfile.TemporaryDirectory() as td:
        cache_dir = Path(td)

        class _FixedCache(tax_fgk.CacheManager):
            """把缓存目录钉在临时目录上，避免 CLI 写进用户真实缓存。"""

            def __init__(self, enabled=False, namespace="default"):
                super().__init__(enabled, namespace)
                self.dir = cache_dir

        with _patched(search_chinatax=fake, CacheManager=_FixedCache):
            sys.argv = ["tax_fgk.py", "测试词", "--size", "2", "--cache"]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    tax_fgk.main()   # 第一次：写缓存
                    tax_fgk.main()   # 第二次：命中
            finally:
                sys.argv = saved_argv

        out = buf.getvalue()
        assert "[清单缓存" in out, f"命中缓存应打 [清单缓存]，实际输出：{out[:300]!r}"
        files = list(cache_dir.glob("*.json"))
        assert files, "CLI --cache 应把缓存写进注入的目录"
        print("  [PASS] CLI 命中打出 [清单缓存 …s 前]，缓存落在注入目录")
    return True


# ── 收窄维度（filters） ──────────────────────────────────────────────────────

def test_scan_list_threads_filters_to_every_page():
    """_scan_list 收到的 filters 必须原样带到每一页的 search_chinatax 调用，并回显。

    对应的坑：维度若只在第一页发、后面几页漏掉，取回的就是"首页过滤、后页未过滤"
    的混合清单——这种错法条数照常、结构照常，只有逐页核对 filters 才能发现。
    """
    fake, calls = _make_fake_search({1: 2, 2: 2})
    filters = {"docType": "财税", "docYear": "2018"}
    with _patched(search_chinatax=fake):
        r = tax_fgk._scan_list("测试词", size=4, max_pages=2, adaptive=False,
                               filters=filters)
    assert all(fs == filters for fs in fake.filters_seen), fake.filters_seen
    assert r.get("filters") == filters, r
    print(f"  [PASS] {len(fake.filters_seen)} 页请求都带上同一组 filters 并回显")


def test_search_fgk_caches_each_filter_set_apart():
    """不同 filters 不能共用同一份清单缓存，否则换个维度却读回旧结果。

    None 与 {} 视作同一种"无过滤"，共用缓存；换了维度必须重取。
    """
    fake, _ = _make_fake_search({1: 10})
    with _temp_cache():
        with _patched(search_chinatax=fake):
            r1 = search_fgk("测试词", size=2, max_pages=3, filters={"docYear": "2018"})
            assert r1["_from_cache"] is False, "第一次带维度不该命中"
            r2 = search_fgk("测试词", size=2, max_pages=3, filters={"docYear": "2018"})
            assert r2["_from_cache"] is True, "同维度第二次应命中缓存"
            r3 = search_fgk("测试词", size=2, max_pages=3, filters={"docYear": "2019"})
            assert r3["_from_cache"] is False, "换年份是另一份缓存，不该复用 2018 的"
            r4 = search_fgk("测试词", size=2, max_pages=3)   # 无维度
            assert r4["_from_cache"] is False, "无维度与任何带维度都不同键"
    print("  [PASS] 缓存按 filters 分键：同维度命中、换维度/无维度各自重取")


def test_filters_produce_too_narrow_instead_of_libraries_empty():
    """带维度翻不出法规库条目时，报「维度可能拼窄」而不是「库里没有这份文件」。"""
    saved_scan = tax_fgk.search_chinatax
    empty_with_note = {"total": 0, "results": [], "filters": {"docYear": "2018"},
                       "_filter_note": "命中 0 条，分不清拼窄还是没有"}
    try:
        tax_fgk.search_chinatax = lambda keyword, page=1, size=10, filters=None: \
            empty_with_note
        r = tax_fgk._scan_list("测试词", size=5, max_pages=2, filters={"docYear": "2018"})
    finally:
        tax_fgk.search_chinatax = saved_scan
    assert "分不清" in r.get("_filter_note", ""), r
    assert "未筛出法规库条目" not in r.get("_error", ""), r["_error"]
    print("  [PASS] 带维度 0 条透出「可能拼窄」，不写成翻完N页未筛出")


# ── 关联文件查询（queryManuscriptAssociation） ──────────────────────────────

# 2026-10-01 本机对 c5247431 实测的返回形态（results[0] 是文章本体，[1] 才是关联组）
_ASSOC_PAYLOAD = {
    "results": {"data": {"results": [
        {"channel": [{"channelName": "政策法规"}]},
        {"id": "5247431",
         "policyDocument": [
             {"title": "中华人民共和国<span>增值税</span>法",
              "url": "/zcfgk/c100009/c5237365/content.html",
              "aging": "全文有效", "effectlevel": "法律",
              "writtentext": "中华人民共和国主席令第41号", "id": "5237365"}],
         "policyInterpretation": [],
         "policyGuidance": [],
         "policyQA": [
             {"title": "算力企业提供机架服务如何适用增值税政策？",
              "url": "/zcfgk/c100024/c5252393/content.html",
              "effectlevel": "财税文件",
              "writtentext": "财政部 税务总局公告2026年第9号", "id": "5252393"}]},
    ]}}
}


class _AssocResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_article_id_from_url_forms():
    """articleId 从 URL 末段抠：栏目 id 与文章 id 都是 c 段，取 content.html 前最后一个。"""
    assert tax_fgk.article_id_from_url(
        "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5247431/content.html") == "5247431"
    # 法律类页面 meta 可能没有 articleId，URL 这条路仍要通
    assert tax_fgk.article_id_from_url(
        "https://fgk.chinatax.gov.cn/zcfgk/c100009/c5237365/content.html") == "5237365"
    assert tax_fgk.article_id_from_url("https://x.test/no/content/path") == ""
    print("  [PASS] article_id_from_url 取末段 c 段，取不到时空串")


def test_fetch_associations_parses_measured_payload():
    """关联接口按实测形态解析：POST 走 www 域，相对链接拼 fgk 域，时效/文号归一。"""
    seen = {}

    def fake_request(method, url, *, headers, timeout, verify, data=None, **kw):
        seen["method"] = method
        seen["url"] = url
        seen["data"] = data
        return _AssocResp(_ASSOC_PAYLOAD)

    with _patched(**{"tax_http.request": fake_request}):
        a = tax_fgk.fetch_associations("5247431")

    assert seen["method"] == "POST" and seen["url"].startswith(tax_fgk.CHINATAX_HOST), seen
    assert seen["data"] == {"id": "5247431"}, seen
    assert "_error" not in a, a
    f = a["files"][0]
    assert f["title"] == "中华人民共和国增值税法", f          # <span> 已剥
    assert f["url"].startswith(tax_fgk.FGK_HOST), f          # 相对链接拼 fgk 域
    assert f["document_number"] == "中华人民共和国主席令第41号", f
    assert f["status"] == "全文有效", f
    assert f["effect_level"] == "法律", f
    q = a["qas"][0]
    assert "status" not in q, q        # 该组没有 aging，不该凭空造状态
    assert a["interpretations"] == [] and a["guidances"] == [], a
    print("  [PASS] 关联解析：POST→www、链接拼fgk、标题剥标签、时效文号位阶归一")


def test_fetch_associations_error_paths():
    """三条失败路径都要落到 _error 且四组为空，绝不能静默返回空关联当成功。"""
    # ① 没有 articleId：压根不发请求
    seen = {"called": False}

    def spy(method, url, **kw):
        seen["called"] = True
        return _AssocResp(_ASSOC_PAYLOAD)

    with _patched(**{"tax_http.request": spy}):
        a0 = tax_fgk.fetch_associations("")
    assert a0.get("_error") and not seen["called"], (a0, seen)

    def http404(method, url, **kw):
        return _AssocResp({}, status_code=404)

    with _patched(**{"tax_http.request": http404}):
        a1 = tax_fgk.fetch_associations("5247431")
    assert a1["_error"] == "HTTP 404", a1

    def no_group(method, url, **kw):
        return _AssocResp({"results": {"data": {"results": [{"channel": []}]}}})

    with _patched(**{"tax_http.request": no_group}):
        a2 = tax_fgk.fetch_associations("5247431")
    assert "关联分组" in a2["_error"], a2
    assert a2["files"] == [] and a2["qas"] == [], a2
    print("  [PASS] 关联三条失败路径都报 _error 且四组为空")


def test_attach_associations_runs_per_entry_without_body():
    """attach_associations 给每条现拉一份关联，不依赖正文；就地写 entry['associations']。"""
    calls = []

    def fake_request(method, url, **kw):
        calls.append(kw.get("data"))
        return _AssocResp(_ASSOC_PAYLOAD)

    entries = [{"url": "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5247431/content.html"},
               {"url": "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5200001/content.html"}]
    with _patched(**{"tax_http.request": fake_request}):
        out = tax_fgk.attach_associations(entries)
    assert out is entries, "应就地返回同一列表"
    assert all("associations" in e for e in entries), entries
    assert len(calls) == 2, f"每条一次请求，实际 {len(calls)}"
    print("  [PASS] attach_associations 逐条现拉，无需正文")


# ── 入口 ────────────────────────────────────────────────────────────────────

def main():
    tests = [
        ("FGK_MARKER 自检", test_fgk_marker_selfcheck),
        ("翻页上限与提前收尾", test_max_pages_respected),
        ("自适应收尾与严格模式", test_adaptive_early_stop),
        ("文字正文正常取出", test_text_entry_ok),
        ("视频/图片条目报错", test_video_entry_reports_error),
        ("结果里标注 media_only", test_media_only_marked_in_results),
        ("缓存不含正文", test_cache_excludes_body),
        ("缓存命中标记与不污染", test_cache_hit_marker),
        ("CLI 缓存标记", test_cli_cache_tag),
        ("filters 逐页下推", test_scan_list_threads_filters_to_every_page),
        ("filters 分键缓存", test_search_fgk_caches_each_filter_set_apart),
        ("带维度 0 条报拼窄", test_filters_produce_too_narrow_instead_of_libraries_empty),
        ("articleId 从 URL 取", test_article_id_from_url_forms),
        ("关联解析实测形态", test_fetch_associations_parses_measured_payload),
        ("关联失败路径报错", test_fetch_associations_error_paths),
        ("逐条现拉关联", test_attach_associations_runs_per_entry_without_body),
    ]

    all_passed = 0
    for name, fn in tests:
        print(f"\n{'─' * 50}")
        print(f"> {name}")
        try:
            fn()
            all_passed += 1
            print(f"  [{PASS}] {name}")
        except Exception as e:
            print(f"  [{FAIL}] {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"Results: {all_passed}/{len(tests)} passed")
    print(f"{'=' * 60}")
    return 0 if all_passed == len(tests) else 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
