#!/usr/bin/env python3
"""
在统一评测集上测检索质量：答一题所需的依据能不能被检索到。

评测集由 tests/build_eval_set.py 从公开财税题库归并而来（构成见 SKILL.md）。
这里评的不是"能不能答对题"，而是**答这题需要的依据能不能被检索到**：
从题面提出税种与专题，喂给对应的依据源，看依据文件排到第几位。
检索不到依据的题，系统只能靠记忆作答——那正是幻觉的高发区。

按专题分派源（依据 TAX_TYPE_KEYWORDS 的 authority 字段）：
  authority="npc"  查全国人大法规库标题检索，判本体法排到第几位
  authority="sta"  查税务总局法规库，判前 N 条里有没有标题含核心词的条目。
                   转让定价、税收协定、税务行政处罚这类专题在人大库里没有
                   对应法律，硬查只会得出"检索不到"的错误结论。
  authority="overseas"
                   查税务总局网站全站层（关掉文件类标签），判前 N 条里有没有
                   标题点到这件事的境外立法动态。支柱二这一类在人大库和文件类
                   标签下都是 0 条，按那两路探会把"这一类本就没有境内文件"
                   写成检索缺陷。

探针按专题键缓存，一题一探针改成一键一探针：评测集的题面反复命中同几个
专题，不缓存的话 1003 题要打两千多次请求，人大接口在这个量级必现限流。

三个指标：
  top1_hit      依据排在首位的专题数占比
  found@N       依据落在前 N 条内的专题数占比（N 默认 3）
  covered@N     一题涉及的**每个**专题都取到依据的题数占比

用法：
  python tests/eval_retrieval.py                          # 只跑现行有效的题
  python tests/eval_retrieval.py --validity all --json out.json
  python tests/eval_retrieval.py --source financeiq --topn 5
"""
import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

# 默认评测集写在技能目录内，不跟工作目录走：原来那个 "../eval_data/…" 的相对
# 默认值只有从 tests/ 里发命令才成立，从仓库根发就指向别处。
EVAL_SET = SCRIPT_DIR.parent / "data" / "eval" / "tax_eval_set.jsonl"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import tax_search as T
import tax_fgk as FGK
import tax_web_search as W

