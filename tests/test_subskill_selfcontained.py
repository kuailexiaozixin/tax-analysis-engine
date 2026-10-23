#!/usr/bin/env python3
"""子技能"自成一体、可单独对外分发"的离线结构用例。不联网、不开浏览器、不调模型。

这一层守的是五件事，全都不问内容对错，只问"这一份文档单独拿出去还站不站得住"：

  1. **两份文档齐全**：每个 `subskills/<名>/` 都有 `SKILL.md` 与 `NOTE.md`，且
     `SKILL.md` 里有 `## 工作流` 那一节。只留一份的目录，读者拿到的是命令清单
     而没有做事的顺序；工作流被改名，母技能那句"照子技能那一节走"就落空。
     `chenyiwei-bbs` 是上游镜像件（须与站点字节一致，见它自己的 NOTE.md「收录来源」），
     结构由上游定，这一项对它豁免，其余四项照查。
  2. **不引用主技能的私有编号**：主技能 `SKILL.md` 那条分析主线有 13 步（①–⑬），
     所以子技能文档里 ⑤ 及以上的圈号只可能是"回 ⑦ 定级""进 ⑧"这种指针——一旦
     子技能被单独分发，收的人手上没有那 13 步，编号就指不到东西。文内自数的
     枚举允许，实测本轮 subskills 里的圈号最大到 ④（chinatax/NOTE.md 的三条与四条
     读法、R&D-Expenses 的三条能力线）；要自数到第五项就改用 `1.` 的阿拉伯
     数字列表。
  3. **路径指针存在**：文档里 `references/…`、`scripts/…`、`tests/…`、`frontend/…`、
     `subskills/…`、`eval/…` 那 134 处指针（本轮实测口径见下面"计数"），先按子技能
     目录解、再按仓库根解。R&D-Expenses 有自己的 `references/`，母技能那一份
     在仓库根，两套都算落地；写成 `scripts/tax_browser.BrowserSession` 那种带符号名
     后缀的形态，去掉尾段再试；带 `*` 的按 glob 至少命中一个。
  4. **「第X步／第X节」指针落地**：53 处（本轮实测）逐个解到真实标记。这个仓库里
     两套编号各有唯一形态——步只在 `SKILL.md` 里以 `**第X步 …**` 定义，节只在
     `NOTE.md` 与 `references/*.md` 里以 `## 一、…` 或 `## 第X节` 定义。指针的目标
     先取紧邻前 40 字里最后一个 `.md` 文件名（`NOTE.md`／`SKILL.md` 裸名指向同目录
     那份，前面写着母技能／主技能则指向仓库根 `SKILL.md`），取不到就在本文与同目录
     另一份里找。标题行与 `**第X步` 定义本身不算指针。
  5. **代码符号可核对**：文档里 66 处 `` `tax_模块.符号` `` 反引号引用，import 得到
     该模块且模块有该属性。子技能说"值域常量在 `tax_web_search.FILE_LABELS`"而那个
     名字已漂走，读者按图索骥会扑空，而这句话在文档里读起来完全正常。

计数口径（2026-10-20 本轮扫）：语料 = `subskills/*/*.md` 共 18 份（9 个目录 × 2 份）；
路径指针 134 处、符号引用 66 处、编号指针 53 处（排除标题行与步定义行后）。
这三枚数在本文件里各有一条用例下限盯着，检查规则一旦漏抓（正则被改窄、语料被改空）
就会报红，而不是安静地少查。

每条检查都自带 `test_mutation_*` 自检：往内存副本里注入对应的破坏（把第七步那句
改写成「按 ⑧ 定级层归档」、把 NOTE 里被「第三节」指到的那节序号换成 `## 柒、`、把
`**第四步` 改名、把脚本名拼错一位、符号名加个 `_v2`），确认判据真的会报红。
判据不报红就等于没有。
"""

import importlib
import re
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
SUBSKILLS = ROOT / "subskills"

# 上游镜像件：结构与措辞由站点定，第 1 项（工作流小节）对它豁免
MIRROR_DOCS = {"chenyiwei-bbs"}

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯"
LOCAL_CIRCLED_MAX = 4            # 文内自数只到 ④；⑤ 起算主技能私有编号
NUM = "一二三四五六七八九十"

