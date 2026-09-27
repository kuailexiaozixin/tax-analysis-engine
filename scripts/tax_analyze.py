#!/usr/bin/env python3
"""
税务问题分析层 — 把"问的是什么"和"该找哪些依据"分开。

为什么单独一层：检索解决的是"法条在哪"，分析要解决的是"这道题该由哪几类
依据共同作答"。同一个税种下，问"研发费用加计扣除比例是多少"和问"公司今年
研发投入 500 万、没有高新资质、明年才申报，能不能享受"要查的依据不同：
前者一条法一个数字就够，后者要同时过优惠本体法、判定条件、优惠备案或留存
备查要求、以及追征期和滞纳金。把两类问题都当成"找法条"处理，答案必然只
对上一半。

这一层做三件事：
  1. 判定问题类型（QUESTION_TYPES）。类型决定要检索什么、检索几轮。
  2. 识别分析时必须显式处理的前提（CONTEXT_AXES）。同一道题在不同时点、
     不同地区、不同主体下答案不同，不问清就只能猜，猜出来的答案要标出来。
  3. 判定还缺什么信息才敢下结论（MISSING_INFO）。

它不检索、不联网，只做判定。检索交给 tax_search / tax_fgk / tax_shui5 /
tax_wechat / tax_so360，结论由上层按本层的判定组织。

术语与字段的取名原则：类型名用"这道题要产出什么"命名，不用"用户怎么问"
命名。同一个用户问法可能对应不同类型（"研发费用加计扣除 500 万能省多少"是
测算，不是政策查询），反过来同一类型也可能来自不同问法，所以判定同时看
问法信号与产出信号，产出信号优先。

Usage:
  python tax_analyze.py "公司有500万研发费用，没有高新资质，明年才申报，能享受加计扣除吗"
  python tax_analyze.py "增值税小规模纳税人月销售额10万免税吗" --json
"""

import argparse
import json
import re
import sys

# ── 问题类型 ───────────────────────────────────────────────────────────────
# 每类的 needs 写"这类题必须有的依据类别"，ordered=True 表示取数时按顺序找，
# 顺序本身就是答案的推理顺序（先成立，再享受，后计算）。
QUESTION_TYPES = {
    "entitlement": {
        "label": "优惠资格判定",
        "question": "给一组事实，问某项优惠能不能享受",
        "signals": ["能不能", "能否", "是否适用", "符合条件", "可不可以", "够条件",
                    "享受", "享受吗", "能不能享受", "算不算", "属于"],
        "needs": ["优惠本体法", "资格判定条件", "办理时限或留存备查要求"],
        "ordered": True,
        "verdict": "能/不能/部分能 + 卡在哪一条",
    },
    "liability": {
        "label": "税负测算",
        "question": "给一组业务数据，算应纳多少税",
        "signals": ["多少钱", "算一下", "测算", "应纳", "交多少", "税负", "多少税",
                    "税负率", "算下"],
        "needs": ["计税依据与税率", "减免与加计政策", "起征点或小微优惠"],
        "ordered": True,
        "verdict": "金额 + 每一步的算式",
    },
    "procedure": {
        "label": "办理流程与时限",
        "question": "问怎么办、什么时候办、找谁办",
        "signals": ["怎么申报", "怎么办理", "流程", "材料", "在哪", "什么时候",
                    "截止", "期限", "几月", "逾期", "报税", "备案"],
        "needs": ["征管法或办税指南", "对应的实体法依据", "操作口径文件"],
        "ordered": False,
        "verdict": "步骤 + 时限 + 材料",
    },
    "risk": {
        "label": "合规风险判断",
        "question": "问某个做法会不会被查、有什么后果",
        "signals": ["风险", "被查", "处罚", "罚款", "虚开", "合规", "稽查", "举报",
                    "后果", "是否违法", "合规吗"],
        "needs": ["行为定性的法条", "罚则或处罚幅度", "实务口径与稽查口径"],
        "ordered": True,
        "verdict": "定性 + 后果区间 + 整改动作",
    },
    "treatment": {
        "label": "交易与事项定性",
        "question": "问某个业务该按什么税目、该作何处理",
        "signals": ["视同销售", "按什么", "属于什么", "算什么", "如何认定", "定性",
                    "是否缴纳", "要不要交", "该缴"],
        "needs": ["税目定义", "视同或免税列举", "配套征管口径"],
        "ordered": True,
        "verdict": "定性结论 + 援引条款",
    },
    "compare": {
        "label": "方案比较与选择",
        "question": "问两种做法哪个更划算、该选哪个",
        "signals": ["哪个", "哪种", "还是", "更划算", "怎么选", "区别", "比较",
                    "优于", "差别"],
        "needs": ["各方案各自的法律依据", "各自的适用条件", "差异点与临界值"],
        "ordered": False,
        "verdict": "对比表 + 临界条件 + 推荐及理由",
    },
    "fill_blank": {
        "label": "术语填空",
        "question": "句中留空，问该填哪个法定术语或数值",
        "signals": [],                      # 靠形态判定，不靠词
        "needs": ["该术语或数值的定义条款", "所属法律的完整条文"],
        "ordered": False,
        # 填空要的是"原文中哪一句"，所以主依据必须是该法本身，且要能定位到条
        "verdict": "填入的术语 + 出自哪部法哪一条",
        "exhaustive": False,
    },
    "option_judge": {
        "label": "选项正确性判断",
        "question": "给若干条说法，问哪些成立",
        "signals": ["下列", "以下", "说法正确", "表述正确", "正确的有", "错误的有",
                    "说法错误", "表述错误", "符合规定的有", "符合的有", "包括",
                    "属于", "表述中", "说法中", "选项"],
        "needs": ["与题干同一部法的完整条文", "每条说法对应的具体条款"],
        "ordered": False,
        # 逐条比对，所以"漏一条"的代价最大，不能只找前几条命中
        "verdict": "逐条判定 + 每条指出依据条款 + 错在哪",
        "exhaustive": True,
    },
    "lookup": {
        "label": "政策查询",
        "question": "直接问某项规定是什么、比例多少、最新怎么改",
        "signals": ["规定", "政策", "最新", "文件", "多少", "比例", "是什么",
                    "文号", "哪一条"],
        "needs": ["对应的现行有效法条或文件"],
        "ordered": False,
        "verdict": "结论 + 效力位阶 + 文号 + 生效时点",
    },
}

