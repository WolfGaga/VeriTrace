import asyncio
import json
import logging
import sqlite3
import threading

import httpx
import pytest

from veritrace.app import GenerationParams, create_app, parse_arguments
from veritrace.agent.concurrency import ThreadPoolExecutor
from veritrace.agent.runtime.events import ResearchEvent
from veritrace.agent.usage_tracker import track_llm_usage, track_search_call
from veritrace.conversation_db import ConversationStore
from veritrace.streaming import stream_events


@pytest.fixture(autouse=True)
def isolate_sse_server_lifetime(monkeypatch):
    # sse-starlette 1.x keeps server shutdown state outside the ASGI application.
    from sse_starlette.sse import AppStatus

    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)


def settings(async_mode=False):
    args = ["--lang", "en", "--llm-provider", "fixture", "--llm-model", "fixture-model"]
    return parse_arguments(args + (["--asy"] if async_mode else []))


def test_research_request_has_one_report_pipeline():
    assert GenerationParams(inputs="Question", writer_mode="research").writer_mode == "research"
    with pytest.raises(ValueError, match="writer_mode"):
        GenerationParams(inputs="Question", writer_mode="skip")


def sse_events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    lines = response.text.splitlines()
    assert all(line == "event: response" for line in lines if line.startswith("event:"))
    rows = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    return [row for row in rows if row["type"] not in ("research.run", "research.recovery")]


async def request(app, method, url, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


def test_conversation_crud_restore_and_beacon_preserve_data(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("veritrace.conversation_db.time.time", lambda: clock[0])
    database = tmp_path / "history.sqlite"
    app = create_app(settings(), database_path=database)
    assert not database.exists()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            payload = {"id": "session-1", "title": "中文研究", "data": {"sessions": [{"answer": "Evidence [[1]]"}]}}
            created = await client.post("/api/conversations", json=payload)
            assert created.status_code == 200
            assert created.json()["data"] == payload["data"]
            assert (await client.post("/api/conversations", json=payload)).status_code == 409
            clock[0] += 2
            saved = await client.post(
                "/api/conversations/session-1",
                json={
                    "data": {"sessions": ["更新"]},
                    "skip_timestamp_update": True,
                },
            )
            assert saved.json() == {"success": True}
            fetched = (await client.get("/api/conversations/session-1")).json()
            assert fetched["updated_at"] == created.json()["updated_at"]
            assert fetched["data"] == {"sessions": ["更新"]}
            assert (await client.put("/api/conversations/session-1", json={"title": "Renamed"})).status_code == 200
            fetched = (await client.get("/api/conversations/session-1")).json()
            assert fetched["updated_at"] > created.json()["updated_at"]
            assert fetched["title"] == "Renamed"
            assert (await client.delete("/api/conversations/session-1")).json()["success"]
            assert (await client.get("/api/conversations")).json() == []
            trash = (await client.get("/api/admin/deleted-conversations")).json()
            assert [row["id"] for row in trash] == ["session-1"]
            stats = (await client.get("/api/conversations-stats")).json()
            assert (stats["conversation_count"], stats["deleted_count"]) == (0, 1)
            assert (await client.post("/api/admin/conversations/session-1/restore")).json()["success"]
            assert (await client.get("/api/conversations/session-1")).json()["data"] == {"sessions": ["更新"]}
            await client.delete("/api/conversations/session-1")
            assert (await client.delete("/api/admin/deleted-conversations/all")).json()["deleted_count"] == 1
            assert (await client.get("/api/conversations/session-1")).status_code == 404
            assert (await client.put("/api/conversations/missing", json={"title": "x"})).status_code == 404

    asyncio.run(scenario())


def test_database_upgrade_preserves_pre_trash_history(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT, created_at INTEGER, updated_at INTEGER, data TEXT)"
        )
        conn.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?)", ("old", "Title", 1, 2, '{"answer":"原文"}'))
    store = ConversationStore(path)
    assert store.get_conversation("old")["data"] == {"answer": "原文"}
    assert store.delete_conversation("old")
    assert store.restore_conversation("old")
    assert store.get_conversation("old")["created_at"] == 1


