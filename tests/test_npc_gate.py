#!/usr/bin/env python3
"""NPC 串行闸（tax_search.NpcSerialGate）的用例。

"NPC 必须串行"原先只是 SKILL.md 里的一句话，现在由代码强制。这个闸要是写错，
会直接把所有检索卡住或锁死，所以几条底线都要有用例钉住：

  1. 能进能出，且可反复使用（锁真的被释放）
  2. 锁被占时第二个持有者必须等，等不到就超时（不是静默通过）
  3. **跨进程**互斥——这才是这个闸存在的理由，同进程内的锁拦不住两个脚本
  4. 同进程多线程共用一把闸时排队，不因文件锁不可重入而报错
  5. 超时抛错后闸自己不能被锁死

全部离线，不碰网络；锁文件都落在临时目录。
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import tax_search  # noqa: E402
from tax_search import NpcSerialGate  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


def _new_gate(timeout: float = 5.0):
    """造一把落在临时目录上的闸，连同它的临时目录一起返回。"""
    td = tempfile.TemporaryDirectory()
    return NpcSerialGate(path=Path(td.name) / "gate.lock", timeout=timeout), td


def test_gate_basic():
    """能进能出，且可反复使用——锁必须真的被释放。"""
    gate, td = _new_gate()
    try:
        for _ in range(3):
            with gate:
                pass
        print("  [PASS] 反复进出 3 次均成功（锁正确释放）")
    finally:
        td.cleanup()
    return True


def test_second_holder_times_out():
    """锁被占时，第二个持有者要等；等不到必须抛 TimeoutError，不能静默通过。"""
    gate, td = _new_gate()
    second = NpcSerialGate(path=gate.path, timeout=0.6)
    try:
        with gate:
            t0 = time.monotonic()
            got = False
            try:
                with second:
                    got = True
            except TimeoutError:
                pass
            waited = time.monotonic() - t0
        assert not got, "锁已被占用，第二个持有者不该拿到"
        assert waited >= 0.5, f"应至少等满 0.6s 超时，实际 {waited:.2f}s"
        print(f"  [PASS] 第二个持有者等待 {waited:.2f}s 后超时（未静默通过）")
    finally:
        td.cleanup()
    return True


def test_cross_process_mutual_exclusion():
    """跨进程互斥：别的进程持锁时，本进程必须等它释放。

    这是这个闸存在的理由——同进程内的锁拦不住两个并发的脚本进程，
    而 NPC 一旦被并发访问就会限流（还不回 429）。
    """
    lock_path = Path(tempfile.gettempdir()) / f"npc-gate-test-{os.getpid()}.lock"
    hold = 1.5

    child_code = (
        "import sys, time\n"
        f"sys.path.insert(0, r'{SCRIPT_DIR}')\n"
        "from tax_search import NpcSerialGate\n"
        f"g = NpcSerialGate(path=r'{lock_path}', timeout=30)\n"
        "with g:\n"
        "    print('HELD', flush=True)\n"
        f"    time.sleep({hold})\n"
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-c", child_code],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", env=env)
    try:
        line = p.stdout.readline().strip()
        assert line == "HELD", f"子进程未能持锁，输出：{line!r}"

        gate = NpcSerialGate(path=lock_path, timeout=30)
        t0 = time.monotonic()
        with gate:
            waited = time.monotonic() - t0
        assert waited >= hold * 0.6, \
            f"应等子进程放锁（约 {hold}s），实际只等了 {waited:.2f}s"
        print(f"  [PASS] 子进程持锁期间本进程等待 {waited:.2f}s 后才取得")
    finally:
        p.wait(timeout=30)
        p.stdout.close()
        p.stderr.close()
        lock_path.unlink(missing_ok=True)
    return True


def test_threads_serialize():
    """同进程多线程共用一把闸：要排队，不能因文件锁不可重入而报错。

    Windows 的文件锁按"进程 + 区域"算，同一进程重复加锁会直接失败（不像
    类 Unix 的 flock 可重入）。若不先在进程内排队，tax_aggregator 的
    ThreadPoolExecutor 一并发就会炸。
    """
    gate, td = _new_gate(timeout=10)
    done = []
    guard = threading.Lock()

    def worker(n):
        with gate:
            with guard:
                done.append(n)
            time.sleep(0.15)

    try:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert len(done) == 4, f"4 个线程都该跑完，实际 {done}"
        print(f"  [PASS] 4 线程共用一把闸串行完成，无重复加锁失败：{sorted(done)}")
    finally:
        td.cleanup()
    return True


def test_gate_usable_after_timeout():
    """超时抛错之后，锁不能残留在自己手里——下一次还得能用。"""
    gate, td = _new_gate(timeout=0.4)
    blocked = NpcSerialGate(path=gate.path, timeout=0.4)
    try:
        with gate:
            try:
                with blocked:
                    pass
            except TimeoutError:
                pass
        # gate 已退出；此时 blocked 应当能顺利拿到
        with blocked:
            pass
        print("  [PASS] 超时后闸已释放，可再次取得（未自我锁死）")
    finally:
        td.cleanup()
    return True


def test_request_uses_gate():
    """接线检查：_request 里必须真的套了这个闸，而不是只定义了类。

    NPC 的每次请求都要过闸；漏了这一步，前面几条用例全绿也白搭。
    """
    import inspect
    src = inspect.getsource(tax_search._request)
    assert "npc_gate" in src, "_request 没有使用 npc_gate，护栏未接线"
    assert "with npc_gate" in src.replace("\n", " ").replace("  ", " "), \
        "_request 里没有以 with npc_gate 的形式上锁"
    print("  [PASS] _request 已套用 npc_gate")
    return True


def test_detail_shares_same_gate():
    """tax_detail 必须与 tax_search 共用同一把闸（同一个对象）。

    详情接口与检索接口打的是同一个 NPC 站。若两边各建一把锁，跨进程互斥就
    形同虚设——所以不但要分别上锁，还必须是同一把。
    """
    import inspect
    import tax_detail
    assert tax_detail.npc_gate is tax_search.npc_gate, \
        "tax_detail 与 tax_search 必须是同一个 npc_gate 对象"
    src = inspect.getsource(tax_detail._request)
    assert "with npc_gate" in src, "tax_detail._request 没有上锁"
    print("  [PASS] tax_detail 与 tax_search 共用同一把 npc_gate，且已上锁")
    return True


def main():
    tests = [
        ("闸能进能出", test_gate_basic),
        ("占用时第二个持有者超时", test_second_holder_times_out),
        ("跨进程互斥", test_cross_process_mutual_exclusion),
        ("同进程多线程排队", test_threads_serialize),
        ("超时后不自我锁死", test_gate_usable_after_timeout),
        ("_request 已接线", test_request_uses_gate),
        ("tax_detail 共用同一把闸", test_detail_shares_same_gate),
    ]

    all_passed = 0
    for name, fn in tests:
        print(f"\n{'─' * 50}")
        print(f"> {name}")
        try:
            fn()
            all_passed += 1
            print(f"  [{PASS}] {name}")
        except Exception as e:
            print(f"  [{FAIL}] {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"Results: {all_passed}/{len(tests)} passed")
    print(f"{'=' * 60}")
    return 0 if all_passed == len(tests) else 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
