#!/usr/bin/env python3
"""文档 ↔ 代码契约检查：文档里写的 CLI 参数，代码里必须真的存在。

为什么需要它：SKILL.md 是 Agent 的操作手册，里面每一条
`python scripts/xxx.py --flag` 都是要被照着执行的。文档抄错一个参数名，
Agent 就会照着跑出一条报错命令——而这种错误不会触发任何测试，
因为测试只测代码，不测文档。

检查方向是**单向**的：只查「文档用了、代码没有」，不查「代码有、文档没写」
（文档不必穷举参数，代码留内部开关是正常的）。

两类检查：

1. **命令级**：同一行里有 `scripts/xxx.py` 时，取该行其余参数，比对那个脚本的参数表。
2. **片段级**：文档里还有大量**孤立**的行内参数片段（反引号括起来、不带脚本名），
   例如"总局条目用 `--source fgk --body`"。这类片段没有脚本上下文，若只按
   "参数名在全项目是否存在"去查，`--source`（属 tax_formatter.py）与
   `--body`（属 tax_fgk.py）都"存在"，错法就被放过去。所以改判一件事：
   这组参数能不能在**某一个**脚本上同时成立。成立才认为可执行。

配套约定：脚本一律用 argparse 声明参数。若某脚本自己解析 sys.argv，
本工具静态取不到参数表，会报 SKIP 而不是误判成「参数不存在」——
早期用只认 add_argument 的写法检查时，tax_browser.py 就被误报过一次。

用法：
    python tests/check_doc_cli.py

退出码：无问题 0，有问题 1（可直接挂门禁）。
"""

import ast
import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
REFERENCES_DIR = PROJECT_ROOT / "references"
# 主线 SKILL.md + README.md，加上从 SKILL.md 搬出命令全文的 references/。
# 命令搬进参考文件后，"文档用了、代码没有"这道契约必须跟着走，否则搬一次就脱管一次。
DOCS = ("SKILL.md", "README.md") + tuple(
    f"references/{p.name}" for p in sorted(REFERENCES_DIR.glob("*.md"))
)

# 文档里形如  scripts/xxx.py "关键词" --flag 的命令；取到文件名与其后的同行文本
_CMD_RE = re.compile(r"scripts/([A-Za-z0-9_]+\.py)([^\n`]*)")
# 长选项与短选项；用负向后顾避免把 --a--b 这种粘连切碎
_FLAG_RE = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*|-[A-Za-z])(?![\w-])")

# argparse 自动提供、代码里不会显式声明的选项
_IMPLICIT = {"-h", "--help"}

# 反引号括起来的行内片段，例如 `--source fgk --body`
_INLINE_RE = re.compile(r"`([^`\n]+)`")


def _flags_of(script: Path):
    """取脚本声明的选项。

    Returns:
        (flags, None)        正常，flags 是选项集合
        (None, 原因)         无法静态分析
    """
    try:
        src = script.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"读不到文件：{e}"

    if "import argparse" not in src and "from argparse" not in src:
        return None, "未使用 argparse（自己解析 argv，静态取不到参数表）"

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return None, f"语法错误：{e}"

    flags = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        for a in node.args:
            # add_argument("--foo", ...)
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                if a.value.startswith("-"):
                    flags.add(a.value)
            # add_argument(*["--a", "--b"], ...)
            elif isinstance(a, (ast.List, ast.Tuple)):
                for e in a.elts:
                    if (isinstance(e, ast.Constant) and isinstance(e.value, str)
                            and e.value.startswith("-")):
                        flags.add(e.value)
    return flags, None


def _flag_sets_by_script():
    """全部脚本 → 各自声明的选项集合；静态取不到参数表的脚本直接略过。"""
    out = {}
    for script in sorted(SCRIPTS_DIR.glob("*.py")):
        flags, _why = _flags_of(script)
        if flags is not None:
            out[script.name] = flags
    return out


def _check_orphan_flag_groups(doc, text, by_script):
    """抓「参数名写对了、但挂在错的脚本上」的行内片段。

    只在片段含**两个及以上**参数时才判：单个参数没有归属歧义，
    笼统提一句"--verbose 会打详细日志"是正常写法，不该报。
    """
    problems = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for seg in _INLINE_RE.findall(line):
            if "scripts/" in seg or ".py" in seg:
                continue          # 带脚本名的片段归 main 里那条命令级规则管
            flags = {f for f in _FLAG_RE.findall(seg) if f not in _IMPLICIT}
            if len(flags) < 2:
                continue
            if any(flags <= fs for fs in by_script.values()):
                continue
            owners = sorted(n for n, fs in by_script.items() if flags & fs)
            hint = "、".join(owners) if owners else "没有任何脚本"
            problems.append(
                f"{doc}:{lineno}：`{seg}` 这组参数在任何一个脚本上都跑不通"
                f"（{'、'.join(sorted(flags))} 分别属于 {hint}，凑不成同一条命令）；"
                f"请写明脚本名，改成能整条执行的命令")
    return problems


def main():
    problems, skips = [], []
    checked, mentioned = set(), set()
    by_script = _flag_sets_by_script()

    for doc in DOCS:
        path = PROJECT_ROOT / doc
        if not path.exists():
            problems.append(f"{doc}：文档不存在")
            continue
        text = path.read_text(encoding="utf-8")
        for m in _CMD_RE.finditer(text):
            name, tail = m.group(1), m.group(2)
            script = SCRIPTS_DIR / name
            if not script.exists():
                if name not in mentioned:
                    mentioned.add(name)
                    problems.append(f"{doc}：引用了不存在的 scripts/{name}")
                continue
            wanted = {f for f in _FLAG_RE.findall(tail) if f not in _IMPLICIT}
            if not wanted:
                continue
            if name in checked:
                continue
            checked.add(name)
            flags, why = _flags_of(script)
            if flags is None:
                skips.append(f"{name}：{why}")
                continue
            missing = sorted(f for f in wanted if f not in flags)
            if missing:
                problems.append(
                    f"{doc}：scripts/{name} 用了 {'、'.join(missing)}，"
                    f"但代码里没有这个参数")

        problems.extend(_check_orphan_flag_groups(doc, text, by_script))

    print(f"文档-代码契约检查（{'、'.join(DOCS)} → scripts/）")
    print(f"  抽查脚本 {len(checked)} 个，全部脚本 {len(list(SCRIPTS_DIR.glob('*.py')))} 个")
    for s in skips:
        print(f"  [SKIP] {s}")
    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
        print(f"\n  {len(problems)} 处不一致——文档里的命令会被照着执行，必须改")
        return 1
    print("  [PASS] 文档用到的参数在代码里都存在")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