# 题面常见表述 → TAX_TYPE_KEYWORDS 里的键。
#
# 顺序就是优先级，而且是**有副作用的顺序**：extract_hints 匹配到一个键后会把
# 它的表面词从题面里遮蔽掉，再匹配后面的键。所以笼统的键必须排在具体的后面，
# 否则"土地增值税"里的"增值税"会被重复计一次，一题冒出两个不相干的依据。
# "增值税"排在全部税种的最后就是这个缘故。
#
# 这张表必须跟着 TAX_TYPE_KEYWORDS 长：漏键不会报错，只会把缺口藏起来——
# 探针数一少，命中率反而更好看。tests/test_eval_set.py 会比对两张表的差集。
SURFACE_FORMS = [
    ("城镇土地使用税", ["城镇土地使用税", "土地使用税"]),
    ("土地增值税", ["土地增值税", "土增税", "土地増值税"]),
    ("城市维护建设税", ["城市维护建设税", "城建税", "教育费附加", "地方教育附加"]),
    ("车辆购置税", ["车辆购置税"]),
    ("个人所得税", ["个人所得税", "个税", "稿酬所得", "劳务报酬", "特许权使用费",
                    "综合所得", "经营所得", "财产转让所得", "利息股息", "偶然所得",
                    "专项附加", "全年一次性奖金"]),
    ("企业所得税", ["企业所得税", "不征税收入", "亏损弥补", "资产损失", "扣除限额",
                    "加计扣除", "企业重组", "分立", "合并"]),
    ("烟叶税", ["烟叶税", "烟叶"]),
    ("消费税", ["消费税"]),
    ("房产税", ["房产税"]),
    ("契税", ["契税"]),
    ("印花税", ["印花税"]),
    ("车船税", ["车船税", "车船使用税"]),
    ("船舶吨税", ["船舶吨税", "吨税"]),
    ("耕地占用税", ["耕地占用税", "占用耕地", "耕地"]),
    ("环境保护税", ["环境保护税", "环保税"]),
    ("资源税", ["资源税"]),
    ("关税", ["关税"]),
    ("税收协定", ["税收协定", "双重征税", "双重居民", "国际税收", "税收居民身份",
                  "税收条约", "协定待遇", "缔结关于"]),
    # 支柱二排在建定税收居民身份/常设机构判例的键之前：它的表面词都是专有说法，
    # 不会遮蔽别的键；反过来"BEPS"这个笼统词住在"反避税"里，放在它后面就会被
    # 先遮蔽掉，"BEPS 2.0 支柱二"这类题面就提不出本键。
    ("全球最低税", ["支柱二", "全球最低税", "GloBE", "BEPS2.0", "BEPS 2.0",
                    "低税利润规则", "收入纳入规则", "补足税", "并行方案"]),
    ("非居民企业", ["非居民企业", "非居民", "源泉扣缴", "预提所得税", "支付所得"]),
    ("常设机构", ["常设机构", "营业场所", "固定场所"]),
    ("受控外国企业", ["受控外国企业", "外国企业股息", "视同股息分配"]),
    ("境外所得", ["境外所得", "境外投资", "境外股息", "递延纳税"]),
    ("税收抵免", ["税收抵免", "抵免限额", "抵免额", "分国不分项", "国别抵免"]),
    ("转让定价", ["转让定价", "关联交易", "同期资料", "预约定价", "资本弱化",
                  "成本分摊", "国别报告"]),
    ("反避税", ["反避税", "特别纳税调整", "BEPS", "税基侵蚀", "避税"]),
    ("纳税担保与信用", ["纳税担保", "纳税保证人", "纳税信用", "失信主体",
                        "税收保全", "强制执行"]),
    ("税收争议救济", ["行政复议", "行政诉讼", "起诉期限", "复议前置", "纳税争议"]),
    ("税务行政处罚", ["税务行政处罚", "行政处罚", "听证", "裁量权", "罚款", "滞纳金",
                      "违法", "违规", "失信"]),
    ("税收征管", ["税收征管", "税收征收管理", "税务登记", "纳税申报", "发票管理",
                  "发票", "税务稽查", "账簿", "凭证", "保存期限", "多缴税款", "退还",
                  "征收方式", "税务检查", "查账", "核定", "检举", "举报", "纳税地点",
                  "扣缴义务", "税务代理"]),
    ("税收立法权", ["税收立法", "授权立法", "税收法定", "法律优位", "法律保留",
                    "开征、停征", "税率形式"]),
    ("税收优惠", ["税收优惠", "减免税", "即征即退", "先征后退", "免税", "退税"]),
    # 增值税是最大税种，必须排最后：前面每个含"增值税"的税种键都会先把它遮蔽掉
    ("增值税", ["增值税", "进项税额", "销项税额", "留抵", "免抵退", "简易计税",
                "价外费用", "包装物押金", "视同销售", "一般纳税人", "小规模纳税人"]),
]

# authority="sta" 的专题在税务总局侧判定命中用的核心词。
# 用它而不是整个键名："国际税收"这类键名本身不是任何一份文件的标题用词。
STA_CORE_TERM = {
    "税收协定": ["税收协定", "双重征税", "税收居民身份"],
    "常设机构": ["常设机构"],
    "非居民企业": ["非居民企业", "源泉扣缴", "预提所得税"],
    "境外所得": ["境外所得", "境外投资"],
    "税收抵免": ["税收抵免", "境外所得"],
    "受控外国企业": ["境外所得", "受控外国企业", "特别纳税调整"],
    "转让定价": ["转让定价", "同期资料", "预约定价", "关联交易", "特别纳税调整"],
    "反避税": ["反避税", "特别纳税调整", "转让定价", "预约定价"],
    "税务行政处罚": ["税务行政处罚", "行政处罚", "听证", "裁量权"],
    "税收争议救济": ["行政复议", "行政诉讼", "纳税争议"],
    "纳税担保与信用": ["纳税信用", "纳税缴费信用", "纳税担保", "失信主体"],
    "税收优惠": ["减免税", "退税", "即征即退", "先征后退", "免税", "税收优惠"],
    "税收立法权": ["税收法定", "税收立法", "授权立法"],
}

# 总局站排序不总把依据文件排最前，判命中前要多取几条再截断
STA_FETCH = 8


def extract_hints(question: str) -> list:
    """从题面提取涉及的税种与专题键，按 SURFACE_FORMS 的顺序，命中即遮蔽。

    遮蔽是必需的：不做的话"土地增值税扣除项目"会同时提出土地增值税和增值税
    两个键，评测就会要求系统去查《增值税法》——那不是答这题的依据，
    covered 指标会被这种假缺口压低。
    """
    rest = question or ""
    hits = []
    for key, forms in SURFACE_FORMS:
        matched = [f for f in forms if f in rest]
        if not matched:
            continue
        hits.append(key)
        for f in matched:
            rest = rest.replace(f, "＊")
    return hits


