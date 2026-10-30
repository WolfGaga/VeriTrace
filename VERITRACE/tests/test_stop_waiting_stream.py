"""Stopping a waiting provider does not require its in-flight call to finish."""
import asyncio
import json
import logging
import threading

import httpx

from veritrace.app import create_app, parse_arguments
from veritrace.agent.runtime.events import ResearchEvent


def test_stop_returns_while_provider_is_still_blocked(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    from sse_starlette.sse import AppStatus
    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    entered, release = threading.Event(), threading.Event()

    class Agent:
        def cancel(self):
            pass

        def latest_checkpoint(self):
            return {"schema_version": 1, "question": "Question", "tasks": {}}

        def __call__(self, *args, **kwargs):
            yield ResearchEvent("research.started")
            entered.set()
            release.wait(3)
            yield ResearchEvent("research.completed", {"article": "Late report must not replace stopped work"})

    app = create_app(parse_arguments([]), database_path=tmp_path / "run.sqlite", agent_factory=lambda **kwargs: Agent())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/solve", json={"inputs": "Question"}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                with app.state.research_runs.database._connection() as conn:
                    run_id = conn.execute("SELECT id FROM research_runs").fetchone()[0]
                assert (await client.post(f"/api/research/{run_id}/stop")).status_code == 200
                response = await asyncio.wait_for(pending, timeout=0.8)
                assert response.headers["x-research-run-id"] == run_id
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                assert "research.cancelled" in [event["type"] for event in events]
                assert "research.completed" not in [event["type"] for event in events]
                assert not release.is_set()
                saved = (await client.get(f"/api/research/{run_id}")).json()
                assert saved["recovery"]["status"] == "cancelled"
                assert saved["recovery"]["can_continue"]
            finally:
                release.set()
    asyncio.run(scenario())
    assert "Stop requested; attempt=1" in caplog.text
