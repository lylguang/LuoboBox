"""离线自测：网络通道回退 + 数据目录迁移 + 静默安装的落盘位置。

三个**真实事故**驱动的检查（不是凭空想出来的用例）：

1. 更新下载报 ``WinError 10060 由于连接方在一段时间后没有正确答复…``。
   看着像「GitHub 挂了」，实际是进程从一个外层 shell 继承了**已经死掉的**
   ``HTTP_PROXY``，``urllib`` 老老实实去连、傻等 21 秒才报错。
   → net.py 必须先探活候选通道，死的直接跳过，并回报**实际走通**的那条。

2. 网关升级会往 ``%LOCALAPPDATA%\\LuoboBox\\backups`` 写整目录备份，
   而网关目录里的 ``web/node_modules`` 有 2 万多个小文件 / 700MB 上下 ——
   实测一次升级写出 718MB，系统盘只剩 2GB 时两三次就把 C 盘挤爆。
   → 备份要排除依赖与缓存，且备份份数要有上限。

3. 静默安装不带 ``/DIR``，Inno 的 ``{autopf}`` 在 ``PrivilegesRequired=lowest``
   下解析成 ``%LOCALAPPDATA%\\Programs`` —— 也就是**C 盘**。
   → 助手脚本必须显式 ``/DIR="%APP%"``。

全离线：不碰真网络、不碰真实数据目录（用 LUOBOBOX_DATA_DIR 隔离）。

跑法：
  python tests/net_selftest.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok]   {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  [FAIL] {name} {detail}")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- 假的 HTTP

class FakeResp:
    """够用的 urllib response 替身：read / close / 上下文管理。"""

    def __init__(self, payload: bytes = b"{}", status: int = 200):
        self._buf = payload
        self.status = status
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            out, self._buf = self._buf, b""
            return out
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


def make_opener(payload: bytes, calls: list[str]):
    """返回一个假的 _opener 工厂：记录每次真正用了哪个代理。"""

    class FakeOpener:
        def __init__(self, proxy: str):
            self.proxy = proxy

        def open(self, req, timeout=None):
            calls.append(self.proxy)
            return FakeResp(payload)

    return lambda proxy: FakeOpener(proxy)


def make_tarball(dest: Path, wrapper: str, files: dict[str, str]) -> Path:
    """造一份像 GitHub tarball 那样的归档（内容套在一个顶层目录里）。"""
    with tarfile.open(dest, "w:gz") as tf:
        for relative, text in files.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name=f"{wrapper}/{relative}")
            info.size = len(raw)
            info.mtime = 1700000000
            tf.addfile(info, io.BytesIO(raw))
    return dest


# ---------------------------------------------------------------- 测试主体

def test_proxy_candidates(net) -> None:
    print("== 1. 候选通道：排序 + 去重 ==")
    net.win_system_proxy = lambda: "127.0.0.1:20809"
    net.env_proxy = lambda: "http://127.0.0.1:3559"

    cands = net.proxy_candidates("http://127.0.0.1:9999")
    check("四条候选：手动 → 系统 → 环境 → 直连",
          [v for _, v in cands] == ["http://127.0.0.1:9999", "127.0.0.1:20809",
                                    "http://127.0.0.1:3559", ""],
          str(cands))
    check("第一条是手动配置", cands[0][0].startswith("手动配置"), cands[0][0])
    check("最后一条是直连兜底", cands[-1][1] == "")

    # 手动配置和系统代理指向同一个东西时不能出现两次
    cands2 = net.proxy_candidates("127.0.0.1:20809")
    check("重复代理被去重", len(cands2) == 3, str(cands2))

    # 什么都不配 → 只剩直连
    net.win_system_proxy = lambda: ""
    net.env_proxy = lambda: ""
    cands3 = net.proxy_candidates("")
    check("没有任何代理时只剩直连", [v for _, v in cands3] == [""], str(cands3))

    net.win_system_proxy = lambda: "127.0.0.1:20809"
    net.env_proxy = lambda: "http://127.0.0.1:3559"
    check("describe_routes 不抛异常", isinstance(net.describe_routes(""), list))
    check("win_system_proxy() 真读注册表也不抛", isinstance(net.win_system_proxy(), str))


def test_fallback(net) -> None:
    print("\n== 2. 死代理必须被跳过，而不是让用户等 21 秒 ==")
    net.win_system_proxy = lambda: "127.0.0.1:20809"
    net.env_proxy = lambda: ""
    net._proxy_alive = lambda proxy, timeout=net.PROXY_PROBE_TIMEOUT: proxy.endswith("20809")

    calls: list[str] = []
    net._opener = make_opener(b'{"ok":1}', calls)

    # 手动配了一个**已经死掉**的代理 —— 以前这里会直接失败
    resp, route = net.urlopen("https://example.com/api",
                              cfg_proxy="http://127.0.0.1:9")
    check("死的手动代理被跳过，实际用的是系统代理",
          calls == ["127.0.0.1:20809"], str(calls))
    check("Route 回报走的是系统代理", "系统代理" in route.desc, route.desc)
    check("返回的确实是响应体", resp.read() == b'{"ok":1}')

    # 探活没开时不应该调用探活函数（probe=False 走的是"直接试"）
    print("\n== 2b. 全通道失败要能说清每条为什么失败 ==")
    net._proxy_alive = lambda proxy, timeout=net.PROXY_PROBE_TIMEOUT: False
    net._tcp_ok = lambda host, port, timeout: False
    try:
        net.urlopen("https://example.com/api")
        check("全失败应抛 NetError", False, "没有抛异常")
    except net.NetError as exc:
        check("全失败抛 NetError", True)
        check("消息点明「所有下载通道都失败」", "所有下载通道都失败" in str(exc), str(exc))
        check("逐条列出失败原因（≥2 条）", len(exc.attempts) >= 2, str(exc.attempts))
        check("失败原因里提到探活超时", any("探活" in a for a in exc.attempts),
              str(exc.attempts))
    except Exception as exc:  # noqa: BLE001
        check("全失败应抛 NetError", False, f"{type(exc).__name__}: {exc}")


def test_localhost(net) -> None:
    print("\n== 3. 本机地址永远直连（不能被系统代理绕出去）==")
    net.win_system_proxy = lambda: "127.0.0.1:20809"
    net.env_proxy = lambda: ""
    net._proxy_alive = lambda proxy, timeout=net.PROXY_PROBE_TIMEOUT: True
    net._tcp_ok = lambda host, port, timeout: True

    calls: list[str] = []
    net._opener = make_opener(b'{"status":"ok"}', calls)
    for url in ("http://127.0.0.1:8789/health", "http://localhost:8789/health"):
        resp, route = net.urlopen(url, cfg_proxy="http://127.0.0.1:9999")
        check(f"{url} 强制直连", calls[-1] == "", str(calls))
        check(f"{url} 的 Route 标明本机直连", "本机" in route.desc, route.desc)


def test_read_and_download(net, tmp: Path) -> None:
    print("\n== 4. read_json / download（原子落盘）==")
    net.urlopen = lambda url, **kw: (FakeResp(b'{"tag_name":"v9.9.9"}'),
                                     net.Route(desc="测试通道"))
    data, route = net.read_json("https://example.com/api")
    check("read_json 解析出 JSON", data.get("tag_name") == "v9.9.9", str(data))
    check("read_json 带回 Route", str(route) == "测试通道", str(route))

    payload = b"A" * 5000
    net.urlopen = lambda url, **kw: (FakeResp(payload), net.Route(desc="测试通道"))
    dest = tmp / "dl" / "pkg.zip"
    path, _ = net.download("https://example.com/pkg.zip", dest)
    check("download 落盘且字节数正确",
          path.is_file() and path.stat().st_size == 5000, str(path))
    check("download 不留 .part 残骸",
          not (dest.parent / (dest.name + ".part")).exists())


def test_paths_migration(tmp: Path) -> None:
    print("\n== 5. 数据目录迁移：先复制 → 再写指针 → 最后删旧 ==")
    from luobobox import paths

    os.environ.pop("LUOBOBOX_DATA_DIR", None)
    # 迁移指针现在写在「程序安装目录旁边」。测试里必须改道到临时目录 ——
    # 否则跑一次用例就会在源码树里留下一个真的 datadir.txt（污染后续源码运行）。
    os.environ["LUOBOBOX_POINTER_DIR"] = str(tmp / "pointer-home")
    factory = tmp / "factory"
    # 老位置（≤v1.0.6 的出厂默认目录）也显式改道 —— 默认它指向**本机真实安装版**
    # 正在用的 %LOCALAPPDATA%\LuoboBox\datadir.txt，本用例会写它、让自愈搬走它、
    # 最后 reset 再删掉它。指到 factory 既安全，又保留了下面「指针不再落在
    # 出厂目录里」这条断言的验证力（否则该断言会退化成恒真）。
    os.environ["LUOBOBOX_LEGACY_POINTER_DIR"] = str(factory)
    # 只替换「出厂默认目录」的解析，不替换 data_dir 本身 ——
    # 别的模块是 `from .paths import data_dir` 按值绑定的，换掉 data_dir 它们看不见。
    paths.default_data_dir = lambda: factory

    d0 = paths.data_dir()
    check("默认落在出厂目录", d0 == factory, str(d0))
    write(d0 / "config.json", "{}")
    write(d0 / "backups" / "gateway-1" / "f.bin", "x" * 100)
    write(d0 / "logs" / "luobobox.log", "hello")

    ok, msg = paths.migrate_data_dir(d0)
    check("目标是当前目录 → 拒绝", not ok and "无需迁移" in msg, msg)
    ok, msg = paths.migrate_data_dir(d0 / "inner")
    check("目标在源目录内部 → 拒绝", not ok, msg)
    blocker = d0 / "afile"
    write(blocker, "x")
    ok, msg = paths.migrate_data_dir(blocker)
    check("目标是一个已存在的文件 → 拒绝", not ok, msg)

    new = tmp / "dataD"
    ok, msg = paths.migrate_data_dir(new)
    check("迁移成功", ok, msg)
    check("配置文件已复制", (new / "config.json").is_file())
    check("子目录整棵复制", (new / "backups" / "gateway-1" / "f.bin").is_file())
    check("日志已复制", (new / "logs" / "luobobox.log").is_file())

    # 回归：指针**绝不能**再落在出厂默认数据目录里 ——
    # 那样用户「腾 C 盘 → 删掉 LuoboBox 文件夹」会把指针一起带走，
    # 迁移被静默撤销（数据还在 D 盘，程序却回 C 盘重建一份空的）。
    ptr = paths.data_dir_pointer_path()
    check("指针写在程序目录旁（删数据目录碰不到它）",
          ptr.is_file() and ptr.parent != factory, str(ptr))
    check("指针内容 = 新位置绝对路径",
          ptr.read_text(encoding="utf-8").strip() == str(new.resolve()),
          ptr.read_text(encoding="utf-8").strip())
    left = sorted(p.name for p in factory.iterdir())
    check("旧数据目录已清空（指针不再落在里面）", left == [], str(left))
    # 必须比 resolve()：%TEMP% 可能是 8.3 短名（C:\Users\ADMINI~1\...），
    # 而指针里存的是 resolve() 之后的长名，字符串直接比会假失败。
    check("data_dir() 现在返回新位置",
          paths.data_dir().resolve() == new.resolve(), str(paths.data_dir()))

    ok, msg = paths.reset_data_dir_pointer()
    check("可以撤销迁移", ok, msg)
    check("撤销后回到出厂目录", paths.data_dir() == factory, str(paths.data_dir()))

    print("\n== 5b. 体积格式化 / 系统盘判断 ==")
    check("human_size(0) == 0 B", paths.human_size(0) == "0 B", paths.human_size(0))
    check("human_size(1536) == 1.5 KB", paths.human_size(1536) == "1.5 KB",
          paths.human_size(1536))
    check("human_size(718MB) 可读", paths.human_size(718 * 1048576).endswith("MB"),
          paths.human_size(718 * 1048576))
    check("is_on_system_drive 不抛异常", isinstance(paths.is_on_system_drive(), bool))
    check("D 盘不会被判成系统盘", not paths.is_on_system_drive("D:\\LuoboBoxData"))


def test_updates_dir(tmp: Path) -> None:
    print("\n== 6. 更新中转目录：默认跟随数据目录，可改到别的盘 ==")
    from luobobox import appupdater, config as cfgmod

    data = tmp / "dataE"
    os.environ["LUOBOBOX_DATA_DIR"] = str(data)
    check("默认 = 数据目录/updates",
          appupdater.updates_dir() == data / "updates", str(appupdater.updates_dir()))

    work = tmp / "workD"
    c = cfgmod.Config.load()
    c.set("updater.work_dir", str(work))
    c.save()
    check("配了 updater.work_dir 就用它",
          appupdater.updates_dir() == work, str(appupdater.updates_dir()))
    check("目标目录会被真的创建", work.is_dir())

    # 配了一个不可用的路径 → 必须静默回落，不能卡住整个更新流程
    blocker = tmp / "blocker"
    write(blocker, "x")
    c.set("updater.work_dir", str(blocker / "sub"))
    c.save()
    check("路径不可用时静默回落",
          appupdater.updates_dir() == data / "updates", str(appupdater.updates_dir()))

    c.set("net.proxy", "http://127.0.0.1:20809")
    c.set("net.probe", False)
    c.save()
    check("proxy_setting() 读到手动代理",
          appupdater.proxy_setting() == "http://127.0.0.1:20809", appupdater.proxy_setting())
    check("probe_setting() 读到开关", appupdater.probe_setting() is False)

    c.set("updater.work_dir", "")
    c.set("net.proxy", "")
    c.set("net.probe", True)
    c.save()


def test_installer_dir(tmp: Path) -> None:
    print("\n== 7. 静默安装必须显式 /DIR（否则 Inno 会装回 C 盘）==")
    from luobobox import appupdater

    app = Path(r"D:\LuoboBox")
    script = appupdater.helper_script(
        app_dir=app,
        src=tmp / "staging" / "LuoboBox",
        mode="installer",
        setup=tmp / "LuoboBox-Setup-1.0.4.exe",
        log=tmp / "apply.log",
    )
    check("安装命令带 /DIR=\"%APP%\"", '/DIR="%APP%"' in script)
    check("APP 变量就是当前安装目录", 'set "APP=D:\\LuoboBox"' in script)
    check("日志里记下了安装目标", "静默安装 %SETUP% 到 %APP%" in script)
    check("不传 /TASKS（首次静默安装不该强行打开机自启）",
          "/TASKS=" not in script)
    check("仍然带 /VERYSILENT /NORESTART",
          "/VERYSILENT" in script and "/NORESTART" in script)

    portable = appupdater.helper_script(
        app_dir=app,
        src=tmp / "staging2" / "LuoboBox",
        mode="portable",
        setup=None,
        log=tmp / "apply2.log",
    )
    check("便携版仍走 robocopy 原地覆盖", "robocopy" in portable)
    check("便携版 MODE 正确", 'set "MODE=portable"' in portable)
    # 脚本里两段代码都在（一个 .cmd 要覆盖两种形态），靠 MODE 分流；
    # 便携版必须在这一行就跳走，绝不会落到安装包分支。
    check("便携版在分流处就跳开安装分支",
          'if /I "%MODE%"=="installer" goto install' in portable
          and 'set "MODE=portable"' in portable)


def test_backup_slimming(tmp: Path) -> None:
    print("\n== 8. 网关备份要瘦身（node_modules 不进备份）==")
    from luobobox import paths, updater

    bdir = paths.backup_dir()
    for i in range(4):
        (bdir / f"gateway-2026092{i}-120000").mkdir(parents=True, exist_ok=True)
    removed = updater.prune_backups(2)
    check("备份份数收敛：删掉 2 份", removed == 2, str(removed))
    kept = sorted(p.name for p in bdir.iterdir() if p.name.startswith("gateway-"))
    check("留下的是最新两份",
          kept == ["gateway-20260922-120000", "gateway-20260923-120000"], str(kept))

    gw = tmp / "gw"
    write(gw / "converter.py", "OLD\n")
    write(gw / "auth" / "account.info", "KEEP\n")
    write(gw / "web" / "node_modules" / "pkg" / "index.js", "DEP\n" * 200)
    write(gw / "web" / "src" / "main.ts", "OLD-SRC\n")
    write(gw / ".git" / "HEAD", "ref: refs/heads/main\n")
    archive = make_tarball(tmp / "r.tar.gz", "codebuddy2api-main",
                           {"converter.py": "NEW\n"})
    res = updater.apply_release(gw, archive)
    check("升级本身成功", res.ok, res.message)
    bak = Path(res.backup) if res.backup else None
    check("备份已生成", bak is not None and bak.is_dir(), str(bak))
    # 上面故意留了名字比"现在"更靠后的 decoy（gateway-20260922/23）。
    # 如果修剪只按名字排序，刚做出来的这份会被排到末尾、当场删掉 ——
    # 那样这次升级就完全没有退路了。protect 必须挡住它。
    check("★ 刚做出来的备份不会被自己修剪掉（protect 生效）",
          bak is not None and bak.is_dir(), str(bak))
    if bak:
        check("★ 备份里没有 node_modules",
              not (bak / "web" / "node_modules").exists())
        check("★ 备份里没有 .git", not (bak / ".git").exists())
        check("备份里有 converter.py（真能回滚）",
              (bak / "converter.py").is_file())
        check("备份里有 auth/（凭据不能丢）",
              (bak / "auth" / "account.info").is_file())
        check("BACKUP_IGNORE 覆盖 node_modules",
              "node_modules" in updater.BACKUP_IGNORE)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="luobobox-net-selftest-"))
    saved_env = {k: os.environ.get(k)
                 for k in ("LUOBOBOX_DATA_DIR", "LUOBOBOX_POINTER_DIR",
                           "LUOBOBOX_LEGACY_POINTER_DIR")}

    from luobobox import net

    saved_net = {}
    for name in ("win_system_proxy", "env_proxy", "_proxy_alive",
                 "_tcp_ok", "_opener", "urlopen"):
        saved_net[name] = getattr(net, name)

    try:
        test_proxy_candidates(net)
        test_fallback(net)
        test_localhost(net)
        test_read_and_download(net, tmp)
        test_paths_migration(tmp)
        test_updates_dir(tmp)
        test_installer_dir(tmp)
        test_backup_slimming(tmp)
    finally:
        for name, value in saved_net.items():
            setattr(net, name, value)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  ✗ {item}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
