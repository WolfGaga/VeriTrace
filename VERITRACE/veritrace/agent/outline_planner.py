"""
OutlinePlanner - Generate a structured report outline from cognitive graph.

Layer 1 of 3-layer pipeline: structural planning only (1 LLM call).
- Outputs: section titles + descriptions + answers_aspect + relevant_node_ids
- Does NOT handle insights/evidence bindings (delegated to SectionPlanner)
"""

from .concurrency import check_cancelled
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core import BaseLLMAdapter, Message
from .evidence_store import EvidenceStore


@dataclass
class InsightWithEvidence:
    """An insight/claim with its supporting evidence IDs."""

    insight: str  # The insight or viewpoint, in one or two sentences.
    evidence_ids: List[int]  # Supporting evidence reference IDs.
    presentation_hint: str = ""  # Optional: "table" | "comparison" | "narrative"

    def to_dict(self) -> Dict:
        d = {"insight": self.insight, "evidence_ids": self.evidence_ids}
        if self.presentation_hint:
            d["presentation_hint"] = self.presentation_hint
        return d


@dataclass
class UserRequirement:
    """A user requirement about audience or role extracted from the original question."""

    id: int  # Unique ID (1-based)
    requirement: str  # The specific requirement text
    type: str  # "audience" | "role"

    def to_dict(self) -> Dict:
        return {"id": self.id, "requirement": self.requirement, "type": self.type}


@dataclass
class SectionPlan:
    """A single section in the report outline."""

    section_id: str  # "1", "1.1", "2" etc.
    title: str
    description: str  # What this section should cover
    relevant_node_ids: List[str]  # Cognitive graph node IDs
    insights: List[InsightWithEvidence] = field(default_factory=list)  # Filled by SectionPlanner
    relevant_criteria: List[str] = field(default_factory=list)
    answers_aspect: str = ""  # Which aspect of the user's question this section addresses


@dataclass
class ReportOutline:
    """Complete report outline."""

    sections: List[SectionPlan]
    overall_theme: str  # Overall theme summary
    user_requirements: List[UserRequirement] = field(default_factory=list)


