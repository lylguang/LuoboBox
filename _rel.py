"""提交 + 打 tag + 推送（跑完即删）。

按本仓库的既定流程：清掉代理变量 → 提交 → tag → 先 ls-remote 对账 → 再 push。
"""
from __future__ import annotations

import os
import subprocess
import sys

VER = "1.1.1"
MSG = """fix(ui): 网关已在运行时「启动」按钮仍高亮可点

用户的网关多半是外部启动的（本机就是），而 `start` 的可点性写的是
`not (state == "running")` —— 把 `external` 漏了。表现是首屏右上角
「启动」永远是一枚亮着的高亮按钮，点下去只弹「端口被占用」。

- 把四个按钮的可点性抽成纯函数 `_toolbar_flags(state, busy)`，
  端口上有监听者时 `start` 一律不可点；
- 自测补 8 项：五种状态 × busy 的可点性表、窗口与规格表一致、
  `#primary:disabled` 覆盖存在且禁用色 ≠ 强调色（这条解释了当初
  为什么"看起来能点"会被当成"界面没刷新"）；
- README 补两条决策表（纯函数可测性 / 禁用态必须可分辨）。
"""

env = os.environ.copy()
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
          "https_proxy", "all_proxy", "GIT_HTTP_PROXY"):
    env.pop(k, None)
env["GIT_TERMINAL_PROMPT"] = "0"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(list(args), cwd=os.path.dirname(os.path.abspath(__file__)),
                       capture_output=True, env=env)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    print(f"$ {' '.join(args)}\n{out.strip()}\n")
    if check and p.returncode != 0:
        sys.exit(f"命令失败 rc={p.returncode}: {' '.join(args)}")
    return p


run("git", "add", "-A")
run("git", "status", "--porcelain")
run("git", "commit", "-m", MSG)
run("git", "tag", "-a", f"v{VER}", "-m", f"release v{VER}")
run("git", "log", "--oneline", "-3")
run("git", "ls-remote", "origin", "refs/heads/main")
run("git", "push", "origin", "main")
run("git", "push", "origin", f"v{VER}")
print("完成")
