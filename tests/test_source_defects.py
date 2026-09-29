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

import ast
import contextlib
import io
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_detail as DT      # noqa: E402
import tax_answer as ANS     # noqa: E402
import tax_evidence as E     # noqa: E402
import tax_fgk as FGK        # noqa: E402
import tax_formatter as FMT  # noqa: E402
import tax_http              # noqa: E402
import tax_search as T       # noqa: E402
import tax_wechat as W       # noqa: E402
import tax_aggregator as AGG  # noqa: E402
import tax_web_search as WS   # noqa: E402
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

    def test_empty_state_keeps_the_two_causes_apart(self):
        """空清单的成因是接口给的 `_empty_reason`，页面要原样显示。

        对应的缺陷：翻过末页与"末页之内却没给清单"是两件事，都渲染成
        "未找到相关政策"就等于页面替用户说了"库里没有这份文件"。
        """
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        self.assertIn("_empty_reason", body)
        self.assertIn("esc(result._empty_reason)", body)

    def test_render_results_shows_routing_note(self):
        """换源提示和"没归类"提示都由后端给整句，页面只照抄。

        对应的缺陷：归不出税种时后端拿原话做字面标题检索，实测回 687 条无关
        法条。页面不显示这句，一摞无关法条就看起来像按本题找出来的依据。
        """
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        self.assertIn("_routed", body)
        self.assertIn("esc(result._routed)", body)


# ── 录入项带来的文号/时效/效力级别，页面不能继续显示"时效未标注" ──────────
class TestFrontendShowsSourceMetadata(unittest.TestCase):
    """NPC 条目给数字状态码，总局法规库条目给的是录入项文本。

    缺陷形态很具体：接口已经回 `xxgk_aging="全文有效"`，页面只读
    `status_code`，于是这份现行有效的文件在界面上显示成"⚪ 时效未标注"，
    和真的没标的条目长得一模一样。
    """

    OLD_BADGE = ("function statusBadge(item){\n"
                 "  const cls = statusMap[item.status_code] || 's-unknown';\n"
                 "  return `<span class=\"status-badge ${cls}\">"
                 "${statusNames[item.status_code] || '时效未标注'}</span>`;\n}\n")

    @staticmethod
    def _badge():
        return HTML[HTML.index("function statusBadge(item){"):
                    HTML.index("items.forEach((item,i)=>{")]

    @staticmethod
    def _card():
        return HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]

    def test_badge_reads_both_the_code_and_the_text(self):
        b = self._badge()
        self.assertIn("item.status_code", b)
        self.assertIn("item.status", b)
        # 文本要分四类，"全文有效"不能掉进未标注
        for word in ("废止", "尚未生效", "修改", "有效"):
            self.assertIn(word, b, word)

    def test_card_shows_document_number_and_effect_level(self):
        body = self._card()
        self.assertIn("item.document_number", body)
        self.assertIn("item.effect_level", body)
        self.assertIn("statusBadge(item)", body)

    def test_date_falls_back_to_the_source_field(self):
        """单源 chinatax/fgk 的条目用 `date`，聚合后才统一成 `publish_date`。"""
        self.assertIn("item.publish_date||item.date", self._card())

    def test_placeholder_status_is_not_shown_as_a_source_value(self):
        """NPC 缺 status 时程序自己拼"未知(None)"，那是缺省值不是法规写的时效。

        界面要把它换成"时效未标注"，否则读者的判断依据从"来源没给"变成了
        "来源给了一个叫未知的值"。
        """
        self.assertIn("/^未知/", self._badge())
        self.assertIn("'时效未标注'", self._badge())

    def test_rules_can_actually_fail(self):
        """把同一套断言作用在改前的写法上，确认它拦得住。"""
        for word in ("废止", "尚未生效", "修改", "有效"):
            self.assertNotIn(word, self.OLD_BADGE, word)
        self.assertNotIn("item.status||", self.OLD_BADGE)
        self.assertNotIn("document_number", self.OLD_BADGE)


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


