"""客户端接入自动化：把 Codex / Claude Code 指到萝卜盒网关。

两条来自既有部署实测的硬结论，直接固化在这里：

  1. **必须用 `http_headers` 静态头，不能用 `env_key`。**
     Windows 上改了用户环境变量后，已经在运行的 Explorer 不会刷新，
     从开始菜单启动的 Codex 会读到旧环境直接 401。
  2. **`model_context_window = 200000`。**
     上游实测 ~200K 可过、~256K 报 code:11115。原来写 100 万会让 Codex
     以为还能塞、实际被硬拒。

改动一律「先备份、再手术式改行」，绝不整文件重写 —— 用户自己的
[model_providers.OpenAI] 段和注释必须原样保留。还原 = 把备份放回去。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from .paths import backup_dir, unique_path

MARKER = "# === LuoboBox managed block — 由萝卜盒自动生成，请勿手动编辑 ==="
MARKER_END = "# === LuoboBox managed block end ==="

CODEX_TOP_KEYS = (
    "model_provider",
    "model",
    "model_context_window",
    "model_auto_compact_token_limit",
)

CONTEXT_WINDOW = 200_000
AUTO_COMPACT = 180_000


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _backup(path: Path, tag: str) -> Path | None:
    if not path.is_file():
        return None
    dst = unique_path(backup_dir(), f"{tag}-{path.name}-{_stamp()}.bak")
    shutil.copy2(path, dst)
    return dst


# ====================================================================== Codex

class CodexConfigurator:
    """操作 ~/.codex/config.toml（Codex 桌面版与 CLI 共用同一个文件）。"""

    def __init__(self, config):
        self.config = config

    # ---------------------------------------------------------------- 路径

    @property
    def path(self) -> Path:
        return Path(str(self.config.get("clients.codex.config_path")))

    @property
    def provider_key(self) -> str:
        return str(self.config.get("clients.codex.provider_key", "codebuddy"))

    @property
    def model(self) -> str:
        return str(self.config.get("clients.codex.model", "deepseek-v4.1-flash"))

    @property
    def base_url(self) -> str:
        return self.config.base_url() + "/v1"

    def render_block(self) -> str:
        key = str(self.config.get("gateway.api_key", ""))
        name = str(self.config.get("clients.codex.name", "CodeBuddy (LuoboBox)"))
        return "\n".join([
            MARKER,
            f"model_provider = \"{self.provider_key}\"",
            f"model = \"{self.model}\"",
            f"model_context_window = {CONTEXT_WINDOW}",
            f"model_auto_compact_token_limit = {AUTO_COMPACT}",
            "",
            f"[model_providers.{self.provider_key}]",
            f"name = \"{name}\"",
            f"base_url = \"{self.base_url}\"",
            "wire_api = \"responses\"",
            f"http_headers = {{ \"Authorization\" = \"Bearer {key}\" }}",
            MARKER_END,
            "",
        ])

    # ---------------------------------------------------------------- 状态

    def status(self) -> dict:
        st = {"applied": False, "path": str(self.path), "exists": self.path.is_file(),
              "reason": "", "backups": self.backups()}
        if not self.path.is_file():
            st["reason"] = "配置文件不存在（Codex 可能尚未安装或从未运行过）"
            return st
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            st["reason"] = f"读取失败：{exc}"
            return st
        if MARKER not in text:
            st["reason"] = "尚未接入"
            return st
        key = str(self.config.get("gateway.api_key", ""))
        if key and key not in text:
            st["reason"] = "已接入，但 API Key 与当前配置不一致（需要重新应用）"
            return st
        st["applied"] = True
        st["reason"] = "已接入"
        return st

    def backups(self) -> list[str]:
        pat = re.compile(r"codex-config\.toml-(\d{8}-\d{6})(?:-\d+)?\.bak$")
        out = []
        for f in sorted(backup_dir().glob("codex-config.toml-*.bak"), reverse=True):
            m = pat.search(f.name)
            out.append(m.group(1) if m else f.name)
        return out[:10]

    # ---------------------------------------------------------------- 应用

    def apply(self) -> tuple[bool, str]:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        original_text = ""
        if path.is_file():
            try:
                original_text = path.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                return False, f"读取原配置失败：{exc}"
            backup = _backup(path, "codex")
        else:
            backup = None
            # Codex 未安装时也给出一份可用的初始配置，方便日后手动放置
        try:
            new_text = self._surgical(original_text)
            tmp = path.with_suffix(".toml.tmp")
            tmp.write_text(new_text, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            return False, f"写入失败：{exc}"
        msg = f"已接入 Codex：{path}"
        if backup:
            msg += f"（原文件已备份到 {backup.name}）"
        return True, msg

    def _surgical(self, text: str) -> str:
        """保留原有内容，只替换我们要管的键与表。"""
        text = text.replace("\r\n", "\n")

        # 1) 先把上一次我们自己写入的块整段砍掉（我们始终把它放在文件末尾）
        if MARKER in text:
            text = text.split(MARKER)[0].rstrip("\n") + "\n"

        lines = text.split("\n")

        # 2) 切出"顶层键区"（第一个 [table] 之前）与"表区"
        first_table = len(lines)
        for i, line in enumerate(lines):
            if re.match(r"^\s*\[", line):
                first_table = i
                break
        head, body = lines[:first_table], lines[first_table:]

        # 3) 顶层键区里，把我们要接管的那几个键删掉 —— 值已由备份保全
        kept: list[str] = []
        for line in head:
            m = re.match(r"^\s*([A-Za-z0-9_\-]+)\s*=", line)
            if m and m.group(1) in CODEX_TOP_KEYS:
                continue
            kept.append(line)
        while kept and not kept[-1].strip():
            kept.pop()
        head = kept

        # 4) 表区里删掉旧的 [model_providers.<provider_key>] 段（保留别人的表）
        out_body: list[str] = []
        skip = False
        header_re = re.compile(r"^\s*\[")
        target_header = f"[model_providers.{self.provider_key}]"
        for line in body:
            if header_re.match(line):
                skip = line.strip() == target_header
            if not skip:
                out_body.append(line)
        while out_body and not out_body[-1].strip():
            out_body.pop()
        body = out_body

        # 5) 拼回去：顶层键 → 空行 → 原有表 → 我们管理的块
        parts: list[str] = []
        parts.append(MARKER)
        parts.append(f"model_provider = \"{self.provider_key}\"")
        parts.append(f"model = \"{self.model}\"")
        parts.append(f"model_context_window = {CONTEXT_WINDOW}")
        parts.append(f"model_auto_compact_token_limit = {AUTO_COMPACT}")
        if head:
            parts.append("")
            parts.extend(head)
        if body:
            parts.append("")
            parts.extend(body)
        parts.append("")
        key = str(self.config.get("gateway.api_key", ""))
        name = str(self.config.get("clients.codex.name", "CodeBuddy (LuoboBox)"))
        parts.extend([
            f"[model_providers.{self.provider_key}]",
            f"name = \"{name}\"",
            f"base_url = \"{self.base_url}\"",
            "wire_api = \"responses\"",
            f"http_headers = {{ \"Authorization\" = \"Bearer {key}\" }}",
            MARKER_END,
            "",
        ])
        return "\n".join(parts)

    # ---------------------------------------------------------------- 还原

    def restore(self, backup_name: str | None = None) -> tuple[bool, str]:
        if backup_name:
            src = backup_dir() / backup_name
            if not src.is_file():
                return False, f"找不到备份：{backup_name}"
        else:
            candidates = sorted(backup_dir().glob("codex-config.toml-*.bak"))
            if not candidates:
                return False, "没有可用备份，无法还原"
            src = candidates[-1]
        try:
            if self.path.is_file():
                _backup(self.path, "codex-before-restore")
            shutil.copy2(src, self.path)
            return True, f"已从 {src.name} 还原"
        except Exception as exc:  # noqa: BLE001
            return False, f"还原失败：{exc}"


# ============================================================== Claude Code

class ClaudeConfigurator:
    """Claude Code / Anthropic 兼容客户端：写 ~/.claude/settings.json 的 env 段。"""

    def __init__(self, config):
        self.config = config

    @property
    def path(self) -> Path:
        return Path(str(self.config.get("clients.claude.env_file")))

    def env_payload(self) -> dict[str, str]:
        model = str(self.config.get("clients.claude.model", "deepseek-v4.1-flash"))
        return {
            "ANTHROPIC_BASE_URL": self.config.base_url(),
            "ANTHROPIC_AUTH_TOKEN": str(self.config.get("gateway.api_key", "")),
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": model,
        }

    def status(self) -> dict:
        st = {"applied": False, "path": str(self.path), "exists": self.path.is_file(),
              "reason": "", "backups": self.backups()}
        if not self.path.is_file():
            st["reason"] = "配置文件不存在（Claude Code 可能尚未安装）"
            return st
        try:
            data = json.loads(self.path.read_text(encoding="utf-8", errors="replace") or "{}")
        except Exception as exc:  # noqa: BLE001
            st["reason"] = f"解析失败：{exc}"
            return st
        env = (data.get("env") or {}) if isinstance(data, dict) else {}
        want = self.env_payload()
        if all(str(env.get(k, "")) == v for k, v in want.items()):
            st["applied"] = True
            st["reason"] = "已接入"
        else:
            st["reason"] = "尚未接入"
        return st

    def backups(self) -> list[str]:
        out = []
        for f in sorted(backup_dir().glob("claude-settings.json-*.bak"), reverse=True):
            m = re.search(r"(\d{8}-\d{6})", f.name)
            out.append(m.group(1) if m else f.name)
        return out[:10]

    def apply(self) -> tuple[bool, str]:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
                if not isinstance(data, dict):
                    raise ValueError("根节点不是对象")
            except Exception as exc:  # noqa: BLE001
                return False, f"原 settings.json 无法解析，已放弃改动：{exc}"
            backup = _backup(path, "claude")
        else:
            backup = None
        data.setdefault("env", {})
        data["env"].update(self.env_payload())
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            return False, f"写入失败：{exc}"
        msg = f"已接入 Claude Code：{path}"
        if backup:
            msg += f"（原文件已备份到 {backup.name}）"
        return True, msg

    def restore(self, backup_name: str | None = None) -> tuple[bool, str]:
        if backup_name:
            src = backup_dir() / backup_name
            if not src.is_file():
                return False, f"找不到备份：{backup_name}"
        else:
            candidates = sorted(backup_dir().glob("claude-settings.json-*.bak"))
            if not candidates:
                return False, "没有可用备份，无法还原"
            src = candidates[-1]
        try:
            shutil.copy2(src, self.path)
            return True, f"已从 {src.name} 还原"
        except Exception as exc:  # noqa: BLE001
            return False, f"还原失败：{exc}"


# ============================================================ 通用片段输出

def snippet_codex(config, with_key: bool = True) -> str:
    key = str(config.get("gateway.api_key", "")) if with_key else "<API_KEY>"
    model = str(config.get("clients.codex.model", "deepseek-v4.1-flash"))
    provider = str(config.get("clients.codex.provider_key", "codebuddy"))
    return "\n".join([
        f"model_provider = \"{provider}\"",
        f"model = \"{model}\"",
        f"model_context_window = {CONTEXT_WINDOW}",
        f"model_auto_compact_token_limit = {AUTO_COMPACT}",
        "",
        f"[model_providers.{provider}]",
        f"name = \"{config.get('clients.codex.name', 'CodeBuddy (LuoboBox)')}\"",
        f"base_url = \"{config.base_url()}/v1\"",
        "wire_api = \"responses\"",
        f"http_headers = {{ \"Authorization\" = \"Bearer {key}\" }}",
    ])


def snippet_claude(config, with_key: bool = True) -> str:
    key = str(config.get("gateway.api_key", "")) if with_key else "<API_KEY>"
    model = str(config.get("clients.claude.model", "deepseek-v4.1-flash"))
    return "\n".join([
        f"ANTHROPIC_BASE_URL={config.base_url()}",
        f"ANTHROPIC_AUTH_TOKEN={key}",
        f"ANTHROPIC_MODEL={model}",
        f"ANTHROPIC_SMALL_FAST_MODEL={model}",
    ])
