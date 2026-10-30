"""Thread pools that retain request-local context."""

from concurrent.futures import CancelledError, ThreadPoolExecutor as _ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context


_cancellation = ContextVar("veritrace_cancellation", default=None)


@contextmanager
def cancellation_scope(event):
    token = _cancellation.set(event)
    try:
        yield
    finally:
        _cancellation.reset(token)


def check_cancelled():
    event = _cancellation.get()
    if event is not None and event.is_set():
        raise CancelledError("Research cancelled")


class ThreadPoolExecutor(_ThreadPoolExecutor):
    def submit(self, fn, /, *args, **kwargs):
        return super().submit(copy_context().run, fn, *args, **kwargs)
