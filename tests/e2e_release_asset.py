# -*- coding: utf-8 -*-
"""端到端校验：GitHub Release 上的**真实**便携包能不能覆盖一份真实安装目录。

和 appupdater_selftest.py 的分工：
    appupdater_selftest.py  离线、合成 zip，验证**逻辑**（形态判定/资产挑选/exe-first 闸门）
    本脚本                   联网、真下 Release 资产，验证**发布件本身**可用

为什么要有它：Release 是 CI 出的，本地源码跑得通不代表打出来的包能覆盖上去
（曾踩过：zip 里多一层目录 / 资产名带中文被吞 / exe 版本号没跟着改）。
每次发版后跑一次，能挡住「Release 看着有、装上没反应」这类静默故障。

用法（需要网络，会下载 ~50MB，可选）：
    python tests/e2e_release_asset.py            # 校验 latest release
    python tests/e2e_release_asset.py v1.0.1     # 校验指定 tag

会把 dist/LuoboBox（上一次的构建）复制成"老安装"，用 Release 资产覆盖它，
最后校验：exe 换成了新版、包内每个文件都落到位且字节一致。
覆盖用的是真实助手脚本，但**不重启 GUI**（relaunch=False）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 🔴 必须在 import luobobox **之前**把数据目录改道到临时目录。
#
# 原因：`data_dir()` 会读真实的迁移指针，而读老位置指针时会**顺手自愈**
# （把指针搬到「程序目录旁」）。源码模式下「程序目录旁」= 仓库根，
# 于是本机安装版（app_root = D:\LuoboBox）正在用的那个指针会被搬走并删掉 ——
# 它下次启动两个位置都找不到指针，就回落到 C 盘默认目录，看起来像
# 「配置和备份全没了」。测试一律用临时目录，别碰真实指针。
import tempfile  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="luobobox-e2e-release-"))
os.environ["LUOBOBOX_DATA_DIR"] = str(_TMP)
os.environ["LUOBOBOX_POINTER_DIR"] = str(_TMP / "appdir")
# 老位置也必须改道：pointer_legacy_path() 默认指向本机真实安装版的指针，
# 而 data_dir_override() 的自愈会把它搬走并删掉。
os.environ["LUOBOBOX_LEGACY_POINTER_DIR"] = str(_TMP / "legacy")

from luobobox import appupdater as A  # noqa: E402
from luobobox.updater import _parse_version  # noqa: E402

REPO = "lylguang/LuoboBox"
OLD_INSTALL = ROOT / "dist" / "LuoboBox"
WORKDIR = ROOT / "dist" / "_e2e"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, (" —— " + detail) if detail else ""))
    if not ok:
        FAILURES.append(label)


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_md5(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            fp = Path(dp) / f
            out[str(fp.relative_to(root))] = md5(fp)
    return out


def pe_product_version(path: Path) -> str | None:
    """读 PE 版本资源里的 ProductVersion，用来证明 exe 真的换代了。"""
    import ctypes
    from ctypes import wintypes as wt

    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    out = ctypes.c_void_p()
    ln = wt.UINT()
    if not version.VerQueryValueW(buf, r"\VarFileInfo\Translation",
                                  ctypes.byref(out), ctypes.byref(ln)):
        return None
    lang, cp = ctypes.cast(out, ctypes.POINTER(wt.WORD * 2)).contents
    sub = r"\StringFileInfo\%04x%04x\ProductVersion" % (lang, cp)
    if version.VerQueryValueW(buf, sub, ctypes.byref(out), ctypes.byref(ln)):
        return ctypes.wstring_at(out, ln.value).rstrip("\x00")
    return None


def main() -> int:
    want_tag = sys.argv[1] if len(sys.argv) > 1 else ""
    print("=" * 72)
    print("Release 资产端到端校验：", want_tag or "latest", "  仓库:", REPO)
    print("=" * 72)

    if not (OLD_INSTALL / "LuoboBox.exe").is_file():
        print("跳过：找不到 %s（先跑一次 packaging/build.py 生成「老版本」）" % OLD_INSTALL)
        return 0

    # ---- 1. 查 Release
    info = A.check_app(REPO)
    if info.error:
        print("FAIL 查询 Release 失败：", info.error)
        return 1
    if want_tag and info.tag != want_tag:
        print("注意：latest 是 %s，你要的是 %s" % (info.tag, want_tag))
    tag = info.tag
    print("\n[1] Release %s  资产：%s" % (tag, list(info.assets.keys())))

    name, url = A.pick_asset(info, "portable")
    check("能挑出便携包资产", bool(name), str(name))
    if not name:
        return 1
    check("资产名是纯 ASCII", name.isascii(), name)

    # ---- 2. 老安装副本
    print("\n[2] 用 %s 造一份「老安装」" % OLD_INSTALL)
    app = WORKDIR / "oldinstall"
    if app.exists():
        shutil.rmtree(app)
    app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(OLD_INSTALL, app)
    before = tree_md5(app)
    old_exe_md5 = md5(app / "LuoboBox.exe")
    old_ver = pe_product_version(app / "LuoboBox.exe")
    print("      文件 %d 个，exe ProductVersion=%s" % (len(before), old_ver))
    check("老安装判定为 portable", A.install_mode(app) == "portable")

    # ---- 3. 真下载
    print("\n[3] 下载 Release 资产")
    work = A.updates_dir()
    cached = work / name
    if cached.is_file():
        archive = cached
        print("      复用已下载缓存")
    else:
        t0 = time.time()
        archive = A.download(url, work, name, timeout=900)
        print("      %.1fs" % (time.time() - t0))
    check("下载文件存在且非空", archive.is_file() and archive.stat().st_size > 0,
          "%d B" % archive.stat().st_size)

    # ---- 4. 解压 + 真覆盖
    print("\n[4] 解压并以真实助手脚本覆盖（不重启 GUI）")
    staging = work / "e2e-staging"
    if staging.exists():
        shutil.rmtree(staging)
    src = A.extract_portable(archive, staging)
    new_exe_md5 = md5(src / "LuoboBox.exe")
    new_ver = pe_product_version(src / "LuoboBox.exe")
    print("      包内 exe ProductVersion=%s" % new_ver)
    check("包内 exe 与老安装不是同一个", new_exe_md5 != old_exe_md5)
    check("包内 exe 版本 != 老版本", bool(new_ver) and new_ver != old_ver,
          "%s -> %s" % (old_ver, new_ver))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = work / ("e2e-apply-%s.log" % stamp)
    script = work / ("e2e-apply-%s.cmd" % stamp)
    A._write_batch(script, A.helper_script(
        app, src, "portable", None, log,
        relaunch=False, self_delete=False,
    ))
    proc = subprocess.Popen(["cmd.exe", "/c", str(script)], cwd=str(work),
                            creationflags=A._DETACHED)
    try:
        rc = proc.wait(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = "TIMEOUT"
    check("助手脚本正常结束", rc == 0, "exit=%s" % rc)

    # ---- 5. 校验结果
    print("\n[5] 校验覆盖结果")
    after = tree_md5(app)
    cur_ver = pe_product_version(app / "LuoboBox.exe")
    check("exe 已换成包内版本", md5(app / "LuoboBox.exe") == new_exe_md5)
    check("exe ProductVersion 已更新", cur_ver == new_ver, "%s" % cur_ver)

    src_tree = tree_md5(src)
    missing = [k for k in src_tree if k not in after]
    diff = [k for k in src_tree if k in after and after[k] != src_tree[k]]
    check("包内文件无一缺失", not missing, str(missing[:5]))
    check("包内文件字节一致", not diff, str(diff[:5]))

    print("\n" + "=" * 72)
    if FAILURES:
        print("结果：%d 项失败" % len(FAILURES))
        for f in FAILURES:
            print("  -", f)
        return 1
    print("结果：全部通过 —— Release %s 的便携包能正确覆盖并升级老安装" % tag)
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
