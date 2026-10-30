"""Preserve global citation identity through cognitive graph updates."""

import re
import sys
import threading
from unittest.mock import MagicMock


from veritrace.agent.cognitive_graph import (
    CognitiveGraph,
    EntityNode,
    NodeType,
    _entry_value,
)


# Helpers


def make_node(node_id: str = "e1", name: str = "Scale AI") -> EntityNode:
    n = EntityNode(id=node_id, name=name, node_type=NodeType.DISCOVERED)
    return n


def make_agent_skeleton():
    """Create allocator state without constructing provider clients."""
    from veritrace.agent.veritrace_agent import VeritraceAgent

    a = VeritraceAgent.__new__(VeritraceAgent)
    a._global_url_to_idx = {}
    a._global_references_url = {}
    a._global_ptr = 0
    a._global_ref_lock = threading.Lock()
    return a


# 1. Incremental global allocator — the v3 centerpiece


class TestAllocator:
    def test_first_task_allocates_globals_starting_from_1(self):
        a = make_agent_skeleton()
        ref2url = {
            0: {"url": "https://example.com/a", "title": "A"},
            1: {"url": "https://example.com/b", "title": "B"},
        }
        answer = "Fact X [[0]], Fact Y [[1]]"
        new_answer, lmap = a._allocate_globals_for_task("task_alpha", answer, ref2url)
        assert lmap == {0: 1, 1: 2}
        assert new_answer == "Fact X [[1]], Fact Y [[2]]"
        assert a._global_ptr == 2

    def test_url_dedup_across_tasks(self):
        """Same URL on two tasks gets the SAME global idx."""
        a = make_agent_skeleton()
        a._allocate_globals_for_task(
            "task_alpha",
            "X [[0]]",
            {0: {"url": "https://shared.com/page", "title": "S"}},
        )
        new_answer, lmap = a._allocate_globals_for_task(
            "task_beta",
            "Y [[2]]",
            {2: {"url": "https://shared.com/page", "title": "S"}},  # same URL, different local idx
        )
        assert lmap == {2: 1}, "task_beta's local [[2]] should dedup to task_alpha's global [[1]]"
        assert new_answer == "Y [[1]]"
        assert a._global_ptr == 1, "no new global allocated for the shared URL"

    def test_distinct_urls_get_distinct_globals(self):
        """Identical local indices from different URLs receive distinct global indices."""
        a = make_agent_skeleton()
        new1, lmap1 = a._allocate_globals_for_task(
            "task_a",
            "A [[5]]",
            {5: {"url": "https://a.com", "title": "A"}},
        )
        new2, lmap2 = a._allocate_globals_for_task(
            "task_b",
            "B [[5]]",
            {5: {"url": "https://b.com", "title": "B"}},
        )
        assert lmap1[5] == 1
        assert lmap2[5] == 2  # distinct URL → distinct global
        assert new1 == "A [[1]]"
        assert new2 == "B [[2]]"

    def test_cited_ref_missing_from_ref2url_gets_placeholder_global(self):
        """Missing source metadata still receives a collision-free citation index."""
        a = make_agent_skeleton()
        # ref2url has 0,1 but answer cites 7
        new_answer, lmap = a._allocate_globals_for_task(
            "task_x",
            "Real [[0]], dangling [[7]]",
            {0: {"url": "https://r.com", "title": "R"}},
        )
        assert 0 in lmap
        assert 7 in lmap
        # both rewritten to globals
        refs = sorted(int(m) for m in re.findall(r"\[\[(\d+)\]\]", new_answer))
        assert refs == sorted([lmap[0], lmap[7]])

    def test_references_url_accumulates_global_mapping(self):
        a = make_agent_skeleton()
        a._allocate_globals_for_task(
            "task_a",
            "X [[0]]",
            {0: {"url": "https://a.com", "title": "A"}},
        )
        a._allocate_globals_for_task(
            "task_b",
            "Y [[3]]",
            {3: {"url": "https://b.com", "title": "B"}},
        )
        assert a._global_references_url[1]["url"] == "https://a.com"
        assert a._global_references_url[2]["url"] == "https://b.com"

    def test_concurrent_allocation_is_thread_safe(self):
        """Concurrent tasks allocate distinct global indices."""
        a = make_agent_skeleton()
        results = {}

        def run(task_name: str, url: str):
            new, lmap = a._allocate_globals_for_task(
                task_name,
                "Cited [[0]]",
                {0: {"url": url, "title": "T"}},
            )
            results[task_name] = (new, lmap)

        threads = [threading.Thread(target=run, args=(f"t{i}", f"https://x.com/{i}")) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 20 distinct URLs → 20 distinct globals, ptr = 20
        assert a._global_ptr == 20
        globals_used = {results[f"t{i}"][1][0] for i in range(20)}
        assert globals_used == set(range(1, 21))

    def test_answer_without_refs_passes_through(self):
        a = make_agent_skeleton()
        new_answer, lmap = a._allocate_globals_for_task("task_z", "Just text.", {})
        assert new_answer == "Just text."
        assert lmap == {}


# 2. CGM stores plain str (no task wrapper)


class TestApplyUpdatePlainStr:
    def _make_manager(self):
        from veritrace.agent.cognitive_graph_manager import CognitiveGraphManager

        m = CognitiveGraphManager.__new__(CognitiveGraphManager)
        m.graph = CognitiveGraph()
        m.language = "en"
        m.llm = MagicMock()
        return m

    def test_item_findings_stored_as_plain_str(self):
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e2",
                        "item_findings": {"Scale AI": {"market_position": "Dominant [[3]]"}},
                    }
                ]
            },
            search_context={"node_name": "task_market"},
        )
        entry = m.graph.nodes["e2"].item_findings["Scale AI"]["market_position"]
        # No dict wrapper — plain str
        assert entry == "Dominant [[3]]"
        assert not isinstance(entry, dict)

    def test_cross_item_findings_stored_as_plain_str(self):
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e2",
                        "cross_item_findings": {"trend": "Consolidating market [[1]]"},
                    }
                ]
            },
            search_context={"node_name": "task_overview"},
        )
        entry = m.graph.nodes["e2"].cross_item_findings["trend"]
        assert entry == "Consolidating market [[1]]"
        assert not isinstance(entry, dict)

    def test_unexpected_discoveries_stored_as_plain_str_list(self):
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e2",
                        "unexpected_discoveries": [
                            "Unusual pricing pattern [[2]]",
                            "Emerging competitor [[5]]",
                        ],
                    }
                ]
            },
            search_context={"node_name": "task_scan"},
        )
        ds = m.graph.nodes["e2"].unexpected_discoveries
        assert ds == [
            "Unusual pricing pattern [[2]]",
            "Emerging competitor [[5]]",
        ]
        assert all(isinstance(d, str) for d in ds)

    def test_second_write_overwrites_same_attr(self):
        """v3 removes first-write-wins; second write replaces."""
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {"cognitive_updates": [{"node_id": "e2", "item_findings": {"X": {"a": "v1 [[1]]"}}}]},
            search_context={"node_name": "task_first"},
        )
        m._apply_update(
            {"cognitive_updates": [{"node_id": "e2", "item_findings": {"X": {"a": "v2 [[2]]"}}}]},
            search_context={"node_name": "task_second"},
        )
        assert m.graph.nodes["e2"].item_findings["X"]["a"] == "v2 [[2]]"

    def test_unexpected_discoveries_dedup_by_value(self):
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {"cognitive_updates": [{"node_id": "e2", "unexpected_discoveries": ["same content [[1]]"]}]},
            search_context={"node_name": "task_a"},
        )
        m._apply_update(
            {"cognitive_updates": [{"node_id": "e2", "unexpected_discoveries": ["same content [[1]]"]}]},
            search_context={"node_name": "task_b"},
        )
        assert m.graph.nodes["e2"].unexpected_discoveries == ["same content [[1]]"]


