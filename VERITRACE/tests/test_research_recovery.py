"""Recovery preserves finished work, isolates attempts, and survives API restart."""

import asyncio
from copy import deepcopy
import json
import threading

import httpx
import pytest

from veritrace.app import create_app, parse_arguments
from veritrace.research_runs import ResearchRunStore, RunSession
from veritrace.agent.runtime.events import ResearchEvent
from veritrace.agent.runtime.program import PlannerProgram
from veritrace.agent.runtime.scheduler import TaskScheduler
from veritrace.agent.writing import WritingPipeline
from test_shared_writing_pipeline import inputs, outline, insight


@pytest.fixture(autouse=True)
def isolated_sse(monkeypatch):
    from sse_starlette.sse import AppStatus
    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)


def test_report_resume_reuses_plans_and_completed_sections(scripted_llm):
    llm = scripted_llm([outline(2), insight(), insight(), "## First\nEvidence [[1]].", RuntimeError("writer down")])
    pipeline = WritingPipeline(llm, "fixture", outline_model=None, section_planner_model=None, section_writer_model=None)
    snapshots = []
    pipeline.on_checkpoint = snapshots.append
    graph, store, refs = inputs()
    with pytest.raises(RuntimeError, match="writer down"):
        pipeline.run("Question", graph, store, refs)
    saved = deepcopy(snapshots[-1])
    assert saved["section_texts"] == {"1": "## First\nEvidence [[1]]."}
    assert saved["planned_sections"] == ["1", "2"]
    resumed_llm = scripted_llm(["## Second\nMore evidence [[1]].", "Summary"])
    resumed = WritingPipeline(resumed_llm, "fixture", outline_model=None, section_planner_model=None, section_writer_model=None)
    report = resumed.run("Question", graph, store, refs, resume=saved)
    assert len(resumed_llm.calls) == 2
    assert report.count("## First") == report.count("## Second") == 1
    assert "https://source.example" in report
    assert saved == snapshots[-1]
    assert "First" in resumed_llm.calls[0][0][0].content


def test_attempt_lease_rejects_late_saves_and_archives_previous_attempt(tmp_path):
    store = ResearchRunStore(tmp_path / "runs.sqlite")
    first = store.create("Question", "en")
    store.save(first["id"], first["lease"], checkpoint={"schema_version": 1}, status="failed")
    next_run = store.claim(first["id"])
    assert next_run["attempt"] == 2
    assert not store.save(first["id"], first["lease"], checkpoint={"corrupted": True}, status="completed")
    assert store.get(first["id"])["checkpoint"] == {"schema_version": 1}
    with store.database._connection() as connection:
        row = connection.execute("SELECT status FROM research_attempts WHERE run_id=?", (first["id"],)).fetchone()
        assert row["status"] == "failed"


def test_checkpoint_storage_failure_is_visible_without_discarding_findings(tmp_path, monkeypatch, caplog):
    store = ResearchRunStore(tmp_path / "runs.sqlite")
    tracker = RunSession(store, store.create("Question", "en"))
    monkeypatch.setattr(store, "save", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    payload = tracker.observe({"type": "research.checkpoint", "data": {"checkpoint": {
        "tasks": {"task_a": {"result": {"answer": "Retained finding"}}}}}})
    assert payload["data"]["checkpoint_saved"] is False
    assert tracker.view["tasks"]["task_a"]["result"]["answer"] == "Retained finding"
    assert "disk full" in caplog.text


def test_usage_is_accumulated_once_per_attempt(tmp_path):
    store = ResearchRunStore(tmp_path / "runs.sqlite")
    first = RunSession(store, store.create("Question", "en"))
    usage = {"tokens": {"input": 10, "output": 2, "total": 12},
             "search": {"api_calls": 1, "results_count": 3, "queries": ["query"]},
             "llm": {"calls": 1, "models": ["model"], "per_model": {"model": {"input": 10, "output": 2, "total": 12, "calls": 1}}},
             "timing": {"duration_seconds": 5}}
    assert first.record_usage(usage) == usage
    first.save(status="cancelled")
    second = RunSession(store, store.claim(first.run["id"]))
    total = second.record_usage(usage)
    assert total["tokens"]["total"] == 24
    assert total["llm"]["per_model"]["model"]["calls"] == 2
    assert total["search"]["api_calls"] == 2
    assert total["timing"]["duration_seconds"] == 10
    assert second.record_usage(usage)["tokens"]["total"] == 24
    second.save(status="completed")
    assert store.get(first.run["id"])["view"]["usage"]["tokens"]["total"] == 24


def test_recovered_planner_errors_do_not_remain_active_after_completion(tmp_path):
    store = ResearchRunStore(tmp_path / "runs.sqlite")
    tracker = RunSession(store, store.create("Question", "en"))
    tracker.observe({"type": "planner.observation", "data": {"error": "Recoverable name error"}})
    assert store.get(tracker.run["id"])["error"] is None
    tracker.save(error="Previously stored error")
    tracker.observe({"type": "research.completed", "data": {"article": "The complete report"}})
    assert not store.get(tracker.run["id"])["error"]
    assert not tracker.run["error"]


def test_scheduler_retries_only_unfinished_tasks_and_preserves_metadata():
    calls = []

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, question, **kwargs):
            calls.append(question)
            return {"content": "Recovered", "ref2url": {1: {"url": "https://source.test"}}}

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher)
    completed = {"id": "task_done", "question": "Done", "status": "completed", "result": {"answer": "Existing"}}
    failed = {"id": "task_retry", "question": "Retry", "status": "failed", "result": {"error": "429"},
              "cognitive_edge_id": "r1", "cognitive_target_id": "e1", "core_criteria": ["Accuracy"]}
    try:
        assert scheduler.restore_tasks({"task_done": completed, "task_retry": failed}) == ["task_retry"]
        events = list(scheduler.iter_completed(timeout=2, poll_timeout=0.01))
        assert calls == ["Retry"]
        assert scheduler.task("task_done") == completed
        assert scheduler.task("task_retry")["core_criteria"] == ["Accuracy"]
        assert events[0].type == "task.completed"
    finally:
        scheduler.close()


