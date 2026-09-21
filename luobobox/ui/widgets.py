"""可复用的小部件。"""

from __future__ import annotations

import os
import platform

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


class StatusDot(QWidget):
    """状态圆点：一眼看出运行/停止/异常。"""

    def __init__(self, color: str = theme.TEXT_MUTE, size: int = 12, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size + 6, size + 6)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, D102
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 外圈柔光
        glow = QColor(self._color)
        glow.setAlpha(60)
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, self._size + 6, self._size + 6)
        p.setBrush(self._color)
        p.drawEllipse(3, 3, self._size, self._size)


class Card(QFrame):
    """带标题的内容卡片。"""

    def __init__(self, title: str = "", subtitle: str = "", parent=None,
                 object_name: str = "card"):
        super().__init__(parent)
        self.setObjectName(object_name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        if title:
            header = QHBoxLayout()
            header.setSpacing(8)
            self.title_label = QLabel(title)
            self.title_label.setObjectName("h3")
            header.addWidget(self.title_label)
            header.addStretch(1)
            self.header_extra = QHBoxLayout()
            self.header_extra.setSpacing(6)
            header.addLayout(self.header_extra)
            outer.addLayout(header)

            if subtitle:
                self.subtitle_label = QLabel(subtitle)
                self.subtitle_label.setObjectName("mute")
                self.subtitle_label.setWordWrap(True)
                outer.addWidget(self.subtitle_label)

        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def add_header_widget(self, widget: QWidget) -> QWidget:
        self.header_extra.addWidget(widget)
        return widget


class KeyValue(QWidget):
    """一行「标签 ─ 值」，值可等宽、可点选复制。"""

    def __init__(self, key: str, value: str = "—", mono: bool = False,
                 copyable: bool = False, value_color: str | None = None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.key_label = QLabel(key)
        self.key_label.setObjectName("dim")
        self.key_label.setFixedWidth(96)
        row.addWidget(self.key_label)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("mono" if mono else "h3")
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.value_label.setWordWrap(True)
        self.value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if value_color:
            self.value_label.setStyleSheet(f"color: {value_color};")
        row.addWidget(self.value_label, 1)

        if copyable:
            btn = QPushButton("复制")
            btn.setObjectName("ghost")
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(self._copy)
            row.addWidget(btn)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text or "—")

    def value(self) -> str:
        return self.value_label.text()

    def set_color(self, color: str) -> None:
        self.value_label.setStyleSheet(f"color: {color};")

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.value_label.text())


class Pill(QLabel):
    """小圆角标签，用于状态、数量、倍率。"""

    def __init__(self, text: str = "", color: str = theme.TEXT_DIM, parent=None):
        super().__init__(text, parent)
        self.setObjectName("pill")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(22)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        bg = QColor(color)
        bg.setAlpha(34)
        self.setStyleSheet(
            f"background-color: rgba({bg.red()},{bg.green()},{bg.blue()},0.16);"
            f"color: {color}; border: 1px solid rgba({bg.red()},{bg.green()},{bg.blue()},0.35);"
            "border-radius: 11px; padding: 0 9px; font-size: 11px;"
        )

    def set_text(self, text: str, color: str | None = None) -> None:
        self.setText(text)
        if color:
            self.set_color(color)


class CopyField(QWidget):
    """只读输入框 + 复制按钮，用于地址和 Key。"""

    def __init__(self, value: str = "", masked: bool = False, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        from PySide6.QtWidgets import QLineEdit

        self.edit = QLineEdit(value)
        self.edit.setReadOnly(True)
        self.edit.setObjectName("mono")
        if masked:
            self.edit.setEchoMode(QLineEdit.Password)
        row.addWidget(self.edit, 1)

        self._masked = masked
        self._raw = value

        self.toggle = QPushButton("显示")
        self.toggle.setObjectName("ghost")
        self.toggle.setFixedHeight(28)
        self.toggle.setVisible(masked)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.clicked.connect(self._toggle)
        row.addWidget(self.toggle)

        copy = QPushButton("复制")
        copy.setObjectName("ghost")
        copy.setFixedHeight(28)
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._raw))
        row.addWidget(copy)

    def _toggle(self) -> None:
        from PySide6.QtWidgets import QLineEdit

        show = self.edit.echoMode() == QLineEdit.Password
        self.edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self.toggle.setText("隐藏" if show else "显示")

    def set_value(self, value: str) -> None:
        self._raw = value
        self.edit.setText(value)


