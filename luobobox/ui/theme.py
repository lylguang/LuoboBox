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
    # 命名照抄 iOS 语义色，方便对着 Apple HIG 核对：
    # systemBackground / secondarySystemBackground / tertiarySystemBackground
    "BG": "#000000",
    "BG_ALT": "#1C1C1E",
    "SURFACE": "#1C1C1E",
    "SURFACE_HI": "#2C2C2E",
    "BORDER": "#38383A",          # separator
    "BORDER_HI": "#48484A",
    "TEXT": "#FFFFFF",            # label
    "TEXT_DIM": "#98989F",        # secondaryLabel
    "TEXT_MUTE": "#6C6C70",       # tertiaryLabel（略提亮，保证小字可读）
    "OK": "#30D158",              # systemGreen (dark)
    "WARN": "#FF9F0A",            # systemOrange (dark)
    "ERR": "#FF453A",             # systemRed (dark)
    "INFO": "#0A84FF",            # systemBlue (dark)
    "HOVER": "#2C2C2E",
    "PRESSED": "#3A3A3C",
    "BORDER_HOVER": "#5A5A5E",
    "SCROLL_HOVER": "#636366",
    "SELECTION": "#26456E",       # systemBlue 压在黑底上的 35% 近似
    "DANGER_BG": "#2A1A19",
    "DANGER_BORDER": "#5C2B27",
    "DANGER_HOVER": "#3A2321",
    "DISABLED_BG": "#3A3A3C",
    "DISABLED_TEXT": "#7C7C80",
    "HERO_BG": "#1C1C1E",
    "LOG_BG": "#121214",
    "SWITCH_OFF": "#39393D",      # UISwitch 关闭态滑轨
    "FIELD_BG": "#2C2C2E",        # 卡片内嵌输入框的填充底（比卡片亮，读作"凹槽"）
    "SEGMENT_TRACK": "#2C2C2E",   # UISegmentedControl 轨道
    "SEGMENT_ON": "#48484A",      # 选中分段：iOS 深色下比轨道**更亮**
}

LIGHT = {
    "BG": "#F2F2F7",              # systemGroupedBackground
    "BG_ALT": "#FFFFFF",
    "SURFACE": "#FFFFFF",
    "SURFACE_HI": "#F2F2F7",
    "BORDER": "#D8D8DC",          # separator 的可见版（纯 C6C6C8 做描边偏重）
    "BORDER_HI": "#C7C7CC",
    "TEXT": "#000000",
    "TEXT_DIM": "#6E6E73",        # secondaryLabel（压深一档换可读性）
    "TEXT_MUTE": "#9C9CA1",       # tertiaryLabel
    "OK": "#248A3D",              # systemGreen 可读版（#34C759 当正文太浅）
    "WARN": "#C93400",
    "ERR": "#D70015",
    "INFO": "#0040DD",
    "HOVER": "#E8E8ED",
    "PRESSED": "#DDDDE2",
    "BORDER_HOVER": "#AEAEB2",
    "SCROLL_HOVER": "#AEAEB2",
    "SELECTION": "#B8D8FF",
    "DANGER_BG": "#FFF1F0",
    "DANGER_BORDER": "#F3B5B0",
    "DANGER_HOVER": "#FFE3E0",
    "DISABLED_BG": "#E8E8ED",
    "DISABLED_TEXT": "#AEAEB2",
    "HERO_BG": "#FFFFFF",
    "LOG_BG": "#FBFBFD",
    "SWITCH_OFF": "#E9E9EA",
    "FIELD_BG": "#F2F2F7",        # 白卡片里的浅灰内嵌底
    "SEGMENT_TRACK": "#E4E4E9",   # 浅色下轨道要比窗口底(#F2F2F7)略深才看得见
    "SEGMENT_ON": "#FFFFFF",      # 选中分段抬成白片
}

PALETTES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}