def test_program_snapshot_restores_data_without_copying_commands():
    first = PlannerProgram()
    first.inject("finish", lambda: None)
    assert first.execute('names = ["task_a"]\ncount = 3').success
    saved = first.snapshot()
    assert saved == {"names": ["task_a"], "count": 3}
    second = PlannerProgram()
    second.restore(saved)
    assert second.execute("(names, count)").result == (["task_a"], 3)


def test_active_status_refresh_and_duplicate_resume_remain_operable(tmp_path):
    from veritrace.agent.runtime.progress import report_progress
    ready, stopped = threading.Event(), threading.Event()

    class Agent:
        def cancel(self):
            stopped.set()

        def __call__(self, *args, **kwargs):
            yield ResearchEvent("research.started")
            report_progress("reading", title="Actual source title")
            ready.set()
            stopped.wait(3)
            yield ResearchEvent("research.cancelled")

    app = create_app(parse_arguments([]), database_path=tmp_path / "runs.sqlite", agent_factory=lambda **kwargs: Agent())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/solve", json={"inputs": "Question"}))
            assert await asyncio.to_thread(ready.wait, 2)
            with app.state.research_runs.database._connection() as conn:
                run_id = conn.execute("SELECT id FROM research_runs").fetchone()[0]
            for _ in range(100):
                snapshot = (await client.get(f"/api/research/{run_id}")).json()
                if snapshot["view"].get("activity", {}).get("stage") == "reading":
                    break
                await asyncio.sleep(0.01)
            assert snapshot["view"]["activity"]["stage"] == "reading"
            assert snapshot["view"]["activity"]["title"] == "Actual source title"
            duplicate = await client.post("/solve", json={"inputs": "Question", "resume_run_id": run_id})
            assert duplicate.status_code == 409
            assert (await client.post(f"/api/research/{run_id}/stop")).json()["status"] == "stopping"
            response = await asyncio.wait_for(pending, 2)
            assert "research.cancelled" in response.text
            saved = (await client.get(f"/api/research/{run_id}")).json()
            assert saved["recovery"]["status"] == "cancelled"
            assert "usage" in saved["view"]
    asyncio.run(scenario())


def test_api_restart_restores_checkpoint_and_never_exposes_planner_state(tmp_path):
    checkpoint = {"schema_version": 1, "question": "Question", "language": "en", "messages": [
        {"role": "system", "content": "Private planner context"}], "tasks": {"task_a": {
            "id": "task_a", "status": "completed", "result": {"answer": "Finding", "evidence_records": [{"ref_idx": 1}]}}}}
    restored = []

    class Agent:
        def cancel(self):
            pass

        def latest_checkpoint(self):
            return checkpoint

        def restore_checkpoint(self, saved, action):
            restored.append((saved, action))

        def __call__(self, question, session_id=0):
            yield ResearchEvent("research.started", {"question": question})
            yield ResearchEvent("research.checkpoint", {"checkpoint": checkpoint})
            if restored:
                yield ResearchEvent("research.completed", {"article": "Recovered report"})
            else:
                raise RuntimeError("Provider unavailable")

    settings = parse_arguments(["--lang", "en"])
    def application():
        return create_app(settings, database_path=tmp_path / "runs.sqlite", agent_factory=lambda **kwargs: Agent())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
            response = await client.post("/solve", json={"inputs": "Question"})
            rows = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            run_id = rows[0]["data"]["run_id"]
            assert rows[-2]["data"]["can_continue"]
            assert "Private planner context" not in response.text
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application()), base_url="http://test") as client:
            saved = (await client.get(f"/api/research/{run_id}")).json()
            assert saved["view"]["tasks"]["task_a"]["result"]["answer"] == "Finding"
            assert "messages" not in saved
            assert (await client.post("/solve", json={"inputs": "Changed", "resume_run_id": run_id})).status_code == 400
            response = await client.post("/solve", json={"inputs": "Question", "resume_run_id": run_id, "recovery_action": "write_report"})
            assert "Recovered report" in response.text
            assert restored == [(checkpoint, "write_report")]
            assert (await client.get(f"/api/research/{run_id}")).json()["recovery"]["attempt"] == 2
    asyncio.run(scenario())
