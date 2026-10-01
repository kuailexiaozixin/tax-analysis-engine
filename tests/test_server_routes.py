#!/usr/bin/env python3
"""Flask 服务端路由用例。全部打桩上游，不联网。

tax_server.py 是前端唯一的后端，9 个路由需要接口级测试钉住契约——
只靠联网 e2e 间接碰到它时，e2e 失败说不清是"路由写错了"还是
"对方限流了"。这一组把路由自己的契约钉住：

  - 状态码与错误码（400 / 404 / 500 / 503）
  - 参数怎么传给了下层（关键词换成本体法名、province 只回填不过滤）
  - 分支路由（npc / chinatax / fgk / aggregated）
  - 付费闸门关着时不许碰到模型
  - 上游报错要如实透出，不能吞成空结果

不测的东西：下层的检索质量、真实站点的可用性——那些属于联网 e2e 与评测集。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_server  # noqa: E402

NPC_TAX_TYPE = {
    "type": "增值税",
    "aliases": ["增值税"],
    "authority": "npc",
    "parent_law": "中华人民共和国增值税法",
}
STA_TAX_TYPE = {
    "type": "转让定价",
    "aliases": ["转让定价"],
    "authority": "sta",
    "search_term": "特别纳税调整",
}


class _RouteCase(unittest.TestCase):
    def setUp(self):
        tax_server.app.config["TESTING"] = True
        self.client = tax_server.app.test_client()


class TestStaticRoutes(_RouteCase):
    def test_index_serves_frontend(self):
        # send_from_directory 把正文包成文件型响应，test client 不自动关；
        # 不关就会在 `-W error::ResourceWarning` 下报未关闭文件句柄
        with self.client.get("/") as r:
            self.assertEqual(200, r.status_code)
            self.assertIn(b"<html", r.data.lower())

    def test_quick_tax_types_shape(self):
        r = self.client.get("/api/quick-tax-types")
        self.assertEqual(200, r.status_code)
        items = r.get_json()
        self.assertEqual(12, len(items))
        for it in items:
            self.assertEqual({"label", "icon", "keyword"}, set(it))
            self.assertTrue(it["keyword"])

    def test_health_reports_gate_closed(self):
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("", "闸门没开")):
            r = self.client.get("/api/health")
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual("ok", body["status"])
        self.assertFalse(body["paid_llm"]["enabled"])
        self.assertEqual("闸门没开", body["paid_llm"]["explanation"])

    def test_health_reports_gate_open(self):
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("/usr/bin/model-cli", "")):
            body = self.client.get("/api/health").get_json()
        self.assertTrue(body["paid_llm"]["enabled"])


class TestSearchRoute(_RouteCase):
    def _post(self, payload, tax_type=None, intent="policy_lookup"):
        for p in (mock.patch.object(tax_server, "detect_intent", return_value=intent),
                  mock.patch.object(tax_server, "resolve_tax_type", return_value=tax_type)):
            p.start()
            self.addCleanup(p.stop)
        return self.client.post("/api/search", json=payload)

    def test_requires_keyword(self):
        with mock.patch.object(tax_server, "search_tax") as m:
            r = self.client.post("/api/search", json={})
        self.assertEqual(400, r.status_code)
        self.assertEqual("keyword required", r.get_json()["error"])
        m.assert_not_called()          # 参数没过关就不许去打源

    def test_rejects_blank_keyword(self):
        """只有空格的关键词也要当成没给，否则会拿空串去打五源。"""
        with mock.patch.object(tax_server, "search_tax") as m:
            r = self.client.post("/api/search", json={"keyword": "   "})
        self.assertEqual(400, r.status_code)
        m.assert_not_called()

    def test_swaps_keyword_for_body_law(self):
        """归类出税种就要按本体法名检索。

        拿用户原话去标题检索时，"费用"这类通用字会把《诉讼费用交纳办法》
        顶到首位，所以这一步必须真的换词。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            r = self._post({"keyword": "增值税税率"}, tax_type=NPC_TAX_TYPE)
        self.assertEqual(200, r.status_code)
        self.assertEqual("中华人民共和国增值税法", m.call_args[0][0])
        body = r.get_json()
        self.assertEqual("增值税", body["tax_type"])
        # user_keyword 保留用户原话，只把内部检索词换成本体法名
        self.assertEqual("增值税税率", body["user_keyword"])
        self.assertIn("本体法", body["result"]["_routed"])

    def test_keeps_raw_keyword_when_no_tax_type(self):
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            self._post({"keyword": "诉讼费用"}, tax_type=None)
        self.assertEqual("诉讼费用", m.call_args[0][0])

    def test_flags_unrouted_in_npc_branch(self):
        """归不出税种时检索词是原话，一整摞字面匹配结果必须自己说清来路。

        不写这句，687 条无关法条看起来就像按本题找出来的依据。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}):
            r = self._post({"keyword": "诉讼费用"}, tax_type=None)
        self.assertEqual(tax_server.UNROUTED_NOTE, r.get_json()["result"]["_routed"])

    def test_flags_unrouted_in_aggregated_branch(self):
        """聚合走的是同一套换词逻辑，漏了这句就会只在单查时提示。"""
        with mock.patch.object(tax_server, "aggregate_search", return_value={}):
            r = self._post({"keyword": "诉讼费用", "source": "aggregated"},
                           tax_type=None)
        self.assertEqual(tax_server.UNROUTED_NOTE, r.get_json()["result"]["_routed"])

    def test_routed_note_says_which_law_not_unrouted(self):
        """归类成功时不能同时冒出"未归类"，否则提示自相矛盾。"""
        with mock.patch.object(tax_server, "search_tax", return_value={}):
            r = self._post({"keyword": "增值税税率"}, tax_type=NPC_TAX_TYPE)
        note = r.get_json()["result"]["_routed"]
        self.assertNotEqual(tax_server.UNROUTED_NOTE, note)
        self.assertIn("中华人民共和国增值税法", note)

    def test_routes_sta_topic_to_fgk(self):
        """sta 专题（转让定价等）在 NPC 库里检索无效，必须改查总局法规库。"""
        with mock.patch.object(tax_server, "search_fgk", return_value={}) as m:
            r = self._post({"keyword": "转让定价"}, tax_type=STA_TAX_TYPE)
        self.assertEqual(200, r.status_code)
        self.assertEqual("特别纳税调整", m.call_args[0][0])
        self.assertIn("总局专题", r.get_json()["result"]["_routed"])

    def test_chinatax_source(self):
        with mock.patch.object(tax_server, "search_chinatax", return_value={}) as m:
            r = self._post({"keyword": "发票", "source": "chinatax"}, tax_type=None)
        self.assertEqual(200, r.status_code)
        self.assertEqual("发票", m.call_args[0][0])

    def test_fgk_source_passes_body_flag(self):
        with mock.patch.object(tax_server, "search_fgk", return_value={}) as m:
            self._post({"keyword": "发票", "source": "fgk", "body": True}, tax_type=None)
        self.assertTrue(m.call_args.kwargs["with_body"])

    def test_aggregated_drops_npc_for_sta_topic(self):
        """聚合里也要换源：sta 专题留着 NPC 只会带回无关法规。"""
        with mock.patch.object(tax_server, "DEFAULT_SOURCES",
                               ["npc", "chinatax", "fgk"]), \
             mock.patch.object(tax_server, "aggregate_search", return_value={}) as m:
            r = self._post({"keyword": "转让定价", "source": "aggregated"},
                           tax_type=STA_TAX_TYPE)
        self.assertEqual(200, r.status_code)
        self.assertEqual(["chinatax", "fgk"], m.call_args.kwargs["sources"])
        self.assertEqual("特别纳税调整", m.call_args[0][0])

    def test_aggregated_uses_body_law_and_exact(self):
        with mock.patch.object(tax_server, "aggregate_search", return_value={}) as m:
            self._post({"keyword": "增值税税率", "source": "aggregated"},
                       tax_type=NPC_TAX_TYPE)
        self.assertEqual("中华人民共和国增值税法", m.call_args[0][0])
        self.assertTrue(m.call_args.kwargs["exact"])

    def test_province_is_echoed_but_not_used(self):
        """province 只回填。

        NPC 库是全国性法规，没有省级维度；前端"省份"下拉对搜索结果不起
        过滤作用。这里钉住"只回填"，免得日后误以为它生效了。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            r = self._post({"keyword": "增值税", "province": "广东"}, tax_type=None)
        self.assertEqual("广东", r.get_json()["province"])
        self.assertNotIn("广东", str(m.call_args))

    def test_size_is_capped(self):
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            self._post({"keyword": "增值税", "size": 999}, tax_type=None)
        self.assertEqual(50, m.call_args.kwargs["size"])

    def test_intent_label_is_translated(self):
        with mock.patch.object(tax_server, "search_tax", return_value={}):
            r = self._post({"keyword": "增值税"}, tax_type=None, intent="policy_lookup")
        self.assertEqual("政策查询", r.get_json()["intent_label"])

    def test_accounting_note_keys_on_the_raw_question_not_the_search_term(self):
        """税种归类会把关键词换成上位法名，会计缺口要按用户原话判。

        换成法名之后"账面价值""债务重组"这类会计要件词就没了，缺口会整栏消失——
        界面也就永远不会提示该去取准则。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}):
            hit = self._post({"keyword": "以自产产品抵偿到期债务，债务重组的所得税怎么处理"},
                             tax_type={"type": "企业所得税", "aliases": [],
                                       "authority": "npc",
                                       "parent_law": "企业所得税法"},
                             intent="policy_lookup").get_json()
            miss = self._post({"keyword": "小规模纳税人季度销售额30万元免征增值税吗"},
                              tax_type=None, intent="policy_lookup").get_json()
        self.assertIn("subskills/chenyiwei-bbs", hit["accounting_note"])
        self.assertEqual("", miss["accounting_note"])

    def test_legislative_note_survives_the_keyword_swap(self):
        """点名草案时关键词会被换成本体法名，"草案"两个字只有原话里有。

        换了名之后阶段判据消失，界面就把现行有效版当成草案内容列出来——
        这一栏存在的理由正是提醒那不是同一份文本（判据见 tax_analyze.legislative_stage）。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}):
            hit = self._post({"keyword": "新版《税收征管法》修订草案有哪些变化"},
                             tax_type={"type": "税收征管", "aliases": [],
                                       "authority": "npc",
                                       "parent_law": "中华人民共和国税收征收管理法"},
                             intent="policy_lookup").get_json()
            miss = self._post({"keyword": "增值税的征税范围有哪些"},
                              tax_type=None, intent="policy_lookup").get_json()
        self.assertIn("立法过程文件", hit["legislative_note"])
        self.assertIn("现行有效版本", hit["legislative_note"])
        self.assertEqual("", miss["legislative_note"])


