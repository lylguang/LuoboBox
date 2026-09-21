"""带代理回退的 HTTP 层 —— 让「下载 GitHub 资产」在真实网络环境下尽量成功。

## 为什么需要这个模块

萝卜盒要能连上 GitHub 才能检查更新、下载安装包。而「能不能连上」取决于代理，
Windows 上代理有三个来源，可靠程度完全不同：

    1. 用户在萝卜盒里手动配的    —— 最可信；连不上时用户自己能改，能自救
    2. Windows 系统代理（注册表）—— 用户真正在用的那个（浏览器也走它）
    3. 环境变量 HTTP(S)_PROXY   —— 开发环境常见，但**可能是别人注入的**

## 那个把用户坑了 21 秒的坑

进程从某个 shell 继承来的 ``HTTPS_PROXY`` 可能指向一个**早就死掉的**本地代理
（例如临时端口）。这时 ``urllib`` 会老老实实去连它，连不上就干等 ——
最终抛 ``urlopen error [WinError 10060] 由于连接方在一段时间后没有正确答复…``。

**这个报错极具误导性**：文案像是在说「GitHub 连不上」，实际是「代理连不上」。
用户看到只会以为是网络问题，反复重试也没用。

所以本模块对每个候选代理先做一次 **短超时 TCP 探活**：连不上就立刻跳过，
不浪费用户时间；直连候选也先探一次目标端口，让失败在 3 秒内出结果。
全部失败时，把**每条通道各自的失败原因**汇总抛出去，一眼能看出该改什么。
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# 探活超时：够本机代理/局域网代理应答，又不至于让用户干等
PROXY_PROBE_TIMEOUT = 0.8
DIRECT_PROBE_TIMEOUT = 3.0

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class NetError(urllib.error.URLError):
    """所有候选通道都失败。消息里带每条通道的失败原因。"""

    def __init__(self, message: str, attempts: list[str] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


@dataclass
class Route:
    """一次成功请求实际走的通道。"""
    desc: str = ""
    proxy: str = ""

    def __str__(self) -> str:
        return self.desc or "直连"


# ---------------------------------------------------------------- 代理探测

def _split_proxy(value: str) -> tuple[str, int] | None:
    """把 'host:port' / 'http://host:port' 解析成 (host, port)。"""
    v = (value or "").strip()
    if not v:
        return None
    if "://" not in v:
        v = "http://" + v
    try:
        u = urllib.parse.urlsplit(v)
    except ValueError:
        return None
    host = u.hostname
    if not host:
        return None
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, int(port)


def _tcp_ok(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _proxy_alive(proxy: str, timeout: float = PROXY_PROBE_TIMEOUT) -> bool:
    hp = _split_proxy(proxy)
    if hp is None:
        return False
    return _tcp_ok(hp[0], hp[1], timeout)


def win_system_proxy() -> str:
    """读 Windows 系统代理（Internet 设置）。取不到就返回空串。"""
    try:
        import winreg
    except ImportError:  # 非 Windows
        return ""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
    except OSError:
        return ""
    try:
        try:
            if not int(winreg.QueryValueEx(key, "ProxyEnable")[0]):
                return ""
        except (OSError, ValueError, TypeError):
            return ""
        try:
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
        except OSError:
            return ""
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass
    if not server:
        return ""
    # 可能是 "host:port"，也可能是 "http=h:p;https=h:p" 这种按协议分列
    if "=" in server:
        table = {}
        for chunk in server.split(";"):
            if "=" in chunk:
                k, _, v = chunk.partition("=")
                table[k.strip().lower()] = v.strip()
        return table.get("https") or table.get("http") or ""
    return server


def env_proxy() -> str:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                 "ALL_PROXY", "all_proxy"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def _is_local(url: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS or host.startswith("127.")


def proxy_candidates(cfg_proxy: str = "") -> list[tuple[str, str]]:
    """按优先级返回 [(描述, 代理URL)]，代理为空串表示直连。

    本机地址（localhost/127.x）**永远直连** —— 系统代理设置里通常也会
    把它们排除掉，我们显式短路，避免把本机请求绕出去。

    注意：手动配置的代理**排在第一位，但不是唯一候选**。
    用户配的代理也可能挂掉（进程退了、端口被占），这时继续往后试
    比直接报错有用得多 —— 最后兜底是直连，并把实际走通的通道回报给 UI。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(desc: str, value: str) -> None:
        v = (value or "").strip()
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        out.append((desc, v))

    manual = (cfg_proxy or "").strip()
    if manual:
        add("手动配置的代理 " + manual, manual)
    sysp = win_system_proxy()
    if sysp:
        add("系统代理 " + sysp, sysp)
    envp = env_proxy()
    if envp:
        add("环境变量代理 " + envp, envp)
    add("直连", "")
    return out


