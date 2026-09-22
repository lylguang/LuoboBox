"""一键配置环境：一次点击，把「跑得起来」所需的条件查清并尽量修好。

为什么值得做：萝卜盒要跑起来，依赖链其实不短 —— 数据目录指针在哪、网关源码
在哪、哪个 Python 带齐了 fastapi/uvicorn/httpx、端口空不空、脱敏补丁在不在、
内置 WebUI 有没有……任何一环断掉，用户看到的现象都是同一句话：「启动失败」。
把排查交给用户在四个页签之间来回找，是很差的体验。这里把它压成
**一次点击 + 一份清单**。

三条原则（与 AppContext.ensure_ready 一致）：

1. **不覆盖用户显式设过的值**。只修「空的 / 不存在的 / 验证不过的」——
   路径看着不常见但确实能用，就先尊重它。
2. **每一步都留痕**。返回逐行日志，UI 原样贴出来；绝不静默改配置。
3. **核心逻辑不依赖 Qt**。诊断、命令拼装都是纯函数，能在没有图形界面的
   环境里逐条断言 —— 否则这部分只能靠手点，等于没有回归。

「傻瓜式」的底线（用户点一次，就该真的能用）：

* 一个 Python 都没有时，**自动下载一份官方「嵌入式」Python 放进数据目录**。
  它免安装、免管理员（不碰 Program Files、不要 UAC），跟着数据一起搬 / 删。
  曾经这里只丢一个下载页链接就返回失败 —— 用户点完「一键修复」看到的仍是
  Python 目录空空如也，等于没修。现在补齐 pip 与依赖，直接把路走完。
* 找不到网关源码时**自动从上游仓库拉一份**（解包 → 打补丁 → 补内置 WebUI，
  与「更新网关」复用同一条链路）。

安全边界（刻意不做的事）：

* 不删旧计划任务 —— 那是会跟网关抢端口的东西，但删除动作影响系统，
  留给「设置 → 迁移与诊断」里的显式按钮。
* 不自动启动网关 —— 配置完只报「就绪」，启动仍由用户按。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import net, patcher
from .config import DEFAULT_EXTRA_ARGS, gen_api_key, pick_free_port, port_free
from .paths import (
    DEFAULT_GATEWAY_DIR_NAME,
    REQUIRED_MODULES,
    data_dir,
    default_gateway_dir,
    find_python,
    is_gateway_dir,
    pointer_legacy_path,
    pointer_primary_path,
    probe_modules,
    sync_pointer_home,
)

CREATE_NO_WINDOW = 0x08000000

# 数据目录下的虚拟环境目录名（放在数据目录里，跟着数据一起搬、一起删）
VENV_DIR_NAME = "pyenv"

# 数据目录下的「内置 Python」目录名。用户界面上看到的那个 Python 目录就是它。
BUILTIN_PY_DIR_NAME = "python"

# 内置 Python 用的官方**嵌入式包**版本。
#
# 为什么是嵌入式包而不是官方安装器（.exe）：
#   * 免安装、免管理员 —— 解压即用，不写注册表、不碰 Program Files，不要 UAC；
#   * 体积小（约 11 MB 压缩包 / 解压后 ~50 MB），下载快；
#   * 落在数据目录里，卸载时随数据一起删，不留残余。
# 它唯二的短板（_pth 关掉了 site、不含 pip）由 provision_builtin_python 补齐。
EMBED_PY_VERSION = "3.13.7"

# 嵌入式包的架构后缀。**不写死 amd64** —— 装错位数就是「装完还是用不了」，
# 与向导里那句安装包类型提示（widgets._python_arch_label）同一套判据（见 embed_arch）。
# 三个镜像都实测提供 arm64 包（`python-3.13.7-embed-arm64.zip` 均 200），
# 所以两种架构共用同一份候选列表，不需要为 arm64 单独裁剪。
EMBED_PY_ARCHES = ("amd64", "arm64")

# 嵌入式包的多镜像来源（模板里的 {v}/{arch} 会被替换）。先官方再国内 ——
# 国内镜像对国内用户通常快一个数量级，而官方源在个别网络下会被掐。
EMBED_PY_MIRRORS: tuple[tuple[str, str], ...] = (
    ("python.org", "https://www.python.org/ftp/python/{v}/python-{v}-embed-{arch}.zip"),
    ("华为镜像", "https://mirrors.huaweicloud.com/python/{v}/python-{v}-embed-{arch}.zip"),
    ("阿里云镜像", "https://mirrors.aliyun.com/python-release/windows/python-{v}-embed-{arch}.zip"),
)

# pip 引导脚本（get-pip.py）。嵌入式包连 ensurepip 都没有，只能靠它装 pip。
GET_PIP_MIRRORS: tuple[tuple[str, str], ...] = (
    ("bootstrap.pypa.io", "https://bootstrap.pypa.io/get-pip.py"),
    ("阿里云镜像", "https://mirrors.aliyun.com/pypi/get-pip.py"),
)

# PyPI 源：先默认源（镜像同步有延迟），失败再退国内镜像。
# 顺序写反的话，国内用户会永远吃不到默认源的最新包。
PYPI_MIRRORS: tuple[tuple[str, str], ...] = (
    ("PyPI 默认源", ""),
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
)

GLYPHS = {"ok": "✓", "fix": "→", "warn": "!", "fail": "✗"}


# ============================================================ 数据模型

@dataclass(frozen=True)
class Item:
    """清单里的一项。`state` 取 ok / fix / warn / fail。"""

    key: str
    title: str
    state: str
    detail: str
    fix_label: str = ""

    @property
    def fixable(self) -> bool:
        return self.state == "fix" and bool(self.fix_label)

    @property
    def glyph(self) -> str:
        return GLYPHS.get(self.state, "·")

    def line(self) -> str:
        return f"{self.glyph} {self.title} — {self.detail}"


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)

    # ---------------------------------------------------------- 查询

    def by_key(self, key: str) -> Item | None:
        for item in self.items:
            if item.key == key:
                return item
        return None

    def of(self, *states: str) -> list[Item]:
        want = set(states)
        return [i for i in self.items if i.state in want]

    @property
    def needing_fix(self) -> list[Item]:
        return self.of("fix")

    @property
    def failures(self) -> list[Item]:
        return self.of("fail")

    @property
    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in ("ok", "fix", "warn", "fail")}
        for item in self.items:
            out[item.state] = out.get(item.state, 0) + 1
        return out

    @property
    def ready(self) -> bool:
        """没有任何「必须人工介入」的项 —— 注意 fix 项不算失败。"""
        return not self.failures

    @property
    def todo(self) -> str:
        """给用户的一句话：现在该干什么。"""
        if self.failures:
            return "需要手动处理：" + "、".join(i.title for i in self.failures)
        if self.needing_fix:
            return f"有 {len(self.needing_fix)} 项可以自动修好，点「一键配置环境」。"
        return "环境就绪，可以启动网关。"

    def text(self) -> str:
        return "\n".join(i.line() for i in self.items)


# ============================================================ 纯函数（可断言）

def venv_dir() -> Path:
    return data_dir() / VENV_DIR_NAME


def venv_python(root: Path | None = None) -> Path:
    return (root or venv_dir()) / "Scripts" / "python.exe"


def pip_command(
    python: Path | str,
    *,
    index: str = "",
    proxy: str = "",
    upgrade: bool = False,
    packages: tuple[str, ...] = REQUIRED_MODULES,
) -> list[str]:
    """拼一条 pip 安装命令（纯函数，方便断言）。

    几个参数都是踩出来的：

    * ``--no-cache-dir``：本机沙箱会拦截 pip 缓存目录的批量删除，带上它绕开。
    * ``--no-input``：万一需要交互，宁可直接失败也不要挂着等输入。
    * ``--disable-pip-version-check``：省掉一次联网查版本，安装更快。
    * 代理**只从 --proxy 传**，见 ``pip_env()`` 为什么。
    """
    args = [
        str(python), "-m", "pip", "install",
        "--no-input", "--no-cache-dir", "--disable-pip-version-check",
    ]
    if upgrade:
        args.append("--upgrade")
    if index:
        args += ["--index-url", index]
    if proxy:
        args += ["--proxy", proxy]
    args += list(packages)
    return args


def first_proxy(cfg_proxy: str = "") -> str:
    """挑第一个非空的代理候选（含系统代理）；都没有则返回空串（直连）。"""
    try:
        for _desc, proxy in net.proxy_candidates(cfg_proxy):
            if proxy:
                return proxy
    except Exception:  # noqa: BLE001
        pass
    return ""


def install_attempts(cfg_proxy: str = "") -> list[tuple[str, str, str]]:
    """返回 [(说明, 源 URL, 代理 URL)]，按顺序试到第一个成功。

    先默认源，再国内镜像；每个源下面先"带代理"再"直连"——
    这两条是互相独立的失败模式（源抽风 / 代理抽风），所以要交叉覆盖，
    而不是"有代理就永远只用代理"。
    """
    proxy = first_proxy(cfg_proxy)
    out: list[tuple[str, str, str]] = []
    for label, index in PYPI_MIRRORS:
        if proxy:
            out.append((f"{label} + 代理", index, proxy))
        out.append((f"{label} + 直连", index, ""))
    return out


def pip_env() -> dict[str, str]:
    """pip 的运行环境：把继承来的代理变量全部抹掉。

    本机常态：外层 shell 带着一个**已经死掉的** HTTP_PROXY，pip 会照着它
    傻等到超时。报错看着像「连不上 PyPI」，其实是「代理连不上」——
    和 net.py 里 WinError 10060 那个坑是同一个。代理一律走 --proxy 显式传，
    这样"这次用的是哪条通道"是确定的，也才能在日志里说清楚。
    """
    env = dict(os.environ)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "PIP_PROXY"):
        env.pop(name, None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def locate_gateway_dir(cfg) -> Path | None:
    """找一份可用的 codebuddy2api：先上次用过的，再常见位置。"""
    remembered = Path(str(cfg.get("app.last_gateway_dir") or ""))
    if is_gateway_dir(remembered):
        return remembered
    candidate = default_gateway_dir()
    if is_gateway_dir(candidate):
        return candidate
    return None


def _runs(python: Path) -> bool:
    """这个解释器能不能跑起来（不要求带依赖）。"""
    if not python.is_file():
        return False
    try:
        proc = subprocess.run(
            [str(python), "-c", "print(1)"],
            capture_output=True, text=True, timeout=25,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        return False
    return proc.returncode == 0


def pick_base_python(preferred: Path | None = None) -> Path | None:
    """挑一个**能跑**的解释器（不要求带依赖），用来装依赖或建独立环境。

    与 find_python 的区别：那个只返回"完全合格"的；这里要的是"能用就行"，
    因为缺依赖恰恰是我们打算自己解决的事。
    """
    from .paths import _candidate_interpreters  # noqa: PLC2701

    ordered: list[Path] = []
    if preferred is not None and str(preferred).strip() not in ("", "."):
        ordered.append(preferred)
    ordered.extend(_candidate_interpreters())

    seen: set[str] = set()
    for cand in ordered:
        key = str(cand).lower()
        if key in seen:
            continue
        seen.add(key)
        if _runs(cand):
            return cand
    return None


def _run(argv: list[str], *, timeout: int = 600,
         env: dict[str, str] | None = None) -> tuple[bool, str]:
    """跑一个外部命令，返回 (是否成功, 合并后的输出)。"""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时（{timeout} 秒）"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    body = "\n".join(
        part for part in ((proc.stdout or "").strip(), (proc.stderr or "").strip())
        if part
    )
    return proc.returncode == 0, body


def _last_line(text: str, limit: int = 160) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else "未知错误")[:limit]


# ============================================================ 内置 Python
#
# 「这台机器上一个能用的 Python 都没有」是新手最常见、也最无从下手的死局：
# 他并不知道要去哪里下、下哪个位数、安装时要勾什么。以前这里只丢一个下载页
# 链接就返回失败 —— 用户点完「一键修复环境」，Python 目录还是空的。
# 下面这几个函数把这条路走完：**自动下载一份嵌入式 Python，装齐依赖**。

def embed_arch(machine: str | None = None) -> str:
    """本机该用哪种位数的嵌入式包：``amd64`` 或 ``arm64``。

    装错位数是这个流程里唯一「下完了也用不了」的死法，所以必须按本机架构选。

    判据与向导的安装包类型提示同源：**环境变量优先于 ``platform.machine()``** ——
    后者在个别精简 / 虚拟化环境里会返回空串或 ``x86``，反而是错的。

    读不到或读到别的一律按 ``amd64``：它覆盖绝大多数机器；而且萝卜盒自身是
    x64 构建，**32 位 Windows 上根本跑不起来本程序**，所以不存在「本机是 32 位
    却拿到 64 位包」这种情况。ARM64 上 amd64 包也还能靠 x64 仿真跑。
    """
    sig = (machine if machine is not None
           else (os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine() or ""))
    return "arm64" if str(sig).strip().lower() == "arm64" else "amd64"


def builtin_python_dir() -> Path:
    """内置 Python 的落点：数据目录下的 ``python\\``。

    放数据目录而不是程序目录，理由和 pyenv 一样 —— 跟着数据一起搬、一起删，
    而且这个位置**不需要管理员权限**（装到 Program Files 才要 UAC）。
    """
    return data_dir() / BUILTIN_PY_DIR_NAME


def builtin_python_exe() -> Path:
    return builtin_python_dir() / "python.exe"


def _embedded_pth(root: Path) -> Path | None:
    """嵌入式包自带的 ``pythonXY._pth``（它就是 sys.path 的配置）。"""
    for cand in sorted(root.glob("python*._pth")):
        return cand
    return None


def patch_embedded_pth(root: Path) -> str:
    """改写嵌入式包的 _pth，让 ``Lib\\site-packages`` 进 sys.path 并开启 site。

    嵌入式的 _pth 默认 **关掉 site（``#import site`` 是注释掉的）且不含
    site-packages** —— 不补这一步，后面 get-pip 装进去的包 import 不到，
    表现就是「pip 说装成功了，程序却说没有这个模块」。所以这不是可选优化。

    返回被改写的文件路径（便于日志与断言）。
    """
    pth = _embedded_pth(root)
    if pth is None:
        raise FileNotFoundError("没有找到 pythonXY._pth（下载的包可能不完整）")
    raw_lines = [
        ln.strip()
        for ln in pth.read_text(encoding="utf-8", errors="replace").splitlines()
    ]
    # 只保留有用行：去空行、去注释（默认那行 "#import site" 就在这里被丢掉）。
    useful = [ln for ln in raw_lines if ln and not ln.startswith("#")]
    out: list[str] = [ln for ln in useful if ln.lower().endswith(".zip") or ln == "."]
    if "." not in out:
        out.append(".")
    if "Lib\\site-packages" not in out:
        out.append("Lib\\site-packages")
    out.append("import site")
    pth.write_text("\n".join(out) + "\n", encoding="utf-8")
    return str(pth)


def _download_with_mirrors(specs, dest_dir: Path, filename: str, *, log=None,
                           cfg_proxy: str = "") -> tuple[Path, str]:
    """按镜像顺序下载一个文件到 ``dest_dir/filename``，第一个成功即停。

    ``specs`` 是 [(说明, URL)]（URL 里的 ``{v}`` 已在调用方替换好）。
    全失败时抛异常，消息里带最后一条失败原因 —— 上层直接贴给用户看。
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    last = "未知错误"
    for label, url in specs:
        if log:
            log(f"       下载 ← {label}")
        try:
            path, route = net.download(url, dest, timeout=600, cfg_proxy=cfg_proxy)
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
            if log:
                log(f"       失败：{last}")
            continue
        return path, f"{label}（{route}）"
    raise RuntimeError(last)