class TestDetailRoute(_RouteCase):
    def test_ok(self):
        with mock.patch.object(tax_server, "fetch_detail",
                               return_value={"title": "增值税法", "status_text": "现行有效"}):
            r = self.client.get("/api/detail/abc123")
        self.assertEqual(200, r.status_code)
        self.assertEqual("增值税法", r.get_json()["detail"]["title"])

    def test_upstream_error_becomes_500(self):
        with mock.patch.object(tax_server, "fetch_detail",
                               side_effect=RuntimeError("上游超时")):
            r = self.client.get("/api/detail/abc123")
        self.assertEqual(500, r.status_code)
        self.assertIn("上游超时", r.get_json()["error"])


class TestTextRoute(_RouteCase):
    def test_classifies_paragraph_types(self):
        paras = ["第一章 总则", "第一条 为了规范…", "正文一句话"]
        with mock.patch.object(tax_server, "fetch_detail", return_value={"title": "X"}), \
             mock.patch.object(tax_server, "_download_and_extract", return_value=paras):
            r = self.client.get("/api/text/abc123")
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual(3, body["total_paragraphs"])
        self.assertEqual(1, body["article_count"])
        self.assertEqual(["chapter", "article", "body"],
                         [s["type"] for s in body["sections"]])
        self.assertEqual("第一章 总则", body["sections"][1]["chapter"])

    def test_empty_extraction_is_not_an_error(self):
        """取不到条文不是服务端错误，前端另有空态文案。"""
        with mock.patch.object(tax_server, "fetch_detail", return_value={"title": "X"}), \
             mock.patch.object(tax_server, "_download_and_extract", return_value=[]):
            r = self.client.get("/api/text/abc123")
        self.assertEqual(200, r.status_code)
        self.assertEqual(0, r.get_json()["total_paragraphs"])


