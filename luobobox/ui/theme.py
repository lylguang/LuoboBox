"""主题：暗 / 亮双基调 + 4 套强调色 + 三档字号。

为什么不用 QPalette「跟随系统」：Qt 的调色板机制管不到我们的 QSS 细节
（卡片描边、圆角、语义色、输入框内阴影），两套机制混用只会互相打架，
最后是"亮色下有些地方还是黑的"。所以颜色全部收进 PALETTES，
由一个 stylesheet() 统一产出 —— 换肤 = 换一组变量 + app.setStyleSheet()，
热重载、不重启。

两条约定：
  1. 语义色（OK/WARN/ERR/INFO）只用于状态，不用于装饰。
     状态色一旦滥用，用户就再也读不出状态。
  2. 模块级常量（theme.OK / theme.BG ...）表示**当前生效值**，由 apply() 重写。
     别在 import 阶段把颜色抠进闭包或类属性默认值 —— 那样换肤不会生效。
     真的需要在构造时取色的控件，用 theme.on_change() 注册回刷。
  3. QSS 里的字号一律用 `pt()`（写成 `Npt`），不要写 `Npx`。
     px 字号字体的 pointSize() 是 -1，Qt 内部算菜单字号时会因此报警
     （详见 `pt()` 的说明）。给 QPainter 的 QFont 设字号才用 `px()`。
"""

from __future__ import annotations

DARK = {
    "BG": "#17181A",
    "BG_ALT": "#1E2023",
    "SURFACE": "#242629",
    "SURFACE_HI": "#2C2F33",
    "BORDER": "#34383D",
    "BORDER_HI": "#454A50",
    "TEXT": "#E8EAED",
    "TEXT_DIM": "#A8AEB6",
    "TEXT_MUTE": "#767D86",
    "OK": "#5DCAA5",          # 运行中 / 正常
    "WARN": "#EF9F27",        # 警告
    "ERR": "#F09595",         # 错误
    "INFO": "#85B7EB",        # 信息
    "HOVER": "#363A3F",
    "PRESSED": "#2A2D31",
    "BORDER_HOVER": "#52585F",
    "SCROLL_HOVER": "#5A6068",
    "SELECTION": "#3A4855",
    "DANGER_BG": "#2C1F1F",
    "DANGER_BORDER": "#6B3232",
    "DANGER_HOVER": "#3A2424",
    "DISABLED_BG": "#4A2E24",
    "DISABLED_TEXT": "#9C8478",
    "HERO_BG": "#1C1E21",
    "LOG_BG": "#1A1C1E",
}

LIGHT = {
    "BG": "#F5F6F8",
    "BG_ALT": "#FFFFFF",
    "SURFACE": "#FFFFFF",
    "SURFACE_HI": "#EEF0F3",
    "BORDER": "#E0E2E7",
    "BORDER_HI": "#C6C9D0",
    "TEXT": "#1C1E22",
    "TEXT_DIM": "#555C66",
    "TEXT_MUTE": "#848B95",
    "OK": "#0F7A57",
    "WARN": "#9A5B02",
    "ERR": "#B42318",
    "INFO": "#175CD3",
    "HOVER": "#E7E9ED",
    "PRESSED": "#DCDEE3",
    "BORDER_HOVER": "#A6AAB3",
    "SCROLL_HOVER": "#A6AAB3",
    "SELECTION": "#CBE0F7",
    "DANGER_BG": "#FEF3F2",
    "DANGER_BORDER": "#F2B8B3",
    "DANGER_HOVER": "#FCE7E5",
    "DISABLED_BG": "#EFD9D2",
    "DISABLED_TEXT": "#AE8878",
    "HERO_BG": "#FFFFFF",
    "LOG_BG": "#FBFBFC",
}

PALETTES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}

