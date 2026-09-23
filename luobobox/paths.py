"""路径解析：把「代码在哪」「数据在哪」「Python 在哪」三件事集中到一处。

打包成 exe 后 __file__ 会指向临时解包目录，所以这里区分 frozen / 源码两种形态。
"""

from __future__ import annotations

import os
import re
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

def default_data_dir() -> Path:
    """出厂默认数据目录：%LOCALAPPDATA%\\LuoboBox（在系统盘 C: 上）。"""
    return Path(
        os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    ) / APP_NAME_EN


# ---------------------------------------------------------------- 迁移指针
#
# 指针只回答一个问题：「数据被搬到哪儿去了」。所以它**必须活得比数据目录久**。
#
# ≤ v1.0.6 把指针放在 `%LOCALAPPDATA%\LuoboBox` —— 而那正是出厂默认数据目录。
# 于是最常见的动作「腾 C 盘 → 把 LuoboBox 文件夹整个删掉」会把指针一起带走，
# 迁移被静默撤销：程序回到 C 盘重建一份空数据，用户看到的是「我的配置和
# 备份全没了」。这不是数据被删，是**指针被删**——但用户分不出这两者的区别。
#
# 现在的规则：首选「程序安装目录旁边」（删数据碰不到它）。只有那里确实写不
# 进去时（装到 Program Files 这类受控目录），才退回出厂默认目录 —— 那等于旧
# 行为，聊胜于无，但绝不该是默认。读取时两个位置都看，并把老位置的指针
# 顺手迁到新位置（自愈），存量安装升级上来第一次启动就自动修好。

DATA_DIR_POINTER = "datadir.txt"


def pointer_home() -> Path:
    """迁移指针的首选目录：程序安装目录。

    `LUOBOBOX_POINTER_DIR` 可覆盖（测试用 —— 否则跑一次用例就会在源码树里
    留下一个真的 datadir.txt，污染后续所有源码运行）。
    """
    override = os.environ.get("LUOBOBOX_POINTER_DIR")
    return Path(override) if override else app_root()


def pointer_primary_path() -> Path:
    """新位置：程序安装目录旁边的 datadir.txt。"""
    return pointer_home() / DATA_DIR_POINTER


def pointer_legacy_path() -> Path:
    """旧位置：出厂默认目录里的 datadir.txt（≤ v1.0.6 的行为）。

    `LUOBOBOX_LEGACY_POINTER_DIR` 可覆盖（测试用）。这条护栏不是多余的：
    `pointer_legacy_path()` 指向的是**本机真实安装版正在用的指针**，而
    `sync_pointer_home()` 的自愈会把老位置指针**搬走并删掉**。测试若只覆盖
    `LUOBOBOX_POINTER_DIR`（只管新位置），老位置照样会落到真实路径上，
    跑一次用例就能把用户迁移指针抹掉 —— 曾真实发生过。故两个位置都要能改道。
    """
    override = os.environ.get("LUOBOBOX_LEGACY_POINTER_DIR")
    if override:
        return Path(override) / DATA_DIR_POINTER
    return default_data_dir() / DATA_DIR_POINTER


def pointer_candidates() -> tuple[Path, ...]:
    """读取顺序：新位置优先，老位置兜底。"""
    return (pointer_primary_path(), pointer_legacy_path())


def data_dir_pointer_path() -> Path:
    """写入位置（迁移时用，也给 UI 显示）。"""
    return pointer_primary_path()


def _read_pointer(path: Path) -> Path | None:
    """读一个指针文件；内容不是「确实存在的目录」就返回 None。"""
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def write_pointer(target: Path | str) -> tuple[Path | None, str]:
    """把迁移指针写到首选位置；写不进去就退回老位置。

    返回 (实际落盘路径 或 None, 说明)。两个位置都写不进去才算失败 ——
    此时调用方必须当成「迁移未完成」处理，绝不能只打个日志了事。
    """
    text = str(Path(target).expanduser().resolve())
    primary = pointer_primary_path()
    errors: list[str] = []
    for path in (primary, pointer_legacy_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}：{exc}")
            continue
        if path != primary:
            # 退到了老位置。这个指针一删就丢，必须如实告诉用户，
            # 别让他以为迁移已经高枕无忧了。
            return path, (f"⚠ 指针只能写在 {path}（程序目录不可写）："
                          "它和出厂默认目录绑在一起，删那个文件夹会让迁移失效")
        return path, ""
    return None, "；".join(errors) or "无法写入迁移指针"


