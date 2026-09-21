"""额度消耗独立标签页：基于本地余额变化反推的消耗统计 UI。

设计：完全独立于主窗口源码。由 app.py 在 MainWindow 创建后把本模块构建的
widget 注入为一个新 tab。这样主窗口（ui/main_window.py）无需改动。

数据来源：usage 模块（luobox/usage.py）——持久化每次健康刷新的余额快照，
用“首次记录余额 − 当前余额”反推累计消耗、“当日首次余额 − 当前余额”反推今日消耗。
纯本地、客观，不依赖改上游网关，数据仅存本机 data_dir()/usage.json。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import usage
from .context import AppContext
from .ui import theme
from .ui.widgets import Card, KeyValue, Toast

_HEALTH_LABEL = {
    "ready": ("正常", theme.OK),
    "expired": ("已过期", theme.ERR),
    "circuit_open": ("已熔断", theme.WARN),
    "error": ("异常", theme.ERR),
    "disabled": ("已停用", theme.TEXT_MUTE),
}


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
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.usage_data = None
        self._build()
        # 每次健康刷新后自动更新
        ctx.health_ready.connect(self.refresh)
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

        # ---- 汇总卡片
        card = Card(
            "额度消耗概览",
            "基于每次健康刷新的余额变化反推（自萝卜盒开始记录以来累计；余额上升不计入）。"
            "数据仅存本机 data_dir()/usage.json，不上传任何上游。",
        )
        self.kv_remain = KeyValue("总剩余额度", "—", mono=True)
        self.kv_consumed = KeyValue("累计消耗", "—", mono=True)
        self.kv_today = KeyValue("今日消耗", "—", mono=True)
        self.kv_rate = KeyValue("消耗速率", "—", mono=True)
        for w in (self.kv_remain, self.kv_consumed, self.kv_today, self.kv_rate):
            card.add(w)
        box.addWidget(card)

        # ---- 凭证消耗明细
        cred = Card(
            "凭证消耗明细",
            "每个上游账号的余额与累计消耗。每 15 秒自动刷新；"
            "第二列『积分(余额)』为网关当前余额，第四列『已消耗』为萝卜盒本地反推的用量。",
        )
        self.cred_table = QTableWidget(0, 5)
        self.cred_table.setHorizontalHeaderLabels(
            ["账号", "健康", "积分(余额)", "已消耗", "状态"])
        self.cred_table.verticalHeader().setVisible(False)
        self.cred_table.setSelectionMode(QTableWidget.NoSelection)
        self.cred_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.cred_table.setMinimumHeight(160)
        hh = self.cred_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        cred.add(self.cred_table)
        box.addWidget(cred)

        box.addStretch(1)
        scroll.setWidget(page)
        self.scroll = scroll

    # ---------------------------------------------------------- 刷新
    def refresh(self, snap) -> None:
        try:
            self.usage_data = usage.record_snapshot(snap)
        except Exception:  # noqa: BLE001
            pass
        data = self.usage_data or {}
        s = usage.summary(data)
        self.kv_remain.set_value(_fmt(s["total_remaining"]))
        self.kv_consumed.set_value(_fmt(s["total_consumed"]))
        self.kv_today.set_value(_fmt(s["today_consumed"]))
        self.kv_rate.set_value(
            f"{_fmt(s['rate'])} / 小时" if s["rate"] > 0 else "—")
        self._fill_table(snap, data)

    def _fill_table(self, snap, data) -> None:
        rows = getattr(snap, "credentials", None) or []
        snap_ok = getattr(snap, "ok", False)
        self.cred_table.setRowCount(len(rows) if rows else 1)
        for r, item in enumerate(rows):
            # 逐行兜底：某一行字段形态异常只标记这一行，不拖垮整张表
            # （主窗口早期就是被一个 dict 拖垮整张表，这里沿用同样的保护）
            try:
                name = str(item.get("name") or item.get("id") or "?")
                health = str(item.get("health") or "unknown")
                label, color = _HEALTH_LABEL.get(health, (health, theme.TEXT_DIM))

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
        if not rows:
            self.cred_table.setItem(
                0, 0,
                QTableWidgetItem("暂无凭证" if snap_ok else "网关未运行或尚未连接"))


def build_usage_tab(ctx: AppContext) -> QWidget:
    """供 app.py 注入为新 tab 的工厂函数。"""
    return UsageTab(ctx)
