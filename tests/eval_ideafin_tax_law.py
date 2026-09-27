#!/usr/bin/env python3
"""
用 IDEAFinBench 的 tax_law val 子集评测检索质量。

数据集来源：https://github.com/DataArcTech/IDEAFinBench
  datasets/cpa_one/val/tax_law_val.csv   单选 279 题
  datasets/cpa_multi/val/tax_law_val.csv 多选 186 题
题面是纯税法判断题，答案由给定选项决定，不需要模型推理。

本脚本评的不是"能不能答对题"，而是**答这题需要的依据能不能被检索到**：
把题面里的税种与专题关键词提出来，喂给对应的源，看依据文件是否排在前面。
检索不到依据的题，系统只能靠记忆作答——那正是幻觉的高发区。

依据源按 TAX_TYPE_KEYWORDS 里的 authority 字段分派：
  authority="npc"  查 NPC 标题检索，判定本体法排到第几位
  authority="sta"  查税务总局，判定前 N 条里有没有标题含核心词、
                   且落在总局法规库（fgk.chinatax.gov.cn）的文件。
                   这类专题（转让定价、税收协定、行政处罚等）在 NPC 库里根本
                   没有对应法律，搜"反避税"返回 0 条，硬查只会得出错误结论。

两个指标：
  top1_hit   依据排在首位的比例（用户第一眼看到的就是对的）
  found      依据出现在前 N 条内的比例（N 默认 3）

用法：
  python tests/eval_ideafin_tax_law.py --data-dir ../eval_data
  python tests/eval_ideafin_tax_law.py --data-dir ../eval_data --topn 5 --json out.json
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import tax_search as T
import tax_fgk as FGK

# 题面常见表述 → TAX_TYPE_KEYWORDS 里的键。顺序即匹配优先级：
# 先匹配更具体的税种，否则"土地增值税"会先被"增值税"吃掉，
# "城市维护建设税"会先撞上"增值税"，"烟叶税"会先撞上"消费税"。
SURFACE_FORMS = [
    ("城镇土地使用税", ["城镇土地使用税", "土地使用税"]),
    ("土地增值税", ["土地增值税", "土增税", "土地増值税"]),
    ("城市维护建设税", ["城市维护建设税", "城建税", "教育费附加", "地方教育附加"]),
    ("车辆购置税", ["车辆购置税"]),
    ("个人所得税", ["个人所得税", "个税"]),
    ("企业所得税", ["企业所得税"]),
    ("烟叶税", ["烟叶税", "烟叶"]),
    ("消费税", ["消费税"]),
    ("房产税", ["房产税"]),
    ("契税", ["契税"]),
    ("印花税", ["印花税"]),
    ("车船税", ["车船税", "车船使用税"]),
    ("环境保护税", ["环境保护税", "环保税"]),
    ("资源税", ["资源税"]),
    ("关税", ["关税"]),
    # 税收征管：与下面的行政处罚/救济/担保并列，同一题里只取最具体的一档
    ("税收征管", ["税收征管", "税收征收管理", "税务登记", "纳税申报", "发票管理",
                  "发票", "税务稽查", "账簿", "凭证", "保存期限", "多缴税款", "退还",
                  "征收方式", "税务检查", "查账", "核定"]),
    ("纳税担保与信用", ["纳税担保", "纳税保证人", "纳税信用", "失信主体"]),
    ("税收争议救济", ["行政复议", "行政诉讼", "起诉期限", "复议前置", "纳税争议"]),
    ("税务行政处罚", ["税务行政处罚", "行政处罚", "听证", "裁量权", "罚款", "滞纳金",
                      "违法", "违规", "失信"]),
    ("转让定价", ["转让定价", "关联交易", "同期资料", "预约定价", "资本弱化",
                  "成本分摊", "国别报告", "受控外国企业"]),
    ("反避税", ["反避税", "BEPS", "税基侵蚀", "避税"]),
    ("税收抵免", ["税收抵免", "抵免限额", "抵免额", "分国不分项", "国别抵免"]),
    ("受控外国企业", ["受控外国企业", "外国企业股息", "视同股息分配"]),
    ("常设机构", ["常设机构", "营业场所", "固定场所"]),
    ("非居民企业", ["非居民企业", "非居民", "源泉扣缴", "预提所得税", "支付所得"]),
    ("境外所得", ["境外所得", "境外投资", "境外股息", "递延纳税"]),
    ("税收协定", ["税收协定", "双重征税", "税收居民身份", "税收条约",
                  "中新协定", "协定待遇"]),
    ("税收优惠", ["税收优惠", "减免税", "退税", "即征即退", "先征后退", "免税"]),
]

# authority="sta" 的专题在税务总局侧判定命中用的核心词。
# 用它而不是整个 key："国际税收"这类 key 本身不是任何一份文件的标题用词。
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
}

# 总局站排序不总把依据文件排最前，判命中前要多取一些再截断
STA_FETCH = 8


def extract_tax_hints(question: str) -> list:
    """从题面提取涉及的税种与专题，按 SURFACE_FORMS 声明的顺序去重。"""
    q = question or ""
    hits = []
    for key, forms in SURFACE_FORMS:
        if any(f in q for f in forms):
            hits.append(key)
    return hits


def check_one(question: str, topn: int, pause: float) -> dict:
    """对一道题涉及的每个专题按其 authority 分派到对应源检索。"""
    keys = extract_tax_hints(question)
    out = {"hints": keys, "checks": []}
    for k in keys:
        info = T.TAX_TYPE_KEYWORDS.get(k) or {}
        authority = info.get("authority", "npc")
        c = {"key": k, "authority": authority}
        try:
            if authority == "sta":
                term = info.get("search_term") or k
                c["term"] = term
                # 要取 8 条再判命中：总局的排序不总是把依据文件排在最前，
                # 只取 3 条会把"找到过"误判成"没找到"。反避税的依据
                # 《特别纳税调整实施办法》就排在第 4 位。
                res = FGK.search_fgk(term, size=STA_FETCH)
                items = res.get("results", [])
                c["parent"] = ""
                core = STA_CORE_TERM.get(k, [k])
                # 命中 = 排序后的前 topn 条里，标题含核心词的条目排到第几位
                hit_rank = next(
                    (i for i, it in enumerate(items)
                     if any(t in (it.get("title") or "") for t in core)),
                    -1)
                c["rank"] = hit_rank
                c["n"] = len(items)
                c["titles"] = [it.get("title", "") for it in items]
            else:
                parent = info.get("parent_law") or k
                res = T.search_tax(k, scope="title", search_type=2, status=3, size=20)
                titles = [r["title"] for r in res.get("results", [])]
                c["parent"] = parent
                bare = parent.replace("中华人民共和国", "")
                c["rank"] = next((i for i, t in enumerate(titles)
                                  if t.replace("中华人民共和国", "") == bare), -1)
                c["n"] = len(titles)
                c["titles"] = titles[:topn]
        except Exception as e:                      # 网络抖动不应中断整轮评测
            c.update({"parent": info.get("parent_law") or "", "error": str(e),
                      "rank": -1, "n": 0, "titles": []})
        out["checks"].append(c)
        time.sleep(pause)
    return out


def main():
    p = argparse.ArgumentParser(description="IDEAFinBench tax_law 检索质量评测")
    p.add_argument("--data-dir", default="../eval_data",
                   help="存放 *_tax_law_val.csv 的目录")
    p.add_argument("--topn", type=int, default=3, help="found 指标的 N")
    p.add_argument("--limit", type=int, default=0, help="只跑前 N 题，0=全跑")
    p.add_argument("--pause", type=float, default=0.8, help="每次检索后的间隔秒")
    p.add_argument("--json", default="", help="把逐题结果写到该文件")
    args = p.parse_args()

    d = Path(args.data_dir)
    files = sorted(d.glob("*_tax_law_val.csv"))
    if not files:
        print(f"❌ 在 {d.resolve()} 找不到 *_tax_law_val.csv")
        print("   下载：https://github.com/DataArcTech/IDEAFinBench "
              "→ datasets/cpa_one/val/tax_law_val.csv 与 cpa_multi/val/tax_law_val.csv")
        return 1

    records = []
    for f in files:
        subset = "cpa_one" if "cpa_one" in f.name else "cpa_multi"
        for row in csv.DictReader(open(f, encoding="utf-8-sig")):
            records.append((subset, row))

    if args.limit:
        records = records[:args.limit]

    print(f"题目总数 {len(records)}（来自 {len(files)} 个子集）")
    print("判定：依据在首条=通过；落在前 "
          f"{args.topn} 条内=找到")
    print("  npc 类查本体法排名；sta 类查前 "
          f"{args.topn} 条里有没有总局法规库文件\n")

    rows = []
    total_checks = top1 = found = errored = nohint = 0
    per_subset = Counter()
    per_tax = {}
    per_auth = Counter()

    for i, (subset, row) in enumerate(records, 1):
        out = check_one(row["question"], args.topn, args.pause)
        if not out["hints"]:
            nohint += 1
        rows.append({"subset": subset, "id": row.get("id", ""),
                     "question": row["question"], "answer": row.get("answer", ""),
                     **out})
        for c in out["checks"]:
            total_checks += 1
            per_subset[subset] += 1
            per_auth[c["authority"]] += 1
            slot = per_tax.setdefault(c["key"], {"n": 0, "top1": 0, "found": 0})
            slot["n"] += 1
            if c.get("error"):
                errored += 1
                continue
            if c["rank"] == 0:
                top1 += 1
                slot["top1"] += 1
            if 0 <= c["rank"] < args.topn:
                found += 1
                slot["found"] += 1
        if i % 25 == 0:
            print(f"  …已跑 {i}/{len(records)}")

    print(f"\n{'=' * 62}")
    print(f"题目 {len(records)}，其中提不出任何税种/专题关键词 {nohint} 题")
    print(f"检索次数 {total_checks}（npc {per_auth['npc']} / sta "
          f"{per_auth['sta']}），其中报错 {errored}")
    if total_checks:
        print(f"top1_hit  {top1}/{total_checks} = {top1 / total_checks:.1%}"
              f"   （依据排在首条）")
        print(f"found@{args.topn}   {found}/{total_checks} = "
              f"{found / total_checks:.1%}   （依据在前 {args.topn} 条内）")
    print("=" * 62)

    print("\n分税种/专题：")
    for k in sorted(per_tax, key=lambda x: (per_tax[x]["top1"] / per_tax[x]["n"], x)):
        s = per_tax[k]
        auth = (T.TAX_TYPE_KEYWORDS.get(k) or {}).get("authority", "npc")
        print(f"  {k:<12s} {auth:<4s} n={s['n']:<4d} "
              f"top1={s['top1'] / s['n']:.0%}  found@{args.topn}={s['found'] / s['n']:.0%}")

    bad = [r for r in rows for c in r["checks"]
           if not c.get("error") and (c["rank"] < 0 or c["rank"] >= args.topn)]
    if bad:
        print(f"\n未命中 {len(bad)} 处，前 12 条：")
        for r in bad[:12]:
            for c in r["checks"]:
                if c.get("error") or 0 <= c["rank"] < args.topn:
                    continue
                want = f"「{c['parent']}」" if c["parent"] else "总局法规库文件"
                print(f"  [{c['key']}/{c['authority']}] rank={c['rank']} 期望{want}")
                print(f"      实得前3: {' | '.join(c['titles'][:3])}")
                print(f"      题面: {r['question'][:60]}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n逐题结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
