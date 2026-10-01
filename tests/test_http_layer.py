#!/usr/bin/env python3
"""统一请求层（tax_http）的守门用例。不联网。

两层保护：

1. **静态守门** —— 用 AST 扫 scripts/*.py，除白名单外不许再出现裸 requests
   调用。这一条把"六处收敛成一处"的结果钉住：以后谁再写 requests.get，
   门禁直接红。

   用 AST 而不是 grep，是因为别名会骗过文本搜索：tax_server.py 里那处写的是
   `req.get(...)`（`import requests as req`），按 `requests.` 前缀 grep 一个都
   匹配不到。

2. **运行时行为** —— 确认 tax_http 对参数只做透传、不做隐式补全，并且 verify
   没有默认值。给 verify 一个默认值，就等于替某个调用点偷偷改掉了 SSL 行为。
"""

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_http  # noqa: E402

# 允许自己发请求的模块：
#   tax_http.py    —— 统一出口本身
#   其余四个        —— 各自维护 requests.Session（WAF 挑战、cookie 恢复、二次
#                      跳转），请求形状与统一层不同，强行合并只会把特例塞进来
ALLOWED_BARE = {
    "tax_http.py",
    "tax_shui5.py",
    "tax_so360.py",
    "tax_wechat.py",
    "tax_web_search.py",
}

# 视为"直接发请求"的 requests 属性名
NETWORK_ATTRS = {"get", "post", "put", "delete", "patch", "head", "request", "Session"}

# 必须经由统一层的模块
#
# tax_server.py 不在名单里：它唯一的直接请求（下载 NPC 正文）改成了调用
# tax_detail.download_bytes，本模块不再自己发 HTTP，硬要它 import tax_http
# 只会留下一个没人用的导入。覆盖不会因此变松——上面的白名单规则照样管它，
# 下面的 TestNpcDownloadSinglePath 还额外钉住"下载只有一条路径且上了闸"。
MUST_USE_HTTP_LAYER = ("tax_detail.py", "tax_fgk.py", "tax_search.py")


def _requests_names(tree):
    """收集文件里 requests 模块的所有本地名字，含别名与 from-import 名。"""
    mods, funcs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "requests":
                    mods.add(a.asname or "requests")
        elif isinstance(node, ast.ImportFrom) and node.module == "requests":
            for a in node.names:
                funcs.add(a.asname or a.name)
    return mods, funcs


def _bare_calls(path):
    """返回 [(行号, 表达式)]，列出文件里所有直接发请求的调用。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods, funcs = _requests_names(tree)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id in mods and f.attr in NETWORK_ATTRS):
            hits.append((node.lineno, "{}.{}".format(f.value.id, f.attr)))
        elif isinstance(f, ast.Name) and f.id in funcs and f.id in NETWORK_ATTRS:
            hits.append((node.lineno, f.id))
    return hits


class TestBareRequestsGuard(unittest.TestCase):
    """静态守门：白名单之外，scripts/ 里不该再有直接发请求的调用。"""

    def test_no_bare_requests_outside_allowlist(self):
        offenders = []
        for p in sorted(SCRIPTS_DIR.glob("*.py")):
            if p.name in ALLOWED_BARE:
                continue
            for lineno, expr in _bare_calls(p):
                offenders.append("{}:{}  {}".format(p.name, lineno, expr))
        self.assertEqual(
            [], offenders,
            "发现绕过 tax_http 的裸请求（应改为 tax_http.get / tax_http.request）：\n  "
            + "\n  ".join(offenders))

    def test_allowlisted_modules_still_exist(self):
        """白名单里的文件必须真实存在，否则改名之后豁免会变成幽灵条目。"""
        missing = [n for n in sorted(ALLOWED_BARE) if not (SCRIPTS_DIR / n).is_file()]
        self.assertEqual([], missing, "白名单里的文件已不存在：{}".format(missing))

    def test_consumers_actually_use_the_layer(self):
        """名单里的模块必须真的走统一层，不得自己拼裸请求。"""
        for name in MUST_USE_HTTP_LAYER:
            text = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("tax_http", text, "{} 没有引用统一请求层".format(name))

    def test_scanner_can_see_aliased_imports(self):
        """守门自身的能力自检：别名调用必须被扫出来。

        用一个临时文件验一遍，避免"扫描器失灵导致永远 PASS"这种假绿。
        注意临时文件不能落在 scripts/ 里——那里会被其他用例整目录扫描。
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "sample.py"
            tmp.write_text(
                "import requests as r\n"
                "from requests import post\n"
                "\n"
                "def f():\n"
                "    r.get('https://example.test/a')\n"
                "    post('https://example.test/b')\n",
                encoding="utf-8",
            )
            hits = [expr for _, expr in _bare_calls(tmp)]
        self.assertEqual(["post", "r.get"], sorted(hits))


