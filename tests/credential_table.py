"""凭证池表格回归测试。

起因（真实故障 2026-09-18）：
    管理台里加了 12 个账号，萝卜盒的「凭证池」却一片空白。
    根因是 _fill_credentials 里一句 f"{credits:,}" —— 网关的 credits 字段
    在拉过明细时是一个对象（{"credits": 6845.33, "segments": [...]}），
    不是数字。f"{dict:,}" 抛 TypeError，整个填充循环在第一行就中断，
    后面 11 个账号一个都没写进表格。

本测试用**离线合成数据**复现网关的真实字段形态，锁死这个行为：
只要表格能填满、且不抛异常，就算通过。

跑法：
    <venv>/Scripts/python.exe tests/credential_table.py
    退出码 0 = 通过，1 = 失败（会打印哪一行没填上）
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TMP = Path(tempfile.mkdtemp(prefix="luobobox-cred-"))
os.environ["LUOBOBOX_DATA_DIR"] = str(TMP)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILURES.append(name)


def gateway_style_snapshot() -> "object":
    """照抄真实 /admin/credentials 的字段形态。

    credits 故意给成 dict —— 这正是老代码炸掉的那种输入。
    """
    from luobobox.gateway import HealthSnapshot

    return HealthSnapshot(
        ok=True,
        detail="ok",
        models=6,
        credentials=[
            {"id": "a" * 64, "name": "62f8cd4e-c8d9-4104-801b-d2f6e4289fa7.info",
             "health": "ready", "enabled": True,
             "credits": {"credits": 6845.33, "count": 24,
                         "segments": [{"remaining": 18.8, "total": 100.0}]}},
            {"id": "b" * 64, "name": "699f4228-ac4e-4e59-931b-3e24da1f289c.info",
             "health": "ready", "enabled": True,
             "credits": {"credits": 2185.85, "count": 7}},
            {"id": "c" * 64, "name": "workbuddy-desktop.info",
             "health": "ready", "enabled": True, "credits": 1000},          # 纯数字
            {"id": "d" * 64, "name": "old-desktop.info",
             "health": "disabled", "enabled": False, "credits": None},      # 无积分 + 停用
            {"id": "e" * 64, "name": "cooldown.info",
             "health": "circuit_open", "enabled": True, "credits": 12.5,
             "cooldown_remaining": 42},
            {"id": "f" * 64, "name": "weird.info", "health": "ready",
             "enabled": True, "credits": "不明格式"},                        # 脏数据
        ],
        credits={"credits": {
            r"F:\gw\auth\62f8cd4e.info": {"credits": 6845.33},
            r"F:\gw\auth\workbuddy-desktop.info": {"credits": 152.85},
        }},
    )


def main() -> int:
    print("[1] QApplication…", flush=True)
    from PySide6.QtWidgets import QApplication

    app = QApplication([])

    from luobobox.config import Config
    from luobobox.context import AppContext
    from luobobox.ui.main_window import MainWindow, _fmt_credits

    print("[2] 合成网关风格的快照…", flush=True)
    snap = gateway_style_snapshot()

    print("[3] 构建 MainWindow 并填充…", flush=True)
    cfg = Config.load()
    cfg.set("gateway.auto_start", False)
    cfg.set("funnel.enabled", False)
    cfg.set("app.first_run_done", True)
    cfg.save()
    ctx = AppContext(cfg)
    win = MainWindow(ctx)

    # 关键断言：这一步在老代码上会抛 TypeError
    crashed = None
    try:
        win._fill_credentials(snap)
        win._fill_credits(snap)
    except Exception as exc:  # noqa: BLE001
        crashed = f"{type(exc).__name__}: {exc}"
    check("_fill_credentials 不抛异常", crashed is None, crashed or "")

    t = win.cred_table
    n = len(snap.credentials)
    check("表格行数 = 凭证数", t.rowCount() == n, f"rowCount={t.rowCount()} 期望={n}")

    filled = 0
    expect = {
        0: ("62f8cd4e-c8d9-4104-801b-d2f6e4289fa7.info", "正常", "6,845.33", ""),
        1: ("699f4228-ac4e-4e59-931b-3e24da1f289c.info", "正常", "2,185.85", ""),
        2: ("workbuddy-desktop.info", "正常", "1,000", ""),
        3: ("old-desktop.info", "已停用", "—", "已从调度中排除"),
        4: ("cooldown.info", "已熔断", "12.50", "冷却 42s"),
        5: ("weird.info", "正常", "不明格式", ""),
    }
    for r in range(t.rowCount()):
        cells = [(t.item(r, c).text() if t.item(r, c) else "") for c in range(4)]
        if any(cells):
            filled += 1
        if r in expect:
            want = expect[r]
            check(f"第 {r} 行内容", tuple(cells) == want, f"得到 {tuple(cells)} 期望 {want}")
        print(f"       [{r}] 账号={cells[0][:40]:<40} 健康={cells[1]:<8} "
              f"积分={cells[2]:<12} 状态={cells[3]}", flush=True)
    check(f"全部 {n} 行都写入了内容", filled == n, f"有效行={filled}/{n}")

    print("[4] _fmt_credits 边界…", flush=True)
    for value, want in [({"credits": 6845.33}, "6,845.33"), ({"balance": 1000}, "1,000"),
                        ({"credits": None}, "—"), (None, "—"), (0, "0"), (1000.0, "1,000"),
                        (152.85, "152.85"), ({}, "—"), ("x", "x"), (True, "True")]:
        got = _fmt_credits(value)
        check(f"_fmt_credits({str(value)[:28]})", got == want, f"得到 {got!r} 期望 {want!r}")

    print("[5] 空凭证池兜底文案…", flush=True)
    from luobobox.gateway import HealthSnapshot as HS

    win._fill_credentials(HS(ok=True, credentials=[], credits={}))
    text = win.cred_table.item(0, 0).text() if win.cred_table.item(0, 0) else ""
    check("空池给出「去管理台添加」的指引", "添加" in text and "管理台" in text, text)
    check("空池只保留 1 行", win.cred_table.rowCount() == 1, f"{win.cred_table.rowCount()}")

    print("[6] 积分卡片只显示文件名…", flush=True)
    label = win.credits_label.text()
    check("不出现完整路径", ":\\" not in label and ":/" not in label, label[:90])
    check("出现文件名", "62f8cd4e.info" in label, label[:90])

    print("[7] 添加账号入口…", flush=True)
    check("按钮存在", hasattr(win, "btn_add_cred"))
    check("按钮文案指向管理台", "管理台" in win.btn_add_cred.text(),
          win.btn_add_cred.text())
    check("跳转地址正确",
          cfg.base_url() + "/dashboard/credentials"
          == f"http://127.0.0.1:{cfg.get('gateway.port')}/dashboard/credentials")

    ctx.stop_timers()
    win.hide()
    app.processEvents()

    print()
    if FAILURES:
        print(f"结果：失败 {len(FAILURES)} 项 -> {FAILURES}", flush=True)
        return 1
    print("结果：全部通过", flush=True)
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        import traceback

        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
