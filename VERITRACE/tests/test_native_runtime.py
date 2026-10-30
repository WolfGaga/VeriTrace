"""Observable task, execution, and cancellation contracts without provider calls."""

import asyncio
import threading
from contextvars import ContextVar

import pytest

from veritrace.agent.runtime import ResearchEvent
from veritrace.agent.runtime.async_stream import stream_in_thread
from veritrace.agent.runtime.planner import TaskQuery
from veritrace.agent.runtime.program import PlannerProgram
from veritrace.agent.runtime.scheduler import TaskScheduler


class Searcher:
    def __init__(self, **kwargs):
        pass

    def search_and_summarize(self, question, **kwargs):
        return {
            "content": f"{question} [[1]]",
            "ref2url": {1: {"url": "https://example.test"}},
            "evidence_records": [{"ref_idx": 1, "evidence_quote": "source words"}],
        }


def submit(scheduler, name="task_a"):
    return scheduler.add_task(name, "question", cognitive_edge_id="r1", cognitive_target_id="e1")


def test_completed_task_is_delivered_once_without_pseudo_nodes():
    scheduler = TaskScheduler(llm=object(), searcher_factory=Searcher)
    try:
        scheduler.set_question("original question")
        submit(scheduler)
        events = list(scheduler.iter_completed(timeout=1, poll_timeout=0.01))
        assert [event.type for event in events] == ["task.completed"]
        task = events[0].data["task"]
        assert task["result"]["answer"] == "question [[1]]"
        assert task["result"]["references"][1]["url"] == "https://example.test"
        assert task["result"]["evidence_records"][0]["evidence_quote"] == "source words"
        assert set(scheduler.tasks) == {"task_a"}
        assert scheduler.pending_count == 0
        assert list(scheduler.iter_completed()) == []
        scheduler.finish()
        assert scheduler.finish_requested and set(scheduler.tasks) == {"task_a"}
        scheduler.resume()
        assert not scheduler.finish_requested
        task["result"]["references"].clear()
        assert scheduler.task("task_a")["result"]["references"]
    finally:
        scheduler.close()


def test_search_failure_still_supplies_planner_feedback():
    class FailedSearcher(Searcher):
        def search_and_summarize(self, **kwargs):
            raise ValueError("unavailable")

    scheduler = TaskScheduler(llm=object(), searcher_factory=FailedSearcher)
    try:
        submit(scheduler)
        (event,) = scheduler.iter_completed(timeout=1, poll_timeout=0.01)
        assert event.type == "task.failed"
        assert event.data["task"]["result"]["answer"] == "[Search failed: ValueError: unavailable]"
        assert scheduler.pending_count == 0
    finally:
        scheduler.close()


@pytest.mark.parametrize("text", ["{'尺寸': 'price'}", '{"source": {"value": 3}}', "{question} {topic}", "{} {{already literal}}"])
def test_search_context_braces_remain_literal_without_changing_question_fields(text):
    scheduler = TaskScheduler(llm=object())
    try:
        template = scheduler._build_enhanced_input_template(
            "## Question\n{question}\n## Topic\n{topic}", task_objective=text, attention_notes=[text],
            cognitive_background=text, cognitive_relevance=text, task_type="specified", specified_source=text)
        rendered = template.format(question="Actual question", topic="Original topic")
        assert rendered.startswith(text + "## Question\nActual question\n## Topic\nOriginal topic")
        assert rendered.count(text) == 5
    finally:
        scheduler.close()


def test_cancel_discards_running_result_and_prevents_queued_search():
    entered, release = threading.Event(), threading.Event()
    calls = []

    class SlowSearcher(Searcher):
        def search_and_summarize(self, **kwargs):
            calls.append(kwargs["question"])
            entered.set()
            assert release.wait(2)
            return super().search_and_summarize(**kwargs)

    scheduler = TaskScheduler(llm=object(), searcher_factory=SlowSearcher, max_workers=1)
    try:
        submit(scheduler)
        assert entered.wait(1)
        submit(scheduler, "task_b")
        scheduler.cancel()
        assert list(scheduler.iter_completed()) == []
        assert {task["status"] for task in scheduler.tasks.values()} == {"cancelled"}
        with pytest.raises(RuntimeError, match="closed"):
            submit(scheduler, "task_c")
        release.set()
        scheduler._futures["task_a"].result(timeout=1)
        assert len(calls) == 1
        assert scheduler.task("task_a")["result"] is None
    finally:
        release.set()
        scheduler.close()
        scheduler.close()


@pytest.mark.parametrize("isolated", [False, True])
def test_planner_final_expression_runs_once_and_captures_return(isolated):
    calls = []
    program = PlannerProgram(isolated=isolated)
    program.inject("operation", lambda: calls.append("called") or 42)
    result = program.execute('print("visible output")\noperation()')
    assert result.success and result.result == 42
    assert result.stdout == "visible output\n"
    assert calls == ["called"]