def test_eval_export_matches_query_and_writes_expected_jsonl(tmp_path):
    query = tmp_path / "queries.jsonl"
    query.write_text(json.dumps({"id": 7, "prompt": "问题"}, ensure_ascii=False) + "\n", encoding="utf-8")
    export_dir = tmp_path / "export"
    app = create_app(settings(), database_path=tmp_path / "history.sqlite", export_dir=export_dir, query_file=query)

    async def scenario():
        short = await request(app, "POST", "/api/export-eval", json={"question": "问题", "article": "short"})
        assert short.status_code == 400
        unknown = await request(
            app, "POST", "/api/export-eval", json={"question": "unknown", "article": "Report " * 20}
        )
        assert unknown.status_code == 400
        article = "Evidence supported finding [[1]]. 中文内容。 " * 3
        result = await request(app, "POST", "/api/export-eval", json={"question": " 问题 ", "article": article})
        assert result.json()["query_id"] == 7
        saved = json.loads((export_dir / "q7.jsonl").read_text(encoding="utf-8"))
        assert saved == {
            "id": 7,
            "prompt": "问题",
            "article": article.strip(),
            "cited_references": [],
            "status": "success",
            "error": None,
            "duration_seconds": 0,
        }

    asyncio.run(scenario())


@pytest.mark.parametrize("async_mode", [False, True])
def test_solve_stream_preserves_events_options_and_usage(tmp_path, async_mode):
    received = {}
    cancelled = threading.Event()

    def events(question, session_id):
        received.update(question=question, session_id=session_id)
        track_llm_usage(11, 3, "fixture")
        yield ResearchEvent("research.started")
        yield ResearchEvent(
            "research.completed",
            {
                "article": "中文报告 [[1]]",
                "ref2url": {1: {"url": "https://source.example"}},
                "tasks": {},
                "cognitive_graph": {},
            },
        )

    class Agent:
        def cancel(self):
            cancelled.set()

        def __call__(self, question, session_id=0):
            if not async_mode:
                return events(question, session_id)

            async def generate():
                for event in events(question, session_id):
                    yield event

            return generate()

    def factory(**kwargs):
        received["options"] = kwargs
        return Agent()

    app = create_app(settings(async_mode), database_path=tmp_path / "history.sqlite", agent_factory=factory)
    response = asyncio.run(
        request(
            app, "POST", "/solve", json={"inputs": "Question", "session_id": 42, "lang": "cn"}
        )
    )
    rows = sse_events(response)
    assert [row["type"] for row in rows] == ["research.started", "research.completed", "usage.updated"]
    assert all(row["schema_version"] == 2 for row in rows)
    assert rows[1]["data"]["article"] == "中文报告 [[1]]"
    assert rows[1]["data"]["ref2url"]["1"]["url"] == "https://source.example"
    assert rows[-1]["data"]["usage"]["tokens"] == {"input": 11, "output": 3, "total": 14}
    assert received["options"]["lang"] == "cn"
    assert set(received["options"]) == {
        "lang", "llm_provider", "llm_model", "search_engine", "use_async",
        "searcher_model", "reader_model", "prefilter_model", "quality_model", "cognitive_model", "writer_model",
    }
    assert received["options"]["use_async"] is async_mode
    assert (received["question"], received["session_id"]) == ("Question", 42)
    assert cancelled.is_set()


def test_solve_factory_failure_is_a_terminal_error_event(tmp_path):
    def factory(**kwargs):
        raise ValueError("invalid fixture configuration")

    app = create_app(settings(), database_path=tmp_path / "history.sqlite", agent_factory=factory)
    rows = sse_events(asyncio.run(request(app, "POST", "/solve", json={"inputs": "Question"})))
    assert [row["type"] for row in rows] == ["research.failed", "usage.updated"]
    assert rows[0]["data"]["error"] == "invalid fixture configuration"


