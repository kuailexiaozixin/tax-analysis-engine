#!/usr/bin/env python3
"""tax_sync.ListSynchronizer 与 tax_gov_list 规范化的离线用例。不联网。

ListSynchronizer 的分页抓取靠 fetch_page 回调，测试直接注入一个可控的假
fetch_page（从表里按页返回行、报 total），不打网络。tax_gov_list 的规范化靠
伪造 getFileListByCodeId 的响应体。缓存目录指到临时目录，不碰仓库 data/sync。

钉住 #75 的验收标准：
  1. crawl 按 total 停止，多页拼全、不重复不遗漏
  2. --check 只探第一页拿 total，不爬全、不构建
  3. total 变更在 --check 下报"发现总数变更"
  4. 集合未变 → "无更新（集合未变）"，不重建（首轮那次构建保留）
  5. 同一 url 只有时效性翻转 → 集合 SHA1 变 → 触发重建（sig_fields 的意义）
  6. total=0 / 空集合 → 抓为异常，不落索引、不构建
  7. 爬不满 total（fetch_page 每页都给行但 total 虚高）→ max_pages 截断报错
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tax_sync import ListSynchronizer, SyncError  # noqa: E402
import tax_gov_list as GL  # noqa: E402


def _row(title, url, aging="全文有效", doc=""):
    return {"title": title, "url": url, "时效性": aging, "发文字号": doc,
            "效力等级": "税务规范性文件", "成文日期": "2026-01-01",
            "published": "2026-01-01 00:00:00", "channel": "测试栏目",
            "税费类型": ""}


class _FakePages:
    """假 fetch_page：按 page(1-based) 从预置分页表返回，记录被请求的页号。"""

    def __init__(self, pages, total, *, keep_going=False):
        self.pages = pages            # {page_no: [rows]}
        self.total = total
        self.calls = []
        self.keep_going = keep_going  # True 时每页都给行（模拟 total 虚高、爬不到头）

    def __call__(self, page):
        self.calls.append(page)
        rows = self.pages.get(page, [])
        if not rows and self.keep_going:
            rows = [_row(f"填充{page}", f"http://t/{page}")]
        return {"rows": rows, "total": self.total}


class TestNormalize(unittest.TestCase):
    def test_reads_meta_by_key_across_groups(self):
        item = {
            "title": " 关于增值税的公告 ",
            "url": "http://www.chinatax.gov.cn/zcfgk/c100012/c5252176/content.html",
            "publishedTimeStr": "2026-09-04 00:00:00",
            "channelName": "税务规范性文件",
            "domainMetaList": [
                {"domainMetadataName": "默认元数据集", "resultList": [
                    {"name": "来源", "value": "SZfaguiku", "key": "source"},
                    {"name": "作者", "value": "", "key": "author"},           # 空值应忽略
                ]},
                {"domainMetadataName": "政策文件信息", "resultList": [
                    {"name": "时效性", "value": "尚未生效", "key": "aging"},
                    {"name": "发文字号", "value": "国家税务总局公告2026年第19号", "key": "writtentext"},
                    {"name": "效力等级", "value": "税务规范性文件", "key": "effectlevel"},
                    {"name": "成文日期", "value": "2026-09-04", "key": "writtendate"},
                    {"name": "税费类型", "value": "税收政策-增值税", "key": "taxpolicy"},
                ]},
            ],
        }
        rec = GL.normalize_item(item)
        self.assertEqual("关于增值税的公告", rec["title"])        # 去首尾空格
        self.assertEqual("尚未生效", rec["时效性"])
        self.assertEqual("国家税务总局公告2026年第19号", rec["发文字号"])
        self.assertEqual("税收政策-增值税", rec["税费类型"])
        self.assertEqual("2026-09-04", rec["成文日期"])
        # 空值字段映射进来后仍是空串，不是 None
        self.assertEqual("", rec.get("作者", ""))

    def test_response_to_rows_unwraps(self):
        payload = {"results": {"data": {
            "total": 42, "page": 1, "rows": 10,
            "results": [{"title": "T", "url": "http://x", "domainMetaList": []}]}}}
        out = GL._response_to_rows(payload)
        self.assertEqual(42, out["total"])
        self.assertEqual(1, len(out["rows"]))
        self.assertEqual("T", out["rows"][0]["title"])


class TestListSynchronizer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.built = []

    def tearDown(self):
        self._tmp.cleanup()

    def _syn(self, fetch_page, **kw):
        def build(rows):
            self.built.append(list(rows))
            return {"版本日期": "2026-01-01", "条目数": len(rows)}

        sig = kw.pop("sig_fields", ("url", "时效性", "发文字号", "title"))
        return ListSynchronizer("demo", fetch_page, build, sig_fields=sig,
                                page_size=kw.pop("page_size", 2),
                                data_root=self.root, **kw)

    def test_crawl_stops_at_total(self):
        pages = {1: [_row("A", "http://t/A"), _row("B", "http://t/B")],
                 2: [_row("C", "http://t/C")]}
        fp = _FakePages(pages, total=3)
        res = self._syn(fp).sync()
        self.assertEqual("已更新", res["动作"])
        self.assertEqual([1, 2], fp.calls)          # 爬到 total=3 即停，不多翻
        self.assertEqual(3, res["条目数"])

    def test_check_only_probes_first_page_no_build(self):
        pages = {1: [_row("A", "http://t/A"), _row("B", "http://t/B")],
                 2: [_row("C", "http://t/C")]}
        # 先真正同步一次，落 state.total=3
        self._syn(_FakePages(pages, 3)).sync()
        fp = _FakePages(pages, total=3)
        res = self._syn(fp).sync(check_only=True)
        self.assertEqual("无更新", res["动作"])
        self.assertEqual([1], fp.calls, "--check 却爬了多页")
        self.assertEqual(1, len(self.built), "--check 触发了构建")

    def test_check_detects_total_change(self):
        self._syn(_FakePages({1: [_row("A", "http://t/A")]}, 1)).sync()
        fp = _FakePages({1: [_row("A", "http://t/A"), _row("B", "http://t/B")]}, total=2)
        res = self._syn(fp).sync(check_only=True)
        self.assertEqual("发现总数变更（--check 未抓取）", res["动作"])
        self.assertTrue(res["有更新"])
        self.assertEqual([1], fp.calls)

    def test_unchanged_set_skips_rebuild(self):
        pages = {1: [_row("A", "http://t/A"), _row("B", "http://t/B")]}
        self._syn(_FakePages(pages, 2)).sync()          # 首轮构建 1 次
        res = self._syn(_FakePages(pages, 2)).sync()
        self.assertEqual("无更新（集合未变）", res["动作"])
        self.assertTrue(res["成功"])
        self.assertEqual(1, len(self.built), "集合未变仍重建")

    def test_aging_flip_same_url_triggers_rebuild(self):
        """sig_fields 含时效性：url 不变、只有 aging 从尚未生效翻成全文有效 → 重建。"""
        self._syn(_FakePages({1: [_row("A", "http://t/A", aging="尚未生效")]}, 1)).sync()
        first_builds = len(self.built)
        res = self._syn(_FakePages({1: [_row("A", "http://t/A", aging="全文有效")]}, 1)).sync()
        self.assertEqual("已更新", res["动作"])
        self.assertEqual(first_builds + 1, len(self.built), "时效性翻转未触发重建")

    def test_empty_set_is_rejected_no_build(self):
        res = self._syn(_FakePages({}, total=0)).sync()
        self.assertEqual("抓取异常", res["动作"])
        self.assertFalse(res["成功"])
        self.assertEqual([], self.built, "空集合却构建了")

    def test_truncation_raises_and_fails(self):
        # total 虚高到 10000，fetch_page 每页都给行 → 永远爬不满 → max_pages 截断
        fp = _FakePages({}, total=10000, keep_going=True)
        res = self._syn(fp, max_pages=3).sync()
        self.assertEqual("抓取失败", res["动作"])
        self.assertFalse(res["成功"])
        self.assertEqual([], self.built, "截断却构建了")


class TestSeedCitedCache(unittest.TestCase):
    """清单索引 → 文号缓存的离线预热（#1 落地：把 A 的链接缓存积累做成我们的批量种入）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "cited_links.json"
        self.index = {"记录": [
            {"发文字号": "国家税务总局公告2026年第19号",
             "url": "http://www.chinatax.gov.cn/zcfgk/c100012/c5252176/content.html"},
            {"发文字号": "财税〔2023〕12号",
             "url": "https://fgk.chinatax.gov.cn/x.html"},
            {"发文字号": "某文〔2020〕1号",
             "url": "https://blog.example.com/fake"},         # 非官方域，必须拒
            {"发文字号": "", "url": "http://www.chinatax.gov.cn/x"},  # 无文号，跳过
        ]}

    def tearDown(self):
        self._tmp.cleanup()

    def test_seeds_official_and_skips_nonofficial(self):
        import tax_terms as TT
        stat = GL.seed_cited_cache(self.cache, index=self.index)
        self.assertEqual(2, stat["写入"])
        self.assertEqual(1, stat["跳过非官方"])
        import tax_cited as CITED
        # 键按 doc_number_of 归一（去空格、去发文机关前缀），读取端 locate_cited_document
        # 用同一个归一函数——预热与消费必须共键，否则种进去也取不到。
        dn19 = TT.doc_number_of("国家税务总局公告2026年第19号")
        dn12 = TT.doc_number_of("财税〔2023〕12号")
        self.assertEqual("2026年第19号", dn19)     # 归一形态钉住，防漂移
        self.assertTrue(CITED.get_cited_link(dn19, self.cache))
        self.assertIn("chinatax.gov.cn", CITED.get_cited_link(dn12, self.cache))
        # 缓存里只有两枚官方文号：非官方域与空文号那条都没落进去
        self.assertEqual(2, len(CITED.load_cited_links(self.cache)))

    def test_only_missing_preserves_existing(self):
        import tax_cited as CITED
        import tax_terms as TT
        dn12 = TT.doc_number_of("财税〔2023〕12号")
        CITED.put_cited_link(dn12, "https://fgk.chinatax.gov.cn/OLD.html", self.cache)
        stat = GL.seed_cited_cache(self.cache, index=self.index, only_missing=True)
        # 已存在的文号不覆盖
        self.assertEqual("https://fgk.chinatax.gov.cn/OLD.html",
                         CITED.get_cited_link(dn12, self.cache))
        self.assertEqual(1, stat["已有"])
        self.assertEqual(1, stat["写入"])          # 只补 19号那枚


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
