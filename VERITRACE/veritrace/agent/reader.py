"""
Reader Agent - Independent page reading with structured extraction.

This module separates the "deep reading" functionality from SearcherAgent
to solve context explosion and improve parallel processing.

Key benefits:
- Each Reader has independent context (~5000 tokens vs 30K+ accumulated)
- Parallel execution for multiple pages
- Structured extraction by acceptance criteria
- Reduced hallucination risk (focused attention)
"""

import json
import logging
import re
import traceback
from concurrent.futures import as_completed
from .concurrency import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .core import BaseLLMAdapter, Message
from .config import get_reader_config
from .quality_checker import compute_cr_aap
from .runtime.progress import report_progress


@dataclass
class ReaderInput:
    """Input data for Reader Agent."""

    # Page info
    page_index: int
    page_url: str
    page_title: str
    page_content: str
    page_domain: str = ""
    date_published: Optional[str] = None

    # Task context (structured)
    original_question: str = ""  # Original question: the stable anchor used only to assess Part B discoveries.
    task_goal: str = ""  # Task objective.
    task_context: str = ""  # Background from upstream cognitive nodes.
    constraints: List[str] = field(default_factory=list)  # Constraints.
    acceptance_criteria: List[str] = field(default_factory=list)  # Acceptance criteria.

    # Legacy: full task content (kept for compatibility)
    task_content: str = ""

    # Known context (from previous searches)
    known_findings: Dict[str, str] = field(default_factory=dict)
    known_gaps: List[str] = field(default_factory=list)


@dataclass
class ReaderOutput:
    """
    Output from Reader Agent for a single page.

    Dual-track design:
    - Part A (Structured): findings, gaps - answers acceptance criteria
    - Part B (Exploratory): unexpected_insights - discoveries beyond criteria
    """

    page_index: int
    page_url: str
    page_title: str

    # === Part A: Structured Answer ===
    # Findings by acceptance criteria: criterion -> {answer, evidence}
    findings: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # Gaps - criteria not found in this page
    gaps: List[str] = field(default_factory=list)

    # === Part B: Exploratory Discoveries ===
    # Unexpected insights — findings that diverge from Planner's expectations
    # [{"label": "topic label", "insight": "...", "evidence": "verbatim quote"}]
    unexpected_insights: List[Dict[str, str]] = field(default_factory=list)

    # === Quality & Metadata ===
    # CRAAP quality scores
    quality_scores: Dict[str, float] = field(default_factory=dict)

    # Temporal context
    temporal_context: str = ""

    # Accessibility barriers (info exists but agent can't access it)
    # [{"criterion": "...", "barrier": "requires_download|paywall|login_required",
    #   "detail": "...", "alternative_hint": "..."}]
    accessibility_notes: List[Dict[str, str]] = field(default_factory=list)

    # Processing status
    success: bool = True
    error: Optional[str] = None


@dataclass
class MergedReaderResults:
    """
    Merged results from multiple Reader agents.

    Dual-track aggregation:
    - Part A: all_findings, remaining_gaps
    - Part B: all_unexpected_insights
    """

    # === Part A: Structured Answer Aggregation ===
    # All findings by criteria (criterion -> [(page_index, {answer, evidence})])
    all_findings: Dict[str, List[Tuple[int, Dict[str, str]]]] = field(default_factory=dict)

    # Remaining gaps (criteria not answered by any page)
    remaining_gaps: List[str] = field(default_factory=list)

    # === Part B: Exploratory Discoveries Aggregation ===
    # Unexpected insights from all pages
    all_unexpected_insights: List[Dict[str, Any]] = field(default_factory=list)

    # === Quality & Metadata ===
    # Quality by page
    quality_by_page: Dict[int, Dict[str, float]] = field(default_factory=dict)

    # Pages processed
    pages_processed: List[int] = field(default_factory=list)
    failed_pages: List[int] = field(default_factory=list)

    # Temporal summary
    temporal_summary: str = ""

    # Accessibility barriers (info exists but agent can't access)
    accessibility_notes: List[Dict[str, str]] = field(default_factory=list)


