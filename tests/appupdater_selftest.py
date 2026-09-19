"""appupdater 无界面自测：验证应用自更新里最容易悄悄出错的部分。

重点：
  1. install_mode() 能区分安装版 / 便携版。
  2. pick_asset() 在缺资产时会正确回退。
  3. **助手 .cmd 真的能把新版本覆盖到旧安装目录**（含中文路径 —— 编码最容易翻车）。
  4. **exe 被占用时整体放弃**，绝不留下半新半旧的 _internal（比不更新更糟）。
  5. DETACHED_PROCESS 方式拉起的 cmd 确实能跑完（不然更新会静默失败）。
  6. apply_and_restart() 在安装目录里没有 LuoboBox.exe 时会安全拒绝。

跑法：python tests/appupdater_selftest.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from luobobox import appupdater  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def _make_portable_zip(zip_path: Path, exe_bytes: bytes, data_bytes: bytes) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("LuoboBox/LuoboBox.exe", exe_bytes)
        zf.writestr("LuoboBox/_internal/app.txt", data_bytes)
        zf.writestr("LuoboBox/_internal/lib/x.txt", b"lib")


def main() -> int:
    global PASS, FAIL
    tmp = Path(tempfile.mkdtemp(prefix="luobobox-appupd-"))
    try:
        # ---------------------------------------------------------- 1. 安装形态
        print("[1] install_mode()")
        inst = tmp / "installed"
        inst.mkdir()
        (inst / "LuoboBox.exe").write_bytes(b"x")
        (inst / "unins000.exe").write_bytes(b"x")
        check("有 unins*.exe → installer", appupdater.install_mode(inst) == "installer")

        port = tmp / "portable"
        port.mkdir()
        (port / "LuoboBox.exe").write_bytes(b"x")
        check("无 unins*.exe → portable", appupdater.install_mode(port) == "portable")

        # ---------------------------------------------------------- 2. 资产挑选
        print("[2] pick_asset()")
        info = appupdater.AppReleaseInfo(
            tag="v9.9.9",
            assets={
                "LuoboBox-9.9.9-portable.zip": "https://example/portable.zip",
                "LuoboBox-Setup-9.9.9.exe": "https://example/setup.exe",
            },
        )
        check("installer 优先取 Setup",
              (appupdater.pick_asset(info, "installer") or ("", ""))[0].endswith(".exe"))
        check("portable 优先取 zip",
              (appupdater.pick_asset(info, "portable") or ("", ""))[0].endswith(".zip"))

        only_zip = appupdater.AppReleaseInfo(
            tag="v9.9.9",
            assets={"LuoboBox-9.9.9-portable.zip": "u"},
        )
        check("只有 zip 时 installer 会回退到 zip",
              appupdater.pick_asset(only_zip, "installer") is not None)
        check("资产为空时返回 None",
              appupdater.pick_asset(appupdater.AppReleaseInfo(), "portable") is None)

        # ---------------------------------------------------------- 3. 版本比较
        print("[3] 版本比较（借 updater._parse_version）")
        from luobobox.updater import _parse_version
        check("v1.0.10 > v1.0.9", _parse_version("v1.0.10") > _parse_version("1.0.9"))
        check("v1.0.0 == 1.0.0", _parse_version("v1.0.0") == _parse_version("1.0.0"))

        # ---------------------------------------------------------- 4. 覆盖（含中文路径）
        print("[4] 助手脚本原地覆盖（中文路径 + mbcs 编码）")
        cn = tmp / "反代工具" / "dist"          # 故意用带中文的目录
        app_dir = cn / "LuoboBox"
        app_dir.mkdir(parents=True)
        (app_dir / "LuoboBox.exe").write_bytes(b"OLD-EXE")
        (app_dir / "_internal").mkdir()
        (app_dir / "_internal" / "app.txt").write_bytes(b"OLD-DATA")

        archive = tmp / "LuoboBox-9.9.9-portable.zip"
        _make_portable_zip(archive, b"NEW-EXE", b"NEW-DATA")

        staging = tmp / "staging"
        src = appupdater.extract_portable(archive, staging)
        check("extract_portable 定位到内层 LuoboBox/",
              (src / "LuoboBox.exe").is_file(), str(src))

        log = tmp / "apply.log"
        script = tmp / "apply.cmd"
        appupdater._write_batch(
            script,
            appupdater.helper_script(app_dir, src, "portable", None, log,
                                     relaunch=False, self_delete=False),
        )
        raw = script.read_bytes()
        check("脚本已按 ANSI/OEM 码页写盘（中文路径不是 UTF-8 字节）",
              "反代工具".encode("mbcs") in raw, f"{raw[:60]!r}")

        r = subprocess.run(["cmd.exe", "/c", str(script)], capture_output=True, timeout=180)
        exe_now = (app_dir / "LuoboBox.exe").read_bytes()
        data_now = (app_dir / "_internal" / "app.txt").read_bytes()
        check("LuoboBox.exe 已替换为新版本", exe_now == b"NEW-EXE", repr(exe_now))
        check("_internal/app.txt 已替换", data_now == b"NEW-DATA", repr(data_now))
        check("子目录 _internal/lib 一并复制",
              (app_dir / "_internal" / "lib" / "x.txt").is_file())
        check("助手退出码为 0", r.returncode == 0, f"rc={r.returncode}")

        # ---------------------------------------------------------- 5. 被锁时不许半更新
        print("[5] exe 被锁 → 整体放弃，不留半新半旧的 _internal")
        import ctypes

        locked_app = tmp / "locked" / "LuoboBox"
        locked_app.mkdir(parents=True)
        (locked_app / "LuoboBox.exe").write_bytes(b"OLD-EXE")
        (locked_app / "_internal").mkdir()
        (locked_app / "_internal" / "app.txt").write_bytes(b"OLD-DATA")

        k32 = ctypes.windll.kernel32
        GENERIC_READ = 0x80000000
        OPEN_EXISTING = 3
        handle = k32.CreateFileW(str(locked_app / "LuoboBox.exe"), GENERIC_READ,
                                 0, None, OPEN_EXISTING, 0, None)  # 共享模式 0 = 独占
        check("已用独占句柄锁住旧 exe", handle not in (0, -1, 0xFFFFFFFF), str(handle))

        log2 = tmp / "locked.log"
        script2 = tmp / "locked.cmd"
        appupdater._write_batch(
            script2,
            appupdater.helper_script(locked_app, src, "portable", None, log2,
                                     relaunch=False, self_delete=False,
                                     retries=2, wait_sec=1),
        )
        try:
            r2 = subprocess.run(["cmd.exe", "/c", str(script2)],
                                capture_output=True, timeout=120)
        finally:
            if handle not in (0, -1, 0xFFFFFFFF):
                k32.CloseHandle(ctypes.c_void_p(handle))

        check("锁定期间 exe 未被替换",
              (locked_app / "LuoboBox.exe").read_bytes() == b"OLD-EXE")
        check("锁定期间 _internal 未被碰过（没留下半新半旧）",
              (locked_app / "_internal" / "app.txt").read_bytes() == b"OLD-DATA",
              repr((locked_app / "_internal" / "app.txt").read_bytes()))
        text2 = log2.read_text(encoding="mbcs", errors="replace") if log2.is_file() else ""
        check("日志里明确写了放弃覆盖", "放弃覆盖" in text2, text2[-200:])

        # ---------------------------------------------------------- 6. 分离式拉起
        print("[6] DETACHED_PROCESS 拉起的 cmd 能跑完")
        flag = tmp / "detached-ok.txt"
        tiny = tmp / "detached.cmd"
        appupdater._write_batch(
            tiny,
            "@echo off\r\nping -n 2 127.0.0.1 >nul 2>&1\r\n"
            f'>"{flag}" echo ok\r\n',
        )
        proc = subprocess.Popen(["cmd.exe", "/c", str(tiny)],
                                close_fds=True, creationflags=appupdater._DETACHED)
        for _ in range(40):
            if flag.is_file():
                break
            time.sleep(0.5)
        check("分离进程写出的哨兵文件已出现", flag.is_file(),
              f"pid={proc.pid} 未产出 {flag}")

        # ---------------------------------------------------------- 7. 安全拒绝
        print("[7] apply_and_restart 安全兜底")
        empty = tmp / "empty-app"
        empty.mkdir()
        plan = appupdater.apply_and_restart("portable", archive, empty)
        check("安装目录没有 LuoboBox.exe 时拒绝并给出原因",
              (not plan.ok) and "LuoboBox.exe" in plan.message, plan.message)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
