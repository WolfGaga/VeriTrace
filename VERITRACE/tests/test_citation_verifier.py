"""Citation-verifier design experiments.

Goals:
1. Atomic verification: can a small model answer the Chinese example question
   "这句话的数据是否来自这条证据" correctly?
2. Correction: find the correct source among candidates for citations marked NO.
3. Hallucination detection: identify data absent from all evidence.
4. Format variation: match numeric forms such as 3.33:1 and the Chinese example 3.33比1.

Model: Qwen/Qwen3-30B-A3B-Instruct-2507
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

import re
import json
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from veritrace.agent.core.llm_adapter import SiliconFlowAdapter, Message


# ============================================================
# Test data loaded from external files
# ============================================================
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _load_synthetic_data():
    """Load synthetic evidence, preceding sections and test cases."""
    with open(os.path.join(_DATA_DIR, "synthetic_evidence_table.json"), "r", encoding="utf-8") as f:
        raw = json.load(f)
    evidence_table = {int(k): v for k, v in raw.items()}

    with open(os.path.join(_DATA_DIR, "synthetic_preceding_sections.txt"), "r", encoding="utf-8") as f:
        preceding_sections = f.read().strip()

    with open(os.path.join(_DATA_DIR, "synthetic_test_cases.json"), "r", encoding="utf-8") as f:
        test_cases = json.load(f)

    return evidence_table, preceding_sections, test_cases


EVIDENCE_TABLE, PRECEDING_SECTIONS, TEST_CASES = _load_synthetic_data()


# ============================================================
# Access helpers for the nested evidence table
# ============================================================


def get_full_reference(evidence_table, ref_idx):
    """Join all criterion and quote entries for one URL to approximate a full article."""
    url_data = evidence_table.get(ref_idx)
    if not url_data:
        return ""
    parts = []
    for entry in url_data["entries"]:
        parts.append(f"[{entry['criterion']}]\n{entry['quote']}")
    return "\n\n".join(parts)


def get_best_matching_entry(evidence_table, ref_idx, sentence):
    """Return the criterion and quote entry that best matches the sentence.

    Count numeric-token matches in each quote, then use character overlap to break ties.
    """
    url_data = evidence_table.get(ref_idx)
    if not url_data:
        return ""
    entries = url_data.get("entries", [])
    if not entries:
        return ""
    if len(entries) == 1:
        e = entries[0]
        return f"[{e['criterion']}]\n{e['quote']}"

    # Extract numeric tokens, including percentages, decimals and ratios.
    num_pattern = re.compile(r"[\d]+[.,\d]*[%‰]?(?::\d+)?")
    sentence_nums = set(num_pattern.findall(sentence))

    best_entry = entries[0]
    best_num_hits = -1
    best_char_overlap = -1

    for entry in entries:
        quote = entry.get("quote", "")
        # Numeric-token match count
        num_hits = sum(1 for n in sentence_nums if n in quote)
        # Character overlap also works for Chinese text.
        char_overlap = len(set(sentence) & set(quote))

        if (num_hits > best_num_hits) or (num_hits == best_num_hits and char_overlap > best_char_overlap):
            best_num_hits = num_hits
            best_char_overlap = char_overlap
            best_entry = entry

    return f"[{best_entry['criterion']}]\n{best_entry['quote']}"


# ============================================================
# URL mode fetches actual pages using the FACT benchmark protocol.
# ============================================================

_url_content_cache: Dict[str, Optional[str]] = {}
_URL_CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "url_cache.json")


def _load_url_cache():
    """Load successfully fetched URL content from disk."""
    global _url_content_cache
    if os.path.exists(_URL_CACHE_FILE):
        try:
            with open(_URL_CACHE_FILE, "r", encoding="utf-8") as f:
                disk_cache = json.load(f)
            # Cache successful content only so failed fetches can be retried.
            _url_content_cache = {k: v for k, v in disk_cache.items() if v is not None}
            print(f"    URL cache loaded: {len(_url_content_cache)} URLs from disk")
        except Exception:
            _url_content_cache = {}


def _save_url_cache():
    """Save successfully fetched URL content to disk."""
    # Save successful fetches only.
    to_save = {k: v for k, v in _url_content_cache.items() if v is not None}
    try:
        with open(_URL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False)
        print(f"    URL cache saved: {len(to_save)} URLs to disk")
    except Exception as e:
        print(f"    WARNING: Failed to save URL cache: {e}")


def fetch_url_content(url):
    """Fetch page content through Jina Reader using the FACT scrape.py protocol.

    Return the joined title, description and content, or None on failure.
    Do not truncate the content, matching the benchmark protocol.
    """
    jina_api_key = os.environ.get("JINA_API_KEY")
    if not jina_api_key:
        print("    WARNING: JINA_API_KEY not set, cannot fetch URL content")
        return None

    jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "Accept": "application/json",
        "Authorization": jina_api_key,
        "X-Timeout": "60000",
        "X-With-Generated-Alt": "true",
    }

    retries = 0
    while retries < 3:
        retries += 1
        try:
            response = requests.get(jina_url, headers=headers, timeout=60)
            if response.status_code != 200:
                time.sleep(1)
                continue
            response_dict = response.json()
            title = response_dict.get("data", {}).get("title", "")
            description = response_dict.get("data", {}).get("description", "")
            content = response_dict.get("data", {}).get("content", "")
            return f"{title}\n\n{description}\n\n{content}"
        except Exception:
            time.sleep(1)
            continue

    return None


def get_url_reference(evidence_table, ref_idx):
    """Fetch the page for ref_idx, falling back to evidence quotes on failure."""
    url_data = evidence_table.get(ref_idx)
    if not url_data:
        return "", False

    url = url_data.get("url", "")
    if not url:
        return get_full_reference(evidence_table, ref_idx), False

    if url not in _url_content_cache:
        _url_content_cache[url] = fetch_url_content(url)

    content = _url_content_cache[url]
    if content:
        return content, True  # (content, is_from_url)
    return get_full_reference(evidence_table, ref_idx), False  # fallback


def prefetch_urls(evidence_table, all_ref_indices, max_workers=10):
    """Fetch required URLs in parallel and populate the memory and disk caches."""
    # Load the existing disk cache first.
    if not _url_content_cache:
        _load_url_cache()

    urls_to_fetch = set()
    for ref_idx in all_ref_indices:
        url_data = evidence_table.get(ref_idx)
        if url_data:
            url = url_data.get("url", "")
            if url and url not in _url_content_cache:
                urls_to_fetch.add(url)

    if not urls_to_fetch:
        print(f"    All {len(all_ref_indices)} URLs already cached, skip fetch")
        return

    print(f"    Pre-fetching {len(urls_to_fetch)} URLs (max_workers={max_workers})...", end=" ", flush=True)
    t0 = time.time()

    def _fetch_one(url):
        return url, fetch_url_content(url)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, url): url for url in urls_to_fetch}
        for future in as_completed(futures):
            url, content = future.result()
            _url_content_cache[url] = content

    ok = sum(1 for u in urls_to_fetch if _url_content_cache.get(u) is not None)
    elapsed = time.time() - t0
    print(f"done ({ok}/{len(urls_to_fetch)} ok, {elapsed:.1f}s)")

    # Persist the cache.
    _save_url_cache()


def get_all_criteria_text(evidence_table, ref_idx):
    """Return all criterion descriptions and complete quotes for a URL."""
    url_data = evidence_table.get(ref_idx)
    if not url_data:
        return ""
    lines = [f"来源 [[{ref_idx}]]: {url_data['url_title']}"]
    for entry in url_data["entries"]:
        lines.append(f"  - [{entry['criterion']}]: {entry['quote']}")
    return "\n".join(lines)


# ============================================================
# Verification prompts
# ============================================================

VERIFY_PROMPT = """你是引用校验员。判断下面这句话的内容是否来自给定的证据原文。

## 被检查的句子
{sentence}

## 证据 [[{ref_idx}]] 原文
{evidence_quote}

## 判断标准
- 句子中的关键数据（数字、百分比、日期、统计量）或关键事实是否出现在证据原文中
- 数字格式可以不同（如 "3.33:1" 和 "3.33比1" 视为相同）
- 约数表述允许合理范围（如证据说 "10.29万亿"，句子说 "超过10万亿" 视为匹配）
- 但句子中不能包含证据中完全没有的数据

## 输出
只输出一个 JSON（不要 markdown 代码块）：
{{"verdict": "YES" 或 "NO", "reason": "一句话解释"}}"""

FIND_CORRECT_PROMPT = """你是引用校验员。下面这句话的引用标错了，请从候选证据中找到正确的来源。

## 被检查的句子
{sentence}

## 候选证据
{candidates}

## 任务
判断哪条证据是这句话的实际来源。如果没有任何证据匹配（数据是虚构的），回答 null。

