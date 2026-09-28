#!/usr/bin/env python3
"""⑩「源缺陷」六条的离线用例。不联网、不开浏览器。

这六条原先写在 SKILL.md ⑩ 里，都是"程序解决不了、用的时候要留神"的人肉规则：

  1. NPC 有限流，检索串行跑；评测时不要并行别的 NPC 检索
  2. 360 被封时地方口径与税屋这一层是空的，要在答案里明说、不要拿别的源顶替
  3. fgk 翻得越深相关性越差，深页条目必须回 L1 核对
  4. 公众号依赖会话，并发加压会触发反爬
  5. `_reliability: medium` 只能用于定位
  6. `_reliability: low` 不得当作权威依据引用

"靠人记住"等于没有约束：第 5、6 条最典型——标记只在输出里印一行，定级层压根
不读它，于是 low 的条目照样能被挑成主依据、还打出"可作依据引用（法律）"。

现在六条都落进代码，这里逐条钉住，并附带自检（确认规则不是永远绿的）。
"""

import re
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_detail as DT      # noqa: E402
import tax_evidence as E     # noqa: E402
import tax_fgk as FGK        # noqa: E402
import tax_http              # noqa: E402
import tax_search as T       # noqa: E402
import tax_wechat as W       # noqa: E402
import tax_aggregator as AGG  # noqa: E402
from tax_web_search import FGK_MARKER  # noqa: E402

HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _fgk_hit(n: int, page_tag: str) -> list:
    return [{
        "title": f"财税文件{page_tag}-{i}",
        "url": f"https://fgk.chinatax.gov.cn/{FGK_MARKER}/{page_tag}{i}.htm",
        "date": "2025-01-0%d" % (i + 1),
        "document_number": f"财税〔2025〕{page_tag}{i}号",
        "publisher": "财政部",
        "snippet": "……",
    } for i in range(n)]


# ── ③ fgk 深页条目必须带页码与可靠性标记 ───────────────────────────────────
class TestFgkDeepPageMarker(unittest.TestCase):
    """翻得越深越松，深页条目只可用于定位。以前这件事靠人记页码。"""

    def _scan(self, pages):
        """pages: {页码: 该页法规库条目数}"""
        def fake_search(keyword, page=1, size=10):
            n = pages.get(page, 0)
            return {"total": 99, "results": _fgk_hit(n, "p%d" % page)}

        with mock.patch.object(FGK, "search_chinatax", side_effect=fake_search):
            return FGK._scan_list("测试词", size=99, max_pages=max(pages),
                                  adaptive=False)

    def test_shallow_page_items_carry_page_and_no_marker(self):
        out = self._scan({1: 2})
        self.assertEqual(2, out["total"])
        for it in out["results"]:
            self.assertEqual(1, it["page"], "每条都要能看出取自第几页")
            self.assertNotIn("_reliability", it, "第 1 页是浅页，不该被降级")

    def test_deep_page_items_are_marked_locate_only(self):
        out = self._scan({1: 1, 2: 1})
        deep = [it for it in out["results"] if it["page"] == 2]
        self.assertEqual(1, len(deep), "第 2 页那条应该被收进结果")
        self.assertEqual("medium", deep[0]["_reliability"])
        self.assertIn("第 2 页", deep[0]["_reliability_note"])
        self.assertIn("核对上位法", deep[0]["_reliability_note"])

    def test_marker_threshold_is_configurable(self):
        """阈值是常量，不是散在代码里的魔法数。"""
        old = FGK.FGK_SHALLOW_PAGES
        try:
            FGK.FGK_SHALLOW_PAGES = 2
            out = self._scan({1: 1, 2: 1})
            self.assertTrue(all("_reliability" not in it for it in out["results"]),
                            "阈值放到 2 之后，前两页都不该被降级")
        finally:
            FGK.FGK_SHALLOW_PAGES = old


