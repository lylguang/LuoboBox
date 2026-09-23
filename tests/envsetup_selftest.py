"""一键配置环境 / 数据目录指针 的离线回归自测。

**刻意不联网、不装任何包、不 import Qt** —— 这样它既能在普通解释器下跑，
也能在 CI 里跑。真正会联网装包的那条路径由 `tests/e2e_envsetup.py` 单独覆盖。

三层覆盖：

  A. 数据模型：Item / Report 的状态语义（`fix` 不算失败、`todo` 会催人）。
  B. 纯函数：pip 命令行拼装、源 × 通道阶梯、pip 环境（死代理必须被抹掉）。
  C. 行为：坏环境能被逐项识别 → 能修的修好 → 复检；好环境一个字节都不动。
  D. 迁移指针：新位置优先、老位置兜底、写不进去时退回老位置并如实说明。

跑法（任何 Python 3.10+，不需要 PySide6）：
    <python> tests/envsetup_selftest.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

TMP = Path(tempfile.mkdtemp(prefix="luobobox-envsetup-")).resolve()

# ★ 三个环境变量都必须在 import luobobox 之前设好：
#   LUOBOBOX_DATA_DIR 让配置/备份落到临时目录；
#   LUOBOBOX_POINTER_DIR 让「指针」落到临时目录（否则会在源码树里写一个真的
#   datadir.txt，污染后续所有源码运行）；
#   LOCALAPPDATA 让"出厂默认目录"整体留在临时目录里 —— 这条同时管住了
#   **老位置指针**（pointer_legacy_path() 走 default_data_dir()）。必须用
#   LOCALAPPDATA 而不是 LUOBOBOX_LEGACY_POINTER_DIR，因为本文件后面有一条断言
#   「老位置 = default_data_dir() 下面」，直接改道老位置变量会让它假失败。
os.environ["LUOBOBOX_DATA_DIR"] = str(TMP / "data")
os.environ["LUOBOBOX_POINTER_DIR"] = str(TMP / "appdir")
os.environ["LOCALAPPDATA"] = str(TMP / "localappdata")
(TMP / "appdir").mkdir(parents=True, exist_ok=True)

from luobobox import envsetup, paths                      # noqa: E402
from luobobox.config import Config                        # noqa: E402

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


FULL_TERMS = (
    '"""脱敏模块。"""\n'
    "\n"
    "import re\n"
    "\n"
    "SENSITIVE_TERMS: list[str] = [\n"
    '    "DoS",\n    "malware",\n'
    '    "OpenAI", "Codex", "ChatGPT", "GPT-4", "GPT-5",\n'
    "]\n"
)

WIPED_TERMS = (
    '"""脱敏模块。"""\n'
    "\n"
    "SENSITIVE_TERMS: list[str] = [\n"
    '    "DoS",\n    "malware",\n'
    "]\n"
)


def make_gateway(root: Path, *, terms: str = FULL_TERMS) -> Path:
    """造一个「看起来像 codebuddy2api」的目录。"""
    root.mkdir(parents=True, exist_ok=True)
    # ★ converter.py 必须**真的** import app，并且 app/__init__.py 也得在。
    #   is_gateway_dir() 是「从 converter.py 自己的 import 推出要不要 app 包」的
    #   （见 paths._gateway_needs_app）：伪造一份不带 import 的 converter.py，
    #   这个 fixture 就落在判据之外 —— 测试照样绿，但真实布局反而测不到。
    (root / "converter.py").write_text(
        "import os\n"
        "from app.desensitize import TERMS\n"
        "\n"
        "print('serve')\n",
        encoding="utf-8")
    app = root / "app"
    app.mkdir(exist_ok=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "desensitize.py").write_text(terms, encoding="utf-8")
    return root


def occupied_port():
    """占住一个端口，返回 (端口, HTTPServer)。

    刻意用真的 HTTP 服务而不是裸 socket：裸 socket 的 accept 队列只有 1，
    探两次就会「连接失败」—— 于是同一个端口会一会儿被判「被占」、
    一会儿被判「空闲」（自测里真实翻过车）。HTTP 服务会持续 accept，
    判定才是稳定的。而且它对 /health 返回 404，正好模拟「占着端口的
    不是网关」这种情况。
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Busy(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not a gateway")

        def log_message(self, *a):  # 静音
            pass

    class _Srv(HTTPServer):
        def handle_error(self, request, client_address):
            pass  # 探活客户端提前断开是常态，别刷屏

    srv = _Srv(("127.0.0.1", 0), _Busy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return int(srv.server_port), srv


def main() -> int:  # noqa: PLR0915
    print("=" * 64)
    print("萝卜盒 一键配置环境 / 迁移指针 自测（离线）")
    print("=" * 64)

    envsetup_mod = envsetup

    # ======================================================== A 数据模型
    section("A. Item / Report 状态语义")
    ok_item = envsetup.Item("x", "标题", "ok", "一切正常")
    fix_item = envsetup.Item("y", "标题", "fix", "可以修", "点我")
    warn_item = envsetup.Item("z", "标题", "warn", "只是提醒")
    fail_item = envsetup.Item("w", "标题", "fail", "修不了")

    check("ok 项不可修", not ok_item.fixable)
    check("fix 项可修", fix_item.fixable)
    check("fix 但没有动作文案就不可修",
          not envsetup.Item("q", "t", "fix", "d").fixable)
    check("字形按状态取", (ok_item.glyph, fix_item.glyph, fail_item.glyph) == ("✓", "→", "✗"))
    check("line() 带状态字形", fix_item.line().startswith("→ 标题 — "), fix_item.line())

    rep = envsetup.Report([ok_item, fix_item, warn_item, fail_item])
    check("counts 分类计数",
          rep.counts == {"ok": 1, "fix": 1, "warn": 1, "fail": 1}, str(rep.counts))
    check("needing_fix 只挑 fix", [i.key for i in rep.needing_fix] == ["y"])
    check("failures 只挑 fail", [i.key for i in rep.failures] == ["w"])
    check("by_key 能取回", rep.by_key("z") is warn_item and rep.by_key("nope") is None)
    check("有 fail 就不算就绪", rep.ready is False)
    check("只有 fix 也算就绪（可自动修）",
          envsetup.Report([fix_item]).ready is True)
    check("todo 优先报需要手动的", "需要手动处理" in rep.todo, rep.todo)
    check("todo 无 fail 时催一键配置", "一键配置" in envsetup.Report([fix_item]).todo)
    check("todo 全绿时给结论", envsetup.Report([ok_item]).todo == "环境就绪，可以启动网关。")
    check("text() 每项一行", len(rep.text().splitlines()) == 4)
    check("summary_text 汇总计数",
          envsetup.summary_text(rep) == "4 项检查：1 通过 / 1 可自动修 / 1 提醒 / 1 需手动",
          envsetup.summary_text(rep))
    check("下载提示里有官方入口", "python.org" in envsetup.python_download_hint(),
          envsetup.python_download_hint()[:60])

    # ======================================================== B 纯函数
    section("B. pip 命令行与通道阶梯")
    cmd = envsetup.pip_command(Path(r"C:\py\python.exe"))
    check("命令以解释器开头", cmd[0] == r"C:\py\python.exe")
    check("走 -m pip install", cmd[1:4] == ["-m", "pip", "install"])
    check("带 --no-cache-dir（绕开沙箱批量删除拦截）", "--no-cache-dir" in cmd)
    check("带 --no-input（绝不挂在等输入上）", "--no-input" in cmd)
    check("不带源/代理时没有多余参数",
          "--index-url" not in cmd and "--proxy" not in cmd)
    check("包名排在最后", cmd[-3:] == list(envsetup.REQUIRED_MODULES), str(cmd[-3:]))

    cmd2 = envsetup.pip_command("py", index="https://mirror/simple",
                               proxy="http://127.0.0.1:20809", upgrade=True)
    check("指定源时带 --index-url", cmd2[cmd2.index("--index-url") + 1] == "https://mirror/simple")
    check("指定代理时带 --proxy", cmd2[cmd2.index("--proxy") + 1] == "http://127.0.0.1:20809")
    check("upgrade 时带 --upgrade", "--upgrade" in cmd2)

    attempts = envsetup.install_attempts("http://127.0.0.1:9/manual")
    check("手动配置的代理被选中", envsetup.first_proxy("http://127.0.0.1:9/manual")
          == "http://127.0.0.1:9/manual")
    check("每个源都同时准备了「带代理」与「直连」两条",
          sum(1 for _d, _i, p in attempts if p) >= 1
          and sum(1 for _d, _i, p in attempts if not p) >= 2, str(attempts))
    check("默认源排在镜像前面",
          attempts[0][0].startswith("PyPI 默认源"), attempts[0][0])
    check("最后一条一定是镜像 + 直连",
          attempts[-1][0] == "清华镜像 + 直连", attempts[-1][0])
    check("源只可能是默认源或清华镜像",
          all(i in ("", "https://pypi.tuna.tsinghua.edu.cn/simple")
              for _d, i, _p in attempts))

    os.environ["HTTP_PROXY"] = "http://dead-proxy.invalid:1"
    os.environ["HTTPS_PROXY"] = "http://dead-proxy.invalid:1"
    env = envsetup.pip_env()
    check("pip 环境抹掉继承来的 HTTP_PROXY", "HTTP_PROXY" not in env)
    check("pip 环境抹掉继承来的 HTTPS_PROXY", "HTTPS_PROXY" not in env)
    check("os.environ 本身没被动过", os.environ.get("HTTP_PROXY") == "http://dead-proxy.invalid:1")
    check("pip 环境强制 UTF-8 输出", env.get("PYTHONIOENCODING") == "utf-8")
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)

    check("_last_line 取末行并截断", envsetup._last_line("a\n\n  b  \n") == "b")
    check("_last_line 空输入有兜底", envsetup._last_line("") == "未知错误")
    ok, out = envsetup._run([sys.executable, "-c", "print('hi')"])
    check("_run 能跑到成功", ok and "hi" in out, out)
    ok, out = envsetup._run([sys.executable, "-c", "import sys; sys.exit(3)"])
    check("_run 把非零退出判为失败", not ok)
    ok, out = envsetup._run([str(TMP / "definitely-not-here.exe")])
    check("_run 对不存在的程序不抛异常", not ok and out)

    # ======================================================== C 诊断与修复
    section("C. 诊断：坏环境逐项识别")
    gw = make_gateway(TMP / "gw")
    port, holder = occupied_port()
    try:
        cfg = Config()
        cfg.set("gateway.dir", str(TMP / "nowhere"))
        cfg.set("app.last_gateway_dir", str(gw))
        cfg.set("gateway.port", port)
        cfg.set("gateway.api_key", "")
        cfg.set("gateway.extra_args", ["--desensitize"])
        cfg.set("gateway.python", str(TMP / "nowhere" / "python.exe"))

        real_find, real_pick = envsetup_mod.find_python, envsetup_mod.pick_base_python
        real_probe = envsetup_mod.probe_modules

        rep = envsetup.diagnose(cfg, deep=False)
        check("数据目录指针项在位", rep.by_key("pointer") is not None)
        check("网关源码项被判为可修",
              rep.by_key("gateway_dir").state == "fix", rep.by_key("gateway_dir").detail)
        check("报出建议改用的目录",
              str(gw) in rep.by_key("gateway_dir").detail)
        check("端口被占用判为可修", rep.by_key("port").state == "fix",
              rep.by_key("port").detail)
        check("空 API Key 判为可修且标明是唯一防线",
              rep.by_key("api_key").state == "fix"
              and "防线" in rep.by_key("api_key").detail)
        check("缺启动参数判为可修", rep.by_key("args").state == "fix",
              rep.by_key("args").detail)
        check("缺 --no-compact 被点名",
              "--no-compact" in rep.by_key("args").detail, rep.by_key("args").detail)
        check("网关未定位时补丁项不乱报失败（只提醒）",
              rep.by_key("patch").state == "warn", rep.by_key("patch").detail)
        check("网关未定位时 WebUI 项不乱报失败（只提醒）",
              rep.by_key("webui").state == "warn", rep.by_key("webui").detail)
        check("有可自动修项时不报 fail",
              {i.key for i in rep.failures} <= {"python"}, str([i.key for i in rep.failures]))

        # --- 解释器三种形态（把探测换成受控注入，保证离线且确定）
        envsetup_mod.find_python = lambda *_a, **_k: (None, [])
        envsetup_mod.pick_base_python = lambda *_a, **_k: None
        rep = envsetup.diagnose(cfg, deep=False)
        check("没有可用 Python 时判为「可自动修」（会下载内置 Python）",
              rep.by_key("python").state == "fix"
              and "内置" in rep.by_key("python").detail,
              rep.by_key("python").detail)
        check("禁止自动安装时改为 fail 并给出安装指引",
              envsetup.diagnose(cfg, deep=False, can_install=False)
              .by_key("python").state == "fail")
        check("禁止安装时的提示仍含 PATH 指引",
              "Add python.exe to PATH" in envsetup.diagnose(
                  cfg, deep=False, can_install=False).by_key("python").detail)

        envsetup_mod.pick_base_python = lambda *_a, **_k: Path(sys.executable)
        rep = envsetup.diagnose(cfg, deep=False)
        check("有能跑但缺依赖的解释器 → fix（装依赖）",
              rep.by_key("python").state == "fix"
              and "缺" in rep.by_key("python").detail,
              rep.by_key("python").detail)

        envsetup_mod.find_python = lambda *_a, **_k: (Path(sys.executable), [])
        rep = envsetup.diagnose(cfg, deep=False)
        check("探测到合格解释器 → fix（换过去）",
              rep.by_key("python").state == "fix"
              and "改用" in rep.by_key("python").detail,
              rep.by_key("python").detail)

        envsetup_mod.probe_modules = lambda *_a, **_k: (True, [], "可用")
        cfg.set("gateway.python", sys.executable)
        rep = envsetup.diagnose(cfg)
        check("配好的解释器可用 → ok",
              rep.by_key("python").state == "ok", rep.by_key("python").detail)

        # --- 单项修复
        section("D. 单项修复")
        ok, msg = envsetup.fix_gateway_dir(cfg)
        check("网关目录被改用自动找到的那份", ok and cfg.get("gateway.dir") == str(gw), msg)
        check("同时记住 last_gateway_dir", cfg.get("app.last_gateway_dir") == str(gw))

        # --- D2. 「有 converter.py、缺 app/ 包」的部分目录：必须**就地补齐**
        # 2026-09-23 用户报的 8 段 traceback 就是这个形态 —— 以前它算合格目录，
        # 一路放行到启动，子进程只在 converter.py 第 1 段崩一句
        # ModuleNotFoundError: No module named 'app'。
        section("D2. 缺 app/ 的部分目录：就地补齐")
        partial = TMP / "gw_partial_noapp"
        partial.mkdir(parents=True, exist_ok=True)
        (partial / "converter.py").write_text(
            "from app.desensitize import TERMS\n", encoding="utf-8")
        check("部分目录判为「不完整」（不再是合格网关目录）",
              paths.is_gateway_dir(partial) is False)
        check("缺的正是 app/__init__.py",
              paths.gateway_dir_missing(partial) == ("app/__init__.py",),
              str(paths.gateway_dir_missing(partial)))

        cfg_p = Config()
        cfg_p.set("gateway.dir", str(partial))
        cfg_p.set("gateway.python", sys.executable)
        cfg_p.set("gateway.api_key", "k")
        cfg_p.set("gateway.port", 9791)
        item_p = envsetup.diagnose(cfg_p, deep=False).by_key("gateway_dir")
        check("体检判为可修并点名 app/",
              item_p.state == "fix" and "app" in item_p.detail,
              f"{item_p.state} / {item_p.detail[:80]}")

        seen_p: list = []
        orig_fetch = envsetup.fetch_gateway
        envsetup.fetch_gateway = lambda c2, *, log=None, into=None: (
            seen_p.append(into) or (True, "[spy]"))
        try:
            ok_p, msg_p = envsetup.fix_gateway_dir(cfg_p)
        finally:
            envsetup.fetch_gateway = orig_fetch
        check("★ 修复动作是「就地补齐」（into 就是这个目录）",
              ok_p is True and seen_p == [partial], f"{msg_p} / {seen_p}")
        check("配置里的路径没被改掉", str(cfg_p.get("gateway.dir")) == str(partial))
        check("没有把部分目录记成 last_gateway_dir",
              str(cfg_p.get("app.last_gateway_dir") or "") != str(partial))

        rep_d = envsetup.diagnose(cfg)
        check("网关定位后补丁判为完好", rep_d.by_key("patch").state == "ok",
              rep_d.by_key("patch").detail)
        check("网关定位后内置 WebUI 判为可修", rep_d.by_key("webui").state == "fix",
              rep_d.by_key("webui").detail)

        ok, msg = envsetup.fix_port(cfg)
        check("占用端口被换掉", ok and cfg.get("gateway.port") != port, msg)
        check("新端口是空闲的", paths_default_port_free(int(cfg.get("gateway.port"))))

        ok, msg = envsetup.fix_api_key(cfg)
        check("空 Key 被生成", ok and len(str(cfg.get("gateway.api_key"))) > 20, msg)
        again = cfg.get("gateway.api_key")
        envsetup.fix_api_key(cfg)
        check("已有 Key 不会被覆盖", cfg.get("gateway.api_key") == again)

        ok, msg = envsetup.fix_args(cfg)
        check("启动参数被补齐", ok and not patcher_missing(cfg.get("gateway.extra_args")), msg)
        check("补齐不会产生重复项",
              len(cfg.get("gateway.extra_args")) == len(set(cfg.get("gateway.extra_args"))))
        before_args = list(cfg.get("gateway.extra_args"))
        envsetup.fix_args(cfg)
        check("参数已齐时保持原样", cfg.get("gateway.extra_args") == before_args)

        ok, msg = envsetup.fix_webui(cfg)
        check("内置 WebUI 被补齐", ok and (gw / "web" / "dist" / "index.html").is_file(), msg)
        check("补齐后诊断判为 ok",
              envsetup.diagnose(cfg).by_key("webui").state == "ok")

        # 补丁被冲掉 → fix_patch 能补回，且先备份
        (gw / "app" / "desensitize.py").write_text(WIPED_TERMS, encoding="utf-8")
        check("补丁被冲掉后诊断判为可修",
              envsetup.diagnose(cfg).by_key("patch").state == "fix")
        ok, msg = envsetup.fix_patch(cfg)
        check("fix_patch 补回品牌词", ok, msg)
        check("fix_patch 留下备份",
              any((paths.backup_dir()).glob("desensitize.py-*.bak")), msg)

        # --- setup 端到端（离线：解释器探测已被注入替身，且禁止安装）
        section("E. setup 端到端（不联网）")
        cfg2 = Config()
        cfg2.set("gateway.dir", str(TMP / "nowhere"))
        cfg2.set("app.last_gateway_dir", str(gw))
        cfg2.set("gateway.port", port)
        cfg2.set("gateway.api_key", "")
        cfg2.set("gateway.extra_args", [])
        cfg2.set("gateway.python", str(TMP / "nowhere" / "python.exe"))
        envsetup_mod.find_python = lambda *_a, **_k: (None, [])
        envsetup_mod.pick_base_python = lambda *_a, **_k: None

        logs: list[str] = []
        ready, lines = envsetup.setup(cfg2, on_log=logs.append,
                                      allow_install=False, prefer_venv=False)
        check("日志分三段（体检/修复/复检）",
              any("① 体检" in ln for ln in lines)
              and any("② 修复" in ln for ln in lines)
              and any("③ 复检" in ln for ln in lines))
        check("on_log 与返回的日志一致", logs == lines)
        check("网关目录已修好", cfg2.get("gateway.dir") == str(gw))
        check("端口已换到空闲口", cfg2.get("gateway.port") != port)
        check("API Key 已生成", bool(str(cfg2.get("gateway.api_key")).strip()))
        check("启动参数已补齐", not patcher_missing(cfg2.get("gateway.extra_args")))
        check("WebUI 已补齐", (gw / "web" / "dist" / "index.html").is_file())
        check("禁止安装时解释器项报失败（不静默跳过）",
              ready is False and "解释器" in lines[-2] + lines[-1],
              lines[-1])
        check("失败项写明需要手动处理", "需要手动处理" in lines[-1], lines[-1])

        # 同一份配置再跑一次：应当「没有需要自动修复的项…」且解释器那项仍失败
        _ready2, lines2 = envsetup.setup(cfg2, allow_install=False, prefer_venv=False)
        check("第二次跑不再重复改动",
              any("没有需要自动修复的项" in ln for ln in lines2),
              [ln for ln in lines2 if "修复" in ln])

        # 换成"合格解释器"→ 应当直接就绪
        envsetup_mod.find_python = lambda *_a, **_k: (Path(sys.executable), [])
        envsetup_mod.probe_modules = lambda *_a, **_k: (True, [], "可用")
        cfg2.set("gateway.python", sys.executable)
        ready3, lines3 = envsetup.setup(cfg2, allow_install=False, prefer_venv=False)
        check("解释器可用后整体就绪", ready3 is True, lines3[-1])
        check("就绪时给出明确的下一步", "可以启动网关" in lines3[-1], lines3[-1])
        check("配置已落盘（能被重新读回）",
              Config.load().get("gateway.dir") == str(gw))

        envsetup_mod.find_python, envsetup_mod.pick_base_python = real_find, real_pick
        envsetup_mod.probe_modules = real_probe
    finally:
        holder.shutdown()

    # ======================================================== F 解释器挑选
    section("F. 能跑就行：pick_base_python")
    check("能跑的解释器会被选中", envsetup.pick_base_python(Path(sys.executable))
          == Path(sys.executable))
    check("不存在的路径不会被选中", envsetup._runs(TMP / "no-such.exe") is False)
    check("指向个人目录的假路径不会让它误判",
          envsetup.pick_base_python(TMP / "no-such.exe") is not None)

    # ======================================================== G 迁移指针
    section("G. 数据目录指针：新位置优先 + 老位置兜底 + 写回退")
    primary, legacy = paths.pointer_primary_path(), paths.pointer_legacy_path()
    check("首选位置在程序目录旁边（不在数据目录里）",
          primary.parent == Path(os.environ["LUOBOBOX_POINTER_DIR"]), str(primary))
    check("首选位置不在出厂默认数据目录内部",
          paths.default_data_dir() not in primary.parents)
    check("老位置仍在出厂默认目录（兼容 ≤ v1.0.6）",
          legacy.parent == paths.default_data_dir(), str(legacy))
    check("读取顺序是新在前", paths.pointer_candidates() == (primary, legacy))

    data_a = TMP / "dataA"
    data_a.mkdir(exist_ok=True)
    landed, note = paths.write_pointer(data_a)
    check("有写权限时指针写在首选位置", landed == primary, str(landed))
    check("写在首选位置时不产生告警", note == "", note)
    check("指针内容是新数据目录",
          primary.read_text(encoding="utf-8").strip() == str(data_a.resolve()))

    primary.unlink()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(str(data_a), encoding="utf-8")
    check("只有老指针时也能读出数据目录", paths.data_dir_override() == data_a.resolve())
    check("读一次就把老指针自愈到新位置（老文件已删）",
          primary.is_file() and not legacy.is_file())

    sync_note = paths.sync_pointer_home()
    check("已经没有老指针时自愈不做任何事", sync_note is None)

    # 写不进首选位置 → 退回老位置，并且必须**如实说明**
    blocked = TMP / "blocked-is-a-file"
    blocked.write_text("x", encoding="utf-8")
    os.environ["LUOBOBOX_POINTER_DIR"] = str(blocked / "sub")
    try:
        data_b = TMP / "dataB"
        data_b.mkdir(exist_ok=True)
        fallback_primary = paths.pointer_primary_path()
        landed2, note2 = paths.write_pointer(data_b)
        check("首选位置不可写时退回老位置", landed2 == paths.pointer_legacy_path(),
              str(landed2))
        check("退回老位置会明确告警（不假装成功）", "⚠" in note2, note2)
        check("退回后仍能读出数据目录", paths.data_dir_override() == data_b.resolve())
        check("不可写位置不会被误建出目录", not fallback_primary.parent.is_dir())
    finally:
        os.environ["LUOBOBOX_POINTER_DIR"] = str(TMP / "appdir")

    ok_reset, note_reset = paths.reset_data_dir_pointer()
    check("撤销迁移能删掉老位置的指针", ok_reset, note_reset)
    check("撤销后两个位置都不存在指针",
          not primary.is_file() and not legacy.is_file())
    ok_reset2, note_reset2 = paths.reset_data_dir_pointer()
    check("无指针时撤销给出说明而不是报错", (not ok_reset2) and "本来" in note_reset2, note_reset2)
    check("指针项：无老指针时为 ok",
          envsetup.pointer_item().state == "ok", envsetup.pointer_item().detail)

    # 老指针存在时 → 判为可修，修复后老文件消失
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(str(data_a), encoding="utf-8")
    check("老指针存在时判为可修", envsetup.pointer_item().state == "fix")
    ok, msg = envsetup.fix_pointer()
    check("fix_pointer 把指针搬走", ok and not legacy.is_file() and primary.is_file(), msg)

    # ======================================================== H 迁移
    section("H. 迁移：指针落在新位置")
    move_src = paths.data_dir()
    move_dst = TMP / "moved"
    (move_src / "marker.txt").write_text("hello", encoding="utf-8")
    ok, msg = paths.migrate_data_dir(move_dst, move=False)
    check("迁移成功", ok, msg)
    check("迁移提示里写明指针落在哪", str(primary) in msg, msg)
    check("迁移后指针在新位置", primary.is_file())
    check("数据确实过去了", (move_dst / "marker.txt").read_text(encoding="utf-8") == "hello")
    check("迁移不把指针自己复制过去", not (move_dst / "datadir.txt").is_file())
    paths.reset_data_dir_pointer()

    # ======================================================== I 内置 Python
    section("I. 内置 Python：本机一个都没有也能一键装齐（离线替身）")

    # --- 纯函数：_pth 改写（这一步做错，装进去的包会 import 不到）
    pth_root = TMP / "pth-case"
    pth_root.mkdir(parents=True, exist_ok=True)
    pth = pth_root / "python313._pth"
    pth.write_text("python313.zip\n.\n#import site\n", encoding="utf-8")
    envsetup.patch_embedded_pth(pth_root)
    patched = pth.read_text(encoding="utf-8")
    plines = patched.splitlines()
    check("_pth 补上了 site-packages", "Lib\\site-packages" in plines, patched)
    check("_pth 打开了 site（默认那行是注释掉的）", "import site" in plines, patched)
    check("_pth 保留了 stdlib 压缩包与当前目录",
          "python313.zip" in plines and "." in plines, patched)
    check("_pth 里没有残留注释行",
          not any(ln.startswith("#") for ln in plines), patched)
    envsetup.patch_embedded_pth(pth_root)
    again = pth.read_text(encoding="utf-8").splitlines()
    check("_pth 改写是幂等的（不重复追加）",
          again.count("import site") == 1 and again.count("Lib\\site-packages") == 1,
          str(again))

    empty_root = TMP / "pth-empty"
    empty_root.mkdir(exist_ok=True)
    try:
        envsetup.patch_embedded_pth(empty_root)
        check("缺 _pth 时报错而不是装作成功", False)
    except FileNotFoundError:
        check("缺 _pth 时报错而不是装作成功", True)

    # --- provision_builtin_python：网络 + 子进程全部换成替身（真逻辑，假 IO）
    import shutil as _sh
    import zipfile as _zip

    from luobobox import net as net_mod
    from luobobox.net import Route

    fake_zip = TMP / "fake-embed.zip"
    with _zip.ZipFile(fake_zip, "w") as zf:
        zf.writestr("python313._pth", "python313.zip\n.\n#import site\n")
        zf.writestr("python.exe", b"MZ fake")
        zf.writestr("python313.zip", b"PK fake stdlib")

    calls: list[str] = []
    real_download = net_mod.download
    real_run = envsetup_mod._run
    real_probe2 = envsetup_mod.probe_modules

    def fake_download(url, dest, **_kw):
        calls.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy2(fake_zip, dest)
        return dest, Route(desc="替身")

    net_mod.download = fake_download
    envsetup_mod._run = lambda argv, **_kw: (True, "ok")
    envsetup_mod.probe_modules = lambda *_a, **_k: (True, [], "可用")
    try:
        logs_i: list[str] = []
        okI, msgI = envsetup.provision_builtin_python(log=logs_i.append)
        check("内置 Python 部署成功", okI, msgI)
        check("落点是数据目录下的 python\\",
              envsetup.builtin_python_dir() == paths.data_dir() / "python",
              str(envsetup.builtin_python_dir()))
        check("python.exe 已就位", envsetup.builtin_python_exe().is_file())
        check("_pth 已被改写（site-packages 可见）",
              "Lib\\site-packages" in
              (envsetup.builtin_python_dir() / "python313._pth").read_text(encoding="utf-8"))
        check("下载的压缩包不留残余（解开即删，stdlib 的 python313.zip 要留）",
              not (envsetup.builtin_python_dir()
                   / f"python-{envsetup.EMBED_PY_VERSION}-embed-{envsetup.embed_arch()}.zip")
              .exists()
              and (envsetup.builtin_python_dir() / "python313.zip").is_file(),
              str([p.name for p in envsetup.builtin_python_dir().iterdir()]))
        check("pip 引导的临时目录已清理",
              not (envsetup.builtin_python_dir() / "_bootstrap").exists())
        check("日志里说明了「免安装」", any("免安装" in ln for ln in logs_i), str(logs_i[:3]))
        check("先试官方源（顺序不能反）",
              bool(calls) and "python.org" in calls[0], str(calls[:2]))
        check("下载地址按本机架构选包（不是写死 amd64）",
              f"-embed-{envsetup.embed_arch()}.zip" in calls[0], str(calls[:1]))
        check("架构识别：ARM64 → arm64", envsetup.embed_arch("ARM64") == "arm64")
        check("架构识别：AMD64 → amd64", envsetup.embed_arch("AMD64") == "amd64")
        check("架构识别：前后空格不敏感",
              envsetup.embed_arch(" arm64 ") == "arm64")
        check("架构识别：读不到时兜底 amd64（ARM64 也能 x64 仿真跑）",
              envsetup.embed_arch("") == "amd64")
        check("每种已声明的架构都能拼出下载地址",
              all(f"-embed-{a}.zip" in envsetup.EMBED_PY_MIRRORS[0][1].format(v="1", arch=a)
                  for a in envsetup.EMBED_PY_ARCHES))

        before = len(calls)
        okI2, msgI2 = envsetup.provision_builtin_python()
        check("已有可用内置 Python 时复用、不重复下载",
              okI2 and len(calls) == before and "复用" in msgI2, msgI2)

        cfg3 = Config()
        cfg3.set("gateway.python", str(TMP / "nowhere" / "python.exe"))
        envsetup_mod.find_python = lambda *_a, **_k: (None, [])
        envsetup_mod.pick_base_python = lambda *_a, **_k: None
        okI3, msgI3 = envsetup.fix_python(cfg3, prefer_venv=True)
        check("一个解释器都没有时 fix_python 会部署内置 Python",
              okI3 and str(cfg3.get("gateway.python")) ==
              str(envsetup.builtin_python_exe()), msgI3)

        # 现成解释器怎么都修不好 → 最后一级兜底也必须是内置 Python（不能把用户卡死）
        real_mv, real_id = envsetup_mod.make_venv, envsetup_mod._install_deps
        envsetup_mod.make_venv = lambda *_a, **_k: (False, "建不了独立环境")
        envsetup_mod._install_deps = lambda *_a, **_k: (False, "装不上依赖")
        envsetup_mod.pick_base_python = lambda *_a, **_k: Path(sys.executable)
        try:
            cfg5 = Config()
            cfg5.set("gateway.python", "")
            okI5, msgI5 = envsetup.fix_python(cfg5, prefer_venv=True)
        finally:
            envsetup_mod.make_venv, envsetup_mod._install_deps = real_mv, real_id
        check("现成解释器修不好时兜底部署内置 Python",
              okI5 and str(cfg5.get("gateway.python")) ==
              str(envsetup.builtin_python_exe()), msgI5)

        # 下载全失败 → 必须报失败并带上最后一条原因（不能假装成功）
        net_mod.download = lambda *_a, **_k: (_ for _ in ()).throw(
            OSError("连不上（探活超时）"))
        envsetup_mod.probe_modules = lambda *_a, **_k: (False, [], "文件不存在")
        envsetup.builtin_python_exe().unlink()
        okI4, msgI4 = envsetup.provision_builtin_python()
        check("所有镜像都下不动时报失败并把原因带回",
              (not okI4) and "连不上" in msgI4, msgI4)
    finally:
        net_mod.download = real_download
        envsetup_mod._run = real_run
        envsetup_mod.probe_modules = real_probe2

    # ======================================================== J 自动获取网关
    section("J. 网关源码：本机没有时自动从上游拉一份（离线替身）")
    real_locate = envsetup_mod.locate_gateway_dir
    envsetup_mod.locate_gateway_dir = lambda *_a, **_k: None   # 强制「本地确实没有」
    try:
        cfg_probe = Config()
        cfg_probe.set("gateway.dir", str(TMP / "nowhere"))
        cfg_probe.set("app.last_gateway_dir", "")
        item_j = envsetup.diagnose(cfg_probe, deep=False).by_key("gateway_dir")
    finally:
        envsetup_mod.locate_gateway_dir = real_locate
    check("本地找不到网关时判为「可自动修」（不是 fail）",
          item_j.state == "fix" and "自动下载" in item_j.fix_label,
          f"{item_j.state} / {item_j.fix_label}")

    from luobobox import updater as updater_mod

    real_check = updater_mod.check
    real_dl = updater_mod.download
    real_apply = updater_mod.apply_release
    gw_new = paths.data_dir() / "gateway" / "codebuddy2api"

    class _Info:
        tag = "v9.9.9"
        error = ""
        zipball = "https://example.invalid/zb"
        tarball = ""

        def url_ok(self):
            return True

    def fake_apply(target, _archive):
        target = Path(target)
        make_gateway(target)
        (target / "VERSION").write_text("9.9.9", encoding="utf-8")
        return updater_mod.ApplyResult(ok=True, message="已覆盖")

    def _cfg_absent(repo: str) -> Config:
        c = Config()
        c.set("updater.repo", repo)
        c.set("gateway.dir", str(TMP / "nowhere"))
        c.set("app.last_gateway_dir", "")
        return c

    updater_mod.check = lambda *_a, **_k: _Info()
    updater_mod.download = lambda *_a, **_k: TMP / "fake.tar.gz"
    updater_mod.apply_release = fake_apply
    envsetup_mod.locate_gateway_dir = lambda *_a, **_k: None
    try:
        cfgJ = _cfg_absent("someone/codebuddy2api")
        okJ, msgJ = envsetup.fix_gateway_dir(cfgJ)
        check("自动拉取网关源码成功", okJ, msgJ)
        check("拉到了数据目录下的 gateway\\codebuddy2api",
              cfgJ.get("gateway.dir") == str(gw_new), str(cfgJ.get("gateway.dir")))
        check("同时记住 last_gateway_dir",
              cfgJ.get("app.last_gateway_dir") == str(gw_new))
        check("日志里报出上游版本", "9.9.9" in msgJ, msgJ)

        okJ2, msgJ2 = envsetup.fix_gateway_dir(_cfg_absent(""))
        check("未配置上游仓库时如实失败（不静默）",
              (not okJ2) and "仓库" in msgJ2, msgJ2)

        updater_mod.check = lambda *_a, **_k: updater_mod.ReleaseInfo(
            error="检查更新失败：连不上 GitHub")
        okJ3, msgJ3 = envsetup.fix_gateway_dir(_cfg_absent("someone/codebuddy2api"))
        check("上游不可达时带回上游错误原文", (not okJ3) and "GitHub" in msgJ3, msgJ3)
    finally:
        updater_mod.check = real_check
        updater_mod.download = real_dl
        updater_mod.apply_release = real_apply
        envsetup_mod.locate_gateway_dir = real_locate

    # ======================================================== 收尾
    total = _passed + len(_failed)
    print("\n" + "=" * 64)
    print(f"通过 {_passed} / {total}")
    if _failed:
        print("失败项：")
        for name in _failed:
            print(f"  · {name}")
    print("=" * 64)
    return 0 if not _failed else 1


def paths_default_port_free(port: int) -> bool:
    from luobobox.config import port_free

    return port_free(port)


def patcher_missing(args) -> list[str]:
    from luobobox import patcher

    return patcher.check_args(list(args or []))


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