## 输出
只输出一个 JSON（不要 markdown 代码块）：
{{"correct_ref": 数字或null, "reason": "一句话解释"}}"""


# ============================================================
# Verification logic
# ============================================================


def create_llm(use_writer_model=False):
    """Create the model adapter for the experiment.

    use_writer_model=True tests the writer model to probe the error-rate upper bound.
    use_writer_model=False tests the small model used by the verifier.
    """
    if use_writer_model:
        return SiliconFlowAdapter(
            model="Qwen/Qwen3-Next-80B-A3B-Thinking",
            enable_thinking=True,
            thinking_budget=2048,
        )
    return SiliconFlowAdapter(
        model="Qwen/Qwen3-30B-A3B-Instruct-2507",
        enable_thinking=False,
    )


def verify_single_citation(
    llm,
    sentence: str,
    ref_idx: int,
    evidence_quote: str,
) -> dict:
    """Verify one sentence against one evidence record and return YES or NO."""
    # Strip the [[n]] from sentence for cleaner comparison
    clean_sentence = re.sub(r"\[\[\d+\]\]", "", sentence).strip()

    prompt = VERIFY_PROMPT.format(
        sentence=clean_sentence,
        ref_idx=ref_idx,
        evidence_quote=evidence_quote,
    )
    response = llm.chat([Message(role="user", content=prompt)])
    text = response.content.strip()

    # Parse JSON
    try:
        # Remove markdown code blocks if any
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        result = json.loads(text.strip())
        return result
    except json.JSONDecodeError:
        # Try to extract JSON
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"verdict": "ERROR", "reason": f"Failed to parse: {text[:100]}"}


def find_correct_source(
    llm,
    sentence: str,
    evidence_table: Dict[int, dict],
    exclude_ref: int,
) -> dict:
    """Find the correct source among candidates in the nested evidence format."""
    clean_sentence = re.sub(r"\[\[\d+\]\]", "", sentence).strip()

    candidates_text = ""
    for ref_idx, url_data in sorted(evidence_table.items()):
        if ref_idx == exclude_ref:
            continue
        if "entries" in url_data:
            criteria = "; ".join(e["criterion"] for e in url_data["entries"])
            quote_parts = []
            for e in url_data["entries"]:
                quote_parts.append(f"[{e['criterion']}]: {e['quote']}")
            candidates_text += f"[[{ref_idx}]] ({criteria}):\n" + "\n".join(quote_parts) + "\n\n"
        else:
            candidates_text += f"[[{ref_idx}]] ({url_data['criterion']}): {url_data['quote']}\n\n"

    prompt = FIND_CORRECT_PROMPT.format(
        sentence=clean_sentence,
        candidates=candidates_text,
    )
    response = llm.chat([Message(role="user", content=prompt)])
    text = response.content.strip()

    try:
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        result = json.loads(text.strip())
        return result
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"correct_ref": "ERROR", "reason": f"Failed to parse: {text[:100]}"}


# ============================================================
# Parallel verification tests
# ============================================================


def run_parallel_verification_test(llm, test_cases, evidence_table):
    """Run Step 1 checks in parallel, then apply Step 2 to NO results."""
    print("\n" + "=" * 70)
    print("  STEP 1: 原子核验（并行）")
    print("=" * 70)

    # Step 1: verify individual citations in parallel.
    step1_results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for i, tc in enumerate(test_cases):
            ref_idx = tc["cited_ref"]
            evidence_quote = get_best_matching_entry(evidence_table, ref_idx, tc["sentence"])
            future = executor.submit(
                verify_single_citation,
                llm,
                tc["sentence"],
                ref_idx,
                evidence_quote,
            )
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()
            step1_results[idx] = result

    # Display Step 1 results.
    no_cases = []
    correct_count = 0
    total = len(test_cases)

    for i in range(total):
        tc = test_cases[i]
        result = step1_results[i]
        verdict = result.get("verdict", "ERROR")
        expected = tc["expected_verdict"]
        match = "PASS" if verdict == expected else "FAIL"
        if match == "PASS":
            correct_count += 1

        icon = "v" if match == "PASS" else "X"
        print(f"\n  [{icon}] Case {i + 1}: {tc['name']}")
        print(f"      句子: {tc['sentence'][:60]}...")
        print(f"      引用: [[{tc['cited_ref']}]] | 模型判定: {verdict} | 期望: {expected} | {match}")
        print(f"      理由: {result.get('reason', 'N/A')}")

        if verdict == "NO":
            no_cases.append(i)

    print(f"\n  Step 1 准确率: {correct_count}/{total}")

    # Step 2: find the correct source for NO results.
    if no_cases:
        print("\n" + "=" * 70)
        print("  STEP 2: 查找正确来源（对 NO 的引用）")
        print("=" * 70)

        step2_results = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {}
            for idx in no_cases:
                tc = test_cases[idx]
                future = executor.submit(
                    find_correct_source,
                    llm,
                    tc["sentence"],
                    evidence_table,
                    tc["cited_ref"],
                )
                futures[future] = idx

            for future in as_completed(futures):
                idx = futures[future]
                result = future.result()
                step2_results[idx] = result

        correct_fix = 0
        for idx in no_cases:
            tc = test_cases[idx]
            result = step2_results[idx]
            found_ref = result.get("correct_ref")
            expected_ref = tc["expected_correct_ref"]

            # Handle None/null comparison
            if found_ref is None and expected_ref is None:
                match = "PASS"
                correct_fix += 1
            elif found_ref == expected_ref:
                match = "PASS"
                correct_fix += 1
            else:
                match = "FAIL"

            icon = "v" if match == "PASS" else "X"
            action = f"→ [[{found_ref}]]" if found_ref else "→ 幻觉（无匹配）"
            expected_action = f"[[{expected_ref}]]" if expected_ref else "幻觉"

            print(f"\n  [{icon}] Case {idx + 1}: {tc['name']}")
            print(f"      句子: {tc['sentence'][:60]}...")
            print(f"      纠正: {action} | 期望: {expected_action} | {match}")
            print(f"      理由: {result.get('reason', 'N/A')}")
            if tc.get("note"):
                print(f"      备注: {tc['note']}")

        print(f"\n  Step 2 准确率: {correct_fix}/{len(no_cases)}")

    # Summary
    print("\n" + "=" * 70)
    print("  总结")
    print("=" * 70)
    print(f"  Step 1 (YES/NO 判定): {correct_count}/{total} 正确")
    if no_cases:
        print(f"  Step 2 (纠正/幻觉检测): {correct_fix}/{len(no_cases)} 正确")
    print()


# ============================================================
# Latency tests
# ============================================================


def run_latency_test(llm, n_parallel=10):
    """Measure latency for parallel model calls."""
    import time

    print("\n" + "=" * 70)
    print(f"  延迟测试: {n_parallel} 个并行原子核验")
    print("=" * 70)

    # Prepare n_parallel verification tasks.
    tasks = []
    for i in range(min(n_parallel, len(TEST_CASES))):
        tc = TEST_CASES[i]
        quote = get_best_matching_entry(EVIDENCE_TABLE, tc["cited_ref"], tc["sentence"])
        tasks.append((tc["sentence"], tc["cited_ref"], quote))
    # Repeat the available cases if more tasks are needed.
    while len(tasks) < n_parallel:
        tc = TEST_CASES[len(tasks) % len(TEST_CASES)]
        quote = get_best_matching_entry(EVIDENCE_TABLE, tc["cited_ref"], tc["sentence"])
        tasks.append((tc["sentence"], tc["cited_ref"], quote))

    start = time.time()
    with ThreadPoolExecutor(max_workers=n_parallel) as executor:
        futures = []
        for sentence, ref_idx, quote in tasks:
            future = executor.submit(verify_single_citation, llm, sentence, ref_idx, quote)
            futures.append(future)
        for f in futures:
            f.result()
    elapsed = time.time() - start

    print(f"  {n_parallel} 个并行调用总耗时: {elapsed:.2f}s")
    print(f"  平均每调用: {elapsed / n_parallel:.2f}s (但因并行实际不叠加)")
    print(f"  等效开销: {elapsed:.1f}s（即一个 section 校验的实际延迟）")
    print()


# ============================================================
# E2E: generate a section, verify with the small model, then compare.
# ============================================================

# SectionPlanner insight-to-evidence bindings simulate imperfect real bindings.
# Evidence IDs 1-12 correspond to the 12 URL sources in EVIDENCE_TABLE.
E2E_SECTION_PLAN = {
    "section_id": "3",
    "title": "中产阶层的经济特征：收入结构、消费分层与财富分化",
    "description": "分析中国中产阶层的收入来源构成、消费行为分层现象、以及财产性收入差距对阶层固化的影响。",
    "answers_aspect": "中产阶层内部的经济异质性与结构性分化特征",
    "insights": [
        {
            "insight": "中产阶层的收入增长呈现明显的城乡二元分化，农村居民虽增速较快但绝对差距仍在扩大",
            "evidence_ids": [1, 4, 2],
            "presentation_hint": "comparison",
        },
        {
            "insight": "财产性收入差距是阻碍阶层流动的核心结构性障碍，远超工资性收入差距的影响",
            "evidence_ids": [1, 2, 7],
            "presentation_hint": "narrative",
        },
        {
            "insight": "消费行为出现极端分层：底层节衣缩食增加储蓄，顶层奢侈品消费逆势增长",
            "evidence_ids": [7, 12],
            "presentation_hint": "comparison",
        },
        {
            "insight": "住房支出对中产消费形成挤出效应，是制约消费升级的最大单项因素",
            "evidence_ids": [7, 9, 5],
            "presentation_hint": "narrative",
        },
        {
            "insight": "中国经济总量的高速增长并未等比例转化为中产阶层的获得感，人均指标与总量指标之间存在显著落差",
            "evidence_ids": [6, 1, 12],
            "presentation_hint": "table",
        },
    ],
}

# All evidence visible to this section after binding and deduplication.
E2E_BOUND_EVIDENCE_IDS = set()
for ins in E2E_SECTION_PLAN["insights"]:
    E2E_BOUND_EVIDENCE_IDS.update(ins["evidence_ids"])


def build_e2e_evidence_table(evidence_table, bound_ids):
    """Build the nested evidence table in the production SectionWriter format."""
    lines = []
    for ref_idx in sorted(bound_ids):
        url_data = evidence_table.get(ref_idx)
        if not url_data:
            continue
        lines.append(f"**来源 [[{ref_idx}]]**")
        lines.append("| Criterion | Evidence Quote | Finding |")
        lines.append("|-----------|----------------|---------|")
        for entry in url_data["entries"]:
            quote = entry["quote"].replace("|", "\\|").replace("\n", " ")
            criterion = entry["criterion"].replace("|", "\\|")
            finding = f"关于{criterion}的核心数据和趋势"
            lines.append(f"| {criterion} | {quote[:500]} | {finding} |")
        lines.append("")
    return "\n".join(lines)


def build_e2e_insights_text(plan):
    """Build insight bindings in the production SectionWriter format."""
    lines = []
    for i, ins in enumerate(plan["insights"], 1):
        refs_str = ", ".join(f"[[{eid}]]" for eid in ins["evidence_ids"])
        hint_map = {
            "table": "建议使用表格对比",
            "comparison": "建议进行对比分析",
            "narrative": "建议叙事论证",
        }
        hint = hint_map.get(ins.get("presentation_hint", ""), "")
        lines.append(f"{i}. **Insight**: {ins['insight']}")
        lines.append(f"   **Supporting evidence**: {refs_str}")
        if hint:
            lines.append(f"   **Presentation**: {hint}")
        lines.append("")
    return "\n".join(lines)


def build_e2e_prompt(plan, evidence_table, use_full_evidence=False, preceding_context=None):
    """Build the full Chinese SectionWriter prompt from its production template.

    use_full_evidence=True passes all evidence to simulate fallback or imprecise bindings.
    use_full_evidence=False passes only bound evidence to simulate precise bindings.
    preceding_context supplies completed sections without citation markers to extend the context.
    """
    if use_full_evidence:
        bound_ids = set(evidence_table.keys())  # All evidence
    else:
        bound_ids = E2E_BOUND_EVIDENCE_IDS  # Bound evidence only
    evidence_text = build_e2e_evidence_table(evidence_table, bound_ids)
    insights_text = build_e2e_insights_text(plan)

    outline_text = """
