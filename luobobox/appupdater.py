"""应用自更新：让萝卜盒自己从**自己的** release 升级到最新版。

和 updater.py 的分工（两者互不相干，别混）：

    updater.py      管「网关」  —— 覆盖 codebuddy2api 源码目录，
                                  保留 auth/、.env，最后重打脱敏补丁
    appupdater.py   管「萝卜盒自己」—— 覆盖 LuoboBox.exe 所在的安装目录，然后重启

为什么必须借一个外部 .cmd：

    Windows 上正在运行的 exe 是**锁死**的，自己没法覆盖自己；也没有"重启到
    自身新版本"的原生机制。所以流程是

        下载 → 生成一个临时 .cmd → 分离方式拉起它 → 自己退出

    .cmd 负责：等文件解锁 → 覆盖（或跑静默安装包）→ 重新拉起萝卜盒 → 自删。

两种升级方式（按当前是哪种安装形态自动判断，不用用户选）：

    installer  安装版（目录里能找到 Inno Setup 留下的 unins*.exe）
               → 下载 LuoboBox-Setup-<版本>.exe，用 /VERYSILENT 静默安装
    portable   便携版 / 开发目录 / 从 dist 直接跑
               → 下载 LuoboBox-<版本>-portable.zip，解压后原地覆盖

批处理文件的编码：cmd.exe 按**系统 OEM 代码页**解析 .bat/.cmd。本机（zh-CN）
ANSI=OEM=936，所以用 Python 的 "mbcs" 写盘最稳 —— 这样即使安装目录里有中文
（例如从含中文的工作区直接跑），脚本里的字面路径也不会被解成乱码。
"""

from __future__ import annotations

import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, net
from .paths import app_root, data_dir
from .updater import _parse_version

API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
UA = {"User-Agent": f"LuoboBox/{__version__}", "Accept": "application/vnd.github+json"}

# 最近一次下载实际走通的网络通道（失败/未下载过为 None）。
# UI 拿它回显"经系统代理下载成功"这类信息，方便用户判断该不该改代理设置。
LAST_ROUTE: "net.Route | None" = None

# 资产命名（由 packaging/build.py 决定，纯 ASCII —— 中文在 Release 里会被吞掉）
SETUP_PREFIX = "LuoboBox-Setup-"
SETUP_SUFFIX = ".exe"
PORTABLE_SUFFIX = "-portable.zip"
EXE_NAME = "LuoboBox.exe"

# 不继承父进程控制台、自成进程组 —— 父进程退出不会带走它
_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


# ---------------------------------------------------------------- 数据模型

@dataclass
class AppReleaseInfo:
    tag: str = ""
    name: str = ""
    published: str = ""
    notes: str = ""
    error: str = ""
    newer: bool = False
    local_version: str = ""
    route: str = ""                                        # 实际走通的网络通道
    assets: dict[str, str] = field(default_factory=dict)   # 资产名 -> 下载地址

    def setup_asset(self) -> tuple[str, str] | None:
        for name, url in self.assets.items():
            if name.startswith(SETUP_PREFIX) and name.lower().endswith(SETUP_SUFFIX):
                return name, url
        return None

    def portable_asset(self) -> tuple[str, str] | None:
        for name, url in self.assets.items():
            if name.endswith(PORTABLE_SUFFIX):
                return name, url
        return None


# ---------------------------------------------------------------- 路径

def _cfg_get(key: str, default=None):
    """惰性读配置。

    不能在模块顶层 import .config —— config 会间接走到 paths/appupdater，
    容易形成环。这里每次现读，代价是几十微秒，换来确定性。
    """
    try:
        from .config import Config

        return Config.load().get(key, default)
    except Exception:  # noqa: BLE001  配置读不出来不该让更新流程崩掉
        return default


def proxy_setting() -> str:
    """用户在「设置」里手动指定的代理（空串 = 自动）。"""
    return str(_cfg_get("net.proxy", "") or "").strip()


def probe_setting() -> bool:
    return bool(_cfg_get("net.probe", True))


def updates_dir() -> Path:
    """更新中转目录（下载的包、暂存解压、助手脚本与日志都在这）。

    默认跟随数据目录；配置 ``updater.work_dir`` 可改到别的盘。
    下载包有 35~50MB，攒几次就能把系统盘挤爆，所以要能挪。
    """
    override = str(_cfg_get("updater.work_dir", "") or "").strip()
    if override:
        try:
            d = Path(override)
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            pass  # 配的路径不可用就静默回落，不要因此卡住更新
    d = data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 检查

def local_version() -> str:
    """当前正在运行的应用版本（不是网关版本）。"""
    return __version__


