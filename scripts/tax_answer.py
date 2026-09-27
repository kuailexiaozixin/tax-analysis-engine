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
    a["rounds"] = tpl
    return a


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
def _npc_round(term: str, size: int) -> list:
    # 本体法名已知时走精确检索。模糊检索按相关度排，宪法类文本会因为
    # "法"字出现在"企业所得税法"里而被顶到首位（实测"中华人民共和国企业
    # 所得税法"模糊检索首条是宪法），主依据会被选错。
    st = 1 if term.startswith("中华人民共和国") else 2
    r = T.search_tax(term, scope="title", search_type=st, status=3, size=size)
    return r.get("results", []) if not r.get("_error") else []


def _fgk_round(term: str, size: int) -> list:
    r = FGK.search_fgk(term, size=size)
    return r.get("results", []) if not r.get("_error") else []


def _shui5_round(term: str, size: int) -> list:
    r = S5.search_shui5(term, size=size, read_body=True)
    return r.get("results", []) if not r.get("_error") else []


def _wechat_round(term: str, size: int) -> list:
    r = WX.search_wechat(term, size=size)
    return r.get("results", []) if not r.get("_error") else []


_FETCHERS = {"npc": _npc_round, "fgk": _fgk_round,
             "shui5": _shui5_round, "wechat": _wechat_round}


def search_terms(question: str) -> dict:
    """把用户原话拆成各源该用的检索词。

    直接拿整句去检索不行，NPC 标题检索按整段字面匹配，"研发费用加计扣除比例
    是多少"取回来的全是含"费用"二字的无关法规（实测首条是《国家赔偿费用
    管理条例》）。所以每个源用自己的词：法规库认规范名，法规库清单认专题
    检索词，解读源认口语化的短词。

    Returns:
        {"npc": 本体法名或原话, "fgk": 专题检索词, "shui5": 短词,
        "wechat": 短词, "topic": 识别到的专题名, "parent_law": 本体法}
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

    return {
        "npc": parent or short,
        "fgk": (sta_term if info.get("authority") == "sta" else parent or short),
        "shui5": short,
        "wechat": short,
        "topic": topic,
        "parent_law": parent,
        "authority": info.get("authority", "npc"),
    }


_TAIL_RE = None


def _strip_question_tail(question: str) -> str:
    """把问句压成检索用的短词。

    保留主题，去掉"是多少/可以享受吗/怎么办/比例多少"这类询问措辞。
    只做尾部截断，不改中间内容——中间的"加计扣除""小规模"都是检索关键词，
    改掉会丢信息。
    """
    import re
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
    for rnd in plan["rounds"]:
        got_this_round = 0
        for src in rnd["sources"]:
            fn = _FETCHERS[src]
            kw = terms.get(src, question)
            try:
                if src == "shui5" and not read_body:
                    rows = S5.search_shui5(kw, size=1, read_body=False)
                    rows = rows.get("results", [])
                else:
                    rows = fn(kw, sizes[src])
            except Exception as e:                      # 单源失败不中断整轮
                errors.append(f"第{rnd['round']}轮 {src} 失败："
                              f"{str(e).splitlines()[0][:110]}")
                continue

            for row in rows:
                url = row.get("url") or row.get("id") or row.get("title", "")
                if url in seen:
                    continue
                seen.add(url)
                evidence.append(row)
                got_this_round += 1
        rounds_done.append({"round": rnd["round"], "goal": rnd["goal"],
                            "found": got_this_round})

    graded = E.grade_all(evidence, at=at)
    plan["evidence"] = graded
    plan["rounds_done"] = rounds_done
    plan["errors"] = errors
    plan["at"] = at
    plan["terms"] = terms
    plan["gathered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return plan


# ── 组织 ───────────────────────────────────────────────────────────────────
def compose(plan: dict) -> dict:
    """在 gather 底稿上做依据分层与结论骨架，不联网、不编造结论。

    它不替代模型写答案，而是把"哪些能当依据、哪些只能参考、哪些条件没问"
    先算清楚，模型照着组织文字即可。
    """
    ev = plan.get("evidence", [])
    primary = E.pick_primary(ev)
    strong = [e for e in ev if e.get("score", 0) >= E.PRIMARY_THRESHOLD]
    reference = [e for e in ev if e.get("score", 0) < E.PRIMARY_THRESHOLD]
    repealed = [e for e in ev if e.get("validity") == "repealed"]
    pending = [e for e in ev if e.get("validity") == "pending"]

    spec = A.QUESTION_TYPES[plan["type"]["type"]]
    parts = plan.get("unanswered", [])

    return {
        "question": plan["question"],
        "type": plan["type"],
        "must_answer": plan["must_answer"],
        # 主依据：位阶最高、时效可用那条
        "primary": {
            "title": primary.get("title", ""),
            "rank_label": primary.get("rank_label", ""),
            "validity_label": primary.get("validity_label", ""),
            "score": primary.get("score", 0),
            "url": primary.get("url", ""),
            "why": primary.get("_why", ""),
        },
        # 可作依据的，其余可以用来交叉验证
        "supporting": [{"title": e.get("title", ""),
                        "rank_label": e.get("rank_label", ""),
                        "validity_label": e.get("validity_label", ""),
                        "score": e.get("score", 0),
                        "url": e.get("url", "")} for e in strong],
        # 只能参考的，答案里要单列，不能和依据混写
        "reference_only": [{"title": e.get("title", ""),
                            "rank_label": e.get("rank_label", ""),
                            "why": e.get("citation_hint", "")}
                           for e in reference],
        "repealed": [e.get("title", "") for e in repealed],
        "pending": [e.get("title", "") for e in pending],
        # 答案里必须写出来的限制条件
        "conditions": parts,
        "probes": plan.get("probes", []),
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
        print(f"  {p['title'][:60]}")
        print(f"  {p['rank_label']} / {p['validity_label']} / {p['score']} 分 — {p['why']}")
    else:
        print("  未取到可作主依据的条文")
    if a["supporting"]:
        print(f"\n【并列依据】{len(a['supporting'])} 条")
        for s in a["supporting"][:6]:
            print(f"  {s['score']:5.1f} {s['rank_label']}/{s['validity_label']}"
                  f"  {s['title'][:44]}")
    if a["reference_only"]:
        print(f"\n【只能参考，不能当依据】{len(a['reference_only'])} 条")
        for s in a["reference_only"][:5]:
            print(f"  {s['rank_label']}  {s['title'][:44]}")
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
    if a["probes"]:
        print(f"\n【建议先向用户确认】")
        for pr in a["probes"]:
            print(f"  · {pr['probe']}")
    if a["evidence_gap"]:
        print(f"\n⚠ {a['evidence_gap']}")
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