PATH_RE = re.compile(r"(?:references|scripts|tests|frontend|subskills|eval)"
                     r"/[A-Za-z0-9_./*-]+[A-Za-z0-9_./*-]")
SYM_RE = re.compile(r"`(tax_[a-z_]+)\.([A-Za-z_][A-Za-z0-9_]*)`")
SEC_RE = re.compile(r"第([" + NUM + r"]+)(步|节)")
MD_RE = re.compile(r"(?:references|tests|frontend|subskills|eval)/[A-Za-z0-9_./*-]+\.md"
                   r"|(?:NOTE|SKILL)\.md")
KNOWN_EXT = (".py", ".md", ".html", ".json", ".js", ".txt", ".csv", ".yaml", ".yml")

MIN_PATH_POINTERS = 100
MIN_SYMBOL_REFS = 40
MIN_SECTION_POINTERS = 40


def docs():
    return sorted(SUBSKILLS.glob("*/*.md"))


def line_no(text, idx):
    return text[:idx].count("\n") + 1


# ── 1 两份文档齐全 ───────────────────────────────────────────

def bundle_gaps(dirs, skill_texts):
    """缺文件与缺「## 工作流」的目录名。skill_texts: {目录名: SKILL.md 文本或 None}。"""
    gaps = []
    for d in dirs:
        if not (d / "SKILL.md").exists():
            gaps.append(f"{d.name}: 缺 SKILL.md")
        if not (d / "NOTE.md").exists():
            gaps.append(f"{d.name}: 缺 NOTE.md")
        if d.name in MIRROR_DOCS:
            continue
        t = skill_texts.get(d.name)
        if t is not None and not re.search(r"(?m)^##\s+.*工作流\s*$", t):
            gaps.append(f"{d.name}: SKILL.md 没有 ## 工作流 这一节")
    return gaps


class TestBundleComplete(unittest.TestCase):
    def test_every_subskill_has_both_docs_and_a_workflow(self):
        dirs = [p for p in SUBSKILLS.iterdir() if p.is_dir()]
        self.assertTrue(dirs, "subskills/ 下一个目录都没有，语料空了")
        texts = {}
        for d in dirs:
            f = d / "SKILL.md"
            texts[d.name] = f.read_text(encoding="utf-8") if f.exists() else None
        self.assertEqual(bundle_gaps(dirs, texts), [])

    def test_mutation_workflow_heading_renamed_is_caught(self):
        dirs = [p for p in SUBSKILLS.iterdir() if p.is_dir()]
        victim = next(d for d in dirs if d.name not in MIRROR_DOCS)
        t = (victim / "SKILL.md").read_text(encoding="utf-8").replace(
            "## 工作流", "## 做事顺序", 1)
        self.assertIn(victim.name, " ".join(
            bundle_gaps(dirs, {d.name: (t if d == victim else
                                        (d / "SKILL.md").read_text(encoding="utf-8"))
                               for d in dirs})))

    def test_mirror_exemption_is_named_and_small(self):
        """豁免名单只在真有必要时用，且被豁免的目录其余四项照查。"""
        self.assertIn("chenyiwei-bbs", MIRROR_DOCS)
        self.assertLessEqual(len(MIRROR_DOCS), 2)
        for name in MIRROR_DOCS:
            self.assertTrue((SUBSKILLS / name / "SKILL.md").exists())


# ── 2 不引用主技能私有编号 ───────────────────────────────────

def parent_number_hits(text):
    """返回 (行号, 圈号) 列表：⑤ 及以上，即主技能那条 13 步线的私有编号。"""
    out = []
    for m in re.finditer("[" + CIRCLED + "]", text):
        if CIRCLED.index(m.group(0)) >= LOCAL_CIRCLED_MAX:
            out.append((line_no(text, m.start()), m.group(0)))
    return out