class ReaderAgent:
    """
    Reader Agent - Reads a single page and extracts information by acceptance criteria.

    Stateless design: each call is independent with its own context.
    """

    def __init__(self, llm: BaseLLMAdapter, language: str = "en"):
        """
        Initialize Reader Agent.

        Args:
            llm: LLM adapter for extraction
            language: Prompt language ("en" or "cn")
        """
        self.llm = llm
        self.language = language

    def read_page(self, input_data: ReaderInput) -> ReaderOutput:
        """
        Read a page and extract information via single LLM call (Part A + Part B combined).

        Args:
            input_data: ReaderInput with page content and task context

        Returns:
            ReaderOutput with extracted information
        """
        try:
            report_progress("reading", page_index=input_data.page_index, title=input_data.page_title)
            prompt = self._build_prompt(input_data)
            messages = [Message(role="user", content=prompt)]
            response = self.llm.chat(messages).content
            return self._parse_response(response, input_data)

        except Exception as e:
            logging.error(f"[Reader:{input_data.page_index}] Error: {e}\n{traceback.format_exc()}")
            return ReaderOutput(
                page_index=input_data.page_index,
                page_url=input_data.page_url,
                page_title=input_data.page_title,
                success=False,
                error=f"{type(e).__name__}: {str(e)}",
            )

    def _extract_json(self, text: str) -> Optional[Dict]:
        """Extract JSON from response text."""

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

        # Try regex extraction - match JSON with known top-level keys
        for key in ("findings", "unexpected_insights"):
            json_match = re.search(r'\{[\s\S]*"' + key + r'"[\s\S]*\}', text)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    continue

        return None

    # Context section builder

    def _build_context_section(self, input_data: ReaderInput) -> str:
        """Dispatch to EN/CN context section builder."""
        if self.language == "cn":
            return self._build_context_section_cn(input_data)
        return self._build_context_section_en(input_data)

    def _build_context_section_en(self, input_data: ReaderInput) -> str:
        """Build shared task context + page content section (English)."""
        task_goal_text = input_data.task_goal or "Extract relevant information"
        context_text = input_data.task_context or ""

        constraints_text = ""
        if input_data.constraints:
            constraints_list = "\n".join(f"- {c}" for c in input_data.constraints)
            constraints_text = f"\n## 3. Constraints (priority guidance, not hard filters)\nThese constraints guide information priority but are NOT hard filters. Even if information doesn't fully satisfy all constraints (e.g., slightly older date, non-preferred but authoritative source), extract it as long as it's relevant to the acceptance criteria:\n{constraints_list}\n"

        criteria_list = (
            "\n".join(f"- **{i + 1}. {c}**" for i, c in enumerate(input_data.acceptance_criteria))
            if input_data.acceptance_criteria
            else "- Answer the task's main question"
        )

        known_findings_text = ""
        if input_data.known_findings:
            known_items = "\n".join(
                f"- {k}: {v[:100]}..." if len(v) > 100 else f"- {k}: {v}" for k, v in input_data.known_findings.items()
            )
            known_findings_text = (
                f"\n## Already Known\n{known_items}\n\nFocus on NEW information or CONTRADICTORY evidence.\n"
            )

        known_gaps_text = ""
        if input_data.known_gaps:
            gaps_list = "\n".join(f"- {g}" for g in input_data.known_gaps)
            known_gaps_text = f"\n## Information Gaps (priority)\n{gaps_list}\n"

        task_section = f"## 1. Task Goal\n{task_goal_text}\n"
        if context_text:
            task_section += f"\n## 2. Background Knowledge\n{context_text}\n"
        task_section += constraints_text
        task_section += f"\n## {'4' if context_text else '3'}. Acceptance Criteria\nExtract information for each criterion, prioritizing constraint-matching results:\n{criteria_list}\n\n⚠️ **Constraint handling**: Constraints are quality preferences, NOT hard filters. Extract information as long as it's relevant to the acceptance criteria, even if it doesn't fully satisfy all constraints. Only mark as gap when the page has NO relevant information for that criterion at all.\n"

        return f"""================================================================================
## TASK CONTEXT
================================================================================

{task_section}
{known_findings_text}{known_gaps_text}
================================================================================
## PAGE TO EXTRACT FROM
================================================================================

- **Index**: [{input_data.page_index}]
- **Title**: {input_data.page_title}
- **URL**: {input_data.page_url}
- **Domain**: {input_data.page_domain}
- **Published**: {input_data.date_published or "Unknown"}

## Page Content
{input_data.page_content}"""

    def _build_context_section_cn(self, input_data: ReaderInput) -> str:
        """Build shared task context + page content section (Chinese)."""
        task_goal_text = input_data.task_goal or "提取相关信息"
        context_text = input_data.task_context or ""

        constraints_text = ""
        if input_data.constraints:
            constraints_list = "\n".join(f"- {c}" for c in input_data.constraints)
            constraints_text = f"\n## 3. 约束条件（优先级指引，非硬性过滤）\n以下约束引导信息筛选的优先级，不是硬性过滤条件。即使信息不完全满足所有约束（如时间略早、来源非首选但权威），只要与验收要点相关就应提取：\n{constraints_list}\n"

        criteria_list = (
            "\n".join(f"- **{i + 1}. {c}**" for i, c in enumerate(input_data.acceptance_criteria))
            if input_data.acceptance_criteria
            else "- 回答任务的主要问题"
        )

        known_findings_text = ""
        if input_data.known_findings:
            known_items = "\n".join(
                f"- {k}: {v[:100]}..." if len(v) > 100 else f"- {k}: {v}" for k, v in input_data.known_findings.items()
            )
            known_findings_text = (
                f"\n## 已知信息（来自其他来源）\n{known_items}\n\n请专注于**新信息**或**矛盾证据**。\n"
            )

        known_gaps_text = ""
        if input_data.known_gaps:
            gaps_list = "\n".join(f"- {g}" for g in input_data.known_gaps)
            known_gaps_text = f"\n## 信息缺口（优先搜索）\n{gaps_list}\n"

        task_section = f"## 1. 任务目标\n{task_goal_text}\n"
        if context_text:
            task_section += f"\n## 2. 背景知识\n{context_text}\n"
        task_section += constraints_text
        task_section += f"\n## {'4' if context_text else '3'}. 验收要点\n从页面中提取以下信息，优先满足上述约束条件：\n{criteria_list}\n\n⚠️ **约束处理规则**: 约束是质量偏好，不是硬性过滤。只要信息与验收要点相关就应提取，即使不完全满足约束。只有与验收要点本身完全无关的信息才标为gap。\n"

        return f"""================================================================================
## 任务上下文
================================================================================

{task_section}
{known_findings_text}{known_gaps_text}
================================================================================
## 待提取页面
================================================================================

- **索引**: [{input_data.page_index}]
- **标题**: {input_data.page_title}
- **URL**: {input_data.page_url}
- **域名**: {input_data.page_domain}
- **发布日期**: {input_data.date_published or "Unknown"}

## 页面内容
{input_data.page_content}"""

    # Prompt builders (Part A + Part B combined)

    def _build_prompt(self, input_data: ReaderInput) -> str:
        """Dispatch to EN/CN prompt builder."""
        if self.language == "cn":
            return self._build_prompt_cn(input_data)
        return self._build_prompt_en(input_data)

    def _build_prompt_en(self, input_data: ReaderInput) -> str:
        """Build combined Part A + Part B prompt (English)."""
        ctx = self._build_context_section(input_data)
        return f"""You are a precise information extractor. You have TWO tasks:
1. Extract structured answers to acceptance criteria (Part A)
2. Discover valuable information BEYOND the criteria (Part B)

{ctx}

================================================================================
## PART A: Structured Answer - Answer Acceptance Criteria
================================================================================

⚠️ **COMPLETENESS**: Extract ALL information found in the page. No shortcuts or placeholders.

📊 **DATA ACCURACY (tables/structured data)**: List all column headers first, count from LEFT to RIGHT, include BOTH row and column position when citing numbers.

📋 **EVIDENCE REQUIREMENT (MANDATORY)**:
Every finding MUST include a direct, verbatim quote (200-500 characters) copied from the page.
If you cannot find an exact quote that supports a finding, DO NOT report that finding.
"evidence" is NOT your summary — it is the page's own words.

⚠️ **COPY-PASTE ONLY**: Copy the EXACT text as it appears. Keep ALL spaces, punctuation, special characters.

**Type 1: Descriptive Evidence** — Copy-paste exact sentences verbatim.
**Type 2: Table Evidence** — Copy complete row, then headers on next line.

⚠️ **RELATIONSHIP EVIDENCE REQUIREMENT** (critical for tasks about entity relationships):
When the task asks about relationships (e.g., "X's suppliers", "Y's customers", "partners of Z"):
- Evidence MUST explicitly mention BOTH the subject entity AND the relationship
- Do NOT infer relationships from co-occurrence in the same article

❌ **WRONG** (co-occurrence hallucination):
  Task: "Find ByteDance's data annotation suppliers"
  Page: An industry report describing Company A, Company B, Company C... ByteDance is only mentioned in Company A's section as a client.
  Finding: "Company B is ByteDance's supplier"
  Evidence: "Company B provides full-lifecycle AI data services covering text, image, and speech"
  → INVALID! Evidence only describes Company B's general capabilities. It says NOTHING about ByteDance.

✅ **CORRECT**:
  Finding: "Company A supplies data to ByteDance"
  Evidence: "Company A's clients include Microsoft, Tesla, ByteDance, and Alibaba"
  → VALID. Evidence explicitly names ByteDance.
  Company B, Company C → list as **gaps** (page does not state their relationship with ByteDance)

⚠️ **NO BUNDLING**: If your answer lists MULTIPLE entities (e.g., a list of suppliers), EVERY entity in that list MUST appear in the evidence quote in **explicit** connection with the subject entity. If an entity is **NOT** mentioned in the evidence, **REMOVE** it from the answer and add it to gaps. One evidence quote CANNOT cover entities it does not mention.

For each acceptance criterion:
1. If found: provide the COMPLETE answer with all relevant details
2. If **topic relevant but a descriptor unmet** (e.g., criterion says "official" but content is unofficial, or "2024" but data is from 2021): still extract ALL substantive details, then note which descriptor doesn't match. Reporting only "doesn't match" without extracting the content = information waste.
3. If not found: list as a gap (page has NO relevant information for this criterion at all)
4. If **partially extractable**: page contains a large dataset (e.g., table with hundreds of rows, interactive chart, downloadable file) but you can only extract a few examples — report what you extracted and clearly state the rest is not captured. Do NOT claim the full dataset was obtained.

❌ **WRONG** (content discarded due to descriptor mismatch):
  Criterion: "Government-published average household income by province in 2024"
  Page: Academic research with detailed income data by province from 2022
  Answer: "No government-published 2024 provincial income data found"
  → BAD! Page had complete provincial income data — you discarded it because source was not "government" and year was not "2024".

✅ **CORRECT**:
  Answer: "No government 2024 data on this page. However, [University]'s 2022 study provides provincial household income: Province A = ¥XX,XXX, Province B = ¥YY,YYY... [all data]. Note: academic source (not government), data from 2022 (not 2024)."
  → GOOD! All substantive content extracted, descriptor mismatches clearly noted.

================================================================================
## PART B: Unexpected Discoveries
================================================================================

**Why this matters**: The Planner created this search task based on pre-existing knowledge — educated guesses about what should exist. The acceptance criteria reflect what the Planner *expected* to find.

Your job: identify where the actual page content **diverges from those expectations** — reality differs from assumptions, dimensions the Planner didn't consider, unexpected connections or contradictions. These discoveries flow back through the Searcher to the cognitive graph, inspiring the Planner to adjust its understanding, correct assumptions, or search from new angles.

As the only component that directly reads source material, you are responsible for faithfully recording the gap between "what was expected" and "what is actually there."

**Context for this section**:
- Original Question: {input_data.original_question or "(not provided)"}
  The user's research question — use this to judge whether a discovery genuinely serves the research intent.
- Task Goal: the Planner's hypothesis for addressing a dimension of the original question (based on pre-training knowledge, may not match reality).
- Acceptance Criteria: the Planner's specific assumed directions (highest assumption bias).

A finding qualifies as an unexpected discovery only if **BOTH** are true:
(1) Would it help address the dimension of the original question that the Task Goal is trying to resolve? (If the Planner knew this, would it change how it searches for or understands this task?)
(2) Is it completely outside all acceptance criteria directions?

**What to look for:**
- Facts that contradict or nuance the Planner's assumptions
- Important entities, relationships, or patterns not asked about
- Timeline events that change the picture
- Leads or angles worth investigating further

**Give each discovery a short `label`** (like a criterion title) to categorize it.

⚠️ **EVIDENCE REQUIREMENT**: Same as Part A — every discovery MUST include a verbatim "evidence" quote (200-500 chars). No evidence = discovery is INVALID.

================================================================================
## Quality Scoring (CRAAP Framework)
================================================================================

Score each dimension 1-5 with these anchors:

**Currency** (1-5): Does the source's time coverage match the query's needs?
  5 = Covers the exact period the query targets (current queries: within 6 months; historical queries: from the target period itself)
  4 = Close temporal match with minor gaps (e.g., a 2021 retrospective for a 2020 query)
  3 = Related period but not optimal (e.g., 2022 data for a 2024 query, or general overview instead of period-specific)
  2 = Significant temporal mismatch — covers the wrong time window
  1 = Wrong era entirely, or so outdated the information is likely invalid

**Relevance** (1-5): Does it answer the query with citable specifics THAT YOU ACTUALLY EXTRACTED?
  Score based on what appears in your findings and evidence, NOT what merely exists on the page.
  If data is behind a paywall/download/login and you could not extract it → it does NOT count toward Relevance (record it in accessibility_notes instead).
  ⚠️ TRAP: If the page only DESCRIBES what data it offers (e.g., "this platform provides 10 years of market data", "click to download full report") but your findings and evidence contain NO specific values, facts, or conclusions that directly answer the task → Relevance ≤ 2. If you extracted SOME usable data but not all, score proportionally based on what you actually have.
  Test: Can your findings be directly cited in a final report to answer the question? If not, the page told you about data — it didn't give you data.
  5 = Directly answers core question with citable data you extracted (numbers, dates, names, methodology), covers multiple acceptance criteria
  4 = Answers core question but extracted data incomplete, or broad coverage lacking specific evidence
  3 = Touches one aspect of the question, or scattered info requiring synthesis
  2 = Related topic but doesn't answer the question (e.g., asked market trends, only got company overview)
  1 = No substantive connection to query, or key data exists but entirely inaccessible

**Authority** (1-5): How credible is the source? Judge by institutional accountability and editorial oversight, not by name.
  5 = Official body with statutory/regulatory authority, or peer-reviewed venue (e.g., government statistical agency, central bank, academic journal)
  4 = Organization with public accountability and professional editorial process (e.g., licensed news wire, industry regulatory body, public company filings)
  3 = Named authors or outlet with visible editorial standards and reputation at stake (e.g., professional trade media, credentialed analyst)
  2 = No editorial gate: self-published, personal blog, forum post, unsigned article
  1 = Anonymous or unattributable: no identifiable author or institution

**Accuracy** (1-5): Is the extracted information well-supported?
  5 = Claims backed by cited evidence, data includes methodology/source you can see, independently verifiable
  4 = Most claims supported with some verifiable references
  3 = Some evidence present, moderately supported but gaps exist
  2 = Few or no citations, claims hard to verify independently
  1 = Unsupported assertions, sensational, or contradicts known facts

**Purpose** (1-5): Is the content objective?
  5 = Educational/informational, objective tone, no commercial interest
  4 = Slight perspective but factually grounded, transparent about standpoint
  3 = Identifiable bias but factual core preserved
  2 = Promotional/advocacy content with facts selectively presented
  1 = Agenda-driven, misleading framing, or undisclosed conflicts of interest

================================================================================
## Accessibility Notes
================================================================================

If you identify high-value information that EXISTS but CANNOT be extracted from this page
(e.g., "full dataset available for download", "see the PDF report", "login required to view"),
note it in accessibility_notes. This is NOT the same as "information doesn't exist" — it means
the information is real but our tools cannot access it.

================================================================================
## Output Format
================================================================================

Respond with ONLY a JSON object (no markdown code blocks):
{{
  "findings": {{
    "criterion_name": {{
      "answer": "extracted information",
      "evidence": "verbatim quote 200-500 chars"
    }}
  }},
  "gaps": ["criterion_name not found in this page"],
  "unexpected_insights": [
    {{"label": "short topic label", "insight": "what was discovered and why it matters", "evidence": "verbatim quote 200-500 chars"}}
  ],
  "quality_scores": {{
    "currency": 4, "relevance": 5, "authority": 3, "accuracy": 4, "purpose": 4
  }},
  "temporal_context": "Information appears to be from 2024 based on...",
  "accessibility_notes": [
    {{"criterion": "criterion_name", "barrier": "requires_download", "detail": "Full dataset at example.com/download", "alternative_hint": "Summary statistics available in the text"}}
  ]
}}

**Citation**: Use [[{input_data.page_index}]] format for all claims."""

    def _build_prompt_cn(self, input_data: ReaderInput) -> str:
        """Build combined Part A + Part B prompt (Chinese)."""
        ctx = self._build_context_section_cn(input_data)
        return f"""你是一个精确的信息提取器。你有两个任务：
1. 根据验收要点提取结构化答案（Part A）
2. 发现超出验收要点范围的有价值信息（Part B）

{ctx}

================================================================================
## PART A: 结构化回答 - 回答验收要点
================================================================================

⚠️ **完整性要求**：你**必须**提取页面中找到的**所有**信息。不要总结或省略。

📊 **数据准确性（表格/结构化数据）**：先列出所有列标题，从左到右计数，引用数字时同时包含行和列的位置。

📋 **证据要求（强制）**：
每条发现**必须**包含从来源**逐字复制**的直接证据（200-500个字符）。
如果找不到支持某条发现的确切引用，则**不要**报告该发现。
"evidence"不是你的总结——它是页面自身的原文。

⚠️ **仅逐字复制——不要重新格式化！** 复制来源中原样出现的文本，保留所有空格、标点、特殊字符。

**类型1：描述性证据** — 逐字复制原句。
**类型2：表格证据** — 先写行数据，下一行写表头。

⚠️ **关系证据要求**（当任务涉及实体间关系时，此规则至关重要）：
当任务问的是实体间关系（如"X的供应商"、"Y的客户"、"Z的合作伙伴"）：
- evidence必须**同时提到**目标实体**和**所声称的关系
- **不要**从同一篇文章中的共现推断关系

❌ **错误示范**（共现幻觉）：
  任务："调查字节跳动的数据标注供应商"
  页面：一篇行业报告介绍了A公司、B公司、C公司……字节跳动仅在A公司的段落中被提及为客户。
  answer: "B公司是字节跳动的数据供应商"
  evidence: "B公司提供覆盖AI全生命周期的数据服务，包括文本、图像、语音等"
  → 无效！evidence仅描述了B公司的通用能力，完全没有提到字节跳动。

✅ **正确做法**：
  answer: "A公司为字节跳动提供数据标注服务"
  evidence: "客户包括微软、特斯拉、字节跳动、阿里巴巴等"
  → 有效！evidence明确提到了字节跳动。
  B公司、C公司 → 归入**gaps**（页面未说明它们与字节跳动的关系）

⚠️ **禁止捆绑**：如果你的answer列出了**多个**实体（如多个供应商），那么列表中的**每个**实体都必须在evidence引用中被明确提到、且与目标实体有关联。如果某个实体**没有**在evidence中被提及，将它从answer中**删除**并加入gaps。一条evidence引用不能覆盖它未提及的实体。

对于每条验收要点：
1. 如果找到：提供**完整**回答，包含所有相关细节
2. 如果**主题相关但限定词不满足**（如要点要求"官方"但内容是非官方的，或要求"2024年"但数据来自2021年）：仍然提取**所有实质性内容**，然后标注哪个限定词未满足。只报告"不满足"而不提取具体内容 = 信息浪费。
3. 如果未找到：列为信息缺口（页面与此验收要点完全无关）
4. 如果**只能部分提取**：页面包含大型数据集（如数百行的表格、交互式图表、可下载文件）但你只能提取部分示例——报告你提取到的内容，并明确说明其余部分未被捕获。不要声称获得了完整数据集。

❌ **错误示范**（因限定词不匹配而丢弃内容）：
  验收要点："2024年政府发布的各省居民平均家庭收入"
  页面：某大学2022年的学术研究，含各省详细收入数据
  answer: "未找到2024年政府发布的各省收入数据"
  → 错！页面有完整的各省收入数据，你因为来源不是"政府"、年份不是"2024"就全部丢弃了。

✅ **正确做法**：
  answer: "本页无政府2024年数据。但[某大学]2022年研究提供了各省家庭收入：A省 = ¥XX,XXX、B省 = ¥YY,YYY……[所有数据]。注意：来源为学术研究（非政府），数据来自2022年（非2024年）。"
  → 正确！所有实质内容已提取，限定词不匹配之处已标注。

================================================================================
## PART B: 意外发现
================================================================================

**为什么重要**：Planner基于已有知识（对世界的预设假设）创建了这个搜索任务。验收要点反映的是Planner"认为应该能找到什么"。

你的任务：识别页面实际内容与这些预期之间的**偏差**——现实与假设不同、Planner没考虑到的维度、意外的关联或矛盾。这些发现会通过Searcher传回认知图，启发Planner调整对世界的理解、修正假设、或从新角度发起搜索。

作为整个系统中唯一直接阅读原始资料的角色，你有责任忠实记录"预期"与"现实"之间的差距。

**本部分上下文**：
- 原始问题：{input_data.original_question or "（未提供）"}
  用户的研究问题——用它来判断一条发现是否真正服务于研究意图。
- 任务目标：Planner对"原始问题中某个维度"的假设性切入点（基于预训练知识，可能与现实不符）。
- 验收要点：Planner预设的具体调查方向（假设偏差最高）。

一条发现只有同时满足以下**两个条件**，才算是"意外发现"：
(1) 是否有助于回答"原始问题"中任务目标所针对的那个维度？（如果Planner提前知道这件事，会改变它对该任务的搜索策略或理解方式吗？）
(2) 是否完全超出了所有验收要点的方向？

**关注方向：**
- 与Planner假设相矛盾或增添细微差别的事实
- 未被问及但重要的实体、关系或模式
- 改变整体图景的时间线事件
- 值得进一步调查的线索或角度

**给每条发现一个简短的 `label`** 用于分类——例如"市场整合"、"定价转变"、"监管风险"。

⚠️ **证据要求**：同Part A——每条发现都必须包含逐字引用(200-500字符)。无证据=发现无效。

================================================================================
## 质量评估（CRAAP框架）
================================================================================

对每个维度评分1-5，使用以下锚点：

**Currency（时间对齐度）** (1-5): 来源的时间覆盖是否匹配查询需要？
  5 = 覆盖查询目标的确切时段（查当前信息：6个月内；查历史数据：来自目标时段本身）
  4 = 时间接近，有小偏差（如：用2021年回顾性文章查2020年数据）
  3 = 相关时段但非最优（如：用2022年数据回答2024年问题，或泛述代替时段专项数据）
  2 = 明显时间错配——覆盖的是错误的时间窗口
  1 = 完全不对的年代，或过时到信息可能已失效

**Relevance（信息完整度）** (1-5): 你实际提取到的内容是否回答了查询？
  评分基于你的findings和evidence中实际包含的内容，而非页面上"存在"的内容。
  如果数据在付费墙/需下载/需登录后面且你无法提取 → 不计入Relevance分数（记录到accessibility_notes）。
  ⚠️ 陷阱：如果页面只是介绍自己提供什么数据（如"本平台提供10年市场数据"、"点击下载完整报告"），但你的findings和evidence里没有任何能正面回答task要求的数值、事实或结论 → Relevance ≤ 2。如果你提取到了部分可用数据但不完整，则按部分完成程度评分。
  检验方法：你的findings能否被直接引用到最终报告中回答问题？如果不能，说明页面告诉了你有什么数据——但没有给你数据。
  5 = 直接回答核心问题，你提取到了可引用的具体数据（数字、日期、名称、方法论），覆盖多个验收要点
  4 = 回答了核心问题但提取到的数据不完整，或覆盖面广但缺乏具体证据支撑
  3 = 仅触及问题的某个侧面，或信息分散需自行整合
  2 = 主题相关但不回答问题（如：问市场趋势，只给了公司概况）
  1 = 与查询无实质关联，或关键数据存在但完全无法获取

**Authority（权威性）** (1-5): 来源可信度？根据机构问责性和编辑监督判断，而非根据名称。
  5 = 具有法定/监管权力的官方机构，或同行评审场所（如：政府统计局、央行、学术期刊）
  4 = 有公共问责和专业编审流程的机构（如：持牌通讯社、行业监管机构、上市公司公告）
  3 = 有署名作者或可见编辑标准、声誉约束的渠道（如：行业专业媒体、持证分析师）
  2 = 无编辑把关：自发布内容、个人博客、论坛帖子、无署名文章
  1 = 匿名或无法溯源：无法识别作者或机构

**Accuracy（准确性）** (1-5): 提取到的信息是否有充分支撑？
  5 = 声明有引用来源支撑，数据含你能看到的方法论/出处，可独立核实
  4 = 大部分声明有据，部分可验证
  3 = 有一定证据但存在空白，支撑力度中等
  2 = 少量或无引用，声明难以独立验证
  1 = 无依据的断言、耸动内容、或与已知事实矛盾

**Purpose（目的性）** (1-5): 内容是否客观？
  5 = 教育/信息性，语调客观，无商业利益
  4 = 略有立场但基于事实，对自身视角透明
  3 = 有可识别的偏向但事实核心保留
  2 = 推广/倡导性内容，事实被选择性呈现
  1 = 议程驱动、误导性框架、或未披露的利益冲突

================================================================================
## 信息可获取性说明
================================================================================

如果你发现了高价值信息**存在**但**无法从本页提取**（如"完整数据集可下载"、"查看PDF报告"、"需登录查看"），
请在accessibility_notes中注明。这与"信息不存在"不同——信息是真实存在的，但我们的工具无法获取。

================================================================================
## 输出格式
================================================================================

仅返回一个 JSON 对象（不要使用 markdown 代码块）：
{{
  "findings": {{
    "criterion_name": {{
      "answer": "提取的信息",
      "evidence": "逐字引用 200-500字符"
    }}
  }},
  "gaps": ["未找到的验收要点"],
  "unexpected_insights": [
    {{"label": "简短主题标签", "insight": "发现了什么以及为什么重要", "evidence": "逐字引用 200-500字符"}}
  ],
  "quality_scores": {{
    "currency": 4, "relevance": 5, "authority": 3, "accuracy": 4, "purpose": 4
  }},
  "temporal_context": "根据...判断信息来自2024年",
  "accessibility_notes": [
    {{"criterion": "criterion_name", "barrier": "requires_download", "detail": "完整数据集在example.com/download", "alternative_hint": "文中可见摘要统计数据"}}
  ]
}}

**引用格式**：所有论断使用 [[{input_data.page_index}]] 格式引用。"""

    def _parse_response(self, response: str, input_data: ReaderInput) -> ReaderOutput:
        """Parse LLM response (Part A + Part B combined) into ReaderOutput."""
        output = ReaderOutput(
            page_index=input_data.page_index,
            page_url=input_data.page_url,
            page_title=input_data.page_title,
        )
        try:
            data = self._extract_json(response)
            if not data:
                output.success = False
                output.error = "Failed to parse combined JSON response"
                return output

            # === Part A: Structured findings (reuse Part A validation logic) ===
            raw_findings = data.get("findings", {})
            output.findings = {}
            for k, v in raw_findings.items():
                if isinstance(v, dict):
                    output.findings[k] = {"answer": str(v.get("answer", "")), "evidence": str(v.get("evidence", ""))}
                elif isinstance(v, list):
                    output.findings[k] = {"answer": " | ".join(str(x) for x in v), "evidence": ""}
                else:
                    output.findings[k] = {"answer": str(v) if v else "", "evidence": ""}
            # Filter findings without evidence
            validated = {}
            for k, v in output.findings.items():
                if v.get("evidence") and v["evidence"].strip():
                    validated[k] = v
                else:
                    logging.warning(f"[Reader:{input_data.page_index}] Discarded finding for '{k}': no evidence quote")
            output.findings = validated
            output.gaps = data.get("gaps", [])

            # === Part B: Unexpected discoveries (reuse Part B validation) ===
            raw_insights = data.get("unexpected_insights", [])
            output.unexpected_insights = [
                ins for ins in raw_insights if isinstance(ins, dict) and ins.get("evidence") and ins["evidence"].strip()
            ]
            if len(output.unexpected_insights) < len(raw_insights):
                logging.warning(
                    f"[Reader:{input_data.page_index}] Discarded "
                    f"{len(raw_insights) - len(output.unexpected_insights)} "
                    f"unexpected_insights without evidence"
                )

            # === Quality: compute CR/AAP ===
            output.quality_scores = data.get("quality_scores", {})
            if output.quality_scores:
                if "overall" not in output.quality_scores:
                    scores = [
                        output.quality_scores.get(d, 3)
                        for d in ("currency", "relevance", "authority", "accuracy", "purpose")
                    ]
                    output.quality_scores["overall"] = sum(scores) / len(scores)
                output.quality_scores = compute_cr_aap(output.quality_scores)

            output.temporal_context = data.get("temporal_context", "")

            # === Accessibility notes ===
            raw_notes = data.get("accessibility_notes", [])
            output.accessibility_notes = [n for n in raw_notes if isinstance(n, dict) and n.get("barrier")]

        except Exception as e:
            output.success = False
            output.error = f"Combined parse error: {str(e)}"

        return output