def provision_builtin_python(*, log=None, cfg_proxy: str = "",
                             version: str = EMBED_PY_VERSION) -> tuple[bool, str]:
    """下载官方嵌入式 Python 到数据目录，并装齐依赖。返回 (是否成功, 说明)。

    嵌入式包有两个必须补的短板，缺一不可：

      1. ``pythonXY._pth`` 默认关掉 site、且不含 site-packages → 改写它；
      2. **不含 pip（连 ensurepip 都没有）** → 用 get-pip.py 引导。
    """
    root = builtin_python_dir()
    py = builtin_python_exe()
    if py.is_file():
        ok, _missing, _why = probe_modules(py)
        if ok:
            return True, f"复用已有的内置 Python：{root}"

    root.mkdir(parents=True, exist_ok=True)
    arch = embed_arch()
    try:
        if log:
            log(f"       部署内置 Python {version}（{arch}，免安装、免管理员）→ {root}")
        urls = tuple((label, tpl.format(v=version, arch=arch))
                     for label, tpl in EMBED_PY_MIRRORS)
        archive, via = _download_with_mirrors(
            urls, root, f"python-{version}-embed-{arch}.zip",
            log=log, cfg_proxy=cfg_proxy)
        if log:
            log(f"       解压嵌入式包（来自 {via}）")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(root)
        try:
            archive.unlink()          # 压缩包已解开，留着只是白占几十 MB
        except OSError:
            pass
        patch_embedded_pth(root)
        if not py.is_file():
            return False, "内置 Python 解压后没有 python.exe（下载的文件可能不完整）"

        # ---- pip 引导
        bootstrap = root / "_bootstrap"
        bootstrap.mkdir(parents=True, exist_ok=True)
        pip_ok, last = False, "未知错误"
        try:
            getpip, _via2 = _download_with_mirrors(
                GET_PIP_MIRRORS, bootstrap, "get-pip.py",
                log=log, cfg_proxy=cfg_proxy)
            for desc, index, proxy in install_attempts(cfg_proxy):
                args = [str(py), str(getpip), "--no-input", "--no-cache-dir",
                        "--disable-pip-version-check", "--no-warn-script-location"]
                if index:
                    args += ["--index-url", index]
                if proxy:
                    args += ["--proxy", proxy]
                if log:
                    log(f"       引导 pip ← {desc}")
                ok, out = _run(args, timeout=600, env=pip_env())
                if ok:
                    pip_ok = True
                    break
                last = _last_line(out)
                if log:
                    log(f"       失败：{last}")
        finally:
            shutil.rmtree(bootstrap, ignore_errors=True)
        if not pip_ok:
            return False, f"内置 Python 已就位，但 pip 引导失败：{last}"

        ok, msg = _install_deps(py, cfg_proxy=cfg_proxy, log=log)
        if not ok:
            return False, msg
        good, missing, why = probe_modules(py)
        if not good:
            return False, f"内置 Python 依赖仍不齐：{why or '、'.join(missing)}"
        return True, f"已部署内置 Python（免安装）：{root}"
    except Exception as exc:  # noqa: BLE001
        return False, f"部署内置 Python 失败：{type(exc).__name__}: {exc}"


