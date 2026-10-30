"""Writing helpers and explicitly enabled live model checks."""

import json
import sys
import os
import logging
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from veritrace.agent.evidence_store import EvidenceStore, EvidenceRecord
from veritrace.agent.outline_planner import OutlinePlanner, SectionPlan, ReportOutline
from veritrace.agent.section_planner import SectionPlanner, GlobalSectionPlanner
from veritrace.agent.section_writer import SectionWriter


# Test Fixtures


def make_test_evidence_store() -> EvidenceStore:
    """Create a realistic EvidenceStore for testing."""
    es = EvidenceStore()
    records = [
        EvidenceRecord(
            ref_idx=1,
            url="https://example.com/ai-market",
            title="AI Market Report 2024",
            evidence_quote="The global AI market reached $196.6 billion in 2023, growing at 36.6% CAGR.",
            finding_answer="AI market size $196.6B in 2023",
            criterion="market_size",
            page_idx=0,
            search_node="search_ai_market",
        ),
        EvidenceRecord(
            ref_idx=1,
            url="https://example.com/ai-market",
            title="AI Market Report 2024",
            evidence_quote="North America accounted for 42% of global AI revenue.",
            finding_answer="North America 42% share",
            criterion="regional_distribution",
            page_idx=0,
            search_node="search_ai_market",
        ),
        EvidenceRecord(
            ref_idx=2,
            url="https://example.com/openai",
            title="OpenAI Company Profile",
            evidence_quote="OpenAI was founded in December 2015 as a non-profit AI research lab.",
            finding_answer="OpenAI founded 2015",
            criterion="company_history",
            page_idx=1,
            search_node="search_openai",
        ),
        EvidenceRecord(
            ref_idx=3,
            url="https://example.com/competition",
            title="AI Competition Landscape",
            evidence_quote="Google DeepMind, Anthropic, and Meta AI are the primary competitors.",
            finding_answer="Main competitors identified",
            criterion="competitors",
            page_idx=0,
            search_node="search_competition",
        ),
        EvidenceRecord(
            ref_idx=4,
            url="https://example.com/funding",
            title="AI Funding Tracker",
            evidence_quote="OpenAI raised $6.6 billion in October 2024 at a $157 billion valuation.",
            finding_answer="OpenAI $6.6B funding round",
            criterion="funding",
            page_idx=2,
            search_node="search_openai",
        ),
    ]
    for r in records:
        es.add_record(r)
    return es


def make_test_cognitive_graph() -> dict:
    """Link cognitive nodes to evidence records through their search tasks."""
    return {
        "nodes": {
            "search_ai_market": {
                "id": "search_ai_market",
                "name": "AI Market Overview",
                "node_type": "discovered",
                "cognitive_state": "KNOWN",
                "state": "KNOWN",  # Legacy field for backward compat
                "item_findings": {
                    "AI_Market": {
                        "size": "Global AI market reached $196.6B in 2023[[1]]",
                        "growth": "Growing at 36.6% CAGR[[1]]",
                    }
                },
                "cross_item_findings": {"regional_distribution": "North America leads with 42% share[[1]]"},
                "core_pending": [],
                "pending_criteria": [],
                "temporal_notes": "Data as of 2023-2024",
                "related_tasks": ["search_ai_market"],  # Links to EvidenceStore
            },
            "search_openai": {
                "id": "search_openai",
                "name": "OpenAI Analysis",
                "node_type": "discovered",
                "cognitive_state": "PARTIAL",
                "state": "PARTIAL",
                "item_findings": {
                    "OpenAI": {
                        "founding": "Founded December 2015 as non-profit[[2]]",
                        "funding": "Raised $6.6B at $157B valuation in Oct 2024[[4]]",
                    }
                },
                "cross_item_findings": {},
                "core_pending": ["revenue_breakdown"],
                "pending_criteria": ["revenue_breakdown"],
                "temporal_notes": "Founded 2015, latest funding Oct 2024",
                "related_tasks": ["search_openai"],
            },
            "search_competition": {
                "id": "search_competition",
                "name": "Competition Landscape",
                "node_type": "discovered",
                "cognitive_state": "KNOWN",
                "state": "KNOWN",
                "item_findings": {},
                "cross_item_findings": {
                    "competitors": "Main competitors: Google DeepMind, Anthropic, Meta AI[[3]]",
                    "market_dynamics": "Intense competition driving rapid innovation",
                },
                "core_pending": [],
                "pending_criteria": [],
                "temporal_notes": None,
                "related_tasks": ["search_competition"],
            },
        },
        "edges": {},
        "gaps": {
            "information": ["revenue_breakdown"],
            "quality": [],
        },
    }


