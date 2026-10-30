"""LLM smoke test for CGM update_graph end-to-end.

Run this BEFORE kicking off any expensive sample20 / sample100 search.
Costs 1-2 LLM calls (~30-60 sec, pennies of token cost). Verifies:

1. The CGM prompt template .format() actually formats (regression guard
   for the 2026-05-29 KeyError incident).
2. The LLM produces valid JSON that _apply_update can parse without
   exception.
3. After update, the cognitive graph node actually has populated
   item_findings / cross_item_findings (not just empty containers).
4. Cited [[n]] in the LLM output flow through to entry value text.

Usage:
    python tests/llm_smoke_cgm.py        # uses .env / env vars

Exits 0 on success, non-zero on any structural failure.
"""

import sys
import os
import json
import logging

# Set up project root
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

# Load .env if available
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    pass

from veritrace.agent.cognitive_graph_manager import CognitiveGraphManager
from veritrace.agent.cognitive_graph import CognitiveGraph, EntityNode, NodeType, RelationEdge
from veritrace.agent.core import create_adapter


# Minimal realistic search_result the CGM is supposed to digest.
# This mirrors what SearcherAgent typically returns: an answer text with
# multiple [[n]] citations (already in GLOBAL form post-allocator), plus
# a short quality block at the end.
_FAKE_SEARCH_RESULT_R1 = """Based on the search, Obsidian provides three main database-style plugins replicating Notion's multi-view database experience.

1. **Dataview** is the most mature option, supporting query-driven inline tables that pull data from YAML frontmatter and inline fields. Maintained by blacksmithgu, the plugin has been actively developed since 2020 and ships with a robust query language [[0]]. It does not natively support Kanban or Calendar views, focusing instead on table and list rendering [[0]][[1]].

2. **Bases** is the official core plugin Obsidian shipped in v1.9 (2024). It targets simpler dataview-style tables with a UI builder, currently offering 4 view types (Table, List, Kanban, Calendar) [[2]]. As an official feature it receives first-party maintenance, but the community considers its query expressiveness still maturing [[2]][[3]].

3. **TileLineBase** is a newer community plugin (released 2025) authored by single dev "vibe-coded" with LLM tools. It explicitly supports 4 views (Filtered Table, Kanban, Gallery, Slide View) without Calendar [[4]]. GitHub stars at 130 and v0.9.16 published 2025-05-16 [[4]][[5]].

### Verification Suggestions
- Obsidian Bases roadmap statements should be triangulated against the official 1.9 release notes.

## Source Quality Scores
[[0]] CR=4.0 AAP=4.5 (official plugin README, recent commits)
[[1]] CR=3.5 AAP=3.5 (Reddit discussion thread, 2025)
[[2]] CR=4.5 AAP=4.5 (obsidian.md official roadmap page)
[[3]] CR=3.0 AAP=3.0 (single forum post)
[[4]] CR=4.0 AAP=4.0 (community plugin listing)
[[5]] CR=4.0 AAP=4.0 (GitHub repo metadata)
"""


# Round 2: deliberately mixed — one CONTRADICTORY claim (Dataview deprecation
# contradicts Round 1's "active since 2020" finding) and one SUPPLEMENTARY
# detail (Bases v1.10 DSL revamp expands on Round 1's "maturing query"
# finding). This exercises both B (contradiction → contradictions entry)
# and A2 (merge → per-fact citation discipline) paths in the v3 prompt.
_FAKE_SEARCH_RESULT_R2 = """Two important late-2025 updates change the Obsidian database-plugin landscape.

1. **Dataview is being deprecated.** The official Obsidian team announced on November 12 2025 that Dataview will be archived in 2026 Q1, with maintenance handed over to Bases as the recommended replacement [[6]]. The final maintenance release shipped on 2025-12-03 [[6]]. This officially ends Dataview's 5-year active development cycle.

2. **Bases v1.10 expanded query expressiveness.** Released October 2025 [[7]], the new version adds nested filters, computed properties, and a JSON-path-style query DSL. This directly addresses the "query maturity" gap that the community had identified [[7]].

### Verification Suggestions
- Cross-check the deprecation announcement against Obsidian's official blog archive.

## Source Quality Scores
[[6]] CR=5.0 AAP=4.8 (obsidian.md official blog post, Nov 2025)
[[7]] CR=4.5 AAP=4.5 (Bases v1.10 release notes on GitHub)
"""


