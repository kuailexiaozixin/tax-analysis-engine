#!/usr/bin/env python3
"""eval_answer.py 判分口径的离线用例（不联网、不调模型）。

为什么单独成一个离线文件而不是挂在 test_tax_search.py 的联网 e2e 里：这两组
守卫判分口径本身，一次网络都不需要，放进联网组就意味着默认门禁不跑它们，
而联网组随时可能因为外部站点限流整组转红——那时它们既不会被跑到，也不会被
想起。判分口径是每次改评测都要过的关，必须在离线组里。

两组守卫：
  输出格式那一根轴   三档覆盖、分母口径、拒答拆两档、缺记录不报 0%
  PROMPT 版本号       改作答要求必须让既有模型缓存失配，同版本必须仍命中

每条断言都可证伪：把对应实现改坏会让该条转红（本轮逐环节验过八种变异，
全部报红）。
"""

import contextlib
import io
import os
import re
import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import eval_answer as X  # noqa: E402

DIAG = {"plan_type": "treatment", "parent_law": "中华人民共和国契税法",
        "primary_title": "中华人民共和国契税法", "primary_rank": "法律",
        "primary_validity": "现行有效", "evidence_n": 3, "statutory_n": 2,
        "gap": "", "conditions": [], "body_titles": [], "bundle_chars": 400}

# 五种真实输出形态，逐字取自 parse_choice 已有用例，不另造口径
RAWS = [
    ('{"answer":"A","basis":"契税法","reasoning":"r"}', "合规且答对"),
    ('{"answer":"","basis":"无","reasoning":"前提不足"}', "合规且主动留空"),
    ('经比对各选项，答案：A', "不成 JSON，正则回捞到字母"),
    ('这题涉及 A 与 C 两项，我倾向于都选', "不成 JSON，也没拿到字母"),
    ('{"_error":"调用失败：上游超时"}', "链路故障"),
]


def make_rows(strip_parse=False):
    """造出与 run_one 产出同形状的行：报告段要读的字段一个都不能缺。

    strip_parse 只抹掉存档里的 parse 一档，判分仍按真实解析结果算——否则调用
    失败那行会因为认不出 `parse` 而混进分母，测的就不是"缺记录"而是"缺判分"了。
    """
    rows = []
    for i, (raw, _why) in enumerate(RAWS):
        parsed = X.parse_choice(raw)
        score = X.score_one({"answer": "A", "answer_type": "single"}, parsed)
        model = ({k: v for k, v in parsed.items() if k != "parse"}
                 if strip_parse else parsed)
        rows.append({
            "key": f"k{i}", "id": f"k{i}", "arm": "evidence", "source": "s",
            "subset": "", "validity": "ok", "question": "问",
            "options": {"A": "甲"}, "answer_type": "single",
            "score": score, "model": model, "diag": DIAG, "health": "on_topic",
            "from_cache": False, "raw_head": raw[:60]})
    return rows


