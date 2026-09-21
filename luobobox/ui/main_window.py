"""主窗口：概览 / 客户端 / 日志 / 更新 / 设置。

原则：所有耗时操作都走 AppContext.run_task 到后台线程，UI 线程只做渲染。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .. import __version__, appupdater, autostart, patcher, updater
from ..clientconfig import snippet_claude, snippet_codex
from ..config import gen_api_key, port_free
from ..context import AppContext, warn_scheduled_task
from ..gateway import is_admin, remove_scheduled_task
from ..paths import data_dir, find_python, icon_path, log_dir

HEALTH_LABEL = {
    "ready": ("正常", theme.OK),
    "expired": ("已过期", theme.ERR),
    "circuit_open": ("已熔断", theme.WARN),
    "error": ("异常", theme.ERR),
    "disabled": ("已停用", theme.TEXT_MUTE),
}


def _fmt_credits(value) -> str:
    """把「积分」字段收敛成一行文本。

    网关这个字段并不总是数字：账号拉过明细时它是一个对象
    （{"credits": 6845.33, "segments": [...]}），没拉到时是 None。
    早期版本直接 f"{value:,}" —— 一遇到 dict 就抛
    TypeError: unsupported format string passed to dict.__format__，
    整个 _fill_credentials 中断，凭证表一行都填不出来。
    （真实故障：管理台里明明有 12 个账号，app 里却是一片空白。）
    """
    if isinstance(value, dict):
        value = value.get("credits", value.get("balance"))
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        try:
            return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _btn(text: str, object_name: str = "", height: int = 32) -> QPushButton:
    b = QPushButton(text)
    if object_name:
        b.setObjectName(object_name)
    b.setMinimumHeight(height)
    b.setCursor(Qt.PointingHandCursor)
    return b


class MainWindow(QMainWindow):

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._closing = False
        self._pending_quit = False
        self._force_quit = False

        self.setWindowTitle(f"萝卜盒 LuoboBox {__version__}")
        if icon_path().is_file():
            self.setWindowIcon(QIcon(str(icon_path())))
        self.resize(940, 720)
        self.setMinimumSize(780, 560)

        self._build()
        self._connect()

        self.ctx.refresh_health()
        QTimer.singleShot(900, self._post_show_checks)

    # ================================================================ 构建

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 8)
        root.setSpacing(10)

        root.addWidget(self._header())

        self.tabs = QTabWidget()
        # 页签索引集中登记。以前 _on_toast / _refresh_log 里把 1/2/3 写死，
        # 一插入新页签就会静默错位（提示条跑到别页、日志不再自动刷新）。
        self._tab_index: dict[str, int] = {}
        for key, label, widget in (
            ("overview", "概览", self._tab_overview()),
            ("admin", "管理台", self._tab_admin()),
            ("clients", "客户端接入", self._tab_clients()),
            ("logs", "日志", self._tab_logs()),
            ("update", "更新", self._tab_update()),
            ("settings", "设置", self._tab_settings()),
        ):
            self._tab_index[key] = self.tabs.addTab(widget, label)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        self.statusBar().showMessage("就绪")

    def _on_tab_changed(self, index: int) -> None:
        """切到「管理台」时才拉数据 —— 不打扰其它页签，也不空转网络。"""
        if index == self._tab_index.get("admin"):
            self.admin.refresh()

    # ---------------------------------------------------------------- 管理台

    def _tab_admin(self) -> QWidget:
        """原生管理台。

        为什么不用内嵌 WebUI：上游前端的 dist/ 不在源码仓库里，而网关升级是
        「整目录 rmtree + copytree」—— 每次升级都会把 web/dist 冲掉，
        /dashboard/ 直接 503。这就是「后台管理打不开」的根因，且必然复发。
        这里直接调网关的 /admin REST 接口，与前端构建彻底解耦。
        """
        from .admin import AdminConsole

        self.admin = AdminConsole(self.ctx)

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 8)
        box.setSpacing(8)

        hint = QLabel(
            "本页直接调用网关的管理接口，与前端构建无关 —— 升级网关也不会再「打不开」。"
            "新增账号、看用量、翻日志都在这里。"
        )
        hint.setObjectName("mute")
        hint.setWordWrap(True)
        box.addWidget(hint)
        box.addWidget(self.admin, 1)
        return page

    # ---------------------------------------------------------------- 页头

    def _header(self) -> QWidget:
        from .widgets import StatusDot

        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(10)

        self.dot = StatusDot(theme.TEXT_MUTE, 13)
        row.addWidget(self.dot)

        text = QVBoxLayout()
        text.setSpacing(1)
        self.state_label = QLabel("正在检测…")
        self.state_label.setObjectName("h2")
        text.addWidget(self.state_label)
        self.state_sub = QLabel("")
        self.state_sub.setObjectName("mute")
        text.addWidget(self.state_sub)
        row.addLayout(text)
        row.addStretch(1)

        self.btn_start = _btn("启动", "primary")
        self.btn_stop = _btn("停止")
        self.btn_restart = _btn("重启")
        self.btn_dashboard = _btn("网页版管理台")
        self.btn_dashboard.setToolTip(
            "用浏览器打开网关自带的 WebUI（/dashboard/）。\n"
            "日常用上面的「管理台」页签即可；这个入口留给多屏 / 远程场景。")
        for b in (self.btn_start, self.btn_stop, self.btn_restart, self.btn_dashboard):
            row.addWidget(b)
        return box

    # ---------------------------------------------------------------- 概览

    def _tab_overview(self) -> QWidget:
        from .widgets import Card, KeyValue, Separator, Toast

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(12)

        self.overview_toast = Toast()
        box.addWidget(self.overview_toast)

        # ---- 服务
        card = Card("服务状态", "网关进程由萝卜盒托管，关掉窗口不会中断服务")
        self.kv_port = KeyValue("端口", "—", mono=True)
        self.kv_pid = KeyValue("进程号", "—", mono=True)
        self.kv_models = KeyValue("可用模型", "—")
        self.kv_latency = KeyValue("响应延迟", "—")
        for w in (self.kv_port, self.kv_pid, self.kv_models, self.kv_latency):
            card.add(w)
        card.add(Separator())
        self.kv_patch = KeyValue("脱敏补丁", "—")
        card.add(self.kv_patch)
        btn_row = QHBoxLayout()
        self.btn_repatch = _btn("一键重打补丁", "ghost")
        self.btn_repatch.setToolTip(
            "检查 app/desensitize.py 是否还含 Codex 必需的 5 个品牌词；\n"
            "缺失就自动补回。升级网关后必做，否则 Codex 会被上游 11128 拦截。"
        )
        self.btn_checkin = _btn("立即签到", "ghost")
        btn_row.addWidget(self.btn_repatch)
        btn_row.addWidget(self.btn_checkin)
        btn_row.addStretch(1)
        card.add_layout(btn_row)
        box.addWidget(card)

        # ---- 地址
        addr = Card("接入地址", "把下面的地址填进客户端；Key 与 WebUI 登录用的是同一把")
        from .widgets import CopyField

        self.f_local = CopyField(self.ctx.config.base_url())
        self.f_lan = CopyField("")
        self.f_ts = CopyField("")
        self.f_pub = CopyField("")
        self.f_key = CopyField(str(self.ctx.config.get("gateway.api_key", "")), masked=True)

        addr.add(self._field_row("本机", self.f_local))
        addr.add(self._field_row("局域网", self.f_lan))
        addr.add(self._field_row("Tailscale", self.f_ts))
        addr.add(self._field_row("公网", self.f_pub))
        addr.add(self._field_row("API Key", self.f_key))
        box.addWidget(addr)

        # ---- 凭证池
        cred = Card("凭证池",
                    "这里只做展示（每 15 秒自动刷新）。新增账号请到「管理台 → 凭证」"
                    "用扫码或导入 .info 文件，加完会自动出现在下表。")
        self.cred_table = QTableWidget(0, 4)
        self.cred_table.setHorizontalHeaderLabels(["账号", "健康", "积分", "状态"])
        self.cred_table.verticalHeader().setVisible(False)
        self.cred_table.setSelectionMode(QTableWidget.NoSelection)
        self.cred_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.cred_table.setMinimumHeight(140)
        hh = self.cred_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        cred.add(self.cred_table)

        cred_row = QHBoxLayout()
        self.btn_add_cred = _btn("添加账号（扫码 / 导入）", "primary")
        self.btn_add_cred.setToolTip(
            "萝卜盒本身不写凭证，所有账号都在管理台里添加。\n"
            "点这里直接切到「管理台 → 凭证」页。"
        )
        self.btn_refresh_cred = _btn("刷新凭证池", "ghost")
        cred_row.addWidget(self.btn_add_cred)
        cred_row.addWidget(self.btn_refresh_cred)
        cred_row.addStretch(1)
        cred.add_layout(cred_row)
        box.addWidget(cred)

        self.overview_extra = Card("积分与签到", "")
        self.credits_label = QLabel("—")
        self.credits_label.setObjectName("dim")
        self.credits_label.setWordWrap(True)
        self.overview_extra.add(self.credits_label)
        box.addWidget(self.overview_extra)

        box.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _field_row(self, label: str, field_widget: QWidget) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        lbl = QLabel(label)
        lbl.setObjectName("dim")
        lbl.setFixedWidth(96)
        row.addWidget(lbl)
        row.addWidget(field_widget, 1)
        return w

    # ---------------------------------------------------------------- 客户端

    def _tab_clients(self) -> QWidget:
        from .widgets import Card, StatusDot, Toast

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(12)

        self.client_toast = Toast()
        box.addWidget(self.client_toast)

        # Codex
        codex = Card("Codex（桌面版 / CLI）",
                     "写入 config.toml。用静态 http_headers 而不是环境变量，"
                     "避免 Windows 不刷新已运行进程的环境导致 401")
        self.codex_dot = StatusDot(theme.TEXT_MUTE, 10)
        codex.add_header_widget(self.codex_dot)
        self.codex_status = QLabel("检测中…")
        self.codex_status.setObjectName("dim")
        self.codex_status.setWordWrap(True)
        codex.add(self.codex_status)

        row = QHBoxLayout()
        self.codex_model = QComboBox()
        self.codex_model.setEditable(True)
        self.codex_model.addItems([
            "deepseek-v4.1-flash", "glm-5.3", "deepseek-v4-pro", "kimi-k2.7",
            "minimax-m3", "hy3", "auto",
        ])
        self.codex_model.setCurrentText(str(self.ctx.config.get("clients.codex.model")))
        row.addWidget(QLabel("默认模型"))
        row.addWidget(self.codex_model, 1)
        codex.add_layout(row)

        btns = QHBoxLayout()
        self.btn_codex_apply = _btn("接入 Codex", "primary")
        self.btn_codex_restore = _btn("还原备份", "ghost")
        btns.addWidget(self.btn_codex_apply)
        btns.addWidget(self.btn_codex_restore)
        btns.addStretch(1)
        codex.add_layout(btns)

        codex.add(QLabel("等价的配置片段："))
        self.codex_snippet = QPlainTextEdit()
        self.codex_snippet.setReadOnly(True)
        self.codex_snippet.setMaximumHeight(150)
        codex.add(self.codex_snippet)
        box.addWidget(codex)

        # Claude
        claude = Card("Claude Code / Anthropic 兼容客户端",
                      "写入 ~/.claude/settings.json 的 env 段；Anthropic SDK 会自己追加 /v1/messages")
        self.claude_dot = StatusDot(theme.TEXT_MUTE, 10)
        claude.add_header_widget(self.claude_dot)
        self.claude_status = QLabel("检测中…")
        self.claude_status.setObjectName("dim")
        self.claude_status.setWordWrap(True)
        claude.add(self.claude_status)

        btns2 = QHBoxLayout()
        self.btn_claude_apply = _btn("接入 Claude Code", "primary")
        self.btn_claude_restore = _btn("还原备份", "ghost")
        btns2.addWidget(self.btn_claude_apply)
        btns2.addWidget(self.btn_claude_restore)
        btns2.addStretch(1)
        claude.add_layout(btns2)

        claude.add(QLabel("等价的环境变量："))
        self.claude_snippet = QPlainTextEdit()
        self.claude_snippet.setReadOnly(True)
        self.claude_snippet.setMaximumHeight(110)
        claude.add(self.claude_snippet)
        box.addWidget(claude)

        hint = QLabel("提示：改完 Codex 配置后需要**完全退出并重新打开** Codex 桌面版才会生效；"
                      "不要用桌面 App 里的模型选择器换模型（那是 OpenAI 的型号列表）。")
        hint.setObjectName("mute")
        hint.setWordWrap(True)
        box.addWidget(hint)

        box.addStretch(1)
        scroll.setWidget(page)
        return scroll

    # ---------------------------------------------------------------- 日志

    def _tab_logs(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)

        bar = QHBoxLayout()
        self.log_autoscroll = QCheckBox("自动滚动")
        self.log_autoscroll.setChecked(True)
        bar.addWidget(self.log_autoscroll)

        bar.addWidget(QLabel("过滤"))
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("包含关键字，留空显示全部…")
        bar.addWidget(self.log_filter, 1)

        self.btn_log_refresh = _btn("刷新", "ghost", 28)
        self.btn_log_open = _btn("打开文件", "ghost", 28)
        self.btn_log_clear = _btn("清空", "ghost", 28)
        for b in (self.btn_log_refresh, self.btn_log_open, self.btn_log_clear):
            bar.addWidget(b)
        box.addLayout(bar)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(6000)
        self.log_view.setStyleSheet(
            f"background-color: {theme.BG_ALT}; border: 1px solid {theme.BORDER};"
            f"border-radius: 8px; font-family: {theme.MONO_FAMILY}; font-size: 12px;"
        )
        box.addWidget(self.log_view, 1)

        self.log_meta = QLabel("")
        self.log_meta.setObjectName("mute")
        box.addWidget(self.log_meta)
        return page

    # ---------------------------------------------------------------- 更新

    def _tab_update(self) -> QWidget:
        from .widgets import Card, KeyValue, Toast

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(12)

        self.update_toast = Toast()
        box.addWidget(self.update_toast)

        # ---- 萝卜盒自身（应用）更新 --------------------------------------
        app_card = Card(
            "萝卜盒更新",
            f"让萝卜盒自己升级到最新版。来源：本项目的 GitHub Release。"
            f"升级方式按安装形态自动选择（安装版走静默安装包，便携版走原地覆盖），"
            f"完成后自动重启；配置 / 日志 / 备份都在 {appupdater.updates_dir().parent}，不受影响")
        self.kv_app_local = KeyValue("当前版本", __version__, mono=True)
        self.kv_app_remote = KeyValue("最新版本", "未检查", mono=True)
        app_card.add(self.kv_app_local)
        app_card.add(self.kv_app_remote)

        row_app = QHBoxLayout()
        self.btn_check_app = _btn("检查应用更新", "primary")
        self.btn_do_app_update = _btn("立即更新萝卜盒")
        self.btn_do_app_update.setEnabled(False)
        row_app.addWidget(self.btn_check_app)
        row_app.addWidget(self.btn_do_app_update)
        row_app.addStretch(1)
        app_card.add_layout(row_app)

        self.app_update_notes = QPlainTextEdit()
        self.app_update_notes.setReadOnly(True)
        self.app_update_notes.setMaximumHeight(160)
        self.app_update_notes.setPlaceholderText("检查后在此显示新版本说明…")
        app_card.add(self.app_update_notes)
        box.addWidget(app_card)

        # ---- 网关（codebuddy2api）更新 ------------------------------------
        card = Card("网关更新", "更新来源：上游 GitHub release。升级会自动保留 auth/、.env "
                               "和既有启动脚本，并自动重打脱敏补丁")
        self.kv_local_ver = KeyValue("当前版本", updater.local_version(
            self.ctx.config.get("gateway.dir")) or "未知", mono=True)
        self.kv_remote_ver = KeyValue("上游版本", "未检查", mono=True)
        card.add(self.kv_local_ver)
        card.add(self.kv_remote_ver)

        row = QHBoxLayout()
        self.btn_check_update = _btn("检查更新", "primary")
        self.btn_do_update = _btn("立即更新")
        self.btn_do_update.setEnabled(False)
        row.addWidget(self.btn_check_update)
        row.addWidget(self.btn_do_update)
        row.addStretch(1)
        card.add_layout(row)

        self.update_notes = QPlainTextEdit()
        self.update_notes.setReadOnly(True)
        self.update_notes.setMaximumHeight(180)
        self.update_notes.setPlaceholderText("检查后在此显示更新说明…")
        card.add(self.update_notes)
        box.addWidget(card)

        box.addStretch(1)
        scroll.setWidget(page)
        return scroll

    # ---------------------------------------------------------------- 设置

    def _tab_settings(self) -> QWidget:
        from .widgets import Card

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(12)

        # ---- 网关
        gw = Card("网关")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        dir_row = QHBoxLayout()
        self.in_dir = QLineEdit(str(self.ctx.config.get("gateway.dir")))
        btn_dir = _btn("浏览", "ghost", 28)
        btn_dir.clicked.connect(self._pick_dir)
        dir_row.addWidget(self.in_dir, 1)
        dir_row.addWidget(btn_dir)
        dw = QWidget()
        dw.setLayout(dir_row)
        form.addRow("源码目录", dw)

        py_row = QHBoxLayout()
        self.in_python = QLineEdit(str(self.ctx.config.get("gateway.python")))
        btn_py = _btn("探测", "ghost", 28)
        btn_py.clicked.connect(self._detect_python)
        py_row.addWidget(self.in_python, 1)
        py_row.addWidget(btn_py)
        pw = QWidget()
        pw.setLayout(py_row)
        form.addRow("解释器", pw)

        self.in_port = QSpinBox()
        self.in_port.setRange(1, 65535)
        self.in_port.setValue(int(self.ctx.config.get("gateway.port", 8788)))
        form.addRow("端口", self.in_port)

        key_row = QHBoxLayout()
        self.in_key = QLineEdit(str(self.ctx.config.get("gateway.api_key")))
        btn_key = _btn("重新生成", "ghost", 28)
        btn_key.clicked.connect(self._regen_key)
        key_row.addWidget(self.in_key, 1)
        key_row.addWidget(btn_key)
        kw = QWidget()
        kw.setLayout(key_row)
        form.addRow("API Key", kw)

        gw.add_layout(form)
        gw.add(QLabel("启动参数（Codex 必需）——缺 --desensitize 必被上游 11128 拦截："))
        self.in_args = QPlainTextEdit()
        self.in_args.setPlainText("\n".join(self.ctx.config.get("gateway.extra_args", [])))
        self.in_args.setMaximumHeight(96)
        gw.add(self.in_args)
        box.addWidget(gw)

        # ---- 网络
        net = Card("网络与公网入口",
                   "公网走 Tailscale Funnel，不经过 Windows 防火墙；"
                   "Funnel 只允许 443 / 8443 / 10000 三个端口")
        self.chk_funnel = QCheckBox("开启公网入口（Funnel）")
        self.chk_funnel.setChecked(bool(self.ctx.config.get("funnel.enabled")))
        net.add(self.chk_funnel)

        f2 = QFormLayout()
        f2.setLabelAlignment(Qt.AlignRight)
        self.in_funnel_port = QComboBox()
        self.in_funnel_port.addItems(["443", "8443", "10000"])
        self.in_funnel_port.setCurrentText(str(self.ctx.config.get("funnel.port", "8443")))
        f2.addRow("Funnel 端口", self.in_funnel_port)

        self.in_ts = QLineEdit(str(self.ctx.config.get("funnel.tailscale_exe")))
        f2.addRow("tailscale.exe", self.in_ts)
        net.add_layout(f2)

        warn = QLabel("⚠ 关闭时只会关掉本端口，绝不会执行 funnel reset —— "
                      "那会把同机 443 上其他服务的公网入口一起清掉。")
        warn.setObjectName("mute")
        warn.setWordWrap(True)
        net.add(warn)
        box.addWidget(net)

        # ---- 启动与退出
        life = Card("启动与退出")
        self.chk_autostart = QCheckBox("开机自启（登录时启动萝卜盒）")
        self.chk_autostart.setChecked(autostart.is_autostart_on())
        self.chk_gw_autostart = QCheckBox("启动萝卜盒时自动拉起网关")
        self.chk_gw_autostart.setChecked(bool(self.ctx.config.get("gateway.auto_start")))
        self.chk_min_tray = QCheckBox("关闭窗口时最小化到托盘（不退出）")
        self.chk_min_tray.setChecked(bool(self.ctx.config.get("app.minimize_to_tray")))
        self.chk_stop_exit = QCheckBox("退出萝卜盒时同时停止网关")
        self.chk_stop_exit.setChecked(bool(self.ctx.config.get("gateway.stop_on_exit")))
        for c in (self.chk_autostart, self.chk_gw_autostart, self.chk_min_tray, self.chk_stop_exit):
            life.add(c)
        box.addWidget(life)

        # ---- 迁移与诊断
        diag = Card("迁移与诊断")
        from ..gateway import scheduled_task_exists

        if scheduled_task_exists():
            msg = QLabel("检测到旧部署的计划任务 codebuddy2api 仍存在，会和萝卜盒抢同一端口。")
            msg.setWordWrap(True)
            msg.setStyleSheet(f"color: {theme.WARN};")
            diag.add(msg)
            self.btn_del_task = _btn("移除旧计划任务", "danger")
            diag.add(self.btn_del_task)
        else:
            diag.add(QLabel("未检测到遗留的计划任务，环境干净。"))

        row2 = QHBoxLayout()
        self.btn_diag = _btn("环境体检", "ghost")
        self.btn_data = _btn("打开数据目录", "ghost")
        self.btn_logdir = _btn("打开日志目录", "ghost")
        row2.addWidget(self.btn_diag)
        row2.addWidget(self.btn_data)
        row2.addWidget(self.btn_logdir)
        row2.addStretch(1)
        diag.add_layout(row2)

        self.diag_out = QPlainTextEdit()
        self.diag_out.setReadOnly(True)
        self.diag_out.setMaximumHeight(140)
        diag.add(self.diag_out)
        box.addWidget(diag)

        # ---- 保存
        save_row = QHBoxLayout()
        self.btn_save = _btn("保存设置", "primary", 38)
        self.btn_reload = _btn("放弃改动", "ghost", 38)
        save_row.addStretch(1)
        save_row.addWidget(self.btn_reload)
        save_row.addWidget(self.btn_save)
        box.addLayout(save_row)

        box.addStretch(1)
        scroll.setWidget(page)
        return scroll

    # ================================================================ 信号

    def _connect(self) -> None:
        ctx = self.ctx
        ctx.state_changed.connect(self._refresh_state)
        ctx.health_ready.connect(self._on_health)
        ctx.toast.connect(self._on_toast)
        ctx.log_tick.connect(self._refresh_log)
        ctx.busy_changed.connect(self._on_busy)

        self.btn_start.clicked.connect(lambda: ctx.start_gateway())
        self.btn_stop.clicked.connect(lambda: ctx.stop_gateway())
        self.btn_restart.clicked.connect(ctx.restart_gateway)
        self.btn_dashboard.clicked.connect(self._open_dashboard)

        self.btn_repatch.clicked.connect(ctx.repatch)
        self.btn_checkin.clicked.connect(self._checkin)

        self.btn_add_cred.clicked.connect(self._open_credentials_page)
        self.btn_refresh_cred.clicked.connect(ctx.refresh_health)

        self.btn_codex_apply.clicked.connect(self._apply_codex)
        self.btn_codex_restore.clicked.connect(self._restore_codex)
        self.btn_claude_apply.clicked.connect(self._apply_claude)
        self.btn_claude_restore.clicked.connect(self._restore_claude)

        self.btn_log_refresh.clicked.connect(self._refresh_log)
        self.btn_log_open.clicked.connect(lambda: self._open_path(log_dir()))
        self.btn_log_clear.clicked.connect(self._clear_log)

        self.btn_check_app.clicked.connect(self._check_app_update)
        self.btn_do_app_update.clicked.connect(self._do_app_update)

        self.btn_check_update.clicked.connect(self._check_update)
        self.btn_do_update.clicked.connect(self._do_update)

        self.btn_save.clicked.connect(self._save_settings)
        self.btn_reload.clicked.connect(self._reload_settings)
        self.btn_diag.clicked.connect(self._run_diag)
        self.btn_data.clicked.connect(lambda: self._open_path(data_dir()))
        self.btn_logdir.clicked.connect(lambda: self._open_path(log_dir()))
        for name in ("btn_del_task",):
            b = getattr(self, name, None)
            if b is not None:
                b.clicked.connect(self._remove_old_task)

        self.chk_funnel.toggled.connect(self._on_funnel_toggled)
        self._refresh_clients()

    # ================================================================ 刷新

    def _refresh_state(self) -> None:
        ctx = self.ctx
        state = ctx.quick_state
        color = theme.status_color(state)
        self.dot.set_color(color)
        self.state_label.setText(ctx.gateway.state_label)
        self.state_label.setStyleSheet(f"color: {color};")

        base = ctx.config.base_url()
        lan = f"http://{self._lan_ip()}:{ctx.config.get('gateway.port')}"
        self.state_sub.setText(f"{base}")

        self.kv_port.set_value(str(ctx.config.get("gateway.port")))
        pid = ctx.gateway.pid
        self.kv_pid.set_value(str(pid) if pid else "—")
        self.kv_models.set_value(str(ctx.health.models) if ctx.health.ok else "—")
        self.kv_latency.set_value(
            f"{ctx.health.latency_ms} ms" if ctx.health.ok else "—"
        )

        self.f_local.set_value(base)
        self.f_lan.set_value(lan)
        self.f_ts.set_value(self._ts_url())
        pub = ctx.funnel_status.url or (
            ctx.funnel.public_url() if ctx.config.get("funnel.enabled") else "")
        self.f_pub.set_value(pub or "未开启")
        self.f_key.set_value(str(ctx.config.get("gateway.api_key", "")))

        running = state == "running"
        self.btn_start.setEnabled(not running and not ctx.runner.busy())
        self.btn_stop.setEnabled(state in ("running", "external", "starting"))
        self.btn_restart.setEnabled(running)
        self.btn_dashboard.setEnabled(state in ("running", "external"))
        reachable = state in ("running", "external")
        self.btn_add_cred.setEnabled(reachable)
        self.btn_refresh_cred.setEnabled(reachable)

        self._refresh_patch_badge()

    def _refresh_patch_badge(self) -> None:
        rep = self.ctx.patch_status()
        if not rep.target_ok:
            self.kv_patch.set_value("无法体检")
            self.kv_patch.set_color(theme.WARN)
        elif rep.missing:
            self.kv_patch.set_value("缺失：" + "、".join(rep.missing))
            self.kv_patch.set_color(theme.ERR)
        else:
            self.kv_patch.set_value(f"完好（{len(rep.present)} 个品牌词在位）")
            self.kv_patch.set_color(theme.OK)

    def _on_health(self, snap) -> None:
        self._refresh_state()
        self._fill_credentials(snap)
        self._fill_credits(snap)

    def _fill_credentials(self, snap) -> None:
        rows = snap.credentials or []
        self.cred_table.setRowCount(len(rows) if rows else 1)
        for r, item in enumerate(rows):
            # 逐行兜底：某一行字段形态异常时只标记这一行，
            # 不能让整个循环中断（早期就是被一个 dict 拖垮了整张表）。
            try:
                name = str(item.get("name") or item.get("id") or "?")
                health = str(item.get("health") or "unknown")
                label, color = HEALTH_LABEL.get(health, (health, theme.TEXT_DIM))

                self.cred_table.setItem(r, 0, QTableWidgetItem(name))
                h = QTableWidgetItem(label)
                h.setForeground(QColor(color))
                self.cred_table.setItem(r, 1, h)
                self.cred_table.setItem(
                    r, 2, QTableWidgetItem(_fmt_credits(item.get("credits"))))
                extra = ""
                if health == "circuit_open":
                    extra = f"冷却 {item.get('cooldown_remaining', 0)}s"
                elif item.get("last_error_code"):
                    extra = str(item["last_error_code"])
                elif item.get("enabled") is False:
                    extra = "已从调度中排除"
                self.cred_table.setItem(r, 3, QTableWidgetItem(extra))
            except Exception:  # noqa: BLE001
                try:
                    self.cred_table.setItem(r, 0, QTableWidgetItem(
                        str(item.get("name") or item.get("id") or "?")))
                    self.cred_table.setItem(r, 3, QTableWidgetItem("该行数据无法解析"))
                except Exception:  # noqa: BLE001
                    pass
        if not rows:
            self.cred_table.setItem(0, 0, QTableWidgetItem(
                "暂无凭证" if not snap.ok
                else "凭证池为空 —— 点下方「添加账号」到管理台扫码添加"))

    def _fill_credits(self, snap) -> None:
        credits = snap.credits or {}
        data = credits.get("credits") if isinstance(credits, dict) else None
        if isinstance(data, dict) and data:
            parts = []
            for key, val in list(data.items())[:6]:
                # 网关用完整路径当 key，这里只显示文件名，否则一行塞不下
                short = Path(str(key)).name or str(key)
                if isinstance(val, dict):
                    bal = val.get("credits")
                    if bal is None:
                        bal = val.get("balance")
                    parts.append(f"{short}：{_fmt_credits(bal)}")
                else:
                    parts.append(f"{short}：{_fmt_credits(val)}")
            self.credits_label.setText("　".join(parts))
        else:
            self.credits_label.setText(
                "暂无积分数据（网关未运行或尚未签到）。点上方「立即签到」可拉取一次。")

    def _on_busy(self, busy: bool) -> None:
        self.statusBar().showMessage("处理中…" if busy else "就绪")
        if busy:
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            QApplication.restoreOverrideCursor()
        self._refresh_state()

    def _on_toast(self, text: str, level: str) -> None:
        widget = self._tab_toast()
        widget.show_message(text, level)
        self.statusBar().showMessage(text.splitlines()[0][:120], 8000)

    def _tab_toast(self):
        """提示条跟着当前页签走 —— 用键查索引，别再写死数字。"""
        idx = self.tabs.currentIndex()
        for key, widget in (("clients", getattr(self, "client_toast", None)),
                            ("update", getattr(self, "update_toast", None)),
                            ("admin", getattr(getattr(self, "admin", None), "toast", None))):
            if widget is not None and idx == self._tab_index.get(key):
                return widget
        return self.overview_toast

    def _refresh_log(self) -> None:
        if not self.isVisible() or self.tabs.currentIndex() != self._tab_index.get("logs"):
            return
        text = self.ctx.gateway.tail_log(int(self.ctx.config.get("ui.log_tail_lines", 800)))
        needle = self.log_filter.text().strip()
        if needle:
            text = "\n".join(l for l in text.splitlines() if needle.lower() in l.lower())
        bar = self.log_view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        if text != self.log_view.toPlainText():
            self.log_view.setPlainText(text)
            if self.log_autoscroll.isChecked() and at_bottom:
                self.log_view.moveCursor(QTextCursor.End)
        path = log_dir() / "gateway.log"
        size = path.stat().st_size if path.is_file() else 0
        self.log_meta.setText(f"gateway.log　{size / 1024:.1f} KB　{self._log_stamp()}")

    @staticmethod
    def _log_stamp() -> str:
        return time.strftime("%H:%M:%S")

    # ================================================================ 动作

    def _open_dashboard(self) -> None:
        QDesktopServices.openUrl(QUrl(self.ctx.config.dashboard_url()))

    def _open_credentials_page(self) -> None:
        """账号只在管理台里添加 —— 直接切到「管理台 → 凭证」，省得用户找。"""
        index = self._tab_index.get("admin")
        if index is None:
            return
        self.tabs.setCurrentIndex(index)
        self.admin.show_credentials_tab()

    def _open_path(self, path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _lan_ip(self) -> str:
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:  # noqa: BLE001
            return "127.0.0.1"

    def _ts_url(self) -> str:
        host = self.ctx.config.get("funnel.hostname") or ""
        if host:
            return f"http://{host}:{self.ctx.config.get('gateway.port')}"
        return "http://100.64.0.0/10（Tailscale 内网）"

    def _checkin(self) -> None:
        from ..gateway import _http_json  # noqa: SLF001

        base = self.ctx.config.base_url()
        key = str(self.ctx.config.get("gateway.api_key", ""))

        def work():
            # 走 _http_json：它绕过系统代理 —— 裸 urllib 会吃 http_proxy，
            # 本机签到请求被绕到代理上是"无法连接管理 API"的来源之一
            code, body = _http_json(
                base + "/admin/checkin", key=key, timeout=60,
                data=b"{}", method="POST",
            )
            if isinstance(body, (dict, list)):
                text = json.dumps(body, ensure_ascii=False)
            else:
                text = str(body)
            return code, text[:400]

        def done(res):
            code, body = res
            self._on_toast(f"签到完成（HTTP {code}）{body[:160]}", "ok" if code == 200 else "warn")
            self.ctx.refresh_health()

        self.ctx.run_task(work, done, busy_text="正在签到并刷新积分…")

    # ---------------------------------------------------------- 客户端

    def _refresh_clients(self) -> None:
        st = self.ctx.codex.status()
        self.codex_dot.set_color(theme.OK if st["applied"] else theme.TEXT_MUTE)
        self.codex_status.setText(f"{st['reason']}\n{st['path']}")
        self.codex_snippet.setPlainText(snippet_codex(self.ctx.config, with_key=True))

        st2 = self.ctx.claude.status()
        self.claude_dot.set_color(theme.OK if st2["applied"] else theme.TEXT_MUTE)
        self.claude_status.setText(f"{st2['reason']}\n{st2['path']}")
        self.claude_snippet.setPlainText(snippet_claude(self.ctx.config))

    def _apply_codex(self) -> None:
        model = self.codex_model.currentText().strip()
        if not model:
            self._on_toast("请先填模型名", "warn")
            return
        self.ctx.config.set("clients.codex.model", model)
        self.ctx.config.save()
        self.ctx.run_task(
            self.ctx.codex.apply,
            lambda r: (self._on_toast(r[1], "ok" if r[0] else "error"), self._refresh_clients()),
            lambda m: self._on_toast(f"接入失败：{m.splitlines()[0]}", "error"),
        )

    def _restore_codex(self) -> None:
        self.ctx.run_task(
            self.ctx.codex.restore,
            lambda r: (self._on_toast(r[1], "ok" if r[0] else "warn"), self._refresh_clients()),
            lambda m: self._on_toast(f"还原失败：{m.splitlines()[0]}", "error"),
        )

    def _apply_claude(self) -> None:
        self.ctx.run_task(
            self.ctx.claude.apply,
            lambda r: (self._on_toast(r[1], "ok" if r[0] else "error"), self._refresh_clients()),
            lambda m: self._on_toast(f"接入失败：{m.splitlines()[0]}", "error"),
        )

    def _restore_claude(self) -> None:
        self.ctx.run_task(
            self.ctx.claude.restore,
            lambda r: (self._on_toast(r[1], "ok" if r[0] else "warn"), self._refresh_clients()),
            lambda m: self._on_toast(f"还原失败：{m.splitlines()[0]}", "error"),
        )

    # ---------------------------------------------------------- 日志

    def _clear_log(self) -> None:
        if QMessageBox.question(self, "清空日志", "确定清空 gateway.log？") == QMessageBox.Yes:
            self.ctx.gateway.clear_log()
            self.log_view.clear()
            self._on_toast("日志已清空", "ok")

    # ---------------------------------------------------------- 应用自更新

    def _check_app_update(self) -> None:
        """检查萝卜盒自己有没有新版本（与网关更新是两条独立的路）。"""
        repo = str(self.ctx.config.get("updater.app_repo", "lylguang/LuoboBox"))
        self._app_check_result = None

        def done(info):
            self._app_check_result = info
            if info.error:
                self._on_toast(info.error, "error")
                self.app_update_notes.setPlainText(info.error)
                self.btn_do_app_update.setEnabled(False)
                return
            self.kv_app_remote.set_value(info.tag or "未知")
            self.kv_app_local.set_value(info.local_version or __version__)
            if info.newer:
                self._on_toast(f"萝卜盒有新版本 {info.tag}（当前 {__version__}）", "warn")
                self.app_update_notes.setPlainText(
                    f"{info.name}\n发布于 {info.published}\n\n{info.notes}")
                self.btn_do_app_update.setEnabled(True)
            else:
                self._on_toast(f"萝卜盒已是最新（{__version__}）", "ok")
                self.app_update_notes.setPlainText("当前已是最新版本。")
                self.btn_do_app_update.setEnabled(False)

        self.ctx.run_task(
            lambda: appupdater.check_app(repo), done,
            lambda m: self._on_toast(f"检查应用更新失败：{m.splitlines()[0]}", "error"),
            busy_text="正在检查萝卜盒更新…",
        )

    def _do_app_update(self) -> None:
        info = getattr(self, "_app_check_result", None)
        if not info or info.error:
            self._on_toast("请先检查应用更新", "warn")
            return
        if not info.newer:
            self._on_toast("已是最新版本，无需更新", "ok")
            return

        mode = appupdater.install_mode()
        asset = appupdater.pick_asset(info, mode)
        if not asset:
            self._on_toast("该版本没有可用的更新包（缺 portable.zip / Setup.exe）", "error")
            return
        name, url = asset
        kind = "安装包（静默安装）" if mode == "installer" else "便携包（原地覆盖）"
        dest = appupdater.updates_dir()
        if QMessageBox.question(
            self, "更新萝卜盒",
            f"将下载 {info.tag} 的{kind}：\n    {name}\n\n"
            f"下载完成后萝卜盒会**自动关闭**，由后台助手完成替换并重新启动。\n\n"
            f"· 安装形态：{mode}\n"
            f"· 下载与日志目录：{dest}\n"
            f"· 配置 / 日志 / 备份不受影响\n\n现在更新？",
        ) != QMessageBox.Yes:
            return

        self.ctx.run_task(
            lambda: self._do_app_update_work(mode, name, url),
            self._after_app_update,
            lambda m: self._on_toast(f"应用更新失败：{m.splitlines()[0]}", "error"),
            busy_text="正在下载萝卜盒更新…",
        )

    def _do_app_update_work(self, mode: str, name: str, url: str) -> str:
        archive = appupdater.download(url, appupdater.updates_dir(), name)
        plan = appupdater.apply_and_restart(mode, archive)
        if not plan.ok:
            return plan.message
        return plan.message

    def _after_app_update(self, message: str) -> None:
        self._on_toast(f"{message}；萝卜盒即将退出并自动重启…", "ok")
        QTimer.singleShot(1200, self._quit_for_update)

    def _quit_for_update(self) -> None:
        """交棒给助手脚本：先落盘配置，再正常退出，让文件锁释放。"""
        try:
            self.ctx.config.set("updater.app_last_check",
                                time.strftime("%Y-%m-%d %H:%M:%S"))
            self.ctx.config.save()
        except Exception:  # noqa: BLE001
            pass
        self.request_quit()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ---------------------------------------------------------- 网关更新

    def _check_update(self) -> None:
        gw = self.ctx.config.get("gateway.dir")
        repo = str(self.ctx.config.get("updater.repo"))
        self._check_result = None

        def done(info):
            self._check_result = info
            if info.error:
                self._on_toast(info.error, "error")
                self.update_notes.setPlainText(info.error)
                self.btn_do_update.setEnabled(False)
                return
            self.kv_remote_ver.set_value(info.tag or "未知")
            self.kv_local_ver.set_value(info.local_version or "未知")
            if info.newer:
                self._on_toast(f"发现新版本 {info.tag}（当前 {info.local_version}）", "warn")
                self.update_notes.setPlainText(
                    f"{info.name}\n发布于 {info.published}\n\n{info.notes}")
                self.btn_do_update.setEnabled(True)
            else:
                self._on_toast(f"已是最新（{info.local_version or info.tag}）", "ok")
                self.update_notes.setPlainText("当前版本已是最新。")
                self.btn_do_update.setEnabled(False)

        self.ctx.run_task(
            lambda: updater.check(gw, repo), done,
            lambda m: self._on_toast(f"检查更新失败：{m.splitlines()[0]}", "error"),
            busy_text="正在检查更新…",
        )

    def _do_update(self) -> None:
        info = getattr(self, "_check_result", None)
        if not info or not info.url_ok():
            self._on_toast("请先检查更新", "warn")
            return
        if QMessageBox.question(
            self, "更新网关",
            f"将从上游下载 {info.tag} 覆盖网关代码。\n\n"
            "· 会自动备份整个网关目录\n"
            "· 保留 auth/、.env 与既有启动脚本\n"
            "· 升级后自动重打脱敏补丁\n\n继续？",
        ) != QMessageBox.Yes:
            return

        gw = Path(str(self.ctx.config.get("gateway.dir")))
        self.ctx.run_task(
            lambda: self._do_update_work(gw, info),
            lambda r: self._on_toast(r, "ok"),
            lambda m: self._on_toast(f"更新失败：{m.splitlines()[0]}", "error"),
            busy_text="正在下载并应用更新…",
        )

    def _do_update_work(self, gw: Path, info) -> str:
        from ..paths import backup_dir

        dl = backup_dir() / "_downloads"
        archive = updater.download(info.tarball or info.zipball, dl)
        res = updater.apply_release(gw, archive)
        updater.cleanup_downloads()
        if not res.ok:
            return res.message
        return f"{res.message}；脱敏补丁：{res.patched}。请重启网关使改动生效。"

    # ---------------------------------------------------------- 设置

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 codebuddy2api 源码目录",
                                                self.in_dir.text())
        if path:
            self.in_dir.setText(path)

    def _detect_python(self) -> None:
        self.diag_out.setPlainText("正在探测解释器…")

        def work():
            py, report = find_python(self.in_python.text().strip() or None)
            lines = []
            for cand, why in report[:12]:
                mark = "✓" if why == "可用" else "✗"
                lines.append(f"{mark} {cand}   {why}")
            return py, "\n".join(lines)

        def done(res):
            py, report = res
            self.diag_out.setPlainText(report)
            if py:
                self.in_python.setText(str(py))
                self._on_toast(f"选中解释器：{py}", "ok")
            else:
                self._on_toast("没有找到带 fastapi/uvicorn/httpx 的 Python", "error")

        self.ctx.run_task(work, done,
                          lambda m: self.diag_out.setPlainText(f"探测失败：{m}"))

    def _regen_key(self) -> None:
        if QMessageBox.question(
            self, "重新生成 API Key",
            "生成新 Key 后需要重新「保存设置」，并重新执行一次客户端接入。\n\n继续？",
        ) == QMessageBox.Yes:
            self.in_key.setText(gen_api_key())

    def _on_funnel_toggled(self, checked: bool) -> None:
        if checked and str(self.in_funnel_port.currentText()) == "443":
            self._on_toast("443 端口通常已被其它服务占用（例如 tuangou），"
                           "建议改用 8443", "warn")

    def _save_settings(self) -> None:
        cfg = self.ctx.config
        problems = []

        new_dir = Path(self.in_dir.text().strip())
        if not (new_dir / "converter.py").is_file():
            problems.append("源码目录里找不到 converter.py")
        new_py = Path(self.in_python.text().strip())
        if not new_py.exists():
            problems.append("解释器路径不存在")
        new_port = int(self.in_port.value())
        if new_port != int(cfg.get("gateway.port")) and not port_free(new_port):
            problems.append(f"端口 {new_port} 已被占用")
        if not self.in_key.text().strip():
            problems.append("API Key 不能为空")
        if problems:
            QMessageBox.warning(self, "设置有问题", "\n".join("· " + p for p in problems))
            return

        cfg.backup("config-before-save")
        args = [a.strip() for a in self.in_args.toPlainText().splitlines() if a.strip()]
        missing = patcher.check_args(args)

        cfg.set("gateway.dir", str(new_dir))
        cfg.set("gateway.python", str(new_py))
        cfg.set("gateway.port", new_port)
        cfg.set("gateway.api_key", self.in_key.text().strip())
        cfg.set("gateway.extra_args", args)
        cfg.set("funnel.port", str(self.in_funnel_port.currentText()))
        cfg.set("funnel.tailscale_exe", self.in_ts.text().strip())
        cfg.set("gateway.auto_start", self.chk_gw_autostart.isChecked())
        cfg.set("app.minimize_to_tray", self.chk_min_tray.isChecked())
        cfg.set("gateway.stop_on_exit", self.chk_stop_exit.isChecked())
        cfg.save()

        ok, msg = autostart.set_autostart(self.chk_autostart.isChecked())
        if not ok:
            self._on_toast(msg, "error")

        if missing:
            self._on_toast("参数不完整——" + "；".join(missing), "warn")
        else:
            self._on_toast("设置已保存", "ok")
        self._refresh_clients()
        self._refresh_state()

    def _reload_settings(self) -> None:
        from ..config import Config

        self.ctx.config = Config.load()
        self._on_toast("已从磁盘重新载入配置（部分改动需重启萝卜盒生效）", "info")

    def _run_diag(self) -> None:
        self.diag_out.setPlainText("体检中…")

        def work():
            cfg = self.ctx.config
            lines = []
            lines.append(f"萝卜盒版本      {__version__}")
            lines.append(f"数据目录        {data_dir()}")
            lines.append(f"配置文件        {cfg.path}")
            lines.append(f"管理员权限      {'是' if is_admin() else '否（Funnel / 计划任务可能需要）'}")
            lines.append("")

            gw = Path(str(cfg.get("gateway.dir", "")))
            lines.append(f"[网关] 目录      {gw}  {'✓' if (gw / 'converter.py').is_file() else '✗ 找不到 converter.py'}")
            ver = updater.local_version(gw)
            lines.append(f"[网关] 版本      {ver or '未知'}")
            py = Path(str(cfg.get("gateway.python", "")))
            ok, why = (False, "不存在") if not py.exists() else _probe_python(py)
            lines.append(f"[网关] 解释器    {py}  {'✓ 可用' if ok else '✗ ' + why}")
            lines.append(f"[网关] 端口      {cfg.get('gateway.port')}  "
                         f"{'空闲' if port_free(int(cfg.get('gateway.port'))) else '已占用'}")
            from ..gateway import firewall_rule

            fexists, fdetail = firewall_rule(int(cfg.get("gateway.port")))
            lines.append(f"[网关] 防火墙    {'✓ ' if fexists else '⚠ '}{fdetail}")
            if not fexists:
                lines.append("                 网关绑 0.0.0.0，没有这条规则时端口对所有网段开放，"
                             "仅剩 API Key 一道防线")
            lines.append("")

            rep = patcher.inspect(gw)
            lines.append(f"[补丁] 状态      {rep.summary()}")
            args = cfg.get("gateway.extra_args", [])
            miss = patcher.check_args(args)
            lines.append(f"[补丁] 参数      {'✓ 齐全' if not miss else '✗ ' + '；'.join(miss)}")
            lines.append("")

            fst = self.ctx.funnel.status()
            lines.append(f"[公网] tailscale {'✓ 存在' if fst.available else '✗ 未找到'}")
            lines.append(f"[公网] 后端      {'运行中' if fst.running else '未运行'}")
            lines.append(f"[公网] Funnel    {fst.detail}")
            lines.append("")

            from ..clientconfig import ClaudeConfigurator, CodexConfigurator

            cx = CodexConfigurator(cfg).status()
            lines.append(f"[客户端] Codex   {cx['reason']}")
            cl = ClaudeConfigurator(cfg).status()
            lines.append(f"[客户端] Claude  {cl['reason']}")
            return "\n".join(lines)

        self.ctx.run_task(work, lambda t: self.diag_out.setPlainText(t),
                          lambda m: self.diag_out.setPlainText(f"体检失败：{m}"))

    def _remove_old_task(self) -> None:
        if QMessageBox.question(
            self, "移除旧计划任务",
            "将删除计划任务 codebuddy2api，之后网关只由萝卜盒管理。\n"
            "（不会删除任何文件）\n\n继续？",
        ) != QMessageBox.Yes:
            return
        ok, msg = remove_scheduled_task()
        self._on_toast(("已移除旧计划任务" if ok else "移除失败：") + f" {msg[:120]}",
                       "ok" if ok else "error")
        b = getattr(self, "btn_del_task", None)
        if b is not None and ok:
            b.setEnabled(False)

    # ---------------------------------------------------------- 启动后自检

    def _post_show_checks(self) -> None:
        warn = warn_scheduled_task(self.ctx.config)
        if warn:
            self._on_toast(warn, "warn", )
        from ..paths import is_gateway_dir

        if not is_gateway_dir(self.ctx.config.get("gateway.dir")):
            self._on_toast("网关目录无效，请到「设置」里指定 codebuddy2api 源码目录", "error")

    # ================================================================ 关闭

    def force_quit_on_close(self) -> None:
        """无托盘可用时：关窗即退出，否则程序会变成没有入口的幽灵进程。"""
        self._force_quit = True

    def closeEvent(self, event) -> None:  # noqa: N802
        tray_exists = getattr(QApplication.instance(), "tray", None) is not None
        if (self.ctx.config.get("app.minimize_to_tray") and tray_exists
                and not self._pending_quit and not getattr(self, "_force_quit", False)):
            event.ignore()
            self.hide()
            from PySide6.QtWidgets import QSystemTrayIcon

            QApplication.instance().tray.showMessage(
                "萝卜盒仍在后台运行", "网关继续提供服务，双击托盘图标可以回来。",
                QSystemTrayIcon.Information, 2500,
            )
            return
        self._closing = True
        try:
            self.admin.stop()   # 停掉扫码轮询定时器，否则退出后还在后台打网关
        except Exception:  # noqa: BLE001
            pass
        self.ctx.shutdown()
        event.accept()

    def request_quit(self) -> None:
        self._pending_quit = True
        self.close()


def _probe_python(py: Path) -> tuple[bool, str]:
    from ..paths import _check  # noqa: PLC2701

    return _check(py)
