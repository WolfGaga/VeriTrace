"""Failure diagnostics must reach the console, files, and research clients."""

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import threading

import httpx
import pytest

from veritrace.app import create_app, parse_arguments
from veritrace.agent.runtime.events import ResearchEvent
from veritrace.agent.runtime.scheduler import TaskScheduler


@pytest.fixture(autouse=True)
def isolated_sse_lifetime(monkeypatch):
    from sse_starlette.sse import AppStatus

    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)


def settings(*extra):
    return parse_arguments(["--llm-provider", "fixture", "--llm-model", "fixture", *extra])


async def solve(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/solve", json={"inputs": "Offline question", "session_id": 42})
    assert response.status_code == 200
    rows = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    return [row for row in rows if row["type"] not in ("research.run", "research.recovery")]


def test_task_exception_logs_full_traceback_and_keeps_planner_feedback(caplog):
    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            raise ValueError("nested search failure")

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher)
    try:
        scheduler.add_task(task_name="task_search", node_content="Question", cognitive_edge_id="r1", cognitive_target_id="e1")
        (event,) = scheduler.iter_completed(timeout=2, poll_timeout=0.01)
        assert event.type == "task.failed"
        assert event.data["task"]["result"]["answer"] == "[Search failed: ValueError: nested search failure]"
        (failure,) = [record for record in caplog.records if record.levelno >= logging.ERROR]
        assert "task_search" in failure.getMessage()
        assert failure.exc_info[0] is ValueError
        assert "search_and_summarize" in caplog.text
        assert "nested search failure" in caplog.text
    finally:
        scheduler.close()


@pytest.mark.parametrize("fails", [False, True])
def test_task_lifecycle_logs_terminal_status_with_request_context(caplog, fails):
    caplog.set_level(logging.INFO)

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            if fails:
                raise ValueError("controlled failure")
            return {"content": "Controlled finding"}

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher, session_id=42)
    try:
        scheduler.add_task(task_name="task_lifecycle", node_content="Question", cognitive_edge_id="r1", cognitive_target_id="e1")
        (event,) = scheduler.iter_completed(timeout=2, poll_timeout=0.01)
        assert event.type == ("task.failed" if fails else "task.completed")
        messages = [record.getMessage() for record in caplog.records]
        start, = [text for text in messages if "Task 'task_lifecycle' started" in text]
        end, = [text for text in messages if "Task 'task_lifecycle' ended" in text]
        assert "[Research:42]" in start and "[Research:42]" in end
        assert f"status={'failed' if fails else 'completed'}" in end
        assert float(end.split("elapsed=", 1)[1].removesuffix("s")) >= 0
    finally:
        scheduler.close()


def test_late_task_result_is_logged_as_cancelled_not_completed(caplog):
    caplog.set_level(logging.INFO)
    entered, release = threading.Event(), threading.Event()

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            entered.set()
            assert release.wait(2)
            return {"content": "Late finding"}

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher, session_id=43)
    try:
        scheduler.add_task(task_name="task_cancelled", node_content="Question", cognitive_edge_id="r1", cognitive_target_id="e1")
        assert entered.wait(1)
        scheduler.cancel()
        release.set()
        scheduler._executor.shutdown(wait=True)
        assert scheduler.task("task_cancelled")["status"] == "cancelled"
        assert list(scheduler.iter_completed()) == []
        end, = [record.getMessage() for record in caplog.records if "Task 'task_cancelled' ended" in record.getMessage()]
        assert "status=cancelled" in end
    finally:
        release.set()
        scheduler.close()