# ============================================================ 诊断

def _gateway_item(cfg) -> Item:
    gw = Path(str(cfg.get("gateway.dir") or ""))
    if is_gateway_dir(gw):
        ver = ""
        try:
            from . import updater

            ver = updater.local_version(gw) or ""
        except Exception:  # noqa: BLE001
            pass
        return Item("gateway_dir", "网关源码", "ok",
                    f"{gw}" + (f"（{ver}）" if ver else ""))
    found = locate_gateway_dir(cfg)
    shown = str(gw) if str(gw) not in ("", ".") else "（未设置）"
    if found is not None:
        return Item("gateway_dir", "网关源码", "fix",
                    f"{shown} 里没有 converter.py → 可改用 {found}",
                    "改用自动找到的网关目录")
    # 本地哪儿都没有 → 可以自动从上游仓库拉一份，所以报 fix 而不是 fail。
    return Item("gateway_dir", "网关源码", "fix",
                f"{shown} 里没有 converter.py，本机也没找到 codebuddy2api",
                "自动下载一份网关源码")


def _python_item(cfg, *, deep: bool, can_install: bool = True) -> Item:
    raw = str(cfg.get("gateway.python") or "").strip()
    cur = Path(raw) if raw else None

    if deep and cur is not None and cur.is_file():
        ok, missing, why = probe_modules(cur)
        if ok:
            return Item("python", "解释器与依赖", "ok", str(cur))
        if missing:
            return Item("python", "解释器与依赖", "fix",
                        f"{cur} 缺依赖：{'、'.join(missing)}", "自动安装依赖")
        return Item("python", "解释器与依赖", "fix",
                    f"{cur} 用不了（{why}）", "换一个能用的解释器")

    found, _report = find_python(raw or None)
    if found is not None:
        if str(found) == raw:
            return Item("python", "解释器与依赖", "ok", str(found))
        return Item("python", "解释器与依赖", "fix",
                    f"{raw or '（未设置）'} → 可改用 {found}", "改用自动找到的解释器")

    base = pick_base_python(cur if cur is not None and cur.is_file() else None)
    if base is not None:
        return Item("python", "解释器与依赖", "fix",
                    f"{base} 能跑但缺少 fastapi/uvicorn/httpx", "自动安装依赖")
    if can_install:
        # 一个 Python 都没有也不再是"死项" —— 会被自动下载一份内置的修好，
        # 所以这里报 fix（可自动修）而不是 fail（要人工介入）。
        return Item("python", "解释器与依赖", "fix",
                    "这台机器上没有 Python → 自动下载一份内置的（免安装、免管理员）",
                    "下载内置 Python 并装齐依赖")
    return Item("python", "解释器与依赖", "fail",
                "这台机器上没有可用的 Python。先装一个再点一次；"
                "装的时候记得勾「Add python.exe to PATH」")