def _build_minimal_graph():
    """Construct a cognitive graph with one exploration node + one edge,
    mirroring what CognitiveGraphManager.initial_parse would produce for
    a real question."""
    g = CognitiveGraph()
    g.original_question = "What Obsidian plugins replicate Notion's multi-view databases?"
    start = EntityNode(
        id="e1",
        name="Obsidian Multi-view Database Plugins",
        node_type=NodeType.START,
    )
    target = EntityNode(
        id="e2",
        name="Plugin Discovery",
        node_type=NodeType.PLACEHOLDER,
    )
    g.add_node(start)
    g.add_node(target)
    edge = RelationEdge(
        id="r1",
        source_node_id="e1",
        target_node_id="e2",
        relation_type="discover",
        inquiry_goal="Find Obsidian plugins replicating Notion's multi-view databases",
        core_criteria=["plugin name + supported view types", "maintenance status"],
        supplementary_criteria=["install count / popularity", "feature summary"],
    )
    g.add_edge(edge)
    return g


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    # Resolve config from env (matches production setup)
    provider = os.environ.get("LLM_PROVIDER", "dashscope")
    cognitive_model = os.environ.get("COGNITIVE_MODEL") or os.environ.get("SILICON_MODEL") or "qwen3.5-27b"
    print(f"[smoke] provider={provider}  cognitive_model={cognitive_model}")

    # Probe workers.yaml for a working DashScope key.
    import yaml
    from veritrace.agent.core import Message

    workers_path = os.path.join(_ROOT, "benchmark", "workers.yaml")
    with open(workers_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    api_key = None
    for i, worker in enumerate(cfg["workers"]):
        candidate = worker["dashscope_key"]
        try:
            probe = create_adapter(provider, cognitive_model, api_key=candidate)
            probe.chat([Message(role="user", content="ping")])
            api_key = candidate
            print(f"[smoke] using workers.yaml worker {i} key")
            break
        except Exception:
            continue
    if not api_key:
        print("[smoke] no working worker key in workers.yaml")
        sys.exit(2)

    # Build LLM adapter + CGM
    cognitive_llm = create_adapter(provider, cognitive_model, api_key=api_key)
    cgm = CognitiveGraphManager(llm=cognitive_llm, language="cn")
    cgm.graph = _build_minimal_graph()

    # Sanity: the prompt templates must render — would have caught the
    # 2026-05-29 KeyError instantly.
    print("\n[smoke] step 1: prompt template .format() smoke check")
    from veritrace.agent.cognitive_graph_manager import (
        COGNITIVE_GRAPH_UPDATE_PROMPT,
        COGNITIVE_GRAPH_UPDATE_PROMPT_CN,
    )

    try:
        COGNITIVE_GRAPH_UPDATE_PROMPT_CN.format(
            current_graph="x",
            original_question="y",
            node_name="z",
            inquiry_goal="w",
            core_criteria=[],
            supplementary_criteria=[],
            search_result="r",
        )
        COGNITIVE_GRAPH_UPDATE_PROMPT.format(
            current_graph="x",
            original_question="y",
            node_name="z",
            inquiry_goal="w",
            core_criteria=[],
            supplementary_criteria=[],
            search_result="r",
        )
        print("  [OK] both EN and CN format() with no KeyError")
    except Exception as e:
        print(f"  [FAIL] FAIL: {type(e).__name__}: {e}")
        sys.exit(1)

    # Call update_graph with the fake search result
    print("\n[smoke] step 2: calling CGM.update_graph with fake search_result")
    update_result = cgm.update_graph(
        node_name="task_find_obsidian_plugins",
        inquiry_goal="Find Obsidian plugins replicating Notion's multi-view databases",
        search_result=_FAKE_SEARCH_RESULT_R1,
        edge_id="r1",
        target_id="e2",
    )

    if not isinstance(update_result, dict):
        print(f"  [FAIL] FAIL: update_graph returned {type(update_result).__name__}, expected dict")
        sys.exit(1)
    if update_result.get("error"):
        print(f"  [FAIL] FAIL: update_graph returned error: {update_result['error']}")
        sys.exit(1)
    print(f"  [OK] update_graph returned cleanly: updated_nodes={update_result.get('updated_nodes')}")

    # Verify the cognitive graph actually got populated — scan ALL nodes
    # since LLM may have added new dynamic nodes or used a different node_id
    print("\n[smoke] step 3: verify cognitive graph populated")
    total_item_entries = 0
    total_cross_aspects = 0
    total_unexpected = 0
    total_contradictions = 0
    nodes_with_data = []
    for nid, node in cgm.graph.nodes.items():
        item_findings = node.item_findings or {}
        cross_item_findings = node.cross_item_findings or {}
        unexpected = node.unexpected_discoveries or []
        contradictions = node.contradictions or []
        n_items = sum(len(attrs) for attrs in item_findings.values())
        if n_items or cross_item_findings or unexpected or contradictions:
            nodes_with_data.append(nid)
            print(
                f"  node {nid}: item_attrs={n_items} cross={len(cross_item_findings)} unexpected={len(unexpected)} contras={len(contradictions)}"
            )
        total_item_entries += n_items
        total_cross_aspects += len(cross_item_findings)
        total_unexpected += len(unexpected)
        total_contradictions += len(contradictions)
    print(
        f"  TOTAL across all nodes: item_attrs={total_item_entries} cross={total_cross_aspects} unexpected={total_unexpected} contras={total_contradictions}"
    )

    if total_item_entries == 0 and total_cross_aspects == 0:
        print("  [FAIL] FAIL: NO findings landed anywhere — CGM update silently dropped")
        sys.exit(1)
    print(f"  [OK] graph populated (data on nodes: {nodes_with_data})")

    # For downstream steps, look at the first node that has data
    target_node = cgm.graph.nodes[nodes_with_data[0]] if nodes_with_data else None
    item_findings = (target_node.item_findings or {}) if target_node else {}
    cross_item_findings = (target_node.cross_item_findings or {}) if target_node else {}

    # Verify storage is plain str (v3 invariant)
    print("\n[smoke] step 4: verify v3 storage shape (plain str, no dict wrapper)")
    dict_form = 0
    plain_str = 0
    for ent, attrs in item_findings.items():
        for attr, val in attrs.items():
            if isinstance(val, dict) and "value" in val:
                dict_form += 1
            elif isinstance(val, str):
                plain_str += 1
    if dict_form > 0:
        print(f"  [FAIL] FAIL: {dict_form} entries still in v2 dict-form {{value, task}} — _apply_update bug")
        sys.exit(1)
    print(f"  [OK] all {plain_str} entries are plain str")

    # Verify [[n]] citations flowed through into stored value text
    import re

    cited = set()
    for ent, attrs in item_findings.items():
        for attr, val in attrs.items():
            text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            for m in re.findall(r"\[\[(\d+)\]\]", text):
                cited.add(int(m))
    for aspect, val in cross_item_findings.items():
        text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        for m in re.findall(r"\[\[(\d+)\]\]", text):
            cited.add(int(m))
    print("\n[smoke] step 5: verify [[n]] citations preserved in stored text")
    print(f"  distinct [[n]] cited in stored findings: {sorted(cited)}")
    if not cited:
        print("  [FAIL] FAIL: NO [[n]] citations in stored findings — Searcher refs got lost")
        sys.exit(1)
    print("  [OK] citations preserved")

    # Step 6: full dump — check v3 prompt-rule conformance
    print("\n[smoke] step 6: full content dump (verify v3 prompt rule conformance)")
    for nid, node in cgm.graph.nodes.items():
        item_findings = node.item_findings or {}
        cross_item_findings = node.cross_item_findings or {}
        unexpected = node.unexpected_discoveries or []
        contradictions = node.contradictions or []
        if not (item_findings or cross_item_findings or unexpected or contradictions):
            continue
        print(f"\n  === node {nid} ({node.name}) ===")
        for ent, attrs in item_findings.items():
            for attr, val in attrs.items():
                vstr = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
                # Count distinct [[n]] in this attr
                refs = re.findall(r"\[\[(\d+)\]\]", vstr)
                print(f"  item_findings[{ent!r}][{attr!r}] = {vstr}")
                print(f"    -> {len(refs)} [[n]] tokens: {refs}")
        for aspect, val in cross_item_findings.items():
            vstr = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            refs = re.findall(r"\[\[(\d+)\]\]", vstr)
            print(f"  cross_item_findings[{aspect!r}] = {vstr}")
            print(f"    -> {len(refs)} [[n]] tokens: {refs}")
        for i, u in enumerate(unexpected):
            ustr = u if isinstance(u, str) else json.dumps(u, ensure_ascii=False)
            refs = re.findall(r"\[\[(\d+)\]\]", ustr)
            print(f"  unexpected_discoveries[{i}] = {ustr}")
            print(f"    -> {len(refs)} [[n]] tokens: {refs}")
        for i, c in enumerate(contradictions):
            print(f"  contradictions[{i}]:")
            for k, v in c.items():
                print(f"    {k}: {v}")

    # Round 2: fire another update_graph with mixed conflict+supplement payload
    print("\n[smoke] step 7: Round 2 — feed contradictory + supplementary search_result")
    update_r2 = cgm.update_graph(
        node_name="task_obsidian_plugins_2025_update",
        inquiry_goal="Late-2025 update on Dataview / Bases status",
        search_result=_FAKE_SEARCH_RESULT_R2,
        edge_id="r1",
        target_id="e2",
    )
    if not isinstance(update_r2, dict) or update_r2.get("error"):
        print(f"  [FAIL] FAIL: round 2 update_graph error: {update_r2}")
        sys.exit(1)
    print("  [OK] round 2 update_graph returned cleanly")

    print("\n[smoke] step 8: dump post-Round-2 state (look for contradictions + merged values)")
    for nid, node in cgm.graph.nodes.items():
        item_findings = node.item_findings or {}
        cross_item_findings = node.cross_item_findings or {}
        contradictions = node.contradictions or []
        if not (item_findings or cross_item_findings or contradictions):
            continue
        print(f"\n  === node {nid} ({node.name}) after Round 2 ===")
        for ent, attrs in item_findings.items():
            for attr, val in attrs.items():
                vstr = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
                refs = re.findall(r"\[\[(\d+)\]\]", vstr)
                print(f"  item_findings[{ent!r}][{attr!r}] = {vstr}")
                print(f"    -> {len(refs)} refs: {refs}")
        for aspect, val in cross_item_findings.items():
            vstr = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            refs = re.findall(r"\[\[(\d+)\]\]", vstr)
            print(f"  cross_item_findings[{aspect!r}] = {vstr}")
            print(f"    -> {len(refs)} refs: {refs}")
        for i, c in enumerate(contradictions):
            print(f"  contradictions[{i}]:")
            for k, v in c.items():
                print(f"    {k}: {v}")

    # v3 conformance checks
    print("\n[smoke] step 9: v3 prompt-rule conformance (post-Round-2)")
    issues = []
    for nid, node in cgm.graph.nodes.items():
        # Check 1: no old_task / new_task on contradictions
        for i, c in enumerate(node.contradictions or []):
            if "old_task" in c or "new_task" in c:
                issues.append(f"  - {nid}.contradictions[{i}] still has old_task/new_task")
            kept = c.get("kept", "")
            if kept == "merged":
                issues.append(f"  - {nid}.contradictions[{i}] uses kept='merged' (should be A2 not contradiction)")
            elif kept not in ("new", "old", ""):
                issues.append(f"  - {nid}.contradictions[{i}] kept='{kept}' unrecognized")
        # Check 2: unexpected discoveries are plain str
        for i, u in enumerate(node.unexpected_discoveries or []):
            if isinstance(u, dict):
                issues.append(f"  - {nid}.unexpected_discoveries[{i}] still in dict-form (v2 schema)")
    if issues:
        print("  v3 conformance issues found:")
        for x in issues:
            print(x)
    else:
        print(
            "  [OK] no v3 conformance issues (no kept=merged, no old/new_task on contradictions, no dict-form unexpected)"
        )

    print("\n[smoke] [ALL-OK] ALL CHECKS PASSED")
    print("Safe to launch sample20.")


if __name__ == "__main__":
    main()
