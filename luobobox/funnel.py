"""Tailscale Funnel 公网入口开关。

踩过的坑（写死在代码里，防止以后再踩）：
  * Funnel 只允许 443 / 8443 / 10000 三个端口。443 已被 tuangou 占用，这里默认 8443。
  * **绝对不用 `tailscale funnel reset`** —— 那会把同机 443 上别人的 Funnel 一起清掉。
    关闭必须用带端口的 `--https=<port> off`。
  * serve/funnel 配置**不保证跨重启保留**，所以要能被重复调用（幂等）。
  * 登录自启那一刻 tailscaled 可能还没就绪，所以带重试。
  * 网关绑定 0.0.0.0 的直连端口**不需要**动防火墙：Funnel 由本机 tailscaled
    从回环 127.0.0.1 转发进来，不走防火墙入站。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

CREATE_NO_WINDOW = 0x08000000


@dataclass
class FunnelStatus:
    available: bool = False
    enabled: bool = False
    url: str = ""
    detail: str = ""
    running: bool = False


class FunnelManager:
    def __init__(self, config, log: Callable[[str], None] | None = None):
        self.config = config
        self._log = log or (lambda _m: None)

    # ------------------------------------------------------------ 基础设施

    @property
    def exe(self) -> str:
        return str(self.config.get("funnel.tailscale_exe", r"C:\Program Files\Tailscale\tailscale.exe"))

    @property
    def port(self) -> str:
        return str(self.config.get("funnel.port", "8443"))

    def available(self) -> bool:
        return os.path.exists(self.exe)

    def _run(self, args: list[str], timeout: int = 30) -> tuple[int, str]:
        if not self.available():
            return 127, f"找不到 tailscale.exe：{self.exe}"
        try:
            r = subprocess.run(
                [self.exe, *args],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            return r.returncode, f"{(r.stdout or '').strip()}\n{(r.stderr or '').strip()}".strip()
        except subprocess.TimeoutExpired:
            return 124, "执行超时"
        except Exception as exc:  # noqa: BLE001
            return 1, str(exc)

    def backend_state(self) -> tuple[bool, str]:
        """tailscaled 是否在跑（BackendState == Running）。"""
        code, out = self._run(["status", "--json"], timeout=20)
        if code != 0:
            return False, out[:200]
        try:
            data = json.loads(out)
        except Exception:  # noqa: BLE001
            return False, "无法解析 tailscale status --json"
        return str(data.get("BackendState", "")) == "Running", str(data.get("BackendState", ""))

    def hostname(self) -> str:
        """本机在 Tailscale 里的完整域名，用于拼公网 URL。"""
        cached = str(self.config.get("funnel.hostname", "") or "")
        if cached:
            return cached
        code, out = self._run(["status", "--json"], timeout=20)
        if code == 0:
            try:
                name = str(json.loads(out).get("Self", {}).get("DNSName", "")).rstrip(".")
                if name:
                    self.config.set("funnel.hostname", name)
                    self.config.save()
                    return name
            except Exception:  # noqa: BLE001
                pass
        return ""

    def public_url(self) -> str:
        host = self.hostname()
        return f"https://{host}:{self.port}" if host else ""

    # ------------------------------------------------------------ 状态

    def status(self) -> FunnelStatus:
        st = FunnelStatus(available=self.available())
        if not st.available:
            st.detail = f"未找到 tailscale.exe（{self.exe}）"
            return st
        running, state = self.backend_state()
        st.running = running
        code, out = self._run(["funnel", "status"], timeout=20)
        if code != 0:
            st.detail = out[:200] or "funnel status 失败"
            return st
        target = f"127.0.0.1:{self.config.get('gateway.port', 8788)}"
        st.enabled = target in out and f":{self.port}" in out
        st.url = self.public_url() if st.enabled else ""
        st.detail = "已开启" if st.enabled else ("未开启" if running else f"Tailscale 未运行（{state}）")
        return st

    # ------------------------------------------------------------ 开关

    def enable(self, url_target: str | None = None, attempts: int = 12, interval: float = 5.0) -> tuple[bool, str]:
        """幂等开启。url_target 默认指向本机网关端口。"""
        if not self.available():
            return False, f"找不到 tailscale.exe：{self.exe}"
        target = url_target or f"http://127.0.0.1:{self.config.get('gateway.port', 8788)}"
        last = ""
        for attempt in range(1, attempts + 1):
            code, out = self._run(
                ["funnel", "--bg", f"--https={self.port}", target], timeout=40
            )
            if code == 0:
                self._log(f"[funnel] 已挂上 {target} -> :{self.port}（第 {attempt} 次尝试）")
                return True, f"公网入口已开启：{self.public_url() or target}"
            last = out[:300]
            self._log(f"[funnel] 第 {attempt} 次失败 rc={code}: {last}")
            if attempt < attempts:
                time.sleep(interval)
        return False, f"连续 {attempts} 次失败：{last}"

    def disable(self) -> tuple[bool, str]:
        """按端口关闭。绝不 reset —— 会连别人的 443 一起清掉。"""
        if not self.available():
            return False, f"找不到 tailscale.exe：{self.exe}"
        code, out = self._run(["funnel", f"--https={self.port}", "off"], timeout=30)
        if code == 0:
            self._log(f"[funnel] 已关闭 :{self.port}")
            return True, "公网入口已关闭"
        return False, out[:300] or f"关闭失败（rc={code}）"

    def enable_async(self, on_done: Callable[[bool, str], None] | None = None) -> None:
        """守护线程里开启，不阻塞 UI / 启动流程。"""
        import threading

        def worker():
            ok, msg = self.enable()
            if on_done:
                on_done(ok, msg)

        threading.Thread(target=worker, name="funnel-enable", daemon=True).start()
