#!/usr/bin/env python3
"""
VERITRACE Evaluation Runner

One-click script to run FACT and/or RACE evaluation on terminal runner output.

Examples:
    # Run both FACT and RACE
    python run_eval.py --input results.jsonl --name my_test

    # Run only FACT
    python run_eval.py --input results.jsonl --name my_test --fact-only

    # Run only RACE
    python run_eval.py --input results.jsonl --name my_test --race-only
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

VERITRACE_DIR = Path(__file__).resolve().parent.parent  # benchmark/ → VERITRACE/
load_dotenv(VERITRACE_DIR / ".env")
if str(VERITRACE_DIR) not in sys.path:
    sys.path.insert(0, str(VERITRACE_DIR))
BENCH_DIR = VERITRACE_DIR.parent / "deep_research_bench"
QUERY_DATA = BENCH_DIR / "data" / "prompt_data" / "query.jsonl"


def load_successful_results(path: str):
    """Load terminal runner results, keep only successful ones with articles."""
    results = []
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                total += 1
                if obj.get("status") == "success" and obj.get("article"):
                    results.append(obj)
            except json.JSONDecodeError:
                continue
    return results, total


def _veritrace_scrape_url(url):
    """Fetch a single URL using VERITRACE PageSelectTool (multi-level fallback)."""
    from veritrace.agent.core.tools import PageSelectTool
    tool = PageSelectTool(indices=[0])
    try:
        content = tool._fetch_page(url)
        return {'url': url, 'url_content': content or 'scrape failed: empty content'}
    except Exception as e:
        return {'url': url, 'url_content': f'scrape failed: {e}'}


def _veritrace_scrape(dedup_path, scraped_path, n_workers=5):
    """Scrape all URLs using VERITRACE PageSelectTool with thread pool."""
    data = []
    with open(dedup_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))

    print(f"[FACT] VERITRACE scraping {len(data)} articles with {n_workers} workers...")

    # Clear output file
    with open(scraped_path, "w", encoding="utf-8") as f:
        pass

    for i, d in enumerate(data):
        urls = list(d.get('citations_deduped', {}).keys())
        if not urls:
            with open(scraped_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
            continue

        print(f"  Article {i+1}/{len(data)}: {len(urls)} URLs to scrape")
        scraped = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_veritrace_scrape_url, url): url for url in urls}
            for future in as_completed(futures):
                res = future.result()
                d['citations_deduped'][res['url']]['url_content'] = res['url_content']
                if res['url_content'].startswith('scrape failed'):
                    failed += 1
                else:
                    scraped += 1

        print(f"    Done: {scraped} scraped, {failed} failed")

        with open(scraped_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


def run_fact(results, name, n_workers=1, veritrace_scrape=False, scrape_workers=None, resume=False, extract_workers=None):
    """Run official FACT pipeline: extract → deduplicate → scrape → validate → stat."""
    fact_dir = BENCH_DIR / "results" / "fact" / name
    fact_dir.mkdir(parents=True, exist_ok=True)

    # Write raw data for extract.py (only needs id + article)
    raw_path = fact_dir / "raw.jsonl"
    with open(raw_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({"id": r["id"], "article": r["article"]}, ensure_ascii=False) + "\n")
    print(f"[FACT] Wrote {len(results)} articles to {raw_path}")

    extracted_path = fact_dir / "extracted.jsonl"
    dedup_path = fact_dir / "deduplicated.jsonl"
    scraped_path = fact_dir / "scraped.jsonl"
    validated_path = fact_dir / "validated.jsonl"
    if resume:
        # Validated must be redone since unknown rate / scrape resume changes results.
        # Keep extracted/deduplicated/scraped as caches.
        if validated_path.exists():
            validated_path.unlink()
        print("[FACT] Resume mode: keeping extracted/dedup/scraped caches; only re-validating")
    else:
        for p in [extracted_path, dedup_path, scraped_path, validated_path]:
            if p.exists():
                p.unlink()
        print("[FACT] Cleared intermediate caches")

    # Step 1: Extract citations (parallel)
    ew = extract_workers if extract_workers is not None else n_workers
    print(f"\n[FACT] Step 1/5: Extracting citations ({ew} workers)...")
    ret = subprocess.run(
        [sys.executable, "-m", "utils.extract",
         "--raw_data_path", str(raw_path),
         "--query_data_path", str(QUERY_DATA),
         "--output_path", str(extracted_path),
         "--n_total_process", str(ew)],
        cwd=str(BENCH_DIR),
    )
    if ret.returncode != 0:
        print("[FACT] Step 1 FAILED")
        return False

    # Step 2: Deduplicate (parallel)
    print(f"\n[FACT] Step 2/5: Deduplicating citations ({n_workers} workers)...")
    ret = subprocess.run(
        [sys.executable, "-m", "utils.deduplicate",
         "--raw_data_path", str(extracted_path),
         "--query_data_path", str(QUERY_DATA),
         "--output_path", str(dedup_path),
         "--n_total_process", str(n_workers)],
        cwd=str(BENCH_DIR),
    )
    if ret.returncode != 0:
        print("[FACT] Step 2 FAILED")
        return False

    # Step 3: Scrape URLs
    sw = scrape_workers if scrape_workers is not None else n_workers
    if veritrace_scrape:
        print(f"\n[FACT] Step 3/5: Scraping URLs (VERITRACE PageSelectTool, {sw} workers)...")
        _veritrace_scrape(dedup_path, scraped_path, sw)
    else:
        print(f"\n[FACT] Step 3/5: Scraping URLs (Jina Reader, {sw} workers)...")
        ret = subprocess.run(
            [sys.executable, "-m", "utils.scrape",
             "--raw_data_path", str(dedup_path),
             "--output_path", str(scraped_path),
             "--n_total_process", str(sw)],
            cwd=str(BENCH_DIR),
        )
        if ret.returncode != 0:
            print("[FACT] Step 3 FAILED")
            return False

    # Step 4: Validate citations (Gemini Flash)
    print("\n[FACT] Step 4/5: Validating citations (Gemini Flash)...")
    ret = subprocess.run(
        [sys.executable, "-m", "utils.validate",
         "--raw_data_path", str(scraped_path),
         "--query_data_path", str(QUERY_DATA),
         "--output_path", str(validated_path),
         "--n_total_process", str(n_workers)],
        cwd=str(BENCH_DIR),
    )
    if ret.returncode != 0:
        print("[FACT] Step 4 FAILED")
        return False

    # Step 5: Compute scores
    result_file = fact_dir / "fact_result.txt"
    print("\n[FACT] Step 5/5: Computing scores...")
    ret = subprocess.run(
        [sys.executable, "-m", "utils.stat",
         "--input_path", str(validated_path),
         "--output_path", str(result_file)],
        cwd=str(BENCH_DIR),
    )
    if ret.returncode != 0:
        print("[FACT] Step 5 FAILED")
        return False

    # Print results
    if result_file.exists():
        print("\n" + "=" * 60)
        print("  FACT RESULTS")
        print("=" * 60)
        print(result_file.read_text(encoding="utf-8"))
        print("=" * 60)
    return True


def run_race(results, name, force=True, rescore=False):
    """Run RACE evaluation pipeline: place data → clean → score."""
    # Place results in raw_data dir
    raw_dir = BENCH_DIR / "data" / "test_data" / "raw_data"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{name}.jsonl"

    with open(raw_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({
                "id": r["id"],
                "prompt": r["prompt"],
                "article": r["article"],
            }, ensure_ascii=False) + "\n")
    print(f"[RACE] Wrote {len(results)} articles to {raw_path}")

    if rescore:
        # Score-only mode: keep cleaned_data cache, skip cleaning
        cleaned_path = BENCH_DIR / "data" / "test_data" / "cleaned_data" / f"{name}.jsonl"
        if not cleaned_path.exists():
            print(f"[RACE] WARNING: No cleaned_data cache found at {cleaned_path}")
            print("[RACE] Will run full pipeline (clean + score) instead")
            rescore = False
        else:
            print(f"[RACE] Rescore mode: reusing cleaned_data cache ({cleaned_path})")

    if not rescore:
        # Full mode: clear cleaned_data cache so articles are re-cleaned
        cleaned_path = BENCH_DIR / "data" / "test_data" / "cleaned_data" / f"{name}.jsonl"
        if cleaned_path.exists():
            cleaned_path.unlink()
            print(f"[RACE] Cleared cleaned_data cache: {cleaned_path}")

    # Run RACE evaluation (output to results/race/<name>/)
    race_output_dir = f"results/race/{name}"
    mode_desc = "score only (reuse cleaned cache)" if rescore else "clean → match criteria → score"
    print(f"\n[RACE] Running RACE evaluation ({mode_desc})...")
    cmd = [sys.executable, str(BENCH_DIR / "deepresearch_bench_race.py"), name,
           "--output_dir", race_output_dir]
    # rescore mode: always force re-scoring; normal mode: respect --no-force
    if rescore or force:
        cmd.append("--force")
    ret = subprocess.run(cmd, cwd=str(BENCH_DIR))
    if ret.returncode != 0:
        print("[RACE] Evaluation FAILED")
        return False

    # Print results
    result_file = BENCH_DIR / "results" / "race" / name / "race_result.txt"
    if result_file.exists():
        print("\n" + "=" * 60)
        print("  RACE RESULTS")
        print("=" * 60)
        print(result_file.read_text(encoding="utf-8"))
        print("=" * 60)
    return True


def main():
    parser = argparse.ArgumentParser(description="VERITRACE Evaluation Runner")
    parser.add_argument("--input", required=True, help="Terminal runner output JSONL path")
    parser.add_argument("--name", required=True, help="Test name (used for result directories)")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fact-only", action="store_true", help="Run FACT evaluation only")
    mode.add_argument("--race-only", action="store_true", help="Run RACE evaluation only")
    parser.add_argument("--n-workers", type=int, default=5, help="Parallel workers for FACT Gemini steps (extract/dedup/validate, default: 5)")
    parser.add_argument("--scrape-workers", type=int, default=None, help="Parallel workers for scrape step (default: same as --n-workers; can be much higher e.g. 40 for Jina)")
    parser.add_argument("--extract-workers", type=int, default=None, help="Parallel workers for FACT extract step (default: same as --n-workers; large requests may need lower e.g. 3-5)")
    parser.add_argument("--resume-fact", action="store_true", help="Reuse extracted/deduplicated/scraped caches; only re-run validate (saves Gemini calls + Jina hits)")
    parser.add_argument("--veritrace-scrape", action="store_true",
                        help="Use VERITRACE PageSelectTool for scraping (more robust for CN sites)")
    parser.add_argument("--no-force", action="store_true",
                        help="Skip --force flag: reuse existing scoring cache (cleaned_data cache is still cleared)")
    parser.add_argument("--rescore", action="store_true",
                        help="Score-only mode: skip cleaning (reuse cleaned_data cache), only re-run scoring")

    args = parser.parse_args()

    # Validate paths
    if not os.path.exists(args.input):
        print(f"Input file not found: {args.input}")
        sys.exit(1)
    if not BENCH_DIR.exists():
        print(f"Benchmark directory not found: {BENCH_DIR}")
        sys.exit(1)
    if not QUERY_DATA.exists():
        print(f"Query data not found: {QUERY_DATA}")
        sys.exit(1)

    # Load results
    results, total = load_successful_results(args.input)
    if not results:
        print(f"No successful results found in {args.input} ({total} total entries)")
        sys.exit(1)
    print(f"Loaded {len(results)} successful results from {args.input} ({total} total entries)")

    # Run evaluations
    do_fact = not args.race_only
    do_race = not args.fact_only

    fact_ok = True
    race_ok = True

    force = not args.no_force

    if do_fact:
        print("\n" + "=" * 60)
        print("  Starting FACT Evaluation")
        print("=" * 60)
        fact_ok = run_fact(results, args.name, n_workers=args.n_workers,
                          veritrace_scrape=args.veritrace_scrape,
                          scrape_workers=args.scrape_workers,
                          resume=args.resume_fact,
                          extract_workers=args.extract_workers)

    if do_race:
        print("\n" + "=" * 60)
        print("  Starting RACE Evaluation")
        print("=" * 60)
        race_ok = run_race(results, args.name, force=force, rescore=args.rescore)

    # Summary
    print("\n" + "=" * 60)
    print("  EVALUATION SUMMARY")
    print("=" * 60)
    if do_fact:
        print(f"  FACT: {'PASS' if fact_ok else 'FAIL'}")
        if fact_ok:
            print(f"    Results: deep_research_bench/results/fact/{args.name}/fact_result.txt")
    if do_race:
        print(f"  RACE: {'PASS' if race_ok else 'FAIL'}")
        if race_ok:
            print(f"    Results: deep_research_bench/results/race/{args.name}/race_result.txt")
    print("=" * 60)


if __name__ == "__main__":
    main()