def probe(key: str, topn: int, pause: float) -> dict:
    """查一个专题的依据排在第几位。结果按键缓存，同键只打一次网络。"""
    info = T.TAX_TYPE_KEYWORDS.get(key) or {}
    authority = info.get("authority", "npc")
    c = {"key": key, "authority": authority, "parent": info.get("parent_law") or "",
         "term": "", "rank": -1, "n": 0, "titles": [], "error": ""}
    try:
        if authority == "sta":
            term = info.get("search_term") or key
            c["term"] = term
            # 取 8 条再判命中：总局的排序不总是把依据文件排在最前，只取 3 条
            # 会把"找到过"误判成"没找到"，反避税的依据就排在第 4 位。
            items = FGK.search_fgk(term, size=STA_FETCH).get("results", [])
            core = STA_CORE_TERM.get(key, [key])
            c["rank"] = next((i for i, it in enumerate(items)
                              if any(t in (it.get("title") or "") for t in core)), -1)
            c["n"] = len(items)
            c["titles"] = [it.get("title", "") for it in items]
        elif authority == "overseas":
            # 探针跟着路由走：路由把这一类送到全站层，探针就送同一个点。照 sta
            # 那一路查文件类标签会永远 0 条，报告里写成的却是"这个专题检索不到
            # 依据"，而这一类的境内依据本来就没有——线上要答的是境外辖区的动态。
            term = info.get("search_term") or key
            c["term"] = term
            items = W.search_chinatax(term, size=STA_FETCH,
                                      file_only=False).get("results", [])
            core = [term, "支柱二", "全球最低税", "GloBE"]
            c["rank"] = next((i for i, it in enumerate(items)
                              if any(t in (it.get("title") or "") for t in core)), -1)
            c["n"] = len(items)
            c["titles"] = [it.get("title", "") for it in items]
        else:
            parent = info.get("parent_law") or key
            c["parent"] = parent
            # 与 /api/search 的实际路由保持一致：归类出本体法就用本体法名做
            # 精确检索。用模糊检索测出来的命中是虚的——线上不会那样查。
            titles = [r["title"] for r in
                      T.search_tax(parent, scope="title",
                                   search_type=1 if info.get("parent_law") else 2,
                                   status=3, size=20).get("results", [])]
            bare = parent.replace("中华人民共和国", "")
            c["rank"] = next((i for i, t in enumerate(titles)
                              if t.replace("中华人民共和国", "") == bare), -1)
            c["n"] = len(titles)
            c["titles"] = titles[:topn]
    except Exception as e:                      # 单次网络异常不应中断整轮评测
        c["error"] = f"{type(e).__name__}: {e}"
    time.sleep(pause)
    return c


