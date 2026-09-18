"""萝卜盒 源码模式启动器（用 pythonw 运行，无控制台窗口）。

打包成 exe 后走 LuoboBox.exe，这个文件只在源码调试时用。
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

if os.environ.get("LUOBOBOX_DEBUG"):
    sys.stderr = open(BASE / "debug.err.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr

from luobobox.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