# ── ② 缺了哪一层由程序说，不靠人推断 ───────────────────────────────────────
class TestAggregatorGaps(unittest.TestCase):
    """源级失败要翻译成"缺了哪一层、能不能拿别的源顶"。"""

    def _agg(self, *, so360=None, shui5=None, wechat=None):
        ok = lambda n: {"total": n, "results": [{"title": "财税〔2025〕1号",
                                                 "url": "https://x.test/a"}] if n else []}
        with mock.patch.object(AGG, "search_tax", return_value=ok(1)), \
             mock.patch.object(AGG, "search_chinatax", return_value=ok(1)), \
             mock.patch.object(AGG, "so360_search",
                               return_value=so360 if so360 is not None else ok(1)), \
             mock.patch.object(AGG, "search_shui5",
                               return_value=shui5 if shui5 is not None else ok(0)), \
             mock.patch.object(AGG, "search_wechat",
                               return_value=wechat if wechat is not None else ok(0)):
            return AGG.aggregate_search("测试词", size=5)

    def test_no_gap_when_nothing_failed(self):
        r = self._agg()
        self.assertEqual([], r["gaps"])
        self.assertEqual("", r["degraded_note"])

    def test_blocked_source_yields_a_gap_with_impact_and_no_substitute(self):
        r = self._agg(so360={"_error": "360 被拦截（访问异常出错）", "results": []})
        gaps = {g["source"]: g for g in r["gaps"]}
        self.assertIn("so360", gaps)
        self.assertEqual("360 被拦截（访问异常出错）", gaps["so360"]["reason"])
        self.assertIn("地方口径", gaps["so360"]["impact"])
        self.assertTrue(gaps["so360"]["do_not_substitute"])
        # 断这句话的两个要点：不许拿别的源顶替 + 必须说明缺的是哪一层。
        # 别截半句去匹配——"不要用其他源顶替"在实现文案里并不连续
        # （实现写的是"不要用其他源的条目顶替缺失层"），断半句是形式审查。
        self.assertIn("不要用其他源的条目顶替", r["degraded_note"])
        self.assertIn("要明说缺的是哪一层", r["degraded_note"])

    def test_shui5_empty_is_attributed_to_so360_not_to_shui5(self):
        """税屋链接靠 360 的 site:shui5.cn 检索取——360 挂了，税屋的空是连带的。"""
        r = self._agg(so360={"_error": "360 被拦截", "results": []})
        shui5 = [g for g in r["gaps"] if g["source"] == "shui5"]
        self.assertEqual(1, len(shui5), "税屋该被单独标一条，说明它是被连带空掉的")
        self.assertEqual("so360", shui5[0]["blocked_by"])
        self.assertIn("不代表税屋没有内容", shui5[0]["impact"])

    def test_shui5_own_failure_is_not_attributed_to_so360(self):
        r = self._agg(shui5={"_error": "WAF 挑战未通过", "results": []})
        shui5 = [g for g in r["gaps"] if g["source"] == "shui5"]
        self.assertEqual(1, len(shui5))
        self.assertNotIn("blocked_by", shui5[0], "税屋自己报错时不该甩给 360")
        self.assertIn("WAF", shui5[0]["reason"])

    def test_note_counts_hits_and_names_the_layer(self):
        r = self._agg(so360={"_error": "360 被拦截", "results": []})
        self.assertIn("2/5", r["degraded_note"], r["degraded_note"])
        self.assertIn("税屋", r["degraded_note"])


# ── ②' 网页端要把缺口显示出来，不能显示成普通的 0 条 ──────────────────────
class TestFrontendShowsDegradedNote(unittest.TestCase):
    def test_render_results_shows_degraded_note(self):
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        self.assertIn("degraded_note", body)
        self.assertIn("esc(result.degraded_note)", body)


