#!/usr/bin/env python3
"""
依据定级 — 给检索到的每条依据打上效力位阶、时效状态和可用性判断。

为什么需要这一层：五个源检索回来的东西不是一个量级的凭据。NPC 里的
《企业所得税法》是全国人大立的法，总局的一纸公告是部门规范性文件，税屋的
一篇文章是第三方解读。三者排在一起当"依据"用，读者分不出哪句能拿去和
税务机关据理力争、哪句只是行业参考。把它们混成一堆贴进答案，是把检索
结果直接当分析结论的通病。

同一层内部还要看时效。政策有生效和失效日期，同一问题在 2023 年和 2026 年
答案可能完全不同；拿一份已废止的公告去回答 2026 年的问题，比不回答更糟，
因为它看起来像个答案。

这一层做四件事：
  1. 按效力位阶定级（LEGAL_RANK）：宪法 > 法律 > 行政法规 > 部门规章 >
     规范性文件 > 地方性文件 > 技术性口径 > 实务解读。冲突时高位阶优先。
  2. 判时效（effective_at）：给定观察时点，判定每条依据在该时点是现行有效、
     尚未生效、已废止还是查不到时效。
  3. 给可引用性打分（authority_score）：位阶与时效的合成值，用于排序与
     提示哪些依据只能当参考。
  4. 合并去重与冲突提示：同一件事有多层依据时，把位阶最高的那条挑出来当主依据。

它不做判断题，只给判断提供刻度。真正的判断由上层按 tax_analyze 判出的
问题类型组织。

Usage:
  python tax_evidence.py --rank "中华人民共和国企业所得税法"
  python tax_evidence.py --rank "国家税务总局公告2018年第28号" --at 2026-09-27
"""

import re

# ── 效力位阶 ───────────────────────────────────────────────────────────────
# 数字越大效力越高。分档依据是《立法法》确立的位阶：宪法、法律、行政法规、
# 部门规章、地方性法规，再往下是规范性文件与技术口径。
# 不在同一序列里的两类依据不能靠数字比大小，_NOT_COMPARABLE 记着这件事。
LEGAL_RANK = {
    "constitution": 100,      # 宪法
    "law": 90,                # 法律（全国人大及其常委会）
    "admin_regulation": 80,   # 行政法规（国务院）
    "department_rule": 70,    # 部门规章（总局、财政部等部委以令形式发布）
    "normative": 55,          # 规范性文件（总局公告、通知、批复，不以令发布）
    "local_regulation": 50,   # 地方性法规与地方政府规章
    "local_normative": 40,    # 地方税务规范性文件
    "technical": 25,          # 技术性口径：解读、指南、答复口径
    "interpretation": 10,    # 实务解读：第三方文章、公众号
    "unknown": 30,
}
RANK_LABEL = {
    "constitution": "宪法",
    "law": "法律",
    "admin_regulation": "行政法规",
    "department_rule": "部门规章",
    "normative": "规范性文件",
    "local_regulation": "地方性法规",
    "local_normative": "地方规范性文件",
    "technical": "技术性口径",
    "interpretation": "实务解读",
    "unknown": "未定性",
}

# 位阶判定：先按标题形态的强特征，再按发布机关。
# 顺序有讲究——"暂行"条例、"实施条例"这类形态比机关名更能定级，
# 所以形态规则排在机关规则前面。
_RANK_RULES = (
    # 宪法
    (r"^中华人民共和国宪法", "constitution"),
    # 法律：全国人大及其常委会决定，"法"结尾且非其他形态。
    # 结尾允许修饰语，因为法条名常带"（草案）""（修订）"等尾巴
    (r"^中华人民共和国[一-龥]{2,15}法(?:（[^）]*）)?\s*$", "law"),
    # 行政法规：国务院以"条例/实施条例/暂行条例"命名。
    # "实施细则"是国务院实施细则或部门实施细则，落在部门规章一档更常见，
    # 但形态上与部门办法同级，故先归部门规章。
    (r"^中华人民共和国.*?(?:暂行|实施)?条例\s*$", "admin_regulation"),
    (r"^中华人民共和国.*?实施细则\s*$", "department_rule"),
    (r"^[一-龥]{2,10}(?:暂行)?条例\s*$", "admin_regulation"),
    # 部门规章：部委以"令"发布的办法/规程/实施细则
    (r"^中华人民共和国[一-龥]{2,12}(?:办法|规程)\s*$", "department_rule"),
    # 规范性文件：部委的公告/通知/批复/函/意见/决定。
    # 部委名可能多到四字（国家税务总局）或带空格（财政部 国家税务总局），
    # 所以部委名段用"非空且不超过 12 字"来兜，不要写死部委清单。
    (r"^国家税务总局(?:公告)?\s*(?:第)?\d{4}\s*年?\s*第?\s*\d*\s*号", "normative"),
    (r"^国家税务总局[一-龥]{0,40}?(?:公告|通知|批复|函|意见|决定|令)", "normative"),
    (r"^(?:财政部|国家发展改革委|商务部|海关总署|国家统计局|国家外汇管理局)"
     r"[^。]{0,60}?(?:公告|通知|批复|函|意见|决定)", "normative"),
    # 地方
    (r"^[一-龥]{2,10}(?:省|自治区|市|自治州|地区|盟)[一-龥]{0,8}?"
     r"(?:人民政府)?(?:暂行)?(?:条例|办法|规定)\s*$", "local_regulation"),
    (r"^[一-龥]{2,10}省[一-龥]{0,6}税务局[^。]{0,20}$", "local_normative"),
    (r"^[一-龥]{2,10}市[一-龥]{0,6}税务局[^。]{0,20}$", "local_normative"),
    # 实务解读
    (r"^解读[:：]|^关于.*的?解读\s*$", "technical"),
    (r"^(?:解读|答问|问答|实务|案例|操作指引|办税指南|指南|辅导)", "technical"),
    (r"政策(?:辅导)?指引|执行指引", "technical"),
)
_COMPILED_RANK_RULES = tuple((re.compile(p), r) for p, r in _RANK_RULES)