# 强调色：每套自带按钮上的前景色 —— 青柠偏亮，铺白字读不清。
ACCENTS: dict[str, dict[str, str]] = {
    "rooboo": {"label": "萝卜红", "color": "#E06A4A", "fg": "#FFFFFF", "hover": "#E97A5C"},
    "lime": {"label": "青柠", "color": "#8CBB2E", "fg": "#1A2205", "hover": "#9ACB3B"},
    "violet": {"label": "电紫", "color": "#7C5CFF", "fg": "#FFFFFF", "hover": "#8B6EFF"},
    "lake": {"label": "湖蓝", "color": "#2E9BD6", "fg": "#FFFFFF", "hover": "#41A8DF"},
}

DEFAULT_PALETTE = "dark"
DEFAULT_ACCENT = "rooboo"
DEFAULT_SCALE = 1.0

# 字号三档。放大只动字体，不动布局 —— 动布局会让所有手工排版全崩。
SCALE_STEPS: tuple[float, ...] = (1.0, 1.15, 1.3)
SCALE_LABELS: tuple[str, ...] = ("标准", "大", "特大")

FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
MONO_FAMILY = '"Cascadia Mono", "Consolas", "Microsoft YaHei Mono", monospace'

# ---------------------------------------------------------------- 当前值

_state = {
    "palette": DEFAULT_PALETTE,
    "accent": DEFAULT_ACCENT,
    "scale": DEFAULT_SCALE,
}

# 下面这批名字会被 _sync() 反复重写，外部一律以 `theme.XXX` 读取。
BG = BG_ALT = SURFACE = SURFACE_HI = BORDER = BORDER_HI = ""
TEXT = TEXT_DIM = TEXT_MUTE = ""
OK = WARN = ERR = INFO = ACCENT = ACCENT_FG = ""

_watchers: list = []


def _sync() -> None:
    """把当前配色写回模块级常量（module global），供 theme.XXX 直接读。"""
    g = globals()
    pal = PALETTES[_state["palette"]]
    for key, value in pal.items():
        g[key] = value
    acc = ACCENTS[_state["accent"]]
    g["ACCENT"] = acc["color"]
    g["ACCENT_FG"] = acc["fg"]


def apply(palette: str | None = None, accent: str | None = None,
          scale: float | None = None) -> None:
    """切换配色并通知订阅者。调用方负责 app.setStyleSheet(theme.stylesheet())。"""
    if palette in PALETTES:
        _state["palette"] = palette
    if accent in ACCENTS:
        _state["accent"] = accent
    if scale:
        try:
            _state["scale"] = float(scale)
        except (TypeError, ValueError):
            pass
    _sync()
    for fn in list(_watchers):
        try:
            fn()
        except Exception:  # noqa: BLE001
            # 换肤不应该因为某个控件回刷失败就整体中断
            pass


def on_change(fn) -> None:
    """注册换肤回调（控件回刷自己缓存的颜色）。"""
    if fn not in _watchers:
        _watchers.append(fn)


def current() -> dict:
    return dict(_state)


def palette_name() -> str:
    return _state["palette"]


def accent_name() -> str:
    return _state["accent"]


def scale() -> float:
    return _state["scale"]


def px(size: float) -> int:
    """按当前档位缩放一个字号。"""
    return int(round(size * _state["scale"]))


def pt(size: float) -> int:
    """按当前档位缩放一个字号，并换算成 pt —— **QSS 里一律用这个**。

    ★ 为什么 QSS 字号必须写 pt 而不是 px：`font-size: Npx` 产出的是
      「像素字号」字体 —— 它的 `pointSize()` 恒为 -1。而 Qt 内部有多处会
      基于 pointSize 再算一次字号（最典型的是带 QMenu 的按钮，
      QMenu/QPushButton 在 polish 时会做 `pointSize() - 1`），碰到 -1 就抛
      `QFont::setPointSize: Point size <= 0 (-1), must be greater than 0`。
      不崩、不影响显示，但每次启动都往 stderr 塞一行；只要有菜单
      （「⋯ 更多」、托盘右键菜单）就必然出现。
      写 pt 则字体自带正数 pointSize，从根上消除这类警告。

    pt ≈ px × 0.75（96 dpi 下的换算），所以 `pt(13) == 10pt ≈ 13.3px`，
    与原来的 13px 视觉上基本一致。

    需要给 QPainter 的 QFont 设字号时用 `px()` 的数值配合 setPointSizeF，
    不要拿 px 的数值去写 QSS —— 那正是上面这个坑的来源。
    """
    return max(1, int(round(size * 0.75 * _state["scale"])))