# 3. Contradictions — no task tags (everything already global)


class TestContradictionsNoTaskTags:
    def _make_manager(self):
        from veritrace.agent.cognitive_graph_manager import CognitiveGraphManager

        m = CognitiveGraphManager.__new__(CognitiveGraphManager)
        m.graph = CognitiveGraph()
        m.language = "en"
        m.llm = MagicMock()
        return m

    def test_contradictions_have_no_task_tags(self):
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e2",
                        "contradictions": [
                            {
                                "criterion": "X.year",
                                "old_claim": "2018 [[1]]",
                                "new_claim": "2016 [[3]]",
                                "kept": "new",
                            }
                        ],
                    }
                ]
            },
            search_context={"node_name": "task_verify"},
        )
        c = m.graph.nodes["e2"].contradictions[0]
        # No new_task / old_task — values are already global, task tags meaningless
        assert "new_task" not in c
        assert "old_task" not in c
        # Quality fields preserved
        assert c["old_claim"] == "2018 [[1]]"
        assert c["new_claim"] == "2016 [[3]]"

    def test_contradictions_strip_legacy_task_fields(self):
        """Discard obsolete attribution fields from contradiction records."""
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e2",
                        "contradictions": [
                            {
                                "criterion": "X.year",
                                "old_claim": "2018 [[1]]",
                                "old_task": "legacy_task_label",
                                "new_claim": "2016 [[3]]",
                                "new_task": "another_legacy_label",
                                "kept": "new",
                            }
                        ],
                    }
                ]
            },
            search_context={"node_name": "task_verify"},
        )
        c = m.graph.nodes["e2"].contradictions[0]
        assert "new_task" not in c
        assert "old_task" not in c

    def test_contradictions_quality_fields_preserved(self):
        m = self._make_manager()
        n = make_node(node_id="e2")
        m.graph.add_node(n)
        m._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e2",
                        "contradictions": [
                            {
                                "criterion": "X.year",
                                "old_claim": "2018 [[1]]",
                                "old_quality": {"cr": 3.0, "aap": 2.5},
                                "new_claim": "2016 [[3]]",
                                "new_quality": {"cr": 4.5, "aap": 4.2},
                                "kept": "new",
                            }
                        ],
                    }
                ]
            },
            search_context={"node_name": "task_verify"},
        )
        c = m.graph.nodes["e2"].contradictions[0]
        assert c["old_quality"] == {"cr": 3.0, "aap": 2.5}
        assert c["new_quality"] == {"cr": 4.5, "aap": 4.2}


