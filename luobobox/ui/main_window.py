"""主窗口：概览 / 客户端 / 日志 / 更新 / 设置。

原则：所有耗时操作都走 AppContext.run_task 到后台线程，UI 线程只做渲染。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QShortcut,
    QTextCursor,
)
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import motion, theme
from .. import __version__, appupdater, autostart, net, patcher, updater
from ..clientconfig import snippet_claude, snippet_codex
from ..config import gen_api_key, port_free
from ..context import AppContext, warn_scheduled_task
from ..gateway import is_admin, remove_scheduled_task
from ..paths import (
    app_root,
    backup_dir,
    data_dir,
    dir_size,
    find_python,
    human_size,
    icon_path,
    is_on_system_drive,
    log_dir,
    migrate_data_dir,
    pointer_legacy_path,
    pointer_primary_path,
)

# 顶部导航分组：日常最高频的四个留在外面，其余收进「⋯ 更多」。
# 顺序即 Ctrl+1..4 的顺序。
TAB_PRIMARY: tuple[str, ...] = ("overview", "admin", "clients", "logs")

# 状态词的短版本（塞进 96px 的环里，必须够短）。
STATE_SHORT = {
    "running": "运行中",
    "starting": "启动中",
    "stopping": "停止中",
    "external": "外部",
    "error": "异常",
    "stopped": "已停止",
}


def health_label(health: str) -> tuple[str, str]:
    """健康度 → (中文标签, 颜色)。

    ★ 这里**必须**是函数，不能写成模块级 dict。
    dict 会在 import 那一刻把 `theme.OK` 的**值**拷贝进去，等于把颜色冻住；
    换到浅色主题后表格里的"异常"还是深色主题那种浅粉红（#F09595），
    铺在白底上几乎看不见。函数每次调用都重新取当前主题的颜色。
    """
    return {
        "ready": ("正常", theme.OK),
        "expired": ("已过期", theme.ERR),
        "circuit_open": ("已熔断", theme.WARN),
        "error": ("异常", theme.ERR),
        "disabled": ("已停用", theme.TEXT_MUTE),
    }.get(health, (health, theme.TEXT_DIM))


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

    # 一键配置环境的进度日志：配置跑在工作线程里，绝不能在那里直接碰控件。
    # 走信号 → 自动排队回主线程（Qt 的跨线程信号是队列连接）。
    env_logged = Signal(str)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._closing = False
        self._pending_quit = False
        self._force_quit = False

        self.setWindowTitle(f"萝卜盒 LuoboBox {__version__}")
        if icon_path().is_file():
            self.setWindowIcon(QIcon(str(icon_path())))
        self._shortcuts: list[QShortcut] = []
        self._last_snap = None
        self.setMinimumSize(780, 560)
        self._restore_geometry()

        # 新版本角标：两条更新链路各自登记，合成一条给托盘 / 标题用
        self._badge_parts: dict[str, str] = {"app": "", "gateway": ""}
        self.update_badge = ""
        self._update_listeners: list = []

        self._build()
        self._connect()
        self._bind_shortcuts()

        # 一键配置环境的日志：工作线程 emit → 排在主线程执行（见 env_logged）
        self.env_logged.connect(self._append_env_log)

        self.ctx.refresh_health()
        QTimer.singleShot(900, self._post_show_checks)

    # ================================================================ 新版本角标

    def on_update_found(self, fn) -> None:
        """注册「发现新版本」回调（托盘用）。回调签名 fn(text: str)。"""
        self._update_listeners.append(fn)

    def set_update_badge(self, which: str, text: str) -> None:
        """登记某条更新链路的新版本角标。

        `which` 取 "app"（萝卜盒自己）或 "gateway"（网关）。
        两条链路是**独立**的，谁先检查完谁先登记 —— 所以按 key 分开存再合成，
        否则后检查完的那条一"没有新版本"就会把另一条的角标清掉。
        """
        self._badge_parts[which] = (text or "").strip()
        parts = [p for p in self._badge_parts.values() if p]
        self.update_badge = " / ".join(parts)
        for fn in list(self._update_listeners):
            try:
                fn(self.update_badge)
            except Exception:
                pass  # 角标只是提示，绝不能因为它把更新流程带崩

    # ================================================================ 窗口几何

    def _restore_geometry(self) -> None:
        """恢复上次的窗口大小 / 位置；还没有记录时按屏幕大小给一个首屏尺寸。

        为什么要记：把日志页拉大、把窗口挪到副屏，是用户对这套工具
        「配置」的一部分。每次都重置回 940×720，等于永远不承认这个配置。
        """
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None

        default_w, default_h = 940, 720
        if avail is not None:
            # 1080p 以上还开 940 宽，四张卡片会被挤到折行；
            # 但也不能全屏铺满 —— 工具窗口铺满反而让人找不到重点。
            default_w = min(max(940, int(avail.width() * 0.56)), 1360)
            default_h = min(max(720, int(avail.height() * 0.74)), 980)

        geo = str(self.ctx.config.get("ui.window_geometry", "") or "").strip()
        m = re.match(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$", geo)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            self.resize(min(max(w, 780), 4000), min(max(h, 560), 4000))
            if m.group(3) is not None:
                self.move(int(m.group(3)), int(m.group(4)))
        else:
            self.resize(default_w, default_h)
            if avail is not None:
                self.move(avail.center().x() - default_w // 2,
                          max(avail.top(), avail.center().y() - default_h // 2))
        if self.ctx.config.get("ui.window_maximized"):
            self.setWindowState(Qt.WindowMaximized)

    def _save_geometry(self) -> None:
        """落盘窗口几何。最大化时只记标志位 —— 记尺寸会把还原尺寸覆盖掉。"""
        try:
            cfg = self.ctx.config
            cfg.set("ui.window_maximized", bool(self.isMaximized()))
            if not self.isMaximized() and not self.isFullScreen():
                g = self.geometry()
                cfg.set("ui.window_geometry",
                        f"{g.width()}x{g.height()}{g.x():+d}{g.y():+d}")
            cfg.save()
        except Exception:  # noqa: BLE001
            pass

    # ================================================================ 构建

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 8)
        root.setSpacing(10)

        root.addWidget(self._header())

        # 页签索引集中登记。以前 _on_toast / _refresh_log 里把 1/2/3 写死，
        # 一插入新页签就会静默错位（提示条跑到别页、日志不再自动刷新）。
        # ★ 不变量：凡是"跳到某一页"的动作（托盘、命令面板、快捷键、卡片按钮）
        #   一律走 goto_tab(key)，严禁再出现 setCurrentIndex(<字面量>)。
        self.tabs = QTabWidget()
        self._tab_index: dict[str, int] = {}
        self._tab_labels: dict[str, str] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._overflow_keys: list[str] = []

        root.addWidget(self._nav_bar())
        root.addWidget(self.tabs, 1)

        for key, label, widget in (
            ("overview", "概览", self._tab_overview()),
            ("admin", "管理台", self._tab_admin()),
            ("clients", "客户端接入", self._tab_clients()),
            ("logs", "日志", self._tab_logs()),
            ("update", "更新", self._tab_update()),
            ("settings", "设置", self._tab_settings()),
        ):
            self.add_tab(key, widget, label)

        # 原生 tab bar 退场，交给自绘的分组导航（7 个平铺页签没人扫得完）
        self.tabs.tabBar().setVisible(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._rebuild_nav()

        self.statusBar().showMessage("就绪")

    # ---------------------------------------------------------------- 页签注册

    def add_tab(self, key: str, widget: QWidget, label: str) -> int:
        """注册一个页签。

        **所有**页签都必须经这里登记（含 app.py 在 _build 之后追加的额度页）——
        没登记的页签 goto_tab 找不到、快捷键也数不到，就会退化成"必须用鼠标点"。
        """
        idx = self.tabs.addTab(widget, label)
        self._tab_index[key] = idx
        self._tab_labels[key] = label
        if getattr(self, "_nav_buttons", None) is not None:
            self._rebuild_nav()
        # 页签变了 → Ctrl+1..N 的落点也要跟着重排（否则新页永远没有快捷键）
        if getattr(self, "_tab_shortcuts", None) is not None:
            self._bind_tab_shortcuts()
        return idx

    def tab_label(self, key: str) -> str:
        return self._tab_labels.get(key, key)

    def tab_key(self, index: int | None = None) -> str:
        """当前（或指定）索引对应的 key；未知索引返回空串。"""
        idx = self.tabs.currentIndex() if index is None else index
        for key, i in self._tab_index.items():
            if i == idx:
                return key
        return ""

    def tab_keys(self) -> list[str]:
        """按页签**实际顺序**返回 key —— Ctrl+1..N 就按这个顺序排。"""
        return [k for k, _ in sorted(self._tab_index.items(), key=lambda kv: kv[1])]

    def goto_tab(self, key: str) -> bool:
        """按 key 切页签（找不到返回 False）。

        这是唯一正确的跨页跳转方式：索引会随页签增删漂移，key 不会。
        托盘"检查更新"曾经写死 setCurrentIndex(3)，tab 顺序一变就跳到了日志页。
        """
        idx = self._tab_index.get(str(key))
        if idx is None:
            return False
        self.tabs.setCurrentIndex(idx)
        return True

    # ---------------------------------------------------------------- 导航

    def _nav_bar(self) -> QWidget:
        """顶部导航：主入口按钮 + 「⋯ 更多」+ 命令面板入口。"""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._nav_host = QWidget()
        self._nav_host_row = QHBoxLayout(self._nav_host)
        self._nav_host_row.setContentsMargins(0, 0, 0, 0)
        self._nav_host_row.setSpacing(6)
        row.addWidget(self._nav_host)
        row.addStretch(1)

        self.btn_more = QPushButton("⋯ 更多")
        self.btn_more.setObjectName("navMore")
        self.btn_more.setCursor(Qt.PointingHandCursor)
        self.btn_more.setToolTip("更新 / 额度消耗 / 设置")
        row.addWidget(self.btn_more)

        self.btn_palette = QPushButton("Ctrl+K 搜索")
        self.btn_palette.setObjectName("navMore")
        self.btn_palette.setCursor(Qt.PointingHandCursor)
        self.btn_palette.setToolTip("命令面板：搜功能、跳页、换肤，一个框搞定")
        self.btn_palette.clicked.connect(self._open_palette)
        row.addWidget(self.btn_palette)
        return bar

    def _rebuild_nav(self) -> None:
        """按 _tab_index 重建导航条。页签增删后自动跟上，不用手工维护。"""
        row = self._nav_host_row
        while row.count():
            item = row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._nav_buttons = {}

        keys = self.tab_keys()
        primary = [k for k in TAB_PRIMARY if k in self._tab_index]
        overflow = [k for k in keys if k not in primary]

        for key in primary:
            btn = QPushButton(self.tab_label(key))
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.goto_tab(k))
            row.addWidget(btn)
            self._nav_buttons[key] = btn

        self._overflow_keys = overflow
        menu = QMenu(self)
        for key in overflow:
            act = menu.addAction(self.tab_label(key))
            act.triggered.connect(lambda _=False, k=key: self.goto_tab(k))
        self._nav_menu = menu          # 持引用，否则菜单会被 GC 掉
        self.btn_more.setMenu(menu)
        self.btn_more.setVisible(bool(overflow))
        self._nav_sync()

    def _nav_sync(self) -> None:
        """同步导航高亮。收在「更多」里的页，直接把名字显示在按钮上。"""
        key = self.tab_key()
        for k, btn in self._nav_buttons.items():
            btn.setChecked(k == key)
        more = getattr(self, "btn_more", None)
        if more is not None:
            more.setText(f"⋯ {self.tab_label(key)}" if key in self._overflow_keys
                         else "⋯ 更多")

    def _on_tab_changed(self, index: int) -> None:
        """切页时：同步导航高亮；切到「管理台」才拉数据 —— 不打扰其它页、不空转网络。"""
        self._nav_sync()
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
        from .widgets import (
            Card,
            EmptyState,
            KeyValue,
            Pill,
            Separator,
            StatusRing,
            Toast,
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(12)

        self.overview_toast = Toast()
        box.addWidget(self.overview_toast)

        # ---- 英雄区 ------------------------------------------------------
        # 把「状态 / 地址 / 凭证数 / 模型数」压成一眼可见的一屏：
        # 这四个数就是用户打开窗口 90% 想知道的东西，不该让他去表里翻。
        hero = Card(object_name="hero")
        hrow = QHBoxLayout()
        hrow.setSpacing(18)

        self.hero_ring = StatusRing(96)
        hrow.addWidget(self.hero_ring, 0, Qt.AlignVCenter)

        hcol = QVBoxLayout()
        hcol.setSpacing(6)
        kicker = QLabel("网关状态")
        kicker.setObjectName("heroKicker")
        hcol.addWidget(kicker)

        self.hero_title = QLabel("正在检测…")
        self.hero_title.setObjectName("heroTitle")
        hcol.addWidget(self.hero_title)

        self.hero_sub = QLabel("")
        self.hero_sub.setObjectName("mute")
        self.hero_sub.setWordWrap(True)
        hcol.addWidget(self.hero_sub)

        pills = QHBoxLayout()
        pills.setSpacing(6)
        self.hero_pill_addr = Pill("地址 —", theme.TEXT_DIM)
        self.hero_pill_port = Pill("端口 —", theme.TEXT_DIM)
        self.hero_pill_cred = Pill("凭证 —", theme.TEXT_DIM)
        self.hero_pill_model = Pill("模型 —", theme.TEXT_DIM)
        for pill in (self.hero_pill_addr, self.hero_pill_port,
                     self.hero_pill_cred, self.hero_pill_model):
            pills.addWidget(pill)
        pills.addStretch(1)
        hcol.addLayout(pills)

        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.hero_btn_start = _btn("启动网关", "primary")
        self.hero_btn_start.setToolTip("启动 / 停止由萝卜盒托管的网关进程")
        self.hero_btn_package = _btn("复制接入包", "ghost")
        self.hero_btn_package.setToolTip(
            "把本机 / 局域网 / Tailscale / 公网地址、API Key，以及 Codex、\n"
            "Claude Code 两段配置片段汇成一段 Markdown 复制走（Ctrl+Shift+C）。\n"
            "省得四个地址一个个复制还漏掉 Key。")
        acts.addWidget(self.hero_btn_start)
        acts.addWidget(self.hero_btn_package)
        acts.addStretch(1)
        hcol.addLayout(acts)

        hrow.addLayout(hcol, 1)
        hero.add_layout(hrow)
        box.addWidget(hero)

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
        hh = self.cred_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        # 空池时不再塞一行灰字占位 —— 那看起来像"加载坏了"。
        # 换成带按钮的空态：直接给出下一步该点哪。
        self.cred_empty = EmptyState(
            "凭证池是空的",
            "网关不会自己产生凭证。到「管理台 → 凭证」扫码或导入 .info 文件，"
            "加完会自动出现在这里。",
            glyph="🗝",
            action=("去添加账号", self._open_credentials_page),
        )
        self.cred_stack = QStackedWidget()
        self.cred_stack.addWidget(self.cred_table)     # 0 = 有数据
        self.cred_stack.addWidget(self.cred_empty)     # 1 = 空态
        self.cred_stack.setMinimumHeight(150)
        cred.add(self.cred_stack)

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

        hint = QLabel("提示：改完 Codex 配置后需要「完全退出并重新打开」Codex 桌面版才会生效；"
                      "不要用桌面 App 里的模型选择器换模型（那是 OpenAI 的型号列表）。")
        hint.setObjectName("mute")
        hint.setWordWrap(True)
        box.addWidget(hint)

        box.addStretch(1)
        scroll.setWidget(page)
        return scroll

    # ---------------------------------------------------------------- 日志

    def _tab_logs(self) -> QWidget:
        from .widgets import LogHighlighter

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)

        bar = QHBoxLayout()
        self.log_autoscroll = QCheckBox("自动滚动")
        self.log_autoscroll.setChecked(True)
        bar.addWidget(self.log_autoscroll)

        self.chk_log_regex = QCheckBox("正则")
        self.chk_log_regex.setToolTip(
            "勾上后过滤串按正则解释，例如 ERROR|WARN。\n写错了会自动退回普通的包含匹配。")
        bar.addWidget(self.chk_log_regex)

        bar.addWidget(QLabel("过滤"))
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText(
            "包含关键字（或勾「正则」写 ERROR|WARN），留空显示全部…")
        self.log_filter.setClearButtonEnabled(True)
        bar.addWidget(self.log_filter, 1)

        self.btn_log_refresh = _btn("刷新", "ghost", 28)
        self.btn_log_export = _btn("导出", "ghost", 28)
        self.btn_log_export.setToolTip("把当前显示的内容导出成 .log 文件")
        self.btn_log_open = _btn("打开文件", "ghost", 28)
        self.btn_log_clear = _btn("清空", "ghost", 28)
        for b in (self.btn_log_refresh, self.btn_log_export,
                  self.btn_log_open, self.btn_log_clear):
            bar.addWidget(b)
        box.addLayout(bar)

        self.log_view = QPlainTextEdit()
        # 配色交给主题里的 QPlainTextEdit#logView —— 以前 inline 写死，
        # 换到浅色主题后日志区还是一块深色，非常突兀。
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(6000)
        self.log_view.setPlaceholderText(
            "暂无日志 —— 启动网关后这里会实时滚动。\n"
            "也可以点右上角「打开文件」直接看 gateway.log。")
        # ERROR / WARN / INFO 分色：上千行里靠肉眼找错是不现实的
        self.log_highlighter = LogHighlighter(self.log_view.document())
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

        # ---- 下载通道 ---------------------------------------------------
        # 放在最上面：用户在这里看到"更新出错"，修的地方也该在这里。
        dl_card = Card(
            "下载通道",
            "检查更新、下载安装包都要连 GitHub。萝卜盒会依次尝试下列通道，"
            "每条先做一次 0.8 秒探活 —— 连不上的直接跳过。\n"
            "为什么要这样：进程可能从一个外层 shell 继承到一个「已经死掉的」代理，"
            "这时直接请求会干等约 21 秒，最后报「WinError 10060 连接方没有正确答复」——"
            "那个提示看着像 GitHub 挂了，其实是代理连不上。")
        self.routes_out = QPlainTextEdit()
        self.routes_out.setReadOnly(True)
        self.routes_out.setMaximumHeight(150)
        self.routes_out.setPlaceholderText(
            "点「诊断下载通道」查看每条通道是否可达、以及实测结果…")
        dl_card.add(self.routes_out)

        proxy_row = QHBoxLayout()
        self.in_proxy = QLineEdit(str(self.ctx.config.get("net.proxy", "") or ""))
        self.in_proxy.setPlaceholderText("手动代理，留空 = 自动（如 http://127.0.0.1:20809）")
        self.chk_probe = QCheckBox("下载前先探活")
        self.chk_probe.setChecked(bool(self.ctx.config.get("net.probe", True)))
        proxy_row.addWidget(self.in_proxy, 1)
        proxy_row.addWidget(self.chk_probe)
        dl_card.add_layout(proxy_row)

        btn_row = QHBoxLayout()
        self.btn_save_diag = _btn("保存并诊断", "primary")
        self.btn_save_diag.clicked.connect(self._save_and_diagnose)
        btn_row.addWidget(self.btn_save_diag)
        btn_row.addStretch(1)
        dl_card.add_layout(btn_row)
        box.addWidget(dl_card)

        # ---- 萝卜盒自身（应用）更新 --------------------------------------
        app_card = Card(
            "萝卜盒更新",
            "让萝卜盒自己升级到最新版。来源：本项目的 GitHub Release。"
            "升级方式按安装形态自动选择（安装版走静默安装包，便携版走原地覆盖），"
            "完成后自动重启。\n"
            "升级只覆盖下面这个「程序目录」，且安装包会显式带上 /DIR 指定到该目录 ——"
            "不会另装一份到 C 盘。配置 / 日志 / 备份都在数据目录里，不受影响。")
        self.kv_app_dir = KeyValue("程序目录", str(app_root()), mono=True)
        self.kv_app_work = KeyValue("下载中转", str(appupdater.updates_dir()), mono=True)
        self.kv_app_local = KeyValue("当前版本", __version__, mono=True)
        self.kv_app_remote = KeyValue("最新版本", "未检查", mono=True)
        for kv in (self.kv_app_dir, self.kv_app_work, self.kv_app_local, self.kv_app_remote):
            app_card.add(kv)

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
        from .widgets import Card, python_download_tip

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
        # 同向导：默认隐藏，探测不到才浮出来。
        self.py_dl_tip = python_download_tip()
        self.py_dl_tip.setVisible(False)
        form.addRow("", self.py_dl_tip)

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
        net_card = Card("网络与公网入口",
                        "公网走 Tailscale Funnel，不经过 Windows 防火墙；"
                        "Funnel 只允许 443 / 8443 / 10000 三个端口")
        self.chk_funnel = QCheckBox("开启公网入口（Funnel）")
        self.chk_funnel.setChecked(bool(self.ctx.config.get("funnel.enabled")))
        net_card.add(self.chk_funnel)

        f2 = QFormLayout()
        f2.setLabelAlignment(Qt.AlignRight)
        self.in_funnel_port = QComboBox()
        self.in_funnel_port.addItems(["443", "8443", "10000"])
        self.in_funnel_port.setCurrentText(str(self.ctx.config.get("funnel.port", "8443")))
        f2.addRow("Funnel 端口", self.in_funnel_port)

        self.in_ts = QLineEdit(str(self.ctx.config.get("funnel.tailscale_exe")))
        f2.addRow("tailscale.exe", self.in_ts)
        net_card.add_layout(f2)

        warn = QLabel("⚠ 关闭时只会关掉本端口，绝不会执行 funnel reset —— "
                      "那会把同机 443 上其他服务的公网入口一起清掉。")
        warn.setObjectName("mute")
        warn.setWordWrap(True)
        net_card.add(warn)
        box.addWidget(net_card)

        # ---- 外观
        look = Card("外观", "换肤立即生效，不用重启；选择会被记住。")
        lf = QFormLayout()
        lf.setLabelAlignment(Qt.AlignRight)

        self.cmb_palette = QComboBox()
        self.cmb_palette.addItem("深色", "dark")
        self.cmb_palette.addItem("浅色", "light")
        lf.addRow("主题基调", self.cmb_palette)

        self.cmb_accent = QComboBox()
        for key, label in theme.accent_choices():
            self.cmb_accent.addItem(label, key)
        lf.addRow("强调色", self.cmb_accent)

        self.cmb_scale = QComboBox()
        for step, label in zip(theme.SCALE_STEPS, theme.SCALE_LABELS):
            self.cmb_scale.addItem(f"{label}（{int(step * 100)}%）", float(step))
        lf.addRow("字号", self.cmb_scale)
        look.add_layout(lf)

        look_hint = QLabel(
            "强调色只作用于主按钮、选中态和进度条；"
            "红 / 黄 / 绿这些状态色是固定语义，不会被换掉 —— "
            "状态色一旦跟着主题漂，用户就读不出状态了。")
        look_hint.setObjectName("mute")
        look_hint.setWordWrap(True)
        look.add(look_hint)
        box.addWidget(look)

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

        # ---- 数据与磁盘
        disk = Card(
            "数据与磁盘",
            "配置、日志、备份、下载中转都放在「数据目录」里。默认在系统盘"
            "（%LOCALAPPDATA%\\LuoboBox）—— 网关备份一份就可能几百 MB，"
            "C 盘吃紧时可以把整个数据目录搬到别的盘。")
        self.lbl_app_dir = QLabel(str(app_root()))
        self.lbl_app_dir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_app_dir.setWordWrap(True)
        self.lbl_data_dir = QLabel("")
        self.lbl_data_dir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_data_dir.setWordWrap(True)
        self.lbl_data_size = QLabel("正在统计…")
        self.lbl_data_size.setObjectName("mute")
        disk.add(QLabel("程序安装目录（升级只覆盖这里，不会另装到 C 盘）："))
        disk.add(self.lbl_app_dir)
        disk.add(QLabel("数据目录（配置 / 日志 / 备份 / 下载中转）："))
        disk.add(self.lbl_data_dir)
        disk.add(self.lbl_data_size)
        # 指针必须活得比数据目录久，所以它**不在**数据目录里 ——
        # 这一点必须让用户看得见，否则"为什么删了数据目录还能找回来"就成了巫术。
        self.lbl_pointer = QLabel("")
        self.lbl_pointer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_pointer.setWordWrap(True)
        disk.add(QLabel("迁移指针（下次启动靠它去找数据目录）："))
        disk.add(self.lbl_pointer)
        self.lbl_pointer_warn = QLabel("")
        self.lbl_pointer_warn.setObjectName("warnText")
        self.lbl_pointer_warn.setWordWrap(True)
        disk.add(self.lbl_pointer_warn)

        self.lbl_disk_warn = QLabel("")
        self.lbl_disk_warn.setObjectName("warnText")
        self.lbl_disk_warn.setWordWrap(True)
        disk.add(self.lbl_disk_warn)

        mrow = QHBoxLayout()
        self.btn_migrate = _btn("迁移数据目录到其他盘…", "ghost")
        self.btn_migrate.clicked.connect(self._migrate_data_dir)
        self.btn_refresh_disk = _btn("刷新", "ghost")
        self.btn_refresh_disk.clicked.connect(self._refresh_disk_info)
        mrow.addWidget(self.btn_migrate)
        mrow.addWidget(self.btn_refresh_disk)
        mrow.addStretch(1)
        disk.add_layout(mrow)
        box.addWidget(disk)
        # 体积要递归统计两万多个文件，别卡住建界面；推到事件循环里再跑。
        QTimer.singleShot(0, self._refresh_disk_info)

        # ---- 一键配置环境
        env_card = Card(
            "一键配置环境",
            "把「能跑起来」的条件一次性查清并尽量修好：数据目录指针、网关源码、"
            "解释器与依赖、端口、API Key、启动参数、脱敏补丁、内置 WebUI。"
            "已经有值、且验证通过的项不会被覆盖。\n"
            "本机没有 Python 也不用管 —— 会自动下载一份内置的（免安装）；"
            "找不到网关源码会自动从上游拉一份。")
        self.chk_env_venv = QCheckBox("缺依赖时新建独立虚拟环境（推荐：不污染系统 Python）")
        self.chk_env_venv.setChecked(True)
        self.chk_env_venv.setToolTip(
            "建在数据目录下的 pyenv\\，跟着数据一起搬、一起删。\n"
            "不勾选则直接往现有解释器里 pip install（会影响该解释器的其它项目）。")
        env_card.add(self.chk_env_venv)

        erow = QHBoxLayout()
        self.btn_env_setup = _btn("一键配置环境", "primary", 38)
        self.btn_env_diag = _btn("只体检", "ghost", 38)
        self.btn_env_plat = _btn("打开 Python 下载页", "ghost", 38)
        erow.addWidget(self.btn_env_setup)
        erow.addWidget(self.btn_env_diag)
        erow.addWidget(self.btn_env_plat)
        erow.addStretch(1)
        env_card.add_layout(erow)

        self.env_out = QPlainTextEdit()
        self.env_out.setReadOnly(True)
        self.env_out.setMaximumHeight(190)
        self.env_out.setPlaceholderText("点「一键配置环境」后在这里逐行显示过程…")
        env_card.add(self.env_out)
        # 这一页就是为「环境不对」而来的，下载入口摆出来不算噪音。
        env_card.add(python_download_tip())
        box.addWidget(env_card)

        # ---- 迁移与诊断
        diag = Card("迁移与诊断")
        from ..gateway import scheduled_task_exists

        if scheduled_task_exists():
            msg = QLabel("检测到旧部署的计划任务 codebuddy2api 仍存在，会和萝卜盒抢同一端口。")
            msg.setObjectName("warnText")
            msg.setWordWrap(True)
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
        self.btn_log_export.clicked.connect(self._export_log)
        self.btn_log_open.clicked.connect(lambda: self._open_path(log_dir()))
        self.btn_log_clear.clicked.connect(self._clear_log)
        # lambda 包一层：textChanged/toggled 会带参数过来，而 _refresh_log 不收参
        self.log_filter.textChanged.connect(lambda *_: self._refresh_log())
        self.chk_log_regex.toggled.connect(lambda *_: self._refresh_log())

        self.btn_check_app.clicked.connect(self._check_app_update)
        self.btn_do_app_update.clicked.connect(self._do_app_update)

        self.btn_check_update.clicked.connect(self._check_update)
        self.btn_do_update.clicked.connect(self._do_update)

        self.btn_save.clicked.connect(self._save_settings)
        self.btn_reload.clicked.connect(self._reload_settings)
        self.btn_diag.clicked.connect(self._run_diag)
        self.btn_env_setup.clicked.connect(self.run_env_setup)
        self.btn_env_diag.clicked.connect(self._env_diagnose)
        self.btn_env_plat.clicked.connect(self._open_python_download)
        self.btn_data.clicked.connect(lambda: self._open_path(data_dir()))
        self.btn_logdir.clicked.connect(lambda: self._open_path(log_dir()))
        for name in ("btn_del_task",):
            b = getattr(self, name, None)
            if b is not None:
                b.clicked.connect(self._remove_old_task)

        self.chk_funnel.toggled.connect(self._on_funnel_toggled)

        # 英雄区：主按钮 + 复制接入包
        self.hero_btn_start.clicked.connect(self._toggle_gateway)
        self.hero_btn_package.clicked.connect(self.copy_access_package)

        # 外观：三个下拉框任一变更都即时换肤
        for cmb in (self.cmb_palette, self.cmb_accent, self.cmb_scale):
            cmb.currentIndexChanged.connect(self._on_appearance_changed)
        self._sync_appearance_widgets()

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
        reachable = state in ("running", "external")
        self.btn_start.setEnabled(not running and not ctx.runner.busy())
        self.btn_stop.setEnabled(state in ("running", "external", "starting"))
        self.btn_restart.setEnabled(running)
        self.btn_dashboard.setEnabled(reachable)
        self.btn_add_cred.setEnabled(reachable)
        self.btn_refresh_cred.setEnabled(reachable)

        self._refresh_hero(state, color, base, reachable)
        self._refresh_patch_badge()

    def _refresh_hero(self, state: str, color: str, base: str, reachable: bool) -> None:
        """首屏英雄区：状态环 + 标题 + 四个 pill + 主按钮。"""
        label = self.ctx.gateway.state_label

        # 只有真的在跑才呼吸。一直闪着动 = 噪音，"确实活着"这个信号就废了。
        self.dot.set_breathing(state == "running")
        self.hero_ring.set_state(STATE_SHORT.get(state, label), color,
                                 str(self.ctx.config.get("gateway.port")))
        self.hero_ring.set_breathing(state == "running")

        self.hero_title.setText(label)
        self.hero_title.setStyleSheet(f"color: {color};")
        self.hero_sub.setText(
            f"{base}　·　{'已就绪，可直接接入' if reachable else '未就绪'}")

        self.hero_pill_addr.set_text("地址 已就绪" if reachable else "地址 未就绪",
                                     theme.OK if reachable else theme.TEXT_MUTE)
        self.hero_pill_port.set_text(
            f"端口 {self.ctx.config.get('gateway.port')}", theme.TEXT_DIM)

        creds = getattr(self._last_snap, "credentials", None) or []
        if creds:
            good = sum(1 for c in creds if str(c.get("health") or "") == "ready")
            self.hero_pill_cred.set_text(f"凭证 {good}/{len(creds)}",
                                         theme.OK if good else theme.WARN)
        else:
            self.hero_pill_cred.set_text("凭证 —", theme.TEXT_MUTE)

        models = getattr(self.ctx.health, "models", 0) or 0
        self.hero_pill_model.set_text(f"模型 {models}" if models else "模型 —",
                                      theme.OK if models else theme.TEXT_MUTE)

        # 主按钮表达的是"现在该做的那件事"，不是固定标签 —— 运行中就点不了"启动"，
        # 留着它可点只会让用户误以为状态没刷新。
        active = state in ("running", "external", "starting")
        self.hero_btn_start.setText("停止网关" if active else "启动网关")
        self.hero_btn_start.setObjectName("" if active else "primary")
        # objectName 变了必须重套样式表，否则 #primary 的强调色配不上
        self.hero_btn_start.style().unpolish(self.hero_btn_start)
        self.hero_btn_start.style().polish(self.hero_btn_start)
        self.hero_btn_start.setEnabled(not self.ctx.runner.busy())

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
        self._last_snap = snap
        self._refresh_state()
        self._fill_credentials(snap)
        self._fill_credits(snap)

    def _fill_credentials(self, snap) -> None:
        rows = getattr(snap, "credentials", None) or []
        if not rows:
            # 空态而不是"一行灰字占位"：灰字看起来像加载坏了，
            # 空态会说明原因并给出下一步按钮。
            self.cred_table.setRowCount(0)
            self.cred_empty.set_text(
                "凭证池是空的" if snap.ok else "暂时读不到凭证池",
                "到「管理台 → 凭证」扫码或导入 .info 文件，加完会自动出现在这里。"
                if snap.ok else
                "网关可能没有在运行 —— 启动网关后这里会自动刷新。")
            self.cred_stack.setCurrentIndex(1)
            return

        self.cred_stack.setCurrentIndex(0)
        self.cred_table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            # 逐行兜底：某一行字段形态异常时只标记这一行，
            # 不能让整个循环中断（早期就是被一个 dict 拖垮了整张表）。
            try:
                name = str(item.get("name") or item.get("id") or "?")
                health = str(item.get("health") or "unknown")
                label, color = health_label(health)

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

    def _log_pattern(self) -> re.Pattern | None:
        """把过滤框编译成正则；没填、没勾正则、或写错了都返回 None。"""
        needle = self.log_filter.text().strip()
        if not needle or not self.chk_log_regex.isChecked():
            return None
        try:
            return re.compile(needle, re.IGNORECASE)
        except re.error:
            # 正则写错不该让日志页变空白 —— 静默退回普通包含匹配，
            # 并在状态行里说明，用户才知道"为什么没按正则生效"。
            return None

    def _refresh_log(self) -> None:
        if not self.isVisible() or self.tab_key() != "logs":
            return
        raw = self.ctx.gateway.tail_log(
            int(self.ctx.config.get("ui.log_tail_lines", 800)))
        lines = raw.splitlines()

        needle = self.log_filter.text().strip()
        rx = self._log_pattern()
        if rx is not None:
            shown = [ln for ln in lines if rx.search(ln)]
        elif needle:
            low = needle.lower()
            shown = [ln for ln in lines if low in ln.lower()]
        else:
            shown = lines
        text = "\n".join(shown)

        bar = self.log_view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        if text != self.log_view.toPlainText():
            self.log_view.setPlainText(text)
            if self.log_autoscroll.isChecked() and at_bottom:
                self.log_view.moveCursor(QTextCursor.End)

        path = log_dir() / "gateway.log"
        size = path.stat().st_size if path.is_file() else 0
        tally = ""
        if needle:
            tally = f"　命中 {len(shown)}/{len(lines)} 行"
            if self.chk_log_regex.isChecked():
                tally += "（正则）" if rx is not None else "　⚠ 正则无效，已按普通文本匹配"
        self.log_meta.setText(
            f"gateway.log　{size / 1024:.1f} KB　共 {len(lines)} 行{tally}"
            f"　{self._log_stamp()}")

    def _export_log(self) -> None:
        """导出当前显示内容（含过滤结果）。文件名带时间戳，两次导出不会互相覆盖。"""
        text = self.log_view.toPlainText()
        if not text.strip():
            self._on_toast("当前没有可导出的日志内容", "warn")
            return
        default = str(Path.home()
                      / f"luobobox-gateway-{time.strftime('%Y%m%d-%H%M%S')}.log")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", default, "日志文件 (*.log);;文本文件 (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._on_toast(f"导出失败：{exc}", "error")
            return
        self._on_toast(f"已导出 {len(text.splitlines())} 行 → {Path(path).name}", "ok")
        motion.flash(self.btn_log_export, theme.OK)

    @staticmethod
    def _log_stamp() -> str:
        return time.strftime("%H:%M:%S")

    # ================================================================ 快捷键

    def _bind_shortcuts(self) -> None:
        """窗口级快捷键。

        键位一律沿用编辑器 / 浏览器里的既有习惯，不自创：
        Ctrl+1..N 切页、F5 刷新、Ctrl+K 命令面板、Ctrl+, 设置。

        ★ 切页的顺序严格跟着 `tab_keys()`，页签增删后自动跟上 ——
        不会出现"Ctrl+4 以前是日志、现在变成了更新"。
        """
        def bind(seq: str, slot) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

        self._bind_tab_shortcuts()

        bind("Ctrl+K", self._open_palette)
        bind("Ctrl+R", lambda: self.ctx.restart_gateway())
        bind("F5", self.refresh_all)
        bind("Ctrl+Shift+C", self.copy_access_package)
        bind("Ctrl+,", lambda: self.goto_tab("settings"))

    def _bind_tab_shortcuts(self) -> None:
        """把 Ctrl+1..N 按**当前**页签顺序重绑。

        ★ 为什么不能只在 __init__ 里绑一次：额度页是 app.py 在 `_build()`
          **之后**才 add_tab 进来的，绑定时它还不存在 —— 结果是 Ctrl+7 永远
          绑不上；以后每加一页都会漏一个。所以 add_tab 会回调这里重绑。
        """
        for sc in getattr(self, "_tab_shortcuts", []):
            sc.setEnabled(False)          # 先禁用，避免重绑瞬间两个快捷键同时响应
            if sc in self._shortcuts:
                self._shortcuts.remove(sc)
            sc.deleteLater()
        self._tab_shortcuts = []

        for i, key in enumerate(self.tab_keys()[:9], start=1):
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.activated.connect(lambda k=key: self.goto_tab(k))
            self._shortcuts.append(sc)
            self._tab_shortcuts.append(sc)

    def _open_palette(self) -> None:
        from .palette import build_commands, open_palette

        cmd = open_palette(self, build_commands(self))
        if cmd is None or cmd.run is None:
            return
        try:
            cmd.run()
        except Exception as exc:  # noqa: BLE001
            self._on_toast(f"命令执行失败：{type(exc).__name__}：{exc}", "error")

    def refresh_all(self) -> None:
        """统一刷新入口（F5）：状态 + 客户端 + 日志。"""
        self.ctx.refresh_health()
        self._refresh_clients()
        self._refresh_log()
        self._on_toast("已刷新", "info")

    def _toggle_gateway(self) -> None:
        """英雄区那颗主按钮：正在跑就停，否则就启动。"""
        if self.ctx.quick_state in ("running", "external", "starting"):
            self.ctx.stop_gateway()
        else:
            self.ctx.start_gateway()

    # ================================================================ 接入包

    def access_package(self) -> str:
        """把接入所需的一切拼成一段 Markdown。

        为什么要"打包"：地址有 4 个（本机 / 局域网 / Tailscale / 公网），
        加上 Key、再加两段客户端配置 —— 分别复制至少切 6 次窗口，
        而且极容易漏掉 Key（漏了就会报 401，用户还找不到原因）。
        汇成一份一次复制走，贴到群里或备忘里就是完整的一份。
        """
        cfg = self.ctx.config
        port = cfg.get("gateway.port")
        pub = self.ctx.funnel_status.url or (
            self.ctx.funnel.public_url() if cfg.get("funnel.enabled") else "")
        return "\n".join([
            f"# 萝卜盒 LuoboBox {__version__} 接入信息",
            "",
            f"- 本机地址：{cfg.base_url()}",
            f"- 局域网：http://{self._lan_ip()}:{port}",
            f"- Tailscale：{self._ts_url()}",
            f"- 公网入口：{pub or '未开启'}",
            f"- API Key：`{cfg.get('gateway.api_key', '')}`",
            "",
            "## Codex（写入 config.toml）",
            "",
            "```toml",
            snippet_codex(cfg, with_key=True).strip(),
            "```",
            "",
            "## Claude Code / Anthropic 兼容客户端",
            "",
            "```bash",
            snippet_claude(cfg).strip(),
            "```",
            "",
        ])

    def copy_access_package(self) -> None:
        text = self.access_package()
        QApplication.clipboard().setText(text)
        self._on_toast(f"接入包已复制到剪贴板（Markdown，{len(text)} 字符）", "ok")
        motion.flash(self.hero_btn_package, theme.OK)

    def copy_api_key(self) -> None:
        QApplication.clipboard().setText(
            str(self.ctx.config.get("gateway.api_key", "")))
        self._on_toast("API Key 已复制到剪贴板", "ok")

    # ================================================================ 外观

    def _on_appearance_changed(self, *_args) -> None:
        self.apply_appearance(
            palette=self.cmb_palette.currentData(),
            accent=self.cmb_accent.currentData(),
            scale=float(self.cmb_scale.currentData()),
        )

    def apply_appearance(self, palette=None, accent=None, scale=None,
                         announce: bool = True) -> None:
        """换肤 + 落盘。设置页与命令面板都走这里 —— 只有一处实现。

        顺序不能反：先改 theme 的当前值 → 再重出样式表 →
        最后回刷那些把颜色"抠"进内联样式的控件。
        漏掉最后一步，磁盘告警这类用 setStyleSheet 上色的地方
        会停在旧配色上（浅色主题里一块深橙）。
        """
        theme.apply(palette=palette, accent=accent, scale=scale)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.stylesheet())

        cur = theme.current()
        cfg = self.ctx.config
        cfg.set("ui.palette", cur["palette"])
        cfg.set("ui.accent", cur["accent"])
        cfg.set("ui.scale", cur["scale"])
        cfg.save()

        self._sync_appearance_widgets()
        if announce:
            scale_label = dict(zip(theme.SCALE_STEPS, theme.SCALE_LABELS)).get(
                cur["scale"], "标准")
            self._on_toast(
                "外观已更新：{} / {} / {}".format(
                    "深色" if cur["palette"] == "dark" else "浅色",
                    theme.ACCENTS[cur["accent"]]["label"], scale_label),
                "ok")

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        """按用户数据选中下拉项（blockSignals，避免换肤又触发一次换肤）。"""
        combo.blockSignals(True)
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                break
        combo.blockSignals(False)

    def _sync_appearance_widgets(self) -> None:
        """把换肤结果同步到下拉框与内联着色的控件。"""
        if getattr(self, "cmb_palette", None) is not None:
            self._select_data(self.cmb_palette, theme.palette_name())
            self._select_data(self.cmb_accent, theme.accent_name())
            self._select_data(self.cmb_scale, float(theme.scale()))

        self._refresh_state()
        if self._last_snap is not None:
            self._fill_credentials(self._last_snap)
        self._refresh_clients()
        for toast in (self.overview_toast, self.client_toast, self.update_toast):
            toast.repaint_theme()
        self.update()

    # ================================================================ 动作

    def _open_dashboard(self) -> None:
        """打开网页版管理台。

        上游 Release 从不带 web/dist，升级网关就会把它冲掉 → /dashboard/ 503。
        这里现场自愈：先用萝卜盒内置的预构建 dist 补齐，再开浏览器。
        拷贝只有几百 KB，同步做掉即可，不必走后台线程。
        """
        try:
            from .. import webui

            rep = webui.ensure(self.ctx.config.get("gateway.dir"))
            if rep.changed:
                self._on_toast(f"网页版管理台：{rep.message}", "ok")
        except Exception:  # noqa: BLE001
            pass
        QDesktopServices.openUrl(QUrl(self.ctx.config.dashboard_url()))

    def _open_credentials_page(self) -> None:
        """账号只在管理台里添加 —— 直接切到「管理台 → 凭证」，省得用户找。"""
        if not self.goto_tab("admin"):
            return
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
                self.set_update_badge("app", f"萝卜盒 {info.tag}")
                self._on_toast(f"萝卜盒有新版本 {info.tag}（当前 {__version__}）", "warn")
                self.app_update_notes.setPlainText(
                    f"{info.name}\n发布于 {info.published}\n\n{info.notes}")
                self.btn_do_app_update.setEnabled(True)
            else:
                self.set_update_badge("app", "")
                self._on_toast(f"萝卜盒已是最新（{__version__}）", "ok")
                self.app_update_notes.setPlainText("当前已是最新版本。")
                self.btn_do_app_update.setEnabled(False)

        self.ctx.run_task(
            lambda: appupdater.check_app(repo), done,
            lambda m: self._app_net_failed("检查应用更新失败", m),
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
            "下载完成后萝卜盒会自动关闭，由后台助手完成替换并重新启动。\n\n"
            f"· 程序目录（升级只覆盖这里）：{app_root()}\n"
            f"· 下载中转目录：{dest}\n"
            f"· 数据目录（配置 / 日志 / 备份）：{data_dir()}\n"
            f"· 安装包会显式指定 /DIR={app_root()}，不会另装一份到 C 盘\n\n"
            "现在更新？",
        ) != QMessageBox.Yes:
            return

        self.ctx.run_task(
            lambda: self._do_app_update_work(mode, name, url),
            self._after_app_update,
            lambda m: self._app_net_failed("应用更新失败", m),
            busy_text="正在下载萝卜盒更新…",
        )

    def _app_net_failed(self, prefix: str, message: str) -> None:
        """失败时把**完整**原因铺开。

        net 层抛出来的是一份逐通道清单（"手动代理…：连不上（探活 0.8s 超时，已跳过）"…）。
        只取第一行等于把最有用的信息丢掉，用户就又回到"只知道出错、不知道改哪"。
        """
        self._on_toast(f"{prefix}：{message.splitlines()[0]}", "error")
        self.app_update_notes.setPlainText(message)

    def _gateway_net_failed(self, prefix: str, message: str) -> None:
        self._on_toast(f"{prefix}：{message.splitlines()[0]}", "error")
        self.update_notes.setPlainText(message)

    def _do_app_update_work(self, mode: str, name: str, url: str) -> str:
        archive = appupdater.download(url, appupdater.updates_dir(), name)
        route = appupdater.LAST_ROUTE
        plan = appupdater.apply_and_restart(mode, archive)
        if not plan.ok:
            return plan.message
        return f"下载走的是{route}；{plan.message}" if route else plan.message

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
                self.set_update_badge("gateway", f"网关 {info.tag}")
                self._on_toast(f"发现新版本 {info.tag}（当前 {info.local_version}）", "warn")
                self.update_notes.setPlainText(
                    f"{info.name}\n发布于 {info.published}\n\n{info.notes}")
                self.btn_do_update.setEnabled(True)
            else:
                self.set_update_badge("gateway", "")
                self._on_toast(f"已是最新（{info.local_version or info.tag}）", "ok")
                self.update_notes.setPlainText("当前版本已是最新。")
                self.btn_do_update.setEnabled(False)

        self.ctx.run_task(
            lambda: updater.check(gw, repo), done,
            lambda m: self._gateway_net_failed("检查更新失败", m),
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
            "· 会自动备份整个网关目录（已排除 node_modules / .venv / .git 等，"
            f"只保留最近 {updater.BACKUP_KEEP} 份）\n"
            "· 保留 auth/、.env 与既有启动脚本\n"
            "· 升级后自动重打脱敏补丁\n\n继续？",
        ) != QMessageBox.Yes:
            return

        gw = Path(str(self.ctx.config.get("gateway.dir")))
        self.ctx.run_task(
            lambda: self._do_update_work(gw, info),
            lambda r: self._on_toast(r, "ok"),
            self._gateway_update_failed,
            busy_text="正在下载并应用更新…",
        )

    def _gateway_update_failed(self, message: str) -> None:
        """同 _app_update_failed：完整原因进正文，别只留第一行。"""
        self._on_toast(f"更新失败：{message.splitlines()[0]}", "error")
        self.update_notes.setPlainText(message)

    def _do_update_work(self, gw: Path, info) -> str:
        dl = backup_dir() / "_downloads"
        archive = updater.download(info.tarball or info.zipball, dl)
        res = updater.apply_release(gw, archive)
        updater.cleanup_downloads()
        if not res.ok:
            return res.message
        extra = f"；已清理 {res.pruned} 份旧备份" if res.pruned else ""
        return (f"{res.message}；脱敏补丁：{res.patched}；"
                f"网页版管理台：{res.webui}{extra}。请重启网关使改动生效。")

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
            # 与向导同款：不存在的候选折成一行计数，别把真正有用的失败原因冲掉。
            lines = []
            missing = 0
            for cand, why in report:
                if why == "文件不存在":
                    missing += 1
                    continue
                mark = "✓" if why == "可用" else "✗"
                lines.append(f"{mark} {cand}   {why}")
            if missing:
                lines.append(f"（另有 {missing} 个候选路径不存在，已省略）")
            return py, "\n".join(lines[:12]) or "未发现任何 Python 解释器。"

        def done(res):
            py, report = res
            self.diag_out.setPlainText(report)
            self.py_dl_tip.setVisible(py is None)
            if py:
                self.in_python.setText(str(py))
                self._on_toast(f"选中解释器：{py}", "ok")
            else:
                self._on_toast("没有找到带 fastapi/uvicorn/httpx 的 Python，"
                               "可点「解释器」下方的下载链接装一个", "error")

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

    def _refresh_disk_info(self) -> None:
        """刷新数据目录位置与体积（体积统计放后台线程）。"""
        d = data_dir()
        self.lbl_data_dir.setText(str(d))
        self.lbl_disk_warn.setText(
            "⚠ 数据目录在系统盘上。网关备份一份可能几百 MB，"
            "C 盘吃紧时建议迁到 D 盘 —— 备份和下载中转会一起搬走。"
            if is_on_system_drive(d) else "")

        # 指针的位置必须看得见：它决定了「删掉数据目录之后程序还找不找得回来」。
        primary, legacy = pointer_primary_path(), pointer_legacy_path()
        if legacy.is_file() and legacy != primary and not primary.is_file():
            self.lbl_pointer.setText(f"⚠ 还在老位置：{legacy}")
            self.lbl_pointer_warn.setText(
                "指针躺在出厂默认目录里 —— 用户清理 C 盘时删掉那个文件夹，指针就跟着没了，"
                "迁移会被静默撤销（数据不会丢，但程序会回 C 盘重建一份空数据）。"
                "点「一键配置环境」即可把它挪到程序目录旁边。")
        else:
            self.lbl_pointer.setText(str(primary))
            self.lbl_pointer_warn.setText("")

        self.lbl_data_size.setText("正在统计体积…")

        def work() -> str:
            return (f"当前占用 {human_size(dir_size(d))}"
                    f"，其中备份 {human_size(dir_size(backup_dir()))}")

        self.ctx.run_task(work, self.lbl_data_size.setText,
                          lambda m: self.lbl_data_size.setText(f"统计失败：{m}"))

    def _migrate_data_dir(self) -> None:
        """把整个数据目录搬到别的盘，缓解系统盘压力。"""
        src = data_dir()
        start = (str(Path(src).drive) + "\\") if Path(src).drive else ""
        target = QFileDialog.getExistingDirectory(
            self, "选择新的数据目录所在位置（建议 D 盘）", start)
        if not target:
            return
        chosen = Path(target)
        dst = chosen if chosen.name.lower() == "luoboboxdata" else chosen / "LuoboBoxData"
        if dst.resolve() == src.resolve():
            self._on_toast("选中的就是当前数据目录，无需迁移", "warn")
            return
        if QMessageBox.question(
            self, "迁移数据目录",
            f"把整个数据目录搬到：\n    {dst}\n\n"
            f"· 原位置：{src}\n"
            "· 顺序是「先复制 → 再写指针 → 最后删旧」，中途失败不会丢数据\n"
            "· 指针文件留在原位置，程序下次启动才知道新位置在哪\n"
            "· 搬完需要重启萝卜盒才生效\n\n继续？",
        ) != QMessageBox.Yes:
            return

        def done(res) -> None:
            ok, msg = res
            self._on_toast(msg if ok else f"迁移未完成：{msg}", "ok" if ok else "error")
            self._refresh_disk_info()

        self.ctx.run_task(lambda: migrate_data_dir(dst), done,
                          lambda m: self._on_toast(f"迁移失败：{m}", "error"),
                          busy_text="正在迁移数据目录…")

    # ---------------------------------------------------------- 一键配置环境

    def _open_python_download(self) -> None:
        from ..paths import PYTHON_DOWNLOADS

        QDesktopServices.openUrl(QUrl(PYTHON_DOWNLOADS[0][1]))

    def _append_env_log(self, text: str) -> None:
        """工作线程 emit 的日志落到控件上（永远在主线程执行）。"""
        self.env_out.appendPlainText(text)

    def _env_diagnose(self) -> None:
        """只体检、不改动 —— 想先看看差在哪的时候用。"""
        from .. import envsetup

        self.env_out.setPlainText("正在体检…")

        def work() -> str:
            rep = envsetup.diagnose(self.ctx.config)
            return f"{envsetup.summary_text(rep)}\n\n{rep.text()}\n\n{rep.todo}"

        self.ctx.run_task(work, self.env_out.setPlainText,
                          lambda m: self.env_out.setPlainText(f"体检失败：{m}"))

    def run_env_setup(self) -> None:
        """一键配置环境：体检 → 能修的当场修 → 复检，日志实时回显。

        刻意不改用「体检完弹个确认框再修」：这些都是幂等且不破坏数据的动作
        （缺什么补什么、已有的不覆盖），多一次确认只多一次犹豫。
        真正有破坏性的动作（删旧计划任务、迁移数据目录）不在这里，各有自己的按钮。
        """
        from .. import envsetup

        prefer_venv = self.chk_env_venv.isChecked()
        self.env_out.clear()
        self.env_out.setPlainText(
            "开始一键配置环境…（缺依赖时会联网装包，通常几十秒，慢的话 1-2 分钟）")

        def work():
            return envsetup.setup(self.ctx.config, on_log=self.env_logged.emit,
                                  prefer_venv=prefer_venv)

        def done(res) -> None:
            ok, lines = res
            # 回灌表单，否则用户点一下「保存设置」就把刚修好的值覆盖回去了
            self._sync_env_widgets()
            if ok:
                self._on_toast("环境已就绪，可以启动网关", "ok")
            else:
                self._on_toast("还有项目需要手动处理，见清单最后一行", "warn")
                # 日志会被滚走、Toast 在角落 —— 没配好必须弹窗，让用户当场看到。
                from .widgets import message_popup

                detail = "\n".join(
                    [ln for ln in (lines or []) if str(ln).strip()][-10:])
                message_popup(
                    self, "环境还没配好",
                    "一键配置环境没能把所有项都配好。\n\n"
                    "可以再点一次重试；若提示下载失败，多半是网络或代理问题 —— "
                    "到「设置 → 下载通道」配好代理后重试。",
                    detail, icon="warn")
            self._refresh_state()

        def fail(msg) -> None:
            self.env_out.appendPlainText(f"\n配置失败：{msg}")
            from .widgets import message_popup

            message_popup(
                self, "配置失败",
                "配置环境时出错了，没能完成。",
                str(msg or "").strip() or "未知错误", icon="error")

        self.ctx.run_task(work, done, fail, busy_text="正在配置环境…")

    def _sync_env_widgets(self) -> None:
        cfg = self.ctx.config
        self.in_dir.setText(str(cfg.get("gateway.dir") or ""))
        self.in_python.setText(str(cfg.get("gateway.python") or ""))
        self.in_port.setValue(int(cfg.get("gateway.port", 8788) or 8788))
        self.in_key.setText(str(cfg.get("gateway.api_key") or ""))
        self.in_args.setPlainText("\n".join(cfg.get("gateway.extra_args", []) or []))

    def _save_and_diagnose(self) -> None:
        """把下载通道设置写进配置，然后逐条实测。

        这里刻意就地保存（不等「保存设置」）—— 用户是在更新页撞上
        「更新出错」的，修的地方就该在同一屏，多绕一步只会让人放弃。
        """
        cfg = self.ctx.config
        cfg.set("net.proxy", self.in_proxy.text().strip())
        cfg.set("net.probe", self.chk_probe.isChecked())
        cfg.save()
        self._on_toast("下载通道设置已保存", "ok")
        self._diagnose_network()

    def _diagnose_network(self) -> None:
        proxy = str(self.ctx.config.get("net.proxy", "") or "")
        self.routes_out.setPlainText("正在逐条探测下载通道…")
        self.ctx.run_task(
            lambda: net.diagnose(proxy),
            self.routes_out.setPlainText,
            lambda m: self.routes_out.setPlainText(f"网络诊断失败：{m}"),
            busy_text="正在探测下载通道…",
        )

    def _run_diag(self) -> None:
        self.diag_out.setPlainText("体检中…")

        def work():
            cfg = self.ctx.config
            lines = []
            lines.append(f"萝卜盒版本      {__version__}")
            lines.append(f"程序目录        {app_root()}")
            lines.append(f"数据目录        {data_dir()}"
                         f"  {'（系统盘）' if is_on_system_drive() else '（非系统盘）'}")
            lines.append(f"数据占用        {human_size(dir_size(data_dir()))}"
                         f"，其中备份 {human_size(dir_size(backup_dir()))}")
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

            manual = str(cfg.get("net.proxy", "") or "")
            lines.append(f"[网络] 手动代理  {manual or '（未设置，自动挑选）'}")
            lines.append("[网络] 下载通道  （探活 0.8 秒，连不上就跳过）")
            for row in net.describe_routes(manual):
                lines.append(f"                 · {row}")
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
            return

        # 启动时补齐内置 WebUI —— 覆盖「升级后没重启过」「dist 从没构建过」等情况
        try:
            from .. import webui

            rep = webui.ensure(self.ctx.config.get("gateway.dir"))
            if rep.changed:
                self._on_toast(f"网页版管理台：{rep.message}", "ok")
        except Exception:  # noqa: BLE001
            pass

    # ================================================================ 关闭

    def force_quit_on_close(self) -> None:
        """无托盘可用时：关窗即退出，否则程序会变成没有入口的幽灵进程。"""
        self._force_quit = True

    def closeEvent(self, event) -> None:  # noqa: N802
        # 无论后面是"收进托盘"还是"真退出"，窗口几何都先落盘 ——
        # 收进托盘之后用户下次回来，看到的还应该是他调好的那个大小。
        self._save_geometry()
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
