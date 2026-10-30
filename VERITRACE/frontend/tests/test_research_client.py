import importlib.util
import json
from pathlib import Path
import sys

PATH = Path(__file__).resolve().parents[1] / "research_client.py"
SPEC = importlib.util.spec_from_file_location("research_client", PATH)
client = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = client
SPEC.loader.exec_module(client)


def test_sse_multiline_and_utf8():
    payload = {"schema_version": 2, "type": "research.started", "data": {"question": "中文问题"}}
    lines = [b": keepalive", b"event: response", ("data: " + json.dumps(payload, ensure_ascii=False)).encode("utf-8"), b""]
    assert list(client.parse_sse(lines)) == [payload]
    assert list(client.parse_sse(['data: {"schema_version": 2,', 'data: "type":"research.cancelled","data":{}}']))[0]["type"] == "research.cancelled"


def test_native_task_report_usage():
    view = client.ResearchView()
    view.apply({"schema_version": 2, "type": "research.started", "data": {"question": "Question"}})
    task = {"id": "task", "question": "Task", "status": "completed", "result": {
        "answer": "Answer [[0]]", "references": {"0": {"url": "https://example.test", "title": "Source"}}}}
    view.apply({"schema_version": 2, "type": "task.completed", "data": {"task": task}})
    view.apply({"schema_version": 2, "type": "research.completed", "data": {"article": "Report", "tasks": [task]}})
    view.apply({"schema_version": 2, "type": "usage.updated", "data": {"usage": {"tokens": {"total": 3}}}})
    assert view.status == "completed"
    assert view.article == "Report"
    assert view.usage["tokens"]["total"] == 3
    assert "Source" in client.task_details(view.tasks["task"])
    assert "Task" in client.task_graph(view)


def test_graph_escapes_untrusted_labels():
    view = client.ResearchView(question="<script>bad()</script>")
    assert "<script>" not in client.task_graph(view)


def test_failure_and_cancel():
    view = client.ResearchView()
    view.apply({"schema_version": 2, "type": "research.failed", "data": {"error": "offline"}})
    assert (view.status, view.error) == ("failed", "offline")
    view.apply({"schema_version": 2, "type": "research.cancelled", "data": {}})
    assert view.status == "cancelled"


def test_mixed_references_preserve_urls_metadata_and_trace():
    references = {"0": "https://string.test/source?key=value#section", "1": {
        "url": "https://object.test/source", "title": "Object source", "snippet": "Excerpt", "publisher": "Publisher"}}
    task = {"id": "task", "question": "Question", "status": "completed", "result": {
        "answer": "Answer", "references": references, "messages": [{"content": "Search trace"}],
        "feedback": {"reason": "Feedback"}}}
    view = client.ResearchView()
    view.apply({"schema_version": 2, "type": "research.completed", "data": {
        "article": "Report", "ref2url": references, "tasks": [task]}})
    assert view.references["0"]["url"] == references["0"]
    assert view.references["1"] == references["1"]
    result = view.tasks["task"]["result"]
    assert result["references"]["0"]["url"] == references["0"]
    assert result["messages"] == task["result"]["messages"]
    assert result["feedback"] == task["result"]["feedback"]
    assert isinstance(task["result"]["references"]["0"], str)
    assert references["0"] in client.task_details(task)
    assert references["1"]["url"] in client.task_details(view.tasks["task"])
    details = client.task_details(view.tasks["task"])
    assert "Search trace" in details
    assert "Feedback" in details


def test_single_task_update_normalizes_string_reference():
    view = client.ResearchView()
    view.apply({"schema_version": 2, "type": "task.completed", "data": {"task": {
        "id": "task", "question": "Question", "result": {"answer": "Answer", "references": {0: "https://string.test"}}}}})
    assert view.tasks["task"]["result"]["references"][0] == {"url": "https://string.test"}


def test_progress_heartbeat_and_terminal_task_indicators():
    view = client.ResearchView(status="running", tasks={"task": {"id": "task", "status": "running"}})
    view.apply({"schema_version": 2, "type": "research.progress", "data": {
        "stage": "writing_section", "section_index": 2, "section_total": 3}})
    assert "Writing report" in view.status_text
    assert "2/3" in view.status_text
    view.apply({"schema_version": 2, "type": "research.heartbeat", "data": {"waiting_seconds": 30}})
    assert "connected, waiting 30s" in view.status_text
    view.apply({"schema_version": 2, "type": "research.failed", "data": {"error": "offline"}})
    assert view.status_text == "Failed"
    assert view.tasks["task"]["status"] == "cancelled"
    view.apply({"schema_version": 2, "type": "research.progress", "data": {"stage": "searching"}})
    assert view.status_text == "Failed"