# ── ⑤⑥ 定级层必须认 `_reliability` ────────────────────────────────────────
class TestReliabilityVetoInGrading(unittest.TestCase):
    """标记要能真的拦住条目，不能只是印一行字。"""

    LAW = "中华人民共和国企业所得税法"

    def test_low_is_not_citable(self):
        g = E.grade({"title": self.LAW, "_reliability": "low"})
        self.assertEqual("low", g["reliability"])
        self.assertIn("不得作为依据引用", g["citation_hint"])
        self.assertEqual(0.0, g["score"], "标了 low 还留 85 分，下游会当成高可信")

    def test_medium_is_locate_only_and_carries_the_source_note(self):
        g = E.grade({"title": self.LAW, "_reliability": "medium",
                     "_reliability_note": "取自总局检索第 3 页"})
        self.assertIn("仅用于定位法规", g["citation_hint"])
        self.assertIn("取自总局检索第 3 页", g["citation_hint"])

    def test_unmarked_item_keeps_old_verdict(self):
        """回归：没标记的条目判定口径一个字都不该变。"""
        g = E.grade({"title": self.LAW})
        self.assertEqual("ok", g["reliability"])
        self.assertGreater(g["score"], 0)
        self.assertIn("可作主依据", g["citation_hint"])

    def test_pick_primary_skips_low(self):
        best = E.pick_primary([
            E.grade({"title": self.LAW, "_reliability": "low",
                     "_reliability_note": "全文检索偏题"}),
            E.grade({"title": "国家税务总局公告2018年第28号"}),
        ])
        self.assertNotIn("low", best.get("reliability", ""))
        self.assertIn("公告", best["title"])

    def test_pick_primary_refuses_when_everything_is_low(self):
        best = E.pick_primary([E.grade({"title": self.LAW, "_reliability": "low"})])
        self.assertIn("全部带 _reliability: low", best["_why"])
        self.assertNotIn("rank_label", best, "全是 low 时不该挑出任何主依据")

    def test_pick_primary_prefers_unmarked_over_medium(self):
        best = E.pick_primary([
            E.grade({"title": "财税〔2025〕9号通知", "_reliability": "medium"}),
            E.grade({"title": "国家税务总局公告2018年第28号"}),
        ])
        self.assertEqual("ok", best["reliability"])

    def test_pick_primary_falls_back_to_medium_with_a_caveat(self):
        best = E.pick_primary([E.grade({"title": self.LAW, "_reliability": "medium"})])
        self.assertEqual("medium", best["reliability"])
        self.assertIn("不得作为条文依据", best["_why"])


# ── ④ 搜狗微信要进闸，不能靠"别并发"的提醒 ────────────────────────────────
class _FakeSession:
    """记录并发峰值的假会话，用来验证闸真的把并发压成 1。"""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def get(self, url, **kwargs):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            return object()
        finally:
            with self._lock:
                self.active -= 1


