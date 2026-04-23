from __future__ import annotations

import traceback
from inspect import signature
from typing import Callable, Generic, TypeVar

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

T = TypeVar("T")


class Worker(QObject, Generic[T]):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn: Callable[[], T]) -> None:
        super().__init__()
        self.fn = fn
        # Background tasks may optionally accept a progress callback as their single argument.
        self.accepts_progress = len(signature(fn).parameters) > 0

    @Slot()
    def run(self) -> None:
        try:
            if self.accepts_progress:
                self.finished.emit(self.fn(self.progress.emit))  # type: ignore[misc]
            else:
                self.finished.emit(self.fn())
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")


class _CallbackProxy(QObject):
    def __init__(
        self,
        on_success: Callable[[object], None],
        on_error: Callable[[str], None],
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_success = on_success
        self._on_error = on_error
        self._on_progress = on_progress

    @Slot(object)
    def handle_success(self, payload: object) -> None:
        self._on_success(payload)

    @Slot(str)
    def handle_error(self, message: str) -> None:
        self._on_error(message)

    @Slot(str)
    def handle_progress(self, message: str) -> None:
        if self._on_progress:
            self._on_progress(message)


def run_in_thread(
    fn: Callable[..., T],
    on_success: Callable[[T], None],
    on_error: Callable[[str], None],
    on_progress: Callable[[str], None] | None = None,
) -> QThread:
    thread = QThread()
    worker = Worker(fn)
    proxy = _CallbackProxy(on_success, on_error, on_progress)
    thread.worker = worker  # type: ignore[attr-defined]
    thread.callback_proxy = proxy  # type: ignore[attr-defined]
    worker.moveToThread(thread)
    # All UI updates must happen through signals; the worker itself never touches widgets directly.
    thread.started.connect(worker.run)
    worker.finished.connect(proxy.handle_success, Qt.ConnectionType.QueuedConnection)
    worker.failed.connect(proxy.handle_error, Qt.ConnectionType.QueuedConnection)
    if on_progress:
        worker.progress.connect(proxy.handle_progress, Qt.ConnectionType.QueuedConnection)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    proxy.destroyed.connect(lambda: None)
    thread.finished.connect(proxy.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
