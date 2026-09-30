#!/usr/bin/env python3
"""judge_validity 对"时效性分类状态"枚举的判据用例（离线）。

钉住 #2 的核心：时效性录入的整串枚举（全文有效/全文废止/已修改/部分失效/
尚未生效）必须按语义判，不能被脆弱的子串带偏。回归重点是"部分失效"——
它含子串"失效"，改造前会被误判成全文废止（repealed），把仍在使用的文件
压到不可引用；改造后应判 effective，且 note 点明"仅部分条款已失效"。

每条断言都可被证伪：把某分支改坏（如删掉 partial 前置分支）会让对应用例转红。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_evidence as E  # noqa: E402


class TestEnumClassification(unittest.TestCase):
    """无观察时点时，只按状态枚举判。"""

    def test_fulltext_effective(self):
        self.assertEqual(E.judge_validity({"status": "全文有效"})["validity"],
                         "effective")

    def test_fulltext_repealed(self):
        self.assertEqual(E.judge_validity({"status": "全文废止"})["validity"],
                         "repealed")
        self.assertEqual(E.judge_validity({"status": "已废止"})["validity"],
                         "repealed")

    def test_amended_is_effective(self):
        r = E.judge_validity({"status": "已修改"})
        self.assertEqual(r["validity"], "effective")
        self.assertIn("修改", r["note"])

    def test_pending(self):
        self.assertEqual(E.judge_validity({"status": "尚未生效"})["validity"],
                         "pending")

    def test_partial_lapse_is_not_repealed(self):
        """回归锁：部分失效/废止/无效 含子串"失效/废止"，必须判 effective。"""
        for term in ("部分失效", "部分废止", "部分无效"):
            r = E.judge_validity({"status": term})
            self.assertEqual(r["validity"], "effective", msg=term)
            self.assertNotEqual(r["validity"], "repealed", msg=term)
            self.assertIn("部分条款", r["note"], msg=term)

    def test_bare_lapse_still_repealed(self):
        """不能因给"部分失效"让路就把全文"失效/废止"也误放行。"""
        self.assertEqual(E.judge_validity({"status": "失效"})["validity"],
                         "repealed")


class TestWithObservationDate(unittest.TestCase):
    AT = "2026-09-30"

    def test_partial_with_date_still_effective(self):
        r = E.judge_validity({"status": "部分失效"}, at=self.AT)
        self.assertEqual(r["validity"], "effective")
        self.assertIn("部分条款", r["note"])

    def test_pending_future_date_blocks_effective(self):
        # 尚未生效 + 施行日期晚于观察时点 → 仍 pending，不能倒向 effective。
        r = E.judge_validity({"status": "尚未生效", "effective_date": "2027-01-01"},
                             at=self.AT)
        self.assertEqual(r["validity"], "pending")

    def test_pending_past_date_promotes_effective(self):
        r = E.judge_validity({"status": "尚未生效", "effective_date": "2026-01-01"},
                             at=self.AT)
        self.assertEqual(r["validity"], "effective")


class TestCorroboratedFallback(unittest.TestCase):
    def test_cited_by_rescues_unknown(self):
        r = E.judge_validity({"status": "", "corroborated_by": "税收征管法"})
        self.assertEqual(r["validity"], "effective")
        self.assertIn("制定依据", r["note"])

    def test_repealed_ignores_cited_by(self):
        # 明文废止优先于援引证据，不能被 corroborated_by 拉回在效。
        r = E.judge_validity({"status": "全文废止", "corroborated_by": "某法"})
        self.assertEqual(r["validity"], "repealed")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
