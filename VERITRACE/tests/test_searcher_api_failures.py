"""Caught provider failures remain failed tasks and retain collected material."""
import logging
import threading
from concurrent.futures import CancelledError

import pytest

from veritrace.agent.searcher import SearcherAgent
from veritrace.agent.concurrency import cancellation_scope
from veritrace.agent.runtime.events import TaskResult
from veritrace.research_runs import recovery_info


@pytest.mark.parametrize("stage", ["initial", "final"])
def test_cancellation_propagates_without_api_failure_or_feedback(stage, scripted_llm, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    replies = [CancelledError("Research cancelled")] if stage == "initial" else ["Unknown tool"] * 6 + [CancelledError("Research cancelled")]
    model = scripted_llm(replies)
    searcher = SearcherAgent(model)
    monkeypatch.setattr(searcher, "_parse_tool_call_with_error", lambda text: ({"tool": "unsupported"}, None))
    with pytest.raises(CancelledError):
        searcher.search_and_summarize("Question", "Topic")
    assert len(model.calls) == (1 if stage == "initial" else 7)
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)
    assert "LLM API error" not in caplog.text


def test_response_arriving_after_stop_is_not_processed_as_success(scripted_llm, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    cancelled = threading.Event()
    model = scripted_llm(["Late answer"])
    original_chat = model.chat

    def chat(*args, **kwargs):
        response = original_chat(*args, **kwargs)
        cancelled.set()
        return response

    monkeypatch.setattr(model, "chat", chat)
    searcher = SearcherAgent(model)
    with cancellation_scope(cancelled), pytest.raises(CancelledError):
        searcher.search_and_summarize("Question", "Topic")
    assert len(model.calls) == 1
    assert "LLM response length" not in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.parametrize("stage", ["initial", "final"])
def test_provider_failure_retains_context_and_is_not_a_success(stage, scripted_llm, monkeypatch, caplog):
    replies = [RuntimeError("Provider unavailable")] if stage == "initial" else ["Unknown tool"] * 6 + [RuntimeError("Final synthesis unavailable")]
    model = scripted_llm(replies)
    searcher = SearcherAgent(model)
    monkeypatch.setattr(searcher, "_parse_tool_call_with_error", lambda text: ({"tool": "unsupported"}, None))
    record = {"ref_idx": 1, "url": "https://source.example", "evidence_quote": "Retained source words"}
    original_chat = model.chat

    def chat(*args, **kwargs):
        if stage == "final" and len(model.calls) == 2:
            searcher.evidence_records.append(record)
        return original_chat(*args, **kwargs)

    monkeypatch.setattr(model, "chat", chat)
    result = searcher.search_and_summarize("Question", "Topic")
    assert "RuntimeError" in result["error"]
    assert result["feedback"]["can_improve"] is True
    assert result["memory"][0]["role"] == "system"
    assert result["memory"][-1]["role"] == "assistant"
    assert result["evidence_records"] == ([] if stage == "initial" else [record])
    assert TaskResult.from_search(result).error == result["error"]
    assert len(model.calls) == (1 if stage == "initial" else 7)
    assert any(item.levelno >= logging.ERROR and item.exc_info for item in caplog.records)


def test_failed_partial_search_does_not_offer_report_until_evidence_is_ready():
    run = {"id": "run", "attempt": 1, "status": "failed", "updated_at": 0,
           "checkpoint": {"tasks": {"failed": {"status": "failed", "result": {"evidence_records": [{"ref_idx": 1}]}}}}}
    assert recovery_info(run)["can_continue"] is True
    assert recovery_info(run)["can_write_report"] is False


def test_reused_searcher_clears_previous_question_evidence(scripted_llm):
    searcher = SearcherAgent(scripted_llm([RuntimeError("First provider failure"), RuntimeError("Provider unavailable")]))
    first = searcher.search_and_summarize("First", "Topic")
    assert "First provider failure" in first["error"]
    searcher.evidence_records.append({"evidence_quote": "Previous question"})
    second = searcher.search_and_summarize("Second", "Topic")
    assert second["evidence_records"] == []
    assert "RuntimeError" in second["error"]


def test_successful_final_synthesis_keeps_its_payload_and_model_call_count(scripted_llm, monkeypatch):
    model = scripted_llm(["Unknown tool"] * 6 + ["The final answer."])
    searcher = SearcherAgent(model)
    monkeypatch.setattr(searcher, "_parse_tool_call_with_error", lambda text: ({"tool": "unsupported"}, None))
    result = searcher.search_and_summarize("Question", "Topic")
    assert result["content"] == "The final answer."
    assert "error" not in result
    assert len(model.calls) == 7
