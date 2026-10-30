import asyncio
import logging
import threading

import pytest

from veritrace.agent import veritrace_agent as engine
from veritrace.agent.runtime.scheduler import TaskScheduler
from veritrace.agent.report_extractor import extract_pipeline_data


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_diagnostic_verbosity_preserves_research_and_keeps_payloads_opt_in(research_harness, caplog, level):
    caplog.set_level(level)
    agent, providers, searches, schedulers = research_harness()
    question = "Private research context {customer: sample}"
    events = list(agent(question, session_id=111))
    completed, = [event for event in events if event.type == "research.completed"]
    assert "Revenue is 12 million [[1]]" in completed.data["article"]
    assert searches and all(not scheduler.pending_count for scheduler in schedulers)
    assert "[Research:111] [TaskScheduler] Task 'research-company' started" in caplog.text
    assert "ended; status=completed" in caplog.text
    detailed = level == logging.DEBUG
    assert (question in caplog.text) is detailed
    assert ("LLM RESPONSE" in caplog.text) is detailed
    assert ('add_task(node_name="research-company"' in caplog.text) is detailed


def test_default_planner_research_accepts_baseline_dynamic_calls(monkeypatch, research_harness):
    monkeypatch.delenv("PLANNER_ISOLATION", raising=False)
    agent, providers, searches, schedulers = research_harness()
    original = providers["planner"].responses[0]
    checks = ('assert hasattr(graph, "node")\n'
              'assert type(graph.node("root")) == dict\n'
              'assert "root" in graph.nodes\n')
    providers["planner"].responses[0] = original.replace('```python\n', '```python\n' + checks).replace(
        'add_task(', 'getattr(graph, "add_task")(', 1)
    providers["planner"].responses[-1] = "```python\ngraph.finish()\n```"
    assert not agent.interpreter.isolated
    events = list(agent("Baseline-compatible research", session_id=112))
    completed, = [event for event in events if event.type == "research.completed"]
    assert "Revenue is 12 million [[1]]" in completed.data["article"]
    assert len(searches) == 1 and all(not scheduler.pending_count for scheduler in schedulers)


@pytest.mark.parametrize("isolated", [False, True])
def test_engine_planner_mode_is_explicit_and_preserves_the_normal_flow(monkeypatch, research_harness, isolated):
    monkeypatch.setenv("PLANNER_ISOLATION", "true" if isolated else "false")
    agent, providers, searches, schedulers = research_harness()
    assert agent.interpreter.isolated is isolated
    events = list(agent("Research with explicit execution mode", session_id=113))
    completed, = [event for event in events if event.type == "research.completed"]
    assert "Revenue is 12 million [[1]]" in completed.data["article"]
    assert len(searches) == 1 and all(not scheduler.pending_count for scheduler in schedulers)


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("max_turn", [1, 2])
def test_completed_searches_count_toward_round_budget_before_results_are_drained(
    monkeypatch, scripted_llm, failed, max_turn
):
    completed = {f"task_fast_{index}": threading.Event() for index in range(1, max_turn * 3 + 1)}
    searches, schedulers = [], []

    def plan(messages, **kwargs):
        name = f"task_fast_{len(planner.calls)}"
        return ('```python\n'
                f'add_task(node_name="{name}", node_content="Research detail", '
                'cognitive_edge_id="r1", cognitive_target_id="e1")\n```')

    planner = scripted_llm(plan)

    class Searcher:
        def __init__(self, **kwargs):
            pass

        def search_and_summarize(self, **kwargs):
            searches.append(kwargs)
            if failed:
                raise RuntimeError("Search unavailable")
            return {"content": "Research finding", "ref2url": {}}

    class Scheduler(TaskScheduler):
        def __init__(self, **kwargs):
            super().__init__(searcher_factory=Searcher, max_workers=1, **kwargs)
            schedulers.append(self)

        def _complete(self, task_id, future, submitted_task):
            super()._complete(task_id, future, submitted_task)
            completed[task_id].set()

    monkeypatch.setattr(engine, "TaskScheduler", Scheduler)
    monkeypatch.setattr(engine, "create_adapter", lambda *args, **kwargs: planner)
    monkeypatch.setattr("veritrace.agent.config.SOFT_DEADLINE_MINUTES", 0)
    monkeypatch.setattr(engine.VeritraceAgent, "_finish_report", lambda self, graph, state, prefix="":
                        engine.ResearchEvent("research.completed", {"article": "Report", "tasks": graph.tasks}))
    agent = engine.VeritraceAgent(llm_provider="fixture", enable_cognitive_graph=False, max_turn=max_turn)
    events = []
    for event in agent("Research question"):
        events.append(event)
        if event.type == "tasks.updated":
            assert completed[f"task_fast_{len(planner.calls)}"].wait(timeout=2)
            assert schedulers[0].pending_count == 0

    assert len(planner.calls) == len(searches) == max_turn
    task_events = [event for event in events if event.type in ("task.completed", "task.failed")]
    assert len(task_events) == max_turn
    assert all(event.type == ("task.failed" if failed else "task.completed") for event in task_events)
    assert events[-1].type == "research.completed"
    assert len(events[-1].data["tasks"]) == max_turn
    assert schedulers[0]._closed


