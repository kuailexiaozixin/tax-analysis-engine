#!/usr/bin/env python3
"""⑩「源缺陷」六条的离线用例。不联网、不开浏览器。

这六条是人肉规则——程序解决不了、用的时候要留神：

  1. NPC 有限流，检索串行跑；评测时不要并行别的 NPC 检索
  2. 360 被封时地方口径与税屋这一层是空的，要在答案里明说、不要拿别的源顶替
  3. fgk 翻得越深相关性越差，深页条目必须回 L1 核对
  4. 公众号依赖会话，并发加压会触发反爬
  5. `_reliability: medium` 说的是这条的召回方式，要落成一句具体提醒
  6. `_reliability: low` 同样只提醒核对，不折算成分数、不从挑选里剔除

"靠人记住"等于没有约束：第 5、6 条若只在输出里印一行、定级层不读它，标了 low
的条目照样能被挑成主依据、还打出"可作依据引用（法律）"。六条
都落进代码，这里逐条钉住，并附带自检（确认规则不是永远绿的）。
"""

import ast
import contextlib
import io
import json
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

import tax_ctax as CT      # noqa: E402
import tax_detail as DT      # noqa: E402
import tax_answer as ANS     # noqa: E402
import tax_evidence as E     # noqa: E402
import tax_fgk as FGK        # noqa: E402
import tax_formatter as FMT  # noqa: E402
import tax_http              # noqa: E402
import tax_search as T       # noqa: E402
import tax_shui5 as SH       # noqa: E402
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
    """翻得越深越松，深页条目只可用于定位。这件事不落进代码就靠人记页码。"""

    def _scan(self, pages):
        """pages: {页码: 该页法规库条目数}"""
        def fake_search(keyword, page=1, size=10, filters=None, **opts):
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
                               return_value=wechat if wechat is not None else ok(0)), \
             mock.patch.object(AGG, "search_ctax", return_value=ok(1)):
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
        # 分母取的是这一轮实际发出的源名单长度，不是文案里的常量：名单加到六枚，
        # 这句就得跟着改，改了才发现漏了。打桩给的是 npc／chinatax／ctax 三路有命中。
        self.assertIn("3/6", r["degraded_note"], r["degraded_note"])
        self.assertIn("税屋", r["degraded_note"])


# ── ②' 网页端要把缺口显示出来，不能显示成普通的 0 条 ──────────────────────
class TestFrontendShowsDegradedNote(unittest.TestCase):
    def test_render_results_shows_degraded_note(self):
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        self.assertIn("degraded_note", body)
        self.assertIn("esc(result.degraded_note)", body)

    def test_empty_state_keeps_the_causes_apart(self):
        """空清单有四种成因，页面要分流、原样显示，不得全塌成"未找到"。

        对应的缺陷：翻过末页、维度拼窄、接口没给清单是"没取到/取法不对"，
        取数失败（_fetch_failed）更是服务侧故障。都渲染成"未找到相关政策"就
        等于页面替用户说了"库里没有这份文件"——用户会换个词重搜，而真相是
        要么稍后再试、要么把维度放宽。分流顺序按"越靠近真相越先看"，取数失败
        排最前，不能和其余三种共用未找到那一句。
        """
        body = HTML[HTML.index("function renderResults(data){"):
                    HTML.index("KEYWORD HIGHLIGHT")]
        # 取数失败走独立分支：有专门的标题，且这一句排在"未找到"之前
        self.assertIn("result._fetch_failed", body)
        self.assertIn("取数失败", body)
        self.assertLess(body.index("result._fetch_failed"), body.index("未找到相关政策"),
                        "取数失败被并进了未找到分支")
        # 其余三种成因仍逐字透出：filter_note / empty_reason / error 都进 why 链
        self.assertIn("result._filter_note", body)
        self.assertIn("result._empty_reason", body)
        # 透出的句子来自外部检索结果，必须走 esc() 才拼进 HTML
        self.assertIn("esc(why)", body)

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
        """把同一套断言作用在违规写法上，确认它拦得住。"""
        for word in ("废止", "尚未生效", "修改", "有效"):
            self.assertNotIn(word, self.OLD_BADGE, word)
        self.assertNotIn("item.status||", self.OLD_BADGE)
        self.assertNotIn("document_number", self.OLD_BADGE)


# ── ⑤⑥ 定级层读到 `_reliability` 后要给出可核对的提醒 ──────────────────────
class TestReliabilityBecomesAReminder(unittest.TestCase):
    """来源标记要说清"疑在哪一处、要核对什么"，不许再用降权代替核对。

    断言的是提醒，不是否决。"标了 low 就把分清零、整组不许挑主依据"是在限制材料
    的作用——读者只看到一个小标签，既不知道为什么，
    也无从下手。同样的来源信息写成逐条提醒，材料照常参与分层。
    """

    LAW = "中华人民共和国企业所得税法"

    def test_low_carries_a_checkable_reminder(self):
        g = E.grade({"title": self.LAW, "_reliability": "low"})
        self.assertEqual("low", g["reliability"])
        self.assertIn(E.RELIABILITY_CAVEAT["low"], g["caveats"])
        self.assertNotIn("不得作为依据引用", g["citation_hint"])

    def test_medium_reminder_is_the_sources_own_sentence(self):
        """同是 medium，三条来源各说各的原因，提醒必须用来源自己那句。

        `medium` 在本仓库有三个出处：NPC 正文检索按全文分词命中、总局法规库第 2
        页起排序变松、立法过程件不在六个源的收录范围内。定级层若按档位配一句
        固定话（"这一条来自清单靠后的页位"），三处里只有法规库那处对得上，另两处
        给的是假提醒——全文检索和站内检索没有"页位"这件事。

        变异自检：把 `tax_evidence._caveats` 里那句改回
        `RELIABILITY_CAVEAT[rel] + "；" + rel_note`，`assertEqual` 与
        `not in` 两组断言同时报红；把 `RELIABILITY_CAVEAT["medium"]` 改写成带
        某个具体机制的说法，最后一条断言报红。
        """
        fgk = E.grade({"title": self.LAW, "_reliability": "medium",
                       "_reliability_note": FGK.FGK_DEEP_NOTE.format(page=3)},
                      topic="企业所得税")
        npc = E.grade({"title": self.LAW, "_reliability": "medium",
                       "_reliability_note": T.RELIABILITY_NOTES["medium"]},
                      topic="企业所得税")
        self.assertEqual(FGK.FGK_DEEP_NOTE.format(page=3), fgk["caveats"][-1])
        self.assertEqual(T.RELIABILITY_NOTES["medium"], npc["caveats"][-1])
        for g in (fgk, npc):
            self.assertTrue(all(E.RELIABILITY_CAVEAT["medium"] not in c
                                for c in g["caveats"]), g["caveats"])
        self.assertNotIn("第 3 页", "；".join(npc["caveats"]),
                         "全文检索的条目不该被告知自己来自法规库的第 3 页")

    def test_marker_without_a_note_falls_back_to_a_neutral_reminder(self):
        """只有档位、没有原因说明时，兜底句不许断言某个具体机制。"""
        g = E.grade({"title": self.LAW, "_reliability": "medium"})
        self.assertIn(E.RELIABILITY_CAVEAT["medium"], g["caveats"])
        for word in ("页位", "分词", "页"):
            self.assertNotIn(word, E.RELIABILITY_CAVEAT["medium"], word)
            self.assertNotIn(word, E.RELIABILITY_CAVEAT["low"], word)

    def test_unmarked_item_gets_no_reliability_reminder(self):
        g = E.grade({"title": self.LAW, "status": "全文有效"}, topic="企业所得税")
        self.assertEqual("ok", g["reliability"])
        self.assertEqual([], [c for c in g["caveats"]
                              if c in (E.RELIABILITY_CAVEAT["low"],
                                       E.RELIABILITY_CAVEAT["medium"])])
        self.assertEqual("direct", g["role"])

    def test_reliability_does_not_change_the_pick(self):
        """标了 low 的《企业所得税法》不再被整组剔除，也不再因此让位。

        变异自检：把 `_reliability` 塞回 `_order_key`（例如 low 时 tier 取 0），
        这一条与下一条都会报红——那正是本文件不许回来的做法。
        """
        g = E.pick_primary([
            E.grade({"title": self.LAW, "_reliability": "low",
                     "_reliability_note": "全文检索偏题"}, topic="企业所得税"),
            E.grade({"title": "国家税务总局公告2018年第28号"}, topic="企业所得税"),
        ])
        self.assertEqual(self.LAW, g["title"])
        self.assertEqual("low", g["reliability"])
        self.assertIn("全文检索偏题", g["_why"])

    def test_pick_is_by_role_not_by_marker(self):
        """两条在能不能引、角色、时效、层级、主题对应上全相等时，标记不参与排队。

        先断言两条真的并列，否则这条测的是排队规则而不是标记——上一次它就用一份
        未定级的通知去比一份总局公告，角色本来就不同，结论说明不了任何事。

        变异自检：把 `_reliability` 塞进 `_order_key`（low 排后），两次断言里
        必有一次报红。
        """
        a = E.grade({"title": "国家税务总局公告2018年第28号",
                     "_reliability": "low"}, topic="企业重组")
        b = E.grade({"title": "国家税务总局公告2019年第11号"}, topic="企业重组")
        self.assertEqual(E._order_key(a), E._order_key(b),
                         (a["rank"], a["role"], a["validity"], a["on_topic"]))
        self.assertEqual("low", a["reliability"])
        self.assertEqual(a["title"], E.pick_primary([a, b])["title"])
        self.assertEqual(b["title"], E.pick_primary([b, a])["title"])

    def test_every_candidate_keeps_its_own_caveats(self):
        """全组都带标记时照样挑得出来，且落选项在 `_runners_up` 里带着提醒。"""
        best = E.pick_primary([E.grade({"title": self.LAW, "_reliability": "low"},
                                       topic="企业所得税")])
        self.assertEqual(self.LAW, best["title"])
        self.assertIn(E.RELIABILITY_CAVEAT["low"], best["_why"])
        run = E.pick_primary([
            E.grade({"title": self.LAW, "status": "全文有效"}, topic="企业所得税"),
            E.grade({"title": "中华人民共和国增值税暂行条例", "status": "全文有效",
                     "_reliability": "medium"}, topic="企业所得税"),
        ])["_runners_up"]
        self.assertIn(E.RELIABILITY_CAVEAT["medium"], run[0]["caveats"])


# ── ⑤'' 聚合层带下来的标记必须说得出原因 ──────────────────────────────────
class TestAggregatorCarriesTheReminder(unittest.TestCase):
    """整源标记按定义就是"这一窗条目共同的取回方式"，逐条带上不算冤枉。

    它只在源自己的输出里印一次、不带进聚合的话，聚合清单上看不出这些条目来自
    全文检索，定级层与界面都收不到这句提醒。

    变异自检：把 `tax_aggregator` 里 `item["_reliability_note"] = source_note`
    那两行删掉，第一条断言报红；把命令行摘要改回按档位配一句固定话（低档写
    "不得作为权威依据引用"），第二条报红。
    """

    NPC_NOTE = "NPC 正文检索按全文分词命中，可能偏题；请回到标题检索确定条文归属"
    DEEP_NOTE = "取自总局检索第 3 页：深页条目可能只是沾了检索词"

    def _agg(self):
        npc = {"total": 1, "results": [{"title": "中华人民共和国增值税暂行条例",
                                        "url": "http://x/npc"}],
               "_reliability": "medium", "_reliability_note": self.NPC_NOTE}
        chinatax = {"total": 1, "results": [{"title": "国家税务总局公告2025年第3号",
                                             "url": "http://x/ct"}]}
        empty = {"total": 0, "results": []}
        with mock.patch.object(AGG, "search_tax", return_value=npc), \
             mock.patch.object(AGG, "search_chinatax", return_value=chinatax), \
             mock.patch.object(AGG, "so360_search", return_value=empty), \
             mock.patch.object(AGG, "search_shui5", return_value=empty), \
             mock.patch.object(AGG, "search_wechat", return_value=empty), \
             mock.patch.object(AGG, "search_ctax", return_value=empty):
            return AGG.aggregate_search("增值税", size=5, scope="fulltext")

    def test_source_level_marker_reaches_each_item_with_its_note(self):
        r = self._agg()
        row = [i for i in r["items"] if i["_source"] == "npc"][0]
        self.assertEqual("medium", row["_reliability"])
        self.assertEqual(self.NPC_NOTE, row["_reliability_note"])
        # 没带标记的那一路不能被连坐
        self.assertNotIn("_reliability",
                         [i for i in r["items"] if i["_source"] == "chinatax"][0])

    def test_cli_prints_the_reminder_text_not_a_level_ban(self):
        """命令行摘要印每条自带的提醒原文；按档位配死一句禁令是旧做法。"""
        res = {"searched_at": "2026-10-03 00:00:00",
               "source_summary": {"npc": 1, "chinatax": 1},
               "items": [{"_source": "npc", "title": "A",
                          "_reliability": "medium", "_reliability_note": self.NPC_NOTE},
                         {"_source": "chinatax", "title": "B",
                          "_reliability": "medium", "_reliability_note": self.DEEP_NOTE},
                         {"_source": "npc", "title": "C",
                          "_reliability": "low"}],
               "gaps": [], "errors": {}}
        out = io.StringIO()
        with mock.patch.object(AGG, "aggregate_search", return_value=res), \
             mock.patch.object(sys, "argv", ["tax_aggregator.py", "增值税"]), \
             contextlib.redirect_stdout(out):
            AGG.main()
        text = out.getvalue()
        self.assertIn(self.NPC_NOTE, text)
        self.assertIn(self.DEEP_NOTE, text)
        self.assertIn("未写明存疑在哪一处", text, "只有档位没有说明时要明说缺的是什么")
        for ban in ("不得作为权威依据引用", "只能参考", "不得作为依据引用"):
            self.assertNotIn(ban, text, ban)


