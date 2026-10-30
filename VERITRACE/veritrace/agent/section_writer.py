"""
SectionWriter - Generate report sections using cognitive graph findings + evidence.

Layer 3 of 3-layer pipeline:
- Receives insights from SectionPlanner (Layer 2) with evidence bindings
- Only sees evidence quotes that were bound by SectionPlanner (not full evidence)
- Uses TWO-STEP process: insight sentences (no citation) + fact sentences (with [[n]])

Key design principles:
- SectionWriter does NOT see Searcher prose - only subgraph findings + evidence quotes
- Each section receives: outline context + insight→evidence bindings + evidence table + previous text
- Citation rules enforce single-source [[n]] per claim, evidence-first expression
"""

from .concurrency import check_cancelled
from .runtime.progress import report_progress
import logging
import re
from typing import Any, Dict, List

from .core import BaseLLMAdapter, Message
from .core.llm_adapter import PayloadTooLargeError
from .evidence_store import EvidenceRecord, EvidenceStore
from .outline_planner import ReportOutline, SectionPlan, UserRequirement


class SectionWriter:
    """Generate report by writing sections sequentially with evidence."""

    def __init__(
        self,
        llm: BaseLLMAdapter,
        language: str = "en",
        include_search_refs: bool = True,
    ):
        self.llm = llm
        self.language = language
        self.include_search_refs = include_search_refs
        self.section_texts: Dict[str, str] = {}
        self.completed_sections = {}
        self.on_section = None

    def write_report(
        self,
        outline: ReportOutline,
        cognitive_graph: Dict[str, Any],
        evidence_store: EvidenceStore,
        original_question: str,
        cited_references: Dict[int, Dict],
    ) -> str:
        """
        Main flow: write each section sequentially, then append references.

        Args:
            outline: Report outline with section plans
            cognitive_graph: Remapped cognitive graph dict
            evidence_store: EvidenceStore with all evidence records
            original_question: User's original question
            cited_references: {ref_idx: {url, title, quality}}

        Returns:
            Complete report text with references
        """
        logging.info(f"[SectionWriter] Starting report writing: {len(outline.sections)} sections")

        written_text = ""
        self.section_texts.clear()

        # Build global audience/role requirements text (applied to ALL sections)
        audience_role_text = self._format_audience_role(outline.user_requirements)

        # Pre-build node mapping once (used by evidence lookup below)
        nodes = cognitive_graph.get("nodes", {})
        ref_to_node = {}
        for nid, ndata in nodes.items():
            ref_to_node[nid] = ndata  # by ID
            ref_to_node[ndata.get("name", nid)] = ndata  # by name

        for i, section in enumerate(outline.sections):
            check_cancelled()
            if section.section_id in self.completed_sections:
                section_text = self.completed_sections[section.section_id]
                self.section_texts[section.section_id] = section_text
                written_text += section_text + "\n\n"
                continue
            report_progress("writing_section", section_index=i + 1, section_total=len(outline.sections),
                            section_id=section.section_id, title=section.title)
            logging.info(f"[SectionWriter] Writing section {section.section_id}: {section.title}")

            # Get task names from cognitive graph nodes' related_tasks field
            task_names = []
            resolved_node_ids = []  # Track which nodes we actually resolved

            for node_ref in section.relevant_node_ids:
                node_data = ref_to_node.get(node_ref)
                if node_data:
                    resolved_node_ids.append(node_ref)
                    related_tasks = node_data.get("related_tasks", [])
                    task_names.extend(related_tasks)
                else:
                    logging.warning(f"[SectionWriter] Section {section.section_id}: Node ref '{node_ref}' not found")

            # Deduplicate task names while preserving order
            seen = set()
            unique_task_names = []
            for t in task_names:
                if t not in seen:
                    seen.add(t)
                    unique_task_names.append(t)
            task_names = unique_task_names

            # Extract evidence for this section's nodes (using task names)
            section_evidence = evidence_store.get_evidence_for_nodes(task_names)

            # Log detailed evidence mapping info
            evidence_count = sum(len(v) for v in section_evidence.values())
            logging.debug(
                f"[SectionWriter] Section {section.section_id} evidence mapping: "
                f"node_refs={section.relevant_node_ids}, "
                f"resolved={len(resolved_node_ids)}/{len(section.relevant_node_ids)}, "
                f"task_names={task_names}, "
                f"evidence={len(section_evidence)} refs ({evidence_count} records)"
            )

            # Evidence filtering: if SectionPlanner provided insights, only pass bound evidence
            if section.insights:
                bound_refs = set()
                for ins in section.insights:
                    bound_refs.update(ins.evidence_ids)
                filtered_evidence = {k: v for k, v in section_evidence.items() if k in bound_refs}
                filtered_count = sum(len(v) for v in filtered_evidence.values())
                logging.info(
                    f"[SectionWriter] Section {section.section_id}: filtered evidence "
                    f"{evidence_count} -> {filtered_count} records (bound by SectionPlanner)"
                )
                section_evidence_for_writer = filtered_evidence
            else:
                # Fallback: no insights, pass full evidence
                section_evidence_for_writer = section_evidence

            # Warn if no evidence found
            if not section_evidence_for_writer:
                logging.warning(
                    f"[SectionWriter] Section {section.section_id}: No evidence found. "
                    f"Check if related_tasks are properly populated in cognitive graph."
                )

            # Write section
            section_text = self.write_section(
                section=section,
                evidence_records=section_evidence_for_writer,
                previous_text=written_text,
                full_outline=outline,
                original_question=original_question,
                assigned_requirements_text=audience_role_text,
            )

            self.section_texts[section.section_id] = section_text
            written_text += section_text + "\n\n"
            if self.on_section:
                self.on_section(dict(self.section_texts))

            logging.info(f"[SectionWriter] Section {section.section_id} complete: {len(section_text)} chars")

        # Generate executive summary and prepend to report
        report_progress("writing_summary")
        exec_summary = self._generate_executive_summary(written_text, original_question)
        if exec_summary:
            written_text = exec_summary + "\n\n" + written_text

        # Append reference list
        report_progress("finalizing")
        report = self._append_references(written_text.strip(), cited_references)

        logging.info(f"[SectionWriter] Report complete: {len(report)} chars")
        return report

    # Evidence quote truncation levels for payload-too-large retry
    QUOTE_TRUNCATION_LEVELS = [500, 400, 300, 200]

    def write_section(
        self,
        section: SectionPlan,
        evidence_records: Dict[int, List[EvidenceRecord]],
        previous_text: str,
        full_outline: ReportOutline,
        original_question: str,
        assigned_requirements_text: str = "",
    ) -> str:
        """
        Write a single section via LLM call.
        On PayloadTooLargeError, progressively reduce evidence quote length and retry.

        Args:
            section: The section plan
            evidence_records: Evidence records for this section {ref_idx: [records]}
            previous_text: Text written so far
            full_outline: Complete outline for context
            original_question: User's original question
            assigned_requirements_text: Formatted user requirements for this section

        Returns:
            Section text with [[n]] citations
        """
        for max_quote_chars in self.QUOTE_TRUNCATION_LEVELS:
            prompt = self._build_section_prompt(
                section=section,
                evidence_records=evidence_records,
                previous_text=previous_text,
                full_outline=full_outline,
                original_question=original_question,
                max_quote_chars=max_quote_chars,
                assigned_requirements_text=assigned_requirements_text,
            )

            messages = [Message(role="user", content=prompt)]
            try:
                check_cancelled()
                response = self.llm.chat(messages)
            except PayloadTooLargeError:
                if max_quote_chars <= self.QUOTE_TRUNCATION_LEVELS[-1]:
                    raise  # Already at minimum, give up
                logging.warning(
                    f"[SectionWriter] Payload too large with quote limit {max_quote_chars}, "
                    f"reducing to {max_quote_chars - 100}..."
                )
                continue

            # Clean up: remove any thinking tags or code blocks
            text = response.content if isinstance(response.content, str) else str(response.content or "")
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
            text = text.strip()

            # Filter out hallucinated citations not in evidence_records
            valid_refs = set(evidence_records.keys())
            text = self._filter_invalid_citations(text, valid_refs)

            return text

    def _build_section_prompt(
        self,
        section: SectionPlan,
        evidence_records: Dict[int, List[EvidenceRecord]],
        previous_text: str,
        full_outline: ReportOutline,
        original_question: str,
        max_quote_chars: int = 500,
        assigned_requirements_text: str = "",
    ) -> str:
        """Build the prompt for writing one section."""
        # 1. Outline overview
        outline_text = self._format_outline(full_outline, section.section_id)

        # 2. Evidence table (max_quote_chars adjustable for payload-too-large retry)
        evidence_table = self._format_evidence_table(evidence_records, max_quote_chars=max_quote_chars)

        # 3. Compressed previous text
        prev_summary = self._compress_previous_text(previous_text)

        if self.language == "cn":
            return self._build_section_prompt_cn(
                section,
                evidence_table,
                prev_summary,
                outline_text,
                original_question,
                assigned_requirements_text,
            )
        else:
            return self._build_section_prompt_en(
                section,
                evidence_table,
                prev_summary,
                outline_text,
                original_question,
                assigned_requirements_text,
            )

    def _format_insights_binding(self, section: SectionPlan) -> str:
        """Format insight→evidence bindings for prompt."""
        if not section.insights:
            return "(No pre-bound insights - write based on evidence table)"

        lines = []
        for i, ins in enumerate(section.insights, 1):
            evidence_refs = (
                ", ".join(f"[[{e}]]" for e in ins.evidence_ids) if ins.evidence_ids else "(no specific evidence)"
            )
            lines.append(f"{i}. **Insight**: {ins.insight}")
            lines.append(f"   **Supporting evidence**: {evidence_refs}")
            if ins.presentation_hint:
                hint_labels = {
                    "table": "建议用表格呈现" if self.language == "cn" else "suggest table format",
                    "comparison": "建议比较分析" if self.language == "cn" else "suggest comparative analysis",
                    "narrative": "建议叙述性论证" if self.language == "cn" else "suggest narrative form",
                }
                hint_text = hint_labels.get(ins.presentation_hint, ins.presentation_hint)
                lines.append(f"   **Presentation**: {hint_text}")
            lines.append("")
        return "\n".join(lines)

    def _build_section_prompt_en(
        self,
        section: SectionPlan,
        evidence_table: str,
        prev_summary: str,
        outline_text: str,
        original_question: str,
        assigned_requirements_text: str = "",
    ) -> str:
        prev_section = ""
        if prev_summary:
            prev_section = f"""
## 6. Previously Written Text (for continuity — avoid repeating)
{prev_summary}
"""
        # Format insights with evidence bindings
        insights_section = self._format_insights_binding(section)

        answers_aspect_text = ""
        if section.answers_aspect:
            answers_aspect_text = f"\n**This section answers**: {section.answers_aspect}"

        requirements_section = ""
        if assigned_requirements_text:
            requirements_section = f"\n{assigned_requirements_text}\n"

        return f"""You are a senior research analyst and report writer. Write ONE **high-quality analytical section** for the report.

## 1. Original Question (the report must directly answer this)
{original_question}

## 2. Full Report Outline
{outline_text}

## 3. Current Section
**Section {section.section_id}: {section.title}**
{section.description}{answers_aspect_text}{requirements_section}

## 4. Writing Blueprint (Insight→Evidence Bindings)
{insights_section}

How to use: Each Insight is your analytical judgment (write WITHOUT citation). The Supporting evidence [[n]] are for factual claims (each fact gets ONE [[n]]).

## 5. Evidence Table (verbatim quotes — factual claims must be based on these)
{evidence_table}
{prev_section}
## Writing Requirements

### Quality Standards (keep in mind throughout writing)
- Directly and visibly answer this section's answers_aspect
- Use tables for quantitative comparisons — do not bury numbers in prose
- Every major claim must explain its significance, not just state facts
- Connect paragraphs with logical transitions
- Lead with prose paragraphs, not bullet-point lists

### Audience & Role
Default tone: write as a senior consultant delivering to a decision-maker — clear, confident, accessible. **Bold** key terms. Define jargon on first use. Assume the reader is intelligent but not a domain expert.
Only if "Audience & Role Requirements" is explicitly listed above, adjust depth, technicality, and style to match the specified audience or persona, overriding the default tone.

### Writing Philosophy
A valuable research report does more than answer the user's explicit question — it also uses the facts gathered through research to reveal, via reasoning and analysis, discoveries the user may not have initially realized but that are crucial to fully understanding the topic. What the user wants to know: answer it directly. What the research shows they need to know: present it with solid evidence. Together, this helps the user achieve genuine, deep understanding. Every factual claim with [[n]] must be written strictly based on the evidence quote, ensuring that the cited source fully supports the stated fact.

### Analytical Depth
- **Do NOT simply list facts.** For each point: state the judgment, support with evidence, then analyze its meaning or implications.
- Reveal trends, patterns, and causal relationships behind the data (e.g., "This suggests...", "The underlying reason is...")
- Compare across sources: if different institutions/years/standards show different data, point out the differences and analyze why.
- Use analytical language: "Despite... however", "Notably", "In contrast", "This phenomenon reflects..."

### Avoiding Redundancy
- **Don't narrate the evidence — use it.** Bad: "According to source A, the value is X. Source B also reports Y. Furthermore, source C states Z." Good: "The market reached X[[1]], though regional variation is significant — Y in Asia[[2]] versus Z in Europe[[3]]."
- **No cross-section repetition.** If a fact or analysis was already covered in a previous section, do not repeat it — reference it briefly and move on.

### Data Presentation
- When comparing entities, metrics, or time periods, **MUST use a Markdown table**. Aim for at least one table per section when quantitative data is present.
- Table format:
  | Header1 | Header2 | Header3 |
  |---------|---------|---------|
  | data    | data    | data    |
- **Table citations**: Each cell containing factual data gets its own `[[n]]` directly after the data — do NOT put citations below the table.
  - ✅ `| Bureau of Stats | 100-500K[[2]] | 400M people[[2]] |`
  - ❌ `*Sources: [[2]][[5]]*` (below table — verifier cannot match which data each citation supports)
- Lists follow the same rule: each item's factual data gets `[[n]]` inline.

### Structure & Flow
- **Answer visibly**: When this section's `answers_aspect` involves multiple comparable items (definitions, standards, entities), present the direct answer in a structured format (table or list) first, then analyze. Burying answers inside analytical paragraphs makes them hard to find.
- Build logical transitions between points — use progressive or contrastive argument structures. Each paragraph should open by connecting to the previous one (progression, contrast, pivot, or elaboration).
- Maintain continuity with previously written sections; do not repeat what has been covered.
- **Terminology**: When a technical term first appears, briefly explain it in parentheses (e.g., "RLHF (Reinforcement Learning from Human Feedback)"). No need to re-explain on subsequent use.

### Regarding Missing Information
- If an evidence source explicitly documents absence (e.g., "data not published"), and you can cite it with [[n]] → write it (the absence itself is a finding).
- If something simply wasn't found in our search with no evidence → **skip it entirely**. Do NOT write "no information was found" or "sources did not mention".

## Citation Format (mandatory)
- Analytical judgments: no citation ("The market is consolidating.")
- Factual claims: exactly ONE [[n]] ("Revenue reached $3.4B[[2]]")
- **HARD RULE: Any sentence containing a specific number, percentage, dollar amount, date, or statistic MUST have a [[n]] citation. No exceptions.** If you cannot find a matching evidence source for a number, do not write that number.
- Never multiple refs per fact: ❌ "Revenue reached $3.4B[[2]][[5]]"
- Different facts may cite different sources: ✅ "Founded in 2015[[2]], valued at $80B[[5]]"
- Use ONLY `[[n]]` format. Never `[n]`, `[[n,m]]`, or `[[n],[m]]`
- Facts must match evidence quotes exactly — do not alter numbers or conclusions

## Output Rules
- Write ONLY Section {section.section_id}, using `## {section.section_id}. Title` as heading
- Use numbered `###` subsection headings to organize content within the section (e.g., `### {section.section_id}.1 Subtitle`, `### {section.section_id}.2 Subtitle`). Each subsection should have a clear logical focus. Ensure smooth transitions between subsections.
- Use rich Markdown formatting throughout: **bold** key terms and conclusions, use bulleted/numbered lists to enumerate factors or steps, use tables to compare data. Write like a professionally typeset article.
Begin writing Section {section.section_id}:"""

    def _build_section_prompt_cn(
        self,
        section: SectionPlan,
        evidence_table: str,
        prev_summary: str,
        outline_text: str,
        original_question: str,
        assigned_requirements_text: str = "",
    ) -> str:
        prev_section = ""
        if prev_summary:
            prev_section = f"""
## 6. 已写文本（用于衔接，避免重复）
{prev_summary}
"""
        # Format insights with evidence bindings
        insights_section = self._format_insights_binding(section)

        answers_aspect_text = ""
        if section.answers_aspect:
            answers_aspect_text = f"\n**本节回答的问题方面**：{section.answers_aspect}"

        requirements_section = ""
        if assigned_requirements_text:
            requirements_section = f"\n{assigned_requirements_text}\n"

        return f"""你是一位资深研究分析师和报告撰稿人。请为报告撰写一个**高质量的分析性章节**。

## 1. 原始问题（报告必须正面回答这个问题）
{original_question}

## 2. 完整报告大纲
{outline_text}

## 3. 当前要写的章节
**第 {section.section_id} 节：{section.title}**
{section.description}{answers_aspect_text}{requirements_section}

## 4. 写作蓝图（观点→证据绑定）
{insights_section}

使用方法：每个 Insight 是你的分析性判断（写时不加引用），Supporting evidence 的 [[n]] 用于写事实性声明（每个事实一个 [[n]]）。

## 5. 证据表（原文引述——事实性声明必须基于这些内容）
{evidence_table}
{prev_section}
## 写作要求

### 质量标准（写作全程牢记）
- 直接且清晰地回答本节的 answers_aspect
- 定量对比数据用表格呈现，不要埋在段落里
- 每个核心论点都要解释其意义，不能只陈述事实
- 段落之间用逻辑过渡衔接
- 以段落叙述为主，不要被列表要点主导

### 受众与角色
默认以资深顾问交付报告的风格写作——清晰、自信、易读。**加粗**关键术语。术语首次出现时简要解释。假设读者有判断力但非本领域专家。
仅当上方明确列出了"目标受众与角色要求"时，才据此调整写作的深度、专业度和语气风格，覆盖上述默认风格。

### 写作哲学
一篇有价值的研究报告，不仅要回答用户明确提出的问题，还要基于搜索到的现实事实，通过推理和分析揭示用户一开始可能未意识到、但对全面理解问题至关重要的发现。用户想知道的，正面回答；研究过程中发现他需要知道的，有理有据地呈现——两者结合，才能帮助用户真正深入地理解他所关心的问题。每个带 [[n]] 的事实性声明必须严格参考证据原文来写，确保该引用来源能完整支撑所述事实。

### 分析深度
- **不要简单罗列事实**。对每个观点，先陈述判断，再用证据支撑，最后分析其含义或影响
- 揭示数据背后的趋势、规律、因果关系（如"这表明..."、"其背后原因在于..."）
- 进行跨来源比较：不同机构/年份/标准的数据如果存在差异，要指出并分析原因
- 使用分析性语言：尽管…但是、值得注意的是、相比之下、这一现象反映了…

### 避免冗余
- **用证据，不要叙述证据。** 差：「根据来源A，数值为X。来源B也报告了Y。此外来源C指出Z。」好：「市场规模达到X[[1]]，但地区差异显著——亚洲Y[[2]]，欧洲Z[[3]]。」
- **禁止跨章节重复。** 前面章节已经论述过的事实或分析，不要再写一遍——简要提及即可。

### 数据呈现
- 对比实体、指标或时间段时，**必须使用 Markdown 表格**。有定量数据的章节应至少包含一张表格。
- 表格格式：
  | 表头1 | 表头2 | 表头3 |
  |-------|-------|-------|
  | 数据  | 数据  | 数据  |
- **表格引用**：每个含事实数据的单元格直接在数据后标注 `[[n]]`——禁止把引用放在表格下方
  - ✅ `| 国家统计局 | 10-50万[[2]] | 4亿人[[2]] |`
  - ❌ `*数据来源：[[2]][[5]]*`（放在表格下方——验证器无法匹配哪个数据对应哪个引用）
- 列表同理：每个条目的事实数据直接跟 `[[n]]`

### 结构与衔接
- **答案可见化**：当本节的 `answers_aspect` 涉及多个可比较项（定义、标准、实体等）时，先用结构化格式（表格或列表）直接呈现答案，再展开分析。把答案埋在分析段落里会导致难以发现。
- 观点之间要有逻辑过渡，构建递进或对比的论证结构。每段开头应承上启下，说明与上段的关系（递进、对比、转折或补充）
- 与已写章节保持衔接，不要重复已论述的内容
- **术语解释**：专业术语首次出现时用括号简要解释（如RLHF（基于人类反馈的强化学习）），后续使用无需重复解释

### 关于缺失信息
- 如果证据来源明确记录了某事物的缺失或不存在，且你能用 [[n]] 引用支撑 → 可以写（这本身是有价值的发现）
- 如果只是搜索未覆盖到、没有证据支撑 → **直接跳过，不要提及**。禁止写"未找到"、"资料未提及"等表述

## 引用格式（必须遵守）
- 分析性判断不加引用："市场正在整合。"
- 事实性声明加一个 [[n]]："收入达到34亿美元[[2]]"
- **硬性规则：任何包含具体数字、百分比、金额、日期或统计量的句子，必须有 [[n]] 引用，无例外。** 如果找不到匹配的证据来源，就不要写这个数字。
- 禁止一个事实多个引用：❌ "收入达34亿[[2]][[5]]"
- 不同事实可引不同源：✅ "成立于2015年[[2]]，估值800亿[[5]]"
- 只用 `[[n]]` 格式，禁止 `[n]`、`[[n,m]]`、`[[n],[m]]`
- 事实必须与证据原文数据一致，不可篡改数字或结论

## 输出规则
- 只写第 {section.section_id} 节，用 `## {section.section_id}. 标题` 作为章节标题
- 节内根据论述逻辑自行决定是否使用 `###` 子标题（如 `### {section.section_id}.1 子标题`、`### {section.section_id}.2 子标题`）。优先保证段落间的逻辑递进和流畅衔接，而非机械分块
- 善用丰富的 Markdown 格式：**加粗**关键术语和结论、用有序/无序列表分点罗列要素或步骤、用表格对比数据。像专业排版的文章一样书写。

请开始撰写第 {section.section_id} 节："""

    def _generate_executive_summary(
        self,
        full_text: str,
        original_question: str,
    ) -> str:
        """Generate an executive summary from the completed report text.

        Called after all sections are written. Produces a concise summary
        that directly answers the user's question without [[n]] citations.
        """
        # Truncate full text if too long for prompt
        text_for_summary = full_text
        if len(text_for_summary) > 120000:
            text_for_summary = text_for_summary[:120000] + "\n\n[... remainder omitted for brevity ...]"

        # Strip [[n]] citations from the text to avoid them leaking into summary
        text_for_summary = re.sub(r"\[\[\d+\]\]", "", text_for_summary)

        if self.language == "cn":
            prompt = f"""你是一位资深研究分析师。请为以下研究报告撰写一段**执行摘要（Executive Summary）**。

## 用户问题
{original_question}

## 完整报告
{text_for_summary}

## 要求
- **第一句话直接回答用户的核心问题**，不要铺垫
- 300-500字，概括报告中最重要的发现和建议
- 不使用任何引用标记（不要写[[n]]）
- 用 `## Executive Summary` 作为标题
- 语言简洁、清晰，适合快速阅读"""
        else:
            prompt = f"""You are a senior research analyst. Write an **Executive Summary** for the following research report.

## User Question
{original_question}

## Full Report
{text_for_summary}

## Requirements
- **The very first sentence must directly answer the user's core question** — no preamble
- 300-500 words, summarize the most important findings and recommendations
- Do NOT use any citation markers (no [[n]])
- Use `## Executive Summary` as heading
- Clear, concise language suitable for quick reading"""

        messages = [Message(role="user", content=prompt)]

        try:
            logging.info("[SectionWriter] Generating executive summary...")
            check_cancelled()
            response = self.llm.chat(messages)
            summary = response.content if isinstance(response.content, str) else str(response.content or "")
            summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL).strip()
            # Strip any [[n]] that might have leaked in
            summary = re.sub(r"\[\[\d+\]\]", "", summary)
            logging.info(f"[SectionWriter] Executive summary generated: {len(summary)} chars")
            return summary
        except Exception as e:
            logging.warning(f"[SectionWriter] Executive summary generation failed: {e}")
            return ""

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

    def _format_evidence_table(self, records: Dict[int, List[EvidenceRecord]], max_quote_chars: int = 500) -> str:
        """Format evidence records grouped by source for better cross-evidence analysis."""
        if not records:
            return "(No evidence records available for this section)"

        max_finding_chars = max(max_quote_chars - 200, 100)

        lines = []
        for ref_idx in sorted(records.keys()):
            recs = records[ref_idx]
            lines.append(f"**来源 [[{ref_idx}]]**")
            lines.append("| Criterion | Evidence Quote | Finding |")
            lines.append("|-----------|----------------|---------|")

            for r in recs:
                # Ensure all fields are strings (LLM may return list/dict)
                quote = str(r.evidence_quote) if not isinstance(r.evidence_quote, str) else r.evidence_quote
                finding = str(r.finding_answer) if not isinstance(r.finding_answer, str) else r.finding_answer
                criterion = str(r.criterion) if not isinstance(r.criterion, str) else r.criterion
                # Truncate long quotes (limit adjustable for payload-too-large retry)
                if len(quote) > max_quote_chars:
                    quote = quote[:max_quote_chars] + "..."
                if len(finding) > max_finding_chars:
                    finding = finding[:max_finding_chars] + "..."
                # Escape pipe chars
                quote = quote.replace("|", "\\|").replace("\n", " ")
                finding = finding.replace("|", "\\|").replace("\n", " ")
                criterion = criterion.replace("|", "\\|")

                lines.append(f"| {criterion} | {quote} | {finding} |")

            lines.append("")  # blank line between sources

        return "\n".join(lines)

    def _compress_previous_text(self, text: str) -> str:
        """Compress previously written text to fit in prompt.

        Memory management strategy:
        1. If text fits in limit, keep all
        2. Otherwise: keep last complete section + heading summaries of earlier sections
        3. This ensures continuity while preventing context explosion

        The goal is to provide enough context for smooth transitions without
        overwhelming the LLM with irrelevant details.
        """
        if not text or not text.strip():
            return ""

        # Strip [[n]] citations from previous text to prevent cross-section citation confusion
        text = re.sub(r"\[\[\d+\]\]", "", text)

        # ~10K tokens: CN ≈ 1 char/token, EN ≈ 4 chars/token
        max_chars = 20000 if self.language == "cn" else 40000

        # If within limit, keep everything
        if len(text) <= max_chars:
            return text

        # Split into sections by ## headers
        sections = re.split(r"\n(?=## )", text)

        # Single section case: just truncate from end
        if len(sections) <= 1:
            return f"[Truncated]...{text[-max_chars:]}"

        # Keep the last section complete for continuity
        last_section = sections[-1]

        # Build compact summaries of earlier sections (heading + first paragraph)
        earlier_summaries = []
        for s in sections[:-1]:
            heading_match = re.match(r"(## [^\n]+)", s)
            if not heading_match:
                continue

            heading = heading_match.group(1)

            # Get first paragraph (consecutive non-empty, non-heading lines)
            content_lines = [line.strip() for line in s.split("\n") if line.strip() and not line.startswith("#")]
            # Take up to 3 lines or 300 chars for the first paragraph
            first_para_lines = []
            char_count = 0
            for cl in content_lines:
                if char_count + len(cl) > 300:
                    if not first_para_lines:
                        first_para_lines.append(cl[:300] + "...")
                    break
                first_para_lines.append(cl)
                char_count += len(cl)
                if len(first_para_lines) >= 3:
                    break
            first_para = " ".join(first_para_lines) if first_para_lines else ""

            earlier_summaries.append(f"{heading}\n{first_para}")

        # Combine: summaries + last section
        if earlier_summaries:
            summary_block = "\n\n".join(earlier_summaries)
            result = f"[Earlier sections - summaries only]\n{summary_block}\n\n[Last complete section]\n{last_section}"
        else:
            result = last_section

        # Final safety truncation (keep end which has the last section)
        if len(result) > max_chars:
            result = f"[Truncated]...{result[-(max_chars - 15) :]}"

        return result

    def _filter_invalid_citations(self, text: str, valid_refs: set) -> str:
        """Remove [[n]] citations where n is not in valid_refs."""

        def _replace(match):
            ref_idx = int(match.group(1))
            if ref_idx in valid_refs:
                return match.group(0)
            logging.warning(f"[SectionWriter] Removed hallucinated citation [[{ref_idx}]]")
            return ""

        return re.sub(r"\[\[(\d+)\]\]", _replace, text)

    def _format_outline(self, outline: ReportOutline, current_section_id: str) -> str:
        """Format the full outline, marking the current section."""
        lines = [f"Theme: {outline.overall_theme}", ""]
        for section in outline.sections:
            marker = " <-- CURRENT" if section.section_id == current_section_id else ""
            lines.append(f"- {section.section_id}. {section.title}{marker}")
        return "\n".join(lines)

    def _append_references(
        self,
        report: str,
        cited_references: Dict[int, Dict],
    ) -> str:
        """Append the reference list to the report."""
        # Extract actually cited indices from report text
        report_cited = set(int(m) for m in re.findall(r"\[\[(\d+)\]\]", report))

        all_indices = set(cited_references.keys())
        other_indices = all_indices - report_cited

        parts = ["\n\n---\n"]

        # Report citations
        if report_cited:
            if self.language == "cn":
                parts.append("\n## 报告引用来源\n")
            else:
                parts.append("\n## Report Citations\n")

            for idx in sorted(report_cited):
                if idx in cited_references:
                    ref = cited_references[idx]
                    title = ref.get("title", "Unknown")
                    url = ref.get("url", "")
                    parts.append(f"\n[[{idx}]] [{title}]({url})\n")

        # Search process references
        if other_indices and self.include_search_refs:
            if self.language == "cn":
                parts.append("\n## 搜索过程参考来源\n")
            else:
                parts.append("\n## Search Process References\n")

            for idx in sorted(other_indices):
                if idx in cited_references:
                    ref = cited_references[idx]
                    title = ref.get("title", "Unknown")
                    url = ref.get("url", "")
                    parts.append(f"\n[[{idx}]] [{title}]({url})\n")

        return report + "".join(parts)