def accent_choices() -> list[tuple[str, str]]:
    """[(key, 中文名)] —— 按声明顺序，UI 用它填下拉框。"""
    return [(k, v["label"]) for k, v in ACCENTS.items()]


def log_level_color(level: str) -> str:
    """日志分级配色（日志页着色用）。"""
    return {
        "ERROR": ERR,
        "CRITICAL": ERR,
        "WARN": WARN,
        "WARNING": WARN,
        "INFO": INFO,
        "DEBUG": TEXT_MUTE,
    }.get(level.upper(), TEXT_DIM)


# ---------------------------------------------------------------- 样式表


def stylesheet() -> str:
    c = dict(PALETTES[_state["palette"]])
    acc = ACCENTS[_state["accent"]]
    c["ACCENT"] = acc["color"]
    c["ACCENT_FG"] = acc["fg"]
    c["ACCENT_HOVER"] = acc["hover"]
    p = lambda n: pt(n)  # noqa: E731

    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: {p(13)}pt;
    color: {c['TEXT']};
}}
/* 注意：这里**不能**写 `QWidget {{ background-color: ... }}` ——
   Qt 的 QWidget 选择器会命中所有子控件（含 QLabel），
   于是每个标签都会在自己的父卡片上再刷一层深色底，形成一条条难看的横条。
   背景只在顶层容器上设，其余一律透明。 */
QMainWindow, QDialog {{
    background-color: {c['BG']};
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

QLabel#h1 {{ font-size: {p(19)}pt; font-weight: 500; }}
QLabel#h2 {{ font-size: {p(15)}pt; font-weight: 500; }}
QLabel#h3 {{ font-size: {p(13)}pt; font-weight: 500; }}
QLabel#dim {{ color: {c['TEXT_DIM']}; }}
QLabel#mute {{ color: {c['TEXT_MUTE']}; font-size: {p(12)}pt; }}
QLabel#mono {{ font-family: {MONO_FAMILY}; font-size: {p(12)}pt; color: {c['TEXT_DIM']}; }}
QLabel#stat {{ font-size: {p(22)}pt; font-weight: 500; }}
QLabel#statLabel {{ color: {c['TEXT_MUTE']}; font-size: {p(12)}pt; }}
QLabel#heroTitle {{ font-size: {p(20)}pt; font-weight: 500; }}
QLabel#emptyTitle {{ font-size: {p(14)}pt; font-weight: 500; color: {c['TEXT_DIM']}; }}
QLabel#emptyGlyph {{ color: {c['TEXT_MUTE']}; font-size: {p(24)}pt; }}
/* 语义色文字（磁盘告警、旧计划任务提醒）：靠 objectName 吃样式，
   不能在各处 inline 写死 —— 那样换到浅色主题后还是深色版的橙。 */
QLabel#warnText {{ color: {c['WARN']}; }}
QLabel#errText {{ color: {c['ERR']}; }}

QFrame#card {{
    background-color: {c['SURFACE']};
    border: 1px solid {c['BORDER']};
    border-radius: 10px;
}}
QFrame#cardHi {{
    background-color: {c['SURFACE_HI']};
    border: 1px solid {c['BORDER_HI']};
    border-radius: 10px;
}}
QFrame#hero {{
    background-color: {c['HERO_BG']};
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
}}
QFrame#emptyState {{
    background-color: {c['BG_ALT']};
    border: 1px dashed {c['BORDER_HI']};
    border-radius: 10px;
}}
QFrame#separator {{
    background-color: {c['BORDER']};
    max-height: 1px;
    border: none;
}}

