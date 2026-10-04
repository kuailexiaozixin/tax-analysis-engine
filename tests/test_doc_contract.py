#!/usr/bin/env python3
"""文档内部契约的离线用例。不联网、不开浏览器、不调模型。

守的是五类结构问题——都不问内容对错，只问"改了一处有没有忘了另一处"：

  1. `references/*.md` 顶部目录里每个锚点都必须落到本文某个真实二级标题；对
     `output_templates.md` 与 `out_of_library_layers.md` 这两份按"一节一行"维护
     目录的文件，要求每个二级标题都进目录。
  2. SKILL.md ⑥ 声明的输出形态清单（含"N 种"这个计数）必须与模板文件的
     二级标题逐一对应——往模板加一节却不写进 ⑥，等于那节没人知道要走。同一
     条理由管着另外三处复述：⑥ 照抄的行判断四值与项结论五档、模板【候选总览】
     逐项计数的五档，都是同一套取值的复写处——定义处改了名而复写处不动，用户就
     会看到表格里查不到的档位；【优惠交互与限制】的子检查清单删掉一条，就等于
     那一层永久不查。
  3. SKILL.md 里形如"在 X 文件的 Y 一节"的前向指针必须真的存在那一节；
     `source_defects.md`「主线动作归属」表里引用的代码符号必须 import 得到。
     归属表写的是"这一格由脚本还是 Agent 做"，符号名一旦漂走，表就变成
     一句无法核对的自我声明。
  4. SKILL.md 与全部 `references/*.md` 的正文里不得出现段内紧挨着的重复片段——
     改文档时"上一行行尾留半句、下一行行首又写一遍"这种自伤，按行扫看不出。
  5. 模板三个补充块（立法理由、易混点、救济与期限）的定性在两处复写：SKILL.md ⑥
     说它们是必查项，模板说各自去哪几层取、取不到时留痕那一句怎么写。⑥ 的定性漂回
     "可选"，或模板那句留痕被换成"取不到就不写"，模型就有一条不必检索的出口——
     留白还会让 ⑦ 第 9 条无从核对，因为读者分不清是没查到还是根本没查。

第 3 项的"文件 → 章节名"抽取只认 SKILL.md 现有的两种句式（`` `x.md` 的"Y"一节``
与 `` `x.md`「Y」 ``，后者已是多数），扩句式前先加提取规则，否则新指针会静默逃过
检查——变异用例里钉了这一点。目录项的显示文字不在判据之内，理由写在
`TestTocAnchors` 的说明里。

每条检查都要能报红才算还在工作，所以本文件自带 `test_mutation_*` 自检：往内存副本
里注入对应的破坏（删枚举值、表格改名、矩阵塞域外取值、删整条交互 bullet、删候选
总览档位、指针指向不存在的小节、「」式指针整体不认、半句贴两遍、归属表符号漂走、
把留痕句改成"就整段不写"等），断言那一条检查转红。自检改的是内存副本，不落盘，
也不往仓库里留注入脚本。
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "SKILL.md"
REF_DIR = ROOT / "references"

sys.path.insert(0, str(ROOT / "scripts"))

H2_RE = re.compile(r"^## ([^#\s].*?)\s*$", re.M)
TOC_RE = re.compile(r"^- \[(?P<text>[^\]]+)\]\(#(?P<slug>[^)]+)\)", re.M)


def strip_fences(md: str) -> str:
    """去掉 ``` 围栏内的内容：模板骨架里的 `## [问题] — …` 是产出物的行，
    不是本文件的章节，混进标题会把目录检查带偏。"""
    out, in_fence = [], False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def h2_titles(md: str) -> list:
    return [m.strip() for m in H2_RE.findall(strip_fences(md))]


def toc_entries(md: str) -> list:
    return [(m.group("text"), m.group("slug")) for m in TOC_RE.finditer(md)]


def slugify(title: str) -> str:
    """按 GitHub 的 markdown 锚点规则从标题算锚点：

      1. 小写；
      2. 删除"非字母数字、非空白、非连字符/下划线"的字符——中文全角标点因此被
         **删掉**而不是换成 `-`（`缺口：准则` 的锚点写成 `缺口准则`）；
      3. 空白换成连字符；
      4. 去掉首尾连字符。

    已知偏差：本实现保留下划线，而 GitHub 实际会删它。仓内只有 `evaluation.md`
    一条锚点含下划线，所以判据只比"抹平连字符与下划线后的字符集合"
    （见 anchor_candidates），不去猜渲染器产出哪一种——猜错就是把错的锚点
    钉成标准。
    """
    s = title.strip().lower()
    s = "".join(c for c in s if c.isalnum() or c.isspace() or c in "-_")
    s = re.sub(r"\s", "-", s)
    return s.strip("-")


def anchor_key(text: str) -> set:
    """锚点与标题锚的可比形式：抹掉连字符与下划线的差异。"""
    return {re.sub(r"[-_]", "", text)}


def heading_for_anchor(md: str, slug: str) -> str:
    """在本文二级标题里找该锚点对应的那一节，找不到返回空串。"""
    target = anchor_key(slug)
    for h in h2_titles(md):
        if anchor_key(slugify(h)) & target:
            return h
    return ""


def split_section(md: str, heading: str) -> str:
    """取某个二级标题到下一个二级标题之间的正文。"""
    parts = md.split("\n## ")
    for p in parts:
        if p.strip().startswith(heading):
            return p
    return ""


def _norm_list(items: list) -> list:
    return [x.strip().replace("\n", "") for x in items if x.strip()]


# 模板定义的三张取值域。它们钉在这里，是因为两处都要比：模板自身不得漂走，
# SKILL.md ⑥ 的复述必须与模板逐项一致。行判断四值出自模板正文那行反引号串，
# 项结论五档出自「| 结论 |」表格第一列，交互子检查出自围栏模板的 bullet 标签。
ROW_VALUES = ["满足", "不满足", "未知", "不适用"]
VERDICT_VALUES = ["明确匹配", "条件匹配", "待资格确认", "明确不匹配", "无法判断"]
INTERACTION_AXES = ["叠加", "进项", "开票", "放弃优惠", "留存"]


def row_enum(md: str) -> list:
    """模板「行判断只有四值」那行反引号串，原样解析。"""
    m = re.search(r"\*\*行判断只有四值\*\*：`([^`]*)`", md)
    return _norm_list(m.group(1).split("/")) if m else []


def tables(md: str) -> list:
    """按出现顺序抽出全部 markdown 表格，每表为 (表头单元格, [数据行单元格…])。

    条件矩阵那张表在围栏里面，所以这里传原文、不走 `strip_fences`。分隔行
    （`|---|---|`）整行丢掉。
    """
    out = []
    for block in re.findall(r"(?:^\|.*\|\s*$\n?)+", md, re.M):
        rows = [[c.strip() for c in ln.strip().strip("|").split("|")]
                for ln in block.strip().splitlines()]
        rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
        if len(rows) >= 2:
            out.append((rows[0], rows[1:]))
    return out


def table_col(md: str, header: str) -> list:
    """表头含 `header` 那一列的全部数据单元格。"""
    for head, rows in tables(md):
        if header in head:
            i = head.index(header)
            return [r[i] for r in rows if len(r) > i]
    return []


def verdict_table(md: str) -> list:
    """「每项优惠的结论只有五档」表格的第一列。"""
    return table_col(md, "结论")


def matrix_judgments(md: str) -> list:
    """条件矩阵示例表「判断」列实际写出的取值。"""
    return table_col(md, "判断")


def interaction_span(md: str) -> str:
    """【优惠交互与限制】段的全文。"""
    m = re.search(r"\*\*优惠交互与限制\*\*[^\n]*\n(.*?)\n\*\*排除项", md, re.S)
    return m.group(1) if m else ""


def labelled_span(md: str, label: str) -> str:
    """围栏模板里 `**label**` 起、到空行为止的那一段（折行已抹平）。"""
    m = re.search(r"\*\*%s\*\*(.*?)(?:\n\n|\n```)" % re.escape(label), md, re.S)
    return m.group(0).replace("\n", "") if m else ""


def tally_states(md: str) -> list:
    """【候选总览】那一行逐项计数的档位名，按写出顺序返回。

    这一行是五档的第二处枚举：五档表格改了名却不改总览，用户就会看到一个
    表格里查不到的档位，所以两处必须逐项一致。
    """
    span = labelled_span(md, "候选总览")
    return re.findall(r"([一-鿿]+) \[n\] 项", span)


def skill_enums(skill_md: str) -> list:
    """⑥ 里以反引号写出的枚举串（斜杠分隔），逐条取出。

    五档那一串在 SKILL.md 里跨了行，字符类必须收换行再抹平，否则那一档整个
    取不到，一致性检查会退化成只查四值。
    """
    body = split_section(skill_md, "⑥")
    out = []
    for span in re.findall(r"`([一-鿿A-Za-z/\n]+)`", body):
        parts = _norm_list(span.split("/"))
        if len(parts) >= 2:
            out.append(parts)
    return out


def interaction_checks(md: str) -> list:
    """模板【优惠交互与限制】段列出的子检查项标签。

    bullet 会折行（续行缩进两格），所以按"行首减号"切段而不是按行切。
    """
    return [b.lstrip("- ").split("：")[0].strip()
            for b in re.split(r"\n(?=- )", interaction_span(md).strip())
            if b.startswith("- ")]


def adjacent_dupes(md: str) -> list:
    """段内紧挨着重复出现的 5 字以上片段——改文档时最容易自伤的一类缺陷。

    按空行切段后把段内换行抹平再找：中文文档一行约 40 字，"删半句、多贴半句"
    这种事故几乎总落在同一段里，跨段重复则是有意复述，不算缺陷。两处噪声排除：
    重复单元须含汉字（否则表格分隔行 `|---|---|` 会常年假报警），表格行整行不参与
    （"| 境外所得 | 境外所得 |" 这种两列同值是合法内容，不是自伤）。
    """
    out = []
    for para in re.split(r"\n\s*\n", md):
        prose = "\n".join(ln for ln in para.splitlines() if not ln.lstrip().startswith("|"))
        s = re.sub(r"\s+", "", prose)
        out += [m.group(1) for m in re.finditer(r"(.{5,20}?)\1", s)
                if re.search(r"[一-鿿]", m.group(1))]
    return sorted(set(out))


# 三个补充块（立法理由 / 易混点 / 救济与期限）里"确实取不到时那句留痕话"的取值。
# 判据比的是整句而不是"未取到"三个字：三个字在他处也会合法出现，按词在不在文件里
# 判就是假绿——留痕话整句被换成"取不到就不写"，检查照样过。
GAP_NOTE = {
    "立法理由": ("未取到官方说明", "不得凭训练数据", "④ 五源"),
    "易混点": ("未定位到相邻规定的原文", "④ 五源"),
    "救济与期限": ("未取到明文", "不得删", "税收争议救济"),
}

# "查不到就留白"的出口措辞。出现任何一条都算缺陷，见 TestOptionalReasoningBlocks。
SILENT_EXIT_WORDS = ("就整段不写", "就不写这一段", "就不写这一行", "就不写这一条")


def silent_exit_words(md: str) -> list:
    """模板正文里出现的留白措辞，按写出顺序返回。"""
    return [w for w in SILENT_EXIT_WORDS if w in md]


def forms_listed_in_skill(skill_md: str) -> tuple:
    """⑥ 声明的输出形态清单，以及"N 种"那个计数。"""
    body = split_section(skill_md, "⑥")
    m = re.search(r"([一二三四五六七八九十\d]+)种输出形态（(.*?)）", body, re.S)
    if not m:
        return None, []
    count_token = m.group(1)
    names = [n.strip() for n in re.split(r"[、,，]", m.group(2)) if n.strip()]
    return count_token, names


def forward_pointers(skill_md: str) -> list:
    """SKILL.md 里指向 `references/*.md` 某一节的前向指针，抽出 (文件, 章节名)。

    只认仓内现有两种句式：`references/x.md` 的"Y"一节，和 `x.md`「Y」（前缀
    references/ 可省）。扩句式前先加提取规则，否则新指针会静默逃过检查——
    变异用例里钉了这一点。
    """
    quote = '"\u201c\u201d'
    pat = re.compile(r"`references/([^`]+)`\s*的[" + quote + r"]([^" + quote + r"]+)["
                     + quote + r"]\s*一节")
    out = [(m.group(1), m.group(2)) for m in pat.finditer(skill_md)]
    pat2 = re.compile(r"`(?:references/)?([A-Za-z0-9_-]+\.md)`「([^」]+)」")
    return out + [(m.group(1), m.group(2)) for m in pat2.finditer(skill_md)]


def dotted_symbols(text: str) -> list:
    """形如 module.attr / pkg.Class 的反引号符号名（跳过文件名与命令）。"""
    out = []
    for raw in re.findall(r"`([^`\n]+)`", text):
        s = raw.strip()
        if "/" in s or ".py" in s or " " in s or "(" in s or ":" in s:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", s):
            out.append(s)
    return out


class TestTocAnchors(unittest.TestCase):
    """目录两项判据：每个锚点指得到存在的小节；目录只建了就必须逐节建全。

    锚点落不到任何标题就是断链（改标题或删小节后目录留在原地）。显示文字**不纳入
    判据**：省写可以省掉标题中段（`答题正确率（主指标）` 指的就是省掉了
    `eval_answer.py` 的那一节），任何前缀或包含比对都会在这种合法省写上假报警，
    一条常年假报警的检查等于没有检查。
    """

    def test_every_anchor_resolves_to_a_heading(self):
        for f in sorted(REF_DIR.glob("*.md")):
            md = f.read_text(encoding="utf-8")
            for text, slug in toc_entries(md):
                self.assertTrue(heading_for_anchor(md, slug),
                                "%s 目录项「%s」的锚点 #%s 落不到任何二级标题"
                                % (f.name, text, slug))

    def test_toc_covers_every_heading(self):
        """只对两份按"一节一行"维护目录的文件硬要求全覆盖。"""
        for name in ("output_templates.md", "out_of_library_layers.md"):
            md = (REF_DIR / name).read_text(encoding="utf-8")
            entries = [t for t, _ in toc_entries(md)]
            for h in h2_titles(md):
                if h == "目录":
                    continue
                self.assertTrue(any(h.startswith(e) or e in h for e in entries),
                                "%s 的「%s」没进目录" % (name, h))

    def test_mutation_renamed_heading_is_caught(self):
        """自检：把一节改名而目录不动，锚点检查必须报红，而不是永远绿。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        broken = md.replace("## 资格判定条件矩阵式", "## 甲乙丙丁戊己", 1)
        unresolved = [(t, s) for t, s in toc_entries(broken)
                      if not heading_for_anchor(broken, s)]
        self.assertIn(("资格判定条件矩阵式", "资格判定条件矩阵式"), unresolved,
                      "改名后锚点检查没报红，说明提取或匹配规则失效")

    def test_mutation_dropped_toc_entry_is_caught(self):
        """自检：删掉目录里一行，按覆盖检查同一判据必须报红。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        broken = md.replace("- [规则陈述式](#规则陈述式)\n", "", 1)
        entries = [t for t, _ in toc_entries(broken)]
        self.assertNotIn("规则陈述式", entries)
        uncovered = [h for h in h2_titles(broken)
                     if h != "目录"
                     and not any(h.startswith(e) or e in h for e in entries)]
        self.assertIn("规则陈述式", uncovered, "删行后覆盖检查没抓到，判据失效")


class TestSkillFormsInventory(unittest.TestCase):
    def test_forms_listed_in_6_match_the_template_file(self):
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        heads = [h for h in h2_titles(md) if h != "目录"]
        count_token, names = forms_listed_in_skill(SKILL.read_text(encoding="utf-8"))
        self.assertTrue(names, "SKILL.md ⑥ 里找不到输出形态清单，提取规则已失效")
        self.assertEqual(_cn_count(count_token), len(names),
                         "⑥ 声明的种数与实列形态数不符")
        for n in names:
            self.assertTrue(any(n in h or h.startswith(n) for h in heads),
                            "⑥ 列的「%s」在模板文件里没有对应小节" % n)
        self.assertEqual(len(names), len(heads),
                         "模板文件有 %s 节而 ⑥ 只列 %s 种" % (len(heads), len(names)))

    def test_mutation_unlisted_template_is_caught(self):
        """自检：模板新加一节却不写进 ⑥，必须报红。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        heads = [h for h in h2_titles(md) if h != "目录"] + ["凭空多出来的一节"]
        _, names = forms_listed_in_skill(SKILL.read_text(encoding="utf-8"))
        self.assertEqual(len(names), len(heads) - 1)
        self.assertNotIn("凭空多出来的一节", names)

    def test_template_defines_the_two_enums(self):
        """模板自己得把两套取值域写全：四值一行、五档一表、示例矩阵不越域且不缺值。

        判据比的是**整串枚举表**，不是零散词。按"某个词在不在文件里"写就是假绿——
        把 `满足 / 不满足 / 未知 / 不适用` 删到只剩两值，单个词
        仍在他处出现，检查照样过。
        """
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        self.assertEqual(row_enum(md), ROW_VALUES,
                         "模板的行判断枚举被改动（现读作 %s）" % row_enum(md))
        self.assertEqual(verdict_table(md), VERDICT_VALUES,
                         "模板的项结论五档被改动（现读作 %s）" % verdict_table(md))
        cells = matrix_judgments(md)
        self.assertTrue(cells, "条件矩阵示例表的「判断」列读不到，判据读的表变了")
        self.assertTrue(set(cells) <= set(ROW_VALUES),
                        "示例矩阵写出了域外的行判断值：%s"
                        % sorted(set(cells) - set(ROW_VALUES)))
        self.assertEqual(set(cells), set(ROW_VALUES),
                         "四值中有值在示例矩阵里从未出现，缺 %s"
                         % sorted(set(ROW_VALUES) - set(cells)))

    def test_6_restates_the_enums_verbatim(self):
        """⑥ 复述的两串枚举必须与模板逐项一致，含顺序。"""
        skill_lists = skill_enums(SKILL.read_text(encoding="utf-8"))
        self.assertEqual(len(skill_lists), 2,
                         "⑥ 里读到的枚举串不是两串（%s），提取规则已漂" % skill_lists)
        self.assertIn(ROW_VALUES, skill_lists, "⑥ 没照抄行判断四值")
        self.assertIn(VERDICT_VALUES, skill_lists, "⑥ 没照抄项结论五档")

    def test_mutation_enum_shrunk_is_caught(self):
        """自检：把模板那行四值删到两值，改前读作四值、改后必须不等。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        broken = md.replace("`满足 / 不满足 / 未知 / 不适用`", "`满足 / 不满足`", 1)
        self.assertNotEqual(broken, md, "变异没落到模板那行枚举上，判据读的行没被改")
        self.assertEqual(row_enum(md), ROW_VALUES)
        self.assertNotEqual(row_enum(broken), ROW_VALUES, "删值后检查不会报红")

    def test_mutation_out_of_domain_cell_is_caught(self):
        """自检：示例矩阵里塞一个含糊出口（"大概满足"），域检查必须报红。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        broken = md.replace("| 业务所属期 | …… | 未知 |", "| 业务所属期 | …… | 大概满足 |", 1)
        self.assertNotEqual(broken, md, "变异没落到矩阵示例行上")
        self.assertTrue(set(matrix_judgments(md)) <= set(ROW_VALUES))
        self.assertFalse(set(matrix_judgments(broken)) <= set(ROW_VALUES),
                         "域外取值没被抓到，四值枚举形同虚设")

    def test_mutation_6_restatement_drifted_is_caught(self):
        """自检：⑥ 少抄一档（删掉"无法判断"），一致性检查必须报红。"""
        skill = SKILL.read_text(encoding="utf-8")
        broken = skill.replace("待资格确认/明确不匹配/\n无法判断`", "待资格确认/明确不匹配`", 1)
        self.assertNotEqual(broken, skill, "变异没落到 ⑥ 那串五档上")
        lists = skill_enums(skill)
        self.assertIn(VERDICT_VALUES, lists)
        self.assertNotIn(VERDICT_VALUES, skill_enums(broken), "少抄一档后检查不会报红")

    def test_candidate_rollup_reuses_the_five_states(self):
        """多项候选的答案级两行：总览逐项计数用的就是那五档，同享结论单独在位。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        tally = tally_states(md)
        self.assertEqual(tally, VERDICT_VALUES,
                         "【候选总览】计的档与五档表格不一致（现读作 %s）" % tally)
        self.assertTrue(labelled_span(md, "同享结论"),
                        "围栏模板里没有【同享结论】那一行")
        for lab in ("候选总览", "同享结论"):
            self.assertIn("两项以上候选时必填", labelled_span(md, lab),
                          "%s 没写明两项以上必填" % lab)

    def test_mutation_tally_state_drifted_is_caught(self):
        """自检：把总览里一个档位改名而五档表格不动，两处一致性检查必须报红。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        broken = md.replace("待资格确认 [n] 项", "待确认 [n] 项", 1)
        self.assertNotEqual(broken, md, "变异没落到总览那一行")
        self.assertEqual(tally_states(md), VERDICT_VALUES)
        self.assertNotEqual(tally_states(broken), VERDICT_VALUES, "改名后检查不会报红")

    def test_interaction_checks_listed_in_template(self):
        """交互段：子检查项清单固定，「叠加」那条要给出可搜的原文措辞。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        self.assertEqual(interaction_checks(md), INTERACTION_AXES,
                         "【优惠交互与限制】的子检查项被改动（现读作 %s）"
                         % interaction_checks(md))
        bullets = interaction_span(md)
        for w in ("同时符合", "择优", "不得叠加", "可叠加", "同一期间"):
            self.assertIn(w, bullets, "「叠加」那条没列出要搜的原文措辞 %s" % w)
        for w in ("转出", "分别核算"):
            self.assertIn(w, bullets, "「进项」那条没写 %s 的处理" % w)

    def test_mutation_dropped_interaction_axis_is_caught(self):
        """自检：删掉"放弃优惠"那一整条 bullet，清单检查必须报红。"""
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        broken = re.sub(r"\n- 放弃优惠：[^\n]*", "", md, count=1)
        self.assertNotEqual(broken, md, "变异没落到那条 bullet 上")
        self.assertEqual(interaction_checks(md), INTERACTION_AXES)
        self.assertNotIn("放弃优惠", interaction_checks(broken), "删条后检查不会报红")

    def test_skill_side_forbids_skipping_the_layer(self):
        """模板给怎么查，SKILL.md 给不许跳过：两条禁止项必须在位。"""
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("未查叠加与择一", skill, "⑦ 没有禁止未查叠加与择一那一条")
        self.assertIn("禁止把条件全满足当成多项优惠可同享", skill,
                      "全绿≠可同享没进最高优先级约束")
        self.assertIn("禁止择一采用矛盾输入", skill,
                      "冲突输入的处置没进最高优先级约束")
        self.assertIn("择一采用", skill.split("## ⑦")[1],
                      "② 的矛盾输入规则没同步进 ⑦ 禁止清单")


class TestForwardPointers(unittest.TestCase):
    def test_every_pointer_names_a_real_section(self):
        pointers = forward_pointers(SKILL.read_text(encoding="utf-8"))
        self.assertGreaterEqual(
            len(pointers), 5,
            "只提取到 %s 条指针，少于 SKILL.md 现有的 5 条——某一式没被认出来，"
            "该式里的指针会静默逃过检查" % len(pointers))
        self.assertTrue(pointers, "SKILL.md 里一个前向指针都没抓到，提取规则已失效")
        for fname, sec in pointers:
            path = REF_DIR / fname
            self.assertTrue(path.exists(), "指针指向不存在的文件 references/%s" % fname)
            md = path.read_text(encoding="utf-8")
            self.assertIn(sec, h2_titles(md),
                          "references/%s 里没有「%s」这一节" % (fname, sec))

    def test_ownership_table_symbols_import(self):
        """归属表引用的代码符号必须真的存在，漂走的符号名等于没写。"""
        md = (REF_DIR / "source_defects.md").read_text(encoding="utf-8")
        table = split_section(md, "主线动作归属")
        self.assertTrue(table, "source_defects.md 没有「主线动作归属」一节")
        syms = sorted(set(dotted_symbols(table)))
        self.assertTrue(len(syms) >= 8, "归属表里可核对的符号太少（%s 个）" % len(syms))
        for s in syms:
            head, rest = s.split(".", 1)
            try:
                mod = __import__(head)
            except ImportError:
                self.fail("归属表引用了导入不了的模块 %s" % s)
            obj = mod
            for attr in rest.split("."):
                self.assertTrue(hasattr(obj, attr), "归属表符号 %s 不存在" % s)
                obj = getattr(obj, attr)

    def test_mutation_missing_section_is_caught(self):
        """自检：把指针指向一个不存在的小节，必须报红。"""
        fake = SKILL.read_text(encoding="utf-8") + (
            "\n展开在 `references/source_defects.md` 的\"凭空小节\"一节。\n")
        pointers = forward_pointers(fake)
        self.assertIn(("source_defects.md", "凭空小节"), pointers,
                      "新增句式没被提取到，检查会静默逃过")
        heads = h2_titles((REF_DIR / "source_defects.md").read_text(encoding="utf-8"))
        self.assertNotIn("凭空小节", heads)

    def test_mutation_bracket_pointer_form_is_extracted(self):
        """自检：`x.md`「Y」这一式也必须被提取——SKILL.md 里它已是多数句式。

        快索引与 ⑦ 第 22 条都用这一式；只认"……一节"的话，新指针会一条不漏地
        静默逃过检查。
        """
        skill = SKILL.read_text(encoding="utf-8")
        fake = skill.replace("`references/output_templates.md`「资格判定条件矩阵式」",
                             "`references/output_templates.md`「不存在的一式」", 1)
        self.assertNotEqual(fake, skill, "变异没落到那一式上")
        self.assertIn(("output_templates.md", "不存在的一式"), forward_pointers(fake),
                      "「」式指针没被提取，检查静默逃过")


class TestProseSelfInjury(unittest.TestCase):
    """改文档时最容易自伤的一类：半句被贴两遍。下面这个形态就是它长出来的样子。

    一次 Edit 把"它解决的是六段式"留在上一行行尾、又在新行行首重写了一遍，
    按行肉眼扫过去看不出，跨行抹平才现形。
    """

    def test_no_adjacent_duplicate_run_in_docs(self):
        for f in [SKILL] + sorted(REF_DIR.glob("*.md")):
            dupes = adjacent_dupes(f.read_text(encoding="utf-8"))
            self.assertFalse(dupes,
                             "%s 段内出现紧挨着的重复片段：%s" % (f.name, dupes[:3]))

    def test_mutation_duplicated_half_sentence_is_caught(self):
        md = (REF_DIR / "output_templates.md").read_text(encoding="utf-8")
        broken = md.replace("它解决的是六段式", "它解决的是六段式它解决的是六段式", 1)
        self.assertNotEqual(broken, md, "变异片段没找到，判据读的原文变了")
        self.assertEqual(adjacent_dupes(md), [])
        self.assertIn("它解决的是六段式", adjacent_dupes(broken), "贴两遍不会被报红")


class TestOptionalReasoningBlocks(unittest.TestCase):
    """三个补充块都要求"取不到要留痕"，且正文里不得出现留白措辞。

    这三块从另一技能的固定三层输出借来，借的前提是它们在本仓不许退化成可选段：
    立法理由撞 ⑦ 第 9 条（不得用训练数据的政策信息），救济与期限撞 ⑦ 第 2 条
    （争议类必须走到 L4），SKILL.md ④ 末又早写了"五源均无结果时不要直接结束，
    明确告知未找到"。模板若再开一条"取不到就不写"的出口，等于同一件事留了个更松的
    走法，模型一定走那条——而留白比写错更难发现：读者看不出到底查过没有，⑦ 第 9 条
    也因此无从核对。所以这里的判据是双向的：正向钉每块的留痕句，反向禁留白措辞。
    """

    @staticmethod
    def _md():
        return (REF_DIR / "output_templates.md").read_text(encoding="utf-8")

    def test_the_three_blocks_are_present(self):
        md = self._md()
        for lab in GAP_NOTE:
            self.assertTrue(labelled_span(md, lab), "模板里没有【%s】那一块" % lab)

    def test_each_block_demands_a_disclosed_gap(self):
        """每块都得在自己那段里写出留痕句与检索对象，不是全文某处出现就算过。"""
        md = self._md()
        for lab, tokens in GAP_NOTE.items():
            span = labelled_span(md, lab)
            for tok in tokens:
                self.assertIn(tok, span, "【%s】块缺「%s」" % (lab, tok))

    def test_no_silent_exit_wording(self):
        self.assertEqual(silent_exit_words(self._md()), [],
                         "模板出现了查不到就留白的出口措辞：%s"
                         % silent_exit_words(self._md()))

    def test_skill_side_says_they_are_mandatory(self):
        """⑥ 把这三段定性成必查项；定性漂回"可选"就等于把留白合法化。"""
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("必查项不是可选项", skill, "⑥ 没写明这三段是必查项")
        for lab in GAP_NOTE:
            self.assertIn(lab, skill, "⑥ 漏了 %s 这一段" % lab)

    def test_mutation_gap_note_replaced_by_silence_is_caught(self):
        """自检：把留痕句改回"就整段不写"，正向与反向两条判据都要报红。"""
        md = self._md()
        broken = md.replace("未取到官方说明", "就整段不写", 1)
        self.assertNotEqual(broken, md, "变异没落到立法理由那句留痕上")
        self.assertEqual(silent_exit_words(md), [])
        self.assertIn("未取到官方说明", labelled_span(md, "立法理由"))
        self.assertNotIn("未取到官方说明", labelled_span(broken, "立法理由"),
                         "改留白后正向判据没报红")
        self.assertIn("就整段不写", silent_exit_words(broken),
                      "改留白后反向判据没报红，说明措辞检查形同虚设")


def _cn_count(token: str) -> int:
    table = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if token.isdigit():
        return int(token)
    return table.get(token, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
