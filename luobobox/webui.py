"""内置 WebUI：让网关的 /dashboard/ 永远打得开。

背景（这是「后台管理打不开」的根因，且每次升级网关都必然复发）：
网关 codebuddy2api 的 web/dist 是**本地构建产物** —— 上游仓库的 web/.gitignore
把 dist/ 忽略了，Release 包里根本没有它。于是：

    rmtree(网关/web) + copytree(Release/web)  →  web/dist 被连根拔掉
    GET /dashboard/  →  503 {"detail":"WebUI 尚未构建；在 web/ 运行 vp install && vp build"}

updater.PRESERVE_SUBPATHS 能挡住「被删掉」，但挡不住另外两种情况：
  1. 这台机器从来没构建过（全新机器、或网关是手工拷来的）；
  2. 上游改了 web/src —— 保留下来的 dist 与新源码不匹配，页面会缺功能。

而且目标机器不一定有 Node/pnpm 工具链，指望用户自己 `vp build` 不现实。

所以萝卜盒自己带一份**预构建 dist**（打包进 assets/webui/），在三个时机补齐：
  · 网关升级完成之后（updater.apply_release）
  · 萝卜盒启动自检时（main_window._post_show_checks）
  · 用户点「网页版管理台」时（main_window._open_dashboard，现场自愈）

安全策略 —— 绝不覆盖用户自己的劳动成果：
  · dist 缺失            → 补齐
  · dist 带我们的戳且版本不同 → 刷新（内置的更新）
  · dist 存在但没有我们的戳 → 视为用户自行构建，**保持原样**
    （要强制覆盖得显式 force=True）
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .paths import resource_dir

# 内置 dist 在资源目录下的名字。打包时 assets/ 整体平铺进 _MEIPASS，
# 所以 resource_dir()/webui 在源码态与冻结态都拿得到。
BUNDLED_NAME = "webui"

# 我们写进 dist 的版本戳。有它就说明这个 dist 是萝卜盒装的。
STAMP_NAME = ".luobobox-webui.json"

# packaging/sync_webui.py 写进内置目录的来源记录。它只是溯源信息，
# 不该影响内容指纹 —— 否则换个构建机（路径不同）就会触发一次无谓的刷新。
SOURCE_NAME = "_source.json"


@dataclass
class EnsureResult:
    changed: bool = False
    message: str = ""

    def __bool__(self) -> bool:  # 方便 `if webui.ensure(...)`
        return self.changed


# ---------------------------------------------------------------- 路径

def bundled_dir() -> Path | None:
    """萝卜盒自带的预构建 dist；没内置则返回 None。"""
    d = resource_dir() / BUNDLED_NAME
    return d if (d / "index.html").is_file() else None


def target_dir(gateway_dir: Path | str) -> Path:
    return Path(gateway_dir) / "web" / "dist"


def installed(gateway_dir: Path | str) -> bool:
    return (target_dir(gateway_dir) / "index.html").is_file()


# ---------------------------------------------------------------- 版本戳

def _files(root: Path) -> list[Path]:
    skip = {STAMP_NAME, SOURCE_NAME}
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.name not in skip:
            out.append(p)
    return sorted(out, key=lambda p: str(p.relative_to(root)).lower())


def content_stamp(root: Path) -> str:
    """目录内容指纹：相对路径 + 字节内容，与文件时间无关。

    刻意不用 mtime —— 拷贝/解包会改写时间，指纹会假性变化。
    """
    h = hashlib.sha256()
    for p in _files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        h.update(rel.encode("utf-8", "replace"))
        h.update(b"\x00")
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
        h.update(b"\x01")
    return h.hexdigest()[:16]


def read_stamp(dist: Path) -> dict | None:
    p = dist / STAMP_NAME
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _write_stamp(dist: Path, stamp: str) -> None:
    try:
        (dist / STAMP_NAME).write_text(
            json.dumps(
                {
                    "stamp": stamp,
                    "app_version": __version__,
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "note": "由萝卜盒 LuoboBox 自动部署；删除本文件可让萝卜盒不再自动更新 web/dist",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------- 主入口

def ensure(gateway_dir: Path | str, *, force: bool = False) -> EnsureResult:
    """确保 <网关>/web/dist 可用。幂等、可随时调用。"""
    gw = Path(gateway_dir) if gateway_dir else None
    if gw is None or not gw.is_dir():
        return EnsureResult(False, f"网关目录不存在：{gateway_dir}")

    src = bundled_dir()
    if src is None:
        return EnsureResult(False, "萝卜盒未内置 WebUI（assets/webui 缺失）")

    dst = target_dir(gw)
    stamp = content_stamp(src)

    if dst.exists() and not force:
        cur = read_stamp(dst)
        if cur is None:
            return EnsureResult(False, "web/dist 不是萝卜盒装的（本地自行构建），保持不变")
        if cur.get("stamp") == stamp:
            return EnsureResult(False, "web/dist 已是最新内置版本")
        reason = f"内置 WebUI 已更新（{cur.get('app_version') or '?'} → {__version__}），已刷新"
    elif dst.exists():
        reason = "已按请求强制刷新 web/dist"
    else:
        reason = "web/dist 缺失，已用内置版本补齐"

    # 部署：先删干净再拷，避免新旧 assets 混在一起（旧 hash 文件会变成垃圾）
    try:
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    except Exception as exc:  # noqa: BLE001
        return EnsureResult(False, f"部署 web/dist 失败：{exc}")

    _write_stamp(dst, stamp)
    return EnsureResult(True, reason)
