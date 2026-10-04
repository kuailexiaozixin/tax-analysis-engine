#!/usr/bin/env python3
"""detail 详情缓存的离线用例。

覆盖四件事：
  1. 键算法稳定：键与既有缓存文件名一致，升级不失联（兼容性契约）
  2. 命中留痕：_from_cache / _cache_age_s，以及"恒开 + TTL 1 小时"的默认语义
  3. 逃生门与作用域：--no-cache 不写盘；clear() 只清本命名空间，不越界删别的命名空间
  4. 契约：缓存里只放元数据与目录骨架，**不出现条文正文**

全程打桩网络出口（tax_detail._request），不联网、不碰用户真实缓存目录。
"""

import contextlib
import io
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tax_detail                                        # noqa: E402
from tax_cache import CacheManager                       # noqa: E402

LAW_ID = "ff808181927b083b0193fd65a0eb02cb"
# _key("detail", LAW_ID) 的已知值＝现有缓存的真实文件名。
# 键算法一旦改动，所有现存缓存都会静默失联，这条常量就是那道哨兵。
KNOWN_KEY = "276dc607b1ab7197"


class _Resp:
    """够用的假响应：只实现 fetch_detail 会碰的那几个成员。"""

    def __init__(self, payload):
        body = json.dumps(payload, ensure_ascii=False)
        self._payload = payload
        self.status_code = 200
        self.headers = {"Content-Type": "application/json"}
        self.text = body
        self.content = body.encode("utf-8")

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _detail_payload(title="中华人民共和国测试法"):
    """按 NPC 详情接口的真实结构造响应：content 是单个根节点，且不带正文。"""
    return {"data": {
        "title": title, "flxz": "法律", "gbrq": "2024-01-01", "sxrq": "2024-06-01",
        "sxx": 3, "zdjgName": "全国人民代表大会常务委员会",
        "ossFile": {"ossWordPath": "https://example.invalid/a.docx",
                    "ossPdfPath": "https://example.invalid/a.pdf"},
        "content": {"title": title, "level": 0, "children": [
            {"title": "第一章 总则", "level": 1, "content": ""},
            {"title": "第二章 税率", "level": 1, "content": ""},
        ]},
    }}


@contextlib.contextmanager
def _patched(**kw):
    """临时替换 tax_detail 的模块属性，退出时原样还原。"""
    saved = [(k, getattr(tax_detail, k)) for k in kw]
    for k, v in kw.items():
        setattr(tax_detail, k, v)
    try:
        yield
    finally:
        for k, old in saved:
            setattr(tax_detail, k, old)


class _Counter:
    """记下被打桩的请求次数，用来断言"有没有真的走缓存"。"""

    def __init__(self):
        self.calls = []

    def __call__(self, url, max_retries=4):
        self.calls.append(url)
        return _Resp(_detail_payload())

    def __len__(self):
        return len(self.calls)


def _temp_manager(td):
    cm = CacheManager(enabled=True, namespace="detail")
    cm.dir = Path(td)
    return cm


# ── 用例 ────────────────────────────────────────────────────────────────────

def test_key_algorithm_unchanged():
    """键算法必须与既有缓存文件名一致——否则现存缓存全部失联。"""
    cm = CacheManager(namespace="detail")
    got = cm._key("detail", LAW_ID)
    assert got == KNOWN_KEY, (
        f"_key('detail', LAW_ID) 得到 {got!r}，既有值是 {KNOWN_KEY!r}。"
        f"改键算法等于把所有已写下的缓存变成孤儿，确认是有意为之再更新本常量。")
    # 共享实现与内联老实现的拼接方式必须一致：raw = 'detail|<id>'
    assert cm._key("detail", LAW_ID) == cm._key(*["detail", LAW_ID])
    return True


