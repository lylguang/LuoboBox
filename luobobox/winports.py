"""Windows 原生监听表：一次系统调用拿到「哪些端口被谁监听」。

为什么要单独开一个模块
----------------------
本机（Windows）向 loopback 上的**空闲**端口 `connect_ex` 并不会立刻
收到 RST，SYN 被直接丢掉，于是要等满超时才返回 WSAEWOULDBLOCK(10035)。
实测：

    connect_ex(timeout=0.6)   空闲端口 614.6ms / 已监听端口 0.5ms
    bind                      空闲端口   0.03ms / 已监听端口 0.01ms

`Config.port_free()` 原来就是这个 connect_ex，而 UI 每刷新一轮要问
3 次「端口空不空」（`gateway.state` / `gateway.state_label` /
`context.quick_state`），再加上 `gateway.pid` → `listening_pids()`
每次都拉一个 `netstat -ano` 子进程（约 228ms）—— 一轮刷新两秒多，
用户感知就是「特别的卡」。

`GetExtendedTcpTable(TCP_TABLE_OWNER_PID_LISTENER)` 是纯内存查询，
一次拿全表（端口 → PID），顺带把 netstat 子进程也一起替掉。

调用方约定：返回 `None` 表示「这套原生 API 用不了」（非 Windows、
iphlpapi 缺失等），此时调用方自行回退到 socket / netstat 老路子。

★ 缓存使用铁律（踩过一次，记住）
--------------------------------
`CACHE_TTL_SEC` 的短缓存只允许「看一眼」的调用方吃
（状态显示、托盘、`is_listening()` 这类不产生副作用的读）。
**凡是会改配置、决定启停、决定挪端口的调用点，一律传 `fresh=True`。**

反例：`AppContext.ensure_ready()` 里吃缓存 → 它拿到的快照是
`pick_free_port()` 那一刻的（那时端口确实空着），而测试随后真的在
该端口起了监听器 → 快照里看不见 → "该挪的端口没挪"。同理
`gateway.start()` 的预检、向导的端口校验、`fix_port()` 都必须 fresh。
"""

from __future__ import annotations

import ctypes
import socket
import sys
import time
from ctypes import wintypes

AF_INET = 2
TCP_TABLE_OWNER_PID_LISTENER = 3
ERROR_INSUFFICIENT_BUFFER = 122
NO_ERROR = 0

# 缓存时长取得很短：短到不会让「刚启动的网关」被误判成没起来，
# 又足够把同一轮刷新里的 3~4 次查询合并成 1 次系统调用。
# 需要绝对真值的调用方传 fresh=True 绕过缓存。
CACHE_TTL_SEC = 0.25

_table: dict[int, tuple[int, ...]] | None = None
_table_at = 0.0
_native_ok: bool | None = None


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


def native_available() -> bool:
    """原生 API 是否可用（上一次探测结果，未探测过就先探一次）。"""
    if _native_ok is None:
        listening_table(fresh=True)
    return bool(_native_ok)


def _query() -> dict[int, tuple[int, ...]] | None:
    """问一次 iphlpapi；返回 None 表示这套 API 不可用。"""
    global _native_ok
    if sys.platform != "win32":
        _native_ok = False
        return None
    try:
        fn = ctypes.windll.iphlpapi.GetExtendedTcpTable
    except (AttributeError, OSError):
        _native_ok = False
        return None
    fn.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    fn.restype = wintypes.DWORD

    size = wintypes.DWORD(0)
    rc = fn(None, ctypes.byref(size), False, AF_INET,
            TCP_TABLE_OWNER_PID_LISTENER, 0)
    if rc not in (NO_ERROR, ERROR_INSUFFICIENT_BUFFER):
        _native_ok = False
        return None
    if size.value == 0:
        # 一个监听端口都没有：这也是一条有效答案
        _native_ok = True
        return {}

    buf = ctypes.create_string_buffer(size.value)
    rc = fn(buf, ctypes.byref(size), False, AF_INET,
            TCP_TABLE_OWNER_PID_LISTENER, 0)
    if rc != NO_ERROR:
        _native_ok = False
        return None

    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    if not count:
        _native_ok = True
        return {}
    rows = ctypes.cast(
        ctypes.byref(buf, ctypes.sizeof(wintypes.DWORD)),
        ctypes.POINTER(_MIB_TCPROW_OWNER_PID * count),
    ).contents

    acc: dict[int, list[int]] = {}
    for row in rows:
        # dwLocalPort 是高字节在前（网络序），高 16 位是填充
        port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        acc.setdefault(port, []).append(int(row.dwOwningPid))
    _native_ok = True
    return {p: tuple(sorted(set(pids))) for p, pids in acc.items()}


def listening_table(*, fresh: bool = False,
                    ttl: float | None = None
                    ) -> dict[int, tuple[int, ...]] | None:
    """{端口: (PID, ...)}；原生 API 不可用时返回 None。"""
    global _table, _table_at
    now = time.monotonic()
    limit = CACHE_TTL_SEC if ttl is None else ttl
    if not fresh and _table is not None and (now - _table_at) < limit:
        return _table
    table = _query()
    if table is None:
        return None
    _table = table
    _table_at = now
    return table


def forget_port_cache() -> None:
    """丢缓存。启停网关后立刻调用，别让 0.25s 的旧快照骗过后面的判断。"""
    global _table, _table_at
    _table = None
    _table_at = 0.0


def port_in_use(port: int, *, fresh: bool = False) -> bool | None:
    """端口是否有人监听；None = 原生 API 不可用，请回退。"""
    table = listening_table(fresh=fresh)
    if table is None:
        return None
    return int(port) in table


def listening_pids(port: int, *, fresh: bool = False) -> list[int] | None:
    """监听该端口的 PID 列表；None = 原生 API 不可用，请回退。"""
    table = listening_table(fresh=fresh)
    if table is None:
        return None
    return list(table.get(int(port), ()))