# ── ⑤' 显示层也不许把标记吞掉 ──────────────────────────────────────────────
class TestFormatterShowsReliability(unittest.TestCase):
    """标记进了数据、却印不出来，等于没标记。

    `tax_formatter.py --mode single` 原先不读响应级的 `_reliability`：把 NPC 全文
    检索（恒带 medium）的结果灌进去，输出里一个字都不提"可能偏题"，读者只看到
    "共 N 条法规"，会当成能引用的清单。多源归并那条路径印了，单源这条漏了。
    """

    def _render(self, **extra):
        """喂一份 tax_search 真实会返回的最小响应，返回渲染出的 markdown。"""
        resp = {
            "keyword": "研发费用加计扣除", "total": 8833,
            "scope": "fulltext", "search_type": "fuzzy",
            "results": [{"title": "中华人民共和国企业所得税法", "id": "x1",
                         "status_code": 3, "publish_date": "2018-12-29"}],
        }
        resp.update(extra)
        return FMT.format_search_response(resp, intent="policy_lookup")

    def test_medium_marker_reaches_the_markdown(self):
        md = self._render(_reliability="medium",
                          _reliability_note="结果已排序但可能偏题（全文分词匹配）")
        self.assertIn("可靠性 medium", md)
        self.assertIn("可能偏题", md)
        # 警告要出现在条目之前，不能等读者看完清单才看到
        self.assertLess(md.index("可靠性"), md.index("中华人民共和国企业所得税法"))

    def test_local_wording_is_the_fallback(self):
        """数据里没带说明时用本地兜底文案，而不是印出空的破折号。"""
        md = self._render(_reliability="medium")
        self.assertIn("可靠性 medium", md)
        self.assertIn(FMT._RELIABILITY_NOTE, md)

    def test_no_marker_no_warning(self):
        """回归：标题检索不带标记，就不能凭空多出一句警告。"""
        self.assertNotIn("可靠性", self._render())


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


class TestGradingReadsFgkMetadata(unittest.TestCase):
    """法规库清单带来的三个定级口子：解读件、已修改、标题里的书名号。

    都是 2026-09-29 用《关于企业重组业务所得税处理有关征管问题的公告》
    （2026年第13号）这道真题撞出来的：翻页基准修正后清单里同时出现了公告原文、
    公告的解读、以及公告正文援引的配套文件，定级层一照面就把解读按被解读文件
    的形态判成了规范性文件。
    """

    def test_interpretation_is_not_the_document_it_explains(self):
        t = "国家税务总局关于企业重组业务企业所得税征收管理若干问题的公告的解读"
        self.assertEqual("technical", E.rank_of(t)["rank"])
        # 带 effect_level 也不该被分类规则顶成原文的位阶
        self.assertEqual("technical",
                         E.rank_of(t, category="税务规范性文件")["rank"])

    def test_amended_document_still_in_force(self):
        v = E.judge_validity({"status": "已修改"}, "2026-09-29")
        self.assertEqual("effective", v["validity"])
        self.assertIn("修改后的版本", v["note"])
        # 认不出的状态仍然只能报未标明，不猜
        self.assertEqual("unknown",
                         E.judge_validity({"status": "待定"}, "2026-09-29")["validity"])

    def test_title_with_book_marks_still_ranks(self):
        t = "国家税务总局关于发布《企业重组业务企业所得税管理办法》的公告"
        r = E.rank_of(t)
        self.assertEqual("normative", r["rank"])
        self.assertGreaterEqual(r["score"], 50)


