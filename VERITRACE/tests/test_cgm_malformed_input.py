"""Preserve recoverable graph findings without misattributing malformed updates."""

from unittest.mock import MagicMock

from veritrace.agent.cognitive_graph import CognitiveGraph, EntityNode, NodeType


def _mgr():
    from veritrace.agent.cognitive_graph_manager import CognitiveGraphManager

    m = CognitiveGraphManager.__new__(CognitiveGraphManager)
    m.graph = CognitiveGraph()
    m.language = "en"
    m.llm = MagicMock()
    return m


def _node(node_id="e2", name="Scale AI", node_type=NodeType.DISCOVERED):
    return EntityNode(id=node_id, name=name, node_type=node_type)


# B2 — flat-string item value is coerced, not dropped


def test_b2_flat_string_item_coerced_to_summary():
    m = _mgr()
    m.graph.add_node(_node("e2"))
    m._apply_update(
        {
            "cognitive_updates": [
                {
                    "node_id": "e2",
                    # LLM flattened: each item is a string, not {attr: value}
                    "item_findings": {
                        "PwC": "Big Four firm, AI consulting arm [[3]]",
                        "Deloitte": "Largest by revenue, GenAI practice [[4]]",
                    },
                }
            ]
        },
        search_context={"node_name": "task_firms"},
    )
    itf = m.graph.nodes["e2"].item_findings

    assert set(itf.keys()) == {"PwC", "Deloitte"}
    assert itf["PwC"]["summary"] == "Big Four firm, AI consulting arm [[3]]"
    assert itf["Deloitte"]["summary"] == "Largest by revenue, GenAI practice [[4]]"


def test_b2_mixed_nested_and_flat_both_survive():
    m = _mgr()
    m.graph.add_node(_node("e2"))
    m._apply_update(
        {
            "cognitive_updates": [
                {
                    "node_id": "e2",
                    "item_findings": {
                        "A": {"signal": "proper nested [[1]]"},  # correct shape
                        "B": "flat string [[2]]",  # flattened
                    },
                }
            ]
        },
        search_context={"node_name": "t"},
    )
    itf = m.graph.nodes["e2"].item_findings
    assert itf["A"]["signal"] == "proper nested [[1]]"
    assert itf["B"]["summary"] == "flat string [[2]]"


# B3 — item_findings top-level non-dict is salvaged, not dropped


def test_b3_top_level_string_salvaged():
    m = _mgr()
    m.graph.add_node(_node("e2"))
    m._apply_update(
        {
            "cognitive_updates": [
                {
                    "node_id": "e2",
                    "item_findings": "everything in one blob [[7]]",  # non-dict top level
                }
            ]
        },
        search_context={"node_name": "t"},
    )
    itf = m.graph.nodes["e2"].item_findings
    assert itf["_misc"]["summary"] == "everything in one blob [[7]]"


# B1 — mis-keyed node_id is resolved, not silently dropped whole


def test_b1_node_name_used_as_id_is_resolved():
    m = _mgr()
    m.graph.add_node(_node("e2", name="AI talent strategy"))
    m._apply_update(
        {
            "cognitive_updates": [
                {
                    # LLM used the NAME instead of the id
                    "node_id": "AI talent strategy",
                    "item_findings": {"X": {"a": "v [[1]]"}},
                }
            ]
        },
        search_context={"node_name": "t", "target_id": "e2"},
    )
    # Resolved to e2 by name — findings landed (used to be dropped whole)
    assert m.graph.nodes["e2"].item_findings["X"]["a"] == "v [[1]]"


def test_b1_missing_node_id_falls_back_to_target():
    m = _mgr()
    m.graph.add_node(_node("e2", name="Target node"))
    m._apply_update(
        {
            "cognitive_updates": [
                {
                    # node_id omitted entirely
                    "cross_item_findings": {"trend": "consolidating [[2]]"},
                }
            ]
        },
        search_context={"node_name": "t", "target_id": "e2"},
    )
    assert m.graph.nodes["e2"].cross_item_findings["trend"] == "consolidating [[2]]"


def test_b1_unknown_id_dropped_not_misattributed():
    """Do not redirect a specific unknown node ID to another target."""
    m = _mgr()
    m.graph.add_node(_node("e2", name="Target node"))
    m._apply_update(
        {
            "cognitive_updates": [
                {
                    "node_id": "e_hallucinated_999",
                    "item_findings": {"Ghost": {"a": "should not land on e2 [[9]]"}},
                }
            ]
        },
        search_context={"node_name": "t", "target_id": "e2"},
    )
    # Not misattributed to e2
    assert m.graph.nodes["e2"].item_findings == {}


# B5 — contradiction missing 'criterion' is kept (defaulted), not dropped


def test_b5_contradiction_without_criterion_is_kept():
    m = _mgr()
    m.graph.add_node(_node("e2"))
    m._apply_update(
        {
            "cognitive_updates": [
                {
                    "node_id": "e2",
                    "contradictions": [
                        {
                            # no 'criterion'
                            "old_claim": "founded 2018 [[7]]",
                            "new_claim": "founded 2016 [[2]]",
                            "kept": "new",
                        }
                    ],
                }
            ]
        },
        search_context={"node_name": "t"},
    )
    cons = m.graph.nodes["e2"].contradictions
    assert len(cons) == 1
    assert cons[0]["criterion"] == "(未指定)"
    assert cons[0]["new_claim"] == "founded 2016 [[2]]"