1. 中国中产阶层的界定标准与规模测算
2. 中产阶层的地理分布与城市群集聚效应
3. **中产阶层的经济特征：收入结构、消费分层与财富分化** ← 当前章节
4. 教育、住房与社会保障：中产阶层的三座大山
5. 中产阶层的社会心理与未来展望
""".strip()

    answers_aspect_text = f"\n**本节回答的问题维度**: {plan['answers_aspect']}"

    # Build the preceding-sections block.
    preceding_block = ""
    if preceding_context:
        preceding_block = f"""
## 已完成的前置章节（供参考上下文，请保持风格一致）

{preceding_context}

---

"""

    prompt = f"""你是一位资深研究分析师和报告撰稿人。请为报告撰写一个**高质量的分析性章节**。

## 1. 原始问题（报告必须正面回答这个问题）
中国中产阶层的现状、结构特征与面临的核心挑战是什么？

## 2. 完整报告大纲
{outline_text}
{preceding_block}
## 3. 当前要写的章节
**第 {plan["section_id"]} 节：{plan["title"]}**
{plan["description"]}{answers_aspect_text}

## 4. 写作蓝图（观点→证据绑定）
{insights_text}
使用方法：每个 Insight 是你的分析性判断（写时不加引用），Supporting evidence 的 [[n]] 用于写事实性声明（每个事实一个 [[n]]）。

## 5. 证据表（原文引述——事实性声明必须基于这些内容）
{evidence_text}

## 写作要求
### 分析深度（最重要）
- **不要简单罗列事实**。对每个观点，先陈述判断，再用证据支撑，最后分析其含义或影响
- 揭示数据背后的趋势、规律、因果关系（如"这表明..."、"其背后原因在于..."）
- 进行跨来源比较：不同机构/年份/标准的数据如果存在差异，要指出并分析原因
- 使用分析性语言：尽管…但是、值得注意的是、相比之下、这一现象反映了…

### 数据呈现
- 当有 3 个以上可比数据点（如不同年份、不同机构、不同类别的数值）时，**必须使用 Markdown 表格**
- 表格需有清晰的列标题，表格下方用斜体注明数据来源

### 结构与衔接
- 使用 ### 子标题将章节内不同论述主题分开
- 观点之间要有逻辑过渡，构建递进或对比的论证结构

### 关于缺失信息
- 如果证据来源明确记录了某事物的缺失或不存在，且你能用 [[n]] 引用支撑 → 可以写
- 如果只是搜索未覆盖到、没有证据支撑 → **直接跳过，不要提及**

## 引用格式（必须遵守）
- 分析性判断不加引用："市场正在整合。"
- 事实性声明加一个 [[n]]："收入达到34亿美元[[2]]"
- **硬性规则：任何包含具体数字、百分比、金额、日期或统计量的句子，必须有 [[n]] 引用，无例外。** 如果找不到匹配的证据来源，就不要写这个数字。
- 禁止一个事实多个引用：❌ "收入达34亿[[2]][[5]]"
- 不同事实可引不同源：✅ "成立于2015年[[2]]，估值800亿[[5]]"
- 只用 `[[n]]` 格式，禁止 `[n]`、`[[n,m]]`、`[[n],[m]]`
- 事实必须与证据原文数据一致，不可篡改数字或结论

## 输出规则
- 只写第 {plan["section_id"]} 节，使用 ## 标题、### 子标题
- 专业分析性语气，不提及"观点"、"证据表"、"蓝图"等元信息
- 本节内容必须服务于回答用户的原始问题

