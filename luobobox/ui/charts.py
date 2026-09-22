"""纯 QPainter 小图表：按天柱状 + 环形占比。

为什么不用 matplotlib / pyqtgraph：这两张图加起来不到 200 行，
而引入任何绘图库都会把 PyInstaller 产物体积推高几十 MB，
还要额外处理打包时的后端插件。为一个柱状图付这个代价不划算。

约定：颜色一律在 paintEvent 里现取 `theme.XXX`，
不要在 __init__ 里抠进属性 —— 换肤之后图会停在旧配色上。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


def _num(value: float) -> str:
    """图上的数字：整数不带小数点，小数留两位，超大数走 k。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 100000:
        return f"{v / 1000:.1f}k"
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}"


def _cached_font(kind: str, px_size: float, weight=None) -> QFont:
    """按 (用途, 字号, 字重) 缓存 QFont。

    QFont 的构造要走一次字体匹配，不算便宜；柱状图原来在**每根柱子**的循环里
    调一次 `_small()`，30 根柱子就是 30 次字体匹配。
    字号由 `theme.px()` 算进来，所以切换"大 / 特大"档位时 key 自然变化，
    缓存不会把图钉在旧字号上。

    ★ 共享 QFont 是安全的：`QPainter.setFont()` 是拷贝语义。
    """
    key = (kind, round(float(px_size), 2), weight)
    f = _font_cache.get(key)
    if f is None:
        f = QFont()
        f.setPointSizeF(float(px_size))
        if weight is not None:
            f.setWeight(weight)
        _font_cache[key] = f
    return f


_font_cache: dict[tuple, QFont] = {}


