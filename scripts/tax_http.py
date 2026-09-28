#!/usr/bin/env python3
"""统一 HTTP 请求层 —— 本模块是 scripts/ 下裸 requests 调用的唯一出口。

为什么要有这一层
----------------
同一件事（发一个 GET）原先在三个模块里各写一遍：

    tax_detail.py   _request / get_download_url / download_file   3 处
    tax_fgk.py      fetch_fgk_body                                1 处
    tax_search.py   _request                                      1 处

于是 UA、SSL 校验开关、超时值在三处各定一份，改一处容易漏掉另外两处。
收成一个出口之后，"项目里还有谁在发请求、以什么姿势发"能被一眼看全——
想给所有税务站点统一加一个请求头，只需要动这里。

verify 为什么是必填参数
-----------------------
这三个模块原本的 SSL 策略并不一致：

    tax_fgk.py     走 requests 的默认值 verify=True
    tax_detail.py  走 VERIFY_SSL（默认 False）
    tax_search.py  走 VERIFY_SSL（默认 False）

如果本层给 verify 一个默认值，就等于悄悄改掉其中一方的线上行为。
设成必填，这个差异会留在调用点上、看得见；将来要统一是另一件独立的事。

不进这一层的模块
----------------
tax_shui5 / tax_so360 / tax_wechat / tax_web_search 各自维护 requests.Session
（WAF 挑战应对、cookie 恢复、页面二次跳转），请求形状与这里不同。
强行合并只会把它们的特例塞进通用层，所以它们保持自管。
"""

import os

import requests

# SSL 校验总开关，默认关：NPC 法规库与总局法规库在本机的证书链不完整，
# 打开会直接连不上，需要时用 TAX_SEARCH_VERIFY_SSL=1 打开。
#
# 注意：这个常量是给调用方取用的"默认值来源"，不是本层的隐式默认——
# 每个调用点仍必须把 verify 显式传进来。
VERIFY_SSL = os.getenv("TAX_SEARCH_VERIFY_SSL", "0") == "1"


def request(method: str, url: str, *, headers: dict, timeout: float,
            verify: bool, **kwargs) -> requests.Response:
    """发一个请求。

    参数一律显式传递，本层不做隐式补全，也不改返回值，好让调用点的行为
    与直接调 requests 逐字等价。kwargs 原样透传给 requests
    （params / data / json / allow_redirects / stream 等）。
    """
    return requests.request(method, url, headers=headers, timeout=timeout,
                            verify=verify, **kwargs)


def get(url: str, *, headers: dict, timeout: float, verify: bool,
        **kwargs) -> requests.Response:
    """GET 的简写，参数含义与 request() 完全一致。

    内部走 requests.get，而不是 request("GET", ...)。两者在 requests 里本就
    等价，但 tax_detail / tax_fgk 原来的调用点用的就是 requests.get，离线用例
    里对 requests.get 打的桩因此不必跟着改（改了桩点等于把测试和实现绑死）。
    """
    return requests.get(url, headers=headers, timeout=timeout,
                        verify=verify, **kwargs)