def test_default_enabled_and_ttl():
    """详情缓存恒开、TTL 1 小时——与 tax_search / tax_fgk 的默认关是有意差异。"""
    assert tax_detail._detail_cache.enabled is True, "详情缓存应默认开启"
    assert tax_detail._detail_cache.namespace == "detail"
    assert tax_detail.DETAIL_CACHE_TTL == 3600, "详情元数据 TTL 应为 1 小时"
    return True


def test_first_fetch_then_cache_hit():
    """首次现拉、二次命中，并带上 _from_cache 与 _cache_age_s 留痕。"""
    fake = _Counter()
    with tempfile.TemporaryDirectory() as td:
        cm = _temp_manager(td)
        with _patched(_request=fake, _detail_cache=cm):
            d1 = tax_detail.fetch_detail(LAW_ID)
            d2 = tax_detail.fetch_detail(LAW_ID)
    assert d1["_from_cache"] is False, "首次不该标成命中缓存"
    assert d2["_from_cache"] is True, "二次应命中缓存"
    assert isinstance(d2.get("_cache_age_s"), (int, float)), "命中时要报缓存年龄"
    assert len(fake) == 1, f"二次应命中缓存不再请求，实际请求 {len(fake)} 次"
    assert d2["title"] == d1["title"]
    return True


def test_ttl_expiry_forces_refetch():
    """过了 TTL 必须重新抓，不能拿过期元数据当现值。"""
    fake = _Counter()
    with tempfile.TemporaryDirectory() as td:
        cm = _temp_manager(td)
        with _patched(_request=fake, _detail_cache=cm):
            tax_detail.fetch_detail(LAW_ID)
            p = Path(td) / f"{cm._key('detail', LAW_ID)}.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            data["_cached_at"] = time.time() - tax_detail.DETAIL_CACHE_TTL - 1
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            d2 = tax_detail.fetch_detail(LAW_ID)
    assert len(fake) == 2, f"过期后应重新抓，实际共请求 {len(fake)} 次"
    assert d2["_from_cache"] is False, "重新抓回来的不该标成命中"
    return True


def test_cli_no_cache_writes_nothing():
    """--no-cache 是逃生门：既不读缓存，也不落缓存。"""
    fake = _Counter()
    saved_argv = sys.argv
    with tempfile.TemporaryDirectory() as td:
        cm = _temp_manager(td)
        try:
            with _patched(_request=fake, _detail_cache=cm):
                sys.argv = ["tax_detail.py", "--info", LAW_ID, "--no-cache"]
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    tax_detail.main()
        finally:
            sys.argv = saved_argv
        left = sorted(p.name for p in Path(td).glob("*.json"))
    assert not left, f"--no-cache 不该落任何缓存，实际留下 {left}"
    assert len(fake) == 1, f"应现拉一次，实际 {len(fake)} 次"
    return True


def test_clear_is_namespace_local():
    """clear() 只清本命名空间：不得越界删除其他命名空间的 *.json 文件。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        a = CacheManager(enabled=True, namespace="search")
        b = CacheManager(enabled=True, namespace="detail")
        a.dir = b.dir = d
        a.set(a._key("search", "kw"), {"v": 1})
        b.set(b._key("detail", LAW_ID), {"v": 2})
        assert a.stats()["entries"] == 1 and b.stats()["entries"] == 1
        assert a.stats().get("other_namespaces") == 1, "统计应报出还有别家的条目"

        removed = a.clear()
        assert removed == 1, f"只该删自己那 1 条，实际删了 {removed}"
        assert a.stats()["entries"] == 0
        assert b.stats()["entries"] == 1, "detail 的条目被误删了——作用域没隔离住"
    return True


def test_legacy_entry_claimed_on_read():
    """没有 _ns 字段的老条目，在被读到时就地转正，且 TTL 不被重置。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        cm = CacheManager(enabled=True, namespace="detail")
        cm.dir = d
        key = cm._key("detail", "legacy-entry")
        ts = time.time()
        (d / f"{key}.json").write_text(
            json.dumps({"_cached_at": ts, "payload": {"v": 1}}), encoding="utf-8")

        before = cm.stats()
        assert before["entries"] == 0 and before["legacy_entries"] == 1, \
            f"无主条目应被单独报出，实际 {before}"

        assert cm.get(key, max_age=10 ** 9) == {"v": 1}, "老条目必须仍然读得到"

        after = cm.stats()
        assert after["entries"] == 1 and not after.get("legacy_entries"), \
            f"读过之后应转正，实际 {after}"
        on_disk = json.loads((d / f"{key}.json").read_text(encoding="utf-8"))
        assert on_disk["_ns"] == "detail"
        assert abs(on_disk["_cached_at"] - ts) < 1, "转正不能顺带重置 TTL"
    return True