class BarChart(QWidget):
    """按天消耗柱状图。鼠标悬停在某根柱子上会给出当天数值。

    只把峰值那根涂成强调色，其余压暗 —— 一眼就能看出"哪天用得多"，
    而不是一片同色的柱子让人自己比高低。
    """

    PAD_LEFT = 8
    PAD_RIGHT = 8
    PAD_TOP = 16
    PAD_BOTTOM = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[tuple[str, float]] = []
        self._unit = ""
        self.setMinimumHeight(124)
        self.setMouseTracking(True)

    def set_data(self, data, unit: str = "") -> None:
        self._data = list(data or [])
        self._unit = unit
        self.update()

    def _plot_geometry(self) -> tuple[float, float]:
        plot_w = max(10.0, self.width() - self.PAD_LEFT - self.PAD_RIGHT)
        return plot_w, self.PAD_LEFT

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        plot_w, left = self._plot_geometry()
        base_y = h - self.PAD_BOTTOM
        plot_h = max(10.0, h - self.PAD_TOP - self.PAD_BOTTOM)

        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(int(left), int(base_y), int(left + plot_w), int(base_y))
        p.setFont(self._small())

        if not self._data:
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return

        peak = max((float(v) for _, v in self._data), default=0.0)
        n = len(self._data)
        slot = plot_w / n
        bar_w = max(3.0, min(34.0, slot * 0.6))

        accent = QColor(theme.ACCENT)
        dim = QColor(theme.ACCENT)
        dim.setAlpha(95)
        # 文字画笔只建一次（原来每根柱子都新建一个 QColor / 隐式 QPen）
        axis_pen = QPen(QColor(theme.TEXT_MUTE))
        peak_pen = QPen(QColor(theme.TEXT_DIM))

        for i, (day, value) in enumerate(self._data):
            cx = left + slot * (i + 0.5)
            value = float(value)
            if peak <= 0:
                bh = 2.0          # 全为 0 时留一条底线，别让人以为图没画出来
            else:
                bh = max(2.0, plot_h * (value / peak))
            # 每根柱子都显式清笔：上一轮可能刚用过文字画笔，
            # 不清的话这根柱子会被描上一圈边框。
            p.setPen(Qt.NoPen)
            p.setBrush(accent if (peak > 0 and value >= peak) else dim)
            p.drawRoundedRect(
                QRectF(cx - bar_w / 2, base_y - bh, bar_w, bh), 3, 3)

            # 日期抽稀：30 根柱子全标会糊成一片
            if n <= 8 or i == 0 or i == n - 1 or i % 5 == 0:
                p.setPen(axis_pen)
                p.drawText(QRectF(cx - slot / 2, base_y + 2, slot, 16),
                           Qt.AlignHCenter | Qt.AlignTop, str(day)[5:])

        if peak > 0:
            p.setPen(peak_pen)
            p.drawText(QRectF(left, 0, plot_w, self.PAD_TOP),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       f"峰值 {_num(peak)}{self._unit}")

    @staticmethod
    def _small() -> QFont:
        return _cached_font("small", max(6.5, theme.px(10) * 0.68))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._data:
            return
        plot_w, left = self._plot_geometry()
        slot = plot_w / len(self._data)
        idx = int((event.position().x() - left) // slot)
        if 0 <= idx < len(self._data):
            day, value = self._data[idx]
            self.setToolTip(f"{day}　{_num(value)}{self._unit}")


class Donut(QWidget):
    """环形占比图 + 中心大字。

    中心放"百分比"、下排放"已用 / 总量"—— 环本身只表达比例，
    具体数字还是得写出来，否则用户得眯着眼估。
    """

    def __init__(self, size: int = 136, parent=None):
        super().__init__(parent)
        self._size = size
        self._parts: list[tuple[str, float, str]] = []
        self._title = "—"
        self._sub = ""
        self.setFixedSize(size, size)

    def set_data(self, parts, title: str = "", sub: str = "") -> None:
        """parts: [(label, value, color), ...]"""
        self._parts = [(str(label), max(0.0, float(value)), color)
                       for label, value, color in (parts or [])]
        self._title = title
        self._sub = sub
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width = 12.0
        pad = width / 2 + 1
        box = QRectF(pad, pad, self._size - 2 * pad, self._size - 2 * pad)
        total = sum(v for _, v, _ in self._parts)

        if total <= 0:
            p.setPen(QPen(QColor(theme.BORDER), width))
            p.drawEllipse(box)
        else:
            start = 90 * 16
            for _label, value, color in self._parts:
                span = -int(round(360 * 16 * (value / total)))
                pen = QPen(QColor(color), width)
                pen.setCapStyle(Qt.FlatCap)
                p.setPen(pen)
                p.drawArc(box, start, span)
                start += span

        p.setFont(_cached_font("donut-main", max(8.0, theme.px(15) * 0.64),
                               QFont.DemiBold))
        p.setPen(QColor(theme.TEXT))
        inner = QRectF(6, 6, self._size - 12, self._size - 12)
        if self._sub:
            inner.setTop(inner.top() + self._size * 0.10)
        p.drawText(inner, Qt.AlignCenter, self._title)

        if self._sub:
            p.setFont(_cached_font("donut-sub", max(6.5, theme.px(10) * 0.62)))
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(QRectF(6, self._size * 0.60, self._size - 12, self._size * 0.28),
                       Qt.AlignHCenter | Qt.AlignTop, self._sub)


class Legend(QWidget):
    """环形图的图例：色块 + 名称 + 数值。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, str, str]] = []
        self.setMinimumHeight(30)

    def set_items(self, items) -> None:
        """items: [(label, text, color), ...]"""
        self._items = list(items or [])
        # 高度跟着条数走，否则图例会被压扁/留一片空白
        self.setFixedHeight(max(24, 19 * len(self._items) + 8))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(_cached_font("legend", max(7.0, theme.px(11) * 0.68)))
        y = 4
        for label, text, color in self._items:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(0, y + 4, 9, 9), 2, 2)
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(QRectF(15, y, self.width() - 15, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{label}　{text}")
            y += 19
