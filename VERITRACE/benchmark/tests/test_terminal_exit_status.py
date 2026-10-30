"""CLI exit statuses and persistence with offline query fixtures."""

import json
import queue
import runpy
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from benchmark import run_terminal, run_parallel


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch):
    def configure(**kwargs):
        for handler in kwargs.get("handlers", []):
            handler.close()
    monkeypatch.setattr(run_terminal.logging, "basicConfig", configure)


def options(tmp_path, *mode):
    return [*mode, "--output", str(tmp_path / "results.jsonl"), "--log-file", str(tmp_path / "runner.log")]


def records(tmp_path):
    return [json.loads(line) for line in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()]


def result(status):
    return {"status": status, "article": "Fixture report" if status == "success" else "",
            "error": None if status == "success" else "Timeout after 3600s", "usage_stats": {}}


@pytest.mark.parametrize("status, expected", [("success", 0), ("error", 1)])
def test_single_query_main_returns_status_and_preserves_record_schema(tmp_path, monkeypatch, status, expected):
    monkeypatch.setattr(run_terminal, "run_single_query", lambda *args, **kwargs: result(status))
    assert run_terminal.main(options(tmp_path, "--query", "Question")) == expected
    saved = records(tmp_path)
    assert saved[0]["status"] == status
    assert set(saved[0]) == {"id", "prompt", "article", "cited_references", "status", "error", "duration_seconds", "usage_stats"}


def test_single_query_exception_is_persisted_as_failure(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("fixture exception")
    monkeypatch.setattr(run_terminal, "run_single_query", fail)
    assert run_terminal.main(options(tmp_path, "--query", "Question")) == 1
    assert records(tmp_path)[0]["status"] == "error"
    assert "fixture exception" in records(tmp_path)[0]["error"]


@pytest.mark.parametrize("status, expected", [("success", 0), ("error", 1), ("exception", 1)])
def test_rewrite_main_returns_status_and_saves_every_attempt(tmp_path, monkeypatch, status, expected):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    def rewrite(*args):
        if status == "exception":
            raise RuntimeError("fixture rewrite failure")
        return result(status)
    monkeypatch.setattr(run_terminal, "rewrite_from_evidence", rewrite)
    assert run_terminal.main(options(tmp_path, "--rewrite", str(evidence))) == expected
    saved = records(tmp_path)[0]
    assert saved["status"] == ("success" if status == "success" else "error")
    assert set(saved) == {"id", "prompt", "article", "status", "error", "duration_seconds"}


def test_batch_continues_after_failures_and_resume_skips_successes(tmp_path, monkeypatch):
    queries = tmp_path / "queries.jsonl"
    queries.write_text(''.join(json.dumps({"id": index, "prompt": f"Q{index}"}) + '\n' for index in range(1, 5)), encoding="utf-8")
    called = []
    def query(prompt, *args, **kwargs):
        called.append(prompt)
        if prompt == "Q3":
            raise RuntimeError("fixture exception")
        return result("error" if prompt == "Q2" else "success")
    monkeypatch.setattr(run_terminal, "run_single_query", query)
    argv = options(tmp_path, "--input", str(queries))
    assert run_terminal.main(argv) == 1
    assert called == ["Q1", "Q2", "Q3", "Q4"]
    assert [entry["status"] for entry in records(tmp_path)] == ["success", "error", "error", "success"]
    called.clear()
    monkeypatch.setattr(run_terminal, "run_single_query", lambda prompt, *args, **kwargs: called.append(prompt) or result("success"))
    assert run_terminal.main(argv) == 0
    assert called == ["Q2", "Q3"]
    called.clear()
    before = (tmp_path / "results.jsonl").read_bytes()
    assert run_terminal.main(argv) == 0
    assert called == []
    assert (tmp_path / "results.jsonl").read_bytes() == before


def test_interrupt_returns_130_and_keeps_completed_batch_results(tmp_path, monkeypatch):
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"id":1,"prompt":"Q1"}\n{"id":2,"prompt":"Q2"}\n', encoding="utf-8")
    def query(prompt, *args, **kwargs):
        if prompt == "Q2":
            raise KeyboardInterrupt
        return result("success")
    monkeypatch.setattr(run_terminal, "run_single_query", query)
    assert run_terminal.main(options(tmp_path, "--input", str(queries))) == 130
    assert [entry["id"] for entry in records(tmp_path)] == [1]


def test_argument_parser_retains_exit_two():
    with pytest.raises(SystemExit) as error:
        run_terminal.main([])
    assert error.value.code == 2


def test_script_entrypoint_propagates_failed_query_exit(tmp_path, monkeypatch):
    import dotenv
    import veritrace.agent
    from veritrace.agent.runtime import ResearchEvent

    class Agent:
        def __call__(self, question):
            yield ResearchEvent("research.failed", {"error": "fixture failure"})
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(veritrace.agent, "init_agent", lambda **kwargs: Agent())
    path = Path(run_terminal.__file__)
    monkeypatch.setattr(sys, "argv", [str(path), *options(tmp_path, "--query", "Question")])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(path), run_name="__main__")
    assert error.value.code == 1
    assert records(tmp_path)[0]["status"] == "error"


def test_parallel_worker_keeps_nonzero_exit_records_without_retry(tmp_path, monkeypatch):
    calls = []
    def process(command, **kwargs):
        qid = int(command[1])
        calls.append(qid)
        status = "error" if qid == 1 else "success"
        Path(command[2]).write_text(json.dumps({"id": qid, **result(status)}) + '\n', encoding="utf-8")
        return SimpleNamespace(wait=lambda: 1 if qid == 1 else 0)
    monkeypatch.setattr(run_parallel, "build_env", lambda worker: {})
    monkeypatch.setattr(run_parallel, "build_research_cmd", lambda args, ids, output, log: ["fixture", str(ids[0]), output])
    monkeypatch.setattr(run_parallel.subprocess, "Popen", process)
    monkeypatch.setattr(run_parallel, "_verify_key_dead", lambda key: pytest.fail("Unexpected provider probe"))
    tasks = queue.Queue()
    tasks.put(1)
    tasks.put(2)
    tracker = {"outputs": [], "completed": 0, "success": 0, "errors": 0, "total": 2}
    run_parallel._slot_worker(0, {"dashscope_key": "fixture-key"}, tasks, SimpleNamespace(mode="research"),
        str(tmp_path), "fixture", tmp_path, "test", tracker, threading.Lock(), [])
    assert calls == [1, 2]
    assert tasks.unfinished_tasks == 0
    assert (tracker["completed"], tracker["success"], tracker["errors"]) == (2, 1, 1)
    merged = run_parallel.merge_results(tracker["outputs"], str(tmp_path / "merged.jsonl"))
    assert merged[1]["status"] == "error"
    assert merged[2]["status"] == "success"