def test_cache_never_holds_article_text():
    """契约：缓存里是"元数据 + 目录骨架"，不是条文。

    NPC 详情接口的 content 树只给章/节标题，正文只在 DOCX/PDF 里，所以
    "正文永不缓存"这条原则目前没被违反。本用例把该契约钉住——上游一旦开始
    在 content 里给条文，下面的断言就会失败，那时要么在 fetch_detail 里剥离
    正文，要么明确作废这条契约，而不是让过期条文静静写进磁盘。
    """
    fake = _Counter()
    with tempfile.TemporaryDirectory() as td:
        cm = _temp_manager(td)
        with _patched(_request=fake, _detail_cache=cm):
            tax_detail.fetch_detail(LAW_ID)
        blob = (Path(td) / f"{cm._key('detail', LAW_ID)}.json").read_text(
            encoding="utf-8")
    payload = json.loads(blob)["payload"]
    tree = payload["content_tree"]
    assert tree, "假数据应带出目录骨架，否则本用例是空转"
    for node in tree:
        assert set(node) <= {"level", "title", "content"}, f"节点多了字段：{node}"
        assert not (node.get("content") or "").strip(), \
            f"缓存里出现了条文正文：{node!r}——正文必须每次现拉"
    return True


def test_atomic_write_leaves_no_partial_json():
    """并发写同一个键，文件始终是完整 JSON，且不残留临时文件。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        cm = CacheManager(enabled=True, namespace="detail")
        cm.dir = d
        key = cm._key("detail", "concurrent")
        errors = []

        def worker(n):
            try:
                for _ in range(20):
                    cm.set(key, {"n": n, "pad": "x" * 2000})
            except Exception as e:                      # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发写报错：{errors}"
        raw = (d / f"{key}.json").read_text(encoding="utf-8")
        json.loads(raw)                                  # 半截 JSON 会在这里炸
        leftovers = [p.name for p in d.iterdir() if p.suffix == ".tmp"]
        assert not leftovers, f"残留临时文件：{leftovers}"
    return True


TESTS = [
    ("键算法与既有值一致", test_key_algorithm_unchanged),
    ("恒开 + TTL 1 小时", test_default_enabled_and_ttl),
    ("首次现拉 / 二次命中留痕", test_first_fetch_then_cache_hit),
    ("过 TTL 重新抓", test_ttl_expiry_forces_refetch),
    ("--no-cache 不读不写", test_cli_no_cache_writes_nothing),
    ("clear 不越界", test_clear_is_namespace_local),
    ("老条目读时转正", test_legacy_entry_claimed_on_read),
    ("缓存不含条文正文", test_cache_never_holds_article_text),
    ("原子写不留半截 JSON", test_atomic_write_leaves_no_partial_json),
]


def main():
    passed = 0
    print(f"detail 缓存用例（{len(TESTS)} 条，全离线打桩）")
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"  [PASS] {name}")
        except AssertionError as e:
            print(f"  [FAIL] {name}")
            print(f"         {e}")
        except Exception as e:                          # noqa: BLE001
            print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
    print(f"\n  结果：{passed}/{len(TESTS)} 通过")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