def sync_pointer_home() -> str | None:
    """把老位置的指针迁到新位置（自愈）。说明性文字，无需动作时返回 None。

    为什么值得每次启动都试一下：老指针会一直躺在 %LOCALAPPDATA%\\LuoboBox 里，
    用户哪天一删文件夹就前功尽弃。成本是一次 is_file()，只有真的存在老指针时
    才会多一次读 + 一次写。
    """
    primary, legacy = pointer_primary_path(), pointer_legacy_path()
    if primary == legacy or primary.is_file() or not legacy.is_file():
        return None
    target = _read_pointer(legacy)
    if target is None:
        return None
    landed, _note = write_pointer(target)
    if landed != primary:
        return None          # 新位置写不进去就先维持原样，别白删
    try:
        legacy.unlink()
    except OSError:
        pass                 # 删不掉也无妨：读取顺序里新位置优先
    return f"数据目录指针已搬到 {landed}（旧位置不会再因删数据而失效）"


def data_dir_override() -> Path | None:
    """读迁移指针：返回数据被搬到的位置；没迁过返回 None。

    在返回老位置的结果**之前**会尝试把它迁到新位置 —— 这是自愈点，
    也是唯一一处「读操作带副作用」的地方，因为晚一步就可能被删掉。
    """
    primary = pointer_primary_path()
    for path in pointer_candidates():
        found = _read_pointer(path)
        if found is None:
            continue
        if path != primary:
            sync_pointer_home()
        return found
    return None


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
    landed, note = write_pointer(dst)
    if landed is None:
        return False, f"写迁移指针失败（旧数据仍在 {src}）：{note}"

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
           + f"）。迁移指针：{landed}。"
           + (note or "")
           + "重启萝卜盒后生效。")
    return True, msg


def reset_data_dir_pointer() -> tuple[bool, str]:
    """撤销迁移：删掉指针（新老两个位置都删），数据目录回到出厂默认位置。

    只删指针、不搬文件 —— 搬回几百 MB 是个重操作，而且用户选的"撤销"
    往往只是因为想换一个目标盘，直接覆盖写新指针即可。
    """
    removed: list[str] = []
    for path in pointer_candidates():
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError as exc:
            return False, f"删除指针失败：{path}：{exc}"
        removed.append(str(path))
    if not removed:
        return False, "当前没有迁移指针，数据目录本来就是出厂默认位置"
    return True, ("指针已删除（" + "、".join(removed) + "），数据目录回到 "
                  f"{default_data_dir()}（文件没有搬回，需要手工处理）")


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


def gateway_dir_candidates() -> list[Path]:
    """按「猜」的顺序列出可能的网关源码目录。"""
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
    out: list[Path] = []
    for c in candidates:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def default_gateway_dir() -> Path:
    """默认网关源码目录。

    打包成 exe 后，网关通常**不在** exe 旁边（用户是把它放在别处的），
    所以这里除了同级/上级，还逐级向上找 —— 遍历 dist/LuoboBox/dist/luobobox/
    这样的层级后，一般能撞到用户真正的 codebuddy2api 目录。

    ⚠️ 全部猜不中时返回 `candidates[0]`（一个**可能并不存在**的路径）。这是刻意
    的：`default_config()` 需要一个"看起来合理"的初值，真正该做的是把猜中的
    这个"猜"字讲清楚 —— 见 `locate_gateway_dir()`，它会在放弃之前去问
    **正在运行的网关进程**，那才是知情者。
    """
    candidates = gateway_dir_candidates()
    for c in candidates:
        if is_gateway_dir(c):
            return c.resolve()
    return candidates[0].resolve()


def gateway_from_process(timeout: int = 12) -> tuple[Path, int | None] | None:
    """问**正在运行的网关进程**：返回 ``(网关源码目录, 它的端口 或 None)``。

    ★ 为什么要问进程：网关源码放在哪儿是用户的自由。靠 `app_root()` 逐级向上猜，
      在「exe 装在默认位置、网关源码放在别的盘」这种最常见的组合下必然猜错 ——
      表现为向导把一个**不存在的路径**填进输入框，而用户能做的只有
      自己去找。可是机器上明明有人知道答案：那个正在跑的网关，它的命令行里
      就写着 `<网关目录>\\converter.py serve --host … --port …`。

    ★ 为什么连端口一起回：只把目录改对、端口留在默认值，照着向导点完就会在
      **同一份源码目录**上再起一个实例 —— 两个进程同时写同一份 `auth/`、`.env`，
      比端口冲突更难查。既然认了"运行中的那个网关"作为目标，它的端口也得认。

    这条路径**会起一个 PowerShell**（约 1~3 秒），所以只在明确的"定位"动作里调
      （向导的自动探测、一键修复），不要放进任何会被高频调用的默认值函数。
    """
    import subprocess

    # 服务端过滤（只回带 converter.py 的进程），避免把几百个进程的行都传回来；
    # [Console]::OutputEncoding 必须先设成 UTF-8 —— 中文用户名/路径在 GBK 控制台
    # 下会变成一堆问号，反推出的路径也就废了。
    ps = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -like '*converter.py*' } | "
        "Select-Object -First 8 -ExpandProperty CommandLine"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None

    for line in (proc.stdout or "").splitlines():
        found = _gateway_dir_in_cmdline(line)
        if found is not None:
            return found, _gateway_port_in_cmdline(line)
    return None


