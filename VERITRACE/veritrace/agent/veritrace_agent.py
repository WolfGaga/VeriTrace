"""Coordinate cognitive planning, evidence collection, and report writing."""

import json
import logging
from .runtime.progress import report_progress, publish_research_event
import os
import re
import threading
from collections import defaultdict
from concurrent.futures import CancelledError, as_completed
from .concurrency import ThreadPoolExecutor, cancellation_scope
from copy import deepcopy
from typing import Dict, Iterator, List, Optional, Tuple, AsyncIterator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

from .core import (
    Message,
    create_adapter,
    build_veritrace_planner_prompt,
)

from .runtime import ResearchEvent, ResearchResult
from .runtime.scheduler import TaskScheduler
from .runtime.planner import TaskQuery
from .runtime.planner_graph import PlannerGraph
from .runtime.program import CodeExtractor, PlannerProgram
from .cognitive_graph_manager import CognitiveGraphManager
from .evidence_store import EvidenceStore, EvidenceRecord


def _update_ref(ref: str, ref2url: Dict[str, str], ptr: int) -> Tuple[str, Dict, int]:
    """Update reference indices in text."""

    numbers = sorted({int(n) for n in re.findall(r"\[\[(\d+)\]\]", ref)})
    numbers_map = {n: idx + 1 for idx, n in enumerate(numbers)}

    logging.debug(f"[_update_ref] ptr={ptr}, numbers={numbers}, numbers_map={numbers_map}")
    logging.debug(f"[_update_ref] ref2url keys: {list(ref2url.keys())}")

    updated_ref = re.sub(
        r"\[\[(\d+)\]\]",
        lambda match: f"[[{numbers_map[int(match.group(1))] + ptr}]]",
        ref,
    )
    updated_ref2url = {}
    if numbers_map:
        missing_refs = [n for n in numbers_map if n not in ref2url]
        if missing_refs:
            logging.warning(f"[_update_ref] Missing refs in ref2url: {missing_refs}")

        if ref2url:
            updated_ref2url = {numbers_map[idx] + ptr: ref2url[idx] for idx in numbers_map if idx in ref2url}

            for idx in numbers_map:
                if idx in ref2url:
                    new_idx = numbers_map[idx] + ptr
                    url_info = ref2url[idx]
                    title = url_info.get("title", "N/A") if isinstance(url_info, dict) else "N/A"
                    logging.debug(f"[_update_ref] [[{idx}]] -> [[{new_idx}]]: {title[:50]}")

    return updated_ref, updated_ref2url, len(numbers_map)


