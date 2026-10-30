"""Cognitive-graph entities, inquiry edges, and operations for serial, parallel, and converging research."""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Set, Union
from enum import Enum


# Backward-compat helpers for legacy v2 dict-form entries

# Finding schema:
#   item_findings / cross_item_findings / unexpected_discoveries store
#   plain strings (or list[str] for cross_item_findings list-shape).
#   All [[n]] tokens are GLOBAL — the incremental allocator in
#   VeritraceAgent._allocate_globals_for_task rewrites them BEFORE CGM
#   ever sees the response_content. No per-task LOCAL namespace exists
#   inside the cognitive graph anymore.
#
# Legacy v2 evidence files stored entries as {"value": str, "task": str}
# dicts. `_entry_value` is kept solely so we can read those old files
# (report rewrites from saved evidence) without failing on the dictionary
# shape. All new writes are plain str.


def _entry_value(entry):
    """Backward-compat unwrapper for legacy v2 dict-form entries.

    New cognitive graphs (v3+) never produce dicts here — entries are
    plain str/list. This helper exists only for reading saved v2-era
    evidence files where item_findings values were wrapped as
    {"value":..., "task":...}.
    """
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return entry


class CognitiveState(Enum):
    """Knowledge state of an entity."""

    START = "START"  # Known entity explicitly named in the question; anchors the search.
    KNOWN = "KNOWN"  # Research complete: all core criteria have answers.
    PARTIAL = "PARTIAL"  # Partially known: some values have been discovered.
    UNKNOWN = "UNKNOWN"  # Unknown: requires search.


class NodeType(Enum):
    """Entity-node category."""

    START = "start"  # Starting entity that anchors the search.
    PLACEHOLDER = "placeholder"  # Placeholder entity awaiting discovery.
    DISCOVERED = "discovered"  # Discovered entity.


class EdgeState(Enum):
    """Inquiry-edge state."""

    KNOWN = "known"  # Known relationship.
    TO_SOLVE = "to_solve"  # Unresolved inquiry.
    INVESTIGATING = "investigating"  # Investigation has progressed, but the target remains PARTIAL.
    SOLVED = "solved"  # Resolved: the target has reached KNOWN.


class StructureType(Enum):
    """Research dependency structure."""

    SERIAL = "serial"  # Serial dependencies.
    PARALLEL = "parallel"  # Parallel chains.
    CONVERGENCE = "convergence"  # Converging branches.
    MIXED = "mixed"  # Mixed structure.


@dataclass
class EntityNode:
    """An entity node in the cognitive graph."""

    id: str  # Unique identifier.
    name: str  # Entity name, including placeholders such as '?supplier'.
    node_type: str = "start"  # "start" | "placeholder" | "discovered"
    cognitive_state: str = "START"  # "START" | "KNOWN" | "PARTIAL" | "UNKNOWN"
    type_constraint: Optional[str] = None  # Type constraints, such as company or country.
    condition_constraints: List[str] = field(default_factory=list)  # Condition constraints.
    hop_distance: int = 0  # Hop distance from the root.
    discovery_dependency: Optional[str] = None  # Prerequisite node IDs for discovery.
    discovered_items: List[str] = field(default_factory=list)  # Distinct discovered items, such as entities, options, or approaches.
    # Cognitive-graph search fields.
    pending_criteria: List[str] = field(default_factory=list)  # Deprecated field retained for compatibility.
    # Criteria tiers: core is required; supplementary is optional.
    core_pending: List[str] = field(default_factory=list)  # Unanswered core criteria.
    supplementary_pending: List[str] = field(default_factory=list)  # Unanswered supplementary criteria.
    unexpected_discoveries: List[str] = field(default_factory=list)  # Unexpected findings that may inform planning.

    # Structured findings.
    # Per-item findings used by the Planner to decide whether to split tasks.
    # Format: {item_name: {attribute: value_with_citation}}.
    # Example: {'Scale AI': {'market_position': 'Leading annotation vendor [[0]]', 'services': 'SFT, RLHF [[1]]'}}.
    # Example: {'RL approach': {'maturity': 'Mature, with commercial deployments [[0]]'}}.
    item_findings: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # Patterns, trends, and insights that span multiple items.
    # Format: {aspect: description_with_citation} or {aspect: [item1, item2, ...]}.
    # Example: {'industry_trend': 'Annotation is shifting toward AI assistance [[0]]'}.
    # List example: {'TIOBE ranking': ['#1 Python [[0]]', '#2 C++ [[0]]', '#3 Java [[0]]']}.
    cross_item_findings: Dict[str, Union[str, List[str]]] = field(default_factory=dict)

    cited_refs: List[int] = field(default_factory=list)  # Cited source indices, such as [0, 1, 3].
    temporal_notes: Optional[str] = None  # Temporal context.
    # Conflicting claims and the decision between them.
    # [{'criterion': 'supplier', 'old_claim': 'Company A [[0]]', 'new_claim': 'Company B [[3]]',
    #   'resolution': 'Use the official announcement over the news report', 'kept': 'new'}].
    contradictions: List[Dict[str, str]] = field(default_factory=list)

    # Search-task mapping.
    # Names of search tasks that contributed information to this node.
    # Used by OutlinePlanner and SectionWriter to query EvidenceStore.
    # Format: ['task_xxx', 'task_yyy'].
    related_tasks: List[str] = field(default_factory=list)

    # === CR/AAP Quality Profile ===
    # Aggregated quality signals from Reader, used by Planner for Free Energy decisions
    # Schema: {cr_mean, aap_mean, cr_min, aap_min, page_count,
    #          finding_strength, unexpected_strength,
    #          accessibility_barriers: List[str],
    #          _all_cr: List[float], _all_aap: List[float]}
    quality_profile: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Serialize the node to a dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "EntityNode":
        """Restore a node, ignoring unknown fields as RelationEdge.from_dict does."""
        allowed_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in allowed_fields}
        return cls(**filtered_data)

    def is_placeholder(self) -> bool:
        """Return whether the node is a placeholder."""
        return self.node_type == "placeholder"

    def is_discovered(self) -> bool:
        """Return whether a concrete value has been discovered."""
        return len(self.discovered_items) > 0

    def has_findings(self) -> bool:
        """Check for findings that protect the node during restructuring."""
        return bool(self.item_findings or self.cross_item_findings or self.discovered_items)

    def update_state(self):
        """Update the cognitive state from available findings."""
        if self.node_type == "start":
            self.cognitive_state = "START"
        elif len(self.discovered_items) == 0 and not self.item_findings and not self.cross_item_findings:
            # No findings yet.
            self.cognitive_state = "UNKNOWN"
        else:
            # Discoveries or findings exist, but may be incomplete.
            self.cognitive_state = "PARTIAL"


