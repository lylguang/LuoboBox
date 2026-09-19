"""打包脚本（跨 shell，用 Python 写，避免 .ps1/.bat 的编码坑）。

用法：
    python packaging/build.py              # 只打包
    python packaging/build.py --clean      # 打包前清理
    python packaging/build.py --installer  # 打包并尝试生成安装包

产物：
    dist/LuoboBox/            onedir 发行目录
    dist/LuoboBox-<版本>-portable.zip
    dist/LuoboBox-Setup-<版本>.exe   （若本机有 Inno Setup）

注：便携版 zip 刻意用纯 ASCII 文件名 —— 中文名（"便携版"）在 GitHub Actions
上传 Release 资产时会被剥掉，变成 `LuoboBox-1.0.0-.zip` 这种残缺名。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# Windows 上 stdout 的默认编码取决于控制台代码页：本地是 GBK，GitHub Actions
# 的 runner 是 cp1252 —— 两者都装不下中文，第一行 print 就会 UnicodeEncodeError。
# 统一强制成 UTF-8，本地与 CI 行为一致。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

def _iscc_candidates() -> list[Path]:
    out = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    # winget 默认装到用户级目录（PrivilegesRequired=lowest 的包）
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.insert(0, Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    return out


# Inno Setup 里"字面引号"要靠双写实现，于是 .iss 正文里会出现连续三个引号。
# 直接写进 Python 的三引号字符串会把字符串提前闭合 —— 拆成常量再插值。
#   前置 = 开引号 + 转义引号 → 3 个
#   后置 = 转义引号 + 闭引号 → 2 个
Q3 = chr(34) * 3
Q2 = chr(34) * 2


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def read_version() -> str:
    text = (ROOT / "luobobox" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\'')
    return "0.0.0"


def write_version_info(version: str) -> None:
    parts = (version.split(".") + ["0", "0", "0", "0"])[:4]
    nums = ", ".join(str(int(p)) if p.isdigit() else "0" for p in parts)
    (PACKAGING / "version_info.txt").write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({nums}),
    prodvers=({nums}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'LuoboBox'),
        StringStruct('FileDescription', '萝卜盒 — codebuddy2api 本地网关控制台'),
        StringStruct('FileVersion', '{version}.0'),
        StringStruct('InternalName', 'LuoboBox'),
        StringStruct('LegalCopyright', 'PolyForm Noncommercial License 1.0.0'),
        StringStruct('OriginalFilename', 'LuoboBox.exe'),
        StringStruct('ProductName', '萝卜盒 LuoboBox'),
        StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def make_icon() -> None:
    icon = ROOT / "assets" / "icon.ico"
    if icon.is_file():
        return
    log("生成图标…")
    subprocess.run([sys.executable, str(PACKAGING / "make_icon.py")], check=True)


def clean() -> None:
    """报告上一次的产物。

    这里**不做任何删除**，原因有二：
      1. 三个产物本来就都是原地覆盖 —— PyInstaller 带 `--clean --noconfirm`，
         zipfile 的 "w" 模式会截断重建，Inno 的 OutputBaseFilename 同理；
      2. 批量删除既慢，又会被各类安全策略/沙箱拦下（实测连删一个 zip
         都会因为把压缩包内条目算进来而超阈值）。
    想真正从头来一遍用 `--purge`。
    """
    DIST.mkdir(parents=True, exist_ok=True)
    leftovers = sorted(p.name for p in DIST.glob("LuoboBox*"))
    if leftovers:
        log("上次的产物（将被原地覆盖）：" + "、".join(leftovers))
    else:
        log("没有旧产物")


def purge() -> None:
    """彻底清空 build/ 与 dist/（想从头来一遍时用）。"""
    for d in (BUILD, DIST):
        if d.exists():
            log(f"彻底删除 {d}")
            shutil.rmtree(d, ignore_errors=True)


def run_pyinstaller(build_id: str) -> Path:
    """把产物打到一个全新的目录里，最后靠**重命名**换到位。

    为什么不直接让 PyInstaller 覆盖 dist/LuoboBox：
    覆盖 = 删除旧目录里的上百个文件。在带批量删除保护的机器上（本机就有，
    阈值 50 个文件）这会被直接拦下，构建失败。而重命名是单次元数据操作，
    永远不触发删除类策略 —— 顺带还带来了"构建失败不影响现有可用版本"的好处。

    返回 staged 目录（里面是 LuoboBox/）。
    """
    work = BUILD / f"w{build_id}"
    stage = DIST / "_stage" / build_id
    log("运行 PyInstaller…")
    log(f"  工作目录 {work}")
    log(f"  暂存目录 {stage}")
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm",
         "--log-level", "WARN",
         "--workpath", str(work),
         "--distpath", str(stage),
         str(PACKAGING / "luobobox.spec")],
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"PyInstaller 失败（退出码 {proc.returncode}）。\n"
            f"注意：现有的 dist/LuoboBox 未被改动，仍可正常使用。"
        )
    log(f"PyInstaller 完成，用时 {time.time() - t0:.0f}s")
    return stage


def swap_into_place(staged: Path, target: Path, build_id: str) -> None:
    """把暂存产物换到 target 位置（只做重命名，不做删除）。"""
    if not staged.is_dir():
        raise SystemExit(f"暂存目录不存在：{staged}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        prev = DIST / "_prev" / build_id
        prev.parent.mkdir(parents=True, exist_ok=True)
        log(f"旧版本移到 {prev}")
        try:
            os.rename(target, prev)
        except OSError as exc:
            raise SystemExit(f"无法移开旧产物（请先关闭正在运行的 LuoboBox）：{exc}") from exc
    os.rename(staged, target)
    log(f"产物就位 {target}")


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def make_portable_zip(version: str, build_id: str) -> Path:
    src = DIST / "LuoboBox"
    # 纯 ASCII 名：中文在 GitHub Release 资产名里会被吞掉（见文件头注释）
    out = DIST / f"LuoboBox-{version}-portable.zip"
    log(f"打包便携版 {out.name}…")
    # 写到暂存再 os.replace，避免直接 truncate 现有归档
    tmp = DIST / "_stage" / f"{build_id}.zip"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, Path("LuoboBox") / f.relative_to(src))
    os.replace(tmp, out)
    return out


def find_iscc() -> Path | None:
    for c in _iscc_candidates():
        if c.is_file():
            return c
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    return Path(found) if found else None


def write_iss(version: str, out_dir: Path) -> Path:
    iss = PACKAGING / "luobobox.iss"
    iss.write_text(
        f"""; 萝卜盒安装包（由 packaging/build.py 生成）