# ── 分析前提 ───────────────────────────────────────────────────────────────
# 这四根轴不问清就没法给唯一答案。题面里出现了就必须显式处理。
CONTEXT_AXES = {
    "time": {
        "label": "时点",
        "why": "政策有生效与失效日期，跨年的同一行为适用不同版本",
        "signals": ["今年", "去年", "明年", "之前", "之后", "何时", "从什么时候",
                    "起", "止", "期间", "年度", "月", "季度", "汇算清缴"],
        "probe": "这笔业务发生在什么时间？涉及哪个纳税年度？",
    },
    "entity": {
        "label": "主体与身份",
        "why": "小规模与一般纳税人、居民与非居民企业、居民与非居民个人，"
               "同一事项适用税率和优惠完全不同",
        "signals": ["小规模", "一般纳税人", "居民企业", "非居民", "个体工商户",
                    "有限公司", "个人", "合伙企业", "纳税人身份"],
        "probe": "纳税主体是谁、是什么身份（小规模/一般纳税人、居民/非居民）？",
    },
    "place": {
        "label": "地区",
        "why": "地方税源、附加税种、扶持政策由省级以下政府定，全国检索答不了",
        "signals": ["本地", "省", "市", "县", "地区", "哪里", "外地", "跨省",
                    "注册地", "经营地"],
        "probe": "纳税人在哪个地区？涉及跨地区经营吗？",
    },
    "scale": {
        "label": "金额与规模",
        "why": "大量优惠带规模门槛（季度销售额、资产、人数、利润），"
               "差一点也不适用",
        "signals": ["万元", "万", "亿", "元", "收入", "销售额", "利润", "资产",
                    "人数", "规模", "金额"],
        "probe": "涉及的金额、收入、资产、人数分别是多少？",
    },
}

# 命中这些信号说明题面自带前提，不必再追问
_AXIS_PRESENT_RE = {
    "time": re.compile(r"\d{4}\s*年|\d+\s*月份?|今年|去年|明年|上半年|下半年|"
                       r"第[一二三四]季度|汇算清缴|年度"),
    # 主体轴要认自然人称谓。题目里写"白某""小王""刘某"时主体已经确定，
    # 只差一个"个人"字面；不认这类称谓会把已答完的主体轴误报成缺失，
    # 逼着问一个用户已经答过的问题。
    "entity": re.compile(r"小规模|一般纳税人|居民企业|非居民|个体工商户|"
                         r"股份有限公司|有限责任公司|合伙企业|"
                         r"[一-龥]{1,2}某|小[一-龥]|[刘张王李赵陈杨黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕]"
                         r"[一-龥]{0,2}(?=[一-龥]{0,6}(?:于|在|将|购买|出租|取得|转让|持有|投资|获得|申报|缴纳|符合|可以|有权|取得))"),
    "place": re.compile(r"[一-龥]{2,4}(省|市|自治区)|北京|上海|广东|浙江|江苏|"
                        r"注册地|经营地"),
    "scale": re.compile(r"\d+(\.\d+)?\s*(万|亿|元)|收入|销售额|利润|资产|人数|规模"),
}

