"""GUI 冒烟测试：构建真实窗口（用临时数据目录，不碰用户的真实配置），
逐页签渲染成 PNG，便于肉眼检查布局。

刻意**不进入 app.exec()** —— 只手动 processEvents 若干次后立刻退出。
无头环境下跑事件循环容易被外部终止，而且冒烟测试本来也不需要真跑循环。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
# 默认必须用 windows 平台：offscreen 平台取不到系统字体，
# 中文会整片渲染成豆腐块（□），看图的人会以为程序坏了。
# 想跑无头就显式 QT_QPA_PLATFORM=offscreen，但别拿那种图当验收依据。
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "tests" / "_shots"
OUT.mkdir(parents=True, exist_ok=True)

TMP = Path(tempfile.mkdtemp(prefix="luobobox-gui-"))
os.environ["LUOBOBOX_DATA_DIR"] = str(TMP)


def log(msg: str) -> None:
    print(msg, flush=True)


def pump(app, rounds: int = 6) -> None:
    for _ in range(rounds):
        app.processEvents()


def main() -> int:
    log("[1] 导入 PySide6…")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    log("[2] 导入萝卜盒模块…")
    from luobobox import __version__
    from luobobox.config import Config
    from luobobox.context import AppContext
    from luobobox.paths import default_gateway_dir, icon_path
    from luobobox.ui import theme
    from luobobox.ui.main_window import MainWindow
    from luobobox.ui.wizard import FirstRunWizard

    log("[3] 创建 QApplication…")
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    if icon_path().is_file():
        app.setWindowIcon(QIcon(str(icon_path())))
    log(f"    platform = {app.platformName()}")

    log("[4] 准备临时配置…")
    cfg = Config.load()
    cfg.set("gateway.dir", str(default_gateway_dir()))
    cfg.set("gateway.auto_start", False)
    cfg.set("funnel.enabled", False)
    cfg.set("app.first_run_done", True)
    cfg.save()

    log("[5] 构建 AppContext…")
    ctx = AppContext(cfg)
    log("[5b] 环境自愈（探测 Python / 端口）…")
    for line in ctx.ensure_ready():
        log(f"     {line}")

    log("[6] 构建 MainWindow…")
    win = MainWindow(ctx)
    win.resize(960, 760)
    win.show()
    pump(app, 10)

    tabs = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    log(f"    页签：{tabs}")

    for i, name in enumerate(tabs):
        win.tabs.setCurrentIndex(i)
        pump(app, 6)
        path = OUT / f"{i + 1:02d}_{name}.png"
        ok = win.grab().save(str(path))
        log(f"[7] 截图 {'OK ' if ok else 'FAIL'} {path.name}")

    log("[8] 构建向导…")
    wiz = FirstRunWizard(ctx, None)
    wiz.resize(700, 620)
    wiz.show()
    pump(app, 6)
    names = ["欢迎", "运行环境", "客户端", "启动方式"]
    for i in range(wiz.stack.count()):
        wiz._goto(i)  # noqa: SLF001
        pump(app, 5)
        path = OUT / f"1{i + 1}_向导_{names[i]}.png"
        ok = wiz.grab().save(str(path))
        log(f"    截图 {'OK ' if ok else 'FAIL'} {path.name}")
    wiz.close()

    ctx.stop_timers()
    win.hide()
    pump(app, 3)

    log(f"\n版本 {__version__}")
    log(f"临时数据目录 {TMP}")
    log(f"截图 {len(list(OUT.glob('*.png')))} 张 -> {OUT}")
    for f in sorted(OUT.glob("*.png")):
        log(f"  {f.name}")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback

        traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