def check_app(repo: str) -> AppReleaseInfo:
    """查自己仓库的最新 release，并和当前运行版本比大小。"""
    info = AppReleaseInfo(local_version=__version__)
    url = API_LATEST.format(repo=repo)
    try:
        data, route = net.read_json(url, headers=UA, timeout=20,
                                    cfg_proxy=proxy_setting(),
                                    probe=probe_setting())
        info.route = str(route)
    except Exception as exc:  # noqa: BLE001
        info.error = f"检查应用更新失败：{exc}"
        return info

    info.tag = str(data.get("tag_name") or "")
    info.name = str(data.get("name") or "")
    info.published = str(data.get("published_at") or "")
    info.notes = str(data.get("body") or "")[:4000]
    info.assets = {
        str(a.get("name") or ""): str(a.get("browser_download_url") or "")
        for a in (data.get("assets") or [])
    }
    if info.tag:
        try:
            info.newer = _parse_version(info.tag) > _parse_version(__version__)
        except Exception:  # noqa: BLE001
            info.newer = info.tag.lstrip("vV") != __version__.lstrip("vV")
    return info


def install_mode(root: Path | str | None = None) -> str:
    """判断当前是安装版还是便携版。

    只看一个事实：安装目录里有没有 Inno Setup 生成的卸载器 unins*.exe。
    有 = 走安装包静默升级（保留注册表/快捷方式/卸载信息的一致性）；
    没有 = 便携版/开发目录，走 zip 原地覆盖。
    """
    base = Path(root or app_root())
    try:
        if any(base.glob("unins*.exe")):
            return "installer"
    except OSError:
        pass
    return "portable"


def pick_asset(info: AppReleaseInfo, mode: str) -> tuple[str, str] | None:
    """按安装形态挑资产；缺了就退回另一种，总比什么都不做强。"""
    if mode == "installer":
        return info.setup_asset() or info.portable_asset()
    return info.portable_asset() or info.setup_asset()


# ---------------------------------------------------------------- 下载 / 解包

def download(url: str, dest_dir: Path, filename: str, timeout: int = 600) -> Path:
    """流式下载到 .part 再原子改名 —— 中断不会留下半个"看起来能装"的包。

    实际下载交给 :mod:`luobobox.net`：它会按
    ``手动代理 → 系统代理 → 环境变量代理 → 直连`` 的顺序挑一条**探活过**的
    通道。这一点很关键 —— 进程从外层 shell 继承来的 ``HTTPS_PROXY`` 可能指向
    一个早就死掉的端口，直接 ``urlopen`` 会干等 21 秒然后抛
    ``WinError 10060``，用户只会以为是 GitHub 挂了。

    实际走通的通道记在 :data:`LAST_ROUTE`，UI 可以回显（"经系统代理下载成功"）。
    """
    global LAST_ROUTE
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target, route = net.download(
        url, dest_dir / filename,
        headers=UA, timeout=timeout,
        cfg_proxy=proxy_setting(), probe=probe_setting(),
    )
    LAST_ROUTE = route
    return target


def extract_portable(archive: Path, into: Path) -> Path:
    """解压便携包，返回里面 LuoboBox/ 那一层。"""
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(into)
    inner = into / "LuoboBox"
    return inner if inner.is_dir() else into


