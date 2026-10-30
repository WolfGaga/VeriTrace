import multiprocessing
import json
import os
import argparse
import re
from functools import partial
from .io_utils import load_jsonl
from .api import call_model


prompt_template = """你会看到一篇研究报告，研究报告正文中会有一些对参考文献的引用。
正文中的引用可能以如下形式出现：
1. 一段文字+空格+数字，例如："李强基于收入、教育和职业构造了一个社会经济地位指数（SES），将社会划分为7个等级 15"
2. 一段文字+[（一个或多个)数字]，例如："李强基于收入、教育和职业构造了一个社会经济地位指数（SES），将社会划分为7个等级[15]"
3. 一段文字+[（一个或多个)数字†(一些行号等内容)]，例如："李强基于收入、教育和职业构造了一个社会经济地位指数（SES），将社会划分为7个等级[15†L10][5L23][7†summary][9summary]"
4. [引用来源](引用链接)，例如："根据[ChinaFile: A Guide to Social Class in Modern China](https://www.chinafile.com/reporting-opinion/media/guide-social-class-modern-china)'s分类，中国社会可分为九个阶层"

请从正文中找出**所有**引用了参考文献的地方，提取出(fact, ref_idx, url)三元组，提取的时候，注意以下事项：
1. 由于后续需要检验这些facts是否正确，你可能需要在引用的前后寻找一些上下文，以确保fact是完整可理解的，而不是简单的词组或短语
2. 如果一个fact引用了多个文献，那么它应该对应多个三元组，例如如果引用了2个文献，则应该是(fact, ref_idx_1, url_1)和(fact, ref_idx_2, url_2)
3. 对于第三种形式的引用，ref_idx仅考虑第一个数字部分，不考虑其他指示具体位置的内容；对于第四种形式的引用（即引用来源和链接直接出现在正文中）的情况，ref_idx统一设置为0
4. 如果正文中没有标出引用的具体位置（比如仅在文章结尾列出了参考文献列表，而没有在正文中标出），请返回空列表

你应该返回json列表格式，列表中的每一项是一个三元组，例如：
[
    {{
        "fact": "原文中的文本片段，注意中文引号要用全角, 英文引号前加单个反斜杠转义",
        "ref_idx": "该段文字引用的参考文献在参考文献列表中的索引",
        "url": "该段文字引用的参考文献链接（从研究报告结尾的参考文献列表或引用处的括号中提取）"
    }}
]

下面是研究报告的正文：
{report_text}

下面开始提取，直接输出json列表，不要输出任何闲聊或解释。"""


prompt_template_en = """You will be provided with a research report. The body of the report will contain some citations to references.

Citations in the main text may appear in the following forms:
1. A segment of text + space + number, for example: "Li Qiang constructed a socioeconomic status index (SES) based on income, education, and occupation, dividing society into 7 levels 15"
2. A segment of text + [number], for example: "Li Qiang constructed a socioeconomic status index (SES) based on income, education, and occupation, dividing society into 7 levels[15]"
3. A segment of text + [number†(some line numbers, etc.)], for example: "Li Qiang constructed a socioeconomic status index (SES) based on income, education, and occupation, dividing society into 7 levels[15†L10][5L23][7†summary]"
4. [Citation Source](Citation Link), for example: "According to [ChinaFile: A Guide to Social Class in Modern China](https://www.chinafile.com/reporting-opinion/media/guide-social-class-modern-china)'s classification, Chinese society can be divided into nine strata"

Please identify **all** instances where references are cited in the main text, and extract (fact, ref_idx, url) triplets. When extracting, pay attention to the following:
1. Since these facts will need to be verified later, you may need to look for some context before and after the citation to ensure that the fact is complete and understandable, rather than just a simple phrase or short expression.
2. If a fact cites multiple references, then it should correspond to two triplets: (fact, ref_idx_1, url_1) and (fact, ref_idx_2, url_2).
3. For the third form of citation (i.e., where the citation source and link appear directly in the text), the ref_idx should be uniformly set to 0.
4. If the main text does not specify the exact location of the citation (for example, only the reference list is listed at the end of the article, without specifying the citation point in the text), please return an empty list.

You should return a JSON list format, where each item in the list is a triplet, for example:
[
    {{
        "fact": "Text segment from the original document. Note that Chinese quotation marks should use full-width marks. And add a single backslash before the English quotation mark to make it a readable for python json module.",
        "ref_idx": "The index of the cited reference in the reference list for this text segment.",
        "url": "The URL of the cited reference for this text segment (extracted from the reference list at the end of the research report or from the parentheses at the citation point)."
    }}
]

Here is the main text of the research report:
{report_text}

Please begin the extraction now. Output only the JSON list directly, without any chitchat or explanations."""


