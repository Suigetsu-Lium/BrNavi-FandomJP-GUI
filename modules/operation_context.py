"""Shared execution context for GUI and command-line operations."""

import threading


class OperationCancelled(Exception):
    """Raised when an operation reaches a safe cancellation point."""


class OperationContext:
    def __init__(self, cancel_event=None, on_log=None, on_progress=None):
        self.cancel_event = cancel_event or threading.Event()
        self._on_log = on_log
        self._on_progress = on_progress

    def log(self, message="", level="info"):
        if self._on_log:
            self._on_log(str(message), level)

    def progress(self, current=None, total=None, message=""):
        if self._on_progress:
            self._on_progress(current, total, str(message))

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise OperationCancelled("処理は利用者によって停止されました。")
