"""首次运行向导（4 步）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .. import __version__, autostart
from ..config import gen_api_key, port_free
from ..paths import find_python, icon_path, is_gateway_dir

STEPS = ["欢迎", "运行环境", "客户端", "启动方式"]

HINTS = [
    f"版本 {__version__}　·　点击「下一步」开始，约 1 分钟。",
    "端口若被占用，萝卜盒会提示你换一个（默认 8788）。",
    "接入后若想还原，到「客户端接入」页点「还原备份」即可。",
    "推荐：8443（443 常被同机其他服务占用）。",
]


class StepDots(QWidget):
    """顶部的步骤指示器。"""

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self.labels = labels
        self.current = 0
        self.setFixedHeight(46)

    def set_current(self, index: int) -> None:
        self.current = index
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QFont, QPainter, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.labels)
        if n == 0:
            return
        w = self.width()
        gap = w / n
        cy = 16
        f = QFont()
        f.setPointSize(8)
        p.setFont(f)

        for i, label in enumerate(self.labels):
            cx = gap * i + gap / 2
            done = i < self.current
            active = i == self.current
            if i < n - 1:
                p.setPen(QPen(QColor(theme.OK if done else theme.BORDER), 1.4))
                p.drawLine(int(cx + 10), cy, int(cx + gap - 10), cy)
            color = QColor(theme.OK if done else (theme.ACCENT if active else theme.BORDER))
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(int(cx - 7), cy - 7, 14, 14)
            if done:
                p.setPen(QPen(QColor("#FFFFFF"), 1.6))
                p.drawLine(int(cx - 3), cy, int(cx - 1), cy + 3)
                p.drawLine(int(cx - 1), cy + 3, int(cx + 3), cy - 3)

            p.setPen(QColor(theme.TEXT if active or done else theme.TEXT_MUTE))
            p.drawText(QRectF(cx - gap / 2, 28, gap, 18), Qt.AlignCenter, label)


class FirstRunWizard(QDialog):
    """返回 True 表示用户走完了向导。"""

    finished_ok = Signal(bool)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(f"萝卜盒 · 首次设置向导")
        if icon_path().is_file():
            self.setWindowIcon(QIcon(str(icon_path())))
        self.setMinimumSize(660, 560)
        self._build()
        self._goto(0)

    # ---------------------------------------------------------------- 构建

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(14)

        self.dots = StepDots(STEPS)
        root.addWidget(self.dots)

        # hint 必须在各 step 构造**之前**建好 —— 每个 _step_* 都会往里写提示文案，
        # 而 step 是在 addWidget 时就被构造的。
        self.hint = QLabel("")
        self.hint.setObjectName("mute")
        self.hint.setWordWrap(True)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._step_welcome())
        self.stack.addWidget(self._step_runtime())
        self.stack.addWidget(self._step_clients())
        self.stack.addWidget(self._step_launch())
        root.addWidget(self.stack, 1)

        root.addWidget(self.hint)

        bar = QHBoxLayout()
        self.btn_skip = QPushButton("跳过向导")
        self.btn_skip.setObjectName("ghost")
        self.btn_skip.setCursor(Qt.PointingHandCursor)
        bar.addWidget(self.btn_skip)
        bar.addStretch(1)

        self.btn_back = QPushButton("上一步")
        self.btn_next = QPushButton("下一步")
        self.btn_next.setObjectName("primary")
        for b in (self.btn_back, self.btn_next):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(96)
            b.setMinimumHeight(34)
        bar.addWidget(self.btn_back)
        bar.addWidget(self.btn_next)
        root.addLayout(bar)

        self.btn_skip.clicked.connect(self._skip)
        self.btn_back.clicked.connect(lambda: self._goto(self.stack.currentIndex() - 1))
        self.btn_next.clicked.connect(self._next)

    # ---------------------------------------------------------------- 步骤

    def _step_welcome(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)

        title = QLabel("欢迎使用萝卜盒")
        title.setObjectName("h1")
        box.addWidget(title)

        sub = QLabel(
            "萝卜盒是你本机 codebuddy2api 网关的桌面控制台 —— "
            "它负责把网关跑起来、开关公网入口、把 Codex / Claude Code 接到网关，"
            "并在网关升级后自动补回必需的小补丁。"
        )
        sub.setWordWrap(True)
        sub.setObjectName("dim")
        box.addWidget(sub)

        from .widgets import Card

        card = Card("它不会做的事")
        for line in (
            "· 不修改 codebuddy2api 的源码结构（只有脱敏词表补丁例外，且改前必先备份）",
            "· 不上传任何数据；账号凭据始终留在本机 auth/ 目录",
            "· 不会执行 tailscale funnel reset（那会破坏同机其他服务的公网入口）",
        ):
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            lbl.setObjectName("dim")
            card.add(lbl)
        box.addWidget(card)

        card2 = Card("数据存放位置")
        from ..paths import data_dir

        lbl = QLabel(str(data_dir()))
        lbl.setObjectName("mono")
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card2.add(lbl)
        lbl2 = QLabel("配置、日志、备份都在这里；卸载时整个目录删掉即可，不留残余。")
        lbl2.setObjectName("mute")
        lbl2.setWordWrap(True)
        card2.add(lbl2)
        box.addWidget(card2)

        box.addStretch(1)
        self.hint.setText(f"版本 {__version__}　·　点击「下一步」开始，约 1 分钟。")
        return page

    def _step_runtime(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)

        title = QLabel("运行环境")
        title.setObjectName("h1")
        box.addWidget(title)
        sub = QLabel("萝卜盒需要用你已有的 Python 来跑网关。点「自动探测」帮你找到带依赖的那个。")
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        box.addWidget(sub)

        from .widgets import Card

        card = Card("")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.w_dir = QLineEdit(str(self.ctx.config.get("gateway.dir")))
        form.addRow("网关目录", self.w_dir)

        py_row = QHBoxLayout()
        self.w_py = QLineEdit(str(self.ctx.config.get("gateway.python")))
        self.btn_probe = QPushButton("自动探测")
        self.btn_probe.setObjectName("ghost")
        self.btn_probe.setCursor(Qt.PointingHandCursor)
        self.btn_probe.clicked.connect(self._probe)
        py_row.addWidget(self.w_py, 1)
        py_row.addWidget(self.btn_probe)
        pw = QWidget()
        pw.setLayout(py_row)
        form.addRow("Python", pw)

        # 探测失败时的兜底入口：直接把下载地址摆在眼前，别让用户自己去搜。
        # 默认藏起来 —— 本机 Python 好使的用户不该被"没装 Python？"打扰，
        # 进这一页会自动探测，命中就保持隐藏，没命中才浮出来。
        from .widgets import python_download_tip

        self.py_dl_tip = python_download_tip()
        self.py_dl_tip.setVisible(False)
        form.addRow("", self.py_dl_tip)

        self.w_port = QSpinBox()
        self.w_port.setRange(1, 65535)
        self.w_port.setValue(int(self.ctx.config.get("gateway.port", 8788)))
        self.w_port.valueChanged.connect(self._port_changed)
        form.addRow("端口", self.w_port)

        key_row = QHBoxLayout()
        self.w_key = QLineEdit(str(self.ctx.config.get("gateway.api_key")))
        btn_key = QPushButton("重新生成")
        btn_key.setObjectName("ghost")
        btn_key.setCursor(Qt.PointingHandCursor)
        btn_key.clicked.connect(lambda: self.w_key.setText(gen_api_key()))
        key_row.addWidget(self.w_key, 1)
        key_row.addWidget(btn_key)
        kw = QWidget()
        kw.setLayout(key_row)
        form.addRow("API Key", kw)
        card.add_layout(form)
        box.addWidget(card)

        self.probe_out = QLabel("")
        self.probe_out.setObjectName("mono")
        self.probe_out.setWordWrap(True)
        box.addWidget(self.probe_out)
        box.addStretch(1)

        self.hint.setText("端口若被占用，萝卜盒会提示你换一个（默认 8788）。")
        return page

    def _step_clients(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)

        title = QLabel("接入客户端")
        title.setObjectName("h1")
        box.addWidget(title)
        sub = QLabel("勾选的客户端会被自动指向萝卜盒网关。改动前会先备份原配置，随时能还原。")
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        box.addWidget(sub)

        from .widgets import Card

        card = Card("Codex（桌面版 / CLI）")
        self.w_codex = QCheckBox("接入 Codex")
        self.w_codex.setChecked(True)
        card.add(self.w_codex)
        row = QHBoxLayout()
        row.addWidget(QLabel("默认模型"))
        self.w_model = QComboBox()
        self.w_model.setEditable(True)
        self.w_model.addItems(["deepseek-v4.1-flash", "glm-5.3", "deepseek-v4-pro",
                               "kimi-k2.7", "minimax-m3", "auto"])
        self.w_model.setCurrentText(str(self.ctx.config.get("clients.codex.model")))
        row.addWidget(self.w_model, 1)
        card.add_layout(row)
        note = QLabel("会把 config.toml 的 model_context_window 设为 200000 —— "
                      "上游实测上限约 200K，写太大会被硬拒。")
        note.setObjectName("mute")
        note.setWordWrap(True)
        card.add(note)
        box.addWidget(card)

        card2 = Card("Claude Code / Anthropic 兼容")
        self.w_claude = QCheckBox("接入 Claude Code")
        card2.add(self.w_claude)
        note2 = QLabel("写入 ~/.claude/settings.json 的 env 段。"
                       "如果你用的是 CC Switch 这类切换器，也可以只复制片段手动粘贴。")
        note2.setObjectName("mute")
        note2.setWordWrap(True)
        card2.add(note2)
        box.addWidget(card2)

        box.addStretch(1)
        self.hint.setText("接入后若想还原，到「客户端接入」页点「还原备份」即可。")
        return page

    def _step_launch(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)

        title = QLabel("启动方式")
        title.setObjectName("h1")
        box.addWidget(title)
        sub = QLabel("决定萝卜盒和网关怎么跟着系统走。随时可以在「设置」里改。")
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        box.addWidget(sub)

        from .widgets import Card

        card = Card("开机与常驻")
        self.w_autostart = QCheckBox("开机自启（登录时自动运行萝卜盒）")
        self.w_gw_auto = QCheckBox("启动萝卜盒时自动拉起网关")
        self.w_gw_auto.setChecked(True)
        self.w_min_tray = QCheckBox("关闭窗口时最小化到托盘，不退出")
        self.w_min_tray.setChecked(True)
        self.w_stop_exit = QCheckBox("退出萝卜盒时同时停止网关")
        for c in (self.w_autostart, self.w_gw_auto, self.w_min_tray, self.w_stop_exit):
            card.add(c)
        box.addWidget(card)

        card2 = Card("公网入口（可选）")
        self.w_funnel = QCheckBox("开启 Tailscale Funnel，让外网也能用")
        card2.add(self.w_funnel)
        warn = QLabel("提醒：公网开启后，唯一的防线就是 API Key。"
                      "网关本身没有限流和配额，Key 一旦泄露会被无成本消耗订阅额度。"
                      "不用时记得关掉。")
        warn.setObjectName("mute")
        warn.setWordWrap(True)
        card2.add(warn)
        row = QHBoxLayout()
        row.addWidget(QLabel("Funnel 端口"))
        self.w_funnel_port = QComboBox()
        self.w_funnel_port.addItems(["8443", "10000", "443"])
        self.w_funnel_port.setCurrentText("8443")
        row.addWidget(self.w_funnel_port, 1)
        card2.add_layout(row)
        box.addWidget(card2)

        box.addStretch(1)
        self.hint.setText("推荐：8443（443 常被同机其他服务占用）。")
        return page

    # ---------------------------------------------------------------- 逻辑

    def _goto(self, index: int) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        self.stack.setCurrentIndex(index)
        self.dots.set_current(index)
        self.hint.setStyleSheet(f"color: {theme.TEXT_MUTE};")
        self.hint.setText(HINTS[index] if index < len(HINTS) else "")
        self.btn_back.setEnabled(index > 0)
        last = index == self.stack.count() - 1
        self.btn_next.setText("完成" if last else "下一步")
        if index == 1 and not self.probe_out.text():
            self._probe()

    def _next(self) -> None:
        idx = self.stack.currentIndex()
        if idx == 1 and not self._validate_runtime():
            return
        if idx == self.stack.count() - 1:
            self._finish()
            return
        self._goto(idx + 1)

    def _validate_runtime(self) -> bool:
        gw = Path(self.w_dir.text().strip())
        if not is_gateway_dir(gw):
            self._warn("网关目录里找不到 converter.py，请确认目录是否正确。")
            return False
        if not Path(self.w_py.text().strip()).exists():
            self._warn("Python 解释器路径不存在，请点「自动探测」，或从下方链接下载安装。")
            return False
        port = int(self.w_port.value())
        if not port_free(port):
            self._warn(f"端口 {port} 已被占用，请换一个（试试 {port + 1}）。")
            return False
        if not self.w_key.text().strip():
            self._warn("API Key 不能为空。")
            return False
        return True

    def _warn(self, text: str) -> None:
        self.hint.setText(f"⚠ {text}")
        self.hint.setStyleSheet(f"color: {theme.WARN};")

    def _probe(self) -> None:
        self.probe_out.setText("探测中…")
        self.probe_out.setStyleSheet(f"color: {theme.TEXT_DIM};")
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        py, report = find_python(self.w_py.text().strip() or None)
        # 候选里绝大多数是"这台机器上根本没这个路径"，全列出来只会淹没有用信息，
        # 折成一行计数；真正被检查过但不合格的（缺依赖 / 调用失败）才逐条显示。
        lines: list[str] = []
        missing = 0
        for cand, why in report:
            if why == "文件不存在":
                missing += 1
                continue
            lines.append(f"{'✓' if why == '可用' else '✗'} {cand}   {why}")
        if missing:
            lines.append(f"（另有 {missing} 个候选路径不存在，已省略）")
        self.probe_out.setText("\n".join(lines[:8]) or "未发现任何 Python 解释器。")
        if py:
            self.py_dl_tip.setVisible(False)
            self.w_py.setText(str(py))
            self.hint.setStyleSheet(f"color: {theme.OK};")
            self.hint.setText(f"已选中可用解释器：{py}")
            return
        # 没探到才把下载入口亮出来（这就是"自动适配"：有 Python 时界面不留噪音）。
        self.py_dl_tip.setVisible(True)
        # 失败分两种：装了但缺依赖 vs 压根没装 —— 两者下一步动作完全不同。
        if any(why.startswith(("缺依赖", "调用失败")) for _, why in report):
            self._warn("找到 Python 但缺少依赖，请执行 pip install fastapi uvicorn httpx 后重试。")
        else:
            self._warn("这台机器上没找到 Python，点 Python 下方的「下载安装包」链接装一个。")

    def _port_changed(self, value: int) -> None:
        if not port_free(value):
            self._warn(f"端口 {value} 已被占用。")

    def _skip(self) -> None:
        self.ctx.config.set("app.first_run_done", True)
        self.ctx.config.save()
        self.finished_ok.emit(False)
        self.accept()

    def _finish(self) -> None:
        cfg = self.ctx.config
        cfg.backup("config-before-wizard")
        cfg.set("gateway.dir", self.w_dir.text().strip())
        cfg.set("gateway.python", self.w_py.text().strip())
        cfg.set("gateway.port", int(self.w_port.value()))
        cfg.set("gateway.api_key", self.w_key.text().strip())
        cfg.set("clients.codex.model", self.w_model.currentText().strip())
        cfg.set("clients.codex.enabled", self.w_codex.isChecked())
        cfg.set("clients.claude.enabled", self.w_claude.isChecked())
        cfg.set("gateway.auto_start", self.w_gw_auto.isChecked())
        cfg.set("app.minimize_to_tray", self.w_min_tray.isChecked())
        cfg.set("gateway.stop_on_exit", self.w_stop_exit.isChecked())
        cfg.set("funnel.enabled", self.w_funnel.isChecked())
        cfg.set("funnel.port", self.w_funnel_port.currentText())
        cfg.set("app.first_run_done", True)
        cfg.save()

        results: list[str] = []

        if self.w_autostart.isChecked():
            ok, msg = autostart.set_autostart(True)
            results.append(("开机自启已开启" if ok else f"开机自启失败：{msg}"))

        if self.w_codex.isChecked():
            ok, msg = self.ctx.codex.apply()
            results.append(msg if ok else f"Codex 接入失败：{msg}")
        if self.w_claude.isChecked():
            ok, msg = self.ctx.claude.apply()
            results.append(msg if ok else f"Claude 接入失败：{msg}")

        self._summary = results
        self.finished_ok.emit(True)
        self.accept()

    @property
    def summary(self) -> list[str]:
        return getattr(self, "_summary", [])