QPushButton {{
    background-color: {c['SURFACE_HI']};
    border: 1px solid {c['BORDER_HI']};
    border-radius: 7px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background-color: {c['HOVER']}; border-color: {c['BORDER_HOVER']}; }}
QPushButton:pressed {{ background-color: {c['PRESSED']}; }}
QPushButton:disabled {{ color: {c['TEXT_MUTE']}; background-color: {c['BG_ALT']}; border-color: {c['BORDER']}; }}

QPushButton#primary {{
    background-color: {c['ACCENT']};
    border: 1px solid {c['ACCENT']};
    color: {c['ACCENT_FG']};
    font-weight: 500;
}}
QPushButton#primary:hover {{ background-color: {c['ACCENT_HOVER']}; border-color: {c['ACCENT_HOVER']}; }}
QPushButton#primary:disabled {{ background-color: {c['DISABLED_BG']}; border-color: {c['DISABLED_BG']}; color: {c['DISABLED_TEXT']}; }}

QPushButton#danger {{ border-color: {c['DANGER_BORDER']}; color: {c['ERR']}; background-color: {c['DANGER_BG']}; }}
QPushButton#danger:hover {{ background-color: {c['DANGER_HOVER']}; }}

QPushButton#ghost {{
    background-color: transparent;
    border: 1px solid {c['BORDER']};
}}
QPushButton#ghost:hover {{ background-color: {c['SURFACE_HI']}; }}

QPushButton#iconBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
    color: {c['TEXT_DIM']};
}}
QPushButton#iconBtn:hover {{ background-color: {c['SURFACE_HI']}; color: {c['TEXT']}; }}

QPushButton#toastClose {{
    background: transparent;
    border: none;
    padding: 0;
    color: {c['TEXT_MUTE']};
}}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {c['BG_ALT']};
    border: 1px solid {c['BORDER']};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {c['SELECTION']};
}}
QLineEdit#mono {{ font-family: {MONO_FAMILY}; font-size: {p(12)}pt; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {c['INFO']};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    color: {c['TEXT_MUTE']};
    background-color: {c['SURFACE']};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px; border: none; background: transparent;
}}
QSpinBox::up-arrow, QSpinBox::down-arrow {{ width: 0px; height: 0px; }}
QComboBox QAbstractItemView {{
    background-color: {c['SURFACE']};
    border: 1px solid {c['BORDER_HI']};
    selection-background-color: {c['SURFACE_HI']};
    outline: none;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {c['BORDER_HI']};
    border-radius: 4px;
    background-color: {c['BG_ALT']};
}}
QCheckBox::indicator:checked {{
    background-color: {c['ACCENT']};
    border-color: {c['ACCENT']};
}}
QCheckBox::indicator:hover {{ border-color: {c['INFO']}; }}

QTabWidget::pane {{
    border: 1px solid {c['BORDER']};
    border-radius: 9px;
    top: -1px;
    background-color: {c['BG']};
}}
QTabBar::tab {{
    background: transparent;
    padding: 8px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    color: {c['TEXT_DIM']};
}}
QTabBar::tab:selected {{
    background-color: {c['BG']};
    border-color: {c['BORDER']};
    border-bottom: 1px solid {c['BG']};
    color: {c['TEXT']};
    font-weight: 500;
}}
QTabBar::tab:hover:!selected {{ color: {c['TEXT']}; }}

QGroupBox {{
    border: 1px solid {c['BORDER']};
    border-radius: 9px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: 500;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {c['TEXT_DIM']};
}}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c['BORDER_HI']}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['SCROLL_HOVER']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c['BORDER_HI']}; border-radius: 5px; min-width: 30px; }}

