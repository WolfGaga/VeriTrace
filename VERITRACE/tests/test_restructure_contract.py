import copy

import pytest

from veritrace.agent.cognitive_graph import CognitiveGraph, EntityNode, RelationEdge
from veritrace.agent.cognitive_graph_manager import CognitiveGraphManager


def research_graph():
    graph = CognitiveGraph()
    graph.original_question = "Which suppliers meet the criteria?"
    for node in [
        EntityNode(id="root", name="Question", node_type="start", cognitive_state="START"),
        EntityNode(
            id="kept",
            name="Known supplier",
            node_type="discovered",
            cognitive_state="PARTIAL",
            item_findings={"Supplier": {"revenue": "12 million [[1]]"}},
            cited_refs=[1],
        ),
        EntityNode(id="branch", name="Unused category", node_type="placeholder", cognitive_state="UNKNOWN"),
        EntityNode(id="leaf", name="Pending supplier", node_type="placeholder", cognitive_state="UNKNOWN"),
    ]:
        graph.add_node(node)
    for edge_id, source, target, state in [
        ("known-edge", "root", "kept", "solved"),
        ("branch-edge", "root", "branch", "to_solve"),
        ("leaf-edge", "branch", "leaf", "to_solve"),
    ]:
        graph.add_edge(RelationEdge(edge_id, source, target, "supplier", "Find the supplier", state=state))
    graph.structure.dependency_order = ["root", "branch", "leaf", "kept"]
    return graph


def removal():
    return {
        "should_restructure": True,
        "evaluation": "Remove the unused category",
        "changes": [{"action": "remove_node", "id": "branch"}],
    }


@pytest.mark.parametrize("language", ["en", "cn"])
def test_restructure_reconnects_orphan_and_preserves_evidence(scripted_llm, language):
    llm = scripted_llm(
        [
            removal(),
            {
                "actions": [
                    {
                        "action": "add_edge",
                        "edge_data": {
                            "id": "reconnected",
                            "source": "root",
                            "target": "leaf",
                            "inquiry_goal": "Find supplier",
                        },
                    }
                ]
            },
        ]
    )
    manager = CognitiveGraphManager(llm, language=language)
    manager.graph = research_graph()
    findings = copy.deepcopy(manager.graph.nodes["kept"].to_dict())

    result = manager.restructure_graph({"op_type": "remove_node", "reasoning": "Category is unnecessary"})

    assert result["should_restructure"]
    assert result["removed_nodes"] == ["branch"]
    assert set(result["removed_edges"]) == {"branch-edge", "leaf-edge"}
    assert result["added_edges"] == ["reconnected"]
    assert manager.graph.nodes["kept"].to_dict() == findings
    assert manager.graph.edges["known-edge"].state == "solved"
    assert manager.graph.edges["reconnected"].target_node_id == "leaf"
    assert "branch" not in manager.graph.structure.dependency_order
    assert len(llm.calls) == 2


def test_restructure_rolls_back_when_orphan_reconnection_is_exhausted(scripted_llm):
    llm = scripted_llm([removal()] + [{"actions": []} for _ in range(5)])
    manager = CognitiveGraphManager(llm)
    manager.graph = research_graph()
    before = copy.deepcopy(manager.graph.to_dict())

    result = manager.restructure_graph("Remove the unused category")

    assert result["error"] == "restructure_rollback"
    assert result["should_restructure"] is False
    assert result["orphan_count"] == 1
    assert manager.graph.to_dict() == before
    assert len(llm.calls) == 6


def test_restructure_rejects_removing_evidence_changing_protected_target_and_cycles(scripted_llm):
    proposal = {
        "should_restructure": True,
        "evaluation": "Invalid edits",
        "changes": [
            {"action": "remove_node", "id": "kept"},
            {"action": "modify_edge", "id": "known-edge", "new_target_id": "leaf"},
            {"action": "add_edge", "edge_data": {"id": "cycle", "source": "kept", "target": "root"}},
        ],
    }
    llm = scripted_llm([proposal, proposal, proposal, "Keep the researched node and avoid a cycle."])
    manager = CognitiveGraphManager(llm)
    manager.graph = research_graph()
    before = copy.deepcopy(manager.graph.to_dict())

    result = manager.restructure_graph("Try unsupported changes")

    assert len(result["violations"]) == 3
    assert "protected" in " ".join(result["violations"])
    assert "cycle" in " ".join(result["violations"])
    assert manager.graph.to_dict() == before
    assert len(llm.calls) == 4


def test_restructure_parse_failure_leaves_graph_untouched(scripted_llm):
    llm = scripted_llm(["not JSON", "[]", "still not JSON"])
    manager = CognitiveGraphManager(llm)
    manager.graph = research_graph()
    before = copy.deepcopy(manager.graph.to_dict())

    result = manager.restructure_graph("Revise the graph")

    assert "error" in result
    assert manager.graph.to_dict() == before
    assert len(llm.calls) == 3
