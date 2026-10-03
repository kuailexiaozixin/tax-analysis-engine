#!/usr/bin/env python3
"""答题正确率评测 — 让模型在环作答，逐题与标准答案比对打分。

为什么要有第三个评测：这套技能的目的是对税务问题作出可判定的分析结论，
不是把法条捞出来。前两个评测各自回答了一个次要问题——
  eval_retrieval.py  正确的那部法有没有排进前三名（检索命中）
  eval_analysis.py   判型、主依据层级、限制条件处理得对不对（分析质量）
它们都不看最终答案。一个"检索全满分、答案全答错"的实现是完全可能的：
主依据选对了《契税法》，但把"转移土地承包经营权不征契税"判成了征收。
所以本脚本把评测对象换成模型交付的那份答案本身。

三项判定，逐题独立：
  严格正确    选项集合与标准答案完全相等（多选漏一个也算错，与考试同口径）
  部分分      多选按 F1 计，用来区分"方向对但没答全"和"完全答反"
  错法分解    错选（选了标准答案外的）／漏选（少了标准答案里的）／拒答

两个对照组（--arms）：
  evidence  题面＋选项＋本技能检索到的政策依据（含主依据正文条文）
  blind     题面＋选项，不给任何检索材料
两组的差值才是这套检索与分析链条的净贡献。只有 evidence 组数字时，
无法判断答对是因为依据取得好，还是因为模型本来就会做这道题。

时效档必须分开报：161 道 stale、100 道 review 题的现行法可能已变，
把它们混进总正确率里，答错会被算成能力缺陷，实际是题目本身过期。

Usage:
  python tests/eval_answer.py --sample 12                 # evidence 组跑 12 题
  python tests/eval_answer.py --sample 12 --arms evidence,blind
  python tests/eval_answer.py --rescore                   # 只重算分，不再问模型
  python tests/eval_answer.py --sample 40 --workers 6 --json

问模型这一步要花钱，所以它受 scripts/tax_llm.py 的闸门管：默认关死，
开启要同时设 TAX_ENABLE_PAID_LLM=1 和 TAX_LLM_CMD=<模型 CLI 绝对路径>。
真要发起调用前会打印次数并等一句确认（脚本化跑批加 --yes 跳过等待）。
--rescore 只吃缓存，不调模型，不设闸门也能跑。
"""

import argparse
import concurrent.futures as F
import glob
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import tax_answer as AN               # noqa: E402
import tax_detail as DT               # noqa: E402
import tax_fgk as FG                  # noqa: E402
import tax_llm as L                   # noqa: E402

EVAL_DIR = HERE.parent.parent / "eval_data"
EVAL_SET = EVAL_DIR / "tax_eval_set.jsonl"
EVAL_GLOB = str(EVAL_DIR / "*tax_law_val.csv")
RESULTS_DIR = HERE / "results"
CACHE_PATH = RESULTS_DIR / "answer_llm_cache.jsonl"
# 依据块单独落盘：取一次依据要打人大库检索加下载解析全文，
# 而模型侧的调参（改判分口径、改抽样、补跑 blind 组）不该重付这笔开销。
EV_CACHE_PATH = RESULTS_DIR / "answer_evidence_cache.jsonl"

# 选项字母域：三个题库最多到 E，F 以上不存在
LETTER_RE = re.compile(r"[A-E]")


# ── 题库 ────────────────────────────────────────────────────────────────────
def load_questions(validity: str = "ok") -> list:
    """读统一评测集，保留选项与题型档位。

    不能复用 eval_analysis 的取题结果：那份题面只有问句，选项被丢掉了，
    而答题评测的全部对象就是选项。这里独立读，保证两组评测挑到的是同一批题
    （同一 key、同一过滤条件）。
    """
    rows = []
    if EVAL_SET.is_file():
        with EVAL_SET.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if validity != "all" and r.get("validity") != validity:
                    continue
                if not r.get("options"):
                    # 没有独立选项的题（简答/判断）不在这套判分口径内
                    continue
                rows.append({"id": r.get("key", "")[:16], "key": r.get("key", ""),
                             "question": r["question"],
                             "options": r["options"],
                             "answer": r.get("answer", ""),
                             "answer_type": r.get("answer_type", ""),
                             "source": r.get("source", ""),
                             "subset": r.get("subset", ""),
                             "validity": r.get("validity", "")})
    else:
        for f in glob.glob(EVAL_GLOB):
            with open(f, encoding="utf-8-sig") as fh:
                for r in csv_rows(fh):
                    rows.append(r)
    return rows


def csv_rows(fh) -> list:
    """原始 CSV 退回路径：A/B/C/D 四列拼成选项，答案字母数决定单多选。"""
    import csv as _csv
    out = []
    for r in _csv.DictReader(fh):
        opts = {k: (r.get(k) or "").strip() for k in "ABCD" if (r.get(k) or "").strip()}
        ans = (r.get("answer") or "").strip()
        if not opts or not ans:
            continue
        out.append({"id": r.get("id", ""), "key": "csv-" + r.get("id", ""),
                    "question": r.get("question", ""), "options": opts,
                    "answer": ans,
                    "answer_type": "single" if len(LETTER_RE.findall(ans.upper())) < 2
                    else "multiple",
                    "source": "fineval_csv", "subset": "", "validity": "review"})
    return out