def rank_of(title: str, category: str = "", source: str = "") -> dict:
    """判定一条依据的效力位阶。

    Args:
        title: 法规名或文章标题。
        category: NPC 库返回的法律性质字段（flxz），如"法律""行政法规"。
            有它时优先用，接口给的分类比自己按标题猜准。
        source: 来源标识，用来给"实务解读"兜底。

    Returns:
        {"rank","label","score","by"}，by 说明是按什么判出来的。
    """
    title = (title or "").strip()
    cat = (category or "").strip()

    if cat:
        for key, label in (("宪法", "constitution"), ("法律", "law"),
                           ("行政法规", "admin_regulation"),
                           ("部门规章", "department_rule"),
                           ("地方性法规", "local_regulation"),
                           ("地方政府规章", "local_regulation"),
                           ("法律性质", "")):
            if key in cat:
                if label:
                    return _mk(label, f"NPC 法律性质字段「{cat}」")
                break

    for pat, key in _COMPILED_RANK_RULES:
        if pat.search(title):
            return _mk(key, f"标题形态匹配「{title[:24]}」")

    if source in ("shui5.cn", "mp.weixin.qq.com") or "解读" in title:
        return _mk("interpretation", "来源或标题指向实务解读")

    return _mk("unknown", "标题形态与来源都不足以定级")


def _mk(key: str, by: str) -> dict:
    return {"rank": key, "label": RANK_LABEL[key],
            "score": LEGAL_RANK[key], "by": by}


# ── 时效 ───────────────────────────────────────────────────────────────────
# 三个态：现行有效 / 尚未生效 / 已废止。查不到时效的必须显式标出来，
# 因为"不知道还生效没有"和"确认生效"不能混为一谈。
VALIDITY = {
    "effective": "现行有效",
    "pending": "尚未生效",
    "repealed": "已废止",
    "unknown": "时效未标明",
}


def judge_validity(item: dict, at: str = "") -> dict:
    """按观察时点判定一条依据的时效。

    Args:
        item: 含 status / status_code / effective_date / publish_date 之一的字典。
        at: 观察时点 YYYY-MM-DD，空串表示不判生效区间，只看状态字段。

    Returns:
        {"validity","label","as_of","note"}。as_of 为空表示没做时点判定。
    """
    at = (at or "").strip()
    status = (item.get("status") or "").strip()
    code = item.get("status_code")

    # NPC 的 sxx 码：3=有效，1=尚未生效，9=已废止（以 SXX_MAP 文字为准）
    if "有效" in status and "尚未" not in status:
        base = "effective"
    elif "尚未生效" in status or "未生效" in status:
        base = "pending"
    elif any(k in status for k in ("废止", "失效", "被废止")):
        base = "repealed"
    elif code == 9:
        base = "repealed"
    else:
        base = "unknown"

    if at and base in ("effective", "pending"):
        eff = (item.get("effective_date") or "").strip()
        if eff and eff > at:
            return {"validity": "pending", "label": VALIDITY["pending"],
                    "as_of": at, "note": f"生效日 {eff} 晚于观察时点 {at}"}
        return {"validity": "effective", "label": VALIDITY["effective"],
                "as_of": at, "note": f"按状态字段判定，效力期间含 {at}"}

    return {"validity": base, "label": VALIDITY[base], "as_of": at,
            "note": "无状态字段可判" if base == "unknown" else "按状态字段判定"}


