"""
SectionPlanner - Sequential insight→evidence binding (Layer 2 of 3-layer pipeline).

Global sequential planning: sections are planned one by one in order.
Each section sees previous sections' insight plans (without citation markers)
but only its own subgraph findings and evidence index.

This eliminates the problems of parallel planning:
- Duplication: planner knows what previous sections already planned
- Disconnection: planner can build logical continuity across sections
- Homogeneity: planner can assign different narrative roles to each section

SectionWriter uses these insights as "Insight→Evidence Bindings" blueprint:
- Insight sentence = written WITHOUT citation
- Supporting evidence = written as separate fact sentences WITH [[n]]
"""

from .concurrency import check_cancelled
from .runtime.progress import report_progress
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .core import BaseLLMAdapter, Message
from .evidence_store import EvidenceRecord, EvidenceStore
from .outline_planner import InsightWithEvidence, ReportOutline, SectionPlan, UserRequirement


@dataclass
class SectionPlanResult:
    """Result of planning a single section."""

    section_id: str
    insights: List[InsightWithEvidence]


class SectionPlanner:
    """Plan insights for a single section (1 LLM call per section)."""

    def __init__(self, llm: BaseLLMAdapter, language: str = "en"):
        self.llm = llm
        self.language = language

    def plan_section(
        self,
        section: SectionPlan,
        section_evidence_index: str,
        subgraph_context: str,
        original_question: str,
        outline_summary: str,
        previous_plans: str = "",
        assigned_requirements_text: str = "",
    ) -> SectionPlanResult:
        """
        Generate insights with evidence bindings for one section.

        Args:
            section: The section plan from OutlinePlanner
            section_evidence_index: Evidence index string (ref_idx + criterion, no quotes)
            subgraph_context: Findings from relevant cognitive graph nodes
            original_question: User's original question
            outline_summary: Brief outline summary for context
            previous_plans: Formatted insight plans from previous sections (no [[n]] markers)
            assigned_requirements_text: Formatted user requirements assigned to this section

        Returns:
            SectionPlanResult with insights
        """
        prompt = self._build_prompt(
            section,
            section_evidence_index,
            subgraph_context,
            original_question,
            outline_summary,
            previous_plans,
            assigned_requirements_text,
        )
        messages = [Message(role="user", content=prompt)]

        logging.info(f"[SectionPlanner] Planning section {section.section_id}: {section.title}")
        check_cancelled()
        response = self.llm.chat(messages)

        insights = self._parse_insights(response.content, section_evidence_index)

        logging.info(f"[SectionPlanner] Section {section.section_id}: {len(insights)} insights planned")
        return SectionPlanResult(section_id=section.section_id, insights=insights)

    def _build_prompt(
        self,
        section: SectionPlan,
        section_evidence_index: str,
        subgraph_context: str,
        original_question: str,
        outline_summary: str,
        previous_plans: str = "",
        assigned_requirements_text: str = "",
    ) -> str:
        """Build the prompt for single-section insight planning."""
        answers_aspect_text = ""
        if section.answers_aspect:
            answers_aspect_text = f"\n**This section answers**: {section.answers_aspect}"
        if assigned_requirements_text:
            answers_aspect_text += f"\n{assigned_requirements_text}"

        # Extract actual ref_idx values from evidence index for dynamic example
        actual_refs = sorted(set(int(m.group(1)) for m in re.finditer(r"\[\[(\d+)\]\]", section_evidence_index)))

        insight_range = "4-8"
        if len(actual_refs) >= 3:
            ex1 = actual_refs[:3]
            ex2 = [actual_refs[1], actual_refs[-1]]
        elif len(actual_refs) == 2:
            ex1 = actual_refs
            ex2 = actual_refs
        elif len(actual_refs) == 1:
            ex1 = actual_refs
            ex2 = actual_refs
        else:
            ex1 = [1, 2, 3]
            ex2 = [2, 4]

        if self.language == "cn":
            return self._build_prompt_cn(
                section,
                section_evidence_index,
                subgraph_context,
                original_question,
                outline_summary,
                previous_plans,
                answers_aspect_text,
                actual_refs,
                insight_range,
                ex1,
                ex2,
            )
        else:
            return self._build_prompt_en(
                section,
                section_evidence_index,
                subgraph_context,
                original_question,
                outline_summary,
                previous_plans,
                answers_aspect_text,
                actual_refs,
                insight_range,
                ex1,
                ex2,
            )

    def _build_prompt_cn(
        self,
        section,
        section_evidence_index,
        subgraph_context,
        original_question,
        outline_summary,
        previous_plans,
        answers_aspect_text,
        actual_refs,
        insight_range,
        ex1,
        ex2,
    ) -> str:
        previous_plans_section = ""
        if previous_plans:
            previous_plans_section = f"""
## 前序章节的观点规划（了解全局进度，建立衔接）
{previous_plans}
"""

        return f"""你是一位高级研究分析师。请为报告章节规划**观点（insights）**及其对应的证据绑定。

## 用户问题
{original_question}

## 报告大纲
{outline_summary}

## 当前章节
**第 {section.section_id} 节：{section.title}**
{section.description}{answers_aspect_text}
{previous_plans_section}
## 认知图子图（本章节的研究发现）
{subgraph_context}

## 可用证据索引（ref_idx + 验收要点）
{section_evidence_index}

## 任务
为本章节规划 {insight_range} 个**观点（insights）**，每个观点绑定支撑它的证据。

### 什么是观点？
- 从证据中提炼出的**分析性判断**，可以是一到两句话
- 描述模式、趋势、因果关系、比较结论，**但不含具体数字**（数字由 SectionWriter 从证据中引用）
- ✅ 好的示例："AI市场正在快速整合，头部公司的市场份额显著上升"、"不同机构对中产阶层的定义差异导致规模估算差距悬殊"
- ❌ 不好的示例："市场份额从40%上升至65%"（具体数字不属于观点，应由证据提供）

### 核心原则
1. **回答用户**：优先围绕本章节 answers_aspect 生成 insight，确保用户问题的每个方面都有对应的 insight 正面回答。
2. **揭示发现**：如果证据中涌现出用户未直接询问但对理解问题有价值的模式、矛盾、因果关系或前瞻性判断，也应纳入 insight。用户想知道的要有，研究发现他需要知道的也要有。
3. **先答后纠**：如果研究发现用户问题的前提有误，先给出正面回答，再用证据论证纠偏。
4. **承上启下**：了解前序章节已覆盖的内容，使本章节的观点与前文形成逻辑递进。如果同一发现在本章节有不同的分析维度，可以从新角度引用。
5. **insight**：一到两句话的分析性判断。不带引用号，不含具体数字。
6. **evidence_ids**：支撑这个观点的证据。每个观点绑定 2-5 条证据。
7. **最大化覆盖**：每条证据至少出现在一个观点中。
8. **presentation_hint**（可选）：建议 SectionWriter 如何呈现这个观点。
   - `"table"`: 数据适合用表格对比呈现（如多年份/多机构的数值对比）
   - `"list"`: 适合用有序/无序列表分点罗列（如多个要点、步骤、分类）
   - `"comparison"`: 涉及跨来源/跨时期的比较分析
   - `"narrative"`: 适合叙述性论证

### 禁止事项
- **禁止把搜索未覆盖到的信息包装为 insight。** 认知图子图中可能包含"未找到数据"等搜索记录，这些是系统搜索过程的日志，不是写作素材。但如果证据本身记录了某个事实的缺失（如"该行业无强制披露要求"、"官方未公布统计"），且有 [[n]] 证据支撑，则可以作为 insight——因为"数据不存在"本身是有价值的发现。
- 总之：**有证据支撑的"缺失"是发现，没有证据的"没搜到"不是。**

### 要求
- evidence_ids 只能使用上面证据索引中出现的 [[n]] 编号（本章节可用: {", ".join(str(r) for r in actual_refs)}）
- 每个观点绑定 2-5 个 evidence_ids

## 输出格式
返回 JSON（不要 markdown 代码块）：
{{
  "insights": [
    {{
      "insight": "分析性判断（一到两句，不含具体数字）",
      "evidence_ids": {ex1},
      "presentation_hint": "table"
    }},
    {{
      "insight": "分析性判断（一到两句，不含具体数字）",
      "evidence_ids": {ex2}
    }}
  ]
}}"""

    def _build_prompt_en(
        self,
        section,
        section_evidence_index,
        subgraph_context,
        original_question,
        outline_summary,
        previous_plans,
        answers_aspect_text,
        actual_refs,
        insight_range,
        ex1,
        ex2,
    ) -> str:
        previous_plans_section = ""
        if previous_plans:
            previous_plans_section = f"""
## Previous Sections' Insight Plans (global progress, build continuity)
{previous_plans}
"""

        return f"""You are a senior research analyst. Plan **insights** and their evidence bindings for one report section.

## User Question
{original_question}

## Report Outline
{outline_summary}

## Current Section
**Section {section.section_id}: {section.title}**
{section.description}{answers_aspect_text}
{previous_plans_section}
## Cognitive Graph Subgraph (findings for this section)
{subgraph_context}

## Available Evidence Index (ref_idx + criterion)
{section_evidence_index}

## Task
Plan {insight_range} **insights** for this section, each bound to supporting evidence.

### What is an insight?
- An **analytical judgment** distilled from the evidence, one to two sentences
- Describes patterns, trends, causality, or comparative conclusions — **without specific numbers** (numbers are cited by SectionWriter from evidence)
- Good: "The AI market is consolidating rapidly, with leading players capturing an increasingly dominant share"
- Good: "Different institutions define middle class using vastly different criteria, leading to widely divergent size estimates"
- Bad: "Market share rose from 40% to 65%" (specific numbers belong in evidence-backed fact sentences, not insights)

### Core Principles
1. **Answer the user**: Prioritize generating insights around this section's answers_aspect — ensure every aspect of the user's question has a corresponding insight that directly addresses it.
2. **Reveal discoveries**: If the evidence reveals patterns, contradictions, causal relationships, or forward-looking judgments that the user didn't directly ask about but are valuable for understanding the topic, include them as insights too. What the user wants to know should be there, AND what the research shows they need to know.
3. **Answer first, correct second**: If research found the user's premise incorrect, first give a direct answer, then use evidence to argue the correction.
4. **Build continuity**: Be aware of what previous sections have covered, so this section's insights form a logical progression. If the same finding is relevant from a different analytical angle in this section, you may reference it from the new perspective.
5. **insight**: One to two sentences of analytical judgment. NO citation numbers, NO specific data.
6. **evidence_ids**: Evidence supporting this insight. Each insight binds 2-5 evidence items.
7. **Maximize coverage**: Every evidence item should appear in at least one insight.
8. **presentation_hint** (optional): Suggest how SectionWriter should present this insight.
   - `"table"`: Data is best presented as a comparison table (e.g., multi-year or multi-source numbers)
   - `"list"`: Best presented as a bulleted or numbered list (e.g., multiple factors, steps, categories)
   - `"comparison"`: Involves cross-source or cross-period comparative analysis
   - `"narrative"`: Best as narrative argumentation

### Prohibitions
- **Do NOT turn unfound information into insights.** The cognitive graph subgraph may contain search-process notes like "no data found" — these are system logs, not writing material. However, if evidence itself documents the absence of something (e.g., "no mandatory disclosure requirements in this industry", "government has not published official statistics") AND you can cite [[n]] evidence for it, then it CAN be an insight — because "data does not exist" is itself a valuable finding.
- In short: **an absence backed by evidence is a discovery; an absence due to search limitations is not.**

### Requirements
- evidence_ids MUST only use [[n]] numbers from the evidence index above (available for this section: {", ".join(str(r) for r in actual_refs)})
- Each insight should bind 2-5 evidence_ids

## Output Format
Return JSON (no markdown code blocks):
{{
  "insights": [
    {{
      "insight": "Analytical judgment (1-2 sentences, no specific numbers)",
      "evidence_ids": {ex1},
      "presentation_hint": "table"
    }},
    {{
      "insight": "Analytical judgment (1-2 sentences, no specific numbers)",
      "evidence_ids": {ex2}
    }}
  ]
}}"""

    def _parse_insights(
        self,
        response: str,
        section_evidence_index: str,
    ) -> List[InsightWithEvidence]:
        """Parse LLM response and filter invalid evidence references."""
        data = self._extract_json(response)
        if not data or "insights" not in data:
            logging.warning("[SectionPlanner] Failed to parse insights response")
            return []

        # Extract valid ref_idx set from the evidence index
        valid_refs = set()
        for m in re.finditer(r"\[\[(\d+)\]\]", section_evidence_index):
            valid_refs.add(int(m.group(1)))

        insights = []
        for ins in data["insights"]:
            if not isinstance(ins, dict):
                continue
            insight_text = ins.get("insight", "")
            if not isinstance(insight_text, str):
                insight_text = str(insight_text) if insight_text else ""
            evidence_ids = ins.get("evidence_ids", [])

            # Ensure evidence_ids is a list of integers
            if isinstance(evidence_ids, (int, float)):
                evidence_ids = [evidence_ids]
            elif isinstance(evidence_ids, str):
                # LLM returned "1,2,3" instead of [1,2,3]
                evidence_ids = [x.strip() for x in evidence_ids.split(",") if x.strip()]
            elif not isinstance(evidence_ids, list):
                evidence_ids = []
            evidence_ids = [int(e) for e in evidence_ids if isinstance(e, (int, float, str)) and str(e).isdigit()]

            # Filter out invalid references
            filtered_ids = [eid for eid in evidence_ids if eid in valid_refs]
            if filtered_ids != evidence_ids:
                removed = set(evidence_ids) - set(filtered_ids)
                if removed:
                    logging.warning(
                        f"[SectionPlanner] Removed invalid evidence refs {removed} from insight: {insight_text[:60]}..."
                    )

            if insight_text and filtered_ids:
                presentation_hint = ins.get("presentation_hint", "")
                if not isinstance(presentation_hint, str):
                    presentation_hint = ""
                insights.append(
                    InsightWithEvidence(
                        insight=insight_text,
                        evidence_ids=filtered_ids,
                        presentation_hint=presentation_hint,
                    )
                )

        return insights

    def _extract_json(self, text: str) -> Optional[Dict]:
        """Extract JSON from LLM response."""
        if not text or not isinstance(text, str):
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        clean_text = re.sub(r"```json\s*", "", text)
        clean_text = re.sub(r"```\s*", "", clean_text)

        try:
            return json.loads(clean_text.strip())
        except json.JSONDecodeError:
            pass

        json_match = re.search(r'\{[\s\S]*"insights"[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return None


class GlobalSectionPlanner:
    """Orchestrate sequential SectionPlanner calls for all sections.

    Plans sections one by one in order. Each section sees the insight plans
    from all previous sections (without [[n]] citation markers), enabling
    cross-section awareness for continuity and deduplication.
    """

    def __init__(
        self,
        llm: BaseLLMAdapter,
        language: str = "en",
        max_workers: int = 4,  # kept for interface compat, unused
    ):
        self.llm = llm
        self.language = language
        self.completed_section_ids = set()
        self.on_section = None

    def plan_all_sections(
        self,
        outline: ReportOutline,
        cognitive_graph: Dict[str, Any],
        evidence_store: EvidenceStore,
        original_question: str,
    ) -> ReportOutline:
        """
        Run SectionPlanner sequentially for all sections.

        Each section's planner sees the insight plans from all previous sections,
        enabling global awareness without information isolation.
        """
        if not outline.sections:
            return outline

        logging.info(f"[GlobalSectionPlanner] Planning {len(outline.sections)} sections sequentially")

        # Pre-build node mapping
        nodes = cognitive_graph.get("nodes", {})
        ref_to_node = {}
        for nid, ndata in nodes.items():
            ref_to_node[nid] = ndata
            ref_to_node[ndata.get("name", nid)] = ndata

        # Build outline summary (shared context for all sections)
        outline_summary = self._build_outline_summary(outline)

        # Build global audience/role requirements text (applied to ALL sections)
        audience_role_text = self._format_audience_role(outline.user_requirements)

        # Sequential planning with accumulated context
        planner = SectionPlanner(llm=self.llm, language=self.language)
        completed_plans: List[Dict] = []  # [{section_id, title, insights: [str]}]
        total_insights = 0

        for section_index, section in enumerate(outline.sections, 1):
            if section.section_id in self.completed_section_ids:
                completed_plans.append({"section_id": section.section_id, "title": section.title,
                                        "insights": [item.insight for item in section.insights]})
                total_insights += len(section.insights)
                continue
            report_progress("planning_sections", section_index=section_index, section_total=len(outline.sections),
                            section_id=section.section_id, title=section.title)
            # Build previous plans context (no [[n]] markers)
            previous_plans = self._format_previous_plans(completed_plans)

            # Extract subgraph context
            subgraph_context = self._extract_subgraph(cognitive_graph, section.relevant_node_ids)

            # Collect task names from nodes
            task_names = []
            for node_ref in section.relevant_node_ids:
                node_data = ref_to_node.get(node_ref)
                if node_data:
                    task_names.extend(node_data.get("related_tasks", []))

            # Deduplicate
            seen = set()
            unique_tasks = []
            for t in task_names:
                if t not in seen:
                    seen.add(t)
                    unique_tasks.append(t)

            # Get evidence and build index string (ref_idx + criterion)
            section_evidence = evidence_store.get_evidence_for_nodes(unique_tasks)
            evidence_index = self._build_evidence_index(section_evidence)

            # Plan this section
            try:
                result = planner.plan_section(
                    section=section,
                    section_evidence_index=evidence_index,
                    subgraph_context=subgraph_context,
                    original_question=original_question,
                    outline_summary=outline_summary,
                    previous_plans=previous_plans,
                    assigned_requirements_text=audience_role_text,
                )

                if result.insights:
                    section.insights = result.insights
                    total_insights += len(result.insights)

                    # Record for next sections' context
                    completed_plans.append(
                        {
                            "section_id": section.section_id,
                            "title": section.title,
                            "insights": [ins.insight for ins in result.insights],
                        }
                    )
                else:
                    logging.warning(
                        f"[GlobalSectionPlanner] Section {section.section_id}: "
                        "no insights (SectionWriter will use full evidence fallback)"
                    )
                    completed_plans.append(
                        {
                            "section_id": section.section_id,
                            "title": section.title,
                            "insights": [],
                        }
                    )
            except Exception as e:
                logging.error(f"[GlobalSectionPlanner] Section {section.section_id} failed: {e}")
                completed_plans.append(
                    {
                        "section_id": section.section_id,
                        "title": section.title,
                        "insights": [],
                    }
                )

            self.completed_section_ids.add(section.section_id)
            if self.on_section:
                self.on_section()

        logging.info(
            f"[GlobalSectionPlanner] Complete: {total_insights} total insights across {len(outline.sections)} sections"
        )
        return outline

    def _format_audience_role(
        self,
        user_requirements: List[UserRequirement],
    ) -> str:
        """Format audience/role requirements for prompt display (global, all sections)."""
        if not user_requirements:
            return ""

        lines = []
        for req in user_requirements:
            if req.type in ("audience", "role"):
                lines.append(f"- [{req.type}] {req.requirement}")

        if not lines:
            return ""

        if self.language == "cn":
            header = "**目标受众与角色要求**（适用于全部章节）："
        else:
            header = "**Audience & Role Requirements** (applies to all sections):"
        return f"{header}\n" + "\n".join(lines)

    def _format_previous_plans(self, completed_plans: List[Dict]) -> str:
        """Format previous sections' insight plans for context.

        Strips [[n]] markers from insight text to avoid citation interference.
        """
        if not completed_plans:
            return ""

        lines = []
        for plan in completed_plans:
            section_id = plan["section_id"]
            title = plan["title"]
            insights = plan["insights"]

            if insights:
                lines.append(f"**Section {section_id}: {title}**")
                for i, ins in enumerate(insights, 1):
                    # Strip any [[n]] markers that might have leaked in
                    clean_ins = re.sub(r"\[\[\d+\]\]", "", ins).strip()
                    lines.append(f"  {i}. {clean_ins}")
                lines.append("")

        return "\n".join(lines)

    def _build_outline_summary(self, outline: ReportOutline) -> str:
        """Build a brief outline summary for context."""
        lines = [f"Theme: {outline.overall_theme}", ""]
        for s in outline.sections:
            lines.append(f"- {s.section_id}. {s.title}")
        return "\n".join(lines)

    def _extract_subgraph(
        self,
        cognitive_graph: Dict[str, Any],
        node_refs: List[str],
    ) -> str:
        """Extract findings from specified cognitive graph nodes."""
        nodes = cognitive_graph.get("nodes", {})
        if not nodes:
            return "(No cognitive graph data available)"

        ref_to_node = {}
        for nid, ndata in nodes.items():
            ref_to_node[nid] = ndata
            ref_to_node[ndata.get("name", nid)] = ndata

        lines = []
        processed_ids = set()

        for node_ref in node_refs:
            node_data = ref_to_node.get(node_ref)
            if not node_data:
                continue

            node_id = node_data.get("id", node_ref)
            if node_id in processed_ids:
                continue
            processed_ids.add(node_id)

            node_name = node_data.get("name", node_ref)
            state = node_data.get("cognitive_state", node_data.get("state", "UNKNOWN"))
            lines.append(f"**{node_name}** [{state}]")

            # unwrap new dict-form entries
            def _unwrap(entry):
                if isinstance(entry, dict) and "value" in entry:
                    return entry["value"]
                return entry

            item_findings = node_data.get("item_findings", {})
            if isinstance(item_findings, dict):
                for entity_name, attrs in item_findings.items():
                    if isinstance(attrs, dict):
                        for attr_name, entry in attrs.items():
                            attr_value = _unwrap(entry)
                            val = str(attr_value)
                            if len(val) > 300:
                                val = val[:300] + "..."
                            lines.append(f"- {entity_name}.{attr_name}: {val}")

            cross_item_findings = node_data.get("cross_item_findings", {})
            if isinstance(cross_item_findings, dict):
                for aspect, entry in cross_item_findings.items():
                    description = _unwrap(entry)
                    desc = str(description)
                    if len(desc) > 300:
                        desc = desc[:300] + "..."
                    lines.append(f"- {aspect}: {desc}")

            unexpected = node_data.get("unexpected_discoveries", [])
            if unexpected and isinstance(unexpected, list):
                lines.append("- **Unexpected discoveries:**")
                for d in unexpected:
                    # unwrap {value, task} dict; legacy str passthrough
                    val = _unwrap(d) if isinstance(d, dict) else d
                    d_str = val if isinstance(val, str) else str(val)
                    lines.append(f"  - {d_str}")

            # Contradictions: render full (no truncation) — drives insight→
            # evidence binding when prior search round overturned earlier claims.
            contradictions = node_data.get("contradictions", []) or []
            if contradictions:
                lines.append(f"- **Contradictions ({len(contradictions)}):**")
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

            lines.append("")

        return "\n".join(lines) if lines else "(No findings for these nodes)"

    def _build_evidence_index(
        self,
        evidence_records: Dict[int, List[EvidenceRecord]],
    ) -> str:
        """Build evidence index string: [[n]] (criterion) — NO quotes."""
        if not evidence_records:
            return "(No evidence available for this section)"

        lines = []
        for ref_idx in sorted(evidence_records.keys()):
            for r in evidence_records[ref_idx]:
                lines.append(f"[[{ref_idx}]] ({r.criterion})")

        return "\n".join(lines)
