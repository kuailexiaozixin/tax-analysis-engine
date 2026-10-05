#!/usr/bin/env python3
"""限流实测探针 —— 给 `references/source_defects.md` 的「限流记录实测台账」产出证据。

为什么要这么查：串行闸与退避都是照"某年某月并行被限流过"建的，而站点风控是
随时间变化的活物。记录过期之后，闸就成了没人敢拆、也没人证明它还有必要的保守。
这个脚本把一条记录当场重量一遍，回吐台账要的三字段：

    最近实测日期 / 实测方法（连发 N 次 + M 线程 × 每线程 K 次）/ 当时结果

`当时结果` 按失败形态逐项计数（挑战页、429、5xx、断连、反爬跳转各几次），所以
"没复现"与"复现了"读到的是同一张表；真被限的那天，形态、状态码、字节数也留在
同一份回显里，直接抄进台账，不必再凭记忆补写。

用法：
    python tests/probe_rate_limit.py --target npc --burst 15 --threads 8 --per-thread 2
    python tests/probe_rate_limit.py --target fgk-list --dry-run   # 只看要发的请求，一个不发

请求总量被 `MAX_REQUESTS` 卡住：这是查询式访问的复测，不是压测。想刷量级就调
这个常量，不调它脚本会直接拒绝并说明为什么拒——不给"顺手多跑几轮"留口子。
更高量级的阈值本脚本不探测，台账里也就不得写成"已探到上限"。
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import requests  # noqa: E402
import urllib3  # noqa: E402

import tax_http  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

#: 单次运行的请求总量上限。超过就拒绝执行，见模块文档那句"不是压测"。
MAX_REQUESTS = 60


# ── 取"生产路径真实要发的那一次请求"───────────────────────────────────────
class _Stub:
    """让被拦住的调用正常走完，返回值只求形状对，内容一律当空清单。"""

    status_code = 200
    headers = {"Content-Type": "application/json"}
    url = ""

    def __init__(self, text='{"results":{"data":{"total":0,"results":[]}}}', content=None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


def capture_request(build):
    """执行 build()，截住其中第一次 tax_http.request，返回 (method, url, kwargs)。

    为什么不手写一份请求副本：NPC 的 payload 有十几个字段、search5 的 filters 还在
    长，手写副本在源模块改版之后会照发一份过时的请求，然后被"对方给了 5xx"当成
    限流形态记进台账。从生产路径抓就永远与真实取数同一形状。
    """
    real, box = tax_http.request, {}

    def spy(method, url, **kw):
        box.setdefault("call", (method, url, kw))
        return _Stub()

    tax_http.request = spy
    try:
        build()
    finally:
        tax_http.request = real
    if "call" not in box:
        raise RuntimeError("这条生产路径没有经过 tax_http.request，探针取不到请求形状")
    return box["call"]


# ── 四个入口：记录原话、请求形状、失败形态判据 ──────────────────────────────
def _npc_call():
    import tax_search
    return capture_request(lambda: tax_search.search_tax("增值税", size=5))


def _fgk_call():
    import tax_gov_list
    return capture_request(lambda: tax_gov_list.make_fetch_page() (1))


def _so360_call():
    import tax_so360
    return ("GET", f"{tax_so360.SEARCH_URL}?q={quote('企业所得税法')}&pn=1",
            {"headers": tax_so360.HEADERS, "timeout": tax_so360.TIMEOUT, "verify": False})


def _sogou_call():
    import tax_wechat
    return ("GET", f"{tax_wechat.SOGOU_SEARCH}?type=2&query={quote('研发费用加计扣除')}",
            {"headers": tax_wechat.HEADERS_SOGOU, "timeout": 15, "verify": False})


def _json_shape(r):
    """200 且 Content-Type 是 JSON 才算正常；其余按"多少字节的非 JSON 正文"记形态。"""
    ctype = (r.headers.get("Content-Type") or "").lower()
    if r.status_code == 200 and "json" in ctype:
        return "200-JSON"
    if r.status_code == 200:
        return f"200-非JSON正文({len(r.content)}字节)"
    return f"HTTP {r.status_code}"


def _npc_shape(r):
    """NPC 的限流形态判据直接用生产那一份，探针不自立一套。

    生产判据是 tax_search._is_challenge_page：HTTP 200 但正文是带
    `<noscript>` 与混淆 JS 的挑战页。探针另写一份就会两处演化，台账里
    "复现"与"没复现"量的就不是同一件事。
    """
    import tax_search
    if tax_search._is_challenge_page(r):
        return f"200-挑战页({len(r.content)}字节)"
    return _json_shape(r)


def _html_shape(marker):
    def fn(r):
        if r.status_code == 200 and marker in r.text:
            return f"200-{marker}"
        if r.status_code == 200:
            return "200-正常页"
        return f"HTTP {r.status_code}"
    return fn


def _sogou_shape(r):
    if "/antispider/" in (r.url or ""):
        return "跳转-antispider"
    return f"HTTP {r.status_code}" if r.status_code != 200 else "200-正常页"


TARGETS = {
    # 每条都带 record：台账里那句"记录原话"，探针回显时照抄，免得两处各写一份。
    "npc": {
        "record": "NPC 限流，并行必现",
        "endpoint": "POST https://flk.npc.gov.cn/law-search/search/list",
        "build_call": _npc_call,
        "shape": _npc_shape,
        "good": "200-JSON",
    },
    "fgk-list": {
        "record": "总局清单接口连发不限流（建闸时未取阈值）",
        "endpoint": "POST https://www.chinatax.gov.cn/getFileListByCodeId",
        "build_call": _fgk_call,
        "shape": _json_shape,
        "good": "200-JSON",
    },
    "so360": {
        "record": "360 对本机 IP 限流，回一份「访问异常出错」页",
        "endpoint": "GET https://m.so.com/s",
        "build_call": _so360_call,
        "shape": _html_shape(unescape("访问异常出错")),
        "good": "200-正常页",
    },
    "sogou": {
        "record": "公众号并发加压触发反爬",
        "endpoint": "GET https://weixin.sogou.com/weixin",
        "build_call": _sogou_call,
        "shape": _sogou_shape,
        "good": "200-正常页",
    },
}


def classify(target, method, url, kwargs, details):
    """发一次，把回显归成一个形态标签；异常也算一种形态，不吞。

    `details` 按标签留一份原文（首次出现时），台账要的"特征词、状态码、形态"
    就从这里抄——只留计数会把被限那天的证据压成一个词。
    """
    try:
        r = tax_http.request(method, url, **kwargs)
    except requests.RequestException as e:
        label = type(e).__name__
        details.setdefault(label, tax_http.short_reason(e))
        return label
    except Exception as e:                       # 闸超时之类的本地故障也要留名
        return f"本地-{type(e).__name__}"
    label = target["shape"](r)
    if label != target["good"]:
        details.setdefault(label, f"HTTP {r.status_code}，"
                                 f"{len(r.content)} 字节，"
                                 f"Content-Type {r.headers.get('Content-Type')}，"
                                 f"正文首 120 字 {r.text[:120]!r}")
    return label


def run(target, call, burst, threads, per_thread):
    method, url, kwargs = call
    total = burst + threads * per_thread
    if total > MAX_REQUESTS:
        raise SystemExit(
            f"请求总量 {total}（连发 {burst} + {threads} 线程 × 每线程 {per_thread}）"
            f"超过上限 {MAX_REQUESTS}。这个探针只做查询式访问的复测，不是压测；"
            f"要探更高量级请先改 MAX_REQUESTS，并在台账里写明探测到的边界。")
    print(f"数据源：{url}")
    print("请求形状（从生产路径抓取）："
          f"{method} body={str(kwargs.get('json') or kwargs.get('data') or '')[:160]}")
    if os.getenv("TAX_SEARCH_VERIFY_SSL", "0") == "1":
        print("注：TAX_SEARCH_VERIFY_SSL=1，证书链在本机不完整，这一趟可能是连不上而不是被限。")

    details = {}
    serial = Counter(classify(target, method, url, kwargs, details) for _ in range(burst))

    concurrent = Counter()
    lock = threading.Lock()

    def one(_):
        s = classify(target, method, url, kwargs, details)
        with lock:
            concurrent[s] += 1

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(one, range(threads * per_thread)))

    return total, serial, concurrent, details, time.time() - t0


def report(target, key, burst, threads, per_thread, serial, concurrent, details, secs):
    good = target["good"]
    all_counts = serial + concurrent
    hit = sum(v for k, v in all_counts.items() if k != good)
    print("\n=== 台账三字段（照抄进 references/source_defects.md 的对应行）===")
    print(f"记录：{target['record']}")
    print(f"入口：{target['endpoint']}")
    print(f"最近实测日期：{date.today().isoformat()}")
    print(f"实测方法：连发 {burst} 次（0 间隔）+ {threads} 线程 × 每线程 "
          f"{per_thread} 次（共 {burst + threads * per_thread} 次，耗时 {secs:.1f} 秒）")
    print(f"当时结果：{' / '.join(f'{k}×{v}' for k, v in sorted(all_counts.items()))}"
          f"；{'未复现' if hit == 0 else f'复现 {hit} 次'}")
    print(f"      分项：串行段 {' / '.join(f'{k}×{v}' for k, v in sorted(serial.items()))}"
          f"，并发段 {' / '.join(f'{k}×{v}' for k, v in sorted(concurrent.items()))}")
    for label, detail in details.items():
        if label != good:
            print(f"      形态留证 {label}：{detail}")
    print(f"      复跑命令：python tests/probe_rate_limit.py --target {key} "
          f"--burst {burst} --threads {threads} --per-thread {per_thread}")
    return {"record": target["record"], "hit": hit,
            "date": date.today().isoformat(),
            "method": f"连发 {burst} 次 + {threads} 线程 × 每线程 {per_thread} 次",
            "result": " / ".join(f"{k}×{v}" for k, v in sorted(all_counts.items()))}


def main():
    ap = argparse.ArgumentParser(description="限流记录实测探针")
    ap.add_argument("--target", required=True, choices=sorted(TARGETS))
    ap.add_argument("--burst", type=int, default=15, help="串行连发次数（0 间隔）")
    ap.add_argument("--threads", type=int, default=8, help="并发线程数")
    ap.add_argument("--per-thread", type=int, default=2, help="每线程请求数")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印抓到的请求形状，一个请求都不发")
    a = ap.parse_args()

    target = TARGETS[a.target]
    call = target["build_call"]()
    if a.dry_run:
        method, url, kwargs = call
        print(f"[dry-run] 将发 {method} {url}")
        print(f"[dry-run] kwargs={ {k: str(v)[:200] for k, v in kwargs.items()} }")
        print(f"[dry-run] 计划请求数：连发 {a.burst} + {a.threads} 线程 × {a.per_thread} "
              f"= {a.burst + a.threads * a.per_thread}（上限 {MAX_REQUESTS}）")
        return 0
    total, serial, concurrent, details, secs = run(target, call, a.burst, a.threads, a.per_thread)
    return 1 if report(target, a.target, a.burst, a.threads, a.per_thread,
                       serial, concurrent, details, secs)["hit"] else 0


if __name__ == "__main__":
    sys.exit(main())