def make_test_cited_references() -> dict:
    return {
        1: {
            "url": "https://example.com/ai-market",
            "title": "AI Market Report 2024",
            "quality": {"overall": 4.2, "currency": 4, "relevance": 5, "authority": 4, "accuracy": 4, "purpose": 4},
        },
        2: {
            "url": "https://example.com/openai",
            "title": "OpenAI Company Profile",
            "quality": {"overall": 3.8, "currency": 3, "relevance": 5, "authority": 4, "accuracy": 4, "purpose": 3},
        },
        3: {
            "url": "https://example.com/competition",
            "title": "AI Competition Landscape",
            "quality": {"overall": 3.5, "currency": 3, "relevance": 4, "authority": 3, "accuracy": 4, "purpose": 3},
        },
        4: {
            "url": "https://example.com/funding",
            "title": "AI Funding Tracker",
            "quality": {"overall": 4.0, "currency": 5, "relevance": 4, "authority": 4, "accuracy": 4, "purpose": 3},
        },
    }


def make_test_outline() -> ReportOutline:
    return ReportOutline(
        overall_theme="Analysis of the global AI market and OpenAI's competitive position",
        sections=[
            SectionPlan(
                section_id="1",
                title="AI Market Overview",
                description="Global AI market size, growth trends, and regional distribution.",
                relevant_node_ids=["search_ai_market"],
            ),
            SectionPlan(
                section_id="2",
                title="OpenAI: Company Profile and Funding",
                description="OpenAI's history, funding trajectory, and current valuation.",
                relevant_node_ids=["search_openai"],
            ),
            SectionPlan(
                section_id="3",
                title="Competitive Landscape and Outlook",
                description="Key competitors and market dynamics in the AI industry.",
                relevant_node_ids=["search_competition"],
            ),
        ],
    )


# Layer 1: Mechanical Function Tests (No LLM)


def test_section_writer_format_evidence_table():
    """Test _format_evidence_table formats correctly."""
    print("\n--- test_section_writer_format_evidence_table ---")
    sw = SectionWriter.__new__(SectionWriter)
    sw.language = "en"
    es = make_test_evidence_store()

    # Get evidence for specific nodes
    records = es.get_evidence_for_nodes(["search_ai_market"])
    table = sw._format_evidence_table(records)
    assert "[[1]]" in table
    assert "market_size" in table
    assert "$196.6 billion" in table
    assert "regional_distribution" in table
    print(f"  Evidence table: OK ({len(table)} chars)")
    print(f"  Preview:\n{table[:500]}")

    # Empty records
    empty_table = sw._format_evidence_table({})
    assert "No evidence" in empty_table
    print("  Empty table: OK")

    print("  PASSED")


def test_section_writer_compress_previous_text():
    """Test _compress_previous_text compression logic."""
    print("\n--- test_section_writer_compress_previous_text ---")
    sw = SectionWriter.__new__(SectionWriter)
    sw.language = "en"

    # Short text (should return as-is)
    short = "## Section 1\nSome content here."
    result = sw._compress_previous_text(short)
    assert result == short
    print("  Short text unchanged: OK")

    # we need >40K input to actually trigger the summary path.
    sections = []
    for i in range(5):
        sections.append(f"## Section {i + 1}\nThis is the content of section {i + 1}. " * 200)
    long_text = "\n\n".join(sections)
    assert len(long_text) > 40000, "test input should exceed EN compression threshold"
    result = sw._compress_previous_text(long_text)
    assert len(result) <= 40000  # max_chars for EN
    assert len(result) < len(long_text)  # actually compressed
    assert "Section 5" in result  # Last section preserved for continuity
    print(f"  Long text compressed: {len(long_text)} -> {len(result)} chars: OK")

    # Empty text
    result_empty = sw._compress_previous_text("")
    assert result_empty == ""
    print("  Empty text: OK")

    print("  PASSED")