请开始撰写第 {plan["section_id"]} 节："""

    return prompt


def run_e2e_test(run_idx=None, verbose=True):
    """Have the writer generate a section and the small model verify every citation.

    Return a metrics dictionary for aggregation across runs.
    """
    prefix = f"  [Run {run_idx}] " if run_idx is not None else "  "

    if verbose:
        print("\n" + "=" * 70)
        title = f"  端到端测试 Run {run_idx}" if run_idx else "  端到端测试"
        print(f"{title}：写作模型写 Section → 小模型校验")
        print("=" * 70)

    use_full = "--full-evidence" in sys.argv
    n_evidence = len(EVIDENCE_TABLE)
    if verbose:
        if use_full:
            print(f"{prefix}模式: 全量证据池 ({n_evidence} 条)")
        else:
            print(f"{prefix}模式: 精确绑定证据 ({len(E2E_BOUND_EVIDENCE_IDS)} 条)")

    # 1. Generate the section with the writer model.
    if verbose:
        print(f"\n{prefix}[1] 调用写作模型生成 section...")
    writer_llm = create_llm(use_writer_model=True)
    prompt = build_e2e_prompt(E2E_SECTION_PLAN, EVIDENCE_TABLE, use_full_evidence=use_full)

    response = writer_llm.chat([Message(role="user", content=prompt)])
    section_text = response.content.strip()

    if verbose:
        print(f"{prefix}生成完毕，共 {len(section_text)} 字符")
        if run_idx is None:  # Print the full section for a single run only.
            print("\n" + "-" * 40 + " 生成文本 " + "-" * 40)
            print(section_text)
            print("-" * 91)

    # 2. Extract every [[n]] citation.
    citations = []
    sentences = re.split(r"(?<=[。！？\n])", section_text)
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        refs = re.findall(r"\[\[(\d+)\]\]", sent)
        for ref_str in refs:
            ref_idx = int(ref_str)
            citations.append(
                {
                    "sentence": sent,
                    "cited_ref": ref_idx,
                }
            )

    if verbose:
        print(f"{prefix}共提取到 {len(citations)} 个引用")

    if not citations:
        if verbose:
            print(f"{prefix}没有引用可校验")
        return None

    # 3. Verify citations in parallel with the small model.
    if verbose:
        print(f"{prefix}[2] 小模型原子校验...")
    verifier_llm = create_llm(use_writer_model=False)

    step1_results = {}
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {}
        for i, cit in enumerate(citations):
            ref_idx = cit["cited_ref"]
            if ref_idx not in EVIDENCE_TABLE:
                step1_results[i] = {"verdict": "NO", "reason": f"[[{ref_idx}]] 不在证据表中（幻觉编号）"}
                continue
            evidence_quote = get_best_matching_entry(EVIDENCE_TABLE, ref_idx, cit["sentence"])
            future = executor.submit(
                verify_single_citation,
                verifier_llm,
                cit["sentence"],
                ref_idx,
                evidence_quote,
            )
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            step1_results[idx] = future.result()

    # 4. Aggregate Step 1 results.
    yes_count = 0
    no_indices = []
    for i in range(len(citations)):
        verdict = step1_results[i].get("verdict", "ERROR")
        if verdict == "YES":
            yes_count += 1
        else:
            no_indices.append(i)

    total = len(citations)
    if verbose:
        print(f"{prefix}Step 1: {yes_count}/{total} ({yes_count / total * 100:.1f}%)，{len(no_indices)} 个可疑")

    # 5. Apply Step 2 correction to NO results.
    step2_results = {}
    if no_indices:
        if verbose:
            print(f"{prefix}[3] 对 {len(no_indices)} 个可疑引用查找正确来源...")
        if use_full:
            bound_evidence = EVIDENCE_TABLE
        else:
            bound_evidence = {k: v for k, v in EVIDENCE_TABLE.items() if k in E2E_BOUND_EVIDENCE_IDS}

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {}
            for idx in no_indices:
                cit = citations[idx]
                future = executor.submit(
                    find_correct_source,
                    verifier_llm,
                    cit["sentence"],
                    bound_evidence,
                    cit["cited_ref"],
                )
                futures[future] = idx

            for future in as_completed(futures):
                idx = futures[future]
                step2_results[idx] = future.result()

    # 6. Aggregate correction results.
    corrected = 0
    hallucinated = 0
    for i in range(total):
        verdict = step1_results[i].get("verdict", "ERROR")
        if verdict != "YES":
            if i in step2_results:
                found_ref = step2_results[i].get("correct_ref")
                if found_ref is not None:
                    corrected += 1
                else:
                    hallucinated += 1
            else:
                hallucinated += 1

    if verbose:
        print(f"{prefix}小模型统计: 原始正确={yes_count}, 纠正={corrected}, 幻觉={hallucinated}")

    # 7. Build the corrected citation mapping.
    corrected_citations = []
    for i in range(total):
        cit = citations[i].copy()
        verdict = step1_results[i].get("verdict", "ERROR")
        if verdict == "YES":
            cit["final_ref"] = cit["cited_ref"]
        else:
            if i in step2_results:
                found_ref = step2_results[i].get("correct_ref")
                cit["final_ref"] = found_ref if found_ref is not None else cit["cited_ref"]
            else:
                cit["final_ref"] = cit["cited_ref"]
        corrected_citations.append(cit)

    # 8. Evaluate independently with Gemini Flash.
    gemini_before = None
    gemini_after = None
    if "--gemini-eval" in sys.argv:
        gemini_before, gemini_after = run_gemini_eval(citations, corrected_citations, EVIDENCE_TABLE, verbose=verbose)

    return {
        "total_citations": total,
        "step1_correct": yes_count,
        "step1_acc": yes_count / total,
        "corrected": corrected,
        "hallucinated": hallucinated,
        "after_correction_acc": (yes_count + corrected) / total,
        "gemini_before": gemini_before,
        "gemini_after": gemini_after,
    }


def run_e2e_multi(n_runs):
    """Repeat the E2E experiment and aggregate its statistics."""
    print("\n" + "=" * 70)
    print(f"  E2E 多次运行测试 ({n_runs} 次)")
    print("=" * 70)

    all_results = []
    for i in range(1, n_runs + 1):
        result = run_e2e_test(run_idx=i, verbose=True)
        if result:
            all_results.append(result)
        print()

    if not all_results:
        print("  所有运行都失败了")
        return

    # Aggregate statistics
    print("\n" + "=" * 70)
    print(f"  汇总统计 ({len(all_results)} 次有效运行)")
    print("=" * 70)

    # Table header
    has_gemini = all_results[0].get("gemini_before") is not None
    if has_gemini:
        print(f"  {'Run':<5} {'引用数':>6} {'Step1':>8} {'纠正后':>8} {'Gemini前':>9} {'Gemini后':>9} {'提升':>7}")
        print(f"  {'-' * 5} {'-' * 6} {'-' * 8} {'-' * 8} {'-' * 9} {'-' * 9} {'-' * 7}")
    else:
        print(f"  {'Run':<5} {'引用数':>6} {'Step1':>8} {'纠正后':>8}")
        print(f"  {'-' * 5} {'-' * 6} {'-' * 8} {'-' * 8}")

    sum_step1 = 0
    sum_after = 0
    sum_gemini_before = 0
    sum_gemini_after = 0

    for i, r in enumerate(all_results, 1):
        s1 = r["step1_acc"] * 100
        af = r["after_correction_acc"] * 100
        sum_step1 += r["step1_acc"]
        sum_after += r["after_correction_acc"]

        if has_gemini and r.get("gemini_before") is not None:
            gb = r["gemini_before"]
            ga = r["gemini_after"]
            sum_gemini_before += gb
            sum_gemini_after += ga
            delta = ga - gb
            print(
                f"  {i:<5} {r['total_citations']:>6} {s1:>7.1f}% {af:>7.1f}% {gb * 100:>8.1f}% {ga * 100:>8.1f}% {delta * 100:>+6.1f}%"
            )
        else:
            print(f"  {i:<5} {r['total_citations']:>6} {s1:>7.1f}% {af:>7.1f}%")

    n = len(all_results)
    print(f"  {'-' * 5} {'-' * 6} {'-' * 8} {'-' * 8}" + (f" {'-' * 9} {'-' * 9} {'-' * 7}" if has_gemini else ""))

    avg_s1 = sum_step1 / n * 100
    avg_af = sum_after / n * 100
    if has_gemini:
        avg_gb = sum_gemini_before / n * 100
        avg_ga = sum_gemini_after / n * 100
        avg_delta = avg_ga - avg_gb
        print(
            f"  {'AVG':<5} {'':>6} {avg_s1:>7.1f}% {avg_af:>7.1f}% {avg_gb:>8.1f}% {avg_ga:>8.1f}% {avg_delta:>+6.1f}%"
        )
    else:
        print(f"  {'AVG':<5} {'':>6} {avg_s1:>7.1f}% {avg_af:>7.1f}%")

    print()


# ============================================================
# Independent Gemini Flash evaluation using the FACT protocol
# ============================================================

GEMINI_VALIDATE_PROMPT = """你会看到一个参考资料和一些statement，请你判断对于参考资料来说statement是supported、unsupported、或者unknown，注意：
首先判断参考资料是否存在有效内容，如果参考资料中没有任何有效信息，如"page not found"页面，则认为所有statement的状态都是unknown。
除此之外，参考资料有效的情况下，对于一个statement来说，如果它包含的事实或数据在参考资料中可以全部或部分找到，就认为它是supported的（数据接受四舍五入）；如果statement中所有的事实和数据在参考资料中都找不到，认为它是unsupported的。

你应该返回json列表格式，列表中的每一项包含statement的序号和判断结果，例如：
[
    {{
        "idx": 1,
        "result": "supported"
    }},
    {{
        "idx": 2,
        "result": "unsupported"
    }}
]

下面是参考资料和statements：
<reference>
{reference}
</reference>

<statements>
{statements}
</statements>

