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

DATA_DIR_POINTER = "datadir.txt"


def default_data_dir() -> Path:
    """出厂默认数据目录：%LOCALAPPDATA%\\LuoboBox（在系统盘 C: 上）。"""
    return Path(
        os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    ) / APP_NAME_EN


def data_dir_override() -> Path | None:
    """读「数据目录迁移指针」。

    指针文件**永远留在出厂默认目录**里 —— 这是关键：无论数据被搬到哪个盘，
    程序下次启动都能从固定位置找回它。指针内容是一行绝对路径。
    """
    ptr = default_data_dir() / DATA_DIR_POINTER
    try:
        if not ptr.is_file():
            return None
        raw = ptr.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def data_dir() -> Path:
    """可写数据目录。

    优先级：LUOBOBOX_DATA_DIR 环境变量（测试用）> 迁移指针 > 出厂默认。
    """
    override = os.environ.get("LUOBOBOX_DATA_DIR")
    if override:
        base = Path(override)
    else:
        base = data_dir_override() or default_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_on_system_drive(path: Path | str | None = None) -> bool:
    """目标是否落在系统盘（C:）。给 UI 提示用。"""
    target = Path(path or data_dir())
    system = (os.environ.get("SystemDrive") or "C:").rstrip("\\/")
    try:
        return target.drive.upper().rstrip(":") == system.upper().rstrip(":")
    except (AttributeError, IndexError):
        return False


def dir_size(path: Path | str) -> int:
    """递归统计目录字节数（失败的文件跳过，不抛）。"""
    total = 0
    for q in Path(path).rglob("*"):
        try:
            if q.is_file():
                total += q.stat().st_size
        except OSError:
            continue
    return total


def human_size(num: float) -> str:
    """1536 -> '1.5 KB'。给 UI 显示体积用。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def data_dir_pointer_path() -> Path:
    """迁移指针的固定位置（永远在出厂默认目录里）。"""
    return default_data_dir() / DATA_DIR_POINTER


def migrate_data_dir(target: Path | str, *, move: bool = True) -> tuple[bool, str]:
    """把整个数据目录迁到 target。返回 (是否成功, 说明)。

    顺序刻意是「**先复制 → 再写指针 → 最后删旧**」：

    * 指针是唯一的真相来源，只有确认新位置内容齐了才敢写它；
    * 写了指针之后旧目录才允许删。

    这样任何一步失败都不会出现「两边都没有」的最坏情况 ——
    最差也只是多占一份磁盘，用户重试一次即可。
    """
    src = data_dir()
    dst = Path(target).expanduser()
    if not str(dst).strip():
        return False, "目标目录为空"
    if dst == src:
        return False, "目标就是当前数据目录，无需迁移"
    try:
        dst.relative_to(src)
        return False, "目标目录不能位于当前数据目录内部"
    except ValueError:
        pass
    if dst.is_file():
        return False, f"目标位置已经有一个同名文件：{dst}"

    # 空间检查：按当前数据的 1.1 倍估算
    need = int(dir_size(src) * 1.1)
    try:
        free = shutil.disk_usage(dst.anchor or dst.drive or "C:\\").free
    except OSError:
        free = None
    if free is not None and need > free:
        return False, (f"目标盘空间不足：需要约 {need / 1048576:.0f} MB，"
                       f"可用 {free / 1048576:.0f} MB")

    try:
        dst.mkdir(parents=True, exist_ok=True)
        probe = dst / ".luobox-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"目标目录不可写：{exc}"

    copied = 0
    try:
        for item in src.iterdir():
            if item.name == DATA_DIR_POINTER:
                continue
            dest = dst / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
            copied += 1
    except OSError as exc:
        return False, f"复制失败（旧数据仍在 {src}，未做任何删除）：{exc}"

    # 写指针 —— 这一步之后程序才会去新位置
    ptr = data_dir_pointer_path()
    try:
        ptr.parent.mkdir(parents=True, exist_ok=True)
        ptr.write_text(str(dst.resolve()), encoding="utf-8")
    except OSError as exc:
        return False, f"写迁移指针失败（旧数据仍在 {src}）：{exc}"

    removed = 0
    if move:
        for item in src.iterdir():
            if item.name == DATA_DIR_POINTER:
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                removed += 1
            except OSError:
                pass

    msg = (f"数据目录已迁到 {dst}（复制 {copied} 项，"
           f"约 {human_size(dir_size(dst))}"
           + (f"；清理旧目录 {removed} 项" if move else "")
           + "）。重启萝卜盒后生效。")
    return True, msg


def reset_data_dir_pointer() -> tuple[bool, str]:
    """撤销迁移：删掉指针，让数据目录回到出厂默认位置（不搬文件）。"""
    ptr = data_dir_pointer_path()
    if not ptr.is_file():
        return False, "当前没有迁移指针，数据目录本来就是出厂默认位置"
    try:
        ptr.unlink()
    except OSError as exc:
        return False, f"删除指针失败：{exc}"
    return True, f"指针已删除，数据目录回到 {default_data_dir()}（文件没有搬回，需要手工处理）"


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
