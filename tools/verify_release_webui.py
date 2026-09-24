#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_release_webui.py - LuoboBox release 发版复用工具

下载指定 tag 的 GitHub Release 便携包，并校验其内置 webui 是否含多份 React
（多份 React = /dashboard/ 白屏根因）。

判据：跨 webui 目录下所有 js 统计 react 副本标记：
  - `__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED`（minify 安全的字符串属性名，
    每份 react 恰好设置一次）；辅助 `ReactSharedInternals`（dev 模式保留，生产被重命名）。
  - 两个标记任一 >= 2 -> 混入多份 react 坏包（全新安装白屏），禁止发布
  - 否则（react 外链=0，或内联单份=1）-> 干净，可发布

用法：
  # 下载 + 校验（默认仓库 lylguang/LuoboBox，资产名 LuoboBox-<tag>-portable.zip）
  python verify_release_webui.py v1.1.8

  # 只校验本地已下载的 zip
  python verify_release_webui.py --zip ./LuoboBox-1.1.8-portable.zip

  # 自定义仓库 / 下载目录 / 资产前缀
  python verify_release_webui.py v1.1.8 --repo owner/repo --out ./dl --prefix MyApp

下载实现说明（Windows 本机实测坑，必须照做）：
  - 直连 urllib 约 16MB 处被隧道截断；代理挂死会卡在 27MB 且永不超时。
  - 因此一律用 curl 断点续传 + 全错误重试 + 限速自杀：
      curl -L --retry 20 --retry-all-errors --continue-at - \
           --speed-limit 500 --speed-time 20 -o <out> <url>
  - 若环境无 curl，回落 urllib 并显式超时（仅兜底，不推荐大文件）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from urllib.parse import quote

# LuoboBox 默认仓库与资产命名
DEFAULT_REPO = "lylguang/LuoboBox"
DEFAULT_PREFIX = "LuoboBox"

# 白屏根因判据（面向“bundle 内混入多份 React 副本”这种坏包）：
#   __SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED 是 react 主对象的字符串属性名，
#   minifier 不会改字符串字面量，每份 react 副本恰好设置一次该属性 → 计数 == react 副本数。
#   ReactSharedInternals 是 react 内部变量名（生产 minified 会被重命名 → 0），
#   仅在 development / 未充分压缩构建里保留，作为辅助判据抓 dev 模式坏包。
# 干净包两种可能都判 PASS：(a) react 外链 → 两个标记都为 0；(b) 内联单份 react → 各为 1。
# 坏包 = 内联 >=2 份 react → 任一标记 >=2。
SECRET_MARKER = b"__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED"
RSI_MARKER = b"ReactSharedInternals"


def _log(msg: str) -> None:
    print(msg, flush=True)


def build_asset_url(repo: str, tag: str, prefix: str):
    tag = tag if tag.startswith("v") else "v" + tag
    name = f"{prefix}-{tag}-portable.zip"
    url = f"https://github.com/{repo}/releases/download/{tag}/{quote(name)}"
    return url, name