# ── ⑤' 显示层也不许把标记吞掉 ──────────────────────────────────────────────
class TestFormatterShowsReliability(unittest.TestCase):
    """标记进了数据、却印不出来，等于没标记。

    `tax_formatter.py --mode single` 必须读响应级的 `_reliability`：把 NPC 全文
    检索（恒带 medium）的结果灌进去，输出里一个字都不提"可能偏题"，读者只看到
    "共 N 条法规"，会当成能引用的清单。多源归并那条路径印了，单源这条不许漏。
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

    翻页基准修正后清单里同时出现了公告原文、公告的解读、以及公告正文援引的
    配套文件（《关于企业重组业务所得税处理有关征管问题的公告》，2026年第13号），
    定级层一照面就把解读按被解读文件的形态判成了规范性文件。
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
        self.assertEqual(55, r["tier"])
        # 判成"未定性"时层级掉到 0、角色掉到待核对线索——那才是这个用例要拦的
        # 回归，所以两条都断言，而不是断言掉了几分
        g = E.grade({"title": t}, topic="企业重组")
        self.assertEqual("normative", g["rank"])
        self.assertEqual(55, g["rank_tier"])
        self.assertEqual("direct", g["role"])
        # 主题词对不上时它仍是有层级的法定文件，落"上位依据与授权"：
        # "待核对线索"只留给层级与主题两头都空着的那类（见 `role_of`）。
        self.assertEqual("superior",
                         E.grade({"title": t}, topic="留抵退税")["role"])
        off = E.grade({"title": t}, topic="留抵退税")
        self.assertIs(False, off["on_topic"])
        self.assertIn("标题与正文里都没有本题的主题词", "；".join(off["caveats"]))


class TestNormativeIsABasisNotReference(unittest.TestCase):
    """总局公告是本题的直接规定，不该被任何一根"参考"线挡在依据之外。

    断言的是角色，不是"分数够不够 50 那条线"——那根线是把能不能引和该不该让位
    乘成一个分数的合成判据，两根轴各判各的才分得开。规范性文件在与本题对得上、
    且没废止时就是"本题的直接规定"，并且挑得主依据，压过层级更高的法律。
    """

    ANN = "国家税务总局关于企业重组业务所得税处理有关征管问题的公告"

    def test_current_announcement_is_a_direct_basis(self):
        g = E.grade({"title": self.ANN, "status": "全文有效"},
                    at="2026-09-29", topic="企业重组")
        self.assertEqual("effective", g["validity"])
        self.assertEqual("direct", g["role"])

    def test_a_higher_tier_law_does_not_steal_the_headline(self):
        """问企业重组的征管口径时，头条要给规定这件事的公告，不给《企业所得税法》。

        变异自检：把 `pick_primary` 的排序键换成单按 `rank_tier` 降序，这一条
        立刻报红——法律 90 压公告 55。旧的可引用性公式就是这个形状。
        """
        g = E.grade_all([
            {"title": "中华人民共和国企业所得税法", "status": "全文有效"},
            {"title": self.ANN, "status": "全文有效"},
        ], at="2026-09-29", topic="企业重组")
        best = E.pick_primary(g)
        self.assertEqual(self.ANN, best["title"])
        self.assertEqual("direct", best["role"])

    def test_validity_problem_becomes_a_reminder_not_a_ban(self):
        """时效那一栏照常是唯一带后果的轴，但后果写成"要核对什么"。"""
        for status, want in (("", "unknown"), ("尚未生效", "pending"),
                             ("全文废止", "repealed")):
            g = E.grade({"title": self.ANN, "status": status},
                        at="2026-09-29", topic="企业重组")
            self.assertEqual(want, g["validity"], status)
            self.assertIn(E.VALIDITY_CAVEAT[want], g["caveats"], status)
        # 已废止的那条角色转成政策沿革，仍在分层里列出，没有被删掉
        self.assertEqual("history",
                         E.grade({"title": self.ANN, "status": "全文废止"},
                                 at="2026-09-29", topic="企业重组")["role"])

    def test_practice_layer_keeps_its_own_role(self):
        """税屋、公众号与官方解读各归"执行口径"，不再被压成不能引用的那堆。

        判的次序是"废止 → 实务形态 → 主题对得上 → 层级未定"：一份层级判不出来
        的稿子只要字面上就在讲本题，它就是"本题的直接规定"，层级未定那件事由提醒
        那几句话说清；层级与主题都落空的才落"待核对线索"。
        """
        cases = (
            ({"title": "关于企业重组业务所得税处理有关征管问题的公告的解读"},
             "technical", "practice"),
            ({"title": "解读：企业重组特殊性税务处理怎么备案"},
             "technical", "practice"),
            ({"title": "六税两费减免的十个易错点", "source": "税屋 (shui5.cn)"},
             "interpretation", "practice"),
            ({"title": "实务问答：留抵退税的口径", "source": "微信公众号 (搜狗微信)"},
             "technical", "practice"),
            ({"title": "上海市税务局关于做好企业重组备案工作的通知"},
             "local_normative", "direct"),
            ({"title": "企业重组有关的几点提示"}, "unknown", "direct"),
            ({"title": "几点提示"}, "unknown", "unmatched"),
        )
        for item, want_rank, want_role in cases:
            g = E.grade(dict(item, status="全文有效"), at="2026-09-29",
                        topic="企业重组")
            self.assertEqual(want_rank, g["rank"], item["title"])
            self.assertEqual(want_role, g["role"], item["title"])
        # 层级未定但主题对得上的那条，提醒里必须写着"层级没判出来"这件事
        g = E.grade({"title": "企业重组有关的几点提示", "status": "全文有效"},
                    at="2026-09-29", topic="企业重组")
        self.assertEqual("标题形态与来源都不足以定级", g["rank_by"])

    def test_pending_without_effective_date_cannot_be_read_as_in_force(self):
        """判 --at 时"没查到施行日期"不等于"日期一定在过去"。

        时点分支若只要没取到 effective_date 就落回 effective，一份标着尚未生效
        的公告会在带 --at 的用法下直接顶成主依据；只有日期确认早于观察时点才
        允许转正。转正那一条另配一句"生效日不晚于观察时点"的常规交代，它不是
        提醒，所以 `qualified` 为假。
        """
        for it in ({"status": "尚未生效"},
                   {"status": "尚未生效", "publish_date": "2026-07-08"},
                   {"status": "未生效", "effective_date": ""}):
            g = E.grade(dict(it, title=self.ANN), at="2026-09-29", topic="企业重组")
            self.assertEqual("pending", g["validity"], it)
            self.assertIn(E.VALIDITY_CAVEAT["pending"], g["caveats"], it)
        g = E.grade({"title": self.ANN, "status": "尚未生效",
                     "effective_date": "2026-01-01"}, at="2026-09-29",
                    topic="企业重组")
        self.assertEqual("effective", g["validity"])
        self.assertNotIn("生效日 2026-01-01 不晚于观察时点 2026-09-29",
                         g["caveats"], "这是判据来源的交代，不该占一条提醒")
        # 不传 --at 时本来就是纯状态判定，pending 不受影响
        self.assertEqual("pending",
                         E.judge_validity({"status": "尚未生效"}, "")["validity"])


class TestConstitutionIsNotTheHeadline(unittest.TestCase):
    """宪法位阶最高，但不能顶当主依据——位阶×时效的乘法会给它 100×1.0 的全场最高分。"""

    def test_flagged_and_yielded_to_the_law(self):
        c = E.grade({"title": "中华人民共和国宪法", "category": "宪法"},
                    at="2026-09-29", topic="税收法定")
        self.assertEqual(100, c["rank_tier"])
        self.assertTrue(c["not_directly_quotable"])
        self.assertIn(E.CONSTITUTION_NOTE, c["caveats"])
        self.assertEqual(E.CONSTITUTION_NOTE, c["citation_hint"].split("；")[1])
        mixed = [c, E.grade({"title": "中华人民共和国税收征收管理法",
                             "status": "全文有效"}, at="2026-09-29",
                            topic="税收法定")]
        self.assertEqual("中华人民共和国税收征收管理法",
                         E.pick_primary(mixed)["title"])

    def test_alone_it_is_picked_but_told_to_go_down_to_law(self):
        """一组里只有宪法时仍要挑它，同时说明它对税务机关不构成征税依据。

        变异自检：若改成"宪法一律不挑"，这一条报红——那时候答案会连一条
        规定税收法定原则的文件都提不出来，而题问的正是这个原则。
        """
        only = [E.grade({"title": "中华人民共和国宪法", "category": "宪法"},
                        at="2026-09-29", topic="税收法定")]
        best = E.pick_primary(only)
        self.assertEqual("中华人民共和国宪法", best["title"])
        self.assertIn("不构成征税依据", best["_why"])


class TestNoSynthesisedCitationScore(unittest.TestCase):
    """不设可引用性合成分数：留一个字段名就会被下游重新拿去排队。"""

    def test_grade_exposes_the_axes_not_a_product(self):
        g = E.grade({"title": "中华人民共和国企业所得税法", "status": "全文有效"},
                    at="2026-09-29", topic="企业所得税")
        for key in ("rank_tier", "validity", "on_topic", "role", "caveats",
                    "not_directly_quotable"):
            self.assertIn(key, g)
        self.assertNotIn("score", g)
        self.assertNotIn("score", E.rank_of("中华人民共和国宪法"))

    def test_removed_symbols_stay_removed(self):
        for name in ("authority_score", "PRIMARY_THRESHOLD", "RELIABILITY_BLOCK"):
            self.assertFalse(hasattr(E, name), name)

    def test_no_consumer_reads_a_score(self):
        for rel in ("scripts/tax_answer.py", "scripts/tax_server.py",
                    "tests/eval_answer.py", "frontend/index.html"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn('get("score"', text, rel)
            self.assertNotIn("PRIMARY_THRESHOLD", text, rel)

    def test_frontend_prints_the_reminder_not_a_downweight_badge(self):
        """界面上"⚪ 仅参考"那个角标换成了一句看得见的提醒。

        角标只给一个记号：读者既不知道疑在哪一处，也无从核对，只能整条忽略。
        现在卡面直接印提醒，句子取自条目自带的 `_reliability_note`。界面不再存
        第二份文案：同一档位在不同来源里原因不同，按档位配死一句就会有一句是假的
        （判据与定级层同一处，见 `tax_evidence._caveats`）。

        变异自检：把角标那段 `${item._reliability==='medium'?...仅参考...}` 抄回
        index.html，第一条断言报红；把 `rel-note` 那一行删掉，第二、三条报红；
        在界面里另存一份按档位配的固定文案，第四条报红。
        """
        self.assertNotIn("仅参考", HTML, "降权标签不能留在界面上")
        self.assertIn('class="rel-note"', HTML, "提醒要印在卡面上，不能只挂在 title 里")
        self.assertIn("item._reliability_note", HTML, "提醒要用来源自己那句")
        self.assertNotIn("RELIABILITY_NOTE", HTML, "界面不另存一份按档位配的文案")


class TestValidityCorroboratedByCitation(unittest.TestCase):
    """「财税文件」那一栏根本不录时效，援引证据补这一格。

    59 号、109 号是点名公告正文列明的制定依据，却是这道题的上位规则；
    因为法规库这两条的时效录入项为空（财税〔2003〕16 号那个位置上是
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
            g = E.grade(r, at=self.AT, topic="企业重组")
            self.assertEqual("effective", g["validity"])
            self.assertIn("制定依据判定在效", g["validity_note"])
            # 这一条的"现行有效"是佐证出来的，不是法规库录的：必须占一条提醒，
            # 免得读的人把它当成源里的时效字段
            self.assertIn("引用前按该文自身的时效复核", "；".join(g["caveats"]),
                          g["caveats"])

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

    NPC 那一轮静默失败时，90 分的现行有效法律不见了，顶上【主依据】的是
    70 分的《个体工商户建账管理暂行办法》，而 `rounds_done` 只写着 found=0、
    `errors` 是空的——读的人无法区分"库里没有"和"这一轮没取回来"。
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
        """立法过程实位取回的条目带 `legislative_process`，定级层据此不挑它当主依据。

        人大网站的草案与审议公告是"找到文本的线索"，本身不是已公布的规定。
        这一条拒绝的理由是"它不是已公布的条文"这个事实，不是降权：
        `_reliability: medium` 只负责附带一句提醒，
        真正把这一层挡在头条之外的是 `legislative_process` 这个标记。
        """
        page = {"results": [{"title": "法律草案审议 中国人大网",
                             "url": "http://www.npc.gov.cn/npc/c2/x.html"}], "total": 1}
        with mock.patch.object(ANS.S360, "so360_search", return_value=page) as hit:
            rows, err = ANS._legis_round("税收征收管理法 修订草案", 8)
        self.assertEqual("", err)
        self.assertEqual("npc.gov.cn", hit.call_args.kwargs["site"])
        self.assertEqual("medium", rows[0]["_reliability"])
        self.assertTrue(rows[0]["legislative_process"])
        graded = [E.grade(r, at="2026-09-29") for r in rows]
        best = E.pick_primary(graded)
        self.assertEqual("", best.get("title", ""), "立法过程线索不能被选成主依据")
        self.assertIn("立法过程线索", best["_why"])
        # 掺进一条正常依据时，主依据让位给那条，线索仍不能靠兜底上位
        mixed = graded + [E.grade({"title": "中华人民共和国税收征收管理法",
                                   "status": "全文有效"}, at="2026-09-29")]
        self.assertNotEqual("法律草案审议 中国人大网",
                            E.pick_primary(mixed)["title"])
        # 但它没有被删掉：落选清单里仍列着这一条
        self.assertIn("法律草案审议 中国人大网",
                      [r["title"] for r in best["_runners_up"]])

    def test_draft_round_also_searches_shui5_and_wechat(self):
        """草案那一轮必须连带搜税屋与公众号：草案解读文章只活在这两个源里。

        草案轮若只排 sources=["legis"]（人大网站内），税屋与公众号一次都不查，
        会把最有料的实务解读整层漏掉：search_shui5("税收征管法 修订草案") 首条
        即国务院法制办征求意见稿通知、含"修订重点逐条解读"多篇。
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
    （报错原文 `'str' object has no attribute 'get'`）。
    路由用例若把桩打在 `_search_web_broad` 上、桩形用二元组，就会与被测函数的真实
    返回形态不一致，这条断路在测试里一直是绿的。

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


class TestPracticeCitationCheck(unittest.TestCase):
    """实务材料的口径要追到一份现行有效的法定文件上，逐条核对。

    这一层替代的是"给税屋/公众号打 10 分、标成只能参考"：降权既不告诉读者哪条
    口径有文件托着，也不告诉读者哪条悬空。现在按文号回官方库查存在与时效。
    全部用例用打桩的库，不发真实检索。
    """

    @staticmethod
    def _文章(content="", source="税屋 (shui5.cn)"):
        row = {"title": "减征车辆购置税的执行口径", "source": source,
               "url": "https://www.shui5.cn/article/9/1.html"}
        if content:
            row["content"] = content
        return row

    def _核对(self, rows, 库里=None, 库报错="", 命中数=None, 响应=None, **kw):
        """跑一次核对，带回 (统计, 发出去查的检索词列表)。

        `库报错` 走 `_fetch_failed` 标记——真实接口零命中时也会写一句 `_error`
        当说明（`tax_fgk._scan_list`），本层认的是标记不是句子。要看接口原样
        回的东西就用 `响应`，它绕过上面两个参数。
        """
        calls = []

        def lookup(term):
            calls.append(term)
            if 响应 is not None:
                return dict(响应)
            if 库报错:
                return {"_error": 库报错, "_fetch_failed": True}
            return {"results": 库里 or [], "total_hits": 命中数}

        stats = ANS.check_practice_citations(rows, at="2026-09-29",
                                             lookup=lookup, **kw)
        return stats, calls

    有效件 = {"title": "财政部 税务总局关于减征车辆购置税的公告",
              "document_number": "财政部 税务总局公告2023年第19号",
              "status": "现行有效", "url": "http://x/19"}

    def test_citation_found_in_library_is_named_with_its_number(self):
        文 = self._文章("依据财政部 税务总局公告2023年第19号，新能源车免征购置税")
        stats, calls = self._核对([文], 库里=[dict(self.有效件)])
        # 发出去的是带机关的宽写法（实测裸短形在库里零命中），记下来的还是
        # 文章里那枚短形——两头各管一件事
        self.assertEqual(["财政部 税务总局公告2023年第19号"], calls)
        st = 文["official_status"]
        self.assertEqual("effective", st["outcome"], st)
        self.assertEqual("2023年第19号", st["doc_number"], "记的是文章援引的那个短形")
        self.assertEqual(self.有效件["title"], st["title"], "文件名要取库里的规范名")
        self.assertEqual("http://x/19", st["url"])
        self.assertEqual({"effective": 1}, stats["by_outcome"])
        self.assertEqual(1, stats["checked"])

    def test_reminder_lands_in_the_graded_caveats(self):
        """核对结果只写进字段不算交付；定级那一条必须把它说成人话。"""
        文 = self._文章("依据财政部 税务总局公告2023年第19号执行")
        self._核对([文], 库里=[dict(self.有效件)])
        g = E.grade(文, at="2026-09-29", topic="车辆购置税")
        self.assertTrue(any("2023年第19号" in c and "税务总局法规库" in c
                            for c in g["caveats"]), g["caveats"])

    def test_repealed_citation_says_reread_the_current_text(self):
        库 = dict(self.有效件, status="全文废止")
        文 = self._文章("按财政部 税务总局公告2023年第19号的规定免征")
        self._核对([文], 库里=[库])
        line = E.official_caveat(文["official_status"])
        self.assertEqual("repealed", 文["official_status"]["outcome"])
        self.assertIn("不再执行的规则", line)

    def test_library_miss_is_stated_as_a_miss_not_as_repealed(self):
        """库里没有这一份，与库里有但已废止，是两条完全不同的下一步动作。"""
        文 = self._文章("按国税发〔1999〕43号执行")
        stats, _ = self._核对([文], 库里=[])
        self.assertEqual("not_in_library", 文["official_status"]["outcome"], stats)
        self.assertIn("没有对上同一份文件", E.official_caveat(文["official_status"]))

    def test_zero_hit_with_an_explained_empty_is_a_miss_not_a_failure(self):
        """接口自己说"这次没命中"和"这轮没连上"，不能读成同一件事。

        真实 `search_fgk` 的形态（实测）：零命中时带一句 `_error` 但**不带**
        `_fetch_failed`；请求或解析失败才带 `_fetch_failed`。拿 `_error` 当故障，
        每一篇援引了生僻文号的文章都会被写成"连不上库、补一轮"，而补一百轮也
        还是零命中。反向弄错同样糟：真挂了报成"库里没有这一份"，读者会去怀疑
        自己抄错文号。所以两个方向都在这里钉住。
        """
        文 = self._文章("按财税〔1999〕43号执行")
        stats, _ = self._核对([文], 响应={"results": [], "total_hits": 0,
                                          "_error": "未检索到相关内容"})
        self.assertEqual("not_in_library", 文["official_status"]["outcome"], stats)
        self.assertIn("检索零命中", E.official_caveat(文["official_status"]))

        挂 = self._文章("按财税〔1999〕43号执行")
        self._核对([挂], 响应={"results": [], "_error": "连接超时",
                              "_fetch_failed": True})
        self.assertEqual("lookup_failed", 挂["official_status"]["outcome"])

    def test_retrieval_term_borrows_the_issuer_not_the_prose(self):
        """补前缀只能借原文里的机关名，借不到就用裸文号。

        喂进 `_citation_phrase` 的是标题+正文拼起来的一整段（实测形态），
        按字符宽度从右截会把"减征车辆购置税的执行口径 依据"一起当成检索词。
        这种词在库里一个也命中不了，于是"现行有效"被写成"库里没对上"——
        假结论还带一句看起来有据的说明。裸文号最坏是命中宽，不会指错文件。
        """
        self.assertEqual(
            "财政部 税务总局公告2023年第19号",
            ANS._citation_phrase("减征车辆购置税的执行口径 依据财政部 税务总局公告"
                                 "2023年第19号", "2023年第19号"))
        self.assertEqual(
            "国家税务总局公告2021年第5号",
            ANS._citation_phrase("国家税务总局公告2021年第5号", "2021年第5号"))
        # 本身就带机关名的写法原样返回；认不到机关名不猜前缀
        self.assertEqual("财税〔2016〕36号",
                         ANS._citation_phrase("依据财税〔2016〕36号附件",
                                              "财税〔2016〕36号"))
        self.assertEqual("2024年第1号",
                         ANS._citation_phrase("某单位关于优惠的公告2024年第1号",
                                              "2024年第1号"))
        # 原文里文号常被排版拆开（"2021 年第 5 号"），而 `tax_terms` 交回来的是
        # 去过空格的短形：按字面 find 会找不到，前缀也就补不上了
        self.assertEqual(
            "国家税务总局公告2021年第5号",
            ANS._citation_phrase("根据国家税务总局公告 2021 年第 5 号的规定",
                                 "2021年第5号"))

    def test_empty_library_document_number_does_not_count_as_a_match(self):
        """只认 want 是 got 的子串：库里那条没录文号时不能算命中，否则任何一篇
        正文里提到文号的文章都会匹配上这条空记录。"""
        文 = self._文章("按国税发〔1999〕43号执行")
        self._核对([文], 库里=[{"title": "某文件", "document_number": "",
                                "status": "现行有效"}])
        self.assertEqual("not_in_library", 文["official_status"]["outcome"])

    def test_lookup_failure_is_not_read_as_missing(self):
        文 = self._文章("按财税〔2016〕36号的规定")
        self._核对([文], 库报错="响应不是 JSON")
        st = 文["official_status"]
        self.assertEqual("lookup_failed", st["outcome"], st)
        line = E.official_caveat(st)
        self.assertIn("不能把「查不到」读成「库里没有」", line)
        self.assertNotIn("没有对上同一份文件", line)

    def test_missing_body_is_not_reported_as_missing_citation(self):
        """`--no-body` 那一趟只有标题：说"这篇没写文号"是假的，要说"没读正文"。"""
        无正文 = self._文章()
        有正文 = self._文章("这篇文章讲的是地方执行的口径细节，没引文号")
        stats, calls = self._核对([无正文, 有正文])
        self.assertEqual("no_body", 无正文["official_status"]["outcome"], stats)
        self.assertEqual("no_citation", 有正文["official_status"]["outcome"], stats)
        self.assertEqual([], calls)
        self.assertIn("没读正文", E.official_caveat(无正文["official_status"]))
        self.assertNotIn("没读正文", E.official_caveat(有正文["official_status"]))

    def test_limit_stops_further_requests_and_says_so(self):
        rows = [self._文章(f"依据国家税务总局公告202{i}年第5号执行") for i in range(1, 4)]
        stats, calls = self._核对(rows, 库里=[dict(self.有效件)], limit=2)
        self.assertEqual(2, len(calls), calls)
        self.assertEqual(1, stats["skipped"], stats)
        self.assertEqual("not_checked", rows[2]["official_status"]["outcome"])
        self.assertIn("上限", E.official_caveat(rows[2]["official_status"]))
        self.assertNotIn("没有对上同一份文件", E.official_caveat(rows[2]["official_status"]))

    def test_same_number_is_looked_up_once_for_all_articles(self):
        rows = [self._文章("按财政部 税务总局公告2023年第19号执行") for _ in range(3)]
        stats, calls = self._核对(rows, 库里=[dict(self.有效件)])
        self.assertEqual(1, len(calls), calls)
        self.assertEqual(1, stats["checked"], stats)
        self.assertEqual(3, stats["by_outcome"]["effective"], stats)
        self.assertEqual({r["official_status"]["title"] for r in rows},
                         {self.有效件["title"]})

    def test_statutory_rows_are_left_alone(self):
        """法定层自己有 status 字段可判时效，不该被这一层重复劳动一遍。"""
        法 = {"title": "中华人民共和国车辆购置税法", "category": "法律",
              "status": "现行有效", "url": "http://x/law"}
        stats, calls = self._核对([法, self._文章("按2023年第19号执行")],
                                  库里=[dict(self.有效件)])
        self.assertNotIn("official_status", 法)
        self.assertEqual(["2023年第19号"], calls)
        self.assertEqual({"effective": 1}, stats["by_outcome"], stats)

    def test_practice_recognition_shares_the_graders_vocabulary(self):
        """认源用 `tax_evidence.PRACTICE_SOURCES`：这里各写一套源名就会静默空转。

        实测过的三种真实形态都要认出来——税屋的标签、公众号的标签、360 回填的纯域名。
        """
        for src in ("税屋 (shui5.cn)", "微信公众号", "shui5.cn"):
            row = self._文章("按财税〔2016〕36号执行", source=src)
            self.assertTrue(ANS._is_practice_row(row), src)
        self.assertFalse(ANS._is_practice_row(
            {"title": "车辆购置税法", "source": "🏛️ 国家税务总局"}))

    def test_nine_outcomes_are_all_reachable_and_each_has_a_sentence(self):
        """九种结果逐一跑出来，一种都不许只在表里挂着。

        这是这套用例自己的覆盖率检查：`OFFICIAL_CAVEAT` 里多一条却没有场景能产出它，
        就说明本层的分支已经和文档对不上；少一条则这里直接红。
        """
        库 = {"results": [dict(self.有效件)]}
        seen = set()

        def 跑(rows, **kw):
            ANS.check_practice_citations(rows, at="2026-09-29", **kw)
            seen.update(r["official_status"]["outcome"] for r in rows
                        if r.get("official_status"))

        def 库内(status):
            return lambda t: {"results": [dict(self.有效件, status=status)]}

        # 库里查到了这一份的四种时效（结果名直接取 `judge_validity` 的取值）
        for status in ("现行有效", "全文废止", "尚未生效", ""):
            跑([self._文章("按财政部 税务总局公告2023年第19号执行")],
               lookup=库内(status))
        # 库里没有同一份 / 正文没写文号 / 没取正文 / 库没连上 / 轮次用满
        跑([self._文章("按财税〔1999〕43号执行")], lookup=lambda t: {"results": []})
        跑([self._文章("只讲口径，没引文号")], lookup=lambda t: 库)
        跑([self._文章()], lookup=lambda t: 库)
        跑([self._文章("按2023年第19号执行")],
           lookup=lambda t: {"_error": "超时", "_fetch_failed": True})
        跑([self._文章(f"依据国家税务总局公告202{i}年第5号执行") for i in range(1, 4)],
           lookup=lambda t: 库, limit=1)
        self.assertEqual(set(E.OFFICIAL_CAVEAT), seen,
                         f"用例产出的结果与提醒表不一致：{sorted(seen)}")
        self.assertEqual(set(E.OFFICIAL_CAVEAT), set(E.OFFICIAL_OUTCOME_LABEL),
                         "两张表必须同键，否则汇总句里会漏出英文键名")
        for key, tpl in E.OFFICIAL_CAVEAT.items():
            self.assertTrue(E.official_caveat({"outcome": key}),
                            f"{key} 的提醒句格式化后为空")

    def test_every_outcome_literal_in_the_producer_has_a_caveat(self):
        """新增核对结果却忘了配句子时，提醒会静默消失——这条拦字面量那一类漏配。

        不跑代码，只读源码：把 `check_practice_citations` 里所有写进字典值的
        小写英文字面量取出来（结果名都是这个形态），逐个要求在两张表里有条目。
        时效那四种结果名来自 `judge_validity` 的取值，不在这个函数里写字面量，
        由上面那条用例逐场景覆盖。
        """
        src = Path(ANS.__file__).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "check_practice_citations")
        emitted = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                for v in node.values:
                    for s in ([v.value] if isinstance(v, ast.Constant) else
                              ([v.body.value, v.orelse.value]
                               if isinstance(v, ast.IfExp)
                               and all(isinstance(x, ast.Constant)
                                       for x in (v.body, v.orelse)) else [])):
                        if isinstance(s, str) and re.fullmatch(r"[a-z][a-z_]{3,}", s):
                            emitted.add(s)
        self.assertTrue(emitted, "没从源码里取出任何结果字面量，用例失效了")
        self.assertEqual(set(), emitted - set(E.OFFICIAL_CAVEAT),
                         f"这些结果没有提醒句：{sorted(emitted - set(E.OFFICIAL_CAVEAT))}")

    def test_unknown_outcome_is_not_silently_dropped(self):
        """认不出的结果要报出来，不能和"没核对过"共用一个空串。"""
        line = E.official_caveat({"outcome": "someday", "doc_number": "2023年第19号"})
        self.assertIn("someday", line)
        self.assertEqual("", E.official_caveat({}))


class TestCitationCheckReachesTheAnswer(unittest.TestCase):
    """核对要一路走到答案输出，不能停在 gather 的字典里。"""

    # 判成 lookup 的问句只走 npc/fgk 两轮，永远碰不到税屋那一轮，这一层就没得核对；
    # 换成算税题，第 3 轮才是实务材料源。
    Q = "增值税小规模纳税人月销售额10万，应纳增值税多少"

    def _跑一趟(self, check_citations=True, with_practice=True):
        文 = {"title": "新能源车购置税免税口径", "source": "税屋 (shui5.cn)",
              "url": "https://www.shui5.cn/article/9/1.html",
              "content": "依据财政部 税务总局公告2023年第19号免征"}
        法 = {"title": "中华人民共和国车辆购置税法", "category": "法律",
              "status": "现行有效", "url": "http://x/law"}
        库 = {"results": [{"title": "财政部 税务总局关于减征车辆购置税的公告",
                          "document_number": "财政部 税务总局公告2023年第19号",
                          "status": "现行有效", "url": "http://x/19"}]}
        calls = []

        def fake_fgk(term, size=5, **rest):
            calls.append(term)
            return dict(库)

        def make(src):
            """按源发桩：返回 fetcher，取数契约是 (条目, 失败说明)。"""
            if src == "shui5":
                return lambda term, size: ([dict(文)] if with_practice else [], "")
            if src == "npc":
                return lambda term, size: ([dict(法)], "")
            return lambda term, size: ([], "")

        with mock.patch.dict(ANS._FETCHERS,
                             {s: make(s) for s in ("npc", "fgk", "shui5",
                                                   "wechat", "legis")}), \
             mock.patch.object(FGK, "search_fgk", fake_fgk):
            return ANS.gather(self.Q, at="2026-09-29",
                              check_citations=check_citations), calls

    def test_gather_records_progress_and_compose_says_it(self):
        plan, calls = self._跑一趟()
        self.assertEqual(["财政部 税务总局公告2023年第19号"], calls)
        self.assertEqual(1, plan["citation_check"]["checked"], plan["citation_check"])
        a = ANS.compose(plan)
        self.assertIn("税务总局法规库核对 1 个", a["citation_note"], a["citation_note"])
        self.assertIn("在库且现行有效 1 条", a["citation_note"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ANS._print_answer(a)
        self.assertIn("文号已回税务总局法规库核对", out.getvalue())

    def test_turning_the_check_off_leaves_a_visible_empty_not_a_fake_zero(self):
        plan, calls = self._跑一趟(check_citations=False)
        self.assertEqual([], calls)
        self.assertIsNone(plan["citation_check"])
        self.assertEqual("", ANS.compose(plan)["citation_note"])

    def test_no_practice_row_says_none_needed_instead_of_nothing_found(self):
        """一道纯法条题：这一栏要说"没有文号需要核对"，不是留空让人猜。"""
        plan, calls = self._跑一趟(with_practice=False)
        self.assertEqual([], calls)
        self.assertEqual({"checked": 0, "skipped": 0, "by_outcome": {}},
                         plan["citation_check"])
        note = ANS.compose(plan)["citation_note"]
        self.assertIn("没有取回税屋或公众号的文章", note)


# ── ②'' 税屋：正文收口、续页读到末页、站内检索这一路 ──────────────────────
# 下面三份 DOM 全部照 2026-10-18 实测的站点结构搭：
#   文章页  <div class="arcContent"> 之后紧跟 <div class="left2e">（点赞＋分享＋扫码块），
#           再往后才是 left2b 相关阅读、pagebreak 页码区
#   结果页  每行 <div class="xwt2">，标题在 xwt2_a>a 的 title 属性里（带 <font> 高亮），
#           摘要 xwt2_c>p.p1、日期 <p class="p3">、栏目 <p class="p4"><a>
#   提示页  文案两种：「没有搜索到相关的内容」＝这一轮没命中，
#           「系统限制的搜索时间间隔为 2 秒,请稍后再搜索」＝请求太密（两者只差 31 字节）
class _Resp:
    def __init__(self, html, url="", status=200):
        self.content = html.encode("utf-8")
        self.text = html
        self.url = url
        self.status_code = status


def _article_page(body, own_pages=(), other_pages=(), with_share=True):
    """搭一篇文章页。own_pages 是本篇 pagebreak 里列出的页码。"""
    links = " ".join(
        '<a href="https://www.shui5.cn/article/42/70138%s.html">%d</a>'
        % (("" if n == 1 else "$%d" % n), n) for n in own_pages)
    others = " ".join('<a href="https://www.shui5.cn/article/9/%d$2.html">x</a>' % i
                      for i in other_pages)
    share = ('<div class="left2e"><div class="diggnum">点赞（）</div>'
             '<div class="showshare">微信 微博</div>'
             '<div class="qrcode_wrap hidden">× 用微信扫码二维码 分享至好友和朋友圈</div></div>'
             if with_share else "")
    return ('<html><body><h1>测试文章</h1>'
            '<div class="articleResource">时间：2026-08-10</div>'
            '<div class="articleDes">这是摘要</div>'
            '<div class="arcContent"><p>%s</p></div>'
            '%s'
            '<div class="left2b">相关阅读 %s</div>'
            '<div class="pagebreak">本文章更多内容：%s</div>'
            '</body></html>' % (body, share, others, links))


class _ReadSession:
    """按 URL 发页面的假会话；记下请求顺序，代码要了没准备的地址就直接 KeyError。"""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []
        self.cookies = type("_C", (), {"set": lambda *a, **k: None})()

    def get(self, url, **kwargs):
        self.requested.append(url)
        return self.pages[url]


P1 = "https://www.shui5.cn/article/42/70138.html"


def _pp(n):
    return P1 if n == 1 else "https://www.shui5.cn/article/42/70138$%d.html" % n


class TestShui5BodyBoundary(unittest.TestCase):
    """P2：正文容器后面第一个兄弟容器是 left2e，旧边界没有它，抽样 12 篇尾部全混入。"""

    def test_share_block_is_cut_off(self):
        art = SH._parse_article(
            _article_page("正文第一段", own_pages=(1, 2)), P1)
        self.assertEqual("正文第一段", art["content"], repr(art["content"]))
        for noise in ("点赞", "分享至好友和朋友圈", "相关阅读", "本文章更多内容"):
            self.assertNotIn(noise, art["content"])

    def test_old_boundary_names_still_work_as_backups(self):
        """站点改版把 left2e 换掉时，left2b / blank20 还该收得住。"""
        page = ('<div class="arcContent"><p>正文</p></div>'
                '<div class="blank20"></div><div class="left2b">别的</div>')
        self.assertEqual("正文", SH._parse_article(page, P1)["content"])

    def test_meta_and_title_still_parsed_after_the_change(self):
        art = SH._parse_article(_article_page("正文", own_pages=(1,)), P1)
        self.assertEqual("测试文章", art["title"])
        self.assertEqual("2026-08-10", art["date"])
        self.assertEqual("这是摘要", art["summary"])


class TestShui5ContinuationPages(unittest.TestCase):
    """P1：长文按本篇 ID 枚举续页读到末页；越界页被站点钳回已有内容，不靠 404 试探。"""

    def _fetch(self, pages, **kw):
        sess = _ReadSession(pages)
        return SH.fetch_shui5(P1, session=sess, page_interval=0, **kw), sess

    def test_reads_every_page_listed_for_this_article(self):
        sess_pages = {
            _pp(1): _Resp(_article_page("第一段", own_pages=(1, 2, 3))),
            _pp(2): _Resp(_article_page("第二段", own_pages=(1, 2, 3))),
            _pp(3): _Resp(_article_page("第三段", own_pages=(1, 2, 3))),
        }
        art, sess = self._fetch(sess_pages)
        self.assertEqual(3, art["pages"])
        self.assertEqual("第一段\n第二段\n第三段", art["content"])
        self.assertEqual([_pp(1), _pp(2), _pp(3)], sess.requested,
                          "页码只到 3，不该再去要 $4")
        self.assertNotIn("_pages_error", art)

    def test_clamped_page_beyond_the_end_is_never_appended(self):
        """实测 $4、$5 回 200 与同一份正文：文本重复即到底，不拼第二份、不计页数。"""
        sess_pages = {
            _pp(1): _Resp(_article_page("第一段", own_pages=(1, 2, 3))),
            _pp(2): _Resp(_article_page("第二段", own_pages=(2, 3, 4))),
            _pp(3): _Resp(_article_page("第三段", own_pages=(3, 4))),
            _pp(4): _Resp(_article_page("第三段", own_pages=(4, 5))),  # 钳回第三段
        }
        art, sess = self._fetch(sess_pages)
        self.assertEqual(3, art["pages"], art)
        self.assertEqual(1, art["content"].count("第三段"), art["content"])
        self.assertEqual([_pp(1), _pp(2), _pp(3), _pp(4)], sess.requested)

    def test_other_articles_page_links_do_not_make_a_single_page_article_multi_page(self):
        """相关阅读里混着别人文章的 $2 链接：按链接形态数会把单页文章判成分页文章。"""
        art, sess = self._fetch({_pp(1): _Resp(
            _article_page("只有第一段", own_pages=(1,), other_pages=(111, 222)))})
        self.assertEqual(1, art["pages"])
        self.assertEqual([_pp(1)], sess.requested)
        self.assertEqual("只有第一段", art["content"])

    def test_window_style_page_list_expands_while_reading(self):
        """页码区只列相邻几页时，读一页就把新页码并进队列，不能停在第 2 页。"""
        sess_pages = {
            _pp(1): _Resp(_article_page("第一段", own_pages=(1, 2))),
            _pp(2): _Resp(_article_page("第二段", own_pages=(2, 3))),
            _pp(3): _Resp(_article_page("第三段", own_pages=(3,))),
        }
        art, _ = self._fetch(sess_pages)
        self.assertEqual(3, art["pages"], art)
        self.assertEqual("第一段\n第二段\n第三段", art["content"])

    def test_a_page_that_cannot_be_read_stops_and_says_where(self):
        """中间某一页取不到：正文停在已读部分并留原因，不能整篇当没有。"""
        sess_pages = {
            _pp(1): _Resp(_article_page("第一段", own_pages=(1, 2, 3))),
            _pp(2): _Resp("", status=404),
        }
        art, sess = self._fetch(sess_pages)
        self.assertEqual("第一段", art["content"])
        self.assertEqual(1, art["pages"])
        self.assertIn("第 2 页取不到", art["_pages_error"])
        self.assertIn("正文止于第 1 页", art["_pages_error"])
        self.assertNotIn(_pp(3), sess.requested)

    def test_page_cap_stops_the_loop(self):
        sess_pages = {_pp(n): _Resp(_article_page("第%d段" % n,
                                                  own_pages=tuple(range(1, 9))))
                      for n in range(1, 9)}
        art, sess = self._fetch(sess_pages, max_pages=3)
        self.assertEqual(3, art["pages"])
        self.assertEqual(3, len(sess.requested))


class TestShui5NativeSearch(unittest.TestCase):
    """P0：站内原生检索——行解析、总数、两份提示页、翻页去重。"""

    RESULT_URL = SH.SEARCH_RESULT + "?page=0&searchid=12345"

    def _row(self, n, url=None, column="税收问答"):
        return ('<div class="xwt2">'
                '<div class="xwt2_a"><a href="%s" title="留抵<font color=\'red\'>退税</font>口径%d"'
                '>\u53ef\u89c1\u6587\u672c%d</a></div>'
                '<div class="xwt2_c"><p class="p1">摘要%d<font color=\'red\'>词</font></p></div>'
                '<div class="xwt2_d"><p class="p1">来源</p></div>'
                '<p class="p3">2026-08-%02d</p>'
                '<p class="p4"><a href="/e/action/ListInfo/?classid=9">%s</a></p>'
                '</div>' % (url or "https://www.shui5.cn/article/42/%d.html" % (100 + n),
                            n, n, n, n + 1, column))

    def _result_page(self, rows, total=420):
        return ("<div>%d条关于 留抵退税 的搜索结果</div>%s" % (total, "".join(rows)))

    def test_rows_carry_date_column_and_clean_title(self):
        rows = SH._search_rows(self._result_page([self._row(1), self._row(2)]))
        self.assertEqual(2, len(rows))
        self.assertEqual("留抵退税口径1", rows[0]["title"], rows[0]["title"])
        self.assertEqual("2026-08-02", rows[0]["date"])
        self.assertEqual("税收问答", rows[0]["column"])
        self.assertEqual("摘要1词", rows[0]["snippet"])
        self.assertEqual("https://www.shui5.cn/article/42/101.html", rows[0]["url"])
        # 两条通道共用行形状：360 那一路给的键，站内这路也必须全有
        self.assertEqual({"title", "url", "date", "snippet", "source",
                          "source_label", "column"}, set(rows[0]))

    def test_total_count_sentence_is_read(self):
        t = SH._TOTAL_RE.search("420条关于 留抵退税 的搜索结果")
        self.assertEqual("420", t.group(1))

    def _search(self, pages, size=5, keyword="留抵退税"):
        """pages: 依次回给 post 的页面；get 按 URL 取。"""

        class S:
            def __init__(self, posts, gets, landing):
                self.posts, self.gets, self.landing = list(posts), gets, landing
                self.post_urls = []
                self.get_urls = []

            def post(self, url, **kwargs):
                self.post_urls.append(url)
                return _Resp(self.posts.pop(0), url=self.landing)

            def get(self, url, **kwargs):
                self.get_urls.append(url)
                return _Resp(self.gets[url])

        s = S(pages.get("posts", []), pages.get("gets", {}), self.RESULT_URL)
        return SH.search_native(keyword, size=size, session=s, gap=0), s

    def test_no_result_marker_means_zero_rows_not_a_failed_channel(self):
        r, _ = self._search({"posts": ["<div>%s</div>" % SH.NO_RESULT_MARK], "gets": {}})
        self.assertEqual([], r["results"])
        self.assertEqual(0, r["total"])
        self.assertTrue(r["_status"].startswith("0 条"), r["_status"])

    def test_zero_hit_status_hands_both_causes_to_the_caller(self):
        r, _ = self._search({"posts": ["<div>%s</div>" % SH.NO_RESULT_MARK], "gets": {}})
        self.assertIn("两种成因分不开", r["_status"],
                      "同一份页面既是真零命中也可能是参数被拒，归因要随读数交出去")
        self.assertIn("站内确实没有", r["_status"])
        self.assertIn("不接受", r["_status"])

    def test_rate_limit_marker_retries_once_and_can_still_come_back_empty(self):
        limit = "<div>%s,请稍后再搜索</div>" % SH.RATE_LIMIT_MARK
        r, s = self._search({"posts": [limit, "<div>%s</div>" % SH.NO_RESULT_MARK],
                             "gets": {}})
        self.assertEqual(2, len(s.post_urls), "限流只该重发一次")
        self.assertEqual(0, r["total"])
        self.assertTrue(r["_status"].startswith("0 条"), r["_status"])

    def test_still_limited_after_the_retry_is_reported_as_not_walked(self):
        limit = "<div>%s,请稍后再搜索</div>" % SH.RATE_LIMIT_MARK
        r, s = self._search({"posts": [limit, limit], "gets": {}})
        self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
        self.assertIsNone(r["total"], "限流轮不能报成 0 条")

    def test_waf_challenge_is_reported_as_not_walked(self):
        page = ('<textarea id="renderData">{"arg1":"%s"}</textarea>' % ("A" * 40))
        r, _ = self._search({"posts": [page], "gets": {}})
        self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
        self.assertIn("WAF", r["_status"])

    def test_pages_are_walked_and_duplicates_dropped(self):
        posts = [self._result_page([self._row(1)], total=3)]
        gets = {SH.SEARCH_RESULT + "?page=1&searchid=12345":
                self._result_page([self._row(2), self._row(1)]),   # 第 1 条重复
                SH.SEARCH_RESULT + "?page=2&searchid=12345":
                self._result_page([self._row(3)])}
        r, s = self._search({"posts": posts, "gets": gets}, size=3)
        self.assertEqual(["留抵退税口径1", "留抵退税口径2", "留抵退税口径3"],
                         [x["title"] for x in r["results"]],
                         "翻页要接着落点页往后取，重复的那条不能算新结果")
        # 落点页本身就是 page=0，往后从 1 起翻——再请求一次 0 会把首屏那行当新结果重复收
        self.assertEqual([SH.SEARCH_RESULT + "?page=1&searchid=12345",
                          SH.SEARCH_RESULT + "?page=2&searchid=12345"], s.get_urls)
        self.assertEqual(3, r["total"], "站点报的总条数原样带出")
        self.assertIn("这一词站内共 3 条", r["_status"])


class TestShui5ChannelRouting(unittest.TestCase):
    """auto 先走 360、它没取到才启浏览器；两条通道各 0 条是"没有"，不是"没取到"。"""

    def _run(self, so360, native, channel="auto"):
        calls = []

        def fake_so360(keyword, site="", size=5):
            calls.append("360")
            return so360

        def fake_native(keyword, size=5, **kw):
            calls.append("站内")
            return native

        with mock.patch.object(SH, "so360_search", fake_so360), \
             mock.patch.object(SH, "search_native", fake_native):
            return SH.search_shui5("留抵退税", size=5, channel=channel), calls

    def test_hit_from_360_does_not_start_a_browser(self):
        r, calls = self._run({"total": 1,
                              "results": [{"title": "留抵退税实务税 屋——第一时间传递财税政策法规",
                                           "url": P1, "snippet": "摘要"}]},
                             {"results": [], "total": None, "_status": "没走成：无浏览器"})
        self.assertEqual(["360"], calls)
        self.assertEqual("360", r["_channel"])
        self.assertEqual("未走（指定通道）", r["_channels"]["站内"])
        self.assertEqual("留抵退税实务", r["results"][0]["title"], "标语后缀要剥掉")

    def test_blocked_360_falls_through_to_native(self):
        r, calls = self._run({"_error": "360 被拦截（访问异常出错）", "results": []},
                             {"results": [SH._row_dict("站内标题", P1, "2026-08-10", "摘要")],
                              "total": 1, "_status": "命中 1 条"})
        self.assertEqual(["360", "站内"], calls)
        self.assertEqual("站内", r["_channel"])
        self.assertEqual(1, r["total"])
        self.assertNotIn("_error", r, "换通道换到了就不该报整体失败")

    def test_forced_channels_each_touch_only_their_own_source(self):
        _, calls = self._run({"total": 0, "results": []},
                             {"results": [], "total": None, "_status": "没走成"},
                             channel="so360")
        self.assertEqual(["360"], calls)
        _, calls = self._run({"total": 1, "results": [{"title": "t", "url": P1}]},
                             {"results": [], "total": None, "_status": "没走成"},
                             channel="native")
        self.assertEqual(["站内"], calls)

    def test_both_channels_genuinely_empty_is_not_an_error(self):
        r, _ = self._run({"total": 0, "results": []},
                         {"results": [], "total": 0, "_status": "0 条：站点回「没有搜索到相关的内容」"})
        self.assertEqual(0, r["total"])
        self.assertNotIn("_error", r, "两条都跑通、各 0 条＝这一轮没命中，不是取数失败")
        self.assertEqual("", r["_channel"])

    def test_both_channels_failing_says_which_and_why(self):
        r, _ = self._run({"_error": "360 被拦截", "results": []},
                         {"results": [], "total": None, "_status": "没走成：无浏览器，改纯 HTTP 直连"})
        self.assertIn("两条检索通道都没取到", r["_error"])
        self.assertIn("360 被拦", r["_error"])
        self.assertIn("站内 没走成", r["_error"])


def _xwt2_row(n, column="财税实务"):
    """结果页/栏目页/标签页共用的那一行 HTML，编号进 URL 保证每行地址唯一。"""
    return ('<div class="xwt2"><div class="xwt2_a">'
            '<a href="https://www.shui5.cn/article/42/%d.html" title="标题%d">可见%d</a></div>'
            '<div class="xwt2_c"><p class="p1">摘要%d</p></div>'
            '<p class="p3">2026-08-%02d</p>'
            '<p class="p4"><a href="/e/action/ListInfo/?classid=9">%s</a></p></div>'
            % (n, n, n, n, n % 28 + 1, column))


def _list_page(start, count, total):
    return ("<div>%d条关于 增值税 的搜索结果</div>%s"
            % (total, "".join(_xwt2_row(i) for i in range(start, start + count))))


class TestShui5ScopedSearch(unittest.TestCase):
    """限定参数那一组（栏目 / 区间 / 匹配域 / 取全）的换算、下推与归因。

    用例只钉关系，不钉站点某天报的总数：逗号两栏 == 两单栏之和、只给起始 == 没限定、
    取全翻到总数那一页为止——换词换天都成立，具体读数会过期。
    """

    TABLE = [
        {"dir": "BeiJingShiCaiShuiFaGui", "nav": "", "label": "北京", "classid": 152,
         "columns": ["北京市法规"], "pages": 178, "records": 6198},
        {"dir": "ShenZhenShiCaiShuiFaGui", "nav": "", "label": "深圳", "classid": 12673,
         "columns": ["深圳市法规"], "pages": 3, "records": 75},
        {"dir": "category_181", "nav": "", "label": "山西", "classid": 181,
         "columns": ["山西省法规"], "pages": 9, "records": 300},
        {"dir": "ShanXiShengCaiShuiFaGui", "nav": "", "label": "陕西", "classid": 175,
         "columns": ["陕西省法规"], "pages": 11, "records": 400},
    ]
    LANDING = SH.SEARCH_RESULT + "?page=0&searchid=999"

    def setUp(self):
        p = mock.patch.object(SH, "load_columns", return_value=self.TABLE)
        p.start()
        self.addCleanup(p.stop)

    class _Form:
        """把发出去的表单字段留下，页面按调用方给的清单依次回。"""

        def __init__(self, first_page, landing, gets=None):
            self.sent = []
            self.first_page = first_page
            self.landing = landing
            self.gets = gets or {}
            self.get_urls = []

        def post(self, url, data=None, **kw):
            self.sent.append(dict(data or {}))
            return _Resp(self.first_page, url=self.landing)

        def get(self, url, **kw):
            self.get_urls.append(url)
            return _Resp(self.gets[url])

    def _native(self, total=61, **kw):
        s = self._Form(_list_page(1, 30, total), self.LANDING)
        return SH.search_native("增值税", size=kw.pop("size", 5), session=s, gap=0, **kw), s

    def test_comma_spec_resolves_each_part_however_it_is_written(self):
        self.assertEqual("152,12673", SH.classid_spec("北京,深圳"))
        self.assertEqual("152,12673", SH.classid_spec("152,深圳市法规"))
        self.assertEqual("152,12673", SH.classid_spec(
            "BeiJingShiCaiShuiFaGui,ShenZhenShiCaiShuiFaGui"))

    def test_pipe_and_bracket_forms_never_reach_the_site(self):
        """站点只认逗号：竖线那一路实测只取第一段，classid[] 回空页，所以换算必须拒绝。"""
        self.assertEqual("", SH.classid_spec("152|12673"))
        self.assertEqual("", SH.classid_spec("北京,不存在的栏"))

    def test_unresolvable_part_voids_the_whole_spec_and_nothing_is_posted(self):
        s = self._Form(_list_page(1, 30, 61), self.LANDING)
        r = SH.search_native("增值税", size=5, session=s, gap=0, classid="北京,不存在的栏")
        self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
        self.assertIn("栏目表", r["_status"])
        self.assertEqual([], s.sent, "半截限定不该发出去——那看着像收窄了其实没有")

    def test_resolved_classid_and_the_other_three_dimensions_are_posted(self):
        _, s = self._native(classid="北京,深圳", match="title",
                            date_from="2025-01-01", date_to="2025-12-31")
        sent = s.sent[0]
        self.assertEqual("152,12673", sent["classid"])
        self.assertEqual("title", sent["show"], "show 是匹配域，不是排版参数")
        self.assertEqual("2025-01-01", sent["starttime"])
        self.assertEqual("2025-12-31", sent["endtime"])

    def test_date_from_alone_is_flagged_instead_of_read_as_a_narrowing(self):
        r, s = self._native(date_from="2025-01-01")
        self.assertEqual("2025-01-01", s.sent[0]["starttime"])
        self.assertNotIn("endtime", s.sent[0])
        self.assertIn("不过滤", r["_filter_note"], "站点单给 starttime 不收窄，这个缺口要写出来")

    def test_unscoped_call_carries_no_filter_note(self):
        r, _ = self._native()
        self.assertNotIn("_filter_note", r)

    def test_fetch_all_walks_to_the_total_the_page_reports(self):
        total = 61
        gets = {SH.SEARCH_RESULT + "?page=1&searchid=999": _list_page(31, 30, total),
                SH.SEARCH_RESULT + "?page=2&searchid=999": _list_page(61, 1, total)}
        s = self._Form(_list_page(1, 30, total), self.LANDING, gets)
        r = SH.search_native("增值税", size=5, session=s, gap=0, fetch_all=True)
        self.assertEqual(61, len(r["results"]), "取全按页面总数走，不受 size 约束")
        self.assertEqual([SH.SEARCH_RESULT + "?page=1&searchid=999",
                          SH.SEARCH_RESULT + "?page=2&searchid=999"], s.get_urls)

    def test_fetch_all_names_the_cap_when_it_could_not_reach_the_total(self):
        total = 61
        gets = {SH.SEARCH_RESULT + "?page=1&searchid=999": _list_page(31, 30, total)}
        s = self._Form(_list_page(1, 30, total), self.LANDING, gets)
        with mock.patch.object(SH, "SEARCH_ALL_MAX_PAGES", 1):
            r = SH.search_native("增值税", size=5, session=s, gap=0, fetch_all=True)
        self.assertEqual(30, len(r["results"]))
        self.assertIn("翻到上限 1 页只取了 30 条", r["_status"],
                      "撞到上限时必须写明只取了多少，不能报成取全了")

    def _route(self, so360, native, **kw):
        calls = []

        def fake_so360(keyword, site="", size=5):
            calls.append("360")
            return so360

        def fake_native(keyword, size=5, **inner):
            calls.append("站内")
            self.inner = inner
            return native

        with mock.patch.object(SH, "so360_search", fake_so360), \
             mock.patch.object(SH, "search_native", fake_native):
            return SH.search_shui5("增值税", size=5, **kw), calls

    def test_scoped_params_move_auto_off_360_and_say_why(self):
        hit = {"results": [SH._row_dict("站内标题", P1, "2026-08-10", "摘要")],
               "total": 1, "_status": "命中 1 条"}
        r, calls = self._route({"total": 9, "results": [{"title": "t", "url": P1}]},
                               hit, column="北京")
        self.assertEqual(["站内"], calls, "360 那一路没有栏目维度，这一轮压根不该问它")
        self.assertTrue(r["_channels"]["360"].startswith("没问"), r["_channels"]["360"])
        self.assertNotEqual("未走（指定通道）", r["_channels"]["360"],
                            "换路是限定参数逼的，不是调用方指定的")

    def test_forced_so360_with_scoped_params_reports_that_they_did_not_apply(self):
        r, calls = self._route({"total": 9,
                                "results": [{"title": "t", "url": P1}]},
                               {"results": [], "total": None, "_status": "没走成"},
                               channel="so360", date_from="2025-01-01", date_to="2025-12-31")
        self.assertEqual(["360"], calls)
        self.assertIn("限定没加上", r["_filter_note"],
                      "指定了 360 又给限定：结果看着像收窄了，其实一条都没下推")

    def test_unscoped_auto_still_asks_360_first(self):
        r, calls = self._route({"total": 1, "results": [{"title": "t", "url": P1}]},
                               {"results": [], "total": None, "_status": "没走成"})
        self.assertEqual(["360"], calls)
        self.assertEqual("命中 1 条", r["_channels"]["360"])
        self.assertNotIn("_filter_note", r)


class TestShui5ColumnTable(unittest.TestCase):
    """随仓库发货的栏目表：归属只认行里的 column 字段，表里不能有猜出来的值。"""

    def test_every_shipped_row_is_resolvable_by_its_own_dir_and_classid(self):
        rows = SH.load_columns()      # 读的是随仓库发货的 data/shui5_columns.json
        self.assertTrue(len(rows) >= 40, "栏目表整表丢失时这一条要报红")
        for r in rows:
            self.assertIsInstance(r["classid"], int, r)
            for key in ("dir", "classid"):
                got = SH.resolve_column(r[key])
                self.assertIsNotNone(got, f"{key}={r[key]} 查不到")
                self.assertEqual(r["classid"], got["classid"], f"{key}={r[key]}")
        self.assertEqual(len(rows), len({r["dir"] for r in rows}), "目录名不该重复")

    def test_pinyin_dir_that_looks_like_shanxi_is_actually_shaanxi(self):
        """目录名的拼音串与行内栏目名不同源：猜目录名会把陕西的栏当成山西的栏。"""
        p = mock.patch.object(SH, "load_columns", return_value=TestShui5ScopedSearch.TABLE)
        p.start()
        self.addCleanup(p.stop)
        self.assertEqual("陕西省法规", SH.resolve_column("ShanXiShengCaiShuiFaGui")["columns"][0])
        self.assertEqual("山西省法规", SH.resolve_column("category_181")["columns"][0])

    def test_shipped_table_keeps_those_two_rows_apart(self):
        """同一条判据也要钉住随仓库发货的那份表，不然表被改错时上一条约查不出。"""
        by_dir = {r["dir"]: r for r in SH.load_columns()}
        self.assertEqual(["陕西省法规"], by_dir["ShanXiShengCaiShuiFaGui"]["columns"])
        self.assertEqual(["山西省法规"], by_dir["category_181"]["columns"])

    def test_unknown_column_is_refused_before_any_request(self):
        r = SH.list_column("这个栏目名站点没有")
        self.assertEqual([], r["results"])
        self.assertIn("栏目表里没有", r["_status"])


class TestAggregatorShui5Attribution(unittest.TestCase):
    """360 被拦时税屋的空算不算被连带，由这一轮站内那一路的回报决定，不再靠推断。"""

    def _agg(self, channels):
        ok = lambda n: {"total": n, "results": [{"title": "财税〔2025〕1号",
                                                "url": "https://x.test/a"}] if n else []}
        shui5 = {"total": 0, "results": [], "_channels": channels}
        with mock.patch.object(AGG, "search_tax", return_value=ok(1)), \
             mock.patch.object(AGG, "search_chinatax", return_value=ok(1)), \
             mock.patch.object(AGG, "so360_search",
                               return_value={"_error": "360 被拦截", "results": []}), \
             mock.patch.object(AGG, "search_shui5", return_value=shui5), \
             mock.patch.object(AGG, "search_wechat", return_value=ok(0)), \
             mock.patch.object(AGG, "search_ctax", return_value=ok(1)):
            return AGG.aggregate_search("测试词", size=5)

    def _shui5_gap(self, r):
        g = [x for x in r["gaps"] if x["source"] == "shui5"]
        self.assertEqual(1, len(g), r["gaps"])
        return g[0]

    def test_native_ran_but_empty_is_own_not_inherited(self):
        g = self._shui5_gap(self._agg({"360": "被拦：360 被拦截",
                                       "站内": "0 条：站点回「没有搜索到相关的内容」"}))
        self.assertNotIn("blocked_by", g, "站内那一路跑通了，就不能再甩给 360")
        self.assertIn("站内原生检索这一轮也没命中", g["reason"])

    def test_native_not_walked_keeps_the_inheritance(self):
        g = self._shui5_gap(self._agg({"360": "被拦：360 被拦截",
                                       "站内": "没走成：无浏览器，改纯 HTTP 直连"}))
        self.assertEqual("so360", g["blocked_by"])
        self.assertIn("不代表税屋没有内容", g["impact"])
        self.assertIn("只走了 360 的 site:shui5.cn 那一路", g["reason"])

    def test_channel_report_absent_falls_back_to_inheritance(self):
        """没带 _channels 的旧结果（打桩、缓存）仍按老判据走，不因为新字段缺失就改口。"""
        ok = lambda n: {"total": n, "results": [{"title": "t", "url": "https://x.test/a"}]
                        if n else []}
        with mock.patch.object(AGG, "search_tax", return_value=ok(1)), \
             mock.patch.object(AGG, "search_chinatax", return_value=ok(1)), \
             mock.patch.object(AGG, "so360_search",
                               return_value={"_error": "360 被拦截", "results": []}), \
             mock.patch.object(AGG, "search_shui5", return_value=ok(0)), \
             mock.patch.object(AGG, "search_wechat", return_value=ok(0)), \
             mock.patch.object(AGG, "search_ctax", return_value=ok(1)):
            g = self._shui5_gap(AGG.aggregate_search("测试词", size=5))
        self.assertEqual("so360", g["blocked_by"])


class TestShui5DocNumberLookup(unittest.TestCase):
    """--find-doc 的查询阶梯。税屋标题里的文号一律写半角方括号，
    官方那种六角括号要换过去再查，否则站内 0 条会被读成"税屋没有这份文件"。
    阶梯各档的实测读数（2026-10-18，站内标题匹配）：`财税[2019]13号` 7 条、
    `财税〔2019〕13号` 0 条、`财税[2016]36号` 20 条、`财税〔2016〕36号` 4 条。
    """

    def setUp(self):
        self.calls = []
        # 阶梯档与档之间按 SEARCH_MIN_GAP 隔开发，用例里不等
        p = mock.patch.object(SH.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def _run(self, doc, hits=None, session=object()):
        """hits: 第几档命中（None 表示逐档走完都空）。session 传 None 走自己过 WAF 那一路。"""
        def fake(q, size=5, session=None, match="title", **kw):
            self.calls.append(q)
            hit = (hits == len(self.calls))
            return {"results": [SH._row_dict("命中：" + q, P1, "2019-01-17", "摘要")]
                    if hit else [],
                    "total": 1 if hit else 0,
                    "_status": "命中 1 条" if hit else "0 条：站点回「没有搜索到相关的内容」"}

        with mock.patch.object(SH, "search_native", fake):
            return SH.find_by_doc_number(doc, session=session)

    def test_full_width_is_asked_in_half_width_first(self):
        r = self._run("财税〔2019〕13号", hits=1)
        self.assertEqual(["财税[2019]13号"], self.calls, "第一档就该是半角形态")
        self.assertEqual("财税[2019]13号", r["_query"])
        self.assertEqual(1, len(r["_queries_tried"]), "命中了不再往后试")

    def test_spaces_in_the_number_go_but_agency_spaces_stay(self):
        """站点按整串匹配，编号里多一个空格就是另一条查询；机关名之间那个空格是文号的一部分。"""
        self._run("财税〔 2019 〕 13 号", hits=1)
        self.assertEqual(["财税[2019]13号"], self.calls)
        self.calls.clear()
        self._run("财政部 税务总局公告2019年第13号", hits=1)
        self.assertEqual(["财政部 税务总局公告2019年第13号"], self.calls)
        self.calls.clear()
        self._run("财税 〔2019〕13号", hits=1)
        self.assertEqual(["财税[2019]13号"], self.calls,
                         "括号前面带空格也要整形——靠的就是字符类里那一枚开括号，"
                         "去掉它这一串只能整串提交")

    def test_the_ladder_ends_with_the_number_without_the_issuer(self):
        r = self._run("税总发[1999]999号")
        self.assertEqual(["税总发[1999]999号", "税总发〔1999〕999号", "[1999]999号"],
                         self.calls)
        self.assertTrue(r["_status"].startswith("0 条"),
                        "跑通的阶梯不能报成没走成——聚合侧靠这一句分辨税屋空还是被连带")
        for q in self.calls:
            self.assertIn(q, r["_caveat"], "0 条要把试过的写法都交出去")
        self.assertIn("不代表税屋没有这份文件", r["_caveat"])

    def test_announcement_form_skips_the_full_width_stage(self):
        """没有方括号就没有全角形态可试，第二档直接去掉机关名只留「2019年第13号」。"""
        r = self._run("财政部 税务总局公告2019年第13号", hits=2)
        self.assertEqual(["财政部 税务总局公告2019年第13号", "2019年第13号"], self.calls)
        self.assertEqual(2, len(r["_queries_tried"]))
        self.assertIn("与给进来的写法不同", r["_caveat"], "改过查询串要说明")

    def test_a_half_width_hit_is_not_reported_as_rewritten(self):
        r = self._run("财税[2016]36号", hits=1)
        self.assertNotIn("写法不同", r["_caveat"])
        self.assertIn("子串匹配", r["_caveat"], "候选里混着地方同号文件与解读文章，这条提醒常驻")

    def test_garbage_input_is_asked_once_as_given(self):
        """落不进「编号」那一段的串（如整句正文）按原样提交一次，不编出写法来。"""
        r = self._run("执行至2024年4月15日，令第108号")
        self.assertEqual(["执行至2024年4月15日，令第108号"], r["_queries_tried"])
        self.assertNotIn("〔", r["_caveat"], "换不出全角形态，就不能在提醒里报出这一档")

    def test_empty_input_asks_the_site_nothing(self):
        r = self._run("   ")
        self.assertEqual([], self.calls)
        self.assertIn("没给文号", r["_status"])

    def test_no_browser_sends_no_search(self):
        with mock.patch.object(SH, "_pass_waf",
                               return_value=(None, "无浏览器，改纯 HTTP 直连")):
            r = self._run("财税[2016]36号", session=None)
        self.assertEqual([], self.calls)
        self.assertTrue(r["_status"].startswith("没走成"), r["_status"])

    def test_size_defaults_to_twenty_candidates(self):
        seen = []

        def fake(q, size=5, session=None, match="title", **kw):
            seen.append((q, size, match))
            return {"results": [SH._row_dict("t", P1, "", "摘要")], "total": 1,
                    "_status": "命中 1 条"}

        with mock.patch.object(SH, "search_native", fake):
            SH.find_by_doc_number("财税[2019]13号", session=object())
        self.assertEqual([("财税[2019]13号", 20, "title")], seen,
                         "文号定位要交候选，只给 5 条会把中央那份挤掉")


class TestShui5StatusMark(unittest.TestCase):
    """行标题尾部的站点时效标记，取值 2026-10-18 检索标记词的标题读数定为五种。"""

    def test_mark_splits_out_and_title_stays_as_the_page(self):
        row = SH._row_dict("财税[2019]13号 关于实施小微企业普惠性税收减免政策的通知[全文失效]",
                           P1, "2019-01-17", "摘要")
        self.assertEqual("全文失效", row["status_mark"])
        self.assertIn("[全文失效]", row["title"], "剥掉标记会让标题与页面不一致")

    def test_unmarked_row_does_not_carry_the_key(self):
        row = SH._row_dict("留抵退税口径", P1, "2026-08-10", "摘要")
        self.assertNotIn("status_mark", row)

    def test_all_five_shipped_values_are_read(self):
        for v in ("条款失效", "全文失效", "条款废止", "全文废止", "条款修订"):
            self.assertEqual(v, SH._row_dict("某办法[%s]" % v, P1, "", "")["status_mark"])

    def test_full_width_brackets_would_still_be_read(self):
        """取样到的带标记标题一律半角；站点改用全角时不至于静默漏读。"""
        self.assertEqual("条款修订",
                         SH._row_dict("某办法〔条款修订〕", P1, "", "")["status_mark"])

    def test_the_word_alone_without_brackets_is_not_a_mark(self):
        """「全文失效」出现在标题正文里（如目录类文章的名目）不算站点标记。"""
        self.assertNotIn("status_mark",
                         SH._row_dict("全文失效和废止部分文件的通知", P1, "", ""))

    def test_result_page_rows_carry_the_mark(self):
        page = ('<div class="xwt2"><div class="xwt2_a">'
                '<a href="https://www.shui5.cn/article/42/101.html" title='
                '"湖南省水资源费改税试点实施办法[条款废止]">可见</a></div>'
                '<div class="xwt2_c"><p class="p1">摘要</p></div>'
                '<div class="xwt2_d"><p class="p1">来源</p></div>'
                '<p class="p3">2019-11-30</p>'
                '<p class="p4"><a href="/e/action/ListInfo/?classid=9">税收法规</a></p></div>')
        rows = SH._search_rows(page)
        self.assertEqual("条款废止", rows[0]["status_mark"])


class TestShui5RepealNote(unittest.TestCase):
    """正文开头的「税屋提示——」批注。批注后面紧跟的就是文件正文，正文里也有文号，
    按整段读会把正文文号混进依据——按「。」切句、只留与时效有关的那几句。
    这里的分句都取 2026-10-18 实测过的真实批注。
    """

    GANSU = ("税屋提示——依据甘人社通[2026]86号 甘肃省人力资源和社会保障厅关于宣布"
             "失效和废止部分文件的通知，自2026年3月5日起本法规全文失效。 "
             "各市州人力资源和社会保障局、财政局： 为深入贯彻党中央、国务院和省委、"
             "省政府稳就业决策部署，根据《关于继续实施失业保险促进就业政策的通知》"
             "（人社部发〔2024〕45号）")
    CAIJIN = ("税屋提示——依据财金[2026]1号 财政部中国人民银行 金融监管总局关于优化实施"
              "个人消费贷款财政贴息政策有关事项的通知，本通知部分条款修订。 "
              "各省、自治区、直辖市、计划单列市财政厅(局)： 为贯彻落实党中央、国务院"
              "关于大力提振消费的决策部署，现印发。")
    ORDER108 = ("税屋提示——依据国家市场监督管理总局令第108号 国家市场监督管理总局关于"
                "修改《个体工商户年度报告办法》等四部规章的决定，自2025年12月25日起，"
                "本法规将第十一条、第十二条、第十三条、第十四条合并，作为第十一条，"
                "修改为：“被列入经营异常名录的企业，符合下列条件之一的，可以依法向其"
                "登记地市场监督管理部门申请移出经营异常名录： “(一)因未按照规定报送"
                "并公示年度报告被列入经营异常名录，已经补报未报年份的年度报告并公示； "
                "“(二)因未按")
    TAIZHOU = ("税屋提示——依据国家税务总局台州市税务局公告2025年第1号 关于公布全文有效、"
               "全文失效废止、部分条款失效废止和部分条款修改的税务规范性文件目录的公告 ，"
               "自2025年8月15日本法规第五条已修订。 为进一步规范全市存量房交易纳税申报行为，"
               "现予以发布，自2025年9月1日起施行。 闽商务规〔2024〕1104号")
    TWO_ITEMS = ("税屋提示—— 1.依据闽商务规[2024]10号 福建省商务厅 福建省发展和改革委员会 "
                 "福建省财政厅关于调整优化消费品以旧换新政策的公告，本法规部分条款修订优化，"
                 "优化详情可查看闽商务规[2024]10号。 2.依据福建省商务厅 福建省发展和改革委员会 "
                 "福建省财政厅关于调整消费品以旧换新部分政策的公告，本法规部分条款修订。 "
                 "各市、县(区)人民政府： 《福建省加力支持消费品以旧换新实施方案》"
                 "（发改环资〔2024〕1104号）")

    def test_single_sentence_with_end_date(self):
        self.assertEqual(
            "依据甘人社通[2026]86号 甘肃省人力资源和社会保障厅关于宣布失效和废止部分文件的"
            "通知，自2026年3月5日起本法规全文失效",
            SH._repeal_note(self.GANSU))

    def test_note_without_an_end_date_is_still_kept(self):
        """不带止期那种（本通知部分条款修订）也要收下，不能因为没有日期就丢。"""
        self.assertEqual(
            "依据财金[2026]1号 财政部中国人民银行 金融监管总局关于优化实施个人消费贷款"
            "财政贴息政策有关事项的通知，本通知部分条款修订",
            SH._repeal_note(self.CAIJIN))

    def test_numbered_items_are_both_kept(self):
        note = SH._repeal_note(self.TWO_ITEMS)
        self.assertTrue(note.startswith("1.依据闽商务规[2024]10号"), note[:40])
        self.assertIn("2.依据福建省商务厅", note)
        self.assertNotIn("各市、县", note)
        self.assertNotIn("实施方案", note.split("2.依据")[1])

    def test_numbered_date_clause_needs_the_head_pattern(self):
        """取样那 11 篇的分句都带「依据」或「本法规/本通知」，这一形不是量出来的，
        钉的是判据本身：只讲「自YYYY年起」又带序号的那一句，靠 `_TIP_KEEP_RE`
        的「序号 + 自YYYY年」那一路留下；后面 `or "本法规" in seg` 那一路顶不上。
        """
        note = SH._repeal_note(
            "税屋提示—— 1.自2026年1月1日起《××办法》第三条废止。 "
            "2.本《办法》附件二同时作废。 各市财政局： 现印发给你们。")
        self.assertEqual("1.自2026年1月1日起《××办法》第三条废止", note)

    def test_body_doc_number_does_not_leak_into_the_basis(self):
        """台州那篇的正文里另有闽商务规〔2024〕1104号，不能算进这一条的依据。"""
        note = SH._repeal_note(self.TAIZHOU)
        self.assertEqual(["2025年第1号"], SH.doc_numbers(SH._to_full_brackets(note)))
        self.assertNotIn("1104", note)

    def test_basis_reading_after_bracket_swap(self):
        """税屋写成半角，tax_terms 认的是六角；不换括号一枚也抽不到。"""
        note = SH._repeal_note(self.GANSU)
        self.assertEqual([], SH.doc_numbers(note), "按原文（半角）抽不到")
        self.assertEqual(["〔2026〕86号"], SH.doc_numbers(SH._to_full_brackets(note)))

    def test_regulation_order_number_yields_no_basis(self):
        """批注引用的是规章令号（令第108号），不在文号形态里：这一段只能交出提醒文字。"""
        self.assertTrue(SH._repeal_note(self.ORDER108))
        self.assertEqual([], SH.doc_numbers(
            SH._to_full_brackets(SH._repeal_note(self.ORDER108))))

    def test_quoted_clause_is_capped_by_the_window(self):
        self.assertLessEqual(len(SH._repeal_note(self.ORDER108)), SH._TIP_MAX)

    def test_no_anchor_and_non_validity_tip_both_return_empty(self):
        self.assertEqual("", SH._repeal_note(
            "各市州人力资源和社会保障局： 根据财税〔2019〕13号文件规定，自2026年1月1日起执行。"))
        self.assertEqual("", SH._repeal_note("税屋提示：本文由用户上传，仅供参考。"))

    def test_parse_article_carries_note_and_basis(self):
        art = SH._parse_article(_article_page(self.GANSU, own_pages=(1,)), P1)
        self.assertTrue(art["repeal_note"].startswith("依据甘人社通"))
        self.assertEqual(["〔2026〕86号"], art["repeal_basis"])

    def test_parse_article_omits_both_keys_without_a_note(self):
        art = SH._parse_article(_article_page("正文第一段，没有批注。", own_pages=(1,)), P1)
        self.assertNotIn("repeal_note", art)
        self.assertNotIn("repeal_basis", art)


# ── ③ L4 中国税网：检索契约、同稿合并、老地址判定与定层不把关 ──────────────
# 这一站的可用面全长在站点自己那一边（参数名、翻页基准、地址分界、注释标记写几遍），
# 所以用例全部离线：假会话按 URL 交出事先搭好的内容，发出去的参数留在会话上回读。
CTAX_SEARCH = CT.SEARCH_ENDPOINTS[0]
CTAX_NEW_URL = "https://www.ctaxnews.com.cn/policy/2023-02/10/content_1016514.html"
CTAX_OLD_URL = "http://www.ctaxnews.com.cn/2020-06/01/content_966206.html"
CTAX_OLD_URLS = "https://www.ctaxnews.com.cn/2020-06/01/content_966206.html"


def _ctax_row_json(title="财政部 税务总局公告2026年第29号", date="2026-10-19",
                   subtitle="财政部 税务总局公告2026年第29号", column="中央税收法规",
                   url=CTAX_NEW_URL, articleid="1016514", **extra):
    """一枚检索行的站点形态（15 枚键里这一层读的那几枚）。"""
    a = {"title": title, "date": date, "subtitle": subtitle, "url": url,
         "articleid": articleid, "nodesearchname": column,
         "introtitle": "财政部 国家税务总局", "sourcename": "中国税务报",
         "author": "2026年10月12日", "enpcontent": "正文起两行……",
         "Abstract": "编辑写的摘要"}
    a.update(extra)
    return a


def _ctax_json(rows, found=None):
    return json.dumps({"foundNum": found if found is not None else len(rows),
                       "article": list(rows)}, ensure_ascii=False)


class _CtaxResp:
    """照 `_http_get` 与 `fetch_ctax` 用得上的那几枚属性搭。
    encoding 按 requests 的规矩给默认值：声明了 charset 用声明的，没声明给 iso-8859-1。"""

    def __init__(self, body, status=200, ctype="application/json;charset=UTF-8"):
        self.text = body
        self.content = body.encode("utf-8")
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        m = re.search(r"charset=([\w-]+)", ctype, re.I)
        self.encoding = m.group(1) if m else "iso-8859-1"

    def json(self):
        return json.loads(self.text)


class _CtaxSession:
    """按 URL 发内容的假会话；每一次 (url, params) 都记下。
    值是列表的按顺序消费，用完还索要就 IndexError；没准备的地址直接 KeyError。
    Search.do 有两个落点，默认把域名那一路备好的内容同样交给镜像那一路；
    要单独试两路的用例自己给出两个键。"""

    def __init__(self, pages):
        self.pages = dict(pages)
        alt = CT.SEARCH_ENDPOINTS[1]
        if CTAX_SEARCH in self.pages and alt not in self.pages:
            self.pages[alt] = self.pages[CTAX_SEARCH]
        self.requested = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, **kw):
        self.requested.append((url, dict(params or {})))
        p = self.pages[url]
        if isinstance(p, list):
            p = p.pop(0)
        return p

    @property
    def sent_params(self):
        return [q for _, q in self.requested]


def _ctax_article_page(body="<p>正文第一段。</p><p>第二段</p>", props=None, times=2):
    p = props if props is not None else {
        "title": "财政部 税务总局公告2026年第29号",
        "date": "2026-10-19 09:10:00",
        "subtitle": "财政部 税务总局公告2026年第29号",
        "introtitle": "财政部 税务总局",
        "nodename": "中央税收法规", "nodeid": "8215",
        "liability": "全文有效", "tag": "增值税；企业所得税",
        "sourcename": "中国税务报", "author": "2026年10月12日",
        "abstract": "编辑摘要"}
    prop = ("<!--enpproperty"
            + "".join("<%s>%s</%s>" % (k, v, k) for k, v in p.items()) + "-->")
    block = "<!--enpcontent-->" + body + "<!--/enpcontent-->"
    return "<html><body>" + (block + prop) * times + "</body></html>"


class _CtaxOffline(unittest.TestCase):
    """共用：把站点侧的礼节性限速在离线用例里等掉。"""

    def setUp(self):
        p = mock.patch.object(CT.time, "sleep")
        p.start()
        self.addCleanup(p.stop)


class TestCtaxSearchContract(_CtaxOffline):
    """Search.do 的参数形状：翻页只认 pageNo、位置参数只提交一枚、越界值不发出去。"""

    def _one_hit(self, **kw):
        s = _CtaxSession({CTAX_SEARCH: _CtaxResp(_ctax_json([_ctax_row_json()]))})
        r = CT.search_ctax("增值税", size=1, session=s, **kw)
        return s, r

    def test_default_match_sends_only_q(self):
        s, _ = self._one_hit()
        q = s.sent_params[0]
        self.assertEqual("增值税", q["q"])
        for other in ("title", "content", "keyword", "author"):
            self.assertNotIn(other, q, "多提交一枚就换了匹配域")
        self.assertEqual(1, q["pageNo"])
        self.assertEqual(CT.SEARCH_PAGE_SIZE, q["pageSize"])
        self.assertEqual("date", q["sort"])
        self.assertEqual(CT.SITE_ID, q["siteID"])
        self.assertEqual(CT.CHANNEL_WEB, q["channel"])

    def test_match_title_sends_only_the_title_slot(self):
        """q= 会把位置参数盖掉：同发两枚时站点报的是 q 那一份数，限定等于没生效。"""
        s, _ = self._one_hit(match="title")
        q = s.sent_params[0]
        self.assertEqual("增值税", q["title"])
        self.assertNotIn("q", q)

    def test_page_aliases_are_never_sent(self):
        s, _ = self._one_hit()
        for alias in ("start", "page", "pageNum", "p", "offset", "currentPage"):
            self.assertNotIn(alias, s.sent_params[0],
                             "站点不认这些别名，被忽略后落回第一页")

    def test_channel_zero_omits_the_param(self):
        """站点给 channel=0 回 37 字节空结构；这一层把 0 当"不限"，省掉这一枚取两端并集。"""
        s, _ = self._one_hit(channel=0)
        self.assertNotIn("channel", s.sent_params[0])
        s2, _ = self._one_hit(channel=CT.CHANNEL_TOUCH)
        self.assertEqual(CT.CHANNEL_TOUCH, s2.sent_params[0]["channel"])

    def test_one_sided_date_sends_only_that_side(self):
        s, _ = self._one_hit(date_from="2025-01-01")
        self.assertEqual({"startDate"}, {"startDate", "endDate"} & set(s.sent_params[0]))
        s2, _ = self._one_hit(date_to="2026-10-01")
        self.assertEqual({"endDate"}, {"startDate", "endDate"} & set(s2.sent_params[0]))
        s3, _ = self._one_hit()
        self.assertEqual(set(), {"startDate", "endDate"} & set(s3.sent_params[0]))

    def test_bad_date_is_refused_before_the_request(self):
        """站点这一档越界直接回 HTTP 500，拦在发出之前才有意义。"""
        s = _CtaxSession({})
        r = CT.search_ctax("增值税", date_from="2025/01/01", session=s)
        self.assertEqual([], s.requested)
        self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
        self.assertIn("yyyy-mm-dd", r["_status"])

    def test_empty_keyword_without_column_is_refused(self):
        s = _CtaxSession({})
        r = CT.search_ctax("", session=s)
        self.assertEqual([], s.requested)
        self.assertIn("空词只在全站范围可用", r["_status"])

    def test_second_page_failure_is_not_rewritten_as_zero_rows(self):
        """第一页成了、第二页挂了：停在已取的条数并把这一句交出去，不许读成"共 0 条"。"""
        s = _CtaxSession({CTAX_SEARCH: [
            _CtaxResp(_ctax_json([_ctax_row_json(articleid="1")], found=500)),
            _CtaxResp("x", status=500, ctype="text/html"),
            _CtaxResp("x", status=500, ctype="text/html")]})
        r = CT.search_ctax("增值税", size=2, session=s)
        self.assertEqual(1, len(r["results"]))
        self.assertIn("第 2 页取不到", r["_status"])
        self.assertIn("停在已取的 1 条", r["_status"])

    def test_mirror_endpoint_is_tried_when_the_domain_fails(self):
        """域名那一路打不通就换镜像落点，换成功了不叫失败。"""
        s = _CtaxSession({CTAX_SEARCH: [_CtaxResp("x", status=500, ctype="text/html"),
                                        _CtaxResp(_ctax_json([_ctax_row_json()]))]})
        r = CT.search_ctax("增值税", size=1, session=s)
        self.assertEqual(2, len(s.requested))
        self.assertEqual(CT.SEARCH_ENDPOINTS[1], s.requested[1][0])
        self.assertEqual(1, len(r["results"]))
        self.assertNotIn("_error", r)

    def test_both_endpoints_failing_is_named_as_a_failure(self):
        """两个落点都试完才算失败；500 那句要带上"不是限流"的判据。"""
        s = _CtaxSession({CTAX_SEARCH: [_CtaxResp("x", status=500, ctype="text/html"),
                                        _CtaxResp("y", status=500, ctype="text/html")]})
        r = CT.search_ctax("增值税", size=1, session=s)
        self.assertEqual(2, len(s.requested))
        self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
        self.assertIn("HTTP 500", r["_status"])
        self.assertIn("不是限流", r["_status"])

    @staticmethod
    def _wide_pages(count):
        """备 count 页、每页 SEARCH_PAGE_SIZE 行且行行不同篇的清单，foundNum 给一份取不完的数。"""
        pages = []
        for p in range(count):
            rows = [_ctax_row_json(title="第%d页第%d行" % (p, i),
                                   articleid=str(300000 + p * 1000 + i), subtitle="")
                    for i in range(CT.SEARCH_PAGE_SIZE)]
            pages.append(_CtaxResp(_ctax_json(rows, found=100000)))
        return pages

    def test_fetch_all_stops_at_max_rows(self):
        """--max-rows 写的是行数上限，翻到底那一档过去只认页数，40 发交回 2000 行。"""
        s = _CtaxSession({CTAX_SEARCH: self._wide_pages(6)})
        r = CT.search_ctax("增值税", fetch_all=True, max_rows=60, session=s)
        self.assertEqual([1, 2], [q["pageNo"] for q in s.sent_params])
        self.assertEqual(60, len(r["results"]))
        self.assertIn("max_rows=60 行的上限", r["_status"])
        self.assertIn("不是这一词只有这些", r["_status"])

    def test_max_rows_only_tightens_and_never_expands_size(self):
        s = _CtaxSession({CTAX_SEARCH: self._wide_pages(6)})
        r = CT.search_ctax("增值税", size=1, max_rows=120, session=s)
        self.assertEqual(1, len(s.requested))
        self.assertEqual(1, len(r["results"]), "max_rows 不能反过来把 size 顶开")

    def test_channel_outside_the_sites_domain_is_refused(self):
        """站点给 3、负数回一份 37 字节空结构，那句读数跟"这一词没命中"长得一样。"""
        for ch in (3, 20, -1):
            s = _CtaxSession({})
            r = CT.search_ctax("增值税", size=1, channel=ch, session=s)
            self.assertEqual([], s.requested, "越界的 channel 不该发出去")
            self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
            self.assertIn("37 字节", r["_status"])

    def test_match_outside_the_four_slots_is_refused(self):
        """拼错的 match 过去会换成 q 发出去，交回的读数是全文那一路的，看着像限定生效了。"""
        for m in ("titel", "Tile", ""):
            s = _CtaxSession({})
            r = CT.search_ctax("增值税", size=1, match=m, session=s)
            self.assertEqual([], s.requested)
            self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
            self.assertIn("四枚", r["_status"])

    def test_non_positive_size_is_refused_unless_paging_to_the_end(self):
        s = _CtaxSession({})
        for size in (0, -1):
            r = CT.search_ctax("增值税", size=size, session=s)
            self.assertEqual([], s.requested, "size=0 过去白发一次请求再交回空清单")
            self.assertIn("一篇也不要", r["_status"])
        # 翻到底这一档不看 size：find_by_doc_number 按 size=0 加 max_rows 走
        s2 = _CtaxSession({CTAX_SEARCH: _CtaxResp(_ctax_json([_ctax_row_json()], found=1))})
        CT.search_ctax("增值税", size=0, fetch_all=True, session=s2)
        self.assertEqual(1, len(s2.requested))

    def test_negative_max_rows_is_refused(self):
        s = _CtaxSession({})
        r = CT.search_ctax("增值税", fetch_all=True, max_rows=-1, session=s)
        self.assertEqual([], s.requested)
        self.assertIn("负数", r["_status"])


class TestCtaxColumnScope(_CtaxOffline):
    """栏目限定的三种判据：唯一命中、重名、表里没有。后两种都不发出请求。"""

    TABLE = [
        {"nodeid": 24, "name": "政策", "dir": "policy", "foundNum": 0, "parent": True},
        {"nodeid": 8215, "name": "其他税种", "dir": "other", "foundNum": 23},
        {"nodeid": 63, "name": "实务分析", "dir": "sssw", "foundNum": 120},
        {"nodeid": 77, "name": "实务分析", "dir": "news", "foundNum": 90},
    ]

    def setUp(self):
        super().setUp()
        p = mock.patch.object(CT, "load_columns", return_value=self.TABLE)
        p.start()
        self.addCleanup(p.stop)

    def test_three_states_of_the_lookup(self):
        self.assertEqual(("ok", ["8215"]), CT.column_lookup("其他税种"))
        state, cand = CT.column_lookup("实务分析")
        self.assertEqual("ambiguous", state)
        self.assertEqual(2, len(cand))
        self.assertEqual(("missing", []), CT.column_lookup("没有这一栏"))
        self.assertEqual(("missing", []), CT.column_lookup("  "))

    def test_nodeid_of_returns_empty_for_both_non_ok_states(self):
        self.assertEqual("", CT.nodeid_of("实务分析"))
        self.assertEqual("", CT.nodeid_of("没有这一栏"))
        self.assertEqual("8215", CT.nodeid_of("其他税种"))

    def test_ambiguous_name_is_refused_without_a_half_limit(self):
        """站点确实有重名栏目，不替读者挑一条；报错把两行都列出来。"""
        s = _CtaxSession({})
        r = CT.search_ctax("增值税", node="实务分析", session=s)
        self.assertEqual([], s.requested)
        self.assertEqual("ambiguous column", r["_error"])
        self.assertIn("2 行对上", r["_status"])
        self.assertIn("nodeID", r["_status"])

    def test_missing_name_tells_to_rebuild_the_table(self):
        s = _CtaxSession({})
        r = CT.search_ctax("增值税", node="没有这一栏", session=s)
        self.assertEqual([], s.requested)
        self.assertIn("--update-columns", r["_status"])

    def test_parent_column_is_sent_but_flagged(self):
        """父栏目检索按叶节点收，整片回 0 不是"这一类没有文件"，要随读数交出。"""
        s = _CtaxSession({CTAX_SEARCH: _CtaxResp(_ctax_json([], found=0))})
        r = CT.search_ctax("增值税", node="政策", size=1, session=s)
        self.assertEqual("24", s.sent_params[0]["nodeID"])
        self.assertIn("父栏目", r["_filter_note"])
        self.assertIn("叶节点", r["_filter_note"])

    def test_leaf_column_carries_no_parent_note(self):
        s = _CtaxSession({CTAX_SEARCH: _CtaxResp(_ctax_json([_ctax_row_json()]))})
        r = CT.search_ctax("增值税", node="其他税种", size=1, session=s)
        self.assertEqual("8215", s.sent_params[0]["nodeID"])
        self.assertNotIn("_filter_note", r)

    def test_numeric_node_skips_the_table(self):
        s = _CtaxSession({CTAX_SEARCH: _CtaxResp(_ctax_json([_ctax_row_json()]))})
        CT.search_ctax("增值税", node="63", size=1, session=s)
        self.assertEqual("63", s.sent_params[0]["nodeID"])


class TestCtaxDedupCountsArticles(_CtaxOffline):
    """一稿多发：同一篇挂几栏就出几行，收满与计数都按篇不按行。"""

    @staticmethod
    def _four_columns(title="财政部 税务总局公告2026年第29号", date="2026-10-19",
                      doc="财政部 税务总局公告2026年第29号", aid="1016514"):
        cols = ["中央税收法规", "增值税", "其他税种", "财政部 国家税务总局公告"]
        return [_ctax_row_json(title=title, date=date, subtitle=doc, column=c,
                               articleid=str(int(aid) + i))
                for i, c in enumerate(cols)]

    def test_four_columns_merge_into_one_article(self):
        rows = CT.dedup_rows([CT._row_from_json(a) for a in self._four_columns()])
        self.assertEqual(1, len(rows))
        also = rows[0]["also_in_columns"]
        self.assertEqual(3, len(also))
        self.assertEqual(sorted(also), also, "同一份输入两次重建的顺序要一样")
        self.assertNotIn("中央税收法规", also, "首位那一栏不重复列进同稿")

    def test_merge_key_is_title_date_doc_number(self):
        """四行是四个 articleid，所以判据不用 articleid；文号与发布日期各管一件事。"""
        same = [CT._row_from_json(a) for a in self._four_columns()]
        self.assertEqual(1, len(CT.dedup_rows(same)))
        other_doc = self._four_columns()
        other_doc[2]["subtitle"] = "财政部 税务总局公告2026年第30号"
        merged = CT.dedup_rows([CT._row_from_json(a) for a in other_doc])
        self.assertEqual(2, len(merged), "同号不同件是两份文件，不许合")
        self.assertEqual("其他税种", merged[1]["column"])
        other_date = self._four_columns()
        other_date[1]["date"] = "2026-10-18"
        self.assertEqual(2, len(CT.dedup_rows([CT._row_from_json(a)
                                               for a in other_date])), "同名不同日发布不合")

    def test_size_is_counted_in_articles_not_rows(self):
        """第一页 8 行只合出 2 篇，--size 3 必须再翻一页；按行数收会当已收满。"""
        page1 = (self._four_columns()
                 + self._four_columns(title="国家税务总局公告2026年第15号",
                                      doc="国家税务总局公告2026年第15号", aid="1016520"))
        page2 = self._four_columns(title="增值税法实施条例", date="2026-09-01",
                                   doc="", aid="1016600")
        s = _CtaxSession({CTAX_SEARCH: [
            _CtaxResp(_ctax_json(page1, found=200)),
            _CtaxResp(_ctax_json(page2, found=200)),
            _CtaxResp(_ctax_json([], found=200))]})
        r = CT.search_ctax("公告", size=3, session=s)
        self.assertEqual(2, len(s.requested), "没收满就该翻页")
        self.assertEqual([1, 2], [q["pageNo"] for q in s.sent_params])
        self.assertEqual(3, len(r["results"]))
        self.assertIn("合并同一篇挂多栏的行 9 行", r["_status"])

    def test_small_size_still_pages_when_a_page_merges_too_hard(self):
        """一页 48 行只合出 1 篇：页数上限不拿 --size 去换算行数，否则 --size 2 永远取不到第二篇。"""
        rows = self._four_columns() * 12
        s = _CtaxSession({CTAX_SEARCH: [_CtaxResp(_ctax_json(rows, found=600)),
                                        _CtaxResp(_ctax_json(rows, found=600))]})
        r = CT.search_ctax("公告", size=1, session=s)
        self.assertEqual(1, len(s.requested), "一页就收满 1 篇，不必翻页")
        self.assertEqual(1, len(r["results"]))
        s2 = _CtaxSession({CTAX_SEARCH: [_CtaxResp(_ctax_json(rows, found=600)),
                                         _CtaxResp(_ctax_json(
                                             self._four_columns(
                                                 title="国家税务总局公告2026年第15号",
                                                 date="2026-09-30",
                                                 doc="国家税务总局公告2026年第15号",
                                                 aid="2000000"),
                                             found=600))]})
        r2 = CT.search_ctax("公告", size=2, session=s2)
        self.assertEqual(2, len(s2.requested), "第一页合出的篇数不够，第二页要发出去")
        self.assertEqual(2, len(r2["results"]))


class TestCtaxAddressState(_CtaxOffline):
    """2023-02 改版前的老行只看地址形状就能判，不必先吃一个 500。"""

    def test_three_segment_is_refused_on_both_protocols(self):
        for u in (CTAX_OLD_URL, CTAX_OLD_URLS):
            state = CT._address_state(u)
            self.assertNotEqual("ok", state, u)
            self.assertIn("2023-02", state)

    def test_four_segment_is_ok(self):
        self.assertEqual("ok", CT._address_state(CTAX_NEW_URL))

    def test_missing_url_is_refused(self):
        self.assertEqual("行里没有可用地址", CT._address_state(""))

    def test_fetch_sends_nothing_for_an_old_row(self):
        s = _CtaxSession({})
        art = CT.fetch_ctax(CTAX_OLD_URL, session=s)
        self.assertEqual([], s.requested)
        self.assertIn("2023-02", art["_error"])

    def test_row_carries_the_flag_only_when_unreachable(self):
        bad = CT._row_from_json(_ctax_row_json(url=CTAX_OLD_URL))
        good = CT._row_from_json(_ctax_row_json())
        self.assertIn("_url_state", bad)
        self.assertNotIn("_url_state", good)

    def test_row_field_mapping(self):
        row = CT._row_from_json(_ctax_row_json())
        self.assertEqual("财政部 税务总局公告2026年第29号", row["doc_number"])
        self.assertEqual("财政部 国家税务总局", row["agency"])
        self.assertEqual("中国税务报", row["byline"])
        self.assertEqual("中央税收法规", row["column"])
        self.assertEqual("2026年10月12日", row["draft_date"], "author 段装的是成文日期")
        self.assertEqual("2026-10-19", row["date"], "发布与成文两件事都交出")
        self.assertNotIn("author", row)

    def test_non_date_author_survives_as_author(self):
        row = CT._row_from_json(_ctax_row_json(author="张三"))
        self.assertEqual("张三", row["author"])
        self.assertNotIn("draft_date", row)

    def test_snippet_and_abstract_stay_display_only(self):
        """enpcontent 是正文截断、Abstract 是编辑摘要：两样都不接进抽取。"""
        row = CT._row_from_json(_ctax_row_json())
        self.assertEqual("正文起两行……", row["snippet"])
        self.assertEqual("编辑写的摘要", row["abstract"])
        row2 = CT._row_from_json(_ctax_row_json(enpcontent=""))
        self.assertEqual("编辑写的摘要", row2["snippet"])


class TestCtaxForcedUtf8(unittest.TestCase):
    """正文页回 `Content-Type: text/html` 不带 charset，requests 按 ISO-8859-1 解且不报错。"""

    URL = "https://www.ctaxnews.com.cn/policy/2023-02/10/content_9.html"

    def _get(self, ctype, encoding):
        resp = _CtaxResp("正文", ctype=ctype)
        resp.encoding = encoding
        got = CT._http_get(self.URL, session=_CtaxSession({self.URL: resp}))
        self.assertIs(resp, got)
        return got.encoding

    def test_html_without_charset_is_read_as_utf8(self):
        for enc in ("iso-8859-1", "latin-1", "ISO-8859-1"):
            self.assertEqual("utf-8", self._get("text/html", enc), enc)

    def test_declared_charset_is_left_to_the_site(self):
        self.assertEqual("gbk", self._get("text/html;charset=GBK", "gbk"))

    def test_a_default_that_is_not_latin1_is_not_rewritten(self):
        self.assertEqual("utf-8", self._get("text/html", "utf-8"))


class TestCtaxBodyBlocks(_CtaxOffline):
    """正文与元信息夹在注释标记里；政策页与专题页两对标记各写两遍。"""

    def _fetch(self, page):
        s = _CtaxSession({CTAX_NEW_URL: _CtaxResp(page, ctype="text/html")})
        return CT.fetch_ctax(CTAX_NEW_URL, session=s, fetch_interval=0)

    def test_identical_twins_take_the_first_and_say_so(self):
        art = self._fetch(_ctax_article_page())
        self.assertEqual("正文第一段。\n第二段", art["content"], "两份相同，取一份不是漏读")
        self.assertIn("逐字节相同", art["_note"])

    def test_differing_twins_warn_instead_of_choosing_silently(self):
        page = ("<html><body><!--enpcontent--><p>桌面版</p><!--/enpcontent-->"
                "<!--enpproperty<title>x</title>-->"
                "<!--enpcontent--><p>移动版</p><!--/enpcontent-->"
                "<!--enpproperty<title>x</title>--></body></html>")
        art = self._fetch(page)
        self.assertEqual("桌面版", art["content"])
        self.assertIn("两份不一致", art["_note"])
        self.assertIn("复测", art["_note"])

    def test_single_block_page_gets_no_note(self):
        art = self._fetch(_ctax_article_page(times=1))
        self.assertNotIn("_note", art)
        self.assertEqual("正文第一段。\n第二段", art["content"])

    def test_meta_fields_come_from_the_property_block(self):
        art = self._fetch(_ctax_article_page(times=1))
        self.assertEqual("2026-10-19", art["published"])
        self.assertEqual("财政部 税务总局公告2026年第29号", art["doc_number"])
        self.assertEqual("全文有效", art["liability"])
        self.assertEqual(["增值税", "企业所得税"], art["tags"])
        self.assertEqual("2026年10月12日", art["draft_date"])
        self.assertEqual("8215", art["nodeid"])

    def test_empty_content_is_named_picture_body(self):
        art = self._fetch(_ctax_article_page(body="", times=1))
        self.assertEqual("", art["content"])
        self.assertIn("图片型正文", art["_error"])

    def test_page_without_the_marker_reports_raw_length(self):
        page = "<html><body>无标记</body></html>"
        art = self._fetch(page)
        self.assertIn("enpcontent", art["_error"])
        self.assertEqual(len(page), art["_raw_len"])

    def test_error_page_is_read_as_a_dead_address(self):
        """这一站的错误地址回 500 而不是 404，读成地址不存在、不重试。"""
        s = _CtaxSession({CTAX_NEW_URL: _CtaxResp("x" * 572, status=500,
                                                  ctype="text/html")})
        art = CT.fetch_ctax(CTAX_NEW_URL, session=s, fetch_interval=0)
        self.assertIn("500 而不是 404", art["_error"])
        self.assertIn("不重试", art["_error"])


class TestCtaxReadKeepsUnreachableRows(_CtaxOffline):
    """取正文失败的行留在原位带上原因：丢了行就没人知道这一篇没读到。"""

    def test_old_row_keeps_its_place_with_the_reason(self):
        rows = [CT._row_from_json(_ctax_row_json(articleid="1016514")),
                CT._row_from_json(_ctax_row_json(url=CTAX_OLD_URL, articleid="966206"))]
        s = _CtaxSession({CTAX_NEW_URL: _CtaxResp(_ctax_article_page(times=1),
                                                  ctype="text/html")})
        out = CT.read_articles(rows, limit=2, session=s)
        self.assertEqual(2, len(out))
        self.assertEqual("正文第一段。\n第二段", out[0]["content"])
        self.assertIn("2023-02", out[1]["_error"])
        self.assertIn("2023-02", out[1]["_url_state"], "行上的可达性标注不被正文那一步抹掉")
        self.assertEqual([CTAX_NEW_URL], [u for u, _ in s.requested])


class TestCtaxGradingDoesNotGuardThisRoute(unittest.TestCase):
    """这一路的材料算不算实务层，程序不替读者判：`rank_of` 先按标题形态、
    再按来源兜底，而 `PRACTICE_SOURCES` 里没有这一站——转载件按原文件的位阶计。"""

    SRC = "中国税网 (ctaxnews.com.cn)"

    def test_this_site_is_not_in_the_practice_source_list(self):
        joined = "".join(E.PRACTICE_SOURCES)
        self.assertNotIn("ctaxnews", joined)
        self.assertNotIn("中国税网", joined)

    def test_reprint_is_rated_as_the_original_document(self):
        r = E.rank_of("财政部 税务总局公告2026年第29号", source=self.SRC)
        self.assertEqual("normative", r["rank"], r["by"])
        self.assertIn("标题形态", r["by"])
        self.assertEqual("direct", E.role_of(r["rank"], "effective", True))
        self.assertNotEqual("practice", E.role_of(r["rank"], "effective", True))

    def test_only_an_interpretation_title_reaches_the_practice_tier(self):
        """标题以「解读」收尾才落到实务档；按来源名判不到这一站。"""
        hit = E.rank_of("增值税留抵退税政策的解读", source=self.SRC)
        self.assertIn(hit["rank"], E.PRACTICE_RANKS)
        self.assertEqual("practice", E.role_of(hit["rank"], "effective", True))
        miss = E.rank_of("某省税务局关于征管有关事项的说明", source=self.SRC)
        self.assertEqual("unknown", miss["rank"], miss["by"])
        self.assertEqual("unmatched", E.role_of(miss["rank"], "effective", False))


class TestCtaxColumnPageRows(unittest.TestCase):
    """取行只按地址形状，不认列表容器的类名（站点实测有三枚类名，见 NOTE.md
    「栏目页的行形态」）；切不出本栏标题时整页都算本栏，
    靠目录段与本栏检索集两道过滤扣外来行。"""

    @staticmethod
    def _href(d, ym, dd, aid):
        """站点地址里就带着发布日期：/<目录>/<yyyy-mm>/<dd>/content_<id>.html。"""
        return "https://www.ctaxnews.com.cn/%s/%s/%s/content_%s.html" % (d, ym, dd, aid)

    @classmethod
    def _li(cls, d, ym, dd, aid, text):
        return '<li><a href="%s">%s</a></li>' % (cls._href(d, ym, dd, aid), text)

    def _page(self, own=(), extra=(), header=True):
        """extra 那几行放进 layout-380 里面，是侧栏推荐位；
        own 那几行是本栏列表，其中目录段不对的属于"留在段内的外来行"。
        外壳类名 `tabList` 是合成的：站点这一批 14 份栏目页副本里它 0 处命中
        （见 NOTE.md「栏目页的行形态」），故意挑一枚站点没发过的名字，
        才能验出 `_page_rows` 根本不读类名。"""
        head = '<div class="column-header-1">税案通报</div>' if header else ""
        return ('<html><body>%s<ul class="tabList">%s</ul>'
                '<div class="layout-380">侧栏推荐%s</div></body></html>'
                % (head, "".join(own), "".join(extra)))

    def test_rows_come_from_the_href_shape_not_a_container_class(self):
        """这一页的行包在合成类名 `ul.tabList` 里，没有 div.newsList-item：
        按类名取行的话读出 0 行，按地址形状取照样交回 2 行。"""
        page = self._page(own=[self._li("sswfaj", "2026-06", "22", "111", "某案通报"),
                               self._li("sswfaj", "2026-05", "08", "222", "另一案通报")])
        rows, off_col, sliced, off_set = CT._page_rows(page, "sswfaj", "税案通报")
        self.assertEqual(2, len(rows))
        self.assertEqual("2026-06-22", rows[0]["date"], "发布日期就在地址里")
        self.assertTrue(sliced)
        self.assertEqual((off_col, off_set), (0, 0))

    def test_off_directory_rows_inside_the_region_are_counted(self):
        page = self._page(own=[self._li("sswfaj", "2026-06", "22", "111", "本案"),
                               self._li("policy", "2026-06", "20", "333", "别栏的政策")])
        rows, off_col, sliced, _ = CT._page_rows(page, "sswfaj", "税案通报")
        self.assertEqual(1, len(rows))
        self.assertEqual(1, off_col)
        self.assertTrue(sliced)

    def test_sidebar_rows_are_cut_before_scanning(self):
        """侧栏那块在 layout-380 之后：整段先切掉，所以不占 off_col 那一档。"""
        page = self._page(own=[self._li("sswfaj", "2026-06", "22", "111", "本案")],
                          extra=[self._li("policy", "2026-06", "20", "333", "侧栏推荐")])
        rows, off_col, sliced, _ = CT._page_rows(page, "sswfaj", "税案通报")
        self.assertEqual(1, len(rows))
        self.assertEqual(0, off_col)
        self.assertTrue(sliced)

    def test_unsliced_page_is_judged_by_the_search_set(self):
        """页面没有 column-header-1 时切不出本栏段，行的归属只能由本栏检索集判。
        实测走这一支的三栏：按别的栏铺块的落地页 node_13、
        只铺一块相关文号列表的短页 node_8030、父栏目页 node_24；
        专题页 node_8131 有本栏标题，不在此列。"""
        page = self._page(header=True,
                          own=[self._li("sswfaj", "2026-06", "22", "111", "在册的"),
                               self._li("sswfaj", "2026-06", "21", "222", "不在册的")])
        no_head = self._page(header=False,
                             own=[self._li("sswfaj", "2026-06", "22", "111", "在册的"),
                                  self._li("sswfaj", "2026-06", "21", "222", "不在册的")])
        _, _, sliced, _ = CT._page_rows(page, "sswfaj", "税案通报")
        self.assertTrue(sliced)
        rows, off_col, sliced2, off_set = CT._page_rows(
            no_head, "sswfaj", "税案通报", {"111"})
        self.assertFalse(sliced2)
        self.assertEqual(1, len(rows))
        self.assertEqual((off_col, off_set), (0, 1))
        rows_all, _, _, _ = CT._page_rows(no_head, "sswfaj", "税案通报")
        self.assertEqual(2, len(rows_all), "不给检索集时同目录的行一律收，宁多不漏")

    def test_date_only_line_is_not_glued_into_the_title(self):
        """发布日期与标题在同一枚锚里，中间由 <br> 或块级标签分开：只剔纯日期那一行。"""
        li = ('<li><a href="%s">某逃税案查处情况通报'
              '<br><span class="time">2026.6.22</span></a></li>'
              % self._href("sswfaj", "2026-06", "22", "111"))
        rows, _, _, _ = CT._page_rows(self._page(own=[li]), "sswfaj", "税案通报")
        self.assertEqual("某逃税案查处情况通报", rows[0]["title"])
        # 规则的作用范围：日期与标题之间没有任何分隔时，两者本就是同一行，
        # 这一条判据不动它——留着这一句，免得以后把"整行只有日期"读成"标题里不含日期"。
        glued = ('<li><a href="%s"><span>某逃税案查处情况通报</span>'
                 '<span class="time">2026.6.22</span></a></li>'
                 % self._href("sswfaj", "2026-06", "22", "112"))
        rows2, _, _, _ = CT._page_rows(self._page(own=[glued]), "sswfaj", "税案通报")
        self.assertEqual("某逃税案查处情况通报2026.6.22", rows2[0]["title"])

    def test_two_line_title_keeps_both_lines(self):
        li = ('<li><a href="%s"><p>关于发布</p><p>增值税若干事项的公告</p></a></li>'
              % self._href("sswfaj", "2026-06", "22", "111"))
        rows, _, _, _ = CT._page_rows(self._page(own=[li]), "sswfaj", "税案通报")
        self.assertEqual("关于发布 增值税若干事项的公告", rows[0]["title"])

    def test_image_only_row_is_dropped_not_invented(self):
        li = '<li><a href="%s"><img src="/i/1.jpg"></a></li>' % self._href(
            "sswfaj", "2026-06", "22", "111")
        rows, _, _, _ = CT._page_rows(self._page(own=[li]), "sswfaj", "税案通报")
        self.assertEqual([], rows)

    def test_summary_attaches_to_the_title_row(self):
        href = self._href("sswfaj", "2026-06", "22", "111")
        chunk = ('<div class="newsList-item clearfix">'
                 '<p class="title"><a href="%s">某案通报</a></p>'
                 '<p class="summary"><a href="%s">案情摘要两行</a></p>'
                 '<span class="time">2026.6.22</span></div></div>' % (href, href))
        page = ('<html><body><div class="column-header-1">税案通报</div>%s'
                '<div class="layout-380">侧栏</div></body></html>' % chunk)
        rows, off_col, sliced, _ = CT._page_rows(page, "sswfaj", "税案通报")
        self.assertEqual(1, len(rows))
        self.assertTrue(sliced)
        self.assertEqual("案情摘要两行", rows[0]["snippet"])

    def test_pages_below_one_is_refused_instead_of_reading_one_page(self):
        """--pages 0 与负数过去落到 max(pages, 1)，交回的清单不在请求里。"""
        for pages in (0, -1):
            s = _CtaxSession({})
            r = CT.list_column("63", pages=pages, session=s)
            self.assertEqual([], s.requested)
            self.assertTrue(r["_status"].startswith("没走成"), r["_status"])
            self.assertIn("一页也不要", r["_status"])


class TestCtaxSubSkillDocHooks(unittest.TestCase):
    """子技能文档点名的符号要在发货代码里；探针的逐项请求数与 NOTE.md 那张表对得上账。"""

    DOC_DIR = ROOT / "subskills" / "ctaxnews"

    def test_named_symbols_exist(self):
        named = set()
        for name in ("SKILL.md", "NOTE.md"):
            text = (self.DOC_DIR / name).read_text(encoding="utf-8")
            named |= set(re.findall(r"tax_ctax\.(?!py\b)([A-Za-z_][A-Za-z0-9_]*)", text))
        self.assertTrue(named, "两份文档至少要点名一条判据")
        self.assertEqual([], [n for n in sorted(named) if not hasattr(CT, n)])

    def test_probe_item_costs_match_the_note_table(self):
        src = (self.DOC_DIR / "probe_ctaxnews.py").read_text(encoding="utf-8")
        cost = None
        for node in ast.parse(src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "COST"):
                cost = ast.literal_eval(node.value)
        self.assertIsNotNone(cost, "探针里没有 COST 那张表")
        text = (self.DOC_DIR / "NOTE.md").read_text(encoding="utf-8")
        block = text.split("## 探针项与请求数", 1)
        self.assertEqual(2, len(block), "NOTE.md 里没有「探针项与请求数」这一节")
        table = {}
        for ln in block[1].splitlines():
            m = re.match(r"^\|\s*(\d+)\s*\|.*\|\s*(?:约\s*)?(\d+)\s*\|\s*$", ln)
            if m:
                table[int(m.group(1))] = int(m.group(2))
        self.assertEqual(sorted(cost), sorted(table), "表的行与探针的项不成同一集合")
        for n in cost:
            self.assertEqual(cost[n], table[n], "第 %d 项的请求数对不上" % n)
        total, fast = sum(cost.values()), sum(cost.values()) - cost[10]
        self.assertEqual(209, total, "探针的 COST 合计与文档那两个数不等，先核对哪一侧改了")
        self.assertEqual(107, fast, "去掉第 10 项（栏目表重建）之后的合计对不上")
        # 同一对合计复述在六处文字里：NOTE 的复测入口那一行、表下面那一行、单轮耗时那一行，
        # 子技能 SKILL 命令行那两条注释，README 目录树那一行，台账登记那一行。
        # 这六处都在用例够得着的路径上——改了表不改它们，读者按文字发请求就会数错。
        entry = [ln for ln in text.splitlines() if "复测入口" in ln]
        self.assertEqual(1, len(entry), "NOTE.md 里复测入口那一行不唯一")
        self.assertEqual([total, fast],
                         [int(x) for x in re.findall(r"(\d+) 次", entry[0])],
                         "NOTE.md 复测入口那一行的两个数与表不等")
        below = [ln for ln in block[1].splitlines() if ln.startswith("全项")]
        self.assertEqual(1, len(below), "表下面那行合计不唯一")
        self.assertEqual([total, fast],
                         [int(x) for x in re.findall(r"(\d+) 次", below[0])][:2],
                         "表下面那行的两个数与表不等")
        skill = (self.DOC_DIR / "SKILL.md").read_text(encoding="utf-8").splitlines()
        for label, hit, want in (("全项复测", "全项复测", total),
                                 ("--fast", "--fast", fast)):
            ln = [l for l in skill if hit in l and "次" in l]
            self.assertEqual(1, len(ln), "SKILL.md 里 %s 那一行不唯一" % label)
            self.assertEqual(want, int(re.search(r"(\d+) 次", ln[0]).group(1)),
                             "SKILL.md %s 那行的请求数与表不等" % label)
        for rel, phrase in ((self.DOC_DIR / "NOTE.md", "`--fast` 一轮 %d 次请求" % fast),
                            (self.DOC_DIR / "NOTE.md", "全项一轮 %d 次" % total),
                            (ROOT / "README.md",
                             "全项声明 %d 次请求、`--fast` %d 次" % (total, fast)),
                            (ROOT / "references" / "source_defects.md",
                             "（全项 %d、去掉第 10 项后 %d）" % (total, fast))):
            n = rel.read_text(encoding="utf-8").count(phrase)
            self.assertEqual(1, n, "%s 里 %r 要与表相等且只出现一次，实际 %d 次"
                             % (rel.name, phrase, n))

    def test_the_route_is_wired_into_the_aggregator(self):
        """这一路是 ④ 的第 6 源：六处登记一处不缺，日期走服务端而不是本地补筛。

        逐处分开断言——漏一处不会带着别处一起报红：漏 `SOURCE_IMPACT` 时缺口句只剩
        "该源本次没有取到内容"这句兜底，漏 `SOURCE_LABELS` 时缺口行印成裸键 ctax，
        漏 `DATE_SERVER_SOURCES` 时它被本地补筛再过一遍、同一维度算两次。
        """
        for table in ("DEFAULT_SOURCES", "SOURCE_RANK", "SOURCE_LABELS",
                      "SOURCE_IMPACT", "SOURCE_PLAIN", "DATE_SERVER_SOURCES"):
            self.assertIn("ctax", getattr(AGG, table), "接线缺在 %s" % table)
        self.assertNotIn("ctax", AGG.DATE_LOCAL_SOURCES)
        # 四张按源键索引的表与名单必须同集合：只往名单加一枚、表里没登记，
        # 运行时是 KeyError 或 .get 兜底，看输出看不出来。
        keys = {frozenset(AGG.SOURCE_RANK), frozenset(AGG.SOURCE_LABELS),
                frozenset(AGG.SOURCE_IMPACT), frozenset(AGG.SOURCE_PLAIN)}
        self.assertEqual(1, len(keys), "四张源表的键集合不一致")
        self.assertEqual(sorted(AGG.DEFAULT_SOURCES), sorted(keys.pop()))
        # 服务端那一档要逐源登记字段名，否则回显给出"某源由接口自己收窄"却没说收哪一枚
        self.assertEqual(sorted(AGG.DATE_SERVER_SOURCES), sorted(AGG.DATE_SERVER_FIELDS))
        with mock.patch.object(AGG, "search_ctax",
                               return_value={"total": 1, "results": []}) as spy:
            r = AGG.aggregate_search("测试词", size=5, sources=["ctax"],
                                     date_from="2024-01-01", date_to="2026-12-31")
        self.assertEqual("2024-01-01", spy.call_args.kwargs["date_from"])
        self.assertEqual("2026-12-31", spy.call_args.kwargs["date_to"])
        self.assertEqual(["ctax"], r["_date_filter"]["server_side"])
        self.assertEqual({}, r["_date_filter"]["local_window"],
                         "日期已经下推给接口，不该再在窗口内补筛一遍")
        self.assertIn("中国税网收发布日期", r["_date_note"])
        # SKILL 里两处都得各自钉住：只按整篇 assertIn 时，⑪ 那一行能替 ③ L4 那一格
        # 过关，L4 里的接线说明被删掉也不报红，而按 L4 走的调用方读的就是那一格。
        lines = (ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()
        starts = [i for i, ln in enumerate(lines)
                  if ln.startswith("- **L4 实务案例与解读")]
        self.assertEqual(1, len(starts), "③ 的 L4 那一格在 SKILL 里要只有一处")
        block = lines[starts[0]:starts[0] + 40]
        block = block[:next((i for i, ln in enumerate(block) if not ln.strip()),
                            len(block))]
        self.assertTrue(any("源键 `ctax`" in ln for ln in block),
                        "③ L4 那一段要写明这一路在 ④ 的默认名单里")
        rows = [ln for ln in lines if ln.startswith("| 中国税网 ctaxnews.com.cn")]
        self.assertEqual(1, len(rows), "⑪ 数据源表里中国税网那一行要只有一处")
        self.assertIn("源键 `ctax`", rows[0],
                      "⑪ 那一行也要写明它在 ④ 的默认名单里")


# ── flk 的 total 随参数换值：回显不许把它当命中数或可达页数 ──────────────────
class _NoCache:
    def _key(self, *parts):
        return "|".join(str(p) for p in parts)

    def get(self, key, max_age=0):
        return None

    def set(self, key, value):
        pass


class _FlkResp:
    """`POST /law-search/search/list` 的响应形状：`total` 与 `rows` 都在**根节点**上，
    没有 `data` 那一层（同前缀下的 `enumData` 反倒包在 `data` 里，两条形态相反）。"""

    def __init__(self, total, rows):
        self._payload = {"total": total, "rows": rows, "code": 200, "msg": "查询成功",
                         "searchType": 2, "searchContent": None}
        self.status_code = 200
        self.content = b"{}"
        self.headers = {"Content-Type": "application/json"}
        self.text = "{}"

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestFlkTotalIsNotTheEchoedHitCount(unittest.TestCase):
    """命令行的回显：flk 那一格 `total` 是接口随参数换值的回报，不是命中总数。

    取样口径与逐发读数在 `subskills/npc-flk/NOTE.md` 第二节：同一枚「环境保护」在
    `pageSize=5` 回 3,118、在 10／20／50 回 224，`pageSize=10` 翻到第 23 页它又回 3,118。
    按 `⌈total/pageSize⌉` 报"一共几页"会在一份还能翻的集合上给出一个假的顶。
    """

    def _cli_out(self, total, rows, size):
        buf = io.StringIO()
        argv = ["tax_search.py", "环境保护", "--size", str(size)]
        with mock.patch.object(T, "_request", lambda m, u, **k: _FlkResp(total, rows)), \
                mock.patch.object(T, "_cache", _NoCache()), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(buf):
            T.main()
        return buf.getvalue()

    def _rows(self, n):
        return [{"bbbs": "id%d" % i, "flfgname": "环境保护法条目%d" % i,
                 "gbrq": "2024-01-01", "sxrq": "2024-02-01", "sxx": 3,
                 "zdjgName": "全国人大常委会", "flxz": "法律"} for i in range(n)]

    def test_rows_read_from_the_root_and_total_is_labelled_as_the_interface_value(self):
        """根节点这一层不包 `data` 也要取到行；`total` 那一格要说成接口回报。"""
        out = self._cli_out(224, self._rows(20), 20)
        self.assertIn("环境保护法条目0", out, "按 data 统一解包会把整批读成空")
        self.assertIn("接口 total 224 条", out)
        self.assertNotIn("共 224 条", out, "这一枚不是命中总数，不许报成「共」")

    def test_no_page_ceiling_derived_from_total(self):
        """224 条按每页 20 行算出的"12 页"不许出现——同一集合按每页 5 行实测翻到 624 页。"""
        out = self._cli_out(224, self._rows(20), 20)
        self.assertIsNone(re.search(r"第 \d+/\d+ 页", out),
                          "回显不许按 total 报可达页数：%s" % out)
        self.assertIn("本屏 20 条", out)


# ── 用例自身的能力自检：规则不能永远绿 ─────────────────────────────────────
class TestRulesCanActuallyFail(unittest.TestCase):
    """把规则作用在构造的反例上，确认它真的拦得住。"""

    def test_degraded_note_is_empty_only_without_gaps(self):
        self.assertEqual("", AGG._degraded_note(["npc"], [], {"npc": 3}))
        note = AGG._degraded_note(["npc"], [{"source": "npc", "label": "NPC"}],
                                  {"npc": 3})
        self.assertTrue(note)

    def test_reliability_marker_normalizes_only_known_levels(self):
        """认不出的取值一律当"没有标记"，免得凭空多出一条提醒。"""
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