def test_section_writer_format_outline():
    """Test _format_outline marks current section."""
    print("\n--- test_section_writer_format_outline ---")
    sw = SectionWriter.__new__(SectionWriter)
    sw.language = "en"
    outline = make_test_outline()

    result = sw._format_outline(outline, "2")
    assert "<-- CURRENT" in result
    assert "2. OpenAI" in result
    # Only section 2 should be marked
    lines_with_current = [l for l in result.split("\n") if "CURRENT" in l]
    assert len(lines_with_current) == 1
    assert "OpenAI" in lines_with_current[0]
    print("  Outline formatting: OK")
    print(f"  Preview:\n{result}")

    print("  PASSED")


def test_section_writer_append_references():
    """Test _append_references separates report citations from search refs."""
    print("\n--- test_section_writer_append_references ---")
    sw = SectionWriter.__new__(SectionWriter)
    sw.language = "en"
    sw.include_search_refs = True

    report = "The AI market is growing[[1]]. OpenAI was founded in 2015[[2]]."
    refs = make_test_cited_references()

    result = sw._append_references(report, refs)

    # Report citations should include [[1]] and [[2]]
    assert "Report Citations" in result
    assert "AI Market Report 2024" in result
    assert "OpenAI Company Profile" in result

    # Search process refs should include [[3]] and [[4]] (not cited in report)
    assert "Search Process References" in result
    assert "AI Competition Landscape" in result
    assert "AI Funding Tracker" in result
    print(f"  Reference separation: OK ({len(result)} chars)")

    # Test with include_search_refs=False
    sw.include_search_refs = False
    result2 = sw._append_references(report, refs)
    assert "Search Process References" not in result2
    print("  include_search_refs=False: OK")

    print("  PASSED")


def test_section_writer_filter_invalid_citations():
    """Test _filter_invalid_citations removes hallucinated [[n]] refs."""
    print("\n--- test_section_writer_filter_invalid_citations ---")
    sw = SectionWriter.__new__(SectionWriter)
    sw.language = "en"

    text = "AI grew 30%[[1]]. Revenue was $100B[[2]]. Users reached 1M[[6]]."
    valid_refs = {1, 2, 3}

    result = sw._filter_invalid_citations(text, valid_refs)

    assert "[[1]]" in result
    assert "[[2]]" in result
    assert "[[6]]" not in result
    assert "Users reached 1M." in result
    print("  Hallucinated [[6]] removed: OK")

    # All valid - no change
    text2 = "Fact A[[1]]. Fact B[[2]]."
    result2 = sw._filter_invalid_citations(text2, valid_refs)
    assert result2 == text2
    print("  All valid citations preserved: OK")

    # Empty valid_refs - all removed
    result3 = sw._filter_invalid_citations(text2, set())
    assert "[[" not in result3
    print("  Empty valid_refs removes all: OK")

    print("  PASSED")


# Layer 2: JSON Parsing / Fallback Tests (No LLM)


def test_outline_planner_parse_response():
    """Test OutlinePlanner._parse_response with various inputs."""
    print("\n--- test_outline_planner_parse_response ---")
    op = OutlinePlanner.__new__(OutlinePlanner)
    op.language = "en"
    cg = make_test_cognitive_graph()
    es = make_test_evidence_store()

    # Valid JSON
    valid_json = """{
        "overall_theme": "AI market analysis",
        "sections": [
            {"section_id": "1", "title": "Market Overview", "description": "Size and growth", "relevant_node_ids": ["search_ai_market"]},
            {"section_id": "2", "title": "Key Players", "description": "OpenAI and competitors", "relevant_node_ids": ["search_openai", "search_competition"]}
        ]
    }"""
    result = op._parse_response(valid_json, cg, es)
    assert len(result.sections) == 2
    assert result.sections[0].title == "Market Overview"
    assert result.sections[1].relevant_node_ids == ["search_openai", "search_competition"]
    print(f"  Valid JSON: OK ({len(result.sections)} sections)")

    # JSON wrapped in markdown code blocks
    md_json = f"```json\n{valid_json}\n```"
    result2 = op._parse_response(md_json, cg, es)
    assert len(result2.sections) == 2
    print("  Markdown-wrapped JSON: OK")

    # Invalid response (should fallback)
    result3 = op._parse_response("This is not JSON at all.", cg, es)
    assert len(result3.sections) > 0  # Fallback should produce sections
    print(f"  Invalid response fallback: OK ({len(result3.sections)} sections)")

    print("  PASSED")


