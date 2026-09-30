#!/usr/bin/env python3
"""tax-preference 子技能 preference.py 的离线用例。不联网、不读真实索引。

覆盖纯逻辑：文号/标题抽取、AND 打分、状态与税种过滤、build_index 解析。
build_index 用一个临时合成 xlsx 打样本，不碰官网、不覆盖真实 preference_index.json。
"""

import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREF_DIR = ROOT / "subskills" / "tax-preference"
sys.path.insert(0, str(PREF_DIR))

import preference as P  # noqa: E402


class _FakeResp:
    def __init__(self, content: str):
        self.content = content.encode("utf-8")


class _FakeHttp:
    """假 tax_http.get：按 url 返回预置 HTML，fail 里的 url 抛异常。记录调用顺序。"""

    def __init__(self, pages, fail=()):
        self.pages = pages
        self.fail = set(fail)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        if url in self.fail:
            raise RuntimeError("页面不可达")
        return _FakeResp(self.pages.get(url, ""))


def _use_fake_http(fake):
    """把 fake 装进 sys.modules['tax_http']，返回还原用的原值（可能为 None）。"""
    saved = sys.modules.get("tax_http")
    sys.modules["tax_http"] = fake
    return saved


def _restore_http(saved):
    if saved is not None:
        sys.modules["tax_http"] = saved
    else:
        sys.modules.pop("tax_http", None)


class TestExtract(unittest.TestCase):
    def test_doc_no_variants(self):
        cases = {
            "《财政部 国家税务总局关于免征鲜活肉蛋增值税的通知》财税〔2012〕75号": "财税〔2012〕75号",
            "主席令第四十一号": "主席令第四十一号",
            "国家税务总局公告2011年第48号": "国家税务总局公告2011年第48号",
        }
        for src, want in cases.items():
            self.assertIn(want.replace(" ", ""), P.extract_doc_no(src).replace(" ", ""))

    def test_title_from_bookmarks(self):
        self.assertEqual(P.extract_title("财政部关于印发《免征目录》的通知"), "免征目录")


class TestScore(unittest.TestCase):
    def test_and_semantics_excludes_partial(self):
        rec = {"代码": "01010503", "文件标题": "鲜活肉蛋产品免征增值税",
               "收入种类": "增值税", "状态": "有效"}
        # 两个词都在同一条里 → 命中
        self.assertGreater(P._score(rec, ["鲜活", "增值税"]), 0)
        # 掺一个不存在的词 → AND 落空为 0
        self.assertEqual(0, P._score(rec, ["鲜活", "不存在词"]))

    def test_status_bonus(self):
        eff = {"文件标题": "研发费用", "状态": "有效"}
        inv = {"文件标题": "研发费用", "状态": "失效"}
        self.assertGreater(P._score(eff, ["研发费用"]), P._score(inv, ["研发费用"]))


