"""Progress remains observable while worker/model calls are blocked."""

import asyncio
import threading

from veritrace.agent.concurrency import ThreadPoolExecutor
from veritrace.agent.runtime.async_stream import stream_in_thread
from veritrace.agent.runtime.events import ResearchEvent
from veritrace.agent.runtime.progress import progress_scope, report_progress, task_progress


def test_progress_arrives_before_a_blocking_stage_finishes():
    release = threading.Event()

    def source():
        report_progress("planning_outline")
        assert release.wait(2)
        yield ResearchEvent("research.completed", {"article": "Unchanged report"})

    async def scenario():
        events = stream_in_thread(source, release.set)
        try:
            first = await asyncio.wait_for(anext(events), timeout=1)
            assert first.type == "research.progress"
            assert first.data == {"stage": "planning_outline"}
            release.set()
            remaining = [event async for event in events]
            assert remaining[-1].data["article"] == "Unchanged report"
        finally:
            release.set()
            await events.aclose()

    asyncio.run(scenario())


def test_heartbeat_reports_waiting_without_finishing_or_restarting_research():
    release = threading.Event()

    def source():
        yield ResearchEvent("research.started")
        assert release.wait(2)
        yield ResearchEvent("research.completed", {"article": "Report"})

    async def scenario():
        events = stream_in_thread(source, release.set, heartbeat_interval=0.01)
        try:
            async def started():
                while (event := await anext(events)).type == "research.heartbeat":
                    pass
                return event

            assert (await asyncio.wait_for(started(), timeout=1)).type == "research.started"
            heartbeat = await asyncio.wait_for(anext(events), timeout=1)
            assert heartbeat.type == "research.heartbeat"
            assert heartbeat.data["waiting_seconds"] >= 0
            release.set()
            assert [event.type async for event in events if event.type != "research.heartbeat"] == ["research.completed"]
        finally:
            release.set()
            await events.aclose()

    asyncio.run(scenario())


def test_parallel_task_progress_preserves_request_and_task_context():
    barrier = threading.Barrier(2)

    def source(name):
        def worker():
            with task_progress(name):
                barrier.wait(timeout=2)
                report_progress("reading", title=name)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(worker).result(timeout=2)
        yield ResearchEvent("research.completed", {"article": name})

    async def collect(name):
        return [event async for event in stream_in_thread(lambda: source(name), lambda: None)]

    async def scenario():
        results = await asyncio.gather(collect("alpha"), collect("beta"))
        for name, events in zip(("alpha", "beta"), results):
            (progress,) = [event for event in events if event.type == "research.progress"]
            assert (progress.task_id, progress.data["title"]) == (name, name)

    asyncio.run(scenario())


def test_missing_or_failed_progress_observer_does_not_change_research(caplog):
    report_progress("planning")

    def fail(event):
        raise RuntimeError("observer disconnected")

    with progress_scope(fail):
        report_progress("planning")
    assert "Could not publish research progress" in caplog.text


def test_progress_backpressure_does_not_block_work_or_drop_the_final_result():
    finished = threading.Event()

    def source():
        for index in range(200):
            report_progress("reading", page_index=index)
        finished.set()
        yield ResearchEvent("research.completed", {"article": "Complete report"})

    async def scenario():
        events = stream_in_thread(source, lambda: None)
        try:
            assert (await anext(events)).type == "research.progress"
            assert await asyncio.to_thread(finished.wait, 1), "A paused progress consumer blocked the research worker"
            remaining = [event async for event in events]
            assert remaining[-1].type == "research.completed"
            assert remaining[-1].data["article"] == "Complete report"
        finally:
            await events.aclose()

    asyncio.run(scenario())
