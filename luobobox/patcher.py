"""脱敏词表补丁守护。

背景：gateway 升级会整目录覆盖 `app/`，而本地为了接入 Codex 往
`app/desensitize.py` 的 SENSITIVE_TERMS 里补了 5 个 OpenAI 系品牌词。
补丁被冲掉后 Codex 会立刻重新被上游 11128 拦截 —— 原来的做法是
"升级后记得手动重打"，很容易忘。

这里把它自动化：每次启动、每次升级后都自动体检并补齐，且改动前先备份。
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

# 必须存在的品牌词：缺了它们，Codex 的 system prompt 会被上游安全策略命中
# （复现条件：system 同时含 Codex 与 OpenAI 且长度 ≥185 字符）
REQUIRED_TERMS = ("OpenAI", "Codex", "ChatGPT", "GPT-4", "GPT-5")

LIST_HEAD_RE = re.compile(r"^\s*SENSITIVE_TERMS\s*(?::[^=]*)?=\s*\[", re.MULTILINE)


class PatchReport:
    def __init__(self) -> None:
        self.target_ok = False
        self.file: Path | None = None
        self.present: list[str] = []
        self.missing: list[str] = []
        self.patched = False
        self.backup: Path | None = None
        self.notes: list[str] = []
        self.error = ""

    @property
    def healthy(self) -> bool:
        return self.target_ok and not self.missing

    def summary(self) -> str:
        if not self.target_ok:
            return self.error or "未找到 app/desensitize.py，无法体检"
        if self.patched:
            return f"已补齐缺失品牌词：{'、'.join(self.missing)}（原文件已备份为 {self.backup.name if self.backup else '—'}）"
        if self.missing:
            return "仍缺失：" + "、".join(self.missing)
        return f"补丁完好（{len(self.present)}/{len(REQUIRED_TERMS)} 个品牌词在位）"


def _read(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def inspect(gateway_dir: Path | str) -> PatchReport:
    """只体检、不改动。"""
    rep = PatchReport()
    path = Path(gateway_dir) / "app" / "desensitize.py"
    rep.file = path
    if not path.is_file():
        rep.error = f"未找到 {path}"
        return rep
    text = _read(path)
    if not LIST_HEAD_RE.search(text):
        rep.error = "desensitize.py 结构不认识（找不到 SENSITIVE_TERMS 列表），未做任何改动"
        return rep
    rep.target_ok = True
    for term in REQUIRED_TERMS:
        if re.search(rf'["\']{re.escape(term)}["\']', text):
            rep.present.append(term)
        else:
            rep.missing.append(term)
    return rep


def ensure(gateway_dir: Path | str, backup_dir: Path | None = None) -> PatchReport:
    """体检并在必要时补齐缺失的品牌词。"""
    from .paths import backup_dir as default_backup_dir
    from .paths import unique_path

    rep = inspect(gateway_dir)
    if not rep.target_ok or not rep.missing or rep.file is None:
        return rep

    path = rep.file
    text = _read(path)
    lines = text.split("\n")

    # 定位 SENSITIVE_TERMS 列表的起止行
    start = None
    for i, line in enumerate(lines):
        if LIST_HEAD_RE.match(line):
            start = i
            break
    if start is None:
        rep.error = "定位 SENSITIVE_TERMS 失败"
        rep.target_ok = False
        return rep
    end = None
    for j in range(start + 1, len(lines)):
        if re.match(r"^\s*\]", lines[j]):
            end = j
            break
    if end is None:
        rep.error = "SENSITIVE_TERMS 列表未正常闭合"
        rep.target_ok = False
        return rep

    # 备份
    bdir = Path(backup_dir) if backup_dir else default_backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    dst = unique_path(bdir, f"desensitize.py-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    try:
        shutil.copy2(path, dst)
        rep.backup = dst
    except Exception as exc:  # noqa: BLE001
        rep.error = f"备份失败，已放弃改动：{exc}"
        return rep

    indent = re.match(r"^(\s*)", lines[start + 1]).group(1) if start + 1 < end else "    "
    injected = [f"{indent}# --- 萝卜盒 LuoboBox 补丁：OpenAI 系品牌词（Codex 接入必需）---"]
    injected += [f'{indent}"{term}",' for term in rep.missing]

    new_lines = lines[:end] + injected + lines[end:]
    try:
        tmp = path.with_suffix(".py.tmp")
        tmp.write_text("\n".join(new_lines), encoding="utf-8", newline="\n")
        import os

        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        rep.error = f"写入失败：{exc}"
        return rep

    rep.patched = True
    rep.notes.append("改动已生效，需重启网关后才会加载新词表")
    return rep


def check_args(args: list[str]) -> list[str]:
    """检查启动参数里 Codex 必需的三件套是否齐全，返回缺失项说明。"""
    problems: list[str] = []
    if "--desensitize" not in args:
        problems.append("缺少 --desensitize：词表补了也不生效，Codex 必被 11128 拦截")
    if "--no-compact" not in args:
        problems.append("缺少 --no-compact：Codex 的 17KB system prompt 会被压成一句话，agent 能力显著退化")
    if "--keep-tool-metadata" not in args:
        problems.append("缺少 --keep-tool-metadata：工具 description 会被整段删掉，模型不知道工具怎么用")
    return problems
