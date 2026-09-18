"""开机自启。

用 HKCU\\...\\Run 注册表键，而不是启动文件夹快捷方式 —— 不需要 pywin32/COM，
路径带空格也不怕，卸载时删一个键就干净了。

为什么不沿用那套"计划任务"：计划任务是给**无界面常驻服务**用的，萝卜盒是
带托盘界面的桌面程序，走标准登录自启更自然。但如果用户希望网关脱离萝卜盒
独立常驻，settings 里也提供了计划任务方案（见 create_logon_task）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LuoboBox"
TASK_NAME = "LuoboBox"


def _winreg():
    import winreg

    return winreg


def current_command() -> str:
    """本程序的自启命令行。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    entry = Path(__file__).resolve().parent.parent / "run_luobobox.pyw"
    py = Path(sys.executable).with_name("pythonw.exe")
    exe = py if py.exists() else Path(sys.executable)
    return f'"{exe}" "{entry}" --tray'


def get_autostart() -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def set_autostart(enabled: bool, command: str | None = None) -> tuple[bool, str]:
    winreg = _winreg()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = command or current_command()
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
                return True, f"已开启开机自启：{cmd}"
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
            return True, "已关闭开机自启"
    except Exception as exc:  # noqa: BLE001
        return False, f"操作注册表失败：{exc}"


def is_autostart_on() -> bool:
    return get_autostart() is not None


# ------------------------------------------------------------ 计划任务方案

def logon_task_exists(name: str = TASK_NAME) -> bool:
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", name],
                           capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace",
                           creationflags=0x08000000)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def create_logon_task(command: str | None = None, name: str = TASK_NAME) -> tuple[bool, str]:
    """创建"登录即启动"的计划任务（脱离萝卜盒界面独立常驻时用）。"""
    cmd = command or current_command()
    try:
        r = subprocess.run(
            ["schtasks", "/create", "/tn", name, "/sc", "onlogon",
             "/tr", cmd, "/rl", "highest", "/f"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000,
        )
        ok = r.returncode == 0
        return ok, (r.stdout or r.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def delete_logon_task(name: str = TASK_NAME) -> tuple[bool, str]:
    try:
        r = subprocess.run(["schtasks", "/delete", "/tn", name, "/f"],
                           capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace",
                           creationflags=0x08000000)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