class ParallelReaderExecutor:
    """
    Executes multiple Reader agents in parallel and merges results.
    """

    def __init__(
        self,
        llm: BaseLLMAdapter,
        max_workers: Optional[int] = None,
        timeout: float = 60.0,
        language: str = "en",
    ):
        """
        Initialize parallel executor.

        Args:
            llm: LLM adapter for readers
            max_workers: Maximum concurrent readers (None = use config default)
            timeout: Timeout per page in seconds
            language: Prompt language ("en" or "cn")
        """
        config = get_reader_config()
        self.llm = llm
        self.max_workers = max_workers if max_workers is not None else config.max_workers
        self.timeout = timeout
        self.language = language

    def read_pages(
        self,
        pages: List[Dict],
        task_content: str,
        acceptance_criteria: List[str],
        task_goal: str = "",
        task_context: str = "",
        constraints: Optional[List[str]] = None,
        known_findings: Optional[Dict[str, str]] = None,
        known_gaps: Optional[List[str]] = None,
        original_question: str = "",
    ) -> MergedReaderResults:
        """
        Read multiple pages in parallel.

        Args:
            pages: List of page dicts with content and metadata
            task_content: Task description (legacy, kept for compatibility)
            acceptance_criteria: List of criteria to extract
            task_goal: Clear task objective
            task_context: Background knowledge from cognitive graph parent node
            constraints: List of constraints that apply to all criteria
            known_findings: Previously found information
            known_gaps: Known information gaps

        Returns:
            MergedReaderResults with all extracted information
        """
        if not pages:
            return MergedReaderResults()

        constraints = constraints or []
        known_findings = known_findings or {}
        known_gaps = known_gaps or []

        # Prepare reader inputs
        reader_inputs = []
        for page in pages:
            reader_input = ReaderInput(
                page_index=page.get("index", 0),
                page_url=page.get("url", ""),
                page_title=page.get("title", ""),
                page_content=page.get("content", ""),
                page_domain=page.get("domain", ""),
                date_published=page.get("date_published"),
                task_goal=task_goal,
                task_context=task_context,
                constraints=constraints,
                acceptance_criteria=acceptance_criteria,
                task_content=task_content,  # Legacy
                known_findings=known_findings,
                known_gaps=known_gaps,
                original_question=original_question,
            )
            reader_inputs.append(reader_input)

        # Execute in parallel
        outputs: List[ReaderOutput] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for reader_input in reader_inputs:
                reader = ReaderAgent(self.llm, language=self.language)
                future = executor.submit(reader.read_page, reader_input)
                futures[future] = reader_input.page_index

            try:
                for future in as_completed(futures, timeout=self.timeout * len(reader_inputs)):
                    try:
                        output = future.result(timeout=self.timeout)
                        outputs.append(output)
                        logging.info(
                            f"✅ [Reader:{output.page_index}] Completed: "
                            f"{len(output.findings)} findings, {len(output.gaps)} gaps"
                        )
                    except Exception as e:
                        page_index = futures[future]
                        print(f"\n🔴 [Reader:{page_index}] {type(e).__name__}: {str(e)[:300]}")
                        logging.error(f"❌ [Reader:{page_index}] Failed: {e}\n{traceback.format_exc()}")
                        outputs.append(
                            ReaderOutput(
                                page_index=page_index,
                                page_url="",
                                page_title="",
                                success=False,
                                error=f"{type(e).__name__}: {str(e)}",
                            )
                        )
            except TimeoutError:
                completed_indices = {o.page_index for o in outputs}
                for future, page_index in futures.items():
                    if page_index not in completed_indices:
                        logging.warning(f"⏰ [Reader:{page_index}] Timed out, skipping")
                        future.cancel()
                        outputs.append(
                            ReaderOutput(
                                page_index=page_index,
                                page_url="",
                                page_title="",
                                success=False,
                                error="TimeoutError: reader deadline exceeded",
                            )
                        )

        # Merge results
        return self._merge_results(outputs, acceptance_criteria)

    def _merge_results(self, outputs: List[ReaderOutput], acceptance_criteria: List[str]) -> MergedReaderResults:
        """Merge multiple ReaderOutputs into a single result with dual-track aggregation."""

        merged = MergedReaderResults()

        # Track which criteria have been answered
        answered_criteria = set()

        for output in outputs:
            if output.success:
                merged.pages_processed.append(output.page_index)

                # === Part A: Structured Answer Merging ===
                # Merge findings (now with evidence: {answer, evidence})
                for criterion, finding_data in (output.findings or {}).items():
                    if criterion not in merged.all_findings:
                        merged.all_findings[criterion] = []
                    merged.all_findings[criterion].append((output.page_index, finding_data))
                    answered_criteria.add(criterion)

                # === Part B: Unexpected Discoveries Merging ===
                for insight in output.unexpected_insights or []:
                    insight_copy = insight.copy()
                    insight_copy["page_index"] = output.page_index
                    merged.all_unexpected_insights.append(insight_copy)

                # Store quality
                merged.quality_by_page[output.page_index] = output.quality_scores

                # Collect accessibility barriers
                for note in output.accessibility_notes or []:
                    note_copy = note.copy()
                    note_copy["page_index"] = output.page_index
                    merged.accessibility_notes.append(note_copy)

            else:
                merged.failed_pages.append(output.page_index)

        # Calculate remaining gaps
        for criterion in acceptance_criteria:
            if criterion not in answered_criteria:
                merged.remaining_gaps.append(criterion)

        # Build temporal summary
        temporal_notes = [
            f"[{o.page_index}]: {o.temporal_context}" for o in outputs if o.success and o.temporal_context
        ]
        merged.temporal_summary = " | ".join(temporal_notes) if temporal_notes else ""

        return merged

    def format_summary(self, results: MergedReaderResults) -> str:
        """
        Format merged results as a concise summary for Searcher.

        This summary replaces the full page content in Searcher's context.
        Includes both Part A (structured) and Part B (exploratory) content.
        """
        lines = ["## Reader Summary"]
        lines.append("")

        # Statistics
        lines.append(f"**Pages processed**: {len(results.pages_processed)} | **Failed**: {len(results.failed_pages)}")
        lines.append("")

        # ===== Part A: Structured Answer =====
        lines.append("## Part A: Structured Answer")
        lines.append("")

        # Findings by criteria (with evidence for verification)
        if results.all_findings:
            lines.append("### Findings by Criteria")
            for criterion, findings_list in results.all_findings.items():
                lines.append(f"**{criterion}**:")
                for page_idx, finding in findings_list:
                    answer = finding.get("answer", "") if isinstance(finding, dict) else str(finding)
                    evidence = finding.get("evidence", "") if isinstance(finding, dict) else ""
                    lines.append(f"  - [[{page_idx}]] {answer}")
                    if evidence:
                        lines.append(f"    📋 Evidence: {evidence}")
            lines.append("")

        # Remaining gaps
        if results.remaining_gaps:
            lines.append("### Information Gaps")
            for gap in results.remaining_gaps:
                lines.append(f"- ❌ {gap}")
            lines.append("")

        # ===== Part B: Unexpected Discoveries =====
        if results.all_unexpected_insights:
            lines.append("## Part B: Unexpected Discoveries")
            lines.append("")
            for insight in results.all_unexpected_insights:
                page_idx = insight.get("page_index", "?")
                label = insight.get("label", "")
                text = insight.get("insight", "")
                evidence = insight.get("evidence", "")
                label_prefix = f"**[{label}]** " if label else ""
                lines.append(f"- 💡 [[{page_idx}]] {label_prefix}{text}")
                if evidence:
                    lines.append(f"    📋 Evidence: {evidence}")
            lines.append("")

        # ===== Quality & Metadata =====
        # Quality summary with CR/AAP composites
        if results.quality_by_page:
            lines.append("### Quality Scores (CR=info density, AAP=trustworthiness)")
            for page_idx, scores in sorted(results.quality_by_page.items()):
                cr = float(scores.get("cr", 0) or 0)
                aap = float(scores.get("aap", 0) or 0)
                # Emoji: 🟢=EXPLOIT-ready (CR≥4.0+AAP≥3.5), 🟡=usable, 🔴=weak
                emoji = "🟢" if (cr >= 4.0 and aap >= 3.5) else "🟡" if cr >= 3.0 else "🔴"
                lines.append(
                    f"- [{page_idx}] {emoji} CR:{cr:.1f} AAP:{aap:.1f} "
                    f"(C:{float(scores.get('currency', 0) or 0):.0f} R:{float(scores.get('relevance', 0) or 0):.0f} "
                    f"A:{float(scores.get('authority', 0) or 0):.0f} Ac:{float(scores.get('accuracy', 0) or 0):.0f} "
                    f"P:{float(scores.get('purpose', 0) or 0):.0f})"
                )
            lines.append("")

        # Accessibility barriers
        if results.accessibility_notes:
            lines.append("### Accessibility Barriers")
            for note in results.accessibility_notes:
                page_idx = note.get("page_index", "?")
                barrier = note.get("barrier", "unknown")
                detail = note.get("detail", "")
                hint = note.get("alternative_hint", "")
                line = f"- [[{page_idx}]] ⚠️ {barrier}: {detail}"
                if hint:
                    line += f" (hint: {hint})"
                lines.append(line)
            lines.append("")

        # Temporal context
        if results.temporal_summary:
            lines.append(f"### Temporal Context\n{results.temporal_summary}")
            lines.append("")

        return "\n".join(lines)