def stratified(rows: list, sample: int, seed: int) -> list:
    """按 (题库 × 时效档) 分层取样，轮转抽取。

    纯随机抽样会让 financeiq（488 题，多选占比高）吃掉大半样本，
    ideafin 与 fineval 各只剩一两题，分题库正确率就成了噪声。
    """
    cells = defaultdict(list)
    for r in rows:
        cells[(r["source"], r["validity"])].append(r)
    rnd = random.Random(seed)
    for v in cells.values():
        rnd.shuffle(v)
    picked, exhausted = [], set()
    while len(picked) < sample and len(exhausted) < len(cells):
        for k in sorted(cells):
            if len(picked) >= sample:
                break
            if not cells[k]:
                exhausted.add(k)
                continue
            picked.append(cells[k].pop(0))
    return picked[:sample]


# ── 依据装配 ────────────────────────────────────────────────────────────────
def stem_with_options(item: dict) -> str:
    """题面＋选项拼成分析输入。

    只给问句会把"下列说法正确的有（ ）"判成政策查询：判型靠的是选项句式，
    答题评测必须按用户实际粘贴的完整题目走。
    """
    opts = "；".join(f"{k}.{v}" for k, v in sorted(item["options"].items()))
    return f"{item['question']}　{opts}"


def npc_articles(bbbs_id: str, limit: int = 120) -> list:
    """取 NPC 法的条文段落；取不到返回空表，不抛异常。

    上限取到 120 条而不是 40 条：《关税法》七十余条、《税收征收管理法》
    九十余条，截在前四十条会把后面那些真正决定选项对错的条文剪掉，
    评测就会把"依据没送全"算成"模型答错"。

    走 tax_detail.download_bytes 而不是自己 requests.get：那个版本绕过了
    tax_http 与 NPC 串行闸，评测逐题取依据时会连发几十次，等于在评测里
    自己给自己限流。现在取地址与取文件都在闸内。
    """
    try:
        data = DT.download_bytes(bbbs_id, "docx")
        paras = DT._parse_docx_from_bytes(data)
        arts = [p for p in paras if re.match(r"^第[一二三四五六七八九十百千\d]+条", p)]
        return arts[:limit] or paras[:limit]
    except Exception:
        return []


def evidence_bundle(item: dict, at: str, with_body: bool = True) -> dict:
    """按技能工作流取依据，返回给模型的文本块与检索诊断。"""
    plan = AN.gather(stem_with_options(item), at=at, read_body=False)
    comp = AN.compose(plan)
    ev = plan.get("evidence", [])
    # 法定文件条数（角色为"本题的直接规定"或"上位依据与授权"），不是过阈条数：
    # ⑧ 取消了可引用性分数，实务解读那一层也照常进依据块，这里只数法定那两层。
    statutory = [e for e in ev if e.get("role") in ("direct", "superior")]

    lines, bodies = [], {}
    for e in ev[:8]:
        head = (f"- {e.get('rank_label', '?')}｜{e.get('validity_label', '?')}｜"
                f"{e.get('title', '')}"
                + (f"（{e.get('publish_date')} 公布）" if e.get("publish_date") else ""))
        lines.append(head)
        if not with_body:
            continue
        # 只给法律/法规层正文：位阶够高、篇幅可控。规范性文件走 fgk 正文。
        if e.get("rank") in ("law", "admin_regulation") and e.get("id"):
            arts = npc_articles(e["id"])
            if arts:
                bodies[e["title"]] = arts
        elif e.get("url", "").startswith("http") and "fgk.chinatax.gov.cn" in e.get("url", ""):
            got = FG.fetch_fgk_body(e["url"])
            if got.get("content"):
                bodies[e["title"]] = [got["content"][:3000]]

    diag = {
        "plan_type": plan["type"]["type"],
        "topic": plan.get("terms", {}).get("topic", ""),
        "parent_law": plan.get("terms", {}).get("parent_law", ""),
        "primary_title": comp["primary"]["title"],
        "primary_rank": comp["primary"]["rank_label"],
        "primary_validity": comp["primary"]["validity_label"],
        "evidence_n": len(ev),
        "statutory_n": len(statutory),
        "gap": comp.get("evidence_gap", ""),
        "conditions": comp.get("conditions", []),
        "body_titles": [k for k in bodies],
    }

    if not lines:
        text = "（本库未检索到相关政策依据）"
    else:
        text = "检索到的政策依据（观察时点 %s）：\n%s" % (at, "\n".join(lines))
        for title, arts in bodies.items():
            text += f"\n\n【{title}】条文：\n" + "\n".join(arts)
    diag["bundle_chars"] = len(text)
    return {"text": text, "diag": diag}


# ── 模型 ────────────────────────────────────────────────────────────────────
# 这一段是全脚本唯一花钱的地方，所以发起调用全部交给 scripts/tax_llm.py 的
# 闸门，这里不再自己拼 npm 全局目录去找哪个 CLI。"探测到就能用"等于替用户
# 决定这批调用记在谁账上——本技能会被别人装走，那种写法不能留。