def gateway_dir_from_process(timeout: int = 12) -> Path | None:
    """只要目录（`gateway_from_process` 的薄包装，给不关心端口的调用方用）。"""
    got = gateway_from_process(timeout=timeout)
    return got[0] if got else None


def _gateway_port_in_cmdline(cmdline: str) -> int | None:
    """从命令行里抠出 `--port N` / `--port=N`。抠不到返回 None（不猜）。"""
    m = re.search(r"--port[=\s]+(\d{1,5})", cmdline or "")
    if not m:
        return None
    port = int(m.group(1))
    return port if 1 <= port <= 65535 else None


def _gateway_dir_in_cmdline(cmdline: str) -> Path | None:
    """从一条命令行里抠出 `.../converter.py` 前面的那个目录，并验证它真的成立。

    写成独立纯函数是为了能被自测直接覆盖。**按 token 取，不用正则扫整条** ——
    实测踩过：正则 `([A-Za-z]:[\\/][^"']*?)[\\/]converter\\.py` 会从命令行里
    **第一个**盘符开始吞，于是

        C:\\…\\Scripts\\pythonw.exe F:\\…\\反代工具\\codebuddy2api\\converter.py serve …
        └────────────── 被整段当成目录名 ──────────────┘

    得到的是拼了半条命令行的垃圾路径。按空白/引号切开、只认「以 converter.py
    结尾的那个参数」才是对的。
    """
    if not cmdline:
        return None

    tokens = re.findall(r'"([^"]+)"', cmdline)          # 带引号的（可含空格）
    tokens += [t for t in cmdline.split() if '"' not in t]  # 裸 token

    for tok in tokens:
        if not tok.lower().endswith("converter.py"):
            continue
        # 只认真的成立的那个目录 —— 抠错一点就当作没找到，
        # 而不是把一个坏路径填给用户。
        cand = Path(tok).parent
        if is_gateway_dir(cand):
            return cand.resolve()
    return None


def locate_gateway_dir(current: str | Path | None = None
                       ) -> tuple[Path | None, str, int | None]:
    """尽量定位网关源码目录，返回 ``(目录 或 None, 来源说明, 端口 或 None)``。

    顺序体现的是"可信度递减 + 代价递增"：
      ① 当前配置里填的（如果它真的成立）
      ② 同级 / 上级 / 数据目录逐级猜（纯文件系统，很便宜）
      ③ **问正在运行的网关进程**（要起 PowerShell，但它是唯一真正知情的）
    全部落空才返回 ``(None, "", None)``，由调用方决定怎么提示 —— 不再把猜出来的
    默认值当成结果。

    端口只在第 ③ 种来源下才有值（别人的命令行里写着），前两种来源回答不了
    "那个网关跑在哪个端口上"。
    """
    if current:
        cand = Path(str(current))
        if is_gateway_dir(cand):
            return cand.resolve(), "当前配置", None

    for cand in gateway_dir_candidates():
        if is_gateway_dir(cand):
            return cand.resolve(), "同级 / 上级目录", None

    got = gateway_from_process()
    if got is not None:
        # 三级分支都要给出**同一种形态**的路径。命令行的路径不一定被规范化
        # （本机 Temp 就常带 8.3 短名 `ADMINI~1`），一处 resolve 一处不 resolve
        # 会让调用方拿到两种写法、比较起来以为"换目录了"。
        return got[0].resolve(), "正在运行的网关进程", got[1]
    return None, "", None


# 🔴 「是不是网关目录」不能只看 converter.py 在不在。
#
# 上游 converter.py 的第 1 段就是 `from app.adapters.responses_adapter import …`。
# 这句话能不能成立，取决于 **app/ 包在不在同一个目录里** —— 而 converter.py
# 在不在，完全说明不了 app/ 在不在：半途中断的下载、手工拷了一半的目录、
# 被别的东西覆盖过的目录，都会留下「有 converter.py、没有 app/」这种状态。
#
# 这种目录以前会被判成「合格的网关目录」，然后一路通过所有检查被启动，子进程
# 只会在 line 40 抛一句
#     ModuleNotFoundError: No module named 'app'
# 并以退出码 1 死掉（2026-09-23 用户就是这么报上来的：
#  gateway.log 里 8 段 traceback，全部收尾于这一句）。
#
# 所以判据必须是「两个都在」，而且第二个**从 converter.py 自己的源码里读出来**
# —— 不写死 app/：万一上游改了布局，这里会跟着源码走，不会变成假警报。
_GATEWAY_APP_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+app(?:[.\s]|$)", re.M)