def gateway_port_item(cfg, *, deep: bool) -> Item:
    """端口检查。被占 ≠ 该挪 —— 先看占着的是不是一个健康的网关实例。"""
    port = int(cfg.get("gateway.port", 0) or 0)
    if not (1 <= port <= 65535):
        return Item("port", "服务端口", "fix", f"{port} 不是合法端口", "自动换一个空闲端口")
    if port_free(port, fresh=True):
        return Item("port", "服务端口", "ok", f"{port} 空闲")

    if deep and port_owner_is_gateway(port):
        # 计划任务 / 旧脚本拉起的实例常驻在这。无脑挪端口会让签到 / 余额 /
        # 凭证全部打到空端口上（真实踩过：8788 被外部网关占用 → 挪到 8789 →
        # 管理接口全连不上）。
        return Item("port", "服务端口", "warn",
                    f"{port} 已有一个健康的网关在跑，采用它（不重复启动）")
    return Item("port", "服务端口", "fix", f"{port} 已被其它程序占用",
                "自动换一个空闲端口")


def port_owner_is_gateway(port: int) -> bool:
    """端口上占着的那个是不是一个能响应 /health 的网关。"""
    try:
        from .gateway import _http_json  # noqa: PLC2701

        code, _ = _http_json(f"http://127.0.0.1:{port}/health", timeout=2.5)
    except Exception:  # noqa: BLE001
        return False
    return code == 200


