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

import os
import shutil
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, net
from .paths import backup_dir, gateway_dir_missing

API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
UA = {"User-Agent": f"LuoboBox/{__version__}", "Accept": "application/vnd.github+json"}


def _cfg_get(key: str, default=None):
    """惰性读配置（不能在模块顶层 import .config，容易形成环）。"""
    try:
        from .config import Config

        return Config.load().get(key, default)
    except Exception:  # noqa: BLE001  配置读不出来不该让更新流程崩掉
        return default


def proxy_setting() -> str:
    """用户在「设置」里手动指定的代理（空串 = 自动挑选）。"""
    return str(_cfg_get("net.proxy", "") or "").strip()


def probe_setting() -> bool:
    return bool(_cfg_get("net.probe", True))

# 升级时必须原样保留的路径（相对网关目录）
KEEP_PATHS = {
    "auth",
    ".env",
    "run_service.pyw",
    "start.bat",
    "stop.bat",
    "gateway.log",
}

# 升级时必须原样保留的「目录内子路径」（相对网关目录）。
#
# 为什么需要它：下面的覆盖动作是「整目录 rmtree + copytree」。而上游仓库里
# web/.gitignore 把 dist/ 忽略了 —— 升级包里的 web/ 只有 src，一覆盖就把本地
# 构建好的 web/dist 连根拔掉，/dashboard/ 立刻 503「WebUI 尚未构建」。
# 这正是「后台管理打不开」的根因，且每次升级都必然复发。
# node_modules 同理：重装一次要好几分钟。
PRESERVE_SUBPATHS = (
    "web/dist",
    "web/node_modules",
)

# 备份时要排除的路径名（shutil.ignore_patterns，按 basename 匹配、不进子目录）。
#
# 备份是「整目录 copytree」，而网关目录里的 web/node_modules 有 2 万多个小文件、
# 700MB 上下。实测一次升级就写出 718MB / 20145 个文件到 %LOCALAPPDATA%，
# 而系统盘常常只剩 2GB —— 两三次升级就把 C 盘挤爆，用户看到的却是
# 「更新出错」这种跟磁盘毫无关系的提示。
#
# 备份的目的是「升级搞坏了能退回去」，而依赖 / 虚拟环境 / 缓存都是**可再生**的，
# 没必要占着备份的空间。
BACKUP_IGNORE = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.log",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pypackages__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
)