class TestWechatSogouGate(unittest.TestCase):
    def test_sogou_requests_go_through_the_gate(self):
        src = (SCRIPTS_DIR / "tax_wechat.py").read_text(encoding="utf-8")
        for fn in ("def search_wechat", "def _restore_url"):
            body = src[src.index(fn):]
            body = body[:body.index("\ndef ")]
            self.assertIn("_sogou_get", body, "{} 没走闸".format(fn))
            self.assertNotIn("sess.get(", body, "{} 里还有绕开闸的裸 sess.get".format(fn))
        helper = src[src.index("def _sogou_get"):src.index("def search_wechat")]
        self.assertIn("with sogou_gate:", helper)

    def test_gate_is_the_shared_implementation_with_its_own_lock(self):
        self.assertIsInstance(W.sogou_gate, tax_http.SerialGate)
        self.assertIsInstance(T.npc_gate, tax_http.SerialGate)
        self.assertIsInstance(T.npc_gate, T.NpcSerialGate)
        self.assertNotEqual(
            W.sogou_gate.path, T.npc_gate.path,
            "两个站必须各用一把锁，共用一把会让不相关的请求互相排队甚至死等")

    def test_concurrent_callers_are_serialized(self):
        sess = _FakeSession()
        old = W._SOGOU_MIN_INTERVAL
        try:
            W._SOGOU_MIN_INTERVAL = 0.0      # 只验证闸，不验证限速
            threads = [threading.Thread(
                target=W._sogou_get,
                args=(sess, "https://weixin.sogou.com/weixin?type=2&query=x"))
                for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            W._SOGOU_MIN_INTERVAL = old
        self.assertEqual(1, sess.max_active,
                         "并发峰值 %d：闸没起作用" % sess.max_active)

    def test_min_interval_is_enforced(self):
        sess = _FakeSession()
        old_int, old_at = W._SOGOU_MIN_INTERVAL, W._last_sogou_at
        try:
            W._SOGOU_MIN_INTERVAL = 0.4
            W._last_sogou_at = 0.0
            t0 = time.monotonic()
            W._sogou_get(sess, "https://weixin.sogou.com/weixin?type=2&query=x")
            W._sogou_get(sess, "https://weixin.sogou.com/weixin?type=2&query=y")
            dt = time.monotonic() - t0
        finally:
            W._SOGOU_MIN_INTERVAL, W._last_sogou_at = old_int, old_at
        self.assertGreaterEqual(dt, 0.4, "两次请求之间没有按最小间隔让路")


# ── ① NPC 闸要包住下载路径 ────────────────────────────────────────────────
class TestNpcGateCoversDownload(unittest.TestCase):
    def test_get_download_url_uses_the_gated_request(self):
        src = (SCRIPTS_DIR / "tax_detail.py").read_text(encoding="utf-8")
        body = src[src.index("def get_download_url"):src.index("def download_bytes")]
        self.assertIn("_request(", body, "取下载地址没走带闸的 _request")
        self.assertNotIn("tax_http.get(", body, "取下载地址还在闸外裸调")

    def test_npc_gate_is_a_subclass_not_a_copy(self):
        """一份实现两个用户：闸本体在 tax_http，NPC 只是钉了默认值。"""
        self.assertTrue(issubclass(T.NpcSerialGate, tax_http.SerialGate))
        npc_body = (SCRIPTS_DIR / "tax_search.py").read_text(encoding="utf-8")
        npc_body = npc_body[npc_body.index("class NpcSerialGate"):
                            npc_body.index("npc_gate = NpcSerialGate()")]
        self.assertNotIn("msvcrt", npc_body,
                         "NPC 子类里又抄了一遍文件锁实现，说明没共用")

    def test_eval_and_server_both_use_download_bytes(self):
        for rel in ("scripts/tax_server.py", "tests/eval_answer.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("law-search/" + "download/pc", text,
                             "{} 自己拼了下载地址".format(rel))
            self.assertIn("download_bytes", text)


# ── 用例自身的能力自检：规则不能永远绿 ─────────────────────────────────────
class TestRulesCanActuallyFail(unittest.TestCase):
    """把规则作用在构造的反例上，确认它真的拦得住。"""

    def test_degraded_note_is_empty_only_without_gaps(self):
        self.assertEqual("", AGG._degraded_note(["npc"], [], {"npc": 3}))
        note = AGG._degraded_note(["npc"], [{"source": "npc", "label": "NPC"}],
                                  {"npc": 3})
        self.assertTrue(note)

    def test_evidence_veto_only_fires_on_known_levels(self):
        self.assertEqual("", E._reliability_of({"_reliability": "HIGH"}))
        self.assertEqual("low", E._reliability_of({"_reliability": " LOW "}))
        self.assertEqual("", E._reliability_of({}))


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    r = unittest.main(verbosity=2, exit=False)
    print("\n结果：%d/%d 通过" % (r.result.testsRun - len(r.result.failures)
                                - len(r.result.errors), r.result.testsRun))
    sys.exit(0 if r.result.wasSuccessful() else 1)
