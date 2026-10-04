#!/usr/bin/env python3
"""前端「时效」控件的契约用例。不联网、不开浏览器。

背景：时效这一栏在两条链路上量的是同一根轴，值域却不通用——NPC 库用数字码
（3=现行有效／2=已修改…），search5 那一侧用自己的时效维度（`xxgkAging`，五个
文本值）。把控件对后者置灰是错的：控件空转不等于维度不存在。
本文件验的是**按数据源换值域**：NPC／聚合发数字 status，税务总局／法规库发文本 aging，
并且后者默认「全部」而不是「现行有效」（白名单基数 1908 条里 1165 条该栏为空）。

两套值域共用一个 select，就多出三个只能靠用例盯住的风险：
1. 页面上抄的五个文本值与后端 `tax_web_search.AGING_VALUES` 漂移；
2. 拼 option 时写出没有 value 的条目——浏览器里会渲染成一个「undefined」选项，
   本机 2026-10-02 真浏览器读那一栏时就是这样抓到过一次；
3. 别处绕过 `setNpcStatus` 直接往 select 写 NPC 码：源是 search5 时那个值不在
   值域里，选择被静默吞成空值，用户以为收窄了其实没有。

用法：
    python tests/test_frontend_aging_control.py

退出码：全过 0，有失败 1。真浏览器里读那一栏仍然要做（见
references/web_interface.md），本文件只保证源码层面的契约不漂。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
WEB = (ROOT / "scripts" / "tax_web_search.py").read_text(encoding="utf-8")


def _slice(text, start, end):
    """取 start 到其后第一个 end 之间的片段。找不到就抛错，避免用例静默变空。"""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def _js_string_literals(block):
    """抽出成对引号里的字符串字面量，单双引号都认（后端那两份是双引号元组）。"""
    return re.findall(r"['\"]([^'\"]*)['\"]", block)


def _aging_s5_from_html():
    """页面上的 search5 时效值域（AGING_S5，不含 opts 里现拼的「全部」占位）。"""
    line = re.search(r"const AGING_S5=\[([^\]]*)\]", HTML)
    assert line, "index.html 里找不到 const AGING_S5=[…]"
    return _js_string_literals(line.group(1))


def _aging_values_from_python():
    """后端 build_filters 认的时效取值域（tax_web_search.AGING_VALUES）。"""
    block = re.search(r"AGING_VALUES = \((.*?)\)", WEB, re.S)
    assert block, "tax_web_search.py 里找不到 AGING_VALUES = (…)"
    return _js_string_literals(block.group(1))


class TestStatusValueSpaceFollowsSource(unittest.TestCase):
    """一个 select 装两套值域：换源必须换选项，不是置灰。"""

    def test_s5_texts_equal_the_backend_domain(self):
        """页面抄的五个时效文本必须与 AGING_VALUES 逐项一致。

        漂移的后果是静默：界面发「失效」而后端只认「全文失效」，
        build_filters 直接 ValueError，用户看到的是 400 而不是选项没了。
        """
        self.assertEqual(_aging_values_from_python(), _aging_s5_from_html())
        self.assertEqual(5, len(_aging_s5_from_html()))

    def test_npc_codes_map_into_values_that_exist_in_the_s5_space(self):
        """NPC 码换 search5 文本那张表，换出来的值必须真在值域里。"""
        body = re.search(r"const AGING_FROM_NPC=(\{.*?\});", HTML)
        assert body, "index.html 里找不到 const AGING_FROM_NPC={…}"
        mapped = _js_string_literals(body.group(1))[1::2]   # 取每对的 value
        s5 = _aging_values_from_python()
        for text in mapped:
            self.assertIn(text, s5, f"NPC 码换出的「{text}」不在 search5 值域里")

    def test_no_disabled_trick_left_on_the_status_control(self):
        """置灰那一套整个退役：函数体里不许再碰 disabled。"""
        body = _slice(HTML, "function syncFilterControls(){", "\n}\n")
        self.assertNotIn("disabled", body,
                         "时效栏改的是值域不是可用性，disabled 一回来控件就又开始空转")

    def test_control_carries_why_the_default_is_all(self):
        """默认「全部」的理由要写在 title 里，否则用户以为筛选栏坏了。

        1165/1908 条没录时效，照 NPC 的默认「现行有效」收窄会把现行文件一起筛掉，
        这个反直觉的默认必须当场可解释。
        """
        body = _slice(HTML, "function syncFilterControls(){", "\n}\n")
        self.assertIn("该栏为空", body)
        self.assertIn("默认「全部」", body)


class TestOptionPairsAreNeverIncomplete(unittest.TestCase):
    """拼 <option> 的那个数组，每一项都必须是 [value, 文案] 两项。"""

    def setUp(self):
        line = re.search(r"^\s*const opts=.*$", HTML, re.M)
        assert line, "index.html 里找不到 const opts=…"
        self.opts_line = line.group(0)

    def test_no_bare_empty_entry(self):
        """`[[]` 会让 o[0] 与 o[1] 都是 undefined，下拉里就出现字面量 undefined。

        2026-10-02 真浏览器读 chinatax／fgk 那一栏读到过 `undefined=undefined`：
        select.value 回 ""、看起来正常，显示的却是第一个选项的文字。纯静态读
        源码抓不到这个错，所以把它转写成一条能离线跑红的形状检查。
        """
        self.assertNotIn("[[]", self.opts_line.replace(" ", ""),
                         f"opts 里出现了空项：{self.opts_line.strip()}")

    def test_both_branches_start_with_a_value_and_a_label(self):
        """两个分支的首项都得写成 ['值','文案']，「全部」那项也不能省值。"""
        pairs = re.findall(r"\['([^']*)',\s*'([^']*)'\]", self.opts_line)
        self.assertTrue(pairs, f"opts 里一个完整两项都没拼出来：{self.opts_line.strip()}")
        self.assertIn(['', '全部'], [list(p) for p in pairs],
                      "「全部」这一项要显式写成 ['','全部']，不能靠缺项")


class TestWritesGoThroughTheSharedEntry(unittest.TestCase):
    """任何地方改时效栏都得走 setNpcStatus，读则走 doSearch 那一路。"""

    def test_filter_status_is_written_only_inside_the_two_helpers(self):
        """给时效栏赋值的语句只许待在 syncFilterControls / setNpcStatus 里。

        doSearch 那一处是读值（`…filterStatus').value;`），不赋值。写法散出去
        就意味着有人绕过了值域判断，search5 源下写进 NPC 码会被静默吞成空值。
        """
        assigns = re.findall(
            r"getElementById\('filterStatus'\)\s*(?:\.value\s*=[^=]|=[^=])", HTML)
        self.assertEqual(0, len(assigns),
                         f"filterStatus 被直接赋值 {len(assigns)} 处，改值须走 setNpcStatus")
        written = re.findall(r"st\.value\s*=", HTML)
        self.assertEqual(2, len(written),
                         "赋值点只该有两个：syncFilterControls 与 setNpcStatus 各一")

    def test_guided_search_uses_the_shared_entry(self):
        body = _slice(HTML, "function executeGuidedSearch(){", "toggleGuidance()")
        self.assertIn("setNpcStatus(", body)
        self.assertNotIn("getElementById('filterStatus').value=", body,
                         "向导自己写 select 就绕过了值域判断")

    def test_reader_splits_the_two_axes_by_source(self):
        """发请求时数字 status 与文本 aging 只能发一套，另一套留空。"""
        body = _slice(HTML, "async function doSearch(){", "function renderResults(")
        self.assertIn("status:s5?null:(parseInt(stv)||null)", body)
        self.assertIn("aging:s5?stv:''", body)


class TestEffectLevelStaysOfflineFromTheBar(unittest.TestCase):
    """效力等级这一维只有命令行入口，界面没有对应控件。"""

    def test_no_ghost_control_in_the_bar(self):
        self.assertNotIn("filterEffectLevel", HTML,
                         "界面没有效力等级控件；要加就同时加后端映射，不能只发文本")


if __name__ == "__main__":
    unittest.main(verbosity=2)