def describe_routes(cfg_proxy: str = "") -> list[str]:
    """给 UI 用：列出候选通道以及它们的可用性（不发起真实请求）。"""
    rows = []
    for desc, proxy in proxy_candidates(cfg_proxy):
        if not proxy:
            rows.append(f"{desc}（最后兜底）")
        else:
            ok = _proxy_alive(proxy)
            rows.append(f"{desc} —— {'可达' if ok else '不可达，会被跳过'}")
    return rows


# ---------------------------------------------------------------- 请求

def _opener(proxy: str) -> urllib.request.OpenerDirector:
    if not proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def urlopen(url: str, *, headers: dict | None = None, timeout: int = 30,
            cfg_proxy: str = "", probe: bool = True):
    """按候选通道依次尝试。返回 ``(response, Route)``；全失败抛 ``NetError``。

    每个代理先探活（probe=False 可关掉，测试用）；直连候选也先探一次目标端口，
    这样「全都连不上」能在几秒内给出结论，而不是让用户等几分钟。
    """
    attempts: list[str] = []
    if _is_local(url):
        candidates: list[tuple[str, str]] = [("直连（本机地址）", "")]
    else:
        candidates = proxy_candidates(cfg_proxy)

    target = None
    if probe and not _is_local(url):
        try:
            u = urllib.parse.urlsplit(url)
            target = (u.hostname, u.port or (443 if u.scheme == "https" else 80))
        except ValueError:
            target = None

    for desc, proxy in candidates:
        if probe and proxy and not _proxy_alive(proxy):
            attempts.append(f"{desc}：连不上（探活 {PROXY_PROBE_TIMEOUT}s 超时，已跳过）")
            continue
        if probe and not proxy and target and target[0]:
            if not _tcp_ok(target[0], target[1], DIRECT_PROBE_TIMEOUT):
                attempts.append(
                    f"{desc}：连不上 {target[0]}:{target[1]}"
                    f"（探活 {DIRECT_PROBE_TIMEOUT}s 超时）"
                )
                continue
        try:
            req = urllib.request.Request(url, headers=dict(headers or {}))
            resp = _opener(proxy).open(req, timeout=timeout)
            return resp, Route(desc=desc, proxy=proxy)
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{desc}：{type(exc).__name__}: {str(exc)[:140]}")

    raise NetError("所有下载通道都失败：\n  - " + "\n  - ".join(attempts), attempts)


def read_bytes(url: str, *, headers: dict | None = None, timeout: int = 30,
               cfg_proxy: str = "", probe: bool = True) -> tuple[bytes, Route]:
    resp, route = urlopen(url, headers=headers, timeout=timeout,
                          cfg_proxy=cfg_proxy, probe=probe)
    with resp:
        return resp.read(), route


def read_json(url: str, *, headers: dict | None = None, timeout: int = 20,
              cfg_proxy: str = "", probe: bool = True) -> tuple[dict, Route]:
    raw, route = read_bytes(url, headers=headers, timeout=timeout,
                            cfg_proxy=cfg_proxy, probe=probe)
    return json.loads(raw.decode("utf-8", "replace")), route


def download(url: str, dest: Path, *, headers: dict | None = None,
             timeout: int = 600, cfg_proxy: str = "", probe: bool = True,
             chunk: int = 1024 * 256) -> tuple[Path, Route]:
    """流式下载到 ``dest.with_suffix(dest.suffix + '.part')`` 再原子改名。

    中断不会留下半个「看起来能装」的包。返回 ``(最终路径, Route)``。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    resp, route = urlopen(url, headers=headers, timeout=timeout,
                          cfg_proxy=cfg_proxy, probe=probe)
    try:
        with open(tmp, "wb") as fh:
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                fh.write(block)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    os.replace(tmp, dest)
    return dest, route


def diagnose(cfg_proxy: str = "", url: str = "https://api.github.com/") -> str:
    """给「网络诊断」按钮用：逐条试，返回人类可读的报告。"""
    lines = [f"目标：{url}", "候选通道："]
    lines.extend("  " + r for r in describe_routes(cfg_proxy))
    lines.append("")
    lines.append("实测：")
    for desc, proxy in proxy_candidates(cfg_proxy):
        if proxy and not _proxy_alive(proxy):
            lines.append(f"  ✗ {desc} —— 探活失败")
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LuoboBox"})
            with _opener(proxy).open(req, timeout=15) as resp:
                lines.append(f"  ✓ {desc} —— HTTP {resp.status}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  ✗ {desc} —— {type(exc).__name__}: {str(exc)[:120]}")
    return "\n".join(lines)