@dataclass
class RelationEdge:
    """An inquiry relationship with bounded search-attempt history. Source references belong to the corresponding EntityNode.cited_refs."""

    id: str  # Unique identifier.
    source_node_id: str  # Source entity ID.
    target_node_id: str  # Target entity ID.
    relation_type: str  # Relationship type, such as annotation services or office location.
    inquiry_goal: str  # Inquiry objective, expressed as a searchable question.
    state: str = "to_solve"  # "known" | "to_solve" | "investigating" | "solved" | "exhausted"

    # Criteria tiers assigned by the Planner when creating a task.
    core_criteria: List[str] = field(default_factory=list)  # Required core criteria.
    supplementary_criteria: List[str] = field(default_factory=list)  # Optional supplementary criteria.

    # Task type determines the Searcher's strategy.
    # 'specified': investigate the source explicitly required by the question.
    # 'open': explore freely and change search angles as needed.
    task_type: str = "open"
    # For specified-source tasks, retain the named source, such as William Reed or Wikipedia.
    specified_source: Optional[str] = None

    # Track search attempts.
    attempt_count: int = 0  # Number of attempts.
    max_attempts: int = 3  # Maximum attempts.
    search_history: List[Dict] = field(default_factory=list)  # Search history.
    # History format: [{'query': '...', 'quality': 2.1, 'summary': 'Insufficient information', 'node_name': 'search_q1'}].

    def to_dict(self) -> Dict:
        """Serialize the edge to a dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "RelationEdge":
        """Restore an edge from a dictionary."""
        # Supply defaults for fields absent from older records.
        allowed_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in allowed_fields}
        return cls(**filtered_data)

    def is_solved(self) -> bool:
        """Return whether the inquiry is solved."""
        return self.state == "solved"

    def is_exhausted(self) -> bool:
        """Return whether the attempt limit has been reached."""
        return self.attempt_count >= self.max_attempts or self.state == "exhausted"

    def mark_solved(self):
        """Mark the edge solved when its target reaches KNOWN."""
        self.state = "solved"

    def mark_investigating(self):
        """Mark progress while the target remains PARTIAL."""
        self.state = "investigating"

    def is_investigating(self) -> bool:
        """Return whether the inquiry is under investigation."""
        return self.state == "investigating"

    def mark_exhausted(self):
        """Mark the inquiry exhausted because the information is unavailable."""
        self.state = "exhausted"

    def record_attempt(self, query: str, summary: str, node_name: str = "", feedback: Optional[Dict] = None):
        """Record a search query, result summary, task name, and structured Searcher feedback."""
        import logging

        self.attempt_count += 1
        self.search_history.append(
            {
                "query": query,
                "summary": summary,
                "node_name": node_name,
                "feedback": feedback,
            }
        )

        # Log the attempt and feedback.
        logging.info(f"📝 [Edge:{self.id}] 记录第 {self.attempt_count}/{self.max_attempts} 次尝试")
        logging.debug(f"   query: {query[:50]}...")
        if feedback:
            logging.debug(f"   feedback: {feedback.get('availability')} | {feedback.get('suggested_action')}")

        # Mark the edge exhausted when the attempt limit is reached.
        if self.attempt_count >= self.max_attempts:
            logging.warning(f"⛔ [Edge:{self.id}] 已达最大尝试次数，标记为 exhausted")
            self.mark_exhausted()


@dataclass
class GraphStructure:
    """Dependency and concurrency information for the cognitive graph."""

    structure_type: str = "serial"  # "serial" | "parallel" | "convergence" | "mixed"
    parallel_chains: List[List[str]] = field(default_factory=list)  # Chains that can run in parallel.
    convergence_nodes: List[str] = field(default_factory=list)  # Convergence-node IDs.
    dependency_order: List[str] = field(default_factory=list)  # Dependency order.

    def to_dict(self) -> Dict:
        """Serialize graph structure to a dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "GraphStructure":
        """Restore graph structure from a dictionary."""
        return cls(**data)


