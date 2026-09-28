#!/usr/bin/env python3
"""统一测试入口 —— 一键门禁。

默认只跑**离线组**：不碰网络，几秒出结果，改完代码随手就能跑。
加 `--online` 才带上联网 e2e 组（要打 NPC、总局、360、搜狗、税屋这些
真实站点，受网络和对方限流影响，不适合当"每次必过"的门禁）。

用法：
    python tests/run_all.py              # 离线门禁（默认）
    python tests/run_all.py --online     # 离线 + 联网都跑
    python tests/run_all.py --list       # 只列有哪些组，不执行

退出码：全过 0，有失败 1——可以直接挂到 CI 或 git hook 上。
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

PY = sys.executable


def _compile_all():
    """编译 scripts/ 与 tests/ 下所有 .py，挡住语法错误这道最基本的关。"""
    targets = sorted(SCRIPTS_DIR.glob("*.py")) + sorted(TESTS_DIR.glob("*.py"))
    if not targets:
        return False, "没找到任何 .py 文件"
    r = subprocess.run([PY, "-m", "py_compile", *[str(t) for t in targets]],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        return True, f"{len(targets)} 个文件编译通过"
    return False, (r.stderr or r.stdout or "").strip()


# 离线组：只依赖本地代码与打桩，跑得快、结果稳，适合做"每次必过"的门禁
OFFLINE_GROUP = [
    ("语法编译（scripts + tests）", _compile_all),
    ("文档-代码契约（CLI 参数是否真的存在）", [PY, str(TESTS_DIR / "check_doc_cli.py")]),
    ("文档契约规则自检（片段级检查确实有效）", [PY, str(TESTS_DIR / "test_check_doc_cli.py")]),
    ("统一请求层守门（禁止绕过 tax_http）", [PY, str(TESTS_DIR / "test_http_layer.py")]),
    ("fgk 离线用例（翻页/正文/缓存）", [PY, str(TESTS_DIR / "test_tax_fgk.py")]),
    ("NPC 串行闸用例（含跨进程）", [PY, str(TESTS_DIR / "test_npc_gate.py")]),
    ("detail 缓存用例（命名空间/留痕/正文红线）", [PY, str(TESTS_DIR / "test_detail_cache.py")]),
    ("服务端路由用例（9 路由/状态码/打桩上游）", [PY, str(TESTS_DIR / "test_server_routes.py")]),
    ("评测集规则用例", [PY, str(TESTS_DIR / "test_eval_set.py")]),
]

# 联网组：真打外部站点，通不过可能是网络或对方限流，不当作代码问题
ONLINE_GROUP = [
    ("全链路 e2e（联网）", [PY, str(TESTS_DIR / "test_tax_search.py")]),
]


def _tail(text, n=4):
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _run_item(item):
    """跑一项。item 是 callable（返回 (ok, detail)）或 argv 列表。"""
    t0 = time.time()
    if callable(item):
        ok, detail = item()
    else:
        r = subprocess.run(item, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=str(PROJECT_ROOT))
        ok = r.returncode == 0
        detail = _tail(r.stdout)
        if not ok:
            detail = _tail((r.stdout or "") + (r.stderr or ""), 8)
    return bool(ok), detail, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description="统一测试门禁")
    ap.add_argument("--online", action="store_true",
                    help="额外跑联网 e2e 组（依赖真实站点，可能因限流失败）")
    ap.add_argument("--list", action="store_true", help="只列出有哪些组，不执行")
    args = ap.parse_args()

    groups = [("离线组（默认，不联网）", OFFLINE_GROUP)]
    if args.online:
        groups.append(("联网组（真打外部站点）", ONLINE_GROUP))

    if args.list:
        for gname, items in groups:
            print(f"\n[{gname}]")
            for name, _ in items:
                print(f"  - {name}")
        return 0

    total = passed = 0
    failed_names = []
    for gname, items in groups:
        print(f"\n{'=' * 64}")
        print(f"  {gname}")
        print("=" * 64)
        for name, item in items:
            total += 1
            ok, detail, dt = _run_item(item)
            if ok:
                passed += 1
            else:
                failed_names.append(name)
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}  （{dt:.1f}s）")
            if detail:
                for ln in detail.splitlines():
                    print(f"         {ln}")

    print(f"\n{'=' * 64}")
    print(f"  门禁结果：{passed}/{total} 通过")
    if failed_names:
        print("  未通过：" + "、".join(failed_names))
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