class OutlinePlanner:
    """Generate a structured report outline from cognitive graph (1 LLM call)."""

    def __init__(self, llm: BaseLLMAdapter, language: str = "en", **kwargs):
        self.llm = llm
        self.language = language

    def plan_outline(
        self,
        original_question: str,
        cognitive_graph: Dict[str, Any],
        evidence_store: EvidenceStore,
    ) -> ReportOutline:
        """
        Generate a report outline from the cognitive graph (single LLM call).

        Args:
            original_question: The user's original question
            cognitive_graph: Remapped cognitive graph dict
            evidence_store: EvidenceStore with all collected evidence

        Returns:
            ReportOutline with section plans (insights empty — filled by SectionPlanner)
        """
        MAX_OUTLINE_ATTEMPTS = 3

        graph_summary = self._build_graph_summary(cognitive_graph, evidence_store)
        prompt = self._build_prompt(original_question, graph_summary)
        messages = [Message(role="user", content=prompt)]

        outline = None
        for attempt in range(1, MAX_OUTLINE_ATTEMPTS + 1):
            logging.info(f"[OutlinePlanner] Generating report outline (attempt {attempt}/{MAX_OUTLINE_ATTEMPTS})...")
            check_cancelled()
            response = self.llm.chat(messages)
            data = self._extract_json(response.content)
            if data and data.get("sections"):
                outline = self._parse_response(response.content, cognitive_graph, evidence_store)
                if outline.sections:
                    break
            logging.warning(
                f"[OutlinePlanner] Attempt {attempt} parse failed; retrying"
                if attempt < MAX_OUTLINE_ATTEMPTS
                else f"[OutlinePlanner] All {MAX_OUTLINE_ATTEMPTS} attempts failed; using fallback"
            )

        if outline is None or not outline.sections:
            outline = self._fallback_outline(cognitive_graph, evidence_store)

        self._validate_coverage(outline, cognitive_graph)

        logging.info(f"[OutlinePlanner] Outline generated: {len(outline.sections)} sections")
        return outline

    def _build_graph_summary(
        self,
        cognitive_graph: Dict[str, Any],
        evidence_store: EvidenceStore,
    ) -> str:
        """Build a structural summary of the cognitive graph for the prompt.

        Shows node info + findings + evidence ref_idx index for structural planning.
        """
        nodes = cognitive_graph.get("nodes", {})
        if not nodes:
            return "(No cognitive graph nodes available)"

        # Filter out root nodes with no incoming edges (initial known anchors with no research content)
        edges = cognitive_graph.get("edges", {})
        nodes_with_incoming = set()
        for edge_data in edges.values():
            target = edge_data.get("target_node_id")
            if target:
                nodes_with_incoming.add(target)

        lines = []
        for node_id, node_data in nodes.items():
            has_findings = bool(node_data.get("item_findings")) or bool(node_data.get("cross_item_findings"))
            if node_id not in nodes_with_incoming and not node_data.get("related_tasks") and not has_findings:
                continue  # Skip root anchor nodes that have no evidence
            node_name = node_data.get("name", node_id)
            state = node_data.get("cognitive_state", node_data.get("state", "UNKNOWN"))

            # Collect findings
            # Accept wrapped values, plain strings and lists in saved evidence.
            item_findings = node_data.get("item_findings", {})
            cross_item_findings = node_data.get("cross_item_findings", {})

            def _unwrap(entry):
                if isinstance(entry, dict) and "value" in entry:
                    return entry["value"]
                return entry

            findings_summary = []
            if isinstance(item_findings, dict):
                for entity_name, attrs in item_findings.items():
                    if isinstance(attrs, dict):
                        for attr, entry in attrs.items():
                            val = _unwrap(entry)
                            findings_summary.append(f"{entity_name}.{attr}: {val}")

            if isinstance(cross_item_findings, dict):
                for aspect, entry in cross_item_findings.items():
                    desc = _unwrap(entry)
                    findings_summary.append(f"{aspect}: {desc}")

            # Unexpected discoveries (valuable findings beyond criteria)
            unexpected = node_data.get("unexpected_discoveries", [])

            # Get evidence ref_idx + criterion index for structural planning
            related_tasks = node_data.get("related_tasks", [])
            if related_tasks:
                node_evidence = evidence_store.get_evidence_for_nodes(related_tasks)
            else:
                node_evidence = {}

            evidence_index = []
            for ref_idx, records in sorted(node_evidence.items()):
                for r in records:
                    evidence_index.append(f"[[{ref_idx}]] ({r.criterion})")

            lines.append(f"### Node ID: `{node_id}` | Name: {node_name} [{state}]")

            if findings_summary:
                lines.append("**Findings:**")
                for f in findings_summary:
                    lines.append(f"  - {f}")

            if unexpected:
                lines.append("**Unexpected Discoveries:**")
                for d in unexpected:
                    # unwrap {value, task} dict; legacy str passthrough
                    val = _unwrap(d) if isinstance(d, dict) else d
                    d_str = val if isinstance(val, str) else str(val)
                    lines.append(f"  - {d_str}")

            # Contradictions: research-uncovered conflicts between sources.
            # Render full (no truncation) — these are high-signal Insight-layer
            # material for the outline structurer.
            contradictions = node_data.get("contradictions", []) or []
            if contradictions:
                lines.append(f"**Contradictions ({len(contradictions)}):**")
                for c in contradictions:
                    if not isinstance(c, dict):
                        continue
                    crit = c.get("criterion", "")
                    old_claim = c.get("old_claim") or ""
                    new_claim = c.get("new_claim") or ""
                    kept = c.get("kept", "")
                    resolution = c.get("resolution") or ""
                    oq = c.get("old_quality") or {}
                    nq = c.get("new_quality") or {}
                    lines.append(f"  - **{crit}** [kept={kept}]")
                    oq_s = f" (CR={oq.get('cr')}, AAP={oq.get('aap')})" if oq else ""
                    nq_s = f" (CR={nq.get('cr')}, AAP={nq.get('aap')})" if nq else ""
                    lines.append(f"    - OLD: {old_claim}{oq_s}")
                    lines.append(f"    - NEW: {new_claim}{nq_s}")
                    if resolution:
                        lines.append(f"    - resolution: {resolution}")

            if evidence_index:
                lines.append(f"**Evidence available ({len(evidence_index)} items):**")
                lines.append(f"  {', '.join(evidence_index)}")

            lines.append("")

        return "\n".join(lines)

    def _build_prompt(self, original_question: str, graph_summary: str) -> str:
        """Build the outline generation prompt (structure only, no insights)."""
        if self.language == "cn":
            return f"""你是一位专业的研究报告结构设计师。请根据以下信息设计一份报告大纲。

## 用户问题
{original_question}

## 认知图摘要
{graph_summary}

## 任务
1. **逐条提取用户要求**：从用户问题中提取所有具体的指令和约束，逐条列出。分为六类：
   - **content**：需要回答的子问题/子方面（如"分析技术路线"、"评估商业化前景"）
   - **structure**：用户指定的组织方式（如"按地区分类"、"纵向对比"、"从理论到实践"）
   - **output**：用户期望的输出形式（如"给出排名"、"制定方案"、"推荐最优的3个"、"写综述"）
   - **constraint**：具体的限制条件（如"前五名"、"2020年以来"、"仅限亚太地区"、"必须包含成本分析"）
   - **audience**：目标读者或语气要求（如"面向董事会"、"适合初学者"、"投资级分析"）
   - **role**：用户指定的角色扮演（如"作为资深产品经理"、"作为K-12教育专家"）
   - ⚠️ 只提取用户**明确写出**的要求，不要推断隐含要求
2. **审视研究发现**：浏览认知图摘要，识别用户可能未预料到但对理解问题至关重要的发现（如数据矛盾、意外模式、深层因果等）
3. **设计大纲**：章节既要覆盖用户的子问题，也要为研究中发现的重要洞察留出空间。大纲结构应体现 structure/output/constraint 类要求

### 结构要求
1. **回答层（必须）**：确保用户问题的每个子方面都有对应章节正面回答（answers_aspect）
2. **洞察层（鼓励）**：如果研究发现了用户未直接询问但对理解问题有价值的内容（跨维度的规律、深层原因、数据矛盾、前瞻性判断等），应设计章节呈现这些发现
3. **锚定用户原始术语**：章节标题必须使用用户问题中的原始概念和术语，即使研究发现这些概念有误。例如用户问"味精为什么致癌"，章节标题应包含"味精致癌"而非直接替换为"谷氨酸钠的安全性评估"
4. 章节数量建议 4-10 个（证据量大、涉及多个实体时可适当增加）
5. **充分覆盖节点**：每个认知图节点至少被一个章节引用
6. **完整性检查**：所有章节合在一起，应该既完整回答用户的问题，又呈现研究过程中发现的重要洞察
7. **禁止"数据挑战"章节**：不要规划围绕"数据源局限性"、"信息透明度挑战"等搜索未覆盖内容的章节。认知图中的"未找到数据"是搜索日志，不是报告主题。聚焦于利用已有证据回答用户问题并给出深度分析

## ⚠️ CRITICAL
- relevant_node_ids: 使用 Node ID（如 `e_xxx`）

## 输出格式
返回 JSON（不要 markdown 代码块）：
{{
  "overall_theme": "报告整体主题",
  "user_requirements": [
    {{"id": 1, "requirement": "具体的用户要求描述", "type": "content"}},
    {{"id": 2, "requirement": "具体的用户要求描述", "type": "structure"}},
    {{"id": 3, "requirement": "具体的用户要求描述", "type": "audience"}}
  ],
  "sections": [
    {{
      "section_id": "1",
      "title": "章节标题",
      "description": "章节内容描述",
      "answers_aspect": "本章节回答用户问题的哪个方面/子问题（若为洞察型章节，写明揭示了什么）",
      "relevant_node_ids": ["e_xxx"]
    }}
  ]
}}"""
        else:
            return f"""You are a professional research report architect. Design a report outline structure.

## User Question
{original_question}

## Cognitive Graph Summary
{graph_summary}

## Task
1. **Extract user requirements item by item**: Identify ALL specific instructions and constraints from the user's question, listed one by one. Categorize each as:
   - **content**: Sub-questions/aspects to answer (e.g., "analyze technical roadmaps", "assess commercialization prospects")
   - **structure**: How the user wants it organized (e.g., "by region", "longitudinal comparison", "from theory to practice")
   - **output**: Expected output format (e.g., "provide a ranking", "develop a plan", "recommend the top 3", "write a review")
   - **constraint**: Specific limits (e.g., "top 5", "since 2020", "Asia-Pacific only", "must include cost analysis")
   - **audience**: Target reader or tone (e.g., "boardroom-level", "for beginners", "investment-grade analysis")
   - **role**: Persona the user wants adopted (e.g., "acting as a senior product manager", "as a K-12 education expert")
   - ⚠️ Only extract requirements the user **explicitly stated**, do NOT infer implicit ones
2. **Review research findings**: Scan the cognitive graph summary for discoveries the user may not have anticipated but are crucial to understanding the topic (data contradictions, unexpected patterns, deeper causes, etc.)
3. **Design the outline**: Sections should cover the user's sub-questions AND make room for important research-driven insights. The outline structure should reflect structure/output/constraint requirements

### Structure Requirements
1. **Answer layer (required)**: Ensure every sub-aspect of the user's question has a corresponding section that directly addresses it (answers_aspect)
2. **Insight layer (encouraged)**: If research uncovered findings the user didn't directly ask about but that are valuable for understanding the topic (cross-cutting patterns, deeper causes, data contradictions, forward-looking judgments), design sections to present these findings
3. **Anchor to user's original terms**: Section titles MUST use the original concepts and terminology from the user's question, even if research found them incorrect. E.g. if user asks "Why does MSG cause cancer", the section title should include "MSG causes cancer" rather than replacing it with "Glutamate safety assessment"
4. Recommended 4-10 sections total (more sections when evidence volume is large or multiple entities are involved)
5. **Full node coverage**: Every cognitive graph node should be referenced by at least one section
6. **Completeness check**: All sections combined should both fully answer the user's question AND present important insights discovered during research
7. **No "data challenges" sections**: Do NOT plan sections about "data source limitations", "information transparency challenges", or other search-coverage gaps. "No data found" entries in the cognitive graph are search logs, not report topics. Focus on using available evidence to answer the user's question with deep analysis

## ⚠️ CRITICAL
- relevant_node_ids: Use Node ID (like `e_xxx`)

## Output Format
Return JSON (no markdown code blocks):
{{
  "overall_theme": "Overall report theme",
  "user_requirements": [
    {{"id": 1, "requirement": "specific user requirement", "type": "content"}},
    {{"id": 2, "requirement": "specific user requirement", "type": "structure"}},
    {{"id": 3, "requirement": "specific user requirement", "type": "audience"}}
  ],
  "sections": [
    {{
      "section_id": "1",
      "title": "Section title",
      "description": "Section content description",
      "answers_aspect": "Which aspect/sub-question this addresses (for insight sections, state what it reveals)",
      "relevant_node_ids": ["e_xxx"]
    }}
  ]
}}"""

    def _parse_response(
        self,
        response: str,
        cognitive_graph: Dict[str, Any],
        evidence_store: EvidenceStore,
    ) -> ReportOutline:
        """Parse LLM response into a ReportOutline (insights ignored at this layer)."""
        data = self._extract_json(response)

        if not data or "sections" not in data:
            logging.warning("[OutlinePlanner] Failed to parse outline, using fallback")
            return self._fallback_outline(cognitive_graph, evidence_store)

        # Parse user_requirements (all 6 types — audience/role passed to SectionWriter, others inform outline design)
        user_requirements = []
        raw_reqs = data.get("user_requirements", [])
        if isinstance(raw_reqs, list):
            for r in raw_reqs:
                if isinstance(r, dict) and r.get("requirement"):
                    req_type = str(r.get("type", "content"))
                    if req_type not in ("content", "structure", "output", "constraint", "audience", "role"):
                        req_type = "content"
                    user_requirements.append(
                        UserRequirement(
                            id=int(r.get("id", len(user_requirements) + 1)),
                            requirement=str(r["requirement"]),
                            type=req_type,
                        )
                    )

        if user_requirements:
            logging.info(f"[OutlinePlanner] Extracted {len(user_requirements)} user requirements")
            for req in user_requirements:
                logging.info(f"  [{req.type}] #{req.id}: {req.requirement}")

        sections = []
        for s in data["sections"]:
            if not isinstance(s, dict):
                continue
            # Ensure relevant_node_ids is a list
            node_ids = s.get("relevant_node_ids", [])
            if isinstance(node_ids, str):
                node_ids = [x.strip() for x in node_ids.split(",") if x.strip()]
            elif not isinstance(node_ids, list):
                node_ids = [str(node_ids)] if node_ids else []

            section = SectionPlan(
                section_id=str(s.get("section_id", str(len(sections) + 1))),
                title=str(s.get("title", f"Section {len(sections) + 1}")),
                description=str(s.get("description", "")),
                relevant_node_ids=node_ids,
                insights=[],  # Left empty — SectionPlanner fills this
                answers_aspect=str(s.get("answers_aspect", "")),
            )
            sections.append(section)

            logging.info(f"[OutlinePlanner] Section {section.section_id}: {section.title}")

        if not sections:
            return self._fallback_outline(cognitive_graph, evidence_store)

        return ReportOutline(
            sections=sections,
            overall_theme=data.get("overall_theme", ""),
            user_requirements=user_requirements,
        )

    def _validate_coverage(self, outline: ReportOutline, cognitive_graph: Dict[str, Any]) -> None:
        """Check that all nodes with findings are covered by at least one section.

        Supports both node_id and node_name matching for robustness.
        """
        nodes = cognitive_graph.get("nodes", {})

        # Build bidirectional mapping: id <-> name
        id_to_name = {}
        name_to_id = {}
        for node_id, node_data in nodes.items():
            node_name = node_data.get("name", node_id)
            id_to_name[node_id] = node_name
            name_to_id[node_name] = node_id

        # Collect node_ids that have findings
        # E1 fix: a node whose only content is unexpected_discoveries / contradictions
        # (insight-only nodes) must also be protected by the coverage backstop, else it
        # silently falls out of the report when the outline LLM forgets it.
        nodes_with_findings = set()
        for node_id, node_data in nodes.items():
            item_findings = node_data.get("item_findings", {})
            cross_item_findings = node_data.get("cross_item_findings", {})
            unexpected = node_data.get("unexpected_discoveries", [])
            contradictions = node_data.get("contradictions", [])
            if item_findings or cross_item_findings or unexpected or contradictions:
                nodes_with_findings.add(node_id)

        # Normalize covered nodes to node_ids (handle both id and name)
        covered_node_ids = set()
        for section in outline.sections:
            normalized_ids = []
            for ref in section.relevant_node_ids:
                if ref in nodes:
                    # It's already a node_id
                    covered_node_ids.add(ref)
                    normalized_ids.append(ref)
                elif ref in name_to_id:
                    # It's a node_name, convert to node_id
                    node_id = name_to_id[ref]
                    covered_node_ids.add(node_id)
                    normalized_ids.append(node_id)
                    logging.debug(f"[OutlinePlanner] Normalized node name '{ref}' to id '{node_id}'")
                else:
                    # Unknown reference, keep as-is
                    normalized_ids.append(ref)
                    logging.warning(f"[OutlinePlanner] Unknown node reference: '{ref}'")
            # Update section with normalized node_ids
            section.relevant_node_ids = normalized_ids

        # Check for uncovered nodes
        uncovered = nodes_with_findings - covered_node_ids
        if uncovered:
            logging.warning(f"[OutlinePlanner] Uncovered nodes with findings: {uncovered}. Appending to last section.")
            # Add uncovered node_ids to the last section
            if outline.sections:
                outline.sections[-1].relevant_node_ids.extend(sorted(uncovered))

    def _fallback_outline(
        self,
        cognitive_graph: Dict[str, Any],
        evidence_store: EvidenceStore,
    ) -> ReportOutline:
        """Generate a simple fallback outline when LLM parsing fails."""
        nodes = cognitive_graph.get("nodes", {})
        all_node_ids = list(nodes.keys())

        sections = [
            SectionPlan(
                section_id="1",
                title="Overview and Background",
                description="Introduction to the topic and key context.",
                relevant_node_ids=all_node_ids[:2] if all_node_ids else [],
            ),
            SectionPlan(
                section_id="2",
                title="Detailed Analysis",
                description="In-depth analysis of findings from all search results.",
                relevant_node_ids=all_node_ids,
            ),
            SectionPlan(
                section_id="3",
                title="Conclusions and Key Findings",
                description="Summary of key findings and conclusions.",
                relevant_node_ids=all_node_ids[-2:] if all_node_ids else [],
            ),
        ]

        return ReportOutline(
            sections=sections,
            overall_theme="Research analysis report",
        )

    def _extract_json(self, text: str) -> Optional[Dict]:
        """Extract JSON from LLM response."""
        if not text or not isinstance(text, str):
            return None
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Remove markdown code blocks
        clean_text = re.sub(r"```json\s*", "", text)
        clean_text = re.sub(r"```\s*", "", clean_text)

        try:
            return json.loads(clean_text.strip())
        except json.JSONDecodeError:
            pass

        # Try regex extraction
        json_match = re.search(r'\{[\s\S]*"sections"[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return None
