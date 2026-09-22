"""GUI 布局 / 交互回归自测（无头可跑）。

覆盖这一轮 GUI 升级里**最容易悄悄坏掉**的那几类东西：

  A. 页签注册表：每个页签都必须能用 key 跳到；绝不允许再出现
     `tabs.setCurrentIndex(<字面量>)`（托盘"检查更新"跳错页的老 bug）。
  B. 导航分组：4 个主入口 + 「⋯ 更多」，所有页签都可达。
  C. 快捷键表：Ctrl+1..N 严格按 tab_keys() 顺序。
  D. 主题引擎：8 套配色 × 3 档字号都能产出合法样式表；
     颜色必须是"活"的（换肤后 health_label 立刻跟着变）。
  E. 提示条：错误常驻、点一下复制全文。
  F. 命令面板 / 日志过滤 / 图表 / 空态 / 窗口几何 / 动效降级 / 新版本角标。

跑法（必须有 PySide6）：
    <venv-python> tests/gui_layout_selftest.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE
sys.path.insert(0, str(BASE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TMP = Path(tempfile.mkdtemp(prefix="luobobox-layout-"))
os.environ["LUOBOBOX_DATA_DIR"] = str(TMP)
# 指针两个位置都改道：本文件会跑 envsetup.diagnose()（含 pointer 项），
# 不改道的话它会去读**本机真实安装版**的迁移指针 —— 虽然 diagnose 只是读，
# 但结果会随机器状态漂移（有迁移的机器上 pointer 项会变成 fix），
# 改到临时目录后行为确定。源码模式下「程序目录旁」= 仓库根，也必须改道。
os.environ["LUOBOBOX_POINTER_DIR"] = str(TMP / "appdir")
os.environ["LUOBOBOX_LEGACY_POINTER_DIR"] = str(TMP / "appdir-legacy")

_passed = 0
_failed: list[str] = []


def check(name: str, cond, extra: str = "") -> bool:
    global _passed
    ok = bool(cond)
    if ok:
        _passed += 1
    else:
        _failed.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))
    return ok


def section(title: str) -> None:
    print(f"\n---- {title} " + "-" * max(0, 56 - len(title)))


def code_only(src: str) -> str:
    """把注释与字符串字面量抹成空格，只留可执行代码（保持行列位置不变）。

    为什么要这样扫：tray.py 的 docstring 里**特意**留了一句
    「这里以前是 tabs.setCurrentIndex(3)」—— 那是防复发的说明，不是代码。
    直接对源码做正则，会把这段说明判成违规；于是要么删掉这条有价值的注释，
    要么把检查做成摆设。抹掉注释与字符串，两边都能保住。
    """
    import io
    import tokenize

    lines = src.splitlines(keepends=True)
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln))

    chars = list(src)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src

    for tok in toks:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        start = offsets[tok.start[0] - 1] + tok.start[1]
        end = offsets[tok.end[0] - 1] + tok.end[1]
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def lines_of(text: str) -> list[str]:
    """按行比较日志内容。

    QPlainTextEdit.toPlainText() 会吃掉末尾换行（"a\\nb\\nc\\n" → "a\\nb\\nc"），
    直接和原文 == 比会稳定误报。比行列表才是"内容是否一致"的正确问法。
    """
    return text.splitlines()


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    from luobobox.config import Config
    from luobobox.context import AppContext
    from luobobox.paths import default_gateway_dir
    from luobobox.ui import charts, motion, palette, theme
    from luobobox.ui.main_window import (
        TAB_PRIMARY,
        MainWindow,
        _toolbar_flags,
        health_label,
    )
    from luobobox.ui.widgets import EmptyState, IOSSwitch, StatusDot, StatusRing, Toast

    app.setStyleSheet(theme.stylesheet())

    # ============================================================ 准备窗口
    section("准备：临时配置 + 真实 MainWindow")
    cfg = Config.load()
    cfg.set("gateway.dir", str(default_gateway_dir()))
    cfg.set("gateway.auto_start", False)
    cfg.set("funnel.enabled", False)
    cfg.set("app.first_run_done", True)
    cfg.save()

    ctx = AppContext(cfg)
    win = MainWindow(ctx)
    win.resize(1000, 780)
    # 模拟 app.py：额度页是在 _build 之后注册的
    import luobobox.usage_view as uv

    win.add_tab("usage", uv.build_usage_tab(ctx), "额度消耗")
    # 必须真的 show：_refresh_log / _refresh_clients 都以 isVisible() 为前置条件，
    # 隐藏窗口下它们会直接 return，测出来的是"什么都没发生"而不是真实行为。
    win.show()
    for _ in range(6):
        app.processEvents()
    check("窗口构建成功", win is not None)
    check("页签数量 = 7", win.tabs.count() == 7, win.tabs.count())

    # ============================================================ A 注册表
    section("A. 页签注册表：key 是唯一入口")
    keys = win.tab_keys()
    check("tab_keys 顺序符合预期",
          keys == ["overview", "admin", "clients", "logs", "update", "settings", "usage"],
          keys)
    check("每个页签都登记了 label",
          all(win.tab_label(k) and win.tab_label(k) != k for k in keys))

    for k in keys:
        ok = win.goto_tab(k)
        check(f"goto_tab('{k}') 命中第 {win._tab_index[k]} 页",
              ok and win.tab_key() == k, win.tab_key())
    check("goto_tab('不存在的页') 返回 False 且不改变当前页",
          win.goto_tab("nope") is False and win.tab_key() == keys[-1])

    # 老 bug 的回归护栏：托盘"检查更新"必须落在「更新」而不是「日志」
    check("update 与 logs 不是同一页",
          win._tab_index["update"] != win._tab_index["logs"])
    check("日志页的确是索引 3（旧代码写死 3 = 跳到日志）",
          win._tab_index["logs"] == 3, win._tab_index["logs"])

    tray_src = (ROOT / "luobobox" / "ui" / "tray.py").read_text(encoding="utf-8")
    check("tray.py 不再写死 setCurrentIndex(数字)",
          not re.search(r"tabs\.setCurrentIndex\(\s*\d", code_only(tray_src)))
    check('tray.py 改调 goto_tab("update")', 'goto_tab("update")' in tray_src)

    hard = []
    for p in (ROOT / "luobobox").rglob("*.py"):
        for m in re.finditer(r"tabs\.setCurrentIndex\(\s*\d+\s*\)",
                             code_only(p.read_text(encoding="utf-8"))):
            hard.append(f"{p.name}:{m.group(0)}")
    check("全包内无 tabs.setCurrentIndex(<字面量>)", not hard, hard)

    # ============================================================ B 导航分组
    section("B. 导航分组：4 主入口 + ⋯ 更多")
    check("主入口恰好 4 个", len(win._nav_buttons) == 4, list(win._nav_buttons))
    check("主入口 = TAB_PRIMARY", tuple(win._nav_buttons) == TAB_PRIMARY,
          list(win._nav_buttons))
    check("溢出项 = 其余 3 页",
          sorted(win._overflow_keys) == ["settings", "update", "usage"],
          win._overflow_keys)
    check("主入口能真正切页",
          all(win.goto_tab(k) and win.tab_key() == k for k in TAB_PRIMARY))
    check("溢出页也能切（goto_tab 是唯一入口）",
          all(win.goto_tab(k) and win.tab_key() == k for k in win._overflow_keys))
    check("原生 tab bar 已隐藏", not win.tabs.tabBar().isVisible())

    win.goto_tab("update")
    check("停在溢出页时「更多」按钮显示当前页名",
          win.btn_more.text() == "⋯ 更新", win.btn_more.text())
    win.goto_tab("overview")
    check("回到主入口时「更多」按钮复位",
          win.btn_more.text() == "⋯ 更多", win.btn_more.text())
    check("主入口按钮高亮跟随当前页",
          win._nav_buttons["overview"].isChecked()
          and not win._nav_buttons["logs"].isChecked())

    # ---- 「⋯ 更多」的 2 列磁贴面板（取代老的竖排 QMenu）
    # 注意：本函数后面还有一处 `from PySide6.QtCore import Qt`，那会让 Qt 变成
    # 整个函数的**局部名**。这里必须自己再导入一次，否则在那一行之前用 Qt 会
    # 直接 UnboundLocalError（踩过）。
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from luobobox.ui.widgets import TilePanel

    panel = win._more_panel
    check("「更多」挂的是 TilePanel，不再有 QMenu",
          isinstance(panel, TilePanel) and win.btn_more.menu() is None)
    check("磁贴 key 与溢出项一一对应（顺序也一致）",
          panel.keys() == list(win._overflow_keys),
          f"{panel.keys()} vs {win._overflow_keys}")
    check("每格 = 该页标题 + 一句说明",
          all(t.title_label.text() == win.tab_label(t.key)
              and t.hint_label.text() == win.tab_hint(t.key)
              for t in panel.tiles()),
          [(t.key, t.title_label.text(), t.hint_label.text()) for t in panel.tiles()])

    panel.hide()
    win.goto_tab("settings")
    win._toggle_more_panel()
    for _ in range(6):
        app.processEvents()
    check("点「更多」真的弹出面板", panel.isVisible())
    check("弹出时按钮呈按下态", win.btn_more.isChecked())
    check("当前所在页那格被高亮，且只有一格",
          [t.key for t in panel.tiles() if t.is_active()] == ["settings"],
          [t.key for t in panel.tiles() if t.is_active()])
    btn_bottom = win.btn_more.mapToGlobal(QPoint(0, win.btn_more.height())).y()
    btn_right = win.btn_more.mapToGlobal(QPoint(win.btn_more.width(), 0)).x()
    check("面板贴在按钮下方、右边缘对齐",
          panel.y() >= btn_bottom
          and abs(panel.x() + panel.width() - btn_right) <= 2,
          f"panel=({panel.x()},{panel.y()},{panel.width()},{panel.height()}) "
          f"btn_bottom={btn_bottom} btn_right={btn_right}")

    widths = {t.width() for t in panel.tiles()}
    heights = sorted(t.height() for t in panel.tiles())
    check("磁贴等宽", len(widths) == 1, widths)
    check("磁贴等高（末行不许矮一截）", heights[-1] - heights[0] <= 1, heights)
    # 位置要等面板真的弹出来才有值（没 show 过时所有格子都在 (0,0)）
    check("是 2 列布局（第 2 格与第 1 格同一行、在其右侧）",
          len(panel.tiles()) >= 3
          and panel.tiles()[1].y() == panel.tiles()[0].y()
          and panel.tiles()[1].x() > panel.tiles()[0].x(),
          [(t.x(), t.y()) for t in panel.tiles()])

    # 高亮是"动态属性 + unpolish/polish"换来的 —— 只 update() 底色不变
    # （像素级验证过）。这条直接抓像素，样式一旦退回"改了属性没反应"就红。
    t0 = panel.tiles()[0]
    off_px = t0.grab().toImage().pixelColor(t0.width() // 2, t0.height() - 4).name()
    t0.set_active(True)
    for _ in range(3):
        app.processEvents()
    on_px = t0.grab().toImage().pixelColor(t0.width() // 2, t0.height() - 4).name()
    want_px = theme.ACCENTS[theme.accent_name()]["color"].lower()
    check("高亮格底色 = 当前强调色（像素级）",
          on_px.lower() == want_px and on_px != off_px, f"{off_px} -> {on_px}")
    t0.set_active(False)

    qss = theme.stylesheet()
    check("QSS 给面板配了背景（普通 QWidget 必须 WA_StyledBackground 才画）",
          bool(re.search(r"QWidget#tilePanel\s*\{[^}]*background-color", qss)))
    check("QSS 用动态属性表达 hover / active",
          '[hover="true"]' in qss and '[active="true"]' in qss)
    check("高亮格的文字色另给一条规则（祖先属性选择器不生效，改用 objectName）",
          "QLabel#tileTitleOn" in qss and "QLabel#tileHintOn" in qss)

    QTest.keyClick(panel, Qt.Key_Escape)
    for _ in range(3):
        app.processEvents()
    check("Esc 收起面板", not panel.isVisible())
    check("收起后按钮放掉按下态", not win.btn_more.isChecked())

    win._toggle_more_panel()
    for _ in range(4):
        app.processEvents()
    target = panel.tiles()[0]
    QTest.mouseClick(target, Qt.LeftButton)
    for _ in range(5):
        app.processEvents()
    check("点磁贴切到对应页", win.tab_key() == target.key, win.tab_key())
    check("点完自动收起", not panel.isVisible())

    # 磁贴是长生命周期控件：换页/换肤后高亮必须跟着走，颜色不许抠死在构造期
    win.goto_tab("usage")
    check("换页后高亮跑到新页",
          [t.key for t in panel.tiles() if t.is_active()] == ["usage"],
          [t.key for t in panel.tiles() if t.is_active()])
    win.goto_tab("overview")
    check("回到主入口时磁贴全部不高亮",
          not any(t.is_active() for t in panel.tiles()))

    # ============================================================ C 快捷键
    section("C. 快捷键表")
    seqs = [s.key().toString() for s in win._shortcuts]
    check("快捷键已绑定", len(seqs) >= 11, len(seqs))
    for i in range(1, 8):
        check(f"Ctrl+{i} 已绑定", f"Ctrl+{i}" in seqs)
    for s in ("Ctrl+K", "Ctrl+R", "F5", "Ctrl+Shift+C", "Ctrl+,"):
        check(f"{s} 已绑定", s in seqs)
    check("没有重复快捷键", len(seqs) == len(set(seqs)),
          [s for s in set(seqs) if seqs.count(s) > 1])

    # Ctrl+1..7 的落点必须等于 tab_keys 顺序
    want = {f"Ctrl+{i}": k for i, k in enumerate(keys[:9], start=1)}
    got: dict[str, str] = {}
    for sc, seq in zip(win._shortcuts, seqs):
        if seq in want:
            win.goto_tab("settings")
            sc.activated.emit()
            got[seq] = win.tab_key()
    check("Ctrl+1..7 落点与 tab_keys 顺序一致", got == want, got)

    # ============================================================ D 主题引擎
    section("D. 主题引擎")
    combos = 0
    for pname in theme.PALETTES:
        for acc in theme.ACCENTS:
            theme.apply(palette=pname, accent=acc, scale=1.0)
            qss = theme.stylesheet()
            if qss.count("{") == qss.count("}") and "{" not in re.findall(
                    r"\{[a-zA-Z_]+\}", qss):
                combos += 1
    check("8 套配色全部生成合法样式表", combos == 8, combos)

    # 两套调色板的键集合必须完全一致：QSS 是无条件按 pal['XXX'] 取的，
    # 只在一边加键（比如只给 dark 加 FIELD_BG）会在另一套上直接 KeyError ——
    # 而 KeyError 的触发点是"切到浅色"，测试不切就永远看不见。
    key_sets = {name: set(pal) for name, pal in theme.PALETTES.items()}
    names = sorted(key_sets)
    check("两套调色板键集合一致",
          len({frozenset(s) for s in key_sets.values()}) == 1,
          {n: sorted(key_sets[n] ^ key_sets[names[0]]) for n in names[1:]})
    # QSS 引用的每个键都必须真的存在（拼错键名 = 启动即崩）
    used = set(re.findall(r"c\['([A-Z_]+)'\]", theme.stylesheet()))
    check("QSS 引用的配色键全部存在",
          used <= key_sets[names[0]], sorted(used - key_sets[names[0]]))

    scales_ok = True
    for step in theme.SCALE_STEPS:
        theme.apply(scale=step)
        if theme.px(13) != round(13 * step):
            scales_ok = False
    check("3 档字号缩放正确",
          scales_ok and theme.px(13) == 17, f"scale1.3 -> px(13)={theme.px(13)}")
    theme.apply(scale=1.0)

    # QSS 字号必须用 pt：`font-size: Npx` 会让字体 pointSize() = -1，
    # Qt 在算菜单字号时做 pointSize()-1 就会抛警告（带 QMenu 的按钮必现）。
    qss_font_sizes = re.findall(r"font-size:\s*([^;]+);", theme.stylesheet())
    check("QSS 里不存在 px 字号", qss_font_sizes
          and not any("px" in v for v in qss_font_sizes), qss_font_sizes[:3])
    pt_sizes = [v for v in qss_font_sizes if v.strip().endswith("pt")]
    check("QSS 字号全为 pt 且为正数",
          len(pt_sizes) == len(qss_font_sizes)
          and all(float(v.strip()[:-2]) > 0 for v in pt_sizes),
          f"{len(pt_sizes)}/{len(qss_font_sizes)}")
    check("pt() 与 px() 相差 0.75 倍（96dpi 换算）",
          theme.pt(13) == round(13 * 0.75), f"pt(13)={theme.pt(13)}")

    # 颜色必须是"活"的：切到浅色后 health_label 立即跟着变
    # 注意：这两个期望值跟着 iOS 调色板走（深色 systemRed / 浅色可读红）。
    # 改 PALETTES 时必须同步这里 —— 否则这里通过、主题其实没生效。
    theme.apply(palette="dark", accent="rooboo", scale=1.0)
    dark_err = health_label("error")[1]
    theme.apply(palette="light")
    light_err = health_label("error")[1]
    check("health_label 颜色随主题变化（不是被 dict 冻住的）",
          dark_err != light_err and dark_err == "#FF453A" and light_err == "#D70015",
          f"{dark_err} -> {light_err}")
    check("状态词映射正确", health_label("ready")[0] == "正常"
          and health_label("禁用态") [0] == "禁用态")
    check("status_color 也随主题变化", theme.status_color("running") == "#248A3D",
          theme.status_color("running"))

    # 真正走一遍换肤入口
    win.cmb_palette.setCurrentIndex(1)           # 浅色
    for _ in range(3):
        app.processEvents()
    check("设置页换肤 → theme 生效", theme.palette_name() == "light")
    check("设置页换肤 → 落盘到 config",
          cfg.get("ui.palette") == "light", cfg.get("ui.palette"))
    check("换肤后导航按钮仍是主入口 4 个", len(win._nav_buttons) == 4)
    win.cmb_palette.setCurrentIndex(0)
    for _ in range(3):
        app.processEvents()
    check("再换回深色", theme.palette_name() == "dark")

    theme.apply(palette="dark", accent="rooboo", scale=1.0)

    # ============================================================ E 提示条
    section("E. 提示条（Toast）")
    t = Toast()
    t.show_message("普通信息", "info")
    check("info 6 秒后自动收起", t._timer.isActive() and t._timer.interval() == 6000)
    t.show_message("出问题了\n第二行原因", "error")
    check("error 常驻（不自动消失）", not t._timer.isActive())
    check("error 保留多行", "\n" in t.text())
    check("提示条有「✕」关闭按钮", t._close.text() == "✕")
    check("QSS 花括号平衡", t.styleSheet().count("{") == t.styleSheet().count("}"))
    t.hide_message()
    check("可手动关闭", not t.isVisible())

    # 点击复制全文
    t.show_message("复制我", "warn")
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    ev = QMouseEvent(QEvent.MouseButtonPress, QPoint(5, 5), Qt.LeftButton,
                     Qt.LeftButton, Qt.NoModifier)
    t.mousePressEvent(ev)
    check("点击提示条 = 复制全文",
          QApplication.clipboard().text() == "复制我",
          QApplication.clipboard().text())
    check("复制后有「已复制」反馈", t._hint.isVisible())

    # ============================================================ F 状态点/环
    section("F. 状态点 / 状态环（呼吸）")
    dot = StatusDot(theme.OK, 13)
    dot.set_breathing(True)
    check("圆点可开呼吸", dot._anim.isActive())
    dot.set_breathing(False)
    check("圆点可停呼吸", not dot._anim.isActive() and dot._phase == 0.0)
    ring = StatusRing(96)
    ring.set_state("运行中", theme.OK, "8788")
    ring.set_breathing(True)
    check("状态环可呼吸并渲染",
          ring.grab().width() == 96 and ring._anim.isActive())
    ring.set_breathing(False)
    check("状态环停呼吸", not ring._anim.isActive())

    check("概览英雄区已接入状态",
          win.hero_title.text() == ctx.gateway.state_label, win.hero_title.text())
    check("英雄区四枚 pill 都在",
          all(p.text() for p in (win.hero_pill_addr, win.hero_pill_port,
                                 win.hero_pill_cred, win.hero_pill_model)),
          [p.text() for p in (win.hero_pill_addr, win.hero_pill_port,
                              win.hero_pill_cred, win.hero_pill_model)])
    check("英雄区主按钮有动态文案",
          win.hero_btn_start.text() in ("启动网关", "停止网关"),
          win.hero_btn_start.text())

    # ============================================================ F2 iOS 部件
    section("F2. iOS 开关 / 分段式导航")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QCheckBox

    sw = IOSSwitch("测试开关")
    # ★ 这条是整个替换方案的地基：16 处调用点（含本文件后面的
    #   `win.chk_log_regex.setChecked`）一行都没改，靠的就是继承关系。
    check("IOSSwitch 仍是 QCheckBox（调用点无需改动）", isinstance(sw, QCheckBox))
    got: list = []
    sw.toggled.connect(got.append)
    sw.setChecked(True)
    check("开关可勾选并派发 toggled", sw.isChecked() and got == [True])
    sw.setChecked(False)
    check("开关可取消勾选", not sw.isChecked() and got == [True, False])

    # 文字没被压掉：sizeHint 必须为滑轨 + 间距 + 文本留够宽度。
    text_w = sw.fontMetrics().horizontalAdvance(sw.text())
    check("开关 sizeHint 留够滑轨与文字的宽度",
          sw.sizeHint().width() >= IOSSwitch.TRACK_W + IOSSwitch.GAP + text_w,
          f"{sw.sizeHint().width()} vs {IOSSwitch.TRACK_W + IOSSwitch.GAP + text_w}")
    check("开关不会被压扁（固定高度）",
          sw.minimumSizeHint().height() >= IOSSwitch.TRACK_H)

    # 两种状态 × 两套调色板都要能画出来。自绘控件的典型坑是
    # 换肤后不重绘、或者某套配色下取到不存在的键直接 KeyError。
    paint_ok = True
    detail = ""
    for pname in theme.PALETTES:
        theme.apply(palette=pname, accent="rooboo", scale=1.0)
        for state in (False, True):
            sw.setChecked(state)
            pm = sw.grab()
            if pm.isNull() or pm.width() < IOSSwitch.TRACK_W:
                paint_ok = False
                detail = f"{pname}/{state} -> {pm.width()}x{pm.height()}"
    check("开关在两套调色板 × 两种状态下都能渲染", paint_ok, detail)

    # SWITCH_OFF（关闭态滑轨色）必须两套调色板都有 —— 少一个就是 KeyError。
    check("两套调色板都定义了 SWITCH_OFF",
          all("SWITCH_OFF" in p for p in theme.PALETTES.values()))
    theme.apply(palette="dark", accent="rooboo", scale=1.0)

    # 迁移彻底性：ui/ 下不该再有人直接 new QCheckBox（否则两套开关观感不一致）
    # 正则要求 QCheckBox( 前面是行首或 = ( , 空格 —— 避免把 docstring 里
    # 那句「`QCheckBox(...)` 的创建点」也当成真实调用（前面是反引号）。
    _mk = re.compile(r"(^|[=(,\s])QCheckBox\(", re.M)
    leftovers = []
    for src in (BASE / "luobobox" / "ui").glob("*.py"):
        if _mk.search(src.read_text(encoding="utf-8")):
            leftovers.append(src.name)
    check("ui/ 下不再直接创建 QCheckBox", not leftovers, leftovers)

    # 分段控件的轨道：普通 QWidget 不开 WA_StyledBackground 就不画 QSS 背景，
    # 结果"轨道"凭空消失、只剩几个孤立按钮 —— 这个坑必须钉住。
    check("分段导航容器开了 WA_StyledBackground",
          win._nav_host.testAttribute(Qt.WA_StyledBackground))
    check("分段导航容器有 segmented objectName",
          win._nav_host.objectName() == "segmented")
    check("分段导航轨道比按钮高（有内缩留白）",
          win._nav_host.height() >= win._nav_buttons["overview"].height(),
          f"{win._nav_host.height()} vs {win._nav_buttons['overview'].height()}")

    # QSS 里若写了 QWidget#segmented 却漏了 WA_StyledBackground，
    # 上面那条会挂 —— 反过来也要确认 QSS 真的给轨道配了背景色。
    seg_qss = re.search(r"QWidget#segmented\s*\{([^}]*)\}", theme.stylesheet())
    check("QSS 给分段轨道配了背景色",
          bool(seg_qss) and "background-color" in seg_qss.group(1),
          seg_qss.group(1).strip()[:60] if seg_qss else "段未找到")

    # ============================================================ F3 工具栏
    section("F3. 工具栏按钮可点性（启动 / 停止 / 重启 / 管理台）")

    # 这张表就是规格：端口上有没有监听者决定「启动」能不能点。
    # `external` 是萝卜盒**最常见**的状态（用户自己先起了网关），
    # 它曾经被漏掉 —— 于是"启动"亮着、点下去只弹「端口被占用」。
    expect_start = {
        "stopped": True, "starting": False, "running": False,
        "external": False, "stopping": False,
    }
    bad = []
    for st, want in expect_start.items():
        got = _toolbar_flags(st, busy=False)["start"]
        if got != want:
            bad.append(f"{st}:{got}!={want}")
    check("「启动」只在端口无人监听时可点（含 external）", not bad, bad)

    check("「启动」在 runner 忙时一律不可点",
          all(not _toolbar_flags(st, busy=True)["start"]
              for st in expect_start))
    check("「停止」只在跑着 / 外部接管 / 启动中可点",
          [_toolbar_flags(st, False)["stop"]
           for st in ("stopped", "starting", "running", "external", "stopping")]
          == [False, True, True, True, False])
    check("「重启」只在自家托管的 running 下可点（external 不抢）",
          [_toolbar_flags(st, False)["restart"]
           for st in ("stopped", "starting", "running", "external")] == [False, False, True, False])
    check("「网页版管理台」在可连通时就可点",
          [_toolbar_flags(st, False)["dashboard"]
           for st in ("stopped", "running", "external")] == [False, True, True])

    # 上面那张表必须真的是 win 用的那张 —— 否则测试只是自说自话。
    real = _toolbar_flags(win.ctx.gateway.state, win.ctx.runner.busy())
    check("窗口上的四个按钮与规格表一致",
          [win.btn_start.isEnabled(), win.btn_stop.isEnabled(),
           win.btn_restart.isEnabled(), win.btn_dashboard.isEnabled()]
          == [real["start"], real["stop"], real["restart"], real["dashboard"]],
          f"state={win.ctx.gateway.state} busy={win.ctx.runner.busy()}")

    # 为什么这个 bug 会"看得见"：停用态必须真的变灰。QSS 里
    # `#primary` 若没有 :disabled 覆盖，禁用的强调按钮会和启用时**长得一模一样**，
    # 用户以为能点 —— 这正是当初误判"状态没刷新"的原因之一。
    dis = re.search(r"QPushButton#primary:disabled\s*\{([^}]*)\}", theme.stylesheet())
    check("QSS 里 #primary 有 :disabled 覆盖（禁用态会变灰）", bool(dis))
    check("禁用强调色 ≠ 强调色（肉眼可分辨）",
          bool(dis) and theme.DISABLED_BG != theme.ACCENT,
          f"{theme.DISABLED_BG} vs {theme.ACCENT}")

    # ============================================================ G 接入包
    section("G. 一键复制接入包")
    pkg = win.access_package()
    check("接入包含本机地址", cfg.base_url() in pkg)
    check("接入包含局域网地址", "局域网" in pkg)
    check("接入包含 Tailscale", "Tailscale" in pkg)
    check("接入包含公网入口", "公网入口" in pkg)
    check("接入包含 API Key", str(cfg.get("gateway.api_key")) in pkg)
    check("接入包含 Codex 片段", "config.toml" in pkg and "```toml" in pkg)
    check("接入包含 Claude 片段", "```bash" in pkg)
    check("接入包是 Markdown", pkg.lstrip().startswith("# "))
    win.copy_access_package()
    check("复制接入包 → 剪贴板一致",
          QApplication.clipboard().text() == pkg)

    # ============================================================ H 命令面板
    section("H. 命令面板（Ctrl+K）")
    check("fuzzy 命中前缀加分", palette.fuzzy_score("更新", "检查更新") is not None)
    check("fuzzy 不命中返回 None", palette.fuzzy_score("zzzz", "检查更新") is None)
    check("拼音首字母 hint 生效（qd → 立即签到）",
          any(c.title == "立即签到" for c in palette.rank(
              palette.build_commands(win), "qd")))
    cmds = palette.build_commands(win)
    ckeys = [c.key for c in cmds]
    check("命令 key 唯一", len(ckeys) == len(set(ckeys)),
          [k for k in set(ckeys) if ckeys.count(k) > 1])
    check("每个页签都有对应命令",
          all(f"tab:{k}" in ckeys for k in keys))
    check("换肤命令齐备（4 强调色 + 2 基调）",
          sum(1 for k in ckeys if k.startswith("theme:accent:")) == 4
          and sum(1 for k in ckeys if k.startswith("theme:palette:")) == 2)
    check("所有命令都带可执行回调",
          all(callable(c.run) for c in cmds))
    check("空查询返回全部命令",
          len(palette.rank(cmds, "")) == len(cmds))
    check("搜索有结果数收敛", 0 < len(palette.rank(cmds, "更新")) < len(cmds),
          len(palette.rank(cmds, "更新")))

    # 面板本身能构建 + ↑↓ 移动 + Esc 关闭
    dlg = palette.CommandPalette(win, cmds)
    check("面板默认选中第一项", dlg.list.currentRow() == 0)
    dlg._move(1)
    check("↓ 能移动选中项", dlg.list.currentRow() == 1)
    dlg._move(-1)
    check("↑ 能移动回选中项", dlg.list.currentRow() == 0)
    first_key = dlg.list.currentItem().data(Qt.UserRole)
    dlg._activate()
    check("回车 = 选中并关闭",
          dlg.chosen is not None and dlg.chosen.key == first_key)
    dlg.deleteLater()

    # 每个页签命令都能真的跳页
    ok_goto = True
    for c in cmds:
        if c.key.startswith("tab:"):
            win.goto_tab("settings")
            c.run()
            if win.tab_key() != c.key.split(":", 1)[1]:
                ok_goto = False
    check("页签命令全部可执行并跳对页", ok_goto)

    # ============================================================ I 日志页
    section("I. 日志页：正则过滤 / 导出 / 分级着色")
    win.goto_tab("logs")
    check("日志区吃到主题 objectName", win.log_view.objectName() == "logView")
    check("日志区不再 inline 写死颜色",
          "background-color" not in win.log_view.styleSheet())

    # 喂一段确定的日志：真实 gateway.log 内容不可控，断言会变成"看运气"
    sample = ("2026-09-21 10:00:00 INFO  started\n"
              "2026-09-21 10:00:01 ERROR boom\n"
              "2026-09-21 10:00:02 WARN  slow\n")
    _real_tail = win.ctx.gateway.tail_log
    win.ctx.gateway.tail_log = lambda *a, **k: sample

    win.chk_log_regex.setChecked(True)
    win.log_filter.setText("ERROR|WARN")
    check("合法正则被编译", win._log_pattern() is not None)
    win.log_filter.setText("ERROR|(")
    check("非法正则退回 None（不炸）", win._log_pattern() is None)
    win.log_filter.setText("")
    win.chk_log_regex.setChecked(False)
    check("未勾正则时不编译", win._log_pattern() is None)

    win.log_view.setPlainText(sample)
    for _ in range(3):
        app.processEvents()
    check("分级着色器附着", win.log_highlighter is not None)
    check("日志行数已灌入", lines_of(win.log_view.toPlainText()) == lines_of(sample))

    win.log_filter.setText("INFO")
    win._refresh_log()
    check("普通过滤只留命中行", "INFO" in win.log_view.toPlainText()
          and "boom" not in win.log_view.toPlainText())
    win.log_filter.setText("ERROR|INFO")
    win.chk_log_regex.setChecked(True)
    win._refresh_log()
    check("正则过滤同时留两类行",
          "boom" in win.log_view.toPlainText()
          and "started" in win.log_view.toPlainText()
          and "slow" not in win.log_view.toPlainText())
    check("状态行给出命中计数", "命中 2/3 行" in win.log_meta.text(), win.log_meta.text())

    win.log_filter.setText("ERROR|(")
    win._refresh_log()
    check("正则写错 → 退回普通匹配并在状态行说明",
          "正则无效" in win.log_meta.text(), win.log_meta.text())

    win.log_filter.setText("")
    win.chk_log_regex.setChecked(False)
    win._refresh_log()
    check("清空过滤 → 恢复全部行",
          lines_of(win.log_view.toPlainText()) == lines_of(sample))

    out = TMP / "exported.log"
    Path(out).write_text(win.log_view.toPlainText(), encoding="utf-8")
    check("当前视图内容可落盘（导出通路）",
          lines_of(Path(out).read_text(encoding="utf-8")) == lines_of(sample))

    win.ctx.gateway.tail_log = _real_tail

    # ============================================================ J 图表
    section("J. 图表（纯 QPainter）")
    from luobobox import usage

    fake = {"accounts": {
        "a": {"first_seen": 0, "first_balance": 100.0, "last_balance": 70.0,
              "daily": {"2026-09-19": 100.0, "2026-09-20": 90.0}},
    }}
    series = usage.daily_series(fake, 7)
    check("daily_series 返回 N 天", len(series) == 7, len(series))
    check("daily_series 按日期升序", [d for d, _ in series] == sorted(d for d, _ in series))
    check("daily_series 只算窗口内的天",
          all(v >= 0 for _, v in series))
    short = usage.daily_series(fake, 2)
    check("缩到 2 天窗口只返回 2 天，末尾值与 7 天一致",
          len(short) == 2 and short[-1] == series[-1]
          and short[0] == series[-2], short)
    # 20 号的消耗 = 90 - 70(last_balance) = 20
    day20 = dict(series).get("2026-09-20")
    check("跨天差额推导正确（90 → 70 记 20）", day20 == 20.0, day20)

    bc = charts.BarChart()
    bc.resize(320, 130)
    bc.set_data([])
    check("空数据柱状图可渲染", bc.grab().width() == 320)
    bc.set_data(series, unit="")
    check("有数据柱状图可渲染", bc.grab().height() == 130)
    dn = charts.Donut(136)
    dn.set_data([("已用", 30, theme.ACCENT), ("剩余", 70, theme.BORDER_HI)], "30%", "已用占比")
    check("环形图可渲染", dn.grab().width() == 136)
    dn.set_data([], "—", "暂无数据")
    check("环形图空数据可渲染（不除零）", dn.grab().width() == 136)
    lg = charts.Legend()
    lg.set_items([("已用", "30", theme.ACCENT), ("剩余", "70", theme.BORDER_HI)])
    check("图例高度随条数变化", lg.height() == 19 * 2 + 8, lg.height())

    usage_page = win.tabs.widget(win._tab_index["usage"])
    check("额度页已挂上环形图与柱状图",
          usage_page.findChild(charts.Donut) is not None
          and usage_page.findChild(charts.BarChart) is not None)

    # ============================================================ K 空态
    section("K. 空态（EmptyState）")
    es = EmptyState("标题", "提示", glyph="X", action=("点我", lambda: None))
    check("空态有 objectName", es.objectName() == "emptyState")
    check("空态可改文案", (es.set_text("新标题", "新提示") or True)
          and es._title.text() == "新标题")
    check("凭证池空态/表格用 stacked 切换",
          isinstance(win.cred_stack, __import__(
              "PySide6.QtWidgets", fromlist=["QStackedWidget"]).QStackedWidget)
          and win.cred_stack.count() == 2)


    class _Snap:
        ok = True
        credentials: list = []

    win._fill_credentials(_Snap())
    check("无凭证 → 切到空态", win.cred_stack.currentIndex() == 1)

    class _Snap2:
        ok = True
        credentials = [{"name": "acct-1", "health": "ready", "credits": 12}]

    win._fill_credentials(_Snap2())
    check("有凭证 → 切回表格",
          win.cred_stack.currentIndex() == 0 and win.cred_table.rowCount() == 1)
    check("健康度着色用的是当前主题色",
          win.cred_table.item(0, 1).foreground().color().name().lower() == theme.OK.lower(),
          win.cred_table.item(0, 1).foreground().color().name())

    # ============================================================ L 几何记忆
    section("L. 窗口几何记忆")
    win.resize(1024, 700)
    win._save_geometry()
    saved = str(cfg.get("ui.window_geometry") or "")
    check("几何已落盘", re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", saved) is not None, saved)

    win.resize(900, 620)          # 先弄脏，再让 _restore_geometry 拉回来
    win._restore_geometry()
    check("几何可解析回放", win.width() == 1024 and win.height() == 700,
          f"{win.width()}x{win.height()}")

    cfg.set("ui.window_geometry", "乱写的值")
    win._restore_geometry()
    check("坏几何值退回默认尺寸（不炸）",
          win.width() >= 940 and win.height() >= 720, f"{win.width()}x{win.height()}")
    check("坏几何值不会顶到离谱尺寸",
          win.width() <= 4000 and win.height() <= 4000)

    # ============================================================ M 动效
    section("M. 动效降级")
    check("fade_in 对普通控件返回动画", motion.fade_in(win.log_view) is not None)
    motion.flash(win.btn_log_export, theme.OK)
    check("flash 不抛异常", True)

    # ============================================================ N 新版本角标
    section("N. 新版本角标：托盘不打开窗口也能看见")
    seen: list[str] = []
    win.on_update_found(seen.append)
    win.set_update_badge("app", "萝卜盒 v9.9.9")
    check("登记 app 角标", win.update_badge == "萝卜盒 v9.9.9", win.update_badge)
    check("注册的回调收到角标", seen and seen[-1] == "萝卜盒 v9.9.9", seen)
    win.set_update_badge("gateway", "网关 v9.9.9")
    check("两条链路合成一条文案",
          win.update_badge == "萝卜盒 v9.9.9 / 网关 v9.9.9", win.update_badge)
    win.set_update_badge("app", "")
    check("清掉一条不会连带清掉另一条", win.update_badge == "网关 v9.9.9", win.update_badge)
    win.set_update_badge("gateway", "")
    check("两条都清掉后角标为空", win.update_badge == "")

    check("tray.py 的菜单文案走常量", "UPDATE_MENU_TEXT" in tray_src)
    check("tray.py 的 tooltip 会拼角标", "有新版本" in tray_src)
    check("tray.py 注册了 on_update_found", "on_update_found" in tray_src)

    # 真建一个托盘控制器（无头下 QSystemTrayIcon 可能不可用 → 降级为源码校验）
    try:
        from luobobox.ui.tray import UPDATE_MENU_TEXT, TrayController

        tray = TrayController(app, ctx, win)
        check("角标为空时菜单项是默认文案",
              tray.act_update.text() == UPDATE_MENU_TEXT, tray.act_update.text())
        tray._on_update_found("萝卜盒 v9.9.9")
        check("托盘菜单项写出新版本",
              "v9.9.9" in tray.act_update.text()
              and tray.act_update.text() != UPDATE_MENU_TEXT,
              tray.act_update.text())
        check("托盘 tooltip 带角标", "有新版本" in tray.tray.toolTip(), tray.tray.toolTip())
        tray._on_update_found("")
        check("角标清掉后菜单项回到默认文案",
              tray.act_update.text() == UPDATE_MENU_TEXT, tray.act_update.text())
        check("角标清掉后 tooltip 不带角标", "有新版本" not in tray.tray.toolTip())
        tray.tray.hide()

        # 场景2：更新是在**托盘创建之前**就发现的（比如开机后台自动检查完）
        win.set_update_badge("app", "萝卜盒 v9.9.9")
        late = TrayController(app, ctx, win)
        check("托盘创建前已发现的更新也能显示",
              "v9.9.9" in late.act_update.text()
              and "有新版本" in late.tray.toolTip(),
              late.act_update.text())
        late.tray.hide()
        win.set_update_badge("app", "")
    except Exception as exc:  # noqa: BLE001 —— 无头环境托盘不可用时降级
        print(f"     （托盘不可用，本段降级为源码校验：{type(exc).__name__}: {exc}）")

    # ============================================================ O 一键配置环境
    section("O. 一键配置环境（体检 + 修复）")
    from luobobox import envsetup

    rep = envsetup.diagnose(cfg, deep=False)
    ikeys = [i.key for i in rep.items]
    check("体检清单覆盖 9 个必要项",
          {"pointer", "gateway_dir", "python", "port", "api_key",
           "args", "patch", "webui", "task"} <= set(ikeys), ikeys)
    check("体检项 key 不重复", len(ikeys) == len(set(ikeys)))
    check("每项都有图标 / 标题 / 说明",
          all(i.glyph and i.title and i.detail for i in rep.items))
    check("摘要写明检查项数", str(len(rep.items)) in envsetup.summary_text(rep),
          envsetup.summary_text(rep))
    check("counts 覆盖四种状态",
          set(rep.counts) == {"ok", "fix", "warn", "fail"})
    check("ready 只看 fail（可自动修的项不算失败）",
          rep.ready == (not rep.failures), f"{rep.ready} / {len(rep.failures)}")
    check("每个可修项都登记了修复器",
          all(i.key in envsetup.FIXERS for i in rep.needing_fix),
          [i.key for i in rep.needing_fix if i.key not in envsetup.FIXERS])
    check("todo 是给人看的一句话",
          len(rep.todo) > 4 and rep.todo.endswith("。"), rep.todo)

    check("pip 命令带齐防卡参数",
          all(f in envsetup.pip_command(Path("py.exe"))
              for f in ("--no-input", "--no-cache-dir",
                        "--disable-pip-version-check")))
    check("pip 环境抹掉了继承来的代理变量",
          not any(k in envsetup.pip_env()
                  for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                            "http_proxy", "PIP_PROXY")))
    attempts = envsetup.install_attempts("")
    check("安装阶梯先默认源后国内镜像（镜像有同步延迟）",
          attempts[0][1] == ""
          and any("tuna" in idx for _, idx, _ in attempts), [a[0] for a in attempts])
    check("独立环境建在数据目录下（跟着数据一起搬 / 一起删）",
          Path(envsetup.venv_dir()).parent == Path(TMP), str(envsetup.venv_dir()))
    check("没装 Python 时给出下载入口", "https://" in envsetup.python_download_hint())

    # --- 傻瓜式兜底：本机一个 Python 都没有时，也要能一键装齐
    check("内置 Python 落在数据目录下（免安装、免管理员）",
          Path(envsetup.builtin_python_dir()).parent == Path(TMP)
          and envsetup.BUILTIN_PY_DIR_NAME == "python",
          str(envsetup.builtin_python_dir()))
    check("内置 Python 先试官方源（顺序不能反）",
          envsetup.EMBED_PY_MIRRORS[0][0] == "python.org")
    check("内置 Python 有多镜像兜底", len(envsetup.EMBED_PY_MIRRORS) >= 2)
    check("get-pip 引导脚本也有镜像兜底", len(envsetup.GET_PIP_MIRRORS) >= 1)
    check("嵌入式包地址是可下载的 zip 直链",
          all(tpl.endswith(".zip") and "{v}" in tpl for _, tpl in envsetup.EMBED_PY_MIRRORS))
    check("内置 Python 按本机架构选包，不写死 amd64",
          all("{arch}" in tpl for _, tpl in envsetup.EMBED_PY_MIRRORS)
          and envsetup.embed_arch() in envsetup.EMBED_PY_ARCHES,
          envsetup.embed_arch())

    # --- 设置页侧
    win.goto_tab("settings")
    check("设置页有「一键配置环境」按钮",
          win.btn_env_setup.text().startswith("一键配置"), win.btn_env_setup.text())
    check("设置页另有「只体检」与「打开下载页」",
          bool(win.btn_env_diag.text()) and bool(win.btn_env_plat.text()))
    check("缺依赖时默认走独立虚拟环境", win.chk_env_venv.isChecked())
    check("环境输出区是只读的", win.env_out.isReadOnly())
    check("设置页标出了指针位置", bool(win.lbl_pointer.text()), win.lbl_pointer.text())

    win.env_out.clear()
    win.env_logged.emit("第一行")      # 模拟工作线程 emit
    win.env_logged.emit("第二行")
    check("后台日志经信号落到输出区",
          win.env_out.toPlainText() == "第一行\n第二行", win.env_out.toPlainText())

    check("命令面板有「一键配置环境」入口",
          "act:envsetup" in [c.key for c in palette.build_commands(win)])

    # --- 向导侧
    from luobobox.ui.wizard import FirstRunWizard

    wz = FirstRunWizard(ctx)
    check("向导「运行环境」页有一键修复按钮",
          wz.btn_env_fix.isEnabled() and "一键修复" in wz.btn_env_fix.text(),
          wz.btn_env_fix.text())
    check("一键修复的提示说明了「没有 Python 会自动装一份内置的」",
          "内置" in wz.btn_env_fix.toolTip(), wz.btn_env_fix.toolTip())
    check("向导用信号转发修复日志（不跨线程碰控件）",
          hasattr(wz, "env_logged"))
    wz._append_env_log("向导日志")
    check("向导日志能落到提示区",
          "向导日志" in wz.probe_out.text(), wz.probe_out.text())

    # --- 弹窗：配置没配好时必须弹，而不是只往日志区写一行
    import luobobox.ui.widgets as _widgets

    _orig_popup = _widgets.message_popup
    _captured: list = []
    _widgets.message_popup = (
        lambda parent, title, text, detail="", icon="warn":
        _captured.append((title, icon, text, detail)))
    try:
        wz._popup_env_failed(["① 体检", "✗ 解释器与依赖：下载失败（连不上）"])
    finally:
        _widgets.message_popup = _orig_popup
    check("一键修复没修好时会弹窗", len(_captured) == 1 and _captured[0][1] == "warn",
          str(_captured[:1]))
    check("弹窗细节里带上逐行清单",
          "下载失败" in (_captured[0][3] if _captured else ""))
    check("弹窗主文案给出下一步动作",
          "重试" in (_captured[0][2] if _captured else ""))

    # ================================================ ZZ 网关目录定位
    section("ZZ. 网关目录定位（别把猜出来的默认值当结果）")

    from luobobox import paths as _paths
    import luobobox.ui.wizard as wizard_mod

    # 从命令行抠目录。真实命令行里 `pythonw.exe` 与网关路径**在同一行**，
    # 早先那条「从第一个盘符开始吞」的正则会拼出半条命令行的垃圾路径，
    # 而 is_gateway_dir() 会把它否掉 → 表现为"功能静默失效"。
    # 目录自己造（含中文段），别写死本机真实路径 —— 否则这套自测换台机器就红。
    _root = Path(tempfile.mkdtemp(prefix="luobobox-cmdline-")) / "反代工具"
    _real_gw = _root / "codebuddy2api"
    _real_gw.mkdir(parents=True)
    (_real_gw / "converter.py").write_text("# fake gateway", encoding="utf-8")
    _gwpy = str(_real_gw / "converter.py")
    _real_cmd = (r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe "
                 + _gwpy + " serve --host 0.0.0.0 --port 8789")
    _gw_real = _paths._gateway_dir_in_cmdline(_real_cmd)
    check("能从 pythonw + 中文路径的命令行里抠出网关目录",
          _gw_real == _real_gw.resolve(), str(_gw_real))
    check("抠出来的目录真的带 converter.py",
          _gw_real is not None and (_gw_real / "converter.py").is_file())
    check("引号包裹 / 正斜杠 的写法也能抠出来",
          _paths._gateway_dir_in_cmdline('"' + _gwpy + '" serve') == _real_gw.resolve()
          and _paths._gateway_dir_in_cmdline(
              _real_gw.as_posix() + "/converter.py serve") == _real_gw.resolve())
    check("抠不出来的一律 None（不瞎填一个坏路径给用户）",
          all(_paths._gateway_dir_in_cmdline(c) is None for c in (
              "", "python converter.py serve",
              str(_root / "nope" / "converter.py") + " serve",
              str(_root / "other.py") + " serve")))

    # 端口也要抠得出来：只改目录不改端口，照向导点完就会在**同一份源码目录**上
    # 再起一个实例，两个进程同时写同一份 auth/ 与 .env。
    check("能从命令行里抠出 --port（两种写法 + 越界一律 None）",
          _paths._gateway_port_in_cmdline(_real_cmd) == 8789
          and _paths._gateway_port_in_cmdline("serve --port=65535") == 65535
          and _paths._gateway_port_in_cmdline("serve --port 99999") is None
          and _paths._gateway_port_in_cmdline("serve --host 0.0.0.0") is None,
          str(_paths._gateway_port_in_cmdline(_real_cmd)))

    # 三级顺序：当前配置 → 同级/上级 → 问进程。把前两级掐掉，确认第三级
    # （唯一真正知情的那个）确实会被问到 —— 这正是用户那次卡住的地方。
    _gwdir = Path(tempfile.mkdtemp(prefix="luobobox-gwdir-")) / "codebuddy2api"
    _gwdir.mkdir(parents=True)
    (_gwdir / "converter.py").write_text("# fake gateway", encoding="utf-8")
    _o_cands = _paths.gateway_dir_candidates
    _o_proc = _paths.gateway_from_process
    _o_wiz_locate = wizard_mod.locate_gateway_dir
    _o_warn = wz._warn
    _paths.gateway_dir_candidates = lambda: [Path("C:/definitely/nope/codebuddy2api")]
    try:
        _paths.gateway_from_process = lambda timeout=12: None
        check("全猜不中、也没有在跑的网关 → 三项都空，不编一个出来",
              _paths.locate_gateway_dir("") == (None, "", None))

        _paths.gateway_from_process = lambda timeout=12: (_gwdir, 9999)
        _got, _why, _gp = _paths.locate_gateway_dir(r"C:\不存在的\codebuddy2api")
        check("配置里是个不存在的路径时，会去问正在运行的网关（连端口一起回）",
              _got == _gwdir.resolve() and _why == "正在运行的网关进程" and _gp == 9999,
              f"{_got} / {_why} / {_gp}")

        # 向导侧：进「运行环境」页会自动探测，目录与端口都要因此被改对。
        #
        # ★ 探测体现在是**纯函数** probe_environment()，而 _probe() 只负责把它
        #   丢进后台线程 —— 因为问进程要 1537ms、找解释器要 513ms，同步跑会让
        #   「运行环境」页在入口白掉两秒（1.1.0 刚修掉的那种卡）。
        #   所以这里调「确定性的一对」：纯函数 → 回填。线程行为另行单独断言。
        wizard_mod.locate_gateway_dir = lambda cur=None: (
            _gwdir.resolve(), "正在运行的网关进程", 9999)
        try:
            wz.w_dir.setText(
                "C:\\Users\\Administrator\\AppData\\Local\\Programs\\LuoboBox\\codebuddy2api")
            _res = wizard_mod.probe_environment(
                wz.w_dir.text(), wz.w_py.text(), int(wz.w_port.value()))
            check("探测结果是纯 dict，不碰控件（这样才能丢进线程）",
                  isinstance(_res, dict)
                  and {"dir", "port", "py", "lines", "warn", "ok_hint"} <= set(_res),
                  str(sorted(_res)) if isinstance(_res, dict) else type(_res).__name__)
            wz._apply_probe(_res)
            check("自动探测把填错的网关目录改对了",
                  Path(wz.w_dir.text()) == _gwdir.resolve(), wz.w_dir.text())
            check("自动探测连端口一起采纳（否则同目录会起第二个实例）",
                  int(wz.w_port.value()) == 9999, str(wz.w_port.value()))
            check("探测结果里写明目录是从哪找到的",
                  "正在运行的网关进程" in wz.probe_out.text(),
                  wz.probe_out.text().splitlines()[0][:70] if wz.probe_out.text() else "")

            # 「这个目录不存在」和「目录在、但没有 converter.py」要给不同的提示 ——
            # 老版本只有后一句，于是"向导塞了个不存在的默认值"这种情况，
            # 用户看到的是「请确认目录是否正确」，而那个目录从来不该被填进去。
            warned: list = []
            wz._warn = lambda t: warned.append(t)
            wz.w_dir.setText("C:\\definitely\\not\\here")
            wz._validate_runtime()
            check("目录不存在时提示「不存在」而不是「找不到 converter.py」",
                  bool(warned) and "不存在" in warned[0], str(warned[:1]))

            wz.w_dir.setText(tempfile.mkdtemp(prefix="luobobox-notgw-"))
            warned.clear()
            wz._validate_runtime()
            check("目录在但不是网关目录时说清缺的是 converter.py",
                  bool(warned) and "converter.py" in warned[0], str(warned[:1]))
        finally:
            wizard_mod.locate_gateway_dir = _o_wiz_locate
            wz._warn = _o_warn
    finally:
        _paths.gateway_dir_candidates = _o_cands
        _paths.gateway_from_process = _o_proc

    # 自动探测必须**不阻塞主线程**：问进程 1537ms + 找解释器 513ms，同步跑就是
    # 「进入运行环境页白掉两秒」—— 1.1.0 专门修掉的那种卡，不能再塞回来。
    # 断言办法：把 run_task 换成"只记录、不执行"的替身 → _probe() 应当立刻返回。
    import time as _time
    _calls: list = []
    _o_run_task = ctx.run_task
    # ★ 替身要**照抄真签名**：`AppContext.run_task` 只认
    #   (fn, on_ok, on_err, busy_text)，**不转发额外参数**。
    #   写成 `*a, **k` 的宽松替身会把「多传了参数」这种错掩盖掉 ——
    #   真踩过：自测全绿，实机一进向导就 TypeError。
    ctx.run_task = lambda fn, ok=None, err=None, busy_text=None: _calls.append((fn, ok))
    try:
        wz._probing = False
        wz.probe_out.setText("")
        _o_find_py = wizard_mod.find_python
        # 纯函数里最贵的是 find_python（实测 513ms），这里换掉只为让断言快。
        wizard_mod.find_python = lambda preferred=None: (  # noqa: ARG005
            None, [(Path(r"C:\nope\python.exe"), "文件不存在")])
        try:
            _t0 = _time.perf_counter()
            wz._probe()
            _dt = (_time.perf_counter() - _t0) * 1000
            check("自动探测把活丢给后台线程，自己不阻塞",
                  len(_calls) == 1 and _dt < 300,
                  f"{_dt:.1f}ms  calls={len(_calls)}")
            _res2 = _calls[0][0]() if _calls else None
            check("丢进线程的那个可调用对象，跑起来真的产出探测结果",
                  isinstance(_res2, dict) and "dir" in _res2, str(_res2)[:60])
        finally:
            wizard_mod.find_python = _o_find_py
        check("探测期间显示「探测中…」并按住「下一步」",
              wz.probe_out.text() == "探测中…" and not wz.btn_next.isEnabled()
              and not wz.btn_probe.isEnabled(),
              f"{wz.probe_out.text()!r} next={wz.btn_next.isEnabled()}")
        wz._probe()
        check("连点「自动探测」不会攒出第二个线程", len(_calls) == 1, str(len(_calls)))

        # 结果回来（后台回调）→ 按钮放开、内容回填
        wz._apply_probe({"dir": "", "port": None, "py": "", "lines": ["✗ 没找到"],
                         "warn": "这台机器上没找到 Python", "ok_hint": ""})
        check("结果回来就放开按钮并回填内容",
              wz.btn_next.isEnabled() and wz.btn_probe.isEnabled()
              and not wz._probing and "没找到" in wz.probe_out.text(),
              f"next={wz.btn_next.isEnabled()} text={wz.probe_out.text()[:24]!r}")

        # 后台的活要是炸了，也不能把用户永久卡在「探测中」（下一步永远点不动）
        wz._probe()
        wz._probe_failed("boom")
        check("探测失败同样放开按钮（不许把用户卡在「探测中」）",
              wz.btn_next.isEnabled() and wz.btn_probe.isEnabled() and not wz._probing
              and "boom" in wz.hint.text(),
              wz.hint.text()[:40])
    finally:
        ctx.run_task = _o_run_task
        wz._probing = False

    # --- 回归：窗口比内容矮时，行**不能**被压塌。
    # 用户报过「运行环境页中间区域显示异常」：Python 输入框只剩 3px、「一键修复
    # 环境」和「下载安装包」被压成几像素并互相叠字。根因是每页都是固定高度布局，
    # 内容（体检日志最多 12 行 + 缺 Python 时多出的提示块）一旦超过窗口高度，
    # Qt 不会溢出而是把每一行压扁。修法：每页套可滚动容器 + 把 probe_out 的
    # 最小高度钉死在「按当前宽度换行后真正需要的高度」。
    from PySide6.QtWidgets import QScrollArea

    # 自动探测现在跑在后台线程里，而下面这些断言（有没有滚动容器 / 行会不会被
    # 压塌 / 提示块有没有被挤扁）要的是**确定性的同步**：把 run_task 换成立刻
    # 执行的替身，否则断言会跟还在跑的线程抢 probe_out 的内容。段末还原。
    # 替身照抄真签名（不写 `*a, **k`）—— 宽松替身会掩盖"多传参数"的错。
    _o_run_task_sync = ctx.run_task
    ctx.run_task = (lambda fn, ok=None, err=None, busy_text=None:
                    ok(fn()) if ok else fn())

    wz._goto(1)  # noqa: SLF001
    # ⚠ 必须 show()：没走过布局的 QWidget 还是默认 640×480，行高断言会
    # 「因为根本没布局」而假通过（实测过 —— 三行都报 h=480）。offscreen 下
    # show 不会弹窗。
    wz.show()
    for _ in range(8):
        app.processEvents()
    wz.resize(660, 560)          # 逼到最小尺寸，强制走滚动分支
    wz._probe()
    wz._append_env_log("\n".join(f"第 {i} 行体检输出" for i in range(1, 13)))
    for _ in range(8):
        app.processEvents()

    import luobobox.ui.wizard as _wizard

    _orig_find_python = _wizard.find_python
    # 模拟「这台机器上没有可用的 Python」—— 正是用户截图里的情形：探测失败才会
    # 亮出下载提示块，也才会把这一页撑到最高（缺 Python + 一屏日志同时出现）。
    # 不模拟的话本机（装了 Python）永远走不到这条分支。
    _wizard.find_python = lambda preferred=None: (  # noqa: ARG005
        None, [(Path(r"C:\Program Files\Python312\python.exe"), "缺依赖：fastapi、uvicorn")])
    try:
        wz._probe()
        wz._append_env_log("\n".join(f"第 {i} 行体检输出" for i in range(1, 13)))
        for _ in range(8):
            app.processEvents()

        check("运行环境页套了可滚动容器", isinstance(wz.stack.widget(1), QScrollArea),
              type(wz.stack.widget(1)).__name__)
        area = wz.stack.widget(1)
        check("内容超高时走滚动，而不是压缩内容",
              area.verticalScrollBar().maximum() > 0,
              f"max={area.verticalScrollBar().maximum()}")

        check("缺 Python 时下载提示块自动亮出", wz.py_dl_tip.isVisible())

        def row_ok(widget) -> bool:
            """行没被压塌、而且确实布局过（排除未布局时的 480 默认值）。"""
            h = widget.height()
            return 40 <= h <= 200

        pw = wz.w_py.parentWidget()
        check("窄窗口下 Python 行不被压塌（输入框/按钮还在）", row_ok(pw),
              f"h={pw.height()} hint={pw.sizeHint().height()}")
        fixed_row = wz.btn_env_fix.parentWidget()
        check("窄窗口下「一键修复环境」行不被压塌", row_ok(fixed_row),
              f"h={fixed_row.height()} hint={fixed_row.sizeHint().height()}")
        tip_h = wz.py_dl_tip.height()
        check("窄窗口下「下载安装包」提示块不被压塌", row_ok(wz.py_dl_tip), f"h={tip_h}")

        # probe_out 不能只按「一行」高就交差 —— 一键修复失败时程序让用户
        # 「看下方清单最后一行」，裁掉最后一行等于把这句提示变成谎言。
        need = wz.probe_out.heightForWidth(wz.probe_out.width())
        log_h = wz.probe_out.height()
        check("体检日志不被裁字（高度 ≥ 换行后所需）",
              40 <= log_h <= 400 and log_h >= need, f"h={log_h} 需要={need}")
    finally:
        _wizard.find_python = _orig_find_python
    ctx.run_task = _o_run_task_sync       # 还原真后台，别的段还要用
    wz.deleteLater()

    # ============================================================ 收尾
    ctx.stop_timers()
    win.hide()
    win.deleteLater()
    for _ in range(3):
        app.processEvents()

    total = _passed + len(_failed)
    print("\n" + "=" * 64)
    print(f"通过 {_passed} / {total}")
    if _failed:
        print("失败项：")
        for name in _failed:
            print(f"  · {name}")
    print("=" * 64)
    return 0 if not _failed else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback

        traceback.print_exc()
        _failed.append("未捕获异常")
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