def test_outline_planner_validate_coverage():
    """Test coverage validation adds uncovered nodes."""
    print("\n--- test_outline_planner_validate_coverage ---")
    op = OutlinePlanner.__new__(OutlinePlanner)
    op.language = "en"
    cg = make_test_cognitive_graph()

    # Outline missing search_competition
    outline = ReportOutline(
        overall_theme="Test",
        sections=[
            SectionPlan(section_id="1", title="Market", description="", relevant_node_ids=["search_ai_market"]),
            SectionPlan(section_id="2", title="OpenAI", description="", relevant_node_ids=["search_openai"]),
        ],
    )

    op._validate_coverage(outline, cg)

    # search_competition should have been appended to last section
    last_section_nodes = outline.sections[-1].relevant_node_ids
    assert "search_competition" in last_section_nodes
    print("  Uncovered node appended to last section: OK")
    print(f"  Last section nodes: {last_section_nodes}")

    print("  PASSED")


def test_outline_planner_build_graph_summary():
    """Test graph summary generation for prompt."""
    print("\n--- test_outline_planner_build_graph_summary ---")
    op = OutlinePlanner.__new__(OutlinePlanner)
    op.language = "en"
    cg = make_test_cognitive_graph()
    es = make_test_evidence_store()

    summary = op._build_graph_summary(cg, es)

    assert "AI Market Overview" in summary
    assert "KNOWN" in summary
    assert "OpenAI Analysis" in summary
    assert "PARTIAL" in summary
    assert "Evidence available" in summary
    assert "[[1]]" in summary  # ref_idx visible for structural planning
    assert "Findings" in summary
    print(f"  Graph summary: OK ({len(summary)} chars)")
    print(f"  Preview:\n{summary[:600]}")

    print("  PASSED")


def test_section_writer_build_section_prompt():
    """Test complete section prompt construction."""
    print("\n--- test_section_writer_build_section_prompt ---")
    sw = SectionWriter.__new__(SectionWriter)
    sw.language = "en"

    es = make_test_evidence_store()
    outline = make_test_outline()
    section = outline.sections[0]

    evidence = es.get_evidence_for_nodes(section.relevant_node_ids)
    evidence_table = sw._format_evidence_table(evidence)

    prompt = sw._build_section_prompt_en(
        section=section,
        evidence_table=evidence_table,
        prev_summary="",
        outline_text=sw._format_outline(outline, section.section_id),
        original_question="What is the current state of the global AI market?",
    )

    assert "Original Question" in prompt
    assert "Insight" in prompt  # Insight→Evidence binding writing
    assert "Citation Format" in prompt  # Citation format rules
    assert "Analytical Depth" in prompt  # Writing quality guidance
    assert "[[1]]" in prompt
    print(f"  Section prompt constructed: OK ({len(prompt)} chars)")

    # Check Chinese version too
    sw.language = "cn"
    prompt_cn = sw._build_section_prompt_cn(
        section=section,
        evidence_table=evidence_table,
        prev_summary="",
        outline_text=sw._format_outline(outline, section.section_id),
        original_question="全球AI市场现状如何？",
    )
    assert "写作蓝图" in prompt_cn  # Writing blueprint
    assert "分析深度" in prompt_cn  # Analytical depth guidance
    print(f"  Chinese prompt: OK ({len(prompt_cn)} chars)")

    print("  PASSED")


# Layer 2b: SectionPlanner Tests (No LLM)


