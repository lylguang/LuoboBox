"""把网关构建好的 web/dist 同步成萝卜盒的内置 WebUI（assets/webui/）。

为什么内置的 dist 要**入库**（而不是 CI 现构建）：
  · dist 是从 **codebuddy2api** 的 web/ 构建出来的，而那个仓库不在本仓库里 ——
    CI 拿不到源码，没法构建；
  · 目标机器也不一定有 Node/pnpm 工具链。

所以流程是：本地（或任何有工具链的机器）构建一次网关 WebUI → 用本脚本同步进来
→ 提交 → 之后所有萝卜盒发行版都自带这份 dist。

用法：
    # 自动找网关目录（仓库同级 / 上级）
    python packaging/sync_webui.py

    # 显式指定
    python packaging/sync_webui.py --gateway-dir F:\\path\\to\\codebuddy2api
    python packaging/sync_webui.py --dist F:\\path\\to\\web\\dist

    # 只检查、不写入
    python packaging/sync_webui.py --check

构建网关 WebUI 的命令（在网关的 web/ 目录下）：
    node_modules\\.bin\\vp.CMD build
注意别用 `pnpm build` —— package.json 里 packageManager 钉了 pnpm@10.32.1，
本机 pnpm 版本更高时会被 corepack 的版本闸门拦下。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "assets" / "webui"
GATEWAY_DIR_NAME = "codebuddy2api"


def log(msg: str) -> None:
    print(f"[webui] {msg}", flush=True)


def find_gateway_dir(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if (p / "converter.py").is_file() else None
    seen: list[Path] = []
    for base in (ROOT, ROOT.parent, Path.cwd()):
        cand = base / GATEWAY_DIR_NAME
        seen.append(cand)
        if (cand / "converter.py").is_file():
            return cand.resolve()
    log("没找到网关目录，试过：")
    for c in seen:
        log(f"  {c}")
    return None


def gateway_version(gw: Path) -> str:
    p = gw / "VERSION"
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace").strip()
    return "?"


def sanity(dist: Path) -> list[str]:
    """检查 dist 是否像一份完整产物。返回问题列表。"""
    problems: list[str] = []
    index = dist / "index.html"
    if not index.is_file():
        problems.append("缺少 index.html")
        return problems
    text = index.read_text(encoding="utf-8", errors="replace")
    # vite 产物形如 <script src="/dashboard/assets/index-xxx.js">
    if "assets/" not in text:
        problems.append("index.html 里没有 assets/ 引用，可能不是 vite 产物")
    for f in sorted((dist / "assets").glob("*")) if (dist / "assets").is_dir() else []:
        if f.suffix in (".js", ".css") and f.stat().st_size < 512:
            problems.append(f"资源疑似不完整：{f.name} 只有 {f.stat().st_size}B")
    if not (dist / "assets").is_dir():
        problems.append("缺少 assets/ 目录")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway-dir", help="codebuddy2api 源码目录")
    ap.add_argument("--dist", help="直接指定构建好的 web/dist 目录")
    ap.add_argument("--check", action="store_true", help="只校验当前内置 dist，不写入")
    ap.add_argument("--force", action="store_true", help="目标已存在时也覆盖")
    args = ap.parse_args()

    if args.check:
        if not (TARGET / "index.html").is_file():
            log(f"内置 WebUI 不存在：{TARGET}")
            return 1
        problems = sanity(TARGET)
        n = sum(len(f) for _, _, f in __import__("os").walk(TARGET))
        log(f"内置 WebUI 就位：{TARGET}")
        log(f"  {n} 个文件")
        if problems:
            for p in problems:
                log(f"  ✗ {p}")
            return 1
        log("  校验通过")
        return 0

    if args.dist:
        dist = Path(args.dist)
    else:
        gw = find_gateway_dir(args.gateway_dir)
        if gw is None:
            return 1
        log(f"网关目录 {gw}")
        log(f"网关版本 {gateway_version(gw)}")
        dist = gw / "web" / "dist"

    if not dist.is_dir():
        log(f"dist 不存在：{dist}")
        log("先在网关的 web/ 目录里构建：node_modules\\.bin\\vp.CMD build")
        return 1

    problems = sanity(dist)
    if problems:
        log("dist 校验未通过，已中止：")
        for p in problems:
            log(f"  ✗ {p}")
        return 1

    src_files = sorted(p for p in dist.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in src_files)
    log(f"来源 {dist}")
    log(f"  {len(src_files)} 个文件，{total / 1024:.1f} KB")
    for p in src_files:
        log(f"    {p.relative_to(dist)}  {p.stat().st_size}B")

    if TARGET.exists():
        if not args.force:
            # 内容一致就没必要动 —— 避免无意义的 diff
            import hashlib

            def digest(root: Path) -> str:
                h = hashlib.sha256()
                for p in sorted(root.rglob("*")):
                    if p.is_file():
                        h.update(str(p.relative_to(root)).replace("\\", "/").encode())
                        h.update(p.read_bytes())
                return h.hexdigest()

            if digest(TARGET) == digest(dist):
                log("内置 WebUI 与来源一致，无需改动。")
                return 0
        log(f"清掉旧的 {TARGET}")
        shutil.rmtree(TARGET)

    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist, TARGET, dirs_exist_ok=True)
    log(f"已写入 {TARGET}")

    # 记录来源，便于以后追查这份 dist 是从哪个网关版本构建的
    (TARGET / "_source.json").write_text(
        json.dumps(
            {
                "gateway_version": gateway_version(dist.parent.parent) if (dist.parent.parent / "VERSION").is_file() else "?",
                "source": str(dist),
                "files": len(src_files),
                "bytes": total,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log("记得把 assets/webui/ 一起提交 —— CI 构建不出它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
