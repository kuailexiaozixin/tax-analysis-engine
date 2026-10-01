#!/usr/bin/env python3
"""前端"省份"控件的契约用例。不联网、不开浏览器。

背景：省份控件的位置有讲究——若长在搜索栏的"高级筛选"里，它对 `/api/search`
毫无作用：NPC 库存的是全国性法规，没有省级维度，服务端收到 `province` 只原样
回填；前端却据此渲染出一个 `📍 上海` 标签。于是"选了省份"看起来像在过滤，
结果其实一条没变，标签还会跟着骗人。

控件只在它真正生效的地方才有意义：法规弹窗的"官方解读"页，那里会按省份换到
`{省}.chinatax.gov.cn` 去检索。下面这些用例把结论钉住，防止有人把控件搬回搜索栏、
把假标签加回来，或者让前端的站点清单与后端悄悄漂移。

用法：
    python tests/test_frontend_province_ui.py

退出码：全过 0，有失败 1。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
SERVER = (ROOT / "scripts" / "tax_server.py").read_text(encoding="utf-8")

FILTERS_BAR = "<div class=\"filters-bar\" id=\"filtersBar\">"


def _slice(text, start, end):
    """取 start 到其后第一个 end 之间的片段。找不到就抛错，避免用例静默变空。"""
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def _code(text):
    """去掉整行注释，只看代码。

    必要：本仓库的注释大量解释"为什么不再这么写"，句子里自然会出现被禁用的标识符。
    直接对原始文本做 assertNotIn，命中的会是注释而不是代码，用例就变成了形式审查。
    """
    keep = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        keep.append(ln)
    return "\n".join(keep)


def _norm(host):
    """模板变量归一化：`${prov}.x.cn` 与 f-string `{province}.x.cn` 视为同一个。"""
    return re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "PROV", host)


def _literals(block):
    """抽出反引号/单引号/双引号里的字符串字面量。"""
    return [_norm(s) for s in re.findall(r"['\"`]([^'\"`]+)['\"`]", block)]


def _js_interp_sites():
    """index.html 里 interpSites() 的两个分支：省级 / 默认。"""
    body = _slice(HTML, "function interpSites(prov){", "\n}")
    returns = re.findall(r"return\s*\[([^\]]*)\]", body)
    return [_literals(returns[0]), _literals(returns[1])]


def _server_interp_sources():
    """tax_server.search_interpretations() 的默认 sources：省级 / 默认。"""
    body = _slice(SERVER, "def search_interpretations(", "\ndef ")
    prov = re.search(r"if province:\s*\n\s*sources = \[(.*?)\]", body, re.S)
    dflt = re.search(r"else:\s*\n\s*sources = \[(.*?)\]", body, re.S)
    return [_literals(prov.group(1)), _literals(dflt.group(1))]


class TestProvinceControlMovedOutOfSearchBar(unittest.TestCase):
    """省份控件不属于搜索栏——它在那里撒不了任何作用。"""

    def test_filter_province_is_gone(self):
        self.assertFalse("filterProvince" in _code(HTML), "filterProvince 不该再出现")

    def test_filters_bar_keeps_only_the_six_real_filters(self):
        bar = _slice(HTML, FILTERS_BAR, "<!-- \u2550\u2550\u2550 RESULTS")
        self.assertEqual(6, bar.count('class="filter-group"'), bar)
        self.assertFalse("省份" in bar, "搜索栏里不该再有省份")
        for fid in ("filterScope", "filterType", "filterStatus",
                    "filterSort", "filterSource", "filterDateFrom"):
            self.assertIn(fid, bar, fid)

    def test_search_payload_has_no_province(self):
        body = _slice(HTML, "async function doSearch(){", "function renderResults(")
        payload = _slice(body, "const payload={", "};")
        self.assertNotIn("province", re.findall(r"(\w+)\s*:", payload),
                         "payload 里不该有 province 键")

    def test_results_header_does_not_render_a_province_tag(self):
        body = _code(_slice(HTML, "function renderResults(data){",
                            "/* \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550 KEYWORD HIGHLIGHT"))
        self.assertFalse("province" in body, "结果头部不该读 province 字段")
        self.assertFalse("\U0001f4cd" in body, "结果头部不该有 \U0001f4cd 标签")


class TestProvinceControlLivesInInterpTab(unittest.TestCase):
    """控件长在"官方解读"页里，因为只有那个接口按省份换站点。"""

    def test_select_is_inside_the_interp_panel_with_a_results_host(self):
        panel = _slice(HTML, '<div class="tab-panel" id="tabInterpContent">',
                       '<div class="tab-panel" id="tabAiContent">')
        self.assertIn('id="interpProvince"', panel)
        self.assertIn('id="interpResults"', panel)
        self.assertIn('onchange="onInterpProvinceChange()"', panel)

    def test_province_list_is_defined_exactly_once(self):
        self.assertEqual(1, HTML.count("const PROVINCES="), "省份清单只该有一处")
        # 省份表不得存在第二份（下拉一份、名字映射一份）——代码里不许出现
        # 复制的名字映射，否则第二份表就回来了。
        code = _code(HTML)
        self.assertNotIn("getProvinceName", code)
        self.assertNotIn("loadProvinces", code)

    def test_load_writes_into_results_host_and_sends_province(self):
        body = _slice(HTML, "async function loadInterpretationsTab(",
                      "/* \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550 WEB RELATED TAB")
        self.assertIn("getElementById('interpResults')", body)
        self.assertIn("province=${encodeURIComponent(prov)}", body)
        # 若还往 tabInterpContent 整体写 innerHTML，工具栏会被连根冲掉
        self.assertNotIn("getElementById('tabInterpContent')", body)

    def test_changing_province_reloads(self):
        body = _slice(HTML, "function onInterpProvinceChange(){", "\nfunction switchTab(")
        self.assertIn("_interpProvince=el?el.value:''", body)
        self.assertIn("loadInterpretationsTab(_modalLawId)", body)


class TestFrontendSitesMatchServer(unittest.TestCase):
    """页面上写的检索站点必须是后端真正去查的站点。"""

    def test_interp_sites_equal_server_sources(self):
        js_prov, js_default = _js_interp_sites()
        sv_prov, sv_default = _server_interp_sources()
        self.assertEqual(sv_prov, js_prov, "省级分支的站点清单与后端不一致")
        self.assertEqual(sv_default, js_default, "默认分支的站点清单与后端不一致")

    def test_footer_label_is_derived_not_hardcoded(self):
        body = _slice(HTML, "function interpFooterLabel(){", "\nfunction onInterpProvinceChange(")
        self.assertIn("interpSites(_interpProvince)", body)
        tab = _slice(HTML, "function switchTab(tab){", "async function refreshAiGate(")
        self.assertIn("interp:interpFooterLabel()", tab)


if __name__ == "__main__":
    unittest.main(verbosity=2)
