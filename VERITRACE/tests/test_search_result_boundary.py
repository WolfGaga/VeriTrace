"""Malformed search payloads fail visibly while valid empty results remain usable."""

from copy import deepcopy

import pytest

from veritrace.agent.runtime.scheduler import TaskScheduler


def run_search(payload):
    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            return deepcopy(payload)

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher)
    try:
        scheduler.set_question("Original question")
        scheduler.add_task("task_result", "Research detail", cognitive_edge_id="r1", cognitive_target_id="e1")
        event, = scheduler.iter_completed(timeout=1, poll_timeout=0.01)
        assert scheduler.pending_count == 0
        assert scheduler.task("task_result") == event.data["task"]
        return event
    finally:
        scheduler.close()


@pytest.mark.parametrize("payload,error_type", [({}, "KeyError"), (None, "TypeError"), ([], "TypeError")])
def test_malformed_search_result_is_a_failed_task(payload, error_type):
    event = run_search(payload)
    assert event.type == "task.failed"
    task = event.data["task"]
    assert task["status"] == "failed"
    assert task["result"]["error"].startswith(error_type + ":")
    assert task["result"]["answer"] == f"[Search failed: {task['result']['error']}]"


def test_explicit_empty_search_answer_is_not_a_malformed_result():
    event = run_search({"content": ""})
    assert event.type == "task.completed"
    assert event.data["task"]["result"]["answer"] == ""
    assert event.data["task"]["result"]["error"] is None


def test_reported_provider_failure_retains_partial_answer_and_evidence():
    reference = {1: {"url": "https://example.test/source", "title": "Source"}}
    messages = [{"role": "assistant", "content": "Partial answer [[1]]"}]
    evidence = [{"ref_idx": 1, "evidence_quote": "Retained quotation"}]
    feedback = {"can_improve": True, "reason": "Provider unavailable"}
    event = run_search({
        "content": "Partial answer [[1]]",
        "error": "Provider unavailable",
        "ref2url": reference,
        "memory": messages,
        "evidence_records": evidence,
        "feedback": feedback,
    })
    assert event.type == "task.failed"
    result = event.data["task"]["result"]
    assert result["answer"] == "Partial answer [[1]]"
    assert result["error"] == "Provider unavailable"
    assert result["references"] == reference
    assert result["messages"] == messages
    assert result["evidence_records"] == evidence
    assert result["feedback"] == feedback