# 4. Backward compat — readers still handle legacy v2 dict entries


class TestBackwardCompatV2Reads:
    """Read saved findings that use the older value-and-task wrapper."""

    def test_entry_value_unwraps_v2_dict_form(self):
        v2_entry = {"value": "Dominant [[5]]", "task": "task_market"}
        assert _entry_value(v2_entry) == "Dominant [[5]]"

    def test_entry_value_passes_plain_str(self):
        assert _entry_value("Dominant [[5]]") == "Dominant [[5]]"

    def test_to_planning_context_handles_v2_dict_entries(self):
        """Render saved findings containing value-and-task wrappers."""
        g = CognitiveGraph()
        n = make_node()
        n.item_findings = {"X": {"a": {"value": "Dominant [[5]]", "task": "task_market"}}}
        g.add_node(n)
        text = g.to_planning_context()
        assert "Dominant [[5]]" in text


# 5. End-to-end: search loop produces native-global cognitive graph


class TestEndToEndGlobalNamespace:
    """Keep distinct task sources in one global citation namespace."""

    def _make_agent_with_cgm(self):
        from veritrace.agent.cognitive_graph_manager import CognitiveGraphManager

        a = make_agent_skeleton()
        a.cognitive_manager = CognitiveGraphManager.__new__(CognitiveGraphManager)
        a.cognitive_manager.graph = CognitiveGraph()
        a.cognitive_manager.language = "en"
        a.cognitive_manager.llm = MagicMock()
        return a

    def test_two_tasks_distinct_urls_same_local_idx_resolve_to_distinct_globals(self):
        """Keep different sources distinct after allocator and graph updates."""
        a = self._make_agent_with_cgm()
        n = make_node(node_id="e_x")
        a.cognitive_manager.graph.add_node(n)

        # Task A done — allocator processes its answer
        ans_a, _ = a._allocate_globals_for_task(
            "task_a",
            "Dominant [[5]]",
            {5: {"url": "https://a.com", "title": "A"}},
        )
        # CGM stores using already-global text
        a.cognitive_manager._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e_x",
                        "item_findings": {"Scale AI": {"market": ans_a}},
                    }
                ]
            },
            search_context={"node_name": "task_a"},
        )

        # Task B done — same LOCAL [[5]] but different URL
        ans_b, _ = a._allocate_globals_for_task(
            "task_b",
            "2016 [[5]]",
            {5: {"url": "https://b.com", "title": "B"}},
        )
        a.cognitive_manager._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e_x",
                        "item_findings": {"Scale AI": {"founding_year": ans_b}},
                    }
                ]
            },
            search_context={"node_name": "task_b"},
        )

        attrs = a.cognitive_manager.graph.nodes["e_x"].item_findings["Scale AI"]
        # Each attr's text has the correct distinct global — no collision
        assert attrs["market"] == "Dominant [[1]]"
        assert attrs["founding_year"] == "2016 [[2]]"

    def test_supplement_merge_can_natively_cite_both_old_and_new(self):
        """Merged findings may cite both earlier and newly collected evidence."""
        a = self._make_agent_with_cgm()
        n = make_node(node_id="e_y")
        a.cognitive_manager.graph.add_node(n)

        # Round 1: task_old writes initial claim
        ans1, _ = a._allocate_globals_for_task(
            "task_old",
            "Released June [[0]]",
            {0: {"url": "https://old.com/release", "title": "Release notes"}},
        )
        a.cognitive_manager._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e_y",
                        "item_findings": {"Claude 4.8": {"info": ans1}},
                    }
                ]
            },
            search_context={"node_name": "task_old"},
        )

        # Round 2: task_new — Searcher answer with new global
        ans2_raw = "Performance jumped 20% [[1]]"
        ans2, _ = a._allocate_globals_for_task(
            "task_new",
            ans2_raw,
            {1: {"url": "https://new.com/bench", "title": "Benchmark"}},
        )
        # CGM does Supplement merge — value cites BOTH old and new globals

        merged_value = "Released June [[1]], measured 20% performance gain [[2]]"
        a.cognitive_manager._apply_update(
            {
                "cognitive_updates": [
                    {
                        "node_id": "e_y",
                        "item_findings": {"Claude 4.8": {"info": merged_value}},
                        "contradictions": [
                            {
                                "criterion": "Claude 4.8.info",
                                "old_claim": "Released June [[1]]",
                                "new_claim": merged_value,
                                "kept": "merged",
                            }
                        ],
                    }
                ]
            },
            search_context={"node_name": "task_new"},
        )

        # The merged value retains BOTH citations to different sources
        stored = a.cognitive_manager.graph.nodes["e_y"].item_findings["Claude 4.8"]["info"]
        refs = sorted(int(m) for m in re.findall(r"\[\[(\d+)\]\]", stored))
        assert refs == [1, 2], "merged value must cite both old [[1]] and new [[2]]"


# Test runner


if __name__ == "__main__":
    import subprocess

    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