def test_section_planner_parse_insights():
    """Test SectionPlanner._parse_insights with valid/invalid refs."""
    print("\n--- test_section_planner_parse_insights ---")
    sp = SectionPlanner.__new__(SectionPlanner)
    sp.language = "en"

    evidence_index = "[[1]] (market_size)\n[[1]] (regional_distribution)\n[[2]] (company_history)\n[[4]] (funding)"

    # Valid JSON response
    valid_response = json.dumps(
        {
            "insights": [
                {"insight": "The AI market is growing rapidly", "evidence_ids": [1, 4]},
                {"insight": "OpenAI leads the market", "evidence_ids": [2, 1]},
            ]
        }
    )
    insights = sp._parse_insights(valid_response, evidence_index)
    assert len(insights) == 2
    assert insights[0].insight == "The AI market is growing rapidly"
    assert insights[0].evidence_ids == [1, 4]
    print(f"  Valid response: OK ({len(insights)} insights)")

    # Response with invalid ref_idx (should be filtered)
    invalid_response = json.dumps(
        {
            "insights": [
                {"insight": "Some insight", "evidence_ids": [1, 99, 200]},
            ]
        }
    )
    insights2 = sp._parse_insights(invalid_response, evidence_index)
    assert len(insights2) == 1
    assert insights2[0].evidence_ids == [1]  # Only valid ref kept
    print(f"  Invalid refs filtered: OK (kept {insights2[0].evidence_ids})")

    # Response with ALL invalid refs (insight should be dropped)
    all_invalid_response = json.dumps(
        {
            "insights": [
                {"insight": "Bad insight", "evidence_ids": [99, 100]},
            ]
        }
    )
    insights3 = sp._parse_insights(all_invalid_response, evidence_index)
    assert len(insights3) == 0  # Insight dropped because no valid evidence
    print("  All-invalid insight dropped: OK")

    # Empty/invalid response
    insights4 = sp._parse_insights("not json at all", evidence_index)
    assert len(insights4) == 0
    print("  Invalid JSON: OK (0 insights)")

    print("  PASSED")


def test_section_planner_build_evidence_index():
    """Test GlobalSectionPlanner._build_evidence_index."""
    print("\n--- test_section_planner_build_evidence_index ---")
    psp = GlobalSectionPlanner.__new__(GlobalSectionPlanner)

    es = make_test_evidence_store()
    records = es.get_evidence_for_nodes(["search_ai_market"])

    index = psp._build_evidence_index(records)
    assert "[[1]]" in index
    assert "(market_size)" in index
    assert "(regional_distribution)" in index
    # Should NOT contain evidence quotes
    assert "$196.6 billion" not in index
    print(f"  Evidence index: OK ({len(index)} chars)")
    print(f"  Preview:\n{index}")

    # Empty records
    empty_index = psp._build_evidence_index({})
    assert "No evidence" in empty_index
    print("  Empty index: OK")

    print("  PASSED")


def test_section_writer_grouped_evidence_table():
    """Test that evidence table is grouped by source [[n]]."""
    print("\n--- test_section_writer_grouped_evidence_table ---")
    sw = SectionWriter.__new__(SectionWriter)
    sw.language = "en"
    es = make_test_evidence_store()

    records = es.get_evidence_for_nodes(["search_ai_market", "search_openai"])
    table = sw._format_evidence_table(records)

    # Should have grouped headers per source
    assert "来源 [[1]]" in table
    assert "来源 [[2]]" in table

    # Each group has its own table header
    assert table.count("| Criterion |") >= 2  # At least 2 source groups
    print(f"  Grouped evidence table: OK ({len(table)} chars)")
    print(f"  Preview:\n{table[:600]}")

    print("  PASSED")


# Layer 3: LLM Integration Tests (Optional, needs API key)


@pytest.mark.live_llm
def test_outline_planner_with_llm():
    """Test OutlinePlanner with actual LLM call."""
    print("\n--- test_outline_planner_with_llm ---")
    from dotenv import load_dotenv

    load_dotenv()

    from veritrace.agent.core import create_adapter

    model = os.environ.get("WRITER_MODEL") or os.environ.get("SILICON_MODEL", "Qwen/Qwen3-32B")
    llm = create_adapter("siliconflow", model)

    op = OutlinePlanner(llm=llm, language="en")
    cg = make_test_cognitive_graph()
    es = make_test_evidence_store()

    outline = op.plan_outline(
        "What is the current state of the global AI market and OpenAI's position?",
        cg,
        es,
    )

    print(f"  Theme: {outline.overall_theme}")
    print(f"  Sections ({len(outline.sections)}):")
    for s in outline.sections:
        print(f"    {s.section_id}. {s.title} -> nodes: {s.relevant_node_ids}")

    assert len(outline.sections) >= 2
    # Check all nodes are covered
    all_covered = set()
    for s in outline.sections:
        all_covered.update(s.relevant_node_ids)
    for node_id in cg["nodes"]:
        assert node_id in all_covered, f"Node {node_id} not covered!"

    print("  PASSED")