def pointer_item() -> Item:
    primary, legacy = pointer_primary_path(), pointer_legacy_path()
    if legacy.is_file() and legacy != primary and not primary.is_file():
        return Item("pointer", "数据目录指针", "fix",
                    f"指针还在出厂默认目录（{legacy}）—— 删数据目录会把它一起删掉",
                    "把指针挪到程序目录旁边")
    return Item("pointer", "数据目录指针", "ok",
                f"{primary}（删数据目录不会把它一起删）")


def _args_item(cfg) -> Item:
    args = list(cfg.get("gateway.extra_args", []) or [])
    missing = patcher.check_args(args)
    if not missing:
        return Item("args", "启动参数", "ok", "Codex 必需的三件套齐全")
    return Item("args", "启动参数", "fix",
                "缺 " + "、".join(m.split("：")[0] for m in missing),
                "补齐必需参数")


def _patch_item(cfg) -> Item:
    gw = Path(str(cfg.get("gateway.dir") or ""))
    if not is_gateway_dir(gw):
        return Item("patch", "脱敏补丁", "warn", "等网关源码定位后再检查")
    rep = patcher.inspect(gw)
    if rep.healthy:
        return Item("patch", "脱敏补丁", "ok", rep.summary())
    return Item("patch", "脱敏补丁", "fix", rep.summary(), "重打脱敏补丁")


