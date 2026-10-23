#!/usr/bin/env python3
"""Flask 服务端路由用例。全部打桩上游，不联网。

tax_server.py 是前端唯一的后端，25 个路由注册（`/api/calc` 的 GET 与 POST 各一条）
需要接口级测试钉住契约——
只靠联网 e2e 间接碰到它时，e2e 失败说不清是"路由写错了"还是
"对方限流了"。这一组把路由自己的契约钉住：

  - 状态码与错误码（400 / 404 / 500 / 503）
  - 参数怎么传给了下层（关键词换成本体法名、province 只回填不过滤）
  - 分支路由（npc / chinatax / fgk / aggregated）
  - 付费闸门关着时不许碰到模型
  - 上游报错要如实透出，不能吞成空结果

不测的东西：下层的检索质量、真实站点的可用性——那些属于联网 e2e 与评测集。
"""

import re
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

# 我国现行十八个税种，按单行法律／暂行条例数出来的，不是从词表里抄的——
# 这一份写在用例里就是为了让它与词表对不上时报红。
STATUTORY_TAX_TYPES = (
    "增值税", "消费税", "关税", "车辆购置税", "企业所得税", "个人所得税",
    "房产税", "车船税", "土地增值税", "城镇土地使用税", "契税", "耕地占用税",
    "烟叶税", "资源税", "城市维护建设税", "环境保护税", "印花税", "船舶吨税",
)


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

    def test_quick_tax_types_are_exactly_the_taxonomy_keys(self):
        """首页税种栏＝归类词表的键，一一对应、不许多也不许少。

        改前这一份是手写的 12 项（含"发票管理""税收征管"两枚不属于归类词表的项），
        而词表有 33 项：全球最低税、税收协定、转让定价这些在命令行和自动换源里都
        走得通，界面上却没有入口，看页面的人以为引擎只认那 12 个。
        键名同时就是发出去的检索词——归类由 resolve_tax_type 判，界面不替它预判，
        所以这里不许出现第三个字段（旧 icon 字段就是第三份要人维护的清单）。
        """
        r = self.client.get("/api/quick-tax-types")
        self.assertEqual(200, r.status_code)
        items = r.get_json()
        self.assertEqual([{"label": t, "keyword": t}
                          for t in tax_server.TAX_TYPE_KEYWORDS], items)

    def test_every_quick_tax_keyword_routes_to_itself(self):
        """这一栏点下去发的是键名，归类必须回到同一个专题。

        归不出时 api_search 按原话字面检索并挂上"未能归类"，界面看起来像查过了：
        2026-10-20 实测「税收争议救济」就是这样——它的六个别名（行政复议、行政
        诉讼、纳税争议、起诉期限、复议前置，加键名本身）原本都不含键名这串字，
        键名反被自己漏掉。修之前 33 个键里 32 个自归，只有它归不出。
        """
        for t in tax_server.TAX_TYPE_KEYWORDS:
            with self.subTest(tax_type=t):
                got = tax_server.resolve_tax_type(t)
                self.assertIsNotNone(got, "%s 归不出任何专题" % t)
                self.assertEqual(t, got["type"])

    def test_mutation_dropping_the_key_name_alias_is_caught(self):
        """上一条款不是空转：把每个键名从自己的别名里去掉，自归那条就报红。

        注入的是 tax_search 模块里的那张表，不落盘。口径（2026-10-20 实测）：
        33 个键逐一走 resolve_tax_type(键名)，看返回的 type 是否仍是键名自己。
        注入后 23 个不再自归，「税收争议救济」是其中之一——它是这 23 个里唯一
        在修复前就已经失归的那个，其余 22 个靠这次注入才现形：键名本身当别名用，
        33 个键全都如此，删掉就露出别的别名压根接不住键名这件事。
        所以这一条报的点是"上一条款读的是别名表，不是界面自己拼的字符串"。
        """
        import tax_search
        alias = "税收争议救济"
        self.assertIn(alias, tax_search.TAX_TYPE_KEYWORDS[alias]["aliases"])
        stripped = {k: dict(v, aliases=[a for a in v["aliases"] if a != k])
                    for k, v in tax_search.TAX_TYPE_KEYWORDS.items()}
        with mock.patch.object(tax_search, "TAX_TYPE_KEYWORDS", stripped):
            off = [k for k in stripped
                   if (tax_search.resolve_tax_type(k) or {}).get("type") != k]
        self.assertIn(alias, off, "注入没打到这一项，上一条款可能是空转")
        self.assertEqual(23, len(off), "自归依赖键名别名的键数变了，口径要重测")

    def test_default_row_covers_all_statutory_tax_types(self):
        """界面默认铺出的那一行必须含全部十八个税种，切点不许落在税种段中间。

        两处各管一半，缺一半都会让某个税种点不到：
        - 词表顺序：接口按 `TAX_TYPE_KEYWORDS` 的键序渲按钮，税种要排在专题之前。
          2026-10-20 之前「烟叶税」挂在词表末尾一个名为「从业/凭证类」的段下——
          那个段名既不是税种也不是专题，它是 18 个税种里唯一放错段的一个。
        - 前端 `QUICK_TAX_SHOWN`：决定铺开几枚、其余收进「展开」。以前写 12，
          切在税种段中间，车辆购置税、印花税、城市维护建设税、烟叶税、资源税、
          环境保护税六个税种默认点不到；用户把「点不到」读成「引擎不认这个税种」，
          而命令行与自动换源明明走得通。

        口径：取词表前 QUICK_TAX_SHOWN 枚，与 STATUTORY_TAX_TYPES 整份比集合——
        数少了会漏（报红），数多了会把专题也拉进这一段（同样报红，因为前 N 项
        不再全是税种）。税种那一份清单按现行单行法律／暂行条例数出来写死在本文件
        顶部，不从词表反推，所以词表自己漏收一个税种也会被这条查到。
        """
        html = (Path(__file__).resolve().parent.parent
                / "frontend" / "index.html").read_text(encoding="utf-8")
        m = re.search(r"const QUICK_TAX_SHOWN\s*=\s*(\d+)\s*;", html)
        self.assertIsNotNone(m, "index.html 里找不到 const QUICK_TAX_SHOWN = <整数>;")
        shown = int(m.group(1))
        keys = list(tax_server.TAX_TYPE_KEYWORDS)
        self.assertEqual(set(STATUTORY_TAX_TYPES), set(keys[:shown]),
                         f"QUICK_TAX_SHOWN={shown} 铺出的那一段与十八个税种对不上："
                         f"差集 {set(STATUTORY_TAX_TYPES) ^ set(keys[:shown])}")

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
        """只有空格的关键词也要当成没给，否则会拿空串去打六源。"""
        with mock.patch.object(tax_server, "search_tax") as m:
            r = self.client.post("/api/search", json={"keyword": "   "})
        self.assertEqual(400, r.status_code)
        m.assert_not_called()

    def test_rejects_source_outside_the_ui_domain(self):
        """「数据源」发来自界面取值域之外的值必须 400，不许当成 NPC 悄悄查完。

        以前 ctax／shui5／wechat／sogou 这些取数层的源名、以及 aggregate 这种
        少个 d 的拼法，都会落进 api_search 的 else 分支按 NPC 检索并回 200：
        调用方以为结果是自己点的那一源出来的。2026-10-20 只读复现：
        source=ctax 与 source=aggregate 都回 200、被调的是 search_tax。
        不给 source 才是默认 NPC，下一条款意钉住这一区分。
        """
        for bad in ("ctax", "shui5", "wechat", "sogou", "aggregate", "chinatax ",
                    "", "NPC", None):
            with self.subTest(source=bad):
                with mock.patch.object(tax_server, "search_tax") as npc, \
                     mock.patch.object(tax_server, "aggregate_search") as agg, \
                     mock.patch.object(tax_server, "search_chinatax") as s5, \
                     mock.patch.object(tax_server, "search_fgk") as fgk:
                    r = self._post({"keyword": "增值税", "source": bad})
                self.assertEqual(400, r.status_code)
                self.assertIn("source", r.get_json()["error"])
                npc.assert_not_called()
                agg.assert_not_called()
                s5.assert_not_called()
                fgk.assert_not_called()

    def test_missing_source_still_defaults_to_npc(self):
        """缺省才是默认 NPC；显式给错值走上一条的 400。"""
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            r = self._post({"keyword": "增值税"})
        self.assertEqual(200, r.status_code)
        m.assert_called_once()

    def test_mutation_widening_the_source_domain_is_caught(self):
        """上一条款是真的在读 SEARCH_SOURCES，不是碰巧回 400：把取值域加宽就放行。

        注入的是内存里的常量，不落盘。加宽之后 "ctax" 落进 else 分支按 NPC 查完
        回 200——那正是修复前的行为，也是 test_rejects_source_outside_the_ui_domain
        要报红的样子。
        """
        bad = "ctax"
        self.assertNotIn(bad, tax_server.SEARCH_SOURCES)
        with mock.patch.object(tax_server, "SEARCH_SOURCES",
                               tax_server.SEARCH_SOURCES + (bad,)), \
             mock.patch.object(tax_server, "search_tax", return_value={}) as npc:
            r = self._post({"keyword": "增值税", "source": bad}, tax_type=None)
        self.assertEqual(200, r.status_code)
        npc.assert_called_once()
        self.assertEqual(("npc", "chinatax", "fgk", "aggregated"),
                         tax_server.SEARCH_SOURCES)

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

    def test_chinatax_maps_ui_filters_into_the_request(self):
        """界面选了 精确＋范围＋日期，chinatax 这路必须把它们带进 filters。

        这三个控件只喂给 NPC 时，数据源切到总局就被静默丢掉。日期补成带时间戳
        的 cwrq 是 build_filters 内部的事，这里只验维度到了调用方。
        变异验证：把 api_search 里的 filters= 去掉，这一条立刻报红。
        """
        with mock.patch.object(tax_server, "search_chinatax", return_value={}) as m:
            self._post({"keyword": "增值税", "source": "chinatax", "exact": True,
                        "scope": "title", "date_from": "2024-01-01",
                        "date_to": "2024-12-31"}, tax_type=None)
        f = m.call_args.kwargs["filters"]
        self.assertEqual("1", f["wordPlace"])
        self.assertEqual("5", f["participleRule"])
        self.assertTrue(f["cwrqStart"].startswith("2024-01-01"))
        self.assertTrue(f["cwrqEnd"].startswith("2024-12-31"))

    def test_fgk_fulltext_does_not_narrow_to_title(self):
        """范围选「全文」时不该发 wordPlace，否则等于替用户把结果收窄到标题。"""
        with mock.patch.object(tax_server, "search_fgk", return_value={}) as m:
            self._post({"keyword": "增值税", "source": "fgk", "scope": "fulltext"},
                       tax_type=None)
        self.assertNotIn("wordPlace", m.call_args.kwargs["filters"])

    def test_bad_date_on_chinatax_is_400_not_silent_zero(self):
        """手改请求体塞非法日期时，接口自己不为这一枚报错，这一层必须挡成 400。"""
        with mock.patch.object(tax_server, "search_chinatax") as m:
            r = self._post({"keyword": "增值税", "source": "chinatax",
                            "date_from": "2024-13-01"}, tax_type=None)
        self.assertEqual(400, r.status_code)
        m.assert_not_called()

    def test_filter_note_reaches_the_frontend_payload(self):
        """收窄到 0 条那句成因必须原样出现在 result 里，界面才有话可显示。"""
        fake = {"total": 0, "results": [], "_filter_note": "叠加收窄维度后命中 0 条"}
        with mock.patch.object(tax_server, "search_chinatax", return_value=fake):
            r = self._post({"keyword": "增值税", "source": "chinatax"}, tax_type=None)
        self.assertIn("_filter_note", r.get_json()["result"])

    def test_sta_topic_routed_to_fgk_carries_ui_filters(self):
        """sta 专题在 NPC 源下被自动换到法规库时，界面的日期/范围/排序也得跟过去。

        这条和 source=fgk 同源：一旦漏传，用户设的日期在换源那一刻又被静默丢掉。
        """
        with mock.patch.object(tax_server, "search_fgk", return_value={}) as m:
            self._post({"keyword": "转让定价", "date_from": "2024-01-01",
                        "date_to": "2024-12-31", "sort": "date"}, tax_type=STA_TAX_TYPE)
        f = m.call_args.kwargs["filters"]
        self.assertTrue(f["cwrqStart"].startswith("2024-01-01"))
        self.assertEqual("date_desc", m.call_args.kwargs["order"])

    def test_bad_aging_on_chinatax_is_400_not_silent_zero(self):
        """「时效」在 search5 那两源走的是文本值域，域外值必须挡成 400。

        用例故意发 NPC 那侧的"现行有效"：search5 认的是"全文有效"，两套值域不通用。
        发错时接口不发错误码，回的是 `total=0`——与"这一维真的没有内容"是同一个形态
        （单发读数见 subskills/chinatax/NOTE.md 第三节）。界面只会显示 0 条，看不出
        是值域写错还是库里就这么多，所以原因必须在 400 这一层报出来。
        """
        with mock.patch.object(tax_server, "search_chinatax") as m:
            r = self._post({"keyword": "增值税", "source": "chinatax",
                            "aging": "现行有效"}, tax_type=None)
        self.assertEqual(400, r.status_code)
        m.assert_not_called()

    def test_ui_aging_reaches_xxgk_aging_and_blank_sends_nothing(self):
        """界面选「全文有效」要进到 filters.xxgkAging；没选时这一维必须不发。

        不发是默认行为的关键：实测 增值税×文件类标签 1908 条里 1165 条该栏为空，
        默认发任何一个时效值都会把那批现行文件一起筛掉（见 tax_server._ui_filters）。
        """
        with mock.patch.object(tax_server, "search_chinatax", return_value={}) as m:
            self._post({"keyword": "增值税", "source": "chinatax",
                        "aging": "全文有效"}, tax_type=None)
        self.assertEqual("全文有效", m.call_args.kwargs["filters"]["xxgkAging"])
        with mock.patch.object(tax_server, "search_chinatax", return_value={}) as m2:
            self._post({"keyword": "增值税", "source": "chinatax", "aging": ""},
                       tax_type=None)
        self.assertNotIn("xxgkAging", m2.call_args.kwargs["filters"])

    def test_sort_from_ui_reaches_order_on_both_search5_paths(self):
        """界面排序「日期↓」必须翻成 order=date_desc（发出的是 orderBy=1）。

        变异验证：把 SORT_TO_ORDER.get(...) 那两处改回不传 order，本条报红——
        不传就是走 search_chinatax 的默认 relevance，用户点的日期↓ 又一次静默空转。
        """
        for source, fn in (("chinatax", "search_chinatax"), ("fgk", "search_fgk")):
            with mock.patch.object(tax_server, fn, return_value={}) as m:
                self._post({"keyword": "增值税", "source": source, "sort": "date"},
                           tax_type=None)
            self.assertEqual("date_desc", m.call_args.kwargs["order"], source)
        with mock.patch.object(tax_server, "search_chinatax", return_value={}) as m2:
            self._post({"keyword": "增值税", "source": "chinatax"}, tax_type=None)
        self.assertEqual("relevance", m2.call_args.kwargs["order"],
                         "不给排序时该留在相关度，别默认改成日期")

    def test_fetch_failed_flag_survives_into_payload(self):
        """上游取数失败带的 _fetch_failed 必须原样到 payload，界面才分得清失败与空。"""
        fake = {"total": 0, "results": [], "_error": "HTTP 500", "_fetch_failed": True}
        with mock.patch.object(tax_server, "search_chinatax", return_value=fake):
            r = self._post({"keyword": "增值税", "source": "chinatax"}, tax_type=None)
        body = r.get_json()["result"]
        self.assertTrue(body.get("_fetch_failed"))

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

    def test_single_source_items_are_stamped_with_the_layer_that_fetched(self):
        """三路单源取回的条目逐条带上 _source，界面才有出处可标。

        卡面的来源角标与日期那一栏都按 _source 取词。改前单源那三路不写这一枚
        （只有聚合写），于是 数据源=税务总局 的一屏卡片既没有角标，日期又被
        按 NPC 那一层的公布日期来念——而 search5 给的是成文日期（cwrq），
        同一份文件上这两个日期可以差好几个月。
        """
        for source, fn in (("npc", "search_tax"),
                           ("chinatax", "search_chinatax"),
                           ("fgk", "search_fgk")):
            with self.subTest(source=source):
                with mock.patch.object(tax_server, fn,
                                       return_value={"total": 2, "results": [
                                           {"title": "a"}, {"title": "b"}]}) as m:
                    r = self._post({"keyword": "增值税", "source": source},
                                   tax_type=None)
                got = [it.get("_source") for it in r.get_json()["result"]["results"]]
                self.assertEqual([source, source], got)
                m.assert_called_once()

    def test_sta_reroute_under_npc_source_stamps_fgk(self):
        """NPC 源下的 sta 专题被自动换到法规库，条目要标成实际那一层。

        这条与筛选透传那条（test_sta_topic_routed_to_fgk_carries_ui_filters）
        查的是同一次换源的两个面：参数要跟过去，出处也要跟着改。标成 npc 就
        成了"角标写 NPC法规库、日期按公布念"，而取回的是法规库的成文日期。
        """
        fake = {"total": 1, "results": [{"title": "a"}]}
        with mock.patch.object(tax_server, "search_fgk", return_value=fake), \
             mock.patch.object(tax_server, "search_tax") as npc:
            r = self._post({"keyword": "转让定价"}, tax_type=STA_TAX_TYPE)
        self.assertEqual("fgk", r.get_json()["result"]["results"][0]["_source"])
        npc.assert_not_called()

    def test_overseas_reroute_stamps_chinatax_on_every_ui_source(self):
        """境外专题在 NPC／总局／法规库三个入口下都换到全站层，标注跟着换。

        三个入口都会去打 search_chinatax（file_only=False），所以三条都必须标
        chinatax；把 search_tax 与 search_fgk 一起打桩是为了万一漏换，用例直接
        打到真站点就会在被 stub 的那枚上暴露。
        """
        overseas = {"type": "全球最低税", "aliases": ["支柱二"],
                    "authority": "overseas", "search_term": "支柱二"}
        for source in ("npc", "chinatax", "fgk"):
            with self.subTest(source=source):
                with mock.patch.object(tax_server, "search_chinatax",
                                       return_value={"total": 1, "results": [{"title": "a"}]}) as s5, \
                     mock.patch.object(tax_server, "search_tax") as npc, \
                     mock.patch.object(tax_server, "search_fgk") as fgk:
                    r = self._post({"keyword": "支柱二对跨国企业的影响",
                                    "source": source}, tax_type=overseas)
                self.assertEqual(
                    "chinatax", r.get_json()["result"]["results"][0]["_source"])
                s5.assert_called_once()
                npc.assert_not_called()
                fgk.assert_not_called()

    def test_aggregated_keeps_the_aggregators_own_stamp(self):
        """聚合那一路的 _source 由 tax_aggregator 逐条写，这一层不许覆写。

        覆写会把六源一起标成 aggregated——界面按源渲的"每源几条"与角标就成了
        一句自相矛盾的话（计数行按 shui5 算，角标却写 aggregated）。
        """
        fake = {"total": 1, "results": [{"title": "a", "_source": "shui5"}]}
        with mock.patch.object(tax_server, "aggregate_search", return_value=fake):
            r = self._post({"keyword": "增值税", "source": "aggregated"},
                           tax_type=None)
        self.assertEqual("shui5", r.get_json()["result"]["results"][0]["_source"])

    def test_province_is_echoed_but_not_used(self):
        """province 只回填。

        NPC 库是全国性法规，没有省级维度；前端"省份"下拉对搜索结果不起
        过滤作用。这里钉住"只回填"，免得日后误以为它生效了。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            r = self._post({"keyword": "增值税", "province": "广东"}, tax_type=None)
        self.assertEqual("广东", r.get_json()["province"])
        self.assertNotIn("广东", str(m.call_args))

    def test_size_and_page_domains(self):
        """size／page 的发值取值域闸：坏值一律 400，且一个源都不许碰。

        改前这一行是 `size = min(data.get("size", 20), 50)`。三种坏值实测（2026-10-20
        本机 5080）：发 "20" 之外的非数字串与 999 分别在比较运算与静默截断上出问题，
        前者回 HTML 的 500、调用方看不出是自己带错了字段；发 0 时上游照样回 200 且
        清单为空，界面读成"库里没有这份文件"。page 改前从不读，发什么都不报。
        数字串按命令行 `--size 20` 的同一口径收下（argparse 的 type=int 也收 "20"）。
        """
        for bad in ({"size": 999}, {"size": 0}, {"size": -1}, {"size": "x"},
                    {"size": True}, {"size": 2.5}, {"page": 0}, {"page": "x"},
                    {"page": -3}):
            with self.subTest(**bad), \
                    mock.patch.object(tax_server, "search_tax") as m:
                r = self._post(dict(keyword="增值税", **bad), tax_type=None)
            self.assertEqual(400, r.status_code)
            self.assertIn("筛选参数不合法", r.get_json()["error"])
            m.assert_not_called()

    def test_size_upper_bound_is_the_npc_layers_own(self):
        """上界取 tax_search.search_tax 往下发的 min(size, 100)。

        发比它大的值时 NPC 自己截成 100，界面上「本轮条数」写的是一个数、实际取回
        的是另一个数。宁可在闸上就把上界说明白，不做静默截断。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            self._post({"keyword": "增值税", "size": 100}, tax_type=None)
        self.assertEqual(100, m.call_args.kwargs["size"])
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            r = self._post({"keyword": "增值税", "size": "20"}, tax_type=None)
        self.assertEqual(200, r.status_code)
        self.assertEqual(20, m.call_args.kwargs["size"])

    def test_page_reaches_only_the_layers_that_have_that_dimension(self):
        """page 下推给 NPC 与税务总局两路；法规库与聚合两路接到说明而不是静默停在第一轮。

        取数层的 page 是可用并已实测的（2026-10-20）：tax_search.search_tax「增值税」
        标题·模糊·现行有效 total=45，page=1 与 page=2 各取 5 条、标题交集 1 条；
        tax_web_search.search_chinatax 同词两页交集 0 条。改前 api_search 从不读请求体
        里的 page，界面永远停在第一轮取回的那一批，元信息那一栏却只写"📋 45条"。
        """
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            r = self._post({"keyword": "增值税", "page": 3, "size": 50}, tax_type=None)
        self.assertEqual(3, m.call_args.kwargs["page"])
        self.assertEqual(50, m.call_args.kwargs["size"])
        self.assertEqual(3, r.get_json()["page"])
        self.assertEqual(50, r.get_json()["size"])
        with mock.patch.object(tax_server, "search_chinatax",
                               return_value={}) as m:
            self._post({"keyword": "增值税", "source": "chinatax", "page": 2},
                       tax_type=None)
        self.assertEqual(2, m.call_args.kwargs["page"])
        for src, patch_name, attr in (("fgk", "search_fgk", "search_fgk"),
                                      ("aggregated", "aggregate_search",
                                       "aggregate_search")):
            with self.subTest(src=src), mock.patch.object(
                    tax_server, attr, return_value={}) as m:
                r = self._post({"keyword": "增值税", "source": src, "page": 2},
                               tax_type=None)
            self.assertNotIn("page", m.call_args.kwargs)
            self.assertIn("第 2 页没有生效", r.get_json()["result"]["_page_note"])

    def test_page_note_follows_the_layer_actually_called(self):
        """点的是 NPC，命中却被自动换到法规库时，页码说明要跟着那一层走。

        这条是"由后端判定而不是让前端看数据源"的根据：前端按 filterSource 只能知道
        用户点了 NPC，不知道这一批条目来自哪一层。
        """
        sta = {"type": "税收优惠", "aliases": [], "authority": "sta",
               "search_term": "税收优惠"}
        with mock.patch.object(tax_server, "search_fgk", return_value={}) as m:
            r = self._post({"keyword": "税收优惠", "page": 2}, tax_type=sta)
        self.assertNotIn("page", m.call_args.kwargs)
        self.assertIn("第 2 页没有生效", r.get_json()["result"]["_page_note"])

    def test_page_control_domain_matches_the_branches_that_forward_it(self):
        """前端那张"哪些源能翻页"的名单，逐源对着后端真实行为核一遍。

        PAGE_SOURCES 是界面上第二份按源分键的源名单：它决定页码按钮在哪几个数据源上
        启用，而后端认的是真正调了哪一层（点的是 NPC、条目却被归类换到法规库时不转）。
        两份各改各的就会一边按钮亮着、一边页码被丢掉，或反过来明明能翻却按不动。
        判据不写在人眼里：把四条分支各发一次 page=2，看哪一层的调用真收到了 2。
        """
        html_path = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        m = re.search(r"const PAGE_SOURCES=\[([^\]]*)\]", html)
        self.assertTrue(m, "前端没有 PAGE_SOURCES 这张名单")
        claimed = sorted(re.findall(r"'([^']+)'", m.group(1)))
        forwarded = []
        for src in tax_server.SEARCH_SOURCES:
            with mock.patch.object(tax_server, "search_tax", return_value={}) as a, \
                    mock.patch.object(tax_server, "search_chinatax",
                                      return_value={}) as b, \
                    mock.patch.object(tax_server, "search_fgk",
                                      return_value={}) as c, \
                    mock.patch.object(tax_server, "aggregate_search",
                                      return_value={}) as d:
                r = self._post({"keyword": "增值税", "source": src, "page": 2},
                               tax_type=None)
                got = [x for x in (a, b, c, d)
                       if x.call_args and x.call_args.kwargs.get("page") == 2]
            # 最多一层收到页码：两层同时收到就说明这一路发重了
            self.assertLessEqual(len(got), 1, f"{src} 这一路有多层收到 page")
            if got:
                forwarded.append(src)
            else:
                self.assertIn("第 2 页没有生效",
                              r.get_json()["result"]["_page_note"])
        self.assertEqual(claimed, sorted(forwarded))

    def test_count_note_separates_library_total_from_items_given(self):
        """条数那一栏分两个口径，整句由后端给。

        改前前端读的是 `result.total || result.total_items || items.length`，一个数字
        同时被两件事用：单源那三路是库内命中总数（实测「增值税」NPC 45、税务总局 1910），
        卡面只有本轮取回那 20 张；聚合那一路 total_items 是六源去重后收全的条数，
        而 tax_aggregator 往下只给 items[:size*3]。
        """
        fake = {"total": 45, "results": [{"title": "x%d" % i} for i in range(20)]}
        with mock.patch.object(tax_server, "search_tax", return_value=fake):
            r = self._post({"keyword": "增值税"}, tax_type=None)
        self.assertEqual("库内命中 45 条 · 本轮给出 20 条",
                         r.get_json()["result"]["_count_note"])
        fake = {"total": 3, "results": [{"title": "x%d" % i} for i in range(3)]}
        with mock.patch.object(tax_server, "search_tax", return_value=fake):
            r = self._post({"keyword": "增值税"}, tax_type=None)
        self.assertEqual("库内命中 3 条（已全部给出）",
                         r.get_json()["result"]["_count_note"])
        fake = {"total_items": 120, "items": [{"title": "x%d" % i} for i in range(60)]}
        with mock.patch.object(tax_server, "aggregate_search", return_value=fake):
            r = self._post({"keyword": "增值税", "source": "aggregated"}, tax_type=None)
        self.assertEqual("六源共收 120 条 · 本轮给出 60 条",
                         r.get_json()["result"]["_count_note"])

    def test_exact_control_reaches_the_aggregator(self):
        """界面上的「匹配」在聚合那一路也要传下去，不被本体法那条规则顶掉。

        改前这传的是 `exact=bool(parent_law)`，请求体里的 exact 从不读：归不出本体法时
        用户点"精确"是空转。本体法那一路仍强制精确（tax_aggregator 里 exact 只落在
        NPC 那一条 future 上，模糊按发布时间排会把本体法顶出首位），两条规则是相加
        不是替换。
        """
        with mock.patch.object(tax_server, "aggregate_search", return_value={}) as m:
            self._post({"keyword": "研发费用", "source": "aggregated", "exact": True},
                       tax_type=None)
        self.assertTrue(m.call_args.kwargs["exact"])
        with mock.patch.object(tax_server, "aggregate_search", return_value={}) as m:
            self._post({"keyword": "研发费用", "source": "aggregated"}, tax_type=None)
        self.assertFalse(m.call_args.kwargs["exact"])
        # 本体法那一路仍强制精确，与用户这一栏选什么无关
        law = {"type": "增值税", "aliases": [], "authority": "npc",
               "parent_law": "增值税法"}
        with mock.patch.object(tax_server, "aggregate_search", return_value={}) as m:
            self._post({"keyword": "增值税", "source": "aggregated", "exact": False},
                       tax_type=law)
        self.assertTrue(m.call_args.kwargs["exact"])

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
        """让全网这一路真的经过 _search_web_broad，只在 sogou_search 处打桩。"""
        found = {"results": [{"title": "企业重组所得税处理的实务解读第%d篇" % i,
                              "url": "https://shui5.cn/a/%d.html" % i,
                              "snippet": "摘要"} for i in range(n)]}
        return (
            mock.patch.object(tax_server.tax_related, "sogou_search",
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

    def test_shared_related_layer_merges_for_flask_and_adapters(self):
        """三路合并、去重、短标题与拦截说明都在 tax_related 这一层。

        Flask 与三个适配器共用它；只在 Flask 路由上测，适配器那一侧就没有
        同一份判据的用例。
        """
        calls = {}

        def fake_practice(query, n):
            calls["practice"] = (query, n)
            return [{"url": "https://a.test/1", "title": "解读A"}]

        def fake_web(query, n):
            calls["web"] = (query, n)
            return [{"url": "https://a.test/1", "title": "重复A"},
                    {"url": "https://b.test/2", "title": "解读B"}], "360 被拦截"

        body = tax_server.tax_related.collect_related(
            "中华人民共和国增值税法", "税率",
            practice_fetch=fake_practice, web_fetch=fake_web)
        self.assertEqual({"practice": ("增值税法 税率", 3),
                          "web": ("增值税法 税率", 8)}, calls)
        self.assertEqual(2, body["total"])
        self.assertEqual(["https://a.test/1", "https://b.test/2"],
                         [s["url"] for s in body["sources"]])
        self.assertEqual("360 被拦截", body["engine_error"])


class TestCalcRoutes(_RouteCase):
    """/api/calc 两路：清单整份由 tax_calc.SKELETONS 渲，求值把参数写法错误挡成 400。

    界面新增的「算式展开」面板是 tax_calc.py 的入口（引擎原先只在命令行里）。这一组
    钉三件事：清单不许在路由里手写第二份（手写那份会漏掉后来加进骨架的参数，界面上
    就成了"算出缺参数却没有地方能填"）；请求体里多给的参数名不许进求值命名空间；
    参数写法导致的报错一律 400 并带回引擎原话。全程离线，不联网、不碰模型闸门。
    """

    calc = tax_server.tax_calc

    def _post(self, payload):
        return self.client.post("/api/calc", json=payload)

    def test_get_lists_every_skeleton_from_the_engine(self):
        r = self.client.get("/api/calc")
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual(self.calc.DISCLAIMER, body["限制"])
        got = body["骨架"]
        self.assertEqual(list(self.calc.SKELETONS), [s["名称"] for s in got])
        for s in got:
            sk = self.calc.SKELETONS[s["名称"]]
            self.assertEqual({"名称", "公式", "参数", "结果"}, set(s))
            self.assertEqual(sk["公式"], s["公式"])
            self.assertEqual(sk["结果"], s["结果"])
            self.assertEqual(self.calc.inputs_spec(s["名称"]), s["参数"])

    def test_get_table_params_are_the_columns_the_engine_reads(self):
        """税率表那一枚的列名要从常量表渲出，且只有用累进函数那三条骨架有表。

        这一条管两件事：界面拿到的 列 就是 RATE_TABLE_COLUMNS（渲染不断链），
        以及"哪几条骨架有表"这份名单不悄悄变长或变短。列名与引擎真正读的那枚
        是不是同一枚，由下面两条从两个方向钉：一条拿引擎的报错原文回吐列名，
        一条拿声明的列名喂回引擎。
        """
        spec = {s["名称"]: [p for p in self.calc.inputs_spec(s["名称"])
                            if p["形态"] == "税率表"][0]["列"]
                for s in self.client.get("/api/calc").get_json()["骨架"]
                if any(p["形态"] == "税率表" for p in self.calc.inputs_spec(s["名称"]))}
        self.assertEqual({"超额累进", "综合所得年度汇算", "超率累进"}, set(spec))
        self.assertEqual(self.calc.RATE_TABLE_COLUMNS["分段累进"], spec["超额累进"])
        self.assertEqual(self.calc.RATE_TABLE_COLUMNS["分段累进"], spec["综合所得年度汇算"])
        self.assertEqual(self.calc.RATE_TABLE_COLUMNS["超率累进"], spec["超率累进"])

    def test_table_columns_are_the_keys_the_progressive_functions_read(self):
        """两个累进函数各读哪一枚速算扣除键，由它们的报错原文给出。"""
        with self.assertRaises(ValueError) as cm:
            self.calc.分段累进(100000, [{"上限": 36000, "税率": "0.03", "速算扣除数": "99999"},
                                       {"上限": None, "税率": "0.45", "速算扣除数": "15160"}])
        self.assertIn("速算扣除数", str(cm.exception))
        with self.assertRaises(ValueError) as cm2:
            self.calc.超率累进(600, 400, [{"上限": 1.0, "税率": "0.3", "速算扣除系数": "9.9"},
                                        {"上限": None, "税率": "0.6", "速算扣除系数": "0"}])
        self.assertIn("速算扣除系数", str(cm2.exception))

    def test_declared_columns_are_read_by_the_skeletons(self):
        """反方向再来一次：拿 /api/calc 声明的三枚列名排一张表，引擎报错时要报出它
        声明的那一枚。

        只写上面两条不够——spec 与 RATE_TABLE_COLUMNS 出自同一张常量表，把
        RATE_TABLE_COLUMNS["超率累进"] 的第三枚改成 "速算扣除数" 那两条都还是绿的
        （2026-10-20 在副本树里变异实测）。这一条把声明的列名当输入喂回引擎：
        列名与引擎真正读的那枚一分叉，row.get(ded_key, 0) 静默取 0，报错原文里
        出现的是引擎那枚而不是声明那枚，assertIn 就断。
        """
        numbers = {"超额累进": {"计税基数": 100000},
                   "综合所得年度汇算": {"收入总额": 200000, "费用减除": 60000,
                                        "专项扣除": 10000, "专项附加扣除": 0,
                                        "已预缴税额": 0},
                   "超率累进": {"转让收入": 1000, "扣除项目金额": 200}}
        for name, given in numbers.items():
            cols = [p["列"] for p in self.calc.inputs_spec(name)
                    if p["形态"] == "税率表"][0]
            # 两档，末档速算扣除故意给一个不可能的值，逼引擎走"不自洽"那条报错
            table = [{cols[0]: 36000, cols[1]: "0.03", cols[2]: "0"},
                     {cols[0]: None, cols[1]: "0.45", cols[2]: "99999"}]
            if name == "超率累进":
                # 这一路的档界是占扣除项目金额的倍数，速算那枚是系数
                table = [{cols[0]: 1, cols[1]: "0.3", cols[2]: "0.5"},
                         {cols[0]: None, cols[1]: "0.6", cols[2]: "999"}]
            with self.subTest(skeleton=name):
                with self.assertRaises(ValueError) as cm:
                    self.calc.run(name, dict(given, 税率表=table))
                self.assertIn(cols[2], str(cm.exception), name)

    def test_post_returns_the_engines_own_expansion(self):
        r = self._post({"骨架": "超额累进", "参数": {
            "计税基数": {"值": 100000, "来源": "http://x/1"},
            "税率表": {"值": [{"上限": 36000, "税率": 0.03, "速算扣除数": 0},
                              {"上限": 144000, "税率": 0.10, "速算扣除数": 2520},
                              {"上限": None, "税率": 0.45, "速算扣除数": 15160}],
                      "来源": "http://x/2"}}})
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual({"应纳税额"}, set(body["结果"]))
        self.assertEqual(7480.0, body["结果"]["应纳税额"])
        self.assertEqual(1, len(body["步骤"]))
        self.assertEqual("分段累进(计税基数, 税率表)", body["步骤"][0]["算式"])
        self.assertEqual(2, body["步骤"][0]["分档"]["适用档"])
        self.assertEqual(2, len(body["步骤"][0]["分档"]["逐档"]))
        # 两枚参数都带了来源，无来源名单该是空的
        self.assertEqual([], body["无来源参数"])
        self.assertEqual(self.calc.DISCLAIMER, body["限制"])

    def test_post_marks_a_param_without_source(self):
        r = self._post({"骨架": "从价计征",
                        "参数": {"计税依据": {"值": 100, "来源": "http://x"}, "税率": 0.04}})
        self.assertEqual(200, r.status_code)
        self.assertEqual(["税率"], r.get_json()["无来源参数"])

    def test_post_never_leaves_the_process(self):
        """这一路纯本地求值：把 socket 钉死也必须回 200。

        界面点「展开算式」不该产生任何对外请求，也不该碰付费闸门
        （/api/health 报的 paid_llm.enabled 与这一路无关）。
        """
        with mock.patch("socket.socket",
                        side_effect=AssertionError("算式展开不许联网")):
            r = self._post({"骨架": "从价计征", "参数": {"计税依据": 100, "税率": 0.04}})
        self.assertEqual(200, r.status_code)
        self.assertEqual(4.0, r.get_json()["结果"]["应纳税额"])

    def test_post_rejects_unknown_param_names(self):
        """多给的参数名必须 400，不能让它进求值命名空间。

        tax_calc.run 把调用方给的键并进 values（values.update(given)），名为 max 的
        键就把算式允许调用的 max 换成一个 Decimal，随后「应纳税额 = max(0, …)」报
        "'decimal.Decimal' object is not callable"——看着像引擎坏了，其实是请求里
        多带一个字段。2026-10-20 实测：其余四枚参数齐全 + {"max": 99} → TypeError。
        """
        good = {"不含税销售额": 1000, "适用税率": 0.13, "进项税额": 0,
                "进项税额转出": 0, "上期留抵税额": 0}
        for extra, value in (("max", 99), ("abs", 99), ("min", 99), ("税率表", [])):
            with self.subTest(extra=extra):
                r = self._post({"骨架": "增值税一般计税",
                                "参数": dict(good, **{extra: value})})
                self.assertEqual(400, r.status_code)
                self.assertIn(extra, r.get_json()["error"])
                self.assertEqual(tax_server.tax_calc.inputs("增值税一般计税"),
                                 r.get_json()["需要的参数"])

    def test_post_lists_every_missing_param_at_once(self):
        r = self._post({"骨架": "增值税一般计税",
                        "参数": {"不含税销售额": 1, "适用税率": 0.13}})
        self.assertEqual(400, r.status_code)
        body = r.get_json()
        self.assertEqual(["进项税额", "进项税额转出", "上期留抵税额"], body["缺的参数"])

    def test_post_rejects_a_skeleton_that_does_not_exist(self):
        """选错一项是请求问题，回 400 并给可选值；引擎这里抛的 KeyError 不能冒成 500。"""
        r = self._post({"骨架": "印花税计算", "参数": {}})
        self.assertEqual(400, r.status_code)
        self.assertEqual(sorted(tax_server.tax_calc.SKELETONS), r.get_json()["可选"])

    def test_post_rejects_a_body_that_is_not_a_param_object(self):
        for payload in ({"骨架": "从价计征", "参数": [1, 2]},
                        {"骨架": "从价计征", "参数": "计税依据=1"},
                        {"骨架": "从价计征"},
                        {}):
            with self.subTest(payload=payload):
                r = self._post(payload)
                self.assertEqual(400, r.status_code)

    def test_post_turns_engine_value_errors_into_400(self):
        """参数写法决定的七种坏值，各抛各的异常，都必须是 400 而不是 500。

        每种坏写法抛哪一类是 2026-10-20 逐条实测的：非数值与布尔与空表与末档封顶
        与不自洽走 ValueError，表行写成列表走 AttributeError（row 没有 .get），
        行里少给"税率"走 KeyError。并成一条是因为界面只关心一件事：能不能读回
        一句人话；needle 那一列就是各条报错原文里必须出现的那句话。
        """
        def body(table):
            return {"骨架": "超额累进", "参数": {"计税基数": 100000, "税率表": table}}
        cases = [
            ("非数值", {"骨架": "从价计征", "参数": {"计税依据": "abc", "税率": 0.04}},
             "不是可解析的数值"),
            ("布尔", {"骨架": "从价计征", "参数": {"计税依据": True, "税率": 0.04}},
             "参数值不是数值"),
            ("表行写成列表", body([[36000, 0.03, 0]]), "has no attribute 'get'"),
            ("空表", body([]), "至少一档的列表"),
            ("末档封顶", body([{"上限": 36000, "税率": 0.03}]), "最后一档必须不封顶"),
            ("档界与速算不自洽", body([
                {"上限": 36000, "税率": 0.03, "速算扣除数": 0},
                {"上限": 144000, "税率": 0.10, "速算扣除数": 99999},
                {"上限": None, "税率": 0.45, "速算扣除数": 15160}]),
             "税率表不自洽"),
            ("表行少给一列", body([{"上限": None}]), "税率表少给一列：税率"),
        ]
        for label, payload, needle in cases:
            with self.subTest(case=label):
                r = self._post(payload)
                self.assertEqual(400, r.status_code, label)
                self.assertIn(needle, r.get_json()["error"], label)

    def test_mutation_dropping_the_unknown_name_guard_is_caught(self):
        """这道闸买的是报错那句话，不是状态码——去掉它仍然 400，但那句话换了个人。

        2026-10-20 在副本树里把 `extra = [...]` 改成 `extra = []` 实测：这一发还是
        400，error 变成引擎原话 "'decimal.Decimal' object is not callable"。原因是
        路由的兜底分支本来就接 TypeError。所以盯这道闸的不是 test_post_rejects_…
        的状态码，是它有没有点名多带的那个字段。这里两件事一起断：run 自己抛
        TypeError（闸不在引擎里），而路由那句里出现 "max"、引擎那句里没有。
        """
        payload = {"不含税销售额": 1000, "适用税率": 0.13, "进项税额": 0,
                   "进项税额转出": 0, "上期留抵税额": 0, "max": 99}
        with self.assertRaises(TypeError) as cm:
            tax_server.tax_calc.run("增值税一般计税", payload)
        self.assertNotIn("max", str(cm.exception))
        r = self._post({"骨架": "增值税一般计税", "参数": payload})
        self.assertEqual(400, r.status_code)
        self.assertIn("max", r.get_json()["error"])


class TestAnalysisRoutes(_RouteCase):
    """① 判型与 ② 覆盖率：网页补齐分析主线的头两步。

    这两条路由此前不存在，界面上能做的只有检索；用例钉的是"参数原样到引擎、
    坏请求在入口报 400、未检与检了没有不被合并"这三件事。
    """

    def test_classify_forwards_question_and_probe_limit(self):
        with mock.patch.object(tax_server.tax_analyze, "analyze",
                               return_value={"type": {"type": "lookup"}}) as m:
            r = self.client.post("/api/classify",
                                 json={"question": "研发费用加计扣除比例", "max_probes": 5})
        self.assertEqual(200, r.status_code)
        self.assertEqual("研发费用加计扣除比例", m.call_args[0][0])
        self.assertEqual(5, m.call_args.kwargs["max_probes"])

    def test_classify_defaults_to_the_engines_own_probe_limit(self):
        with mock.patch.object(tax_server.tax_analyze, "analyze",
                               return_value={}) as m:
            r = self.client.post("/api/classify", json={"question": "增值税税率"})
        self.assertEqual(200, r.status_code)
        self.assertEqual(tax_server.tax_analyze.DEFAULT_MAX_PROBES,
                         m.call_args.kwargs["max_probes"])

    def test_classify_rejects_blank_question_before_the_engine(self):
        for body in ({}, {"question": "   "}, {"question": 3}):
            with self.subTest(body=body):
                with mock.patch.object(tax_server.tax_analyze, "analyze") as m:
                    r = self.client.post("/api/classify", json=body)
                self.assertEqual(400, r.status_code)
                m.assert_not_called()

    def test_classify_rejects_a_non_object_body(self):
        with mock.patch.object(tax_server.tax_analyze, "analyze") as m:
            r = self.client.post("/api/classify", data="不是 JSON",
                                 content_type="text/plain")
        self.assertEqual(400, r.status_code)
        m.assert_not_called()

    def test_coverage_keeps_null_and_empty_evidence_apart(self):
        """null 是『还没检』，[] 是『检了没有』，路由不许把两者折成同一个。"""
        for raw in (None, []):
            with self.subTest(evidence=raw):
                body = {"qtype": "lookup", "question": "研发费用加计扣除"}
                if raw is not None:
                    body["evidence"] = raw
                with mock.patch.object(tax_server.tax_coverage, "assess",
                                       return_value={}) as m:
                    r = self.client.post("/api/coverage", json=body)
                self.assertEqual(200, r.status_code)
                got = m.call_args.kwargs["evidence"]
                if raw is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual([], got)

    def test_coverage_unknown_qtype_is_400_with_the_engine_list(self):
        r = self.client.post("/api/coverage", json={"qtype": "不存在的题型"})
        self.assertEqual(400, r.status_code)
        body = r.get_json()
        self.assertIn("不存在的题型", body["error"])
        self.assertIn("lookup", body["可选题型"])

    def test_coverage_runs_on_a_real_qtype(self):
        r = self.client.post("/api/coverage",
                             json={"qtype": "lookup", "question": "研发费用加计扣除",
                                   "evidence": []})
        self.assertEqual(200, r.status_code)
        self.assertIn("覆盖率", r.get_json())

    def test_coverage_rejects_wrong_container_shapes(self):
        for patch_body in ({"evidence": {}}, {"params": []}):
            with self.subTest(body=patch_body):
                with mock.patch.object(tax_server.tax_coverage, "assess") as m:
                    r = self.client.post("/api/coverage",
                                         json=dict(patch_body, qtype="lookup"))
                self.assertEqual(400, r.status_code)
                m.assert_not_called()


class TestAnswerRoutes(_RouteCase):
    """⑤ 分析计划／取证／成稿底稿：路由只转发，不重写计划与分层。"""

    def test_plan_is_offline_and_returns_rounds(self):
        r = self.client.post("/api/analysis/plan",
                             json={"question": "研发费用加计扣除比例是多少"})
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertIn("rounds", body)
        self.assertIn("type", body)

    def test_plan_requires_a_question(self):
        r = self.client.post("/api/analysis/plan", json={})
        self.assertEqual(400, r.status_code)

    def test_gather_forwards_every_switch(self):
        with mock.patch.object(tax_server.tax_answer, "gather",
                               return_value={"evidence": []}) as m:
            r = self.client.post("/api/analysis/gather", json={
                "question": "研发费用加计扣除", "at": "2025-06-30",
                "read_body": False, "check_citations": False,
                "max_per_round": 3})
        self.assertEqual(200, r.status_code)
        self.assertEqual("研发费用加计扣除", m.call_args[0][0])
        self.assertEqual({"at": "2025-06-30", "read_body": False,
                          "check_citations": False, "max_per_round": 3},
                         m.call_args.kwargs)

    def test_gather_defaults_read_body_and_citation_check_on(self):
        with mock.patch.object(tax_server.tax_answer, "gather",
                               return_value={}) as m:
            self.client.post("/api/analysis/gather", json={"question": "增值税"})
        self.assertTrue(m.call_args.kwargs["read_body"])
        self.assertTrue(m.call_args.kwargs["check_citations"])
        self.assertIsNone(m.call_args.kwargs["max_per_round"])

    def test_bad_at_or_limit_is_400_before_network(self):
        for body in ({"question": "增值税", "at": "2024-13-01"},
                     {"question": "增值税", "max_per_round": 0},
                     {"question": "增值税", "read_body": "yes"}):
            with self.subTest(body=body):
                with mock.patch.object(tax_server.tax_answer, "gather") as m:
                    r = self.client.post("/api/analysis/gather", json=body)
                self.assertEqual(400, r.status_code)
                m.assert_not_called()

    def test_gather_upstream_failure_is_502(self):
        with mock.patch.object(tax_server.tax_answer, "gather",
                               side_effect=RuntimeError("connection reset")):
            r = self.client.post("/api/analysis/gather", json={"question": "增值税"})
        self.assertEqual(502, r.status_code)
        self.assertIn("connection reset", r.get_json()["error"])

    def test_answer_composes_the_gathered_plan(self):
        plan = {"evidence": [{"title": "a"}], "rounds_done": ["r1"]}
        with mock.patch.object(tax_server.tax_answer, "gather",
                               return_value=plan) as g, \
             mock.patch.object(tax_server.tax_answer, "compose",
                               return_value={"主依据": "a"}) as c:
            r = self.client.post("/api/analysis/answer",
                                 json={"question": "增值税", "read_body": False})
        self.assertEqual(200, r.status_code)
        self.assertIs(plan, c.call_args[0][0])
        self.assertFalse(g.call_args.kwargs["read_body"])


class TestTemplateRoutes(_RouteCase):
    """输出模板清单与正文。键集合只能来自 TEMPLATE_KEYS，正文只能来自 references。"""

    def test_index_lists_every_key_and_no_section_is_missing(self):
        r = self.client.get("/api/analysis/templates")
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual(len(tax_server.TEMPLATE_KEYS), body["count"])
        self.assertEqual(list(tax_server.TEMPLATE_KEYS),
                         [t["key"] for t in body["templates"]])
        self.assertEqual([], body["missing"],
                         "模板文件缺小节：界面会静默少一个可选的成稿形态")

    def test_detail_returns_the_section_from_the_reference_file(self):
        r = self.client.get("/api/analysis/templates/analysis")
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertEqual("分析六段式", body["title"])
        self.assertIn("## 分析六段式", body["text"])

    def test_unknown_key_is_404_with_the_allowed_list(self):
        r = self.client.get("/api/analysis/templates/no-such-template")
        self.assertEqual(404, r.status_code)
        self.assertIn("no-such-template", r.get_json()["error"])
        self.assertEqual(list(tax_server.TEMPLATE_KEYS), r.get_json()["可选"])


class TestCaseRoute(_RouteCase):
    """类案台账：参数透传与两条出口（总局／税屋）的开关。"""

    def test_collect_receives_the_whole_request(self):
        with mock.patch.object(tax_server.tax_cases, "collect",
                               return_value={"候选": []}) as m:
            r = self.client.post("/api/cases", json={
                "topic": "骗取出口退税", "elements": ["主体", " "],
                "mode": "recent", "pages": 2, "order": "date_desc",
                "limit": 5, "as_of": "2025-01-01", "shui5": True})
        self.assertEqual(200, r.status_code)
        self.assertEqual(("骗取出口退税", ["主体"], "recent", 2, "date_desc",
                          5, "2025-01-01"), m.call_args[0])
        self.assertTrue(callable(m.call_args.kwargs["shui5_fetch"]))

    def test_shui5_off_keeps_the_second_source_out(self):
        with mock.patch.object(tax_server.tax_cases, "collect",
                               return_value={}) as m:
            self.client.post("/api/cases", json={"topic": "骗取出口退税"})
        self.assertIsNone(m.call_args.kwargs["shui5_fetch"])

    def test_bad_order_is_400_before_any_fetch(self):
        with mock.patch.object(tax_server.tax_cases.tws,
                               "search_chinatax") as fetch:
            r = self.client.post("/api/cases",
                                 json={"topic": "骗取出口退税", "order": "最新"})
        self.assertEqual(400, r.status_code)
        self.assertIn("order", r.get_json()["error"])
        fetch.assert_not_called()

    def test_upstream_failure_is_502(self):
        with mock.patch.object(tax_server.tax_cases, "collect",
                               side_effect=RuntimeError("timed out")):
            r = self.client.post("/api/cases", json={"topic": "骗取出口退税"})
        self.assertEqual(502, r.status_code)
        self.assertIn("timed out", r.get_json()["error"])


class TestInspectionToolRoutes(_RouteCase):
    """账套／稽查／受理／对账／内控五组检查工具：全部离线，坏输入 400。"""

    LEDGER = {"行": [
        {"行号": 1, "科目": "管理费用—业务招待费", "金额": 380000,
         "税前扣除额": 380000},
    ]}

    def test_ledger_scans_a_real_book(self):
        r = self.client.post("/api/ledger", json={"ledger": self.LEDGER})
        self.assertEqual(200, r.status_code)
        body = r.get_json()
        self.assertIn("风险条目", body)
        self.assertIn("规则", body)

    def test_ledger_without_a_book_is_400(self):
        with mock.patch.object(tax_server.tax_ledger, "scan") as m:
            r = self.client.post("/api/ledger", json={})
        self.assertEqual(400, r.status_code)
        self.assertIn("ledger", r.get_json()["error"])
        m.assert_not_called()

    def test_ledger_rejects_out_of_range_or_non_numeric_tolerance(self):
        for tol in (1.2, -0.1, "0.1", True):
            with self.subTest(tolerance=tol):
                r = self.client.post("/api/ledger",
                                     json={"ledger": self.LEDGER, "tolerance": tol})
                self.assertEqual(400, r.status_code)

    def test_inspection_runs_on_empty_answers(self):
        r = self.client.post("/api/inspection", json={})
        self.assertEqual(200, r.status_code)
        self.assertIn("问询", r.get_json())

    def test_inspection_rejects_bad_containers_and_tolerance(self):
        for body in ({"answers": []}, {"tolerance": 1.5}):
            with self.subTest(body=body):
                r = self.client.post("/api/inspection", json=body)
                self.assertEqual(400, r.status_code)

    def test_blank_intake_is_fillable_and_postable(self):
        r = self.client.get("/api/intake/blank")
        self.assertEqual(200, r.status_code)
        doc = r.get_json()["document"]
        self.assertEqual(["CT-1"], [c["代号"] for c in doc["合同"]])
        self.assertEqual(["U1"], [u["代号"] for u in doc["判断单元"]])

        doc["合同"][0]["名称"] = "采购合同"
        r = self.client.post("/api/intake", json={"document": doc})
        self.assertEqual(200, r.status_code)
        self.assertEqual(9, r.get_json()["缺维度数"])

        r = self.client.post("/api/reconcile", json={"document": doc})
        self.assertEqual(200, r.status_code)
        self.assertIn("核对", r.get_json())

    def test_intake_without_a_contract_name_is_400(self):
        doc = tax_server.tax_intake.blank()
        r = self.client.post("/api/intake", json={"document": doc})
        self.assertEqual(400, r.status_code)
        self.assertIn("名称", r.get_json()["error"])

    def test_intake_and_reconcile_require_the_document_object(self):
        for url in ("/api/intake", "/api/reconcile"):
            with self.subTest(url=url):
                r = self.client.post(url, json={})
                self.assertEqual(400, r.status_code)
                self.assertIn("document", r.get_json()["error"])

    def test_controls_need_a_diagnosis_or_assumptions(self):
        r = self.client.post("/api/controls", json={})
        self.assertEqual(400, r.status_code)

        r = self.client.post("/api/controls",
                             json={"assumptions": ["缺少书面合同"]})
        self.assertEqual(200, r.status_code)
        self.assertIn("控制活动", r.get_json())

    def test_controls_rejects_a_scalar_diagnosis(self):
        r = self.client.post("/api/controls", json={"diagnosis": "缺口"})
        self.assertEqual(400, r.status_code)


SOURCE_UI_BLOCK_RE = re.compile(r"(?ms)^const SOURCE_UI=\{(.*?)^\};")
# 卡面日期那一栏的取名表，整张在一行上。它按源分键，但不是第二份源名词表：
# 值给的是这一路的日期取的是哪一枚字段（公布／成文／发布／站点发布），另一根轴。
DATE_FIELD_RE = re.compile(r"(?m)^const DATE_FIELD=\{([^}]*)\};")
# 词表里每一行的字段形态。少一个字段、多一个字段都当成形态漂移直接断言失败，
# 免得解析静默返回空表让整组用例假绿。
SOURCE_UI_ENTRY_RE = re.compile(
    r"^(\w+):\{icon:'([^']*)',label:'([^']*)',short:'([^']*)',"
    r"cls:'([^']*)',url:'([^']*)'\},?$")
# 界面上每一处渲源名的地方：锚 → (全文只出现一次的子串, 这一行必须出现的取词写法,
# 这一行不许出现写死的源名)。锚落到多行或取不到就报红，免得判据指不准是哪一行。
# 提示那一行的取词写法是 AGG_SOURCE_HINT，它自己怎么从词表算出来由
# test_aggregated_hint_is_derived_from_the_table 单独钉，这里不重复断言（写了就是同义反复）。
RENDER_ANCHORS = {
    "每源几条那一行": ("const chips=", "SOURCE_UI", True),
    "区间内补筛计数那一行": ("lw[k].got", "SOURCE_UI", True),
    "卡面来源角标那一行": ("${sourceBadge(item)}", "sourceBadge", True),
    "角标取词那一行": ("const ui=SOURCE_UI[s];", "SOURCE_UI", True),
    "空结果那句提示": ("一次查${AGG_SOURCE_HINT}", None, True),
    "弹窗页脚那一行": ("footerLabels={", "SOURCE_UI.npc.label", False),
}


def parse_source_ui(html):
    """解出那张词表：键 → {icon,label,short,cls,url}，保持文件里的键序。"""
    block = SOURCE_UI_BLOCK_RE.search(html)
    assert block, "index.html 里找不到 const SOURCE_UI={...} 那张表，六源词表用例没法跑"
    ui = {}
    for line in block.group(1).split("\n"):
        line = line.strip()
        if not line:
            continue
        groups = SOURCE_UI_ENTRY_RE.match(line)
        assert groups, "SOURCE_UI 有一行的字段形态不是 icon/label/short/cls/url：%s" % line
        ui[groups.group(1)] = dict(zip(("icon", "label", "short", "cls", "url"),
                                       groups.groups()[1:]))
    assert ui, "SOURCE_UI 解出来是空表，用例已经失去意义"
    return ui


def label_drift(ui, source_labels):
    """逐键比对：label 去掉图标前缀后必须与后端 SOURCE_LABELS 逐字一致。"""
    return [k for k in ui
            if "%s %s" % (ui[k]["icon"], ui[k]["label"]) != source_labels.get(k)]


def strip_vocab_tables(html):
    """去掉界面上那两张按源分键的表，返回其余文本。为什么是两张而不是拼成一张，
    见 hand_written_copies。两张表各用一份取词正则删，与读它们的用例共用同一份模式，
    删掉的块与被断言的块不会分叉。
    """
    for name, rx in (("SOURCE_UI", SOURCE_UI_BLOCK_RE), ("DATE_FIELD", DATE_FIELD_RE)):
        assert rx.search(html), "index.html 里找不到 %s 那张表，扫描范围已经对不上" % name
    html = SOURCE_UI_BLOCK_RE.sub("", html, count=1)
    return DATE_FIELD_RE.sub("", html, count=1)


def hand_written_copies(html, keys):
    """词表之外还有没有人写 `源名:'文本'`——第二份源名词表的形状。

    按源分键的表在界面上只有两张，扫描时整块跳过：SOURCE_UI 给六源的显示名，
    DATE_FIELD 给每一路卡面日期那一栏的名字。后者量的是另一根轴（这一路的日期
    取的是公布、成文、发布还是站点发布时间），值不是源名，与显示名不冲突；把它
    算成第二份词表就会逼着两根轴挤进一张表，而 sogou 的日期取自结果引用行、fgk 又不
    是六源之一，那张表两头都装不下。
    """
    outside = strip_vocab_tables(html)
    return [k for k in keys
            if re.search(r"\b%s\s*:\s*['\"]" % re.escape(k), outside)]


def renderer_lines(html, anchor):
    """锚落在哪几行。返回行文本列表，调用方要求恰好一行。"""
    return [line for line in html.split("\n") if anchor in line]


def footer_function_body(html):
    start = html.find("function renderFooterSources")
    if start < 0:
        return None
    return html[start:html.index("\n}", start)]


class TestFrontendSourceVocabulary(unittest.TestCase):
    """前端那份六源词表与后端逐键对齐，且界面上只此一份。

    改前 index.html 里存着 srcLabels 与 srcIcons 两张表，「每源几条」「区间内补筛」
    两行又各抄了一份短名，四份里 npc 与 ctax 两处文本已与
    tax_aggregator.SOURCE_LABELS 不一致（前端写"NPC国家法规库""中国税网(报社登载)"）；
    页脚手写的站点清单只列 3 个，而多源聚合实际发的是六路。这一组钉两件事：
    词表内容与后端一致、词表只有一份且渲染点都从它取。加一路源只改后端那一处
    就会在这里报红。语料是 frontend/index.html，全离线，不启服务、不联网。
    """

    @classmethod
    def setUpClass(cls):
        html_path = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
        cls.html = html_path.read_text(encoding="utf-8")
        cls.ui = parse_source_ui(cls.html)
        from tax_aggregator import DEFAULT_SOURCES, SOURCE_LABELS
        cls.default_sources = list(DEFAULT_SOURCES)
        cls.source_labels = dict(SOURCE_LABELS)

    def test_keys_are_the_backends_default_sources(self):
        """键集合与顺序都跟 tax_aggregator.DEFAULT_SOURCES：界面计数那行按表序渲。"""
        self.assertEqual(self.default_sources, list(self.ui))

    def test_labels_and_icons_are_the_backends(self):
        self.assertEqual([], label_drift(self.ui, self.source_labels))

    def test_shorts_and_urls_are_usable(self):
        shorts = [v["short"] for v in self.ui.values()]
        urls = [v["url"] for v in self.ui.values()]
        self.assertEqual(len(set(shorts)), len(shorts))
        self.assertEqual(len(set(urls)), len(urls))
        for k, v in self.ui.items():
            self.assertTrue(v["short"], k)
            self.assertTrue(v["cls"], k)
            self.assertTrue(v["url"].startswith("https://"), k)

    def test_no_second_copy_of_the_vocabulary(self):
        """词表之外不许再出现 `源名:'文本'`，旧那两张表也不许重新声明。"""
        self.assertEqual([], hand_written_copies(self.html, list(self.ui)))
        self.assertIsNone(
            re.search(r"(?:const|let|var)\s+(?:srcLabels|srcIcons)\b", self.html))

    def test_each_renderer_line_takes_the_name_from_the_table(self):
        for site, (anchor, token, no_literals) in RENDER_ANCHORS.items():
            with self.subTest(site=site):
                lines = renderer_lines(self.html, anchor)
                self.assertEqual(1, len(lines),
                                 "%s 的锚 %r 落在 %d 行，判据指不准是哪一行"
                                 % (site, anchor, len(lines)))
                if token:
                    self.assertIn(token, lines[0], site)
                if no_literals:
                    for name in [v["label"] for v in self.ui.values()] + \
                                [v["short"] for v in self.ui.values()]:
                        self.assertNotIn(name, lines[0],
                                         "%s 又自己写了一份源名：%r" % (site, name))

    def test_footer_sites_are_rendered_not_hand_listed(self):
        """页脚那行由词表渲出：静态标记里没有链接，函数体里没有站点名。"""
        line = [l for l in self.html.split("\n") if 'id="footerSources"' in l]
        self.assertEqual(1, len(line))
        self.assertNotIn("<a href", line[0])
        body = footer_function_body(self.html)
        self.assertIsNotNone(body, "页脚渲染函数 renderFooterSources 不见了")
        self.assertIn("SOURCE_UI", body)
        for k, v in self.ui.items():
            self.assertNotIn(v["label"], body, k)
            self.assertNotIn(v["url"], body, k)

    def test_aggregated_hint_is_derived_from_the_table(self):
        line = renderer_lines(self.html, "const AGG_SOURCE_HINT=")
        self.assertEqual(1, len(line))
        self.assertIn("SOURCE_UI", line[0])
        self.assertIn("short", line[0])

    def test_source_badge_falls_back_to_the_row_label(self):
        """角标那枚函数：六源查词表，词表外的层（fgk）退回条目自带的 source_label。

        SEARCH_SOURCES 里有 "fgk"，而 SOURCE_UI 只登记聚合的六路——法规库是
        chinatax 那条链路换源后落到的一层，不是第七路源。改前角标只认 SOURCE_UI，
        _source==='fgk' 的卡片整枚不渲染：屏上出现一批不报出处的条目，同一条卡片
        的日期栏却按 fgk 写着「成文」。
        """
        start = self.html.find("function sourceBadge")
        self.assertGreater(start, -1, "sourceBadge 函数不见了")
        body = self.html[start:self.html.index("\n}", start)]
        self.assertIn("SOURCE_UI[s]", body)
        self.assertIn("item.source_label", body)
        for k, v in self.ui.items():
            self.assertNotIn(v["label"], body, k)
            self.assertNotIn(v["short"], body, k)

    def test_date_label_table_matches_what_each_layer_collects(self):
        """卡面日期那一栏的取名，逐源对上后端登记的字段名。

        DATE_SERVER_FIELDS 记的是接口自己收的那枚字段（公布日期 gbrq / 成文日期
        cwrq / 发布日期 startDate-endDate），三者含义不同：同一份文件的公布日与
        成文日可以差好几个月。界面以前不分源一律写"公布"。
        sogou 那一路的日期取自检索结果的引用行（tax_sogou.py 的结果行带 date），
        是站点自己标注的发布日，所以它进这张表、卡面写"站点发布"；取不到日期
        的条目仍按空串处理，页面不替它编一个日期。
        """
        block = DATE_FIELD_RE.search(self.html)
        self.assertIsNotNone(block, "DATE_FIELD 那张表不见了")
        date_field = dict(re.findall(r"(\w+):'([^']*)'", block.group(1)))
        from tax_aggregator import (DATE_SERVER_SOURCES, DATE_SERVER_FIELDS,
                                    DATE_LOCAL_SOURCES)
        for s in DATE_SERVER_SOURCES:
            self.assertIn(s, date_field, s)
            self.assertIn(date_field[s], DATE_SERVER_FIELDS[s],
                          "%s 卡面写的「%s」对不上后端登记的 %r"
                          % (s, date_field[s], DATE_SERVER_FIELDS[s]))
        scripts = Path(__file__).resolve().parent.parent / "scripts"
        sogou_src = (scripts / "tax_sogou.py").read_text(encoding="utf-8")
        # 双向钉：这一路哪天不再给日期，就必须同时从 DATE_FIELD 里删掉
        self.assertEqual(('"date"' in sogou_src), ("sogou" in date_field))
        self.assertIn("sogou", date_field)
        self.assertEqual("站点发布", date_field["sogou"])
        for s in DATE_LOCAL_SOURCES:
            self.assertIn(s, date_field,
                          "%s 没有日期参数、日期只来自条目本身，卡面也得给它一个词" % s)

    def test_fgk_date_label_is_the_chinatax_layer_it_reroutes_to(self):
        """换到法规库那层时卡面写「成文」，因为它抄的就是 chinatax 的 publish_date。

        tax_fgk 从 search5 的行里原样复制 status/effect_level/publish_date 四栏，
        所以这一层的日期与税务总局同一枚（cwrq）。抄的那枚一旦改名，这里要跟着改。
        """
        block = DATE_FIELD_RE.search(self.html)
        date_field = dict(re.findall(r"(\w+):'([^']*)'", block.group(1)))
        fgk_src = (Path(__file__).resolve().parent.parent / "scripts" /
                   "tax_fgk.py").read_text(encoding="utf-8")
        self.assertIn('"publish_date"', fgk_src)
        self.assertEqual(date_field["chinatax"], date_field["fgk"])

    def test_date_and_status_tooltips_name_no_source(self):
        """筛选栏两枚控件的提示里不许出现源名，也不许出现某一源的字段名。

        这两句在改前各写着一条源语义（日期那一栏写"由 NPC／总局的接口自己收窄"，
        而三路各自收的字段不同名；时效那一栏的说明也只提一路）。写在页面上就成了
        对整屏结果的断言，而本轮究竟哪一路收哪一枚字段、取回与剔除多少条，后端
        已经逐轮写在结果上方（result._date_note 与 _filter_note）。提示只指过去。
        """
        names = [v["label"] for v in self.ui.values()] + \
                [v["short"] for v in self.ui.values()]
        date_line = [l for l in self.html.split("\n") if 'id="filterDateFrom"' in l]
        self.assertEqual(1, len(date_line))
        for n in names:
            self.assertNotIn(n, date_line[0], "日期控件的提示又写了源名")
        for word in ("公布", "成文", "发布"):
            self.assertNotIn(word, date_line[0],
                             "日期控件的提示把某一路的字段名当成了全局口径")
        self.assertIn("日期说明", date_line[0])

    def test_hero_subtitle_counts_the_sources_instead_of_naming_one(self):
        """副标题从前写"数据源自国家法规库 · 实时同步 · 现行有效"：六路里只提到一路，
        而"现行有效"是筛选栏在 NPC 侧的默认值、不是数据的属性。现在路数与源名都从
        词表渲，句子里不许出现任何一路的名字。"""
        start = self.html.find("function renderHeroSubtitle")
        self.assertGreater(start, -1, "renderHeroSubtitle 不见了")
        body = self.html[start:self.html.index("\n}", start)]
        self.assertIn("SOURCE_UI", body)
        self.assertIn("AGG_SOURCE_HINT", body)
        for k, v in self.ui.items():
            self.assertNotIn(v["label"], body, k)
        self.assertNotIn("现行有效", body)

    def test_source_select_offers_exactly_the_backends_branches(self):
        """界面「数据源」的 option 值集合＝后端 SEARCH_SOURCES，两边不许各自长。"""
        opts = re.findall(r'<option value="([^"]+)"',
                          [l for l in self.html.split("\n") if 'id="filterSource"' in l][0])
        self.assertEqual(set(tax_server.SEARCH_SOURCES), set(opts))

    def test_mutation_rewritten_label_is_caught(self):
        """把 npc 的 label 改回旧写法"NPC国家法规库"，比对必须报红。"""
        mutated = self.html.replace("label:'NPC法规库'", "label:'NPC国家法规库'")
        self.assertNotEqual(self.html, mutated)
        drift = label_drift(parse_source_ui(mutated), self.source_labels)
        self.assertEqual(["npc"], drift)

    def test_mutation_second_vocabulary_is_caught(self):
        """把补筛那一行改回手写的短名表，两条判据都必须报红。

        注入的就是修复前那一句（`const nm={sogou:'搜狗搜索',shui5:'税屋',…}`），
        所以这两条不是空转：修复前它们确实是红的。
        """
        real = renderer_lines(self.html, "lw[k].got")[0]
        injected = real.replace("(SOURCE_UI[k]||{label:k}).label",
                                "{sogou:'搜狗搜索',shui5:'税屋'}[k]||k")
        self.assertNotIn("SOURCE_UI", injected)
        mutated = self.html.replace(real, injected)
        self.assertEqual(["sogou", "shui5"], hand_written_copies(mutated, list(self.ui)))
        offenders = [name for name in [v["label"] for v in self.ui.values()] +
                     [v["short"] for v in self.ui.values()] if name in injected]
        self.assertTrue(offenders, "改写后的那一行里没有源名，注入没打到点上")


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
