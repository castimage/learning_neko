# 后台任务层：把阻塞调用丢到线程池，结果通过信号回主线程
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from desktop.api.client import ApiError


# 任务开始、成功、失败与结束都以信号形式回到主线程
class WorkerSignals(QObject):
    started = pyqtSignal()
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)   # code, message
    finished = pyqtSignal()


# 承载一次后台调用
class Worker(QRunnable):
    # 保存待执行函数与参数，全部信号挂在 signals 上
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    # 在线程池的线程里执行，异常必须转成信号，否则会静默消失
    def run(self) -> None:
        self.signals.started.emit()
        try:
            result = self._fn(*self._args, **self._kwargs)
        except ApiError as exc:             # 业务错误：透出后端错误码，界面据此分支提示
            self.signals.failed.emit(exc.code, exc.message)
        except Exception as exc:            # 故意宽捕获：后台线程的异常不能漏
            self.signals.failed.emit(type(exc).__name__, str(exc))
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


# 全局线程池，所有后台任务从这里发起；限制并发避免用户连点跑多份模型任务
POOL: QThreadPool = QThreadPool.globalInstance()
POOL.setMaxThreadCount(4)


# 发起一次后台调用，四个回调都可选
def run_async(
    fn: Callable[..., Any],
    *args: Any,
    on_ok: Callable[[Any], None] | None = None,
    on_error: Callable[[str, str], None] | None = None,
    on_start: Callable[[], None] | None = None,
    on_finish: Callable[[], None] | None = None,
    **kwargs: Any
) -> Worker:
    worker = Worker(fn, *args, **kwargs)
    if on_start is not None:
        worker.signals.started.connect(on_start)
    if on_ok is not None:
        worker.signals.succeeded.connect(on_ok)
    if on_error is not None:
        worker.signals.failed.connect(on_error)
    if on_finish is not None:
        worker.signals.finished.connect(on_finish)
    POOL.start(worker)
    return worker