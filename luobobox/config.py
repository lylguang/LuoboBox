"""配置层：单文件 JSON，落在 %LOCALAPPDATA%\\LuoboBox\\config.json。

刻意不用 QSettings / 注册表 —— 自用工具，用户要能一眼看懂、能手改、能整包带走。
任何一次写入都先落临时文件再原子替换，避免断电写坏。
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import socket
from pathlib import Path
from typing import Any

from .paths import (
    backup_dir,
    config_file,
    default_gateway_dir,
    is_gateway_dir,
)

CONFIG_VERSION = 1

# 与既有部署（start.bat / run_service.pyw）保持一致的默认环境变量
DEFAULT_ENV: dict[str, str] = {
    "CODEBUDDY2API_ADMIN_CSRF": "true",
    "CODEBUDDY2API_MAX_IMAGES": "16",
    "CODEBUDDY2API_IMAGE_POLICY": "truncate",
    "CODEBUDDY2API_MAX_REQUEST_BYTES": "33554432",
    "CODEBUDDY2API_LOG_BODY_LIMIT": "65536",
    "CODEBUDDY2API_AUTO_TRIAL": "false",
}

# Codex 必需的一组参数，缺一则 11128 拦截或 agent 能力退化
DEFAULT_EXTRA_ARGS: list[str] = [
    "--desensitize",
    "--no-compact",
    "--keep-tool-metadata",
]

DEFAULT_TAILSCALE = r"C:\Program Files\Tailscale\tailscale.exe"


def gen_api_key() -> str:
    return secrets.token_urlsafe(48)


def default_config() -> dict[str, Any]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    gw = default_gateway_dir()
    return {
        "version": CONFIG_VERSION,
        "gateway": {
            "dir": str(gw),
            # 刻意留空：裸写 "python" 在打包成 exe 后必然失效，
            # 交给 AppContext.ensure_ready() 用真正的依赖探测来填。
            "python": "",
            "host": "0.0.0.0",
            "port": 8788,
            "api_key": gen_api_key(),
            "extra_args": list(DEFAULT_EXTRA_ARGS),
            "env": dict(DEFAULT_ENV),
            "auto_start": True,
            "stop_on_exit": False,
            "restart_on_exit": False,
        },
        "funnel": {
            "enabled": False,
            "port": "8443",
            "tailscale_exe": DEFAULT_TAILSCALE,
            "hostname": "",
        },
        "clients": {
            "codex": {
                "enabled": False,
                "config_path": str(home / ".codex" / "config.toml"),
                "model": "deepseek-v4.1-flash",
                "name": "CodeBuddy (LuoboBox)",
                "provider_key": "codebuddy",
            },
            "claude": {
                "enabled": False,
                "env_file": str(home / ".claude" / "settings.json"),
                "model": "deepseek-v4.1-flash",
            },
        },
        "app": {
            "autostart": False,
            "minimize_to_tray": True,
            "silent_start": False,
            "first_run_done": False,
            "last_gateway_dir": str(gw),
        },
        "updater": {
            "repo": "maiphucgiang/codebuddy2api",
            "check_on_start": True,
            "last_check": "",
            "last_known_version": "",
        },
        "ui": {
            "log_tail_lines": 800,
            "health_interval_sec": 15,
        },
        "meta": {
            "created": "",
            "luobobox_version": "",
        },
    }


def _deep_merge(base: dict, patch: dict) -> dict:
    """把 patch 合进 base（递归），base 被就地修改并返回。"""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Config:
    """点号路径访问的配置对象：cfg.get("gateway.port") / cfg.set("app.autostart", True)。"""

    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None):
        self.path = Path(path or config_file())
        self.data = _deep_merge(default_config() if data is None else data, {})
        if data is None:
            self._migrate()

    # ---------------------------------------------------------- 载入 / 保存

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = Path(path or config_file())
        if not p.is_file():
            cfg = cls()
            cfg.data["meta"]["created"] = _now()
            cfg.save()
            return cfg
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("根节点不是对象")
        except Exception:  # noqa: BLE001  配置损坏不应让程序打不开
            broken = p.with_suffix(f".broken-{_stamp()}.json")
            try:
                p.replace(broken)
            except OSError:
                pass
            cfg = cls()
            cfg.data["meta"]["created"] = _now()
            cfg.save()
            return cfg
        # 用默认值兜底缺失字段（升级新增字段时自动补齐）
        cfg = cls(_deep_merge(default_config(), raw), path=p)
        cfg._migrate()
        return cfg

    def _migrate(self) -> None:
        self.data["version"] = CONFIG_VERSION
        if not self.data["gateway"].get("api_key"):
            self.data["gateway"]["api_key"] = gen_api_key()

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)
        return self.path

    def backup(self, tag: str = "config") -> Path:
        dst = backup_dir() / f"{tag}-{_stamp()}.json"
        dst.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        return dst

    # ---------------------------------------------------------- 访问

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def clone(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    # ---------------------------------------------------------- 校验

    def validate(self) -> list[str]:
        """返回问题列表，空列表代表可启动。"""
        problems: list[str] = []
        gw = Path(str(self.get("gateway.dir", "")))
        if not gw.is_dir():
            problems.append(f"网关目录不存在：{gw}")
        elif not is_gateway_dir(gw):
            problems.append(f"网关目录里找不到 converter.py：{gw}")
        port = int(self.get("gateway.port", 0) or 0)
        if not (1 <= port <= 65535):
            problems.append(f"端口非法：{port}")
        if not str(self.get("gateway.api_key", "")).strip():
            problems.append("API Key 为空")
        # 注意：空串的 Path("") 会变成 Path(".")，而 Path(".").exists() 是 True，
        # 于是"没配解释器"会被误判为通过，最后在 build_command 里炸出
        # ValueError: WindowsPath('.') has an empty name —— 必须在字符串层先拦。
        py_raw = str(self.get("gateway.python", "") or "").strip()
        if not py_raw:
            problems.append("未指定 Python 解释器（请到「设置」里选择带 fastapi/uvicorn 的解释器）")
        elif not Path(py_raw).is_file():
            problems.append(f"Python 解释器不存在：{py_raw}")
        return problems

    def runtime_env(self) -> dict[str, str]:
        env = dict(self.get("gateway.env", {}) or {})
        env["CODEBUDDY2API_KEY"] = str(self.get("gateway.api_key", ""))
        return env

    def base_url(self, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{int(self.get('gateway.port', 8788))}"

    def dashboard_url(self) -> str:
        return self.base_url() + "/dashboard"


# ---------------------------------------------------------------- 工具

def _now() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) != 0


def pick_free_port(start: int = 8788, tries: int = 200) -> int:
    for port in range(start, start + tries):
        if port_free(port):
            return port
    return start