def _prune_updates(keep: int = 2) -> None:
    """保留最近 keep 次更新的产物，删掉更早的（避免每次更新都攒 50MB）。"""
    d = updates_dir()
    try:
        archives = sorted(
            (p for p in d.iterdir() if p.is_file()
             and (p.suffix == ".exe" or p.name.endswith(".zip"))),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
    except OSError:
        return
    for old in archives[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    try:
        staging = sorted(
            (p for p in d.iterdir() if p.is_dir() and p.name.startswith("staging-")),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
    except OSError:
        return
    for old in staging[keep:]:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------- 助手脚本

def _write_batch(path: Path, text: str) -> None:
    """按系统 ANSI/OEM 码页写 .cmd（zh-CN 下是 936）。"""
    for enc in ("mbcs", "cp936", "utf-8"):
        try:
            path.write_text(text, encoding=enc)
            return
        except (UnicodeEncodeError, LookupError):
            continue
    path.write_text(text, encoding="utf-8", errors="replace")


def helper_script(app_dir: Path, src: Path, mode: str, setup: Path | None,
                  log: Path, relaunch: bool = True, self_delete: bool = True,
                  retries: int = 80, wait_sec: int = 2) -> str:
    """生成一次性助手脚本。路径直接写字面量，不走命令行参数（引号/编码坑太多）。

    便携版的核心顺序是「**先换 exe，再换其余**」：
    运行中的进程只锁住 LuoboBox.exe 一个文件，所以拿它当闸门 ——
    exe 换得掉 = 旧进程确实退了，其余文件必然也能覆盖；
    exe 换不掉就整体放弃，绝不让 _internal/ 变成半新半旧（那比不更新还糟）。
    """
    exe = EXE_NAME
    lines = [
        "@echo off",
        "setlocal enableextensions",
        f'set "MAXN={retries}"',
        "rem LuoboBox self-update helper (one-shot)",
        f'set "APP={app_dir}"',
        f'set "SRC={src}"',
        f'set "SETUP={setup or ""}"',
        f'set "MODE={mode}"',
        f'set "LOG={log}"',
        "",
        "rem 给主程序一点时间退出，释放文件占用",
        "ping -n 3 127.0.0.1 >nul 2>&1",
        'if /I "%MODE%"=="installer" goto install',
        "",
        f'set "NEWEXE=%SRC%\\{exe}"',
        f'set "CUREXE=%APP%\\{exe}"',
        "set /a N=0",
        ":loop",
        "set /a N+=1",
        'copy /y "%NEWEXE%" "%CUREXE%" >nul 2>&1',
        "if errorlevel 1 (",
        "  if %N% GEQ %MAXN% (",
        '    >>"%LOG%" echo [%DATE% %TIME%] exe 替换失败（重试 %N% 次），'
        "放弃覆盖，保持旧版本可用",
        "    goto relaunch",
        "  )",
        f"  ping -n {wait_sec} 127.0.0.1 >nul 2>&1",
        "  goto loop",
        ")",
        '>>"%LOG%" echo [%DATE% %TIME%] exe 已替换，开始覆盖其余文件',
        'robocopy "%SRC%" "%APP%" /E /NFL /NDL /NJH /NJS /NP /R:0 /W:0 >>"%LOG%" 2>&1',
        "goto relaunch",
        "",
        ":install",
        'if not exist "%SETUP%" goto relaunch',
        '>>"%LOG%" echo [%DATE% %TIME%] 静默安装 %SETUP% 到 %APP%',
        "rem /DIR 必须显式给：DefaultDirName 是 {autopf}\\LuoboBox，",
        "rem 而 PrivilegesRequired=lowest 会把 {autopf} 解析成",
        "rem %LOCALAPPDATA%\\Programs —— 也就是 C 盘。",
        "rem 显式 /DIR 才能保证升级始终落在用户自己选的目录（如 D:\\LuoboBox），",
        "rem 而不是又装一份到 C 盘、留下两处互不相干的安装。",
        "rem /TASKS 故意不传：Inno 的 UsePreviousTasks 默认 yes，升级时会",
        "rem 自动沿用上次勾选的开机自启 / 桌面快捷方式；而首次静默安装时",
        "rem 强行打开机自启反而是错的（向导里那两个任务本来就是不勾的）。",
        '"%SETUP%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS'
        ' /RESTARTAPPLICATIONS /DIR="%APP%" >>"%LOG%" 2>&1',
        "goto relaunch",
        "",
        ":relaunch",
        "ping -n 2 127.0.0.1 >nul 2>&1",
    ]
    if relaunch:
        lines.append(f'if exist "%CUREXE%" start "" "%CUREXE%"')
    else:
        lines.append("rem (relaunch disabled)")
    lines.append('if /I not "%MODE%"=="installer" rmdir /s /q "%SRC%" >nul 2>&1')
    if self_delete:
        lines.append('(goto) 2>nul & del "%~f0"')
    lines.append("")
    return "\r\n".join(lines)



@dataclass
class ApplyPlan:
    ok: bool = False
    message: str = ""
    mode: str = ""
    script: Path | None = None
    log: Path | None = None
    pid: int = 0


def apply_and_restart(mode: str, archive: Path,
                      app_dir: Path | str | None = None) -> ApplyPlan:
    """准备好暂存与助手脚本，并**分离拉起**它；调用方随后应立即退出。"""
    plan = ApplyPlan(mode=mode)
    app = Path(app_dir or app_root())
    if not (app / EXE_NAME).is_file():
        plan.message = f"安装目录里找不到 {EXE_NAME}：{app}"
        return plan

    work = updates_dir()
    _prune_updates()
    stamp = time.strftime("%Y%m%d-%H%M%S")

    if mode == "installer":
        src = archive
    else:
        staging = work / f"staging-{stamp}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            src = extract_portable(archive, staging)
        except Exception as exc:  # noqa: BLE001
            plan.message = f"解压便携包失败：{exc}"
            return plan

    log = work / f"apply-{stamp}.log"
    script = work / f"apply-{stamp}.cmd"
    _write_batch(script, helper_script(app, src, mode, archive, log))

    try:
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(script)],
            cwd=str(work),
            close_fds=True,
            creationflags=_DETACHED,
        )
    except Exception as exc:  # noqa: BLE001
        plan.message = f"拉起更新助手失败：{exc}"
        return plan

    plan.ok = True
    plan.script = script
    plan.log = log
    plan.pid = proc.pid
    how = "静默安装包" if mode == "installer" else "原地覆盖便携包"
    plan.message = (f"更新助手已启动（{how}，PID {proc.pid}）；"
                    f"日志：{log}")
    return plan
