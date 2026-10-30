#!/usr/bin/env python3
"""
DeepConsult Benchmark Conversion Tools

Two commands:
  1. to-jsonl:  queries.csv → JSONL for run_parallel.py / run_terminal.py
  2. to-eval:   VERITRACE results JSONL + baseline CSV → evaluation CSV for ydc-deep-research-evals

Usage:
    # Step 1: Convert queries
    python deepconsult_convert.py to-jsonl \
        --queries ../../ydc-deep-research-evals/datasets/DeepConsult/queries.csv \
        --output deepconsult_queries.jsonl

    # Step 2: Run VERITRACE (use run_parallel.py or run_terminal.py)
    python run_parallel.py --input deepconsult_queries.jsonl \
        --output deepconsult_results.jsonl --config workers.yaml --lang en

    # Step 3: Convert results for evaluation
    python deepconsult_convert.py to-eval \
        --results deepconsult_results.jsonl \
        --responses ../../ydc-deep-research-evals/datasets/DeepConsult/responses_OpenAI-DeepResearch_vs_ARI_2025-05-15.csv \
        --output ../../ydc-deep-research-evals/datasets/DeepConsult/veritrace_eval.csv

    # Step 4: Run evaluation
    cd ../../ydc-deep-research-evals
    python -m evals.deep_research_pairwise_evals \
        --input-data datasets/DeepConsult/veritrace_eval.csv \
        --output-dir results/veritrace
"""

import argparse
import csv
import json
import re


def clean_article_for_eval(article: str) -> str:
    """Prepare article for DeepConsult pairwise eval.

    Strips the Search Process References tail (research-process metadata).
    Body and the Report Citations block are preserved as-is; [[n]] markers
    are kept unchanged. Validated on 20q Vertex: line-level and paragraph-level
    citation dedup both tested neutral-to-negative vs. this baseline, so they
    are not worth the complexity.
    """
    article = re.sub(
        r'\n+##\s+(?:Search Process References|搜索过程参考来源)[\s\S]*$',
        '', article
    )
    article = re.sub(r'\n{3,}', '\n\n', article)
    return article.strip()


def to_jsonl(queries_csv: str, output: str):
    """Convert DeepConsult queries.csv to JSONL for VERITRACE runners."""
    queries = []
    with open(queries_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            queries.append({"id": i, "prompt": row["query"]})

    with open(output, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    print(f"Converted {len(queries)} queries → {output}")


def to_eval(results_jsonl: str, responses_csv: str, output: str):
    """Combine VERITRACE results with baseline answers into evaluation CSV.

    Matches by question text (queries.csv question == responses CSV question).
    Only includes successfully completed queries.
    """
    # Load baseline responses (question → baseline_answer)
    baseline = {}
    with open(responses_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            baseline[row["question"]] = row["baseline_answer"]

    print(f"Loaded {len(baseline)} baseline responses")

    # Load VERITRACE results (id → {prompt, article})
    results = {}
    with open(results_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("status") == "success" and obj.get("article"):
                results[obj["id"]] = obj

    print(f"Loaded {len(results)} successful VERITRACE results")

    # Load query mapping (id → original query text from queries.csv)
    # We need this because run_parallel stores the prompt, which should match
    # the queries.csv text, which should match the responses CSV question.

    # Match and write evaluation CSV
    matched = 0
    skipped_no_baseline = 0
    skipped_no_result = 0

    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question", "baseline_answer", "candidate_answer"])
        writer.writeheader()

        for qid, result in sorted(results.items()):
            prompt = result["prompt"]
            article = clean_article_for_eval(result["article"])

            # Find baseline by matching question text
            if prompt in baseline:
                writer.writerow({
                    "question": prompt,
                    "baseline_answer": baseline[prompt],
                    "candidate_answer": article,
                })
                matched += 1
            else:
                # Try fuzzy match (strip whitespace)
                found = False
                for bq, ba in baseline.items():
                    if bq.strip() == prompt.strip():
                        writer.writerow({
                            "question": bq,
                            "baseline_answer": ba,
                            "candidate_answer": article,
                        })
                        matched += 1
                        found = True
                        break
                if not found:
                    skipped_no_baseline += 1
                    print(f"  WARNING: No baseline match for id={qid}: {prompt[:80]}...")

    total_baseline = len(baseline)
    skipped_no_result = total_baseline - matched - skipped_no_baseline

    print(f"\nResult: {matched} matched → {output}")
    if skipped_no_baseline:
        print(f"  {skipped_no_baseline} skipped (no baseline match)")
    if skipped_no_result > 0:
        print(f"  {skipped_no_result} baseline queries without VERITRACE result")


def main():
    parser = argparse.ArgumentParser(description="DeepConsult benchmark conversion tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # to-jsonl
    p1 = subparsers.add_parser("to-jsonl", help="Convert queries.csv to JSONL")
    p1.add_argument("--queries", required=True, help="Path to DeepConsult queries.csv")
    p1.add_argument("--output", required=True, help="Output JSONL path")

    # to-eval
    p2 = subparsers.add_parser("to-eval", help="Combine results with baseline for evaluation")
    p2.add_argument("--results", required=True, help="VERITRACE results JSONL")
    p2.add_argument("--responses", required=True, help="DeepConsult responses CSV (with baseline_answer)")
    p2.add_argument("--output", required=True, help="Output evaluation CSV")

    args = parser.parse_args()

    if args.command == "to-jsonl":
        to_jsonl(args.queries, args.output)
    elif args.command == "to-eval":
        to_eval(args.results, args.responses, args.output)


if __name__ == "__main__":
    main()
