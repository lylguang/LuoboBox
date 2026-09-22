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
    from luobobox.ui.main_window import TAB_PRIMARY, MainWindow, health_label
    from luobobox.ui.widgets import EmptyState, StatusDot, StatusRing, Toast

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
    check("溢出页也能切（走菜单同一条通路）",
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
    theme.apply(palette="dark", accent="rooboo", scale=1.0)
    dark_err = health_label("error")[1]
    theme.apply(palette="light")
    light_err = health_label("error")[1]
    check("health_label 颜色随主题变化（不是被 dict 冻住的）",
          dark_err != light_err and dark_err == "#F09595" and light_err == "#B42318",
          f"{dark_err} -> {light_err}")
    check("状态词映射正确", health_label("ready")[0] == "正常"
          and health_label("禁用态") [0] == "禁用态")
    check("status_color 也随主题变化", theme.status_color("running") == "#0F7A57",
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
    check("向导用信号转发修复日志（不跨线程碰控件）",
          hasattr(wz, "env_logged"))
    wz._append_env_log("向导日志")
    check("向导日志能落到提示区",
          "向导日志" in wz.probe_out.text(), wz.probe_out.text())
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
