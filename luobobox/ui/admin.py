"""原生管理台：把网关的 ``/admin/*`` REST 接口直接做进 GUI。

为什么走 REST 而不是「内嵌 WebUI」：

  · 内嵌浏览器内核（QtWebEngine）会让安装包胖 ~100MB，本项目明确不内嵌 WebUI；
  · 上游前端的 ``dist/`` **不在源码仓库里**（``web/.gitignore`` 写了 ``dist/``），
    而网关升级是「整目录 rmtree + copytree」—— 每次升级都会把 ``web/dist`` 冲掉，
    于是 ``/dashboard/`` 直接 503「WebUI 尚未构建」。
    这就是「后台管理打不开」的真正根因，且**必然复发**。

直接对话 REST 接口，就与前端构建彻底解耦：网关怎么升级，这个界面都不会坏。

认证：``AdminMiddleware`` 认 ``Authorization: Bearer`` 或 ``X-Api-Key``，
走 header 认证既不需要 session cookie，也不需要 CSRF 令牌 —— 正好适合本机 GUI。

线程模型与 main_window 一致：网络请求全部丢给 ``ctx.run_task`` 在后台线程跑，
回调里只更新控件。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import Card, Toast

# 扫码登录可选的站点。键是给用户看的中文，值是网关认的 site 参数。
SITE_CHOICES = [
    ("国内 CodeBuddy", "cn"),
    ("国际 WorkBuddy", "intl"),
    ("国际 CodeBuddy", "intl-codebuddy"),
]

def health_label(health: str) -> tuple[str, str]:
    """健康度 → (中文标签, 颜色)。

    ★ 必须是函数，不能是模块级 dict。dict 会在 import 那一刻把
    `theme.OK` 的**值**拷进去，等于把颜色冻死 —— 换到浅色主题后，
    表格里的"异常"仍是深色主题的浅粉红（#F09595），白底上几乎看不见。
    """
    return {
        "ready": ("正常", theme.OK),
        "expired": ("已过期", theme.ERR),
        "circuit_open": ("已熔断", theme.WARN),
        "error": ("异常", theme.ERR),
        "disabled": ("已停用", theme.TEXT_MUTE),
    }.get(health, (health or "—", theme.TEXT_DIM))

CLEAR_CONFIRMATION = "清空全部日志与统计"


# ============================================================ 基础设施


class AdminClient:
    """本机管理 API 客户端：始终绕过系统代理（否则请求会被甩到 http_proxy 上）。"""

    def __init__(self, config):
        self.config = config

    def request(self, path, *, method="GET", payload=None, timeout=25, raw=False):
        """返回 ``(状态码, 解析后的响应体, 响应头)``；连接失败时状态码为 0。"""
        base = str(self.config.base_url())
        key = str(self.config.get("gateway.api_key", ""))
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("X-Api-Key", key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read()
                return resp.status, (body if raw else self._decode(body)), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, (body if raw else self._decode(body)), dict(exc.headers)
        except Exception as exc:  # noqa: BLE001
            return 0, f"{exc}", {}

    def api(self, path, *, method="GET", payload=None, timeout=25):
        """便利包装：只关心 ``(状态码, 解析后的响应体)``。"""
        code, body, _ = self.request(path, method=method, payload=payload, timeout=timeout)
        return code, body

    @staticmethod
    def _decode(body: bytes):
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return body.decode("utf-8", "replace")

    @staticmethod
    def describe(code: int, body) -> str:
        """把网关的错误体翻译成一行可读中文，别把原始 JSON 甩给用户。"""
        if code == 0:
            return f"网关无法连接：{str(body)[:160]}"
        if isinstance(body, dict):
            for container in (body, body.get("detail")):
                if isinstance(container, dict):
                    err = container.get("error")
                    if isinstance(err, dict) and err.get("message"):
                        return f"HTTP {code}：{err['message']}"
                    if isinstance(err, str) and err:
                        return f"HTTP {code}：{err}"
                    if isinstance(container.get("detail"), str):
                        return f"HTTP {code}：{container['detail']}"
            if isinstance(body.get("detail"), str):
                return f"HTTP {code}：{body['detail']}"
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        return f"HTTP {code}：{str(text)[:200]}"


def _btn(text: str, object_name: str = "ghost", height: int = 30) -> QPushButton:
    b = QPushButton(text)
    if object_name:
        b.setObjectName(object_name)
    b.setMinimumHeight(height)
    b.setCursor(Qt.PointingHandCursor)
    return b


def _table(headers: list[str], stretch: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setMinimumHeight(180)
    hh = t.horizontalHeader()
    for i in range(len(headers)):
        hh.setSectionResizeMode(i, QHeaderView.Stretch if i == stretch else QHeaderView.ResizeToContents)
    return t


def _cell(text, color: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    if color:
        item.setForeground(QColor(color))
    return item


def _set(t: QTableWidget, row: int, col: int, text, color: str | None = None) -> None:
    t.setItem(row, col, _cell(text, color))


def _credit_text(value) -> str:
    """积分字段并不总是数字：拉过明细时是 {"credits": ...} 对象，没拉到时是 None。"""
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


def _stamp(seconds) -> str:
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(float(seconds)))
    except (TypeError, ValueError, OSError):
        return "—"


def _expiry(ms) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ms) / 1000.0))
    except (TypeError, ValueError, OSError):
        return "—"


def _yesno(flag, yes: str = "开", no: str = "关") -> tuple[str, str]:
    return (yes, theme.OK) if flag else (no, theme.TEXT_MUTE)


# ============================================================ 管理台


class AdminConsole(QWidget):
    """管理台页签：统计 / 模型 / 凭证 / 日志 / 设置。"""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.client = AdminClient(ctx.config)

        self._stats_days = 7
        self._models: list[dict] = []
        self._models_rev = 0
        self._creds: list[dict] = []
        self._settings_items: list[dict] = []
        self._settings_rev = 0
        self._log_cursors: list[str] = []
        self._log_rows: list[dict] = []
        self._oauth_login_id = ""

        self._oauth_timer = QTimer(self)
        self._oauth_timer.setInterval(2500)
        self._oauth_timer.timeout.connect(self._oauth_poll)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.toast = Toast()
        root.addWidget(self.toast)

        self.sub = QTabWidget()
        self.sub.addTab(self._mk_stats(), "统计")
        self.sub.addTab(self._mk_models(), "模型")
        self.sub.addTab(self._mk_creds(), "凭证")
        self.sub.addTab(self._mk_logs(), "日志")
        self.sub.addTab(self._mk_settings(), "设置")
        self.sub.currentChanged.connect(lambda _i: self.refresh())
        root.addWidget(self.sub, 1)

    # ------------------------------------------------------------ 公共

    def _say(self, text: str, level: str = "info") -> None:
        self.toast.show_message(text, level)

    def show_credentials_tab(self) -> None:
        """给「添加账号」按钮用：直接落到凭证页。"""
        self.sub.setCurrentIndex(2)
        self.refresh()

    def refresh(self) -> None:
        """刷新当前子页。网关没起来时给一句人话，而不是一串连接错误。"""
        if not self.gateway_reachable():
            self._say("网关未在运行 —— 先点顶部「启动」，管理台才能读到数据。", "warn")
            return
        idx = self.sub.currentIndex()
        handler = {0: self._load_stats, 1: self._load_models, 2: self._load_creds,
                   3: self._reload_logs, 4: self._load_settings}.get(idx)
        if handler is not None:
            handler()

    def gateway_reachable(self) -> bool:
        state = self.ctx.gateway.state
        return state in ("running", "external")

    def _run(self, work, done, busy_text: str | None = None) -> None:
        self.ctx.run_task(work, done, lambda m: self._say(f"操作失败：{m.splitlines()[0]}", "error"),
                          busy_text=busy_text)

    # ============================================================ 统计

    def _mk_stats(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(10)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("时间范围"))
        self.stats_days = QComboBox()
        for label, days in (("今天", 1), ("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90)):
            self.stats_days.addItem(label, days)
        self.stats_days.setCurrentIndex(1)
        self.stats_days.currentIndexChanged.connect(self._load_stats)
        bar.addWidget(self.stats_days)
        self.btn_stats_refresh = _btn("刷新")
        self.btn_stats_refresh.clicked.connect(self._load_stats)
        bar.addWidget(self.btn_stats_refresh)
        bar.addStretch(1)
        box.addLayout(bar)

        kpi = Card("核心指标")
        self.stats_kpi = QLabel("—")
        self.stats_kpi.setObjectName("dim")
        self.stats_kpi.setWordWrap(True)
        kpi.add(self.stats_kpi)
        self.stats_storage = QLabel("")
        self.stats_storage.setObjectName("mute")
        self.stats_storage.setWordWrap(True)
        kpi.add(self.stats_storage)
        box.addWidget(kpi)

        series_card = Card("按天用量", "积分消耗与请求量随时间的分布")
        self.stats_series = _table(["日期", "请求", "成功", "失败", "积分", "总 Token"], stretch=0)
        self.stats_series.setMinimumHeight(150)
        series_card.add(self.stats_series)
        box.addWidget(series_card)

        model_card = Card("按模型", "哪个模型在吃积分")
        self.stats_models = _table(["模型", "请求", "成功", "失败", "积分"], stretch=0)
        self.stats_models.setMinimumHeight(150)
        model_card.add(self.stats_models)
        box.addWidget(model_card)

        bal_card = Card("账号余额", "官方口径的剩余积分（来自 /admin/dashboard）")
        self.stats_balance = _table(["账号", "昵称", "健康", "剩余积分"], stretch=0)
        self.stats_balance.setMinimumHeight(150)
        bal_card.add(self.stats_balance)
        box.addWidget(bal_card)

        box.addStretch(1)
        return page

    def _load_stats(self) -> None:
        if not self.gateway_reachable():
            self._say("网关未在运行，无法读取统计。", "warn")
            return
        days = self.stats_days.currentData() or 7

        def work():
            return self.client.api(f"/admin/dashboard?days={days}")

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._say(self.client.describe(code, body), "error")
                return
            self._fill_stats(body)

        self._run(work, done, busy_text="正在读取统计…")

    def _fill_stats(self, data: dict) -> None:
        s = data.get("summary") or {}
        rate = s.get("success_rate")
        rate_txt = f"{rate * 100:.2f}%" if isinstance(rate, (int, float)) else "—"
        self.stats_kpi.setText(
            f"请求 {s.get('requests', 0):,}　成功 {s.get('success', 0):,}　"
            f"失败 {s.get('error', 0):,}　取消 {s.get('cancelled', 0):,}　"
            f"成功率 {rate_txt}\n"
            f"消耗积分 {_credit_text(s.get('credit'))}　"
            f"输入 Token {s.get('input_tokens', 0) or 0:,}　"
            f"输出 Token {s.get('output_tokens', 0) or 0:,}　"
            f"推理 Token {s.get('reasoning_tokens', 0) or 0:,}　"
            f"合计 Token {s.get('total_tokens', 0) or 0:,}"
        )

        st = data.get("storage") or {}
        if st:
            degraded = "是（数值可能不准）" if st.get("degraded") else "否"
            self.stats_storage.setText(
                f"审计存储：逻辑 {st.get('logical_bytes', 0) / 1048576:.1f} MB"
                f" / 上限 {st.get('max_bytes', 0) / 1048576:.0f} MB　"
                f"请求 {st.get('request_count', 0):,}　事件 {st.get('event_count', 0):,}　"
                f"保留 {st.get('retention_days', '?')} 天　降级：{degraded}"
            )
        else:
            self.stats_storage.setText("")

        series = data.get("series") or []
        self.stats_series.setRowCount(len(series))
        for r, row in enumerate(series):
            _set(self.stats_series, r, 0, row.get("date") or _stamp(row.get("bucket")))
            _set(self.stats_series, r, 1, f"{row.get('requests', 0):,}")
            _set(self.stats_series, r, 2, f"{row.get('success', 0):,}", theme.OK)
            err = row.get("error", 0)
            _set(self.stats_series, r, 3, f"{err:,}", theme.ERR if err else theme.TEXT_DIM)
            _set(self.stats_series, r, 4, _credit_text(row.get("credit")))
            _set(self.stats_series, r, 5, f"{row.get('total_tokens', 0) or 0:,}")

        models = data.get("models") or []
        self.stats_models.setRowCount(len(models))
        for r, row in enumerate(models):
            _set(self.stats_models, r, 0, row.get("model") or "（未记录）")
            _set(self.stats_models, r, 1, f"{row.get('requests', 0):,}")
            _set(self.stats_models, r, 2, f"{row.get('success', 0):,}", theme.OK)
            err = row.get("error", 0)
            _set(self.stats_models, r, 3, f"{err:,}", theme.ERR if err else theme.TEXT_DIM)
            _set(self.stats_models, r, 4, _credit_text(row.get("credit")))

        official = data.get("official_credits") or {}
        health = (data.get("health") or {}).get("credentials") or []
        self.stats_balance.setRowCount(len(health))
        for r, row in enumerate(health):
            identity = row.get("id")
            label, color = health_label(str(row.get("health") or ""))
            entry = official.get(identity) or {}
            _set(self.stats_balance, r, 0, row.get("name") or identity or "?")
            _set(self.stats_balance, r, 1, row.get("nickname") or "—")
            _set(self.stats_balance, r, 2, label, color)
            _set(self.stats_balance, r, 3, _credit_text(entry.get("credits") if entry else row.get("credits")))

    # ============================================================ 模型

    def _mk_models(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(10)

        bar = QHBoxLayout()
        self.btn_models_refresh = _btn("刷新")
        self.btn_models_refresh.clicked.connect(self._load_models)
        self.btn_model_toggle = _btn("启用 / 停用选中", "primary")
        self.btn_model_toggle.clicked.connect(self._toggle_model)
        bar.addWidget(self.btn_models_refresh)
        bar.addWidget(self.btn_model_toggle)
        bar.addStretch(1)
        self.models_hint = QLabel("")
        self.models_hint.setObjectName("mute")
        bar.addWidget(self.models_hint)
        box.addLayout(bar)

        card = Card("模型目录", "停用即从 /v1/models 与调度里摘掉；对外 ID 是客户端看到的模型名")
        self.models_table = _table(
            ["对外 ID", "上游 ID", "状态", "区域", "产品", "可用凭证", "绑定", "类型"], stretch=0)
        card.add(self.models_table)
        box.addWidget(card, 1)
        return page

    def _load_models(self) -> None:
        if not self.gateway_reachable():
            self._say("网关未在运行，无法读取模型目录。", "warn")
            return

        def work():
            return self.client.api("/admin/models")

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._say(self.client.describe(code, body), "error")
                return
            self._models = body.get("models") or []
            self._models_rev = int(body.get("revision") or 0)
            guard = "开" if body.get("model_capability_guard") else "关"
            self.models_hint.setText(f"修订号 {self._models_rev}　能力预检：{guard}　共 {len(self._models)} 条")
            self.models_table.setRowCount(len(self._models))
            for r, m in enumerate(self._models):
                enabled = m.get("enabled", True)
                _set(self.models_table, r, 0, m.get("public_id") or m.get("id"))
                _set(self.models_table, r, 1, m.get("upstream_id") or m.get("id"))
                _set(self.models_table, r, 2, *(_yesno(enabled, "启用", "停用")))
                _set(self.models_table, r, 3, m.get("region") or "全部")
                _set(self.models_table, r, 4, m.get("profile") or "全部")
                _set(self.models_table, r, 5, m.get("available_credentials", "—"))
                ids = m.get("credential_ids") or []
                _set(self.models_table, r, 6, len(ids) if ids else "—")
                _set(self.models_table, r, 7, "自建" if m.get("custom") else "目录")

        self._run(work, done, busy_text="正在读取模型目录…")

    def _toggle_model(self) -> None:
        row = self.models_table.currentRow()
        if row < 0 or row >= len(self._models):
            self._say("先在表里选中一个模型。", "warn")
            return
        rule = self._models[row]
        target = not bool(rule.get("enabled", True))
        source = rule.get("id")
        payload = {
            "revision": self._models_rev,
            "public_id": rule.get("public_id") or source,
            "upstream_id": rule.get("upstream_id") or source,
            "enabled": target,
            "keep_original": bool(rule.get("keep_original")),
            "region": rule.get("region"),
            "profile": rule.get("profile"),
            "credential_ids": rule.get("credential_ids") or [],
        }

        def work():
            return self.client.api(f"/admin/models/{source}", method="PUT", payload=payload)

        def done(res):
            code, body = res
            if code == 200:
                self._say(f"{payload['public_id']} 已{'启用' if target else '停用'}", "ok")
                self._load_models()
            else:
                self._say(self.client.describe(code, body), "error")
                self._load_models()

        self._run(work, done, busy_text="正在更新模型策略…")

    # ============================================================ 凭证

    def _mk_creds(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        row1 = QHBoxLayout()
        self.oauth_site = QComboBox()
        for label, value in SITE_CHOICES:
            self.oauth_site.addItem(label, value)
        row1.addWidget(QLabel("站点"))
        row1.addWidget(self.oauth_site)
        self.btn_oauth = _btn("扫码添加账号", "primary")
        self.btn_oauth.clicked.connect(self._oauth_start)
        self.btn_import = _btn("导入 .info 文件")
        self.btn_import.clicked.connect(self._import_files)
        self.btn_export = _btn("导出选中")
        self.btn_export.clicked.connect(self._export_selected)
        row1.addWidget(self.btn_oauth)
        row1.addWidget(self.btn_import)
        row1.addWidget(self.btn_export)
        row1.addStretch(1)
        self.btn_creds_refresh = _btn("刷新")
        self.btn_creds_refresh.clicked.connect(self._load_creds)
        row1.addWidget(self.btn_creds_refresh)
        box.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_cred_toggle = _btn("启用 / 停用")
        self.btn_cred_toggle.clicked.connect(lambda: self._cred_flag("enabled"))
        self.btn_cred_checkin = _btn("自动签到 开/关")
        self.btn_cred_checkin.clicked.connect(lambda: self._cred_flag("auto_checkin"))
        self.btn_cred_travel = _btn("自动旅行 开/关")
        self.btn_cred_travel.clicked.connect(lambda: self._cred_flag("auto_travel"))
        self.btn_cred_do_checkin = _btn("单独签到")
        self.btn_cred_do_checkin.clicked.connect(lambda: self._cred_action("checkin"))
        self.btn_cred_do_sync = _btn("单独同步")
        self.btn_cred_do_sync.clicked.connect(lambda: self._cred_action("sync"))
        self.btn_cred_delete = _btn("删除", "danger")
        self.btn_cred_delete.clicked.connect(self._cred_delete)
        for b in (self.btn_cred_toggle, self.btn_cred_checkin, self.btn_cred_travel,
                  self.btn_cred_do_checkin, self.btn_cred_do_sync, self.btn_cred_delete):
            row2.addWidget(b)
        row2.addStretch(1)
        self.btn_checkin_all = _btn("全部签到", "ghost")
        self.btn_checkin_all.clicked.connect(lambda: self._all_action("/admin/checkin", "签到"))
        self.btn_sync_all = _btn("全部同步", "ghost")
        self.btn_sync_all.clicked.connect(lambda: self._all_action("/admin/sync", "同步"))
        row2.addWidget(self.btn_checkin_all)
        row2.addWidget(self.btn_sync_all)
        box.addLayout(row2)

        card = Card("凭证池", "勾选多行可批量操作；「删除」会真的删除 auth/ 下的 .info 文件")
        self.creds_table = _table(
            ["账号文件", "昵称", "健康", "积分", "启用", "自动签到", "自动旅行", "令牌到期"], stretch=0)
        self.creds_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.creds_table.setMinimumHeight(260)
        card.add(self.creds_table)
        self.creds_hint = QLabel("")
        self.creds_hint.setObjectName("mute")
        card.add(self.creds_hint)
        box.addWidget(card, 1)
        return page

    def _load_creds(self) -> None:
        if not self.gateway_reachable():
            self._say("网关未在运行，无法读取凭证池。", "warn")
            return

        def work():
            return self.client.api("/admin/credentials")

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._say(self.client.describe(code, body), "error")
                return
            self._creds = body.get("credentials") or []
            self.creds_table.setRowCount(len(self._creds))
            for r, c in enumerate(self._creds):
                health = str(c.get("health") or "unknown")
                label, color = health_label(health)
                _set(self.creds_table, r, 0, c.get("name") or c.get("id") or "?")
                _set(self.creds_table, r, 1, c.get("nickname") or "—")
                extra = ""
                if health == "circuit_open":
                    extra = f"（冷却 {c.get('cooldown_remaining', 0)}s）"
                elif c.get("last_error_code"):
                    extra = f"（{c['last_error_code']}）"
                _set(self.creds_table, r, 2, label + extra, color)
                _set(self.creds_table, r, 3, _credit_text(c.get("credits")))
                _set(self.creds_table, r, 4, *(_yesno(c.get("enabled", True))))
                _set(self.creds_table, r, 5, *(_yesno(c.get("auto_checkin"))))
                _set(self.creds_table, r, 6, *(_yesno(c.get("auto_travel"))))
                expired = c.get("token_expired")
                _set(self.creds_table, r, 7, _expiry(c.get("token_expires_at")),
                     theme.ERR if expired else None)
            self.creds_hint.setText(
                f"共 {len(self._creds)} 个账号；多选后点左侧按钮可批量开关。"
                "自动签到 / 自动旅行由网关后台按开关执行。")

        self._run(work, done, busy_text="正在读取凭证池…")

    def _selected_creds(self) -> list[dict]:
        rows = sorted({i.row() for i in self.creds_table.selectedIndexes()})
        return [self._creds[r] for r in rows if 0 <= r < len(self._creds)]

    def _cred_flag(self, field: str) -> None:
        items = self._selected_creds()
        if not items:
            self._say("先在表里选中至少一个账号。", "warn")
            return
        payloads = [(c.get("id"), {field: not bool(c.get(field))}) for c in items]
        label = {"enabled": "启用状态", "auto_checkin": "自动签到", "auto_travel": "自动旅行"}[field]

        def work():
            codes = []
            for identity, payload in payloads:
                codes.append(self.client.api(f"/admin/credentials/{identity}", method="PATCH", payload=payload)[0])
            return codes

        def done(codes):
            bad = [c for c in codes if c != 200]
            if bad:
                self._say(f"{label}更新失败（HTTP {bad[0]}）", "error")
            else:
                target = "开启" if payloads[0][1][field] else "关闭"
                self._say(f"{len(payloads)} 个账号的{label}已{target}", "ok")
            self._load_creds()

        self._run(work, done, busy_text=f"正在更新{label}…")

    def _cred_action(self, action: str) -> None:
        items = self._selected_creds()
        if not items:
            self._say("先在表里选中一个账号。", "warn")
            return
        action_label = {"checkin": "签到", "sync": "同步", "travel": "旅行"}.get(action, action)

        def work():
            results = []
            for c in items:
                code, body = self.client.api(f"/admin/credentials/{c.get('id')}/{action}", method="POST", payload={}, timeout=90)
                results.append((c.get("name"), code, body))
            return results

        def done(results):
            ok = sum(1 for _n, code, _b in results if code == 200)
            if ok == len(results):
                self._say(f"{action_label}完成（{ok} 个账号）", "ok")
            else:
                first = next((r for r in results if r[1] != 200), None)
                detail = self.client.describe(first[1], first[2]) if first else ""
                self._say(f"{action_label}：{ok}/{len(results)} 成功；{detail}", "warn")
            self._load_creds()

        self._run(work, done, busy_text=f"正在{action_label}…")

    def _all_action(self, path: str, label: str) -> None:
        def work():
            return self.client.api(path, method="POST", payload={}, timeout=180)

        def done(res):
            code, body = res
            if code == 200:
                self._say(f"全部{label}完成", "ok")
                self._load_creds()
            else:
                self._say(self.client.describe(code, body), "error")

        self._run(work, done, busy_text=f"正在执行全部{label}…")

    def _cred_delete(self) -> None:
        items = self._selected_creds()
        if not items:
            self._say("先在表里选中要删除的账号。", "warn")
            return
        names = [str(c.get("name") or "") for c in items if c.get("name")]
        if not names:
            self._say("这些账号没有可删除的文件名。", "warn")
            return
        listed = "\n".join("　· " + n for n in names[:10])
        more = f"\n　…… 共 {len(names)} 个" if len(names) > 10 else ""
        if QMessageBox.question(
            self, "删除凭证",
            f"将从 auth/ 目录删除以下凭据文件，并从调度里移除：\n\n{listed}{more}\n\n"
            "删除后需要重新扫码或导入才能恢复。确定删除？",
        ) != QMessageBox.Yes:
            return

        def work():
            codes = []
            for name in names:
                codes.append((name, self.client.api(f"/admin/credentials/{name}", method="DELETE")[0]))
            return codes

        def done(codes):
            bad = [n for n, c in codes if c != 200]
            if bad:
                self._say(f"{len(codes) - len(bad)} 个已删除，{len(bad)} 个失败（首个：{bad[0]}）", "warn")
            else:
                self._say(f"已删除 {len(codes)} 个凭据", "ok")
            self._load_creds()

        self._run(work, done, busy_text="正在删除凭据…")

    def _import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的凭据文件", str(Path.home()), "凭据文件 (*.info);;所有文件 (*)")
        if not paths:
            return
        files = []
        for p in paths:
            try:
                files.append({"name": Path(p).name,
                              "content": Path(p).read_text(encoding="utf-8")})
            except Exception as exc:  # noqa: BLE001
                self._say(f"读取 {Path(p).name} 失败：{exc}", "error")
                return

        def work():
            return self.client.api("/admin/credentials/upload",
                                   method="POST", payload={"files": files, "replace": False}, timeout=60)

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._say(self.client.describe(code, body), "error")
                return
            results = body.get("results") or []
            ok = sum(1 for r in results if r.get("ok"))
            failed = [r for r in results if not r.get("ok")]
            if failed:
                reasons = "；".join(f"{r.get('name')}：{r.get('error')}" for r in failed[:3])
                self._say(f"导入 {ok}/{len(results)} 成功。失败：{reasons}", "warn")
            else:
                self._say(f"已导入 {ok} 个凭据", "ok")
            self._load_creds()

        self._run(work, done, busy_text="正在导入凭据…")

    def _export_selected(self) -> None:
        items = self._selected_creds()
        ids = [c.get("id") for c in items if c.get("id")]
        if not ids:
            self._say("先在表里选中要导出的账号。", "warn")
            return
        if QMessageBox.question(
            self, "导出凭证",
            f"将导出 {len(ids)} 个账号的凭据文件。\n\n"
            "⚠ 文件里是**明文 token**，等于账号钥匙，别通过聊天工具转发。\n\n继续？",
        ) != QMessageBox.Yes:
            return
        suffix = ".info" if len(ids) == 1 else ".zip"
        default = str(Path.home() / f"luobobox-credentials{suffix}")
        target, _ = QFileDialog.getSaveFileName(self, "保存导出文件", default,
                                                "凭据文件 (*.info *.zip)")
        if not target:
            return

        def work():
            return self.client.request("/admin/credentials/export", method="POST",
                                       payload={"ids": ids, "confirm": True}, timeout=120, raw=True)

        def done(res):
            code, body, _headers = res
            if code != 200 or not isinstance(body, (bytes, bytearray)):
                self._say(self.client.describe(code, body), "error")
                return
            try:
                Path(target).write_bytes(body)
            except Exception as exc:  # noqa: BLE001
                self._say(f"写文件失败：{exc}", "error")
                return
            self._say(f"已导出 {len(ids)} 个凭据 → {target}", "ok")

        self._run(work, done, busy_text="正在导出凭据…")

    # ------------------------------------------------------------ 扫码登录

    def _oauth_start(self) -> None:
        if not self.gateway_reachable():
            self._say("网关未在运行，无法发起扫码登录。", "warn")
            return
        site = self.oauth_site.currentData() or "cn"

        def work():
            return self.client.api(f"/admin/oauth/start?site={site}", method="POST", timeout=30)

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._say(self.client.describe(code, body), "error")
                return
            self._oauth_login_id = str(body.get("login_id") or "")
            link = body.get("verification_uri") or ""
            if not self._oauth_login_id or not link:
                self._say("网关没有返回有效的授权链接", "error")
                return
            QDesktopServices.openUrl(QUrl(link))
            self._say("已打开浏览器授权页，请在其中完成扫码/登录；完成后这里会自动入库。", "info")
            self._oauth_timer.start()

        self._run(work, done, busy_text="正在发起扫码登录…")

    def _oauth_poll(self) -> None:
        if not self._oauth_login_id:
            self._oauth_timer.stop()
            return

        def work():
            return self.client.api(f"/admin/oauth/poll?login_id={self._oauth_login_id}", timeout=20)

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._oauth_timer.stop()
                self._say(self.client.describe(code, body), "error")
                return
            if not body.get("done"):
                return  # 继续等
            self._oauth_timer.stop()
            self._oauth_login_id = ""
            if body.get("error"):
                self._say(f"扫码登录失败：{body['error']}", "error")
            else:
                who = body.get("nickname") or body.get("uid") or "账号"
                self._say(f"已添加账号：{who}", "ok")
                self._load_creds()

        # 轮询很频繁，不进 busy 队列，失败也不刷错误横幅
        self.ctx.runner.run(work, done, lambda _m: None)

    # ============================================================ 日志

    def _mk_logs(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("类型"))
        self.log_kind = QComboBox()
        self.log_kind.addItem("请求", "request")
        self.log_kind.addItem("运行", "runtime")
        self.log_kind.addItem("管理", "admin")
        self.log_kind.currentIndexChanged.connect(self._reload_logs)
        bar.addWidget(self.log_kind)

        bar.addWidget(QLabel("条数"))
        self.log_limit = QSpinBox()
        self.log_limit.setRange(1, 200)
        self.log_limit.setValue(50)
        bar.addWidget(self.log_limit)

        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("关键字（模型 / 账号 / 状态…）")
        self.log_search.returnPressed.connect(self._reload_logs)
        bar.addWidget(self.log_search, 1)

        self.btn_logs_refresh = _btn("刷新")
        self.btn_logs_refresh.clicked.connect(self._reload_logs)
        self.btn_logs_more = _btn("加载更多")
        self.btn_logs_more.clicked.connect(self._logs_more)
        self.btn_logs_clear = _btn("清理", "danger")
        self.btn_logs_clear.clicked.connect(self._logs_clear)
        for b in (self.btn_logs_refresh, self.btn_logs_more, self.btn_logs_clear):
            bar.addWidget(b)
        box.addLayout(bar)

        card = Card("审计记录", "点任意一行看明细（含提示词预览、错误诊断）")
        self.logs_table = _table(
            ["时间", "模型", "状态", "耗时", "输入", "输出", "积分", "凭据", "错误"], stretch=1)
        self.logs_table.itemSelectionChanged.connect(self._logs_detail)
        self.logs_table.setMinimumHeight(240)
        card.add(self.logs_table)
        self.logs_hint = QLabel("")
        self.logs_hint.setObjectName("mute")
        card.add(self.logs_hint)
        box.addWidget(card, 1)

        self.logs_detail = QPlainTextEdit()
        self.logs_detail.setReadOnly(True)
        self.logs_detail.setMaximumHeight(190)
        self.logs_detail.setPlaceholderText("选中一行后在此显示请求明细…")
        box.addWidget(self.logs_detail)
        return page

    def _reload_logs(self) -> None:
        self._log_cursors = []
        self._load_logs(reset=True)

    def _logs_more(self) -> None:
        if not self._log_cursors:
            self._say("没有更多了 —— 先点「刷新」重新开始。", "warn")
            return
        self._load_logs(reset=False)

    def _load_logs(self, *, reset: bool) -> None:
        if not self.gateway_reachable():
            self._say("网关未在运行，无法读取日志。", "warn")
            return
        kind = self.log_kind.currentData() or "request"
        limit = int(self.log_limit.value())
        search = self.log_search.text().strip()
        cursor = "" if reset else (self._log_cursors[-1] if self._log_cursors else "")
        path = f"/admin/logs?kind={kind}&limit={limit}"
        if search:
            from urllib.parse import quote

            path += f"&search={quote(search)}"
        if cursor:
            from urllib.parse import quote

            path += f"&cursor={quote(cursor)}"

        def work():
            return self.client.api(path, timeout=30)

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._say(self.client.describe(code, body), "error")
                return
            rows = body.get("items") or []
            if reset:
                self._log_rows = rows
            else:
                self._log_rows = self._log_rows + rows
            nxt = body.get("next_cursor")
            if nxt:
                self._log_cursors.append(str(nxt))
            self._fill_logs()
            self.logs_hint.setText(
                f"本次 {len(rows)} 条，累计 {len(self._log_rows)} 条"
                + ("（还有更多，可点「加载更多」）" if body.get("has_more") else "（已到末尾）"))

        self._run(work, done, busy_text="正在读取日志…")

    def _fill_logs(self) -> None:
        self.logs_table.setRowCount(len(self._log_rows))
        for r, row in enumerate(self._log_rows):
            outcome = str(row.get("outcome") or "")
            color = {"success": theme.OK, "error": theme.ERR,
                     "cancelled": theme.WARN}.get(outcome, theme.TEXT_DIM)
            _set(self.logs_table, r, 0, _stamp(row.get("started_at")))
            _set(self.logs_table, r, 1, row.get("model") or "—")
            status = f"{row.get('status_code') or '—'} {outcome}".strip()
            _set(self.logs_table, r, 2, status, color)
            dur = row.get("duration_ms")
            _set(self.logs_table, r, 3, f"{dur / 1000:.2f}s" if isinstance(dur, (int, float)) else "—")
            _set(self.logs_table, r, 4, f"{row.get('input_tokens', 0) or 0:,}")
            _set(self.logs_table, r, 5, f"{row.get('output_tokens', 0) or 0:,}")
            _set(self.logs_table, r, 6, _credit_text(row.get("credit")))
            ident = str(row.get("credential") or "")
            _set(self.logs_table, r, 7, ident[:8] if ident else "—")
            _set(self.logs_table, r, 8, row.get("error_code") or "", theme.ERR if row.get("error_code") else None)

    def _logs_detail(self) -> None:
        row = self.logs_table.currentRow()
        if row < 0 or row >= len(self._log_rows):
            return
        record = self._log_rows[row]
        rid = record.get("event_id") or record.get("id")
        if not rid:
            self.logs_detail.setPlainText(json.dumps(record, ensure_ascii=False, indent=2))
            return

        def work():
            return self.client.api(f"/admin/logs/{rid}", timeout=20)

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self.logs_detail.setPlainText(self.client.describe(code, body))
                return
            self.logs_detail.setPlainText(json.dumps(body, ensure_ascii=False, indent=2))

        self.ctx.runner.run(work, done, lambda m: self.logs_detail.setPlainText(f"读取明细失败：{m}"))

    def _logs_clear(self) -> None:
        choice = QMessageBox.question(
            self, "清理日志",
            "「是」= 只清空请求明细（保留统计汇总）\n"
            "「否」= 取消\n\n"
            "（彻底清空全部日志与统计请用下面的按钮）",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if choice == QMessageBox.Cancel:
            return
        if choice == QMessageBox.Yes:
            scope, payload = "details", {"scope": "details"}
        else:
            if QMessageBox.question(
                self, "彻底清空",
                "将同时清空**全部日志与统计**，不可恢复。确定继续？",
            ) != QMessageBox.Yes:
                return
            payload = {"scope": "all", "confirmation": CLEAR_CONFIRMATION,
                       "api_key": str(self.ctx.config.get("gateway.api_key", ""))}
            scope = "all"

        def work():
            return self.client.api("/admin/logs/clear", method="POST", payload=payload, timeout=120)

        def done(res):
            code, body = res
            if code == 200:
                self._say("日志已清理" if scope == "details" else "全部日志与统计已清空", "ok")
                self._reload_logs()
            else:
                self._say(self.client.describe(code, body), "error")

        self._run(work, done, busy_text="正在清理日志…")

    # ============================================================ 设置

    def _mk_settings(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(8)

        bar = QHBoxLayout()
        self.btn_settings_refresh = _btn("刷新")
        self.btn_settings_refresh.clicked.connect(self._load_settings)
        bar.addWidget(self.btn_settings_refresh)
        bar.addStretch(1)
        self.settings_hint = QLabel("")
        self.settings_hint.setObjectName("mute")
        bar.addWidget(self.settings_hint)
        box.addLayout(bar)

        card = Card("网关配置",
                    "来源为 cli / environment 的项被锁定（灰显），请改启动参数或 .env；"
                    "「热更新」项改完立即生效，「重启」项需重启网关")
        self.settings_table = _table(["配置", "当前值", "来源", "生效", "锁定"], stretch=1)
        self.settings_table.itemSelectionChanged.connect(self._settings_selected)
        card.add(self.settings_table)
        box.addWidget(card, 1)

        edit = QHBoxLayout()
        edit.addWidget(QLabel("编辑选中项"))
        self.settings_value = QLineEdit()
        self.settings_value.setPlaceholderText("选中一行后在此填新值（布尔填 true / false）")
        self.settings_value.returnPressed.connect(self._settings_apply)
        edit.addWidget(self.settings_value, 1)
        self.btn_settings_apply = _btn("应用", "primary")
        self.btn_settings_apply.clicked.connect(self._settings_apply)
        edit.addWidget(self.btn_settings_apply)
        box.addLayout(edit)

        self.settings_audit = QLabel("")
        self.settings_audit.setObjectName("mute")
        self.settings_audit.setWordWrap(True)
        box.addWidget(self.settings_audit)
        return page

    def _load_settings(self) -> None:
        if not self.gateway_reachable():
            self._say("网关未在运行，无法读取配置。", "warn")
            return

        def work():
            return self.client.api("/admin/settings")

        def done(res):
            code, body = res
            if code != 200 or not isinstance(body, dict):
                self._say(self.client.describe(code, body), "error")
                return
            self._settings_items = body.get("items") or []
            self._settings_rev = int(body.get("revision") or 0)
            self.settings_table.setRowCount(len(self._settings_items))
            for r, item in enumerate(self._settings_items):
                value = item.get("value")
                if item.get("type") == "secret":
                    shown = "••••••"
                elif isinstance(value, bool):
                    shown = "true" if value else "false"
                else:
                    shown = "—" if value is None else str(value)
                _set(self.settings_table, r, 0, item.get("label") or item.get("key"))
                _set(self.settings_table, r, 1, shown)
                _set(self.settings_table, r, 2, item.get("source") or "default")
                _set(self.settings_table, r, 3, "重启" if item.get("mode") == "restart" else "热更新")
                locked = item.get("locked")
                _set(self.settings_table, r, 4, "锁定" if locked else "可改",
                     theme.WARN if locked else theme.OK)
            audit = body.get("audit") or {}
            self.settings_audit.setText(
                f"审计存储：逻辑 {audit.get('logical_bytes', 0) / 1048576:.1f} MB　"
                f"保留 {audit.get('retention_days', '?')} 天　"
                f"诊断字节 {audit.get('preview_limit', '?')}　"
                f"降级：{'是' if audit.get('degraded') else '否'}")
            self.settings_hint.setText(f"修订号 {self._settings_rev}")

        self._run(work, done, busy_text="正在读取配置…")

    def _settings_selected(self) -> None:
        row = self.settings_table.currentRow()
        if row < 0 or row >= len(self._settings_items):
            return
        item = self._settings_items[row]
        value = item.get("value")
        if isinstance(value, bool):
            self.settings_value.setText("true" if value else "false")
        elif value is None:
            self.settings_value.clear()
        else:
            self.settings_value.setText(str(value))
        if item.get("locked"):
            self.settings_value.setPlaceholderText("该项由 CLI / 环境变量锁定，不可在此修改")
        else:
            self.settings_value.setPlaceholderText("填新值后回车或点「应用」")

    def _settings_apply(self) -> None:
        row = self.settings_table.currentRow()
        if row < 0 or row >= len(self._settings_items):
            self._say("先在表里选中要修改的配置项。", "warn")
            return
        item = self._settings_items[row]
        if item.get("locked"):
            self._say(f"{item.get('label')} 由启动来源锁定，请改 CLI 参数或 .env。", "warn")
            return
        raw = self.settings_value.text().strip()
        kind = item.get("type")
        try:
            if kind == "boolean":
                if raw.lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    raise ValueError("布尔值请填 true 或 false")
                value = raw.lower() in ("true", "1", "yes", "on")
            elif kind == "integer":
                value = int(raw)
            elif kind == "number":
                value = float(raw)
            else:
                value = raw
        except ValueError as exc:
            self._say(f"值不合法：{exc}", "error")
            return

        key = item.get("key")
        payload = {"revision": self._settings_rev, "values": {key: value}}

        def work():
            return self.client.api("/admin/settings", method="PATCH", payload=payload, timeout=30)

        def done(res):
            code, body = res
            if code != 200:
                self._say(self.client.describe(code, body), "error")
                self._load_settings()
                return
            note = "（重启网关后生效）" if item.get("mode") == "restart" else ""
            self._say(f"{item.get('label')} 已更新{note}", "ok")
            self._load_settings()
            self.ctx.refresh_health()

        self._run(work, done, busy_text="正在保存配置…")

    # ------------------------------------------------------------ 生命周期

    def stop(self) -> None:
        self._oauth_timer.stop()
