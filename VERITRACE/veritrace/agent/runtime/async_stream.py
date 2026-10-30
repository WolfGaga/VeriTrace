"""Bridge a synchronous research iterator into an async consumer."""

import asyncio
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import copy_context
from .events import ResearchEvent
from .progress import progress_scope


async def stream_in_thread(factory, cancel, *, heartbeat_interval=15.0):
    loop = asyncio.get_running_loop()
    events = asyncio.Queue(maxsize=64)
    stopped = threading.Event()
    last_activity = time.monotonic()

    def publish(kind, value=None):
        nonlocal last_activity
        if stopped.is_set() or loop.is_closed():
            return False
        delivery = events.put((kind, value))
        try:
            pending = asyncio.run_coroutine_threadsafe(delivery, loop)
        except RuntimeError:
            delivery.close()
            return False
        while not stopped.is_set() and not loop.is_closed():
            try:
                pending.result(timeout=0.05)
                if kind == "event":
                    last_activity = time.monotonic()
                return True
            except FutureTimeout:
                continue
            except Exception:
                return False
        pending.cancel()
        return False

    def publish_progress(event):
        def deliver():
            nonlocal last_activity
            if stopped.is_set():
                return
            try:
                events.put_nowait(("event", event))
                last_activity = time.monotonic()
            except asyncio.QueueFull:
                pass  # Progress must not block model or search work behind a slow client.

        if not stopped.is_set() and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(deliver)
            except RuntimeError:
                pass

    def produce():
        iterator = None
        try:
            with progress_scope(lambda event: publish_progress(event) if event.type == "research.progress" else publish("event", event)):
                iterator = factory()
                for event in iterator:
                    if stopped.is_set():
                        break
                    if not publish("event", event):
                        break
        except BaseException as exc:
            publish("error", exc)
        finally:
            try:
                if iterator is not None:
                    iterator.close()
            except BaseException as exc:
                publish("error", exc)
            finally:
                publish("done")

    context = copy_context()
    worker = threading.Thread(target=lambda: context.run(produce), name="veritrace-stream", daemon=True)
    worker.start()
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(events.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield ResearchEvent("research.heartbeat", {"waiting_seconds": round(time.monotonic() - last_activity, 1)})
                continue
            if kind == "done":
                return
            if kind == "error":
                raise value
            yield value
    finally:
        stopped.set()
        cancel()
        # Active HTTP calls finish under their configured timeout; their results are discarded.
        await asyncio.to_thread(worker.join, 0.1)
