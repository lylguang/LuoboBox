"""路径解析：把「代码在哪」「数据在哪」「Python 在哪」三件事集中到一处。

打包成 exe 后 __file__ 会指向临时解包目录，所以这里区分 frozen / 源码两种形态。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME_EN

FROZEN = getattr(sys, "frozen", False)


# ---------------------------------------------------------------- 应用自身

def app_root() -> Path:
    """应用安装根目录（exe 所在目录 / 源码目录的上一级）。"""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读资源（图标等）。"""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return app_root() / "assets"


def icon_path() -> Path:
    return resource_dir() / "icon.ico"


# ---------------------------------------------------------------- 用户数据

def data_dir() -> Path:
    """可写数据目录：%LOCALAPPDATA%\\LuoboBox（可用 LUOBOBOX_DATA_DIR 覆盖，便于测试）。"""
    override = os.environ.get("LUOBOBOX_DATA_DIR")
    base = Path(override) if override else Path(
        os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    ) / APP_NAME_EN
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_file() -> Path:
    return data_dir() / "config.json"


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_log() -> Path:
    """桌面壳自己的日志（与网关日志分开，便于排障）。"""
    return log_dir() / "luobobox.log"


def gateway_log() -> Path:
    """网关 stdout/stderr 落盘位置。"""
    return log_dir() / "gateway.log"


def backup_dir() -> Path:
    d = data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def unique_path(directory: Path, filename: str) -> Path:
    """避免同名覆盖。

    备份文件名精确到秒是不够的 —— 同一秒里连续 apply 两次会把第一份备份
    直接盖掉，用户就失去了真正的"改动前"状态。这里冲突时追加 -2、-3…
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(2, 1000):
        candidate = directory / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{os.getpid()}{suffix}"


# ---------------------------------------------------------------- 网关代码

DEFAULT_GATEWAY_DIR_NAME = "codebuddy2api"


def default_gateway_dir() -> Path:
    """默认网关源码目录。

    打包成 exe 后，网关通常**不在** exe 旁边（用户是把它放在别处的），
    所以这里除了同级/上级，还逐级向上找 —— 遍历 dist/LuoboBox/dist/luobobox/
    这样的层级后，一般能撞到用户真正的 codebuddy2api 目录。
    """
    candidates: list[Path] = []
    roots: list[Path] = []

    if FROZEN:
        roots.append(app_root())
        # 逐级向上（最多 4 层）找同级目录
        node = app_root()
        for _ in range(4):
            node = node.parent
            if node == node.parent:
                break
            roots.append(node)
    else:
        roots.append(app_root())
        roots.append(app_root().parent)

    roots.append(Path.cwd())

    for root in roots:
        candidates.append(root / DEFAULT_GATEWAY_DIR_NAME)
    # 数据目录下也允许放一份（便于把网关随身带着走）
    try:
        candidates.append(data_dir() / "gateway" / DEFAULT_GATEWAY_DIR_NAME)
        candidates.append(data_dir() / "gateway")
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    for c in candidates:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if (c / "converter.py").is_file():
            return c.resolve()
    return candidates[0].resolve()


def is_gateway_dir(path: Path | str) -> bool:
    p = Path(path)
    return (p / "converter.py").is_file()


# ---------------------------------------------------------------- Python

REQUIRED_MODULES = ("fastapi", "uvicorn", "httpx")


def _candidate_interpreters() -> list[Path]:
    """按优先级列出候选解释器。"""
    out: list[Path] = []
    if not FROZEN:
        out.append(Path(sys.executable))
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    # 本机既有部署使用的隔离 venv（start.bat 也用这个）
    venv = home / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "Scripts"
    out.append(venv / "pythonw.exe")
    out.append(venv / "python.exe")
    # 托管运行时
    versions = home / ".workbuddy" / "binaries" / "python" / "versions"
    if versions.is_dir():
        for v in sorted(versions.iterdir(), reverse=True):
            out.append(v / "pythonw.exe")
            out.append(v / "python.exe")
    # 系统 PATH
    for name in ("pythonw.exe", "python.exe"):
        found = shutil.which(name)
        if found:
            out.append(Path(found))
    # py launcher
    launcher = shutil.which("py")
    if launcher:
        out.append(Path(launcher))
    return out


def _check(python: Path, modules: tuple[str, ...] = REQUIRED_MODULES) -> tuple[bool, str]:
    """验证解释器可用且带齐依赖。返回 (是否可用, 说明)。"""
    import subprocess

    if not python.exists():
        return False, "文件不存在"
    code = (
        "import importlib.util as u,sys;"
        f"miss=[m for m in {list(modules)!r} if u.find_spec(m) is None];"
        "print(','.join(miss))"
    )
    try:
        proc = subprocess.run(
            [str(python), "-c", code],
            capture_output=True, text=True, timeout=25,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"调用失败：{exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return False, detail[-1] if detail else f"退出码 {proc.returncode}"
    missing = [m for m in (proc.stdout or "").strip().split(",") if m]
    if missing:
        return False, "缺依赖：" + "、".join(missing)
    return True, "可用"


def find_python(preferred: str | None = None) -> tuple[Path | None, list[tuple[Path, str]]]:
    """挑选解释器。preferred 优先且必须通过校验。

    返回 (选中的解释器 或 None, 探测报告列表)。
    """
    report: list[tuple[Path, str]] = []
    seen: set[str] = set()

    ordered: list[Path] = []
    if preferred:
        ordered.append(Path(preferred))
    ordered.extend(_candidate_interpreters())

    for cand in ordered:
        key = str(cand).lower()
        if key in seen:
            continue
        seen.add(key)
        ok, why = _check(cand)
        report.append((cand, "可用" if ok else why))
        if ok:
            return cand, report
    return None, report


def pythonw_for(python: Path) -> Path:
    """把 python.exe 换成同目录的 pythonw.exe（无控制台窗口）。

    容错：调用方可能把一个空路径传进来（Path("") == Path(".")），
    这种情况下 with_name() 会抛 ValueError，返回原值让上层去报"解释器没配"。
    """
    if not python.name or python.name == ".":
        return python
    if python.name.lower() == "py":
        return python  # py 启动器没有 pythonw 变体，靠 CREATE_NO_WINDOW 兜底
    sibling = python.with_name("pythonw.exe")
    return sibling if sibling.exists() else python
