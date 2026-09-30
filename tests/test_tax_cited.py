#!/usr/bin/env python3
"""tax_cited（官方域判据 + 文号链接缓存）与其在 locate_cited_document 里接线的离线用例。

不联网。缓存指到临时文件，不碰真实 ~/.cache。桩打在检索入口 AN.FGK.search_fgk。

钉住两件事（B 的验收标准）：
  A) 域名判据 is_official：chinatax.gov.cn 及任意子域为真；把官方域塞进路径 /
     后缀拼假的站、商业站一律假。put_cited_link 据此拒收非官方链接。
  B) 缓存闭环：冷启动检索 → 命中官方链接落缓存；再查同文号 → _from_cache=True
     且**不打检索**；检索到的条目 URL 非官方域 → 不落缓存（下次仍走检索）。
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_answer as AN          # noqa: E402
import tax_cited as CITED        # noqa: E402
import tax_terms as TT           # noqa: E402

OFFICIAL = "https://fgk.chinatax.gov.cn/zcfgk/c102416/c5207148/content.html"


class TestIsOfficial(unittest.TestCase):
    def test_real_official_hosts(self):
        for url in [
            "https://chinatax.gov.cn/",
            "https://www.chinatax.gov.cn/chinatax/n810346/index.html",
            OFFICIAL,
            "https://shanghai.chinatax.gov.cn/zcfw/zcfgk/zzs/201210/t400596.html",
        ]:
            self.assertTrue(CITED.is_official(url), url)

    def test_lookalike_hosts_rejected(self):
        for url in [
            "https://example.com/chinatax.gov.cn",           # 官方域在路径里
            "http://evil-chinatax.gov.cn/x",                 # 连字符前缀，不是子域
            "https://chinatax.gov.cn.attacker.net/x",        # 官方域只是中间标签
            "https://notchinatax.gov.cn/x",
            "https://shanghai.chinatax.gov.cn.evil.com/x",
        ]:
            self.assertFalse(CITED.is_official(url), url)


class TestPutGet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "cited_links.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_put_accepts_official_rejects_other(self):
        dn = "财税〔2012〕75号"
        self.assertTrue(CITED.put_cited_link(dn, OFFICIAL, self.path))
        self.assertEqual(OFFICIAL, CITED.get_cited_link(dn, self.path))
        self.assertFalse(CITED.put_cited_link("国税发〔2001〕110号",
                                              "https://example.com/x", self.path))
        self.assertEqual("", CITED.get_cited_link("国税发〔2001〕110号", self.path))

    def test_empty_doc_number_no_op(self):
        self.assertFalse(CITED.put_cited_link("", OFFICIAL, self.path))


class TestLocateIntegration(unittest.TestCase):
    def setUp(self):
        self._saved_get = AN.FGK.search_fgk
        self._saved_path = AN.CITED_CACHE_PATH
        self._tmp = tempfile.TemporaryDirectory()
        AN.CITED_CACHE_PATH = Path(self._tmp.name) / "cited_links.json"
        self.calls = []

        def stub(keyword, size=6, **kw):
            self.calls.append(keyword)
            # 返回的就是被点名的那一份（标题与 title 字面相同 → identity="same"），
            # 这样才会触发"定位成功 + 官方域 → 落缓存"这一路径。
            return {"results": [{"title": keyword,
                                 "url": OFFICIAL,
                                 "document_number": "财税〔2012〕75号"}]}
        AN.FGK.search_fgk = stub

    def tearDown(self):
        AN.FGK.search_fgk = self._saved_get
        AN.CITED_CACHE_PATH = self._saved_path
        self._tmp.cleanup()

    def _title(self):
        # 标题字面即点名文件：doc_number_of 能从标题尾部抽出文号
        return "财税〔2012〕75号 鲜活肉蛋产品免征增值税"

    def test_cold_then_hot_cache_skips_search(self):
        title = self._title()
        dn = TT.doc_number_of(title)
        self.assertTrue(dn, "用例标题应能被 doc_number_of 归出文号")

        rows1, _ = AN.locate_cited_document(title)
        self.assertTrue(rows1 and rows1[0].get("_cited_identity") == "same", rows1)
        self.assertFalse(rows1[0].get("_from_cache"))
        first_call_count = len(self.calls)
        self.assertGreater(first_call_count, 0, "冷启动却没打检索")

        # 现在缓存里应有这枚文号 → 官方链接
        self.assertEqual(OFFICIAL, CITED.get_cited_link(dn, AN.CITED_CACHE_PATH))

        rows2, tried2 = AN.locate_cited_document(title)
        self.assertEqual(len(self.calls), first_call_count, "命中缓存仍打了检索")
        self.assertTrue(rows2[0]["_from_cache"])
        self.assertEqual(OFFICIAL, rows2[0]["url"])
        self.assertEqual([dn], tried2)

    def test_non_official_result_not_cached(self):
        # 让检索回一条非官方域：定位成功但不落缓存，下次仍走检索
        def stub(keyword, size=6, **kw):
            self.calls.append(keyword)
            return {"results": [{"title": "鲜活肉蛋产品免征增值税",
                                 "url": "https://example.com/x",
                                 "document_number": "财税〔2012〕75号"}]}
        AN.FGK.search_fgk = stub
        title = self._title()
        rows, _ = AN.locate_cited_document(title)
        self.assertTrue(rows)   # 定位本身不受域名影响
        dn = TT.doc_number_of(title)
        self.assertEqual("", CITED.get_cited_link(dn, AN.CITED_CACHE_PATH),
                         "非官方域链接竟被写进了缓存")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
