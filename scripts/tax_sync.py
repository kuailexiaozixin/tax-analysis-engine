# -*- coding: utf-8 -*-
"""离线目录的"内容版本 diff 同步器"。

为什么不用日期判新旧：官方 xlsx/清单直链的版本节点每月都变、文件名里带的
日期只是录入项。真正可靠的新旧信号是**资源直链本身变没变**（现抓 → 与
state.json 记录的直链比对 → 变了才下载重建）。本模块把这套"定位 → diff →
下载 → 构建 → 落 state（含内容 SHA1）"抽成通用能力，任何固定资源（目录、
税率表、清单）传进三个回调即可复用，不必各写一遍。

三个回调：
    locate()      → 现抓远端，返回 {"url": 必填, "version": 可选, "ext": 可选}
    build(path)   → 下载完成后用产物文件重建本地索引（自行落盘）
    no-op         → 直链未变且未 --force 时什么都不做

产物与状态分目录存放：
    data/sync/<source>/latest<ext>   下载到的原始文件
    data/sync/<source>/state.json    上次同步的 url / version / sha1 / 时间戳

硬性守卫（照搬既有实现并补一层）：
  - 内容少于 MIN_CONTENT_BYTES 视为异常页（错误页 / 半截响应），不覆盖旧产物；
  - 下载成功但内容 SHA1 与上次相同 → 不重建，只刷新时间戳（白下不白烧构建）。

网络出口统一走 tax_http.get（bare requests 被 tests/test_http_layer.py 门禁拦下）。
"""

import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import tax_http  # noqa: E402

DATA_ROOT = _HERE.parent / "data" / "sync"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT_PAGE = 60      # 抓栏目页定位直链
TIMEOUT_FILE = 180     # 下载产物文件
MIN_CONTENT_BYTES = 10000   # 小于此字节数判为异常页（沿用官方目录实现的守卫阈值）


class SyncError(Exception):
    """同步过程中的可预期错误（定位失败 / 下载异常 / 构建失败）。"""


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Synchronizer:
    """把一个固定远端资源同步成本地索引的通用外壳。"""

    def __init__(self, source: str, locate, build, *, ext: str = ".dat",
                 data_root: Path = None):
        self.source = source
        self.locate = locate
        self.build = build
        self.ext = ext
        root = Path(data_root) if data_root else DATA_ROOT
        self.dir = root / source
        self.state_path = self.dir / "state.json"
        self.artifact_path = self.dir / ("latest" + ext)

    # ── HTTP 出口：一个方法，便于打桩 ──────────────────────────────────
    def fetch_bytes(self, url: str, *, timeout: int) -> bytes:
        """拉一个文件的原始字节。所有下载只从这里出。"""
        resp = tax_http.get(url, headers={"User-Agent": UA},
                            timeout=timeout, verify=True)
        if resp.status_code != 200:
            raise SyncError(f"下载返回 HTTP {resp.status_code}：{url}")
        return resp.content

    # ── 主流程 ─────────────────────────────────────────────────────────
    def sync(self, *, check_only: bool = False, force: bool = False) -> dict:
        """跑一轮：定位 → 比直链 → （需要才）下载 → （内容变了才）构建。

        Args:
            check_only: 只报有没有新版本，不下载、不构建。
            force: 即使直链未变也重新下载；构建仍只在内容 SHA1 变化时触发。

        Returns:
            {"source","检查时间","动作","成功", 以及 url/version/本地版本/…}
        """
        res = {"source": self.source, "检查时间": _now(), "动作": "无", "成功": True}
        state = _read_json(self.state_path, {})
        res["本地链接"] = state.get("url", "")
        res["本地版本"] = state.get("version", "")

        try:
            found = self.locate()
        except Exception as e:
            res.update({"成功": False, "动作": "定位失败", "错误": str(e)})
            return res
        if not found or not found.get("url"):
            res.update({"成功": False, "动作": "定位失败",
                        "错误": "locate() 未返回带 url 的字典"})
            return res

        url = found["url"]
        version = found.get("version", "") or ""
        res["远端链接"] = url
        res["远端版本"] = version

        changed = url != state.get("url", "")
        res["有更新"] = changed

        if not changed and not force:
            res["动作"] = "无更新"
            state.update({"url": url, "version": version,
                          "last_checked": res["检查时间"]})
            _write_json(self.state_path, state)
            return res

        if check_only:
            res["动作"] = "发现新版本（--check 未下载）"
            return res

        try:
            data = self.fetch_bytes(url, timeout=TIMEOUT_FILE)
        except Exception as e:
            res.update({"成功": False, "动作": "下载失败", "错误": str(e)})
            return res
        if len(data) < MIN_CONTENT_BYTES:
            res.update({"成功": False, "动作": "下载异常",
                        "错误": f"内容仅 {len(data)} 字节，低于 {MIN_CONTENT_BYTES} 阈值，"
                                f"疑为错误页；未覆盖旧产物"})
            return res

        sha = _sha1(data)
        res["字节数"] = len(data)
        res["SHA1"] = sha

        if (not force) and sha == state.get("sha1") and state.get("url"):
            # 直链变了、内容却没变：只刷新时间戳与链接，不白烧一次构建。
            res["动作"] = "无更新（内容未变）"
            state.update({"url": url, "version": version, "sha1": sha,
                          "last_checked": res["检查时间"]})
            _write_json(self.state_path, state)
            return res

        self.dir.mkdir(parents=True, exist_ok=True)
        self.artifact_path.write_bytes(data)

        try:
            built = self.build(str(self.artifact_path))
        except Exception as e:
            res.update({"成功": False, "动作": "构建失败",
                        "错误": str(e)[:800]})
            return res

        # 构建回调可返回统计（含版本日期）。目录直链本身不带版本，版本写在文件里，
        # 所以成功构建后采纳它——否则 state 永远记着空版本，回显也看不到更新到了哪一版。
        new_version = (built or {}).get("版本日期") if isinstance(built, dict) else ""
        if new_version:
            version = new_version
        state.update({"url": url, "version": version, "sha1": sha,
                      "updated_at": res["检查时间"], "last_checked": res["检查时间"]})
        _write_json(self.state_path, state)
        res["动作"] = "已更新"
        res["新版本"] = version
        return res