def _extract_verification_suggestions(content: str) -> str:
    """Extract verification suggestions; answer details already reach the Planner through cognitive-graph findings."""

    patterns = [
        r"###\s*Verification Suggestions\s*\n(.*?)(?=\n##|\n---|\Z)",
        r"###\s*验证建议\s*\n(.*?)(?=\n##|\n---|\Z)",
        r"\*\*Verification Suggestions\*\*\s*\n(.*?)(?=\n##|\n---|\Z)",
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if match:
            suggestions = match.group(1).strip()
            if suggestions:
                return f"### Verification Suggestions\n{suggestions}"

    return ""


def _generate_references_from_graph(graph: Dict[str, dict]) -> Tuple[str, Dict[int, dict]]:
    """Generate reference mappings and return only verification notes to the Planner, avoiding duplicate findings."""
    ptr, references_url = 0, {}
    verification_suggestions = []

    for name, data_item in graph.items():
        logging.debug(f"📑 [RefGen] Processing node: {name}")

        ref2url = {}
        if data_item.get("result"):
            memory = data_item.get("result") or {}
            if isinstance(memory, dict):
                raw_ref2url = memory.get("references", {})
                if raw_ref2url:
                    try:
                        ref2url = {int(k): v for k, v in raw_ref2url.items()}
                        logging.debug(f"   ref2url has {len(ref2url)} entries: {list(ref2url.keys())}")
                        for idx, info in sorted(ref2url.items()):
                            title = info.get("title", "N/A") if isinstance(info, dict) else "N/A"
                            logging.debug(f"   [{idx}] {title[:60]}")
                    except (ValueError, AttributeError):
                        pass

        response_content = ""
        if data_item.get("result"):
            response = data_item["result"]
            if isinstance(response, dict):
                response_content = response.get("answer", "")
            else:
                response_content = str(response)

        if response_content:
            original_refs = re.findall(r"\[\[(\d+)\]\]", response_content)
            logging.debug(f"   Original refs in response: {sorted(set(int(r) for r in original_refs))}")

            updated_ref, ref2url_updated, added_ptr = _update_ref(response_content, ref2url, ptr)

            logging.debug(f"   Mapping (ptr={ptr}): {list(ref2url_updated.keys())}")
            ptr += added_ptr
            references_url.update(ref2url_updated)

            suggestions = _extract_verification_suggestions(updated_ref)
            if suggestions:
                node_content = data_item.get("question", name)

                if len(node_content) > 80:
                    node_content = node_content[:80] + "..."
                verification_suggestions.append(f"## {name}\n{suggestions}")
                logging.debug(f"   ✅ Extracted Verification Suggestions for {name}")
            else:
                logging.debug(f"   ⚠️ No Verification Suggestions found for {name}")

    logging.debug(f"📚 [RefGen] Final ref2url: {len(references_url)} entries")
    for idx, info in sorted(references_url.items()):
        title = info.get("title", "N/A") if isinstance(info, dict) else "N/A"
        logging.debug(f"   [[{idx}]] -> {title[:50]}")

    if verification_suggestions:
        return "## Searcher Verification Notes\n\n" + "\n\n".join(verification_suggestions), references_url
    else:
        return "", references_url


def _prepare_writing_input(
    graph_nodes: Dict[str, dict],
    cognitive_graph_dict: Optional[Dict] = None,
    pre_built_state: Optional[Dict] = None,
) -> Tuple[List[Dict], Dict[int, Dict], Dict[str, Dict[int, int]], EvidenceStore, Dict[int, dict]]:
    """Build writing inputs in one global citation namespace. Reuse pre_built_state for already allocated references."""
    tasks = []
    cited_indices = set()

    if isinstance(pre_built_state, dict):
        url_to_global: Dict[str, int] = dict(pre_built_state.get("url_to_global") or {})
        references_url: Dict[int, dict] = dict(pre_built_state.get("references_url") or {})
        ptr: int = int(pre_built_state.get("ptr") or 0)
    else:
        url_to_global: Dict[str, int] = {}
        references_url: Dict[int, dict] = {}
        ptr: int = 0

    node_ref_mappings: Dict[str, Dict[int, int]] = {}

    global_quality_by_idx: Dict[int, dict] = {}

    evidence_store = EvidenceStore()

    for node_name, node_data in graph_nodes.items():
        if not node_data.get("result"):
            continue

        response_data = node_data.get("result") or {}
        memory_data = node_data.get("result") or {}

        if isinstance(response_data, dict):
            answer = response_data.get("answer", "")
        else:
            answer = str(response_data)

        ref2url_node = {}
        if isinstance(memory_data, dict):
            raw_ref2url = memory_data.get("references", {})
            if raw_ref2url:
                try:
                    ref2url_node = {int(k): v for k, v in raw_ref2url.items()}
                except (ValueError, AttributeError):
                    pass

        local_to_global = {}
        new_allocations = 0

        def _register_page(local_idx: int, page_info) -> int:
            """Allocate or reuse a global page reference index."""
            nonlocal new_allocations

            url = (
                page_info.get("url", "")
                if isinstance(page_info, dict)
                else (page_info if isinstance(page_info, str) else "")
            )

            if url and url in url_to_global:
                gidx = url_to_global[url]
                logging.debug(f"[PrepWriter] URL dedup: local {local_idx} -> existing global {gidx} ({url[:60]})")
                return gidx

            gidx = ptr + new_allocations + 1
            new_allocations += 1
            if url:
                url_to_global[url] = gidx

            if isinstance(page_info, dict):
                references_url[gidx] = page_info
            elif isinstance(page_info, str):
                references_url[gidx] = {"url": page_info, "title": "Unknown"}
            return gidx

        numbers = sorted({int(n) for n in re.findall(r"\[\[(\d+)\]\]", answer)})
        for local_idx in numbers:
            page_info = ref2url_node.get(local_idx)
            if page_info is not None:
                local_to_global[local_idx] = _register_page(local_idx, page_info)
            else:
                gidx = ptr + new_allocations + 1
                new_allocations += 1
                local_to_global[local_idx] = gidx
                logging.warning(
                    f"[PrepWriter] Cited local_idx={local_idx} missing from ref2url in node {node_name}"
                )

        for local_idx in sorted(ref2url_node.keys()):
            if local_idx not in local_to_global:
                local_to_global[local_idx] = _register_page(local_idx, ref2url_node[local_idx])

        updated_answer = re.sub(
            r"\[\[(\d+)\]\]",
            lambda m: f"[[{local_to_global.get(int(m.group(1)), int(m.group(1)))}]]",
            answer,
        )

        total_mapped = new_allocations

        node_ref_mappings[node_name] = local_to_global
        cited_count = len(numbers)
        uncited_count = len(local_to_global) - cited_count
        dedup_count = len(local_to_global) - new_allocations
        if uncited_count > 0 or dedup_count > 0:
            logging.info(
                f"[PrepWriter] Node {node_name}: {cited_count} cited + {uncited_count} uncited pages, {dedup_count} deduped, {new_allocations} new global indices"
            )
        logging.debug(f"[PrepWriter] Node {node_name} ref mapping: {local_to_global}")

        if isinstance(memory_data, dict):
            raw_evidence = memory_data.get("evidence_records", [])
            for record in raw_evidence:
                local_idx = record.get("ref_idx")
                if local_idx is not None and local_idx in local_to_global:
                    global_idx = local_to_global[local_idx]
                    evidence_store.add_record(
                        EvidenceRecord(
                            ref_idx=global_idx,
                            url=str(record.get("url", "")),
                            title=str(record.get("title", "")),
                            evidence_quote=str(record.get("evidence_quote", "")),
                            finding_answer=str(record.get("finding_answer", "")),
                            criterion=str(record.get("criterion", "")),
                            page_idx=local_idx,
                            search_node=node_name,
                        )
                    )
                elif local_idx is not None:
                    record_url = str(record.get("url", ""))
                    if record_url and record_url in url_to_global:
                        global_idx = url_to_global[record_url]
                    else:
                        global_idx = ptr + new_allocations + 1
                        new_allocations += 1
                        total_mapped = new_allocations
                        if record_url:
                            url_to_global[record_url] = global_idx
                            references_url[global_idx] = {
                                "url": record_url,
                                "title": str(record.get("title", "Unknown")),
                            }
                    local_to_global[local_idx] = global_idx
                    evidence_store.add_record(
                        EvidenceRecord(
                            ref_idx=global_idx,
                            url=record_url,
                            title=str(record.get("title", "")),
                            evidence_quote=str(record.get("evidence_quote", "")),
                            finding_answer=str(record.get("finding_answer", "")),
                            criterion=str(record.get("criterion", "")),
                            page_idx=local_idx,
                            search_node=node_name,
                        )
                    )
                    logging.info(
                        f"[PrepWriter] Rescued orphan evidence: local_idx={local_idx} -> global {global_idx} in node {node_name}"
                    )

        ptr += total_mapped

        cited_in_answer = re.findall(r"\[\[(\d+)\]\]", updated_answer)
        for idx_str in cited_in_answer:
            cited_indices.add(int(idx_str))

        feedback = {}
        if isinstance(memory_data, dict):
            feedback = memory_data.get("feedback", {})

        quality_results = []
        if isinstance(memory_data, dict):
            quality_results = memory_data.get("quality_results", [])

        for q in quality_results:
            local_idx = q.get("index")
            if local_idx is not None and local_idx in local_to_global:
                global_idx = local_to_global[local_idx]
                global_quality_by_idx[global_idx] = q
                logging.debug(f"[PrepWriter] Quality score: local {local_idx} -> global {global_idx}")

        tasks.append(
            {
                "task_name": node_name,
                "task_content": node_data.get("question", ""),
                "answer": updated_answer,
                "feedback": feedback,
            }
        )

    cited_references = {}

    for idx in cited_indices:
        if idx in references_url:
            info = references_url[idx]

            quality = global_quality_by_idx.get(idx, {})
            cited_references[idx] = {
                "url": info.get("url", "") if isinstance(info, dict) else "",
                "title": info.get("title", "Unknown") if isinstance(info, dict) else str(info),
                "quality": {
                    "overall": quality.get("overall", 0),
                    "currency": quality.get("currency", 0),
                    "relevance": quality.get("relevance", 0),
                    "authority": quality.get("authority", 0),
                    "accuracy": quality.get("accuracy", 0),
                    "purpose": quality.get("purpose", 0),
                    "cr": quality.get("cr", 0),
                    "aap": quality.get("aap", 0),
                },
            }

        else:
            logging.warning(f"🔴 [PrepWriter] Citation [[{idx}]] not found in references_url")

    evidence_nodes = set()
    for records in evidence_store.records.values():
        for r in records:
            evidence_nodes.add(r.search_node)
    logging.info(
        f"📦 [PrepWriter] {len(tasks)} tasks, {len(cited_references)} cited refs, {len(node_ref_mappings)} node mappings, {len(evidence_store)} evidence records"
    )
    logging.debug(f"📦 [PrepWriter] Evidence search_nodes: {sorted(evidence_nodes)}")

    return tasks, cited_references, node_ref_mappings, evidence_store, references_url


def _cleanup_planner_memory(messages: List[Message], keep_recent: int = 1):
    """Remove redundant cognitive-graph snapshots from Planner history."""
    COGNITIVE_MARKER = "## Updated Cognitive Graph Status"

    obs_with_cognitive = [i for i, msg in enumerate(messages) if msg.role == "user" and COGNITIVE_MARKER in msg.content]
    if len(obs_with_cognitive) > keep_recent:
        for idx in obs_with_cognitive[:-keep_recent]:
            content = messages[idx].content
            pos = content.find(COGNITIVE_MARKER)
            if pos >= 0:
                messages[idx] = Message(role="user", content=content[:pos].rstrip())

    if obs_with_cognitive and messages[0].role == "system":
        sys_content = messages[0].content

        cg_start = sys_content.find("## 当前认知图状态")
        if cg_start >= 0:
            cg_end = -1
            for end_marker in ["The graph shows:", "认知图展示了："]:
                pos = sys_content.find(end_marker, cg_start)
                if pos >= 0:
                    cg_end = pos
                    break
            if cg_end >= 0:
                replacement = "(See '## Updated Cognitive Graph Status' in the latest observation)\n\n"
                messages[0] = Message(
                    role="system", content=sys_content[:cg_start] + replacement + sys_content[cg_end:]
                )
                logging.info("🧹 [MemoryCleanup] Replaced initial cognitive graph in system prompt with pointer")

    cleaned_count = 0
    if len(obs_with_cognitive) > keep_recent:
        cleaned_count += len(obs_with_cognitive) - keep_recent
    if cleaned_count > 0:
        logging.info(
            f"🧹 [MemoryCleanup] Cleaned {cleaned_count} old cognitive graph snapshot(s), keeping {keep_recent} recent"
        )


def _remove_code_block(text: str) -> str:
    """Remove code blocks from text to save context window and reduce noise."""
    patterns = [
        r"<\|action_start\|><\|interpreter\|>\s*```python\s*\n(.*?)```\s*<\|action_end\|>",
        r"<\|interpreter\|>\s*```python\s*\n(.*?)```",
        r"```python\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
    ]

    cleaned_text = text
    for pattern in patterns:
        cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.DOTALL)

    return cleaned_text.strip()