class TestAiInterpretRoute(_RouteCase):
    def test_blocked_when_gate_closed(self):
        """关着就必须 503，而且不能碰到模型——碰一下就是别人的钱。"""
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("", "未开启付费通道")), \
             mock.patch.object(tax_server.tax_llm, "ask") as m_ask:
            r = self.client.get("/api/ai-interpret/abc123")
        self.assertEqual(503, r.status_code)
        body = r.get_json()
        self.assertEqual("paid_llm_disabled", body["code"])
        self.assertTrue(body["paid"])
        self.assertFalse(body["enabled"])
        m_ask.assert_not_called()

    def test_calls_model_when_gate_open(self):
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("/usr/bin/model-cli", "")), \
             mock.patch.object(tax_server, "fetch_detail",
                               return_value={"title": "增值税法"}), \
             mock.patch.object(tax_server, "_download_and_extract",
                               return_value=["第一章 总则", "第一条 内容"]), \
             mock.patch.object(tax_server.tax_llm, "ask",
                               return_value="解读正文") as m_ask:
            r = self.client.get("/api/ai-interpret/abc123?keyword=税率")
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual("解读正文", body["interpretation"])
        self.assertIn("model-cli", body["model"])
        self.assertEqual("增值税法", body["law_title"])
        m_ask.assert_called_once()

    def test_quota_exhausted_is_not_retried(self):
        """额度用完不重试：在没钱的账号上每重试一次都可能继续计费。"""
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("/usr/bin/model-cli", "")), \
             mock.patch.object(tax_server, "fetch_detail",
                               return_value={"title": "增值税法"}), \
             mock.patch.object(tax_server, "_download_and_extract",
                               return_value=["第一条 内容"]), \
             mock.patch.object(tax_server.tax_llm, "ask",
                               side_effect=tax_server.tax_llm.QuotaExhausted("额度用完")) as m_ask:
            r = self.client.get("/api/ai-interpret/abc123")
        self.assertEqual(503, r.status_code)
        self.assertEqual("quota_exhausted", r.get_json()["code"])
        self.assertEqual(1, m_ask.call_count)

    def test_model_failure_is_502(self):
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("/usr/bin/model-cli", "")), \
             mock.patch.object(tax_server, "fetch_detail",
                               return_value={"title": "增值税法"}), \
             mock.patch.object(tax_server, "_download_and_extract",
                               return_value=["第一条 内容"]), \
             mock.patch.object(tax_server.tax_llm, "ask",
                               side_effect=RuntimeError("CLI 挂了")):
            r = self.client.get("/api/ai-interpret/abc123")
        self.assertEqual(502, r.status_code)
        self.assertEqual("model_error", r.get_json()["code"])

    def test_500_when_no_article_extracted(self):
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("/usr/bin/model-cli", "")), \
             mock.patch.object(tax_server, "fetch_detail",
                               return_value={"title": "增值税法"}), \
             mock.patch.object(tax_server, "_download_and_extract",
                               return_value=["没有条号的段落"]):
            r = self.client.get("/api/ai-interpret/abc123")
        self.assertEqual(500, r.status_code)
        self.assertIn("Could not extract", r.get_json()["error"])

    def test_404_when_law_missing(self):
        with mock.patch.object(tax_server.tax_llm, "channel",
                               return_value=("/usr/bin/model-cli", "")), \
             mock.patch.object(tax_server, "fetch_detail", return_value={}):
            r = self.client.get("/api/ai-interpret/abc123")
        self.assertEqual(404, r.status_code)


