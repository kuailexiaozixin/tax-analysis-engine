#!/usr/bin/env python3
"""日期区间这一维的用例：NPC 单源与多源聚合两路都不许空转。

2026-10-02 经 /api/search 实测出的两个缺陷都在这里钉住：

  1. NPC 单源：精确检索一旦带上 gbrq 区间，接口就把检索词丢掉，回的是"该区间
     的法律清单"（实测「中华人民共和国增值税法」起 2026-01-01 回 88 条、0 条
     含该词，同词不带日期是 2 条）。危害方向是"带日期反而递回无关清单"，
     所以修法是取回后按标题复核并把复核结果写进 _date_note。
  2. 聚合层：aggregate_search 不吃 date_from/date_to 时，界面日期控件在这条
     路径上被静默丢掉（带与不带日期的 12 条一模一样）。三个网页源没有
     日期参数，只能在取回的窗口内补筛——补筛不是源端收窄，这个区别必须由
     _date_filter 的计数与 _date_note 的判读规则说清楚。

外加只给上界时的空转（实测只给 date_to 时 NPC 回 45 条与不带日期完全一样）
与非法日期不许发出去（接口对非法值是静默不收窄）。

全程打桩上游，不联网。
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tax_search  # noqa: E402
import tax_aggregator  # noqa: E402
import tax_server  # noqa: E402

KW = "中华人民共和国增值税法"


def _rows(titles, dates=None):
    dates = dates or [""] * len(titles)
    return [{"bbbs": f"id{i}", "flfgname": t, "gbrq": d, "sxx": 3}
            for i, (t, d) in enumerate(zip(titles, dates))]


class _Resp:
    """够用就好的 NPC 响应：只实现 search_tax 真用到的那几个成员。"""

    def __init__(self, rows, total=None):
        self._payload = {"data": {"total": total if total is not None else len(rows),
                                  "rows": rows}}
        self.status_code = 200
        self.content = b"{}"
        self.headers = {"Content-Type": "application/json"}
        self.text = "{}"

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _NoCache:
    def _key(self, *parts):
        return "|".join(str(p) for p in parts)

    def get(self, key, max_age=0):
        return None

    def set(self, key, value):
        pass


def _npc(rows, total=None, **kw):
    """打桩网络出口跑一遍 search_tax，返回 (结果, 发出去的 payload 列表)。"""
    payloads = []

    def fake_request(method, url, **kwargs):
        payloads.append(kwargs.get("json") or {})
        return _Resp(rows, total)

    with mock.patch.object(tax_search, "_request", fake_request), \
            mock.patch.object(tax_search, "_cache", _NoCache()):
        return tax_search.search_tax(**kw), payloads


class TestDateFormat(unittest.TestCase):
    """非法日期不许发出去：接口对它是静默不收窄，报出来比回一份没筛过的清单好。"""

    def test_valid_and_blank(self):
        for v in ("2026-01-01", " 2026-01-01 ", None, ""):
            self.assertIn(tax_search.check_iso_date(v, "date_from"),
                          ("2026-01-01", ""))

    def test_rejects_non_padded_and_impossible(self):
        for bad in ("2026-1-1", "2026-13-01", "2026-02-30", "20260101",
                    "2026/01/01", 20260101):
            with self.assertRaises(ValueError, msg=repr(bad)):
                tax_search.check_iso_date(bad, "date_from")

    def test_search_tax_raises_before_requesting(self):
        """校验必须在发请求之前——所以桩一次都不该被调用。"""
        calls = []

        def boom(*a, **kw):
            calls.append(1)
            raise AssertionError("非法日期不该走到发请求")

        with mock.patch.object(tax_search, "_request", boom), \
                mock.patch.object(tax_search, "_cache", _NoCache()):
            with self.assertRaises(ValueError):
                tax_search.search_tax(keyword=KW, search_type=1,
                                      date_from="2026-1-1")
        self.assertEqual([], calls)

    def test_aggregator_raises_before_calling_sources(self):
        calls = {}
        with _stubbed_sources(calls):
            with self.assertRaises(ValueError):
                tax_aggregator.aggregate_search("增值税", date_from="2026-13-01")
        self.assertEqual({}, calls)


class TestNpcDateAssembly(unittest.TestCase):
    """gbrq 区间怎么拼：任一端单独给也要真的下发区间。"""

    def test_both_bounds(self):
        _, payloads = _npc(_rows(["《中华人民共和国增值税法》"]), search_type=1,
                           keyword=KW, date_from="2024-01-01", date_to="2026-12-31")
        self.assertEqual(["2024-01-01", "2026-12-31"], payloads[0]["gbrq"])

    def test_only_from_uses_ceiling(self):
        _, payloads = _npc(_rows(["《中华人民共和国增值税法》"]), search_type=1,
                           keyword=KW, date_from="2024-01-01")
        self.assertEqual(["2024-01-01", tax_search.DATE_CEIL], payloads[0]["gbrq"])

    def test_only_to_uses_floor(self):
        """只给上界不补下界是空转：gbrq 留空，回的和不带日期一模一样（实测 45 条）。"""
        _, payloads = _npc(_rows(["《中华人民共和国土地增值税暂行条例》"]),
                           search_type=1, keyword=KW, date_to="2020-12-31")
        self.assertEqual([tax_search.DATE_FLOOR, "2020-12-31"], payloads[0]["gbrq"])

    def test_no_dates_sends_empty_range(self):
        _, payloads = _npc(_rows(["《中华人民共和国增值税法》"]), search_type=1,
                           keyword=KW)
        self.assertEqual([], payloads[0]["gbrq"])

    def test_overfetch_inherits_the_range(self):
        """模糊标题那一路会过取一次，第二发的 payload 不能把区间丢了。"""
        _, payloads = _npc(_rows(["《中华人民共和国增值税法》"]), search_type=2,
                           keyword=KW, date_to="2020-12-31", size=3)
        self.assertGreaterEqual(len(payloads), 2)
        for p in payloads:
            self.assertEqual([tax_search.DATE_FLOOR, "2020-12-31"], p["gbrq"])


class TestExactDateRecheck(unittest.TestCase):
    """精确检索带区间时按标题复核，复核掉了必须说出来。"""

    def _mixed(self):
        return _rows(["《中华人民共和国民族团结进步促进法》", "《仲裁法》"],
                     ["2026-03-01", "2026-02-01"])

    def test_drops_off_topic_and_notes_it(self):
        r, _ = _npc(self._mixed(), total=88, search_type=1, keyword=KW,
                    date_from="2026-01-01")
        self.assertEqual([], r["results"])
        self.assertEqual(0, r["total"])
        self.assertEqual(88, r["source_total"])
        self.assertIn("_date_note", r)
        self.assertIn("88", r["_date_note"])

    def test_keeps_on_topic_without_note(self):
        r, _ = _npc(_rows(["《中华人民共和国增值税法》"], ["2026-01-01"]), total=2,
                    search_type=1, keyword=KW, date_from="2026-01-01")
        self.assertEqual(1, len(r["results"]))
        self.assertNotIn("_date_note", r)
        self.assertNotIn("source_total", r)

    def test_fuzzy_path_not_rechecked(self):
        """模糊检索不吃这个亏（实测带不带日期都按分词过滤），所以不许误伤。"""
        r, _ = _npc(self._mixed(), total=88, search_type=2, keyword=KW,
                    date_from="2026-01-01", size=20)
        self.assertNotIn("_date_note", r)
        self.assertEqual(2, len(r["results"]))

    def test_no_date_means_no_recheck(self):
        r, _ = _npc(self._mixed(), total=2, search_type=1, keyword=KW)
        self.assertNotIn("_date_note", r)
        self.assertEqual(2, len(r["results"]))

    def test_note_placeholders_all_filled(self):
        """模板里留了没被 format 替换的 {x} 会原样印到界面上，这条钉住它。"""
        r, _ = _npc(self._mixed(), total=88, search_type=1, keyword=KW,
                    date_from="2026-01-01")
        self.assertIsNone(re.search(r"\{[a-z_]+\}", r["_date_note"]))
        self.assertIn("税务总局", r["_date_note"])

    def test_title_has_keyword_ignores_wrappers(self):
        self.assertTrue(tax_search.title_has_keyword("《中华人民共和国增值税法》", KW))
        self.assertTrue(tax_search.title_has_keyword("中华人民共和国 增值税法", KW))
        self.assertFalse(tax_search.title_has_keyword("《仲裁法》", KW))
        self.assertFalse(tax_search.title_has_keyword("", KW))
        self.assertFalse(tax_search.title_has_keyword("《中华人民共和国增值税法》", ""))


def _stubbed_sources(calls, payloads=None):
    """把聚合层的五个源函数换成预置清单，并记录各自被怎么调用。

    payloads: {源名: [条目]}，缺省给一个空清单。
    """
    payloads = payloads or {}
    mocks = []
    for name in ("search_tax", "search_chinatax", "so360_search",
                 "search_shui5", "search_wechat"):
        def make(fn_name):
            def stub(*a, **kw):
                calls[fn_name] = {"args": a, "kwargs": kw}
                items = payloads.get(_FN_SOURCE[fn_name], [])
                return {"keyword": kw.get("keyword") or (a[0] if a else ""),
                        "total": len(items), "results": list(items),
                        "searched_at": "2026-10-03 00:00:00"}
            return stub
        m = mock.patch.object(tax_aggregator, name, make(name))
        mocks.append(m)
    return _Group(mocks)


class _Group:
    def __init__(self, mocks):
        self._mocks = mocks

    def __enter__(self):
        for m in self._mocks:
            m.__enter__()
        return self

    def __exit__(self, *exc):
        for m in self._mocks:
            m.__exit__(*exc)
        return False


_FN_SOURCE = {"search_tax": "npc", "search_chinatax": "chinatax",
              "so360_search": "so360", "search_shui5": "shui5",
              "search_wechat": "wechat"}


def _src_result(items, extra=None):
    out = {"keyword": "增值税", "total": len(items), "results": items,
           "searched_at": "2026-10-03 00:00:00"}
    out.update(extra or {})
    return out


class TestAggregatorDateForward(unittest.TestCase):
    """日期交给能收的那两源：NPC 直接给参数，税务总局走 build_filters。"""

    def _run(self, payloads, **kw):
        calls = {}
        with _stubbed_sources(calls, payloads):
            return tax_aggregator.aggregate_search("增值税", **kw), calls

    def test_forwards_to_both_server_sources(self):
        _, calls = self._run({}, date_from="2024-01-01", date_to="2026-12-31")
        self.assertEqual("2024-01-01", calls["search_tax"]["kwargs"]["date_from"])
        self.assertEqual("2026-12-31", calls["search_tax"]["kwargs"]["date_to"])
        self.assertEqual({"cwrqStart": "2024-01-01 00:00:00",
                          "cwrqEnd": "2026-12-31 23:59:59"},
                         calls["search_chinatax"]["kwargs"]["filters"])

    def test_no_dates_leaves_chinatax_unfiltered(self):
        """不给日期时必须与改动前完全一致：filters 为空 dict，不发这一维。"""
        r, calls = self._run({})
        self.assertEqual({}, calls["search_chinatax"]["kwargs"]["filters"])
        self.assertNotIn("_date_filter", r)
        self.assertNotIn("_date_note", r)

    def test_only_upper_bound_sends_only_cwrq_end(self):
        _, calls = self._run({}, date_to="2020-12-31")
        self.assertEqual({"cwrqEnd": "2020-12-31 23:59:59"},
                         calls["search_chinatax"]["kwargs"]["filters"])


class TestAggregatorLocalWindow(unittest.TestCase):
    """三个网页源没有日期参数：只能在窗口内补筛，且不许静默丢无日期条目。"""

    def _payloads(self):
        return {
            "npc": [{"title": "法A", "publish_date": "2025-05-01"}],
            "chinatax": [{"title": "公告B", "date": "2025-06-01"}],
            "so360": [{"title": "省局C"}, {"title": "省局D"}],
            "shui5": [{"title": "解读E", "date": "2025-04-01"},
                      {"title": "解读F", "date": "2019-01-01"}],
            "wechat": [{"title": "公号G", "date": "2018-01-01"},
                       {"title": "公号H", "date": "2025-02-01"}],
        }

    def _run(self, **kw):
        calls = {}
        with _stubbed_sources(calls, self._payloads()):
            return tax_aggregator.aggregate_search("增值税", **kw)

    def test_out_of_range_dropped_in_range_kept(self):
        r = self._run(date_from="2024-01-01", date_to="2026-12-31")
        titles = {it["title"] for it in r["items"]}
        self.assertIn("解读E", titles)
        self.assertNotIn("解读F", titles)
        self.assertNotIn("公号G", titles)
        self.assertIn("公号H", titles)

    def test_undated_items_are_kept_and_counted(self):
        """360 那一路解析结果根本没有日期字段，按区间外处理会整源消失。"""
        r = self._run(date_from="2024-01-01", date_to="2026-12-31")
        titles = {it["title"] for it in r["items"]}
        self.assertIn("省局C", titles)
        self.assertIn("省局D", titles)
        self.assertEqual(2, r["_date_filter"]["local_window"]["so360"]["no_date"])
        self.assertEqual(0, r["_date_filter"]["local_window"]["so360"]["out_of_range"])

    def test_local_counts_add_up_per_source(self):
        r = self._run(date_from="2024-01-01", date_to="2026-12-31")
        for src, st in r["_date_filter"]["local_window"].items():
            self.assertEqual(st["got"],
                             st["in_range"] + st["out_of_range"] + st["no_date"],
                             msg=src)
        self.assertEqual(2, r["_date_filter"]["local_window"]["shui5"]["got"])
        self.assertEqual(1, r["_date_filter"]["local_window"]["shui5"]["in_range"])
        self.assertEqual(1, r["_date_filter"]["local_window"]["shui5"]["out_of_range"])

    def test_every_local_source_in_scope_has_an_entry(self):
        """某源这一轮 0 条也要留键，缺键会被读成"这一源没参与补筛"。"""
        calls = {}
        with _stubbed_sources(calls, {"npc": [], "chinatax": [], "so360": [],
                                      "shui5": [], "wechat": []}):
            r = tax_aggregator.aggregate_search("增值税", date_from="2024-01-01")
        self.assertEqual({"so360", "shui5", "wechat"},
                         set(r["_date_filter"]["local_window"]))
        for st in r["_date_filter"]["local_window"].values():
            self.assertEqual(0, st["got"])

    def test_note_carries_the_reading_rules(self):
        r = self._run(date_from="2024-01-01", date_to="2026-12-31")
        note = r["_date_note"]
        self.assertIsNone(re.search(r"\{[a-z_]+\}", note))
        for phrase in ("公布日期", "成文日期", "没有日期参数", "窗口内按条目自带日期补筛",
                       "窗口外该源仍可能有区间内的文件", "不能读成", "不算区间内条目"):
            self.assertIn(phrase, note)

    def test_note_does_not_send_the_reader_to_a_json_key(self):
        """这句会原样印在界面上，让读者去查一个看不见的键等于没给计数的去处。"""
        r = self._run(date_from="2024-01-01", date_to="2026-12-31")
        self.assertNotIn("_date_filter", r["_date_note"])
        self.assertIn("另列一行", r["_date_note"])

    def test_note_names_only_sources_in_this_run(self):
        """剔掉 NPC 的 sta 专题那一路，说明里不许出现 NPC 的收窄方式。"""
        calls = {}
        sources = [s for s in tax_aggregator.DEFAULT_SOURCES if s != "npc"]
        with _stubbed_sources(calls, self._payloads()):
            r = tax_aggregator.aggregate_search("特别纳税调整", sources=sources,
                                                date_from="2024-01-01")
        self.assertEqual(("特别纳税调整",), calls["search_chinatax"]["args"])
        self.assertNotIn("NPC", r["_date_note"])
        self.assertEqual(["chinatax"], r["_date_filter"]["server_side"])

    def test_npc_recheck_note_is_carried_up(self):
        """NPC 复核掉条目那句话在子结果里，聚合输出必须把它抬上来，否则整源少了几条没人知道。"""
        calls = {}
        payloads = self._payloads()
        with _stubbed_sources(calls, payloads):
            with mock.patch.object(tax_aggregator, "search_tax",
                                   return_value=_src_result(
                                       [], {"_date_note": "NPC 复核掉了 88 条"})):
                r = tax_aggregator.aggregate_search("增值税", date_from="2024-01-01")
        self.assertIn("NPC 那一路另有情况：NPC 复核掉了 88 条", r["_date_note"])


class TestServerForwardsDates(unittest.TestCase):
    """界面 → 后端 → 聚合层，日期这一维不许在中间某条分支上被丢掉。"""

    NPC_TAX_TYPE = {"type": "增值税", "authority": "npc", "parent_law": KW,
                    "aliases": ["增值税"]}
    STA_TAX_TYPE = {"type": "转让定价", "authority": "sta",
                    "search_term": "特别纳税调整", "aliases": ["转让定价"]}

    def setUp(self):
        tax_server.app.config["TESTING"] = True
        self.client = tax_server.app.test_client()

    def _post(self, body, tax_type):
        with mock.patch.object(tax_server, "resolve_tax_type", return_value=tax_type), \
                mock.patch.object(tax_server, "detect_intent", return_value="policy_lookup"):
            return self.client.post("/api/search", json=body)

    def test_aggregated_npc_branch_forwards_dates(self):
        with mock.patch.object(tax_server, "aggregate_search",
                               return_value={}) as m:
            r = self._post({"keyword": "增值税", "source": "aggregated",
                            "date_from": "2024-01-01", "date_to": "2026-12-31"},
                           self.NPC_TAX_TYPE)
        self.assertEqual(200, r.status_code)
        self.assertEqual("2024-01-01", m.call_args.kwargs["date_from"])
        self.assertEqual("2026-12-31", m.call_args.kwargs["date_to"])

    def test_aggregated_sta_branch_forwards_dates(self):
        """换源到总局专题那条路同样吃日期控件，别只在默认那条分支上接。"""
        with mock.patch.object(tax_server, "aggregate_search",
                               return_value={}) as m:
            r = self._post({"keyword": "转让定价", "source": "aggregated",
                            "date_from": "2024-01-01"}, self.STA_TAX_TYPE)
        self.assertEqual(200, r.status_code)
        self.assertEqual("2024-01-01", m.call_args.kwargs["date_from"])

    def test_aggregated_bad_date_is_400(self):
        with mock.patch.object(tax_server, "aggregate_search",
                               side_effect=ValueError("date_from 要 YYYY-MM-DD")):
            r = self._post({"keyword": "增值税", "source": "aggregated",
                            "date_from": "2024-1-1"}, self.NPC_TAX_TYPE)
        self.assertEqual(400, r.status_code)
        self.assertIn("筛选参数不合法", r.get_json()["error"])

    def test_npc_single_source_bad_date_is_400(self):
        with mock.patch.object(tax_server, "search_tax",
                               side_effect=ValueError("date_to 不是真实日期")):
            r = self._post({"keyword": "增值税", "source": "npc",
                            "date_to": "2026-02-30"}, None)
        self.assertEqual(400, r.status_code)
        self.assertIn("筛选参数不合法", r.get_json()["error"])

    def test_npc_single_source_forwards_dates(self):
        with mock.patch.object(tax_server, "search_tax", return_value={}) as m:
            r = self._post({"keyword": "zzz", "source": "npc",
                            "date_from": "2024-01-01", "date_to": "2026-12-31"}, None)
        self.assertEqual(200, r.status_code)
        self.assertEqual("2024-01-01", m.call_args.kwargs["date_from"])
        self.assertEqual("2026-12-31", m.call_args.kwargs["date_to"])


class TestFrontendDateNote(unittest.TestCase):
    """后端给的日期说明要真到用户眼前，控件也要写清各源语义不同。"""

    def setUp(self):
        self.src = (Path(__file__).resolve().parent.parent /
                    "frontend" / "index.html").read_text(encoding="utf-8")

    def test_reads_note_from_the_result_layer(self):
        """读的是 result 那一层——单源与聚合两条路都把这句挂在那里。"""
        self.assertRegex(self.src, r"result\?\._date_note")

    def test_note_is_escaped(self):
        """说明里带用户输入的检索词，不转义就是 XSS 入口（与 _routed 同一条要求）。"""
        self.assertRegex(self.src, r"esc\(result\._date_note\)")

    def test_date_control_documents_per_source_semantics(self):
        """日期控件的悬浮说明要写明三种生效方式，否则用户会把补筛读成源端收窄。"""
        label = re.search(r'<label title="([^"]*)">日期</label>', self.src)
        self.assertIsNotNone(label, "日期控件的 label 缺少 title 说明")
        text = label.group(1)
        for phrase in ("公布日期", "成文日期", "窗口内补筛"):
            self.assertIn(phrase, text)

    def test_local_counts_are_rendered(self):
        """说明里承诺"另列一行"，那一行必须由 _date_filter.local_window 真渲染出来。"""
        self.assertIn("result?._date_filter?.local_window", self.src)
        self.assertRegex(self.src, r"取回 \$\{lw\[k\]\.got\} 条")
        for phrase in ("区间内", "区间外剔除", "无日期保留"):
            self.assertIn(phrase, self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