def _webui_item(cfg) -> Item:
    gw = Path(str(cfg.get("gateway.dir") or ""))
    if not is_gateway_dir(gw):
        return Item("webui", "内置 WebUI", "warn", "等网关源码定位后再检查")
    try:
        from . import webui
    except Exception as exc:  # noqa: BLE001
        return Item("webui", "内置 WebUI", "warn", f"模块不可用：{exc}")
    try:
        ok = webui.installed(gw)
    except Exception as exc:  # noqa: BLE001
        return Item("webui", "内置 WebUI", "warn", f"检查失败：{exc}")
    if ok:
        return Item("webui", "内置 WebUI", "ok", f"{webui.target_dir(gw)} 已就位")
    return Item("webui", "内置 WebUI", "fix", "网页版管理台缺文件（会 503）",
                "补齐内置 WebUI")


def _scheduled_task_item() -> Item:
    try:
        from .gateway import scheduled_task_exists

        exists = scheduled_task_exists()
    except Exception:  # noqa: BLE001
        return Item("task", "旧计划任务", "ok", "检查跳过")
    if not exists:
        return Item("task", "旧计划任务", "ok", "未检测到遗留的 codebuddy2api 计划任务")
    return Item("task", "旧计划任务", "warn",
                "计划任务 codebuddy2api 仍在，会和萝卜盒抢同一端口 —— "
                "到「迁移与诊断」里点「移除旧计划任务」")


def diagnose(cfg, *, deep: bool = True, can_install: bool = True) -> Report:
    """体检。deep=False 时跳过子进程 / 网络探测（界面预检用，更快）。

    ``can_install=False`` 时「这台机器上没有 Python」判为 **fail**（要人工介入），
    因为此时不会去下载内置解释器 —— 判成 fix 会让 setup 误报「已就绪」。
    """
    items = [
        pointer_item(),
        _gateway_item(cfg),
        _python_item(cfg, deep=deep, can_install=can_install),
        gateway_port_item(cfg, deep=deep),
    ]

    key = str(cfg.get("gateway.api_key") or "").strip()
    items.append(Item("api_key", "API Key", "ok" if key else "fix",
                      "已设置" if key else "为空 —— 网关唯一的防线",
                      "" if key else "生成一个随机 Key"))

    items += [_args_item(cfg), _patch_item(cfg), _webui_item(cfg), _scheduled_task_item()]
    return Report(items)


# ============================================================ 修复

def fix_pointer(cfg=None, **_kw) -> tuple[bool, str]:
    note = sync_pointer_home()
    if note:
        return True, note
    return True, f"指针位置正确：{pointer_primary_path()}"


def fetch_gateway(cfg, *, log=None) -> tuple[bool, str]:
    """从上游仓库拉一份网关源码到数据目录。返回 (是否成功, 说明)。

    与「更新网关」复用同一条链路（下载 → 解包 → 打脱敏补丁 → 补内置 WebUI），
    所以拿到手就是一份能直接跑的源码，不需要用户再点别的。
    """
    from . import updater

    repo = str(cfg.get("updater.repo") or "").strip()
    if not repo:
        return False, "未配置上游仓库地址，无法自动获取网关源码"
    root = data_dir() / "gateway"
    target = root / DEFAULT_GATEWAY_DIR_NAME
    try:
        if log:
            log(f"       查询上游最新版本：{repo}")
        info = updater.check(target, repo)
        if info.error:
            return False, info.error
        if not info.url_ok():
            return False, "上游没有可下载的发行包"
        url = info.zipball or info.tarball
        if log:
            log(f"       下载网关源码（{info.tag or '最新'}）")
        archive = updater.download(url, root / "_downloads")
        target.mkdir(parents=True, exist_ok=True)
        res = updater.apply_release(target, archive)
        if not res.ok:
            return False, res.message
    except Exception as exc:  # noqa: BLE001
        return False, f"获取网关源码失败：{type(exc).__name__}: {exc}"
    if not is_gateway_dir(target):
        return False, "下载完成但没有找到 converter.py（上游包结构可能变了）"
    cfg.set("gateway.dir", str(target))
    cfg.set("app.last_gateway_dir", str(target))
    return True, f"已自动获取网关源码（{info.tag or '最新'}）→ {target}"


def fix_gateway_dir(cfg, *, log=None, **_kw) -> tuple[bool, str]:
    found = locate_gateway_dir(cfg)
    if found is None:
        # 本地确实没有 —— 自动拉一份，而不是把问题丢回给用户。
        return fetch_gateway(cfg, log=log)
    cfg.set("gateway.dir", str(found))
    cfg.set("app.last_gateway_dir", str(found))
    return True, f"网关目录 → {found}"


def _install_deps(python: Path, *, cfg_proxy: str = "", log=None,
                  packages: tuple[str, ...] = REQUIRED_MODULES) -> tuple[bool, str]:
    """按「源 × 通道」阶梯给一个解释器装依赖，第一个成功即停。"""
    last = "未知错误"
    for desc, index, proxy in install_attempts(cfg_proxy):
        if log:
            log(f"       pip ← {desc}")
        ok, out = _run(pip_command(python, index=index, proxy=proxy, packages=packages),
                       timeout=900, env=pip_env())
        if ok:
            return True, f"依赖安装完成（{desc}）"
        last = _last_line(out)
        if log:
            log(f"       失败：{last}")
    return False, f"依赖安装失败：{last}"