def initial_graph():
    return {
        "entities": [
            {"id": "root", "name": "Research question", "type": "start", "cognitive_state": "START"},
            {"id": "company", "name": "Company", "type": "placeholder", "cognitive_state": "UNKNOWN"},
        ],
        "relations": [
            {
                "id": "revenue-edge",
                "source": "root",
                "target": "company",
                "type": "research",
                "inquiry_goal": "Find company revenue",
            }
        ],
        "structure": {"type": "serial", "dependency_order": ["root", "company"]},
    }


def graph_update():
    return {
        "cognitive_updates": [
            {
                "node_id": "company",
                "state": "KNOWN",
                "discovered_items": ["FixtureCo"],
                "item_findings": {"FixtureCo": {"revenue": "Revenue is 12 million [[1]]"}},
                "core_pending": [],
                "supplementary_pending": [],
            }
        ]
    }


@pytest.fixture
def research_harness(monkeypatch, scripted_llm):
    def make(*, forced=False, async_mode=False):
        release_search = threading.Event()
        search_calls = []
        schedulers = []
        planner_reply = '```python\nadd_task(node_name="research-company", node_content="Find company revenue", cognitive_edge_id="revenue-edge", cognitive_target_id="company", core_criteria=["revenue"])\n```'
        providers = {
            "planner": scripted_llm([planner_reply] + ([] if forced else ["```python\nfinish()\n```"])),
            "cognitive": scripted_llm([initial_graph(), graph_update()]),
            "writer": scripted_llm(
                [
                    {
                        "overall_theme": "Company",
                        "sections": [
                            {
                                "section_id": "1",
                                "title": "Revenue",
                                "description": "Revenue finding",
                                "relevant_node_ids": ["company"],
                            }
                        ],
                    },
                    {"insights": [{"insight": "Revenue is established", "evidence_ids": [1]}]},
                    "## Revenue\nRevenue is 12 million [[1]]. Unsupported [[999]].",
                    "## Executive Summary\nRevenue is established.",
                ]
            ),
        }

        class Searcher:
            def __init__(self, **kwargs):
                pass

            def search_and_summarize(self, **kwargs):
                assert release_search.wait(timeout=2), "Research did not start waiting for its search task"
                search_calls.append(kwargs)
                return {
                    "content": "Revenue is 12 million [[0]].",
                    "ref2url": {0: {"url": "https://source.example/revenue", "title": "Revenue source"}},
                    "quality_results": [{"index": 0, "cr": 4.5, "aap": 4.0}],
                    "feedback": {"can_improve": False, "reason": "Criterion answered"},
                    "memory": [],
                    "evidence_records": [
                        {
                            "ref_idx": 0,
                            "url": "https://source.example/revenue",
                            "title": "Revenue source",
                            "evidence_quote": "Revenue was 12 million.",
                            "finding_answer": "12 million",
                            "criterion": "revenue",
                        }
                    ],
                }

        class Scheduler(TaskScheduler):
            def __init__(self, **kwargs):
                super().__init__(searcher_factory=Searcher, max_workers=1, **kwargs)
                schedulers.append(self)

            def iter_completed(self, *args, **kwargs):
                release_search.set()
                yield from super().iter_completed(*args, **kwargs)

        monkeypatch.setattr(engine, "TaskScheduler", Scheduler)
        monkeypatch.setattr(engine, "create_adapter", lambda provider, model, **kwargs: providers[model])
        monkeypatch.setattr("veritrace.agent.config.SOFT_DEADLINE_MINUTES", 0)
        agent_type = engine.AsyncVeritraceAgent if async_mode else engine.VeritraceAgent
        agent = agent_type(
            llm_provider="fixture",
            llm_model="planner",
            cognitive_model="cognitive",
            writer_model="writer",
            max_turn=1 if forced else 3,
            language="en",
            include_search_refs=False,
        )
        return agent, providers, search_calls, schedulers

    return make