class TestNoParentStepNumbers(unittest.TestCase):
    def test_subskill_docs_stay_off_the_parent_numbering(self):
        bad = [(str(p.relative_to(ROOT)), hit) for p in docs()
               for hit in parent_number_hits(p.read_text(encoding="utf-8"))]
        self.assertEqual(bad, [], f"子技能文档里出现主技能私有编号：{bad}")

    def test_the_corpus_actually_uses_local_circled_numbers(self):
        """①–④ 确实在用（文内自数），否则第 2 项是在防一件不存在的事。"""
        used = sum(1 for p in docs()
                   for m in re.finditer("[①②③④]", p.read_text(encoding="utf-8")))
        self.assertGreaterEqual(used, 10, f"语料里 ①–④ 只有 {used} 处，规则可能已空转")

    def test_mutation_parent_step_pointer_is_caught(self):
        p = SUBSKILLS / "chinatax" / "SKILL.md"
        text = p.read_text(encoding="utf-8")
        anchor = "定级层按 `status`／`effect_level` 归档"
        self.assertIn(anchor, text, "注入点不在这份文档里，先改注入锚")
        broken = text.replace(anchor, "按 ⑧ 定级层归档", 1)
        self.assertTrue(parent_number_hits(broken), "改写成「按 ⑧ 定级」这种指针没被查出来")
        self.assertEqual(parent_number_hits(text), [], "原文本身就该是零命中")


# ── 3 路径指针存在 ──────────────────────────────────────────

def token_paths(token):
    """一个路径指针的候选写法：原样、去掉尾部 `.符号名`、目录尾斜杠去掉。"""
    out, t = [], token.strip("/")
    for _ in range(4):
        out.append(t)
        seg = t.rsplit("/", 1)[-1]
        if "." in seg and not seg.endswith(KNOWN_EXT):
            t = t.rsplit(".", 1)[0]
            out.append(t)
            continue
        break
    return out


def path_resolves(token, sdir):
    for cand in token_paths(token):
        if "*" in cand:
            if any(list(Path(base).glob(cand)) for base in (sdir, ROOT)):
                return True
            continue
        for base in (sdir, ROOT):
            q = Path(base) / cand
            if q.exists() or q.with_suffix(".py").exists():
                return True
    return False


def path_gaps(pairs):
    """pairs: [(相对路径, 子技能目录, 文本)]。返回 [(文件, 行号, 指针)]。"""
    return [(rel, line_no(text, m.start()), m.group(0))
            for rel, sdir, text in pairs
            for m in PATH_RE.finditer(text)
            if not path_resolves(m.group(0), sdir)]


class TestPathPointers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs = [(str(p.relative_to(ROOT)), p.parent, p.read_text(encoding="utf-8"))
                     for p in docs()]

    def test_counted_pointer_volume_is_still_there(self):
        n = sum(len(PATH_RE.findall(t)) for _r, _s, t in self.pairs)
        self.assertGreaterEqual(n, MIN_PATH_POINTERS,
                                f"路径指针只有 {n} 处，低于登记口径 {MIN_PATH_POINTERS}："
                                f"要么文档被清空，要么提取正则改窄了")

    def test_every_repo_path_pointer_lands(self):
        self.assertEqual(path_gaps(self.pairs), [])

    def test_local_references_directory_counts_as_resolved(self):
        """R&D-Expenses 自带 references/，指针先按子技能目录解——这条钉住
        两解法的优先级，否则删掉本地那份会被读成断链。"""
        sdir = SUBSKILLS / "R&D-Expenses" / "subskills" / "rd-internal-control"
        self.assertTrue((sdir / "references").is_dir())
        self.assertTrue(path_resolves("references/rd-mgmt-methodology.md", sdir))

    def test_mutation_bogus_script_name_is_caught(self):
        broken = [("subskills/chinatax/SKILL.md", SUBSKILLS / "chinatax",
                   "入口在 `scripts/tax_web_searchx.py`")]
        gaps = path_gaps(broken)
        self.assertEqual([g[2] for g in gaps], ["scripts/tax_web_searchx.py"],
                         "拼错的脚本名没被查出来")

    def test_mutation_dropped_local_reference_file_is_caught(self):
        sdir = SUBSKILLS / "R&D-Expenses" / "subskills" / "rd-internal-control"
        real = path_resolves("references/rd-mgmt-methodology.md", sdir)
        ghost = path_resolves("references/rd-mgmt-methodologx.md", sdir)
        self.assertTrue(real)
        self.assertFalse(ghost, "子技能本地 references/ 里不存在的文件被放过了")


# ── 4 「第X步／第X节」指针落地 ───────────────────────────────

