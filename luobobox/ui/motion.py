"""动效语言：统一、克制、失败即降级。

三条规矩：

1. **只做有信息量的动效**。淡入 = "这里出现了新东西"；描边闪烁 = "刚才是
   这个控件生效了"。纯装饰的位移 / 缩放一律不加 —— 屏上动着的东西越多，
   用户越难判断"到底哪个变了"。
2. **150~220ms**。超过 300ms 用户就开始觉得"卡"，而低于 100ms 又看不见。
3. **动效绝不能成为功能的单点故障**。任何一步失败都静默跳过：
   动画没了顶多不好看，功能不能因此不可用。

注意：QPropertyAnimation 必须被持有（这里挂在控件上），
否则 Python 侧一 GC，动画就中途停住 —— 表现为"淡入到一半不动了"。
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect

FAST = 140
NORMAL = 180
SLOW = 220


def fade_in(widget, ms: int = NORMAL) -> QPropertyAnimation | None:
    """淡入。返回值挂在 widget 上，调用方不必自己保存引用。"""
    try:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        # 动画结束后把 effect 摘掉：QGraphicsOpacityEffect 常驻会让控件
        # 走离屏合成，文字发虚、滚动变卡。用完即卸。
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        widget._motion_anim = anim  # type: ignore[attr-defined]
        anim.start()
        return anim
    except Exception:  # noqa: BLE001
        return None


def flash(widget, color: str, ms: int = 620) -> None:
    """一段短暂的描边高亮（复制成功、保存成功的即时确认）。

    用控件级 stylesheet 覆盖再还原 —— 比 QGraphicsEffect 便宜，
    也不会影响布局。还原时置空即可回落到应用级样式表。
    """
    try:
        original = widget.styleSheet()
        widget.setStyleSheet(
            f"{original}\nborder: 1px solid {color}; border-radius: 6px;")
        QTimer.singleShot(ms, lambda: widget.setStyleSheet(original))
    except Exception:  # noqa: BLE001
        pass