class TestNormativeIsABasisNotReference(unittest.TestCase):
    """现行有效的总局公告要能进依据层；这道线原先卡在 60，把它挡在外面。

    规范性文件是税务机关据以执法、纳税人据以办理的直接依据，写"只能参考，
    不能当依据"会让答案绕开 L2 下挖真正取回的那层规则。改到 50 之后，
    "依据"这个身份仍然由时效把关：时效一不明就自己掉回参考层。
    """

    ANN = "国家税务总局关于企业重组业务所得税处理有关征管问题的公告"

    def _score(self, status):
        g = E.grade({"title": self.ANN, "status": status}, at="2026-09-29")
        return g["score"], g["validity"]

    def test_current_announcement_passes_the_line(self):
        score, validity = self._score("全文有效")
        self.assertEqual("effective", validity)
        self.assertGreaterEqual(score, E.PRIMARY_THRESHOLD,
                                "现行有效的规范性文件应可作依据")

    def test_unknown_validity_falls_back_to_reference(self):
        """折减要把它请回去，否则"没核对时效的公告"也能顶当依据。"""
        for status, expect in (("", "unknown"), ("尚未生效", "pending"),
                               ("全文废止", "repealed")):
            score, validity = self._score(status)
            self.assertEqual(expect, validity, status)
            self.assertLess(score, E.PRIMARY_THRESHOLD, status)

    def test_reference_tiers_stay_below_the_line(self):
        """线以下的三类：技术口径、地方税务局文件、认不出形态的条目。"""
        for title, want_low in (
                ("关于企业重组业务所得税处理有关征管问题的公告的解读", "technical"),
                ("解读：企业重组特殊性税务处理怎么备案", "technical"),
                ("上海市税务局关于做好企业重组备案工作的通知", "local_normative"),
                ("企业重组有关的几点提示", "unknown")):
            g = E.grade({"title": title, "status": "全文有效"}, at="2026-09-29")
            self.assertEqual(want_low, g["rank"], title)
            self.assertLess(g["score"], E.PRIMARY_THRESHOLD,
                            "{} 判成 {}，分数 {}".format(title, g["rank_label"],
                                                       g["score"]))
        # 可靠性标记优先于这条线：medium 的条目分数清成 0
        g = E.grade({"title": self.ANN, "status": "全文有效",
                     "_reliability": "medium"}, at="2026-09-29")
        self.assertEqual(0.0, g["score"])

    def test_the_old_line_would_have_failed_this(self):
        """自检：拿 60 当线，现行有效的公告就进不了依据层。"""
        self.assertLess(E.authority_score("normative", "effective"), 60.0)
        self.assertGreaterEqual(E.authority_score("normative", "effective"),
                                E.PRIMARY_THRESHOLD)

    def test_pending_without_effective_date_cannot_be_a_basis(self):
        """判 --at 时"没查到施行日期"不等于"日期一定在过去"。

        时点分支原先只要没取到 effective_date 就落回 effective，一份标着尚未
        生效的公告在被推荐用法（带 --at）下会变成 55 分主依据；只有日期确认
        早于观察时点才允许转正。
        """
        for it in ({"status": "尚未生效"},
                   {"status": "尚未生效", "publish_date": "2026-07-08"},
                   {"status": "未生效", "effective_date": ""}):
            g = E.grade(dict(it, title=self.ANN), at="2026-09-29")
            self.assertEqual("pending", g["validity"], it)
            self.assertLess(g["score"], E.PRIMARY_THRESHOLD, it)
        # 有了日期且早于观察时点才转正，别把这一档一起压死
        g = E.grade({"title": self.ANN, "status": "尚未生效",
                     "effective_date": "2026-01-01"}, at="2026-09-29")
        self.assertEqual("effective", g["validity"])
        self.assertGreaterEqual(g["score"], E.PRIMARY_THRESHOLD)
        # 不传 --at 时本来就是纯状态判定，pending 不受影响
        self.assertEqual("pending",
                         E.judge_validity({"status": "尚未生效"}, "")["validity"])