PROMPT = """你在参加中国税务师职业资格考试，请作答下面这道题。

## 题目
{question}

## 选项
{options}
{evidence}
## 作答要求
1. 单选题选一个，多选题选出所有正确项；本卷不定项，正确项可能只有一个。
2. 只依据你确认成立的现行规定。资料不足时宁可少选，不要凭印象补选。
3. 若确实无法判断，answer 字段留空字符串，并在 reasoning 里说明缺什么前提。
4. 答案必须自己推导，不要引用题目里没有的"标准答案"。

只输出一个 JSON，不要任何其他文字或代码块围栏：
{{"answer": "选项字母连写，例如 D 或 ABD；无法判断则留空", "basis": "你依据的法规或文件名称，没有则填 无", "reasoning": "80 字以内，说明每个选中项为什么对"}}"""


def ask_model(item: dict, bundle_text: str, arm: str, timeout: int,
              retries: int = 1) -> str:
    """经闸门问一次模型。

    重问默认关掉：失败重试是"把上游故障也当成链路抖动"，额度耗尽时三次重试
    全打在同一个没钱的账户上，除了把一次报错变成一批零分答案什么也没修。只有
    本机 CLI 自身的问题（版本不认模型名、路径写错、超时）允许重试，因为这类
    调用根本没打到上游、不产生费用。
    SpendRefused 与 QuotaExhausted 直接向上抛，不落进 `_error`——闸门回绝和
    账户没钱都不是"模型答错了"，混进答案里会让评测分背错锅，还会让上层以为
    该继续跑完这一批。
    """
    opts = "\n".join(f"{k}. {v}" for k, v in sorted(item["options"].items()))
    ev = ("\n## 政策依据（由检索系统提供，可能不完整）\n" + bundle_text + "\n"
          if arm == "evidence" and bundle_text else "\n")
    prompt = PROMPT.format(question=item["question"], options=opts, evidence=ev)

    last = ""
    for attempt in range(max(1, retries)):
        try:
            return L.ask(prompt, timeout)
        except (L.SpendRefused, L.QuotaExhausted):
            raise
        except Exception as e:
            last = str(e)[:260]
            if not L.local_failure(last):
                break
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
    return json.dumps({"_error": f"调用失败：{last}"}, ensure_ascii=False)


CALL_FAILED = "调用失败"


def parse_choice(raw: str) -> dict:
    """从模型输出里取 answer/basis/reasoning。

    三层解析：整段 JSON → "答案：ABD" 这类写法 → 都不成就算没答上。
    刻意**不做**"把文里出现的大写字母拼起来"这种兜底：调用失败时 CLI 会把
    一行英文告警原样打出来，里面的散字母能被拼成任意选项。实测有一批 blind
    组题就是这么被记成"模型答错"的，正确率从满格掉到两成却看不出是链路故障。
    取不到答案就判未答，并且和"模型真的拒答"分档，两类问题的修法不同。
    """
    cleaned = re.sub(r"```(?:json)?|```", "", raw or "").strip()
    out = {"answer": "", "basis": "", "reasoning": "", "parse": "json"}
    for cand in reversed(re.findall(r"\{[^{}]*\}", cleaned, re.S)):
        try:
            d = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if "answer" in d:
            out["answer"] = str(d.get("answer", ""))
            out["basis"] = str(d.get("basis", ""))
            out["reasoning"] = str(d.get("reasoning", ""))
            out["parse"] = "json"
            return out
        if "_error" in d:
            out["parse"] = CALL_FAILED
            out["reasoning"] = str(d.get("_error", ""))[:200]
            return out
    if '"_error"' in cleaned or "is not a model" in cleaned:
        out["parse"] = CALL_FAILED
        out["reasoning"] = cleaned[:200]
        return out
    m = re.search(r"(?:答案|answer)[^\nA-E]{0,8}([A-E]{1,5})", cleaned, re.I)
    if m:
        out["answer"], out["parse"] = m.group(1), "regex"
        out["reasoning"] = cleaned[:120]
    else:
        out["parse"] = "none"
        out["reasoning"] = cleaned[:160]
    return out


# ── 判分 ────────────────────────────────────────────────────────────────────
def letters(s: str) -> list:
    return sorted(set(LETTER_RE.findall((s or "").upper())))


