#!/usr/bin/env python3
"""
Translate VERITRACE Chinese results to English using LLM.

Splits long articles into sections (by ## headers), translates each,
then reassembles. Preserves [[n]] citations and markdown formatting.

Supports parallel translation with multiple API keys from workers.yaml.

Usage:
    # With workers.yaml (parallel, one article per key)
    python translate_results.py --input deepconsult_sample10_results.jsonl \
        --output deepconsult_sample10_results_en.jsonl \
        --config workers.yaml --model qwen-plus

    # With single key
    python translate_results.py --input results.jsonl --output results_en.jsonl \
        --api-key sk-xxx --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
        --model qwen-plus
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import openai

try:
    import yaml
except ImportError:
    yaml = None


def load_keys_from_yaml(config_path: str) -> list[str]:
    """Load dashscope keys from workers.yaml."""
    if yaml is None:
        print("ERROR: pyyaml not installed. Run: pip install pyyaml")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    keys = []
    for w in config.get("workers", []):
        k = w.get("dashscope_key", "")
        if k:
            keys.append(k)
    return keys


def split_into_sections(article: str) -> list[str]:
    """Split article by ## headers into translatable chunks."""
    parts = re.split(r'(\n(?=## ))', article)
    sections = []
    current = ""
    for part in parts:
        if part == "\n":
            current += part
        else:
            if current:
                sections.append(current)
            current = part
    if current:
        sections.append(current)

    # Split further if sections are too long
    final_sections = []
    for sec in sections:
        if len(sec) > 6000:
            subsections = re.split(r'(\n(?=### ))', sec)
            subsec_current = ""
            for sub in subsections:
                if sub == "\n":
                    subsec_current += sub
                else:
                    if len(subsec_current) + len(sub) > 5000 and subsec_current:
                        final_sections.append(subsec_current)
                        subsec_current = sub
                    else:
                        subsec_current += sub
            if subsec_current:
                final_sections.append(subsec_current)
        else:
            final_sections.append(sec)

    return final_sections


TRANSLATE_SYSTEM_PROMPT = (
    "You are a professional translator. Translate the following Chinese research report section "
    "into fluent, professional English. Preserve ALL markdown formatting (headers ##, ###, "
    "tables, bold, lists, etc.) and ALL [[n]] citation markers exactly as they appear. "
    "Do not add, remove, or renumber any [[n]] citations. "
    "Translate naturally — do not produce word-by-word translation. "
    "Keep technical terms accurate. Output ONLY the translated text."
)


def translate_section(client: openai.OpenAI, model: str, section: str) -> str:
    """Translate a single section from Chinese to English."""
    if not section.strip():
        return section

    # Skip sections that are already mostly English
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', section))
    total_chars = len(section.strip())
    if total_chars > 0 and chinese_chars / total_chars < 0.05:
        return section

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": section},
        ],
        temperature=0.3,
        max_tokens=8000,
    )
    return response.choices[0].message.content


def translate_article(client: openai.OpenAI, model: str, article: str, article_id: int) -> str:
    """Translate a full article by splitting into sections."""
    sections = split_into_sections(article)
    print(f"  id={article_id}: {len(sections)} sections, {len(article)} chars")

    translated_sections = []
    for i, sec in enumerate(sections):
        try:
            translated = translate_section(client, model, sec)
            translated_sections.append(translated)
            sys.stdout.write(f"\r  id={article_id}: {i+1}/{len(sections)} sections done")
            sys.stdout.flush()
        except Exception as e:
            print(f"\n  WARNING: id={article_id} section {i+1} failed: {e}")
            translated_sections.append(sec)

    print()
    return "\n".join(translated_sections)


def translate_one_record(rec: dict, api_key: str, base_url: str, model: str) -> dict:
    """Translate one record (used for parallel execution)."""
    if rec.get("status") != "success" or not rec.get("article"):
        print(f"  id={rec['id']}: skipped (status={rec.get('status')})")
        return rec

    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    rec["article"] = translate_article(client, model, rec["article"], rec["id"])
    return rec


def main():
    parser = argparse.ArgumentParser(description="Translate Chinese results to English")
    parser.add_argument("--input", required=True, help="Input JSONL (Chinese results)")
    parser.add_argument("--output", required=True, help="Output JSONL (English results)")
    parser.add_argument("--config", default="", help="workers.yaml for parallel keys")
    parser.add_argument("--api-key", default="", help="Single API key (if no --config)")
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1", help="API base URL")
    parser.add_argument("--model", default="qwen-plus", help="Model for translation")
    args = parser.parse_args()

    # Load keys
    if args.config:
        keys = load_keys_from_yaml(args.config)
        print(f"Loaded {len(keys)} keys from {args.config}")
    elif args.api_key:
        keys = [args.api_key]
    else:
        keys = [os.environ.get("OPENAI_API_KEY", "")]

    if not keys or not keys[0]:
        print("ERROR: No API keys provided")
        sys.exit(1)

    # Load records
    records = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    # Filter to success only for translation
    to_translate = [r for r in records if r.get("status") == "success" and r.get("article")]
    print(f"Loaded {len(records)} records, {len(to_translate)} to translate")

    # Parallel translate: assign keys round-robin
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(keys), len(to_translate))) as executor:
        futures = {}
        for i, rec in enumerate(to_translate):
            key = keys[i % len(keys)]
            future = executor.submit(translate_one_record, rec, key, args.base_url, args.model)
            futures[future] = rec["id"]

        for future in as_completed(futures):
            qid = futures[future]
            try:
                translated_rec = future.result()
                results[qid] = translated_rec
                print(f"  id={qid}: DONE")
            except Exception as e:
                print(f"  id={qid}: FAILED - {e}")

    # Merge back (keep original order, update translated ones)
    for i, rec in enumerate(records):
        if rec["id"] in results:
            records[i] = results[rec["id"]]

    # Save
    with open(args.output, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\nDone: {len(results)} articles translated → {args.output}")


if __name__ == "__main__":
    main()
