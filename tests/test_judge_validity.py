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


class TestSelfStatedExpiry(unittest.TestCase):
    """正文自载执行期限的止日：抠得到就用来提醒，抠不准就不判。

    正向用例里的每一种引导词句式都逐字取自本机现拉的法规库正文（2023年第12号、
    2022年第4号、财税〔2011〕58号、2012年第12号），不自己编——编出来的句式大概
    率不是公告的写法，用例打不中真实文本。只有把几种真实句式拼在一起构造的组合
    场景（一段正文里出现两个不同止期、与制定依据援引同时成立）是按真实写法组装的。
    反向用例同样取自真实正文：`截至2021年9月30日成立满一年的企业` 里那个日期是
    统计基准日、不是执行期限的止日，所以引导词表里没有裸"截至"；2022年第14号
    "延长至每月最后一个工作日"则证明引导词后面不一定跟着日子。
    """

    AT = "2026-10-03"

    def test_marker_forms_all_extract(self):
        for body, want in (
            ("……按20%的税率缴纳企业所得税政策，延续执行至2027年12月31日。",
             "2027-12-31"),
            ("……中规定的税收优惠政策，执行期限延长至2023年12月31日。",
             "2023-12-31"),
            ("一、自2023年1月1日至2027年12月31日，对个体工商户年应纳税所得额"
             "不超过200万元的部分，减半征收个人所得税。", "2027-12-31"),
            ("自2011年1月1日至2020年12月31日，对……免征增值税。", "2020-12-31"),
        ):
            self.assertEqual(E.expiry_of({"body": body}), want, msg=body)

    def test_cutoff_and_signature_dates_are_not_expiry(self):
        # 落款单独日期没有引导词；"截至X日成立满一年"里的日期是统计基准日。
        self.assertEqual(E.expiry_of({"body": "财政部 税务总局公告2022年第4号\n"
                                              "2022年1月29日"}), "")
        self.assertEqual(E.expiry_of({"body": "（一）截至2021年9月30日成立满一年的"
                                              "企业，按照所属期为2021年的计算。"}), "")

    def test_verb_without_a_date_is_not_expiry(self):
        # 2022年第14号原文："留抵退税申请时间，延长至每月最后一个工作日"——
        # 引导词后面不是某个具体日子，不能当成止期。
        self.assertEqual(E.expiry_of({"body": "将2022年4月至6月的留抵退税申请时间，"
                                              "延长至每月最后一个工作日。"}), "")

    def test_two_different_endings_refuse_to_guess(self):
        # 一份公告把多项政策各自延到不同日期时，挑任何一个都是替用户猜。
        body = ("甲项政策执行至2023年12月31日；乙项政策执行期限延长至2025年12月31日。"
                "丙项政策自2021年1月1日至2024年12月31日免征。")
        self.assertEqual(E.expiry_of({"body": body}), "")

    def test_same_endings_repeated_still_count(self):
        # 同一止期在多条里重复出现不算歧义，去重后仍只有一个值。
        body = "一、……执行至2027年12月31日。二、……执行至2027年12月31日。"
        self.assertEqual(E.expiry_of({"body": body}), "2027-12-31")

    def test_impossible_month_day_rejected(self):
        self.assertEqual(E.expiry_of({"body": "执行至2027年13月40日"}), "")

    def test_zero_padding_keeps_string_compare_correct(self):
        # 补零是必须的："2027-1-5" 按字符串比会排在 "2027-12-31" 之后，判反方向。
        self.assertEqual(E.expiry_of({"body": "执行至2027年1月5日"}), "2027-01-05")
        r = E.judge_validity({"status": "全文有效", "body": "执行至2027年1月5日"},
                             at="2027-01-04")
        self.assertNotIn("止于", r["note"])
        r = E.judge_validity({"status": "全文有效", "body": "执行至2027年1月5日"},
                             at="2027-01-06")
        self.assertIn("止于 2027-01-05", r["note"])

    def test_entry_field_wins_over_body(self):
        r = E.expiry_of({"expiry_date": "2030-06-30", "body": "执行至2023年12月31日"})
        self.assertEqual(r, "2030-06-30")

    def test_past_expiry_warns_without_downgrading(self):
        """止期已过：只出提醒，不改 validity——判成 repealed 会把仍可能有效的件整条压掉。"""
        it = {"status": "全文有效", "body": "一、自2022年1月1日至2023年12月31日，减半征收。"}
        r = E.judge_validity(it, at=self.AT)
        self.assertEqual(r["validity"], "effective")
        self.assertTrue(r["qualified"])
        self.assertIn("止于 2023-12-31", r["note"])
        self.assertIn("先查这段期限之后有没有延续文件", r["note"])
        self.assertIn(r["note"], E.grade(it, at=self.AT)["caveats"])

    def test_future_expiry_is_notified_not_warned(self):
        it = {"status": "全文有效", "body": "延续执行至2027年12月31日。"}
        r = E.judge_validity(it, at=self.AT)
        self.assertIn("2027-12-31", r["note"])
        self.assertFalse(r["qualified"])
        self.assertNotIn(r["note"], E.grade(it, at=self.AT)["caveats"])

    def test_unknown_status_with_past_expiry_still_warns(self):
        # 财税文件那一栏根本不录时效，正文里的止期是唯一可得的到期证据。
        r = E.judge_validity({"status": "", "body": "执行期限延长至2023年12月31日。"},
                             at=self.AT)
        self.assertEqual(r["validity"], "unknown")
        self.assertTrue(r["qualified"])
        self.assertIn("早于观察时点", r["note"])

    def test_corroboration_survives_past_expiry(self):
        """制定依据援引与自载止期各说一件事，谁也不许把谁顶掉。

        援引说的是"这份文件整体还在效"，止期说的是"正文里那一段优惠期限已过"。
        早先的分支顺序让止期先返回，带正文的财税文件一被抠出止期就从 effective
        掉回 unknown，第 7 条补的那格白补。
        """
        it = {"status": "", "corroborated_by": "企业所得税法",
              "body": "一、自2021年1月1日至2023年12月31日，减征。"}
        r = E.judge_validity(it, at=self.AT)
        self.assertEqual(r["validity"], "effective")
        self.assertIn("制定依据", r["note"])
        self.assertIn("止于 2023-12-31", r["note"])
        self.assertTrue(r["qualified"])
        # "本条无时效录入"在拼接时只出现一次：止期那句单独成 note 才带判据来源的交代
        self.assertEqual(r["note"].count("本条无时效录入"), 1)
        self.assertIn(r["note"], E.grade(it, at=self.AT)["caveats"])

    def test_corroboration_with_future_expiry_keeps_date_in_note(self):
        it = {"status": "", "corroborated_by": "企业所得税法",
              "body": "延续执行至2027年12月31日。"}
        r = E.judge_validity(it, at=self.AT)
        self.assertEqual(r["validity"], "effective")
        self.assertIn("至 2027-12-31", r["note"])
        self.assertNotIn("止于", r["note"])

    def test_future_effective_date_outranks_past_expiry(self):
        # 施行日期在观察时点之后、正文又带一段已过去的期限：先按未生效判。
        # 一份还没开始施行的文件谈不上"这段期限已经过去"。
        r = E.judge_validity({"status": "全文有效", "effective_date": "2027-01-01",
                              "body": "执行至2023年12月31日。"}, at=self.AT)
        self.assertEqual(r["validity"], "pending")
        self.assertIn("生效日 2027-01-01 晚于观察时点", r["note"])
        self.assertNotIn("止于", r["note"])

    def test_repealed_not_double_warned(self):
        r = E.judge_validity({"status": "全文废止", "body": "执行至2020年12月31日。"},
                             at=self.AT)
        self.assertEqual(r["validity"], "repealed")
        self.assertNotIn("止于", r["note"])

    def test_no_body_leaves_other_branches_untouched(self):
        # 没取正文时（gather 的常用形态）这条机制必须完全静默，
        # 否则"没读到日期"会写成一条凭空的时效结论。
        r = E.judge_validity({"status": "全文有效"}, at=self.AT)
        self.assertEqual(r["validity"], "effective")
        self.assertNotIn("执行期限", r["note"])


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
