#!/usr/bin/env python3
"""tax_fgk.py 的离线用例：翻页上限、正文档/视频条目、缓存边界。

与 test_tax_search.py 里的 test_fgk_* 分工不同：

  - 那边是**联网 e2e**，验的是"线上此刻真拿得到"；
  - 这边全部打桩，验的是**逻辑本身对不对**，不碰网络，可反复跑、结果稳定。

打桩点选在"网络出口"，不是被测函数内部：
  tax_fgk.search_chinatax  → 总局 search5 检索（清单层）
  tax_fgk.requests.get     → 详情页 HTML（正文层）
被测代码的翻页、去重、正文切分、缓存判断全部照常执行。

覆盖五件事：
  1. max_pages 是硬上限，且够 size 就提前收尾
  2. 文字正文取得出；视频/图片条目必须明确报错，不能返回空正文当成功
  3. 缓存里**绝不能**出现正文
  4. 命中缓存要有标记，且命中时不再打网络、缓存不被正文污染
  5. 正文提取保留表格结构（合并格、跨格不拼假期限）、图片型条目带回素材地址、
     清单层附件字段透到条目上

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
import tax_web_search  # noqa: E402  清单层行构造（attachments）在这边，字段转发过去
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
    opts_seen: list = []

    def fake(keyword, page=1, size=PAGE_SIZE, filters=None, **opts):
        calls.append(page)
        filters_seen.append(filters)
        opts_seen.append(opts)
        n_fgk = pages_fgk_count.get(page, 0)
        items = []
        for i in range(PAGE_SIZE):
            items.append(_fgk_item(page, i) if i < n_fgk else _news_item(page, i))
        return {"total": total_hits, "results": items}

    fake.filters_seen = filters_seen
    fake.opts_seen = opts_seen
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


# ── 正文提取：表格结构、素材地址、附件 ──────────────────────────────────────
#
# 这一组用例对应 2026-10-04 的三处实测：
#   · 正文里有 <table> 的页面不普遍但存在：两批共 75 篇详情页里 8 篇含表
#     （「税目税额表」等词那批 35 篇里 6 篇、另一批 40 篇里 2 篇），带表的多是
#     税则/税目税额类公告；现行公告的表常常只在附件里（「消费税 成品油」首屏
#     10 条里 6 条带附件、appendixContent 全空）——"表铺成可读的行"和
#     "附件直链落地"两条都要有，缺一条就有一半的表读不到；
#   · 摊平写法丢行列对应：Word 粘贴的表每格包 <p>，摊平成"一格一行"，只能靠
#     数行号配对；没有 <p> 的表整行各格首尾相接成一串字；
#   · 图片/视频型正文原先只回一句"无文字内容"，人拿不到去看原文的入口。

def test_table_lines_markdown_shape():
    """一张表铺成 Markdown 行：colspan 补空列、rowspan 带到延续行、宽度补齐。

    每条断言各钉一种合并格。合并格处理错的后果不是报错而是**读错位**：
    少补一列会让后面每一列整体左移，"13%"挂到下一档税率头上。
    """
    # ① colspan 横向合并：占两列，第二列补空，不能把后面的列顶左移
    got = tax_fgk._table_lines(
        "<table><tr><td colspan=\"2\">合计</td><td>3</td></tr></table>")
    assert got == ["| 合计 |  | 3 |"], got

    # ② 只有一行时不插 Markdown 分隔行——插了会被当成表头，下面没有数据行
    got = tax_fgk._table_lines("<table><tr><td>甲</td><td>乙</td></tr></table>")
    assert got == ["| 甲 | 乙 |"], got

    # ③ rowspan 纵向合并：延续行填**同一个文本**而不是空串。合并格的语义是
    #    "这几行都算这个值"，留空会让读者以为那几行没有这一列。
    got = tax_fgk._table_lines(
        "<table><tr><td rowspan=\"2\">成品油</td><td>1.2元/升</td></tr>"
        "<tr><td>1.52元/升</td></tr></table>")
    assert got == ["| 成品油 | 1.2元/升 |",
                   "| --- | --- |",
                   "| 成品油 | 1.52元/升 |"], got

    # ④ 各行单元格数不齐（页面里很常见）：短的行补空列到最宽，否则 Markdown
    #    渲染时列数不一致，整张表塌成纯文本
    got = tax_fgk._table_lines(
        "<table><tr><td>a</td><td>b</td><td>c</td></tr><tr><td>d</td></tr></table>")
    assert got == ["| a | b | c |", "| --- | --- | --- |", "| d |  |  |"], got

    # ⑤ 空表与"有行但全空"的表都回空列表——正文里留一串孤零零的分隔行是噪声
    assert tax_fgk._table_lines("<table></table>") == []
    assert tax_fgk._table_lines("<table><tr><td> </td><td></td></tr></table>") == []
    assert tax_fgk._table_lines("<div>根本没有表</div>") == []
    print("  [PASS] 合并格 colspan/rowspan、参差补列、单行不插分隔、空表回空")


def test_text_of_mixes_prose_and_tables_in_order():
    """表格段与文字段按原文顺序拼，没有表的页面一个字节都不该变。

    ②是等价性检查：改版把原 _text_of 的函数体搬进 _plain_text，文字路径若
    顺带动了（比如把 </td> 也当换行），全线正文都会跟着变形。
    """
    # ① 表前表后的段落都保留，且顺序与页面一致
    got = tax_fgk._text_of(
        "<p>前段</p><table><tr><td>a</td><td>b</td></tr></table><p>后段</p>")
    assert got == "前段\n| a | b |\n后段", repr(got)

    # ② 无表路径与改版前逐字符一致（段落分行、空段丢弃、脚本剥离）
    assert tax_fgk._text_of("<p>甲</p><p>乙</p>") == "甲\n乙"
    assert tax_fgk._text_of("<p>甲</p><p></p><p>乙</p>") == "甲\n乙"
    assert tax_fgk._text_of("<script>var t='| 假 | 表 |';</script><p>甲</p>") == "甲"
    assert tax_fgk._text_of("<p>甲　乙</p>") == "甲 乙"   # 全角空格归一

    # ③ 单元格内多段 <p> 压成同一格，不能换行——换行会把一行表拆成两行表
    got = tax_fgk._text_of("<table><tr><td><p>A</p><p>B</p></td><td>c</td></tr></table>")
    assert got == "| A B | c |", repr(got)
    print("  [PASS] 表与段落按序拼接；无表路径逐字符未变；格内多段压成一格")


def test_table_cell_split_does_not_fabricate_a_period():
    """跨格的"自X年Y月Z日 / 至…"不能被拼成一段执行期限。

    这是表格改版的副作用检查，两个方向都要钉住：
      · 拼在一起（旧的摊平写法）会被 _EXPIRY_RANGE 读成一个区间——表头里
        "自"与"至"分列两格的文件会凭空多出期限；
      · 拆开后（现在的 " | "）读不出区间，但**同一格内**写全的区间必须照常
        读得出，否则这条用例改成什么都还是绿的。
    """
    import tax_evidence as EV

    split = ("<table><tr><td>自2023年1月1日</td>"
             "<td>至2027年12月31日</td></tr></table>")
    item = {"content": tax_fgk._text_of(split)}
    assert " | " in item["content"], item["content"]
    assert EV.period_start_of(item) == "", item["content"]
    assert EV.expiry_of(item) == "", item["content"]

    # 反面对照：同样两个字面量拼在一起确实会抽出一段区间（说明上面那次为空
    # 是分隔符起的作用，不是抽取逻辑坏掉了）
    glued = {"content": "自2023年1月1日至2027年12月31日"}
    assert EV.period_start_of(glued) == "2023-01-01", glued
    assert EV.expiry_of(glued) == "2027-12-31", glued

    # 同一格内写全的区间（表格里真这么写）仍要读得出
    one_cell = {"content": tax_fgk._text_of(
        "<table><tr><td>享受免征</td>"
        "<td>自2023年1月1日至2027年12月31日</td></tr></table>")}
    assert EV.period_start_of(one_cell) == "2023-01-01", one_cell
    assert EV.expiry_of(one_cell) == "2027-12-31", one_cell
    print("  [PASS] 跨格区间不拼成假期限；格内区间与裸文本仍照常被抽出")


def test_media_urls_absolute_dedup_and_placeholders():
    """素材地址要拼成点得开的绝对链接：相对、站根、协议相对、data: 四种写法。

    详情页里的图片多数是相对地址，直接印出来点开是 404；以 / 开头的是站根
    相对，拼在详情页目录后面会多一层路径——这两种是最容易"看起来有链接、
    其实全打不开"的形态。
    """
    page = "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5207148/content.html"
    got = tax_fgk._media_urls(
        "<img src=\"images/a.png\">"
        "<img src=\"/zcfgk/pic/b.png\">"
        "<video src=\"https://x.test/v.mp4\"></video>"
        "<source src=\"//cdn.test/m.mp4\">"
        "<img src=\"data:image/png;base64,AAAA\">"
        "<img src=\"images/a.png\">",
        page)
    assert got == [
        "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5207148/images/a.png",
        "http://fgk.chinatax.gov.cn/zcfgk/pic/b.png",
        "https://x.test/v.mp4",
        "http://cdn.test/m.mp4",
    ], got
    assert not tax_fgk._media_urls("<p>有文字</p>", page), "无素材应回空列表"
    print(f"  [PASS] {len(got)} 个地址：相对/站根/协议相对/绝对四种写法都拼对，"
          "data: 跳过、重复去重")


def test_media_only_body_carries_asset_urls():
    """图片/视频型正文：报错之外必须把素材地址带回来，文字型不带这个键。

    只回"正文为视频/图片"等于把人挡在外面——这一类的全部内容就在那张图里，
    给了地址读者至少能自己看一眼原文再决定信不信。
    """
    page = "http://fgk.chinatax.gov.cn/zcfgk/c100023/c5210001/content.html"
    html = _detail_html('<img src="images/p1.png"><img src="images/p2.png">',
                        note="税法小课堂")
    with _patched(**{"requests.get": lambda url, **kw: _Resp(html)}):
        r = fetch_fgk_body(page)
    assert r.get("_error") and "视频" in r["_error"], r
    assert r.get("media_urls") == [
        "http://fgk.chinatax.gov.cn/zcfgk/c100023/c5210001/images/p1.png",
        "http://fgk.chinatax.gov.cn/zcfgk/c100023/c5210001/images/p2.png"], r
    assert not r.get("content"), "图片型条目不该给出正文"
    print(f"  [PASS] 图片型正文带回 {len(r['media_urls'])} 个素材地址且仍报 _error")

    # 容器里只有 video 标签但一个 src 都没有（地址写在 JS 里）：不能凭空造地址
    html_empty = _detail_html('<video class="v"></video>')
    with _patched(**{"requests.get": lambda url, **kw: _Resp(html_empty)}):
        r2 = fetch_fgk_body(page)
    assert r2.get("_error") and "media_urls" not in r2, r2
    print("  [PASS] 取不到 src 时不带 media_urls 键（不编造地址）")

    # 文字型条目不沾这个键
    html_text = _detail_html("<p>第一条 正文。</p><img src=\"images/logo.png\">")
    with _patched(**{"requests.get": lambda url, **kw: _Resp(html_text)}):
        r3 = fetch_fgk_body(page)
    assert not r3.get("_error") and "media_urls" not in r3, r3
    assert "第一条" in r3["content"] and "logo" not in r3["content"], r3
    print("  [PASS] 文字型条目不带 media_urls，正文里也不混进图片地址")


def test_search_fgk_forwards_attachments_and_media():
    """清单层的 attachments 与正文层的 media_urls 都要出现在返回条目里。

    两头各自丢一次都看不出来：接口回了附件、上层没接，条数照常、只有字段少了；
    反过来 media_urls 来自正文层，绝不能跟着清单一起落进缓存——落盘的那张图
    换一份正文就没人管了。
    """
    raw = [{"appendixName": "成品油消费税税目税率表.xls", "appendixType": "XLS",
            "appendixUrl": "http://fgk.chinatax.gov.cn/zcfgk/c102416/c5203976/"
                           "5203976/files/成品油消费税税目税率表.xls"},
           {"appendixName": "只有名字没有链接的附件", "appendixUrl": ""},
           {"appendixName": "", "appendixUrl": "http://x.test/anon.pdf"},
           "不是字典的一栏", None]
    got = tax_web_search._attachments(raw)
    assert len(got) == 1, f"名或链缺一项的就丢掉，实际 {got}"
    assert got[0]["type"] == "xls", f"type 要归一成小写：{got[0]}"
    assert tax_web_search._attachments(None) == []
    print(f"  [PASS] 附件归一：{len(raw)} 条原始项留 1 条（缺名/缺链/脏项丢弃）")

    with_att = _fgk_item(1, 0)
    with_att["attachments"] = got
    video = _fgk_item(1, 1)

    def fake(keyword, page=1, size=PAGE_SIZE, filters=None, **opts):
        return {"total": 2, "results": [dict(with_att), dict(video)]}

    def fake_get(url, **kw):
        if url == video["url"]:
            return _Resp(_detail_html('<img src="images/taxrate.png">'))
        return _Resp(_detail_html("<p>第一条。</p>"))

    with _temp_cache() as cache_dir:
        with _patched(search_chinatax=fake, **{"requests.get": fake_get}):
            r = search_fgk("测试词", size=2, with_body=True)

        by_url = {e["url"]: e for e in r["results"]}
        assert by_url[with_att["url"]]["attachments"] == got, by_url[with_att["url"]]
        assert by_url[video["url"]].get("media_urls") == [
            "http://fgk.chinatax.gov.cn/zcfgk/c102416/c50101/images/taxrate.png"
        ], by_url[video["url"]]
        assert "attachments" not in by_url[video["url"]], "没附件的条目不该造空列表"

        files = list(cache_dir.glob("*.json"))
        assert len(files) == 1, f"应只落一份清单缓存，实际 {len(files)} 个"
        raw_cache = files[0].read_text(encoding="utf-8")
        assert "taxrate.png" not in raw_cache, "media_urls 是正文层字段，不该进缓存"
        assert "第一条" not in raw_cache, "缓存里出现了正文"
        assert "成品油消费税税目税率表.xls" in raw_cache, \
            "附件是清单层字段，缓存里应留着"
        print("  [PASS] attachments 进缓存、media_urls 与正文都不进缓存")
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
        tax_fgk.search_chinatax = lambda keyword, page=1, size=10, filters=None, \
            **opts: empty_with_note
        r = tax_fgk._scan_list("测试词", size=5, max_pages=2, filters={"docYear": "2018"})
    finally:
        tax_fgk.search_chinatax = saved_scan
    assert "分不清" in r.get("_filter_note", ""), r
    assert "未筛出法规库条目" not in r.get("_error", ""), r["_error"]
    print("  [PASS] 带维度 0 条透出「可能拼窄」，不写成翻完N页未筛出")


def test_scan_list_flags_fetch_failure_apart_from_empty():
    """清单层遇到 search_chinatax 报 _error：要带 _fetch_failed，不能读成翻完未筛出。

    对应的坑：四种空清单成因里只有"请求真失败"该被前端标成取数故障。_scan_list
    把 found 的 _error 提到结果的 _error 同时必须补 _fetch_failed，否则前端只能
    看到一句 _error，分不清是服务没连上还是库里确实没有。
    """
    saved_scan = tax_fgk.search_chinatax
    failed = {"total": 0, "results": [], "_error": "HTTP 500"}
    try:
        tax_fgk.search_chinatax = lambda keyword, page=1, size=10, filters=None, \
            **opts: failed
        r = tax_fgk._scan_list("测试词", size=5, max_pages=2)
    finally:
        tax_fgk.search_chinatax = saved_scan
    assert r.get("_fetch_failed") is True, r
    assert r["_error"] == "HTTP 500", r
    # 失败不是"库里没有"：不带那两种措辞
    assert "_filter_note" not in r and "_empty_reason" not in r, r
    assert "未筛出法规库条目" not in r["_error"], r["_error"]
    print("  [PASS] 清单层请求失败带 _fetch_failed，与未筛出/拼窄分开报")


# ── 检索范围与排序（label 白名单 / orderBy） ─────────────────────────────────

def test_scan_list_threads_scope_and_order_to_every_page():
    """file_only 与 order 必须像 filters 一样逐页下推，不能只在首页生效。

    对应的坑：翻页窗口是按范围/排序取的那一屏序列，若后续页漏掉参数，取回的
    清单就是"首页收窄、后页全站"的混合体——条数照常、结构照常，只有逐页核对
    参数才能发现。默认（白名单+相关度）也要显式发出去，且此时不打 label_scope。
    """
    fake, calls = _make_fake_search({1: 2, 2: 2, 3: 2})
    with _patched(search_chinatax=fake):
        r = tax_fgk._scan_list("测试词", size=6, max_pages=3, adaptive=False,
                               file_only=False, order="date_desc")
    assert len(calls) == 3 and len(fake.opts_seen) == 3, (calls, fake.opts_seen)
    assert all(o == {"file_only": False, "order": "date_desc"}
               for o in fake.opts_seen), fake.opts_seen
    assert r.get("label_scope") == "全站（含新闻、视频、各地动态）", r

    fake2, _ = _make_fake_search({1: 2, 2: 2})
    with _patched(search_chinatax=fake2):
        r2 = tax_fgk._scan_list("测试词", size=4, max_pages=2, adaptive=False)
    assert all(o == {"file_only": True, "order": "relevance"}
               for o in fake2.opts_seen), fake2.opts_seen
    assert "label_scope" not in r2, "默认范围不需要回显，界面上会当成一条异常说明"
    print(f"  [PASS] {len(fake.opts_seen)} 页都带同一组范围/排序；全站回显、默认不回显")


def test_zero_hit_sentence_names_the_label_scope():
    """收窄到 0 条时那句成因必须点出"文件类标签之外不在窗口内"。

    对应的坑：默认白名单把「视频政策解读」「图片政策解读」两类法规库条目挡在窗口
    外（2026-10-02 实测「研发费用加计扣除」全站前 3 页有 7 条这类），界面若只印
    "未筛出法规库条目"，用户会读成"库里没有这份文件"。关掉白名单后这句话就失去
    依据，必须跟着消失。
    """
    fake, _ = _make_fake_search({})            # 十条全是新闻，没有法规库条目
    with _patched(search_chinatax=fake):
        narrowed = tax_fgk._scan_list("测试词", size=5, max_pages=1, adaptive=False)
        whole = tax_fgk._scan_list("测试词", size=5, max_pages=1, adaptive=False,
                                   file_only=False)
    assert narrowed["total"] == 0, narrowed
    assert "文件类标签" in narrowed["_error"] and "--all-labels" in narrowed["_error"], \
        narrowed["_error"]
    assert "--all-labels" not in whole.get("_error", ""), whole["_error"]
    print("  [PASS] 白名单下 0 条那句点出范围并给出 --all-labels；全站下不写")


def test_scope_token_only_adds_a_key_segment_when_not_default():
    """scope_token 只在偏离默认时追加键段，默认必须回空串。

    默认那份清单的键要落回 LIST_KEY_REV 那一条，否则改一次默认就把自己刚建的
    缓存全冲掉；反过来只要偏离默认就必须另立键，否则全站清单会顶替白名单清单
    被读出来（test_scope_and_order_have_their_own_cache_keys 验的是这条的实际后果）。
    """
    assert tax_fgk.scope_token(True, "relevance") == ""
    assert tax_fgk.scope_token(False, "relevance") == "labels=all"
    assert tax_fgk.scope_token(True, "date_desc") == "order=date_desc"
    assert tax_fgk.scope_token(False, "date_desc") == "labels=all;order=date_desc"
    print("  [PASS] 默认空串、关白名单/换序各自成段，两者同时偏则并段")


def test_scope_and_order_have_their_own_cache_keys():
    """换范围或换排序必须另立缓存，不许读回默认那份清单。

    这条管的是真会发生的错法：同一检索词在两种范围下取回的是完全不同的集合
    （「转让定价」全站前 3 页 0 条、白名单 18 条），共用一条缓存时用户点了
    --all-labels 却拿回白名单的结果，还以为是全站就长这样。
    """
    fake, _ = _make_fake_search({1: 10})
    with _temp_cache():
        with _patched(search_chinatax=fake):
            r1 = search_fgk("测试词", size=2, max_pages=3)
            assert r1["_from_cache"] is False, "第一次取不该命中"
            n = len(fake.opts_seen)
            r2 = search_fgk("测试词", size=2, max_pages=3)
            assert r2["_from_cache"] is True, "同参数第二次应命中"
            assert len(fake.opts_seen) == n, "命中缓存却又发了请求"

            r3 = search_fgk("测试词", size=2, max_pages=3, file_only=False)
            assert r3["_from_cache"] is False, "全站范围读到了白名单的缓存"
            assert fake.opts_seen[-1] == {"file_only": False, "order": "relevance"}, \
                "search_fgk 的参数没传到清单层"
            assert r3.get("label_scope"), "全站那份要带着范围回显，缓存后也不能丢"

            r4 = search_fgk("测试词", size=2, max_pages=3, order="date_desc")
            assert r4["_from_cache"] is False, "换排序读到了相关度的缓存"
            assert fake.opts_seen[-1]["order"] == "date_desc"

            r5 = search_fgk("测试词", size=2, max_pages=3, file_only=False,
                            order="date_desc")
            assert r5["_from_cache"] is False, "范围与排序的组合又是一份，不能复用单偏那份"

            r6 = search_fgk("测试词", size=2, max_pages=3)
            assert r6["_from_cache"] is True, "默认那份不能被偏默认的请求冲掉"
            assert "label_scope" not in r6, r6
    print("  [PASS] 缓存按 (范围, 排序) 分键：默认命中、四种偏离各自重取")


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
        ("表格铺成 Markdown 行", test_table_lines_markdown_shape),
        ("表与段落按序拼接、无表路径不变", test_text_of_mixes_prose_and_tables_in_order),
        ("跨格不拼假执行期限", test_table_cell_split_does_not_fabricate_a_period),
        ("素材地址四种写法", test_media_urls_absolute_dedup_and_placeholders),
        ("图片型正文带回素材地址", test_media_only_body_carries_asset_urls),
        ("附件与素材地址透到条目", test_search_fgk_forwards_attachments_and_media),
        ("filters 逐页下推", test_scan_list_threads_filters_to_every_page),
        ("filters 分键缓存", test_search_fgk_caches_each_filter_set_apart),
        ("带维度 0 条报拼窄", test_filters_produce_too_narrow_instead_of_libraries_empty),
        ("请求失败带 _fetch_failed", test_scan_list_flags_fetch_failure_apart_from_empty),
        ("范围/排序逐页下推", test_scan_list_threads_scope_and_order_to_every_page),
        ("0 条成因点出范围", test_zero_hit_sentence_names_the_label_scope),
        ("scope_token 只在偏离默认时成段",
         test_scope_token_only_adds_a_key_segment_when_not_default),
        ("范围/排序各自分键缓存", test_scope_and_order_have_their_own_cache_keys),
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
