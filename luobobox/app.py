"""应用入口：单实例、主题、首启向导、托盘、自启网关。"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import __version__, logging_setup
from .config import Config
from .context import AppContext
from .paths import icon_path

SERVER_NAME = "LuoboBox.SingleInstance"
log = logging_setup.get("app")


def _already_running() -> bool:
    """已有实例就把它唤到前台，然后自己退出。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(400):
        sock.write(b"SHOW\n")
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return True
    return False


def _install_server(on_show) -> QLocalServer:
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    def handle():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: (conn.readAll(), on_show()))
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(handle)
    return server


def _selftest(app: QApplication, config) -> int:
    """打包产物的自检：把启动链路全跑一遍但**不进事件循环**，然后退出。

    为什么需要：GUI 程序从自动化环境里跑事件循环会被回收，没法验证"双击能用"。
    这个模式把所有会出错的构建步骤都执行到，足以证明包是完整的。
    结果同时写到 <数据目录>/selftest.log（冻结后没有控制台）。
    """
    from .paths import data_dir

    lines: list[str] = []
    ok = True

    def step(name: str, fn):
        nonlocal ok
        try:
            detail = fn()
            lines.append(f"PASS  {name}" + (f"   {detail}" if detail else ""))
        except Exception as exc:  # noqa: BLE001
            ok = False
            import traceback

            lines.append(f"FAIL  {name}   {type(exc).__name__}: {exc}")
            lines.append(traceback.format_exc())

    step("QApplication", lambda: app.platformName())
    step("图标资源", lambda: f"{icon_path()} 存在={icon_path().is_file()}")
    step("托盘资源", lambda: "、".join(
        p.name for p in sorted(icon_path().parent.glob("tray_*.png"))))
    step("样式表", lambda: f"{len(theme_stylesheet())} 字符")

    ctx_holder: dict = {}

    def build_ctx():
        from .context import AppContext

        ctx = AppContext(config)
        ctx_holder["ctx"] = ctx
        return None

    step("AppContext", build_ctx)

    def ensure():
        ctx = ctx_holder["ctx"]
        return "；".join(ctx.ensure_ready())

    step("环境自愈", ensure)

    def build_win():
        from .ui.main_window import MainWindow

        win = MainWindow(ctx_holder["ctx"])
        ctx_holder["win"] = win
        return f"{win.tabs.count()} 个页签"

    step("主窗口", build_win)

    def build_tray():
        from PySide6.QtWidgets import QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return "跳过（本环境无托盘区域，已降级为无托盘模式）"
        from .ui.tray import TrayController

        tray = TrayController(app, ctx_holder["ctx"], ctx_holder["win"])
        ctx_holder["tray"] = tray
        return f"{len(tray.menu.actions())} 个菜单项"

    step("系统托盘", build_tray)

    def check_gw():
        from .paths import is_gateway_dir

        gw = config.get("gateway.dir")
        if not is_gateway_dir(gw):
            raise RuntimeError(f"网关目录无效：{gw}")
        return str(gw)

    step("网关目录", check_gw)

    step("Python 探测", lambda: str(config.get("gateway.python")) or "未设置")

    def check_patcher():
        from . import patcher

        rep = patcher.inspect(config.get("gateway.dir"))
        return rep.summary()

    step("脱敏补丁体检", check_patcher)

    def check_funnel():
        from .funnel import FunnelManager

        fm = FunnelManager(config)
        return f"tailscale 存在={fm.available()} 端口={fm.port}"

    step("Funnel", check_funnel)

    def check_clients():
        from .clientconfig import ClaudeConfigurator, CodexConfigurator

        cx = CodexConfigurator(config).status()
        cl = ClaudeConfigurator(config).status()
        return f"Codex={cx['reason']}；Claude={cl['reason']}"

    step("客户端配置", check_clients)

    lines.append("")
    lines.append("自检结果：" + ("全部通过" if ok else "存在失败项"))

    text = "\n".join(lines)
    try:
        (data_dir() / "selftest.log").write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    # 冻结后的窗口程序（console=False）stdout 落到管道时按系统码页（GBK）编码，
    # 且不理会 PYTHONIOENCODING —— 中文打出来就是乱码。统一重配成 UTF-8，
    # 与打包脚本 build.py 的 utf-8 解码约定对齐。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print(text, flush=True)

    for key in ("tray", "win"):
        obj = ctx_holder.get(key)
        if obj is not None:
            try:
                obj.hide()
            except Exception:  # noqa: BLE001
                pass
    ctx = ctx_holder.get("ctx")
    if ctx is not None:
        ctx.stop_timers()

    # 自检模式**必须**用 os._exit 收尾，不能正常 return。
    # 原因：打包后正常退出会走 Python 解释器拆解，此时上面建的
    # QApplication / MainWindow / 托盘还挂在各种引用上，拆解顺序不可控，
    # 实测会以 0xC0000409（STATUS_STACK_BUFFER_OVERRUN）崩掉 ——
    # 自检明明全过，退出码却是非 0，打包脚本跟着误报"自检未通过"。
    # gui_smoke.py 早就是这个套路，这里对齐。
    code = 0 if ok else 1
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)