# 强调色：每套自带按钮上的前景色 —— 青柠偏亮，铺白字读不清。
# 除品牌色 rooboo 外，其余三套换成 iOS 系统色（systemGreen / systemPurple /
# systemBlue），这样"iOS 风格"是默认可达的，而萝卜盒自己的红仍然是默认值。
ACCENTS: dict[str, dict[str, str]] = {
    "rooboo": {"label": "萝卜红", "color": "#E06A4A", "fg": "#FFFFFF", "hover": "#E97A5C"},
    "lime": {"label": "青柠", "color": "#34C759", "fg": "#0B2E14", "hover": "#4CD964"},
    "violet": {"label": "电紫", "color": "#AF52DE", "fg": "#FFFFFF", "hover": "#BC6BE5"},
    "lake": {"label": "湖蓝", "color": "#007AFF", "fg": "#FFFFFF", "hover": "#1A8CFF"},
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

/* ---- 字阶：按 iOS 的 Dynamic Type 比例映射到桌面基准 13 ----
   Title2 22 / Headline 17 / Subheadline 14 / Body 13 / Footnote 12 */
QLabel#h1 {{ font-size: {p(22)}pt; font-weight: 600; }}
QLabel#h2 {{ font-size: {p(17)}pt; font-weight: 600; }}
QLabel#h3 {{ font-size: {p(14)}pt; font-weight: 500; }}
QLabel#dim {{ color: {c['TEXT_DIM']}; }}
QLabel#mute {{ color: {c['TEXT_MUTE']}; font-size: {p(12)}pt; }}
QLabel#mono {{ font-family: {MONO_FAMILY}; font-size: {p(12)}pt; color: {c['TEXT_DIM']}; }}
QLabel#stat {{ font-size: {p(28)}pt; font-weight: 600; }}
QLabel#statLabel {{ color: {c['TEXT_MUTE']}; font-size: {p(12)}pt; }}
QLabel#heroTitle {{ font-size: {p(24)}pt; font-weight: 600; }}
QLabel#emptyTitle {{ font-size: {p(15)}pt; font-weight: 500; color: {c['TEXT_DIM']}; }}
QLabel#emptyGlyph {{ color: {c['TEXT_MUTE']}; font-size: {p(26)}pt; }}
/* 语义色文字（磁盘告警、旧计划任务提醒）：靠 objectName 吃样式，
   不能在各处 inline 写死 —— 那样换到浅色主题后还是深色版的橙。 */
QLabel#warnText {{ color: {c['WARN']}; }}
QLabel#errText {{ color: {c['ERR']}; }}

/* ---- 卡片：iOS inset grouped，12 圆角 + 发丝描边 ---- */
QFrame#card {{
    background-color: {c['SURFACE']};
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
}}
QFrame#cardHi {{
    background-color: {c['SURFACE_HI']};
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
}}
QFrame#hero {{
    background-color: {c['HERO_BG']};
    border: 1px solid {c['BORDER']};
    border-radius: 14px;
}}
QFrame#emptyState {{
    background-color: {c['SURFACE']};
    border: 1px dashed {c['BORDER_HI']};
    border-radius: 12px;
}}
QFrame#separator {{
    background-color: {c['BORDER']};
    max-height: 1px;
    border: none;
}}

/* ---- 按钮：iOS 圆角矩形。次级按钮走"填充灰"而不是描边 ---- */
QPushButton {{
    background-color: {c['SURFACE_HI']};
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 7px 16px;
    font-weight: 500;
}}
QPushButton:hover {{ background-color: {c['HOVER']}; }}
QPushButton:pressed {{ background-color: {c['PRESSED']}; }}
QPushButton:disabled {{ color: {c['TEXT_MUTE']}; background-color: {c['SURFACE']}; border-color: {c['BORDER']}; }}

QPushButton#primary {{
    background-color: {c['ACCENT']};
    border: 1px solid {c['ACCENT']};
    color: {c['ACCENT_FG']};
    font-weight: 600;
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
    border-radius: 8px;
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

/* ---- 输入：内嵌填充式（iOS 表单不靠描边，靠"比卡片亮/暗一档"） ---- */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {c['FIELD_BG']};
    border: 1px solid {c['BORDER']};
    border-radius: 9px;
    padding: 7px 10px;
    selection-background-color: {c['SELECTION']};
}}
QLineEdit#mono {{ font-family: {MONO_FAMILY}; font-size: {p(12)}pt; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {c['ACCENT']};
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
    border: 1px solid {c['BORDER']};
    border-radius: 10px;
    selection-background-color: {c['SURFACE_HI']};
    outline: none;
}}

/* 原生 QCheckBox 的方形指示器保留给残留调用点；
   iOS 风格开关走 widgets.IOSSwitch，自己画滑轨，不吃这段。 */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {c['BORDER_HI']};
    border-radius: 5px;
    background-color: {c['FIELD_BG']};
}}
QCheckBox::indicator:checked {{
    background-color: {c['ACCENT']};
    border-color: {c['ACCENT']};
}}
QCheckBox::indicator:hover {{ border-color: {c['ACCENT']}; }}

