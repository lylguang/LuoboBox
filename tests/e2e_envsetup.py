"""一键配置环境 · **联网**端到端验证（真建 venv、真 pip install）。

与 `envsetup_selftest.py` 的分工：那边是离线、可进 CI 的逻辑回归；
这边只干一件事 —— 证明「缺依赖的解释器 → 独立虚拟环境 → 依赖装齐」
这条路在真实网络下真的能走通。所以它**需要联网**，不进常规回归。

跑法：
    <python> tests/e2e_envsetup.py

刻意挑「能跑但不带 fastapi/uvicorn/httpx」的解释器当起点（通常是干净的
托管 Python），这样才会真的走到建环境和装包那一步；如果这台机器上每个
解释器都带齐了依赖，就跳过（返回 0）而不是假装通过。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

TMP = Path(tempfile.mkdtemp(prefix="luobobox-envsetup-e2e-")).resolve()
os.environ["LUOBOBOX_DATA_DIR"] = str(TMP / "data")
os.environ["LUOBOBOX_POINTER_DIR"] = str(TMP / "appdir")
os.environ["LOCALAPPDATA"] = str(TMP / "localappdata")

from luobobox import envsetup, patcher, paths  # noqa: E402

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


def find_dep_less_python() -> Path | None:
    """找一个「能跑但缺依赖」的解释器 —— 这才是这条链路的真实起点。"""
    from luobobox.paths import _candidate_interpreters  # noqa: PLC2701

    for cand in _candidate_interpreters():
        if not envsetup._runs(cand):
            continue
        ok, missing, _why = paths.probe_modules(cand)
        if not ok and missing:
            print(f"     起点解释器：{cand}（缺 {'、'.join(missing)}）")
            return cand
    return None


def main() -> int:
    print("=" * 64)
    print("萝卜盒 一键配置环境 · 联网端到端（真建 venv + 真装包）")
    print("=" * 64)

    base = find_dep_less_python()
    if base is None:
        print("跳过：这台机器上找不到「能跑但缺依赖」的解释器，没有可验证的起点。")
        return 0

    # --- pip 环境的净度（本机常有死代理，必须被抹掉）
    os.environ["HTTP_PROXY"] = "http://definitely-dead.invalid:9"
    env = envsetup.pip_env()
    check("pip 环境里没有继承来的死代理", "HTTP_PROXY" not in env)
    os.environ.pop("HTTP_PROXY", None)

    proxy = envsetup.first_proxy("")
    print(f"     通道候选：{envsetup.install_attempts('')}")
    print(f"     探测到代理：{proxy or '（无，直连）'}")

    # --- 真建环境 + 真装包
    target = TMP / "pyenv"
    t0 = time.time()
    logs: list[str] = []

    def log(line: str) -> None:
        logs.append(line)
        print(f"     {line.strip()}")

    ok, msg = envsetup.make_venv(base, log=log, target=target, cfg_proxy="")
    dt = time.time() - t0

    check("独立虚拟环境建成", (target / "Scripts" / "python.exe").is_file(),
          str(target))
    check("依赖安装成功", ok, msg)
    good, missing, why = paths.probe_modules(envsetup.venv_python(target))
    check("新环境确实带齐 fastapi/uvicorn/httpx", good, why or "、".join(missing))
    print(f"     耗时 {dt:.1f}s")

    # --- 幂等：再跑一次应当不重装（复用已有环境）
    t1 = time.time()
    ok2, msg2 = envsetup.make_venv(base, target=target, cfg_proxy="")
    dt2 = time.time() - t1
    check("重复调用仍成功（幂等）", ok2, msg2)
    check("重复调用明显更快（复用了已有环境）", dt2 < max(2.0, dt / 2),
          f"首次 {dt:.1f}s / 复跑 {dt2:.1f}s")

    # --- 走一遍 setup：把配置真的指到这个新环境上
    from luobobox.config import Config

    gw = TMP / "gw"
    (gw / "app").mkdir(parents=True, exist_ok=True)
    (gw / "converter.py").write_text("# 假网关\n", encoding="utf-8")
    (gw / "app" / "desensitize.py").write_text(
        '"""d"""\n\nSENSITIVE_TERMS: list[str] = [\n'
        '    "Do" ,"OpenAI", "Codex", "ChatGPT", "GPT-4", "GPT-5",\n]\n',
        encoding="utf-8",
    )
    cfg = Config()
    cfg.set("gateway.dir", str(gw))
    cfg.set("gateway.python", str(base))          # 故意填那个缺依赖的
    cfg.set("gateway.extra_args", [])
    ready, lines = envsetup.setup(cfg, prefer_venv=True, allow_install=True)
    print("\n".join("     " + ln for ln in lines[-12:]))

    # 这台机器上恰好有一个「现成的合格解释器」时，最不打扰的做法就是直接用它
    # （建独立环境是"不得已才做"的兜底）。所以这里不该断言一定落到 venv。
    picked = Path(str(cfg.get("gateway.python")))
    ok_py, miss, why = paths.probe_modules(picked)
    check("setup 后配置里的解释器确实带齐依赖", ok_py, why or "、".join(miss))
    check("setup 后整体就绪", ready is True, lines[-1])
    check("setup 后 API Key 已生成", bool(str(cfg.get("gateway.api_key")).strip()))
    check("setup 后启动参数已补齐",
          not patcher.check_args(cfg.get("gateway.extra_args")))

    # --- 场景 B：把「现成合格解释器」这条路堵掉，强制走独立虚拟环境那条分支
    #     （这是这条链路真正的兜底，也是最需要被证明的一段）
    os.environ["LUOBOBOX_DATA_DIR"] = str(TMP)     # 让 venv_dir() 指回上面建好的那个
    real_find = envsetup.find_python
    envsetup.find_python = lambda *_a, **_k: (None, [])
    try:
        cfg2 = Config()
        cfg2.set("gateway.dir", str(gw))
        cfg2.set("gateway.python", str(base))
        ready2, lines2 = envsetup.setup(cfg2, prefer_venv=True, allow_install=True)
        print("\n".join("     " + ln for ln in lines2[-8:]))
        check("没有现成解释器时改用数据目录下的独立环境",
              Path(str(cfg2.get("gateway.python"))) == envsetup.venv_python(),
              str(cfg2.get("gateway.python")))
        check("场景 B 也整体就绪", ready2 is True, lines2[-1])
    finally:
        envsetup.find_python = real_find

    total = _passed + len(_failed)
    print("\n" + "=" * 64)
    print(f"通过 {_passed} / {total}")
    if _failed:
        print("失败项：")
        for name in _failed:
            print(f"  · {name}")
    print(f"临时目录（可整目录删掉）：{TMP}")
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