def test_solve_rejects_unknown_request_options(tmp_path):
    app = create_app(settings(), database_path=tmp_path / "history.sqlite", agent_factory=lambda **kwargs: None)
    response = asyncio.run(request(app, "POST", "/solve", json={"inputs": "Question", "unsupported_option": True}))
    assert response.status_code == 422


def test_concurrent_requests_keep_nested_worker_usage_separate(tmp_path):
    barrier = threading.Barrier(2)

    class Agent:
        def cancel(self):
            pass

        def __call__(self, question, session_id=0):
            count = int(question)

            def work():
                barrier.wait(timeout=3)
                track_llm_usage(count, 1, f"model-{count}")
                track_search_call(f"query-{count}", count)

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(work).result(timeout=4)
            yield ResearchEvent("research.completed", {"article": question})

    app = create_app(settings(), database_path=tmp_path / "history.sqlite", agent_factory=lambda **kwargs: Agent())

    async def scenario():
        return await asyncio.gather(
            request(app, "POST", "/solve", json={"inputs": "10"}),
            request(app, "POST", "/solve", json={"inputs": "20"}),
        )

    responses = asyncio.run(scenario())
    for count, response in zip((10, 20), responses):
        rows = sse_events(response)
        assert not any(row["type"] == "research.failed" for row in rows)
        usage = rows[-1]["data"]["usage"]
        assert usage["tokens"] == {"input": count, "output": 1, "total": count + 1}
        assert usage["search"] == {"api_calls": 1, "results_count": count, "queries": [f"query-{count}"]}
        assert usage["llm"]["models"] == [f"model-{count}"]


def test_stream_consumer_close_cancels_and_closes_producer():
    cancelled = threading.Event()
    closed = threading.Event()

    class Agent:
        def cancel(self):
            cancelled.set()

        def __call__(self, question, session_id=0):
            try:
                yield ResearchEvent("research.started")
                cancelled.wait(timeout=2)
                yield ResearchEvent("research.completed")
            finally:
                closed.set()

    async def scenario():
        events = stream_events(Agent(), "Question")
        assert (await anext(events)).type == "research.started"
        await events.aclose()
        assert cancelled.is_set()
        assert await asyncio.to_thread(closed.wait, 2)

    asyncio.run(scenario())


def test_stream_propagates_worker_failure_and_closes_generator():
    closed = threading.Event()

    class Agent:
        def cancel(self):
            pass

        def __call__(self, question, session_id=0):
            try:
                yield ResearchEvent("research.started")
                raise ValueError("worker failed")
            finally:
                closed.set()

    async def scenario():
        with pytest.raises(ValueError, match="worker failed"):
            async for _ in stream_events(Agent(), "Question"):
                pass
        assert closed.is_set()

    asyncio.run(scenario())


def test_http_disconnect_cancels_research_and_closes_worker(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    cancelled = threading.Event()
    closed = threading.Event()

    class Agent:
        def cancel(self):
            cancelled.set()

        def __call__(self, question, session_id=0):
            try:
                yield ResearchEvent("research.started")
                cancelled.wait(timeout=2)
                yield ResearchEvent("research.completed", {"article": "Must not be delivered"})
            finally:
                closed.set()

    app = create_app(settings(), database_path=tmp_path / "history.sqlite", agent_factory=lambda **kwargs: Agent())

    async def scenario():
        first_chunk = asyncio.Event()
        body_sent = False
        chunks = []

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": b'{"inputs":"Question"}', "more_body": False}
            await first_chunk.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                chunks.append(message["body"])
                if b"research.started" in message["body"]:
                    first_chunk.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/solve",
            "raw_path": b"/solve",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=3)
        assert cancelled.is_set()
        assert await asyncio.to_thread(closed.wait, 2)
        assert b"research.started" in b"".join(chunks)
        assert b"research.completed" not in b"".join(chunks)

    asyncio.run(scenario())
    assert "Stream consumer cancelled; stop_requested=False" in caplog.text or "Client disconnected" in caplog.text
    assert "Finished; status=" in caplog.text
