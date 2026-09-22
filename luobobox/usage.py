"""本地额度消耗统计：基于余额变化反推，纯本地、客观、不依赖上游网关。

网关的 /admin/credentials、/admin/credits 只给「当前余额」，不给消耗历史。
我们把每次健康刷新的余额快照持久化到 data_dir()/usage.json，用差额反推消耗：
  · 累计消耗 = 首次记录余额 − 当前余额（自萝卜盒开始记录以来）
  · 今日消耗 = 当日首次余额 − 当前余额
余额上升（充值）不计入消耗，累计消耗按净额 clamp 到 0。
数据完全留在本机，不上传、不发给任何上游。
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .paths import data_dir

_BALANCE_FIELDS = ("credits", "balance", "remain", "remaining")


def _usage_file() -> Path:
    return data_dir() / "usage.json"


def _extract_balance(value: Any) -> Optional[float]:
    """从网关多变的余额形态里抠出数字。

    已知形态：纯数字 / dict({"credits": N}) / dict({"balance": N}) / None。
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for k in _BALANCE_FIELDS:
            v = value.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _account_key(item: dict) -> Optional[str]:
    for k in ("name", "id"):
        v = item.get(k)
        if v:
            return str(v)
    return None


def load() -> dict:
    p = _usage_file()
    if not p.is_file():
        return {"accounts": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"accounts": {}}
    if not isinstance(data, dict):
        return {"accounts": {}}
    data.setdefault("accounts", {})
    return data


def save(data: dict) -> None:
    p = _usage_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def record_snapshot(snap: Any) -> dict:
    """用一次健康快照更新本地消耗记录，返回最新记录字典。

    snap 需提供 .credentials（list[dict]）。
    """
    data = load()
    accounts = data.setdefault("accounts", {})
    now = time.time()
    today = date.today().isoformat()

    creds = getattr(snap, "credentials", None) or []
    changed = False
    for item in creds:
        if not isinstance(item, dict):
            continue
        key = _account_key(item)
        if not key:
            continue
        bal = _extract_balance(item.get("credits"))
        if bal is None:
            continue
        rec = accounts.get(key)
        if rec is None:
            rec = {
                "first_seen": now,
                "first_balance": bal,
                "last_balance": bal,
                "daily": {},  # {iso_date: 当日首次余额}
            }
            accounts[key] = rec
            changed = True
        if today not in rec["daily"]:
            rec["daily"][today] = bal
            changed = True
        if rec["last_balance"] != bal:
            rec["last_balance"] = bal
            changed = True

    # ★ 只在记录真的变了才落盘。本函数由健康轮询驱动（默认 15 秒一次），
    #   而账户余额常常几十分钟不动 —— 每次都 json.dumps + write_text
    #   是纯浪费，而且这一步还跑在 UI 线程上。
    if changed:
        save(data)
    return data


def account_consumed(data: dict, key: str) -> tuple[float, float]:
    """返回 (累计消耗, 今日消耗)，均 clamp 到 >= 0。"""
    rec = data.get("accounts", {}).get(key)
    if not rec:
        return 0.0, 0.0
    today = date.today().isoformat()
    total = max(0.0, rec["first_balance"] - rec["last_balance"])
    day_first = rec.get("daily", {}).get(today, rec["last_balance"])
    today_c = max(0.0, day_first - rec["last_balance"])
    return total, today_c


def _day_start_ts() -> float:
    try:
        now = datetime.now()
        return datetime(now.year, now.month, now.day).timestamp()
    except Exception:  # noqa: BLE001
        return time.time()


def summary(data: dict) -> dict:
    """汇总：总剩余 / 累计消耗 / 今日消耗 / 消耗速率(积分·小时)。"""
    accounts = data.get("accounts", {})
    today = date.today().isoformat()
    total_remaining = 0.0
    total_consumed = 0.0
    today_consumed = 0.0
    for rec in accounts.values():
        rem = rec.get("last_balance")
        if isinstance(rem, (int, float)) and not isinstance(rem, bool):
            total_remaining += rem
        total_consumed += max(0.0, rec.get("first_balance", rem) - rem)
        day_first = rec.get("daily", {}).get(today, rem)
        today_consumed += max(0.0, day_first - rem)
    elapsed_h = max((time.time() - _day_start_ts()) / 3600.0, 0.05)
    rate = today_consumed / elapsed_h if today_consumed > 0 else 0.0
    return {
        "total_remaining": total_remaining,
        "total_consumed": total_consumed,
        "today_consumed": today_consumed,
        "rate": rate,
        "accounts": len(accounts),
    }


def _next_day_value(daily: dict, day: str, all_dates: list[str]) -> Optional[float]:
    """`all_dates` 里排在 day 之后最近那天的首次余额（没有更新的一天就返回 None）。"""
    later = [d for d in all_dates if d > day]
    if not later:
        return None
    value = daily.get(later[0])
    return float(value) if isinstance(value, (int, float)) else None


def daily_series(data: dict, days: int = 7) -> list[tuple[str, float]]:
    """最近 N 天每天的消耗，按日期升序返回 [(YYYY-MM-DD, 消耗), ...]。

    推导方式：某天消耗 = 当天首次余额 − 次日首次余额；
    最后一天（今天）用 `last_balance` 收尾。

    为什么能这样算：`daily` 里存的是"当天第一次看到的余额"，
    所以相邻两天的差值恰好是这中间用掉的量。跨天若发生充值，
    差额会是负数 —— clamp 到 0。宁可少算，不可虚报消耗。
    """
    accounts = data.get("accounts", {})
    all_dates = sorted({d for rec in accounts.values()
                        for d in (rec.get("daily") or {})})

    today = date.today()
    window = [(today - timedelta(days=i)).isoformat()
              for i in range(days - 1, -1, -1)]

    out: list[tuple[str, float]] = []
    for day in window:
        total = 0.0
        for rec in accounts.values():
            daily = rec.get("daily") or {}
            if day not in daily:
                continue
            start = daily[day]
            if not isinstance(start, (int, float)):
                continue
            end = _next_day_value(daily, day, all_dates)
            if end is None:
                end = rec.get("last_balance")
            if isinstance(end, (int, float)):
                total += max(0.0, float(start) - float(end))
        out.append((day, total))
    return out
