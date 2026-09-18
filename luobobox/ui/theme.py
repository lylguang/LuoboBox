"""主题：深色为主，跟随系统明暗由 QPalette 决定不了，这里直接给一套固定配色。

配色沿用萝卜的意象：主体是墨绿灰底 + 萝卜红点缀 + 叶子绿表示"正常/运行中"。
语义色只用于状态，不用于装饰 —— 状态色一旦滥用，用户就再也读不出状态。
"""

from __future__ import annotations

# 基础色
BG = "#17181A"
BG_ALT = "#1E2023"
SURFACE = "#242629"
SURFACE_HI = "#2C2F33"
BORDER = "#34383D"
BORDER_HI = "#454A50"

TEXT = "#E8EAED"
TEXT_DIM = "#A8AEB6"
TEXT_MUTE = "#767D86"

# 语义色
OK = "#5DCAA5"        # 运行中 / 正常
WARN = "#EF9F27"      # 警告
ERR = "#F09595"       # 错误
INFO = "#85B7EB"      # 信息
ACCENT = "#E06A4A"    # 萝卜红（品牌点缀）

FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
MONO_FAMILY = '"Cascadia Mono", "Consolas", "Microsoft YaHei Mono", monospace'


def stylesheet() -> str:
    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: 13px;
    color: {TEXT};
}}
/* 注意：这里**不能**写 `QWidget {{ background-color: ... }}` ——
   Qt 的 QWidget 选择器会命中所有子控件（含 QLabel），
   于是每个标签都会在自己的父卡片上再刷一层深色底，形成一条条难看的横条。
   背景只在顶层容器上设，其余一律透明。 */
QMainWindow, QDialog {{
    background-color: {BG};
}}
QLabel, QCheckBox, QRadioButton, QGroupBox, QToolTip {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

QLabel#h1 {{ font-size: 19px; font-weight: 500; }}
QLabel#h2 {{ font-size: 15px; font-weight: 500; }}
QLabel#h3 {{ font-size: 13px; font-weight: 500; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#mute {{ color: {TEXT_MUTE}; font-size: 12px; }}
QLabel#mono {{ font-family: {MONO_FAMILY}; font-size: 12px; color: {TEXT_DIM}; }}

QFrame#card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#cardHi {{
    background-color: {SURFACE_HI};
    border: 1px solid {BORDER_HI};
    border-radius: 10px;
}}
QFrame#separator {{
    background-color: {BORDER};
    max-height: 1px;
    border: none;
}}

QPushButton {{
    background-color: {SURFACE_HI};
    border: 1px solid {BORDER_HI};
    border-radius: 7px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background-color: #363A3F; border-color: #52585F; }}
QPushButton:pressed {{ background-color: #2A2D31; }}
QPushButton:disabled {{ color: {TEXT_MUTE}; background-color: {BG_ALT}; border-color: {BORDER}; }}

QPushButton#primary {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    color: #FFFFFF;
    font-weight: 500;
}}
QPushButton#primary:hover {{ background-color: #E97A5C; }}
QPushButton#primary:disabled {{ background-color: #4A2E24; border-color: #4A2E24; color: #9C8478; }}

QPushButton#danger {{ border-color: #6B3232; color: {ERR}; background-color: #2C1F1F; }}
QPushButton#danger:hover {{ background-color: #3A2424; }}

QPushButton#ghost {{
    background-color: transparent;
    border: 1px solid {BORDER};
}}
QPushButton#ghost:hover {{ background-color: {SURFACE_HI}; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: #3A4855;
}}
QLineEdit#mono {{ font-family: {MONO_FAMILY}; font-size: 12px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {INFO};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    color: {TEXT_MUTE};
    background-color: {SURFACE};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px; border: none; background: transparent;
}}
QSpinBox::up-arrow, QSpinBox::down-arrow {{ width: 0px; height: 0px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_HI};
    selection-background-color: {SURFACE_HI};
    outline: none;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER_HI};
    border-radius: 4px;
    background-color: {BG_ALT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:hover {{ border-color: {INFO}; }}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 9px;
    top: -1px;
    background-color: {BG};
}}
QTabBar::tab {{
    background: transparent;
    padding: 8px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    color: {TEXT_DIM};
}}
QTabBar::tab:selected {{
    background-color: {BG};
    border-color: {BORDER};
    border-bottom: 1px solid {BG};
    color: {TEXT};
    font-weight: 500;
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 9px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: 500;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {TEXT_DIM};
}}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_HI}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #5A6068; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HI}; border-radius: 5px; min-width: 30px; }}

QTableWidget {{
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    selection-background-color: {SURFACE_HI};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background-color: {SURFACE_HI};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 7px 9px;
    color: {TEXT_DIM};
    font-weight: 500;
}}
QTableWidget::item {{ padding: 5px; }}
QTableCornerButton::section {{ background-color: {SURFACE_HI}; border: none; }}

QMenu {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_HI};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{ padding: 7px 26px 7px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {SURFACE_HI}; }}
QMenu::item:disabled {{ color: {TEXT_MUTE}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

QToolTip {{
    background-color: {SURFACE_HI};
    border: 1px solid {BORDER_HI};
    border-radius: 5px;
    padding: 5px 8px;
    color: {TEXT};
}}

QProgressBar {{
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 5px; }}

QStatusBar {{ background-color: {BG_ALT}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}

QSplitter::handle {{ background-color: {BORDER}; }}
"""


def status_color(state: str) -> str:
    return {
        "running": OK,
        "starting": WARN,
        "stopping": WARN,
        "external": INFO,
        "error": ERR,
        "stopped": TEXT_MUTE,
    }.get(state, TEXT_DIM)
