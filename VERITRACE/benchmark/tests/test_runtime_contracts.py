"""Offline runner checks; no model or search clients are constructed."""

from types import SimpleNamespace
import threading

from benchmark import run_rewrite, run_terminal
from veritrace.agent.runtime import ResearchEvent
from veritrace.agent.usage_tracker import track_llm_usage


def test_terminal_selects_report_before_trailing_events_and_keeps_thread_usage(monkeypatch):
    article = "Verified article with enough content for the runner's minimum length check."

    class Agent:
        def __call__(self, question):
            track_llm_usage(7, 3, "fixture-model")
            yield ResearchEvent("research.completed", {"article": article, "ref2url": {}})
            yield ResearchEvent("usage.updated", {"usage": {}})

    monkeypatch.setattr(run_terminal, "init_agent", lambda **kwargs: Agent())
    result = run_terminal.run_single_query("Question", timeout=2)
    assert result["status"] == "success"
    assert result["article"] == article
    assert result["usage_stats"]["tokens"]["total"] == 10


def test_terminal_reports_failed_event(monkeypatch):
    class Agent:
        def __call__(self, question):
            yield ResearchEvent("research.failed", {"error": "fixture failure"})

    monkeypatch.setattr(run_terminal, "init_agent", lambda **kwargs: Agent())
    result = run_terminal.run_single_query("Question", timeout=2)
    assert result["status"] == "error"
    assert result["error"] == "fixture failure"


def test_timeout_uses_public_cancellation(monkeypatch):
    release = threading.Event()

    class Agent:
        cancelled = False

        def __call__(self, question):
            release.wait(2)
            yield ResearchEvent("research.cancelled")

        def cancel(self):
            self.cancelled = True
            release.set()

    agent = Agent()
    monkeypatch.setattr(run_terminal, "init_agent", lambda **kwargs: agent)
    result = run_terminal.run_single_query("Question", timeout=0)
    assert result["status"] == "error"
    assert agent.cancelled


def test_rewrite_uses_shared_pipeline_and_preserves_evidence_reference_filter(monkeypatch):
    calls = {}

    class Pipeline:
        def __init__(self, writer, provider, language, include_search_refs):
            calls["options"] = (provider, language, include_search_refs)
            self.data = {"outline": [{"title": "Outline"}], "overall_theme": "Theme", "section_texts": {"s1": "Section"}}

        def run(self, question, graph, evidence, references):
            calls["references"] = references
            track_llm_usage(4, 6, "fixture-writer")
            return "Report"

    monkeypatch.setattr(run_rewrite, "WritingPipeline", Pipeline)
    monkeypatch.setattr(run_rewrite, "create_adapter", lambda *args: object())
    monkeypatch.setattr(run_rewrite.EvidenceStore, "from_evidence_table", lambda data: SimpleNamespace(records=data))
    monkeypatch.setenv("WRITER_PROVIDER", "dashscope")
    result = run_rewrite.run_writing_pipeline({"question": "Question", "cognitive_graph": {"nodes": {}},
        "evidence_table": {"1": []}, "ref2url": {"1": {"url": "https://kept.test"}, "2": {"url": "https://unused.test"}}},
        language="en", include_search_refs=False)
    assert calls["options"] == ("dashscope", "en", False)
    assert list(calls["references"]) == [1]
    assert result["outline"] == [{"title": "Outline"}]
    assert result["section_texts"] == {"s1": "Section"}
    assert result["rewrite_usage"]["tokens"]["total"] == 10
