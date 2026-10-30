import logging

import pytest

from veritrace.agent.evidence_store import EvidenceRecord, EvidenceStore
from veritrace.agent.writing import WritingPipeline
from veritrace.agent.runtime.progress import progress_scope


def inputs():
    graph = {
        "nodes": {
            "company": {
                "name": "Company",
                "related_tasks": ["research-company"],
                "item_findings": {"Company": {"revenue": "12 million [[1]]"}},
            }
        }
    }
    store = EvidenceStore()
    store.add_record(
        EvidenceRecord(
            1,
            "https://source.example",
            "Source",
            "Revenue is 12 million.",
            "12 million",
            "revenue",
            0,
            "research-company",
        )
    )
    refs = {1: {"url": "https://source.example", "title": "Source"}}
    return graph, store, refs


def outline(count=1):
    return {
        "overall_theme": "Company",
        "sections": [
            {
                "section_id": str(i),
                "title": f"Section {i}",
                "description": "Company revenue",
                "relevant_node_ids": ["company"],
            }
            for i in range(1, count + 1)
        ],
    }


def insight():
    return {"insights": [{"insight": "Revenue is established", "evidence_ids": [1]}]}


@pytest.mark.parametrize("timeout", [None, 300.0])
@pytest.mark.parametrize("models", [(None, None, None), ("outline", None, "writer"), (None, "planner", None)])
def test_run_preserves_model_fallback_timeout_and_report(scripted_llm, timeout, models, caplog):
    caplog.set_level(logging.INFO)
    outline_model, planner_model, writer_model = models
    roles = [outline_model, planner_model or outline_model, writer_model, writer_model]
    responses = [
        outline(),
        insight(),
        "## Company\nRevenue is 12 million [[1]].",
        "## Executive Summary\nRevenue is established.",
    ]
    queues = {}
    for model, response in zip(roles, responses):
        queues.setdefault(model, []).append(response)
    providers = {model: scripted_llm(queue) for model, queue in queues.items()}
    for model, llm in providers.items():
        llm.model = model or "base"
    calls = []

    def factory(provider, model, **kwargs):
        calls.append((provider, model, kwargs))
        return providers[model]

    pipeline = WritingPipeline(
        providers.get(None),
        "fixture",
        include_search_refs=False,
        adapter_factory=factory,
        adapter_timeout=timeout,
        outline_model=outline_model,
        section_planner_model=planner_model,
        section_writer_model=writer_model,
    )
    graph, store, refs = inputs()
    progress = []
    with progress_scope(progress.append):
        report = pipeline.run("What is the revenue?", graph, store, refs)
    stages = [event.data["stage"] for event in progress]
    assert stages == ["planning_outline", "planning_sections", "planning_sections", "writing_section", "writing_summary", "finalizing"]
    assert progress[3].data["section_index"] == progress[3].data["section_total"] == 1
    options = {} if timeout is None else {"timeout_override": timeout}
    assert calls == [("fixture", model, options) for model in roles[:3] if model]
    assert report.startswith("## Executive Summary\nRevenue is established.")
    assert "Revenue is 12 million [[1]]." in report
    assert "https://source.example" in report
    assert pipeline.data["section_texts"] == {"1": "## Company\nRevenue is 12 million [[1]]."}
    assert pipeline.data["evidence_store"] == store.to_evidence_table()
    assert pipeline.data["cited_references"] == refs
    assert pipeline.data["outline"][0]["insights"][0]["evidence_ids"] == [1]
    assert sum(len(llm.calls) for llm in providers.values()) == 4
    summary, = [record.getMessage() for record in caplog.records if "[WritingPipeline] Configured models" in record.getMessage()]
    for role, model in zip(("outline", "section_planner", "section_writer"), roles):
        assert f"{role}={model or 'base'}" in summary


def test_run_retains_completed_sections_when_later_writer_fails(scripted_llm):
    llm = scripted_llm(
        [outline(2), insight(), insight(), "## First\nEstablished [[1]].", RuntimeError("second section failed")]
    )
    pipeline = WritingPipeline(
        llm, "fixture", outline_model=None, section_planner_model=None, section_writer_model=None
    )
    graph, store, refs = inputs()

    with pytest.raises(RuntimeError, match="second section failed"):
        pipeline.run("Question", graph, store, refs)

    assert pipeline.data["section_texts"] == {"1": "## First\nEstablished [[1]]."}
    assert len(pipeline.data["outline"]) == 2
    assert pipeline.data["evidence_store"] == store.to_evidence_table()
    assert pipeline.data["cited_references"] == refs
    assert len(llm.calls) == 5


def test_section_planning_failure_falls_back_to_full_section_evidence(scripted_llm):
    llm = scripted_llm([outline(), RuntimeError("planning failed"), "## Company\nRevenue [[1]].", "Summary"])
    pipeline = WritingPipeline(
        llm, "fixture", outline_model=None, section_planner_model=None, section_writer_model=None
    )
    graph, store, refs = inputs()

    report = pipeline.run("Question", graph, store, refs)

    assert "Revenue [[1]]." in report
    assert "Revenue is 12 million." in llm.calls[2][0][0].content
    assert pipeline.data["outline"][0]["insights"] == []
