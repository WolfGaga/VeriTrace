"""Search wait budgets exclude time spent processing delivered events."""

import queue
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest

from veritrace.agent.runtime import scheduler as scheduler_module
from veritrace.agent.runtime.events import ResearchEvent
from veritrace.agent.runtime.scheduler import TaskScheduler


class ScriptedQueue:
    def __init__(self, clock, scheduled=()):
        self.clock = clock
        self.scheduled = list(scheduled)
        self.waits = []

    def get_nowait(self):
        if self.scheduled and self.scheduled[0][0] <= self.clock.now:
            return self.scheduled.pop(0)[1]
        raise queue.Empty

    def get(self, timeout):
        self.waits.append(timeout)
        deadline = self.clock.now + timeout
        if self.scheduled and self.scheduled[0][0] <= deadline:
            self.clock.now, event = self.scheduled.pop(0)
            return event
        self.clock.now = deadline
        raise queue.Empty

    def put(self, event):
        self.scheduled.append((self.clock.now, event))
        self.scheduled.sort(key=lambda item: item[0])


@pytest.fixture
def waiting_scheduler(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(scheduler_module.time, "monotonic", lambda: clock.now)
    scheduler = TaskScheduler(llm=object())
    scheduler._pending.add("unfinished")
    channel = ScriptedQueue(clock)
    scheduler._completed = channel
    try:
        yield scheduler, clock, channel
    finally:
        scheduler.close()


def test_search_wait_allows_the_full_default_poll_budget(waiting_scheduler):
    scheduler, clock, channel = waiting_scheduler
    assert list(scheduler.iter_completed()) == []
    assert channel.waits == [10.0] * 91
    assert clock.now == 910.0
    assert scheduler.pending_count == 1


@pytest.mark.parametrize("kind", ["task.completed", "task.failed"])
def test_completion_resets_wait_budget_and_consumer_pause_does_not_consume_it(waiting_scheduler, kind):
    scheduler, clock, channel = waiting_scheduler
    event = ResearchEvent(kind, {"task": {"id": "first"}}, "first")
    channel.scheduled = [(905.0, event)]
    stream = scheduler.iter_completed()
    assert next(stream) is event
    assert clock.now == 905.0
    clock.now += 3600.0
    received_at = clock.now
    polls_before = len(channel.waits)
    assert list(stream) == []
    assert channel.waits[polls_before:] == [10.0] * 91
    assert clock.now - received_at == 910.0


def test_explicit_wait_budget_limits_polling_without_charging_consumer_time(waiting_scheduler):
    scheduler, clock, channel = waiting_scheduler
    event = ResearchEvent("task.completed", {"task": {"id": "first"}}, "first")
    channel.scheduled = [(0.5, event)]
    stream = scheduler.iter_completed(timeout=2.5, poll_timeout=1.0)
    assert next(stream) is event
    clock.now += 100.0
    received_at = clock.now
    polls_before = len(channel.waits)
    assert list(stream) == []
    assert channel.waits[polls_before:] == [1.0, 1.0, 0.5]
    assert clock.now - received_at == 2.5


def test_obsolete_completion_does_not_reset_search_wait_budget(waiting_scheduler):
    scheduler, clock, channel = waiting_scheduler
    channel.scheduled = [(900.0, ("removed", object(), {}))]
    assert list(scheduler.iter_completed()) == []
    assert clock.now == 920.0


def test_cancellation_during_consumer_pause_finishes_without_more_waiting(waiting_scheduler):
    scheduler, clock, channel = waiting_scheduler
    event = ResearchEvent("task.completed", {"task": {"id": "first"}}, "first")
    channel.scheduled = [(0.0, event)]
    stream = scheduler.iter_completed()
    assert next(stream) is event
    scheduler.cancel()
    assert list(stream) == []
    assert channel.waits == []
    assert clock.now == 0.0


def test_zero_wait_budget_still_delivers_already_queued_results(waiting_scheduler):
    scheduler, clock, channel = waiting_scheduler
    event = ResearchEvent("task.completed", {"task": {"id": "first"}}, "first")
    channel.scheduled = [(0.0, event)]
    assert list(scheduler.iter_completed(timeout=0.0)) == [event]
    assert channel.waits == []
    assert clock.now == 0.0


@pytest.mark.parametrize("poll_timeout", [0, -1])
def test_invalid_poll_interval_fails_before_entering_a_wait_loop(waiting_scheduler, monkeypatch, poll_timeout):
    scheduler, _, channel = waiting_scheduler
    polls = []

    def reject_poll(**kwargs):
        polls.append(kwargs)
        raise RuntimeError("Invalid polling must not reach the queue")

    monkeypatch.setattr(channel, "get", reject_poll)
    with pytest.raises(ValueError, match="poll_timeout must be positive"):
        list(scheduler.iter_completed(timeout=1, poll_timeout=poll_timeout))
    assert not polls


@pytest.mark.parametrize("method", ["get_nowait", "get"])
def test_queue_failure_keeps_delivered_results_and_logs_the_error(waiting_scheduler, monkeypatch, caplog, method):
    scheduler, _, channel = waiting_scheduler
    event = ResearchEvent("task.completed", {"task": {"id": "first"}}, "first")
    channel.scheduled = [(0.0, event)]
    stream = scheduler.iter_completed()
    assert next(stream) is event

    def fail(*args, **kwargs):
        raise OSError("Queue unavailable")

    monkeypatch.setattr(channel, method, fail)
    assert list(stream) == []
    assert scheduler.pending_count == 1
    assert "Queue unavailable" in caplog.text
    assert "continuing with collected evidence" in caplog.text
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize("method", ["get_nowait", "get"])
@pytest.mark.parametrize("error", [CancelledError, KeyboardInterrupt])
def test_queue_control_signals_propagate(waiting_scheduler, monkeypatch, method, error):
    scheduler, _, channel = waiting_scheduler

    def fail(*args, **kwargs):
        raise error()

    monkeypatch.setattr(channel, method, fail)
    with pytest.raises(error):
        list(scheduler.iter_completed())


def test_consumer_exception_is_not_handled_as_a_queue_failure(waiting_scheduler, caplog):
    scheduler, _, channel = waiting_scheduler
    event = ResearchEvent("task.completed", {"task": {"id": "first"}}, "first")
    channel.scheduled = [(0.0, event)]
    stream = scheduler.iter_completed()
    assert next(stream) is event
    with pytest.raises(RuntimeError, match="Consumer failed"):
        stream.throw(RuntimeError("Consumer failed"))
    assert "Unable to receive search task results" not in caplog.text
