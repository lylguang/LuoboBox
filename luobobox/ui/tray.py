"""系统托盘：日常操作的主入口。

托盘菜单是这类工具真正的"主界面" —— 打开窗口只是为了看细节。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import theme
from .. import __version__, autostart
from ..paths import icon_path, resource_dir

ICON_FILES = {
    "running": "tray_on.png",
    "external": "tray_on.png",
    "starting": "tray_busy.png",
    "stopping": "tray_busy.png",
    "error": "tray_busy.png",
    "stopped": "tray_off.png",
}


class TrayController(QObject):

    def __init__(self, app: QApplication, ctx, window):
        super().__init__(app)
        self.app = app
        self.ctx = ctx
        self.window = window

        self.tray = QSystemTrayIcon(self._icon("stopped"), app)
        self.tray.setToolTip(f"萝卜盒 {__version__}")
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        ctx.state_changed.connect(self.refresh)
        ctx.toast.connect(self._notify)
        self.refresh()

    # ---------------------------------------------------------------- 图标

    def _icon(self, state: str) -> QIcon:
        name = ICON_FILES.get(state, "tray_off.png")
        path = resource_dir() / name
        if path.is_file():
            return QIcon(str(path))
        fallback = icon_path()
        return QIcon(str(fallback)) if fallback.is_file() else QIcon()

    # ---------------------------------------------------------------- 菜单

    def _build_menu(self) -> None:
        m = self.menu

        self.act_head = QAction(f"萝卜盒 {__version__}", m)
        self.act_head.setEnabled(False)
        m.addAction(self.act_head)

        self.act_state = QAction("状态：检测中", m)
        self.act_state.setEnabled(False)
        m.addAction(self.act_state)
        m.addSeparator()

        self.act_open = QAction("打开控制台", m)
        self.act_open.triggered.connect(self._show_window)
        m.addAction(self.act_open)

        self.act_dashboard = QAction("打开 WebUI 管理台", m)
        self.act_dashboard.triggered.connect(self._open_dashboard)
        m.addAction(self.act_dashboard)
        m.addSeparator()

        self.act_start = QAction("启动网关", m)
        self.act_start.triggered.connect(lambda: self.ctx.start_gateway())
        m.addAction(self.act_start)

        self.act_stop = QAction("停止网关", m)
        self.act_stop.triggered.connect(lambda: self.ctx.stop_gateway())
        m.addAction(self.act_stop)

        self.act_restart = QAction("重启网关", m)
        self.act_restart.triggered.connect(self.ctx.restart_gateway)
        m.addAction(self.act_restart)

        self.act_funnel = QAction("公网入口（Funnel）", m)
        self.act_funnel.setCheckable(True)
        self.act_funnel.triggered.connect(lambda checked: self.ctx.set_funnel(checked))
        m.addAction(self.act_funnel)
        m.addSeparator()

        self.act_copy_key = QAction("复制 API Key", m)
        self.act_copy_key.triggered.connect(self._copy_key)
        m.addAction(self.act_copy_key)

        self.act_checkin = QAction("立即签到", m)
        self.act_checkin.triggered.connect(self._checkin)
        m.addAction(self.act_checkin)

        self.act_update = QAction("检查更新", m)
        self.act_update.triggered.connect(self._check_update)
        m.addAction(self.act_update)
        m.addSeparator()

        self.act_autostart = QAction("开机自启", m)
        self.act_autostart.setCheckable(True)
        self.act_autostart.triggered.connect(self._toggle_autostart)
        m.addAction(self.act_autostart)

        self.act_quit = QAction("退出萝卜盒", m)
        self.act_quit.triggered.connect(self._quit)
        m.addAction(self.act_quit)

    # ---------------------------------------------------------------- 刷新

    def refresh(self) -> None:
        state = self.ctx.quick_state
        label = self.ctx.gateway.state_label
        self.act_state.setText(f"状态：{label}")
        self.act_start.setEnabled(state not in ("running", "starting"))
        self.act_stop.setEnabled(state in ("running", "external", "starting"))
        self.act_restart.setEnabled(state in ("running", "external"))
        self.act_dashboard.setEnabled(state in ("running", "external"))

        self.act_funnel.blockSignals(True)
        self.act_funnel.setChecked(bool(self.ctx.config.get("funnel.enabled")))
        self.act_funnel.blockSignals(False)

        self.act_autostart.blockSignals(True)
        self.act_autostart.setChecked(autostart.is_autostart_on())
        self.act_autostart.blockSignals(False)

        self.tray.setIcon(self._icon(state))
        self.tray.setToolTip(f"萝卜盒 · {label}")

    def _notify(self, text: str, level: str) -> None:
        if level == "error":
            icon = QSystemTrayIcon.Critical
        elif level == "warn":
            icon = QSystemTrayIcon.Warning
        else:
            icon = QSystemTrayIcon.Information
        self.tray.showMessage("萝卜盒", text.splitlines()[0][:240], icon, 4000)

    # ---------------------------------------------------------------- 动作

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._show_window()

    def _show_window(self) -> None:
        self.window.show()
        self.window.setWindowState(
            self.window.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.window.raise_()
        self.window.activateWindow()

    def _open_dashboard(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(self.ctx.config.dashboard_url()))

    def _copy_key(self) -> None:
        QApplication.clipboard().setText(str(self.ctx.config.get("gateway.api_key", "")))
        self._notify("API Key 已复制到剪贴板", "ok")

    def _checkin(self) -> None:
        self.window._checkin()  # noqa: SLF001

    def _check_update(self) -> None:
        self.window.tabs.setCurrentIndex(3)
        self._show_window()
        self.window._check_update()  # noqa: SLF001

    def _toggle_autostart(self, checked: bool) -> None:
        ok, msg = autostart.set_autostart(checked)
        self._notify(msg if ok else f"操作失败：{msg}", "ok" if ok else "error")
        self.refresh()

    def _quit(self) -> None:
        self.window.request_quit()
        self.tray.hide()
        self.app.quit()
