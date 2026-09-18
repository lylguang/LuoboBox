"""应用上下文：把配置、网关、Funnel、后台任务串在一起，并对外发信号。

UI 层只跟信号打交道，不直接碰 subprocess —— 这样窗口永远不卡。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import logging_setup, patcher
from .clientconfig import ClaudeConfigurator, CodexConfigurator
from .config import Config, pick_free_port
from .funnel import FunnelManager, FunnelStatus
from .gateway import GatewayManager, HealthSnapshot, scheduled_task_exists
from .ui.workers import TaskRunner

log = logging_setup.get("context")


class AppContext(QObject):

    state_changed = Signal()
    health_ready = Signal(object)        # HealthSnapshot
    toast = Signal(str, str)             # (text, level)
    log_tick = Signal()                  # 日志文件可能有新增
    busy_changed = Signal(bool)

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self.runner = TaskRunner(self)
        self.gateway = GatewayManager(config, log=lambda m: log.info(m))
        self.funnel = FunnelManager(config, log=lambda m: log.info(m))
        self.codex = CodexConfigurator(config)
        self.claude = ClaudeConfigurator(config)
        self.health = HealthSnapshot()
        self.funnel_status = FunnelStatus()
        self._busy_count = 0

        self._health_timer = QTimer(self)
        self._health_timer.setInterval(int(config.get("ui.health_interval_sec", 15)) * 1000)
        self._health_timer.timeout.connect(self.refresh_health)

        self._log_timer = QTimer(self)
        self._log_timer.setInterval(1200)
        self._log_timer.timeout.connect(self.log_tick.emit)

    # ------------------------------------------------------------ 生命周期

    def start_timers(self) -> None:
        self._health_timer.start()
        self._log_timer.start()

    def stop_timers(self) -> None:
        self._health_timer.stop()
        self._log_timer.stop()

    # ------------------------------------------------------------ 忙碌标记

    def _busy(self, delta: int) -> None:
        self._busy_count = max(0, self._busy_count + delta)
        self.busy_changed.emit(self._busy_count > 0)

    def run_task(self, fn, on_ok=None, on_err=None, busy_text: str | None = None):
        """统一入口：后台跑，UI 上给个忙碌提示。"""
        self._busy(1)
        if busy_text:
            self.toast.emit(busy_text, "info")

        def ok(result):
            self._busy(-1)
            self.state_changed.emit()
            if on_ok:
                on_ok(result)

        def err(message: str):
            self._busy(-1)
            self.state_changed.emit()
            log.error("task failed: %s", message)
            if on_err:
                on_err(message)
            else:
                self.toast.emit(f"操作失败：{message.splitlines()[0]}", "error")

        return self.runner.run(fn, ok, err)

    # ------------------------------------------------------------ 网关

    @property
    def state(self) -> str:
        return self.gateway.state

    @property
    def quick_state(self) -> str:
        """不触发网络探测的轻量状态，供 UI 高频刷新用。"""
        if self.gateway.owned:
            return "running"
        return self.gateway.state

    def validate(self) -> list[str]:
        return self.config.validate()

    def start_gateway(self, with_funnel: bool = True) -> None:
        problems = self.config.validate()
        if problems:
            self.toast.emit("配置有问题：" + "；".join(problems), "error")
            return

        def work():
            ok, msg = self.gateway.start()
            if ok and with_funnel and self.config.get("funnel.enabled"):
                # Funnel 在后台挂，不阻塞启动
                self.funnel.enable_async(lambda o, m: self.toast.emit(m, "ok" if o else "warn"))
            return ok, msg

        def done(result):
            ok, msg = result
            self.toast.emit(msg, "ok" if ok else "error")
            if ok:
                self.refresh_health()

        self.run_task(work, done, busy_text="正在启动网关…")

    def stop_gateway(self, kill_external: bool = True) -> None:
        def work():
            return self.gateway.stop(kill_external=kill_external)

        self.run_task(work, lambda r: self.toast.emit(r[1], "ok"), busy_text="正在停止网关…")

    def restart_gateway(self) -> None:
        def work():
            ok, msg = self.gateway.restart()
            if ok and self.config.get("funnel.enabled"):
                self.funnel.enable_async(None)
            return ok, msg

        def done(result):
            ok, msg = result
            self.toast.emit(msg, "ok" if ok else "error")
            if ok:
                self.refresh_health()

        self.run_task(work, done, busy_text="正在重启网关…")

    # ------------------------------------------------------------ 健康检查

    def refresh_health(self) -> None:
        if self.runner.busy():
            return

        def work():
            snap = self.gateway.collect_health()
            fst = self.funnel.status()
            return snap, fst

        def done(result):
            snap, fst = result
            self.health = snap
            self.funnel_status = fst
            self.health_ready.emit(snap)
            self.state_changed.emit()

        self.runner.run(work, done, lambda _m: None)

    # ------------------------------------------------------------ Funnel

    def set_funnel(self, enabled: bool) -> None:
        self.config.set("funnel.enabled", enabled)
        self.config.save()
        if enabled:
            if not self.config.get("funnel.enabled"):
                return
            self.run_task(lambda: self.funnel.enable(), self._funnel_done,
                          busy_text="正在开启公网入口…")
        else:
            self.run_task(lambda: self.funnel.disable(), self._funnel_done,
                          busy_text="正在关闭公网入口…")

    def _funnel_done(self, result) -> None:
        ok, msg = result
        self.toast.emit(msg, "ok" if ok else "warn")
        self.refresh_health()

    # ------------------------------------------------------------ 脱敏补丁

    def patch_status(self):
        return patcher.inspect(Path(str(self.config.get("gateway.dir"))))

    def repatch(self) -> None:
        gw = Path(str(self.config.get("gateway.dir")))
        self.run_task(lambda: patcher.ensure(gw),
                      lambda rep: self.toast.emit(
                          rep.summary() + ("。改动生效需重启网关" if rep.patched else ""),
                          "ok" if rep.healthy else "err"),
                      busy_text="正在体检脱敏补丁…")

    # ------------------------------------------------------------ 首次运行

    def ensure_ready(self) -> list[str]:
        """把明显没配好的项自动修好，返回修复说明。

        只在"确实是空的/错的"才动手，绝不覆盖用户显式设过的值。
        每次启动都会跑一遍 —— 因为打包成 exe 后，用户的 Python 路径可能已经变了。
        """
        fixes: list[str] = []
        cfg = self.config

        gw = Path(str(cfg.get("gateway.dir") or ""))
        if not (gw / "converter.py").is_file():
            from .paths import default_gateway_dir, is_gateway_dir

            # 先看上次用过且仍然有效的目录
            remembered = Path(str(cfg.get("app.last_gateway_dir") or ""))
            found: Path | None = None
            if is_gateway_dir(remembered):
                found = remembered
            else:
                candidate = default_gateway_dir()
                if is_gateway_dir(candidate):
                    found = candidate
            if found is not None:
                cfg.set("gateway.dir", str(found))
                cfg.set("app.last_gateway_dir", str(found))
                fixes.append(f"网关目录 → {found}")
            else:
                # 刻意**不**写入一个无效路径 —— 那只会把错误藏起来，
                # 让用户在"目录明明填对了却还是报错"里打转。
                fixes.append("未能自动定位 codebuddy2api 网关目录，请在向导 / 「设置」里指定")
        else:
            cfg.set("app.last_gateway_dir", str(gw))

        py_raw = str(cfg.get("gateway.python") or "").strip()
        if not py_raw or not Path(py_raw).exists():
            from .paths import find_python

            py, report = find_python(py_raw or None)
            if py:
                cfg.set("gateway.python", str(py))
                fixes.append(f"Python 解释器 → {py}")
            else:
                log.error("找不到可用的 Python：%s", [str(r) for r in report[:5]])
                fixes.append("未能自动找到带 fastapi/uvicorn/httpx 的 Python，请到「设置」手动指定")

        from .config import port_free

        port = int(cfg.get("gateway.port", 8788) or 0)
        if port > 0 and not port_free(port):
            # 端口被占 ≠ 要挪走。先探测占着的是不是一个健康的网关 ——
            # 计划任务/旧脚本拉起的实例就常驻在这。无脑挪端口会让
            # 签到/余额/凭证全部打到空端口上（真实踩坑：8788 被外部
            # 网关占用 → 配置被挪到 8789 → 管理接口全连不上）。
            from .gateway import _http_json

            code, _ = _http_json(f"http://127.0.0.1:{port}/health", timeout=2.5)
            if code == 200:
                fixes.append(f"检测到网关已在端口 {port} 外部运行，采用它（不重复启动）")
            else:
                new_port = pick_free_port(port + 1)
                cfg.set("gateway.port", new_port)
                fixes.append(f"端口 {port} 被其它程序占用 → {new_port}")
        elif port <= 0:
            new_port = pick_free_port(8788)
            cfg.set("gateway.port", new_port)
            fixes.append(f"端口非法 → {new_port}")

        if fixes:
            cfg.save()
            log.info("ensure_ready: %s", "；".join(fixes))
        return fixes

    # 兼容旧调用名
    def apply_first_run_defaults(self) -> None:
        self.ensure_ready()

    # ------------------------------------------------------------ 退出

    def shutdown(self) -> None:
        log.info("shutdown: stop_on_exit=%s", self.config.get("gateway.stop_on_exit"))
        self.stop_timers()
        if self.config.get("gateway.stop_on_exit") and self.gateway.owned:
            self.gateway.stop(kill_external=False)
        self.runner.wait_all(3000)


def warn_scheduled_task(config: Config) -> str | None:
    """检测旧部署残留的计划任务 —— 会和萝卜盒抢同一个端口。"""
    if scheduled_task_exists():
        return ("检测到系统里还有计划任务 codebuddy2api 在托管同一个网关，"
                "两者会抢端口。建议在「设置 → 迁移」里移除它，改由萝卜盒统一管理。")
    return None


def env_hint() -> str:
    return os.name