class ListSynchronizer:
    """把"分页 JSON 列表"同步成本地索引的外壳（tax_sync 的分页 target）。

    与 Synchronizer 的区别：单文件资源的新旧信号是那条直链变没变，列表没有
    单一直链，改的是"集合"。所以这里比对的是抓全分页后按 sig_fields 拼出的
    集合 SHA1——任一行的任一 sig_field 变了（哪怕 url 不变、只有时效性从
    尚未生效翻成全文有效），SHA1 就变，才触发重建。

    三个回调 / 参数：
        fetch_page(page:int) → {"rows":[已规范化 dict,...], "total":int}
                               1-based 翻页；只依赖此回调即可探量与爬全。
        build(rows:list)     → dict 统计（含"版本日期"/"条目数"），自行落盘索引。
        sig_fields           → 参与集合 SHA1 的字段名，默认 ["url"]。列表源
                               应按会变的字段扩列（如 url+时效性+发文字号），
                               否则时效性翻转检测不到。

    硬性守卫：
      - 空集合（total 或 rows 全空）视为异常，不覆盖旧产物；
      - 超过 max_pages 仍未爬满 total → 判为截断，不构建（宁可少更不写坏）。

    网络出口经 fetch_page 走 tax_http（bare requests 被门禁拦下）。
    """

    def __init__(self, source: str, fetch_page, build, *,
                 sig_fields=("url",), page_size: int = 50, max_pages: int = 1000,
                 data_root: Path = None):
        self.source = source
        self.fetch_page = fetch_page
        self.build = build
        self.sig_fields = tuple(sig_fields)
        self.page_size = int(page_size)
        self.max_pages = int(max_pages)
        root = Path(data_root) if data_root else DATA_ROOT
        self.dir = root / source
        self.state_path = self.dir / "state.json"

    def _sig(self, rows) -> str:
        canon = sorted(_sha1(json.dumps([r.get(f, "") for f in self.sig_fields],
                                        ensure_ascii=False).encode("utf-8"))
                       for r in rows)
        return _sha1("".join(canon).encode("utf-8"))

    def probe(self) -> dict:
        """只抓第一页拿 total，作为 --check 的廉价变更信号。"""
        first = self.fetch_page(1) or {}
        return {"total": int(first.get("total") or 0),
                "首页条数": len(first.get("rows") or [])}

    def crawl(self):
        """按 page_size 爬满 total，返回 (rows, total)。"""
        page, collected, total = 1, [], None
        while True:
            res = self.fetch_page(page) or {}
            rows = res.get("rows") or []
            if total is None:
                total = int(res.get("total") or 0)
            collected.extend(rows)
            if not rows or (total and len(collected) >= total):
                break
            if page >= self.max_pages:
                raise SyncError(
                    f"翻到第 {page} 页（每页 {self.page_size}）仍只有 {len(collected)} 条，"
                    f"未到 total={total}，判为截断，放弃构建")
            page += 1
        return collected, (total or 0)

    def sync(self, *, check_only: bool = False, force: bool = False) -> dict:
        res = {"source": self.source, "检查时间": _now(), "动作": "无", "成功": True}
        state = _read_json(self.state_path, {})
        res["本地条目数"] = state.get("count", 0)
        res["本地总数"] = state.get("total", 0)

        if check_only:
            try:
                p = self.probe()
            except Exception as e:
                res.update({"成功": False, "动作": "探测失败", "错误": str(e)})
                return res
            changed = p["total"] != state.get("total", -1)
            res["远端总数"] = p["total"]
            res["有更新"] = changed
            res["动作"] = "发现总数变更（--check 未抓取）" if changed else "无更新"
            return res

        try:
            rows, total = self.crawl()
        except Exception as e:
            res.update({"成功": False, "动作": "抓取失败", "错误": str(e)[:800]})
            return res

        if total == 0 or not rows:
            res.update({"成功": False, "动作": "抓取异常",
                        "错误": f"抓到 {len(rows)} 条 / total={total}，判为异常，未覆盖旧索引"})
            return res

        sha = self._sig(rows)
        res["条目数"] = len(rows)
        res["总数"] = total
        res["集合SHA1"] = sha
        changed = sha != state.get("set_sha1")
        res["有更新"] = changed

        if changed or force:
            try:
                built = self.build(rows)
            except Exception as e:
                res.update({"成功": False, "动作": "构建失败", "错误": str(e)[:800]})
                return res
            version = (built or {}).get("版本日期", "") if isinstance(built, dict) else ""
            state.update({"total": total, "count": len(rows), "set_sha1": sha,
                          "version": version, "updated_at": res["检查时间"],
                          "last_checked": res["检查时间"]})
            _write_json(self.state_path, state)
            res["动作"] = "已更新"
            res["新版本"] = version
            res["新版本日期"] = version
            return res

        state.update({"total": total, "count": len(rows), "last_checked": res["检查时间"]})
        _write_json(self.state_path, state)
        res["动作"] = "无更新（集合未变）"
        return res