class VeritraceAgent:
    """Coordinate cognitive planning, evidence collection, and report writing."""

    def __init__(
        self,
        llm_provider: str = None,
        llm_model: str = "Qwen/Qwen3-Next-80B-A3B-Thinking",
        searcher_model: Optional[str] = None,
        reader_model: Optional[str] = None,
        prefilter_model: Optional[str] = None,
        quality_model: Optional[str] = None,
        cognitive_model: Optional[str] = None,
        summarizer_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        searcher_cfg: Optional[dict] = None,
        summary_prompt: Optional[str] = None,
        max_turn: int = 10,
        language: str = "en",
        writer_language: Optional[str] = None,
        enable_cognitive_graph: bool = True,
        include_search_refs: bool = True,
        writer_mode: Optional[str] = None,
        *,
        writer_model: Optional[str] = None,
    ):
        """Initialize VERITRACE Agent."""

        if writer_mode and writer_mode != "research":
            raise ValueError("Only the research writing mode is supported")
        llm_provider = llm_provider or os.environ.get("LLM_PROVIDER", "siliconflow")
        self.llm_provider = llm_provider

        def _provider_for(role_env: str) -> str:
            return os.environ.get(role_env, llm_provider)

        if writer_model is None:
            writer_model = summarizer_model
            writer_provider = _provider_for("SUMMARIZER_PROVIDER")
        else:
            writer_provider = os.getenv("SUMMARIZER_PROVIDER") or _provider_for("WRITER_PROVIDER")

        self.llm = create_adapter(llm_provider, llm_model, api_key=llm_api_key)
        self.searcher_llm = (
            create_adapter(_provider_for("SEARCHER_PROVIDER"), searcher_model) if searcher_model else None
        )
        self.reader_llm = create_adapter(_provider_for("READER_PROVIDER"), reader_model) if reader_model else None
        self.prefilter_llm = (
            create_adapter(_provider_for("PREFILTER_PROVIDER"), prefilter_model) if prefilter_model else None
        )
        self.cognitive_llm = (
            create_adapter(_provider_for("COGNITIVE_PROVIDER"), cognitive_model) if cognitive_model else None
        )
        self.writer_llm = (
            create_adapter(writer_provider, writer_model, timeout_override=300.0)
            if writer_model
            else None
        )

        self.searcher_cfg = searcher_cfg or {}
        self.searcher_cfg["language"] = language
        logging.debug(f"🔧 [VeritraceAgent] searcher_cfg: {self.searcher_cfg}")
        self.max_turn = max_turn
        self.language = language

        self.writer_language = writer_language or language
        self.enable_cognitive_graph = enable_cognitive_graph
        self.include_search_refs = include_search_refs

        self._cancelled = threading.Event()
        self._run_lock = threading.Lock()
        self.planner_isolation = os.getenv("PLANNER_ISOLATION", "false").lower() in ("true", "1", "yes")
        self.interpreter = PlannerProgram(self._cancelled, isolated=self.planner_isolation)
        logging.info("[Planner] Execution mode=%s", "isolated" if self.planner_isolation else "compatible")
        self._scheduler = None

        if enable_cognitive_graph:
            cognitive_llm = self.cognitive_llm if self.cognitive_llm else self.llm
            self.cognitive_manager = CognitiveGraphManager(cognitive_llm, language=language)
        else:
            self.cognitive_manager = None

    def _finish_condition(self, graph: TaskScheduler) -> bool:
        """Check if planning is complete by verifying finish() was actually executed."""
        return graph.finish_requested

    def _enforce_soft_deadline(self) -> bool:
        """Remove all UNKNOWN nodes from cognitive graph so finish() can proceed.
        Returns True if any nodes were removed."""
        if not self.cognitive_manager or not self.cognitive_manager.graph:
            return False

        from .cognitive_graph import CognitiveState

        graph = self.cognitive_manager.graph
        unknown_nodes = [n for n in graph.nodes.values() if n.cognitive_state == CognitiveState.UNKNOWN]

        if not unknown_nodes:
            return False

        logging.warning(f"⏰ [SoftDeadline] Removing {len(unknown_nodes)} UNKNOWN node(s) to allow finish()")

        for node in unknown_nodes:
            logging.info(f"⏰ [SoftDeadline] Removing UNKNOWN node: {node.id} ({node.name})")
            graph.remove_node(node.id)

        return True

    def _log_separator(self, title: str):
        """Print a visual separator for logs."""
        logging.debug("=" * 60)
        logging.debug(f"  {title}")
        logging.debug("=" * 60)

    def _log_task_state(self, graph: TaskScheduler, label: str = ""):
        statuses = {name: task["status"] for name, task in graph.tasks.items()}
        logging.debug("Tasks %s: %s; pending=%d", label, statuses, graph.pending_count)

    def _log_message(self, role: str, content: str, max_len: int = 500):
        """Log a message with truncation."""
        truncated = content[:max_len] + "..." if len(content) > max_len else content
        logging.debug(f"💬 {role.upper()}: {truncated}")

    def _allocate_globals_for_task(
        self,
        task_name: str,
        response_content: str,
        ref2url_local: Dict[int, dict],
    ) -> Tuple[str, Dict[int, int]]:
        """Atomically allocate URL-deduplicated references and rewrite task citations before cognitive updates."""
        if not isinstance(ref2url_local, dict):
            ref2url_local = {}
        local_to_global: Dict[int, int] = {}
        with self._global_ref_lock:
            for local_idx, page_info in ref2url_local.items():
                try:
                    local_idx_int = int(local_idx)
                except (ValueError, TypeError):
                    continue
                url = (
                    page_info.get("url", "")
                    if isinstance(page_info, dict)
                    else page_info
                    if isinstance(page_info, str)
                    else ""
                )

                if url and url in self._global_url_to_idx:
                    global_idx = self._global_url_to_idx[url]
                else:
                    self._global_ptr += 1
                    global_idx = self._global_ptr
                    if url:
                        self._global_url_to_idx[url] = global_idx

                    if isinstance(page_info, dict):
                        self._global_references_url[global_idx] = page_info
                    elif isinstance(page_info, str):
                        self._global_references_url[global_idx] = {"url": page_info, "title": "Unknown"}
                    else:
                        self._global_references_url[global_idx] = {"url": "", "title": "Unknown"}
                local_to_global[local_idx_int] = global_idx

        if isinstance(response_content, str) and "[[" in response_content:
            cited_locals = {int(m) for m in re.findall(r"\[\[(\d+)\]\]", response_content)}
            missing = sorted(cited_locals - set(local_to_global.keys()))
            if missing:
                with self._global_ref_lock:
                    for local_idx in missing:
                        self._global_ptr += 1
                        global_idx = self._global_ptr
                        local_to_global[local_idx] = global_idx
                        self._global_references_url[global_idx] = {"url": "", "title": "Unknown"}
                logging.warning(
                    f"[GlobalAlloc] task {task_name}: cited locals {missing} "
                    f"missing from ref2url; allocated placeholder globals"
                )

        if isinstance(response_content, str) and "[[" in response_content and local_to_global:

            def _replace(match):
                lidx = int(match.group(1))
                gidx = local_to_global.get(lidx)
                return f"[[{gidx}]]" if gidx is not None else match.group(0)

            response_content_global = re.sub(r"\[\[(\d+)\]\]", _replace, response_content)
        else:
            response_content_global = response_content

        logging.info(
            f"[GlobalAlloc] task {task_name}: mapped {len(local_to_global)} "
            f"local refs, global_ptr now {self._global_ptr}"
        )
        return response_content_global, local_to_global

    def _update_cognitive_graph_from_searches(self, graph: TaskScheduler, processed_nodes: set) -> set:
        """Update newly completed tasks, serializing tasks that share a cognitive target."""
        if not self.cognitive_manager or not self.cognitive_manager.graph:
            return processed_nodes

        new_completed = set()
        for node_name, node_data in graph.tasks.items():
            if node_data.get("result") and node_name not in processed_nodes:
                new_completed.add(node_name)

        if not new_completed:
            logging.info("🧠 [CognitiveGraph] No new completed nodes to process")
            return processed_nodes

        report_progress("updating_graph", completed_tasks=len(new_completed))
        logging.info(f"🧠 [CognitiveGraph] Updating from {len(new_completed)} new nodes: {new_completed}")

        target_groups: Dict[Optional[str], List[str]] = defaultdict(list)
        for node_name in new_completed:
            target_id = graph.tasks[node_name].get("cognitive_target_id")
            target_groups[target_id].append(node_name)

        logging.info(
            f"🧠 [CognitiveGraph] Parallel groups: {len(target_groups)} (targets: {list(target_groups.keys())})"
        )

        def _process_single_node(node_name: str):
            node_data = graph.tasks[node_name]

            response = node_data.get("result") or {}
            if isinstance(response, dict):
                response_content = response.get("answer", "")
            else:
                response_content = str(response)

            inquiry_goal = node_data.get("question", node_name)
            cognitive_edge_id = node_data.get("cognitive_edge_id")
            cognitive_target_id = node_data.get("cognitive_target_id")

            memory_data = node_data.get("result") or {}
            searcher_feedback = memory_data.get("feedback") if isinstance(memory_data, dict) else None
            quality_results = memory_data.get("quality_results", []) if isinstance(memory_data, dict) else []
            ref2url_local: Dict[int, dict] = {}
            if isinstance(memory_data, dict):
                raw_ref2url = memory_data.get("references", {})
                if isinstance(raw_ref2url, dict):
                    try:
                        ref2url_local = {int(k): v for k, v in raw_ref2url.items()}
                    except (ValueError, TypeError):
                        ref2url_local = {}

            response_content, local_to_global = self._allocate_globals_for_task(
                task_name=node_name,
                response_content=response_content,
                ref2url_local=ref2url_local,
            )

            if quality_results and local_to_global:
                remapped_quality = []
                for q in quality_results:
                    if isinstance(q, dict):
                        q_copy = dict(q)
                        lidx = q_copy.get("index")
                        if isinstance(lidx, int) and lidx in local_to_global:
                            q_copy["index"] = local_to_global[lidx]
                        remapped_quality.append(q_copy)
                    else:
                        remapped_quality.append(q)
                quality_results = remapped_quality

            if searcher_feedback:
                logging.info(f"   📤 [Feedback] 节点 {node_name} 的 Searcher 反馈:")
                logging.info(f"      can_improve: {searcher_feedback.get('can_improve')}")
                logging.info(f"      reason: {searcher_feedback.get('reason', '')[:100]}")
            else:
                logging.info(f"   📤 [Feedback] 节点 {node_name} 无 Searcher 反馈")

            core_criteria = node_data.get("core_criteria", [])
            supplementary_criteria = node_data.get("supplementary_criteria", [])
            if cognitive_edge_id and self.cognitive_manager.graph:
                edge = self.cognitive_manager.graph.get_edge(cognitive_edge_id)
                if edge and (core_criteria or supplementary_criteria):
                    edge.core_criteria = core_criteria
                    edge.supplementary_criteria = supplementary_criteria
                    logging.info(
                        f"   📋 Updated edge {cognitive_edge_id} criteria: core={core_criteria}, supp={supplementary_criteria}"
                    )

            res = None
            try:
                res = self.cognitive_manager.update_graph(
                    node_name=node_name,
                    inquiry_goal=inquiry_goal,
                    search_result=response_content,
                    edge_id=cognitive_edge_id,
                    target_id=cognitive_target_id,
                    feedback=searcher_feedback,
                    quality_data=quality_results,
                )
            except Exception as e:
                logging.error(f"   ❌ CGM update threw for {node_name}: {e}")
                res = {"error": str(e)}
            finally:
                processed_nodes.add(node_name)
            if isinstance(res, dict) and res.get("error"):
                logging.error(f"   ❌ CGM update FAILED for {node_name}: {res['error']}")
                if not hasattr(self, "_failed_cgm_nodes"):
                    self._failed_cgm_nodes = set()
                self._failed_cgm_nodes.add(node_name)
            else:
                logging.info(f"   ✅ Updated cognitive graph for node: {node_name}")

        def _process_group(target_id: Optional[str], node_names: List[str]):
            """Process a group of nodes sharing the same target — sequential within group."""
            for node_name in node_names:
                _process_single_node(node_name)

        if len(target_groups) == 1:
            for target_id, node_names in target_groups.items():
                _process_group(target_id, node_names)
        else:
            max_workers = min(len(target_groups), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for target_id, node_names in target_groups.items():
                    future = executor.submit(_process_group, target_id, node_names)
                    futures[future] = target_id
                for future in as_completed(futures):
                    target_id = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        logging.error(f"🧠 [CognitiveGraph] Group {target_id} failed: {e}")

        if self.cognitive_manager.graph:
            unsolved = self.cognitive_manager.graph.get_unsolved_edges()
            solved = self.cognitive_manager.graph.get_solved_edges()
            logging.info(f"🧠 [CognitiveGraph] Edges: {len(solved)} solved, {len(unsolved)} unsolved")

        failed = getattr(self, "_failed_cgm_nodes", None)
        if failed:
            logging.error(
                f"🧠 [CognitiveGraph] ⚠️ {len(failed)} node(s) FAILED CGM update (search results lost): {sorted(failed)}"
            )

        return processed_nodes

    def _research(
        self,
        question: str,
        session_id: int = 0,
    ) -> Iterator[ResearchEvent]:
        """Process a question and yield responses."""
        self._log_separator(f"NEW SESSION (id={session_id})")
        logging.debug(f"❓ QUESTION: {question}")

        _research_state = dict(tasks={}, ref2url={}, cognitive_graph={})
        restored = getattr(self, "_recovery_state", None) or {}
        if restored.get("writer_language"):
            self.writer_language = restored["writer_language"]

        cognitive_graph_context = None
        if restored.get("cognitive_graph") and self.cognitive_manager:
            from .cognitive_graph import CognitiveGraph

            self.cognitive_manager.graph = CognitiveGraph.from_dict(restored["cognitive_graph"])
            cognitive_graph_context = self.cognitive_manager.get_planning_context()
            _research_state["cognitive_graph"] = self.cognitive_manager.get_graph_dict()
        elif self.cognitive_manager:
            report_progress("initializing")
            logging.info("🧠 [CognitiveGraph] Parsing question structure...")
            try:
                self.cognitive_manager.initial_parse(question)
                cognitive_graph_context = self.cognitive_manager.get_planning_context()
                _research_state["cognitive_graph"] = self.cognitive_manager.get_graph_dict()
                logging.info("✅ [CognitiveGraph] Parsed successfully!")
                logging.debug(
                    f"   Nodes: {list(self.cognitive_manager.graph.nodes.keys()) if self.cognitive_manager.graph else 'None'}"
                )
            except Exception as e:
                import traceback

                logging.error(f"❌ [CognitiveGraph] Parse failed: {e}")
                logging.error(f"   Traceback: {traceback.format_exc()}")
                cognitive_graph_context = None

        yield ResearchEvent("research.started", {"question": question, "resumed": bool(restored), **deepcopy(_research_state)})

        graph = TaskScheduler(
            llm=self.llm,
            searcher_llm=self.searcher_llm,
            reader_llm=self.reader_llm,
            prefilter_llm=self.prefilter_llm,
            searcher_cfg=self.searcher_cfg,
            cognitive_manager=self.cognitive_manager if self.enable_cognitive_graph else None,
            session_id=session_id,
        )
        logging.info("✅ TaskScheduler created")

        import time as _time

        soft_deadline_start = _time.monotonic()
        graph.start_time = soft_deadline_start

        self._scheduler = graph
        graph.set_question(question)
        if restored:
            graph.restore_tasks(restored.get("tasks", {}), retry_unfinished=self._recovery_action == "continue")
            allocation = restored.get("reference_allocation", {})
            self._global_url_to_idx = {url: int(index) for url, index in allocation.get("url_to_global", {}).items()}
            self._global_references_url = {int(index): value for index, value in allocation.get("references_url", {}).items()}
            self._global_ptr = int(allocation.get("ptr", 0))

        self.interpreter.reset()
        if restored:
            self.interpreter.restore(restored.get("program_variables", {}))
        self.interpreter.inject("add_task", graph.add_task)
        self.interpreter.inject("finish", graph.finish)
        self.interpreter.inject("propose_restructure", graph.propose_restructure)
        planner_graph = (TaskQuery(graph.task, question, lambda: graph.finish_requested)
                         if self.planner_isolation else PlannerGraph(graph, emit=self.interpreter._print))
        self.interpreter.inject("graph", planner_graph)
        self.interpreter.inject("question", question)
        logging.info("✅ TaskScheduler functions injected into interpreter")

        system_prompt = build_veritrace_planner_prompt(
            language=self.language, cognitive_graph_context=cognitive_graph_context
        )

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=question),
        ]
        if restored.get("messages"):
            messages = [Message(**item) for item in restored["messages"]]
            if messages and messages[0].role == "system":
                messages[0].content = system_prompt
        self._planner_messages = messages
        logging.info("✅ Initial messages: system prompt + user question")
        if cognitive_graph_context:
            logging.info("✅ Cognitive graph context included in system prompt")

        sent_nodes = set()

        cognitive_processed_nodes = set(restored.get("processed_nodes", []))
        self._cognitive_processed_nodes = cognitive_processed_nodes

        if restored:
            cognitive_processed_nodes = self._update_cognitive_graph_from_searches(graph, cognitive_processed_nodes)
            self._cognitive_processed_nodes = cognitive_processed_nodes
            _research_state.update(tasks=graph.tasks,
                                   cognitive_graph=self.cognitive_manager.get_graph_dict() if self.cognitive_manager else {})
            yield ResearchEvent("research.restored", deepcopy(_research_state))
            if self._recovery_action == "write_report":
                if restored.get("writing"):
                    self._capture_checkpoint(graph)
                    yield self._finish_saved_writing(restored)
                else:
                    self._capture_checkpoint(graph)
                    yield self._finish_report(graph, _research_state)
                return
            completed = [name for name, task in graph.tasks.items() if task.get("status") == "completed"]
            context = self.cognitive_manager.get_planning_context() if self.cognitive_manager else ""
            resume_note = (
                f"研究已从保存的检查点恢复。已完成的搜索任务无需重复：{completed}。"
                "请检查保留的发现和未解决的问题，继续完成原始研究。\n" if self.language == "cn" else
                f"Research resumed from saved work. Do not repeat completed search tasks: {completed}. "
                "Review the retained findings and unresolved inquiries, then continue the original research.\n"
            )
            messages.append(Message(role="user", content=resume_note + context))

        search_turns = 0
        total_iterations = 0
        MAX_TOTAL_ITERATIONS = self.max_turn * 3
        _soft_deadline_triggered = False

        while search_turns < self.max_turn and total_iterations < MAX_TOTAL_ITERATIONS:
            self._capture_checkpoint(graph)
            if self._cancelled.is_set():
                yield ResearchEvent("research.cancelled")
                return
            total_iterations += 1

            from .config import SOFT_DEADLINE_MINUTES

            if SOFT_DEADLINE_MINUTES > 0:
                elapsed_min = (_time.monotonic() - soft_deadline_start) / 60
                if elapsed_min >= SOFT_DEADLINE_MINUTES:
                    logging.warning(
                        f"⏰ [SoftDeadline] Reached {elapsed_min:.1f}min >= {SOFT_DEADLINE_MINUTES}min. "
                        f"Stopping exploration, writing report with collected evidence."
                    )
                    self._enforce_soft_deadline()

                    if graph.pending_count > 0:
                        logging.info(f"⏰ [SoftDeadline] Waiting for {graph.pending_count} in-flight tasks...")
                    for graph_update in self._wait_for_graph(graph):
                        yield graph_update

                    graph.finish()
                    _soft_deadline_triggered = True
                    break

            self._log_separator(f"SEARCH TURN {search_turns + 1}/{self.max_turn} (iteration {total_iterations})")

            logging.debug(f"🧠 MEMORY (messages count): {len(messages)}")
            for i, msg in enumerate(messages[-3:]):
                self._log_message(f"  [{i}] {msg.role}", msg.content, max_len=200)

            logging.info("🤖 Calling LLM...")
            report_progress("planning", turn=search_turns + 1)
            response = self.llm.chat(messages)
            if self._cancelled.is_set():
                yield ResearchEvent("research.cancelled")
                return
            self._log_message("LLM RESPONSE", response.content, max_len=800)

            agent_msg = ResearchEvent("planner.thinking", {"content": response.content})

            code = CodeExtractor.extract(response.content)
            logging.info(f"🔍 CODE EXTRACTED: {bool(code)}")
            if code:
                logging.debug(f"📝 CODE:\n{code}")
                agent_msg = ResearchEvent("planner.executing", {"content": response.content, "code": code})
                yield agent_msg

                logging.info("⚙️ EXECUTING CODE...")
                exec_result = self.interpreter.execute(code)
                logging.info(f"⚙️ EXECUTION RESULT: success={exec_result.success}")
                if exec_result.stdout:
                    logging.debug(f"⚙️ STDOUT: {exec_result.stdout[:300]}...")
                if exec_result.stderr:
                    logging.debug(f"⚙️ STDERR: {exec_result.stderr[:300]}...")
                if exec_result.error:
                    logging.error("Planner program execution failed: %s\n%s", exec_result.error, exec_result.traceback or "")

                if not exec_result.success:
                    logging.warning("❌ CODE EXECUTION FAILED - sending error to LLM")
                    error_msg = exec_result.to_error_feedback()
                    messages.append(Message(role="assistant", content=response.content))
                    messages.append(Message(role="user", content=error_msg))

                    yield ResearchEvent("planner.observation", {"content": error_msg, "error": exec_result.error})
                    continue

                self._log_task_state(graph, "AFTER CODE EXECUTION")

                _research_state["tasks"] = graph.tasks
                yield ResearchEvent("tasks.updated", {"tasks": graph.tasks})

                had_search_tasks = graph.pending_count > 0
                logging.info(f"⏳ WAITING FOR SEARCH TASKS TO COMPLETE... (active: {graph.pending_count})")
                for graph_update in self._wait_for_graph(graph):
                    if graph_update.type in ("task.completed", "task.failed"):
                        had_search_tasks = True
                    _research_state["tasks"] = graph.tasks
                    yield graph_update
                if self._cancelled.is_set():
                    yield ResearchEvent("research.cancelled")
                    return
                logging.info("✅ ALL SEARCH TASKS COMPLETED")
                self._log_task_state(graph, "AFTER SEARCHES")

                if had_search_tasks:
                    search_turns += 1
                    logging.info(f"📊 Search turn {search_turns}/{self.max_turn} consumed")

                if self.cognitive_manager and self.cognitive_manager.graph:
                    cognitive_processed_nodes = self._update_cognitive_graph_from_searches(
                        graph, cognitive_processed_nodes
                    )
                    self._cognitive_processed_nodes = cognitive_processed_nodes
                    _research_state["cognitive_graph"] = self.cognitive_manager.get_graph_dict()

                    yield ResearchEvent(
                        "cognitive.updated",
                        {
                            "content": "Cognitive graph updated",
                            **dict(
                                tasks=graph.tasks,
                                cognitive_graph=deepcopy(_research_state["cognitive_graph"]),
                            ),
                        },
                    )
                    logging.info("🧠 [CognitiveGraph] Sent updated cognitive graph to frontend")

                if self.cognitive_manager and graph.has_pending_restructure():
                    proposal = graph.consume_restructure_proposal()
                    logging.info("🔄 [Restructure] Planner proposed restructuring, routing to CognitiveGraphManager")
                    restructure_result = self.cognitive_manager.restructure_graph(proposal)
                    logging.debug(f"🔄 [Restructure] Result: {restructure_result}")

                    _research_state["cognitive_graph"] = self.cognitive_manager.get_graph_dict()

                    yield ResearchEvent(
                        "cognitive.updated",
                        {
                            "content": "Cognitive graph restructured",
                            **dict(
                                tasks=graph.tasks,
                                cognitive_graph=deepcopy(_research_state["cognitive_graph"]),
                            ),
                        },
                    )

                    messages.append(Message(role="assistant", content=_remove_code_block(response.content)))
                    restructure_observation = []

                    if restructure_result.get("error") == "restructure_rollback":
                        restructure_observation.append(
                            "## Restructuring Rolled Back\n"
                            f"Evaluation: {restructure_result.get('evaluation', '')}\n"
                            f"Guidance: {restructure_result.get('guidance', '')}\n"
                            f"Orphans remaining: {restructure_result.get('orphan_count', 0)}"
                        )
                        logging.error(f"🔄 [Restructure] Rolled back: {restructure_result.get('evaluation')}")
                    elif restructure_result.get("error"):
                        restructure_observation.append(f"## Restructuring Error\nError: {restructure_result['error']}")
                        logging.error(f"🔄 [Restructure] Error: {restructure_result['error']}")
                    elif restructure_result.get("should_restructure"):
                        restructure_observation.append(
                            "## Cognitive Graph Restructured\n"
                            f"Evaluation: {restructure_result.get('evaluation', '')}\n"
                            f"Added nodes: {restructure_result.get('added_nodes', [])}\n"
                            f"Removed nodes: {restructure_result.get('removed_nodes', [])}\n"
                            f"Added edges: {restructure_result.get('added_edges', [])}\n"
                            f"Removed edges: {restructure_result.get('removed_edges', [])}\n"
                            f"Modified nodes: {restructure_result.get('modified_nodes', [])}\n"
                            f"Modified edges: {restructure_result.get('modified_edges', [])}"
                        )

                        if restructure_result.get("planner_feedback"):
                            restructure_observation.append(
                                "## ⚠️ Restructuring Feedback\n"
                                "Some requested changes were rejected. The restructuring system advises:\n\n"
                                f"{restructure_result['planner_feedback']}"
                            )
                        elif restructure_result.get("violations"):
                            restructure_observation.append(f"Violations (skipped): {restructure_result['violations']}")
                    else:
                        restructure_observation.append(
                            "## Restructuring Rejected\n"
                            f"Evaluation: {restructure_result.get('evaluation', 'No restructuring needed')}"
                        )

                    cognitive_context = self.cognitive_manager.get_planning_context()
                    if cognitive_context:
                        restructure_observation.append("\n## Updated Cognitive Graph Status\n" + cognitive_context)

                    restructure_observation.append(
                        "⚠️ **REQUIRED**: Output [Thought] analysis before any code. "
                        "List all remaining UNKNOWN nodes, `to_solve` edges, and `investigating` edges. "
                        "The system will BLOCK `finish()` if UNKNOWN nodes exist."
                    )

                    _cleanup_planner_memory(messages)

                    messages.append(Message(role="user", content="\n\n".join(restructure_observation)))

                    logging.info("🔄 [Restructure] Sent restructured graph to Planner, continuing to next iteration")
                    yield ResearchEvent(
                        "research.updated",
                        {"content": "Restructuring complete, continuing planning", **deepcopy(_research_state)},
                    )
                    continue

                finish_detected = self._finish_condition(graph)
                logging.info(f"🏁 FINISH CONDITION CHECK: {finish_detected}")

                if finish_detected and self.cognitive_manager and self.cognitive_manager.graph:
                    unknown_nodes = [
                        n for n in self.cognitive_manager.graph.nodes.values() if n.cognitive_state == "UNKNOWN"
                    ]
                    if unknown_nodes:
                        node_descriptions = []
                        for n in unknown_nodes:
                            incoming = self.cognitive_manager.graph.get_edges_by_target(n.id)
                            edge_info = ", ".join(e.id for e in incoming) if incoming else "no edges"
                            node_descriptions.append(f"  - {n.id}: {n.name} (edges: {edge_info})")
                        node_list = "\n".join(node_descriptions)
                        reject_msg = (
                            f"⛔ **REJECTED**: `finish()` blocked by system. "
                            f"{len(unknown_nodes)} UNKNOWN node(s) have NO findings:\n{node_list}\n\n"
                            f"You MUST either:\n"
                            f"1. Create Tasks to explore these nodes, OR\n"
                            f"2. Call `propose_restructure()` with `remove_node` to delete nodes that are truly irrelevant to the user's question.\n\n"
                            f"Do NOT call `finish()` again until all nodes have findings or are removed."
                        )
                        logging.warning(
                            f"⛔ [FinishGuard] Rejected finish(): {len(unknown_nodes)} UNKNOWN nodes remain"
                        )

                        graph.resume()
                        messages.append(Message(role="assistant", content=_remove_code_block(response.content)))
                        messages.append(Message(role="user", content=reject_msg))
                        finish_detected = False

                if finish_detected:
                    logging.info("🎉 PLANNING COMPLETE - GENERATING FINAL REPORT")

                    yield self._finish_report(graph, _research_state)
                    return

                logging.info("🔄 CONTINUING TO NEXT PLANNING ITERATION")

                current_nodes = set(graph.tasks)
                new_nodes = current_nodes - sent_nodes

                if new_nodes:
                    new_nodes_data = {k: v for k, v in graph.tasks.items() if k in new_nodes}
                    reference, references_url = _generate_references_from_graph(new_nodes_data)
                    sent_nodes.update(new_nodes)
                    logging.debug(f"📤 NEW NODES to send: {new_nodes}")
                else:
                    reference = ""
                    references_url = {}
                    logging.info("📤 No new nodes to send")

                _research_state.update(
                    tasks=graph.tasks,
                    ref2url=references_url,
                )

                messages.append(Message(role="assistant", content=_remove_code_block(response.content)))

                observation_parts = []

                if exec_result.stdout and ("[ERROR]" in exec_result.stdout or "[WARNING]" in exec_result.stdout):
                    observation_parts.append("## ⚠️ Code Execution Warnings\n" + exec_result.stdout.strip())

                if reference:
                    observation_parts.append(reference)

                if self.cognitive_manager and self.cognitive_manager.graph:
                    cognitive_context = self.cognitive_manager.get_planning_context()
                    if cognitive_context:
                        observation_parts.append("\n## Updated Cognitive Graph Status\n" + cognitive_context)

                warn = (
                    "⚠️ Output [Thought] analysis before code. The system will BLOCK `finish()` if UNKNOWN nodes remain."
                )
                if self.cognitive_manager and self.cognitive_manager.graph:
                    to_solve_count = sum(
                        1 for e in self.cognitive_manager.graph.edges.values() if e.state == "to_solve"
                    )
                    if to_solve_count > 0:
                        warn += f"\n⚠️ {to_solve_count} `to_solve` edge(s) still need investigation — see 🔍 待解决的询问目标 above. Create Tasks for them or use `propose_restructure(remove_edge)` to remove irrelevant ones."
                observation_parts.append(warn)
                observation_content = "\n\n".join(observation_parts)

                _cleanup_planner_memory(messages)

                messages.append(Message(role="user", content=observation_content))

                logging.debug("📤 OBSERVATION SENT TO LLM:")
                logging.debug(f"   New nodes: {new_nodes if new_nodes else 'None'}")
                if self.cognitive_manager and self.cognitive_manager.graph:
                    logging.info("   Cognitive graph context included")

                yield ResearchEvent("research.updated", {"content": reference, **deepcopy(_research_state)})
            else:
                logging.info("ℹ️ NO CODE IN RESPONSE - just text output")
                messages.append(Message(role="assistant", content=response.content))
                yield agent_msg

        if search_turns >= self.max_turn:
            logging.warning(f"⚠️ MAX SEARCH TURNS ({self.max_turn}) REACHED - forcing summary generation")
        else:
            logging.warning(
                f"⚠️ MAX ITERATIONS ({MAX_TOTAL_ITERATIONS}) REACHED (search_turns={search_turns}) - forcing summary generation"
            )

        if not _soft_deadline_triggered:
            if graph.pending_count > 0:
                logging.info("⏳ Waiting for remaining search tasks to complete...")
            for graph_update in self._wait_for_graph(graph):
                _research_state["tasks"] = graph.tasks
                yield graph_update
            if graph.pending_count:
                pending = sorted(name for name, task in graph.tasks.items()
                                 if task["status"] in ("pending", "running"))
                logging.warning("Search wait ended with unfinished tasks; pending=%s; "
                                "generating report from collected evidence", pending)
            else:
                logging.info("All remaining tasks completed")

        logging.info("🎉 GENERATING REPORT FROM COLLECTED DATA")

        max_turns_notice = (
            "⚠️ Note: Maximum planning iterations reached. "
            "The following summary is based on the information collected so far.\n\n"
            if self.language == "en"
            else "⚠️ 注意：已达到最大规划迭代次数。以下总结基于目前收集到的信息。\n\n"
        )

        yield self._finish_report(graph, _research_state, prefix=max_turns_notice)

    def _finish_report(self, graph, _research_state, prefix=""):
        if self._cancelled.is_set():
            return ResearchEvent("research.cancelled")
        self._capture_checkpoint(graph)
        original_question = graph.question

        cognitive_graph_dict = None
        if self.cognitive_manager and self.cognitive_manager.graph:
            cognitive_graph_dict = self.cognitive_manager.get_graph_dict()

        with self._global_ref_lock:
            _alloc_seed = {
                "url_to_global": dict(self._global_url_to_idx),
                "references_url": dict(self._global_references_url),
                "ptr": self._global_ptr,
            }
        tasks, cited_references, node_ref_mappings, evidence_store, references_url = _prepare_writing_input(
            graph.tasks,
            cognitive_graph_dict,
            pre_built_state=_alloc_seed,
        )
        _research_state.update(
            tasks=graph.tasks,
            ref2url=references_url,
        )
        logging.info(
            f"📊 Prepared {len(tasks)} tasks, {len(cited_references)} cited refs, {len(evidence_store)} evidence records, {len(references_url)} URLs for report writing"
        )

        remapped_cognitive_graph = cognitive_graph_dict if cognitive_graph_dict else None

        self._save_partial_evidence_to_disk(
            original_question=original_question,
            remapped_cognitive_graph=remapped_cognitive_graph or {},
            evidence_store=evidence_store,
            cited_references=cited_references,
        )

        writer_llm = self.writer_llm if self.writer_llm else self.llm
        summary_content = self._run_section_writer_pipeline(
            writer_llm=writer_llm,
            remapped_cognitive_graph=remapped_cognitive_graph or {},
            evidence_store=evidence_store,
            original_question=original_question,
            cited_references=cited_references,
        )

        if self._cancelled.is_set():
            return ResearchEvent("research.cancelled")
        self._log_message("FINAL SUMMARY", summary_content, max_len=500)

        logging.info("=" * 60)
        logging.debug("  📊 FINAL GRAPH STATE FOR FRONTEND")
        logging.info("=" * 60)
        logging.debug(f"   Nodes: {list(_research_state['tasks'].keys())}")
        logging.info(f"   Ref2URL count: {len(_research_state.get('ref2url', {}))}")

        return ResearchResult(
            prefix + summary_content, references_url, graph.tasks, cognitive_graph_dict or {}
        ).to_event()

    def _save_partial_evidence_to_disk(
        self,
        original_question: str,
        remapped_cognitive_graph: Dict,
        evidence_store: EvidenceStore,
        cited_references: Optional[Dict],
    ) -> None:
        """Save partial research evidence after the search graph completes.

        Captures the real usage snapshot at this point so token spend on search/reader/
        prefilter is preserved even if downstream planners/writer crash.
        """
        if not getattr(self, "partial_evidence_path", None):
            return
        try:
            from veritrace.agent.usage_tracker import get_usage_summary

            partial_evidence = {
                "question": original_question,
                "cognitive_graph": remapped_cognitive_graph,
                "evidence_table": evidence_store.to_evidence_table(),
                "ref2url": {str(k): v for k, v in (cited_references or {}).items()},
                "outline": [],
                "overall_theme": "",
                "section_texts": {},
                "article": "",
                "usage_stats": get_usage_summary(),
            }
            os.makedirs(os.path.dirname(self.partial_evidence_path), exist_ok=True)
            with open(self.partial_evidence_path, "w", encoding="utf-8") as f:
                json.dump(partial_evidence, f, ensure_ascii=False, indent=2)
            logging.info(f"💾 Saved partial research evidence to {self.partial_evidence_path}")
        except Exception as e:
            logging.warning(f"Failed to save partial evidence: {e}")

    def _run_section_writer_pipeline(
        self,
        writer_llm,
        remapped_cognitive_graph,
        evidence_store,
        original_question,
        cited_references,
    ) -> str:
        from .writing import WritingPipeline

        pipeline = WritingPipeline(
            writer_llm,
            os.environ.get("WRITER_PROVIDER", self.llm_provider),
            self.writer_language,
            self.include_search_refs,
            adapter_timeout=300.0,
        )
        self._pipeline_data = pipeline.data
        if not hasattr(self, "_checkpoint_lock"):
            self._checkpoint_lock = threading.RLock()
            self._latest_checkpoint = {}
        pipeline.on_checkpoint = self._save_writing_checkpoint
        return pipeline.run(original_question, remapped_cognitive_graph, evidence_store, cited_references)

    def _wait_for_graph(self, graph: TaskScheduler) -> Iterator[ResearchEvent]:
        for event in graph.iter_completed():
            self._capture_checkpoint(graph)
            yield event

    def restore_checkpoint(self, checkpoint, action="continue"):
        if checkpoint.get("schema_version") != 1 or not checkpoint.get("question"):
            raise ValueError("Unsupported or incomplete research checkpoint")
        if action not in ("continue", "write_report"):
            raise ValueError("Unknown recovery action")
        self._resume_checkpoint = deepcopy(checkpoint)
        self._resume_action = action

    def latest_checkpoint(self):
        lock = getattr(self, "_checkpoint_lock", None)
        if lock is None:
            return {}
        with lock:
            return deepcopy(self._latest_checkpoint)

    def _capture_checkpoint(self, graph):
        with self._global_ref_lock:
            allocation = {"url_to_global": dict(self._global_url_to_idx),
                          "references_url": dict(self._global_references_url), "ptr": self._global_ptr}
        messages = getattr(self, "_planner_messages", [])
        checkpoint = {
            "schema_version": 1, "question": graph.question, "language": self.language,
            "writer_language": self.writer_language, "tasks": graph.tasks,
            "cognitive_graph": self.cognitive_manager.get_graph_dict() if self.cognitive_manager else {},
            "processed_nodes": sorted(getattr(self, "_cognitive_processed_nodes", set()) - getattr(self, "_failed_cgm_nodes", set())),
            "reference_allocation": allocation,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "program_variables": self.interpreter.snapshot(),
        }
        with self._checkpoint_lock:
            if self._latest_checkpoint.get("writing"):
                checkpoint["writing"] = self._latest_checkpoint["writing"]
            self._latest_checkpoint = deepcopy(checkpoint)
        publish_research_event("research.checkpoint", {"checkpoint": checkpoint})

    def _save_writing_checkpoint(self, data):
        with self._checkpoint_lock:
            self._latest_checkpoint = {**self._latest_checkpoint, "writing": deepcopy(data)}
            checkpoint = deepcopy(self._latest_checkpoint)
        publish_research_event("research.checkpoint", {"checkpoint": checkpoint})
        if data.get("section_texts"):
            publish_research_event("research.partial", {
                "section_texts": dict(data["section_texts"]),
                "partial_article": "\n\n".join(data["section_texts"].values()),
                "ref2url": data.get("cited_references", {}),
            })

    def _finish_saved_writing(self, restored):
        from .writing import WritingPipeline

        data = restored["writing"]
        store = EvidenceStore.from_evidence_table(data["evidence_store"])
        writer_llm = self.writer_llm if self.writer_llm else self.llm
        pipeline = WritingPipeline(writer_llm, os.environ.get("WRITER_PROVIDER", self.llm_provider),
                                   self.writer_language, self.include_search_refs, adapter_timeout=300.0)
        pipeline.on_checkpoint = self._save_writing_checkpoint
        self._pipeline_data = pipeline.data
        references = {int(index): value for index, value in data.get("cited_references", {}).items()}
        article = pipeline.run(restored["question"], data["cognitive_graph"], store, references, resume=data)
        return ResearchResult(article, references, restored.get("tasks", {}), data["cognitive_graph"]).to_event()

    def cancel(self):
        """Stop scheduling work and discard results arriving after cancellation."""
        self._cancelled.set()
        scheduler = getattr(self, "_scheduler", None)
        if scheduler is not None:
            scheduler.cancel()

    def __call__(
        self,
        question: str,
        session_id: int = 0,
    ) -> Iterator[ResearchEvent]:
        """Call the agent with resource cleanup guarantee."""
        return self._safe_forward(question, session_id)

    def forward(self, question: str, session_id: int = 0) -> Iterator[ResearchEvent]:
        return self._safe_forward(question, session_id)

    def _safe_forward(self, question: str, session_id: int = 0) -> Iterator[ResearchEvent]:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("This agent already has an active research session")
        self._cancelled.clear()
        self._scheduler = None
        self._pipeline_data = None
        self._checkpoint_lock = threading.RLock()
        self._latest_checkpoint = {}
        self._planner_messages = []
        self._cognitive_processed_nodes = set()
        self._recovery_state = getattr(self, "_resume_checkpoint", None)
        self._recovery_action = getattr(self, "_resume_action", "continue")
        self._resume_checkpoint = None
        if self._recovery_state and self._recovery_action == "write_report":
            self._latest_checkpoint = deepcopy(self._recovery_state)
        self._failed_cgm_nodes = set()
        self._global_url_to_idx = {}
        self._global_references_url = {}
        self._global_ptr = 0
        self._global_ref_lock = threading.Lock()
        try:
            with cancellation_scope(self._cancelled):
                yield from self._research(question, session_id)
        except CancelledError:
            yield ResearchEvent("research.cancelled")
        except Exception:
            logging.exception("[Research:%s] Unhandled research engine error", session_id)
            raise
        finally:
            try:
                scheduler = self._scheduler
                if scheduler is not None:
                    scheduler.close()
                    self._capture_checkpoint(scheduler)
                self.interpreter.reset()
            except Exception:
                logging.exception("[Research:%s] Research engine cleanup failed", session_id)
                raise
            finally:
                self._scheduler = None
                self._run_lock.release()


class AsyncVeritraceAgent(VeritraceAgent):
    """Run the research iterator on a worker thread without blocking the event loop."""

    async def aforward(self, question: str, session_id: int = 0) -> AsyncIterator[ResearchEvent]:
        from .runtime.async_stream import stream_in_thread

        stream = stream_in_thread(lambda: self._safe_forward(question, session_id), self.cancel)
        try:
            async for event in stream:
                yield event
        finally:
            await stream.aclose()

    async def __call__(self, question: str, session_id: int = 0) -> AsyncIterator[ResearchEvent]:
        stream = self.aforward(question, session_id)
        try:
            async for event in stream:
                yield event
        finally:
            await stream.aclose()
