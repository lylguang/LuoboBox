"""后台任务助手。

网关启停、健康检查、Funnel 切换、GitHub 检查更新 —— 这些都会阻塞几百毫秒到几十秒，
绝不能放在 GUI 主线程里，否则窗口直接假死（Windows 会给它盖一个"无响应"白纱）。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class Worker(QThread):
    """在子线程里跑一个函数，结果通过信号回主线程。"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args, parent: QObject | None = None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=3)}")
        else:
            self.succeeded.emit(result)


class TaskRunner(QObject):
    """统一管理活跃 Worker 的引用，避免被 GC 掉导致线程异常退出。"""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._active: list[Worker] = []

    def run(
        self,
        fn: Callable[..., Any],
        on_ok: Callable[[Any], None] | None = None,
        on_err: Callable[[str], None] | None = None,
        *args,
        **kwargs,
    ) -> Worker:
        worker = Worker(fn, *args, parent=self, **kwargs)
        self._active.append(worker)

        def cleanup() -> None:
            if worker in self._active:
                self._active.remove(worker)

        if on_ok:
            worker.succeeded.connect(on_ok)
        if on_err:
            worker.failed.connect(on_err)
        worker.finished.connect(cleanup)
        worker.start()
        return worker

    def busy(self) -> bool:
        return any(w.isRunning() for w in self._active)

    def wait_all(self, timeout_ms: int = 4000) -> None:
        for w in list(self._active):
            w.wait(timeout_ms)
