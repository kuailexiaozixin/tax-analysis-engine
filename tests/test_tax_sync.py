#!/usr/bin/env python3
"""tax_sync.py（内容版本 diff 同步器）的离线用例。不联网。

打桩点选在网络出口 tax_http.get，被测的定位→diff→下载→构建→落 state
全流程照常执行。缓存/产物目录指到临时目录，不碰仓库 data/sync。

钉住四件事（D 的验收标准）：
  1. 同一直链连跑两轮：第二轮报"无更新"，且**不再发起下载**（只更新 last_checked）
  2. 直链变了、--check：报"发现新版本"，不下载、不构建
  3. 手改 state 的 url 一个字符：下一轮报"有更新"
  4. 下载内容低于阈值：报"下载异常"、成功=False、不覆盖旧产物、不触发构建
  附赠：直链变了但内容 SHA1 相同 → "无更新（内容未变）"，不白烧构建
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_http  # noqa: E402
from tax_sync import Synchronizer, SyncError, MIN_CONTENT_BYTES  # noqa: E402

BIG = b"x" * (MIN_CONTENT_BYTES + 500)   # 够大的"正常产物"
TINY = b"<html>not found</html>"          # 低于阈值的错误页


class _Resp:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


class _FakeHttp:
    """替 tax_http.get：记录被请求的 URL，按 URL 返回预设字节。"""

    def __init__(self, table: dict):
        self.table = table
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        return _Resp(self.table[url])


class _Harness:
    """造一个可控的 Synchronizer：locate 返回可切换的 url，build 记录调用次数。"""

    def __init__(self, data_root: Path):
        self.builds = []
        self.current_url = "http://example.test/dir_v1.xlsx"

        def locate():
            return {"url": self.current_url, "version": self.current_url[-9:]}

        def build(path):
            self.builds.append(Path(path).read_bytes())

        self.locate = locate
        self.syn = Synchronizer("demo", locate, build, ext=".xlsx",
                                data_root=data_root)


def _install(fake: _FakeHttp):
    tax_http.get = fake          # 覆盖模块级出口；tearDown 里还原


class TestTaxSync(unittest.TestCase):
    def setUp(self):
        self._saved_get = tax_http.get
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self.h = _Harness(self.data_root)

    def tearDown(self):
        tax_http.get = self._saved_get
        self._tmp.cleanup()

    def _run(self, table, url=None, **kw):
        if url is not None:
            self.h.current_url = url
        fake = _FakeHttp(table)
        _install(fake)
        res = self.h.syn.sync(**kw)
        return res, fake

    def test_no_change_second_round_does_not_download(self):
        """1) 首轮下载构建；第二轮同链 → 无更新，不再打网络。"""
        table = {"http://example.test/dir_v1.xlsx": BIG}
        res1, fake1 = self._run(table)
        self.assertEqual("已更新", res1["动作"])
        self.assertEqual(1, len(fake1.calls))
        self.assertEqual(1, len(self.h.builds))

        res2, fake2 = self._run(table)
        self.assertEqual("无更新", res2["动作"])
        self.assertTrue(res2["成功"])
        self.assertEqual([], fake2.calls, "无更新仍发起了下载")
        self.assertEqual(1, len(self.h.builds), "无更新仍触发了构建")

    def test_check_only_reports_change_without_download(self):
        """2) 直链变了 + --check：报发现新版本，不下载不构建。"""
        self._run({"http://example.test/dir_v1.xlsx": BIG})   # 先落到 v1
        res, fake = self._run(
            {"http://example.test/dir_v2.xlsx": BIG},
            url="http://example.test/dir_v2.xlsx", check_only=True)
        self.assertEqual("发现新版本（--check 未下载）", res["动作"])
        self.assertTrue(res["有更新"])
        self.assertEqual([], fake.calls)
        self.assertEqual(1, len(self.h.builds))   # 仍只有首轮的构建

    def test_state_url_tamper_is_detected(self):
        """3) 手改 state.url 一个字符 → 下一轮判定为有更新。"""
        table = {"http://example.test/dir_v1.xlsx": BIG}
        self._run(table)
        sp = self.h.syn.state_path
        state = json.loads(sp.read_text(encoding="utf-8"))
        state["url"] = state["url"].replace("v1", "v0")
        sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        res, fake = self._run(table)
        self.assertTrue(res["有更新"])
        self.assertEqual(["http://example.test/dir_v1.xlsx"], fake.calls)

    def test_small_download_is_rejected_and_preserves_artifact(self):
        """4) 首轮即命中错误页：无 state → 进下载 → 内容过小 → 判失败、不落产物、不构建。"""
        url = "http://example.test/dir_v1.xlsx"
        res, fake = self._run({url: TINY})
        self.assertEqual("下载异常", res["动作"])
        self.assertFalse(res["成功"])
        self.assertEqual(["http://example.test/dir_v1.xlsx"], fake.calls)
        self.assertFalse(self.h.syn.artifact_path.exists(), "异常下载却写了产物")
        self.assertEqual([], self.h.builds, "异常下载仍触发了构建")
        # 错误页不该被落成 state.url，否则下一轮会把"没成功"当"已同步"
        state = json.loads(self.h.syn.state_path.read_text(encoding="utf-8")) \
            if self.h.syn.state_path.exists() else {}
        self.assertNotEqual(url, state.get("url"), "失败的下载把错误链接写进了 state")

    def test_link_changed_content_same_skips_build(self):
        """附) 直链变了、内容 SHA1 相同 → 无更新（内容未变），不构建。"""
        same = b"IDENTICAL_CONTENT" + BIG
        self._run({"http://example.test/dir_v1.xlsx": same})
        res, fake = self._run(
            {"http://example.test/dir_v2.xlsx": same},
            url="http://example.test/dir_v2.xlsx")
        self.assertEqual("无更新（内容未变）", res["动作"])
        self.assertEqual(["http://example.test/dir_v2.xlsx"], fake.calls)
        self.assertEqual(1, len(self.h.builds))   # 只有首轮构建


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(unittest.main(verbosity=2))