def fetch_remote_size(url: str):
    """用 curl -sIL 拿远端 Content-Length（失败返回 None，不致命）。"""
    curl = shutil.which("curl")
    if not curl:
        return None
    try:
        out = subprocess.run(
            [curl, "-sIL", url],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return None
    for line in reversed(out.splitlines()):
        if line.lower().startswith("content-length:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def download_with_curl(url: str, out_path: str) -> int:
    """返回退出码；断点续传，失败重试。"""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("未找到 curl，无法下载（Windows 10+ 自带 curl.exe）")
    cmd = [
        curl, "-L", "--retry", "20", "--retry-all-errors",
        "--continue-at", "-",
        "--speed-limit", "500", "--speed-time", "20",
        "-o", out_path, url,
    ]
    _log("[download] curl " + " ".join(cmd))
    return subprocess.run(cmd).returncode


def download_with_urllib(url: str, out_path: str) -> None:
    import urllib.request
    _log("[download] 回落 urllib（不推荐大文件，可能截断/挂死）")
    req = urllib.request.Request(url, headers={"User-Agent": "verify-release/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(out_path, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def verify_webui_zip(zip_path: str) -> dict:
    """解包便携包，统计 webui 目录下所有 js 的 react 副本标记数量。

    返回 per_file 明细 + secret_count / rsi_count 总计；clean = 两个标记均未 >= 2。
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(zip_path)
    secret = 0
    rsi = 0
    per_file = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if ".js" in name and "webui" in name:
                data = zf.read(name)
                cs = data.count(SECRET_MARKER)
                cr = data.count(RSI_MARKER)
                secret += cs
                rsi += cr
                per_file.append((name, len(data), cs, cr))
    clean = not (secret >= 2 or rsi >= 2)
    return {
        "zip": zip_path,
        "per_file": per_file,
        "secret_count": secret,
        "rsi_count": rsi,
        "clean": clean,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="下载并校验 LuoboBox Release 便携包内置 webui 是否含多份 React（白屏根因）")
    ap.add_argument("tag", nargs="?", help="Release tag，如 v1.1.8（省略时须给 --zip）")
    ap.add_argument("--zip", help="直接校验本地 zip（跳过下载）")
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"仓库 owner/name（默认 {DEFAULT_REPO}）")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"资产名前缀（默认 {DEFAULT_PREFIX}）")
    ap.add_argument("--out", default=".", help="下载目录（默认当前目录）")
    ap.add_argument("--no-size-check", action="store_true", help="跳过远程大小比对")
    args = ap.parse_args(argv)

    if args.zip:
        zip_path = args.zip
        _log(f"[verify] 直接校验本地 zip: {zip_path}")
    else:
        if not args.tag:
            ap.error("须提供 tag 或 --zip")
        url, name = build_asset_url(args.repo, args.tag, args.prefix)
        os.makedirs(args.out, exist_ok=True)
        zip_path = os.path.join(args.out, name)
        _log(f"[verify] tag={args.tag} 资产={name}")
        _log(f"[verify] url={url}")

        remote = None
        if not args.no_size_check:
            remote = fetch_remote_size(url)
            if remote:
                _log(f"[verify] 远端 Content-Length={remote} 字节")

        rc = download_with_curl(url, zip_path)
        if rc != 0:
            _log(f"[download] curl 失败 rc={rc}，尝试 urllib 兜底")
            try:
                download_with_urllib(url, zip_path)
            except Exception as e:
                _log(f"[download] 兜底也失败: {e}")
                return 2

        local = os.path.getsize(zip_path)
        if remote and local != remote:
            _log(f"[verify] FAIL 大小不一致：本地 {local} != 远端 {remote}（下载被截断？）")
            return 3
        _log(f"[verify] 本地大小={local} 字节" + (f" = 远端 {remote}" if remote else ""))

    try:
        res = verify_webui_zip(zip_path)
    except Exception as e:
        _log(f"[verify] 解包/校验失败: {e}")
        return 4

    _log("")
    _log("=== 校验结果 ===")
    _log(f"zip                              : {res['zip']}")
    for name, size, cs, cr in res["per_file"]:
        _log(f"  {os.path.basename(name):36s} {size:9d}B  SECRET={cs} RSI={cr}")
    _log(f"__SECRET_INTERNALS 出现次数       : {res['secret_count']}")
    _log(f"ReactSharedInternals 出现次数     : {res['rsi_count']}")
    if res["clean"]:
        _log("结论                             : PASS - 未见 >=2 份 react 混进 bundle（白屏根因已根除），可发布")
        return 0
    else:
        _log("结论                             : FAIL - bundle 内混入 >=2 份 react 副本，全新安装会白屏，禁止发布")
        return 1


if __name__ == "__main__":
    sys.exit(main())