下面开始判断，直接输出json列表，不要输出任何闲聊或解释。"""


def gemini_validate_batch(ref_idx, evidence_quote, statements, thinking_budget=8000):
    """Validate statements from one source as a batch with Gemini Flash.

    Return a list of {"idx": int, "result": "supported"|"unsupported"|"unknown"}.
    """
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key, http_options={"timeout": 600000})

    statements_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(statements))
    prompt = GEMINI_VALIDATE_PROMPT.format(reference=evidence_quote, statements=statements_str)

    retries = 0
    while retries < 3:
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget)
                ),
            )
            text = response.text.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            results = json.loads(text)
            # 0-index
            for r in results:
                r["idx"] -= 1
            return results
        except Exception as e:
            retries += 1
            logging.warning(f"Gemini validate retry {retries}: {e}")
            time.sleep(3)

    return [{"idx": i, "result": "unknown"} for i in range(len(statements))]


def run_gemini_eval(original_citations, corrected_citations, evidence_table, verbose=True):
    """Use Gemini Flash to independently compare original and corrected citation accuracy.

    Follow FACT: group by source, validate batches and calculate C.Acc.
    Return (c_acc_before, c_acc_after).
    """
    if verbose:
        print("\n" + "=" * 70)
        print("  Gemini Flash 独立评判 (FACT 方法论)")
        print("=" * 70)

    def evaluate_scenario(scenario_name, cits, ref_key):
        """Evaluate one scenario and return (supported, unsupported, unknown, details)."""
        if verbose:
            print(f"\n  [{scenario_name}] 按来源分组...")

        # Group citations by ref_idx.
        groups = {}  # ref_idx -> [(citation_idx, sentence)]
        invalid_ref_count = 0
        for i, cit in enumerate(cits):
            rid = cit[ref_key]
            if rid is None or rid not in evidence_table:
                invalid_ref_count += 1
                continue
            if rid not in groups:
                groups[rid] = []
            groups[rid].append((i, cit["sentence"]))

        if verbose and invalid_ref_count:
            print(f"    跳过 {invalid_ref_count} 个无效引用")

        if verbose:
            print(f"    共 {len(groups)} 个来源组，{sum(len(v) for v in groups.values())} 个 statement")

        # Call Gemini Flash for each group.
        all_results = {}  # citation_idx -> result
        for ref_idx, items in groups.items():
            evidence_quote = get_full_reference(evidence_table, ref_idx)
            statements = [s for _, s in items]
            indices = [idx for idx, _ in items]

            if verbose:
                print(f"    验证 [[{ref_idx}]] ({len(statements)} 条)...", end=" ", flush=True)
            results = gemini_validate_batch(ref_idx, evidence_quote, statements)
            if verbose:
                print("done")

            for r in results:
                local_idx = r["idx"]
                if 0 <= local_idx < len(indices):
                    all_results[indices[local_idx]] = r["result"]

        # Aggregate results
        supported = sum(1 for v in all_results.values() if v == "supported")
        unsupported = sum(1 for v in all_results.values() if v == "unsupported")
        unknown = sum(1 for v in all_results.values() if v == "unknown")
        total_eval = supported + unsupported

        c_acc = supported / total_eval if total_eval > 0 else 0.0

        if verbose:
            print(f"    结果: supported={supported}, unsupported={unsupported}, unknown={unknown}")
            print(f"    C.Acc = {supported}/{total_eval} = {c_acc:.4f} ({c_acc * 100:.1f}%)")

        return supported, unsupported, unknown, all_results

    # Scenario A: original citations
    s_a, u_a, k_a, details_a = evaluate_scenario("A:无纠正", original_citations, "cited_ref")

    # Scenario B: corrected citations
    s_b, u_b, k_b, details_b = evaluate_scenario("B:纠正后", corrected_citations, "final_ref")

    # Calculate C.Acc.
    total_a = s_a + u_a
    total_b = s_b + u_b
    acc_a = s_a / total_a if total_a > 0 else 0.0
    acc_b = s_b / total_b if total_b > 0 else 0.0

    if verbose:
        print(f"\n  Gemini 对比: C.Acc {acc_a * 100:.1f}% → {acc_b * 100:.1f}% ({(acc_b - acc_a) * 100:>+.1f}%)")

    return acc_a, acc_b


# ============================================================
# FACT citation extraction uses model-based splitting instead of regex splitting.
# ============================================================

FACT_EXTRACT_PROMPT = """你会看到一篇研究报告片段，正文中会有一些对参考文献的引用。
正文中的引用以 [[数字]] 形式出现，例如："城乡收入比为2.45:1[[3]]"

请从正文中找出**所有**引用了参考文献的地方，提取出(fact, ref_idx)二元组，提取的时候注意：
1. 由于后续需要检验这些facts是否正确，你可能需要在引用的前后寻找一些上下文，以确保fact是完整可理解的，而不是简单的词组或短语
2. 如果一个fact引用了多个文献，那么它应该对应多个二元组：(fact, ref_idx_1)和(fact, ref_idx_2)
3. ref_idx 就是 [[]] 中的数字

你应该返回json列表格式，例如：
[
    {{
        "fact": "城乡居民收入比从2009年的3.33:1持续下降至2023年的2.39:1，农村居民人均可支配收入年均增速达到10.6%",
        "ref_idx": 1
    }},
    {{
        "fact": "中国GDP以购买力平价计算约为33.01万亿美元，超过美国的27.36万亿美元",
        "ref_idx": 8
    }}
]

下面是研究报告片段：
{report_text}

下面开始提取，直接输出json列表，不要输出任何闲聊或解释。"""


def fact_extract_citations(section_text, verbose=True):
    """Extract (fact, ref_idx) pairs with Gemini Flash using the FACT protocol."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key, http_options={"timeout": 600000})
    prompt = FACT_EXTRACT_PROMPT.format(report_text=section_text)

    retries = 0
    while retries < 3:
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_budget=8000)),
            )
            text = response.text.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            citations = json.loads(text)
            if verbose:
                print(f"    FACT 提取: {len(citations)} 个引用")
            return citations
        except Exception as e:
            retries += 1
            logging.warning(f"FACT extract retry {retries}: {e}")
            time.sleep(3)

    logging.error("FACT extraction failed after 3 retries")
    return []


# ============================================================
# Embedding (Qwen3-Embedding-8B via SiliconFlow)
# ============================================================

# ============================================================
# 80B double-verification method
# ============================================================


def run_80b_correction(fact_citations, evidence_table, verbose=True):
    """Verify and correct citations with the 80B writer using the same two steps as 30B.

    Step 1: independently verify each citation as YES/NO with 5 parallel workers.
    Step 2: find a correct candidate source for Step 1 NO results with 5 parallel workers.

    Args:
        fact_citations: FACT-extracted [{fact, ref_idx}] records.
        evidence_table: {ref_idx: {url_title, entries: [{criterion, quote}]}}.

    Returns:
        A list of {fact, cited_ref, final_ref} records.
    """
    if not fact_citations:
        return []

    llm_80b = create_llm(use_writer_model=True)

    if verbose:
        print(f"    80B 验证: {len(fact_citations)} 条引用...")

    # Step 1: reuse verify_single_citation, matching the 30B Step 1.
    step1_results = {}
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {}
        for i, cit in enumerate(fact_citations):
            ref_idx = cit["ref_idx"]
            if ref_idx not in evidence_table:
                step1_results[i] = {"verdict": "NO", "reason": f"[[{ref_idx}]] not in evidence"}
                continue
            evidence_quote = get_best_matching_entry(evidence_table, ref_idx, cit["fact"])
            future = executor.submit(
                verify_single_citation,
                llm_80b,
                cit["fact"],
                ref_idx,
                evidence_quote,
            )
            futures[future] = i
        for future in as_completed(futures):
            idx = futures[future]
            step1_results[idx] = future.result()

    # Step 2: reuse find_correct_source for NO results, matching the 30B Step 2.
    no_indices = [i for i in range(len(fact_citations)) if step1_results.get(i, {}).get("verdict", "ERROR") != "YES"]
    step2_results = {}
    if no_indices:
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {}
            for idx in no_indices:
                cit = fact_citations[idx]
                future = executor.submit(
                    find_correct_source,
                    llm_80b,
                    cit["fact"],
                    evidence_table,
                    cit["ref_idx"],
                )
                futures[future] = idx
            for future in as_completed(futures):
                idx = futures[future]
                step2_results[idx] = future.result()

    # Build results
    results = []
    for i, cit in enumerate(fact_citations):
        ref_idx = cit["ref_idx"]
        verdict = step1_results.get(i, {}).get("verdict", "ERROR")
        if verdict == "YES":
            final_ref = ref_idx
        elif i in step2_results:
            found_ref = step2_results[i].get("correct_ref")
            final_ref = found_ref if found_ref is not None else ref_idx
        else:
            final_ref = ref_idx
        results.append(
            {
                "fact": cit["fact"],
                "cited_ref": ref_idx,
                "final_ref": final_ref,
            }
        )

    if verbose:
        yes_count = sum(1 for i in range(len(fact_citations)) if step1_results.get(i, {}).get("verdict") == "YES")
        print(f"    80B: Step1 {yes_count}/{len(fact_citations)}, 纠正 {len(step2_results)} 个")

    return results


# ============================================================
# Unified E2E comparison of 3 methods
# ============================================================