def test_stream_disconnect_logs_no_stop_request_and_preserves_checkpoint(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    entered, release = threading.Event(), threading.Event()

    class Agent:
        def cancel(self):
            pass

        def latest_checkpoint(self):
            return {"schema_version": 1, "question": "Question", "tasks": {}}

        def __call__(self, *args, **kwargs):
            yield ResearchEvent("research.started")
            yield ResearchEvent("research.progress", {"stage": "reading"})
            entered.set()
            release.wait(3)

    app = create_app(settings(), database_path=tmp_path / "disconnect.sqlite", agent_factory=lambda **kwargs: Agent())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/solve", json={"inputs": "Question"}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                with app.state.research_runs.database._connection() as conn:
                    run_id = conn.execute("SELECT id FROM research_runs").fetchone()[0]
                saved = (await client.get(f"/api/research/{run_id}")).json()
                assert saved["recovery"]["status"] == "cancelled"
                assert saved["recovery"]["can_continue"]
                assert saved["error"] is None
            finally:
                release.set()

    asyncio.run(scenario())
    assert "Stream consumer cancelled; stop_requested=False" in caplog.text
    assert "Research request failed" not in caplog.text


def test_conversation_logs_only_committed_changes(tmp_path, caplog):
    from veritrace.conversation_db import ConversationStore

    caplog.set_level(logging.INFO)
    store = ConversationStore(tmp_path / "history.sqlite")
    store.initialize()
    store.initialize()
    initialized = [record.getMessage() for record in caplog.records if "[ConversationDB] Initialized" in record.getMessage()]
    assert len(initialized) == 1 and str(store.path.resolve()) in initialized[0]
    assert "schema_migrated=False" in initialized[0]
    store.create_conversation("retained", data={"private": "DO_NOT_LOG_CONTENT"})
    with pytest.raises(sqlite3.IntegrityError):
        store.create_conversation("retained")
    assert not store.restore_conversation("retained")
    assert not store.delete_conversation("missing")
    assert store.delete_conversation("retained")
    assert not store.delete_conversation("retained")
    assert store.restore_conversation("retained")
    assert store.permanently_delete_conversation("retained")
    assert not store.permanently_delete_conversation("retained")
    store.create_conversation("trash")
    assert store.delete_conversation("trash")
    assert store.permanently_delete_all_deleted() == 1
    messages = [record.getMessage() for record in caplog.records]
    for action in ["Created", "Soft deleted", "Restored", "Permanently deleted"]:
        assert sum(action + " conversation; id=retained" in text for text in messages) == 1
    assert any("Permanently deleted trash; rows=1" in text for text in messages)
    assert "DO_NOT_LOG_CONTENT" not in caplog.text


def test_database_migration_log_follows_successful_schema_change(tmp_path, caplog):
    from veritrace.conversation_db import ConversationStore

    caplog.set_level(logging.INFO)
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT, created_at INTEGER, updated_at INTEGER, data TEXT)")
    store = ConversationStore(path)
    store.initialize()
    with sqlite3.connect(path) as connection:
        assert "deleted_at" in {column[1] for column in connection.execute("PRAGMA table_info(conversations)")}
    assert "schema_migrated=True" in caplog.text


def test_search_wait_timeout_identifies_pending_tasks(caplog, monkeypatch):
    from itertools import count
    from types import SimpleNamespace
    from veritrace.agent.runtime import scheduler as scheduler_module

    caplog.set_level(logging.INFO)
    ticks = count(step=0.01)
    monkeypatch.setattr(scheduler_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    release = threading.Event()

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            assert release.wait(2)
            return {"content": "Completed"}

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher)
    try:
        scheduler.add_task(task_name="task_slow", node_content="Question", cognitive_edge_id="r1", cognitive_target_id="e1")
        assert list(scheduler.iter_completed(timeout=0.03, poll_timeout=0.005, progress_log_interval=0.005)) == []
        assert "stopped reporting progress" in caplog.text
        assert "task_slow" in caplog.text
        assert "Waiting" in caplog.text
    finally:
        release.set()
        scheduler.close()
        scheduler._executor.shutdown(wait=True)