@pytest.mark.live_llm
def test_section_writer_single_section_with_llm():
    """Test writing a single section with actual LLM."""
    print("\n--- test_section_writer_single_section_with_llm ---")
    from dotenv import load_dotenv

    load_dotenv()

    from veritrace.agent.core import create_adapter

    model = os.environ.get("WRITER_MODEL") or os.environ.get("SILICON_MODEL", "Qwen/Qwen3-32B")
    llm = create_adapter("siliconflow", model)

    sw = SectionWriter(llm=llm, language="en", include_search_refs=True)
    es = make_test_evidence_store()
    outline = make_test_outline()
    section = outline.sections[0]  # AI Market Overview

    evidence = es.get_evidence_for_nodes(section.relevant_node_ids)

    section_text = sw.write_section(
        section=section,
        evidence_records=evidence,
        previous_text="",
        full_outline=outline,
        original_question="What is the current state of the global AI market?",
    )

    print(f"  Section text ({len(section_text)} chars):")
    print("  ---")
    print(section_text[:800])
    print("  ---")

    # Check for citations
    import re

    citations = re.findall(r"\[\[(\d+)\]\]", section_text)
    print(f"  Citations found: {citations}")
    assert len(citations) > 0, "Section should have at least one citation"

    # Check it contains the section heading
    assert "Market" in section_text or "市场" in section_text

    print("  PASSED")


@pytest.mark.live_llm
def test_full_write_report_with_llm():
    """Test full report writing (all sections)."""
    print("\n--- test_full_write_report_with_llm ---")
    from dotenv import load_dotenv

    load_dotenv()

    from veritrace.agent.core import create_adapter

    model = os.environ.get("WRITER_MODEL") or os.environ.get("SILICON_MODEL", "Qwen/Qwen3-32B")
    llm = create_adapter("siliconflow", model)

    cg = make_test_cognitive_graph()
    es = make_test_evidence_store()
    refs = make_test_cited_references()

    # Step 1: Generate outline
    op = OutlinePlanner(llm=llm, language="en")
    outline = op.plan_outline(
        "What is the current state of the global AI market and OpenAI's position?",
        cg,
        es,
    )
    print(f"  Outline: {len(outline.sections)} sections")

    # Step 2: Write report
    sw = SectionWriter(llm=llm, language="en", include_search_refs=True)
    report = sw.write_report(
        outline=outline,
        cognitive_graph=cg,
        evidence_store=es,
        original_question="What is the current state of the global AI market and OpenAI's position?",
        cited_references=refs,
    )

    print(f"  Report ({len(report)} chars):")
    print("  ---")
    print(report[:1200])
    print("  ...")
    print(report[-500:])
    print("  ---")

    import re

    citations = re.findall(r"\[\[(\d+)\]\]", report)
    unique_citations = set(int(c) for c in citations)
    print(f"  Total citations: {len(citations)}, unique: {unique_citations}")

    assert len(report) > 500, "Report should be substantial"
    assert "Report Citations" in report, "Should have reference section"
    assert len(citations) > 0, "Should have citations"

    print("  PASSED")


# Main


if __name__ == "__main__":
    use_llm = "--with-llm" in sys.argv

    print("=" * 60)
    print("  Writing Pipeline Unit Tests")
    print("=" * 60)

    # Layer 1: Mechanical tests
    print("\n### Layer 1: Mechanical Function Tests ###")
    test_section_writer_format_evidence_table()
    test_section_writer_compress_previous_text()
    test_section_writer_format_outline()
    test_section_writer_append_references()

    # Layer 2: Parsing / Fallback tests
    print("\n### Layer 2: JSON Parsing / Fallback Tests ###")
    test_outline_planner_parse_response()
    test_outline_planner_validate_coverage()
    test_outline_planner_build_graph_summary()
    test_section_writer_build_section_prompt()

    # Layer 2b: SectionPlanner tests
    print("\n### Layer 2b: SectionPlanner Tests ###")
    test_section_planner_parse_insights()
    test_section_planner_build_evidence_index()
    test_section_writer_grouped_evidence_table()

    # Layer 3: LLM tests (optional)
    if use_llm:
        print("\n### Layer 3: LLM Integration Tests ###")
        test_outline_planner_with_llm()
        test_section_writer_single_section_with_llm()
        test_full_write_report_with_llm()
    else:
        print("\n### Layer 3: Skipped (use --with-llm to enable) ###")

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60)
