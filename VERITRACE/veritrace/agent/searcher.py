"""Search, filter, and read evidence for one research task."""

import logging
import re
import uuid
from concurrent.futures import CancelledError
from .concurrency import ThreadPoolExecutor, check_cancelled
from .runtime.progress import report_progress
from typing import Any, Dict, List, Optional

from .core import (
    BaseLLMAdapter,
    Message,
    WebSearchTool,
)
from .core.tools import _SEARCH_RESULTS_CACHE
from .prompts.searcher import SEARCHER_PROMPT_EN, SEARCHER_PROMPT_CN
from .quality_checker import SourceQualityEvaluator
from .reader import ParallelReaderExecutor, MergedReaderResults
from .prefilter import PreFilter, PreFilterResult
from .config import get_searcher_config


class SearcherAgent:
    """
    Searcher agent that performs web searches for a single question.

    Uses WebSearchTool for actual searches and LLM for summarization.
    """

    def __init__(
        self,
        llm: BaseLLMAdapter,
        search_engine: Optional[str] = None,
        max_results: Optional[int] = None,
        user_input_template: str = "## Question\n{question}",
        user_context_template: Optional[str] = None,
        language: str = "cn",
        reader_llm: Optional[BaseLLMAdapter] = None,
        prefilter_llm: Optional[BaseLLMAdapter] = None,
        quality_llm: Optional[BaseLLMAdapter] = None,
        enable_three_stage: Optional[bool] = None,
        max_turn: Optional[int] = None,
    ):
        searcher_cfg = get_searcher_config()

        self.llm = llm
        self.search_engine = search_engine if search_engine is not None else searcher_cfg.search_engine
        logging.info(f"🔧 [SearcherAgent] 初始化搜索引擎: {self.search_engine}")
        self.max_results = max_results if max_results is not None else searcher_cfg.max_results
        self.max_turn = max_turn if max_turn is not None else searcher_cfg.max_turn
        if self.max_turn < 1:
            raise ValueError("SEARCH_MAX_TURN must be at least 1")
        self.user_input_template = user_input_template
        self.user_context_template = user_context_template
        self.language = language
        self.memory: List[Message] = []

        self.reader_llm = reader_llm or llm
        self.reader_executor = ParallelReaderExecutor(self.reader_llm, language=language)

        self.enable_three_stage = (
            enable_three_stage if enable_three_stage is not None else searcher_cfg.enable_three_stage
        )
        self.prefilter_llm = prefilter_llm or reader_llm or llm
        self.prefilter = PreFilter(self.prefilter_llm, language=language)

        self.current_prefilter_result: Optional[PreFilterResult] = None

        self.extracted_findings: Dict[str, List[str]] = {}
        self.remaining_gaps: List[str] = []

        self.quality_evaluator = SourceQualityEvaluator(language=language)
        self.retry_count = 0
        self.max_retries = 2
        self.all_quality_results = []

        self.evidence_records: List[Dict] = []

    def _execute_search(self, query: str) -> tuple:
        """
        Execute a single search query.

        Returns:
            tuple: (results_text, cache_key, raw_results)
        """
        logging.debug(f"🔍 [Searcher] 查询: {query} (引擎: {self.search_engine})")
        report_progress("searching")
        tool = WebSearchTool(
            query=query,
            max_results=self.max_results,
            search_engine=self.search_engine,
        )
        result = tool.execute()

        if result.success:
            cache_key = result.output.get("cache_key", "")
            if cache_key:
                shared = _SEARCH_RESULTS_CACHE.get(cache_key, [])

                raw_results = [dict(r) for r in shared]
                call_key = f"web_{uuid.uuid4().hex[:8]}"
                _SEARCH_RESULTS_CACHE[call_key] = raw_results
                logging.info(f"✅ [Searcher] 查询完成: {len(raw_results)}条结果")
                results_text = self._format_search_results(raw_results)
                return results_text, call_key, raw_results
        else:
            logging.error(f"❌ [Searcher] 查询失败: {result.error}")

        return None, None, []

    def _format_search_results(self, results: List[Dict]) -> str:
        """Format search results for display."""
        lines = []
        for r in results:
            lines.append(f"[{r['index']}] {r['title']}")
            lines.append(f"    URL: {r['url']}")
            if r.get("snippet"):
                lines.append(f"    {r['snippet'][:200]}...")
            lines.append("")
        return "\n".join(lines)

    def _rebase_search_indices(self, raw_results, cache_key, base, results_text):
        """Rebase page indices within a Searcher task and refresh its isolated search cache."""
        from .core.tools import _SEARCH_RESULTS_CACHE

        if base and raw_results:
            for r in raw_results:
                r["index"] = r.get("index", 0) + base
            _SEARCH_RESULTS_CACHE[cache_key] = raw_results
            results_text = self._format_search_results(raw_results)
        return results_text, base + len(raw_results or [])

    def _get_system_prompt(self) -> str:
        """Get searcher system prompt based on language with dynamic date."""
        from datetime import datetime

        current_date = datetime.now().strftime("%Y-%m-%d")

        if self.language == "cn":
            base = SEARCHER_PROMPT_CN
            date_info = f"## 当前日期\n{current_date}\n用于判断信息时效性，理解相对时间表达（如'最近'、'今年'）。\n\n"
            return date_info + base
        else:
            base = SEARCHER_PROMPT_EN
            date_info = f"## Current Date\n{current_date}\nUse this to assess information timeliness and interpret relative time expressions (e.g., 'recently', 'this year').\n\n"
            return date_info + base

    def search_and_summarize(
        self,
        question: str,
        topic: str,
        history: Optional[List[Dict]] = None,
        cognitive_background: Optional[str] = None,
        original_question: str = "",
    ) -> Dict[str, Any]:
        """Search for information and generate a summary with optional deep reading."""
        import re
        import logging
        from .core.tools import PageSelectTool

        self.cognitive_background = cognitive_background or ""
        self.original_question = original_question
        self.retry_count = 0
        self.all_quality_results = []
        self.extracted_findings = {}
        self.remaining_gaps = []
        self.memory = []
        self.evidence_records = []

        self.current_prefilter_result = None

        input_parts = []
        if history and self.user_context_template:
            for item in history:
                input_parts.append(self.user_context_template.format_map(item))

        input_parts.append(self.user_input_template.format(question=question, topic=topic))
        user_input = "\n\n".join(input_parts)

        messages = [
            Message(role="system", content=self._get_system_prompt()),
            Message(role="user", content=user_input),
        ]

        ref2url = {}
        cache_key = None
        next_ref_base = 0
        max_turns = self.max_turn
        final_answer = None
        api_error = None
        has_any_search_results = False

        for turn in range(max_turns):
            check_cancelled()
            logging.info(f"[Searcher] Turn {turn + 1}/{max_turns}")

            try:
                response = self.llm.chat(messages)
                check_cancelled()
                response_text = response.content
            except CancelledError:
                raise
            except Exception as e:
                logging.exception("[Searcher] LLM API call failed")

                if final_answer:
                    break
                api_error = f"{type(e).__name__}: {e}"
                final_answer = f"[Error: LLM API failed - {e}]"
                break
            logging.info(f"[Searcher] LLM response length: {len(response_text)}")

            tool_call, parse_error = self._parse_tool_call_with_error(response_text)

            if not tool_call:
                if parse_error:
                    logging.warning(f"[Searcher] 工具调用解析失败: {parse_error}")
                    messages.append(Message(role="assistant", content=response_text))

                    extra_hint = ""
                    if "control character" in parse_error.lower():
                        extra_hint = """
**⚠️ Common Mistake**: You have literal newlines inside JSON strings.
JSON strings must be on a single line, or use `\\n` for newlines.

❌ Wrong:
```json
{{"reason": "Line 1
Line 2"}}
```

✅ Correct:
```json
{{"reason": "Line 1. Line 2"}}
```
"""

                    messages.append(
                        Message(
                            role="user",
                            content=f"""⚠️ **Tool Call Error**: Your tool call could not be parsed.

**Error**: {parse_error}
{extra_hint}
**Correct Format** (use ```json code block, keep strings on single line):
```json
{{"tool": "web_search", "arguments": {{"query": "your search query"}}}}
```

Please fix the JSON format and try again.""",
                        )
                    )
                    continue

                if not has_any_search_results:
                    logging.warning("[Searcher] ❌ 尝试直接回答但未执行任何搜索，强制要求搜索")
                    messages.append(Message(role="assistant", content=response_text))
                    messages.append(
                        Message(
                            role="user",
                            content="⚠️ You MUST search before answering. Use `web_search` or `multi_search` tool first. Direct answers without web sources are NOT allowed.",
                        )
                    )
                    continue

                logging.info("[Searcher] 直接回答（已有搜索结果）")
                if self.all_quality_results:
                    quality_section = self.quality_evaluator.format_quality_summary(self.all_quality_results)
                    final_answer = f"{response_text}\n\n---\n\n{quality_section}"
                else:
                    final_answer = response_text
                break

            tool_name = tool_call.get("tool", "")
            arguments = tool_call.get("arguments", {})
            logging.debug(f"[Searcher] Tool call: {tool_name}, args: {arguments}")

            if tool_name == "web_search":
                query = arguments.get("query", question)
                logging.debug(f"🔍 [Searcher] 正在搜索: {query}")

                results_text, cache_key, raw_results = self._execute_search(query)

                if not results_text:
                    logging.warning("⚠️ [Searcher] 搜索失败: 无结果")
                    messages.append(Message(role="assistant", content=response_text))
                    messages.append(
                        Message(
                            role="user",
                            content=f"⚠️ Search returned 0 results for: '{query}'\n\n**Follow the search strategy in your instructions:**\n- SPECIFIED SOURCE task: Try site: operator variations, then switch to OPEN EXPLORATION\n- OPEN EXPLORATION task: Use multi_search with different angles (not synonyms!) + bilingual queries\n\n❌ Do NOT answer based on your knowledge. You MUST find web sources.",
                        )
                    )
                    continue

                num_results = len(raw_results)
                has_any_search_results = True
                logging.info(f"✅ [Searcher] 搜索完成: 找到{num_results}条结果")

                results_text, next_ref_base = self._rebase_search_indices(
                    raw_results, cache_key, next_ref_base, results_text
                )

                for r in raw_results:
                    ref2url[r["index"]] = {
                        "url": r["url"],
                        "title": r["title"],
                        "snippet": r.get("snippet", ""),
                    }

                messages.append(Message(role="assistant", content=response_text))

                if self.enable_three_stage and len(raw_results) >= 5:
                    acceptance_criteria = self._extract_acceptance_criteria(question)
                    constraints = self._extract_constraints(question)

                    three_stage_results = self._run_prefilter(
                        raw_results=raw_results,
                        task_content=question,
                        acceptance_criteria=acceptance_criteria,
                        constraints=constraints,
                    )

                    messages.append(Message(role="user", content=three_stage_results))
                else:
                    messages.append(
                        Message(
                            role="user",
                            content=f"## Search Results\n\n{results_text}\n\nReview these results. Use `select_pages` to read full content of the most relevant pages (required before answering).",
                        )
                    )

            elif tool_name == "select_pages":
                indices = arguments.get("indices", [])

                MAX_PAGES_PER_ROUND = 15
                if len(indices) > MAX_PAGES_PER_ROUND:
                    logging.warning(f"⚠️ [Searcher] 选取了{len(indices)}个页面，截断为{MAX_PAGES_PER_ROUND}个")
                    indices = indices[:MAX_PAGES_PER_ROUND]
                logging.info(f"📖 [Searcher] 正在精读页面: {indices} (共{len(indices)}个页面，请稍候...)")

                if not cache_key:
                    messages.append(Message(role="assistant", content=response_text))
                    messages.append(
                        Message(role="user", content="No search results available. Please use `web_search` first.")
                    )
                    continue

                page_tool = PageSelectTool(indices=indices, cache_key=cache_key)
                page_result = page_tool.execute()

                if not page_result.success:
                    messages.append(Message(role="assistant", content=response_text))
                    messages.append(
                        Message(
                            role="user",
                            content=f"Failed to read pages: {page_result.error}. Please provide your answer based on available information.",
                        )
                    )
                    continue

                pages_raw = page_result.output.get("pages_raw", [])
                num_pages = page_result.output.get("num_pages", len(indices))
                logging.info(f"✅ [Searcher] 页面获取完成: {num_pages}个页面")

                task_goal = self._extract_task_goal(question)
                constraints = self._extract_constraints(question)
                acceptance_criteria = self._extract_acceptance_criteria(question)

                logging.info(
                    f"📋 [Searcher] 提取Task结构: 目标={task_goal[:50]}..., 约束={len(constraints)}个, 验收要点={len(acceptance_criteria)}个"
                )

                logging.info("🔍 [Searcher] 使用并行 Reader 处理页面...")

                def _flatten_value(v):
                    if isinstance(v, list):
                        return " | ".join(_flatten_value(item) if isinstance(item, list) else str(item) for item in v)
                    return str(v)

                known_findings_flat = {k: _flatten_value(v) for k, v in self.extracted_findings.items()}

                reader_results = self.reader_executor.read_pages(
                    pages=pages_raw,
                    task_content=question,
                    acceptance_criteria=acceptance_criteria,
                    task_goal=task_goal,
                    task_context=self.cognitive_background,
                    constraints=constraints,
                    known_findings=known_findings_flat,
                    known_gaps=self.remaining_gaps,
                    original_question=self.original_question,
                )

                logging.info(
                    f"✅ [Searcher] Reader 处理完成: {len(reader_results.all_findings)}个要点有发现, {len(reader_results.remaining_gaps)}个空缺"
                )

                for criterion, findings_list in reader_results.all_findings.items():
                    if criterion not in self.extracted_findings:
                        self.extracted_findings[criterion] = []
                    for page_idx, answer in findings_list:
                        self.extracted_findings[criterion].append(answer)
                self.remaining_gaps = reader_results.remaining_gaps

                for criterion, findings_list in reader_results.all_findings.items():
                    for page_idx, finding_data in findings_list:
                        if (
                            isinstance(finding_data, dict)
                            and finding_data.get("evidence")
                            and finding_data["evidence"].strip()
                        ):
                            self.evidence_records.append(
                                {
                                    "ref_idx": page_idx,
                                    "url": ref2url.get(page_idx, {}).get("url", "")
                                    if isinstance(ref2url.get(page_idx), dict)
                                    else "",
                                    "title": ref2url.get(page_idx, {}).get("title", "")
                                    if isinstance(ref2url.get(page_idx), dict)
                                    else "",
                                    "evidence_quote": str(finding_data["evidence"]),
                                    "finding_answer": str(finding_data.get("answer", "")),
                                    "criterion": str(criterion),
                                }
                            )

                def _page_info(pidx):
                    info = ref2url.get(pidx, {})
                    if isinstance(info, dict):
                        return info.get("url", ""), info.get("title", "")
                    return "", ""

                part_b_count = 0
                for item in reader_results.all_unexpected_insights:
                    if isinstance(item, dict) and item.get("evidence", "").strip():
                        pidx = item.get("page_index")
                        if pidx is not None:
                            url, title = _page_info(pidx)

                            label = str(item.get("label", "unexpected_insight"))
                            self.evidence_records.append(
                                {
                                    "ref_idx": pidx,
                                    "url": url,
                                    "title": title,
                                    "evidence_quote": str(item["evidence"]),
                                    "finding_answer": str(item.get("insight", "")),
                                    "criterion": label,
                                }
                            )
                            part_b_count += 1

                if part_b_count > 0:
                    logging.info(
                        f"📋 [Searcher] Part B: {part_b_count} unexpected discovery evidence records collected"
                    )

                reader_summary = self._format_reader_summary(reader_results, ref2url)

                from .quality_checker import SourceQuality

                quality_results = []
                for page_idx, scores in reader_results.quality_by_page.items():
                    page_info = ref2url.get(page_idx, {})
                    quality_results.append(
                        SourceQuality(
                            index=page_idx,
                            url=page_info.get("url", ""),
                            title=page_info.get("title", ""),
                            currency=scores.get("currency", 3),
                            relevance=scores.get("relevance", 3),
                            authority=scores.get("authority", 3),
                            accuracy=scores.get("accuracy", 3),
                            purpose=scores.get("purpose", 3),
                            overall=scores.get("overall", 3),
                            reasoning="Evaluated by Reader Agent",
                            cr=scores.get("cr", 0),
                            aap=scores.get("aap", 0),
                        )
                    )
                self.all_quality_results.extend(quality_results)

                MAX_QUALITY_RESULTS = 200
                if len(self.all_quality_results) > MAX_QUALITY_RESULTS:
                    self.all_quality_results = self.all_quality_results[-MAX_QUALITY_RESULTS:]
                    logging.debug(f"[Searcher] 质量结果裁剪至 {MAX_QUALITY_RESULTS} 条")

                high_quality_count = sum(1 for q in quality_results if q.cr >= 4.0 and q.aap >= 3.5)
                force_retry = self.quality_evaluator.should_force_retry(quality_results) if quality_results else False
                quality_level = self.quality_evaluator.get_quality_level(quality_results) if quality_results else "low"
                logging.info(
                    f"📊 [Searcher] 质量评估: {high_quality_count}/{len(quality_results)}个高质量来源 (级别: {quality_level}, 强制重搜: {force_retry})"
                )

                messages.append(Message(role="assistant", content=response_text))

                if force_retry:
                    messages.append(
                        Message(
                            role="user",
                            content=f"{reader_summary}\n\n⚠️ **Quality Warning**: All sources have CR below 3.0 — insufficient information density. Please write your answer with available information and note this in your **Feedback for Planner** section.",
                        )
                    )
                else:
                    messages.append(
                        Message(
                            role="user",
                            content=f"{reader_summary}\n\nReview the findings and quality scores. Note any cross-source contradictions or single-source claims. Write your answer based on the information gathered.",
                        )
                    )

            elif tool_name == "multi_search":
                queries = arguments.get("queries", [])
                if not queries:
                    messages.append(Message(role="assistant", content=response_text))
                    messages.append(
                        Message(
                            role="user",
                            content="multi_search requires a 'queries' array. Please provide at least one query.",
                        )
                    )
                    continue

                logging.info(f"🔍 [Searcher] 执行多关键词并行搜索: {len(queries)}个查询")
                for i, q in enumerate(queries[:5]):
                    logging.debug(f"   {i + 1}. {q[:50]}...")

                results_text, new_cache_key, raw_results = self._execute_multi_search(queries[:5])

                if not results_text:
                    logging.warning("⚠️ [Searcher] 多关键词搜索失败: 无结果")
                    messages.append(Message(role="assistant", content=response_text))
                    messages.append(
                        Message(
                            role="user",
                            content="⚠️ Multi-search returned 0 results.\n\n**Try different search angles:**\n- Shorter keywords (2-5 words each)\n- Different perspectives (not synonyms!)\n- Bilingual: English + Chinese required\n- Check if Task Type suggests specific source strategy\n\n❌ Do NOT answer based on your knowledge. You MUST find web sources.",
                        )
                    )
                    continue

                cache_key = new_cache_key
                num_results = len(raw_results)
                has_any_search_results = True
                logging.info(f"✅ [Searcher] 多关键词搜索完成: 找到{num_results}条去重结果")

                results_text, next_ref_base = self._rebase_search_indices(
                    raw_results, cache_key, next_ref_base, results_text
                )

                for r in raw_results:
                    ref2url[r["index"]] = {
                        "url": r["url"],
                        "title": r["title"],
                        "snippet": r.get("snippet", ""),
                    }

                messages.append(Message(role="assistant", content=response_text))

                if self.enable_three_stage and len(raw_results) >= 5:
                    acceptance_criteria = self._extract_acceptance_criteria(question)
                    constraints = self._extract_constraints(question)

                    three_stage_results = self._run_prefilter(
                        raw_results=raw_results,
                        task_content=question,
                        acceptance_criteria=acceptance_criteria,
                        constraints=constraints,
                    )

                    messages.append(Message(role="user", content=three_stage_results))
                else:
                    messages.append(
                        Message(
                            role="user",
                            content=f"## Multi-Search Results ({num_results} unique results from {len(queries)} queries)\n\n{results_text}\n\nReview these results. Use `select_pages` to read the most relevant pages.",
                        )
                    )

            else:
                logging.warning(f"[Searcher] Unknown tool: {tool_name}")
                messages.append(Message(role="assistant", content=response_text))
                messages.append(
                    Message(
                        role="user",
                        content=f"Unknown tool '{tool_name}'. Available: web_search, multi_search, select_pages. Or just write your answer directly (no tool call needed).",
                    )
                )

        if final_answer is None:
            logging.warning("[Searcher] 达到最大轮次，生成最终回答")

            if messages and messages[-1].role == "user":
                messages.append(
                    Message(
                        role="user",
                        content="⏰ **Time's up!** Please write your final answer now based on all the information gathered. No more tool calls allowed.",
                    )
                )

                try:
                    response = self.llm.chat(messages)
                    check_cancelled()
                    response_text = response.content
                    logging.info(f"[Searcher] 最终回答生成完成: {len(response_text)}字符")
                except CancelledError:
                    raise
                except Exception as e:
                    logging.exception("[Searcher] Final answer generation failed")
                    api_error = f"{type(e).__name__}: {e}"
                    response_text = "[Error: Failed to generate final answer]"

            if self.all_quality_results:
                quality_section = self.quality_evaluator.format_quality_summary(self.all_quality_results)
                final_answer = f"{response_text}\n\n---\n\n{quality_section}"
            else:
                final_answer = response_text

        self.memory = [{"role": m.role, "content": m.content} for m in messages]

        if final_answer and final_answer.lstrip().startswith("```"):
            import re

            final_answer = re.sub(r"^```\w*\n?", "", final_answer.strip())
            final_answer = re.sub(r"\n?```$", "", final_answer).strip()

        self.memory.append({"role": "assistant", "content": final_answer})

        quality_results_serializable = []
        for q in self.all_quality_results:
            quality_results_serializable.append(
                {
                    "index": q.index,
                    "title": q.title,
                    "url": q.url,
                    "overall": q.overall,
                    "currency": q.currency,
                    "relevance": q.relevance,
                    "authority": q.authority,
                    "accuracy": q.accuracy,
                    "purpose": q.purpose,
                    "reasoning": q.reasoning,
                    "cr": q.cr,
                    "aap": q.aap,
                }
            )

        feedback = self._parse_feedback_from_answer(final_answer)
        if api_error:
            feedback = {"can_improve": True, "reason": f"LLM API error: {api_error}"}
        if feedback:
            logging.debug("📤 [Searcher] ===== Feedback for Planner =====")
            logging.debug(f"   can_improve: {feedback.get('can_improve')}")
            logging.debug(f"   reason: {feedback.get('reason', '')[:100]}...")
            logging.debug("   ========================================")
        else:
            logging.warning("⚠️ [Searcher] 未能解析 Feedback for Planner 块")

        if final_answer:
            cited_indices = {int(n) for n in re.findall(r"\[\[(\d+)\]\]", final_answer)}
            read_indices = {r["ref_idx"] for r in self.evidence_records}
            unread_cited = cited_indices - read_indices

            backfilled = 0
            for page_idx in unread_cited:
                page_info = ref2url.get(page_idx)
                if isinstance(page_info, dict) and page_info.get("snippet", "").strip():
                    self.evidence_records.append(
                        {
                            "ref_idx": page_idx,
                            "url": page_info.get("url", ""),
                            "title": page_info.get("title", ""),
                            "evidence_quote": page_info["snippet"],
                            "finding_answer": page_info["snippet"],
                            "criterion": page_info.get("title", ""),
                        }
                    )
                    backfilled += 1

            if backfilled:
                logging.info(
                    f"[Searcher] Backfilled {backfilled} snippet evidence records for cited-but-unread pages: {sorted(unread_cited)}"
                )

        result = {
            "content": final_answer,
            "ref2url": ref2url,
            "memory": self.memory,
            "quality_results": quality_results_serializable,
            "feedback": feedback,
            "evidence_records": self.evidence_records,
        }
        if api_error:
            result["error"] = api_error
        return result

    def _parse_tool_call_with_error(self, text: str) -> tuple[Optional[Dict], Optional[str]]:
        """Parse tool call from LLM response with error reporting."""
        import json
        import re
        import logging

        def _find_json_by_brace_matching(text: str, start_pos: int) -> Optional[str]:
            """Find complete JSON object starting from start_pos using brace matching."""
            depth = 0
            in_string = False
            escape_next = False

            for i, char in enumerate(text[start_pos:], start_pos):
                if escape_next:
                    escape_next = False
                    continue

                if char == "\\" and in_string:
                    escape_next = True
                    continue

                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue

                if in_string:
                    continue

                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start_pos : i + 1]
            return None

        last_error = None

        code_block_match = re.search(r"```json\s*(\{)", text, re.DOTALL)
        if code_block_match:
            brace_start = code_block_match.start(1)
            json_str = _find_json_by_brace_matching(text, brace_start)
            if json_str:
                try:
                    parsed = json.loads(json_str)
                    if "tool" in parsed:
                        logging.debug(f"[_parse_tool_call] Pattern 1 matched: {list(parsed.keys())}")
                        return parsed, None
                except json.JSONDecodeError as e:
                    last_error = f"JSON syntax error in ```json block: {str(e)}"
                    logging.debug(f"[_parse_tool_call] Pattern 1 JSON error: {e}")
            else:
                last_error = "Found ```json block but could not extract complete JSON (unmatched braces)"

        tool_start = text.find('"tool"')
        if tool_start != -1:
            brace_start = text.rfind("{", 0, tool_start)
            if brace_start != -1:
                json_str = _find_json_by_brace_matching(text, brace_start)
                if json_str:
                    try:
                        parsed = json.loads(json_str)
                        if "tool" in parsed:
                            logging.debug(f"[_parse_tool_call] Pattern 2 matched: {list(parsed.keys())}")
                            return parsed, None
                    except json.JSONDecodeError as e:
                        if not last_error:
                            last_error = f"JSON syntax error: {str(e)}"
                        logging.debug(f"[_parse_tool_call] Pattern 2 JSON error: {e}")
                else:
                    if not last_error:
                        last_error = 'Found "tool" keyword but could not extract complete JSON object'

        if not last_error:
            tool_indicators = ['"tool":', "web_search", "multi_search", "select_pages"]
            for indicator in tool_indicators:
                if indicator in text:
                    last_error = f"Detected tool call intent ('{indicator}') but JSON parsing failed"
                    break

        if last_error:
            logging.warning(f"[_parse_tool_call] Tool call parsing failed: {last_error}")
            return None, last_error

        logging.debug(f"[_parse_tool_call] No tool call found in text ({len(text)} chars)")
        return None, None

    def _parse_feedback_from_answer(self, answer: str) -> Optional[Dict]:
        """Extract the Feedback for Planner block from the Searcher's final answer."""
        import json
        import re

        if not answer:
            return None

        feedback_section_match = re.search(
            r"###\s*Feedback\s+for\s+Planner\s*\n```json\s*\n(.*?)\n```", answer, re.DOTALL | re.IGNORECASE
        )

        if not feedback_section_match:
            feedback_section_match = re.search(
                r'Feedback\s+for\s+Planner.*?(\{[^{}]*"can_improve"[^{}]*\})', answer, re.DOTALL | re.IGNORECASE
            )

        if not feedback_section_match:
            logging.debug("[Searcher] No feedback block found in answer")
            return None

        json_str = feedback_section_match.group(1).strip()

        json_str = re.sub(
            r'(?<=": ")(.*?)(?=")', lambda m: m.group(1).replace("\n", " ").replace("\r", ""), json_str, flags=re.DOTALL
        )

        try:
            feedback = json.loads(json_str)

            if not isinstance(feedback, dict):
                logging.warning("[Searcher] Feedback must be a JSON object; received %s", type(feedback).__name__)
                return None

            if "can_improve" not in feedback:
                logging.warning("[Searcher] Missing can_improve field")
                feedback["can_improve"] = True

            if "reason" not in feedback:
                feedback["reason"] = ""

            return feedback

        except json.JSONDecodeError as e:
            logging.warning(f"[Searcher] Failed to parse feedback JSON: {e}")
            return None

    def _extract_acceptance_criteria(self, task_content: str) -> List[str]:
        """Extract acceptance criteria from Task content."""
        criteria = []

        ac_match = re.search(
            r"(?:Acceptance Criteria|验收要点)[:\s]*\n((?:[-*\d.]+\s*.+\n?)+)", task_content, re.IGNORECASE
        )

        if ac_match:
            ac_section = ac_match.group(1)

            for line in ac_section.split("\n"):
                line = line.strip()

                line = re.sub(r"^[-*\d.]+\s*", "", line)
                if line:
                    criteria.append(line)

        if not criteria:
            bullet_matches = re.findall(r"[-*\d.]+\s*(.+?)(?=\n|$)", task_content)
            for match in bullet_matches:
                match = match.strip()
                if match and len(match) > 10:
                    criteria.append(match)

        if not criteria:
            first_line = task_content.split("\n")[0].strip()
            if first_line:
                criteria.append(first_line)

        return criteria[:10]

    def _format_reader_summary(self, results: MergedReaderResults, ref2url: Dict) -> str:
        """Format Reader results as a concise summary for Searcher's context."""
        return self.reader_executor.format_summary(results)

    def _extract_constraints(self, task_content: str) -> List[str]:
        """Extract constraint preferences from Task content."""
        constraints = []

        constraint_match = re.search(
            r"(?:Constraints?|约束|限制)[:\s]*\n((?:[-*\d.]+\s*.+\n?)+)", task_content, re.IGNORECASE
        )

        if constraint_match:
            constraint_section = constraint_match.group(1)
            for line in constraint_section.split("\n"):
                line = line.strip()
                line = re.sub(r"^[-*\d.]+\s*", "", line)
                if line:
                    constraints.append(line)

        time_match = re.search(r"(20\d{2}年?|近\d+年|最近)", task_content)
        if time_match and not any("时间" in c or "time" in c.lower() for c in constraints):
            constraints.append(f"时间偏好: {time_match.group(0)}")

        return constraints[:5]

    def _extract_task_goal(self, task_content: str) -> str:
        """Extract task goal/objective from Task content."""

        goal_match = re.search(
            r"(?:Task\s*目标|Task\s*Goal|目标)[:\s]*(.+?)(?=\n\n|\n验收|\nAcceptance|\n约束|\nConstraint|\n上下文|\nContext|$)",
            task_content,
            re.IGNORECASE | re.DOTALL,
        )

        if goal_match:
            goal = goal_match.group(1).strip()

            goal = re.sub(r"\s+", " ", goal)
            return goal

        first_line = task_content.split("\n")[0].strip()

        first_line = re.sub(r"^(?:Task\s*[:：]?\s*)", "", first_line, flags=re.IGNORECASE)
        return first_line if first_line else ""

    def _run_prefilter(
        self,
        raw_results: List[Dict],
        task_content: str,
        acceptance_criteria: List[str],
        constraints: List[str],
    ) -> str:
        """Run PreFilter to classify search results, then let Searcher decide."""
        if not self.enable_three_stage:
            return self._format_search_results(raw_results)

        logging.info(f"🔍 [PreFilter] 开始分类 {len(raw_results)} 条结果...")

        self.current_prefilter_result = self.prefilter.filter(
            results=raw_results,
            task_content=task_content,
            acceptance_criteria=acceptance_criteria,
            constraints=constraints,
        )

        pf = self.current_prefilter_result
        logging.info(f"✅ [PreFilter] 分类完成: read={len(pf.read)}, skip={len(pf.skip)}")

        return self._format_prefilter_results(raw_results, pf)

    def _format_prefilter_results(
        self,
        raw_results: List[Dict],
        prefilter: PreFilterResult,
    ) -> str:
        """
        Format PreFilter results for Searcher.

        Shows all non-skipped results. Searcher decides which to deep read.
        """
        lines = []

        read_set = set(prefilter.read)

        lines.append("## 搜索结果预筛选")
        lines.append("")
        lines.append(f"**保留**: {len(prefilter.read)} | **已过滤**: {len(prefilter.skip)}")
        lines.append("")

        for r in raw_results:
            if r.get("index") in read_set:
                idx = r.get("index")
                title = r.get("title", "")[:80]
                url = r.get("url", "")
                snippet = r.get("snippet", "")[:300]
                lines.append(f"[{idx}] **{title}**")
                lines.append(f"    {url}")
                lines.append(f"    {snippet}")
                lines.append("")

        lines.append("---")
        lines.append("使用 `select_pages` 选择需要精读的页面。根据相关性选择 5-15 篇（上限15篇）。")

        return "\n".join(lines)

    def _execute_multi_search(self, queries: List[str]) -> tuple:
        """Execute multiple search queries in parallel and merge results."""
        all_results = []
        all_cache_keys = []

        with ThreadPoolExecutor(max_workers=len(queries)) as executor:
            futures = {}
            for query in queries:
                future = executor.submit(self._execute_search, query)
                futures[future] = query

            for future in futures:
                try:
                    results_text, cache_key, raw_results = future.result(timeout=30)
                    if raw_results:
                        offset = len(all_results)
                        for r in raw_results:
                            new_r = r.copy()
                            new_r["index"] = offset + r["index"]
                            all_results.append(new_r)
                        if cache_key:
                            all_cache_keys.append(cache_key)
                        logging.info(
                            f"✅ [Searcher] multi_search query '{futures[future][:30]}...': {len(raw_results)} results"
                        )
                except Exception as e:
                    logging.warning(f"⚠️ [Searcher] multi_search query failed: {e}")

        if not all_results:
            return None, None, []

        seen_urls = set()
        unique_results = []
        for r in all_results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                r["index"] = len(unique_results)
                unique_results.append(r)

        combined_cache_key = f"multi_{uuid.uuid4().hex[:8]}"
        _SEARCH_RESULTS_CACHE[combined_cache_key] = unique_results

        results_text = self._format_search_results(unique_results)

        logging.info(
            f"✅ [Searcher] multi_search completed: {len(unique_results)} unique results from {len(queries)} queries"
        )

        return results_text, combined_cache_key, unique_results
