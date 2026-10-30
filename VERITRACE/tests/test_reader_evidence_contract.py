import json

import pytest

from veritrace.agent.evidence_store import EvidenceStore
from veritrace.agent.reader import ParallelReaderExecutor, ReaderAgent, ReaderInput


def page_input():
    return ReaderInput(
        page_index=7,
        page_url="https://source.example/report",
        page_title="Source report",
        page_content="Revenue was 12.5 million. 收入为一千二百五十万元。",
        original_question="What changed?",
        task_goal="Find revenue",
        acceptance_criteria=["revenue", "margin"],
    )


@pytest.mark.parametrize("language", ["en", "cn"])
def test_read_page_keeps_only_quoted_findings_and_source_identity(scripted_llm, language):
    quote = "  Revenue was 12.5 million. 收入为一千二百五十万元。  "
    payload = {
        "findings": {
            "revenue": {"answer": "12.5 million [[7]]", "evidence": quote},
            "margin": {"answer": "20%", "evidence": "  "},
            "unquoted_list": ["a", "b"],
        },
        "gaps": ["margin"],
        "unexpected_insights": [
            {"label": "timing", "insight": "An update", "evidence": "Published in May"},
            {"label": "unquoted", "insight": "Unsupported"},
        ],
        "quality_scores": {
            "currency": 5,
            "relevance": 4,
            "authority": 3,
            "accuracy": 4,
            "purpose": 5,
        },
        "accessibility_notes": [
            {"criterion": "margin", "barrier": "paywall", "detail": "Full report"},
            {"criterion": "other"},
        ],
    }
    llm = scripted_llm(["```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"])
    result = ReaderAgent(llm, language=language).read_page(page_input())

    assert result.success
    assert (result.page_index, result.page_url, result.page_title) == (
        7,
        "https://source.example/report",
        "Source report",
    )
    assert result.findings == {"revenue": {"answer": "12.5 million [[7]]", "evidence": quote}}
    assert result.gaps == ["margin"]
    assert result.unexpected_insights == payload["unexpected_insights"][:1]
    assert result.accessibility_notes == payload["accessibility_notes"][:1]
    assert result.quality_scores == {**payload["quality_scores"], "overall": 4.2, "cr": 4.3, "aap": 3.85}
    assert len(llm.calls) == 1
    prompt = llm.calls[0][0][0].content
    assert page_input().page_content in prompt
    assert "[[7]]" in prompt
    assert "revenue" in prompt


@pytest.mark.parametrize("response", ["not JSON", {"findings": ["wrong shape"]}, ValueError("provider rejected")])
def test_read_page_failure_retains_identity(scripted_llm, response):
    result = ReaderAgent(scripted_llm([response])).read_page(page_input())
    assert not result.success
    assert result.error
    assert result.page_index == 7
    assert result.page_url == "https://source.example/report"
    assert not result.findings


def test_parallel_reader_to_evidence_export_preserves_attribution(scripted_llm):
    def reply(messages, **kwargs):
        prompt = messages[0].content
        if "SOURCE_C_FAILURE" in prompt:
            return RuntimeError("source unavailable")
        label = "A" if "SOURCE_A_TEXT" in prompt else "B"
        return {
            "findings": {"revenue": {"answer": f"Answer {label}", "evidence": f"  Quote {label}  "}},
            "unexpected_insights": [{"label": label, "insight": label, "evidence": f"Extra {label}"}],
            "accessibility_notes": [{"criterion": "margin", "barrier": "paywall"}],
        }

    llm = scripted_llm(reply)
    pages = [
        {"index": 4, "url": "https://a.example", "title": "A", "content": "SOURCE_A_TEXT"},
        {"index": 9, "url": "https://b.example", "title": "B", "content": "SOURCE_B_TEXT"},
        {"index": 12, "url": "https://c.example", "title": "C", "content": "SOURCE_C_FAILURE"},
    ]
    merged = ParallelReaderExecutor(llm, max_workers=3).read_pages(
        pages,
        task_content="Read the reports",
        acceptance_criteria=["revenue", "margin"],
    )
    assert set(merged.pages_processed) == {4, 9}
    assert merged.failed_pages == [12]
    assert merged.remaining_gaps == ["margin"]
    assert {index: finding["evidence"] for index, finding in merged.all_findings["revenue"]} == {
        4: "  Quote A  ",
        9: "  Quote B  ",
    }
    assert {x["page_index"] for x in merged.all_unexpected_insights} == {4, 9}
    assert {x["page_index"] for x in merged.accessibility_notes} == {4, 9}

    store = EvidenceStore()
    count = store.add_from_reader_results(
        merged.all_findings,
        {p["index"]: p for p in pages},
        "task-revenue",
        {4: 101, 9: 102},
    )
    assert count == 2
    table = store.to_evidence_table()
    assert table[101] == {
        "url": "https://a.example",
        "title": "A",
        "entries": [
            {"criterion": "revenue", "quote": "Quote A", "finding_answer": "Answer A", "search_node": "task-revenue"}
        ],
    }
    assert table[102]["url"] == "https://b.example"
    assert table[102]["entries"][0]["quote"] == "Quote B"
    assert store.records[101][0].page_idx == 4
    assert set(store.get_evidence_for_nodes(["task-revenue"])) == {101, 102}
    restored = EvidenceStore.from_evidence_table(json.loads(json.dumps(table)))
    assert restored.to_evidence_table() == table


def test_evidence_import_drops_missing_mapping_and_empty_quotes():
    store = EvidenceStore()
    count = store.add_from_reader_results(
        {
            "revenue": [
                (1, {"answer": "valid", "evidence": "one"}),
                (2, {"answer": "unmapped", "evidence": "two"}),
                (1, {"answer": "unsupported", "evidence": " "}),
                (1, "malformed"),
            ]
        },
        {1: {"url": "https://one.example", "title": "One"}},
        "search-one",
        {1: 8},
    )
    assert count == 1
    assert len(store) == 1
    assert store.records[8][0].finding_answer == "valid"
    assert store.records[8][0].url == "https://one.example"
