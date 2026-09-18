"""桌面壳自己的日志（与网关日志分开，排障时一眼分得清谁在说话）。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import app_log

_configured = False


def setup(level: int = logging.INFO) -> logging.Logger:
    global _configured
    root = logging.getLogger("luobobox")
    if _configured:
        return root
    root.setLevel(level)
    handler = RotatingFileHandler(
        app_log(), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(handler)
    _configured = True
    return root


def get(name: str = "app") -> logging.Logger:
    setup()
    return logging.getLogger(f"luobobox.{name}")
