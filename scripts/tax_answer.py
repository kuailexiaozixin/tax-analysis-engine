#!/usr/bin/env python3
"""
分析编排 — 把判型、检索、定级串成一次调用，产出结构化的分析底稿。

它不是自动答税题的工具，而是把"该按什么顺序查、查回来的依据哪条能当主
依据、哪些结论条件缺失"这件事固化成代码。上层模型拿着 analyze() 的返回
按 needs 清单组织文字答案，不需要重新判断该查什么。

为什么要编排层：单看各检索脚本，它们都只知道自己那一个源。问题在于
"一道题要几轮检索、每轮用什么源"这件事，取决于问题类型而不是源本身。
同是企业所得税法，lookup 类一轮标题检索就够，entitlement 类至少要三轮
（本体法 → 总局优惠文件 → 税屋实操口径），option_judge 类还得逐条比对
所以每一轮都要取全文。这个顺序住在编排层里，不该让每次调用重新想。

三段产物：
  plan    该查什么、查几轮、每轮用什么源（不联网，纯判定）
  gather  真去检索并定级（联网，按 plan 执行）
  answer  在 gather 基础上把依据分层、标出限制条件（不联网）

Usage:
  python tax_answer.py "研发费用加计扣除比例是多少" --plan
  python tax_answer.py "研发费用加计扣除比例是多少" --gather --at 2026-09-27
  python tax_answer.py "研发费用加计扣除比例是多少" --answer --json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import tax_analyze as A
import tax_evidence as E
import tax_fgk as FGK
import tax_search as T
import tax_shui5 as S5
import tax_so360 as S360
import tax_terms as TT
import tax_wechat as WX

# 每类问题要走哪几轮检索。轮次编号从 1 起，ordered 类必须按序。
# 每轮给 sources 是"该轮优先用的源"，不是"只能用这些源"。
PLAN_TEMPLATES = {
    "lookup": [
        {"round": 1, "goal": "取本体法现行条文", "sources": ["npc"],
         "call": "search_tax(关键词, scope='title', status=3, size=20)"},
        {"round": 2, "goal": "确认有无更新或配套文件", "sources": ["fgk"],
         "call": "search_fgk(关键词, size=8)"},
    ],
    "option_judge": [
        {"round": 1, "goal": "取题干所指法律的完整条文", "sources": ["npc"],
         "call": "search_tax(关键词, scope='title', status=3, size=20) + fetch_detail"},
        {"round": 2, "goal": "逐条比对所需的配套规定", "sources": ["npc", "fgk"],
         "call": "search_fgk(关键词, size=8)"},
        {"round": 3, "goal": "对争议选项找实务口径", "sources": ["shui5"],
         "call": "search_shui5(关键词, size=3, read_body=True)"},
    ],
    "entitlement": [
        {"round": 1, "goal": "优惠本体法与享受条件", "sources": ["npc"],
         "call": "search_tax(优惠名, scope='title', status=3, size=20)"},
        {"round": 2, "goal": "资格判定与备案留存要求", "sources": ["fgk"],
         "call": "search_fgk(优惠名, size=8)"},
        {"round": 3, "goal": "实务口径：卡在条件上怎么认定", "sources": ["shui5", "wechat"],
         "call": "search_shui5(优惠名+条件, size=3, read_body=True)"},
    ],
    "liability": [
        {"round": 1, "goal": "计税依据与税率", "sources": ["npc"],
         "call": "search_tax(税种, scope='title', status=3, size=20)"},
        {"round": 2, "goal": "减免、加计、起征点等调整项", "sources": ["fgk"],
         "call": "search_fgk(税种+优惠, size=8)"},
        {"round": 3, "goal": "计算口径与常见调整", "sources": ["shui5"],
         "call": "search_shui5(税种+计算, size=3, read_body=True)"},
    ],
    "procedure": [
        {"round": 1, "goal": "征管法与办税依据", "sources": ["npc"],
         "call": "search_tax(税收征收管理法, scope='title', status=3, size=20)"},
        {"round": 2, "goal": "办税指南与操作口径", "sources": ["fgk"],
         "call": "search_fgk(事项+办理, size=8)"},
        {"round": 3, "goal": "实操步骤与时限", "sources": ["shui5"],
         "call": "search_shui5(事项+怎么申报, size=3, read_body=True)"},
    ],
    "risk": [
        {"round": 1, "goal": "行为定性与罚则", "sources": ["npc"],
         "call": "search_tax(行为+税种, scope='title', status=3, size=20)"},
        {"round": 2, "goal": "处罚依据与裁量口径", "sources": ["fgk"],
         "call": "search_fgk(行为+处罚, size=8)"},
        {"round": 3, "goal": "稽查口径与合规整改", "sources": ["shui5"],
         "call": "search_shui5(行为+风险, size=3, read_body=True)"},
    ],
    "treatment": [
        {"round": 1, "goal": "税目定义与列举", "sources": ["npc"],
         "call": "search_tax(事项, scope='title', status=3, size=20)"},
        {"round": 2, "goal": "视同销售、免税列举等配套规定", "sources": ["fgk"],
         "call": "search_fgk(事项, size=8)"},
        {"round": 3, "goal": "实操认定口径", "sources": ["shui5"],
         "call": "search_shui5(事项+怎么认定, size=3, read_body=True)"},
    ],
    "fill_blank": [
        {"round": 1, "goal": "取该法全文，按定义条款定位", "sources": ["npc"],
         "call": "search_tax(本体法, scope='title', search_type=1, status=3) + fetch_detail"},
        {"round": 2, "goal": "确认该术语的现行表述有无修订", "sources": ["fgk"],
         "call": "search_fgk(术语, size=8)"},
    ],
    "compare": [
        {"round": 1, "goal": "各方案各自的法律依据", "sources": ["npc"],
         "call": "search_tax(方案A, ...) 与 search_tax(方案B, ...) 各一轮"},
        {"round": 2, "goal": "适用条件与临界值", "sources": ["fgk"],
         "call": "search_fgk(方案, size=8)"},
        {"round": 3, "goal": "实务选择建议", "sources": ["shui5"],
         "call": "search_shui5(方案+怎么选, size=3, read_body=True)"},
    ],
}

# 每轮每源取多少条。option_judge 要逐条比对所以取多，其余按够用即可。
ROUND_FETCH = {
    "option_judge": {"npc": 20, "fgk": 8, "shui5": 3, "wechat": 3},
    "default": {"npc": 20, "fgk": 8, "shui5": 3, "wechat": 3},
}


def build_plan(question: str) -> dict:
    """只做判定与编排，不联网。

    Returns:
        tax_analyze.analyze 的返回 + {"rounds":[...]} + {"probes":[...]}。
    """
    a = A.analyze(question)
    tname = a["type"]["type"]
    tpl = [dict(r) for r in PLAN_TEMPLATES.get(tname, PLAN_TEMPLATES["lookup"])]
    _reroute_first_round(tpl, question)
    if a.get("legislative_stage"):
        _retarget_for_legislation(tpl)
    a["rounds"] = tpl
    return a


def _retarget_for_legislation(rounds: list) -> None:
    """题面问的是草案或征求意见稿时，把轮次目标改成"取修订基线"，再补一轮立法取证。

    这一条是实测逼出来的：同一份《税收征管法》修订草案的提问，原先的轮次写法是
    "取本体法现行条文""确认有无更新或配套文件"，跑完取回的是 2015 年修正的现行
    文本，看起来答完了，实际一句草案内容都没碰——搜索策略按"规定是什么"排，
    而题面问的是"正在审的那份文本改了什么"。立法过程件不在五个税收源里
    （见 tax_analyze.legislative_note），所以取证的落点放在人大网站内检索。
    """
    for rnd in rounds:
        rnd["goal"] = rnd["goal"] + "（作修订基线对照，不是草案内容）"
    rounds.append({
        "round": len(rounds) + 1,
        "goal": "取立法过程件与草案解读：人大网草案/审议公告，及税屋、公众号的逐条解读",
        # 草案正文不在五个税收源里，但草案解读文章在税屋与公众号：实测
        # search_shui5("税收征管法 修订草案") 首条即国务院法制办征求意见稿通知，
        # search_wechat 同名次全是"修订草案解读"。只查人大网等于漏掉这一层。
        "sources": ["legis", "shui5", "wechat"],
        "term_key": "legis",
        "call": "so360_search(草案词, site='npc.gov.cn') + "
                "search_shui5 / search_wechat(同一草案词)",
    })


def _reroute_first_round(rounds: list, question: str) -> None:
    """第 1 轮按专题的 authority 换源。

    模板把第 1 轮都写成 npc，但 sta 专题（转让定价、税收优惠等）在 NPC 库里
    检索无效——搜"转让定价"命中 10 条全是土地和矿产资源转让条例。这类题第 1 轮
    就该查总局法规库，否则取回的全是无关法规，还要多花一轮才发现。
    """
    info = T.resolve_tax_type(question) or {}
    if info.get("authority") != "sta":
        return
    first = rounds[0]
    if "npc" not in first["sources"]:
        return
    # 总局源已在轮内就只调顺序，不重复排
    first["sources"] = (["fgk"] + [s for s in first["sources"] if s != "fgk"]
                        if "fgk" in first["sources"]
                        else ["fgk", "npc"])
    first["call"] = first["call"].replace("search_tax(", "search_fgk(")
    first["goal"] = f"{first['goal']}（该专题在 NPC 库检索无效，改查总局）"


# ── 执行 ───────────────────────────────────────────────────────────────────
def _npc_round(term: str, size: int) -> tuple[list, str]:
    # 本体法名已知时走精确检索。模糊检索按相关度排，宪法类文本会因为
    # "法"字出现在"企业所得税法"里而被顶到首位（实测"中华人民共和国企业
    # 所得税法"模糊检索首条是宪法），主依据会被选错。
    st = 1 if term.startswith("中华人民共和国") else 2
    rows, err = _rows_and_error(
        T.search_tax(term, scope="title", search_type=st, status=3, size=size))
    return rows, err


def _fgk_round(term: str, size: int) -> tuple[list, str]:
    rows, err = _rows_and_error(FGK.search_fgk(term, size=size))
    return rows, err


def _shui5_round(term: str, size: int) -> tuple[list, str]:
    rows, err = _rows_and_error(S5.search_shui5(term, size=size, read_body=True))
    return rows, err


def _wechat_round(term: str, size: int) -> tuple[list, str]:
    rows, err = _rows_and_error(WX.search_wechat(term, size=size))
    return rows, err


def _rows_and_error(r: dict) -> tuple[list, str]:
    """把源返回拆成 (条目, 失败说明)，两种"没有"必须分开带出去。

    原先这几个轮次函数写成 `rows if not r.get("_error") else []`：源挂了和被
    判空都变成长度 0 的列表，错误文本就地丢掉。后果是主依据会静默降级——实测
    点名《税收征管法》那一趟，NPC 没回来时 90 分的现行有效法律不见了，顶上
    【主依据】的是 70 分的《个体工商户建账管理暂行办法》，输出里一个字都看不
    出这是取数失败而不是库里没有。
    """
    err = r.get("_error") or ""
    return (r.get("results", []) if not err else []), err


# 法律草案、审议结果报告、征求意见公告的法定公布平台。五个税收源都不收这一层。
LEGISLATION_SITE = "npc.gov.cn"


def _legis_round(term: str, size: int) -> tuple[list, str]:
    """立法过程实位：在中国人大网站内找草案与审议公告。

    问的是草案时，只取回现行有效文本等于答错题——那一份是修订基线，不是草案
    内容（判据见 `tax_analyze.legislative_stage`）。这里取回的每一条都打上
    `_reliability: medium`：它是定位文本的线索，本身不能当依据引用，定级层
    （`tax_evidence.grade`）据此把可引用性分清零、`citation_hint` 换成否决句。
    """
    rows, err = _rows_and_error(S360.so360_search(term, site=LEGISLATION_SITE,
                                                 size=size))
    for row in rows:
        row["_reliability"] = "medium"
        row["_reliability_note"] = ("立法过程件（中国人大网站内检索所得），"
                                    "不在五个税收源的收录范围内，只用于定位文本")
        # 单独一个标记，不靠 medium 顶替：medium 说的是"检索结果可能偏题"，
        # 这一层的件子可能恰恰就是题面问的那份草案，它不能当主依据的原因是
        # 文本还没有效力，不是排序不可信。compose 据此把它排除在主依据候选之外。
        row["legislative_process"] = True
    return rows, err


_FETCHERS = {"npc": _npc_round, "fgk": _fgk_round,
             "shui5": _shui5_round, "wechat": _wechat_round,
             "legis": _legis_round}


def search_terms(question: str) -> dict:
    """把用户原话拆成各源该用的检索词。

    直接拿整句去检索不行，NPC 标题检索按整段字面匹配，"研发费用加计扣除比例
    是多少"取回来的全是含"费用"二字的无关法规（实测首条是《国家赔偿费用
    管理条例》）。所以每个源用自己的词：法规库认规范名，法规库清单认专题
    检索词，解读源认口语化的短词。

    Returns:
        {"npc": 本体法名或原话, "fgk": 专题检索词, "shui5": 短词,
        "wechat": 短词, "legis": 立法过程检索词（题面没点立法阶段则为空）,
        "topic": 识别到的专题名, "parent_law": 本体法}
    """
    info = T.resolve_tax_type(question) or {}
    topic = info.get("type", "")
    parent = info.get("parent_law", "")
    # 总局专题的检索词不是分类名，"税收争议救济"这类名号搜不到东西
    sta_term = info.get("search_term") or topic
    # 解读源用匹配上的别名当短词：把整句事实丢进 360 搜是搜不出实务文章的，
    # 命中的别名（加计扣除、小规模）才是税屋文章标题里真会用到的词。
    alias = info.get("matched_alias") or ""
    short = _strip_question_tail(question)
    if alias and len(short) > len(alias) + 6:
        short = alias
    # 用户点名了某份文件时，法规库那一轮要找的是这一份文件本身，
    # 不是它所属税种的本体法——本体法留给 npc 那一轮当上位法依据。
    cited = TT.cited_documents(question)
    fgk_term = TT.core_of_title(cited[0]["title"]) if cited else (
        sta_term if info.get("authority") == "sta" else parent or short)
    if cited and (not alias or len(short) > len(cited[0]["core"])):
        short = cited[0]["core"][:6]

    # 立法过程实位用的是法名简称 + 阶段词："中华人民共和国税收征收管理法 草案"
    # 在人大网站内搜不到，去掉国号前缀才命中草案与审议公告的标题写法。
    stage_words = A.legislative_terms(question)
    law_short = (parent.replace("中华人民共和国", "").strip() if parent else short)
    legis_term = f"{law_short} {stage_words[0]}".strip() if stage_words else ""

    return {
        "npc": parent or short,
        "fgk": fgk_term,
        "shui5": short,
        "wechat": short,
        "legis": legis_term,
        "topic": topic,
        "parent_law": parent,
        "authority": info.get("authority", "npc"),
        "cited": cited,
    }


def locate_cited_document(title: str, size: int = 6) -> tuple:
    """按用户点名的文件标题在总局法规库里定位这一份文件。

    检索词由长到短依次试，全部试完再把各次结果合在一起按标题命中率排。
    长词精确但要求库里标题字面含着整串（用户给的标题常缺发文机关前缀或词序
    不同），短词召回广但会把整个主题域都带回来，所以两头都要试，见
    tax_terms.title_candidates。

    原文排在解读之前：解读件标题与原文只差"的解读"三个字，字面命中率几乎
    并列，而解读按 ⑦ 不能当主依据。点名的是文件本身时，只要检索到了原文就
    不该让解读顶上去；只有原文一条没取到，才把解读放首位并另作标记。

    排在最前的还得"就是被点名的那一份"：字符命中率量不出这个区别（见
    tax_terms.title_identity 的说明），所以每条都标上 _cited_identity，
    判过关系的排在没判过的之前，调用方只认 rows[0] 带标记的那一条。

    Returns:
        (按标题命中率降序的条目列表, 实际试过的检索词)
    """
    rows, tried = {}, []
    for cand in TT.title_candidates(title):
        tried.append(cand)
        try:
            r = FGK.search_fgk(cand, size=size)
        except Exception:
            continue
        for row in r.get("results", []):
            url = row.get("url") or ""
            if url and url not in rows:
                rows[url] = row
    sim = {u: TT.title_similarity(title, row.get("title", ""))
           for u, row in rows.items()}
    ident = {u: TT.title_identity(title, row.get("title", ""))
             for u, row in rows.items()}

    def _is_jiedu(row):
        return row.get("title", "").endswith("的解读")

    out = sorted(rows.values(),
                 key=lambda x: (not ident[x.get("url", "")], _is_jiedu(x),
                                -sim[x.get("url", "")]))
    for row in out:
        row["_title_match"] = round(sim[row.get("url", "")], 3)
        row["_is_interpretation"] = _is_jiedu(row)
        row["_cited_identity"] = ident[row.get("url", "")]
    return out[:size], tried


def corroborate_validity_from_target(evidence: list, at: str = "") -> list:
    """用点名文件正文首段的"根据……规定"，为被援引的上位规则文件补时效证据。

    法规库的「财税文件」栏不录入时效：实测财税〔2009〕59 号、财税〔2014〕109 号
    的录入项为空，财税〔2003〕16 号那个位置上是字符串 "null"；详情页也只有成文
    日期，没有时效栏。所以这类文件按状态字段只能判"时效未标明"，会被压在依据线
    以下——而企业重组这道题里，59 号正是点名公告的上位规则。一份现行有效的公告
    把它列为制定依据，是这个来源里能拿到的在效证据。只补状态判不出来的条目，
    已标明废止或未生效的一律不动。

    Args:
        evidence: gather 取回并已带 body 字段的条目列表（原地标注）。
        at: 观察时点，用来确认援引方自己在该时点在效。

    Returns:
        被标注 corroborated_by 的条目标题列表。
    """
    target = next((e for e in evidence
                   if e.get("_cited_target") and (e.get("body") or "").strip()),
                  None)
    if not target or target.get("_is_interpretation"):
        return []
    if E.judge_validity(target, at)["validity"] != "effective":
        return []
    body = target["body"]
    head = body[:600]
    colon = head.find("：")
    para = head[:colon + 1] if colon > 0 else head
    titles = re.findall(r"《([^》]{4,80})》", para)
    tagged = []
    for e in evidence:
        if e is target or e.get("corroborated_by"):
            continue
        if E.judge_validity(e, at)["validity"] != "unknown":
            continue
        num = (e.get("document_number") or "").strip()
        title = (e.get("title") or "").strip()
        hit = bool(num and num in para) or \
            any(t and (t in title or title in t) for t in titles)
        if hit:
            e["corroborated_by"] = target.get("title", "")
            tagged.append(title)
    return tagged


_TAIL_RE = None


def _strip_question_tail(question: str) -> str:
    """把问句压成检索用的短词。

    保留主题，去掉"是多少/可以享受吗/怎么办/比例多少"这类询问措辞。
    只做尾部截断，不改中间内容——中间的"加计扣除""小规模"都是检索关键词，
    改掉会丢信息。
    """
    global _TAIL_RE
    if _TAIL_RE is None:
        _TAIL_RE = re.compile(
            r"(是多少|是多少？|是多少?)|(可以享受吗)|(能不能享受)|"
            r"(能否享受)|(怎么办理|怎么办|怎么算|怎么申报|怎么交)|"
            r"(有什么风险|有风险吗)|(符合条件吗|符合条件|是否适用)|"
            r"[？?。，,、\s]+$")
    out = _TAIL_RE.sub("", question.strip())
    return out.strip() or question.strip()


def gather(question: str, at: str = "", read_body: bool = True,
           max_per_round: int | None = None) -> dict:
    """按 plan 逐轮检索，合并去重后定级。

    Args:
        question: 用户原话。
        at: 观察时点 YYYY-MM-DD，传空则不判生效区间。
        read_body: 是否取税屋正文。取正文要启浏览器，慢，不取时仍能拿到
            标题与地址用于分层展示。
        max_per_round: 每轮每源最多取几条，调试时用来压时间。

    Returns:
        plan 字段 + {"evidence":[定级后的依据], "rounds_done":[...],
        "errors":[...]}。
    """
    plan = build_plan(question)
    tname = plan["type"]["type"]
    sizes = ROUND_FETCH.get(tname, ROUND_FETCH["default"])
    terms = search_terms(question)

    seen, evidence, errors, rounds_done = set(), [], [], []
    cited = terms.get("cited") or []
    cited_located = False
    if cited:
        # 用户点名了文件，先按标题把这一份文件本身捞出来排在证据最前，
        # 后面各轮取回的上位法与配套规定当并列依据，不抢它的位置。
        rows, tried = locate_cited_document(cited[0]["title"])
        terms["cited_candidates"] = tried
        # 只有标题确实就是被点名那一份才认目标。实测点名「税收征管法」时
        # 字面命中率 1.0 的那条是国税发〔2001〕110号——它在标题里引用了这部法，
        # 一旦当上主依据，90 分的现行有效法律就被挤到并列位。
        cited_located = bool(rows) and bool(rows[0].get("_cited_identity"))
        for i, row in enumerate(rows):
            url = row.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            if i == 0 and cited_located:
                row["_cited_target"] = True
                if read_body:
                    body = FGK.fetch_fgk_body(url)
                    if body.get("content"):
                        row["body"] = body["content"]
                        row["body_date"] = body.get("pub_date", "")
                    elif body.get("_error"):
                        errors.append("点名文件取正文失败：" + body["_error"])
            evidence.append(row)
    for rnd in plan["rounds"]:
        got_this_round, failed_this_round = 0, []
        # 一轮内各源默认用自己的词；立法过程轮用 term_key 指定草案词，让
        # 税屋/公众号这一趟按"法名+草案"搜，而不是按主题短词搜（后者取回现行规定）。
        for src in rnd["sources"]:
            fn = _FETCHERS[src]
            kw = terms.get(rnd.get("term_key") or src, question)
            try:
                if src == "shui5" and not read_body:
                    r = S5.search_shui5(kw, size=1, read_body=False)
                    rows, err = _rows_and_error(r)
                else:
                    rows, err = fn(kw, sizes.get(src, 8))
            except Exception as e:                      # 单源失败不中断整轮
                errors.append(f"第{rnd['round']}轮 {src} 失败："
                              f"{str(e).splitlines()[0][:110]}")
                failed_this_round.append(src)
                continue
            if err:
                # 取数失败和被判空是两件事，这里必须留名：下游 rounds_done 的
                # found=0 只有配上这一条才不读成"库里没有"（见 ⑤）
                errors.append(f"第{rnd['round']}轮 {src} 取数失败：{err}")
                failed_this_round.append(src)

            for row in rows:
                url = row.get("url") or row.get("id") or row.get("title", "")
                if url in seen:
                    continue
                seen.add(url)
                evidence.append(row)
                got_this_round += 1
        rounds_done.append({"round": rnd["round"], "goal": rnd["goal"],
                            "found": got_this_round,
                            "failed": failed_this_round})

    # 点名文件正文把上位规则列为制定依据，据此给这些文件补时效证据，再定级。
    corroborated = corroborate_validity_from_target(evidence, at)
    graded = E.grade_all(evidence, at=at)
    plan["evidence"] = graded
    plan["rounds_done"] = rounds_done
    plan["errors"] = errors
    plan["corroborated"] = corroborated
    plan["at"] = at
    plan["terms"] = terms
    # 点名了文件但库里没捞到那一份：compose 要把这件事说明白，不能拿同域文件顶位
    if cited:
        plan["cited_located"] = cited_located
    plan["gathered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return plan


# ── 组织 ───────────────────────────────────────────────────────────────────
# 点名文件定位成功后写进主依据那句的措辞：判据来自 tax_terms.title_identity，
# 这里只把它翻译给人看，不再复述"命中率"——那个数是排序用的，撑不起"就是这一份"。
_CITED_IDENTITY_LABEL = {"same": "标题字面同一",
                         "abbrev": "缩略同一（题面是漏字写法）"}


def compose(plan: dict) -> dict:
    """在 gather 底稿上做依据分层与结论骨架，不联网、不编造结论。

    它不替代模型写答案，而是把"哪些能当依据、哪些只能参考、哪些条件没问"
    先算清楚，模型照着组织文字即可。
    """
    ev = plan.get("evidence", [])
    cited = [e for e in ev if e.get("_cited_target")]
    target = cited[0] if cited else None
    cited_note = ""
    if target and not target.get("_is_interpretation"):
        # 位阶最高的那条不是答案的主依据：用户问的是这一份文件，
        # 拿《企业所得税法》顶上去等于没读那份公告。
        primary = dict(target)
        doc_num = primary.get("document_number") or "文号未标"
        ident = _CITED_IDENTITY_LABEL.get(target.get("_cited_identity", ""),
                                          "标题字面同一")
        primary["_why"] = (
            f"用户点名的文件本身（{doc_num}），与点名标题{ident}；"
            f"{primary.get('rank_label', '未定性')}、"
            f"{primary.get('validity_label', '时效未标明')}。位阶低于其上位法，"
            f"结论要与并列的上位法同读")
    elif target:
        # 只捞到官方解读，没捞到文件原文：解读是参考材料，按 ⑦ 不能当主依据，
        # 主依据仍走正常分层，同时把"原文没取到"这件事说明白。
        primary = E.pick_primary(ev)
        cited_note = (
            f"点名的《{target.get('title', '')[:40]}》只取到官方解读、没取到原文，"
            f"解读按 ⑦ 只能作参考；答案要写明原文待核")
    else:
        primary = E.pick_primary(ev)
        if plan.get("cited_located") is False:
            # 点名了文件、但召回的没有一份是它：不能装作没听见这个点名，
            # 也不能把同域文件当它用——只说清"没定位到"和"现在给的是什么"。
            want = ((plan.get("terms") or {}).get("cited") or [{}])[0].get("title", "")
            cited_note = (
                f"点名的《{want}》没有定位到同一份文件：召回的标题都只是在字面上"
                f"含住这几个字（包括在书名号里引用它的），按 ⑦ 不能顶替被点名的那份。"
                f"下面的主依据按主题分层给出，答案要写明点名文件待核")

    # 主依据那一行不再进并列/参考，否则同一份文件在输出里出现两次，
    # 一次标"可作主依据"一次标"只能参考"。NPC 那条链路不带 url，所以
    # 有 url 按 url 认，没 url 按标题认。
    def _is_primary(e):
        purl, ptitle = primary.get("url", ""), primary.get("title", "")
        if purl:
            return e.get("url", "") == purl
        return e.get("title", "") == ptitle

    rest = [e for e in ev if not _is_primary(e)]
    strong = [e for e in rest if e.get("score", 0) >= E.PRIMARY_THRESHOLD]
    reference = [e for e in rest if e.get("score", 0) < E.PRIMARY_THRESHOLD]
    repealed = [e for e in ev if e.get("validity") == "repealed"]
    pending = [e for e in ev if e.get("validity") == "pending"]

    spec = A.QUESTION_TYPES[plan["type"]["type"]]
    parts = plan.get("unanswered", [])

    return {
        "question": plan["question"],
        "type": plan["type"],
        "must_answer": plan["must_answer"],
        # 用户点名文件时的说明（没点名或已取到原文则为空）
        "cited_note": cited_note,
        # 主依据：位阶最高、时效可用那条
        "primary": {
            "title": primary.get("title", ""),
            "rank_label": primary.get("rank_label", ""),
            "validity_label": primary.get("validity_label", ""),
            "document_number": primary.get("document_number", ""),
            "score": primary.get("score", 0),
            "url": primary.get("url", ""),
            "why": primary.get("_why", ""),
        },
        # 可作依据的，其余可以用来交叉验证
        "supporting": [{"title": e.get("title", ""),
                        "rank_label": e.get("rank_label", ""),
                        "validity_label": e.get("validity_label", ""),
                        "score": e.get("score", 0),
                        "url": e.get("url", ""),
                        "basis": e.get("corroborated_by", "")} for e in strong],
        # 只能参考的，答案里要单列，不能和依据混写
        "reference_only": [{"title": e.get("title", ""),
                            "rank_label": e.get("rank_label", ""),
                            "why": e.get("citation_hint", "")}
                           for e in reference],
        "repealed": [e.get("title", "") for e in repealed],
        "pending": [e.get("title", "") for e in pending],
        # 答案里必须写出来的限制条件
        "conditions": parts,
        # 规则陈述类题型：个案轴缺失不追问，但要在答案里留一句适用边界
        "rule_note": plan.get("rule_note", ""),
        # 题面借用了会计确认与计量的地方：税法条文引的是会计结果，准则那一层
        # 五个税收源都不收，取证入口是子技能 chenyiwei-bbs（见 SKILL.md ③）
        "accounting_gap": plan.get("accounting_gap", []),
        # 题面点名的是草案/征求意见稿时，本轮取回的同名文本是现行有效版本，
        # 不是草案内容。这句话命令行与服务端共用，不在两处各说各话。
        "legislative_note": A.legislative_note(plan.get("legislative_stage", [])),
        "probes": plan.get("probes", []),
        # 取数失败的轮次：这一栏非空时，上面各层列出的条数不能读成"库里只有这些"
        "fetch_errors": plan.get("errors", []),
        "fetch_note": (
            "本轮有源取数失败，上面的分层是在缺源的底稿上做的："
            "没列出的文件不等于库里没有，先补取这一轮再下结论"
            if plan.get("errors") else ""),
        "evidence_gap": (
            "没有取到任何现行有效的法定依据，结论只能给方向，不能给具体数额"
            if not strong else ""),
        "counts": {"total": len(ev), "strong": len(strong),
                   "reference": len(reference), "repealed": len(repealed)},
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def _print_plan(plan: dict):
    t = plan["type"]
    print(f"问题：{plan['question']}")
    print(f"类型：{t['label']}（{t['type']}，把握 {t['confidence']}）")
    print(f"  判型依据：{t['reason']}")
    print(f"\n这类题要答到位，需要：")
    for s in plan["sources"]:
        print(f"  - {s}")
    print(f"结论应包含：{plan['must_answer']}")
    print(f"\n检索轮次：")
    for r in plan["rounds"]:
        print(f"  第{r['round']}轮  {r['goal']}")
        print(f"         源 {'/'.join(r['sources'])}")
        print(f"         {r['call']}")
    if plan["unanswered"]:
        print(f"\n题面没交代的前提：{', '.join(plan['unanswered'])}")
    for pr in plan["probes"]:
        print(f"  建议先问：{pr['probe']}")
    _print_accounting_gap(plan.get("accounting_gap", []))
    _print_legislative_note(A.legislative_note(plan.get("legislative_stage", [])))


def _print_legislative_note(note: str):
    """问的是草案时，这一句必须原样进答案：它划清了库的覆盖边界。"""
    if not note:
        return
    print(f"\n立法阶段限制：{note}")


def _print_accounting_gap(gap: list):
    """会计口径缺口：五个税收源都不收会计准则，这一层要交给子技能去取。"""
    if not gap:
        return
    print("\n会计口径缺口（五个税收源都不收准则条文，取证的入口是子技能）：")
    for g in gap:
        print(f"  · 要先问清会计上的哪一件事：{g}")
    print("  · 读 subskills/chenyiwei-bbs/SKILL.md，准则原文与实务答疑都在那里")
    print("  · 准则与答疑不是税收法定依据：只作为前提写进【适用边界】或参考栏")


def _print_gather(plan: dict):
    _print_plan(plan)
    print(f"\n实际取回 {len(plan.get('evidence', []))} 条依据"
          f"（观察时点 {plan.get('at') or '未指定'}）：")
    for e in plan["evidence"][:15]:
        print(f"  {e.get('score', 0):5.1f}分 {e.get('rank_label','')}/"
              f"{e.get('validity_label','')}  {e.get('title','')[:44]}")
    if len(plan["evidence"]) > 15:
        print(f"  … 另有 {len(plan['evidence']) - 15} 条")
    for err in plan.get("errors", []):
        print(f"  ⚠ {err}")


def _print_answer(a: dict):
    p = a["primary"]
    print(f"问题：{a['question']}")
    print(f"类型：{a['type']['label']}")
    print(f"结论要答到位必须有：{a['must_answer']}\n")
    print("【主依据】")
    if p["title"]:
        num = f"（{p['document_number']}）" if p.get("document_number") else ""
        print(f"  {p['title'][:60]}{num}")
        print(f"  {p['rank_label']} / {p['validity_label']} / {p['score']} 分 — {p['why']}")
    else:
        print("  未取到可作主依据的条文")
    if a.get("cited_note"):
        print(f"  ⚠ {a['cited_note']}")
    if a["supporting"]:
        print(f"\n【并列依据】{len(a['supporting'])} 条")
        for s in a["supporting"][:6]:
            print(f"  {s['score']:5.1f} {s['rank_label']}/{s['validity_label']}"
                  f"  {s['title'][:44]}")
            if s.get("basis"):
                print(f"        时效判定依据：被现行有效的《{s['basis']}》"
                      f"列为制定依据（本栏无时效录入）")
        if len(a["supporting"]) > 6:
            print(f"  …另有 {len(a['supporting']) - 6} 条未列出")
    if a["reference_only"]:
        print(f"\n【只能参考，不能当依据】{len(a['reference_only'])} 条")
        for s in a["reference_only"][:5]:
            print(f"  {s['rank_label']}  {s['title'][:44]}")
        if len(a["reference_only"]) > 5:
            print(f"  …另有 {len(a['reference_only']) - 5} 条未列出")
    if a["repealed"]:
        print(f"\n【已废止，仅可用于说明沿革】")
        for s in a["repealed"][:5]:
            print(f"  {s[:52]}")
    if a["pending"]:
        print(f"\n【尚未生效，不能用于当期回答】")
        for s in a["pending"][:5]:
            print(f"  {s[:52]}")
    if a["conditions"]:
        print(f"\n【答案必须写明的限制条件】")
        for c in a["conditions"]:
            print(f"  · 题面没交代「{c}」，结论要标注这一项未定")
    if a.get("rule_note"):
        print(f"\n【适用边界】\n  · {a['rule_note']}")
    _print_accounting_gap(a.get("accounting_gap", []))
    _print_legislative_note(a.get("legislative_note", ""))
    if a["probes"]:
        print(f"\n【建议先向用户确认】")
        for pr in a["probes"]:
            print(f"  · {pr['probe']}")
    if a["evidence_gap"]:
        print(f"\n⚠ {a['evidence_gap']}")
    if a.get("fetch_errors"):
        print(f"\n【取数失败】")
        for e in a["fetch_errors"][:5]:
            print(f"  ⚠ {e[:130]}")
        if len(a["fetch_errors"]) > 5:
            print(f"  …另有 {len(a['fetch_errors']) - 5} 条")
        print(f"  {a['fetch_note']}")
    print(f"\n统计：{a['counts']}")


def main():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(description="税务问题分析编排")
    p.add_argument("question", help="用户原话")
    p.add_argument("--plan", action="store_true", help="只出检索计划，不联网")
    p.add_argument("--gather", action="store_true", help="执行检索并定级")
    p.add_argument("--answer", action="store_true", help="检索并组织依据分层")
    p.add_argument("--at", default="", help="观察时点 YYYY-MM-DD")
    p.add_argument("--no-body", action="store_true", help="不取税屋正文（更快）")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.answer or args.gather:
        plan = gather(args.question, at=args.at, read_body=not args.no_body)
        if args.answer and not args.json:
            _print_answer(compose(plan))
            return
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
            return
        _print_gather(plan)
        return

    plan = build_plan(args.question)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    _print_plan(plan)


if __name__ == "__main__":
    main()