def clean_urls(input_text):
    # match [title](url) format
    pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    
    def repl(match):
        title = match.group(1)
        url = match.group(2)
        # truncate #:~:text= and its content
        cut_idx = url.find('#:~:text=')
        if cut_idx != -1:
            url = url[:cut_idx]
        return f'[{title}]({url})'
    
    return pattern.sub(repl, input_text)


def remove_urls(input_text):
    # match [title](url) format, only remove the content in the parentheses, keep [title]
    pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    # replace [title](url) with [title]
    return pattern.sub(r'[\1]', input_text)


def clean_escape(input_text):
    # replace illegal escape characters that LLMs sometimes emit in JSON strings
    for ch in ['>', '<', '+', '~', '$', '%', '@', '#', '&', '=', '!', '(', ')', '*', ',', '.', ':', ';', '?', ' ']:
        input_text = input_text.replace("\\" + ch, ch)
    return input_text


# Body chars above which we split into multiple chunks. Each chunk targets
# ~40k body chars (≈ 15-25k tokens) so Flash can produce a complete JSON
# extraction without truncation. The reference list is appended to every chunk.
LONG_ARTICLE_CHAR_THRESHOLD = 40000


def _find_reference_section(text):
    """Locate the references section (heading-based or inline-list) in the article.

    Returns the start index of the reference section if found, else None.
    Supported formats:
      1. Heading: `## References`, `## 参考文献`, `## Bibliography`, `**References**`, etc.
      2. Inline: a trailing block of consecutive `[[N]] [...](url)` entries with
         no other prose between them (start of that block).
    """
    patterns = [
        r'(?im)^\s*#{1,6}\s*(references|参考文献|参考资料|引用文献|works cited|bibliography)\s*$',
        r'(?im)^\s*\*\*\s*(references|参考文献|参考资料|引用文献|works cited|bibliography)\s*\*\*\s*$',
    ]
    last_match = None
    for pat in patterns:
        for m in re.finditer(pat, text):
            if last_match is None or m.start() > last_match.start():
                last_match = m
    if last_match:
        return last_match.start()

    # Fallback: detect a trailing block of consecutive `[[N]] [Title](url)` entries.
    # We require >=5 such ref lines, all clustered near end-of-document,
    # and treat the start of the cluster (where consecutive entries begin) as ref start.
    ref_line_pat = re.compile(r'^[ \t]*\[\[\d+\]\][ \t]*\[[^\]]+\]\(https?://', re.MULTILINE)
    matches = list(ref_line_pat.finditer(text))
    if len(matches) < 5:
        return None
    # Walk back from last match; each consecutive ref must be within a small gap
    # of the previous one (i.e., the article's tail is just a list, no prose).
    MAX_GAP = 4000  # characters between consecutive ref entries; prose between → gap > 4K
    start_idx = matches[-1].start()
    for prev, cur in zip(matches[-2::-1], matches[::-1]):
        if cur.start() - prev.end() > MAX_GAP:
            break
        start_idx = prev.start()
    return start_idx


# Maximum [[N]] markers per chunk to keep extraction output JSON under
# Flash's ~50K char output limit. Each citation entry produces ~250 chars
# (fact + ref_idx + url + JSON overhead), so 200 markers ≈ 50K chars output.
MAX_MARKERS_PER_CHUNK = 200


