"""Planner graph mutations reach native tasks without legacy checkpoint fields."""

import threading
from types import SimpleNamespace

import pytest

from veritrace.agent.runtime.planner_graph import PlannerGraph
from veritrace.agent.runtime.program import PlannerProgram
from veritrace.agent.runtime.scheduler import TaskScheduler


@pytest.fixture
def scheduler():
    scheduler = TaskScheduler(llm=object())
    scheduler.set_question("Original question")
    scheduler.restore_tasks({
        "task_a": {
            "id": "task_a", "question": "Subquestion", "status": "completed",
            "attention_notes": ["original note"], "cognitive_target_id": "e1",
            "cognitive_edge_id": "r1", "core_criteria": ["original criterion"],
            "result": {
                "answer": "Original answer [[1]]", "references": {1: {"url": "https://example.test"}},
                "messages": [{"role": "assistant", "content": "original"}],
                "quality_results": [{"index": 1, "score": 3}],
                "feedback": {"can_improve": True},
                "evidence_records": [{"ref_idx": 1, "evidence_quote": "original evidence"}],
                "error": None,
            },
        },
    })
    try:
        yield scheduler
    finally:
        scheduler.close()


def test_node_is_shallow_dict_and_nested_writes_reach_native_tasks(scheduler):
    graph = PlannerGraph(scheduler)
    node = graph.node("task_a")
    assert type(node) is dict
    node["content"] = "Only the outer copy"
    assert scheduler.task("task_a")["question"] == "Subquestion"

    node["attention_notes"].append("added note")
    node["response"]["content"] = "Revised answer [[1]]"
    node["memory"]["ref2url"][1]["url"] = "https://revised.test"
    node["memory"]["agent.memory"][0]["content"] = "revised message"
    node["memory"]["quality_results"][0]["score"] = 5
    node["memory"]["feedback"]["can_improve"] = False
    node["memory"]["evidence_records"][0]["evidence_quote"] = "revised evidence"

    task = scheduler.task("task_a")
    assert task["attention_notes"] == ["original note", "added note"]
    assert task["result"]["answer"] == "Revised answer [[1]]"
    assert task["result"]["references"][1]["url"] == "https://revised.test"
    assert task["result"]["messages"][0]["content"] == "revised message"
    assert task["result"]["quality_results"][0]["score"] == 5
    assert task["result"]["feedback"] == {"can_improve": False}
    assert task["result"]["evidence_records"][0]["evidence_quote"] == "revised evidence"
    assert not {"content", "response", "memory"} & task.keys()


def test_normal_task_views_remain_serializable_for_checkpoint_recovery(scheduler):
    from veritrace.agent.runtime.program import PlannerProgram

    first = PlannerProgram()
    first.inject("graph", PlannerGraph(scheduler))
    assert first.execute('node = graph.node("task_a")\nnodes = graph.nodes').success
    saved = first.snapshot()
    assert saved["node"]["response"]["content"] == "Original answer [[1]]"
    assert saved["nodes"]["task_a"]["memory"]["ref2url"]["1"]["url"] == "https://example.test"
    second = PlannerProgram()
    second.restore(saved)
    result = second.execute('node["response"]["content"]')
    assert result.success and result.result == "Original answer [[1]]"


def test_pop_returns_usable_values_after_their_backing_node_is_removed(scheduler):
    graph = PlannerGraph(scheduler)
    saved = graph.node("task_a")
    removed = graph.nodes.pop("task_a")
    assert removed["content"] == "Subquestion"
    assert removed["response"]["content"] == "Original answer [[1]]"
    assert removed["memory"]["ref2url"][1]["url"] == "https://example.test"
    assert scheduler.task("task_a") == {}
    assert saved["response"]["content"] == "Original answer [[1]]"
    assert saved["memory"]["evidence_records"][0]["evidence_quote"] == "original evidence"
    assert graph.nodes.pop("missing", None) is None
    assert graph.nodes.popitem() == ("root", {"content": "Original question", "type": "root"})
    assert not graph.nodes


def test_memory_and_response_assignment_replace_fields_instead_of_merging(scheduler):
    graph = PlannerGraph(scheduler)
    graph.nodes["task_a"]["memory"] = {"ref2url": {}}
    assert dict(graph.node("task_a")["memory"]) == {"ref2url": {}}
    result = scheduler.task("task_a")["result"]
    assert not result.get("messages") and not result.get("evidence_records")
    graph.nodes["task_a"]["memory"] = {}
    assert dict(graph.node("task_a")["memory"]) == {}
    graph.nodes["task_a"]["memory"]["ref2url"] = {2: "replacement"}
    assert scheduler.task("task_a")["result"]["references"] == {2: "replacement"}
    removed = graph.nodes["task_a"].pop("memory")
    assert removed == {"ref2url": {2: "replacement"}}
    assert "memory" not in graph.node("task_a")
    graph.nodes["task_a"]["response"] = {}
    assert "content" not in graph.node("task_a")["response"]


