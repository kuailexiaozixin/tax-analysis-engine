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

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
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

# 必须经由统一层的模块（原先是各写一遍裸请求的那几个）
MUST_USE_HTTP_LAYER = ("tax_detail.py", "tax_fgk.py", "tax_search.py", "tax_server.py")


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
        """原先各写一遍裸请求的模块，现在必须真的走统一层。"""
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
