"""离线自测：萝卜盒内置 WebUI 的部署与「绝不覆盖用户劳动成果」策略。

背景：上游 Release 从不带 web/dist（web/.gitignore 把它忽略了），升级网关就会
把它冲掉 → /dashboard/ 503「WebUI 尚未构建」。updater.PRESERVE_SUBPATHS 只能保住
「升级前就已经构建好的」dist；全新机器 / 上游改了 web/src 都无能为力。
所以萝卜盒自带一份预构建 dist，按需补齐。

本测试全离线，用临时目录断言：
  · dist 缺失 → 用内置版本补齐，并写下版本戳
  · 已是最新内置版本 → 不动（幂等）
  · 没有版本戳（用户自行构建）→ 绝不覆盖
  · 版本戳变了（内置更新了）→ 刷新
  · force=True → 连用户自己构建的也覆盖
  · 没内置 / 网关目录不存在 → 安静返回，不抛异常
  · 内容指纹忽略版本戳与来源记录，且对内容变化敏感
  · 端到端：updater.apply_release 在升级后会把内置 dist 补上

跑法：
  python tests/webui_selftest.py
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


def make_bundle(root: Path, js: str = "BUNDLED-JS") -> Path:
    """造一份「内置 WebUI」资源目录。"""
    d = root / "webui"
    write(d / "index.html", '<!doctype html><script src="/dashboard/assets/index-abc.js"></script>\n')
    write(d / "assets" / "index-abc.js", js + "\n")
    write(d / "assets" / "index-abc.css", "BUNDLED-CSS\n")
    return d


def make_tarball(dest: Path, wrapper: str, files: dict[str, str]) -> Path:
    with tarfile.open(dest, "w:gz") as tf:
        for relative, text in files.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name=f"{wrapper}/{relative}")
            info.size = len(raw)
            info.mtime = 1700000000
            tf.addfile(info, io.BytesIO(raw))
    return dest


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="luobobox-webui-selftest-"))
    os.environ["LUOBOBOX_DATA_DIR"] = str(tmp / "data")

    from luobobox import webui

    res = tmp / "resource"
    bundle = make_bundle(res)

    # 把 resource_dir 指到临时目录：源码态它本来是 <仓库>/assets
    webui.resource_dir = lambda: res  # type: ignore[assignment]

    print("== 场景 1：网关没有 web/dist → 用内置版本补齐 ==")
    gw1 = tmp / "gw1"
    (gw1 / "web" / "src").mkdir(parents=True)
    write(gw1 / "converter.py", "x\n")
    r1 = webui.ensure(gw1)
    check("changed = True", r1.changed, r1.message)
    check("index.html 已就位", (gw1 / "web" / "dist" / "index.html").is_file())
    check("assets 已就位", (gw1 / "web" / "dist" / "assets" / "index-abc.js").is_file())
    check("内容与内置一致",
          (gw1 / "web" / "dist" / "assets" / "index-abc.js").read_text(encoding="utf-8") == "BUNDLED-JS\n")
    stamp = webui.read_stamp(gw1 / "web" / "dist")
    check("写下了版本戳", isinstance(stamp, dict) and bool(stamp.get("stamp")), str(stamp))
    check("版本戳内容 == 内置指纹", (stamp or {}).get("stamp") == webui.content_stamp(bundle))
    check("installed = True", webui.installed(gw1))

    print("\n== 场景 2：再跑一次（幂等）==")
    r2 = webui.ensure(gw1)
    check("changed = False", not r2.changed, r2.message)
    check("说明是「已是最新内置版本」", "最新" in r2.message, r2.message)

    print("\n== 场景 3：用户自行构建的 dist（没有我们的戳）→ 绝不覆盖 ==")
    gw3 = tmp / "gw3"
    (gw3 / "web").mkdir(parents=True)
    write(gw3 / "converter.py", "x\n")
    write(gw3 / "web" / "dist" / "index.html", "USER-BUILT-INDEX\n")
    write(gw3 / "web" / "dist" / "assets" / "index-zzz.js", "USER-BUILT-JS\n")
    r3 = webui.ensure(gw3)
    check("changed = False", not r3.changed, r3.message)
    check("用户产物未被改动",
          (gw3 / "web" / "dist" / "index.html").read_text(encoding="utf-8") == "USER-BUILT-INDEX\n")
    check("没被塞进内置资源",
          not (gw3 / "web" / "dist" / "assets" / "index-abc.js").exists())
    check("没有写版本戳", webui.read_stamp(gw3 / "web" / "dist") is None)

    print("\n== 场景 4：版本戳过时（内置更新了）→ 刷新 ==")
    gw4 = tmp / "gw4"
    (gw4 / "web" / "dist").mkdir(parents=True)
    write(gw4 / "converter.py", "x\n")
    write(gw4 / "web" / "dist" / "index.html", "OLD-BUNDLED-INDEX\n")
    write(gw4 / "web" / "dist" / ".luobobox-webui.json",
          '{"stamp": "deadbeefdeadbeef", "app_version": "0.0.1"}')
    r4 = webui.ensure(gw4)
    check("changed = True", r4.changed, r4.message)
    check("已被刷新成内置版本",
          (gw4 / "web" / "dist" / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>"))
    check("旧版本戳已更新",
          (webui.read_stamp(gw4 / "web" / "dist") or {}).get("stamp") == webui.content_stamp(bundle))
    check("说明里带出了旧版本号", "0.0.1" in r4.message, r4.message)

    print("\n== 场景 5：force=True → 连用户自建的也覆盖 ==")
    r5 = webui.ensure(gw3, force=True)
    check("changed = True", r5.changed, r5.message)
    check("用户产物被内置版替换",
          (gw3 / "web" / "dist" / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>"))
    check("旧的用户资源已清掉（不留垃圾）",
          not (gw3 / "web" / "dist" / "assets" / "index-zzz.js").exists())

    print("\n== 场景 6：没内置 / 网关目录不存在 → 安静返回 ==")
    webui.resource_dir = lambda: tmp / "nothing-here"  # type: ignore[assignment]
    r6 = webui.ensure(tmp / "gw1")
    check("changed = False", not r6.changed)
    check("说明提到未内置", "未内置" in r6.message, r6.message)
    webui.resource_dir = lambda: res  # type: ignore[assignment]
    r7 = webui.ensure(tmp / "does-not-exist")
    check("网关目录不存在时不抛异常", not r7.changed, r7.message)
    r8 = webui.ensure("")
    check("空路径不抛异常", not r8.changed, r8.message)

    print("\n== 场景 7：内容指纹 ==")
    a = tmp / "stampA"
    make_bundle(a, js="AAA")
    s_a = webui.content_stamp(a / "webui")
    b = tmp / "stampB"
    make_bundle(b, js="BBB")
    s_b = webui.content_stamp(b / "webui")
    check("内容不同 → 指纹不同", s_a != s_b)
    check("同一份内容 → 指纹稳定", webui.content_stamp(a / "webui") == s_a)
    write(a / "webui" / ".luobobox-webui.json", '{"stamp": "whatever"}')
    check("版本戳不参与指纹", webui.content_stamp(a / "webui") == s_a)
    write(a / "webui" / "_source.json", '{"source": "somewhere-else"}')
    check("来源记录不参与指纹", webui.content_stamp(a / "webui") == s_a)

    print("\n== 场景 8：端到端 —— apply_release 后内置 dist 被补上 ==")
    webui.resource_dir = lambda: res  # type: ignore[assignment]
    from luobobox import updater

    gw8 = tmp / "gw8"
    write(gw8 / "converter.py", "OLD\n")
    write(gw8 / "VERSION", "1.0.0\n")
    write(gw8 / "web" / "src" / "main.ts", "OLD-SRC\n")
    write(gw8 / "auth" / "account.info", '{"secret":"KEEP"}\n')
    archive = make_tarball(
        tmp / "gw8" / "release.tar.gz",
        "codebuddy2api-main",
        {"converter.py": "NEW\n", "VERSION": "2.0.0\n", "web/src/main.ts": "NEW-SRC\n"},
    )
    res8 = updater.apply_release(gw8, archive)
    check("apply_release ok", res8.ok, res8.message)
    check("升级后 web/dist/index.html 存在", (gw8 / "web" / "dist" / "index.html").is_file())
    check("升级后 assets 存在", (gw8 / "web" / "dist" / "assets" / "index-abc.js").is_file())
    check("ApplyResult.webui 有说明", bool(res8.webui), res8.webui)
    check("说明提到补齐", "补齐" in res8.webui, res8.webui)
    check("auth/ 未被动", (gw8 / "auth" / "account.info").read_text(encoding="utf-8") == '{"secret":"KEEP"}\n')

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  ✗ {item}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
