#!/usr/bin/env python3
"""
Save frontend/manual article to eval_data for evaluation.

Converts a pasted article text file into the same JSONL format as run_terminal.py,
so you can use run_eval.py to run FACT and RACE evaluation.

Usage:
    # Save article for benchmark query ID=1
    python save_frontend_article.py --file article.txt --id 1

    # Save with a custom prompt (Chinese example, not a benchmark query)
    python save_frontend_article.py --file article.txt --id 99 --prompt "自定义问题"

    # Save to a specific output file
    python save_frontend_article.py --file article.txt --id 1 --output eval_data/frontend/my_test.jsonl

    # Append multiple articles to the same file
    python save_frontend_article.py --file article_q1.txt --id 1 --output eval_data/frontend/batch.jsonl
    python save_frontend_article.py --file article_q2.txt --id 2 --output eval_data/frontend/batch.jsonl --append

    # Then evaluate:
    python run_eval.py --input eval_data/frontend/q1.jsonl --name frontend_q1
"""

import argparse
import json
import os
import sys
from pathlib import Path

VERITRACE_DIR = Path(__file__).resolve().parent.parent
BENCH_DIR = VERITRACE_DIR.parent / "deep_research_bench"
QUERY_FILE = BENCH_DIR / "data" / "prompt_data" / "query.jsonl"
DEFAULT_OUTPUT_DIR = VERITRACE_DIR / "eval_data" / "frontend"


def load_query_by_id(query_id: int) -> dict:
    """Load a specific query from the benchmark query file."""
    if not QUERY_FILE.exists():
        print(f"Error: Query file not found: {QUERY_FILE}")
        sys.exit(1)
    with open(QUERY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("id") == query_id:
                return obj
    return {}


def main():
    parser = argparse.ArgumentParser(description="Save frontend article to eval_data for evaluation")
    parser.add_argument("--file", required=True, help="Path to article text file")
    parser.add_argument("--id", type=int, required=True, help="Query ID (matches benchmark query.jsonl)")
    parser.add_argument("--prompt", type=str, default=None,
                        help="Custom prompt (overrides query.jsonl lookup)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSONL path (default: eval_data/frontend/q<id>.jsonl)")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing file instead of overwriting")
    args = parser.parse_args()

    # Read article
    if not os.path.exists(args.file):
        print(f"Error: Article file not found: {args.file}")
        sys.exit(1)
    with open(args.file, "r", encoding="utf-8") as f:
        article = f.read().strip()
    if len(article) < 50:
        print(f"Error: Article too short ({len(article)} chars). Check the file.")
        sys.exit(1)

    # Get prompt
    if args.prompt:
        prompt = args.prompt
    else:
        query = load_query_by_id(args.id)
        if not query:
            print(f"Error: Query ID={args.id} not found in {QUERY_FILE}")
            print("Use --prompt to specify a custom prompt.")
            sys.exit(1)
        prompt = query["prompt"]

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = DEFAULT_OUTPUT_DIR / f"q{args.id}.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build result record (same format as run_terminal.py)
    record = {
        "id": args.id,
        "prompt": prompt,
        "article": article,
        "cited_references": [],
        "status": "success",
        "error": None,
        "duration_seconds": 0,
    }

    # Write
    mode = "a" if args.append else "w"
    with open(output_path, mode, encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Saved: ID={args.id}, {len(article)} chars -> {output_path}")
    print(f"Prompt: {prompt[:80]}...")
    print()
    print("Next steps:")
    print(f"  python benchmark/run_eval.py --input {output_path} --name frontend_q{args.id}")
    print(f"  python benchmark/run_eval.py --input {output_path} --name frontend_q{args.id} --race-only")


if __name__ == "__main__":
    main()
