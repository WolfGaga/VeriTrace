"""
Pre-filter Module - Search results pre-filtering before deep reading.

Lightweight filter that only removes completely off-topic or duplicate results.
Everything else is passed through for the Searcher to read.

Design rationale: User questions may reference incorrect claims or low-quality sources.
To verify and address these claims, the Searcher needs to read broadly — including
sources that might seem low-authority — so aggressive filtering is counterproductive.
"""

import json
import logging
import re
from concurrent.futures import as_completed
from .concurrency import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .core import BaseLLMAdapter, Message
from .config import get_prefilter_config
from .runtime.progress import report_progress


@dataclass
class PreFilterResult:
    """Pre-filter classification result."""

    read: List[int] = field(default_factory=list)  # Indices to read
    skip: List[int] = field(default_factory=list)  # Indices to skip (off-topic / duplicate)
    reasoning: str = ""  # Brief reasoning summary


class PreFilter:
    """
    Search results pre-filter - only removes completely off-topic or duplicate results.

    Designed to be lightweight, can use smaller models (e.g., Qwen-7B).
    Uses batch parallel processing for faster classification.
    """

    def __init__(
        self,
        llm: BaseLLMAdapter,
        batch_size: Optional[int] = None,
        max_workers: Optional[int] = None,
        language: str = "en",
    ):
        config = get_prefilter_config()
        self.llm = llm
        self.batch_size = batch_size if batch_size is not None else config.batch_size
        self.max_workers = max_workers if max_workers is not None else config.max_workers
        self.language = language

    def filter(
        self,
        results: List[Dict],
        task_content: str,
        acceptance_criteria: List[str],
        constraints: Optional[List[str]] = None,
    ) -> PreFilterResult:
        """
        Classify search results: read or skip.

        Only skips results that are completely off-topic or duplicate.
        """
        if not results:
            return PreFilterResult()

        report_progress("filtering", sources=len(results))
        # Split into batches
        batches = [results[i : i + self.batch_size] for i in range(0, len(results), self.batch_size)]

        if len(batches) == 1:
            return self._filter_batch(batches[0], task_content, acceptance_criteria, constraints)

        logging.info(f"[PreFilter] Processing {len(results)} results in {len(batches)} batches (parallel)")

        # Parallel batch processing
        batch_results: List[PreFilterResult] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._filter_batch, batch, task_content, acceptance_criteria, constraints): i
                for i, batch in enumerate(batches)
            }

            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    result = future.result()
                    batch_results.append((batch_idx, result))
                    logging.info(f"[PreFilter] Batch {batch_idx + 1}/{len(batches)} completed")
                except Exception as e:
                    logging.error(f"[PreFilter] Batch {batch_idx} failed: {e}")
                    batch = batches[batch_idx]
                    fallback = PreFilterResult(
                        read=[r.get("index", 0) for r in batch],
                        reasoning=f"Batch {batch_idx} failed: {e}, all marked as read",
                    )
                    batch_results.append((batch_idx, fallback))

        # Sort by batch index and merge
        batch_results.sort(key=lambda x: x[0])
        return self._merge_batch_results([r for _, r in batch_results])

    def _filter_batch(
        self,
        batch: List[Dict],
        task_content: str,
        acceptance_criteria: List[str],
        constraints: Optional[List[str]] = None,
    ) -> PreFilterResult:
        """Filter a single batch of results."""
        if self.language == "cn":
            prompt = self._build_prompt_cn(batch, task_content, acceptance_criteria, constraints)
        else:
            prompt = self._build_prompt(batch, task_content, acceptance_criteria, constraints)

        try:
            response = self.llm.chat([Message(role="user", content=prompt)])
            return self._parse_response(response.content, batch)
        except Exception as e:
            logging.error(f"[PreFilter] Batch error: {e}")
            return PreFilterResult(
                read=[r.get("index", 0) for r in batch], reasoning=f"Pre-filter failed: {e}, all marked as read"
            )

    def _merge_batch_results(self, results: List[PreFilterResult]) -> PreFilterResult:
        """Merge multiple batch results into one."""
        merged = PreFilterResult()
        reasonings = []

        for r in results:
            merged.read.extend(r.read)
            merged.skip.extend(r.skip)
            if r.reasoning:
                reasonings.append(r.reasoning)

        merged.reasoning = " | ".join(reasonings) if reasonings else ""
        return merged

    def _build_prompt(
        self,
        results: List[Dict],
        task_content: str,
        acceptance_criteria: List[str],
        constraints: Optional[List[str]] = None,
    ) -> str:
        """Build classification prompt."""

        criteria_text = (
            "\n".join(f"- {i + 1}. {c}" for i, c in enumerate(acceptance_criteria))
            if acceptance_criteria
            else "- Answer the main question"
        )

        constraints_text = ""
        if constraints:
            constraints_list = "\n".join(f"- {c}" for c in constraints)
            constraints_text = f"\n## Soft Constraints\n{constraints_list}\n"

        results_text = []
        for r in results:
            idx = r.get("index", 0)
            title = r.get("title", "Unknown")
            snippet = r.get("snippet", "")[:300]
            results_text.append(f"[{idx}] {title}\n{snippet}\n---")

        results_section = "\n".join(results_text)

        return f"""You are a search result filter. Your ONLY job is to remove results that are completely off-topic or duplicate. When in doubt, keep the result.

## Task
{task_content}

## Acceptance Criteria
{criteria_text}
{constraints_text}
## Search Results ({len(results)} items)
{results_section}

## Classification Rules
- **read**: Keep for reading. This is the DEFAULT — include anything that might be even loosely related to the task.
- **skip**: ONLY for results that are completely off-topic (zero connection to the task) or exact duplicates of another result.

## Output Format
Return ONLY a JSON object (no markdown):
{{
  "read": [0, 1, 3, 4, 5, 7, 8],
  "skip": [2, 6],
  "reasoning": "Brief explanation of why skipped items were removed"
}}

**Key**: When unsure, classify as read. It is far better to read an irrelevant page than to miss a relevant one."""

    def _build_prompt_cn(
        self,
        results: List[Dict],
        task_content: str,
        acceptance_criteria: List[str],
        constraints: Optional[List[str]] = None,
    ) -> str:
        """Build classification prompt (Chinese version)."""

        criteria_text = (
            "\n".join(f"- {i + 1}. {c}" for i, c in enumerate(acceptance_criteria))
            if acceptance_criteria
            else "- 回答主要问题"
        )

        constraints_text = ""
        if constraints:
            constraints_list = "\n".join(f"- {c}" for c in constraints)
            constraints_text = f"\n## 软约束\n{constraints_list}\n"

        results_text = []
        for r in results:
            idx = r.get("index", 0)
            title = r.get("title", "Unknown")
            snippet = r.get("snippet", "")[:300]
            results_text.append(f"[{idx}] {title}\n{snippet}\n---")

        results_section = "\n".join(results_text)

        return f"""你是搜索结果过滤器。你的唯一任务是移除完全离题或重复的结果。拿不准的时候，保留。

## 任务
{task_content}

## 验收标准
{criteria_text}
{constraints_text}
## 搜索结果（共 {len(results)} 条）
{results_section}

## 分类规则
- **read**: 保留供阅读。这是默认分类——任何可能与任务有关的结果都保留。
- **skip**: 仅用于完全离题（与任务毫无关联）或与其他结果完全重复的内容。

## 输出格式
仅返回 JSON 对象（不要 markdown）：
{{
  "read": [0, 1, 3, 4, 5, 7, 8],
  "skip": [2, 6],
  "reasoning": "简要说明被过滤项的移除原因"
}}

**关键**：拿不准时归为 read。多读一篇无关文章的代价远小于漏掉一篇有用文章。"""

    def _parse_response(self, response: str, batch: List[Dict]) -> PreFilterResult:
        """Parse LLM response into PreFilterResult."""

        result = PreFilterResult()
        valid_indices = set(r.get("index", i) for i, r in enumerate(batch))

        try:
            data = self._extract_json(response)

            if data:
                result.read = data.get("read") or []
                result.skip = data.get("skip") or []
                raw_reasoning = data.get("reasoning") or ""
                result.reasoning = raw_reasoning if isinstance(raw_reasoning, str) else str(raw_reasoning)

                # Validate indices
                result.read = [i for i in result.read if i in valid_indices]
                result.skip = [i for i in result.skip if i in valid_indices]

                # Unclassified → read (safe default)
                classified = set(result.read) | set(result.skip)
                unclassified = valid_indices - classified
                if unclassified:
                    result.read.extend(sorted(unclassified))
                    logging.warning(f"[PreFilter] {len(unclassified)} unclassified results added to read")

            else:
                result.read = list(valid_indices)
                result.reasoning = "JSON parsing failed, all marked as read"

        except Exception as e:
            result.read = list(valid_indices)
            result.reasoning = f"Parse error: {e}, all marked as read"

        return result

    def _extract_json(self, text: str) -> Optional[Dict]:
        """Extract JSON from response text."""

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

        json_match = re.search(r'\{[\s\S]*"read"[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return None