QTabWidget::pane {{
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
    top: -1px;
    background-color: {c['BG']};
}}
QTabBar::tab {{
    background: transparent;
    padding: 8px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    color: {c['TEXT_DIM']};
}}
QTabBar::tab:selected {{
    background-color: {c['BG']};
    border-color: {c['BORDER']};
    border-bottom: 1px solid {c['BG']};
    color: {c['TEXT']};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{ color: {c['TEXT']}; }}

QGroupBox {{
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
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

/* ---- 滚动条：iOS 覆盖式滚动条 —— 无轨道、圆头、只在悬停时显形 ---- */
QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c['BORDER_HI']}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['SCROLL_HOVER']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c['BORDER_HI']}; border-radius: 4px; min-width: 32px; }}

QTableWidget {{
    background-color: {c['SURFACE']};
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: {c['SURFACE_HI']};
    selection-color: {c['TEXT']};
}}
QHeaderView::section {{
    background-color: {c['SURFACE_HI']};
    border: none;
    border-bottom: 1px solid {c['BORDER']};
    padding: 8px 10px;
    color: {c['TEXT_DIM']};
    font-weight: 500;
}}
QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {c['BORDER']}; }}
QTableCornerButton::section {{ background-color: {c['SURFACE_HI']}; border: none; }}

QMenu {{
    background-color: {c['SURFACE']};
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
    padding: 6px;
    font-size: {pt(13)}pt;
}}
QMenu::item {{ padding: 8px 26px 8px 14px; border-radius: 8px; }}
QMenu::item:selected {{ background-color: {c['ACCENT']}; color: {c['ACCENT_FG']}; }}
QMenu::item:disabled {{ color: {c['TEXT_MUTE']}; }}
QMenu::separator {{ height: 1px; background: {c['BORDER']}; margin: 5px 8px; }}

QToolTip {{
    background-color: {c['SURFACE_HI']};
    border: 1px solid {c['BORDER_HI']};
    border-radius: 8px;
    padding: 6px 9px;
    color: {c['TEXT']};
}}

QProgressBar {{
    background-color: {c['SURFACE_HI']};
    border: none;
    border-radius: 4px;
    height: 7px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {c['ACCENT']}; border-radius: 4px; }}

QStatusBar {{ background-color: {c['BG_ALT']}; border-top: 1px solid {c['BORDER']}; color: {c['TEXT_DIM']}; }}
QStatusBar::item {{ border: none; }}

QSplitter::handle {{ background-color: {c['BORDER']}; }}

QPlainTextEdit#logView {{
    background-color: {c['LOG_BG']};
    border: 1px solid {c['BORDER']};
    border-radius: 12px;
    font-family: {MONO_FAMILY};
    font-size: {p(12)}pt;
}}

/* ---- 顶部导航 = iOS 分段控件（UISegmentedControl） ----
   轨道是一块填充灰，选中项抬成一张"浮起来的白片"；
   这比"给选中项刷主题色"更像 iOS，也不会让一屏里出现两块强调色。
   注意：QWidget#segmented 必须在 Python 侧 setAttribute(WA_StyledBackground)，
   否则普通 QWidget 不画 QSS 背景（Qt 的经典坑）。 */
QWidget#segmented {{
    background-color: {c['SEGMENT_TRACK']};
    border: 1px solid {c['BORDER']};
    border-radius: 11px;
}}
QPushButton#navBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 16px;
    color: {c['TEXT_DIM']};
    font-weight: 500;
}}
QPushButton#navBtn:hover:!checked {{ color: {c['TEXT']}; }}
QPushButton#navBtn:checked {{
    background-color: {c['SEGMENT_ON']};
    border-color: {c['BORDER']};
    color: {c['TEXT']};
    font-weight: 600;
}}
QPushButton#navMore {{
    background-color: transparent;
    border: 1px solid {c['BORDER']};
    border-radius: 9px;
    padding: 6px 13px;
    color: {c['TEXT_DIM']};
    font-weight: 500;
}}
QPushButton#navMore:hover {{ background-color: {c['SURFACE_HI']}; color: {c['TEXT']}; }}
QPushButton#navMore:checked {{
    background-color: {c['SURFACE_HI']};
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
    border-radius: 11px;
    padding: 10px 13px;
    font-size: {p(15)}pt;
}}
QListWidget#paletteList {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget#paletteList::item {{
    padding: 8px 11px;
    border-radius: 8px;
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