def make_venv(base: Path, *, log=None, target: Path | None = None,
              cfg_proxy: str = "") -> tuple[bool, str]:
    """在数据目录里建一个独立虚拟环境并装好依赖。

    为什么优先建 venv 而不是直接往用户的解释器里装：往系统 / 托管 Python 里
    pip install 会污染它 —— 用户别的项目可能因此被升/降级一个包。
    独立环境跟着数据目录走，坏了删掉重来即可，卸载时也不留残余。
    """
    root = target or venv_dir()
    py = venv_python(root)
    if not py.is_file():
        if log:
            log(f"       创建独立环境 {root}")
        ok, out = _run([str(base), "-m", "venv", str(root)], timeout=600,
                       env=pip_env())
        if not ok or not py.is_file():
            return False, f"创建虚拟环境失败：{_last_line(out)}"
    elif log:
        log(f"       复用已有独立环境 {root}")

    ok, msg = _install_deps(py, cfg_proxy=cfg_proxy, log=log)
    if not ok:
        return False, msg
    good, missing, why = probe_modules(py)
    if not good:
        return False, f"环境建好了但依赖仍不齐：{why or '、'.join(missing)}"
    return True, f"已建好独立环境并装齐依赖：{root}"


def fix_python(cfg, *, log=None, allow_install: bool = True,
               prefer_venv: bool = True, **_kw) -> tuple[bool, str]:
    """确保有一个带齐依赖的解释器。

    从「最不打扰」到「一定成功」逐级尝试：
      1. 已经配好的解释器可用   → 什么都不动。
      2. 换一个现成合格的解释器 → 只改配置，不装任何东西。
      3. 建独立虚拟环境装依赖   → 推荐路径，不污染用户环境。
      4. 直接往那个解释器里装   → 3 失败时的兜底（例如磁盘上不让建 venv）。
      5. **一个解释器都没有**   → 下载一份内置的（免安装）并装齐依赖。

    第 5 级是关键：前四级都要求机器上「已经有一个能跑的 Python」。新手最常见的
    恰恰是没有 —— 那时旧版本只返回一句「请先装一个 Python」，用户点完
    「一键修复」看到的仍是空的 Python 目录。现在把这条路走完。
    """
    raw = str(cfg.get("gateway.python") or "").strip()
    cur = Path(raw) if raw else None
    if cur is not None and cur.is_file():
        ok, _missing, _why = probe_modules(cur)
        if ok:
            return True, f"已有可用解释器 {cur}"

    found, _report = find_python(raw or None)
    if found is not None:
        cfg.set("gateway.python", str(found))
        return True, f"解释器 → {found}"

    if not allow_install:
        return False, "没有现成可用的解释器（已按设置跳过自动安装）"

    cfg_proxy = str(cfg.get("net.proxy", "") or "")

    def use_builtin() -> tuple[bool, str]:
        """兜底：下载一份内置 Python 并装齐依赖。"""
        ok, msg = provision_builtin_python(log=log, cfg_proxy=cfg_proxy)
        if ok:
            cfg.set("gateway.python", str(builtin_python_exe()))
        return ok, msg

    base = pick_base_python(cur if cur is not None and cur.is_file() else None)
    if base is None:
        # 一个能跑的解释器都没有 → 直接部署内置的。
        return use_builtin()

    if prefer_venv:
        ok, msg = make_venv(base, log=log, cfg_proxy=cfg_proxy)
        if ok:
            cfg.set("gateway.python", str(venv_python()))
            return True, msg
        if log:
            log(f"       （独立环境没建成，改为直接装进 {base}）")

    ok, msg = _install_deps(base, cfg_proxy=cfg_proxy, log=log)
    if ok:
        good, missing, why = probe_modules(base)
        if good:
            cfg.set("gateway.python", str(base))
            return True, f"已为 {base} 装好依赖"
        msg = f"依赖装完仍不可用：{why or '、'.join(missing)}"

    # 最后一级兜底：现成解释器怎么都修不好（权限 / 位数 / 环境被玩坏）时，
    # 部署一份干净的内置 Python —— 「傻瓜式」的意思就是不管机器现在什么样都别停。
    if log:
        log(f"       现成解释器修不好（{msg}），改为部署内置 Python")
    okb, msgb = use_builtin()
    if okb:
        return True, f"{msgb}（原解释器装依赖失败：{msg}）"
    return False, f"{msg}；内置 Python 兜底也失败：{msgb}"


def fix_port(cfg, **_kw) -> tuple[bool, str]:
    port = int(cfg.get("gateway.port", 0) or 0)
    # fresh=True：这会真的改配置，必须拿真值
    if 1 <= port <= 65535 and port_free(port, fresh=True):
        return True, f"端口 {port} 本来就空闲"
    start = port + 1 if 1 <= port <= 65534 else 8788
    new = pick_free_port(start)
    cfg.set("gateway.port", new)
    return True, f"端口 {port} → {new}"


