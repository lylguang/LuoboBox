"""整理这一轮的临时文件（跑完即删）。

- 三张实机截图挪进 tests/_shots_live/（命中 .gitignore 的 tests/_shots*/）
- 我这一轮生成的临时脚本 / 日志 / 中间文本逐个删除（显式名单，不递归、不批量）
"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
SHOTS = ROOT / "tests" / "_shots_live"
SHOTS.mkdir(parents=True, exist_ok=True)

moves = {
    "_live_110.png": "01_实机_概览_v1.1.0.png",
    "_live_110_settings.png": "02_实机_概览_加载完成_v1.1.0.png",
    "_live_110_logs.png": "03_实机_日志页_双验证.png",
}
for src, dst in moves.items():
    p = ROOT / src
    if p.exists():
        os.replace(p, SHOTS / dst)
        print("移动:", src, "->", SHOTS.name + "/" + dst)

dele = [
    "_shot_live.py", "_diag_live.py", "_launch_shot.py", "_shot_settings.py",
    "_shot_pages.py", "_launch_app.py", "_rel_info.py", "_upgrade_local.py",
    "_wait_release.py",
    "_mw_head.txt", "_gl_head.txt",
    "_sl.log", "_dl.log", "_sp.log", "_ss.log", "_ls.log", "_smoke.log",
    "_gl.log", "_s_selftest.log", "_s_envsetup_selftest.log", "_s_net_selftest.log",
    "_s_appupdater_selftest.log", "_s_up.log", "_s_webui.log",
    "_e2e.log", "_e2e_builtin_python.log", "_e2e_fetch_gateway.log",
    "_l.log", "_ri.log", "_run_suites.log", "_t_env.log", "_t_gui1.log",
    "_up.log", "_w.log", "_probe_mirrors.log",
]
gone = 0
for name in dele:
    p = ROOT / name
    if p.exists() and p.is_file():
        p.unlink()
        gone += 1
print("删除临时文件:", gone, "/", len(dele))

left = sorted(p.name for p in ROOT.glob("_*"))
print("残留 _* :", left or "(无)")
