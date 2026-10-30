from pathlib import Path
import pytest


EVENTS = [
    {"schema_version": 2, "type": "research.started", "data": {"question": "Question"}},
    {"schema_version": 2, "type": "research.completed", "data": {"article": "Fixture report", "tasks": []}},
    {"schema_version": 2, "type": "usage.updated", "data": {"usage": {"tokens": {"total": 42}}}},
]


def test_gradio_native_events_and_component_construction(monkeypatch):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    pytest.importorskip("gradio")
    from frontend import veritrace_gradio
    monkeypatch.setattr(veritrace_gradio, "research_events", lambda *args, **kwargs: iter(EVENTS))
    frames = list(veritrace_gradio.run_research("Question", [], "en"))
    assert frames[-1][0][-1]["content"] == "Fixture report"
    assert frames[-1][4]["tokens"]["total"] == 42
    assert veritrace_gradio.build_app() is not None


def test_streamlit_native_report(monkeypatch):
    monkeypatch.setenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    from frontend import research_client
    monkeypatch.setattr(research_client, "research_events", lambda *args, **kwargs: iter(EVENTS))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "veritrace_streamlit.py")).run(timeout=10)
    assert not app.exception
    app.chat_input[0].set_value("Question").run(timeout=10)
    assert not app.exception
    assert app.session_state["research_view"].article == "Fixture report"


def test_gradio_preserves_task_history_and_cancels_only_the_active_round(monkeypatch):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    pytest.importorskip("gradio")
    from frontend import veritrace_gradio as ui

    def events(question, **options):
        task = {"id": "same-task-id", "question": question, "status": "completed",
                "result": {"answer": "Finding for " + question}}
        yield {"schema_version": 2, "type": "research.completed", "data": {"article": "Report for " + question, "tasks": [task]}}

    monkeypatch.setattr(ui, "research_events", events)
    first = list(ui.run_research("First question", [], "en"))[-1]
    second = list(ui.run_research("Second question", first[0], "en", first[6]))[-1]
    old_details = ui.select_research_round("0", second[6])[2]
    assert "Finding for First question" in old_details
    assert "Finding for Second question" not in old_details
    assert len(second[6]) == 2
    assert ui.select_research_round("1", second[6])[4].article == "Report for Second question"
    second[6][-1].status = "running"
    second[6][-1].tasks["same-task-id"]["status"] = "running"
    stopped = ui.stop_research(second[0], second[6])
    assert stopped[5].status == "cancelled"
    assert stopped[5].tasks["same-task-id"]["status"] == "cancelled"
    assert stopped[6][0].status == "completed"
    assert second[6][-1].status == "running", "Cancellation must not mutate an in-flight generator's view"
