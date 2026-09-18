"""更新器。

自用场景没有自己的发布服务器，所以直接盯上游仓库的 release：
  https://github.com/maiphucgiang/codebuddy2api/releases

升级流程刻意做成一件事：**升级完自动重打 desensitize 补丁**。
上游覆盖 app/ 会冲掉本地为 Codex 补的 5 个品牌词，补丁一丢 Codex 立刻
被上游 11128 拦截 —— 人工记这件事迟早会忘，交给程序做。

保留清单（升级时绝不覆盖）：
  auth/           账号凭据（明文 token，丢了要重新扫码）
  .env            本地配置
  run_service.pyw / start.bat / stop.bat   既有部署脚本
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .paths import backup_dir

API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
UA = {"User-Agent": f"LuoboBox/{__version__}", "Accept": "application/vnd.github+json"}

# 升级时必须原样保留的路径（相对网关目录）
KEEP_PATHS = {
    "auth",
    ".env",
    "run_service.pyw",
    "start.bat",
    "stop.bat",
    "gateway.log",
}


@dataclass
class ReleaseInfo:
    tag: str = ""
    name: str = ""
    published: str = ""
    notes: str = ""
    tarball: str = ""
    zipball: str = ""
    error: str = ""
    newer: bool = False
    local_version: str = ""
    assets: list[str] = field(default_factory=list)

    def url_ok(self) -> bool:
        return bool(self.tarball or self.zipball)


def local_version(gateway_dir: Path | str) -> str:
    p = Path(gateway_dir) / "VERSION"
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace").strip()
    return ""


def _parse_version(text: str) -> tuple:
    nums = []
    for part in text.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:4])


def check(gateway_dir: Path | str, repo: str) -> ReleaseInfo:
    info = ReleaseInfo(local_version=local_version(gateway_dir))
    url = API_LATEST.format(repo=repo)
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        info.error = f"检查更新失败：{exc}"
        return info
    info.tag = str(data.get("tag_name") or "")
    info.name = str(data.get("name") or "")
    info.published = str(data.get("published_at") or "")
    info.notes = str(data.get("body") or "")[:4000]
    info.tarball = str(data.get("tarball_url") or "")
    info.zipball = str(data.get("zipball_url") or "")
    info.assets = [a.get("name", "") for a in (data.get("assets") or [])]
    if info.tag and info.local_version:
        try:
            info.newer = _parse_version(info.tag) > _parse_version(info.local_version)
        except Exception:  # noqa: BLE001
            info.newer = info.tag.lstrip("vV") != info.local_version.lstrip("vV")
    return info


def download(url: str, dest_dir: Path, timeout: int = 120) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    suffix = ".zip" if url.endswith(".zip") else ".tar.gz"
    path = dest_dir / f"release{suffix}"
    path.write_bytes(raw)
    return path


def _extract(archive: Path, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(into)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            _safe_extract(tf, into)
    children = [c for c in into.iterdir() if c.is_dir()]
    if len(children) == 1:
        return children[0]
    return into


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """防目录穿越（tar 里的 ../../ 路径）。"""
    base = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(base)):
            raise RuntimeError(f"归档包含非法路径，已中止：{member.name}")
    tf.extractall(dest)


@dataclass
class ApplyResult:
    ok: bool = False
    message: str = ""
    backup: Path | None = None
    patched: str = ""


def apply_release(gateway_dir: Path | str, archive: Path) -> ApplyResult:
    """把 release 归档覆盖到网关目录，保留 KEEP_PATHS，最后重打补丁。"""
    gw = Path(gateway_dir)
    result = ApplyResult()
    if not gw.is_dir():
        result.message = f"网关目录不存在：{gw}"
        return result

    # 1) 整目录备份（排除体积大的日志）
    from .paths import unique_path

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = unique_path(backup_dir(), f"gateway-{stamp}")
    try:
        shutil.copytree(gw, backup, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log"))
        result.backup = backup
    except Exception as exc:  # noqa: BLE001
        result.message = f"备份失败，已放弃升级：{exc}"
        return result

    # 2) 解包
    try:
        with tempfile.TemporaryDirectory(prefix="luobobox-rel-") as tmp:
            staging = _extract(archive, Path(tmp))
            # 3) 逐项覆盖（保留清单跳过）
            copied: list[str] = []
            for item in staging.iterdir():
                if item.name in KEEP_PATHS:
                    continue
                target = gw / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
                copied.append(item.name)
            result.message = f"已覆盖 {len(copied)} 项：{'、'.join(sorted(copied)[:12])}"
    except Exception as exc:  # noqa: BLE001
        result.message = f"解包/覆盖失败（已备份到 {backup.name}）：{exc}"
        return result

    # 4) 自动重打脱敏补丁 —— 这是升级流程里最容易忘、后果最重的一步
    try:
        from . import patcher

        rep = patcher.ensure(gw)
        result.patched = rep.summary()
    except Exception as exc:  # noqa: BLE001
        result.patched = f"重打补丁时出错，请手动检查：{exc}"

    result.ok = True
    return result


def cleanup_downloads() -> None:
    """清掉临时下载目录。"""
    d = backup_dir() / "_downloads"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