class TestValidityCorroboratedByCitation(unittest.TestCase):
    """「财税文件」那一栏根本不录时效，援引证据补这一格。

    2026-09-29 用《关于企业重组业务所得税处理有关征管问题的公告》（2026年第13号）
    这道真题撞出来的：59 号、109 号是点名公告正文列明的制定依据，却是这道题的
    上位规则；因为法规库这两条的时效录入项为空（财税〔2003〕16 号那个位置上是
    字符串 "null"），它们全被压在依据线以下，答案只能说"只能参考"。详情页也只有
    成文日期，没有时效栏可回填。
    """

    ANN = "国家税务总局关于企业重组业务所得税处理有关征管问题的公告"
    BODY = ("根据《中华人民共和国企业所得税法》及其实施条例、《财政部 国家税务总局关于"
            "企业重组业务企业所得税处理若干问题的通知》（财税〔2009〕59号）、《财政部 国家"
            "税务总局关于促进企业重组有关企业所得税处理问题的通知》（财税〔2014〕109号）"
            "等文件规定，现对企业重组业务所得税处理有关征管问题公告如下：\n"
            "一、对于企业合并、分立业务……")
    AT = "2026-09-29"

    def _rows(self):
        return [
            {"title": self.ANN, "status": "全文有效", "document_number": "国家税务总局公告2026年第13号",
             "url": "u13", "_cited_target": True, "body": self.BODY},
            {"title": "财政部 国家税务总局关于企业重组业务企业所得税处理若干问题的通知",
             "document_number": "财税〔2009〕59号", "url": "u59"},
            {"title": "财政部 国家税务总局关于促进企业重组有关企业所得税处理问题的通知",
             "document_number": "财税〔2014〕109号", "url": "u109"},
            {"title": "财政部 国家税务总局关于营业税若干政策问题的通知",
             "document_number": "财税〔2003〕16号", "url": "u16"},
        ]

    def test_cited_basis_docs_become_citable(self):
        rows = self._rows()
        tagged = ANS.corroborate_validity_from_target(rows, self.AT)
        self.assertEqual(["财政部 国家税务总局关于企业重组业务企业所得税处理若干问题的通知",
                          "财政部 国家税务总局关于促进企业重组有关企业所得税处理问题的通知"],
                         tagged)
        # 没被援引的那份不补，正文里出现的《企业所得税法》也不动（它本来就有效）
        self.assertNotIn("u16", [r["url"] for r in rows if r.get("corroborated_by")])
        for r in rows:
            if r.get("corroborated_by") != self.ANN:
                continue
            g = E.grade(r, at=self.AT)
            self.assertEqual("effective", g["validity"])
            self.assertGreaterEqual(g["score"], E.PRIMARY_THRESHOLD)
            self.assertIn("制定依据", g["validity_note"])

    def test_explicit_status_beats_citation_evidence(self):
        """明文写着废止或未生效的，援引证据不能翻案。"""
        for status, want in (("全文废止", "repealed"), ("尚未生效", "pending")):
            it = {"title": "财政部 国家税务总局关于某事项的通知", "status": status,
                  "corroborated_by": self.ANN}
            self.assertEqual(want, E.judge_validity(it, self.AT)["validity"], status)

    def test_unusable_target_corrobs_nothing(self):
        """援引方自己时效不明、或是解读件，就不具备给别的文件作证的资格。"""
        rows = self._rows()
        rows[0]["status"] = ""
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))
        rows = self._rows()
        rows[0]["_is_interpretation"] = True
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))
        rows = self._rows()
        rows[0].pop("body")
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))

    def test_preamble_only_is_parsed(self):
        """只读"根据……规定"那一段，正文条款里援引的文件不算制定依据。"""
        rows = self._rows()
        rows[0]["body"] = ("根据《中华人民共和国企业所得税法》规定，现公告如下：\n"
                           "四、本公告自发布之日起施行，《财政部 国家税务总局关于企业重组"
                           "业务企业所得税处理若干问题的通知》（财税〔2009〕59号）同时废止。")
        self.assertEqual([], ANS.corroborate_validity_from_target(rows, self.AT))

    def test_null_placeholder_is_not_a_status(self):
        self.assertEqual("", WS.aging_of("null"))
        self.assertEqual("", WS.aging_of(" NULL "))
        self.assertEqual("", WS.aging_of(None))
        self.assertEqual("全文有效", WS.aging_of("全文有效"))