class CognitiveGraph:
    """Thread-safe graph of entities and inquiries, supporting unresolved edges, parallel chains, convergence, and search-driven updates."""

    def __init__(self):
        import threading

        self._lock = threading.RLock()  # A reentrant lock allows nested access from the same thread.
        self.nodes: Dict[str, EntityNode] = {}
        self.edges: Dict[str, RelationEdge] = {}
        self.structure: GraphStructure = GraphStructure()
        self.languages: List[str] = ["en", "zh"]  # Suggested search languages.
        self.original_question: str = ""

    def add_node(self, node: EntityNode) -> str:
        """Add a node under the lock; warn on an ID collision."""
        import logging

        with self._lock:
            if node.id in self.nodes:
                logging.warning(f"[CognitiveGraph] Node ID collision: '{node.id}' already exists, overwriting")
            self.nodes[node.id] = node
            return node.id

    def add_edge(self, edge: RelationEdge) -> str:
        """Add an edge under the lock unless its source, target, and relationship type already exist."""
        with self._lock:
            # Check for a semantically equivalent edge.
            for existing_edge in self.edges.values():
                if (
                    existing_edge.source_node_id == edge.source_node_id
                    and existing_edge.target_node_id == edge.target_node_id
                    and existing_edge.relation_type == edge.relation_type
                ):
                    # Reuse the existing edge ID when the relationship is duplicated.
                    return existing_edge.id

            self.edges[edge.id] = edge
            return edge.id

    def remove_node(self, node_id: str, protected_edge_ids: Set[str] = None) -> Dict[str, Any]:
        """Remove a node and its incident edges under the lock. Reject removal if a protected edge refers to it; otherwise repair discovery dependencies and GraphStructure. Return blocked status, removed edge IDs, and a reason."""
        import logging

        if protected_edge_ids is None:
            protected_edge_ids = set()

        with self._lock:
            if node_id not in self.nodes:
                return {"blocked": False, "removed_edges": [], "reason": f"Node {node_id} not found"}

            # 1. Find all edges referencing this node.
            referencing_edges = [
                eid for eid, e in self.edges.items() if e.source_node_id == node_id or e.target_node_id == node_id
            ]

            # 2. Reject deletion if a protected edge references it.
            blocked_by = [eid for eid in referencing_edges if eid in protected_edge_ids]
            if blocked_by:
                logging.warning(f"[CognitiveGraph] remove_node({node_id}) blocked by protected edges: {blocked_by}")
                return {
                    "blocked": True,
                    "removed_edges": [],
                    "reason": f"Protected edges {blocked_by} reference this node",
                }

            # 3. Remove all incident edges.
            removed_edges = []
            for eid in referencing_edges:
                del self.edges[eid]
                removed_edges.append(eid)
            if removed_edges:
                logging.info(f"[CognitiveGraph] Cascade-removed edges: {removed_edges}")

            # 4. Remove this node from other nodes' discovery dependencies.
            for other_node in self.nodes.values():
                if other_node.discovery_dependency and node_id in other_node.discovery_dependency:
                    dep_ids = [d.strip() for d in other_node.discovery_dependency.split(",")]
                    dep_ids = [d for d in dep_ids if d != node_id]
                    other_node.discovery_dependency = ",".join(dep_ids) if dep_ids else None

            # 5. Repair GraphStructure.
            if node_id in self.structure.dependency_order:
                self.structure.dependency_order = [nid for nid in self.structure.dependency_order if nid != node_id]
            if node_id in self.structure.convergence_nodes:
                self.structure.convergence_nodes = [nid for nid in self.structure.convergence_nodes if nid != node_id]
            self.structure.parallel_chains = [
                [nid for nid in chain if nid != node_id] for chain in self.structure.parallel_chains
            ]
            # Discard empty chains.
            self.structure.parallel_chains = [chain for chain in self.structure.parallel_chains if len(chain) > 1]

            # 6. Remove the node.
            del self.nodes[node_id]
            logging.info(f"[CognitiveGraph] Removed node {node_id} (cascade-removed {len(removed_edges)} edges)")

            return {"blocked": False, "removed_edges": removed_edges, "reason": "OK"}

    def remove_edge(self, edge_id: str) -> bool:
        """Remove an edge under the lock and return whether deletion succeeded."""
        import logging

        with self._lock:
            if edge_id in self.edges:
                del self.edges[edge_id]
                logging.info(f"[CognitiveGraph] Removed edge {edge_id}")
                return True
            else:
                logging.warning(f"[CognitiveGraph] Edge {edge_id} not found for removal")
                return False

    def has_similar_edge(self, source_id: str, target_id: str, relation_type: str) -> bool:
        """Check for an equivalent edge under the lock."""
        with self._lock:
            for edge in self.edges.values():
                if (
                    edge.source_node_id == source_id
                    and edge.target_node_id == target_id
                    and edge.relation_type == relation_type
                ):
                    return True
            return False

    def get_node(self, node_id: str) -> Optional[EntityNode]:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def get_edge(self, edge_id: str) -> Optional[RelationEdge]:
        """Get an edge by ID."""
        return self.edges.get(edge_id)

    def get_unsolved_edges(self) -> List[RelationEdge]:
        """Return to_solve and investigating edges under the lock."""
        with self._lock:
            return [edge for edge in self.edges.values() if edge.state in ("to_solve", "investigating")]

    def get_solved_edges(self) -> List[RelationEdge]:
        """Return all solved edges."""
        return [edge for edge in self.edges.values() if edge.state == "solved"]

    def get_edges_by_source(self, source_id: str) -> List[RelationEdge]:
        """Return edges originating at the specified node."""
        return [edge for edge in self.edges.values() if edge.source_node_id == source_id]

    def get_edges_by_target(self, target_id: str) -> List[RelationEdge]:
        """Return edges leading to the specified node."""
        return [edge for edge in self.edges.values() if edge.target_node_id == target_id]

    def get_nodes_by_hop(self, hop_distance: int) -> List[EntityNode]:
        """Return nodes at the specified hop distance."""
        return [node for node in self.nodes.values() if node.hop_distance == hop_distance]

    def get_ready_edges(self) -> List[RelationEdge]:
        """Return unresolved edges whose source is not UNKNOWN. Investigating edges remain eligible until their target is KNOWN or attempts are exhausted."""
        ready = []
        for edge in self.get_unsolved_edges():
            source_node = self.get_node(edge.source_node_id)
            if source_node and source_node.cognitive_state != "UNKNOWN":
                ready.append(edge)
        return ready

    def get_investigating_edges(self) -> List[RelationEdge]:
        """Return all investigating edges."""
        return [edge for edge in self.edges.values() if edge.state == "investigating"]

    def get_parallel_chains(self) -> List[List[str]]:
        """Group edge IDs into chains that can be explored in parallel."""
        if self.structure.parallel_chains:
            return self.structure.parallel_chains

        # Identify edges that can currently run in parallel.
        ready_edges = self.get_ready_edges()

        # Group edges by source node.
        by_source: Dict[str, List[str]] = {}
        for edge in ready_edges:
            if edge.source_node_id not in by_source:
                by_source[edge.source_node_id] = []
            by_source[edge.source_node_id].append(edge.id)

        # Edges from the same source can run in parallel.
        parallel = []
        for source_id, edge_ids in by_source.items():
            if len(edge_ids) > 1:
                parallel.append(edge_ids)

        # Independent edges from different sources can also run in parallel.
        independent_edges = []
        for edge in ready_edges:
            # Find edges not already assigned to a parallel group.
            in_parallel = any(edge.id in chain for chain in parallel)
            if not in_parallel:
                independent_edges.append(edge.id)

        if len(independent_edges) > 1:
            parallel.append(independent_edges)

        return parallel

    def get_convergence_nodes(self) -> List[EntityNode]:
        """Find nodes with multiple incoming edges, where research branches converge."""
        if self.structure.convergence_nodes:
            return [self.get_node(nid) for nid in self.structure.convergence_nodes if self.get_node(nid)]

        # Count incoming edges for each node.
        target_count: Dict[str, int] = {}
        for edge in self.edges.values():
            target_id = edge.target_node_id
            target_count[target_id] = target_count.get(target_id, 0) + 1

        # Nodes with multiple incoming edges are convergence points.
        convergence = []
        for node_id, count in target_count.items():
            if count > 1:
                node = self.get_node(node_id)
                if node:
                    convergence.append(node)

        return convergence

    def replace_placeholder(self, placeholder_id: str, values: List[str]):
        """Replace a placeholder with concrete discovered values."""
        node = self.get_node(placeholder_id)
        if not node:
            return

        node.discovered_items = values
        node.node_type = "discovered"
        node.update_state()

    def _resolve_inquiry_goal_placeholders(self, inquiry_goal: str) -> str:
        """Replace [node name] placeholders with the matching node's comma-separated discovered_items."""
        import re

        result = inquiry_goal

        # Find placeholders written as [node name].
        placeholders = re.findall(r"\[([^\]]+)\]", inquiry_goal)

        for placeholder in placeholders:
            # Match each placeholder to a node by name.
            for node in self.nodes.values():
                if node.name == placeholder and node.discovered_items:
                    # Replace the placeholder with discovered values.
                    replacement = ", ".join(node.discovered_items)
                    result = result.replace(f"[{placeholder}]", replacement)
                    break

        return result

    def to_dict(self) -> Dict:
        """Serialize the graph for JSON output and frontend use."""
        return {
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": {eid: edge.to_dict() for eid, edge in self.edges.items()},
            "structure": self.structure.to_dict(),
            "languages": self.languages,
            "original_question": self.original_question,
            "summary": self.get_summary(),
        }

    def get_summary(self) -> Dict:
        """Summarize the cognitive graph."""
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "start_nodes": len([n for n in self.nodes.values() if n.cognitive_state == "START"]),
            "known_nodes": len([n for n in self.nodes.values() if n.cognitive_state == "KNOWN"]),
            "unknown_nodes": len([n for n in self.nodes.values() if n.cognitive_state == "UNKNOWN"]),
            "partial_nodes": len([n for n in self.nodes.values() if n.cognitive_state == "PARTIAL"]),
            "solved_edges": len(self.get_solved_edges()),
            "investigating_edges": len(self.get_investigating_edges()),
            "unsolved_edges": len(self.get_unsolved_edges()),  # Include both to_solve and investigating edges.
            "structure_type": self.structure.structure_type,
            "parallel_chains_count": len(self.get_parallel_chains()),
            "convergence_nodes_count": len(self.get_convergence_nodes()),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "CognitiveGraph":
        """Restore a cognitive graph from a dictionary."""
        graph = cls()
        graph.original_question = data.get("original_question", "")
        graph.languages = data.get("languages", ["en", "zh"])

        # Restore nodes.
        for nid, node_data in data.get("nodes", {}).items():
            graph.nodes[nid] = EntityNode.from_dict(node_data)

        # Restore edges.
        for eid, edge_data in data.get("edges", {}).items():
            graph.edges[eid] = RelationEdge.from_dict(edge_data)

        # Restore graph structure.
        if "structure" in data:
            graph.structure = GraphStructure.from_dict(data["structure"])

        return graph

    def _compute_cascade_impact(self, edge_id: str, protected_nodes: List[str]) -> List[str]:
        """
        Compute which nodes would become orphans if an edge is deleted.

        Uses BFS from root nodes (excluding the target edge) to find reachability.
        Nodes not reachable AND not protected AND not known-type → cascade orphans.

        Args:
            edge_id: The edge to hypothetically remove
            protected_nodes: Node IDs that are protected from deletion

        Returns:
            List of node IDs that would become orphaned
        """
        edge = self.get_edge(edge_id)
        if not edge:
            return []

        # Find root nodes (known type)
        root_ids = {nid for nid, node in self.nodes.items() if node.node_type == "start"}

        # BFS from roots, following all edges EXCEPT the excluded one
        reachable = set(root_ids)
        queue = list(root_ids)
        while queue:
            current = queue.pop(0)
            for e in self.get_edges_by_source(current):
                if e.id == edge_id:
                    continue
                target = e.target_node_id
                if target not in reachable and target in self.nodes:
                    reachable.add(target)
                    queue.append(target)

        # Orphans: not reachable, not protected, not known-type
        protected_set = set(protected_nodes)
        orphans = []
        for nid, node in self.nodes.items():
            if nid not in reachable and nid not in protected_set and node.node_type != "start":
                orphans.append(nid)

        return orphans

    def to_restructure_view(self, protected_nodes: List[str], protected_edges: List[str]) -> str:
        """
        Tree-form topology view for the restructuring LLM.

        Includes:
        1. Tree topology with protection markers (🔒)
        2. DAG convergence handling (first expand, subsequent cross-reference)
        3. Protection status summary
        4. Cascade impact table (only edges whose deletion causes >= 2 orphaned nodes)

        Args:
            protected_nodes: Node IDs that have findings or are KNOWN/PARTIAL
            protected_edges: Edge IDs that have search history (solved/investigating/exhausted)

        Returns:
            Formatted string for LLM consumption
        """
        lines = []

        # --- Section 1: Tree topology ---
        lines.append("## Graph Topology\n")

        # Find root nodes
        root_ids = [nid for nid, n in self.nodes.items() if n.node_type == "start"]
        if not root_ids:
            # Fallback: nodes with no incoming edges
            root_ids = [nid for nid in self.nodes if not self.get_edges_by_target(nid)]

        visited: Set[str] = set()

        def _render_node(node_id: str, prefix: str, is_last: bool, edge_id: str = None):
            node = self.get_node(node_id)
            if not node:
                return

            state = node.cognitive_state
            n_lock = " 🔒" if node_id in protected_nodes else ""

            if edge_id is None:
                # Root node — no connector
                lines.append(f"{node_id} {node.name} [{state}]{n_lock} ← root")
                child_prefix = ""
            else:
                connector = "└─" if is_last else "├─"
                e = self.get_edge(edge_id)
                e_lock = "🔒" if edge_id in protected_edges else ""

                # DAG convergence: already visited → cross-reference
                if node_id in visited:
                    lines.append(f"{prefix}{connector} {edge_id}{e_lock} → (→ see {node_id} above)")
                    return

                lines.append(f"{prefix}{connector} {edge_id}{e_lock} → {node_id} {node.name} [{state}]{n_lock}")

                # Show inquiry goal below the edge line
                cont = "│  " if not is_last else "   "
                if e:
                    lines.append(f'{prefix}{cont}     inquiry: "{e.inquiry_goal}"')

                child_prefix = prefix + cont

            visited.add(node_id)

            # Recurse to children via outgoing edges
            outgoing = self.get_edges_by_source(node_id)
            for i, out_e in enumerate(outgoing):
                _render_node(out_e.target_node_id, child_prefix, i == len(outgoing) - 1, out_e.id)

        for i, rid in enumerate(root_ids):
            _render_node(rid, "", i == len(root_ids) - 1)

        # --- Section 2: Protection status ---
        modifiable_n = [
            nid for nid in self.nodes if nid not in protected_nodes and self.nodes[nid].node_type != "start"
        ]
        modifiable_e = [eid for eid in self.edges if eid not in protected_edges]

        lines.append("")
        lines.append("## Protection Status")

        if protected_nodes:
            labels = []
            for nid in protected_nodes:
                n = self.get_node(nid)
                reason = "start" if (n and n.node_type == "start") else "has findings"
                labels.append(f"{nid}({reason})")
            lines.append(f"PROTECTED nodes: [{', '.join(labels)}]")

        if protected_edges:
            labels = []
            for eid in protected_edges:
                e = self.get_edge(eid)
                labels.append(f"{eid}({e.state if e else '?'})")
            lines.append(f"PROTECTED edges: [{', '.join(labels)}]")

        if modifiable_n:
            lines.append(f"Modifiable nodes: [{', '.join(modifiable_n)}]")
        if modifiable_e:
            lines.append(f"Modifiable edges: [{', '.join(modifiable_e)}]")

        # --- Section 3: Cascade impact table ---
        lines.append("")
        lines.append("## Cascade Impact")

        has_entry = False
        for eid in self.edges:
            cascade = self._compute_cascade_impact(eid, protected_nodes)
            if len(cascade) >= 2:
                edge = self.get_edge(eid)
                target = edge.target_node_id if edge else "?"
                others = [c for c in cascade if c != target]
                if target in cascade:
                    lines.append(f"Delete {eid} → orphan({target}) → cascade orphans({', '.join(others)})")
                else:
                    lines.append(f"Delete {eid} → cascade orphans({', '.join(cascade)})")
                has_entry = True

        if not has_entry:
            lines.append("No cascade impacts (>= 2 nodes) detected.")

        return "\n".join(lines)

    def to_planning_context(self) -> str:
        """Render Planner context with node IDs, hop distances, discovery dependencies, findings, unanswered criteria, and temporal notes."""
        lines = ["## 当前认知图状态", ""]

        # Structure type.
        lines.append(f"**结构类型**: {self.structure.structure_type}")
        lines.append("")

        # Render complete entity-node attributes.
        lines.append("### 实体节点")
        # Order by hop distance, with nearer nodes first.
        sorted_nodes = sorted(self.nodes.values(), key=lambda n: n.hop_distance)
        for node in sorted_nodes:
            state_emoji = {"START": "📌", "KNOWN": "✅", "PARTIAL": "🔶", "UNKNOWN": "❓"}.get(node.cognitive_state, "")

            # Basic node information.
            constraint = f" [{node.type_constraint}]" if node.type_constraint else ""
            lines.append(f"- {state_emoji} **{node.name}** (id: `{node.id}`, hop: {node.hop_distance}){constraint}")

            # Dependencies may contain comma-separated node IDs.
            if node.discovery_dependency:
                dep_ids = [d.strip() for d in node.discovery_dependency.split(",")]
                dep_names = []
                for dep_id in dep_ids:
                    dep_node = self.get_node(dep_id)
                    dep_names.append(dep_node.name if dep_node else dep_id)
                lines.append(f"  - 依赖: 必须先发现 `{'`, `'.join(dep_names)}`")

            # Condition constraints.
            if node.condition_constraints:
                lines.append(f"  - 条件: {', '.join(node.condition_constraints)}")

            # Discovered items.
            if node.discovered_items:
                lines.append(f"  - 发现项目: {', '.join(node.discovered_items)}")

            # Render item_findings as a table.
            # Findings may be plain values or attributed dictionaries.
            if node.item_findings:
                lines.append("  - 📊 **项目属性:**")
                for item_name, attrs in node.item_findings.items():
                    for attr_name, entry in attrs.items():
                        attr_value = _entry_value(entry)
                        attr_text = attr_value if isinstance(attr_value, str) else str(attr_value)
                        value_short = attr_text[:150] + "..." if len(attr_text) > 150 else attr_text
                        lines.append(f"    - {item_name}.{attr_name}: {value_short}")

            # cross_item_findings accepts strings, lists, and attributed dictionaries.
            if node.cross_item_findings:
                lines.append("  - 💡 **跨项目洞察:**")
                for aspect, entry in node.cross_item_findings.items():
                    description = _entry_value(entry)
                    if isinstance(description, list):
                        lines.append(f"    - {aspect}:")
                        for i, item in enumerate(description):
                            item_str = item if isinstance(item, str) else str(item)
                            item_short = item_str[:150] + "..." if len(item_str) > 150 else item_str
                            lines.append(f"      {i + 1}. {item_short}")
                    else:
                        desc_short = str(description)[:150] + "..." if len(str(description)) > 150 else str(description)
                        lines.append(f"    - {aspect}: {desc_short}")

            # Unanswered criteria by tier.
            if node.core_pending:
                lines.append(f"  - 🔴 核心待验收: {', '.join(node.core_pending)}")
            if node.supplementary_pending:
                lines.append(f"  - 🟡 补充待验收: {', '.join(node.supplementary_pending)}")

            # Unexpected findings for the Planner.
            # each item may be {"value":..., "task":...}
            # or legacy plain str — unwrap transparently for display.
            if node.unexpected_discoveries:
                lines.append(f"  - 💡 意外发现 ({len(node.unexpected_discoveries)}条):")
                for discovery in node.unexpected_discoveries[:5]:  # Show only the first five entries.
                    text = _entry_value(discovery)
                    text_str = text if isinstance(text, str) else str(text)
                    lines.append(f"    - {text_str[:120]}...")

            # Temporal context.
            if node.temporal_notes:
                lines.append(f"  - ⏰ 时间说明: {node.temporal_notes}")

            # Conflicts and the decisions between old and new claims.
            # Conflict records may include old_quality / new_quality
            # (CR/AAP at the time of resolution) so the next CGM round can see
            # the per-source quality comparison the LLM used.
            if node.contradictions:
                lines.append(f"  - ⚠️ 矛盾记录 ({len(node.contradictions)}条):")
                for contradiction in node.contradictions:
                    criterion = contradiction.get("criterion") or ""
                    old_claim = (contradiction.get("old_claim") or "")[:150]
                    new_claim = (contradiction.get("new_claim") or "")[:150]
                    resolution = contradiction.get("resolution") or ""
                    kept = contradiction.get("kept") or "new"
                    old_q = contradiction.get("old_quality")
                    new_q = contradiction.get("new_quality")
                    q_suffix = ""
                    if isinstance(old_q, dict) or isinstance(new_q, dict):

                        def _fmt_q(q):
                            if not isinstance(q, dict):
                                return "?"
                            return f"CR={q.get('cr', '?')} AAP={q.get('aap', '?')}"

                        q_suffix = f" | quality: old({_fmt_q(old_q)}) vs new({_fmt_q(new_q)})"
                    lines.append(f"    - [{criterion}] 旧: {old_claim}... → 新: {new_claim}...{q_suffix}")
                    lines.append(f"      决定: {resolution} (采用: {kept})")

            # Quality profile (CR/AAP composites from Reader)
            qp = node.quality_profile
            if qp and qp.get("page_count", 0) > 0:
                cr_mean = qp.get("cr_mean", 0)
                aap_mean = qp.get("aap_mean", 0)
                q_emoji = "🟢" if (cr_mean >= 4.0 and aap_mean >= 3.5) else "🟡" if cr_mean >= 3.0 else "🔴"
                finding_str = qp.get("finding_strength", "unknown")
                unexpected_str = qp.get("unexpected_strength", "unknown")
                lines.append(
                    f"  - 📊 Source Quality: {q_emoji} CR={cr_mean:.1f} AAP={aap_mean:.1f} "
                    f"({qp.get('page_count', 0)} sources) | "
                    f"finding: {finding_str}, unexpected: {unexpected_str}"
                )
                # Per-source quality uses the graph's global reference IDs.
                ref_q = qp.get("ref_quality") or {}
                if isinstance(ref_q, dict) and ref_q:
                    ref_parts = []
                    for ref_key, scores in ref_q.items():
                        if not isinstance(scores, dict):
                            continue
                        cr_r = scores.get("cr", "?")
                        aap_r = scores.get("aap", "?")
                        ref_parts.append(f"[[{ref_key}]] CR={cr_r} AAP={aap_r}")
                    if ref_parts:
                        # Cap rendering at 25 refs to keep prompt compact
                        shown = " · ".join(ref_parts[:25])
                        suffix = f" (+{len(ref_parts) - 25} more)" if len(ref_parts) > 25 else ""
                        lines.append(f"  - 🔍 Per-ref quality: {shown}{suffix}")
                barriers = qp.get("accessibility_barriers", [])
                if barriers:
                    lines.append(f"  - ⚠️ Accessibility: {', '.join(barriers[:3])}")

        lines.append("")

        # Render relationship edges by state.
        solved_edges = [e for e in self.edges.values() if e.state == "solved"]
        investigating_edges = [e for e in self.edges.values() if e.state == "investigating"]
        unsolved_edges = [e for e in self.edges.values() if e.state == "to_solve"]
        exhausted_edges = [e for e in self.edges.values() if e.state == "exhausted"]

        if solved_edges:
            lines.append("### ✅ 已解决的询问目标")
            for edge in solved_edges:
                source = self.get_node(edge.source_node_id)
                target = self.get_node(edge.target_node_id)
                source_name = source.name if source else edge.source_node_id
                target_name = target.name if target else edge.target_node_id
                lines.append(f"- [{source_name}] --{edge.relation_type}--> [{target_name}]")
            lines.append("")

        # Show investigating edges whose targets remain PARTIAL.
        if investigating_edges:
            lines.append("### 🔄 调查中（目标节点仍有信息缺口）")
            for edge in investigating_edges:
                source = self.get_node(edge.source_node_id)
                target = self.get_node(edge.target_node_id)
                source_name = source.name if source else edge.source_node_id
                target_name = target.name if target else edge.target_node_id
                target_state = target.cognitive_state if target else "UNKNOWN"
                lines.append(f"- [{source_name}] --{edge.relation_type}--> [{target_name}] (目标状态: {target_state})")

                # Resolve placeholders in inquiry_goal.
                inquiry_goal = self._resolve_inquiry_goal_placeholders(edge.inquiry_goal)
                lines.append(f"  问题: {inquiry_goal}")
                lines.append(f"  edge_id: {edge.id}, target_id: {edge.target_node_id}")
                lines.append(f"  task_type: {edge.task_type}")
                if edge.specified_source:
                    lines.append(f"  specified_source: {edge.specified_source}")
                lines.append(f"  尝试次数: {edge.attempt_count}/{edge.max_attempts}")

                # Show the edge's criteria.
                if edge.core_criteria:
                    lines.append(f"  📋 核心验收标准: {', '.join(edge.core_criteria)}")
                if edge.supplementary_criteria:
                    lines.append(f"  📋 补充验收标准: {', '.join(edge.supplementary_criteria)}")

                # Show the target node's unanswered criteria by tier.
                if target and target.core_pending:
                    lines.append(f"  🔴 核心待验收: {', '.join(target.core_pending)}")
                if target and target.supplementary_pending:
                    lines.append(f"  🟡 补充待验收: {', '.join(target.supplementary_pending)}")

                # Search history and feedback help the Planner change strategy.
                if edge.search_history:
                    lines.append("  📜 已尝试:")
                    for i, attempt in enumerate(edge.search_history, 1):  # Show the complete history.
                        query = attempt.get("query", "")[:40]
                        lines.append(f'    #{i}: "{query}"')
                        fb = attempt.get("feedback")
                        if fb:
                            can_improve = fb.get("can_improve", "N/A")
                            reason = fb.get("reason", "")
                            lines.append(f"        → can_improve: {can_improve}" + (f" | {reason}" if reason else ""))
                        exp = attempt.get("experience", [])
                        if exp:
                            lines.append(f"        🚫 经验: {'; '.join(str(e)[:150] for e in exp[:5])}")

                # Quality signal for investigating edge (Planner uses for Free Energy decisions)
                if target and target.quality_profile and target.quality_profile.get("page_count", 0) > 0:
                    qp = target.quality_profile
                    cr_m = qp.get("cr_mean", 0)
                    aap_m = qp.get("aap_mean", 0)
                    q_emoji = "🟢" if (cr_m >= 4.0 and aap_m >= 3.5) else "🟡" if cr_m >= 3.0 else "🔴"
                    lines.append(
                        f"  📊 Quality: {q_emoji} CR={cr_m:.1f} AAP={aap_m:.1f} | "
                        f"finding: {qp.get('finding_strength', '?')}, "
                        f"unexpected: {qp.get('unexpected_strength', '?')}"
                    )
                    barriers = qp.get("accessibility_barriers", [])
                    if barriers:
                        lines.append(f"  ⚠️ Accessibility: {', '.join(barriers[:3])}")
            lines.append("")

        # Show edges that have exhausted their attempts.
        if exhausted_edges:
            lines.append("### ⛔ 信息不可得（已耗尽尝试）")
            for edge in exhausted_edges:
                source = self.get_node(edge.source_node_id)
                target = self.get_node(edge.target_node_id)
                source_name = source.name if source else edge.source_node_id
                target_name = target.name if target else edge.target_node_id
                lines.append(f"- [{source_name}] --{edge.relation_type}--> [{target_name}]")
                lines.append(f"  尝试次数: {edge.attempt_count}/{edge.max_attempts}")
                # Show search history and Searcher feedback.
                if edge.search_history:
                    lines.append("  搜索历史:")
                    for h in edge.search_history[-2:]:  # Show only the two most recent attempts.
                        lines.append(f'    - "{h.get("query", "")[:50]}"')
                        fb = h.get("feedback")
                        if fb:
                            lines.append(f"      📊 can_improve: {fb.get('can_improve', 'N/A')}")
                            if fb.get("reason"):
                                lines.append(f"      ❓ 原因: {fb.get('reason')}")
            lines.append("")

        if unsolved_edges:
            lines.append("### 🔍 待解决的询问目标")
            for edge in unsolved_edges:
                source = self.get_node(edge.source_node_id)
                target = self.get_node(edge.target_node_id)
                source_name = source.name if source else edge.source_node_id
                target_name = target.name if target else edge.target_node_id
                lines.append(f"- [{source_name}] --{edge.relation_type}--> [{target_name}]")

                # Resolve placeholders in inquiry_goal.
                inquiry_goal = self._resolve_inquiry_goal_placeholders(edge.inquiry_goal)
                lines.append(f"  问题: {inquiry_goal}")

                # Include the mapping fields required by the Planner.
                lines.append(f"  edge_id: {edge.id}, target_id: {edge.target_node_id}")
                lines.append(f"  task_type: {edge.task_type}")
                if edge.specified_source:
                    lines.append(f"  specified_source: {edge.specified_source}")

                # Show attempt counts and search history.
                lines.append(f"  尝试次数: {edge.attempt_count}/{edge.max_attempts}")
                if edge.attempt_count > 0:
                    lines.append("  📜 已尝试:")
                    for i, attempt in enumerate(edge.search_history, 1):
                        query = attempt.get("query", "")[:40]
                        lines.append(f'    #{i}: "{query}"')
                        fb = attempt.get("feedback")
                        if fb:
                            can_improve = fb.get("can_improve", "N/A")
                            reason = fb.get("reason", "")
                            lines.append(f"        → can_improve: {can_improve}" + (f" | {reason}" if reason else ""))
                        exp = attempt.get("experience", [])
                        if exp:
                            lines.append(f"        🚫 经验: {'; '.join(str(e)[:150] for e in exp[:5])}")
            lines.append("")

        # Summary statistics.
        investigating_count = len(investigating_edges)
        exhausted_count = len(exhausted_edges)
        progress_parts = [f"{len(solved_edges)}/{len(self.edges)} 已解决"]
        if investigating_count > 0:
            progress_parts.append(f"{investigating_count} 调查中")
        if exhausted_count > 0:
            progress_parts.append(f"{exhausted_count} 信息不可得")
        lines.append(f"**进度**: {', '.join(progress_parts)}")
        lines.append("")

        # Parallel chains.
        parallel = self.get_parallel_chains()
        if parallel:
            lines.append("### 可并行探索")
            for i, chain in enumerate(parallel):
                lines.append(f"- 并行组{i + 1}: {', '.join(chain)}")
            lines.append("")

        # Convergence nodes.
        convergence = self.get_convergence_nodes()
        if convergence:
            lines.append("### 汇聚节点（需等待多条链路）")
            for node in convergence:
                lines.append(f"- {node.name} (id: `{node.id}`)")
            lines.append("")

        # Distinguish missing information from insufficient source quality.
        info_gaps = [n for n in self.nodes.values() if n.cognitive_state == "UNKNOWN"]
        quality_gaps = [n for n in self.nodes.values() if n.cognitive_state == "PARTIAL"]

        if info_gaps or quality_gaps:
            lines.append("### 认知空缺")
            if info_gaps:
                lines.append("**Information Gaps (未知):**")
                for node in info_gaps:
                    dep_info = ""
                    if node.discovery_dependency:
                        dep_ids = [d.strip() for d in node.discovery_dependency.split(",")]
                        dep_names = []
                        for dep_id in dep_ids:
                            dep_n = self.get_node(dep_id)
                            dep_names.append(dep_n.name if dep_n else dep_id)
                        dep_info = f" (需先发现: {'、'.join(dep_names)})"
                    lines.append(f"- ❓ {node.name} (id: `{node.id}`, hop: {node.hop_distance}){dep_info}")
            if quality_gaps:
                lines.append("**Quality Gaps (部分已知):**")
                for node in quality_gaps:
                    lines.append(f"- 🔶 {node.name} (id: `{node.id}`)")
                    if node.core_pending:
                        lines.append(f"  核心待验收: {', '.join(node.core_pending)}")
                    if node.supplementary_pending:
                        lines.append(f"  补充待验收: {', '.join(node.supplementary_pending)}")
            lines.append("")

        return "\n".join(lines)

    def __repr__(self) -> str:
        summary = self.get_summary()
        return (
            f"CognitiveGraph(nodes={summary['total_nodes']}, edges={summary['total_edges']}, "
            f"solved={summary['solved_edges']}/{summary['total_edges']})"
        )