def test_direct_engine_failure_logs_and_still_propagates(monkeypatch, caplog):
    from veritrace.agent.veritrace_agent import VeritraceAgent

    agent = object.__new__(VeritraceAgent)
    agent._run_lock = threading.Lock()
    agent._cancelled = threading.Event()
    resets = []
    from types import SimpleNamespace

    agent.interpreter = SimpleNamespace(reset=lambda: resets.append(True))

    def fail(question, session_id):
        yield ResearchEvent("research.started")
        raise RuntimeError("engine failure independent of HTTP")

    monkeypatch.setattr(agent, "_research", fail)
    with pytest.raises(RuntimeError, match="engine failure independent of HTTP"):
        list(agent("Offline question", session_id=42))
    assert "[Research:42] Unhandled research engine error" in caplog.text
    assert any(record.exc_info for record in caplog.records)
    assert resets == [True]
    assert not agent._run_lock.locked()


@pytest.mark.parametrize("async_mode", [False, True])
def test_api_worker_failure_logs_traceback_and_sends_terminal_event(tmp_path, caplog, async_mode):
    class Agent:
        def cancel(self):
            pass

        def __call__(self, *args, **kwargs):
            def sync_events():
                yield ResearchEvent("research.started")
                raise LookupError("nested provider failure")

            async def async_events():
                for event in sync_events():
                    yield event

            return async_events() if async_mode else sync_events()

    caplog.set_level(logging.INFO)
    app = create_app(settings(*(["--asy"] if async_mode else [])), database_path=tmp_path / "history.sqlite",
                     agent_factory=lambda **kwargs: Agent())
    rows = asyncio.run(solve(app))
    assert [row["type"] for row in rows] == ["research.started", "research.failed", "usage.updated"]
    assert rows[1]["data"]["error"] == "nested provider failure"
    assert "[Research:42] Started" in caplog.text
    assert "last_event=research.started" in caplog.text
    assert "status=failed" in caplog.text
    assert "sync_events" in caplog.text
    assert any(record.exc_info and record.exc_info[0] is LookupError for record in caplog.records)


def test_api_failed_event_is_also_reported_in_backend_log(tmp_path, caplog):
    class Agent:
        def cancel(self):
            pass

        def __call__(self, *args, **kwargs):
            yield ResearchEvent("research.failed", {"error": "reported failure without an exception"})

    app = create_app(settings(), database_path=tmp_path / "history.sqlite", agent_factory=lambda **kwargs: Agent())
    rows = asyncio.run(solve(app))
    assert rows[0]["type"] == "research.failed"
    assert "reported failure without an exception" in caplog.text
    assert "[Research:42]" in caplog.text


def test_idle_request_logs_progress_and_stops_monitor_on_completion(tmp_path, caplog):
    async def scenario():
        waiting_logged = asyncio.Event()

        class Notify(logging.Handler):
            def emit(self, record):
                if "[Research:42] Waiting;" in record.getMessage():
                    waiting_logged.set()

        class Agent:
            def cancel(self):
                pass

            async def __call__(self, *args, **kwargs):
                yield ResearchEvent("planner.thinking")
                await asyncio.wait_for(waiting_logged.wait(), timeout=2)
                yield ResearchEvent("research.completed", {"article": "Offline report"})

        notify = Notify()
        logging.getLogger().addHandler(notify)
        try:
            app = create_app(settings("--asy", "--progress-log-interval", "0.01"),
                             database_path=tmp_path / "history.sqlite", agent_factory=lambda **kwargs: Agent())
            rows = await solve(app)
            assert [row["type"] for row in rows] == ["planner.thinking", "research.completed", "usage.updated"]
            assert not any(task.get_coro().__name__ == "_log_request_progress" for task in asyncio.all_tasks())
        finally:
            logging.getLogger().removeHandler(notify)

    caplog.set_level(logging.INFO)
    asyncio.run(scenario())
    assert "Waiting; last_event=planner.thinking" in caplog.text
    assert "status=completed" in caplog.text