# 网关备份保留份数（按目录名里的时间戳排序，新的留下）。
BACKUP_KEEP = 2


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
    route: str = ""                       # 实际走通的网络通道
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
        data, route = net.read_json(url, headers=UA, timeout=20,
                                    cfg_proxy=proxy_setting(),
                                    probe=probe_setting())
        info.route = str(route)
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
    """下载 release 归档，走 net 的代理回退链。

    GitHub 的 ``tarball_url`` / ``zipball_url`` 都**不带扩展名**，所以文件名
    只能靠猜；真正决定怎么解压的是 :func:`_extract` 里的魔数嗅探。
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".zip" if url.lower().endswith(".zip") else ".tar.gz"
    path, _route = net.download(
        url, dest_dir / f"release{suffix}",
        headers=UA, timeout=timeout,
        cfg_proxy=proxy_setting(), probe=probe_setting(),
    )
    return path


def _looks_like_zip(archive: Path) -> bool:
    """按魔数判断是不是 zip（PK\\x03\\x04），不靠扩展名。

    GitHub 的归档地址没有扩展名，光看后缀会把 zip 当成 tar.gz 去解，
    直接 ReadError。读 4 个字节就能定性，比猜靠谱。
    """
    try:
        with open(archive, "rb") as fh:
            return fh.read(4)[:2] == b"PK"
    except OSError:
        return False


def _extract(archive: Path, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip" or _looks_like_zip(archive):
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
    webui: str = ""
    pruned: int = 0


def prune_backups(keep: int = BACKUP_KEEP,
                  protect: Path | str | None = None) -> int:
    """只保留最近 keep 份网关备份，返回删掉的份数。

    排序用**目录名**里的时间戳而不是 mtime：备份可能被同步/搬动，mtime 会乱，
    而 ``gateway-YYYYmmdd-HHMMSS`` 的字典序就是时间序。

    ``protect`` 是**刚做出来的那一份**，永远不删。理由：名字排序在极端情况下
    （系统时间被回拨、目录里有别处同步来的备份）会把新备份排到末尾，
    而「先备份、再修剪」的顺序下，删掉刚做的备份 = 这次升级没有退路。
    宁可多留一份，也不能删掉唯一的回滚点。
    """
    root = backup_dir()
    guard = os.path.normcase(str(Path(protect))) if protect else ""
    try:
        dirs = [p for p in root.iterdir()
                if p.is_dir() and p.name.startswith("gateway-")]
    except OSError:
        return 0
    dirs.sort(key=lambda p: p.name, reverse=True)
    removed = 0
    for old in dirs[max(keep, 0):]:
        if guard and os.path.normcase(str(old)) == guard:
            continue
        shutil.rmtree(old, ignore_errors=True)
        if not old.exists():
            removed += 1
    return removed


def apply_release(gateway_dir: Path | str, archive: Path) -> ApplyResult:
    """把 release 归档覆盖到网关目录，保留 KEEP_PATHS，最后重打补丁。"""
    gw = Path(gateway_dir)
    result = ApplyResult()
    if not gw.is_dir():
        result.message = f"网关目录不存在：{gw}"
        return result

    # 1) 整目录备份（排除日志、依赖、缓存等体积大且可再生的东西）
    from .paths import unique_path

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = unique_path(backup_dir(), f"gateway-{stamp}")
    try:
        shutil.copytree(gw, backup, ignore=shutil.ignore_patterns(*BACKUP_IGNORE))
        result.backup = backup
    except Exception as exc:  # noqa: BLE001
        result.message = f"备份失败，已放弃升级：{exc}"
        return result

    # 1b) 备份完立刻收敛份数 —— 别等到磁盘满了才发现攒了几十份。
    #     刚做出来的这一份必须保护起来（见 prune_backups 的 protect 说明）。
    result.pruned = prune_backups(protect=backup)

    # 2) 解包
    # 暂存目录刻意放在**网关同盘**下：跨盘 shutil.move 会退化成真拷贝，
    # node_modules 那种几万个小文件能卡上几分钟。
    holds = gw.parent / f".luobobox-keep-{stamp}"
    try:
        with tempfile.TemporaryDirectory(prefix="luobobox-rel-") as tmp:
            staging = _extract(archive, Path(tmp))
            # 2b) 解包完整性先于"覆盖线上目录"检查。归档若被截断 / 上游结构变了，
            # staging 里会缺 `app/` 包 —— 此时若直接往下走，会先 rmtree 掉线上目录里
            # 好好的 `app/`，再补不回来，留下「converter.py 在、app/ 不在」的坏目录
            # （正是 2026-09-23 用户报的 `ModuleNotFoundError: No module named 'app'`）。
            # 所以在**暂存层**就把关：不齐就整体放弃，线上目录要么成功、要么纹丝不动。
            missing = gateway_dir_missing(staging)
            if missing:
                result.ok = False
                result.message = (
                    "下载的归档不完整（缺 " + "、".join(missing)
                    + "）—— 可能是网络截断或上游包结构变了；已保留原有目录未改动")
                return result
            # 3) 逐项覆盖（保留清单跳过）
            copied: list[str] = []
            stash: list[tuple[Path, Path]] = []
            for item in staging.iterdir():
                if item.name in KEEP_PATHS:
                    continue
                target = gw / item.name
                if item.is_dir():
                    if target.exists():
                        # 覆盖前先把构建产物搬走（上游包里没有它们）
                        for relative in PRESERVE_SUBPATHS:
                            rel = Path(relative)
                            if rel.parts[0] != item.name:
                                continue
                            live = gw / rel
                            if not live.exists():
                                continue
                            held = holds / ("__".join(rel.parts))
                            try:
                                held.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(live), str(held))
                                stash.append((held, live))
                            except Exception:  # noqa: BLE001
                                pass
                        shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
                copied.append(item.name)
            # 3b) 把暂存的构建产物放回去
            for held, live in stash:
                if not held.exists():
                    continue
                try:
                    if live.exists():
                        shutil.rmtree(live, ignore_errors=True)
                    live.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(held), str(live))
                except Exception:  # noqa: BLE001
                    pass
            if stash:
                copied.append(f"{len(stash)} 项前端构建产物已保留")
            result.message = f"已覆盖 {len(copied)} 项：{'、'.join(sorted(copied)[:12])}"
    except Exception as exc:  # noqa: BLE001
        result.message = f"解包/覆盖失败（已备份到 {backup.name}）：{exc}"
        return result
    finally:
        shutil.rmtree(holds, ignore_errors=True)

    # 4) 自动重打脱敏补丁 —— 这是升级流程里最容易忘、后果最重的一步
    try:
        from . import patcher

        rep = patcher.ensure(gw)
        result.patched = rep.summary()
    except Exception as exc:  # noqa: BLE001
        result.patched = f"重打补丁时出错，请手动检查：{exc}"

    # 5) 补齐内置 WebUI。
    # PRESERVE_SUBPATHS 只能保住「升级前就已经构建好的」dist；全新机器、
    # 或上游改了 web/src 的情况它无能为力。萝卜盒自带一份预构建 dist，
    # 这里按需补齐 —— 否则 /dashboard/ 立刻 503「WebUI 尚未构建」。
    try:
        from . import webui

        result.webui = webui.ensure(gw).message
    except Exception as exc:  # noqa: BLE001
        result.webui = f"部署内置 WebUI 时出错：{exc}"

    result.ok = True
    return result


def cleanup_downloads() -> None:
    """清掉临时下载目录。"""
    d = backup_dir() / "_downloads"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
