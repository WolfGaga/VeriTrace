"""Render cognitive prompt templates with the production placeholders."""

from veritrace.agent.cognitive_graph_manager import (
    COGNITIVE_GRAPH_PARSE_PROMPT,
    COGNITIVE_GRAPH_PARSE_PROMPT_EN,
    COGNITIVE_GRAPH_UPDATE_PROMPT,
    COGNITIVE_GRAPH_UPDATE_PROMPT_CN,
    PHASE1_RESTRUCTURE_PROMPT,
    PHASE1_RESTRUCTURE_PROMPT_EN,
    PHASE2_RECONNECT_PROMPT,
    PHASE2_RECONNECT_PROMPT_EN,
)


def test_parse_prompt_cn_formats():
    p = COGNITIVE_GRAPH_PARSE_PROMPT.format(
        current_date="2026-05-29",
        question="test question",
    )
    assert "test question" in p


def test_parse_prompt_en_formats():
    p = COGNITIVE_GRAPH_PARSE_PROMPT_EN.format(
        current_date="2026-05-29",
        question="test question",
    )
    assert "test question" in p


_UPDATE_KWARGS = dict(
    current_graph="(empty graph)",
    original_question="Compare Obsidian Bases vs Notion databases",
    node_name="task_obsidian_bases",
    inquiry_goal="discover Obsidian plugins replicating Notion db features",
    core_criteria=["plugin name + supported view types", "maintenance status"],
    supplementary_criteria=["install count / popularity", "feature summary"],
    search_result="(searcher response with [[0]] [[1]] citations)",
)


def test_update_prompt_cn_formats():
    """Render the Chinese update template without interpreting literal braces."""
    p = COGNITIVE_GRAPH_UPDATE_PROMPT_CN.format(**_UPDATE_KWARGS)
    assert _UPDATE_KWARGS["search_result"] in p
    assert _UPDATE_KWARGS["node_name"] in p


def test_update_prompt_en_formats():
    """Render the English update template without interpreting literal braces."""
    p = COGNITIVE_GRAPH_UPDATE_PROMPT.format(**_UPDATE_KWARGS)
    assert _UPDATE_KWARGS["search_result"] in p
    assert _UPDATE_KWARGS["node_name"] in p


def test_phase1_restructure_prompt_cn_formats():
    p = PHASE1_RESTRUCTURE_PROMPT.format(
        original_question="test question",
        restructure_view="(graph view)",
        planner_reasoning="(planner reasoning text)",
    )
    assert "test question" in p


def test_phase1_restructure_prompt_en_formats():
    p = PHASE1_RESTRUCTURE_PROMPT_EN.format(
        original_question="test question",
        restructure_view="(graph view)",
        planner_reasoning="(planner reasoning text)",
    )
    assert "test question" in p


def test_phase2_reconnect_prompt_cn_formats():
    p = PHASE2_RECONNECT_PROMPT.format(
        original_question="test question",
        planner_reasoning="(planner reasoning)",
        phase1_summary="(phase 1 summary)",
        current_topology="(topology)",
        orphan_details="orphan_1\norphan_2",
        round_number=1,
        max_rounds=5,
    )
    assert "test question" in p


def test_phase2_reconnect_prompt_en_formats():
    p = PHASE2_RECONNECT_PROMPT_EN.format(
        original_question="test question",
        planner_reasoning="(planner reasoning)",
        phase1_summary="(phase 1 summary)",
        current_topology="(topology)",
        orphan_details="orphan_1\norphan_2",
        round_number=1,
        max_rounds=5,
    )
    assert "test question" in p


def test_no_unescaped_brace_set_literals_in_prompt_constants():
    """Reject unescaped quoted literals in format templates."""
    import re
    import veritrace.agent.cognitive_graph_manager as m

    bad_pattern = re.compile(r'(?<!\{)\{["\']')
    offenders = []
    for attr in dir(m):
        if not attr.startswith("_") and "PROMPT" in attr:
            val = getattr(m, attr, None)
            if isinstance(val, str):
                for line_no, line in enumerate(val.split("\n"), 1):
                    for hit in bad_pattern.finditer(line):
                        ctx = line[max(0, hit.start() - 20) : hit.end() + 20]
                        offenders.append((attr, line_no, ctx))
    assert not offenders, (
        "Unescaped brace + quote pattern in prompt constant — would raise "
        "KeyError at .format() time. Either escape `{{` `}}` or rephrase:\n"
        + "\n".join(f"  {a} L{l}: ...{c}..." for a, l, c in offenders[:10])
    )