# ── CLI ────────────────────────────────────────────────────────────────────

def _emit(res: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        head = "[成功]" if res.get("成功") else "[失败]"
        print(f"{head} {res.get('source')} → {res.get('动作')}")
        for k in ("本地版本", "远端版本", "新版本", "远端链接", "字节数", "错误"):
            if res.get(k):
                print(f"    {k}：{res[k]}")
    return 0 if res.get("成功") else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="离线目录内容版本 diff 同步器")
    ap.add_argument("--source", help="已注册的数据源名")
    ap.add_argument("--check", action="store_true", help="只检查不下载")
    ap.add_argument("--force", action="store_true", help="强制重新下载")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--list-sources", action="store_true", help="列已注册数据源")
    a = ap.parse_args(argv)

    if a.list_sources or not a.source:
        print(__doc__)
        return 0

    reg = _load_registry()
    if a.source not in reg:
        print(f"[错误] 未注册的数据源：{a.source}（已注册：{', '.join(reg) or '无'}）")
        return 2
    spec = reg[a.source]
    if spec["kind"] == "list":
        syn = ListSynchronizer(a.source, spec["fetch_page"], spec["build"],
                               sig_fields=spec["sig_fields"],
                               page_size=spec["page_size"], max_pages=spec["max_pages"])
    else:
        syn = Synchronizer(a.source, spec["locate"], spec["build"], ext=spec.get("ext", ".dat"))
    res = syn.sync(check_only=a.check, force=a.force)
    return _emit(res, a.json)


# 数据源注册表：source 名 → {kind, ...回调}
_REGISTRY = {}


def register(source: str, locate, build, *, ext: str = ".dat") -> None:
    """让单文件数据源把自己的定位/构建回调挂进 CLI。"""
    _REGISTRY[source] = {"kind": "file", "locate": locate, "build": build, "ext": ext}


def register_list(source: str, fetch_page, build, *,
                  sig_fields=("url",), page_size: int = 50,
                  max_pages: int = 1000) -> None:
    """让分页列表数据源把自己的翻页抓取/构建回调挂进 CLI。"""
    _REGISTRY[source] = {"kind": "list", "fetch_page": fetch_page, "build": build,
                         "sig_fields": sig_fields, "page_size": page_size,
                         "max_pages": max_pages}


def _load_registry() -> dict:
    """惰性导入已注册的数据源模块，填充注册表。

    新数据源只要在本函数里 import 一次并调用 register()/register_list()，
    CLI 即可用 --source 调它。
    """
    try:
        import preference  # noqa: F401  subskills/tax-preference/preference.py
    except Exception:
        pass
    try:
        import tax_gov_list  # noqa: F401  scripts/tax_gov_list.py
        tax_gov_list._register()
    except Exception:
        pass
    return _REGISTRY


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