def fix_api_key(cfg, **_kw) -> tuple[bool, str]:
    if str(cfg.get("gateway.api_key") or "").strip():
        return True, "API Key 已存在"
    cfg.set("gateway.api_key", gen_api_key())
    return True, "已生成一个随机 API Key"


def fix_args(cfg, **_kw) -> tuple[bool, str]:
    args = list(cfg.get("gateway.extra_args", []) or [])
    missing = patcher.check_args(args)
    if not missing:
        return True, "启动参数本来就齐全"
    merged = args + [a for a in DEFAULT_EXTRA_ARGS if a not in args]
    cfg.set("gateway.extra_args", merged)
    return True, "补上 " + "、".join(m.split("：")[0] for m in missing)


def fix_patch(cfg, **_kw) -> tuple[bool, str]:
    gw = Path(str(cfg.get("gateway.dir") or ""))
    if not is_gateway_dir(gw):
        return False, "网关源码还没定位到，无法打补丁"
    rep = patcher.ensure(gw)
    if not rep.patched:
        return bool(rep.healthy), rep.summary()
    # 打过补丁之后 PatchReport.healthy 仍是 False —— 它记的是「体检时缺什么」，
    # 不是「补完还缺什么」。所以必须**回读一次**确认真的补进去了，
    # 否则一次成功的修复会被报成失败（这个坑当场被自测抓到过）。
    after = patcher.inspect(gw)
    return bool(after.healthy), rep.summary()


def fix_webui(cfg, **_kw) -> tuple[bool, str]:
    gw = Path(str(cfg.get("gateway.dir") or ""))
    if not is_gateway_dir(gw):
        return False, "网关源码还没定位到，无法补齐内置 WebUI"
    from . import webui

    rep = webui.ensure(gw)
    return True, rep.message


# 与 diagnose 的 item.key 一一对应。没有登记 = 需要人工处理。
FIXERS = {
    "pointer": fix_pointer,
    "gateway_dir": fix_gateway_dir,
    "python": fix_python,
    "port": fix_port,
    "api_key": fix_api_key,
    "args": fix_args,
    "patch": fix_patch,
    "webui": fix_webui,
}


# ============================================================ 总入口

def setup(cfg, *, on_log=None, allow_install: bool = True,
          prefer_venv: bool = True) -> tuple[bool, list[str]]:
    """体检 → 能修的当场修 → 复检。返回 (是否就绪, 逐行日志)。

    `on_log` 会在**工作线程**里被调用（UI 必须自己把它转成信号，
    否则就是在子线程里碰控件）。
    """
    lines: list[str] = []

    def log(text: str) -> None:
        lines.append(text)
        if on_log is not None:
            try:
                on_log(text)
            except Exception:  # noqa: BLE001
                pass  # 日志只是给人看的，绝不能因为它把配置流程带崩

    log("① 体检")
    before = diagnose(cfg, can_install=allow_install)
    for item in before.items:
        log("   " + item.line())

    todo = before.needing_fix
    if not todo:
        log("没有需要自动修复的项。")
    else:
        log(f"② 修复 {len(todo)} 项")
        changed = False
        for item in todo:
            fn = FIXERS.get(item.key)
            if fn is None:
                log(f"   · {item.title}：需要手动处理，跳过")
                continue
            try:
                ok, msg = fn(cfg, log=log, allow_install=allow_install,
                             prefer_venv=prefer_venv)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            log(f"   {'✓' if ok else '✗'} {item.title}：{msg}")
            changed = changed or ok
        if changed:
            try:
                cfg.save()
                log("   配置已保存。")
            except Exception as exc:  # noqa: BLE001
                log(f"   ✗ 配置保存失败：{exc}")

    log("③ 复检")
    after = diagnose(cfg, can_install=allow_install)
    for item in after.items:
        log("   " + item.line())

    left = after.failures
    if left:
        log("仍有 " + str(len(left)) + " 项需要手动处理：" + "、".join(i.title for i in left))
    else:
        log("环境就绪，可以启动网关。")
    if after.needing_fix:
        log("（还有可自动修复项 —— 通常是上一步没成功，看上面的失败原因）")
    return after.ready, lines


def summary_text(report: Report) -> str:
    """给 UI 的一行结论，例如「8 项检查：6 通过 / 1 可修 / 1 需手动」。"""
    c = report.counts
    parts = [f"{c['ok']} 通过"]
    if c["fix"]:
        parts.append(f"{c['fix']} 可自动修")
    if c["warn"]:
        parts.append(f"{c['warn']} 提醒")
    if c["fail"]:
        parts.append(f"{c['fail']} 需手动")
    return f"{len(report.items)} 项检查：" + " / ".join(parts)


def python_download_hint() -> str:
    """没装 Python 时该去哪儿下（与向导共用同一份链接，避免两处写死不同）。"""
    from .paths import PYTHON_DOWNLOADS

    return "　".join(f"{label}：{url}" for label, url in PYTHON_DOWNLOADS)