@pytest.mark.parametrize("drained", [False, True])
def test_forced_report_logs_unfinished_tasks_without_claiming_completion(monkeypatch, caplog, drained):
    caplog.set_level(logging.INFO)

    class PendingScheduler(TaskScheduler):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._tasks["unfinished"] = {"id": "unfinished", "question": "Question", "status": "running", "result": None}
            self._pending.add("unfinished")

        def iter_completed(self, *args, **kwargs):
            if drained:
                self._pending.clear()
                self._tasks["unfinished"]["status"] = "completed"
            return iter(())

    monkeypatch.setattr(engine, "TaskScheduler", PendingScheduler)
    monkeypatch.setattr(engine, "create_adapter", lambda *args, **kwargs: object())
    monkeypatch.setattr(engine.VeritraceAgent, "_finish_report", lambda *args, **kwargs:
                        engine.ResearchEvent("research.completed", {"article": "Retained report"}))
    agent = engine.VeritraceAgent(llm_provider="fixture", enable_cognitive_graph=False, max_turn=0)
    events = list(agent("Question"))
    assert events[-1].data["article"] == "Retained report"
    assert ("All remaining tasks completed" in caplog.text) == drained
    assert ("Search wait ended with unfinished tasks" in caplog.text) != drained
    if not drained:
        assert "pending=['unfinished']" in caplog.text
        assert "generating report from collected evidence" in caplog.text


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("forced", [False, True])
def test_research_completes_with_consistent_evidence_graph_and_report(research_harness, forced, async_mode):
    agent, providers, search_calls, schedulers = research_harness(forced=forced, async_mode=async_mode)

    async def collect():
        return [event async for event in agent("What is company revenue?", session_id=17)]

    events = asyncio.run(collect()) if async_mode else list(agent("What is company revenue?", session_id=17))
    terminal = [
        event for event in events if event.type in ("research.completed", "research.failed", "research.cancelled")
    ]
    assert len(terminal) == 1
    assert terminal[0].type == "research.completed"
    result = terminal[0].data
    assert "Revenue is 12 million [[1]]." in result["article"]
    assert "[[999]]" not in result["article"]
    assert result["ref2url"][1]["url"] == "https://source.example/revenue"
    assert result["cognitive_graph"]["nodes"]["company"]["item_findings"]["FixtureCo"]["revenue"].endswith("[[1]]")
    assert result["cognitive_graph"]["nodes"]["company"]["related_tasks"] == ["research-company"]
    assert result["tasks"]["research-company"]["status"] == "completed"
    assert agent._pipeline_data["evidence_store"][1]["entries"][0]["quote"] == "Revenue was 12 million."
    assert "[[1]]" in providers["cognitive"].calls[1][0][0].content
    assert len(search_calls) == 1
    assert search_calls[0]["topic"] == "What is company revenue?"
    assert len(providers["planner"].calls) == (1 if forced else 2)
    assert len(providers["writer"].calls) == 4
    assert schedulers[0]._closed
    assert agent._scheduler is None


def test_reused_agent_failure_does_not_expose_previous_session_artifacts(research_harness):
    agent, providers, _, schedulers = research_harness()
    first = list(agent("First question"))
    assert first[-1].type == "research.completed"
    assert extract_pipeline_data(agent)["evidence_store"]
    agent._failed_cgm_nodes = {"previous-failed-task"}
    providers["cognitive"].responses.append(initial_graph())
    providers["planner"].responses.append(RuntimeError("second session failed"))

    with pytest.raises(RuntimeError, match="second session failed"):
        list(agent("Second question"))

    assert extract_pipeline_data(agent) is None
    assert not getattr(agent, "_failed_cgm_nodes", set())
    assert all(scheduler._closed for scheduler in schedulers)
    assert agent._scheduler is None


def test_engine_report_recovery_keeps_graph_references_and_completed_search(research_harness):
    from collections import deque
    from test_shared_writing_pipeline import outline, insight

    agent, providers, search_calls, _ = research_harness()
    providers["writer"].responses = deque([outline(2), insight(), insight(),
                                         "## First\nRevenue is 12 million [[1]].", RuntimeError("writing interrupted")])
    with pytest.raises(RuntimeError, match="writing interrupted"):
        list(agent("What is company revenue?"))
    checkpoint = agent.latest_checkpoint()
    assert checkpoint["writing"]["section_texts"] == {"1": "## First\nRevenue is 12 million [[1]]."}
    assert checkpoint["tasks"]["research-company"]["status"] == "completed"
    before = {name: len(model.calls) for name, model in providers.items()}
    providers["writer"].responses.extend(["## Second\nRevenue details [[1]].", "Summary"])
    agent.restore_checkpoint(checkpoint, "write_report")
    result = list(agent("What is company revenue?"))[-1]
    assert result.type == "research.completed"
    assert result.data["article"].count("## First") == 1
    assert result.data["ref2url"][1]["url"] == "https://source.example/revenue"
    assert len(providers["writer"].calls) == before["writer"] + 2
    assert len(providers["planner"].calls) == before["planner"]
    assert len(providers["cognitive"].calls) == before["cognitive"]
    assert len(search_calls) == 1


def test_engine_continues_after_planner_failure_without_repeating_completed_search(research_harness):
    from collections import deque

    agent, providers, search_calls, _ = research_harness()
    providers["planner"].responses = deque([providers["planner"].responses[0], RuntimeError("planner unavailable")])
    with pytest.raises(RuntimeError, match="planner unavailable"):
        list(agent("What is company revenue?"))
    checkpoint = agent.latest_checkpoint()
    assert checkpoint["tasks"]["research-company"]["status"] == "completed"
    cognitive_calls = len(providers["cognitive"].calls)
    providers["planner"].responses.append("```python\nfinish()\n```")
    agent.restore_checkpoint(checkpoint, "continue")
    result = list(agent("What is company revenue?"))[-1]
    assert result.type == "research.completed"
    assert len(search_calls) == 1
    assert len(providers["cognitive"].calls) == cognitive_calls
    assert result.data["ref2url"][1]["url"] == "https://source.example/revenue"
    assert "Do not repeat completed search tasks" in providers["planner"].calls[-1][0][-1].content