# 追问上限：一次把四条都问一遍会让人不想答，按在题面里的相关度排前 N 条
DEFAULT_MAX_PROBES = 2

# 题面形态标记。"下列…的有（ ）"这类题不问"依据是什么"，只问"哪几条成立"，
# 靠主题词根本认不出来，只能认形态。缺了它，一大批多选题会被误判成政策查询。
_OPTION_FORM_RE = re.compile(
    r"下列|以下"                       # 列举引子
    r"|有\s*[（(]\s*[）)]\s*[。．.]?\s*$"   # 题干以"有（ ）。"收尾
    r"|说法[正误确]|表述[正误确]"         # 成败判断措辞
)

# 填空形态：句中留空。判在选项题之前，因为"…的有（ ）"是填空而
# "下列…正确的有"是选项题，两者要分开走不同的取证方式。
# 允许结尾带句号，"以（ ）作为完税价格。"这类也认。
_FILL_BLANK_RE = re.compile(r"[（(]\s*[）)]")

# 留空前面是这些词时，填的是"若干项/若干条"的集合，答案要逐项列，
# 属于选项判断而不是术语填空。判别依据是动词短语而非有无括号：
# "转让定价方法包括（ ）"填的是方法清单，"完税价格以（ ）作为…"填的是术语。
_ENUM_VERBS = ("包括", "有", "属于", "是", "为", "有哪", "以下")


def classify(question: str) -> dict:
    """判定问题类型。

    产出信号（题面里已经给出要什么结论的措辞）优先于问法信号，因为
    "加计扣除比例是多少" 和 "加计扣除怎么算" 都含"多少"，但前者是
    lookup、后者是 liability。

    Args:
        question: 用户原话。

    Returns:
        {"type","label","confidence","reason","alternatives":[...]}
        confidence 是 0~1 的浮点，表示判定有多硬。
    """
    q = question or ""
    scores = {}
    for name, spec in QUESTION_TYPES.items():
        hits = [s for s in spec["signals"] if s in q]
        if hits:
            scores[name] = len(hits)

    # 形态命中优先于词频：多选题的主题词和普通政策查询几乎一样，
    # 靠"下列/以下/…的有（ ）"这类形态标记才分得开。
    is_option_form = bool(_OPTION_FORM_RE.search(q))
    # 填空形态比"下列…的有"更具体，先判：题里同时出现两者时，
    # "下列不属于印花税应税凭证的有（ ）"是选项题而不是填空——
    # 它有"下列"引子且列的是若干说法。所以填空要求句中没有列举引子。
    has_enum = "下列" in q or "以下" in q
    is_fill_blank = False
    if _FILL_BLANK_RE.search(q) and not has_enum:
        # 留空前是"包括/有/属于"这类集合动词的，答案要逐项列，归选项判断
        prefix = _FILL_BLANK_RE.split(q)[0].rstrip()
        enum_tail = any(prefix.endswith(v) for v in _ENUM_VERBS)
        is_fill_blank = not enum_tail
    if is_fill_blank:
        scores["fill_blank"] = scores.get("fill_blank", 0) + 3

    if is_option_form or not is_fill_blank:
        if is_option_form:
            scores["option_judge"] = scores.get("option_judge", 0) + 2
            # 形态明确时，别让零散的主题词把它拽回 lookup
            if not is_fill_blank:
                scores.pop("lookup", None)

    if not scores:
        # 没命中任何信号：默认按政策查询处理，但要标出这是兜底
        return {
            "type": "lookup",
            "label": QUESTION_TYPES["lookup"]["label"],
            "confidence": 0.3,
            "reason": "题面没有出现任何类型信号词，按政策查询兜底，"
                      "若答非所问要重新判型",
            "alternatives": [],
        }

    # 计分：出现位置越靠后越具体（"……能享受吗"比"享受"更像在问资格），
    # 且同时命中问法词与产出词的类型加权
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_n = ranked[0]
    # 第二名分数接近第一名时判定不稳，把第二名带出来
    alternatives = [r[0] for r in ranked[1:] if r[1] >= max(1, best_n - 1)]

    total_hits = sum(scores.values())
    confidence = min(0.95, 0.55 + 0.1 * best_n)
    if not alternatives and total_hits == best_n:
        confidence = min(0.95, confidence + 0.1)
    if len(alternatives) >= 2:
        confidence = max(0.4, confidence - 0.15)

    return {
        "type": best,
        "label": QUESTION_TYPES[best]["label"],
        "confidence": round(confidence, 2),
        "reason": f"命中 {scores[best]} 个该类信号词"
                  + (f"；{', '.join(alternatives)} 分数接近，判型不稳"
                     if alternatives else ""),
        "alternatives": alternatives,
    }