def named_md_target(pre, own_path):
    """紧邻前 40 字里最后一个 .md 文件名 → 目标文件；解不到回 None。"""
    hits = list(MD_RE.finditer(pre))
    if not hits:
        return None
    tok = hits[-1].group(0)
    if tok in ("NOTE.md", "SKILL.md"):
        if tok == "SKILL.md" and ("母技能" in pre or "主技能" in pre):
            return ROOT / "SKILL.md"
        return own_path.parent / tok
    for base in (own_path.parent, ROOT):
        q = Path(base) / tok
        if q.exists():
            return q
    return "MISSING"


def marker_exists(text, num, kind):
    if kind == "步":
        return f"**第{num}步" in text
    return bool(re.search(r"(?m)^#{2,4}\s*" + num + "、", text)) or \
        bool(re.search(r"(?m)^#{2,4}\s*第" + num + "节", text))


def section_pointers(text, own_path):
    """[(行号, 原文, [候选目标文件])] —— 定义行（标题、`**第X步`）不算指针。"""
    out = []
    for m in SEC_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start:m.start()].lstrip().startswith("#"):
            continue
        if text[max(0, m.start() - 2):m.start()] == "**":
            continue
        pre = text[max(0, m.start() - 40):m.start()]
        kind = m.group(2)
        tgt = named_md_target(pre, own_path)
        if tgt == "MISSING":
            cands = [Path("〈不存在〉")]
        elif tgt is not None:
            cands = [tgt]
        else:
            cands = [own_path, own_path.parent / "SKILL.md", own_path.parent / "NOTE.md"]
        out.append((line_no(text, m.start()), m.group(0), cands, kind, m.group(1)))
    return out


def section_gaps(pairs, overrides=None):
    """pairs: [(相对路径, 文件 Path, 文本)]；overrides: {Path: 文本} 供变异用。

    候选目标文件先查 overrides，再落盘读——不然在内存里改坏的 NOTE.md，指针那一路
    仍从盘上读到原版，变异注入等于没注入。
    """
    overrides = overrides or {}

    def read(q):
        return overrides.get(q, q.read_text(encoding="utf-8"))

    gaps = []
    for rel, p, text in pairs:
        for ln, raw, cands, kind, num in section_pointers(text, p):
            if not any(c.exists() and marker_exists(read(c), num, kind) for c in cands):
                gaps.append((rel, ln, raw, [str(c) for c in cands]))
    return gaps


class TestSectionPointers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs = [(str(p.relative_to(ROOT)), p, p.read_text(encoding="utf-8"))
                     for p in docs()]

    def test_counted_pointer_volume_is_still_there(self):
        n = sum(len(section_pointers(t, p)) for _r, p, t in self.pairs)
        self.assertGreaterEqual(n, MIN_SECTION_POINTERS,
                                f"编号指针只有 {n} 处，低于登记口径 {MIN_SECTION_POINTERS}："
                                f"要么提取正则改窄了，要么指针被删空")

    def test_every_numbered_pointer_lands(self):
        self.assertEqual(section_gaps(self.pairs), [])

    def test_two_numbering_conventions_are_both_in_use(self):
        """步与节各有一套定义形态；某一套消失说明规则在查一件不存在的事。"""
        kinds = {k for _r, p, t in self.pairs for _l, _raw, _c, k, _n in section_pointers(t, p)}
        self.assertEqual(kinds, {"步", "节"}, f"本轮只解出 {kinds} 一种编号")

    def test_mutation_renumbered_note_section_is_caught(self):
        """注入锚选被指针指到的那一节。`chinatax/SKILL.md` 里指 NOTE 的是「第三节」
        与「第四节」，「第二节」没人指——改它报不出红，只能证明用例在查一件不存在的事。"""
        p = SUBSKILLS / "chinatax" / "NOTE.md"
        skill = SUBSKILLS / "chinatax" / "SKILL.md"
        pairs = [("subskills/chinatax/SKILL.md", skill, skill.read_text(encoding="utf-8")),
                 ("subskills/chinatax/NOTE.md", p, p.read_text(encoding="utf-8"))]
        self.assertEqual(section_gaps(pairs), [], "对照：未破坏时这一路本来全落地")
        anchor = "## 三、search5 的值域"
        self.assertIn(anchor, p.read_text(encoding="utf-8"), "注入点不在 NOTE 里，先改注入锚")
        broken = p.read_text(encoding="utf-8").replace(anchor, "## 柒、search5 的值域", 1)
        hit = [g for g in section_gaps(pairs, overrides={p: broken})
               if g[0].endswith("SKILL.md") and g[2] == "第三节"]
        self.assertTrue(hit, "NOTE 那节的序号改了名，SKILL 里指它的「第三节」却没报红")

    def test_mutation_renamed_step_marker_is_caught(self):
        p = SUBSKILLS / "npc-flk" / "SKILL.md"
        text = p.read_text(encoding="utf-8")
        self.assertIn("**第四步 分类编号", text, "注入点不在这份文档里，先改注入锚")
        broken = text.replace("**第四步 分类编号", "**丁四步 分类编号", 1)
        pairs = [("subskills/npc-flk/SKILL.md", p, broken)]
        gaps = section_gaps(pairs, overrides={p: broken})
        self.assertTrue(any(g[2] == "第四步" for g in gaps),
                        f"步定义改名之后指它的「第四步」没被报红：{gaps}")
        self.assertEqual(section_gaps([("subskills/npc-flk/SKILL.md", p, text)]), [],
                         "对照：原文这一路本来全落地")