# converter.py 通常 130~210 KB；超过这个大小就不再全文扫（防意外读进巨大文件）
_GATEWAY_SOURCE_SCAN_LIMIT = 4 * 1024 * 1024

# {converter.py 路径小写: (大小, mtime_ns, 是否 import app)}
_GATEWAY_NEEDS_APP_CACHE: dict[str, tuple[int, int, bool]] = {}


def _gateway_needs_app(converter: Path) -> bool:
    """converter.py 自己有没有 ``import app`` —— 有才要求 app/ 包必须在。

    带缓存：`is_gateway_dir()` 会被候选目录循环反复调用，而 converter.py
    有 200 KB 上下，每次都全文读一遍纯属浪费。缓存键带 (大小, mtime_ns)，
    源码被换掉之后自然失效。
    """
    try:
        st = converter.stat()
    except OSError:
        return False
    key = str(converter).lower()
    cached = _GATEWAY_NEEDS_APP_CACHE.get(key)
    if cached is not None and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
        return cached[2]
    needs = False
    if st.st_size <= _GATEWAY_SOURCE_SCAN_LIMIT:
        try:
            text = converter.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        needs = bool(_GATEWAY_APP_IMPORT.search(text))
    _GATEWAY_NEEDS_APP_CACHE[key] = (st.st_size, st.st_mtime_ns, needs)
    return needs


def gateway_dir_missing(path: Path | str) -> tuple[str, ...]:
    """这个目录**缺什么**才够资格当网关目录。空元组 = 合格。

    只返回短名字（给界面拼句子用）；长解释见 `gateway_dir_problem()`。
    """
    p = Path(path)
    converter = p / "converter.py"
    if not converter.is_file():
        return ("converter.py",)
    if _gateway_needs_app(converter) and not (p / "app" / "__init__.py").is_file():
        return ("app/__init__.py",)
    return ()


def gateway_dir_problem(path: Path | str) -> str:
    """人话版的问题描述。合格时返回空串。

    与 `gateway_dir_missing()` 分开：那个给程序判断，这个给用户看。
    """
    p = Path(path)
    missing = gateway_dir_missing(p)
    if not missing:
        return ""
    if "converter.py" in missing:
        return (f"{p} 里没有 converter.py"
                "（正确的网关源码目录里应该有 converter.py）。")
    return (f"{p} 里只有 converter.py，缺少 app/ 包 —— "
            "converter.py 第 1 段就是 `from app.… import`，这么启动会直接以 "
            "ModuleNotFoundError: No module named 'app'（退出码 1）退出。"
            "点「一键配置环境」（设置 → 运行环境）或向导里的「一键修复环境」"
            "会就地把它补齐，auth/ 与 .env 都保留。")


def is_gateway_dir(path: Path | str) -> bool:
    """这个目录**能跑**吗 —— converter.py 与它依赖的 app/ 包都在才算数。

    只查 converter.py 是错的，理由见上面那段注释：那会放行一个必然以
    ``ModuleNotFoundError: No module named 'app'`` 崩掉的目录。
    """
    return not gateway_dir_missing(path)


# ---------------------------------------------------------------- Python

REQUIRED_MODULES = ("fastapi", "uvicorn", "httpx")

# 本机没有 Python 时的下载入口（向导 / 设置页共用）。
# 只放"页面"而不是某个具体版本的下载直链 —— 写死版本号迟早变成 404。
PYTHON_DOWNLOADS: tuple[tuple[str, str], ...] = (
    ("官方下载", "https://www.python.org/downloads/windows/"),
    ("国内镜像", "https://mirrors.huaweicloud.com/python/"),
)


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


def probe_modules(python: Path, modules: tuple[str, ...] = REQUIRED_MODULES
                  ) -> tuple[bool, list[str], str]:
    """探测解释器：返回 (是否可用, 缺失模块, 说明)。

    为什么把「缺失模块」单独回传：两种失败的下一步动作完全不同 ——
    缺依赖 → 装依赖就行；根本调不起来 → 得换个解释器。只回一句
    "不可用"，上层就只能猜或者把两种情况混成一条提示。
    """
    import subprocess

    if not python.exists():
        return False, [], "文件不存在"
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
        return False, [], f"调用失败：{exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return False, [], detail[-1] if detail else f"退出码 {proc.returncode}"
    missing = [m for m in (proc.stdout or "").strip().split(",") if m]
    if missing:
        return False, missing, "缺依赖：" + "、".join(missing)
    return True, [], "可用"


def _check(python: Path, modules: tuple[str, ...] = REQUIRED_MODULES) -> tuple[bool, str]:
    """验证解释器可用且带齐依赖。返回 (是否可用, 说明)。"""
    ok, _missing, why = probe_modules(python, modules)
    return ok, why


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
