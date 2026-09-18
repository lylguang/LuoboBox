"""PyInstaller 冻结入口。

刻意与 run_luobobox.pyw 分开：那个文件会动 sys.path / 重定向 stderr，
只在源码调试时用；打包后要的是最干净的一条路径。
"""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # 在冻结环境里，附带进来的网关依赖如果和主程序冲突，先让主程序赢
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    from luobobox.app import main

    raise SystemExit(main(sys.argv))
