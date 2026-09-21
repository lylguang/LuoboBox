"""额度消耗独立标签页：基于本地余额变化反推的消耗统计 UI。

设计：完全独立于主窗口源码。由 app.py 在 MainWindow 创建后调用
`window.add_tab("usage", build_usage_tab(ctx), "额度消耗")` 注入
（走 add_tab 是为了登记进 _tab_index，这样快捷键 / 命令面板也能跳到这一页）。

数据来源：usage 模块 —— 持久化每次健康刷新的余额快照，
用"首次记录余额 − 当前余额"反推累计消耗、"当日首次余额 − 当前余额"反推今日消耗。
纯本地、客观，不依赖改上游网关，数据仅存本机 data_dir()/usage.json。

图表：手写 QPainter（见 ui/charts.py），不引第三方绘图库 ——
为一个柱状图把打包体积推高几十 MB 不划算。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import usage
from .context import AppContext
from .ui import theme
from .ui.charts import BarChart, Donut, Legend
from .ui.widgets import Card, EmptyState, KeyValue, Toast


def health_label(health: str) -> tuple[str, str]:
    """健康度 → (中文标签, 颜色)。

    ★ 必须是函数，不能是模块级 dict。dict 会在 import 那一刻
    把 `theme.OK` 的**值**拷进去，等于冻住颜色 —— 换到浅色主题后
    表格里的"异常"还是深色主题那种浅粉红（#F09595），白底上几乎看不见。
    """
    return {
        "ready": ("正常", theme.OK),
        "expired": ("已过期", theme.ERR),
        "circuit_open": ("已熔断", theme.WARN),
        "error": ("异常", theme.ERR),
        "disabled": ("已停用", theme.TEXT_MUTE),
    }.get(health, (health, theme.TEXT_DIM))


def _fmt(value) -> str:
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


def _extract_balance(value):
    """从网关多变的余额形态里抠出数字（与 usage 模块一致）。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for k in ("credits", "balance", "remain", "remaining"):
            v = value.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class UsageTab(QWidget):
    RANGES: tuple[tuple[str, int], ...] = (("近 7 天", 7), ("近 30 天", 30))

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.usage_data: dict = {}
        self._last_summary: dict | None = None
        self._last_snap = None
        self._build()
        # 每次健康刷新后自动更新
        ctx.health_ready.connect(self.refresh)
        # 换肤回刷：图表每次 paint 都现取颜色，但图例/表格的颜色是"传进去"的
        theme.on_change(self._on_theme)
        # 立刻用当前快照刷一次（即使网关还没响应也好歹画个壳）
        self.refresh(ctx.health)

    # ---------------------------------------------------------- 构建

    def _build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(12)

        self.toast = Toast()
        box.addWidget(self.toast)

        # ---- 概览：环形 + 四个数
        card = Card(
            "额度消耗概览",
            "基于每次健康刷新的余额变化反推（自萝卜盒开始记录以来累计；余额上升不计入）。"
            "数据仅存本机 data_dir()/usage.json，不上传任何上游。",
        )
        row = QHBoxLayout()
        row.setSpacing(18)

        self.donut = Donut(136)
        row.addWidget(self.donut, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(8)
        self.kv_remain = KeyValue("总剩余额度", "—", mono=True)
        self.kv_consumed = KeyValue("累计消耗", "—", mono=True)
        self.kv_today = KeyValue("今日消耗", "—", mono=True)
        self.kv_rate = KeyValue("消耗速率", "—", mono=True)
        for w in (self.kv_remain, self.kv_consumed, self.kv_today, self.kv_rate):
            col.addWidget(w)
        self.donut_legend = Legend()
        col.addWidget(self.donut_legend)
        col.addStretch(1)
        row.addLayout(col, 1)
        card.add_layout(row)
        box.addWidget(card)

        # ---- 消耗趋势
        trend = Card("消耗趋势", "柱高 = 当天的余额下降量（充值会让当天归零）。鼠标悬停看具体数值。")
        bar = QHBoxLayout()
        bar.addWidget(QLabel("时间范围"))
        self.cmb_range = QComboBox()
        for label, days in self.RANGES:
            self.cmb_range.addItem(label, days)
        self.cmb_range.currentIndexChanged.connect(lambda *_: self._render_chart())
        bar.addWidget(self.cmb_range)
        bar.addStretch(1)
        trend.add_layout(bar)
        self.chart = BarChart()
        trend.add(self.chart)
        box.addWidget(trend)

        # ---- 凭证消耗明细
        cred = Card(
            "凭证消耗明细",
            "每个上游账号的余额与累计消耗。每 15 秒自动刷新；"
            "第三列『积分(余额)』为网关当前余额，第四列『已消耗』为萝卜盒本地反推的用量。",
        )
        self.cred_table = QTableWidget(0, 5)
        self.cred_table.setHorizontalHeaderLabels(
            ["账号", "健康", "积分(余额)", "已消耗", "状态"])
        self.cred_table.verticalHeader().setVisible(False)
        self.cred_table.setSelectionMode(QTableWidget.NoSelection)
        self.cred_table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.cred_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        # 空态而不是"一行灰字"：灰字像加载坏了，空态能说清原因
        self.cred_empty = EmptyState(
            "还没有可统计的凭证",
            "消耗是从网关余额反推出来的，所以要先跑起来："
            "启动网关 → 到「管理台 → 凭证」添加账号。",
            glyph="📊",
            action=("去看凭证池", self._goto_overview),
        )
        self.cred_stack = QStackedWidget()
        self.cred_stack.addWidget(self.cred_table)   # 0 = 有数据
        self.cred_stack.addWidget(self.cred_empty)   # 1 = 空态
        self.cred_stack.setMinimumHeight(180)
        cred.add(self.cred_stack)
        box.addWidget(cred)

        box.addStretch(1)
        scroll.setWidget(page)
        self.scroll = scroll

        # ★ 必须把 scroll 挂到 self 上。只写 `self.scroll = scroll` 是不够的 ——
        #   QScrollArea 没有父控件就不会被显示，整页会渲染成一块空白
        #   （tab 本身有内容，但内容全在一个看不见的游离控件里）。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll)

    def _goto_overview(self) -> None:
        """空态里的按钮：借主窗口的导航跳回概览页。"""
        win = self.window()
        if hasattr(win, "goto_tab"):
            win.goto_tab("overview")

    # ---------------------------------------------------------- 刷新

    def refresh(self, snap) -> None:
        self._last_snap = snap
        try:
            self.usage_data = usage.record_snapshot(snap) or {}
        except Exception:  # noqa: BLE001
            self.usage_data = self.usage_data or {}
        data = self.usage_data
        s = usage.summary(data)
        self._last_summary = s

        self.kv_remain.set_value(_fmt(s["total_remaining"]))
        self.kv_consumed.set_value(_fmt(s["total_consumed"]))
        self.kv_today.set_value(_fmt(s["today_consumed"]))
        self.kv_rate.set_value(f"{_fmt(s['rate'])} / 小时" if s["rate"] > 0 else "—")

        self._render_donut(s)
        self._render_chart()
        self._fill_table(snap, data)

    def _render_donut(self, s: dict) -> None:
        remain = float(s.get("total_remaining") or 0.0)
        used = float(s.get("total_consumed") or 0.0)
        total = remain + used
        if total <= 0:
            self.donut.set_data([], "—", "暂无数据")
            self.donut_legend.set_items([])
            return
        pct = used / total * 100
        self.donut.set_data(
            [("已用", used, theme.ACCENT), ("剩余", remain, theme.BORDER_HI)],
            f"{pct:.0f}%", "已用占比")
        self.donut_legend.set_items([
            ("已用", _fmt(used), theme.ACCENT),
            ("剩余", _fmt(remain), theme.BORDER_HI),
        ])

    def _render_chart(self) -> None:
        days = int(self.cmb_range.currentData() or 7)
        has_data = bool((self.usage_data or {}).get("accounts"))
        series = usage.daily_series(self.usage_data or {}, days)
        self.chart.set_data(series if has_data else [])

    def _on_theme(self) -> None:
        """换肤回刷（图表本身 paint 时取色，但图例与表格的颜色是传入的）。"""
        if self._last_summary is not None:
            self._render_donut(self._last_summary)
        if self._last_snap is not None:
            self._fill_table(self._last_snap, self.usage_data or {})
        self.chart.update()
        self.donut.update()

    # ---------------------------------------------------------- 明细表

    def _fill_table(self, snap, data) -> None:
        rows = getattr(snap, "credentials", None) or []
        snap_ok = getattr(snap, "ok", False)
        if not rows:
            self.cred_table.setRowCount(0)
            self.cred_empty.set_text(
                "还没有可统计的凭证" if snap_ok else "读不到凭证（网关未运行）",
                "消耗是从网关余额反推出来的，所以要先跑起来："
                "启动网关 → 到「管理台 → 凭证」添加账号。"
                if snap_ok else
                "启动网关后这一页会自动出现数据。")
            self.cred_stack.setCurrentIndex(1)
            return

        self.cred_stack.setCurrentIndex(0)
        self.cred_table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            # 逐行兜底：某一行字段形态异常只标记这一行，不拖垮整张表
            # （主窗口早期就是被一个 dict 拖垮整张表，这里沿用同样的保护）
            try:
                name = str(item.get("name") or item.get("id") or "?")
                health = str(item.get("health") or "unknown")
                label, color = health_label(health)

                self.cred_table.setItem(r, 0, QTableWidgetItem(name))
                h = QTableWidgetItem(label)
                try:
                    h.setForeground(QColor(color))
                except Exception:  # noqa: BLE001
                    pass
                self.cred_table.setItem(r, 1, h)
                self.cred_table.setItem(
                    r, 2, QTableWidgetItem(_fmt(_extract_balance(item.get("credits")))))

                c_total, c_today = (
                    usage.account_consumed(data, name) if name != "?" else (0.0, 0.0))
                ci = QTableWidgetItem(_fmt(c_total) if c_total else "—")
                if c_today:
                    ci.setToolTip(f"今日消耗 {_fmt(c_today)}")
                self.cred_table.setItem(r, 3, ci)

                extra = ""
                if health == "circuit_open":
                    extra = f"冷却 {item.get('cooldown_remaining', 0)}s"
                elif item.get("last_error_code"):
                    extra = str(item["last_error_code"])
                elif item.get("enabled") is False:
                    extra = "已从调度中排除"
                self.cred_table.setItem(r, 4, QTableWidgetItem(extra))
            except Exception:  # noqa: BLE001
                try:
                    self.cred_table.setItem(
                        r, 0, QTableWidgetItem(
                            str(item.get("name") or item.get("id") or "?")))
                    self.cred_table.setItem(r, 4, QTableWidgetItem("该行数据无法解析"))
                except Exception:  # noqa: BLE001
                    pass


def build_usage_tab(ctx: AppContext) -> QWidget:
    """供 app.py 注入为新 tab 的工厂函数。"""
    return UsageTab(ctx)