def test_task_query_is_an_isolated_view_and_program_cannot_reach_private_state():
    task = {"result": {"answer": "original"}}
    program = PlannerProgram(isolated=True)
    program.inject("graph", TaskQuery(lambda task_id: task))
    result = program.execute(
        'value = graph.node("task_a")\nvalue["result"]["answer"] = "changed"\ngraph.node("task_a")'
    )
    assert result.success and result.result["result"]["answer"] == "original"
    assert task["result"]["answer"] == "original"
    result = program.execute('graph._TaskQuery__lookup("task_a")')
    assert not result.success and "private" in result.error


@pytest.mark.parametrize("isolated", [False, True])
def test_rejected_command_is_visible_to_planner_once(isolated):
    program = PlannerProgram(isolated=isolated)
    program.inject("add_task", lambda: "[ERROR] Missing mapping")
    result = program.execute("add_task()")
    assert result.success and result.stdout == "[ERROR] Missing mapping\n"


@pytest.mark.parametrize("isolated", [False, True])
def test_cancel_interrupts_a_planner_loop(isolated):
    cancelled = threading.Event()
    program = PlannerProgram(cancelled, isolated=isolated)
    program.inject("stop", cancelled.set)
    result = program.execute("stop()\nwhile True:\n    pass")
    assert not result.success and result.error == "Research cancelled"


def test_async_stream_preserves_context_and_closes_producer_on_consumer_exit():
    request_id = ContextVar("native_runtime_test_request", default="missing")
    exited, cancel = threading.Event(), threading.Event()

    def source():
        try:
            yield ResearchEvent("research.started", {"request_id": request_id.get()})
            cancel.wait(1)
            yield ResearchEvent("research.updated")
        finally:
            exited.set()

    async def consume():
        request_id.set("request-a")
        stream = stream_in_thread(source, cancel.set)
        event = await anext(stream)
        assert event.data["request_id"] == "request-a"
        await stream.aclose()

    asyncio.run(consume())
    assert exited.wait(1)


def test_async_stream_propagates_error_and_finalizes():
    exited = threading.Event()

    def source():
        try:
            yield ResearchEvent("research.started")
            raise ValueError("failed research")
        finally:
            exited.set()

    async def consume():
        stream = stream_in_thread(source, lambda: None)
        with pytest.raises(ValueError, match="failed research"):
            async for _ in stream:
                pass

    asyncio.run(consume())
    assert exited.is_set()


def test_async_stream_applies_backpressure_and_close_releases_a_blocked_publisher():
    produced = []
    exited = threading.Event()

    def source():
        try:
            for index in range(10000):
                produced.append(index)
                yield ResearchEvent("research.updated", {"index": index})
        finally:
            exited.set()

    async def consume():
        stream = stream_in_thread(source, lambda: None)
        await anext(stream)
        await asyncio.sleep(0.03)
        assert len(produced) <= 66
        await stream.aclose()

    asyncio.run(consume())
    assert exited.wait(1)


def test_native_event_serialization_has_no_legacy_message_envelope():
    event = ResearchEvent("task.completed", {"task": {"id": "task_a"}}, "task_a")
    assert event.to_dict() == {
        "schema_version": 2,
        "type": "task.completed",
        "task_id": "task_a",
        "data": {"task": {"id": "task_a"}},
    }


@pytest.mark.parametrize(
    "code",
    [
        '"{0._marker}".format(item)',
        '"{item[_marker]}".format(item=record)',
        '"{item._marker}".format_map({"item": item})',
        '"{0:{1._marker}}".format(5, item)',
        '"{value:{width[_marker]}}".format_map({"value": 5, "width": record})',
        'render = "{0._marker}".format\nrender(item)',
        'render = "{item[_marker]}".format_map\nrender({"item": record})',
        'render = str.format\nrender("{0._marker}", item)',
        'render = str.format_map\nrender("{item._marker}", {"item": item})',
        'template = "{0." + "_marker}"\ntemplate.format(item)',
        '"{0._TaskQuery__lookup.__self__.marker}".format(graph)',
    ],
)
def test_planner_formatting_rejects_private_traversal(code):
    from types import SimpleNamespace

    class FixtureOwner:
        marker = "synthetic-value"

        def lookup(self, task_id):
            return {}

    program = PlannerProgram(isolated=True)
    program.inject("item", SimpleNamespace(_marker="synthetic-value"))
    program.inject("record", {"_marker": "synthetic-value"})
    program.inject("graph", TaskQuery(FixtureOwner().lookup))
    result = program.execute(code)
    assert not result.success and "private fields" in result.error
    assert result.result is None and result.stdout == ""


