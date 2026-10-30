"""Request-local progress events without changing research inputs or results."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from .events import ResearchEvent


_sink = ContextVar("research_progress_sink", default=None)
_task = ContextVar("research_progress_task", default=None)


@contextmanager
def progress_scope(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def task_progress(task_id):
    token = _task.set(task_id)
    try:
        yield
    finally:
        _task.reset(token)


def report_progress(stage, **details):
    publish_research_event("research.progress", {"stage": stage, **details})


def publish_research_event(kind, data):
    sink = _sink.get()
    if sink is not None:
        try:
            sink(ResearchEvent(kind, data, _task.get()))
        except Exception:
            logging.warning("Could not publish research progress", exc_info=True)