def detect_context_gaps(question: str) -> dict:
    """找出题面里没交代、但会影响答案的前提。

    只报"轴"不报"值"——值只能问用户，模块从题面里取不到答案。

    Args:
        question: 用户原话。

    Returns:
        {"present": [轴名], "missing": [轴名], "probes": [追问句], "blocking": bool}
        blocking 为 True 表示至少有一条轴缺失且该轴会改变结论。
    """
    q = question or ""
    present, missing = [], []
    for axis in CONTEXT_AXES:
        if _AXIS_PRESENT_RE[axis].search(q) or any(
                s in q for s in CONTEXT_AXES[axis]["signals"]):
            present.append(axis)
        else:
            missing.append(axis)

    # 只有会改结论的轴才值得追问：时点、主体、地区、规模各自都可能翻转答案
    probes = [CONTEXT_AXES[a]["probe"] for a in missing]
    return {
        "present": present,
        "missing": missing,
        "probes": probes,
        "blocking": bool(missing),
    }


def pick_probes(question: str, limit: int = DEFAULT_MAX_PROBES) -> list:
    """挑出最该追问的若干条，一次最多 limit 条。

    排序依据：该轴的信号词在题面里出现得越接近句尾越像"要算这件事"，
    且规模/主体两类在数值型问题里几乎必然缺失，优先问。

    Returns:
        [{"axis","label","why","probe"}]
    """
    gaps = detect_context_gaps(question)
    q = question or ""
    scored = []
    for axis in gaps["missing"]:
        spec = CONTEXT_AXES[axis]
        # 越靠后出现的信号越具体，分越高
        tail_bonus = max([q.rfind(s) / max(len(q), 1) for s in spec["signals"]
                          if s in q] or [0.0])
        scored.append((axis, 0.6 * tail_bonus, spec))
    scored.sort(key=lambda t: -t[1])
    return [{"axis": a, "label": s["label"], "why": s["why"], "probe": s["probe"]}
            for a, _, s in scored[:limit]]


def analyze(question: str, max_probes: int = DEFAULT_MAX_PROBES) -> dict:
    """做一次完整判定：类型 + 前提缺口 + 该查什么。

    Returns:
        {"question","type","context","probes","sources","must_answer","unanswered"}
    """
    t = classify(question)
    gaps = detect_context_gaps(question)
    spec = QUESTION_TYPES[t["type"]]
    return {
        "question": question,
        "type": t,
        "context": gaps,
        "probes": pick_probes(question, limit=max_probes),
        "sources": spec["needs"],
        "must_answer": spec["verdict"],
        "ordered": spec["ordered"],
        # 题面里明确缺了前提时，这一项就是答案里必须写明的限制条件
        "unanswered": [CONTEXT_AXES[a]["label"] for a in gaps["missing"]],
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def _fmt(probe: dict) -> str:
    return f"  · {probe['label']}：{probe['probe']}\n      为什么要问：{probe['why']}"


def main():
    p = argparse.ArgumentParser(description="税务问题类型判定与前提识别")
    p.add_argument("question", help="用户原话")
    p.add_argument("--probes", type=int, default=DEFAULT_MAX_PROBES,
                   help="最多追问几条，默认 2")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    r = analyze(args.question, max_probes=args.probes)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    t = r["type"]
    print(f"问题：{r['question']}\n")
    print(f"类型：{t['label']}（{t['type']}，把握 {t['confidence']}）")
    print(f"  依据：{t['reason']}")
    if t["alternatives"]:
        print(f"  判型不稳的备选：{', '.join(t['alternatives'])}")
    print(f"  问法：{QUESTION_TYPES[t['type']]['question']}")
    print(f"\n这类题要答到位，需要：")
    for s in r["sources"]:
        print(f"  - {s}")
    print(f"\n结论应包含：{r['must_answer']}")
    print(f"\n题面已交代的前提：{', '.join(r['context']['present']) or '无'}")
    print(f"题面没交代的前提：{', '.join(r['unanswered']) or '无'}")
    if r["probes"]:
        print(f"\n建议先问清楚这 {len(r['probes'])} 条再作答：")
        for pr in r["probes"]:
            print(_fmt(pr))
    else:
        print("\n前提齐了，可以直接作答。")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
