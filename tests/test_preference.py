#!/usr/bin/env python3
"""tax-preference 子技能 preference.py 的离线用例。不联网、不读真实索引。

覆盖纯逻辑：文号/标题抽取、AND 打分、状态与税种过滤、build_index 解析。
build_index 用一个临时合成 xlsx 打样本，不碰官网、不覆盖真实 preference_index.json。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREF_DIR = ROOT / "subskills" / "tax-preference"
sys.path.insert(0, str(PREF_DIR))

import preference as P  # noqa: E402


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


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
