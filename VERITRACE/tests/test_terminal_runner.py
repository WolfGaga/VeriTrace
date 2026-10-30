import importlib.util
import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest

from veritrace.agent.runtime.events import ResearchEvent
from veritrace.agent.usage_tracker import track_llm_usage


@pytest.fixture
def runner(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "benchmark" / "run_terminal.py"
    spec = importlib.util.spec_from_file_location("veritrace_test_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("PLANNER_MAX_TURN", "20")
    return module


def test_terminal_runner_extracts_citations_and_preserves_evidence_usage(runner, monkeypatch, tmp_path):
    article = "## Findings\n" + "The supported finding is documented [[1]]. " * 3

    class Agent:
        _pipeline_data = {"evidence_store": {1: {"entries": [{"quote": "Original quote"}]}}}

        def __call__(self, question):
            track_llm_usage(8, 4, "fixture")
            yield ResearchEvent("research.started")
            yield ResearchEvent(
                "research.completed",
                {
                    "article": article + "\n## Search Process References\n[[2]] Additional source",
                    "ref2url": {
                        1: {"url": "https://source.example", "title": "Source"},
                        2: {"url": "https://unused.example"},
                    },
                },
            )

        def cancel(self):
            pass

    agent = Agent()
    monkeypatch.setattr(runner, "init_agent", lambda **kwargs: agent)
    result = runner.run_single_query(
        "Question",
        timeout=1,
        save_evidence=True,
        output_path=str(tmp_path / "run.jsonl"),
        qid=7,
    )
    assert result["status"] == "success"
    assert result["article"] == article.strip()
    assert result["cited_references"] == [{"ref_idx": 1, "url": "https://source.example", "title": "Source"}]
    assert result["usage_stats"]["tokens"] == {"input": 8, "output": 4, "total": 12}
    assert result["pipeline_data"] == agent._pipeline_data
    assert agent.partial_evidence_path == str(tmp_path / "evidence_run" / "evidence_7.json")


@pytest.mark.parametrize("kind", ["no_completion", "short_report", "failure", "cancelled"])
def test_terminal_runner_does_not_label_incomplete_research_success(runner, monkeypatch, kind):
    class Agent:
        def __call__(self, question):
            yield ResearchEvent("research.started")
            if kind == "short_report":
                yield ResearchEvent("research.completed", {"article": "short"})
            elif kind == "failure":
                yield ResearchEvent("research.failed", {"error": "fixture failed"})
            elif kind == "cancelled":
                yield ResearchEvent("research.cancelled")

        def cancel(self):
            pass

    monkeypatch.setattr(runner, "init_agent", lambda **kwargs: Agent())
    result = runner.run_single_query("Question", timeout=1)
    assert result["status"] == "error"
    assert result["article"] == ""
    assert result["error"]


def test_terminal_timeout_cancels_worker(runner, monkeypatch):
    cancelled, closed = threading.Event(), threading.Event()

    class Agent:
        def __call__(self, question):
            try:
                yield ResearchEvent("research.started")
                cancelled.wait(timeout=2)
                yield ResearchEvent("research.cancelled")
            finally:
                closed.set()

        def cancel(self):
            cancelled.set()

    monkeypatch.setattr(runner, "init_agent", lambda **kwargs: Agent())
    result = runner.run_single_query("Question", timeout=0.01)
    assert result["status"] == "error"
    assert "Timeout" in result["error"]
    assert cancelled.is_set()
    assert closed.wait(1)


def partial_pipeline():
    return {
        "cognitive_graph": {"nodes": {"company": {"name": "Company"}}},
        "evidence_store": {1: {"url": "https://source.example", "entries": [{"quote": "Original quote"}]}},
        "cited_references": {1: {"url": "https://source.example", "title": "Source"}},
        "outline": [{"section_id": "1", "title": "First", "insights": [{"insight": "A finding", "evidence_ids": [1]}], "relevant_criteria": ["revenue"]}],
        "overall_theme": "Company",
        "user_requirements": [{"id": 1, "requirement": "Technical readers", "type": "audience"}],
        "section_texts": {"1": "## First\nA supported finding [[1]]."},
    }


@pytest.mark.parametrize("failure", ["timeout", "exception", "cancelled"])
def test_failed_run_retains_independent_pipeline_snapshot(runner, monkeypatch, tmp_path, failure):
    release, closed = threading.Event(), threading.Event()

    class Agent:
        def __init__(self):
            self._pipeline_data = partial_pipeline()
            self.cancelled = False

        def __call__(self, question):
            try:
                yield ResearchEvent("research.started")
                if failure == "timeout":
                    assert release.wait(2)
                    yield ResearchEvent("research.cancelled")
                elif failure == "exception":
                    raise RuntimeError("Writer failed after section one")
                else:
                    yield ResearchEvent("research.cancelled")
            finally:
                closed.set()

        def cancel(self):
            self.cancelled = True

    agent = Agent()
    monkeypatch.setattr(runner, "init_agent", lambda **kwargs: agent)
    try:
        result = runner.run_single_query("Question", timeout=0.02 if failure == "timeout" else 1, save_evidence=True)
        assert result["status"] == "error" and result["article"] == ""
        assert result["pipeline_data"] == partial_pipeline()
        assert result["ref2url"] == partial_pipeline()["cited_references"]
        assert result["pipeline_data"] is not agent._pipeline_data
        agent._pipeline_data["section_texts"]["2"] = "A later section"
        assert set(result["pipeline_data"]["section_texts"]) == {"1"}
        if failure == "timeout":
            assert agent.cancelled and "Timeout" in result["error"]
        output = tmp_path / "run.jsonl"
        runner._save_evidence_file(str(output), 14, "Question", result)
        saved = json.loads((tmp_path / "evidence_run/evidence_14.json").read_text(encoding="utf-8"))
        assert saved["section_texts"] == result["pipeline_data"]["section_texts"]
        assert saved["outline"] == result["pipeline_data"]["outline"]
        assert saved["user_requirements"] == result["pipeline_data"]["user_requirements"]
        assert saved["evidence_table"]["1"]["entries"][0]["quote"] == "Original quote"
        assert saved["ref2url"]["1"]["url"] == "https://source.example"
    finally:
        release.set()
        assert closed.wait(1)


@pytest.mark.parametrize("mode,qid", [("single", 0), ("batch", 14)])
def test_failed_cli_run_saves_evidence_without_changing_failure_status(runner, monkeypatch, tmp_path, mode, qid):
    result = {"status": "error", "error": "Timeout after 6000s", "article": "", "pipeline_data": partial_pipeline(), "ref2url": partial_pipeline()["cited_references"]}
    monkeypatch.setattr(runner, "run_single_query", lambda *args, **kwargs: deepcopy(result))
    monkeypatch.setattr(runner.logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner.logging, "FileHandler", lambda *args, **kwargs: runner.logging.NullHandler())
    output = tmp_path / "run.jsonl"
    args = ["--output", str(output), "--save-evidence", "--log-file", str(tmp_path / "run.log")]
    if mode == "single":
        args += ["--query", "Question"]
    else:
        query_file = tmp_path / "queries.jsonl"
        query_file.write_text(json.dumps({"id": qid, "prompt": "Question"}) + "\n", encoding="utf-8")
        args += ["--input", str(query_file)]
    assert runner.main(args) == 1
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["id"] == qid and record["status"] == "error"
    assert record["error"] == result["error"] and record["article"] == ""
    path = tmp_path / f"evidence_run/evidence_{qid}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["section_texts"] == partial_pipeline()["section_texts"]
    assert saved["outline"][0]["insights"][0]["evidence_ids"] == [1]
    assert not Path(str(path) + ".tmp").exists()


def test_incomplete_serialization_does_not_replace_saved_evidence(runner, tmp_path):
    evidence = tmp_path / "evidence_run/evidence_14.json"
    evidence.parent.mkdir()
    prior = b'{"search_only": true}'
    evidence.write_bytes(prior)
    result = {"pipeline_data": partial_pipeline(), "ref2url": {1: object()}}
    with pytest.raises(TypeError):
        runner._save_evidence_file(str(tmp_path / "run.jsonl"), 14, "Question", result)
    assert evidence.read_bytes() == prior