# ── 合成打分 ───────────────────────────────────────────────────────────────
# 位阶与时效的合成。位阶是主项，时效是减项：一份高位阶但已废止的法律，
# 对今天的问题价值低于一份现行有效的部门规章。
def authority_score(rank: str, validity: str) -> float:
    """把位阶与时效合成 0~100 的可引用性分。

    纯位阶最多 90 分（法律），时效扣分把已废止压到该位阶的一半以下，
    未知时效扣 10 分——宁可标出来让人自己判断，也不要当作确定有效。
    """
    base = LEGAL_RANK.get(rank, LEGAL_RANK["unknown"])
    factor = {"effective": 1.0, "pending": 0.7, "unknown": 0.78,
              "repealed": 0.35}[validity]
    return round(base * factor, 1)


# 能当主依据的最低分。低于它只能当参考材料，不能当结论支撑。
PRIMARY_THRESHOLD = 60.0


def grade(item: dict, at: str = "") -> dict:
    """给一条检索结果打完整定级。

    Args:
        item: 检索结果字典，至少含 title，可含 category/source/status/
            effective_date/publish_date/url。
        at: 观察时点 YYYY-MM-DD。

    Returns:
        原字段 + rank/label/score/validity/citation_hint 三组。
    """
    rank = rank_of(item.get("title", ""), item.get("category", ""),
                   item.get("source", ""))
    val = judge_validity(item, at)
    score = authority_score(rank["rank"], val["validity"])
    out = dict(item)
    out["rank"] = rank["rank"]
    out["rank_label"] = rank["label"]
    out["rank_by"] = rank["by"]
    out["validity"] = val["validity"]
    out["validity_label"] = val["label"]
    out["validity_note"] = val["note"]
    out["score"] = score
    out["citation_hint"] = _hint(rank, val, score)
    return out


def _hint(rank: dict, val: dict, score: float) -> str:
    """一句人话，说明这条依据该以什么身份引用。"""
    if val["validity"] == "repealed":
        return ("已废止，不能作为结论依据；只可用于说明政策沿革，"
                "且要写明原施行期间")
    if val["validity"] == "pending":
        return "尚未生效，只能用于说明将来规则，不能用于回答当期问题"
    if val["validity"] == "unknown" and rank["rank"] in ("technical", "interpretation"):
        return "参考材料，非法定依据；引用时要与法定依据分开列"
    if score >= 80:
        return f"可作主依据（{rank['label']}，效力最高一层）"
    if score >= PRIMARY_THRESHOLD:
        return f"可作主依据（{rank['label']}）"
    return "只能作参考材料，不足以单独支撑结论"


def pick_primary(graded: list) -> dict:
    """从一组已定级的依据里挑主依据。

    规则：先按可引用性分降序；同分取位阶更高的；再同取来源更权威的
    （NPC 库 > 总局 > 税屋/公众号）。返回的 dict 带 _why 说明为什么选它，
    以及 _runners_up 记下其余候选，供答案里做依据分层展示。
    """
    if not graded:
        return {"_why": "没有任何依据", "_runners_up": []}

    def sort_key(g):
        return (g.get("score", 0),
                LEGAL_RANK.get(g.get("rank", "unknown"), 0))

    ordered = sorted(graded, key=sort_key, reverse=True)
    best = dict(ordered[0])
    best["_why"] = (f"{best.get('rank_label','未定性')}、"
                    f"{best.get('validity_label','时效未标明')}，"
                    f"可引用性 {best.get('score',0)} 分，为本组最高")
    best["_runners_up"] = [
        {"title": g.get("title", ""), "rank_label": g.get("rank_label", ""),
         "validity_label": g.get("validity_label", ""), "score": g.get("score", 0)}
        for g in ordered[1:]
    ]
    return best


def grade_all(items: list, at: str = "") -> list:
    """批量定级，按可引用性分降序返回。"""
    return sorted((grade(i, at) for i in items),
                  key=lambda g: g.get("score", 0), reverse=True)


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    import argparse
    import sys

    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(description="依据效力位阶与时效定级")
    p.add_argument("--rank", help="法规名或标题，判效力位阶")
    p.add_argument("--at", default="", help="观察时点 YYYY-MM-DD")
    args = p.parse_args()

    if args.rank:
        r = rank_of(args.rank)
        v = judge_validity({}, args.at)
        print(f"标题：{args.rank}")
        print(f"位阶：{r['label']}（{r['score']} 分）— {r['by']}")
        print(f"时效：{v['label']}（观察时点 {args.at or '未指定'}）")
        print(f"可引用性：{authority_score(r['rank'], v['validity'])} 分")
        return

    p.print_help()


if __name__ == "__main__":
    main()
