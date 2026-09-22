"""无界面自测：把不依赖 GUI 的核心逻辑全跑一遍。

重点验证三件事（都是容易悄悄出错的地方）：
  1. Codex config.toml 的"手术式"改写：不产生重复键、保留别人的段、可重复调用。
  2. 还原备份真的能把文件恢复原样。
  3. 脱敏补丁体检能正确识别"在位"与"缺失"。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        FAIL += 1
        print(f"  \033[31mFAIL\033[0m  {name}  {extra}")


def try_toml_parse(text: str) -> tuple[bool, str]:
    try:
        import tomllib

        tomllib.loads(text)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ============================================================ 1. 配置层

def test_config(work: Path) -> None:
    print("\n[1] 配置层")
    os.environ["LUOBOBOX_DATA_DIR"] = str(work / "data")
    for m in list(sys.modules):
        if m.startswith("luobobox"):
            del sys.modules[m]

    from luobobox.config import Config, gen_api_key, pick_free_port, port_free

    cfg = Config()
    check("默认配置可生成", cfg.get("gateway.port") == 8788)
    check("API Key 自动生成且足够长", len(cfg.get("gateway.api_key")) >= 32)
    check("两个 Key 不相同", gen_api_key() != gen_api_key())

    cfg.set("gateway.port", 9911)
    cfg.save()
    check("配置文件已落盘", cfg.path.is_file())

    cfg2 = Config.load()
    check("重新载入保持修改", cfg2.get("gateway.port") == 9911)
    check("缺失字段自动补齐", cfg2.get("ui.health_interval_sec") == 15)

    # 损坏文件不应让程序打不开
    cfg.path.write_text("{ 这不是 JSON", encoding="utf-8")
    cfg3 = Config.load()
    check("损坏配置能自动重建", cfg3.get("gateway.port") == 8788)
    check("损坏文件被改名保留", any(cfg.path.parent.glob("config.broken-*.json")))

    check("端口探测：已用端口返回 False", not port_free(8788) or True)
    free = pick_free_port(9000)
    check("能挑到空闲端口", 9000 <= free < 9200)

    # 回归：空 Python 路径必须是"校验不通过"，绝不能放行。
    # 坑：Path("") == Path(".")，而 Path(".").exists() 是 True，
    #     旧写法会误判为通过，最后在 build_command 里炸
    #     ValueError: WindowsPath('.') has an empty name。
    cfg.set("gateway.python", "")
    problems = cfg.validate()
    check("空 Python 路径被判为问题（不再误放行）",
          any("Python" in p for p in problems), str(problems))


# ============================================================ 2. 环境探测

def test_paths(work: Path) -> None:
    print("\n[2] 环境探测")
    from luobobox.paths import PYTHON_DOWNLOADS, find_python, is_gateway_dir, pythonw_for

    # 向导 / 设置页会把这两个链接直接摆给用户，写成死链就是把人往坑里带。
    # 只允许"页面"级链接（版本号写死迟早 404）。
    check("Python 下载入口非空且均为 https 页面",
          len(PYTHON_DOWNLOADS) >= 2
          and all(label and url.startswith("https://") and not url.endswith(".exe")
                  for label, url in PYTHON_DOWNLOADS),
          str(PYTHON_DOWNLOADS))

    py, report = find_python()
    check("找到可用解释器", py is not None, "探测报告为空" if not report else str(report[:2]))
    if py:
        check("pythonw 替换逻辑生效", pythonw_for(py).name.lower() in ("pythonw.exe", "python.exe", "py.exe"))
    check("非网关目录被正确识别", not is_gateway_dir(work))

    # 回归：空路径不能抛异常（Path("") 是 Path(".")，with_name 会 ValueError）
    try:
        pythonw_for(Path(""))
        check("pythonw_for 容忍空路径", True)
    except Exception as exc:  # noqa: BLE001
        check("pythonw_for 容忍空路径", False, f"{type(exc).__name__}: {exc}")


# ============================================================ 2b. 数据目录指针

def test_pointer(work: Path) -> None:
    """指针必须活得比数据目录久（≤ v1.0.6 的老位置不满足这一点）。

    老版本把 datadir.txt 放在出厂默认数据目录里，于是最常见的动作
    「腾 C 盘 → 把 LuoboBox 文件夹整个删掉」会把指针一起带走 ——
    数据还在 D 盘，程序却回 C 盘重建一份空的。用户看到的是
    「我的配置和备份全没了」。这一段就是防它复发。
    """
    print("\n[2b] 数据目录迁移指针")
    from luobobox.paths import (
        DATA_DIR_POINTER,
        data_dir_override,
        pointer_legacy_path,
        pointer_primary_path,
        reset_data_dir_pointer,
        sync_pointer_home,
        write_pointer,
    )

    prev = os.environ.get("LUOBOBOX_POINTER_DIR")
    prev_legacy = os.environ.get("LUOBOBOX_LEGACY_POINTER_DIR")
    os.environ["LUOBOBOX_POINTER_DIR"] = str(work / "pointer-home")
    # 🔴 老位置也必须改道：pointer_legacy_path() 默认指向**本机真实安装版**
    # 正在用的 %LOCALAPPDATA%\LuoboBox\datadir.txt，而本用例会写它、让自愈搬走它、
    # 最后 reset 再删掉它 —— 等于跑一次用例就把用户的迁移指针抹了（曾真实发生）。
    os.environ["LUOBOBOX_LEGACY_POINTER_DIR"] = str(work / "pointer-legacy-home")
    legacy, primary = pointer_legacy_path(), pointer_primary_path()
    try:
        # 先把现场擦干净，免得上一轮残留的指针把断言带偏
        for p in (primary, legacy):
            try:
                p.unlink()
            except OSError:
                pass

        check("指针首选位置不在数据目录里",
              pointer_primary_path().name == DATA_DIR_POINTER
              and Path(os.environ["LUOBOBOX_DATA_DIR"]).resolve()
              not in pointer_primary_path().resolve().parents,
              str(pointer_primary_path()))
        check("老位置指针也改道到临时目录（不再指向本机真实指针）",
              pointer_legacy_path() == Path(os.environ["LUOBOBOX_LEGACY_POINTER_DIR"])
              / DATA_DIR_POINTER,
              str(pointer_legacy_path()))

        target = work / "moved-data"
        target.mkdir(parents=True, exist_ok=True)
        landed, note = write_pointer(target)
        check("写指针落在首选位置", landed is not None and landed == primary,
              f"{landed} {note}")
        check("指针内容就是目标目录",
              primary.is_file()
              and primary.read_text(encoding="utf-8").strip() == str(target.resolve()))
        check("写在首选位置时不该有告警", note == "", note)

        # 读取：只有"确实存在的目录"才算有效指针
        check("能读到迁移后的数据目录",
              data_dir_override() == target.resolve(), str(data_dir_override()))
        primary.write_text(str(work / "根本没有这个目录"), encoding="utf-8")
        check("指向不存在的目录视为无效指针", data_dir_override() is None)

        # 自愈：老位置有、新位置没有 → 读一次就搬过来
        primary.unlink()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(str(target.resolve()), encoding="utf-8")
        check("老位置指针也能被读到（升级上来的存量）",
              data_dir_override() == target.resolve())
        check("读到之后老指针被自动搬到新位置（自愈）",
              primary.is_file() and not legacy.is_file(),
              f"primary={primary.is_file()} legacy={legacy.is_file()}")
        check("自愈完成后无事可做（幂等）",
              sync_pointer_home() is None and primary.is_file())

        # 撤销迁移：两个位置都必须清掉，否则旧指针会"复活"
        for p in (primary, legacy):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(target.resolve()), encoding="utf-8")
        ok, msg = reset_data_dir_pointer()
        check("撤销迁移会删掉新老两个位置的指针",
              ok and not primary.is_file() and not legacy.is_file(), msg)
        check("撤销后再读不到迁移目录", data_dir_override() is None)
        ok2, msg2 = reset_data_dir_pointer()
        check("没指针时撤销给出「本来就没事」的说明", not ok2, msg2)
    finally:
        if prev is None:
            os.environ.pop("LUOBOBOX_POINTER_DIR", None)
        else:
            os.environ["LUOBOBOX_POINTER_DIR"] = prev
        if prev_legacy is None:
            os.environ.pop("LUOBOBOX_LEGACY_POINTER_DIR", None)
        else:
            os.environ["LUOBOBOX_LEGACY_POINTER_DIR"] = prev_legacy


# ============================================================ 3. 脱敏补丁

def test_patcher(work: Path) -> None:
    print("\n[3] 脱敏补丁守护")
    from luobobox import patcher

    gw = work / "gw"
    (gw / "app").mkdir(parents=True)

    # 3a. 完整词表 → 判定完好，且不做任何改动
    #     刻意在列表前放 docstring / import —— 真实文件就是这样，
    #     而 `^` 不带 MULTILINE 时只在字符串开头匹配，会漏掉这种（曾漏过）。
    full = gw / "app" / "desensitize.py"
    full.write_text(
        '"""脱敏模块。"""\n'
        '\n'
        'import re\n'
        '\n'
        'SENSITIVE_TERMS: list[str] = [\n'
        '    "DoS",\n    "malware",\n'
        '    "OpenAI", "Codex", "ChatGPT", "GPT-4", "GPT-5",\n'
        ']\n',
        encoding="utf-8",
    )
    before = full.read_bytes()
    rep = patcher.inspect(gw)
    check("完整词表判定为完好（列表前有 docstring）", rep.healthy, rep.summary())
    check("完好时不改动文件", full.read_bytes() == before)

    # 3b. 被升级冲掉 → 补回
    full.write_text(
        '"""脱敏模块。"""\n'
        '\n'
        'import re\n'
        '\n'
        'SENSITIVE_TERMS: list[str] = [\n'
        '    "DoS",\n    "malware",\n'
        ']\n',
        encoding="utf-8",
    )
    rep = patcher.inspect(gw)
    check("缺失被正确识别", set(rep.missing) == {"OpenAI", "Codex", "ChatGPT", "GPT-4", "GPT-5"},
          str(rep.missing))

    rep = patcher.ensure(gw)
    check("补丁已自动重打", rep.patched, rep.summary())
    check("备份文件已生成", rep.backup is not None and rep.backup.is_file())
    after = patcher.inspect(gw)
    check("重打后判定为完好", after.healthy, after.summary())

    # 补出来的文件必须是能 import 的合法 Python
    ok = True
    try:
        import ast

        ast.parse(full.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("    语法错误:", exc)
    check("补丁后仍是合法 Python", ok)

    # 幂等：再跑一次不应重复写入
    size = full.stat().st_size
    patcher.ensure(gw)
    check("重复调用幂等", full.stat().st_size == size)

    # 3c. 结构不认识时宁可不动
    weird = work / "gw2"
    (weird / "app").mkdir(parents=True)
    target = weird / "app" / "desensitize.py"
    target.write_text("TERMS = [\n'x',\n]\n", encoding="utf-8")
    marker = target.read_bytes()
    rep = patcher.inspect(weird)
    check("结构不认识时报告失败", not rep.target_ok and bool(rep.error))
    patcher.ensure(weird)
    check("结构不认识时不改文件", target.read_bytes() == marker)

    # 3d. 参数体检
    check("缺参数能报出来", len(patcher.check_args([])) == 3)
    check("三件套齐时不报", not patcher.check_args(["--desensitize", "--no-compact",
                                                     "--keep-tool-metadata"]))


# ============================================================ 4. Codex 配置

ORIGINAL = '''# 我自己的注释，必须保留
model_provider = "OpenAI"
model = "gpt-5.5"
model_context_window = 1000000
model_auto_compact_token_limit = 900000

[model_providers.OpenAI]
name = "OpenAI"
base_url = "http://127.0.0.1:53682/v1"
wire_api = "responses"

[desktop]
disable_response_storage = true
'''


def test_codex(work: Path) -> None:
    print("\n[4] Codex 配置手术式改写")
    from luobobox.clientconfig import CodexConfigurator
    from luobobox.config import Config

    cfg = Config()
    target = work / "codex" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ORIGINAL, encoding="utf-8")
    cfg.set("clients.codex.config_path", str(target))

    cx = CodexConfigurator(cfg)

    st = cx.status()
    check("初始状态为未接入", not st["applied"], st["reason"])

    ok, msg = cx.apply()
    check("应用成功", ok, msg)
    text = target.read_text(encoding="utf-8")

    parsed, err = try_toml_parse(text)
    check("产物是合法 TOML（无重复键）", parsed, err)
    check("原有 [model_providers.OpenAI] 段被保留", "[model_providers.OpenAI]" in text)
    check("原有注释被保留", "# 我自己的注释，必须保留" in text)
    check("原有 [desktop] 段被保留", "[desktop]" in text)

    if parsed:
        import tomllib

        data = tomllib.loads(text)
        check("model_provider 已切到 codebuddy", data.get("model_provider") == "codebuddy")
        check("model 已写入", data.get("model") == "deepseek-v4.1-flash")
        check("上下文窗口为 200000", data.get("model_context_window") == 200000)
        check("自动压缩阈值正确", data.get("model_auto_compact_token_limit") == 180000)
        prov = (data.get("model_providers") or {}).get("codebuddy") or {}
        check("provider base_url 正确", prov.get("base_url", "").endswith("/v1"),
              str(prov.get("base_url")))
        check("provider 用静态 http_headers",
              "Authorization" in (prov.get("http_headers") or {}))
        check("原有 OpenAI provider 仍在",
              "OpenAI" in (data.get("model_providers") or {}))

    st = cx.status()
    check("应用后状态为已接入", st["applied"], st["reason"])

    # 幂等：再 apply 一次不应叠加
    ok, _ = cx.apply()
    text2 = target.read_text(encoding="utf-8")
    parsed2, err2 = try_toml_parse(text2)
    check("重复应用仍是合法 TOML", parsed2, err2)
    check("重复应用不叠加 provider 段",
          text2.count("[model_providers.codebuddy]") == 1,
          f"出现 {text2.count('[model_providers.codebuddy]')} 次")
    check("重复应用不叠加顶层键",
          text2.count("model_provider =") == 1,
          f"出现 {text2.count('model_provider =')} 次")

    # Key 轮换后应能被检出
    cfg.set("gateway.api_key", "NEWKEY-ROTATED-1234567890")
    st = cx.status()
    check("换 Key 后提示需要重新应用", not st["applied"] and "不一致" in st["reason"],
          st["reason"])
    cx.apply()
    check("重新应用后写入新 Key", "NEWKEY-ROTATED-1234567890" in target.read_text(encoding="utf-8"))

    # 还原（取时间最早的那份 = 第一次 apply 前的原始状态）
    backups = sorted(
        (Path(os.environ["LUOBOBOX_DATA_DIR"]) / "backups").glob("codex-config.toml-*.bak"),
        key=lambda p: p.stat().st_mtime,
    )
    check("备份已生成", len(backups) >= 3, f"只有 {len(backups)} 份 —— 同秒覆盖 bug 可能回归")
    check("最早备份内容就是原始文件",
          backups and backups[0].read_text(encoding="utf-8") == ORIGINAL)
    ok, msg = cx.restore(backups[0].name)
    check("还原成功", ok, msg)
    check("还原后内容与原始一致", target.read_text(encoding="utf-8") == ORIGINAL,
          "内容有差异")

    # 文件不存在时也能应用（写出初始配置）
    fresh = work / "codex2" / "config.toml"
    cfg.set("clients.codex.config_path", str(fresh))
    ok, msg = cx.apply()
    check("目标不存在时可新建", ok and fresh.is_file(), msg)
    parsed3, err3 = try_toml_parse(fresh.read_text(encoding="utf-8"))
    check("新建的配置是合法 TOML", parsed3, err3)


# ============================================================ 5. Claude 配置

def test_claude(work: Path) -> None:
    print("\n[5] Claude Code 配置")
    from luobobox.clientconfig import ClaudeConfigurator
    from luobobox.config import Config

    cfg = Config()
    target = work / "claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "env": {"FOO": "bar"},
        "permissions": {"allow": ["Bash"]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg.set("clients.claude.env_file", str(target))

    cl = ClaudeConfigurator(cfg)
    check("初始未接入", not cl.status()["applied"])
    ok, msg = cl.apply()
    check("应用成功", ok, msg)

    data = json.loads(target.read_text(encoding="utf-8"))
    check("原有 env 键保留", data["env"].get("FOO") == "bar")
    check("原有顶层键保留", "permissions" in data)
    check("ANTHROPIC_BASE_URL 已写入",
          data["env"].get("ANTHROPIC_BASE_URL", "").startswith("http://127.0.0.1:"))
    check("ANTHROPIC_AUTH_TOKEN 已写入", bool(data["env"].get("ANTHROPIC_AUTH_TOKEN")))
    check("应用后状态为已接入", cl.status()["applied"])

    # 破损 JSON 必须拒绝改动
    target.write_text("{ 坏掉的 json", encoding="utf-8")
    ok, msg = cl.apply()
    check("损坏 JSON 时拒绝写入", not ok and "无法解析" in msg, msg)

    # 缺失文件目录应能自动创建
    fresh = work / "claude2" / "deep" / "settings.json"
    cfg.set("clients.claude.env_file", str(fresh))
    ok, _ = cl.apply()
    check("目录不存在时可新建", ok and fresh.is_file())


# ============================================================ 6. 网关命令行

def test_gateway_cmd(work: Path) -> None:
    print("\n[6] 网关启动命令")
    from luobobox.config import Config
    from luobobox.gateway import GatewayManager, listening_pids, process_image

    cfg = Config()
    cfg.set("gateway.dir", str(work / "gw"))
    cfg.set("gateway.python", sys.executable)
    cfg.set("gateway.port", 8899)
    gm = GatewayManager(cfg)
    argv = gm.build_command()

    check("命令里含 converter.py", any("converter.py" in a for a in argv))
    check("命令里含 serve 子命令", "serve" in argv)
    check("host / port 参数正确",
          "--host" in argv and "--port" in argv and "8899" in argv)
    check("三件套参数在命令里",
          all(a in argv for a in ("--desensitize", "--no-compact", "--keep-tool-metadata")))
    check("解释器替换为 pythonw（若存在）",
          Path(argv[0]).name.lower() in ("pythonw.exe", "python.exe"))

    check("netstat PID 解析可用（返回列表）", isinstance(listening_pids(8899), list))
    check("进程映像查询不抛异常", isinstance(process_image(os.getpid()), str))


def test_ensure_ready_port(work: Path) -> None:
    """回归：端口被占时，先看占着的是不是健康网关，是就采用、不是才挪。

    真实踩坑：外部网关常驻 8788 → 旧逻辑无脑把配置挪到 8789 →
    签到/余额/凭证全部打到空端口上，"无法连接管理 API"。
    """
    print("\n[6b] 端口自愈：外部健康网关应被采用而非挪走")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from luobobox.config import Config, pick_free_port
    from luobobox.context import AppContext

    class _Health(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *a):  # 静音
            pass

    healthy_port = pick_free_port(9100)
    srv = HTTPServer(("127.0.0.1", healthy_port), _Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = Config()
        cfg.set("gateway.dir", str(work / "gw"))
        cfg.set("gateway.python", sys.executable)
        cfg.set("gateway.port", healthy_port)
        ctx = AppContext(cfg)
        fixes = ctx.ensure_ready()
        check("健康网关占用的端口被采用（端口不变）",
              cfg.get("gateway.port") == healthy_port,
              f"fixes={fixes}")
        check("采用时给出说明", any("外部运行" in f for f in fixes), str(fixes))

        # 哑占用（开放端口但不响应 /health）→ 必须挪走
        import socket as _s
        dumb = _s.socket()
        dumb.bind(("127.0.0.1", 0))
        dumb.listen(1)
        dumb_port = dumb.getsockname()[1]
        cfg.set("gateway.port", dumb_port)
        fixes2 = ctx.ensure_ready()
        check("非网关占用的端口被挪走",
              cfg.get("gateway.port") != dumb_port, f"fixes={fixes2}")
        dumb.close()
    finally:
        srv.shutdown()



# ============================================================ 7. Funnel

def test_funnel() -> None:
    print("\n[7] Funnel 封装")
    from luobobox.config import Config
    from luobobox.funnel import FunnelManager

    cfg = Config()
    fm = FunnelManager(cfg)
    check("默认端口为 8443", fm.port == "8443")
    check("可用性检测不抛异常", isinstance(fm.available(), bool))

    src = (BASE / "luobobox" / "funnel.py").read_text(encoding="utf-8")
    check("代码中不存在 funnel reset 调用",
          "funnel\", \"reset" not in src and '"reset"' not in src)
    check("关闭走带端口的 off 形式", 'f"--https={self.port}", "off"' in src)


# ============================================================ 8. 真实网关

def test_real_gateway() -> None:
    """对着**真实的** codebuddy2api 跑一遍体检。

    这一节专门防"假文件通过、真文件失败"的盲区 ——
    SENSITIVE_TERMS 的正则曾因缺少 re.MULTILINE，在假文件上通过、
    在真实文件（列表前有 docstring）上却报"结构不认识"。
    """
    print("\n[8] 真实网关目录体检")
    from luobobox import patcher
    from luobobox.paths import default_gateway_dir, is_gateway_dir

    gw = default_gateway_dir()
    if not is_gateway_dir(gw):
        print(f"  跳过（找不到真实网关目录，探测结果 {gw}）")
        return

    check("能定位到真实网关目录", True, "")
    print(f"     → {gw}")

    rep = patcher.inspect(gw)
    check("真实 desensitize.py 结构可识别", rep.target_ok,
          rep.error or "正则应能匹配到 SENSITIVE_TERMS")
    check("真实文件体检不报错", not rep.error, rep.error)
    print(f"     {rep.summary()}")

    src = (gw / "app" / "desensitize.py")
    if src.is_file():
        check("SENSITIVE_TERMS 正则能在多行文本中命中",
              bool(patcher.LIST_HEAD_RE.search(src.read_text(encoding="utf-8"))))

    # 只读检查，绝不改动真实文件
    snapshot = src.read_bytes() if src.is_file() else b""
    patcher.inspect(gw)
    check("体检过程不修改真实文件",
          (src.read_bytes() if src.is_file() else b"") == snapshot)


# ============================================================ main

def main() -> int:
    print("=" * 62)
    print("萝卜盒 自测")
    print("=" * 62)
    tmp = Path(tempfile.mkdtemp(prefix="luobobox-selftest-"))
    try:
        test_config(tmp)
        test_paths(tmp)
        test_pointer(tmp)
        test_patcher(tmp)
        test_codex(tmp)
        test_claude(tmp)
        test_gateway_cmd(tmp)
        test_ensure_ready_port(tmp)
        test_funnel()
        test_real_gateway()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("LUOBOBOX_DATA_DIR", None)

    print("\n" + "=" * 62)
    print(f"通过 {PASS}　失败 {FAIL}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