def run_e2e_compare(run_idx=None, verbose=True):
    """Generate a section, compare 3 correction methods and evaluate with Gemini.

    Methods:
        A. Baseline without correction.
        B. Small 30B model, Step 1 + Step 2.
        C. 80B double verification.

    Return a metrics dictionary.
    """
    prefix = f"  [Run {run_idx}] " if run_idx is not None else "  "

    if verbose:
        print("\n" + "=" * 70)
        title = f"  3-Method Compare Run {run_idx}" if run_idx else "  3-Method Compare"
        print(title)
        print("=" * 70)

    use_full = "--full-evidence" in sys.argv
    use_long_ctx = "--long-context" in sys.argv
    n_evidence = len(EVIDENCE_TABLE)
    if verbose:
        mode = f"全量证据池 ({n_evidence} 条)" if use_full else f"绑定证据 ({len(E2E_BOUND_EVIDENCE_IDS)} 条)"
        if use_long_ctx:
            mode += f" + 长上下文 ({len(PRECEDING_SECTIONS)} 字符)"
        print(f"{prefix}模式: {mode}")

    # 1. Generate the section with the writer.
    if verbose:
        print(f"\n{prefix}[1/4] Writer 生成 section...")
    writer_llm = create_llm(use_writer_model=True)
    preceding = PRECEDING_SECTIONS if use_long_ctx else None
    prompt = build_e2e_prompt(E2E_SECTION_PLAN, EVIDENCE_TABLE, use_full_evidence=use_full, preceding_context=preceding)
    response = writer_llm.chat([Message(role="user", content=prompt)])
    section_text = response.content.strip()
    if verbose:
        print(f"{prefix}生成完毕，{len(section_text)} 字符")

    # 2. Extract citations with FACT.
    if verbose:
        print(f"\n{prefix}[2/4] FACT 提取引用...")
    fact_citations = fact_extract_citations(section_text, verbose=verbose)
    if not fact_citations:
        if verbose:
            print(f"{prefix}FACT 提取失败，跳过")
        return None

    if verbose:
        print(f"{prefix}FACT: {len(fact_citations)} 引用")

    # ---- 3. Methods B + C in parallel ----
    if verbose:
        print(f"\n{prefix}[3/4] Methods B(30B) + C(80B) in parallel...")

    def _run_method_b():
        """30B verifier: Step1 verify + Step2 find correct source"""
        verifier_llm = create_llm(use_writer_model=False)
        # Step 1: verify individual citations in parallel.
        step1_results = {}
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {}
            for i, cit in enumerate(fact_citations):
                ref_idx = cit["ref_idx"]
                if ref_idx not in EVIDENCE_TABLE:
                    step1_results[i] = {"verdict": "NO", "reason": f"[[{ref_idx}]] not in evidence"}
                    continue
                evidence_quote = get_best_matching_entry(EVIDENCE_TABLE, ref_idx, cit["fact"])
                future = executor.submit(
                    verify_single_citation,
                    verifier_llm,
                    cit["fact"],
                    ref_idx,
                    evidence_quote,
                )
                futures[future] = i
            for future in as_completed(futures):
                idx = futures[future]
                step1_results[idx] = future.result()

        # Step 2: find the correct source for NO results.
        no_indices = [
            i for i in range(len(fact_citations)) if step1_results.get(i, {}).get("verdict", "ERROR") != "YES"
        ]
        step2_results = {}
        if no_indices:
            search_evidence = (
                EVIDENCE_TABLE if use_full else {k: v for k, v in EVIDENCE_TABLE.items() if k in E2E_BOUND_EVIDENCE_IDS}
            )
            with ThreadPoolExecutor(max_workers=15) as executor:
                futures = {}
                for idx in no_indices:
                    cit = fact_citations[idx]
                    future = executor.submit(
                        find_correct_source,
                        verifier_llm,
                        cit["fact"],
                        search_evidence,
                        cit["ref_idx"],
                    )
                    futures[future] = idx
                for future in as_completed(futures):
                    idx = futures[future]
                    step2_results[idx] = future.result()

        # Build 30B corrected citations
        corrected = []
        for i, cit in enumerate(fact_citations):
            c = {"sentence": cit["fact"], "cited_ref": cit["ref_idx"]}
            verdict = step1_results.get(i, {}).get("verdict", "ERROR")
            if verdict == "YES":
                c["final_ref"] = c["cited_ref"]
            elif i in step2_results:
                found_ref = step2_results[i].get("correct_ref")
                c["final_ref"] = found_ref if found_ref is not None else c["cited_ref"]
            else:
                c["final_ref"] = c["cited_ref"]
            corrected.append(c)

        yes_count = sum(1 for i in range(len(fact_citations)) if step1_results.get(i, {}).get("verdict") == "YES")
        if verbose:
            print(f"{prefix}30B: Step1 {yes_count}/{len(fact_citations)}, 纠正 {len(step2_results)} 个")
        return corrected

    def _run_method_c():
        """80B verifier"""
        results_80b = run_80b_correction(fact_citations, EVIDENCE_TABLE, verbose=verbose)
        return [{"sentence": r["fact"], "cited_ref": r["cited_ref"], "final_ref": r["final_ref"]} for r in results_80b]

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_b = executor.submit(_run_method_b)
        future_c = executor.submit(_run_method_c)
        corrected_30b = future_b.result()
        d80b_cits = future_c.result()

    # 4. Evaluate the 3 scenarios independently with Gemini in parallel.
    if verbose:
        print(f"\n{prefix}[4/4] Gemini 评判 3 个场景...")

    baseline_cits = [{"sentence": c["fact"], "cited_ref": c["ref_idx"]} for c in fact_citations]

    with ThreadPoolExecutor(max_workers=3) as executor:
        fa = executor.submit(_gemini_eval_single, "A:Baseline", baseline_cits, "cited_ref", EVIDENCE_TABLE, verbose)
        fb = executor.submit(_gemini_eval_single, "B:30B", corrected_30b, "final_ref", EVIDENCE_TABLE, verbose)
        fc = executor.submit(_gemini_eval_single, "C:80B", d80b_cits, "final_ref", EVIDENCE_TABLE, verbose)
        gemini_a, eff_a = fa.result()
        gemini_b, eff_b = fb.result()
        gemini_c, eff_c = fc.result()

    return {
        "total_citations": len(fact_citations),
        "gemini_baseline": gemini_a,
        "gemini_30b": gemini_b,
        "gemini_80b": gemini_c,
        "eff_baseline": eff_a,
        "eff_30b": eff_b,
        "eff_80b": eff_c,
    }


def _gemini_eval_single(
    scenario_name, citations, ref_key, evidence_table, verbose=True, use_url=False, thinking_budget=8000
):
    """Evaluate one scenario with Gemini and return (C.Acc, supported_count).

    use_url=False uses Reader quotes from EvidenceStore as references.
    use_url=True uses actual Jina-fetched page content, matching the FACT benchmark.
    """
    # Group citations by ref_idx.
    groups = {}
    invalid_count = 0
    for cit in citations:
        rid = cit.get(ref_key)
        if rid is None or rid not in evidence_table:
            invalid_count += 1
            continue
        if rid not in groups:
            groups[rid] = []
        groups[rid].append(cit["sentence"] if "sentence" in cit else cit.get("fact", ""))

    if verbose:
        info = f"({sum(len(v) for v in groups.values())} stmt, {len(groups)} src"
        if invalid_count:
            info += f", skip {invalid_count}"
        info += ")"
        print(f"    Gemini [{scenario_name}] {info}...", end=" ", flush=True)

    # Call Gemini Flash in parallel, with one thread per ref_idx.
    supported = 0
    unsupported = 0
    unknown = 0
    url_fetched = 0
    url_fallback = 0

    def _eval_one_group(ref_idx, statements):
        """Evaluate all statements associated with one ref_idx."""
        _is_url = False
        if use_url:
            eq, _is_url = get_url_reference(evidence_table, ref_idx)
        else:
            eq = get_full_reference(evidence_table, ref_idx)
        res = gemini_validate_batch(ref_idx, eq, statements, thinking_budget=thinking_budget)
        return ref_idx, res, _is_url

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_eval_one_group, ri, stmts): ri for ri, stmts in groups.items()}
        for future in as_completed(futures):
            ref_idx, results, is_from_url = future.result()
            if use_url:
                if is_from_url:
                    url_fetched += 1
                else:
                    url_fallback += 1
            for r in results:
                res = r.get("result", "unknown")
                if res == "supported":
                    supported += 1
                elif res == "unsupported":
                    unsupported += 1
                else:
                    unknown += 1

    total_eval = supported + unsupported
    c_acc = supported / total_eval if total_eval > 0 else 0.0

    if verbose:
        suffix = f"C.Acc={c_acc * 100:.1f}% ({supported}/{total_eval})"
        if use_url:
            suffix += f" [URL:{url_fetched}, fallback:{url_fallback}]"
        print(suffix)

    return c_acc, supported


