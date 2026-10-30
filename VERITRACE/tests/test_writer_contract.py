import pytest

from veritrace.agent.core.llm_adapter import PayloadTooLargeError
from veritrace.agent.evidence_store import EvidenceRecord, EvidenceStore
from veritrace.agent.outline_planner import InsightWithEvidence, ReportOutline, SectionPlan
from veritrace.agent.section_writer import SectionWriter


def writing_input(insights=()):
    store = EvidenceStore()
    for ref, task, quote in [
        (1, "research-market", "EVIDENCE_ALPHA"),
        (2, "research-market", "EVIDENCE_BETA"),
        (3, "other-task", "EVIDENCE_UNRELATED"),
    ]:
        store.add_record(
            EvidenceRecord(
                ref_idx=ref,
                url=f"https://source.example/{ref}",
                title=f"Source {ref}",
                evidence_quote=quote,
                finding_answer=f"Finding {ref}",
                criterion="market",
                page_idx=ref - 1,
                search_node=task,
            )
        )
    outline = ReportOutline(
        overall_theme="Market",
        sections=[
            SectionPlan(
                section_id="1",
                title="Market",
                description="Explain the market",
                relevant_node_ids=["entity-market"],
                insights=list(insights),
            )
        ],
    )
    graph = {"nodes": {"entity-market": {"name": "Market entity", "related_tasks": ["research-market"]}}}
    references = {i: {"url": f"https://source.example/{i}", "title": f"Source {i}"} for i in (1, 2, 3)}
    return outline, graph, store, references


@pytest.mark.parametrize("language", ["en", "cn"])
@pytest.mark.parametrize("bound", [False, True])
def test_write_report_uses_real_evidence_selection_and_fallback(scripted_llm, language, bound):
    insights = [InsightWithEvidence(insight="Market finding", evidence_ids=[1])] if bound else []
    outline, graph, store, references = writing_input(insights)
    llm = scripted_llm(
        [
            "<think>private</think>## Market\nAlpha [[1]]. Beta [[2]]. Invalid [[999]].",
            "## Executive Summary\nThe market changed [[1]].",
        ]
    )
    writer = SectionWriter(llm, language=language, include_search_refs=False)
    report = writer.write_report(outline, graph, store, "How did the market change?", references)

    assert len(llm.calls) == 2
    section_prompt = llm.calls[0][0][0].content
    assert "EVIDENCE_ALPHA" in section_prompt
    assert ("EVIDENCE_BETA" in section_prompt) is not bound
    assert "EVIDENCE_UNRELATED" not in section_prompt
    assert "[[1]]" in writer.section_texts["1"]
    assert ("[[2]]" in writer.section_texts["1"]) is not bound
    assert "[[999]]" not in report
    assert "private" not in report
    assert report.startswith("## Executive Summary\nThe market changed .")
    assert "https://source.example/1" in report
    assert ("https://source.example/2" in report) is not bound
    assert "https://source.example/3" not in report
    assert len(store) == 3


def test_write_report_survives_executive_summary_failure(scripted_llm):
    outline, graph, store, references = writing_input()
    llm = scripted_llm(["## Market\nThe result [[1]].", RuntimeError("summary unavailable")])
    report = SectionWriter(llm, include_search_refs=False).write_report(
        outline,
        graph,
        store,
        "Question",
        references,
    )
    assert report.startswith("## Market\nThe result [[1]].")
    assert "https://source.example/1" in report
    assert len(llm.calls) == 2


def test_write_section_retries_smaller_payload_without_mutating_evidence(scripted_llm):
    outline, _, store, _ = writing_input()
    original_quote = "LONG_QUOTE_" * 60
    store.records[1][0].evidence_quote = original_quote
    llm = scripted_llm([PayloadTooLargeError("too large"), "Accepted [[1]]. Wrong [[999]]."])
    text = SectionWriter(llm).write_section(
        outline.sections[0],
        store.get_evidence_for_refs([1]),
        "",
        outline,
        "Question",
    )
    assert len(llm.calls) == 2
    assert len(llm.calls[1][0][0].content) < len(llm.calls[0][0][0].content)
    assert text == "Accepted [[1]]. Wrong ."
    assert store.records[1][0].evidence_quote == original_quote


def test_write_section_stops_after_payload_retry_budget(scripted_llm):
    outline, _, store, _ = writing_input()
    llm = scripted_llm([PayloadTooLargeError("too large") for _ in range(4)])
    with pytest.raises(PayloadTooLargeError):
        SectionWriter(llm).write_section(outline.sections[0], store.records, "", outline, "Question")
    assert len(llm.calls) == 4
