"""离线自测：网关升级必须保住 web/dist 与 web/node_modules。

背景（就是「后台管理打不开」的根因）：
  上游仓库的 web/.gitignore 忽略了 dist/，所以 release 包里 web/ 只有 src。
  而 apply_release 的覆盖动作是「整目录 rmtree + copytree」—— 一次升级就把
  本地构建好的 web/dist 连根拔掉，/dashboard/ 立刻 503「WebUI 尚未构建」。
  修法是 PRESERVE_SUBPATHS：覆盖前把构建产物搬走，覆盖后放回。

本测试全离线，用合成 tar.gz 断言：
  · 上游文件确实被替换
  · web/dist、web/dist/assets、web/node_modules 原样保留
  · auth/、.env 不在覆盖范围内
  · 暂存目录没有残留

跑法：
  python tests/updater_preserve_selftest.py
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


def make_tarball(dest: Path, wrapper: str, files: dict[str, str]) -> Path:
    """造一份像 GitHub tarball 那样的归档：内容都套在一个顶层目录里。"""
    with tarfile.open(dest, "w:gz") as tf:
        for relative, text in files.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name=f"{wrapper}/{relative}")
            info.size = len(raw)
            info.mtime = 1700000000
            tf.addfile(info, io.BytesIO(raw))
    return dest


def build_gateway(root: Path) -> Path:
    gw = root / "codebuddy2api"
    write(gw / "converter.py", "OLD-CONVERTER\n")
    write(gw / "VERSION", "1.0.0\n")
    write(gw / "app" / "module.py", "OLD-MODULE\n")
    write(gw / "auth" / "account.info", '{"secret":"KEEP-ME"}\n')
    write(gw / ".env", "CODEBUDDY2API_KEY=KEEP-ME\n")
    # 上游有、但不会被替换成同名文件的本地构建产物
    write(gw / "web" / "src" / "main.ts", "OLD-SRC\n")
    write(gw / "web" / "dist" / "index.html", "BUILT-INDEX\n")
    write(gw / "web" / "dist" / "assets" / "index-abc.js", "BUILT-JS\n")
    write(gw / "web" / "node_modules" / "pkg" / "index.js", "DEP\n")
    return gw


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="luobobox-upd-selftest-"))
    data = tmp / "data"
    os.environ["LUOBOBOX_DATA_DIR"] = str(data)

    from luobobox import updater

    print("== 场景 1：正常升级（上游包不含 dist/node_modules）==")
    workspace = tmp / "s1"
    gw = build_gateway(workspace)
    archive = make_tarball(
        workspace / "release.tar.gz",
        "codebuddy2api-main",
        {
            "converter.py": "NEW-CONVERTER\n",
            "VERSION": "2.0.0\n",
            "app/module.py": "NEW-MODULE\n",
            "app/extra.py": "NEW-EXTRA\n",
            "web/src/main.ts": "NEW-SRC\n",
        },
    )

    result = updater.apply_release(gw, archive)
    check("apply_release 返回 ok", result.ok, result.message)
    check("已整目录备份", result.backup is not None and Path(result.backup).is_dir())

    check("converter.py 被替换", (gw / "converter.py").read_text(encoding="utf-8") == "NEW-CONVERTER\n")
    check("VERSION 被替换", (gw / "VERSION").read_text(encoding="utf-8") == "2.0.0\n")
    check("app/module.py 被替换", (gw / "app" / "module.py").read_text(encoding="utf-8") == "NEW-MODULE\n")
    check("app/extra.py 新增", (gw / "app" / "extra.py").read_text(encoding="utf-8") == "NEW-EXTRA\n")
    check("web/src/main.ts 被替换", (gw / "web" / "src" / "main.ts").read_text(encoding="utf-8") == "NEW-SRC\n")

    dist_index = gw / "web" / "dist" / "index.html"
    check("★ web/dist/index.html 保留", dist_index.is_file()
          and dist_index.read_text(encoding="utf-8") == "BUILT-INDEX\n")
    dist_asset = gw / "web" / "dist" / "assets" / "index-abc.js"
    check("★ web/dist/assets/*.js 保留", dist_asset.is_file()
          and dist_asset.read_text(encoding="utf-8") == "BUILT-JS\n")
    nm = gw / "web" / "node_modules" / "pkg" / "index.js"
    check("★ web/node_modules 保留", nm.is_file() and nm.read_text(encoding="utf-8") == "DEP\n")

    check("auth/ 未被动过", (gw / "auth" / "account.info").read_text(encoding="utf-8") == '{"secret":"KEEP-ME"}\n')
    check(".env 未被动过", (gw / ".env").read_text(encoding="utf-8") == "CODEBUDDY2API_KEY=KEEP-ME\n")
    leftovers = [p.name for p in gw.parent.iterdir() if p.name.startswith(".luobobox-keep-")]
    check("暂存目录无残留", not leftovers, str(leftovers))

    print("\n== 场景 2：本地没有 dist / node_modules（首次升级）==")
    workspace2 = tmp / "s2"
    gw2 = build_gateway(workspace2)
    shutil.rmtree(gw2 / "web" / "dist")
    shutil.rmtree(gw2 / "web" / "node_modules")
    archive2 = make_tarball(
        workspace2 / "release.tar.gz",
        "codebuddy2api-main",
        {"converter.py": "NEW2\n", "web/src/main.ts": "NEW-SRC2\n"},
    )
    result2 = updater.apply_release(gw2, archive2)
    check("无构建产物时也能升级", result2.ok, result2.message)
    check("converter.py 已替换", (gw2 / "converter.py").read_text(encoding="utf-8") == "NEW2\n")
    leftovers2 = [p.name for p in gw2.parent.iterdir() if p.name.startswith(".luobobox-keep-")]
    check("暂存目录无残留（场景 2）", not leftovers2, str(leftovers2))

    print("\n== 场景 3：上游包意外带了 dist（我们的构建产物更权威）==")
    workspace3 = tmp / "s3"
    gw3 = build_gateway(workspace3)
    archive3 = make_tarball(
        workspace3 / "release.tar.gz",
        "codebuddy2api-main",
        {"converter.py": "NEW3\n", "web/dist/index.html": "UPSTREAM-DIST\n"},
    )
    result3 = updater.apply_release(gw3, archive3)
    check("升级成功", result3.ok, result3.message)
    check("★ 本地 dist 覆盖上游 dist",
          (gw3 / "web" / "dist" / "index.html").read_text(encoding="utf-8") == "BUILT-INDEX\n")
    check("上游新增的 dist 文件不影响其余保留",
          (gw3 / "web" / "node_modules" / "pkg" / "index.js").is_file())

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  ✗ {item}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