@pytest.mark.parametrize("replace", [False, True])
def test_queued_completion_uses_current_state_and_never_overwrites_a_replacement(replace):
    settled = threading.Event()

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            return {"content": "Original completed answer"}

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher)
    graph = PlannerGraph(scheduler)
    try:
        graph.add_task("task_a", "Question", cognitive_edge_id="r1", cognitive_target_id="e1")
        future = scheduler._futures["task_a"]
        future.add_done_callback(lambda done: settled.set())
        future.result(timeout=1)
        assert settled.wait(1)
        if replace:
            graph.nodes["task_a"] = {"content": "Replacement", "response": {"content": "New answer"}}
            assert list(scheduler.iter_completed(timeout=0.1, poll_timeout=0.01)) == []
        else:
            graph.node("task_a")["response"]["content"] = "New answer"
            event, = scheduler.iter_completed(timeout=0.1, poll_timeout=0.01)
            assert event.data["task"]["result"]["answer"] == "New answer"
        assert scheduler.task("task_a")["result"]["answer"] == "New answer"
    finally:
        scheduler.close()


def test_live_nodes_update_metadata_and_support_mapping_operations(scheduler):
    graph = PlannerGraph(scheduler)
    nodes = graph.nodes
    assert list(nodes) == ["root", "task_a"]
    nodes["task_a"]["content"] = "Revised question"
    nodes["task_a"].update(cognitive_target_id="e2", core_criteria=["revised criterion"])
    assert scheduler.task("task_a")["question"] == "Revised question"
    assert scheduler.task("task_a")["cognitive_target_id"] == "e2"
    assert scheduler.task("task_a")["core_criteria"] == ["revised criterion"]
    assert graph.node("missing") == {}
    assert nodes["task_a"].copy()["content"] == "Revised question"
    assert nodes.copy()["task_a"]["content"] == "Revised question"
    assert "Original answer" in repr(graph.node("task_a"))

    detached = scheduler.tasks
    detached["task_a"]["result"]["answer"] = "outside snapshot"
    assert graph.node("task_a")["response"]["content"] == "Original answer [[1]]"


def test_documented_node_keyword_works_directly_and_in_planner_program(scheduler):
    graph = PlannerGraph(scheduler)
    assert graph.node(node_name="task_a")["content"] == "Subquestion"
    program = PlannerProgram()
    program.inject("graph", graph)
    result = program.execute('graph.node(node_name="task_a")["response"]["content"]')
    assert result.success, result.error
    assert result.result == "Original answer [[1]]"


def test_whole_nodes_assignment_materializes_backed_views_before_clear(scheduler):
    graph = PlannerGraph(scheduler)
    graph.finish()
    graph.nodes = graph.nodes.copy()
    assert scheduler.question == "Original question"
    assert scheduler.finish_requested
    task = scheduler.task("task_a")
    assert task["result"]["answer"] == "Original answer [[1]]"
    assert task["result"]["references"][1]["url"] == "https://example.test"
    assert task["result"]["evidence_records"][0]["evidence_quote"] == "original evidence"
    assert not {"content", "response", "memory"} & task.keys()


def test_root_finish_markers_and_context_forward_to_scheduler(scheduler):
    graph = PlannerGraph(scheduler)
    assert getattr(graph, "llm") is scheduler.llm
    assert hasattr(graph, "cognitive_manager")
    graph.start_time = 123.0
    assert scheduler.start_time == 123.0
    graph.nodes["root"]["content"] = "Changed question"
    assert scheduler.question == "Changed question"
    graph.add_root_node("Alias question", node_name="ignored by baseline")
    assert graph.node("root") == {"content": "Alias question", "type": "root"}
    graph.add_response_node()
    assert scheduler.finish_requested
    assert graph.node("response") == {"type": "end"}
    del graph.nodes["response"]
    assert not scheduler.finish_requested
    assert graph.node("response") == {}
    scheduler.finish()
    assert "response" in graph.nodes
    scheduler.resume()
    assert "response" not in graph.nodes


def test_graph_method_rejections_are_emitted_once(scheduler):
    emitted = []
    graph = PlannerGraph(scheduler, emit=emitted.append)
    error = graph.add_node("r1", "Question")
    assert "[ERROR]" in error and emitted == [error]
    warning = graph.add_task("task_a", "Question", cognitive_edge_id="r1", cognitive_target_id="e1")
    assert "[WARNING]" in warning and emitted == [error, warning]
    invalid = graph.propose_restructure("invalid", "reason")
    assert "[ERROR]" in invalid and emitted[-1] == invalid
    graph.propose_restructure("R_aug", "add a dimension")
    assert graph.consume_restructure_proposal() == {"op_type": "R_aug", "reasoning": "add a dimension"}
    assert len(emitted) == 3


