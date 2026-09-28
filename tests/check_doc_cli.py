#!/usr/bin/env python3
"""文档 ↔ 代码契约检查：文档里写的 CLI 参数，代码里必须真的存在。

为什么需要它：SKILL.md 是 Agent 的操作手册，里面每一条
`python scripts/xxx.py --flag` 都是要被照着执行的。文档抄错一个参数名，
Agent 就会照着跑出一条报错命令——而这种错误不会触发任何测试，
因为测试只测代码，不测文档。

检查方向是**单向**的：只查「文档用了、代码没有」，不查「代码有、文档没写」
（文档不必穷举参数，代码留内部开关是正常的）。

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
DOCS = ("SKILL.md", "README.md")

# 文档里形如  scripts/xxx.py "关键词" --flag 的命令；取到文件名与其后的同行文本
_CMD_RE = re.compile(r"scripts/([A-Za-z0-9_]+\.py)([^\n`]*)")
# 长选项与短选项；用负向后顾避免把 --a--b 这种粘连切碎
_FLAG_RE = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*|-[A-Za-z])(?![\w-])")

# argparse 自动提供、代码里不会显式声明的选项
_IMPLICIT = {"-h", "--help"}


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


def main():
    problems, skips = [], []
    checked, mentioned = set(), set()

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
