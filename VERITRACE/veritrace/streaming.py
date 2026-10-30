"""Stream a synchronous research request over an asynchronous transport."""

from contextlib import aclosing
import asyncio


async def stop_aware_events(events, stop):
    """Wake a waiting stream when stopped, without waiting for provider HTTP calls."""
    stopped = asyncio.create_task(stop.wait())
    reading = None
    terminal = False
    try:
        while True:
            reading = asyncio.create_task(anext(events))
            ready, _ = await asyncio.wait((reading, stopped), return_when=asyncio.FIRST_COMPLETED)
            if reading in ready:
                try:
                    event = reading.result()
                except StopAsyncIteration:
                    return
                kind = event["type"] if isinstance(event, dict) else event.type
                terminal = kind in ("research.completed", "research.failed", "research.cancelled")
                yield event
                if terminal and stopped.done():
                    return
            else:
                if not terminal:
                    yield {"schema_version": 2, "type": "research.cancelled", "data": {}}
                return
    finally:
        stopped.cancel()
        if reading is not None and not reading.done():
            reading.cancel()
        await asyncio.gather(*(task for task in (stopped, reading) if task is not None), return_exceptions=True)
        if hasattr(events, "aclose"):
            await events.aclose()


async def stream_events(agent, question, session_id=0):
    from .agent.runtime.async_stream import stream_in_thread

    events = stream_in_thread(
        lambda: iter(agent(question, session_id=session_id)),
        getattr(agent, "cancel", lambda: None),
    )
    async with aclosing(events):
        async for event in events:
            yield event