class TestNpcDownloadSinglePath(unittest.TestCase):
    """NPC 正文下载只能有一条路径，而且必须在串行闸内。

    tax_server 与 tests/eval_answer 各自拼下载 URL、再裸发请求的两条路都不在
    闸里——"评测时不要并行打 NPC"这条规则只能靠人记。取地址与取文件
    都收进 tax_detail.download_bytes 一个函数。
    """

    def test_download_url_is_built_only_in_tax_detail(self):
        # 针脚拆成两段拼出来：否则本文件自己就含有那个字符串，会被自己扫出来
        # （把本文件排除也能过，但那等于把扫描面缩小，不如让针脚不出现）。
        needle = "law-search/" + "download/pc"
        offenders = []
        for p in sorted(list(SCRIPTS_DIR.glob("*.py")) + list((ROOT / "tests").glob("*.py"))):
            if p.name == "tax_detail.py":
                continue
            if needle in p.read_text(encoding="utf-8"):
                offenders.append(p.name)
        self.assertEqual(
            [], offenders,
            "下载地址只该在 tax_detail.py 里拼，这些文件各拼了一遍：{}".format(offenders))

    def test_download_bytes_holds_the_gate(self):
        src = (SCRIPTS_DIR / "tax_detail.py").read_text(encoding="utf-8")
        body = src[src.index("def download_bytes"):src.index("def download_file")]
        self.assertIn("with npc_gate:", body, "download_bytes 没有上串行闸")

    def test_consumers_use_download_bytes(self):
        for rel in ("scripts/tax_server.py", "tests/eval_answer.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("download_bytes", text,
                          "{} 没有走 tax_detail.download_bytes".format(rel))


class TestTaxHttpPassthrough(unittest.TestCase):
    """运行时：tax_http 只透传，不补全、不改写、不改返回值。"""

    def test_get_passes_everything_through(self):
        sentinel = object()
        with mock.patch("requests.get", return_value=sentinel) as m:
            out = tax_http.get("https://example.test/a", headers={"X": "1"},
                               timeout=7, verify=False)
        self.assertIs(sentinel, out)
        m.assert_called_once_with("https://example.test/a",
                                  headers={"X": "1"}, timeout=7, verify=False)

    def test_request_forwards_method_and_kwargs(self):
        sentinel = object()
        with mock.patch("requests.request", return_value=sentinel) as m:
            out = tax_http.request("POST", "https://example.test/b",
                                   headers={"X": "2"}, timeout=9, verify=True,
                                   params={"q": "增值税"}, allow_redirects=False)
        self.assertIs(sentinel, out)
        m.assert_called_once_with("POST", "https://example.test/b",
                                  headers={"X": "2"}, timeout=9, verify=True,
                                  params={"q": "增值税"}, allow_redirects=False)

    def test_verify_has_no_default(self):
        """verify 必填是刻意的。

        三个调用点原本的 SSL 策略并不一致（tax_fgk 用 requests 默认的 True，
        另两个用默认关闭的 VERIFY_SSL）。这里给个默认值就等于替某一方改了
        线上行为，所以设成必填，把差异留在调用点上。
        """
        with self.assertRaises(TypeError):
            tax_http.get("https://example.test/c", headers={}, timeout=5)
        with self.assertRaises(TypeError):
            tax_http.request("GET", "https://example.test/c", headers={}, timeout=5)

    def test_headers_timeout_also_have_no_default(self):
        with self.assertRaises(TypeError):
            tax_http.get("https://example.test/d", verify=False)


class TestShortReason(unittest.TestCase):
    """取数失败那句话的整形：界面那一行不能塞进 urllib3 的内部结构。"""

    def test_drops_the_url_but_keeps_the_readable_cause(self):
        exc = requests.exceptions.TooManyRedirects(
            "Redirect response '302 Found' for "
            "https://m.so.com/s?q=%E5%A2%9E%E5%80%BC%E7%A8%8E%E8%A7%A3%E8%AF%BB&pn=1 "
            "Redirecting too many times! (25 redirects)")
        why = tax_http.short_reason(exc)
        self.assertIn("TooManyRedirects", why)     # 类名留着，读的人有词可查
        self.assertNotIn("%E5%A2%9E", why)         # 编码后的查询串不进界面
        self.assertIn("Redirecting too many times", why)

    def test_flattens_and_cuts_without_breaking_a_word(self):
        """实测形态：一次 SSL/域名失败给的是三百多字、带折行的 urllib3 结构。

        这里限到 60 字，要求它一行到底、不在词中间断开、把 "(Caused by ...)"
        那层嵌套原因丢掉——嵌套里说的是 urllib3 的实现，不是用户能行动的事。
        """
        exc = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='m.so.com', port=443): Max retries exceeded "
            "with url: /?q=%E5%A2%9E\n  (Caused by SSLError(SSLEOFError(8, "
            "'EOF occurred in violation of protocol')))")
        why = tax_http.short_reason(exc, limit=60)
        self.assertNotIn("\n", why)
        self.assertNotIn("Caused by", why)
        self.assertNotIn("%E5%A2%9E", why)
        self.assertLessEqual(len(why), len("ConnectionError：") + 60)
        # 60 字这一刀落在 "retries" 之后，不是 "excee"：截断点回退到空格
        self.assertTrue(why.endswith("Max retries"), why)


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    total = result.testsRun
    bad = len(result.failures) + len(result.errors)
    print("\n结果：{}/{} 通过".format(total - bad, total))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