# ── 轮次取数失败与"这一轮 0 条"必须分得开 ──────────────────────────────────
class TestRoundFailureIsNotSilent(unittest.TestCase):
    """源挂了不许被记成 found=0，否则主依据会静默降级而输出看不出来。

    2026-09-29 用《税收征管法》修订草案那道题实测：NPC 那一轮静默失败时，
    90 分的现行有效法律不见了，顶上【主依据】的是 70 分的《个体工商户建账
    管理暂行办法》，而 `rounds_done` 只写着 found=0、`errors` 是空的——
    读的人无法区分"库里没有"和"这一轮没取回来"。
    """

    Q = "增值税小规模纳税人月销售额10万，应纳增值税多少"
    规章 = {"title": "个体工商户建账管理暂行办法", "category": "税务部门规章",
            "status": "现行有效", "url": "http://x/规章"}
    法律 = {"title": "中华人民共和国税收征收管理法", "category": "法律",
            "status": "现行有效", "url": "http://x/法律"}

    def _gather(self, fail=(), with_law=True):
        """把所有源换成打桩客户端：fail 里的源报"取数失败"，其余回条目。"""
        def make(src):
            def fn(term, size):
                if src in fail:
                    return [], "响应不是 JSON: Expecting value: line 1 column 1 (char 0)"
                return [dict(self.规章)] + ([dict(self.法律)] if with_law and src == "npc" else []), ""
            return fn
        with mock.patch.dict(ANS._FETCHERS,
                             {s: make(s) for s in ("npc", "fgk", "shui5", "wechat", "legis")}):
            return ANS.gather(self.Q, at="2026-09-29")

    def test_source_error_names_the_round_and_the_source(self):
        g = self._gather(fail=("npc",))
        self.assertTrue(any("第1轮 npc 取数失败" in e for e in g["errors"]), g["errors"])
        self.assertIn("npc", g["rounds_done"][0]["failed"], g["rounds_done"])
        a = ANS.compose(g)
        self.assertTrue(a["fetch_errors"] and a["fetch_note"], a["fetch_note"])
        # 这一趟只剩部门规章：主依据确实被降级，靠 fetch_note 才读得出来
        self.assertEqual(self.规章["title"], a["primary"]["title"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ANS._print_answer(a)
        self.assertIn("【取数失败】", out.getvalue())
        self.assertIn("不等于库里没有", out.getvalue())

    def test_clean_empty_round_stays_silent(self):
        """干净的空结果不许编出一条失败来——两种"没有"分开，也得各自成立。"""
        with mock.patch.dict(ANS._FETCHERS,
                             {s: (lambda t, n: ([], ""))
                              for s in ("npc", "fgk", "shui5", "wechat", "legis")}):
            g = ANS.gather(self.Q, at="2026-09-29")
        self.assertEqual([], g["errors"])
        self.assertEqual([], [f for r in g["rounds_done"] for f in r["failed"]])
        a = ANS.compose(g)
        self.assertEqual("", a["fetch_note"])
        self.assertTrue(a["evidence_gap"], "一条依据都没取到时该说证据缺口，不是取数失败")

    def test_legislation_lane_items_cannot_become_primary(self):
        """立法过程实位取回的条目一律带 medium，定级层据此拒绝它当主依据。

        人大网站的草案与审议公告是"找到文本的线索"，本身不是依据；不标记的话，
        它会以"未标时效"混进参考层，读起来像一条可以引的规定。
        """
        page = {"results": [{"title": "法律草案审议 中国人大网",
                             "url": "http://www.npc.gov.cn/npc/c2/x.html"}], "total": 1}
        with mock.patch.object(ANS.S360, "so360_search", return_value=page) as hit:
            rows, err = ANS._legis_round("税收征收管理法 修订草案", 8)
        self.assertEqual("", err)
        self.assertEqual("npc.gov.cn", hit.call_args.kwargs["site"])
        self.assertEqual("medium", rows[0]["_reliability"])
        graded = [E.grade(r, at="2026-09-29") for r in rows]
        self.assertEqual(0.0, graded[0]["score"])
        best = E.pick_primary(graded)
        self.assertEqual("", best.get("title", ""), "立法过程线索不能被选成主依据")
        self.assertIn("立法过程线索", best["_why"])
        # 掺进一条正常依据时，主依据让位给那条，线索仍不能靠兜底上位
        mixed = graded + [E.grade({"title": "中华人民共和国税收征收管理法",
                                   "status": "全文有效"}, at="2026-09-29")]
        self.assertNotEqual("法律草案审议 中国人大网",
                            E.pick_primary(mixed)["title"])

    def test_draft_round_also_searches_shui5_and_wechat(self):
        """草案那一轮必须连带搜税屋与公众号：草案解读文章只活在这两个源里。

        2026-09-29 实测缺陷：问《税收征管法》修订草案，立法取证轮只排了
        sources=["legis"]（人大网站内），税屋与公众号一次都没查。手工验证
        search_shui5("税收征管法 修订草案") 首条即国务院法制办征求意见稿通知、
        含"修订重点逐条解读"多篇——等于把最有料的实务解读整层漏掉。
        """
        plan = ANS.build_plan("请解读《税收征管法》修订草案，已提请全国人大常委会审议")
        rnd = [r for r in plan["rounds"] if "legis" in r["sources"]]
        self.assertEqual(1, len(rnd), "应有且仅有一轮立法取证")
        self.assertEqual({"legis", "shui5", "wechat"} & set(rnd[0]["sources"]),
                         {"legis", "shui5", "wechat"},
                         "立法取证轮要覆盖人大网+税屋+公众号三个源")
        self.assertEqual("legis", rnd[0].get("term_key"),
                         "这一轮三个源统一用草案词，不用各源的主题短词")

    def test_draft_round_uses_draft_term_not_short_term(self):
        """税屋/公众号在草案轮拿到的词必须含"草案"，否则搜回的是现行规定。

        打桩三个 fetcher，跑 gather，抓每源被调用时收到的检索词。
        """
        seen = {}

        def make(src):
            def fn(term, size):
                seen.setdefault(src, []).append(term)
                return [], ""
            return fn

        Q = "请解读《税收征管法》修订草案，已提请全国人大常委会审议"
        with mock.patch.dict(ANS._FETCHERS,
                             {s: make(s) for s in ("npc", "fgk", "shui5",
                                                   "wechat", "legis")}):
            ANS.gather(Q, at="2026-09-29")
        # 草案轮与非草案轮的 shui5 调用词并存，取含"草案"的那一次
        terms_used = seen.get("shui5", [])
        self.assertTrue(any("草案" in t for t in terms_used),
                        f"shui5 在草案轮应拿到含'草案'的词，实际 {terms_used}")
        self.assertTrue(any("草案" in t for t in seen.get("wechat", [])),
                        f"wechat 在草案轮应拿到含'草案'的词，实际 {seen.get('wechat')}")


# ── 返回形态守门：注解写了 tuple，每一条 return 就得真的给元组 ────────────────
def _tuple_contract_offenders(path):
    """扫一个文件，列出 `-> tuple[...]` 却返回别的形状的 [(函数名, 行号, 返回表达式)]。

    只看直接写在该函数体里的 return，嵌套函数与 lambda 内部的归它们自己的注解管，
    不然一个元组函数里写个返回字符串的闭包会被误报。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for fn in (n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        ann = fn.returns
        if not ann or not ast.unparse(ann).startswith("tuple["):
            continue
        arity = (len(ann.slice.elts)
                 if isinstance(ann, ast.Subscript) and isinstance(ann.slice, ast.Tuple)
                 else None)
        inner = set()
        for sub in ast.walk(fn):
            if sub is fn or not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                 ast.Lambda)):
                continue
            inner.update(id(x) for x in ast.walk(sub))
        for r in (n for n in ast.walk(fn)
                  if isinstance(n, ast.Return) and n.value is not None
                  and id(n) not in inner):
            is_tuple = isinstance(r.value, ast.Tuple)
            right_arity = arity is None or (is_tuple and len(r.value.elts) == arity)
            if not (is_tuple and right_arity):
                offenders.append((fn.name, r.lineno, ast.unparse(r.value)))
    return offenders


class TestTupleReturnContract(unittest.TestCase):
    """`-> tuple[list, str]` 这类函数，成功分支必须和失败分支给同样多的值。

    起因是真缺陷：`tax_server._search_web_broad` 注解与文档都写着返回
    (结果, 拦截说明)，失败分支给了二元组，成功分支却 `return results` 交回裸列表，
    调用方按二元组解包。列表长度说了算：1 条与 3 条都抛 ValueError，被调用方的
    `except Exception` 吞成空结果；正好 2 条时两个名字各接住一个 dict，接口回 500
    （实测报错原文 `'str' object has no attribute 'get'`）。
    当时的路由用例把桩打在 `_search_web_broad` 上、桩形是二元组，与被测函数的真实
    返回形态不一致，所以这条断路在测试里一直是绿的。

    这一条把它变成编译期就拦得住的形状：全项目扫一遍，一处不合规门禁就红。
    """

    def test_scripts_and_tests_honour_their_tuple_annotations(self):
        offenders = []
        for d in ("scripts", "tests"):
            for p in sorted((ROOT / d).glob("*.py")):
                for name, lineno, expr in _tuple_contract_offenders(p):
                    offenders.append("{}:{}  {} 返回 {}".format(p.name, lineno, name, expr))
        self.assertEqual(
            [], offenders,
            "注解是 tuple 却返回别的形状（调用方按元组解包会静默丢数据或报 500）：\n  "
            + "\n  ".join(offenders))

    def test_scanner_sees_the_shape_that_caused_the_bug(self):
        """自检：把当年那一行喂进扫描器，必须报出来；不报就说明守门是假的。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "bad.py"
            tmp.write_text(
                "def ok(q):\n"
                "    if q:\n"
                "        return [], '拦了'\n"
                "    return [], ''\n"
                "\n"
                "def bad(q):\n"
                "    results = []\n"
                "    return results\n",
                encoding="utf-8",
            )
            # 两份都注解成二元组，只有 bad 的返回形状不对
            src = tmp.read_text(encoding="utf-8").replace(
                "def ok(q):", "def ok(q) -> tuple[list, str]:").replace(
                "def bad(q):", "def bad(q) -> tuple[list, str]:")
            tmp.write_text(src, encoding="utf-8")
            offenders = _tuple_contract_offenders(tmp)
        self.assertEqual([("bad", 8, "results")], offenders)


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