class TestInterpretationsRoute(_RouteCase):
    def test_passes_title_keyword_and_province(self):
        with mock.patch.object(tax_server, "fetch_detail",
                               return_value={"title": "增值税法"}), \
             mock.patch.object(tax_server, "search_interpretations",
                               return_value={"total": 0, "sources": []}) as m:
            r = self.client.get("/api/interpretations/abc123?keyword=税率&province=广东")
        self.assertEqual(200, r.status_code)
        self.assertEqual("增值税法", m.call_args[0][0])
        self.assertEqual("广东", m.call_args.kwargs["province"])
        self.assertEqual("abc123", r.get_json()["law_id"])

    def test_404_when_law_missing(self):
        with mock.patch.object(tax_server, "fetch_detail", return_value={}):
            r = self.client.get("/api/interpretations/abc123")
        self.assertEqual(404, r.status_code)
        self.assertEqual("Law not found", r.get_json()["error"])


class TestWebRelatedRoute(_RouteCase):
    def _patches(self, practice, web, web_error=""):
        return (
            mock.patch.object(tax_server, "fetch_detail",
                              return_value={"title": "中华人民共和国增值税法"}),
            mock.patch.object(tax_server, "_search_practice_sources",
                              return_value=practice),
            mock.patch.object(tax_server, "_search_web_broad",
                              return_value=(web, web_error)),
        )

    def test_merges_and_dedups_by_url(self):
        practice = [{"url": "https://a.test/1", "title": "解读A"}]
        web = [{"url": "https://a.test/1", "title": "重复A"},
               {"url": "https://b.test/2", "title": "解读B"}]
        p1, p2, p3 = self._patches(practice, web)
        with p1 as _, p2 as m_practice, p3:
            r = self.client.get("/api/web-related/abc123?keyword=税率")
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual(2, body["total"])
        self.assertEqual(["https://a.test/1", "https://b.test/2"],
                         [s["url"] for s in body["sources"]])
        # 检索词要去掉"中华人民共和国"这种前缀，否则搜不到实务文章
        self.assertEqual("增值税法 税率", m_practice.call_args[0][0])

    def test_surfaces_engine_error(self):
        """全网那一路被拦时必须说明，不然空列表会被读成"网上没有解读"。"""
        p1, p2, p3 = self._patches([], [], web_error="360 被拦截")
        with p1, p2, p3:
            r = self.client.get("/api/web-related/abc123")
        body = r.get_json()
        self.assertEqual(0, body["total"])
        self.assertEqual("360 被拦截", body["engine_error"])

    def test_drops_items_without_url(self):
        p1, p2, p3 = self._patches([{"title": "没有链接"}],
                                   [{"url": "https://b.test/2", "title": "B"}])
        with p1, p2, p3:
            body = self.client.get("/api/web-related/abc123").get_json()
        self.assertEqual(1, body["total"])

    def test_404_when_law_missing(self):
        with mock.patch.object(tax_server, "fetch_detail", return_value={}):
            r = self.client.get("/api/web-related/abc123")
        self.assertEqual(404, r.status_code)

    # ── 打桩在引擎入口的那两条：桩形不能由被测函数自己决定 ──

    def _web_only(self, n):
        """让全网这一路真的经过 _search_web_broad，只在 so360_search 处打桩。"""
        found = {"results": [{"title": "企业重组所得税处理的实务解读第%d篇" % i,
                              "url": "https://shui5.cn/a/%d.html" % i,
                              "snippet": "摘要"} for i in range(n)]}
        return (
            mock.patch.object(tax_server, "so360_search",
                              lambda query, site="", size=10: found),
            mock.patch.object(tax_server, "fetch_detail", return_value={"title": "增值税法"}),
            mock.patch.object(tax_server, "_search_practice_sources", return_value=[]),
        )

    def test_broad_web_survives_to_the_payload(self):
        """取回几条就该回几条：整批丢掉不得发生，丢了也不能照样报 200。

        上面几条用例把 `_search_web_broad` 打桩成 (结果, 说明) 二元组；若它真实
        返回的是裸列表，调用方按二元组解包就会错位。桩形与被测函数自己的返回
        形态不一致时，这一路在测试里永远绿——所以这一条从引擎入口打进去。
        """
        p1, p2, p3 = self._web_only(3)
        with p1, p2, p3:
            body = self.client.get("/api/web-related/abc123").get_json()
        self.assertEqual(3, body["total"])
        self.assertEqual(["https://shui5.cn/a/0.html", "https://shui5.cn/a/1.html",
                          "https://shui5.cn/a/2.html"], [s["url"] for s in body["sources"]])

    def test_two_web_results_are_not_a_server_error(self):
        """恰好 2 条是解包错位的显形处：两个名字各接住一个 dict，回 500。

        错误形态：`{"error": "'str' object has no attribute 'get'"}`。
        """
        p1, p2, p3 = self._web_only(2)
        with p1, p2, p3:
            r = self.client.get("/api/web-related/abc123")
        self.assertEqual(200, r.status_code)
        self.assertEqual(2, r.get_json()["total"])


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