QTableWidget {{
    background-color: {c['BG_ALT']};
    border: 1px solid {c['BORDER']};
    border-radius: 8px;
    gridline-color: {c['BORDER']};
    selection-background-color: {c['SURFACE_HI']};
    selection-color: {c['TEXT']};
}}
QHeaderView::section {{
    background-color: {c['SURFACE_HI']};
    border: none;
    border-bottom: 1px solid {c['BORDER']};
    padding: 7px 9px;
    color: {c['TEXT_DIM']};
    font-weight: 500;
}}
QTableWidget::item {{ padding: 5px; }}
QTableCornerButton::section {{ background-color: {c['SURFACE_HI']}; border: none; }}

QMenu {{
    background-color: {c['SURFACE']};
    border: 1px solid {c['BORDER_HI']};
    border-radius: 8px;
    padding: 5px;
    font-size: {pt(13)}pt;
}}
QMenu::item {{ padding: 7px 26px 7px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {c['SURFACE_HI']}; }}
QMenu::item:disabled {{ color: {c['TEXT_MUTE']}; }}
QMenu::separator {{ height: 1px; background: {c['BORDER']}; margin: 5px 8px; }}

QToolTip {{
    background-color: {c['SURFACE_HI']};
    border: 1px solid {c['BORDER_HI']};
    border-radius: 5px;
    padding: 5px 8px;
    color: {c['TEXT']};
}}

QProgressBar {{
    background-color: {c['BG_ALT']};
    border: 1px solid {c['BORDER']};
    border-radius: 6px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {c['ACCENT']}; border-radius: 5px; }}

QStatusBar {{ background-color: {c['BG_ALT']}; border-top: 1px solid {c['BORDER']}; color: {c['TEXT_DIM']}; }}
QStatusBar::item {{ border: none; }}

QSplitter::handle {{ background-color: {c['BORDER']}; }}

QPlainTextEdit#logView {{
    background-color: {c['LOG_BG']};
    border: 1px solid {c['BORDER']};
    border-radius: 8px;
    font-family: {MONO_FAMILY};
    font-size: {p(12)}pt;
}}

/* ---- 顶部导航：4 个主入口 + 「⋯」更多 ----
   页签到 7 个之后，平铺 tab bar 变成"一排字"，扫不完。
   分组的代价只是一层映射，收益是常用入口一眼可见。 */
QPushButton#navBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 15px;
    color: {c['TEXT_DIM']};
}}
QPushButton#navBtn:hover {{ background-color: {c['SURFACE_HI']}; color: {c['TEXT']}; }}
QPushButton#navBtn:checked {{
    background-color: {c['SURFACE']};
    border-color: {c['BORDER_HI']};
    color: {c['TEXT']};
    font-weight: 500;
}}
QPushButton#navMore {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 12px;
    color: {c['TEXT_DIM']};
    font-weight: 500;
}}
QPushButton#navMore:hover {{ background-color: {c['SURFACE_HI']}; color: {c['TEXT']}; }}
QPushButton#navMore:checked {{
    background-color: {c['SURFACE']};
    border-color: {c['BORDER_HI']};
    color: {c['TEXT']};
}}

/* ---- 首屏英雄区 ---- */
QLabel#heroKicker {{ color: {c['TEXT_MUTE']}; font-size: {p(12)}pt; }}

/* ---- 命令面板（Ctrl+K） ---- */
QDialog#palette {{
    background-color: {c['SURFACE']};
    border: 1px solid {c['BORDER_HI']};
}}
QLineEdit#paletteInput {{
    background-color: {c['BG_ALT']};
    border: 1px solid {c['BORDER']};
    border-radius: 8px;
    padding: 10px 12px;
    font-size: {p(15)}pt;
}}
QListWidget#paletteList {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget#paletteList::item {{
    padding: 7px 10px;
    border-radius: 6px;
    color: {c['TEXT_DIM']};
}}
QListWidget#paletteList::item:selected {{
    background-color: {c['ACCENT']};
    color: {c['ACCENT_FG']};
    font-weight: 500;
}}
QListWidget#paletteList::item:hover:!selected {{
    background-color: {c['SURFACE_HI']};
    color: {c['TEXT']};
}}
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


# 首次导入即把默认配色刷进模块级常量，否则 theme.OK 之类会是一串空字符串。
_sync()