@pytest.mark.parametrize(
    "code,expected",
    [
        ('"{0:07.2f} {name}".format(2.5, name="value")', "0002.50 value"),
        ('"{value:{width}.{precision}f}".format(value=2.5, width=6, precision=2)', "  2.50"),
        ('"{name}: {counts[0]}".format_map({"name": "value", "counts": [4]})', "value: 4"),
        ('render = "{} {:.1f}".format\nrender("value", 2.5)', "value 2.5"),
        ('render = str.format_map\nrender("{name}", {"name": "value"})', "value"),
        ('"{{literal}} {0!r}".format("value")', "{literal} 'value'"),
    ],
)
@pytest.mark.parametrize("isolated", [False, True])
def test_planner_preserves_public_string_formatting(code, expected, isolated):
    result = PlannerProgram(isolated=isolated).execute(code)
    assert result.success, result.error
    assert result.result == expected


def test_format_binder_does_not_provide_arbitrary_attribute_access():
    from types import SimpleNamespace
    from veritrace.agent.runtime.formatting import format_method

    with pytest.raises(ValueError, match="Only string formatting"):
        format_method(SimpleNamespace(_marker="synthetic-value"), "_marker")


@pytest.mark.parametrize(
    "field",
    [
        "gi_frame",
        "cr_frame",
        "ag_frame",
        "f_back",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_code",
        "tb_frame",
        "tb_next",
        "gi_code",
        "co_consts",
    ],
)
@pytest.mark.parametrize("formatting", [False, True])
def test_planner_rejects_execution_attributes_on_synthetic_objects(field, formatting):
    from types import SimpleNamespace

    program = PlannerProgram(isolated=True)
    program.inject("item", SimpleNamespace(**{field: "synthetic-value"}))
    code = ('"{0.' + field + '}".format(item)') if formatting else ("item." + field)
    result = program.execute(code)
    assert not result.success and "execution state" in result.error
    assert result.result is None and result.stdout == ""


def test_task_query_accepts_the_documented_node_name_keyword():
    program = PlannerProgram()
    program.inject("graph", TaskQuery(lambda name: {"id": name}))
    result = program.execute('graph.node(node_name="task_a")')
    assert result.success and result.result == {"id": "task_a"}
    result = program.execute('graph.node("task_b")')
    assert result.success and result.result == {"id": "task_b"}


@pytest.mark.parametrize("isolated", [False, True])
def test_public_mapping_keys_are_not_treated_as_execution_attributes(isolated):
    result = PlannerProgram(isolated=isolated).execute(
        '"{record[f_globals]}".format(record={"f_globals": "public data"})'
    )
    assert result.success and result.result == "public data"


def test_task_queries_expose_report_and_evidence_fields_without_mutating_state():
    task = {"id": "task_a", "question": "Find revenue", "status": "completed", "result": {
        "answer": "Revenue is 12 million [[0]]", "references": {"0": {"url": "https://example.test"}},
        "messages": [{"role": "assistant", "content": "Search trace"}],
        "quality_results": [{"score": 4}], "feedback": {"reason": "Verified"},
        "evidence_records": [{"quote": "Revenue is 12 million"}],
    }}
    program = PlannerProgram()
    program.inject("graph", TaskQuery(lambda name: task if name == "task_a" else {}))
    result = program.execute('info = graph.node(node_name="task_a")\n'
                             '(info["content"], info["type"], info["response"], info["memory"], info["result"]["answer"])')
    assert result.success, result.error
    content, kind, response, memory, answer = result.result
    assert (content, kind, answer) == ("Find revenue", "searcher", task["result"]["answer"])
    assert response == {"content": answer, "stream_state": 6}
    assert memory == {"agent.memory": task["result"]["messages"], "ref2url": task["result"]["references"],
                      "quality_results": task["result"]["quality_results"], "feedback": task["result"]["feedback"],
                      "evidence_records": task["result"]["evidence_records"]}
    memory["ref2url"].clear()
    assert task["result"]["references"]
    assert program.execute('graph.node("missing")').result == {}


def test_pending_task_queries_do_not_invent_completed_results():
    query = TaskQuery(lambda name: {"id": name, "question": "Pending search", "status": "running", "result": None})
    node = query.node("task_a")
    assert node["content"] == "Pending search"
    assert "response" not in node and "memory" not in node


def test_planner_context_queries_do_not_create_synthetic_tasks():
    tasks = {}
    finished = False
    query = TaskQuery(lambda name: tasks.get(name, {}), "Original question", lambda: finished)
    assert query.node("root") == {"content": "Original question", "type": "root"}
    assert query.node("response") == {}
    finished = True
    assert query.node("response") == {"type": "end"}
    assert tasks == {}


def test_failed_task_query_preserves_failure_without_inventing_evidence():
    query = TaskQuery(lambda name: {"id": name, "question": "Failed search", "status": "failed",
                                   "result": {"answer": "[Search failed: offline]", "error": "offline"}})
    node = query.node("task_a")
    assert node["response"] == {"content": "[Search failed: offline]", "stream_state": 6}
    assert "memory" not in node
    assert node["result"]["error"] == "offline"