def score_one(item: dict, parsed: dict) -> dict:
    """严格口径：选项集合完全相等才算答对，与考试同口径。

    多选不给"答对一半"的及格分——多选题漏选与错选在真实阅卷里都是 0 分。
    F1 只作为第二档指标，用来区分"没答全"和"答反了"，不参与正确率。
    """
    gold = letters(item["answer"])
    pred = letters(parsed.get("answer"))
    res = {"gold": "".join(gold), "pred": "".join(pred),
           "multi": len(gold) > 1, "exact": False, "f1": 0.0,
           "wrong": [], "missing": [], "refused": not pred,
           "call_failed": parsed.get("parse") == CALL_FAILED,
           "kind": item.get("answer_type", "")}
    if res["call_failed"]:
        # 链路故障不进分母：把它算成答错，等于用网络状态给分析能力打分
        res["verdict"] = CALL_FAILED
        res["refused"] = False
        return res
    if not pred:
        res["verdict"] = "拒答"
        return res
    if not gold:
        res["verdict"] = "无标准答案"
        return res
    inter = set(pred) & set(gold)
    res["exact"] = set(pred) == set(gold)
    res["wrong"] = sorted(set(pred) - set(gold))
    res["missing"] = sorted(set(gold) - set(pred))
    denom = len(pred) + len(gold)
    res["f1"] = round(2 * len(inter) / denom, 3) if denom else 0.0
    if res["exact"]:
        res["verdict"] = "全对"
    elif not res["multi"]:
        # 单选题没有"漏选"可言：选错就是选错，套多选的错法分解会把
        # "看错了干扰项"和"少选了一项"混成同一档，两类问题的修法完全不同。
        res["verdict"] = "答错"
    elif res["wrong"] and res["missing"]:
        res["verdict"] = "错选+漏选"
    elif res["wrong"]:
        res["verdict"] = "错选"
    else:
        res["verdict"] = "漏选"
    return res


# ── 缓存 ────────────────────────────────────────────────────────────────────
def load_cache() -> dict:
    """按 (key, arm) 复用已问过的模型输出，让长跑可断点续跑。

    模型是外部服务、每题耗时以十秒计，重跑一遍等于把上一次的调用作废；
    题面或依据变了会让 key 相同但内容不同，所以缓存里存题面哈希，
    命中后再比哈希，不一致就当没跑过。
    """
    cache = {}
    if CACHE_PATH.is_file():
        with CACHE_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if '"_error"' in (r.get("raw") or ""):
                    # 早期版本把调用失败也缓存了；这类记录一律作废，重跑时重问
                    continue
                cache[(r["key"], r["arm"])] = r
    return cache


def cache_fingerprint(item: dict, bundle_text: str) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(item["question"].encode("utf-8"))
    h.update(json.dumps(item["options"], sort_keys=True, ensure_ascii=False).encode())
    h.update(bundle_text.encode("utf-8"))
    return h.hexdigest()[:16]


def append_cache(rec: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── 依据装配（串行）─────────────────────────────────────────────────────────
def load_ev_cache() -> dict:
    cache = {}
    if EV_CACHE_PATH.is_file():
        with EV_CACHE_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    cache[(r["key"], r["at"], bool(r["with_body"]))] = r
    return cache


def collect_evidence(items: list, at: str, with_body: bool, cache: dict,
                     pause: float = 0.0) -> dict:
    """逐题取依据，**串行**。

    人大接口对并发检索必现限流（SKILL.md ⑩），而模型那一段才是可以并发的。
    所以这里按题面顺序跑，把结果落盘；并发只用在后面的模型调用上。
    限流一旦混进评测，缺口会表现成"依据变少→模型少依据→答错"，
    看着像能力缺陷，其实是自找的。
    """
    out = {}
    for i, item in enumerate(items, 1):
        ck = (item["key"], at, with_body)
        if ck in cache:
            out[item["key"]] = cache[ck]
            continue
        try:
            b = evidence_bundle(item, at=at, with_body=with_body)
        except Exception as e:
            b = {"text": "", "diag": {"plan_type": "error", "parent_law": "",
                                      "primary_title": "", "primary_rank": "",
                                      "primary_validity": "", "evidence_n": 0,
                                      "statutory_n": 0, "gap": f"取据失败：{str(e)[:90]}",
                                      "conditions": [], "body_titles": [],
                                      "bundle_chars": 0}}
        out[item["key"]] = {"key": item["key"], "at": at, "with_body": with_body,
                            **b}
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with EV_CACHE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(out[item["key"]], ensure_ascii=False) + "\n")
        cache[ck] = out[item["key"]]
        print(f"  取据 {i}/{len(items)}：依据 {b['diag']['evidence_n']} 条，"
              f"块 {b['diag']['bundle_chars']} 字", file=sys.stderr, flush=True)
        if pause:
            time.sleep(pause)
    return out


# ── 跑单题 ──────────────────────────────────────────────────────────────────
def bundle_text_for(arm: str, bundle: dict) -> str:
    """evidence 组送依据块，blind 组恒空——这是两组唯一的输入差异。"""
    return (bundle or {}).get("text", "") if arm == "evidence" else ""


def cached_raw(item: dict, arm: str, bundle: dict, cache: dict) -> str:
    """这次跑批这道题要不要花钱：命中缓存返回那份原始回答，否则返回空串。

    指纹含题面＋选项＋依据块，改一个字都不算命中。花费预告按它来数，所以
    这段判断只能有一份——两处各写一遍迟早会数错次数。
    """
    hit = cache.get((item["key"], arm))
    fp = cache_fingerprint(item, bundle_text_for(arm, bundle))
    return hit["raw"] if hit and hit.get("fp") == fp else ""


