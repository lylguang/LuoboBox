# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

策略：**只打包萝卜盒桌面壳本身，不打包网关**。
网关跑在"用户已有的 Python 解释器"上 —— 这是刻意设计：
  · converter.py 有大量运行时动态导入，塞进 PyInstaller 很容易炸；
  · 网关经常升级，独立于萝卜盒才能覆盖更新；
  · 本机已经有一个配好依赖的 venv，复用它是零风险的。

代价：目标机器需要 Python + fastapi/uvicorn/httpx。
     萝卜盒会自动探测，并在设置里允许手动指定。
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(os.path.abspath(SPECPATH)).parent
ASSETS = ROOT / "assets"

# PySide6 默认会把整套 Qt 拖进来（本机装完 458MB）。只留真正用到的模块。
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtNetworkAuth", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtWebSockets", "PySide6.QtWebChannel",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech", "PySide6.QtHelp", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvgWidgets",
    "PySide6.QtConcurrent", "PySide6.QtPrintSupport",
    # 与本程序无关的重量级第三方库
    "matplotlib", "numpy", "scipy", "pandas", "PIL", "cv2", "librosa", "numba",
    "tkinter", "unittest", "pydoc", "doctest", "setuptools", "pip",
    "fastapi", "uvicorn", "httpx", "httpcore", "starlette", "pydantic",
]

datas = []
if ASSETS.is_dir():
    # paths.resource_dir() 在冻结态返回 sys._MEIPASS，所以平铺到根目录
    datas.append((str(ASSETS), "."))

hiddenimports = collect_submodules("luobobox")

a = Analysis(
    [str(ROOT / "packaging" / "luobobox_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LuoboBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI 程序，不能有控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "icon.ico") if (ASSETS / "icon.ico").is_file() else None,
    version=str(ROOT / "packaging" / "version_info.txt")
    if (ROOT / "packaging" / "version_info.txt").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LuoboBox",
)
