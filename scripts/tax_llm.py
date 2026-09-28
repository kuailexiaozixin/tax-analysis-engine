#!/usr/bin/env python3
"""外部模型调用的付费闸门。

技能本体的全部工作（题型判定、分轮检索、依据定级、分层作答）只走公开的法规
接口，不碰任何模型通道，装完即用、零费用。会走到模型上的只有两处：
`/api/ai-interpret` 的通俗解读，和 `tests/eval_answer.py` 的答题评测。
两处都记在**使用者自己的账号**上——模型 CLI 用的是它自己配置的通道，
不是本技能配的通道，所以一次批量评测花的是别人账户里的钱。

因此这里的默认值是关死，而且刻意不做"自动找到本机装了什么 CLI 就拿来用"
那件事：探测得到就能用，等于把花费的决定权从用户手里拿走。开启要同时满足
两个环境变量——一个表明"知道这要花钱"，一个点名用哪个命令：

    TAX_ENABLE_PAID_LLM=1
    TAX_LLM_CMD=/绝对路径/claude.cmd

`TAX_LLM_TIMEOUT` 可选，单次调用上限秒数，默认 120。
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

ENABLE_ENV = "TAX_ENABLE_PAID_LLM"
CMD_ENV = "TAX_LLM_CMD"
TIMEOUT_ENV = "TAX_LLM_TIMEOUT"
DEFAULT_TIMEOUT = 120

# 上游账户问题的回显形态。宁可多列：把额度耗尽认成"模型答错"，
# 一批评测就会在零分上继续烧钱重试。
QUOTA_PATTERNS = (
    r"\b402\b", r"insufficient balance", r"payment required", r"credit balance",
    r"\bquota\b", r"billing", r"are insufficient", r"余额不足", r"欠费", r"额度不足",
)

# 与钱无关的本地故障（CLI 版本不认模型名、路径写错、超时）。
LOCAL_PATTERNS = (r"is not a model", r"unknown model", r"ENOENT",
                  r"not recognized", r"系统找不到指定的")


class SpendRefused(RuntimeError):
    """闸门未开：没有用户明确的授权，不得发起模型调用。"""


class QuotaExhausted(RuntimeError):
    """上游账户没钱了。调用方必须停掉整批，一次都不许重试。"""


def channel():
    """返回 (可执行命令, 说明)。闸门没开时命令为空串，说明里写清怎么开。

    只认环境变量，绝不猜路径。返回的说明要能直接照着敲，报错才不用二次搜索。
    """
    if os.environ.get(ENABLE_ENV, "").strip().lower() not in ("1", "true", "yes"):
        return "", (f"外部模型调用默认关闭：这一步会花掉模型通道所属账号的额度，"
                    f"不是本技能配的通道。确要开启，设置 {ENABLE_ENV}=1 与 "
                    f"{CMD_ENV}=<模型 CLI 的绝对路径> 后重试。")
    cmd = os.environ.get(CMD_ENV, "").strip()
    if not cmd:
        return "", (f"已设 {ENABLE_ENV}=1，但没设 {CMD_ENV}：本技能不自动探测本机装了哪个 "
                    f"CLI，避免拿错通道计费。请设为模型命令的绝对路径。")
    p = Path(cmd)
    if not p.exists():
        return "", f"{CMD_ENV} 指向的文件不存在：{cmd}"
    return cmd, ""


def is_open():
    """闸门是否已开（供只读判断，如接口决定返不返回 503）。"""
    return bool(channel()[0])


def quota_text(detail: str) -> str:
    """从输出里挑出与钱有关的那行；挑不到就返回空串。

    数字模式带词边界，`req_4027ab` 这类请求 id 里的散数字不算额度信号。
    """
    low = (detail or "").lower()
    for pat in QUOTA_PATTERNS:
        m = re.search(pat, low)
        if m:
            line = next((ln for ln in detail.splitlines()
                         if m.group(0).lower() in ln.lower()), detail.strip()[:200])
            return line.strip()[:200]
    return ""


def local_failure(detail: str) -> bool:
    """判断是不是本机 CLI 自身的配置问题（不该计入上游故障，也不该重试）。"""
    low = (detail or "").lower()
    return any(re.search(p.lower(), low) for p in LOCAL_PATTERNS)


def ask(prompt_text: str, timeout: int = 0) -> str:
    """经闸门调用一次模型，返回标准输出。

    失败时分三类抛出，让调用方知道该停还是该跳过：闸门没开（SpendRefused）、
    上游没钱（QuotaExhausted，重试只会继续扣）、本机 CLI 问题（RuntimeError）。
    超时按调用方给的秒数计，不给用 DEFAULT_TIMEOUT——长批次里不设上限
    会让一个卡住的进程把整批钉住。
    """
    cmd, why = channel()
    if not cmd:
        raise SpendRefused(why)
    timeout = timeout or int(os.environ.get(TIMEOUT_ENV, "") or DEFAULT_TIMEOUT)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8",
                                     delete=False) as pf:
        pf.write(prompt_text)
        name = pf.name
    try:
        r = subprocess.run(f'"{cmd}" --print < "{name}"', capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=str(Path.home()), shell=True)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"模型调用超时（{timeout}s）") from e
    finally:
        os.unlink(name)
    out = (r.stdout or "").strip()
    if r.returncode == 0 and out:
        return out
    # 报错正文可能走标准输出（"API Error: 402 Insufficient Balance"），stderr 那行
    # 可能只是无关的模型名告警。判额度时两边都看，留证据时按优先级取，
    # 否则拼接后截尾会把真正的原因挤出窗口。
    detail = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    q = quota_text(detail)
    if q:
        raise QuotaExhausted(q)
    why = ((r.stdout or "").strip() or (r.stderr or "").strip())
    raise RuntimeError(f"模型调用失败：{why[-260:] or f'退出码 {r.returncode}'}")


def cost_notice(n_calls: int, arms: str = "") -> str:
    """花费预告文本：批量调用前必须把它打给用户，看到数字才知道要多大。"""
    return (f"将发起 {n_calls} 次外部模型调用{('（' + arms + '）') if arms else ''}，"
            f"费用由 {os.environ.get(CMD_ENV, '未配置的模型 CLI')} 所用账号承担；"
            f"单次超时 {os.environ.get(TIMEOUT_ENV, '') or DEFAULT_TIMEOUT}s。")
