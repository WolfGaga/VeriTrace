"""Publish prepared writing artifacts before the report-writing call returns."""

import json
import threading
from types import SimpleNamespace

import pytest

from veritrace.agent import writing
from veritrace.agent.evidence_store import EvidenceRecord, EvidenceStore
from veritrace.agent.report_extractor import extract_pipeline_data
from veritrace.agent.veritrace_agent import VeritraceAgent
from veritrace.agent.outline_planner import InsightWithEvidence, ReportOutline, SectionPlan, UserRequirement


@pytest.mark.parametrize("writer_fails", [False, True])
def test_agent_exposes_prepared_snapshot_while_writer_is_active(monkeypatch, writer_fails):
    agent = VeritraceAgent.__new__(VeritraceAgent)
    agent.llm_provider = "fixture"
    agent.writer_language = "en"
    agent.include_search_refs = False
    agent._pipeline_data = None
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
            "https://fixture.example",
            "Fixture source",
            "Revenue is 12 million.",
            "12 million",
            "revenue",
            0,
            "research-company",
        )
    )
    references = {1: {"url": "https://fixture.example", "title": "Fixture source"}}
    responses = [
        {
            "overall_theme": "Company",
            "sections": [
                {
                    "section_id": "1",
                    "title": "Revenue",
                    "description": "Company revenue",
                    "relevant_node_ids": ["company"],
                }
            ],
        },
        {"insights": [{"insight": "Revenue is established", "evidence_ids": [1]}]},
    ]

    class Model:
        def chat(self, messages):
            if len(responses) == 2:
                assert extract_pipeline_data(agent) is None
            else:
                snapshot = extract_pipeline_data(agent)
                assert snapshot["outline"][0]["section_id"] == "1"
                assert snapshot["evidence_store"] == store.to_evidence_table()
            return type("Response", (), {"content": json.dumps(responses.pop(0))})()

    observed = []
    created = []
    pipeline_type = writing.WritingPipeline

    def pipeline_factory(*args, **kwargs):
        kwargs.update(outline_model=None, section_planner_model=None, section_writer_model=None)
        pipeline = pipeline_type(*args, **kwargs)
        created.append((pipeline, pipeline.data))
        return pipeline

    class InspectingWriter:
        def __init__(self, llm, **kwargs):
            self.section_texts = {}

        def write_report(self, **kwargs):
            snapshot = extract_pipeline_data(agent)
            assert snapshot is not None, "Prepared evidence and outline must be visible while writing"
            assert snapshot is created[0][0].data is created[0][1]
            assert snapshot["evidence_store"] == store.to_evidence_table()
            assert snapshot["cognitive_graph"] == graph
            assert snapshot["outline"][0]["section_id"] == "1"
            assert snapshot["outline"][0]["insights"][0]["evidence_ids"] == [1]
            assert snapshot["cited_references"] == references
            assert snapshot["section_texts"] == {}
            observed.append(snapshot)
            self.section_texts["1"] = "Completed section [[1]]."
            if writer_fails:
                raise RuntimeError("writer failed after one section")
            return "Completed report [[1]]."

    monkeypatch.setattr(writing, "WritingPipeline", pipeline_factory)
    monkeypatch.setattr(writing, "SectionWriter", InspectingWriter)
    arguments = (Model(), graph, store, "What is the revenue?", references)
    if writer_fails:
        with pytest.raises(RuntimeError, match="writer failed after one section"):
            agent._run_section_writer_pipeline(*arguments)
    else:
        assert agent._run_section_writer_pipeline(*arguments) == "Completed report [[1]]."

    snapshot = extract_pipeline_data(agent)
    assert snapshot is observed[0]
    assert snapshot["section_texts"] == {"1": "Completed section [[1]]."}
    assert snapshot["evidence_store"] == store.to_evidence_table()
    assert not responses


def test_real_writer_publishes_completed_section_while_next_section_is_blocked(monkeypatch):
    entered_second, release_second = threading.Event(), threading.Event()
    graph = {"nodes": {"company": {"name": "Company", "related_tasks": ["research-company"]}}}
    store = EvidenceStore()
    store.add_record(EvidenceRecord(1, "https://fixture.example", "Source", "Revenue is 12 million.", "12 million", "revenue", 0, "research-company"))
    refs = {1: {"url": "https://fixture.example", "title": "Source"}}
    outline = ReportOutline(
        sections=[SectionPlan(str(i), f"Section {i}", "Revenue", ["company"],
                              insights=[InsightWithEvidence("Revenue is established", [1])],
                              relevant_criteria=["revenue"]) for i in (1, 2)],
        overall_theme="Company", user_requirements=[UserRequirement(1, "Technical readers", "audience")],
    )

    class Outline:
        def __init__(self, *args, **kwargs):
            pass

        def plan_outline(self, *args):
            return outline

    class Plan:
        def __init__(self, *args, **kwargs):
            pass

        def plan_all_sections(self, *args):
            return outline

    class Model:
        def __init__(self):
            self.calls = []

        def chat(self, messages):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return SimpleNamespace(content="## First\nRevenue is established [[1]].")
            if len(self.calls) == 2:
                entered_second.set()
                assert release_second.wait(2)
                return SimpleNamespace(content="## Second\nRevenue details [[1]].")
            assert len(self.calls) == 3
            return SimpleNamespace(content="## Executive Summary\nRevenue is established.")

    model = Model()
    actual_writer = writing.SectionWriter
    writers, section_dicts = [], []

    def make_writer(*args, **kwargs):
        writer = actual_writer(*args, **kwargs)
        writers.append(writer)
        section_dicts.append(writer.section_texts)
        return writer

    monkeypatch.setattr(writing, "OutlinePlanner", Outline)
    monkeypatch.setattr(writing, "GlobalSectionPlanner", Plan)
    monkeypatch.setattr(writing, "SectionWriter", make_writer)
    pipeline = writing.WritingPipeline(model, "fixture", outline_model=None, section_planner_model=None, section_writer_model=None)
    results, errors = [], []

    def run():
        try:
            results.append(pipeline.run("Question", graph, store, refs))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert entered_second.wait(1)
        assert pipeline.data["section_texts"] is section_dicts[0] is writers[0].section_texts
        assert pipeline.data["section_texts"] == {"1": "## First\nRevenue is established [[1]]."}
        assert pipeline.data["outline"][0]["relevant_criteria"] == ["revenue"]
        assert pipeline.data["user_requirements"] == [{"id": 1, "requirement": "Technical readers", "type": "audience"}]
        assert "## First\nRevenue is established ." in model.calls[1][0].content
    finally:
        release_second.set()
        thread.join(2)
    assert not thread.is_alive() and not errors
    assert len(model.calls) == 3 and len(results) == 1
    assert pipeline.data["section_texts"] is section_dicts[0]
    assert set(pipeline.data["section_texts"]) == {"1", "2"}
    assert results[0].startswith("## Executive Summary")