class BigButton(QPushButton):
    """主操作按钮（带副标题）。"""

    clicked_action = Signal()

    def __init__(self, title: str, subtitle: str = "", primary: bool = False,
                 danger: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("primary" if primary else ("danger" if danger else ""))
        self.setMinimumHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 8, 14, 8)
        box.setSpacing(1)
        self._title = QLabel(title)
        self._title.setAttribute(Qt.WA_TransparentForMouseEvents)
        f = QFont()
        f.setPointSize(10)
        f.setWeight(QFont.DemiBold)
        self._title.setFont(f)
        self._title.setAlignment(Qt.AlignCenter)
        box.addWidget(self._title)
        if subtitle:
            self._sub = QLabel(subtitle)
            self._sub.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._sub.setAlignment(Qt.AlignCenter)
            self._sub.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
            box.addWidget(self._sub)

    def set_title(self, text: str) -> None:
        self._title.setText(text)


class Separator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("separator")
        self.setFixedHeight(1)
        self.setFrameShape(QFrame.NoFrame)


def link_label(text: str, url: str, color: str = theme.INFO) -> QLabel:
    """一行外链标签：蓝色下划线 + 手型光标，点击交给系统浏览器。

    用 QLabel 富文本而不是 QPushButton —— 按钮会抢视线（这里只是备注性的入口），
    而且 `setOpenExternalLinks(True)` 是 Qt 官方路子，不用自己处理 QUrl。
    """
    lbl = QLabel(
        f'<a href="{url}" style="color:{color};text-decoration:underline;">{text}</a>'
    )
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
    lbl.setCursor(Qt.PointingHandCursor)
    lbl.setToolTip(url)
    return lbl


def _python_arch_label() -> str:
    """按本机 CPU 架构给出该下哪种安装包。

    装错位数是"装完还是用不了"的头号原因（32 位包里没有真解释器）。
    环境变量优先于 platform.machine()：后者在个别精简/虚拟化环境下
    会返回空串或 "x86"，反而是错的。
    """
    machine = (os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine() or "").lower()
    return "Windows ARM64" if machine == "arm64" else "Windows x86-64"


def python_download_tip() -> QWidget:
    """「没装 Python？」兜底引导：下载入口 + 装完该做什么。

    向导和设置页共用 —— 探测失败时如果只丢一句"没找到 Python"，
    用户下一步就是去搜索引擎碰运气（还可能装到 32 位 / Microsoft Store 版）。
    """
    from ..paths import PYTHON_DOWNLOADS

    box = QWidget()
    col = QVBoxLayout(box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(2)

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    ask = QLabel("没装 Python？下载安装包：")
    ask.setObjectName("mute")
    row.addWidget(ask)
    for i, (label, url) in enumerate(PYTHON_DOWNLOADS):
        if i:
            dot = QLabel("·")
            dot.setObjectName("mute")
            row.addWidget(dot)
        row.addWidget(link_label(label, url))
    row.addStretch(1)
    col.addLayout(row)

    note = QLabel(f"安装时勾选「Add python.exe to PATH」；包选【{_python_arch_label()}】；"
                  "装完点「自动探测」。")
    note.setObjectName("mute")
    note.setWordWrap(True)
    col.addWidget(note)

    pip = QLabel("已有 Python 只缺依赖：pip install fastapi uvicorn httpx")
    pip.setObjectName("mono")
    pip.setTextInteractionFlags(Qt.TextSelectableByMouse)
    pip.setToolTip("点一下拖选，Ctrl+C 复制")
    col.addWidget(pip)
    return box


def hspacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w


def section_title(text: str, hint: str = "") -> QWidget:
    w = QWidget()
    box = QVBoxLayout(w)
    box.setContentsMargins(0, 6, 0, 0)
    box.setSpacing(2)
    label = QLabel(text)
    label.setObjectName("h2")
    box.addWidget(label)
    if hint:
        sub = QLabel(hint)
        sub.setObjectName("mute")
        sub.setWordWrap(True)
        box.addWidget(sub)
    return w


class Toast(QLabel):
    """内联提示条，替代弹窗做轻量反馈。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setVisible(False)
        self.setContentsMargins(12, 8, 12, 8)

    def show_message(self, text: str, level: str = "info", timeout_ms: int = 6000) -> None:
        color = {"info": theme.INFO, "ok": theme.OK, "warn": theme.WARN,
                 "error": theme.ERR}.get(level, theme.INFO)
        c = QColor(color)
        self.setStyleSheet(
            f"background-color: rgba({c.red()},{c.green()},{c.blue()},0.14);"
            f"color: {color}; border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.4);"
            "border-radius: 7px;"
        )
        self.setText(text)
        self.setVisible(True)
        if timeout_ms:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(timeout_ms, self.hide)

    def hideEvent(self, event) -> None:  # noqa: N802, D102
        super().hideEvent(event)


def elide(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def path_font() -> QFont:
    f = QFont("Cascadia Mono")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(9)
    return f


def rounded_path(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(x, y, w, h, r, r)
    return p
