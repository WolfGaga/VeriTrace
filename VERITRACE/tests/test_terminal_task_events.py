"""Terminal research paths publish completed work before the report."""

import threading

import pytest

from veritrace.agent import veritrace_agent as engine
from veritrace.agent.runtime.scheduler import TaskScheduler


@pytest.mark.parametrize("soft_deadline", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_terminal_paths_drain_completed_tasks_before_report(monkeypatch, scripted_llm, soft_deadline, failed):
    settled = threading.Event()
    clock = {"now": 0.0}
    schedulers = []

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            if failed:
                raise RuntimeError("Search unavailable")
            return {"content": "Retained finding", "ref2url": {}}

    class Scheduler(TaskScheduler):
        def __init__(self, **kwargs):
            super().__init__(searcher_factory=Searcher, max_workers=1, **kwargs)
            schedulers.append(self)

        def _complete(self, *args):
            super()._complete(*args)
            settled.set()

        def set_question(self, question):
            super().set_question(question)
            self.add_task("task_ready", "Research detail", cognitive_edge_id="r1", cognitive_target_id="e1")
            assert settled.wait(2)
            assert self.pending_count == 0
            clock["now"] = 120.0

    monkeypatch.setattr("time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("veritrace.agent.config.SOFT_DEADLINE_MINUTES", 1 if soft_deadline else 0)
    monkeypatch.setattr(engine, "TaskScheduler", Scheduler)
    model = scripted_llm([])
    monkeypatch.setattr(engine, "create_adapter", lambda *args, **kwargs: model)
    monkeypatch.setattr(engine.VeritraceAgent, "_finish_report", lambda self, graph, state, prefix="":
                        engine.ResearchEvent("research.completed", {"tasks": graph.tasks, "article": "Report"}))
    agent = engine.VeritraceAgent(llm_provider="fixture", enable_cognitive_graph=False,
                                 max_turn=2 if soft_deadline else 0)
    events = list(agent("Research question"))
    expected = "task.failed" if failed else "task.completed"
    assert [event.type for event in events if event.type.startswith("task.")] == [expected]
    assert events[-1].type == "research.completed"
    assert events[-1].data["tasks"]["task_ready"]["status"] == ("failed" if failed else "completed")
    assert not model.calls
    assert schedulers[0]._closed