def load_set(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pct(a, b):
    return f"{a / b:.1%}" if b else "  -  "


def main():
    p = argparse.ArgumentParser(description="统一评测集上的检索质量评测")
    p.add_argument("--set", dest="set_path", default=str(EVAL_SET),
                   help="build_eval_set.py 产出的评测集")
    p.add_argument("--validity", default="ok",
                   help="只跑该时效档：ok / review / stale / all")
    p.add_argument("--source", default="", help="只跑该题库：ideafin / financeiq / fineval")
    p.add_argument("--sample", type=int, default=0, help="抽 N 题跑，0=全跑")
    p.add_argument("--seed", type=int, default=2026, help="抽样种子，换种子可检验结论稳不稳")
    p.add_argument("--topn", type=int, default=3, help="found 指标的 N")
    p.add_argument("--pause", type=float, default=0.8, help="每次探针后的间隔秒")
    p.add_argument("--orphans", type=int, default=12,
                   help="列出的提不出专题的题目数，0=不列")
    p.add_argument("--json", default="", help="把逐题结果写到该文件")
    args = p.parse_args()

    records = load_set(Path(args.set_path))
    if not records:
        print(f"❌ 读不到评测集：{Path(args.set_path).resolve()}")
        print("   先跑 python tests/build_eval_set.py --verify，按它回吐的清单补齐原始题面")
        return 1

    if args.validity != "all":
        records = [r for r in records if r["validity"] == args.validity]
    if args.source:
        records = [r for r in records if r["source"] == args.source]
    if args.sample:
        # 抽样而非取前 N：评测集是按题库连续排的，取前 N 只会抽到 ideafin 一家。
        rnd = random.Random(args.seed)
        records = rnd.sample(records, min(args.sample, len(records)))
    if not records:
        print("❌ 过滤后没有题目，检查 --validity / --source")
        return 1

    keys = sorted({k for r in records for k in extract_hints(r["question"])})
    print(f"题目 {len(records)}，涉及专题 {len(keys)} 个 → 探针 {len(keys)} 次")
    print(f"判据：依据排首条=top1；落在前 {args.topn} 条内=found；"
          f"一题的每个专题都 found=covered\n")

    probes = {}
    for i, k in enumerate(keys, 1):
        probes[k] = probe(k, args.topn, args.pause)
        if i % 10 == 0:
            print(f"  …探针 {i}/{len(keys)}", file=sys.stderr)

    rows = []
    probes_ok = probes_top1 = probes_err = 0
    covered = uncovered = 0
    per_key = {}
    bucket = {}
    by_src = {}
    orphans = []

    for r in records:
        hints = extract_hints(r["question"])
        got = [probes[k] for k in hints]
        hits = [c for c in got if not c["error"] and 0 <= c["rank"] < args.topn]
        errs = [c for c in got if c["error"]]
        full = bool(hints) and len(hits) == len(hints) and not errs
        if not hints:
            uncovered += 1
            orphans.append(r)
        elif full:
            covered += 1
        rows.append({**r, "hints": hints, "checks": got,
                     "all_found": full, "no_hint": not hints})
        for slot in (bucket.setdefault(r["validity"], {"n": 0, "cov": 0, "covable": 0}),
                     by_src.setdefault(r["source"], {"n": 0, "cov": 0, "covable": 0})):
            slot["n"] += 1
            slot["covable"] += int(bool(hints))
            slot["cov"] += int(full)
        for c in got:
            s = per_key.setdefault(c["key"], {"q": 0, "top1": 0, "found": 0, "err": 0})
            s["q"] += 1

    found_probes = 0
    for k, c in probes.items():
        if c["error"]:
            probes_err += 1
            continue
        probes_ok += 1
        if c["rank"] == 0:
            probes_top1 += 1
        if 0 <= c["rank"] < args.topn:
            found_probes += 1
    for k, c in probes.items():
        s = per_key[k]
        ok = not c["error"]
        s["top1"] = s["q"] if ok and c["rank"] == 0 else 0
        s["found"] = s["q"] if ok and 0 <= c["rank"] < args.topn else 0
        s["err"] = s["q"] if c["error"] else 0

    covable = len(records) - uncovered
    print(f"\n{'=' * 66}")
    print(f"一、路由覆盖（专题级）。探针 {len(probes)} 个，报错 {probes_err}。"
          f"\n    上限就是专题数，满格只说明路由表没坏，不代表题目答得对。")
    print(f"  top1_hit   {pct(probes_top1, probes_ok)}   依据排在首条")
    print(f"  found@{args.topn}    {pct(found_probes, probes_ok)}   依据在前 {args.topn} 条内")
    print(f"\n二、题库覆盖（题目级）。这才是随题目变化的指标。")
    print(f"  可归类     {pct(covable, len(records))}   题面能提出税种/专题的题占比")
    print(f"  covered@{args.topn}  {pct(covered, covable)}   可归类的题里全部专题都取到依据")
    print("=" * 66)

    print("\n按题库：")
    for src in sorted(by_src):
        s = by_src[src]
        print(f"  {src:<10s} n={s['n']:<5d} 可归类 {pct(s['covable'], s['n'])}"
              f"  covered@{args.topn}={pct(s['cov'], s['covable'])}")

    print("\n按时效档：")
    for v in ("ok", "review", "stale"):
        s = bucket.get(v)
        if s:
            print(f"  {v:<7s} n={s['n']:<5d} 可归类 {s['covable']:<5d} "
                  f"covered@{args.topn}={pct(s['cov'], s['covable'])}")

    print("\n按专题探针（found@%d 从低到高）：" % args.topn)
    for k in sorted(per_key, key=lambda x: (per_key[x]["found"] / max(per_key[x]["q"], 1), x)):
        s = per_key[k]
        c = probes[k]
        want = c["parent"] or (c["term"] or k)
        flag = "报错" if c["error"] else (f"rank={c['rank']}" if c["rank"] >= 0 else "未取到")
        print(f"  {k:<12s} {c['authority']:<4s} 题数={s['q']:<4d} "
              f"top1={pct(s['top1'], s['q'])} found@{args.topn}={pct(s['found'], s['q'])}  "
              f"{flag}  期望依据：{want}")
        if c["error"] or not (0 <= c["rank"] < args.topn):
            print(f"      实得：{' | '.join(t[:38] for t in c['titles'][:3])}")

    if orphans:
        print(f"\n提不出税种/专题的 {len(orphans)} 题——分类表碰不到它们，"
              f"这些题的检索只能靠题面原话。")
        for r in orphans[:args.orphans] if args.orphans else []:
            print(f"  [{r['source']}/{r['validity']}] {r['question'][:64]}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n逐题结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
