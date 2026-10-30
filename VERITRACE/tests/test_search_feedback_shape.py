"""Optional malformed feedback must not discard collected research."""

import json
import logging

import pytest

from veritrace.agent.searcher import SearcherAgent


@pytest.mark.parametrize("feedback", [[], None, "invalid", {"can_improve": False, "reason": "Covered"}])
def test_final_answer_survives_feedback_with_invalid_json_shape(
    monkeypatch, scripted_llm, caplog, feedback
):
    answer = "Finding [[1]].\n\n### Feedback for Planner\n```json\n" + json.dumps(feedback) + "\n```"
    search_command = json.dumps({"tool": "web_search", "arguments": {"query": "Fixture query"}})
    model = scripted_llm(["```json\n" + search_command + "\n```", answer])
    searcher = SearcherAgent(model, max_turn=1)
    source = {"index": 1, "url": "https://example.test/source", "title": "Source", "snippet": "Retained source evidence"}
    monkeypatch.setattr(searcher, "_execute_search", lambda query: ("Search result", "fixture-cache", [source]))
    caplog.set_level(logging.WARNING)

    result = searcher.search_and_summarize("Research question", "Original topic")

    assert result["content"] == answer
    assert result["memory"][-1] == {"role": "assistant", "content": answer}
    assert result["ref2url"][1]["url"] == source["url"]
    assert result["evidence_records"][0]["evidence_quote"] == source["snippet"]
    assert "error" not in result
    assert len(model.calls) == 2
    if isinstance(feedback, dict):
        assert result["feedback"] == feedback
        assert "Feedback must be a JSON object" not in caplog.text
    else:
        assert result["feedback"] is None
        assert "Feedback must be a JSON object" in caplog.text