def _split_article_for_extract(article):
    """Split a long article into N chunks, each chunk = body_segment + full reference list.

    Number of chunks = max of:
      - chars-based: ceil(body_len / LONG_ARTICLE_CHAR_THRESHOLD)
      - markers-based: ceil(body_marker_count / MAX_MARKERS_PER_CHUNK)

    The marker constraint protects against output truncation when citation density
    is high (e.g. body 28K but 240 [[N]] marks → 240 citations × 250 chars = 60K
    output, exceeding Flash limits despite small input).

    Returns a list of 1 or more text chunks. Splits at paragraph boundaries when possible.
    """
    body_marker_count = article.count('[[')

    # Fast path: small article + few markers → single chunk
    if (len(article) <= LONG_ARTICLE_CHAR_THRESHOLD
            and body_marker_count <= MAX_MARKERS_PER_CHUNK):
        return [article]

    ref_start = _find_reference_section(article)
    if ref_start is not None:
        body = article[:ref_start].rstrip()
        refs = article[ref_start:]
        # body_marker_count = markers in body only (excluding refs section markers)
        body_marker_count = body.count('[[')
    else:
        body = article
        refs = ""

    if (len(body) <= LONG_ARTICLE_CHAR_THRESHOLD
            and body_marker_count <= MAX_MARKERS_PER_CHUNK):
        return [article]

    # Need to chunk: pick N to satisfy BOTH char and marker budgets
    n_by_chars = -(-len(body) // LONG_ARTICLE_CHAR_THRESHOLD)
    n_by_markers = -(-body_marker_count // MAX_MARKERS_PER_CHUNK)
    n_chunks = max(n_by_chars, n_by_markers, 2)
    target = len(body) // n_chunks

    # Find n_chunks-1 split points at paragraph boundaries near each target offset
    splits = [0]
    for i in range(1, n_chunks):
        ideal = i * target
        window = 2000
        search_lo = max(splits[-1] + 1, ideal - window)
        search_hi = min(len(body), ideal + window)
        seg = body[search_lo:search_hi]
        candidates = [m.start() for m in re.finditer(r'\n\s*\n', seg)]
        if candidates:
            best = min(candidates, key=lambda p: abs((search_lo + p) - ideal))
            splits.append(search_lo + best)
        else:
            splits.append(ideal)
    splits.append(len(body))

    chunks = []
    for i in range(n_chunks):
        seg = body[splits[i]:splits[i+1]].strip()
        if refs:
            seg = seg + "\n\n" + refs
        chunks.append(seg)
    return chunks


def _extract_one_chunk(user_prompt, article_id):
    """Run one Flash extract attempt with Pro fallback. Returns (citations_list_or_None, last_response_str)."""
    response = ""
    citations = None
    try:
        response = call_model(user_prompt)
    except Exception as e:
        print(f"[chunk extract] flash call failed for {article_id}: {repr(e)}")
        response = ""

    try:
        if response != "":
            resp = response.replace("```json", "").replace("```", "")
            resp = clean_escape(resp)
            citations = json.loads(resp)
    except Exception as e:
        print(f"[chunk extract] flash parse failed for {article_id}: {repr(e)}")
        citations = None

    if citations is None:
        print(f">>>>>>>>>> Flash failed on chunk for {article_id}, falling back to gemini-2.5-pro")
        response_pro = ""
        try:
            response_pro = call_model(user_prompt, model="gemini-2.5-pro")
        except Exception as e:
            print(f"[chunk extract] pro call failed for {article_id}: {repr(e)}")
            response_pro = ""
        try:
            if response_pro:
                resp = response_pro.replace("```json", "").replace("```", "")
                resp = clean_escape(resp)
                citations = json.loads(resp)
        except Exception as e:
            print(f"[chunk extract] pro parse failed for {article_id}: {repr(e)}")
            citations = None
        return citations, (response_pro or response)
    return citations, response


def _build_user_prompt(lang, report_text):
    if lang == "zh":
        return prompt_template.format(report_text=report_text)
    return prompt_template_en.format(report_text=report_text)


def _dedupe_citations(citations):
    """Drop duplicates introduced by overlapping reference list across chunks.

    Dedupe key: (fact, ref_idx, url).
    """
    seen = set()
    out = []
    for c in citations:
        try:
            key = (c.get('fact', ''), str(c.get('ref_idx', '')), c.get('url', ''))
        except Exception:
            key = None
        if key is None or key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# Matches both `[[N]] [Title](url)` (deepseek) and `**[N]** [Title](url)` (27B).
# Title may contain a single nested bracket pair like `[PDF]` (markdown link
# titles in some refs include `[[PDF] Title](url)`).
_REF_LIST_PAT = re.compile(
    r'(?:\[\[(\d+)\]\]|\*\*\[(\d+)\]\*\*)\s*\[((?:\[[^\]]*\]|[^\]])+)\]\((https?://[^)]+)\)'
)


def _build_canonical_refs(article):
    """Parse article's reference list to build {ref_idx: URL} mapping.

    Supports both `[[N]] [Title](URL)` and `**[N]** [Title](URL)` formats,
    with optional nested `[PDF]` etc. inside the title. Returns
    {ref_idx_str: canonical_url}. If the same ref_idx appears multiple times,
    last occurrence wins (refs section is at the end so this is intentional —
    body inline links don't override).
    """
    canonical = {}
    for m in _REF_LIST_PAT.finditer(article):
        idx = m.group(1) or m.group(2)
        canonical[idx] = m.group(4)
    return canonical


def _normalize_citations_to_canonical(citations, canonical_refs):
    """For each citation whose ref_idx is in the article's reference list,
    overwrite the LLM-extracted url with the canonical url from the ref list.

    This fixes a chunked-extraction bug where Gemini in one chunk may grab a
    nearby inline link as the URL for a [[N]] marker instead of looking up
    the actual reference list. Citations whose ref_idx is not in the ref list
    are left unchanged (likely inline-link citations or LLM hallucinations).
    """
    fixed = 0
    for c in citations:
        if not isinstance(c, dict): continue
        ref_idx = str(c.get('ref_idx', ''))
        if ref_idx in canonical_refs:
            canonical = canonical_refs[ref_idx]
            if c.get('url') != canonical:
                c['url'] = canonical
                fixed += 1
    return fixed


def _extract_with_chunking(article, lang, article_id):
    """Extract citations from article, splitting into 2 chunks if over threshold.

    Returns (citations_list_or_None, used_pro_fallback_bool, last_response_str_for_failure).
    """
    chunks = _split_article_for_extract(article)
    all_citations = []
    used_pro = False
    last_failure_response = ""
    for idx, chunk in enumerate(chunks):
        prompt = _build_user_prompt(lang, chunk)
        cits, last_resp = _extract_one_chunk(prompt, f"{article_id}#chunk{idx+1}/{len(chunks)}")
        if cits is None:
            last_failure_response = last_resp
            return None, used_pro, last_failure_response
        all_citations.extend(cits)

    # Normalize URLs against the article's canonical reference list. This
    # corrects cases where chunked extraction may pick a wrong URL for a
    # [[N]] marker (e.g. nearby inline link instead of ref-list lookup).
    canonical_refs = _build_canonical_refs(article)
    if canonical_refs:
        n_fixed = _normalize_citations_to_canonical(all_citations, canonical_refs)
        if n_fixed > 0:
            print(f">>>>>>>>>> Article {article_id}: normalized {n_fixed} URL(s) to canonical ref-list URLs")

    deduped = _dedupe_citations(all_citations)
    return deduped, used_pro, ""


def run(data, output_path, id_to_lang_map):
    for i, d in enumerate(data):
        # Determine the language based on the id in the query data
        article_id = d.get('id')
        
        if not article_id:
            print(f"Article has no ID field, skipping extraction")
            continue
            
        if article_id not in id_to_lang_map:
            print(f"Language not found for article ID: {article_id}")
            continue
            
        lang = id_to_lang_map[article_id]
        if lang not in ["zh", "en"]:
            print(f"Unsupported language: {lang} for article ID: {article_id}")
            continue
            
        article = d['article']
        chunks = _split_article_for_extract(article)
        if len(chunks) > 1:
            print(f">>>>>>>>>> Article {article_id} is long ({len(article)} chars), splitting into {len(chunks)} chunks")
            d['extract_chunked'] = len(chunks)

        citations, _used_pro, last_failure = _extract_with_chunking(article, lang, article_id)
        if citations is not None:
            for c in citations:
                if isinstance(c, dict) and 'fact' in c:
                    c['fact'] = remove_urls(c['fact'])
            d['citations'] = citations
            with open(output_path, "a+", encoding='utf-8') as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
            print(f">>>>>>>>>> generating {d['id']}-th instance ({len(citations)} citations)...")
        else:
            print(f">>>>>>>>>> All attempts failed (incl. pro fallback), article ID: {article_id}")
            try:
                import os as _os
                fail_dir = _os.path.join(_os.path.dirname(output_path), "failed_extracts")
                _os.makedirs(fail_dir, exist_ok=True)
                with open(_os.path.join(fail_dir, f"failed_{article_id}.txt"), "w", encoding="utf-8") as ff:
                    ff.write(last_failure or "<empty response>")
            except Exception:
                pass


if __name__ == '__main__':

    # multiprocessing.set_start_method('fork')  # 'fork' not available on Windows

    parser = argparse.ArgumentParser()
    parser.add_argument("--output_path", type=str, required=True)
    parser.add_argument("--raw_data_path", type=str, required=True)
    parser.add_argument("--query_data_path", type=str, required=True, help="Path to query data with language information")
    parser.add_argument("--n_total_process", type=int, default=1)
    args = parser.parse_args()

    output_path = args.output_path
    
    # Load the query data to get language information
    query_data = load_jsonl(args.query_data_path)
    
    # Create a mapping from ID to language
    id_to_lang_map = {item['id']: item.get('language') for item in query_data if 'id' in item and 'language' in item}
    
    if not id_to_lang_map:
        raise ValueError("No valid language information found in query data")
    
    # Load the raw data
    raw_data = load_jsonl(args.raw_data_path)

    # If the output file exists, load the processed ids and filter out the processed instances
    if os.path.exists(output_path):
        processed = [d['id'] for d in load_jsonl(output_path)]
        data_to_process = [d for d in raw_data if d['id'] not in processed]
    else:
        data_to_process = raw_data

    # OpenAI deep research will add webpage snippets to the citations.
    # For fair comparison, we remove these snippets
    if 'openai' in args.raw_data_path:
        for d in data_to_process:
            d['article'] = clean_urls(d['article'])

    print(f"Processing {len(data_to_process)} instances...")

    n_total_process = args.n_total_process
    if n_total_process <= 1:
        run(data_to_process, output_path, id_to_lang_map)
    else:
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        _write_lock = threading.Lock()

        def _process_one(d):
            article_id = d.get('id')
            if not article_id or article_id not in id_to_lang_map:
                return
            lang = id_to_lang_map[article_id]
            if lang not in ["zh", "en"]:
                return
            article = d['article']
            chunks = _split_article_for_extract(article)
            if len(chunks) > 1:
                print(f">>>>>>>>>> Article {article_id} is long ({len(article)} chars), splitting into {len(chunks)} chunks")
                d['extract_chunked'] = len(chunks)

            citations, _used_pro, last_failure = _extract_with_chunking(article, lang, article_id)
            if citations is not None:
                for c in citations:
                    if isinstance(c, dict) and 'fact' in c:
                        c['fact'] = remove_urls(c['fact'])
                d['citations'] = citations
                with _write_lock:
                    with open(output_path, "a+", encoding='utf-8') as f:
                        f.write(json.dumps(d, ensure_ascii=False) + "\n")
                print(f">>>>>>>>>> generating {d['id']}-th instance ({len(citations)} citations)...")
                return

            print(f">>>>>>>>>> All attempts failed (incl. pro fallback), article ID: {article_id}")
            try:
                import os as _os
                fail_dir = _os.path.join(_os.path.dirname(output_path), "failed_extracts")
                _os.makedirs(fail_dir, exist_ok=True)
                with open(_os.path.join(fail_dir, f"failed_{article_id}.txt"), "w", encoding="utf-8") as ff:
                    ff.write(last_failure or "<empty response>")
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=n_total_process) as executor:
            futures = [executor.submit(_process_one, d) for d in data_to_process]
            for f in as_completed(futures):
                f.result()  # Raise exceptions if any