def test_legacy_node_assignment_translates_response_memory_and_native_status(scheduler):
    graph = PlannerGraph(scheduler)
    graph.nodes["task_b"] = {
        "content": "Added question", "type": "searcher",
        "cognitive_edge_id": "r2", "cognitive_target_id": "e2",
        "response": {"content": "Added answer", "stream_state": 6},
        "memory": {"ref2url": {2: "https://second.test"}, "evidence_records": [{"evidence_quote": "quote"}]},
    }
    task = scheduler.task("task_b")
    assert task["question"] == "Added question" and task["status"] == "completed"
    assert task["result"]["answer"] == "Added answer"
    assert task["result"]["references"] == {2: "https://second.test"}
    assert task["result"]["evidence_records"] == [{"evidence_quote": "quote"}]
    assert not {"content", "type", "response", "memory"} & task.keys()
    graph.nodes["task_b"] = graph.nodes["task_b"].copy()
    assert scheduler.task("task_b")["result"]["answer"] == "Added answer"
    del graph.nodes["task_b"]
    assert "task_b" not in scheduler.tasks


def test_shallow_result_views_retain_their_original_data_after_replacement(scheduler):
    graph = PlannerGraph(scheduler)
    response = graph.node("task_a")["response"]
    replacement = scheduler.task("task_a")["result"]
    replacement.update(answer="Search failed", error="failure")
    with scheduler._lock:
        scheduler._tasks["task_a"]["response"] = {"content": "Search failed", "stream_state": 6}
        scheduler._tasks["task_a"].update(error="failure", status="failed")
    assert response["content"] == "Original answer [[1]]"
    assert graph.node("task_a")["response"]["content"] == "Search failed"
    assert graph.node("task_a")["response"]["stream_state"] == 6
    assert graph.node("task_a")["memory"]["evidence_records"]
    assert scheduler.task("task_a")["status"] == "failed"


def test_adjacency_uses_cognitive_names_and_completion_state(scheduler):
    scheduler.cognitive_manager = SimpleNamespace(graph=SimpleNamespace(get_node=lambda name: SimpleNamespace(name="Target")))
    graph = PlannerGraph(scheduler)
    assert graph.adjacency_list == {
        "e1": [{"id": "task_a", "name": "task_a", "state": 3}],
        "root": [{"id": "e1", "name": "Target", "state": 3}],
    }
    before = scheduler.tasks
    assert graph.add_edge("root", "task_a") is None
    assert scheduler.tasks == before


def test_deleting_queued_and_replacing_running_tasks_discards_stale_futures():
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, question, **kwargs):
            calls.append(question)
            entered.set()
            assert release.wait(2)
            return {"content": "Obsolete answer"}

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher, max_workers=1)
    graph = PlannerGraph(scheduler)
    try:
        graph.add_task("task_running", "First question", cognitive_edge_id="r1", cognitive_target_id="e1")
        assert entered.wait(1)
        running = scheduler._futures["task_running"]
        running.add_done_callback(lambda done: settled.set())
        graph.add_task("task_queued", "Queued question", cognitive_edge_id="r2", cognitive_target_id="e2")
        queued = scheduler._futures["task_queued"]

        del graph.nodes["task_queued"]
        assert scheduler.pending_count == 2
        graph.nodes["task_running"] = {
            "content": "Replacement question", "response": {"content": "Replacement answer", "stream_state": 6},
        }
        release.set()
        running.result(timeout=1)
        queued.result(timeout=1)
        assert settled.wait(1)
        assert calls == ["First question"]
        assert scheduler.task("task_running")["result"]["answer"] == "Replacement answer"
        assert "task_queued" not in scheduler.tasks
        assert list(scheduler.iter_completed(timeout=0.1, poll_timeout=0.01)) == []
    finally:
        release.set()
        scheduler.close()


def test_replacement_before_worker_enters_scheduler_cannot_start_old_search(monkeypatch):
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            calls.append(kwargs["question"])
            return {"content": "Obsolete answer"}

    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher)
    submit = scheduler._executor.submit

    def delayed_submit(function):
        def delayed():
            entered.set()
            assert release.wait(2)
            return function()
        return submit(delayed)

    monkeypatch.setattr(scheduler._executor, "submit", delayed_submit)
    graph = PlannerGraph(scheduler)
    try:
        graph.add_task("task_a", "Original question", cognitive_edge_id="r1", cognitive_target_id="e1")
        assert entered.wait(1)
        future = scheduler._futures["task_a"]
        future.add_done_callback(lambda done: settled.set())
        graph.nodes["task_a"] = {"content": "Replacement", "response": {"content": "Kept answer"}}
        release.set()
        future.result(timeout=1)
        assert settled.wait(1)
        assert calls == []
        assert scheduler.task("task_a")["status"] == "completed"
        assert scheduler.task("task_a")["result"]["answer"] == "Kept answer"
        assert scheduler.pending_count == 0
        assert list(scheduler.iter_completed(timeout=0.1, poll_timeout=0.01)) == []
    finally:
        release.set()
        scheduler.close()
