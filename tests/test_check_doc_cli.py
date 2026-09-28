#!/usr/bin/env python3
"""check_doc_cli.py 的规则自检。

为什么需要：那条"孤立参数片段"规则在文档**改对之后永远是绿的**——没有构造样例，
就无法证明它还在工作，将来它悄悄失效也没人知道。

实例：SKILL.md 曾写着"总局条目取正文用 `--source fgk --body`"，而 `--source` 属
tax_formatter.py、`--body` 属 tax_fgk.py，这条组合在任何脚本上都跑不通。规则加上后
当场报出，修完转绿。这里把"报得出"和"不误报"两边都钉住，避免改动规则时把能力改丢。

与 test_http_layer.py 里 `test_scanner_can_see_aliased_imports` 是同一种自检。

用法：
    python tests/test_check_doc_cli.py

退出码：全过 0，有失败 1。
"""

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import check_doc_cli as cdc          # noqa: E402  （要先垫好 sys.path）

# 假的"脚本 → 参数集合"映射：与真实仓库解耦，规则改了也不会因仓库变化而误红
FAKE_SCRIPTS = {
    "fgk.py": {"--body", "--pages", "--size"},
    "fmt.py": {"--source", "--mode", "--intent"},
}


class TestOrphanFlagGroups(unittest.TestCase):
    """片段级检查：孤立片段里的参数组合必须能落在某一个脚本上。"""

    def check(self, text):
        return cdc._check_orphan_flag_groups("DOC.md", text, FAKE_SCRIPTS)

    def test_cross_script_combo_is_reported(self):
        """两个参数分属不同脚本，凑不成一条命令——必须报。"""
        problems = self.check("总局条目用 `--source fgk --body` 取正文")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("--source", problems[0])
        self.assertIn("--body", problems[0])
        # 报错要说清归属，否则改的人还得自己去找
        self.assertIn("fgk.py", problems[0])
        self.assertIn("fmt.py", problems[0])

    def test_same_script_combo_passes(self):
        """同一脚本的两个参数——合法，不许报。"""
        self.assertEqual(self.check("用 `--body --pages 3` 取正文"), [])

    def test_single_flag_is_not_flagged(self):
        """单个参数没有归属歧义，笼统提一句是正常写法。"""
        self.assertEqual(self.check("`--verbose` 会打详细日志"), [])
        self.assertEqual(self.check("加 `--cache` 可开启缓存"), [])

    def test_segment_with_script_name_is_skipped(self):
        """带脚本名的片段归命令级规则管，这里不重复报。"""
        self.assertEqual(
            self.check("`python scripts/fgk.py --source fgk --body`"), [])

    def test_unknown_flags_are_reported(self):
        """两个都不属于任何脚本——同样跑不通，要报。"""
        problems = self.check("这一段写了 `--foo --bar`")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("没有任何脚本", problems[0])

    def test_line_number_is_correct(self):
        """报的行号要能对上，否则按提示去改会改错行。"""
        text = "第一行\n第二行\n有问题的 `--source x --pages 2`\n第四行\n"
        problems = self.check(text)
        self.assertEqual(len(problems), 1)
        self.assertIn("DOC.md:3", problems[0])

    def test_clean_document_reports_nothing(self):
        """整份文档没有坏片段时，必须一条都不报（这是它在真仓库里的常态）。"""
        text = "\n".join([
            "`--body --pages 3` 取正文",
            "`--source npc --mode single` 指定来源与模式",
            "`--verbose` 打日志",
            "`python scripts/fgk.py --size 20` 是命令级的事",
        ])
        self.assertEqual(self.check(text), [])


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    unittest.main(verbosity=2)