class TestBuildAndQuery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = P.INDEX_PATH
        P.INDEX_PATH = Path(self._tmp.name) / "preference_index.json"

    def tearDown(self):
        P.INDEX_PATH = self._saved
        self._tmp.cleanup()

    def _make_xlsx(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws_eff = wb.active
        ws_eff.title = "现行有效减免税政策"
        head = ["序号", "收入种类", "减免政策大类", "减免政策小类", "减免性质代码",
                "政策名称", "有效期起", "有效期止", "优惠条款", "减免项目名称"]
        ws_eff.append(head)
        ws_eff.append([1, "增值税", "改善民生", "提高居民收入", "01010503",
                       "《财政部 国家税务总局关于免征部分鲜活肉蛋产品流通环节增值税政策的通知》财税〔2012〕75号",
                       "", "", "第一条", "鲜活肉蛋产品免征增值税优惠"])
        ws_inv = wb.create_sheet("已失效减免税政策")
        ws_inv.append(head)
        ws_inv.append([2, "企业所得税", "鼓励高新技术", "科技发展", "04010001",
                       "《某项已废止优惠的通知》国税发〔2010〕999号",
                       "2010/01/01", "2020/12/31", "第二条", "已废止优惠"])
        # 版本日期塞在第一格上方的表头行——放到某张 sheet 首行第一格
        path = Path(self._tmp.name) / "减免税政策代码目录（2026年9月3日）.xlsx"
        wb.save(path)
        return str(path)

    def test_build_then_query_offline(self):
        idx = P.build_index(self._make_xlsx())
        self.assertEqual(1, idx["有效条数"])
        self.assertEqual(1, idx["失效条数"])
        self.assertEqual("2026-09-03", idx["版本日期"])   # 从文件名解析
        # 两行都带规范文号 → 抽取失败计数为 0（好索引不误报）
        self.assertEqual(0, idx["文号抽取失败"])
        self.assertEqual(2, idx["总条数"])

        # 按代码精确反查
        out = P.query([], code="01010503", as_json=True)
        self.assertEqual(1, len(out))
        self.assertEqual("财税〔2012〕75号", out[0][0]["文号"])

        # 关键词 AND
        out = P.query(["鲜活", "肉蛋"], as_json=True)
        self.assertEqual(1, len(out))
        out = P.query(["鲜活", "查无此词"], as_json=True)
        self.assertEqual(0, len(out))

        # 状态过滤：失效条目带终止日期
        out = P.query(["已废止"], status="失效", as_json=True)
        self.assertEqual(1, len(out))
        self.assertEqual("2020/12/31", out[0][0]["有效期止"])

        # 税种过滤
        out = P.query([], type_="增值税", status="有效", as_json=True)
        self.assertEqual(1, len(out))

    def test_query_shows_cached_official_link(self):
        import tax_cited as CITED
        # 目录里的文号写全称"国家税务总局公告2011年第48号"，缓存键却是归一后的
        # "2011年第48号"。_cited_url 必须先 doc_number_of 再查，否则用全称取不到。
        self._write_index({"状态": "有效", "代码": "04010048", "文件标题": "某公告",
                           "文号": "国家税务总局公告2011年第48号",
                           "收入种类": "企业所得税", "大类": "", "小类": ""})
        cache = Path(self._tmp.name) / "cited.json"
        CITED.put_cited_link("2011年第48号",
                             "https://www.chinatax.gov.cn/a48.html", cache)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P.query([], code="04010048", cited_path=cache)
        self.assertIn("官方链接：https://www.chinatax.gov.cn/a48.html", buf.getvalue())

    def test_query_marks_uncached_link(self):
        self._write_index({"状态": "有效", "代码": "04010048", "文件标题": "某公告",
                           "文号": "国家税务总局公告2011年第48号",
                           "收入种类": "企业所得税", "大类": "", "小类": ""})
        cache = Path(self._tmp.name) / "empty.json"      # 空缓存
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P.query([], code="04010048", cited_path=cache)
        self.assertIn("官方链接：<未缓存", buf.getvalue())

    def _make_xlsx_missing_doc(self):
        """有效栏造三行：两行带规范文号、一行的政策名称里根本没有文号形态。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "现行有效减免税政策"
        head = ["序号", "收入种类", "减免政策大类", "减免政策小类", "减免性质代码",
                "政策名称", "有效期起", "有效期止", "优惠条款", "减免项目名称"]
        ws.append(head)
        ws.append([1, "增值税", "a", "b", "01010503",
                   "《免征鲜活肉蛋增值税的通知》财税〔2012〕75号", "", "", "第一条", "肉蛋"])
        ws.append([2, "增值税", "a", "b", "01010504",
                   "《支持小微企业的通知》国家税务总局公告2023年第1号", "", "", "第二条", "小微"])
        # 政策名称不含任何"〔年〕号/公告X年第Y号/令X号"形态 → 文号抽不到
        ws.append([3, "增值税", "a", "b", "01010505",
                   "享受即征即退的一般纳税人名单（附件另发）", "", "", "第三条", "即征即退"])
        path = Path(self._tmp.name) / "减免税政策代码目录（2026年9月3日）.xlsx"
        wb.save(path)
        return str(path)

    def test_build_index_counts_missing_docno(self):
        idx = P.build_index(self._make_xlsx_missing_doc())
        self.assertEqual(3, idx["总条数"])
        self.assertEqual(1, idx["文号抽取失败"])   # 只有第三行抽不到文号

    def _write_index(self, rec):
        P.INDEX_PATH.write_text(json.dumps(
            {"版本日期": "2026-09-03", "记录": {rec["状态"]: [rec]}},
            ensure_ascii=False), encoding="utf-8")


class TestLocateFallback(unittest.TestCase):
    """locate() 的多入口回退：栏目页失败退首页，全失败才抛错（#81）。"""

    ANCHOR_HTML = ('<a href="/zhengce/减免税政策代码目录（2026年9月3日）.xlsx">'
                   '减免税政策代码目录</a>')

    def test_column_page_hit_no_second_call(self):
        fake = _FakeHttp({P.COLUMN_PAGE: self.ANCHOR_HTML})
        saved = _use_fake_http(fake)
        try:
            r = P.locate()
        finally:
            _restore_http(saved)
        self.assertEqual([P.COLUMN_PAGE], fake.calls)
        self.assertTrue(r["url"].endswith(".xlsx"))
        self.assertIn("chinatax.gov.cn", r["url"])

    def test_falls_back_to_homepage(self):
        fake = _FakeHttp({P.HOME_PAGE: self.ANCHOR_HTML}, fail=[P.COLUMN_PAGE])
        saved = _use_fake_http(fake)
        try:
            r = P.locate()
        finally:
            _restore_http(saved)
        self.assertEqual([P.COLUMN_PAGE, P.HOME_PAGE], fake.calls)
        self.assertTrue(r["url"].endswith(".xlsx"))

    def test_raises_when_all_fail(self):
        fake = _FakeHttp({}, fail=[P.COLUMN_PAGE, P.HOME_PAGE])
        saved = _use_fake_http(fake)
        try:
            with self.assertRaises(RuntimeError):
                P.locate()
        finally:
            _restore_http(saved)
        self.assertEqual([P.COLUMN_PAGE, P.HOME_PAGE], fake.calls)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
