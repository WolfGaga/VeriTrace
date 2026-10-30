"""Model-driven cognitive-graph parsing, search updates, planning context, and information-gap analysis."""

import json
import logging
import re
import uuid
from typing import Dict, List, Optional, Any

from .core import BaseLLMAdapter, Message
from .cognitive_graph import (
    CognitiveGraph,
    EntityNode,
    RelationEdge,
    GraphStructure,
)


def _normalize_dependency(val) -> Optional[str]:
    """LLM may return discovery_dependency as list or string; normalize to comma-separated string."""
    if val is None:
        return None
    if isinstance(val, list):
        return ",".join(str(v).strip() for v in val if v) or None
    return str(val)


from .prompts.cognitive import (
    COGNITIVE_GRAPH_PARSE_PROMPT,
    COGNITIVE_GRAPH_PARSE_PROMPT_EN,
    COGNITIVE_GRAPH_UPDATE_PROMPT,
    COGNITIVE_GRAPH_UPDATE_PROMPT_CN,
    PHASE1_RESTRUCTURE_PROMPT,
    PHASE1_RESTRUCTURE_PROMPT_EN,
    PHASE2_RECONNECT_PROMPT,
    PHASE2_RECONNECT_PROMPT_EN,
)


class CognitiveGraphManager:
    """Parse and update cognitive graphs, prepare planning context, and identify information gaps."""

    def __init__(self, llm: BaseLLMAdapter, language: str = "en"):
        """Initialize an independent graph-management model and the en or cn prompt language."""
        self.llm = llm
        self.language = language
        self.graph: Optional[CognitiveGraph] = None
        self._parse_attempts = 0
        self._max_parse_attempts = 3

    def initial_parse(self, question: str) -> CognitiveGraph:
        """Parse the user's original question into a CognitiveGraph."""
        logging.debug(f"[CognitiveGraphManager] 开始解析问题: {question[:100]}...")

        # Get the current date.
        from datetime import datetime

        current_date = datetime.now().strftime("%Y年%m月%d日")

        parse_prompt = COGNITIVE_GRAPH_PARSE_PROMPT_EN if self.language == "en" else COGNITIVE_GRAPH_PARSE_PROMPT
        prompt = parse_prompt.format(current_date=current_date, question=question)
        messages = [Message(role="user", content=prompt)]

        for attempt in range(self._max_parse_attempts):
            try:
                response = self.llm.chat(messages)
                result = self._parse_json_response(response.content)

                if result:
                    self.graph = self._build_graph_from_result(result, question)
                    logging.info(f"[CognitiveGraphManager] 认知图构建完成: {self.graph}")
                    return self.graph

                # Return parsing feedback for the next attempt.
                if attempt < self._max_parse_attempts - 1:
                    messages.append(Message(role="assistant", content=response.content))
                    messages.append(
                        Message(
                            role="user",
                            content="JSON解析失败。请直接输出JSON对象，不要使用markdown代码块。确保JSON格式正确。",
                        )
                    )

            except Exception as e:
                logging.error(f"[CognitiveGraphManager] 解析尝试{attempt + 1}失败: {e}")

        # Use a basic graph when all attempts fail.
        logging.warning("[CognitiveGraphManager] 解析失败，使用基础图")
        return self._create_basic_graph(question)

    def update_graph(
        self,
        node_name: str,
        inquiry_goal: str,
        search_result: str,
        search_quality: Optional[float] = None,
        edge_id: Optional[str] = None,
        target_id: Optional[str] = None,
        feedback: Optional[Dict] = None,
        quality_data: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Update the graph from a task's search result and feedback. Prefer explicit edge and target mappings; attach optional search quality (0-5) and per-page Reader CR/AAP scores. Return the update result."""
        if not self.graph:
            return {"error": "认知图未初始化"}

        # Keep search context local to avoid races.
        search_context = {
            "node_name": node_name,
            "inquiry_goal": inquiry_goal,
            "search_quality": search_quality,
            "edge_id": edge_id,
            "target_id": target_id,
            "feedback": feedback,  # Searcher feedback.
            "quality_data": quality_data or [],  # Reader CR/AAP scores
        }

        logging.info(f"[CognitiveGraphManager] 更新图: 节点={node_name}")

        # Read the edge's criteria.
        core_criteria = []
        supplementary_criteria = []
        if edge_id and edge_id in self.graph.edges:
            edge = self.graph.edges[edge_id]
            core_criteria = edge.core_criteria
            supplementary_criteria = edge.supplementary_criteria

        # Compare current-round source quality with CR/AAP scores in the graph.
        quality_data_local = search_context.get("quality_data", []) or []
        quality_lines = []
        for q in quality_data_local:
            if not isinstance(q, dict):
                continue
            idx = q.get("index")
            if idx is None:
                continue
            cr = q.get("cr")
            aap = q.get("aap")
            try:
                cr_disp = f"{float(cr):.1f}" if cr is not None else "?"
            except (TypeError, ValueError):
                cr_disp = "?"
            try:
                aap_disp = f"{float(aap):.1f}" if aap is not None else "?"
            except (TypeError, ValueError):
                aap_disp = "?"
            quality_lines.append(f"[[{idx}]] CR={cr_disp} AAP={aap_disp}")
        if quality_lines:
            quality_summary = "## Source Quality Scores (CR=info density, AAP=trustworthiness; both 1-5)\n" + "\n".join(
                quality_lines
            )
        else:
            quality_summary = "(no per-ref quality scores available for this round)"

        # Append quality summary to the Searcher response so CGM LLM sees it
        # alongside the [[n]]-citing text. We keep the [:15000] truncation on
        # the original answer and add quality data after.
        search_result_with_quality = search_result[:15000]
        if quality_lines:
            search_result_with_quality = search_result_with_quality + "\n\n" + quality_summary

        # Build the update prompt.
        update_prompt = COGNITIVE_GRAPH_UPDATE_PROMPT_CN if self.language == "cn" else COGNITIVE_GRAPH_UPDATE_PROMPT
        # Catch template formatting failures, including unescaped braces.
        # Return an error result within the three-attempt update contract.
        # The caller reports and records the failure.
        try:
            prompt = update_prompt.format(
                current_graph=self.graph.to_planning_context(),
                original_question=self.graph.original_question or "",
                node_name=node_name,
                inquiry_goal=inquiry_goal,
                core_criteria=core_criteria if core_criteria else ["(未指定)"],
                supplementary_criteria=supplementary_criteria if supplementary_criteria else ["(无)"],
                search_result=search_result_with_quality,
            )
        except (KeyError, IndexError, ValueError) as e:
            logging.error(f"[CognitiveGraphManager] update_prompt.format 失败（模板占位符错误）: {e}")
            return {"error": f"prompt 模板渲染失败: {e}"}

        messages = [Message(role="user", content=prompt)]
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                response = self.llm.chat(messages)
                result = self._parse_json_response(response.content)

                if result:
                    # Pass search context explicitly to avoid shared-state races.
                    update_result = self._apply_update(result, search_context)
                    logging.info(f"[CognitiveGraphManager] 更新成功 (attempt {attempt + 1})")
                    return update_result

                # Return parsing feedback for the next attempt.
                if attempt < max_attempts - 1:
                    logging.warning(
                        f"[CognitiveGraphManager] JSON解析失败 (attempt {attempt + 1}/{max_attempts})，重试..."
                    )
                    messages.append(Message(role="assistant", content=response.content))
                    messages.append(
                        Message(
                            role="user",
                            content="""⚠️ JSON解析失败。请检查并修正你的输出：

1. 确保输出是有效的JSON对象
2. 不要使用markdown代码块（```json）
3. 确保所有字符串使用双引号
4. 确保数组和对象正确闭合

请直接输出修正后的JSON：""",
                        )
                    )

            except Exception as e:
                logging.error(f"[CognitiveGraphManager] 更新尝试{attempt + 1}失败: {e}")
                if attempt < max_attempts - 1:
                    messages.append(Message(role="user", content=f"发生错误: {str(e)}。请重新输出正确格式的JSON。"))

        # All attempts failed.
        logging.error(f"[CognitiveGraphManager] 更新失败，已尝试 {max_attempts} 次")
        return {"error": f"更新解析失败（已重试{max_attempts}次）"}

    def _apply_update(self, result: Dict, search_context: Dict = None) -> Dict[str, Any]:
        """Apply cognitive_updates using explicit per-call search context to avoid shared-state races."""
        if search_context is None:
            search_context = {}
        update_summary = {
            "updated_nodes": [],
            "solved_edges": [],
            "investigating_edges": [],
            "exhausted_edges": [],
            "new_edges": [],
            "skipped_duplicate_edges": [],
            "quality_gaps": result.get("quality_gaps", []),
        }

        # Prefer the cognitive_updates format.
        cognitive_updates = result.get("cognitive_updates", [])
        if cognitive_updates:
            # Resolve the target before redirecting updates away from START nodes.
            _direct_target = search_context.get("target_id") if search_context else None
            _llm_target = result.get("target_node_id")
            _redirect_target_id = _direct_target or _llm_target

            for update in cognitive_updates:
                node_id = update.get("node_id")
                # B1 fix: a mis-keyed node_id used to silently drop the WHOLE
                # per-node update (no else, no log). Resolve it first; only drop
                # loudly when genuinely unresolvable.
                if not (node_id and node_id in self.graph.nodes):
                    resolved = None
                    if node_id:
                        # (a) LLM used the node NAME instead of its id (common — the
                        # prompt shows both). Exact name match is safe, no misattribution.
                        resolved = next(
                            (nid for nid, n in self.graph.nodes.items() if getattr(n, "name", None) == node_id),
                            None,
                        )
                    elif _redirect_target_id and _redirect_target_id in self.graph.nodes:
                        # (b) node_id missing entirely → the Planner-designated target
                        # is the unambiguous intent for this task.
                        resolved = _redirect_target_id
                    if resolved:
                        logging.info(f"[CognitiveGraphManager] node_id {node_id!r} 解析为 {resolved}")
                        node_id = resolved
                        update["node_id"] = resolved
                    else:
                        # Specific-but-unknown id: do NOT gamble on target_id (could
                        # misattribute a legit non-target update). Drop LOUDLY.
                        logging.warning(
                            f"[CognitiveGraphManager] 丢弃更新：node_id={node_id!r} 不在图中且无法解析，"
                            f"丢失 item_findings={list((update.get('item_findings') or {}).keys())} "
                            f"contradictions={len(update.get('contradictions') or [])} "
                            f"unexpected={len(update.get('unexpected_discoveries') or [])}"
                        )
                if node_id and node_id in self.graph.nodes:
                    node = self.graph.nodes[node_id]

                    # START nodes are immutable; redirect findings to the exploration target.
                    if node.node_type == "start":
                        if (
                            _redirect_target_id
                            and _redirect_target_id in self.graph.nodes
                            and _redirect_target_id != node_id
                        ):
                            logging.info(
                                f"[CognitiveGraphManager] START节点 {node_id} 的更新重定向到 {_redirect_target_id}"
                            )
                            update["node_id"] = _redirect_target_id
                            node_id = _redirect_target_id
                            node = self.graph.nodes[node_id]
                        else:
                            logging.warning(
                                f"[CognitiveGraphManager] 跳过START节点 {node_id} 的更新（无有效重定向目标）"
                            )
                            continue

                    # Update cognitive state.
                    new_state = update.get("state")
                    if new_state:
                        node.cognitive_state = new_state

                    # Update discovered items.
                    new_items = update.get("discovered_items", [])
                    if new_items:
                        # Require a list of strings; invalid discovered_items trigger a retry.
                        for v in new_items:
                            if not isinstance(v, str):
                                raise ValueError(
                                    f"discovered_items 必须是字符串列表，但收到了 {type(v).__name__}: {v}。"
                                    f'请确保 discovered_items 格式为 ["value1", "value2"]'
                                )
                        # Merge rather than replace.
                        for v in new_items:
                            if v not in node.discovered_items:
                                node.discovered_items.append(v)

                    # Update additional fields.
                    cited_refs = update.get("cited_refs", [])
                    if cited_refs:
                        node.cited_refs = list(set(node.cited_refs + cited_refs))

                    temporal_notes = update.get("temporal_notes")
                    if temporal_notes:
                        new_note = ""
                        if isinstance(temporal_notes, str):
                            new_note = temporal_notes
                        elif isinstance(temporal_notes, dict):
                            # Convert objects returned by the model to readable strings.
                            new_note = "; ".join(f"{k}: {v}" for k, v in temporal_notes.items())
                        else:
                            new_note = str(temporal_notes)

                        # Append new entries without duplicating existing ones.
                        if new_note:
                            if node.temporal_notes:
                                if new_note not in node.temporal_notes:
                                    node.temporal_notes = f"{node.temporal_notes}; {new_note}"
                            else:
                                node.temporal_notes = new_note

                    # Structured findings.
                    # Findings are plain strings.
                    # All [[n]] tokens are already in the unified global
                    # namespace by the time they reach CGM (VeritraceAgent's
                    # incremental allocator rewrote response_content to
                    # global BEFORE this update_graph call). So we just
                    # store the text as-is; no per-task tagging needed for
                    # downstream remap (there IS no downstream remap).

                    # Per-item findings: {item_name: {attribute: value}}.
                    item_findings = update.get("item_findings", {})
                    if item_findings:
                        written_items = []
                        # Preserve a non-dictionary payload as a summary and warn.
                        if not isinstance(item_findings, dict):
                            logging.warning(
                                f"[CognitiveGraphManager] item_findings 顶层非dict "
                                f"({type(item_findings).__name__})，已转存为 _misc.summary"
                            )
                            item_findings = {"_misc": str(item_findings)}
                        for item_name, attrs in item_findings.items():
                            # Keep flat-string findings as usable content by wrapping them
                            # in {'summary': ...}, rather than discarding them.
                            if not isinstance(attrs, dict):
                                node.item_findings.setdefault(item_name, {})["summary"] = str(attrs)
                                logging.warning(
                                    f"[CognitiveGraphManager] item_findings.{item_name} 非dict "
                                    f"({type(attrs).__name__})，已转存 .summary"
                                )
                                written_items.append(item_name)
                                continue
                            if item_name not in node.item_findings:
                                node.item_findings[item_name] = {}
                            for attr_name, attr_value in attrs.items():
                                new_val = attr_value if isinstance(attr_value, str) else str(attr_value)
                                existing = node.item_findings[item_name].get(attr_name)
                                if existing is not None and existing != new_val:
                                    logging.info(
                                        f"[CognitiveGraphManager] 更新 item_findings.{item_name}.{attr_name}: "
                                        f"({len(str(existing))}字符) -> ({len(new_val)}字符)"
                                    )
                                node.item_findings[item_name][attr_name] = new_val
                            written_items.append(item_name)
                        # Log entries actually written, not merely the input keys.
                        logging.info(f"[CognitiveGraphManager] item_findings 实际写入: {written_items}")

                    # Cross-item findings: {aspect: description} or {aspect: [item1, item2, ...]}.
                    cross_item_findings = update.get("cross_item_findings", {})
                    if cross_item_findings and isinstance(cross_item_findings, dict):
                        for aspect, description in cross_item_findings.items():
                            if isinstance(description, list):
                                normalized_val = [str(item) for item in description]
                            elif isinstance(description, str):
                                normalized_val = description
                            else:
                                normalized_val = str(description)
                            existing = node.cross_item_findings.get(aspect)
                            if existing is not None and existing != normalized_val:
                                logging.info(f"[CognitiveGraphManager] 更新 cross_item_findings.{aspect}")
                            node.cross_item_findings[aspect] = normalized_val
                        logging.info(
                            f"[CognitiveGraphManager] cross_item_findings 触发 aspects: {list(cross_item_findings.keys())}"
                        )

                    # Citation indices in old_claim and new_claim are already global.
                    # no per-claim task tagging needed.
                    contradictions = update.get("contradictions", [])
                    if contradictions:
                        for contradiction in contradictions:
                            if not isinstance(contradiction, dict):
                                logging.warning(
                                    f"[CognitiveGraphManager] 跳过非dict矛盾条目 ({type(contradiction).__name__})"
                                )
                                continue
                            # Preserve conflicts that omit criterion by supplying a default
                            # and warning; keep their claims and resolution details.
                            if "criterion" not in contradiction:
                                logging.warning(
                                    f"[CognitiveGraphManager] 矛盾条目缺 criterion，补 '(未指定)'；"
                                    f"old={(contradiction.get('old_claim') or '')[:60]} "
                                    f"new={(contradiction.get('new_claim') or '')[:60]}"
                                )
                                contradiction["criterion"] = "(未指定)"
                            # Strip the now-obsolete old_task/new_task fields
                            # in case the LLM (or legacy data) still produces them.
                            contradiction.pop("old_task", None)
                            contradiction.pop("new_task", None)
                            node.contradictions.append(contradiction)
                            logging.info(
                                f"[CognitiveGraphManager] 记录矛盾: {contradiction.get('criterion')} - "
                                f"旧: {(contradiction.get('old_claim') or '')[:80]}... → "
                                f"新: {(contradiction.get('new_claim') or '')[:80]}... "
                                f"(采用: {contradiction.get('kept', 'new')}) "
                                f"old_quality={contradiction.get('old_quality')} "
                                f"new_quality={contradiction.get('new_quality')}"
                            )

                    # Replace unanswered criteria by tier.
                    core_pending = update.get("core_pending", [])
                    if isinstance(core_pending, list):
                        node.core_pending = [p for p in core_pending if isinstance(p, str)]
                        if node.core_pending:
                            logging.debug(f"[CognitiveGraphManager] 核心待验收: {node.core_pending}")

                    supplementary_pending = update.get("supplementary_pending", [])
                    if isinstance(supplementary_pending, list):
                        node.supplementary_pending = [p for p in supplementary_pending if isinstance(p, str)]
                        if node.supplementary_pending:
                            logging.debug(f"[CognitiveGraphManager] 补充待验收: {node.supplementary_pending}")

                    # Merge unexpected findings, deduplicating by value text.
                    # Unexpected findings are plain strings.
                    # All [[n]] tokens are already global at this point.
                    unexpected = update.get("unexpected_discoveries", [])
                    if isinstance(unexpected, list):
                        existing_values = set(d for d in node.unexpected_discoveries if isinstance(d, str))
                        for d in unexpected:
                            if not isinstance(d, str) or d in existing_values:
                                continue
                            node.unexpected_discoveries.append(d)
                            existing_values.add(d)
                        if unexpected:
                            logging.debug(f"[CognitiveGraphManager] 意外发现: {node.unexpected_discoveries[:2]}...")

                    # Promote UNKNOWN nodes with findings to PARTIAL.
                    if node.cognitive_state == "UNKNOWN" and (
                        node.item_findings or node.cross_item_findings or node.discovered_items
                    ):
                        node.cognitive_state = "PARTIAL"
                        logging.info(f"[CognitiveGraphManager] 状态自动升级: {node_id} UNKNOWN → PARTIAL (有 findings)")

                    update_summary["updated_nodes"].append(node_id)

                    # Record related_task on every updated node, including secondary targets.
                    # This preserves evidence attribution for tasks covering several nodes.
                    task_name_for_node = search_context.get("node_name") if search_context else None
                    if task_name_for_node and task_name_for_node not in node.related_tasks:
                        node.related_tasks.append(task_name_for_node)
                        logging.info(
                            f"[CognitiveGraphManager] 记录 related_task (via node_updates): {node_id} <- {task_name_for_node}"
                        )

        # Use the supplied search_context instead of instance state.
        # Prefer an explicit target_id; otherwise use the model's inferred target.
        direct_target_id = search_context.get("target_id")
        direct_edge_id = search_context.get("edge_id")
        llm_target_id = result.get("target_node_id")

        # Resolve the target, preferring the explicit mapping.
        target_node_id = direct_target_id if direct_target_id else llm_target_id

        if direct_target_id:
            logging.info(
                f"[CognitiveGraphManager] 使用直接映射: target_id={direct_target_id}, edge_id={direct_edge_id}"
            )
        elif llm_target_id:
            logging.info(f"[CognitiveGraphManager] 回退到 LLM 推断: target_node_id={llm_target_id}")

        # Support the older target_node_id update format.
        if (
            target_node_id
            and target_node_id in self.graph.nodes
            and target_node_id not in update_summary["updated_nodes"]
        ):
            discovered = result.get("discovered_items", [])
            self.graph.replace_placeholder(target_node_id, discovered)
            update_summary["updated_nodes"].append(target_node_id)

        # Resolve the edge, preferring the explicit edge_id mapping.
        if direct_edge_id and direct_edge_id in self.graph.edges:
            # Use the specified edge.
            related_edges = [self.graph.edges[direct_edge_id]]
            # Skip solved, exhausted, or known edges; keep investigating edges active.
            if related_edges[0].state in ("solved", "exhausted", "known"):
                related_edges = []  # The edge is already complete.
        else:
            # Otherwise find an unfinished edge by target_node_id.
            related_edges = [
                e
                for e in self.graph.edges.values()
                if e.target_node_id == target_node_id and e.state in ("to_solve", "investigating")
            ]

        # Read Searcher feedback.
        searcher_feedback = search_context.get("feedback")

        # Derive the edge state from the target node's state.
        target_node = self.graph.get_node(target_node_id) if target_node_id else None
        target_cognitive_state = result.get("new_cognitive_state", "UNKNOWN")
        if target_node:
            target_cognitive_state = target_node.cognitive_state  # Use the updated node state.

        # Link the task to its target for outline planning and evidence lookup.
        task_name = search_context.get("node_name")
        if target_node and task_name:
            if task_name not in target_node.related_tasks:
                target_node.related_tasks.append(task_name)
                logging.info(f"[CognitiveGraphManager] 记录 related_task: {target_node_id} <- {task_name}")

        # Store finding_strength/unexpected_strength from LLM assessment
        finding_strength = result.get("finding_strength", "")
        unexpected_strength = result.get("unexpected_strength", "")

        # Aggregate CR/AAP quality data onto target node's quality_profile
        quality_data = search_context.get("quality_data", [])
        if target_node and (quality_data or finding_strength or unexpected_strength):
            qp = target_node.quality_profile
            if not qp:
                qp = {"_all_cr": [], "_all_aap": [], "accessibility_barriers": []}
                target_node.quality_profile = qp

            # Initialize lists if missing (e.g., from deserialization)
            if "_all_cr" not in qp:
                qp["_all_cr"] = []
            if "_all_aap" not in qp:
                qp["_all_aap"] = []
            if "accessibility_barriers" not in qp:
                qp["accessibility_barriers"] = []

            # Per-ref quality: {local_index: {cr, aap}} for frontend display
            if "ref_quality" not in qp:
                qp["ref_quality"] = {}

            for q in quality_data:
                if not isinstance(q, dict):
                    continue
                cr_val = q.get("cr", 0)
                aap_val = q.get("aap", 0)
                if cr_val or aap_val:
                    qp["_all_cr"].append(float(cr_val))
                    qp["_all_aap"].append(float(aap_val))

                # Store per-ref quality (keyed by local page index, remapped later)
                page_idx = q.get("index")
                if page_idx is not None and (cr_val or aap_val):
                    qp["ref_quality"][str(page_idx)] = {
                        "cr": round(float(cr_val), 1),
                        "aap": round(float(aap_val), 1),
                    }

                # Collect accessibility barriers
                for note in q.get("accessibility_notes", []):
                    if isinstance(note, dict) and note.get("barrier"):
                        barrier_str = f"{note.get('barrier')} ({note.get('detail', '')[:60]})"
                        if barrier_str not in qp["accessibility_barriers"]:
                            qp["accessibility_barriers"].append(barrier_str)

            # Cap at 50 most recent scores
            qp["_all_cr"] = qp["_all_cr"][-50:]
            qp["_all_aap"] = qp["_all_aap"][-50:]

            # Recompute aggregates
            if qp["_all_cr"]:
                qp["cr_mean"] = round(sum(qp["_all_cr"]) / len(qp["_all_cr"]), 2)
                qp["cr_min"] = round(min(qp["_all_cr"]), 2)
                qp["aap_mean"] = round(sum(qp["_all_aap"]) / len(qp["_all_aap"]), 2)
                qp["aap_min"] = round(min(qp["_all_aap"]), 2)
                qp["page_count"] = len(qp["_all_cr"])

            # Store LLM-assessed evidence strength (latest wins)
            if finding_strength:
                qp["finding_strength"] = finding_strength
            if unexpected_strength:
                qp["unexpected_strength"] = unexpected_strength

            logging.info(
                f"[CognitiveGraphManager] Quality profile updated for {target_node_id}: "
                f"CR={qp.get('cr_mean', 0):.1f} AAP={qp.get('aap_mean', 0):.1f} "
                f"({qp.get('page_count', 0)} pages) "
                f"finding={qp.get('finding_strength', '?')} unexpected={qp.get('unexpected_strength', '?')}"
            )

        # Derive the edge state from the target node's state.
        for edge in related_edges:
            # Summarize the search using the target's unanswered criteria.
            attempt_summary = "搜索完成"
            if target_node and target_node.core_pending:
                attempt_summary = f"待验收: {'; '.join(target_node.core_pending[:2])}"
            elif target_cognitive_state == "UNKNOWN":
                attempt_summary = "未找到有效信息"

            # Record this attempt.
            edge.record_attempt(
                query=search_context.get("inquiry_goal", ""),
                summary=attempt_summary,
                node_name=search_context.get("node_name", ""),
                feedback=searcher_feedback,
            )

            # Store search experience in edge's search history
            for cog_update in result.get("cognitive_updates", []):
                search_experience = cog_update.get("search_experience", [])
                if search_experience and edge.search_history:
                    edge.search_history[-1]["experience"] = search_experience
                    logging.info(f"   🚫 Recorded {len(search_experience)} search experience entries on edge {edge.id}")

            # Derive edge state from node state unless the edge is exhausted.
            if edge.is_exhausted():
                update_summary["exhausted_edges"].append(edge.id)
                logging.warning(f"[CognitiveGraphManager] 边 {edge.id} 已耗尽尝试次数")
            elif target_cognitive_state == "KNOWN":
                edge.mark_solved()
                update_summary["solved_edges"].append(edge.id)
                logging.info(f"[CognitiveGraphManager] 边 {edge.id} 已解决（目标节点KNOWN）")
            elif target_cognitive_state == "PARTIAL":
                edge.mark_investigating()
                update_summary["investigating_edges"].append(edge.id)
                logging.info(f"[CognitiveGraphManager] 边 {edge.id} 调查中（目标节点PARTIAL）")
            # Keep UNKNOWN targets on to_solve edges for another attempt.

        # Add at most three discovered edges.
        MAX_NEW_EDGES = 3
        new_edges_data = result.get("new_edges", [])[:MAX_NEW_EDGES]

        for new_edge_data in new_edges_data:
            source_id = new_edge_data.get("source", "")
            target_id = new_edge_data.get("target", "")
            relation_type = new_edge_data.get("type", "")

            # Require both endpoint nodes to exist.
            if not source_id or source_id not in self.graph.nodes:
                logging.warning(f"[CognitiveGraphManager] Skipped new_edge: source '{source_id}' not found in graph")
                continue
            if not target_id or target_id not in self.graph.nodes:
                logging.warning(f"[CognitiveGraphManager] Skipped new_edge: target '{target_id}' not found in graph")
                continue

            # Check for a semantically equivalent edge.
            if self.graph.has_similar_edge(source_id, target_id, relation_type):
                update_summary["skipped_duplicate_edges"].append(f"{source_id}->{target_id}:{relation_type}")
                continue

            new_edge = RelationEdge(
                id=f"r_new_{uuid.uuid4().hex[:8]}",
                source_node_id=source_id,
                target_node_id=target_id,
                relation_type=relation_type,
                inquiry_goal=new_edge_data.get("inquiry_goal", ""),
                state="to_solve",
            )
            added_id = self.graph.add_edge(new_edge)
            if added_id == new_edge.id:  # Confirm that a new edge was added.
                update_summary["new_edges"].append(new_edge.id)

        logging.info(
            f"[CognitiveGraphManager] Update summary: "
            f"updated={len(update_summary['updated_nodes'])}, "
            f"solved={len(update_summary['solved_edges'])}, "
            f"investigating={len(update_summary['investigating_edges'])}, "
            f"new_edges={len(update_summary['new_edges'])}"
        )

        return update_summary

    def get_planning_context(self) -> str:
        """Return formatted cognitive-graph context for the Planner."""
        if not self.graph:
            return "认知图未初始化"
        return self.graph.to_planning_context()

    def get_graph_dict(self) -> Dict:
        """Return the cognitive graph as a dictionary for presentation."""
        if not self.graph:
            return {}
        return self.graph.to_dict()

    def _parse_json_response(self, response: str) -> Optional[Dict]:
        """Extract JSON from plain text, Markdown fences, reasoning tags, or surrounding prose."""
        if not response:
            return None

        # 1. Remove think tags and their content.
        clean = re.sub(r"<think>[\s\S]*?</think>", "", response)

        # 2. Remove other common reasoning tags.
        clean = re.sub(r"<thinking>[\s\S]*?</thinking>", "", clean)
        clean = re.sub(r"<reflection>[\s\S]*?</reflection>", "", clean)

        # 3. Remove Markdown code fences.
        clean = re.sub(r"```json\s*", "", clean)
        clean = re.sub(r"```\s*", "", clean)

        # Log JSON parsing failures for diagnosis.
        # 4. Parse the cleaned content directly.
        try:
            return json.loads(clean.strip())
        except json.JSONDecodeError as e:
            logging.debug(f"[CognitiveGraphManager] 直接解析失败: {e}")

        # 5. Extract the outermost JSON object.
        json_match = re.search(r"\{[\s\S]*\}", clean)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError as e:
                logging.debug(f"[CognitiveGraphManager] 正则提取后解析失败: {e}")

        # 6. Fall back to extracting JSON from the original response.
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError as e:
                logging.debug(f"[CognitiveGraphManager] 从原始响应提取后解析失败: {e}")

        logging.warning(f"[CognitiveGraphManager] JSON解析失败，响应前200字符: {response[:200]}")
        return None

    def _build_graph_from_result(self, result: Dict, question: str) -> CognitiveGraph:
        """Build a cognitive graph from the parsed model response."""
        graph = CognitiveGraph()
        graph.original_question = question
        graph.languages = result.get("languages", ["en", "zh"])

        # Add entity nodes.
        for entity_data in result.get("entities", []):
            node = EntityNode(
                id=entity_data.get("id", f"e_{uuid.uuid4().hex[:8]}"),
                name=entity_data.get("name", ""),
                node_type=entity_data.get("type", "start"),
                cognitive_state=entity_data.get("cognitive_state", "START"),
                type_constraint=entity_data.get("type_constraint"),
                condition_constraints=entity_data.get("condition_constraints", []),
                hop_distance=entity_data.get("hop_distance", 0),
                discovery_dependency=_normalize_dependency(entity_data.get("discovery_dependency")),
            )
            graph.add_node(node)

        # Add relationship edges.
        for relation_data in result.get("relations", []):
            edge = RelationEdge(
                id=relation_data.get("id", f"r_{uuid.uuid4().hex[:8]}"),
                source_node_id=relation_data.get("source", ""),
                target_node_id=relation_data.get("target", ""),
                relation_type=relation_data.get("type", ""),
                inquiry_goal=relation_data.get("inquiry_goal", ""),
                state=relation_data.get("state", "to_solve"),
                # Criteria start empty and are assigned when the Planner creates tasks.
                task_type=relation_data.get("task_type", "open"),
                specified_source=relation_data.get("specified_source"),
            )
            graph.add_edge(edge)

        # Set graph structure.
        structure_data = result.get("structure", {})
        graph.structure = GraphStructure(
            structure_type=structure_data.get("type", "serial"),
            parallel_chains=structure_data.get("parallel_chains", []),
            convergence_nodes=structure_data.get("convergence_nodes", []),
            dependency_order=structure_data.get("dependency_order", []),
        )

        # Root node fix: UNKNOWN nodes with no incoming edges can never be targeted by
        # any task (add_task requires cognitive_edge_id → edge target = updated node).
        # This applies when the question has no known entity anchor, such as a ranking of international insurers.
        # Fix: create a virtual root anchor (KNOWN) with an edge pointing to the orphaned node.
        nodes_with_incoming = set()
        for edge in graph.edges.values():
            nodes_with_incoming.add(edge.target_node_id)
        for node in graph.nodes.values():
            if node.id not in nodes_with_incoming and node.cognitive_state == "UNKNOWN":
                # Create virtual root anchor
                root_id = f"e_root_{node.id}"
                root_node = EntityNode(
                    id=root_id,
                    name="研究主题",
                    node_type="start",
                    cognitive_state="START",
                    hop_distance=0,
                )
                graph.add_node(root_node)
                # Create edge from virtual root to the orphaned UNKNOWN node
                edge_id = f"r_root_{node.id}"
                root_edge = RelationEdge(
                    id=edge_id,
                    source_node_id=root_id,
                    target_node_id=node.id,
                    relation_type="研究目标",
                    inquiry_goal=node.name,
                    state="to_solve",
                    task_type="open",
                )
                graph.add_edge(root_edge)
                # Bump hop_distance for the orphaned node and its descendants
                if node.hop_distance == 0:
                    node.hop_distance = 1
                logging.info(
                    f"[CognitiveGraphManager] 为 UNKNOWN 根节点 {node.id} ({node.name}) "
                    f"创建虚拟锚点 {root_id} + edge {edge_id}"
                )

        return graph

    # ===== 3-Phase Restructure System =====

    def _classify_nodes(self):
        """Classify nodes into protected and modifiable lists."""
        protected = []
        modifiable = []
        for node in self.graph.nodes.values():
            if node.has_findings() or node.cognitive_state in ("START", "KNOWN", "PARTIAL"):
                protected.append(node.id)
            else:
                modifiable.append(node.id)
        return protected, modifiable

    def _classify_edges(self):
        """Classify edges into protected and modifiable lists."""
        protected = []
        modifiable = []
        for edge in self.graph.edges.values():
            if edge.state in ("solved", "investigating", "exhausted"):
                protected.append(edge.id)
            else:
                modifiable.append(edge.id)
        return protected, modifiable

    def _would_create_cycle(self, source_id: str, target_id: str) -> bool:
        """
        Check if adding edge source→target would create a cycle.
        BFS from target along outgoing edges; if we reach source, cycle exists.
        """
        if source_id == target_id:
            return True
        visited = set()
        queue = [target_id]
        while queue:
            current = queue.pop(0)
            if current == source_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            for edge in self.graph.get_edges_by_source(current):
                if edge.target_node_id not in visited:
                    queue.append(edge.target_node_id)
        return False

    def _detect_orphans(self, protected_nodes: List[str]) -> List[Dict]:
        """
        Detect orphan nodes using BFS reachability from root nodes.

        Orphan = not reachable from any root AND not protected AND not "start" type.
        This catches both simple orphans (no incoming edges) and
        unreachable subgraphs (internally connected but disconnected from roots).

        Returns:
            List of dicts with orphan context: node_id, name, state, has_findings,
            former_incoming_edges
        """
        if not self.graph:
            return []

        # Find root nodes: "start" type OR protected (has findings / START / KNOWN / PARTIAL state)
        # Protected nodes are confirmed research anchors — their subtrees are reachable
        protected_set_for_bfs = set(protected_nodes)
        root_ids = {
            nid for nid, node in self.graph.nodes.items() if node.node_type == "start" or nid in protected_set_for_bfs
        }

        # BFS from roots
        reachable = set(root_ids)
        queue = list(root_ids)
        while queue:
            current = queue.pop(0)
            for edge in self.graph.get_edges_by_source(current):
                target = edge.target_node_id
                if target not in reachable and target in self.graph.nodes:
                    reachable.add(target)
                    queue.append(target)

        # Identify orphans
        protected_set = set(protected_nodes)
        orphans = []
        for nid, node in self.graph.nodes.items():
            if nid in reachable:
                continue
            if nid in protected_set:
                continue
            if node.node_type == "start":
                continue

            # Collect incoming edges (may exist from other orphans)
            incoming_edges = []
            for edge in self.graph.edges.values():
                if edge.target_node_id == nid:
                    incoming_edges.append(
                        {
                            "edge_id": edge.id,
                            "source": edge.source_node_id,
                            "inquiry_goal": edge.inquiry_goal,
                        }
                    )

            orphans.append(
                {
                    "node_id": nid,
                    "name": node.name,
                    "state": node.cognitive_state,
                    "has_findings": node.has_findings(),
                    "former_incoming_edges": incoming_edges,
                }
            )

        return orphans

    def restructure_graph(self, proposal) -> Dict[str, Any]:
        """
        3-Phase graph restructuring:
        - Phase 1: Surgical fix (1 LLM call) using tree-form topology view
        - Phase 2: Orphan reconnection loop (up to 5 LLM calls)
        - Rollback: Restore snapshot if Phase 2 fails after 5 rounds

        Args:
            proposal: dict with {"op_type": str, "reasoning": str} or legacy str

        Returns:
            Restructure result summary (or rollback error with guidance)
        """
        if not self.graph:
            return {"error": "Cognitive graph not initialized"}

        # Support both new dict format and legacy string
        if isinstance(proposal, dict):
            planner_reasoning = f"[{proposal.get('op_type', 'UNKNOWN')}] {proposal.get('reasoning', '')}"
        else:
            planner_reasoning = str(proposal)

        logging.info("[CognitiveGraphManager] Starting 3-phase graph restructuring")
        logging.debug(f"   Planner proposal: {planner_reasoning[:200]}...")

        # Snapshot for rollback
        snapshot = self.graph.to_dict()

        # Classify nodes and edges
        protected_nodes, modifiable_nodes = self._classify_nodes()
        protected_edges, modifiable_edges = self._classify_edges()

        logging.debug(f"   Protected nodes: {protected_nodes}")
        logging.debug(f"   Modifiable nodes: {modifiable_nodes}")
        logging.debug(f"   Protected edges: {protected_edges}")
        logging.debug(f"   Modifiable edges: {modifiable_edges}")

        # ===== Phase 1: Surgical fix =====
        phase1_result, phase1_messages = self._phase1_surgical_fix(planner_reasoning, protected_nodes, protected_edges)

        if phase1_result.get("error"):
            return phase1_result

        if not phase1_result.get("should_restructure", False):
            return {
                "should_restructure": False,
                "evaluation": phase1_result.get("evaluation", ""),
            }

        # Apply Phase 1 changes with retry on violations
        summary = self._apply_with_retry(phase1_result, phase1_messages, protected_nodes, protected_edges)
        summary["should_restructure"] = True
        summary["evaluation"] = phase1_result.get("evaluation", "")
        self._refresh_graph_structure()

        # ===== Phase 2: Orphan reconnection (max 5 rounds) =====
        MAX_PHASE2_ROUNDS = 5
        for round_i in range(MAX_PHASE2_ROUNDS):
            orphans = self._detect_orphans(protected_nodes)
            if not orphans:
                break  # No orphans → success

            logging.info(
                f"[Restructure Phase 2] Round {round_i + 1}/{MAX_PHASE2_ROUNDS}: {len(orphans)} orphans detected"
            )

            reconnections = self._phase2_reconnect_orphans(
                orphans,
                protected_nodes,
                protected_edges,
                round_i,
                planner_reasoning=planner_reasoning,
                phase1_summary=summary,
            )
            if reconnections.get("error"):
                logging.warning(f"[Phase 2] Round {round_i + 1} LLM call failed, trying next round")
                continue

            self._apply_reconnections(reconnections, summary, protected_nodes)
            self._refresh_graph_structure()
        else:
            # for-else: loop completed without break → check if last round fixed it
            remaining = self._detect_orphans(protected_nodes)
            if remaining:
                logging.error(
                    f"[Restructure] Phase 2 failed after {MAX_PHASE2_ROUNDS} rounds, "
                    f"{len(remaining)} orphans remain. Rolling back."
                )
                self.graph = CognitiveGraph.from_dict(snapshot)
                return {
                    "error": "restructure_rollback",
                    "should_restructure": False,
                    "evaluation": f"Restructuring rolled back: {len(remaining)} orphans after {MAX_PHASE2_ROUNDS} rounds",
                    "guidance": (
                        "Restructuring failed. Try a simpler approach: "
                        "rename nodes (modify_node), adjust inquiry goals (modify_edge), "
                        "and add missing dimensions directly (add_node + add_edge). "
                        "Avoid removing nodes that create complex orphan chains."
                    ),
                    "orphan_count": len(remaining),
                }

        logging.info(
            f"[CognitiveGraphManager] 3-phase restructuring complete: "
            f"added_nodes={len(summary.get('added_nodes', []))}, "
            f"removed_nodes={len(summary.get('removed_nodes', []))}, "
            f"added_edges={len(summary.get('added_edges', []))}, "
            f"removed_edges={len(summary.get('removed_edges', []))}, "
            f"modified_nodes={len(summary.get('modified_nodes', []))}, "
            f"modified_edges={len(summary.get('modified_edges', []))}, "
            f"violations={len(summary.get('violations', []))}"
        )

        return summary

    def _phase1_surgical_fix(
        self,
        planner_reasoning: str,
        protected_nodes: List[str],
        protected_edges: List[str],
    ) -> tuple:
        """
        Phase 1: Single LLM call for surgical restructuring using tree-form view.

        Returns:
            (result_dict, messages_list) — messages preserved for retry context.
        """
        restructure_view = self.graph.to_restructure_view(protected_nodes, protected_edges)

        prompt_template = PHASE1_RESTRUCTURE_PROMPT_EN if self.language == "en" else PHASE1_RESTRUCTURE_PROMPT
        prompt = prompt_template.format(
            original_question=self.graph.original_question,
            restructure_view=restructure_view,
            planner_reasoning=planner_reasoning,
        )

        messages = [Message(role="user", content=prompt)]
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                response = self.llm.chat(messages)
                result = self._parse_json_response(response.content)
                if isinstance(result, dict) and ("should_restructure" in result or "changes" in result):
                    logging.info(f"[Phase 1] Parsed successfully (attempt {attempt + 1})")
                    messages.append(Message(role="assistant", content=response.content))
                    return result, messages

                if attempt < max_attempts - 1:
                    retry_msg = (
                        "JSON parse failed. Output a single JSON **object** (not array) with "
                        '"should_restructure", "evaluation", and "changes" keys. No markdown code blocks.'
                    )
                    logging.warning(f"[Phase 1] JSON parse failed or wrong shape (attempt {attempt + 1}), retrying...")
                    messages.append(Message(role="assistant", content=response.content))
                    messages.append(Message(role="user", content=retry_msg))
            except Exception as e:
                logging.error(f"[Phase 1] Attempt {attempt + 1} failed: {e}")

        return {"error": f"Phase 1 parsing failed after {max_attempts} attempts"}, messages

    def _phase2_reconnect_orphans(
        self,
        orphans: List[Dict],
        protected_nodes: List[str],
        protected_edges: List[str],
        round_i: int,
        planner_reasoning: str = "",
        phase1_summary: Dict = None,
    ) -> Dict:
        """Phase 2: LLM call to reconnect or confirm-remove orphan nodes."""
        # Build current topology view
        current_view = self.graph.to_restructure_view(protected_nodes, protected_edges)

        # Build orphan details
        orphan_lines = []
        for o in orphans:
            line = f'- {o["node_id"]} "{o["name"]}" [state: {o["state"]}]'
            if o.get("has_findings"):
                line += " (has findings!)"
            if o.get("former_incoming_edges"):
                for fe in o["former_incoming_edges"]:
                    line += f'\n  Edge: {fe["edge_id"]} from {fe["source"]}, inquiry: "{fe["inquiry_goal"]}"'
            orphan_lines.append(line)

        # Build Phase 1 execution summary
        p1_summary_text = "No Phase 1 changes recorded."
        if phase1_summary:
            parts = []
            if phase1_summary.get("removed_nodes"):
                parts.append(f"Removed nodes: {phase1_summary['removed_nodes']}")
            if phase1_summary.get("removed_edges"):
                parts.append(f"Removed edges: {phase1_summary['removed_edges']}")
            if phase1_summary.get("modified_nodes"):
                parts.append(f"Modified nodes: {phase1_summary['modified_nodes']}")
            if phase1_summary.get("modified_edges"):
                parts.append(f"Modified edges: {phase1_summary['modified_edges']}")
            if phase1_summary.get("added_nodes"):
                parts.append(f"Added nodes: {phase1_summary['added_nodes']}")
            if phase1_summary.get("added_edges"):
                parts.append(f"Added edges: {phase1_summary['added_edges']}")
            if parts:
                p1_summary_text = "\n".join(parts)

        prompt_template = PHASE2_RECONNECT_PROMPT_EN if self.language == "en" else PHASE2_RECONNECT_PROMPT
        prompt = prompt_template.format(
            original_question=self.graph.original_question,
            planner_reasoning=planner_reasoning or "Not provided",
            phase1_summary=p1_summary_text,
            current_topology=current_view,
            orphan_details="\n".join(orphan_lines),
            round_number=round_i + 1,
            max_rounds=5,
        )

        messages = [Message(role="user", content=prompt)]
        try:
            response = self.llm.chat(messages)
            result = self._parse_json_response(response.content)
            if isinstance(result, dict) and "actions" in result:
                return result
            logging.warning(f"[Phase 2] Round {round_i + 1}: unexpected JSON shape: {type(result)}")
        except Exception as e:
            logging.error(f"[Phase 2] Round {round_i + 1} failed: {e}")

        return {"error": f"Phase 2 round {round_i + 1} parsing failed"}

    def _apply_reconnections(
        self,
        result: Dict,
        summary: Dict,
        protected_nodes: List[str],
    ):
        """Apply Phase 2 reconnection/removal decisions."""
        actions = result.get("actions", [])

        for action in actions:
            op = action.get("action", "")

            if op == "add_edge":
                edge_data = action.get("edge_data", {})
                source = edge_data.get("source", "")
                target = edge_data.get("target", "")

                # Validate source and target exist
                if source not in self.graph.nodes:
                    summary.setdefault("violations", []).append(f"Phase2: add_edge source {source} not found")
                    continue
                if target not in self.graph.nodes:
                    summary.setdefault("violations", []).append(f"Phase2: add_edge target {target} not found")
                    continue

                # Cycle check
                if self._would_create_cycle(source, target):
                    summary.setdefault("violations", []).append(
                        f"Phase2: add_edge would create cycle {source}→{target}"
                    )
                    continue

                new_edge = RelationEdge(
                    id=edge_data.get("id", f"r_p2_{uuid.uuid4().hex[:8]}"),
                    source_node_id=source,
                    target_node_id=target,
                    relation_type=edge_data.get("type", ""),
                    inquiry_goal=edge_data.get("inquiry_goal", ""),
                    state="to_solve",
                    task_type=edge_data.get("task_type", "open"),
                    specified_source=edge_data.get("specified_source"),
                    max_attempts=3,
                )
                added_id = self.graph.add_edge(new_edge)
                if added_id == new_edge.id:
                    summary.setdefault("added_edges", []).append(new_edge.id)
                    logging.info(f"[Phase 2] Reconnected: {new_edge.id} ({source}→{target})")

            elif op == "confirm_remove":
                node_id = action.get("node_id", "")
                reason = action.get("reason", "")
                if node_id in self.graph.nodes:
                    if node_id not in set(protected_nodes):
                        remove_result = self.graph.remove_node(node_id)
                        if not remove_result["blocked"]:
                            summary.setdefault("removed_nodes", []).append(node_id)
                            summary.setdefault("removed_edges", []).extend(remove_result["removed_edges"])
                            logging.info(f"[Phase 2] Confirmed remove: {node_id} ({reason})")
                    else:
                        summary.setdefault("violations", []).append(
                            f"Phase2: confirm_remove {node_id} blocked (protected)"
                        )

    def _apply_changes(
        self,
        changes: List[Dict],
        protected_nodes: List[str],
        protected_edges: List[str],
    ) -> Dict[str, Any]:
        """
        Apply restructuring changes to the cognitive graph.

        Compared to old _apply_restructure:
        1. New modify_node operation with field whitelist
        2. Relaxed modify_edge: solved/exhausted edges allow inquiry_goal change
        3. add_edge validates source/target exist + no cycle
        4. NO orphan cleanup (Phase 2 handles that)
        """
        summary = {
            "added_nodes": [],
            "removed_nodes": [],
            "added_edges": [],
            "removed_edges": [],
            "modified_edges": [],
            "modified_nodes": [],
            "violations": [],
        }

        for change in changes:
            action = change.get("action", "")
            target_id = change.get("id", "")

            if action == "add_node":
                node_data = change.get("node_data", {})
                new_node = EntityNode(
                    id=node_data.get("id", f"e_dyn_{uuid.uuid4().hex[:8]}"),
                    name=node_data.get("name", ""),
                    node_type=node_data.get("type", "placeholder"),
                    cognitive_state="UNKNOWN",
                    type_constraint=node_data.get("type_constraint"),
                    condition_constraints=node_data.get("condition_constraints", []),
                    hop_distance=node_data.get("hop_distance", 1),
                    discovery_dependency=_normalize_dependency(node_data.get("discovery_dependency")),
                )
                self.graph.add_node(new_node)
                summary["added_nodes"].append(new_node.id)
                logging.info(f"[Restructure] Added node: {new_node.id} ({new_node.name})")

            elif action == "remove_node":
                if target_id in protected_nodes:
                    summary["violations"].append(f"Blocked: remove_node {target_id} (protected)")
                    continue
                if target_id in self.graph.nodes:
                    rm_result = self.graph.remove_node(target_id, set(protected_edges))
                    if rm_result["blocked"]:
                        summary["violations"].append(f"Blocked: remove_node {target_id} ({rm_result['reason']})")
                    else:
                        summary["removed_nodes"].append(target_id)
                        summary["removed_edges"].extend(rm_result["removed_edges"])
                        logging.info(
                            f"[Restructure] Removed node: {target_id} (cascade {len(rm_result['removed_edges'])} edges)"
                        )

            elif action == "add_edge":
                edge_data = change.get("edge_data", {})
                source = edge_data.get("source", "")
                target = edge_data.get("target", "")

                # Validate source and target exist
                if source not in self.graph.nodes:
                    summary["violations"].append(f"Blocked: add_edge {target_id} (source {source} not found)")
                    continue
                if target not in self.graph.nodes:
                    summary["violations"].append(f"Blocked: add_edge {target_id} (target {target} not found)")
                    continue

                # Cycle check
                if self._would_create_cycle(source, target):
                    summary["violations"].append(
                        f"Blocked: add_edge {target_id} (would create cycle {source}→{target})"
                    )
                    continue

                new_edge = RelationEdge(
                    id=edge_data.get("id", f"r_dyn_{uuid.uuid4().hex[:8]}"),
                    source_node_id=source,
                    target_node_id=target,
                    relation_type=edge_data.get("type", ""),
                    inquiry_goal=edge_data.get("inquiry_goal", ""),
                    state="to_solve",
                    task_type=edge_data.get("task_type", "open"),
                    specified_source=edge_data.get("specified_source"),
                    max_attempts=3,
                )
                added_id = self.graph.add_edge(new_edge)
                if added_id == new_edge.id:
                    summary["added_edges"].append(new_edge.id)
                    logging.info(f"[Restructure] Added edge: {new_edge.id}")

            elif action == "remove_edge":
                # remove_edge is not a supported operation — it creates reversible
                # orphans that Phase 2 reconnects, causing infinite loops (Bug #1).
                # Guide LLM toward remove_node which cascade-deletes edges.
                edge = self.graph.get_edge(target_id)
                target_node = edge.target_node_id if edge else "unknown"
                summary["violations"].append(
                    f"Blocked: remove_edge is not supported. "
                    f"To remove a dimension, use remove_node on the target node ({target_node}) instead. "
                    f"remove_node will cascade-delete all connected edges."
                )

            elif action == "modify_node":
                node = self.graph.get_node(target_id)
                if not node:
                    summary["violations"].append(f"Blocked: modify_node {target_id} (not found)")
                    continue

                # Fields ANY node can change (including protected)
                if "name" in change:
                    node.name = change["name"]
                if "type_constraint" in change:
                    node.type_constraint = change["type_constraint"]
                if "condition_constraints" in change:
                    node.condition_constraints = change["condition_constraints"]

                # Pending criteria: always modifiable (search strategy, not search results)
                # Protected nodes protect findings from deletion, NOT search strategy from adjustment
                if "core_pending" in change:
                    node.core_pending = change["core_pending"]
                if "supplementary_pending" in change:
                    node.supplementary_pending = change["supplementary_pending"]

                # Remove discovered items: Planner can clean up noise items
                remove_items = change.get("remove_items", [])
                if remove_items and isinstance(remove_items, list):
                    node.discovered_items = [item for item in node.discovered_items if item not in remove_items]
                    for item_name in remove_items:
                        node.item_findings.pop(item_name, None)
                    logging.debug(f"[Restructure] Removed items from {target_id}: {remove_items}")

                summary["modified_nodes"].append(target_id)
                logging.info(f"[Restructure] Modified node: {target_id}")

            elif action == "modify_edge":
                edge = self.graph.get_edge(target_id)
                if not edge:
                    summary["violations"].append(f"Blocked: modify_edge {target_id} (not found)")
                    continue

                # Bug #2 fix: detect unsupported new_state field
                if "new_state" in change:
                    target_node = edge.target_node_id
                    summary["violations"].append(
                        f"Blocked: modify_edge {target_id} — state changes not supported. "
                        f"Edge states are managed by the search system. "
                        f"To abandon a dimension, use remove_node on the target node ({target_node})."
                    )

                if target_id in protected_edges:
                    # Relaxed protection: solved/exhausted/investigating allow inquiry_goal change
                    new_inquiry = change.get("new_inquiry_goal")
                    if new_inquiry:
                        edge.inquiry_goal = new_inquiry
                        summary["modified_edges"].append(target_id)
                        logging.info(f"[Restructure] Modified edge inquiry (protected/relaxed): {target_id}")
                    # Block target change on all protected edges
                    if change.get("new_target_id"):
                        summary["violations"].append(
                            f"Blocked: modify_edge {target_id} target change (protected, state={edge.state})"
                        )
                    continue

                # Modifiable edge: allow all changes
                new_inquiry = change.get("new_inquiry_goal")
                if new_inquiry:
                    edge.inquiry_goal = new_inquiry
                new_target = change.get("new_target_id")
                if new_target:
                    edge.target_node_id = new_target
                summary["modified_edges"].append(target_id)
                logging.info(f"[Restructure] Modified edge: {target_id}")

        return summary

    def _apply_with_retry(
        self,
        phase1_result: Dict,
        phase1_messages: List,
        protected_nodes: List[str],
        protected_edges: List[str],
    ) -> Dict[str, Any]:
        """
        Apply Phase 1 changes with retry on violations (max 2 retries in same conversation).

        If violations remain after retries, generate planner feedback via LLM.
        """
        # First attempt
        snapshot_before_apply = self.graph.to_dict()
        summary = self._apply_changes(phase1_result.get("changes", []), protected_nodes, protected_edges)

        MAX_RETRIES = 2
        for retry_i in range(MAX_RETRIES):
            if not summary.get("violations"):
                break  # No violations → done

            logging.info(
                f"[Restructure] Violations detected, retry {retry_i + 1}/{MAX_RETRIES}: {summary['violations']}"
            )

            # Rollback to pre-apply state and retry with correction
            self.graph = CognitiveGraph.from_dict(snapshot_before_apply)

            # Build retry prompt in same conversation context
            violation_text = "\n".join(f"- {v}" for v in summary["violations"])
            retry_prompt = (
                f"Some of your operations were rejected:\n{violation_text}\n\n"
                "Please output a corrected JSON with the same format. Rules:\n"
                "- There is NO remove_edge operation. Use remove_node to remove dimensions (cascade-deletes edges).\n"
                "- modify_edge cannot change state. States are managed by the search system.\n"
                "- Protected nodes/edges cannot be removed or have their targets changed.\n"
                "Output corrected JSON only (no markdown code blocks):"
            )
            phase1_messages.append(Message(role="user", content=retry_prompt))

            try:
                response = self.llm.chat(phase1_messages)
                phase1_messages.append(Message(role="assistant", content=response.content))
                corrected = self._parse_json_response(response.content)

                if isinstance(corrected, dict) and "changes" in corrected:
                    snapshot_before_apply = self.graph.to_dict()
                    summary = self._apply_changes(corrected.get("changes", []), protected_nodes, protected_edges)
                    # Preserve evaluation from original
                    if not summary.get("evaluation") and phase1_result.get("evaluation"):
                        summary["evaluation"] = phase1_result["evaluation"]
                else:
                    logging.warning(f"[Restructure] Retry {retry_i + 1} returned invalid JSON")
                    break
            except Exception as e:
                logging.error(f"[Restructure] Retry {retry_i + 1} failed: {e}")
                break

        # If violations still remain, generate planner feedback
        if summary.get("violations"):
            feedback = self._generate_planner_feedback(phase1_messages, summary["violations"])
            if feedback:
                summary["planner_feedback"] = feedback

        return summary

    def _generate_planner_feedback(
        self,
        messages: List,
        violations: List[str],
    ) -> str:
        """
        Generate natural-language feedback for Planner explaining why operations were rejected.

        Uses same conversation context so LLM understands what was attempted.
        """
        violation_text = "\n".join(f"- {v}" for v in violations)
        feedback_prompt = (
            f"The following operations were permanently rejected:\n{violation_text}\n\n"
            "Generate a brief feedback message (2-3 sentences) for the Planner explaining:\n"
            "1. Why these operations were blocked\n"
            "2. What the correct alternative approach would be\n"
            "Output only the feedback text, no JSON."
        )
        messages.append(Message(role="user", content=feedback_prompt))

        try:
            response = self.llm.chat(messages)
            feedback = response.content.strip()
            logging.debug(f"[Restructure] Generated planner feedback: {feedback[:100]}...")
            return feedback
        except Exception as e:
            logging.error(f"[Restructure] Failed to generate planner feedback: {e}")
            return ""

    def _refresh_graph_structure(self):
        """Remove stale node and edge references from GraphStructure after restructuring."""
        if not self.graph:
            return

        existing_node_ids = set(self.graph.nodes.keys())

        # Remove stale entries from dependency_order.
        self.graph.structure.dependency_order = [
            nid for nid in self.graph.structure.dependency_order if nid in existing_node_ids
        ]

        # Remove stale entries from convergence_nodes.
        self.graph.structure.convergence_nodes = [
            nid for nid in self.graph.structure.convergence_nodes if nid in existing_node_ids
        ]

        # Remove stale entries from parallel_chains.
        self.graph.structure.parallel_chains = [
            [nid for nid in chain if nid in existing_node_ids] for chain in self.graph.structure.parallel_chains
        ]
        self.graph.structure.parallel_chains = [
            chain for chain in self.graph.structure.parallel_chains if len(chain) > 1
        ]

        logging.info(
            f"[CognitiveGraphManager] Refreshed GraphStructure: "
            f"dep_order={len(self.graph.structure.dependency_order)}, "
            f"convergence={len(self.graph.structure.convergence_nodes)}, "
            f"parallel_chains={len(self.graph.structure.parallel_chains)}"
        )

    def _create_basic_graph(self, question: str) -> CognitiveGraph:
        """Create a basic fallback graph when parsing fails."""
        graph = CognitiveGraph()
        graph.original_question = question

        # Create the root node for the question.
        root = EntityNode(
            id="e_root",
            name="原始问题",
            node_type="start",
            cognitive_state="START",
        )
        graph.add_node(root)

        # Create a generic exploration target.
        target = EntityNode(
            id="e_answer",
            name="核心问题",
            node_type="placeholder",
            cognitive_state="UNKNOWN",
        )
        graph.add_node(target)

        # Create the inquiry edge.
        edge = RelationEdge(
            id="r_main",
            source_node_id="e_root",
            target_node_id="e_answer",
            relation_type="回答",
            inquiry_goal=question,
            state="to_solve",
        )
        graph.add_edge(edge)

        return graph