def run_one(item: dict, arm: str, bundle: dict, timeout: int,
            cache: dict, cache_lock, no_model: bool = False):
    bundle_text = bundle_text_for(arm, bundle)
    diag = (bundle or {}).get("diag", {}) if arm == "evidence" else {
        "plan_type": "", "parent_law": "", "primary_title": "", "primary_rank": "",
        "primary_validity": "", "evidence_n": 0, "statutory_n": 0,
        "gap": "blind 组不给依据", "conditions": [], "body_titles": [],
        "bundle_chars": 0}

    fp = cache_fingerprint(item, bundle_text)
    hit = cached_raw(item, arm, bundle, cache)
    if hit:
        raw, from_cache = hit, True
    elif no_model:
        # --rescore 不调模型：宁可少一题，也不在"我只是想重算分"时偷偷发起外部调用
        return None
    else:
        raw, from_cache = ask_model(item, bundle_text, arm, timeout), False

    parsed = parse_choice(raw)
    res = score_one(item, parsed)
    out = {"key": item["key"], "id": item["id"], "arm": arm,
           "source": item["source"], "subset": item["subset"],
           "validity": item["validity"], "question": item["question"],
           "options": item["options"], "answer_type": item.get("answer_type", ""),
           "model": parsed, "score": res, "diag": diag,
           "health": basis_health(diag) if arm == "evidence" else "",
           "from_cache": from_cache, "raw_head": raw[:240]}
    if not from_cache and parsed["parse"] != CALL_FAILED:
        # 失败输出不落缓存：下一次跑同一题必须真的重问，而不是把故障永久固化成答案
        with cache_lock:
            append_cache({"key": item["key"], "arm": arm, "fp": fp, "raw": raw,
                          "at": (bundle or {}).get("at", ""),
                          "with_body": (bundle or {}).get("with_body", False),
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            cache[(item["key"], arm)] = {"key": item["key"], "arm": arm, "fp": fp,
                                         "raw": raw}
    return out


# ── 汇总 ────────────────────────────────────────────────────────────────────
def paired(rows: list) -> dict:
    """同题两组的交叉表：依据把哪些题从错变对、又有哪些从对变错。

    两组各自正确率一样时，"净贡献为零"这个结论本身还不够——要分清是
    "依据没改变任何一题"（链路白建），还是"改变了一批题但对错相抵"
    （依据在起作用，但取到的东西有对有错）。这两种情况要修的地方完全不同。

    只配两组都真正拿到模型回答的题。调用失败的题在分母里被 agg 剔除了，
    这里若按"两组都有行"来配，一条失败的 blind 回答会被当成"blind 答错"，
    于是每一道 evidence 答对的题都记成"依据救回来的"。实测有一批 100 题的
    blind 组 78 题调用失败，交叉表照样报出 gain=70、harm=0，读起来像依据有
    决定性作用，真相是上游配额耗尽。所以 `n_both`（真配上的题数）与
    `n_unpaired`（配不上：一组调用失败，或 --rescore 时那一组无缓存被跳过）
    分开报——配对覆盖不足时 gain/harm 一律不作结论。
    """
    by_key = defaultdict(dict)
    for r in rows:
        by_key[r["key"]][r["arm"]] = r
    two_arm = any(len(v) == 2 for v in by_key.values())

    def answered(v: dict) -> bool:
        return not v["score"].get("call_failed")

    both = {k: v for k, v in by_key.items()
            if "evidence" in v and "blind" in v
            and answered(v["evidence"]) and answered(v["blind"])}
    # 单组批次（只跑 evidence）没有对照意图，不报覆盖缺口
    n_unpaired = (len(by_key) - len(both)) if two_arm else 0
    gain = [k for k, v in both.items()
            if v["evidence"]["score"]["exact"] and not v["blind"]["score"]["exact"]]
    harm = [k for k, v in both.items()
            if v["blind"]["score"]["exact"] and not v["evidence"]["score"]["exact"]]
    changed = [k for k, v in both.items()
               if v["evidence"]["score"]["pred"] != v["blind"]["score"]["pred"]]
    return {
        "n_both": len(both),
        "n_keys": len(by_key),
        "n_unpaired": n_unpaired,
        "gain": len(gain), "harm": len(harm),
        "choice_changed": len(changed),
        "same_right": sum(1 for k, v in both.items()
                          if v["evidence"]["score"]["exact"]
                          and v["blind"]["score"]["exact"]),
        "same_wrong": sum(1 for k, v in both.items()
                          if not v["evidence"]["score"]["exact"]
                          and not v["blind"]["score"]["exact"]),
        "gain_keys": sorted(gain), "harm_keys": sorted(harm),
        "changed_keys": sorted(changed),
    }


HEALTH_LABEL = {
    "on_topic": "主依据与题面税种相符",
    "off_topic": "取到了无关法规（路由对了但选错法）",
    "unrouted": "未进路由表，检索词是题面原话",
    "no_evidence": "一条依据都没取到",
}


def basis_health(diag: dict) -> str:
    """主依据健康度分档。

    答对率饱和之后，这一档才是能区分技能好坏的量：模型本来就认得选项时，
    把《国际刑事司法协助法》顶成"国际重复征税"的主依据也不会扣分，
    但对一个真问"国际重复征税有哪些类型"的用户，这份答案就是废的。
    判据只用已存的字段，所以旧批次可以原地重算，不必重新检索。
    """
    parent = (diag or {}).get("parent_law") or ""
    prim = (diag or {}).get("primary_title") or ""
    if not (diag or {}).get("evidence_n"):
        return "no_evidence"
    if not parent:
        return "unrouted"
    if parent == prim or parent in prim or (prim and prim in parent):
        return "on_topic"
    return "off_topic"


def agg(rows: list) -> dict:
    failed = [r for r in rows if r["score"].get("call_failed")]
    rows = [r for r in rows if not r["score"].get("call_failed")]
    scored = [r for r in rows if r["score"]["gold"]]
    ok = [r for r in scored if r["score"]["exact"]]
    multi = [r for r in scored if r["score"]["multi"]]
    return {
        "n": len(rows),
        "call_failed": len(failed),
        "scored": len(scored),
        "exact": len(ok),
        "rate": round(len(ok) / len(scored) * 100, 1) if scored else None,
        "refused": sum(1 for r in scored if r["score"]["refused"]),
        "partial_only": sum(1 for r in scored if r["score"]["verdict"] == "漏选"),
        "has_wrong": sum(1 for r in scored if r["score"]["wrong"]),
        "f1_mean": round(sum(r["score"]["f1"] for r in scored) / len(scored), 3)
        if scored else None,
        "multi_f1": round(sum(r["score"]["f1"] for r in multi) / len(multi), 3)
        if multi else None,
    }


def breakdown(rows: list, field: str) -> dict:
    g = defaultdict(list)
    for r in rows:
        g[r[field] or "-"].append(r)
    return {k: agg(v) for k, v in sorted(g.items())}


VERDICT_ICON = {"全对": "✔", "漏选": "△", "错选": "✘", "错选+漏选": "✘",
                "答错": "✘", "拒答": "∅", "无标准答案": "?", CALL_FAILED: "!"}


def print_report(rows: list, args) -> None:
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)

    for arm in sorted(by_arm):
        rs = by_arm[arm]
        s = agg(rs)
        print("=" * 78)
        print(f"答题正确率评测　arm={arm}　样本 {s['n']} 题"
              f"（时效过滤 {args.validity}，观察时点 {args.at}，"
              f"正文 {'取' if args.with_body else '不取'}）")
        print("=" * 78)
        print(f"  严格正确率   {s['exact']}/{s['scored']} = {s['rate']}%")
        if s["call_failed"]:
            print(f"  调用失败     {s['call_failed']} 题未计入分母"
                  f"（去掉 --rescore 重跑会重试这些题）")
        print(f"  部分分(F1)   均值 {s['f1_mean']}（多选 {s['multi_f1']}）")
        print(f"  漏选未答全   {s['partial_only']} 题　含错选 {s['has_wrong']} 题"
              f"　拒答 {s['refused']} 题")
        for field, label in (("source", "分题库"), ("validity", "分时效档"),
                             ("answer_type", "分题型")):
            print(f"\n  ── {label} ──")
            for k, v in breakdown(rs, field).items():
                print(f"    {k:<16s} {v['exact']}/{v['scored']} = {v['rate']}%"
                      f"　F1 {v['f1_mean']}　错选 {v['has_wrong']}　拒答 {v['refused']}")

    # 同题两组交叉表：净贡献
    pr = paired(rows)
    if pr["n_both"]:
        print("\n  ── 依据的净贡献（同题 evidence × blind）──")
        print(f"    配对题数 {pr['n_both']}　两组同对 {pr['same_right']}　"
              f"同错 {pr['same_wrong']}")
        print(f"    选项被依据改变的 {pr['choice_changed']} 题："
              f"由错转对 {pr['gain']}，由对转错 {pr['harm']}")
        if pr["choice_changed"] == 0:
            print("    选项一个都没变 = 依据块没有参与决策（要么模型本来就会，"
                  "要么依据没送进去），先查 bundle_chars")
    if pr["n_unpaired"]:
        print(f"\n    [警告] 净贡献只覆盖 {pr['n_both']}/{pr['n_keys']} 题："
              f"{pr['n_unpaired']} 题要两组都拿到模型回答才能配对，这些题要么缺另一组"
              "的行、要么那一组调用失败（配额耗尽 / --rescore 无缓存）。"
              "覆盖不足时 gain/harm 不代表净贡献，先补齐缺的那一组："
              "--arms blind 只补缺组，去掉 --rescore 才会真去重试")
    # 依据层级 × 正确率：检索诊断，回答"取到法律层的题是不是更常答对"
    ev = [r for r in rows if r["arm"] == "evidence"]
    if ev:
        print("\n  ── 次级主指标：主依据健康度 × 正确率 ──")
        g = defaultdict(list)
        for r in ev:
            g[basis_health(r["diag"])].append(r)
        for k in ("on_topic", "off_topic", "unrouted", "no_evidence"):
            if k not in g:
                continue
            a = agg(g[k])
            print(f"    {HEALTH_LABEL[k]:<34s} {len(g[k]):>3} 题"
                  f"（{round(len(g[k]) / len(ev) * 100)}%）"
                  f"　答对 {a['exact']}/{a['scored']} = {a['rate']}%")
        print("\n  ── 诊断：主依据层级 × 正确率 ──")
        g = defaultdict(list)
        for r in ev:
            g[(r["diag"].get("primary_rank") or "无")].append(r)
        for k, v in sorted(g.items()):
            a = agg(v)
            print(f"    主依据={k:<12s} {a['exact']}/{a['scored']} = {a['rate']}%"
                  f"　F1 {a['f1_mean']}")

    # 逐题对照表
    for arm in sorted(by_arm):
        print("\n" + "─" * 78)
        print(f"逐题对照　arm={arm}")
        print("─" * 78)
        for i, r in enumerate(sorted(by_arm[arm], key=lambda x: (x["source"],
                                                                x["validity"],
                                                                x["id"])), 1):
            sc, mo, dg = r["score"], r["model"], r["diag"]
            icon = VERDICT_ICON.get(sc["verdict"], "?")
            head = (f"【{i}】{icon} {sc['verdict']}　{r['source']}"
                    f"{('/' + r['subset']) if r['subset'] else ''}"
                    f"·{r['validity']}·{r['answer_type'] or '?'}"
                    f"　标准答案 {sc['gold'] or '—'}｜模型 {sc['pred'] or '（空）'}")
            print(head)
            print(f"  题面：{r['question'][:78]}")
            for k, v in sorted(r["options"].items()):
                mark = "◀对" if k in sc["gold"] else ""
                pick = "←选" if k in sc["pred"] else ""
                tag = " ".join(x for x in (mark, pick) if x)
                print(f"    {k}. {v[:52]}" + (f"　[{tag}]" if tag else ""))
            if sc["multi"] and (sc["wrong"] or sc["missing"]):
                print(f"  错选 {','.join(sc['wrong']) or '—'}"
                      f"　漏选 {','.join(sc['missing']) or '—'}"
                      f"　F1 {sc['f1']}")
            print(f"  模型依据：{mo.get('basis', '')[:60]}"
                  f"　（解析 {mo.get('parse', '')}"
                  f"{'，缓存' if r['from_cache'] else ''}）")
            print(f"  模型理由：{(mo.get('reasoning') or '').replace(chr(10), ' ')[:120]}")
            if arm == "evidence":
                print(f"  检索诊断：判型 {dg.get('plan_type')}"
                      f"｜主依据健康度 {r.get('health') or basis_health(dg)}"
                      f"｜{dg.get('primary_rank')}·{dg.get('primary_validity')}"
                      f"·{dg.get('primary_title') or '（无）'}"
                      f"｜依据 {dg.get('evidence_n')} 条"
                      f"，其中法定依据 {dg.get('statutory_n', '未记')} 条"
                      f"，带正文 {len(dg.get('body_titles') or [])} 部"
                      f"｜依据块 {dg.get('bundle_chars')} 字")
                if dg.get("gap"):
                    print(f"    依据缺口：{str(dg['gap'])[:110]}")
                if dg.get("conditions"):
                    print(f"    题面未交代：{'、'.join(map(str, dg['conditions']))[:100]}")
            print()


def main():
    p = argparse.ArgumentParser(description="答题正确率评测（LLM 在环）")
    p.add_argument("--sample", type=int, default=12, help="抽多少题，0=全量")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--validity", default="ok",
                   help="ok / review / stale / all（分题库分时效报，混档会失真）")
    p.add_argument("--arms", default="evidence",
                   help="evidence（给检索依据）/ blind（不给）/ evidence,blind 对照")
    p.add_argument("--at", default=time.strftime("%Y-%m-%d"),
                   help="时效观察时点，默认今天")
    p.add_argument("--no-body", dest="with_body", action="store_false",
                   help="不下载主依据全文，只给依据清单（快，但断言少依据）")
    p.set_defaults(with_body=True)
    p.add_argument("--workers", type=int, default=4,
                   help="只作用于模型调用段；取依据段恒为串行")
    p.add_argument("--pause", type=float, default=1.5,
                   help="每题取据后的间隔秒数，缓解人大接口限流")
    p.add_argument("--prep-only", action="store_true",
                   help="只准备依据块并落盘，不调用模型")
    p.add_argument("--timeout", type=int, default=150, help="单次模型调用上限秒数")
    p.add_argument("--json", action="store_true", help="输出机器可读结果")
    p.add_argument("--out", default="", help="结果 JSON 落盘路径")
    p.add_argument("--rescore", action="store_true",
                   help="只用缓存重算分，不调模型（新判分口径下复算）")
    p.add_argument("--yes", action="store_true",
                   help="已经看过花费预告、同意计费（脚本化跑批用）")
    args = p.parse_args()

    pool = load_questions(args.validity)
    if not pool:
        raise SystemExit(f"题库为空：检查 {EVAL_SET} 是否存在、validity 是否写错")
    rows = stratified(pool, args.sample, args.seed) if args.sample else pool
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    import threading
    cache = load_cache()
    lock = threading.Lock()
    results = []
    jobs = [(r, a) for a in arms for r in rows]

    # 阶段一：取依据。串行，因为人大接口并发必限流；阶段二才是并发段。
    bundles = {}
    if "evidence" in arms:
        ev_cache = load_ev_cache()
        todo = [r for r in rows if (r["key"], args.at, args.with_body) not in ev_cache]
        print(f"阶段一 取依据：{len(rows)} 题，其中 {len(todo)} 题需新检索"
              f"（串行，约 {len(todo) * 6 // 60} 分钟）", file=sys.stderr, flush=True)
        bundles = collect_evidence(rows, args.at, args.with_body, ev_cache,
                                   pause=args.pause)
        if args.prep_only:
            print("只备依据（--prep-only），未调用模型，依据块已缓存", file=sys.stderr)
            got = sum(1 for v in bundles.values() if v["diag"]["evidence_n"])
            print(f"已备好 {len(bundles)} 题依据，其中有可用依据 {got} 题",
                  file=sys.stderr)
            return

    # 花费确认排在取依据之后：这时才知道这次真要发起几次调用。预告必须是准数，
    # 所以按 run_one 用的同一套缓存命中判断来数，不按抽样数拍脑袋。
    n_new = 0 if args.rescore else sum(
        1 for r, a in jobs if not cached_raw(r, a, bundles.get(r["key"]), cache))
    if n_new:
        why = L.channel()[1]
        if why:
            raise SystemExit(
                f"阶段二需要 {n_new} 次外部模型调用，付费闸门未开：\n  {why}\n"
                f"  只想在现有缓存上重算分数就加 --rescore，它一次模型都不调。")
        print(L.cost_notice(n_new, arms=",".join(arms)), file=sys.stderr, flush=True)
        if not args.yes:
            try:
                ans = input("  同意计费？输入 yes 继续，其他任意输入退出：").strip().lower()
            except EOFError:
                ans = ""
            if ans != "yes":
                raise SystemExit("  未确认，一次模型都没调。已取到的依据块在缓存里，"
                                 "下次不必重取。")

    t0 = time.time()
    skipped = 0
    print(f"阶段二 问模型：{len(jobs)} 题次，其中新发起 {n_new} 次，"
          f"并发 {args.workers}", file=sys.stderr, flush=True)
    halted = ""
    ex = F.ThreadPoolExecutor(max_workers=max(1, args.workers))
    futs = [ex.submit(run_one, r, a, bundles.get(r["key"]), args.timeout,
                      cache, lock, args.rescore) for r, a in jobs]
    done = 0
    try:
        for fu in F.as_completed(futs):
            try:
                got = fu.result()
            except L.QuotaExhausted as e:
                halted = f"上游额度耗尽：{e}"
                break
            except L.SpendRefused as e:
                halted = f"付费闸门回绝：{e}"
                break
            except Exception as e:
                print(f"  某题失败：{str(e)[:120]}", file=sys.stderr)
                continue
            if got is None:
                skipped += 1
                continue
            results.append(got)
            done += 1
            el = time.time() - t0
            print(f"[{done}/{len(jobs)}] 累计 {el:.0f}s"
                  f"（{el / done:.0f}s/题）", file=sys.stderr, flush=True)
    finally:
        # 一停就把还没开跑的排掉。已经在跑的收不回来，它们会各自撞到同一个额度
        # 错误上——这就是长批次前要先拿一题探通道的原因。
        ex.shutdown(wait=not halted, cancel_futures=bool(halted))
    if halted:
        n_cancel = sum(1 for f in futs if f.cancelled())
        print(f"\n[停止] {halted}\n"
              f"       已完成 {done} 题次，未发起 {n_cancel} 题次。\n"
              f"       给所用账号充值后重跑同一条命令即可：答过的题都在缓存里，"
              f"不会二次计费。", file=sys.stderr, flush=True)
    if skipped:
        print(f"--rescore：缓存里没有 {skipped} 题，已跳过未调模型", file=sys.stderr)

    blob = {"meta": {"sampled": len(rows), "arms": arms, "validity": args.validity,
                     "at": args.at, "with_body": args.with_body,
                     "seed": args.seed, "elapsed_s": round(time.time() - t0, 1),
                     "no_answer_jobs": skipped, "new_model_calls": n_new,
                     "halted": halted,
                     "pool_total": len(pool)},
            "summary": {a: agg([r for r in results if r["arm"] == a]) for a in arms},
            "by_source": {a: breakdown([r for r in results if r["arm"] == a],
                                       "source") for a in arms},
            "by_validity": {a: breakdown([r for r in results if r["arm"] == a],
                                         "validity") for a in arms},
            "paired": paired(results),
            "by_health": breakdown([r for r in results if r["arm"] == "evidence"],
                                   "health"),
            "rows": results}
    text = json.dumps(blob, ensure_ascii=False, indent=2)
    if args.json and not args.out:
        # 只要 JSON 时不再打人类报告：两个格式混在同一个流里，两边都没法直接吃
        print(text)
        return
    if args.out:
        # 文档里的 --out 落在 tests/results/，而那个目录已进 .gitignore、不入库，
        # 新克隆上并不存在。父目录不存在就先建，别让"目录没建"看起来像评测出错。
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}", file=sys.stderr)
    print_report(results, args)
    print(f"\n总耗时 {time.time() - t0:.0f}s，缓存 {CACHE_PATH}")


if __name__ == "__main__":
    main()