def theme_stylesheet() -> str:
    from .ui import theme

    return theme.stylesheet()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging_setup.setup()
    log.info("=" * 50)
    log.info("LuoboBox %s starting, argv=%s", __version__, argv)

    QApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings, True)
    app = QApplication(argv)
    app.setApplicationName("LuoboBox")
    app.setApplicationDisplayName("萝卜盒")
    app.setOrganizationName("LuoboBox")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)   # 关掉窗口只是隐藏，托盘继续驻留

    if icon_path().is_file():
        app.setWindowIcon(QIcon(str(icon_path())))

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    if not tray_available:
        # 不直接退出：某些环境（远程会话、精简系统）没有托盘区域，
        # 但窗口仍然能用。降级运行，总比打不开强。
        log.warning("系统没有可用的托盘区域，降级为无托盘模式")

    from .ui import theme

    config = Config.load()
    config.set("meta.luobobox_version", __version__)
    config.save()

    # 先读配置里的外观偏好，再出样式表 —— 反过来会先闪一下默认深色，
    # 再跳成用户选的那套（很明显的"咯噔"一下）。
    theme.apply(config.get("ui.palette"), config.get("ui.accent"),
                config.get("ui.scale"))
    app.setStyleSheet(theme.stylesheet())

    # ---- 打包自检模式：跑完启动链路就退，不进事件循环
    if "--selftest" in argv:
        return _selftest(app, config)

    if _already_running():
        log.info("已有实例在运行，退出本次启动")
        return 0

    ctx = AppContext(config)

    from .ui.main_window import MainWindow

    window = MainWindow(ctx)

    # 额度消耗标签页：基于本地余额快照反推的消耗统计（纯本地，不依赖上游网关）
    from . import usage_view

    # ★ 走 window.add_tab 而不是 window.tabs.addTab：
    # 必须登记进 _tab_index，否则 goto_tab / Ctrl+7 / 命令面板都找不到这一页。
    window.add_tab("usage", usage_view.build_usage_tab(ctx), "额度消耗")

    if tray_available:
        from .ui.tray import TrayController

        tray = TrayController(app, ctx, window)
        app.tray = tray  # type: ignore[attr-defined]
        server = _install_server(tray._show_window)  # noqa: SLF001
        app._server = server  # type: ignore[attr-defined]  防 GC
    else:
        app.tray = None  # type: ignore[attr-defined]
        window.force_quit_on_close()

    # ---- 环境自愈：每次启动都跑，因为打包后用户的 Python 路径可能已经变了
    fixes = ctx.ensure_ready()
    for line in fixes:
        log.info("ensure_ready: %s", line)

    # ---- 首次运行向导
    if not config.get("app.first_run_done"):
        from .ui.wizard import FirstRunWizard

        wizard = FirstRunWizard(ctx, window)
        wizard.finished_ok.connect(lambda ok: _after_wizard(ctx, window, wizard, ok))
        wizard.exec()
    elif fixes:
        window._on_toast("已自动修正：" + "；".join(fixes), "info")  # noqa: SLF001

    ctx.start_timers()

    # ---- 自启网关
    if config.get("gateway.auto_start") and not ctx.gateway.owned:
        ctx.start_gateway(with_funnel=bool(config.get("funnel.enabled")))

    if "--tray" not in argv and not config.get("app.silent_start"):
        window.show()
        if "--minimized" in argv:
            window.hide()

    return app.exec()


def _after_wizard(ctx: AppContext, window, wizard, ok: bool) -> None:
    if not ok:
        window.show()
        return
    for line in wizard.summary:
        log.info("wizard: %s", line)
    if wizard.summary:
        window._on_toast("首次设置完成。" + "；".join(wizard.summary), "ok")  # noqa: SLF001
    window.show()


if __name__ == "__main__":
    raise SystemExit(main())
