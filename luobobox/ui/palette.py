"""Ctrl+K 命令面板：键盘流用户的主入口。

为什么值得做：这个程序有 7 个页签 + 十几个动作，散在菜单、页头、卡片按钮里。
熟练用户不想"找" —— 他们想直接打两个字母就跳过去。

两条设计约束：

1. **面板与菜单同源**。命令全部由 `build_commands(window)` 从主窗口摊平，
   而不是在面板里另抄一份。新增功能只需在 build_commands 里加一行，
   面板永远搜得到 —— 否则它三周后就会变成"搜不到东西的面板"。
2. **匹配逻辑是纯函数**（`fuzzy_score` / `rank`），不依赖 Qt。
   这样它可以在没有 GUI 的测试环境里被逐条断言。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..paths import data_dir, log_dir
from . import motion, theme


@dataclass
class Command:
    """一条可执行命令。`key` 只用于测试与去重。"""

    key: str
    title: str
    group: str = ""
    hint: str = ""
    run: object = None

    @property
    def searchable(self) -> str:
        return f"{self.title} {self.group} {self.hint}"


def fuzzy_score(needle: str, haystack: str) -> int | None:
    """子序列模糊匹配：命中返回分数（越大越靠前），未命中返回 None。

    规则：needle 的字符要按顺序出现在 haystack 里（不要求连续）。
    越靠前命中、连续命中的字符越多、越像前缀，分数越高。
    这样 "qd" 能命中 "立即签到"（走 hint 里的拼音首字母），
    "更新" 能同时命中 "检查萝卜盒更新" 与 "检查网关更新"。
    """
    if not needle:
        return 0
    n = needle.lower().strip()
    if not n:
        return 0
    h = haystack.lower()
    score = 0
    pos = 0
    streak = 0
    for ch in n:
        idx = h.find(ch, pos)
        if idx < 0:
            return None
        streak = streak + 1 if idx == pos else 0
        score += 10 + streak * 8 - min(idx, 20)
        pos = idx + 1
    if h.startswith(n):
        score += 45
    return score


def rank(commands: list[Command], query: str) -> list[Command]:
    """按相关度排序并滤掉未命中的命令。空查询按原顺序全量返回。"""
    q = (query or "").strip()
    if not q:
        return list(commands)
    scored: list[tuple[int, int, Command]] = []
    for i, cmd in enumerate(commands):
        s = fuzzy_score(q, cmd.searchable)
        if s is not None:
            scored.append((-s, i, cmd))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in scored]


def _goto(window, key: str):
    return lambda: window.goto_tab(key)


def _accent(window, key: str):
    return lambda: window.apply_appearance(accent=key)


def _palette(window, name: str):
    return lambda: window.apply_appearance(palette=name)


def build_commands(window) -> list[Command]:
    """把主窗口的能力摊平成命令列表。

    只依赖 window 的公开方法（tab_keys / tab_label / goto_tab /
    apply_appearance 以及既有的动作方法），因此在测试里塞一个假窗口
    也能完整验证"命令齐不齐"。
    """
    cmds: list[Command] = []
    for key in window.tab_keys():
        label = window.tab_label(key)
        cmds.append(Command(f"tab:{key}", f"打开「{label}」", "页面",
                            f"{key} tab {label}", _goto(window, key)))

    cmds += [
        Command("act:start", "启动网关", "服务", "start qidong", window.ctx.start_gateway),
        Command("act:stop", "停止网关", "服务", "stop tingzhi", window.ctx.stop_gateway),
        Command("act:restart", "重启网关", "服务", "restart chongqi",
                window.ctx.restart_gateway),
        Command("act:checkin", "立即签到", "服务", "checkin qd qiandao",
                window._checkin),  # noqa: SLF001
        Command("act:repatch", "一键重打脱敏补丁", "服务", "patch tubuding",
                window.ctx.repatch),
        Command("act:dashboard", "打开网页版管理台", "服务", "webui dashboard guanlitai",
                window._open_dashboard),  # noqa: SLF001
        Command("act:addcred", "添加账号（扫码 / 导入）", "凭证", "add account zhanghao",
                window._open_credentials_page),  # noqa: SLF001
        Command("act:copykey", "复制 API Key", "凭证", "copy key apikey",
                window.copy_api_key),
        Command("act:package", "复制接入包（Markdown）", "凭证", "package jierubao md",
                window.copy_access_package),
        Command("act:appupdate", "检查萝卜盒更新", "更新", "app update luobobox",
                window._check_app_update),  # noqa: SLF001
        Command("act:gwupdate", "检查网关更新", "更新", "gateway update wangguan",
                window._check_update),  # noqa: SLF001
        Command("act:diag", "环境体检（诊断）", "设置", "diag doctor tijian",
                window._run_diag),  # noqa: SLF001
        # 先切到「设置」页再动手：修复过程要往 env_out 里滚日志，
        # 用户在别的页面上只会看到"什么也没发生"。
        Command("act:envsetup", "一键配置环境（体检 + 修复）", "设置",
                "env setup huanjing peizhi oneclick yijian",
                lambda: (window.goto_tab("settings"), window.run_env_setup())),
        Command("act:logdir", "打开日志目录", "设置", "log dir rizhi",
                lambda: window._open_path(log_dir())),  # noqa: SLF001
        Command("act:datadir", "打开数据目录", "设置", "data dir shuju",
                lambda: window._open_path(data_dir())),  # noqa: SLF001
        Command("act:refresh", "刷新状态", "通用", "refresh f5 shuaxin",
                window.refresh_all),
        Command("act:quit", "退出萝卜盒", "通用", "quit exit tuichu",
                window.request_quit),
    ]

    for key, label in theme.accent_choices():
        cmds.append(Command(f"theme:accent:{key}", f"强调色：{label}", "外观",
                            f"accent {key} qiangdiao", _accent(window, key)))
    for name, label in (("dark", "深色"), ("light", "浅色")):
        cmds.append(Command(f"theme:palette:{name}", f"主题基调：{label}", "外观",
                            f"theme palette {name} zhuti", _palette(window, name)))
    return cmds


class CommandPalette(QDialog):
    """输入即过滤的命令面板。回车执行，Esc 关闭。

    执行的时机刻意放在 `exec()` 返回**之后**（见 `chosen`）：
    在模态循环里直接跑动作（例如弹确认框、切页签）容易出现
    "面板还盖在上面"的怪异层级，先收起再执行最稳。
    """

    def __init__(self, parent, commands: list[Command]):
        super().__init__(parent)
        self.setObjectName("palette")
        self.setWindowTitle("命令面板")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.resize(520, 380)

        self._commands = list(commands)
        self.chosen: Command | None = None

        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(8)

        self.input = QLineEdit()
        self.input.setObjectName("paletteInput")
        self.input.setPlaceholderText("输入命令…   ↑↓ 选择 · Enter 执行 · Esc 关闭")
        self.input.installEventFilter(self)
        col.addWidget(self.input)

        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(self._activate)
        self.list.itemClicked.connect(self._activate)
        col.addWidget(self.list, 1)

        self.footer = QLabel("")
        self.footer.setObjectName("mute")
        col.addWidget(self.footer)

        self.input.textChanged.connect(self._refill)
        self._refill("")

    # ------------------------------------------------------------- 数据

    def _refill(self, text: str) -> None:
        hits = rank(self._commands, text)
        self.list.clear()
        for cmd in hits:
            item = QListWidgetItem(f"{cmd.title}          {cmd.group}")
            item.setData(Qt.UserRole, cmd.key)
            self.list.addItem(item)
        if hits:
            self.list.setCurrentRow(0)
        self.footer.setText(
            f"{len(hits)} / {len(self._commands)} 条命令" if text.strip()
            else f"共 {len(self._commands)} 条命令")

    def _current(self) -> Command | None:
        item = self.list.currentItem()
        if item is None:
            return None
        key = item.data(Qt.UserRole)
        for cmd in self._commands:
            if cmd.key == key:
                return cmd
        return None

    def _move(self, delta: int) -> None:
        n = self.list.count()
        if not n:
            return
        row = (self.list.currentRow() + delta) % n
        self.list.setCurrentRow(row)

    def _activate(self, *_args) -> None:
        cmd = self._current()
        if cmd is None:
            return
        self.chosen = cmd
        self.accept()

    # ------------------------------------------------------------- 事件

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Down:
                self._move(1)
                return True
            if key == Qt.Key_Up:
                self._move(-1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._activate()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 居中到父窗口上半部（命令面板的惯常位置，不挡住下面的内容）
        parent = self.parentWidget()
        if parent is not None:
            geo = parent.geometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.top() + max(60, geo.height() // 6))
        self.input.setFocus()
        motion.fade_in(self, motion.FAST)


def open_palette(parent, commands: list[Command]) -> Command | None:
    """跑一次面板，返回用户选中的命令（取消返回 None）。

    动作由调用方在返回之后执行 —— 面板已经收起来了，
    再弹确认框 / 切页签就不会出现层级打架。
    """
    dlg = CommandPalette(parent, commands)
    dlg.exec()
    return dlg.chosen