def test_cleanup_failure_is_logged_without_losing_usage_or_report(tmp_path, caplog):
    closed = []

    class Agent:
        def cancel(self):
            raise RuntimeError("cancellation cleanup failed")

        async def __call__(self, *args, **kwargs):
            try:
                yield ResearchEvent("research.completed", {"article": "Offline report"})
            finally:
                closed.append(True)

    app = create_app(settings("--asy"), database_path=tmp_path / "history.sqlite", agent_factory=lambda **kwargs: Agent())
    rows = asyncio.run(solve(app))
    assert [row["type"] for row in rows] == ["research.completed", "usage.updated"]
    assert rows[0]["data"]["article"] == "Offline report"
    assert closed == [True]
    assert "Agent cancellation failed" in caplog.text
    assert any(record.exc_info for record in caplog.records)


def test_usage_failure_is_logged_without_discarding_completed_report(tmp_path, caplog, monkeypatch):
    class Agent:
        def cancel(self):
            pass

        def __call__(self, *args, **kwargs):
            yield ResearchEvent("research.completed", {"article": "Offline report"})

    def unavailable():
        raise RuntimeError("usage serialization failed")

    monkeypatch.setattr("veritrace.agent.usage_tracker.get_usage_summary", unavailable)
    app = create_app(settings(), database_path=tmp_path / "history.sqlite", agent_factory=lambda **kwargs: Agent())
    (report,) = asyncio.run(solve(app))
    assert report["type"] == "research.completed"
    assert report["data"]["article"] == "Offline report"
    assert "Failed to send usage stats" in caplog.text
    assert "usage serialization failed" in caplog.text
    assert any(record.exc_info for record in caplog.records)


def run_python(code, tmp_path):
    project = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(project), PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=15)


def test_console_file_and_uvicorn_logs_share_utf8_tracebacks(tmp_path):
    result = run_python('''
import json, logging, logging.config
from veritrace.logging_config import build_log_config
config = json.loads(json.dumps(build_log_config("nested/backend.log")))
logging.config.dictConfig(config)
logging.getLogger("uvicorn.error").info("startup marker")
try:
    raise ValueError("diagnostic unicode: \\u4e2d\\u6587")
except ValueError:
    logging.exception("backend failure marker")
''', tmp_path)
    assert result.returncode == 0, result.stderr
    file_text = (tmp_path / "nested/backend.log").read_text(encoding="utf-8")
    for text in (result.stderr, file_text):
        assert text.count("startup marker") == 1
        assert text.count("backend failure marker") == 1
        assert "Traceback (most recent call last)" in text
        assert "diagnostic unicode: 中文" in text


def test_file_logging_rotates_and_keeps_latest_record(tmp_path):
    result = run_python('''
import logging, logging.config
from veritrace.logging_config import build_log_config
config = build_log_config("backend.log")
config["handlers"]["file"]["maxBytes"] = 300
logging.config.dictConfig(config)
for index in range(8):
    logging.error("entry %s %s", index, "x" * 150)
''', tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "backend.log.1").is_file()
    assert "entry 7" in (tmp_path / "backend.log").read_text(encoding="utf-8")
    assert len(list(tmp_path.glob("backend.log.*"))) <= 3


@pytest.mark.parametrize("default_log", [False, True])
def test_batch_runner_creates_log_directory_and_keeps_full_error(tmp_path, default_log):
    result = run_python(f'''
from pathlib import Path
from benchmark import run_terminal
run_terminal.VERITRACE_DIR = Path.cwd() / "clean-project"
run_terminal.run_single_query = lambda *a, **k: {{"status": "error", "article": "", "error": "x" * 1200 + "END_OF_DIAGNOSTIC"}}
args = ["--query", "Offline question", "--output", "result.jsonl"]
if not {default_log!r}:
    args.extend(["--log-file", "nested/runner.log"])
raise SystemExit(run_terminal.main(args))
''', tmp_path)
    assert result.returncode == 1, result.stderr
    log_file = next((tmp_path / "clean-project/benchmark/logs").glob("*.log")) if default_log else tmp_path / "nested/runner.log"
    assert "END_OF_DIAGNOSTIC" in log_file.read_text(encoding="utf-8")
    assert "END_OF_DIAGNOSTIC" in result.stdout
    record = json.loads((tmp_path / "result.jsonl").read_text(encoding="utf-8"))
    assert record["error"].endswith("END_OF_DIAGNOSTIC")