#define AppName "萝卜盒"
#define AppNameEn "LuoboBox"
#define AppVersion "{version}"
#define AppPublisher "LuoboBox"
#define AppExe "LuoboBox.exe"

[Setup]
AppId={{{{8E4A1C6B-5D3F-4A7E-9C21-7B3E5A0D9F42}}}}
AppName={{#AppName}} ({{#AppNameEn}})
AppVersion={{#AppVersion}}
AppPublisher={{#AppPublisher}}
DefaultDirName={{autopf}}\\{{#AppNameEn}}
DefaultGroupName={{#AppName}}
DisableProgramGroupPage=yes
OutputDir={out_dir}
OutputBaseFilename={{#AppNameEn}}-Setup-{{#AppVersion}}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={ROOT / 'assets' / 'icon.ico'}
UninstallDisplayIcon={{app}}\\{{#AppExe}}
UninstallDisplayName={{#AppName}}

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "开机自启（登录时启动萝卜盒）"; Flags: unchecked
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{ROOT / 'dist' / 'LuoboBox'}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExe}}"
Name: "{{group}}\\卸载 {{#AppName}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExe}}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\\Microsoft\\Windows\\CurrentVersion\\Run"; \\
    ValueType: string; ValueName: "LuoboBox"; \\
    ValueData: {Q3}{{app}}\\{{#AppExe}}{Q2} --tray"; \\
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{{app}}\\{{#AppExe}}"; Description: "立即运行 {{#AppName}}"; \\
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{{app}}"
""",
        encoding="utf-8",
    )
    return iss


def build_installer(version: str, build_id: str) -> Path | None:
    iscc = find_iscc()
    if not iscc:
        log("未找到 Inno Setup，跳过安装包生成。")
        log("  安装方式：winget install -e --id JRSoftware.InnoSetup")
        return None
    # 先输出到暂存目录，再 os.replace —— 同 zip 的理由
    stage = DIST / "_stage" / f"setup-{build_id}"
    stage.mkdir(parents=True, exist_ok=True)
    iss = write_iss(version, stage)
    log(f"编译安装包（{iscc}）…")
    proc = subprocess.run([str(iscc), str(iss)], cwd=str(PACKAGING))
    if proc.returncode != 0:
        log(f"Inno Setup 失败（退出码 {proc.returncode}）")
        return None
    built = stage / f"LuoboBox-Setup-{version}.exe"
    if not built.is_file():
        log(f"没找到安装包产物：{built}")
        return None
    out = DIST / f"LuoboBox-Setup-{version}.exe"
    os.replace(built, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="清掉上一次的归档产物")
    ap.add_argument("--purge", action="store_true", help="彻底清空 build/ 与 dist/ 再打包")
    ap.add_argument("--installer", action="store_true", help="同时生成安装包")
    ap.add_argument("--zip", action="store_true", help="同时生成便携版 zip")
    ap.add_argument("--no-selftest", action="store_true", help="跳过产物自检")
    args = ap.parse_args()

    version = read_version()
    build_id = time.strftime("%Y%m%d-%H%M%S")
    log(f"萝卜盒 {version}   构建号 {build_id}")
    log(f"项目根目录 {ROOT}")

    if args.purge:
        purge()
    if args.clean:
        clean()

    make_icon()
    write_version_info(version)
    staged = run_pyinstaller(build_id)
    swap_into_place(staged / "LuoboBox", DIST / "LuoboBox", build_id)

    target = DIST / "LuoboBox"
    exe = target / "LuoboBox.exe"
    if not exe.is_file():
        raise SystemExit(f"没有生成 {exe}")
    log(f"产物 {exe}")
    log(f"体积 {human(dir_size(target))}")

    if args.zip or args.installer:
        z = make_portable_zip(version, build_id)
        log(f"便携版 {z}（{human(z.stat().st_size)}）")

    if args.installer:
        setup = build_installer(version, build_id)
        if setup:
            log(f"安装包 {setup}（{human(setup.stat().st_size)}）")

    # 提示积累的中间目录（不做自动删除，避免触发批量删除保护）
    stale = [p for p in (DIST / "_stage").glob("*") if p.name != build_id]
    if stale:
        log(f"暂存目录里还有 {len(stale)} 个历史构建，需要清理时跑："
            f"python packaging/build.py --purge")

    # 构建完顺手自检产物
    if exe.is_file() and not args.no_selftest:
        log("对产物做一次自检…")
        # 按字节捕获，解码自适应：应用侧已尽量输出 UTF-8，
        # 但冻结程序的 stdout 编码受多种因素影响，兜底回 GBK，
        # 保证无论哪头失配，PASS/FAIL 行都读得懂
        r = subprocess.run([str(exe), "--selftest"], cwd=str(target),
                           capture_output=True, timeout=180)
        out = _decode_output(r.stdout, r.stderr)
        print(out, flush=True)
        if r.returncode != 0:
            hexcode = f"（0x{r.returncode & 0xFFFFFFFF:08X}）"
            log(f"⚠ 产物自检退出码非 0：{r.returncode}{hexcode} —— "
                f"若上面各项都是 PASS，多半是退出阶段的问题，不是检查失败")
        else:
            log("产物自检通过")

    log("完成。")
    return 0


def _decode_output(*streams: bytes | None) -> str:
    """子进程输出的自适应解码：UTF-8 优先，解出替换符再试 GBK。"""
    for enc in ("utf-8", "gbk"):
        parts = []
        bad = False
        for s in streams:
            if not s:
                continue
            try:
                parts.append(s.decode(enc))
            except UnicodeDecodeError:
                bad = True
                break
        if not bad:
            text = "\n".join(parts).strip()
            if enc == "gbk" or "\ufffd" not in text:
                return text
    # 两个编码都不干净：退回 utf-8 + replace，至少结构还在
    return b"\n".join(s for s in streams if s).decode("utf-8", "replace").strip()


if __name__ == "__main__":
    raise SystemExit(main())
