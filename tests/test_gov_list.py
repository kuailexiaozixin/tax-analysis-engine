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

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class TestMissingCited(unittest.TestCase):
    """missing_cited 待补工作清单：有官方 url 却未进缓存的文号才列出（#3）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "cited_links.json"
        self.index = {"记录": [
            {"发文字号": "国家税务总局公告2026年第19号", "title": "A",
             "url": "http://www.chinatax.gov.cn/zcfgk/19.html"},
            {"发文字号": "财税〔2023〕12号", "title": "B",
             "url": "https://fgk.chinatax.gov.cn/12.html"},
            {"发文字号": "财政部 税务总局公告2023年第5号", "title": "C",
             "url": "https://www.chinatax.gov.cn/5.html"},
            {"发文字号": "某文〔2020〕1号", "title": "D",
             "url": "https://blog.example.com/fake"},         # 非官方域，不列
            {"发文字号": "", "title": "E",
             "url": "http://www.chinatax.gov.cn/nodoc"},      # 空文号，不列
        ]}

    def tearDown(self):
        self._tmp.cleanup()

    def test_lists_uncached_official_only(self):
        import tax_cited as CITED
        import tax_terms as TT
        # 先把 19 号种进缓存，它就不该出现在待补清单里
        CITED.put_cited_link(TT.doc_number_of("国家税务总局公告2026年第19号"),
                             "http://www.chinatax.gov.cn/zcfgk/19.html", self.cache)
        dns = {r["文号"] for r in GL.missing_cited(self.cache, index=self.index)}
        self.assertEqual({"财税〔2023〕12号", "2023年第5号"}, dns)
        self.assertNotIn("2026年第19号", dns)     # 已缓存 → 不列

    def test_limit_caps_result(self):
        rows = GL.missing_cited(self.cache, index=self.index, limit=1)
        self.assertEqual(1, len(rows))


class TestBuildIndexCoverage(unittest.TestCase):
    """build_index 建库覆盖率自检：缺文号/时效性的行要计进索引（缺陷2 的对称自检）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_root, self._saved_idx = GL.DATA_ROOT, GL.INDEX_PATH
        root = Path(self._tmp.name)
        GL.DATA_ROOT = root
        GL.INDEX_PATH = root / "gov_list_index.json"

    def tearDown(self):
        GL.DATA_ROOT, GL.INDEX_PATH = self._saved_root, self._saved_idx
        self._tmp.cleanup()

    def test_counts_missing_docno_and_aging(self):
        rows = [
            _row("A", "http://t/A", aging="全文有效", doc="国家税务总局公告2026年第1号"),
            _row("B", "http://t/B", aging="", doc="财税〔2023〕2号"),          # 缺时效性
            _row("C", "http://t/C", aging="全文有效", doc=""),                 # 缺文号
        ]
        idx = GL.build_index(rows, channel="税务规范性文件")
        self.assertEqual(3, idx["条目数"])
        self.assertEqual(1, idx["发文字号缺失"])     # 只有 C 无文号
        self.assertEqual(1, idx["时效性缺失"])       # 只有 B 无时效性
        # 落盘一致
        disk = json.loads(GL.INDEX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(1, disk["发文字号缺失"])

    def test_healthy_index_reports_zero_missing(self):
        """全填的好索引不该误报（防计数恒正的假信号）。"""
        rows = [_row("A", "http://t/A", aging="全文有效", doc="财税〔2012〕75号")]
        idx = GL.build_index(rows, channel="税务规范性文件")
        self.assertEqual(0, idx["发文字号缺失"])
        self.assertEqual(0, idx["时效性缺失"])

    def test_placeholder_aging_counts_as_missing(self):
        """字符串 "null" 要归成空并计入缺失。

        2026-10-04 实测「财税文件」栏第 1/3/5 页共 150 条：138 条空串、12 条
        写成 "null"。少这一步就把这 12 条当"官方标了时效性"，缺失计数随之少报，
        stats 的分布里还会多出一档叫 null 的取值。
        """
        def raw(aging):
            return {"title": "T", "url": "http://t/1", "channelName": "财税文件",
                    "domainMetaList": [{"resultList": [
                        {"key": "aging", "value": aging, "name": "时效性"}]}]}
        for placeholder in ("null", "NULL", "", "-"):
            self.assertEqual("", GL.normalize_item(raw(placeholder))["时效性"],
                             f"占位串 {placeholder!r} 没归成空")
        self.assertEqual("全文有效", GL.normalize_item(raw("全文有效"))["时效性"])
        rows = [_row("A", "http://t/A", aging=""), _row("B", "http://t/B", aging="全文有效")]
        idx = GL.build_index(rows, channel="财税文件")
        self.assertEqual(1, idx["时效性缺失"])


class TestChannelRegistry(unittest.TestCase):
    """栏目登记表：加一栏要同时过这三道，缺一栏就会静默取空或判成 unknown。

    钉住 2026-10-04 收「国务院文件」「税务部门规章」两栏时的验收，以及把
    「其他」改名为接口自报的「其他文件」这件事。
    """

    def test_channel_ids_are_distinct_hex32(self):
        # 抄错一位、两栏粘成同一个 id，都在这里报红——那种错表现为整栏取空，
        # 而 sync 只说"0 条"，不会指向 id。
        for name, cid in GL.CHANNELS.items():
            self.assertRegex(cid, r"^[0-9a-f]{32}$", f"{name} 的 channelId 形态不对")
        self.assertEqual(len(GL.CHANNELS), len(set(GL.CHANNELS.values())),
                         "两栏共用了同一个 channelId")
        self.assertIn(GL.DEFAULT_CHANNEL, GL.CHANNELS)

    def test_channel_keys_are_the_api_own_names_and_facet_names(self):
        """键名 == 接口 channelName == 检索面效力等级名，三处只记一个词。

        --channel 走 CHANNELS.get(name, name)，名字对不上时它会把中文栏名当
        channelId 发出去；--aging/界面筛选那一路用的是 EFFECT_LEVEL_VALUES。
        """
        from tax_web_search import EFFECT_LEVEL_VALUES
        for name in GL.CHANNELS:
            self.assertIn(name, EFFECT_LEVEL_VALUES,
                          f"栏目「{name}」与检索面效力等级名不同名，两套词会各说各话")

    def test_measured_aging_values_all_judge_known(self):
        """七栏实测出现的五种时效性逐个过 judge_validity，一个都不落 unknown。

        取值分布是 2026-10-04 本机逐栏翻到底量的整栏数（不是抽样）：
        规范性文件 1925 = 808/745/347/23/2（五种全出现），规章 86 = 46/18/22，
        法律 75 = 69/3/2/1，行政法规 65 = 43/21/1，国务院文件 35 = 33/1/1；
        财税文件 1532 与其他文件 488 整栏不填。原始分布见 CHANNELS 注释。
        """
        from tax_evidence import judge_validity
        from tax_web_search import AGING_VALUES
        expected = {"全文有效": "effective", "已修改": "effective",
                    "全文废止": "repealed", "全文失效": "repealed",
                    "尚未生效": "pending"}
        self.assertEqual(set(expected), set(AGING_VALUES),
                         "清单实测取值与检索面取值域已经漂移")
        for aging, want in expected.items():
            got = judge_validity({"status": aging})["validity"]
            self.assertEqual(want, got, f"「{aging}」判成 {got}，期望 {want}")


class TestChannelSwitch(unittest.TestCase):
    """切栏目一定要把索引换过去，不能被"集合未变"留在上一栏。

    索引只有一份（INDEX_PATH 单文件），而 ListSynchronizer 按栏位各自存快照：
    2026-10-04 实测先 sync --channel 税务部门规章、再 sync --channel 行政法规，
    后者报"无更新（集合未变）"跳过构建，磁盘上仍是规章栏 86 条，而 stats 读的
    就是这一份。所以 sync 先看索引落在哪一栏，与请求不符就转 force。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = GL.DATA_ROOT, GL.INDEX_PATH
        root = Path(self._tmp.name)
        GL.DATA_ROOT = root
        GL.INDEX_PATH = root / "gov_list_index.json"

    def tearDown(self):
        GL.DATA_ROOT, GL.INDEX_PATH = self._saved
        self._tmp.cleanup()

    def _run_sync(self, index_channel, want_channel):
        """磁盘上放着 index_channel 的索引，请求 want_channel，返回传给同步器的 force。"""
        GL.build_index([_row("占位", "http://t/1")], channel=index_channel)
        seen = {}

        class _Syn:
            source = "chinatax-list"

            def sync(self, check_only=False, force=False):
                seen["force"] = force
                return {"成功": True, "动作": "无更新（集合未变）",
                        "本地条目数": 1, "条目数": 1}

        with mock.patch.object(GL, "_synchronizer", return_value=_Syn()):
            GL.sync(channel=want_channel)
        return seen.get("force")

    def test_stale_index_from_other_channel_forces_rebuild(self):
        self.assertTrue(self._run_sync("税务规范性文件", "行政法规"),
                        "索引还落在别的栏目，却没转成 force——stats 会读错栏")

    def test_same_channel_does_not_force(self):
        self.assertFalse(self._run_sync("行政法规", "行政法规"),
                        "同一栏目本可靠快照跳过重建，不该白白重爬")


class TestLookupFilterDomain(unittest.TestCase):
    """离线 lookup 的 --aging 是精确等值比对，域外值与"库里没有"必须分开。

    写成 "有效" 或带空格的 "全文 有效" 时，筛出来是 0 条，而 0 条读起来像
    "这一栏没有现行有效的文件"。与检索面 build_filters 同一口径：域外值在取数前报错。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = GL.DATA_ROOT, GL.INDEX_PATH
        root = Path(self._tmp.name)
        GL.DATA_ROOT = root
        GL.INDEX_PATH = root / "gov_list_index.json"
        GL.build_index([_row("增值税公告", "http://t/A", aging="全文有效"),
                        _row("废止的", "http://t/B", aging="全文废止")],
                       channel="税务规范性文件")

    def tearDown(self):
        GL.DATA_ROOT, GL.INDEX_PATH = self._saved
        self._tmp.cleanup()

    def test_out_of_domain_aging_is_rejected_before_filtering(self):
        with self.assertRaises(SystemExit) as ctx:
            GL.lookup(["增值税"], aging="有效")
        msg = str(ctx.exception)
        self.assertIn("取值域外", msg)
        for v in GL.AGING_VALUES:
            self.assertIn(v, msg, f"报错没把取值域列出来：{msg}")

    def test_in_domain_aging_with_no_hit_is_not_an_error(self):
        """域内值筛空是正常结果，不该报错——否则"库里没有"被说成用法错。"""
        self.assertEqual([], GL.lookup([], aging="尚未生效"))
        self.assertEqual(1, len(GL.lookup([], aging="全文废止")))


class TestChannelPages(unittest.TestCase):
    """栏目页映射：路径必须是数据，不能是模板拼接。

    2026-10-04 本机逐个 GET：六栏在 `<c码>/listflfg.html`，唯独「税务部门规章」
    在 `c100011/list.html`；按统一模板拼出来的 `c100011/listflfg.html` 回 404。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = GL.DATA_ROOT, GL.INDEX_PATH
        root = Path(self._tmp.name)
        GL.DATA_ROOT = root
        GL.INDEX_PATH = root / "gov_list_index.json"

    def tearDown(self):
        GL.DATA_ROOT, GL.INDEX_PATH = self._saved
        self._tmp.cleanup()

    def test_every_channel_has_exactly_one_page(self):
        self.assertEqual(set(GL.CHANNELS), set(GL.CHANNEL_PAGES),
                         "栏目与栏目页两登记表漏了一栏，stats 会印空")
        for name, url in GL.CHANNEL_PAGES.items():
            self.assertTrue(url.startswith("https://fgk.chinatax.gov.cn/zcfgk/"),
                            f"{name} 的栏目页不在政策法规库域下：{url}")

    def test_page_names_are_not_a_uniform_template(self):
        """规章那条的页面名与其余六栏不同——把它"统一化"就会指到 404 页。"""
        self.assertTrue(GL.CHANNEL_PAGES["税务部门规章"].endswith("/c100011/list.html"))
        for name, url in GL.CHANNEL_PAGES.items():
            if name != "税务部门规章":
                self.assertTrue(url.endswith("/listflfg.html"), f"{name} 的页面名被改错了")

    def test_build_index_records_the_page_and_survives_unknown_channel(self):
        idx = GL.build_index([_row("A", "http://t/A")], channel="法律")
        self.assertEqual("https://fgk.chinatax.gov.cn/zcfgk/c100009/listflfg.html",
                         idx["栏目页"])
        other = GL.build_index([_row("B", "http://t/B")], channel="没登记过的栏目")
        self.assertEqual("", other["栏目页"], "未登记的栏目不该拼出一个 URL")

    def test_stats_says_the_old_index_has_no_page_instead_of_a_blank(self):
        """改版前建的索引没有 `栏目页` 这一格：要写明"重跑 sync 即带出"，不印空串。"""
        idx = GL.build_index([_row("A", "http://t/A")], channel="法律")
        del idx["栏目页"]
        GL.INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GL.stats()
        out = buf.getvalue()
        self.assertIn("旧索引未录", out)
        self.assertNotIn("栏目页：\n", out, "印成空串会被读成这一栏没有官方页")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
