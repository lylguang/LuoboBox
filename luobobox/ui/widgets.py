"""可复用的小部件。"""

from __future__ import annotations

import math
import os
import platform

from PySide6.QtCore import QRectF, QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QSyntaxHighlighter,
    QTextCharFormat,
)
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
    """状态圆点：一眼看出运行/停止/异常。

    「运行中」时外圈做呼吸（半径 + 透明度缓慢起伏）。静态圆点区分不出
    「正盯着看」和「根本没在跑」—— 呼吸一下成本几乎为零，却让"服务活着"
    这件事在余光里就能感知到，不用凑近看颜色。
    """

    def __init__(self, color: str = theme.TEXT_MUTE, size: int = 12, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self._breathing = False
        self._phase = 0.0
        self.setFixedSize(size + 6, size + 6)
        self._anim = QTimer(self)
        self._anim.setInterval(50)          # 20fps 足够顺，又不烧 CPU
        self._anim.timeout.connect(self._tick)

    def set_color(self, color: str) -> None:
        c = QColor(color)
        if c == self._color:
            return
        self._color = c
        self.update()

    def set_breathing(self, on: bool) -> None:
        """running 状态开呼吸；其它状态停掉并归位，避免"没跑还在闪"。"""
        if on == self._breathing:
            return
        self._breathing = on
        if on:
            self._anim.start()
        else:
            self._anim.stop()
            self._phase = 0.0
            self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.12) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, D102
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pulse = (math.sin(self._phase) + 1) / 2 if self._breathing else 0.35
        # 外圈柔光：呼吸时同时变亮、变薄，看起来像在"扩缩"
        glow = QColor(self._color)
        glow.setAlpha(int(30 + 75 * pulse))
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        pad = 1.5 * (1 - pulse)
        p.drawEllipse(int(round(pad)), int(round(pad)),
                      int(round(self._size + 6 - 2 * pad)),
                      int(round(self._size + 6 - 2 * pad)))
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
        # 字号用 theme.pt()：QSS 里写 px 会让字体 pointSize() = -1，
        # Qt 内部算菜单字号时会报警；而且写死 px 也不跟随"特大字号"档位。
        self.setStyleSheet(
            f"background-color: rgba({bg.red()},{bg.green()},{bg.blue()},0.16);"
            f"color: {color}; border: 1px solid rgba({bg.red()},{bg.green()},{bg.blue()},0.35);"
            f"border-radius: 11px; padding: 0 9px; font-size: {theme.pt(11)}pt;"
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
            self._sub.setStyleSheet(
                f"color: {theme.TEXT_DIM}; font-size: {theme.pt(11)}pt;")
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


def message_popup(parent, title: str, text: str, detail: str = "",
                  icon: str = "warn") -> None:
    """统一的模态弹窗。配置类流程出问题时用。

    为什么必须是**弹窗**而不是 Toast / 一行日志：日志会被后续输出滚走，
    Toast 一闪而过（error 级虽然常驻，但它在页面角落）。而「环境没配好」
    是需要用户**当场知道、并且照着实操**的结果 —— 用户不会去翻那 190px 的
    日志框。多行细节塞进「详细信息」里，主文案只留一句话 + 下一步动作。

    icon: warn（默认）/ error / info。
    """
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setIcon({
        "warn": QMessageBox.Warning,
        "error": QMessageBox.Critical,
        "info": QMessageBox.Information,
    }.get(icon, QMessageBox.Warning))
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setDetailedText(detail)
    box.setStandardButtons(QMessageBox.Ok)
    box.exec()


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

    auto = QLabel("懒得自己装？点「一键修复环境 / 一键配置环境」，萝卜盒会"
                  "自动下载一份内置 Python（免安装、免管理员）。")
    auto.setObjectName("mute")
    auto.setWordWrap(True)
    col.addWidget(auto)

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


class EmptyState(QFrame):
    """空态占位：说清「为什么空」+「下一步点哪」。

    空白面板是新手最大的挫败点 —— 用户分不清"没有数据"和"程序坏了"。
    每处空态都必须给出下一步动作（要么给按钮，要么指明去哪一页）。
    """

    def __init__(self, title: str = "暂无数据", hint: str = "", glyph: str = "☐",
                 action: tuple | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("emptyState")
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 22, 20, 22)
        col.setSpacing(6)

        self._glyph = QLabel(glyph)
        self._glyph.setObjectName("emptyGlyph")
        self._glyph.setAlignment(Qt.AlignCenter)
        col.addWidget(self._glyph)

        self._title = QLabel(title)
        self._title.setObjectName("emptyTitle")
        self._title.setAlignment(Qt.AlignCenter)
        col.addWidget(self._title)

        self._hint = QLabel(hint)
        self._hint.setObjectName("mute")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setVisible(bool(hint))
        col.addWidget(self._hint)

        self.button = None
        self._action = action
        if action:
            text, slot = action
            self.button = QPushButton(text)
            self.button.setObjectName("primary")
            self.button.setCursor(Qt.PointingHandCursor)
            self.button.clicked.connect(slot)
            wrap = QHBoxLayout()
            wrap.addStretch(1)
            wrap.addWidget(self.button)
            wrap.addStretch(1)
            col.addLayout(wrap)

    def set_text(self, title: str, hint: str = "") -> None:
        self._title.setText(title)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))

    def set_glyph(self, glyph: str) -> None:
        self._glyph.setText(glyph)