class TestOutputFormatAxis(unittest.TestCase):
    """输出格式与"答得对不对"正交，必须单报且分母口径独立。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = make_rows()
        cls.a = X.agg(cls.rows)

    def test_three_buckets_cover_every_non_failed_row(self):
        # 少一档就有输出无处记账
        self.assertEqual(self.a["call_failed"], 1)
        self.assertEqual(self.a["n"], 4)
        self.assertEqual([self.a["parse_json"], self.a["parse_regex"],
                          self.a["parse_none"]], [2, 1, 1], self.a)
        self.assertEqual(self.a["parse_json"] + self.a["parse_regex"]
                         + self.a["parse_none"], self.a["n"])

    def test_call_failures_stay_out_of_the_format_denominator(self):
        # 把调用失败算进合规率，上游没钱会伪装成"模型不守格式"
        self.assertEqual(self.a["format_rate"], 50.0, self.a)
        self.assertEqual(self.a["parseable_rate"], 75.0, self.a)

    def test_refused_splits_into_rule_based_and_unparsable(self):
        # 输出不成 JSON 会被判分记成拒答；两者的修法相反，不许混在一格
        self.assertEqual(self.a["refused"], 2, self.a)
        self.assertEqual(self.a["refused_rule"], 1, self.a)
        self.assertEqual(self.a["refused_unparsed"], 1, self.a)
        self.assertEqual(self.a["refused"],
                         self.a["refused_rule"] + self.a["refused_unparsed"])

    def test_format_axis_does_not_move_the_score(self):
        # 加轴不改判分口径：答对仍是 2/4
        self.assertEqual(self.a["exact"], 2, self.a)
        self.assertEqual(self.a["rate"], 50.0, self.a)

    def test_batch_without_parse_record_reports_no_record(self):
        # 0% 是一个断言（这批全不合规），而真相是这批数据没有这个字段
        b = X.agg(make_rows(strip_parse=True))
        self.assertIsNone(b["format_rate"], b)
        self.assertIsNone(b["parseable_rate"], b)
        self.assertEqual(b["untagged"], b["n"], b)
        # 没有记录时宁可不拆，也不许凭 score 反推出两档
        self.assertEqual((b["refused"], b["refused_rule"], b["refused_unparsed"]),
                         (2, 0, 0), b)

    def test_report_says_no_record_instead_of_zero_percent(self):
        # 断言要盯住"输出格式"那一行本身：同一句提醒文字在缺行警告里也会出现，
        # 只比字符串在不在整份输出里，会被走错分支的实现蒙过去
        rows = make_rows(strip_parse=True)
        buf = io.StringIO()
        args = types.SimpleNamespace(validity="ok", at="2026-10-03",
                                     with_body=False)
        with contextlib.redirect_stdout(buf):
            X.print_report(rows, args)
        out = buf.getvalue()
        fmt = [ln for ln in out.splitlines() if ln.lstrip().startswith("输出格式")]
        self.assertEqual(len(fmt), 1, out)
        self.assertIn("不报合规率", fmt[0])
        self.assertNotIn("合规率 0.0%", fmt[0])
        # 合规率后面必须不跟数字：跟了就是凭缺字段编出一个比率
        self.assertIsNone(re.search(r"合规率\s*[\d.]+%?", fmt[0]), fmt[0])
        self.assertNotIn("格式 0.0%", out)


class TestPromptVersionGuard(unittest.TestCase):
    """改 PROMPT 必须让既有模型缓存判为未命中，同版本必须仍命中。"""

    ITEM = {"key": "k1", "id": "k1", "question": "问", "options": {"A": "x"},
            "answer": "A", "answer_type": "single", "source": "s",
            "subset": "", "validity": "ok"}

    def _cache(self, fp):
        return {("k1", "blind"): {"key": "k1", "arm": "blind", "fp": fp,
                                  "raw": '{"answer":"A","basis":"b","reasoning":"r"}'}}

    def test_same_version_still_hits(self):
        # 反方向不成立的话，这道守卫就变成了每次全量重问的烧钱开关
        fp = X.cache_fingerprint(self.ITEM, "")
        self.assertTrue(X.cached_raw(self.ITEM, "blind", None, self._cache(fp)))

    def test_bumped_version_invalidates_the_hit(self):
        fp = X.cache_fingerprint(self.ITEM, "")
        saved = X.PROMPT_VERSION
        X.PROMPT_VERSION = saved + "-改过作答要求"
        try:
            self.assertNotEqual(X.cache_fingerprint(self.ITEM, ""), fp)
            self.assertEqual(X.cached_raw(self.ITEM, "blind", None,
                                          self._cache(fp)), "")
        finally:
            X.PROMPT_VERSION = saved
        self.assertTrue(X.cached_raw(self.ITEM, "blind", None, self._cache(fp)))

    def test_written_record_carries_the_version(self):
        # 指纹不可逆，事后要能认出是哪版 prompt 产出了这条回答
        wrote = []
        real_append, real_ask = X.append_cache, X.ask_model
        try:
            X.append_cache = lambda rec: wrote.append(rec)
            X.ask_model = lambda *a, **k: '{"answer":"A","basis":"b","reasoning":"r"}'
            out = X.run_one(dict(self.ITEM), "blind", None, 5, {}, threading.Lock())
        finally:
            X.append_cache, X.ask_model = real_append, real_ask
        self.assertIsNotNone(out)
        self.assertEqual(len(wrote), 1, wrote)
        self.assertEqual(wrote[0].get("prompt_v"), X.PROMPT_VERSION)
        self.assertEqual(wrote[0]["fp"], X.cache_fingerprint(self.ITEM, ""))


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    os.environ.pop("TAX_ENABLE_PAID_LLM", None)   # 本文件一次模型都不该调
    sys.exit(unittest.main(verbosity=2))