# ── 5 代码符号可核对 ────────────────────────────────────────

def symbol_gaps(pairs, modules):
    """pairs: [(相对路径, 文本)]；modules: {模块名: 模块对象或 None}。"""
    gaps = []
    for rel, text in pairs:
        for m in SYM_RE.finditer(text):
            mod, sym = m.group(1), m.group(2)
            if sym == "py":          # `tax_x.py` 这种是文件名写法，不是符号
                continue
            obj = modules.get(mod)
            if obj is None or not hasattr(obj, sym):
                gaps.append((rel, line_no(text, m.start()), m.group(0)))
    return gaps


class TestSymbolRefs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        cls.pairs = [(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8"))
                     for p in docs()]
        names = {m.group(1) for _r, t in cls.pairs for m in SYM_RE.finditer(t)
                 if m.group(2) != "py"}
        cls.modules = {}
        for n in names:
            try:
                cls.modules[n] = importlib.import_module(n)
            except Exception:  # noqa: BLE001 模块取不到本身就是缺口，交给下面那条用例
                cls.modules[n] = None

    def test_counted_pointer_volume_is_still_there(self):
        n = sum(1 for _r, t in self.pairs for m in SYM_RE.finditer(t) if m.group(2) != "py")
        self.assertGreaterEqual(n, MIN_SYMBOL_REFS,
                                f"符号引用只有 {n} 处，低于登记口径 {MIN_SYMBOL_REFS}")

    def test_every_named_symbol_exists_on_the_module(self):
        self.assertEqual(symbol_gaps(self.pairs, self.modules), [])

    def test_missing_module_is_a_gap_not_a_skip(self):
        broken = [("subskills/npc-flk/SKILL.md", "见 `tax_npc_flk.search_npc`")]
        self.assertEqual([g[2] for g in symbol_gaps(broken, {"tax_npc_flk": None})],
                         ["`tax_npc_flk.search_npc`"], "取不到的模块被放过了")

    def test_mutation_renamed_symbol_is_caught(self):
        pairs = [(str((SUBSKILLS / "chinatax" / "SKILL.md").relative_to(ROOT)),
                  (SUBSKILLS / "chinatax" / "SKILL.md").read_text(encoding="utf-8"))]
        self.assertEqual(symbol_gaps(pairs, self.modules), [], "对照：原文本来全落地")
        text = pairs[0][1].replace("`tax_web_search.search_chinatax`",
                                   "`tax_web_search.search_chinatax_v2`", 1)
        self.assertNotEqual(pairs[0][1], text, "注入点没落进文本")
        hit = symbol_gaps([(pairs[0][0], text)], self.modules)
        self.assertEqual([g[2] for g in hit], ["`tax_web_search.search_chinatax_v2`"],
                         f"改名的符号没被报红：{hit}")

    def test_py_filename_form_is_not_read_as_a_symbol(self):
        """`tax_search.py` 里 group(2) 是 "py"，那是文件名不是属性名。"""
        self.assertTrue(SYM_RE.search("`tax_search.py`"))
        self.assertEqual(symbol_gaps([("x.md", "见 `tax_search.py`")],
                                     {"tax_search": self.modules.get("tax_search")}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