def load_real_evidence(json_path):
    """Load saved run evidence in place of the synthetic EVIDENCE_TABLE.

    Args:
        json_path: Path to evidence_<id>.json.

    Returns:
        (evidence_table, section_texts, outline, section_plan)
        - evidence_table: {ref_idx_int: {url_title, entries: [{criterion, quote}]}}
        - section_texts: {section_id: text}
        - outline: list of section dictionaries
        - section_plan: the first section's plan for E2E testing
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Convert evidence_table: adapt from pipeline format to test format
    raw_table = data.get("evidence_table", {})
    evidence_table = {}
    for ref_idx_str, entry_data in raw_table.items():
        ref_idx = int(ref_idx_str)
        # Normalize: ensure entries have "criterion" and "quote" keys
        entries = []
        for e in entry_data.get("entries", []):
            entries.append(
                {
                    "criterion": e.get("criterion", ""),
                    "quote": e.get("quote", ""),
                }
            )
        evidence_table[ref_idx] = {
            "url_title": entry_data.get("title", ""),
            "url": entry_data.get("url", ""),
            "entries": entries,
        }

    section_texts = data.get("section_texts", {})
    outline = data.get("outline", [])

    # Build a section_plan from the first outline section (for E2E testing)
    section_plan = None
    if outline:
        first = outline[0]
        section_plan = {
            "section_id": first.get("section_id", "1"),
            "title": first.get("title", ""),
            "description": first.get("description", ""),
            "answers_aspect": first.get("answers_aspect", ""),
            "insights": first.get("insights", []),
        }

    return evidence_table, section_texts, outline, section_plan


def run_real_data_compare(json_path, n_runs=1, verbose=True, use_url=False):
    """Run the 3-Method Compare experiment on saved research output.

    Unlike run_e2e_compare, use saved section_texts instead of generating sections.
    Apply FACT extraction and the 3 correction methods to those sections.
    use_url=True adds a URL-mode evaluation with Jina-fetched pages for comparison.
    """
    evidence_table, section_texts, outline, _ = load_real_evidence(json_path)

    if not evidence_table:
        print(f"  ERROR: No evidence_table in {json_path}")
        return
    if not section_texts:
        print(f"  ERROR: No section_texts in {json_path}")
        return

    print(f"\n{'=' * 70}")
    print(f"  Real Data Compare: {json_path}")
    print(f"  Evidence: {len(evidence_table)} sources, Sections: {len(section_texts)}")
    print(f"{'=' * 70}")

    # Concatenate all sections as the full report text
    full_text = "\n\n".join(section_texts[k] for k in sorted(section_texts.keys()))
    print(f"  Full text: {len(full_text)} chars")

    # FACT extract citations
    print("\n  [1] FACT extracting citations...")
    fact_citations = fact_extract_citations(full_text, verbose=verbose)
    if not fact_citations:
        print("  FACT extraction failed")
        return
    print(f"  FACT: {len(fact_citations)} citations extracted")

    # ── Methods B, C in parallel ──────────────────────────────────
    print("\n  [2-3] Methods B(30B) + C(80B) in parallel...")

    def _run_method_b():
        """30B verifier: Step1 verify + Step2 find correct source"""
        verifier_llm = create_llm(use_writer_model=False)
        step1_results = {}
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {}
            for i, cit in enumerate(fact_citations):
                ref_idx = cit["ref_idx"]
                if ref_idx not in evidence_table:
                    step1_results[i] = {"verdict": "NO", "reason": f"[[{ref_idx}]] not in evidence"}
                    continue
                evidence_quote = get_best_matching_entry(evidence_table, ref_idx, cit["fact"])
                future = executor.submit(
                    verify_single_citation,
                    verifier_llm,
                    cit["fact"],
                    ref_idx,
                    evidence_quote,
                )
                futures[future] = i
            for future in as_completed(futures):
                idx = futures[future]
                step1_results[idx] = future.result()

        no_indices = [
            i for i in range(len(fact_citations)) if step1_results.get(i, {}).get("verdict", "ERROR") != "YES"
        ]
        step2_results = {}
        if no_indices:
            with ThreadPoolExecutor(max_workers=15) as executor:
                futures = {}
                for idx in no_indices:
                    cit = fact_citations[idx]
                    future = executor.submit(
                        find_correct_source,
                        verifier_llm,
                        cit["fact"],
                        evidence_table,
                        cit["ref_idx"],
                    )
                    futures[future] = idx
                for future in as_completed(futures):
                    idx = futures[future]
                    step2_results[idx] = future.result()

        corrected = []
        for i, cit in enumerate(fact_citations):
            c = {"sentence": cit["fact"], "cited_ref": cit["ref_idx"]}
            verdict = step1_results.get(i, {}).get("verdict", "ERROR")
            if verdict == "YES":
                c["final_ref"] = c["cited_ref"]
            elif i in step2_results:
                found_ref = step2_results[i].get("correct_ref")
                c["final_ref"] = found_ref if found_ref is not None else c["cited_ref"]
            else:
                c["final_ref"] = c["cited_ref"]
            corrected.append(c)

        yes_count = sum(1 for i in range(len(fact_citations)) if step1_results.get(i, {}).get("verdict") == "YES")
        print(f"  30B: Step1 {yes_count}/{len(fact_citations)}, corrected {len(step2_results)}")
        return corrected

    def _run_method_c():
        """80B verifier"""
        results_80b = run_80b_correction(fact_citations, evidence_table, verbose=verbose)
        return [{"sentence": r["fact"], "cited_ref": r["cited_ref"], "final_ref": r["final_ref"]} for r in results_80b]

    # Run B, C + optional URL prefetch all in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_b = executor.submit(_run_method_b)
        future_c = executor.submit(_run_method_c)
        # Start URL prefetch early if url mode
        future_prefetch = None
        if use_url:
            all_ref_indices = set(evidence_table.keys())
            future_prefetch = executor.submit(prefetch_urls, evidence_table, all_ref_indices)

        corrected_30b = future_b.result()
        d80b_cits = future_c.result()
        if future_prefetch is not None:
            future_prefetch.result()  # ensure prefetch done

    # ── Gemini evaluation: all 3 methods in parallel ──────────────
    print("\n  [4] Gemini evaluation (3 methods in parallel)...")
    baseline_cits = [{"sentence": c["fact"], "cited_ref": c["ref_idx"]} for c in fact_citations]

    with ThreadPoolExecutor(max_workers=3) as executor:
        fa = executor.submit(_gemini_eval_single, "A:Baseline", baseline_cits, "cited_ref", evidence_table, verbose)
        fb = executor.submit(_gemini_eval_single, "B:30B", corrected_30b, "final_ref", evidence_table, verbose)
        fc = executor.submit(_gemini_eval_single, "C:80B", d80b_cits, "final_ref", evidence_table, verbose)
        gemini_a, eff_a = fa.result()
        gemini_b, eff_b = fb.result()
        gemini_d, eff_d = fc.result()

    print(f"\n{'=' * 70}")
    print("  Results (Real Data) — Evidence Mode")
    print(f"{'=' * 70}")
    print(f"  {'Method':<15} {'C.Acc':>10} {'Eff.Cit.':>10}")
    print(f"  {'-' * 15} {'-' * 10} {'-' * 10}")
    print(f"  {'Baseline':<15} {gemini_a * 100:>9.1f}% {eff_a:>10}")
    print(f"  {'30B':<15} {gemini_b * 100:>9.1f}% {eff_b:>10}")
    print(f"  {'80B':<15} {gemini_d * 100:>9.1f}% {eff_d:>10}")
    print()

    result = {
        "total_citations": len(fact_citations),
        "gemini_baseline": gemini_a,
        "gemini_30b": gemini_b,
        "gemini_80b": gemini_d,
        "eff_baseline": eff_a,
        "eff_30b": eff_b,
        "eff_80b": eff_d,
    }

    # URL mode validates Jina-fetched page content using the FACT benchmark protocol.
    if use_url:
        print("\n  [5] URL-mode Gemini evaluation (3 methods in parallel, thinking_budget=16000)...")
        # URL prefetch already done in parallel with Methods B/C above

        with ThreadPoolExecutor(max_workers=3) as executor:
            ua = executor.submit(
                _gemini_eval_single, "A:Baseline[URL]", baseline_cits, "cited_ref", evidence_table, verbose, True, 16000
            )
            ub = executor.submit(
                _gemini_eval_single, "B:30B[URL]", corrected_30b, "final_ref", evidence_table, verbose, True, 16000
            )
            uc = executor.submit(
                _gemini_eval_single, "C:80B[URL]", d80b_cits, "final_ref", evidence_table, verbose, True, 16000
            )
            url_a, url_eff_a = ua.result()
            url_b, url_eff_b = ub.result()
            url_d, url_eff_d = uc.result()

        # URL-fetch statistics
        url_ok = sum(1 for v in _url_content_cache.values() if v is not None)
        url_fail = sum(1 for v in _url_content_cache.values() if v is None)
        print(f"\n  URL 获取: 成功 {url_ok}/{url_ok + url_fail}, 失败 {url_fail} (fallback to evidence)")

        # Comparison table
        print(f"\n{'=' * 70}")
        print("  Results (Real Data) — Evidence vs URL")
        print(f"{'=' * 70}")
        print(f"  {'Method':<15} {'Evidence':>10} {'URL':>10} {'Delta':>10} {'Eff(Ev)':>10} {'Eff(URL)':>10}")
        print(f"  {'-' * 15} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")
        for name, ev, ur, eev, eur in [
            ("Baseline", gemini_a, url_a, eff_a, url_eff_a),
            ("30B", gemini_b, url_b, eff_b, url_eff_b),
            ("80B", gemini_d, url_d, eff_d, url_eff_d),
        ]:
            delta = (ur - ev) * 100
            print(f"  {name:<15} {ev * 100:>9.1f}% {ur * 100:>9.1f}% {delta:>+9.1f}% {eev:>10} {eur:>10}")
        print()

        result["url_baseline"] = url_a
        result["url_30b"] = url_b
        result["url_80b"] = url_d
        result["url_eff_baseline"] = url_eff_a
        result["url_eff_30b"] = url_eff_b
        result["url_eff_80b"] = url_eff_d

    return result


def run_real_data_compare_multi(json_path, n_runs, use_url=False):
    """Repeat Real Data Compare and aggregate its results."""
    print("\n" + "=" * 70)
    print(f"  Real Data 3-Method Compare × {n_runs} 次")
    print(f"  Data: {json_path}")
    if use_url:
        print("  Mode: Evidence + URL comparison")
    print("=" * 70)

    all_results = []
    for i in range(1, n_runs + 1):
        print(f"\n{'─' * 70}")
        print(f"  Run {i}/{n_runs}")
        print(f"{'─' * 70}")
        result = run_real_data_compare(json_path, verbose=(i == 1), use_url=use_url)
        if result:
            all_results.append(result)

    if not all_results:
        print("  所有运行都失败了")
        return

    n = len(all_results)
    print("\n" + "=" * 70)
    print(f"  汇总 ({n} 次有效)")
    print("=" * 70)

    header = (
        f"  {'Run':<5} {'引用数':>6} {'Baseline':>10} {'30B':>10} {'80B':>10} {'Eff.B':>8} {'Eff.30':>8} {'Eff.80':>8}"
    )
    print(header)
    print(f"  {'-' * 5} {'-' * 6} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 8}")

    sums = {"baseline": 0, "30b": 0, "80b": 0, "eff_b": 0, "eff_30": 0, "eff_80": 0}

    for i, r in enumerate(all_results, 1):
        gb = r["gemini_baseline"] * 100
        g30 = r["gemini_30b"] * 100
        g80 = r["gemini_80b"] * 100
        eb = r.get("eff_baseline", 0)
        e30 = r.get("eff_30b", 0)
        e80 = r.get("eff_80b", 0)
        sums["baseline"] += r["gemini_baseline"]
        sums["30b"] += r["gemini_30b"]
        sums["80b"] += r["gemini_80b"]
        sums["eff_b"] += eb
        sums["eff_30"] += e30
        sums["eff_80"] += e80
        print(f"  {i:<5} {r['total_citations']:>6} {gb:>9.1f}% {g30:>9.1f}% {g80:>9.1f}% {eb:>8} {e30:>8} {e80:>8}")

    print(f"  {'-' * 5} {'-' * 6} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 8}")
    avg_b = sums["baseline"] / n * 100
    avg_30 = sums["30b"] / n * 100
    avg_80 = sums["80b"] / n * 100
    avg_eb = sums["eff_b"] / n
    avg_e30 = sums["eff_30"] / n
    avg_e80 = sums["eff_80"] / n
    print(
        f"  {'AVG':<5} {'':>6} {avg_b:>9.1f}% {avg_30:>9.1f}% {avg_80:>9.1f}% {avg_eb:>8.1f} {avg_e30:>8.1f} {avg_e80:>8.1f}"
    )

    # Delta vs baseline
    print(f"\n  vs Baseline:           {0:>+9.1f}% {avg_30 - avg_b:>+9.1f}% {avg_80 - avg_b:>+9.1f}%")
    print()


def run_compare_multi(n_runs):
    """Repeat 3-Method Compare and aggregate its results."""
    print("\n" + "=" * 70)
    print(f"  3-Method Compare × {n_runs} 次")
    print("=" * 70)

    all_results = []
    for i in range(1, n_runs + 1):
        result = run_e2e_compare(run_idx=i, verbose=True)
        if result:
            all_results.append(result)
        print()

    if not all_results:
        print("  所有运行都失败了")
        return

    # Aggregate results
    n = len(all_results)
    print("\n" + "=" * 70)
    print(f"  汇总 ({n} 次有效)")
    print("=" * 70)

    header = (
        f"  {'Run':<5} {'引用数':>6} {'Baseline':>10} {'30B':>10} {'80B':>10} {'Eff.B':>8} {'Eff.30':>8} {'Eff.80':>8}"
    )
    print(header)
    print(f"  {'-' * 5} {'-' * 6} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 8}")

    sums = {"baseline": 0, "30b": 0, "80b": 0, "eff_b": 0, "eff_30": 0, "eff_80": 0}

    for i, r in enumerate(all_results, 1):
        gb = r["gemini_baseline"] * 100
        g30 = r["gemini_30b"] * 100
        g80 = r["gemini_80b"] * 100
        eb = r.get("eff_baseline", 0)
        e30 = r.get("eff_30b", 0)
        e80 = r.get("eff_80b", 0)
        sums["baseline"] += r["gemini_baseline"]
        sums["30b"] += r["gemini_30b"]
        sums["80b"] += r["gemini_80b"]
        sums["eff_b"] += eb
        sums["eff_30"] += e30
        sums["eff_80"] += e80
        print(f"  {i:<5} {r['total_citations']:>6} {gb:>9.1f}% {g30:>9.1f}% {g80:>9.1f}% {eb:>8} {e30:>8} {e80:>8}")

    print(f"  {'-' * 5} {'-' * 6} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 8}")
    avg_b = sums["baseline"] / n * 100
    avg_30 = sums["30b"] / n * 100
    avg_80 = sums["80b"] / n * 100
    avg_eb = sums["eff_b"] / n
    avg_e30 = sums["eff_30"] / n
    avg_e80 = sums["eff_80"] / n
    print(
        f"  {'AVG':<5} {'':>6} {avg_b:>9.1f}% {avg_30:>9.1f}% {avg_80:>9.1f}% {avg_eb:>8.1f} {avg_e30:>8.1f} {avg_e80:>8.1f}"
    )

    # Delta vs baseline
    print(f"\n  vs Baseline:           {0:>+9.1f}% {avg_30 - avg_b:>+9.1f}% {avg_80 - avg_b:>+9.1f}%")
    print()


if __name__ == "__main__":
    if "--real-data" in sys.argv:
        # Real data mode: load evidence from a real run and test
        real_data_path = None
        for i, arg in enumerate(sys.argv):
            if arg == "--real-data" and i + 1 < len(sys.argv):
                real_data_path = sys.argv[i + 1]
                break
        if not real_data_path:
            print("Usage: --real-data <path/to/evidence_N.json> [--runs N] [--url-mode]")
            sys.exit(1)
        n_runs = 1
        for i, arg in enumerate(sys.argv):
            if arg == "--runs" and i + 1 < len(sys.argv):
                n_runs = int(sys.argv[i + 1])
                break
        use_url = "--url-mode" in sys.argv
        if n_runs > 1:
            run_real_data_compare_multi(real_data_path, n_runs, use_url=use_url)
        else:
            run_real_data_compare(real_data_path, use_url=use_url)
    elif "--compare" in sys.argv:
        # 3-Method Compare mode
        n_runs = 1
        for i, arg in enumerate(sys.argv):
            if arg == "--runs" and i + 1 < len(sys.argv):
                n_runs = int(sys.argv[i + 1])
                break
        if n_runs > 1:
            run_compare_multi(n_runs)
        else:
            run_e2e_compare()
    elif "--e2e" in sys.argv:
        # Parse --runs N.
        n_runs = 1
        for i, arg in enumerate(sys.argv):
            if arg == "--runs" and i + 1 < len(sys.argv):
                n_runs = int(sys.argv[i + 1])
                break

        if n_runs > 1:
            run_e2e_multi(n_runs)
        else:
            run_e2e_test()
    else:
        # Original verification-accuracy tests
        use_writer = "--writer" in sys.argv
        if use_writer:
            model_name = "Qwen3-Next-80B-A3B-Thinking (写作模型)"
        else:
            model_name = "Qwen3-30B-A3B-Instruct-2507 (小模型)"
        print(f"初始化 LLM ({model_name})...")
        llm = create_llm(use_writer_model=use_writer)

        # Run verification-accuracy tests.
        run_parallel_verification_test(llm, TEST_CASES, EVIDENCE_TABLE)

        # Run latency tests on the small model; the writer model is too slow.
        if not use_writer:
            run_latency_test(llm, n_parallel=10)