class Toast(QFrame):
    """内联提示条：轻量反馈，不打断操作。

    按 level 决定默认停留时长：
      info / ok  → 6 秒自动收起
      warn       → 10 秒
      error      → **常驻**，直到用户自己点「✕」

    为什么 error 必须常驻：失败提示带的常常是多行逐通道清单
    （"手动代理 …：连不上（探活 0.8s 超时，已跳过）"），
    6 秒自动消失等于用户还没读完就没了 —— 那还不如不提示。

    点提示条本身 = 复制全文（多行清单直接粘进聊天 / issue 最有用）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setVisible(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击复制全文")
        self._color: QColor | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_message)
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(lambda: self._hint.setVisible(False))

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(8)

        self._label = QLabel("")
        self._label.setObjectName("toastText")
        self._label.setWordWrap(True)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self._label, 1)

        self._hint = QLabel("已复制")
        self._hint.setObjectName("toastHint")
        self._hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hint.setVisible(False)
        row.addWidget(self._hint, 0, Qt.AlignTop)

        self._close = QPushButton("✕")
        self._close.setObjectName("toastClose")
        self._close.setFixedSize(20, 20)
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setToolTip("关闭")
        self._close.clicked.connect(self.hide_message)
        row.addWidget(self._close, 0, Qt.AlignTop)

        # 换肤时提示条往往正显示着，等到下一次 show_message 才变色会显得"没跟上"。
        # 注册一次即可（提示条是长生命周期控件，不会反复创建）。
        theme.on_change(self.repaint_theme)

    # ------------------------------------------------------------- 显示

    def show_message(self, text: str, level: str = "info",
                     timeout_ms: int | None = None) -> None:
        color, default_ms = {
            "info": (theme.INFO, 6000),
            "ok": (theme.OK, 6000),
            "warn": (theme.WARN, 10000),
            "error": (theme.ERR, 0),
        }.get(level, (theme.INFO, 6000))
        if timeout_ms is None:
            timeout_ms = default_ms

        self._paint(color)
        self._label.setText(text)
        self._hint.setVisible(False)
        self.setVisible(True)
        self.raise_()

        self._timer.stop()
        self._hint_timer.stop()
        if timeout_ms:
            self._timer.start(int(timeout_ms))

    def hide_message(self) -> None:
        self._timer.stop()
        self._hint.setVisible(False)
        self.setVisible(False)

    def _paint(self, color: str) -> None:
        """把配色画到自身与子控件上。

        颜色只能在这里定 —— 换肤时提示条往往正显示着，
        等下一次 show_message 才变色会显得"没跟上"。
        """
        c = QColor(color)
        self._color = c
        rgba = f"{c.red()},{c.green()},{c.blue()}"
        hint_pt, close_pt = theme.pt(11), theme.pt(13)
        self.setStyleSheet(
            f"QFrame#toast {{ background-color: rgba({rgba},0.13);"
            f" border: 1px solid rgba({rgba},0.42); border-radius: 8px; }}"
            f"QFrame#toast QLabel#toastText {{ color: {color}; background: transparent;"
            " border: none; }"
            f"QFrame#toast QLabel#toastHint {{ color: {color}; background: transparent;"
            f" border: none; font-size: {hint_pt}pt; }}"
            "QFrame#toast QPushButton#toastClose {"
            f" color: {color}; background: transparent; border: none;"
            f" padding: 0; font-size: {close_pt}pt; }}"
            "QFrame#toast QPushButton#toastClose:hover {"
            f" background-color: rgba({rgba},0.28); border-radius: 10px; }}"
        )

    def repaint_theme(self) -> None:
        """换肤回调：用当前 layer 的颜色重画（不改变文案与计时）。"""
        if self._color is None or not self.isVisible():
            return
        self._paint(self._color.name())

    # ------------------------------------------------------------- 兼容

    def text(self) -> str:
        """保留旧 QLabel 语义，避免调用方（含测试）拿不到文案。"""
        return self._label.text()

    def setText(self, text: str) -> None:  # noqa: N802
        self._label.setText(text)

    def setWordWrap(self, on: bool) -> None:  # noqa: N802
        self._label.setWordWrap(on)

    # ------------------------------------------------------------- 交互

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            QApplication.clipboard().setText(self._label.text())
            self._hint.setVisible(True)
            self._hint_timer.start(1400)
        super().mousePressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802, D102
        self._timer.stop()
        super().hideEvent(event)


class StatusRing(QWidget):
    """大号状态环：首屏英雄区的视觉锚点。

    环的颜色 = 网关状态，环里直接写状态词。比一行小字更容易
    「扫一眼就懂」—— 用户不需要先读文字再判断颜色。

    running 时外圈做呼吸，和标题栏那颗小圆点同一套动效语言，
    不额外发明第二种"活着"的表示法。
    """

    def __init__(self, size: int = 96, parent=None):
        super().__init__(parent)
        self._size = size
        self._label = "…"
        self._sub = ""
        self._color = QColor(theme.TEXT_MUTE)
        self._breathing = False
        self._phase = 0.0
        self.setFixedSize(size, size)
        self._anim = QTimer(self)
        self._anim.setInterval(50)
        self._anim.timeout.connect(self._tick)

    def set_state(self, label: str, color: str, sub: str = "") -> None:
        self._label = label
        self._sub = sub
        self._color = QColor(color)
        self.update()

    def set_breathing(self, on: bool) -> None:
        if on == self._breathing:
            return
        self._breathing = on
        if on:
            self._anim.start()
        else:
            self._anim.stop()
            self._phase = 0.0
            self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.10) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, D102
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pulse = (math.sin(self._phase) + 1) / 2 if self._breathing else 0.0

        # 外圈柔光（呼吸时宽度与浓度一起起伏）
        halo_w = 10 + 5 * pulse
        halo = QColor(self._color)
        halo.setAlpha(int(38 + 42 * pulse))
        pen = QPen(halo, halo_w)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        pad = halo_w / 2 + 1
        p.drawEllipse(QRectF(pad, pad, self._size - 2 * pad, self._size - 2 * pad))

        # 内圈实线
        pen = QPen(self._color, 7)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawEllipse(QRectF(11, 11, self._size - 22, self._size - 22))

        # 中心：状态词（大字）+ 副标题（小字）
        inner = QRectF(6, 6, self._size - 12, self._size - 12)
        if self._sub:
            inner.setTop(inner.top() + self._size * 0.16)
        f = QFont()
        f.setPointSizeF(max(9.0, theme.px(15) * 0.74))
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(self._color)
        p.drawText(inner, Qt.AlignCenter if not self._sub else Qt.AlignHCenter | Qt.AlignVCenter,
                   self._label)
        if self._sub:
            f2 = QFont()
            f2.setPointSizeF(max(6.5, theme.px(11) * 0.62))
            p.setFont(f2)
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(QRectF(6, self._size * 0.60, self._size - 12, self._size * 0.30),
                       Qt.AlignHCenter | Qt.AlignTop, self._sub)


class LogHighlighter(QSyntaxHighlighter):
    """日志分级着色：把 ERROR / WARN / INFO 挑出来。

    纯文本日志里，一行 ERROR 和一行 INFO 长得一模一样 —— 网关日志动辄
    上千行，靠肉眼找错是不可能的。着色是成本最低的"过滤"。
    """

    RULES: tuple[tuple[str, str], ...] = (
        (r"\b(ERROR|CRITICAL|Traceback|Exception)\b", "ERR"),
        (r"\b(WARN|WARNING)\b", "WARN"),
        (r"\b(INFO)\b", "INFO"),
        (r"\b(DEBUG)\b", "TEXT_MUTE"),
    )

    def __init__(self, document):
        super().__init__(document)
        self._rules = [(QRegularExpression(p), key) for p, key in self.RULES]

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for rx, key in self._rules:
            color = getattr(theme, key, theme.TEXT_DIM)
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                fmt.setFontWeight(QFont.DemiBold)
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


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
