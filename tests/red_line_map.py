"""⑦ 禁止行为清单的归属登记（离线，不联网）。

SKILL.md 的 ⑦ 只全文放着跨层红线；层专属的那几条把正文搬到了所属步骤那一节或对应的
`references/` 段落，⑦ 留一行指针，指针末尾的 `【⑦ 第 N 条正文】` 就是正文那一格的锚。
本模块把"搬没搬丢、指针指没指对"变成可判的：编号、锚、目的地三者要互相对上。

给用例用的出口：
    `problems(skill=…, refs=…)`  违约清单，空表才算合格；两份文本可替换，变异自检就靠这个
    `holder(n)`                  第 n 条正文现在住在哪个文件的哪一节
    `body(n)`                    那一格的正文原文（层专属用例引自己那一条时用）
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "SKILL.md"
REF_DIR = ROOT / "references"
TOTAL = 27                      # ⑦ 现有条数；加一条红线要同时改这里

ANCHOR = "【⑦ 第 {n} 条正文】"
POINTER = re.compile(
    r"正文见\s*(?:(?P<sec>[①-⑳])|(?P<file>references/[\w_]+\.md)\s*"
    r"「(?P<h2>[^」]+)」)[^【]*【\s*⑦\s*第\s*(?P<n>\d+)\s*条正文\s*】")
TITLE_MIN = 6                   # 指针前段的标题短到这个字数就不算标题，防止只剩「正文见」


def norm(s: str) -> str:
    return re.sub(r"[\s*]+", "", s)


def flex(s: str) -> str:
    """让一段文字在比对时容得下换行与加粗记号。"""
    return r"[\s*]*".join(re.escape(c) for c in norm(s))


def read_refs() -> dict:
    return {"references/" + p.name: p.read_text(encoding="utf-8")
            for p in sorted(REF_DIR.glob("*.md"))}


def section7(skill: str) -> str:
    return skill.split("\n## ⑦ ")[1].split("\n## ")[0]


SEC7 = re.compile(r"(?m)^## ⑦ [^\n]*\n(.*?)(?=^## |\Z)", re.S)


def mask7(skill: str) -> str:
    """把 ⑦ 那一节的正文抹成等长空格、位置不动。指针行末尾也带 `【⑦ 第 N 条正文】`，
    那是索引不是正文格；数锚、认锚都属于本节的判据要先把这一节排除。"""
    m = SEC7.search(skill)
    if not m:
        return skill
    s, e = m.span(1)
    return skill[:s] + re.sub(r"[^\n]", " ", skill[s:e]) + skill[e:]


def entries(skill: str) -> list:
    """⑦ 的每一条：(编号, 整条文字, 是不是指针行)。"""
    body = section7(skill)
    ms = list(re.finditer(r"(?m)^(\d+)\. (.*)$", body))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(body)
        chunk = body[m.start(2):end]
        out.append((int(m.group(1)), chunk, "正文见" in chunk))
    return out


def headings(src: str) -> list:
    """`## ` 小节的位置表；代码围栏里的 `## ` 是模板正文，不算小节。"""
    out, inside, pos = [], False, 0
    for line in src.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("```"):
            inside = not inside
        elif not inside and s.startswith("## "):
            out.append((pos, s[3:].strip()))
        pos += len(line)
    return out


def heading_at(src: str, pos: int) -> str:
    head = ""
    for start, title in headings(src):
        if start < pos:
            head = title
    return head


def anchor_span(src: str, n: int):
    """锚在 src 里的位置，以及锚后面那一格（到本段结束）。"""
    hit = [(m.start(), m.end()) for m in re.compile(flex(ANCHOR.format(n=n))).finditer(src)]
    if not hit:
        return None
    start, end = hit[0]
    rest = src[end:]
    cut = rest.find("。\n")
    return (start, end, rest if cut < 0 else rest[:cut + 1]), len(hit)


def corpus(skill: str = None, refs: dict = None) -> dict:
    refs = refs if refs is not None else read_refs()
    skill = skill if skill is not None else SKILL.read_text(encoding="utf-8")
    out = {"SKILL.md": skill}
    out.update(refs)
    return out


def anchor_sources(skill: str = None, refs: dict = None) -> dict:
    """认锚用的文本表：SKILL.md 换成抹掉 ⑦ 正文的版本，指针行末尾那个锚不计入。"""
    skill = skill if skill is not None else SKILL.read_text(encoding="utf-8")
    refs = refs if refs is not None else read_refs()
    srcs = corpus(skill, refs)
    srcs["SKILL.md"] = mask7(skill)
    return srcs


def title_of(chunk: str) -> str:
    """⑦ 那一行（或全文那一条）的标题，指针行在「：正文见」处截断。"""
    return norm(re.split(r"：\s*正文见", chunk, maxsplit=1)[0])


def holders(skill: str = None, refs: dict = None) -> dict:
    """编号 → (文件名, 小节名, 正文那一格)。⑦ 里全文留着的小节名就是「⑦ 禁止行为清单」。"""
    skill = skill if skill is not None else SKILL.read_text(encoding="utf-8")
    srcs = anchor_sources(skill, refs)
    out = {}
    for n, chunk, sunk in entries(skill):
        if not sunk:
            out[n] = ("SKILL.md", "⑦ 禁止行为清单", norm(chunk))
            continue
        ptr = POINTER.search(chunk)
        if ptr is None:
            out[n] = ("?", "?", "")
            continue
        name = "SKILL.md" if ptr.group("sec") else ptr.group("file")
        got = anchor_span(srcs.get(name, ""), n)
        if got is None:
            out[n] = (name, "?", "")
            continue
        out[n] = (name, heading_at(srcs[name], got[0][0]), got[0][2])
    return out


def body(n: int) -> str:
    """第 n 条的正文原文。层专属用例引自己那一条时用它，不必再认 ⑦ 里那一行指针。"""
    return holders()[n][2]


def holder(n: int) -> tuple:
    return holders()[n][:2]


def problems(skill: str = None, refs: dict = None) -> list:
    """全部判据的违约清单。空表才算通过。文本可替换，变异自检用得到。"""
    skill = skill if skill is not None else SKILL.read_text(encoding="utf-8")
    refs = refs if refs is not None else read_refs()
    srcs = corpus(skill, refs)
    asrc = anchor_sources(skill, refs)
    flat = {k: norm(v) for k, v in srcs.items()}
    out = []
    nums = [n for n, _, _ in entries(skill)]
    if nums != list(range(1, len(nums) + 1)):
        out.append(f"⑦ 的红线编号不连续：{nums}")
    if len(nums) != TOTAL:
        out.append(f"⑦ 现有 {len(nums)} 条，登记的条数是 {TOTAL}——加红线要同时改 "
                   f"`red_line_map.TOTAL`，否则少掉一条没人发现")
    for n, chunk, sunk in entries(skill):
        title = re.split(r"[：。]", norm(chunk))[0]
        if not title.startswith("禁止"):
            out.append(f"第 {n} 条不是一条禁止：{title[:20]}")
        if not sunk:
            if re.search(flex(ANCHOR.format(n=n)), asrc["SKILL.md"]):
                out.append(f"第 {n} 条正文在 ⑦，却又在别处立了锚")
            where = [(k, flat[k].count(norm(chunk))) for k in flat
                     if norm(chunk) in flat[k]]
            if sum(c for _, c in where) != 1:
                out.append(f"第 {n} 条正文出现 {sum(c for _, c in where)} 次（{where}）")
            continue
        ptr = POINTER.search(chunk)
        if ptr is None:
            out.append(f"第 {n} 条写了「正文见」却没写成可解析的指针"
                       f"（③ 这种圈码，或 references/x.md「节」＋【⑦ 第 {n} 条正文】）")
            continue
        name = "SKILL.md" if ptr.group("sec") else ptr.group("file")
        if name not in asrc:
            out.append(f"第 {n} 条指针指向 {name}，这个文件不存在")
            continue
        got = anchor_span(asrc[name], n)
        if got is None:
            out.append(f"第 {n} 条指针说正文在 {name}，那里没有 {ANCHOR.format(n=n)}")
            continue
        (start, _end, block), hits = got
        if hits > 1:
            out.append(f"{name} 里有 {hits} 个 {ANCHOR.format(n=n)}，正文不知道认哪个")
        # ⑦ 那一行把原标题逐字留在指针前段，正文那一格必须以同一个标题起头；
        # 标题在、正文被删空时，指针尾下会接上别的段落，这一条就报红。
        if len(title_of(chunk)) < TITLE_MIN:
            out.append(f"第 {n} 条指针前段的标题不足 6 字：{title_of(chunk)}")
        if not norm(block).startswith(title_of(chunk)):
            out.append(f"第 {n} 条指针说标题是「{title_of(chunk)}」，"
                       f"锚后面那一格却以『{norm(block)[:20]}』起头")
        sec = heading_at(asrc[name], start)
        want = ptr.group("sec") or ptr.group("h2")
        if not sec.startswith(want):
            out.append(f"第 {n} 条指针说在「{want}」，锚实际落在『{sec}』（{name}）")
        total = sum(flat[k].count(norm(block)) for k in flat)
        if total != 1:
            other = [k for k in flat if norm(block) in flat[k]]
            out.append(f"第 {n} 条的正文逐字出现 {total} 次（{other}）"
                       f"——抄了两份就会各改各的")
    for n in orphan_anchors(skill, refs):
        out.append(f"第 {n} 条被立了锚，⑦ 里却没有对应指针——正文搬走了，清单上那一行丢了")
    return out


def orphan_anchors(skill: str = None, refs: dict = None) -> list:
    """被立了锚、但 ⑦ 里没有对应指针的正文格（搬过去忘了在 ⑦ 留行）。"""
    skill = skill if skill is not None else SKILL.read_text(encoding="utf-8")
    refs = refs if refs is not None else read_refs()
    pointed = set()
    for n, chunk, sunk in entries(skill):
        ptr = POINTER.search(chunk) if sunk else None
        if ptr:
            pointed.add(int(ptr.group("n")))
    seen = set()
    for src in anchor_sources(skill, refs).values():
        for m in re.finditer(r"【⑦\s*第\s*(\d+)\s*条正文】", src):
            seen.add(int(m.group(1)))
    return sorted(seen - pointed)
