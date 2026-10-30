#!/usr/bin/env python3
"""
VERITRACE Terminal Benchmark Runner

Run VERITRACE from the terminal (no FastAPI server required).
Supports single query, batch JSONL input, incremental save, and auto-resume.

Examples:
    # Single query
    python run_terminal.py --query "What is quantum computing?" --output results.jsonl

    # Batch from JSONL (each line: {"id": 1, "prompt": "..."})
    python run_terminal.py --input queries.jsonl --output results.jsonl

    # Run specific IDs only
    python run_terminal.py --input queries.jsonl --output results.jsonl --ids 1,2,3

    # Re-run: automatically skips successful, retries failed/missing
    python run_terminal.py --input queries.jsonl --output results.jsonl
"""

import argparse
import json
import logging
import os
import sys
import threading
from copy import deepcopy
from contextvars import copy_context
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Force UTF-8 on stdout/stderr so emoji in print()/logging never crash on a
# GBK-default console (Windows). errors="replace" guarantees no encode crash.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Ensure VERITRACE package is importable (benchmark/ is one level below VERITRACE/)
VERITRACE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VERITRACE_DIR))

from dotenv import load_dotenv
load_dotenv(VERITRACE_DIR / ".env")

from veritrace.agent import init_agent
from veritrace.agent.report_extractor import extract_report, extract_pipeline_data
from veritrace.agent.evidence_store import EvidenceStore
from veritrace.agent.usage_tracker import get_usage_summary, reset_tracker, finish_session


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

def load_queries(path: str) -> List[Dict]:
    """Load queries from a JSONL file. Each line: {"id": N, "prompt": "..."}"""
    queries = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                logging.warning(f"Skipping malformed line {lineno}: {e}")
                continue
            if "id" not in obj or "prompt" not in obj:
                logging.warning(f"Skipping line {lineno}: missing 'id' or 'prompt' field")
                continue
            queries.append(obj)
    return queries


def load_existing_results(path: str) -> Dict[int, Dict]:
    """Load existing results JSONL, keyed by id."""
    results: Dict[int, Dict] = {}
    if not os.path.exists(path):
        return results
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                results[obj["id"]] = obj
            except (json.JSONDecodeError, KeyError):
                continue
    return results


def rewrite_results_file(path: str, results: Dict[int, Dict]):
    """Rewrite the entire results file from the dict (used when overwriting error entries)."""
    with open(path, "w", encoding="utf-8") as f:
        for _id in sorted(results.keys()):
            f.write(json.dumps(results[_id], ensure_ascii=False) + "\n")


def append_result(path: str, record: Dict):
    """Append a single result record to the JSONL file."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _save_evidence_file(output_path: str, qid: int, question: str, result: Dict):
    """Save pipeline intermediate data as evidence_<id>.json alongside the output JSONL."""
    pipeline_data = result.get("pipeline_data")
    if not pipeline_data:
        return

    output_dir = os.path.dirname(os.path.abspath(output_path))
    output_stem = os.path.splitext(os.path.basename(output_path))[0]
    evidence_dir = os.path.join(output_dir, f"evidence_{output_stem}")
    os.makedirs(evidence_dir, exist_ok=True)
    evidence_path = os.path.join(evidence_dir, f"evidence_{qid}.json")

    # Convert ref2url keys to strings for JSON serialization
    ref2url_raw = result.get("ref2url", {})
    ref2url = {str(k): v for k, v in ref2url_raw.items()} if ref2url_raw else {}

    evidence_data = {
        "question": question,
        "cognitive_graph": pipeline_data.get("cognitive_graph", {}),
        "evidence_table": pipeline_data.get("evidence_store", {}),
        "ref2url": ref2url,
        "outline": pipeline_data.get("outline", []),
        "overall_theme": pipeline_data.get("overall_theme", ""),
        "user_requirements": pipeline_data.get("user_requirements", []),
        "section_texts": pipeline_data.get("section_texts", {}),
        "article": result.get("article", ""),
        "usage_stats": result.get("usage_stats"),
    }

    temporary_path = evidence_path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(evidence_data, f, ensure_ascii=False, indent=2)
    os.replace(temporary_path, evidence_path)
    logging.info(f"Evidence data saved to {evidence_path} ({os.path.getsize(evidence_path) / 1024:.0f} KB)")


# ---------------------------------------------------------------------------
# Single-query runner (with timeout)
# ---------------------------------------------------------------------------

def run_single_query(
    query: str,
    lang: str = "en",
    search_engine: str = "google",
    max_turn: int = 20,
    timeout: int = 6000,
    save_evidence: bool = False,
    writer_lang: Optional[str] = None,
    output_path: Optional[str] = None,
    qid: Optional[object] = None,
) -> Dict:
    """Run a single VERITRACE query. Returns result dict with article/status/error."""
    # init_agent reads max_turn from env PLANNER_MAX_TURN
    os.environ["PLANNER_MAX_TURN"] = str(max_turn)

    # Read model config from env (same as app.py)
    llm_model = os.getenv("SILICON_MODEL", "Qwen/Qwen3-32B")
    searcher_model = os.getenv("SEARCHER_MODEL") or None
    reader_model = os.getenv("READER_MODEL") or None
    prefilter_model = os.getenv("PREFILTER_MODEL") or None
    quality_model = os.getenv("QUALITY_MODEL") or None
    cognitive_model = os.getenv("COGNITIVE_MODEL") or None
    writer_model = os.getenv("WRITER_MODEL") or None
    summarizer_model = os.getenv("SUMMARIZER_MODEL") or None

    agent = init_agent(
        lang=lang,
        writer_lang=writer_lang,
        llm_model=llm_model,
        searcher_model=searcher_model,
        reader_model=reader_model,
        prefilter_model=prefilter_model,
        quality_model=quality_model,
        cognitive_model=cognitive_model,
        writer_model=writer_model,
        summarizer_model=summarizer_model,
        search_engine=search_engine,
        include_search_refs=True,
    )

    # If save_evidence is on, tell the agent where to drop partial evidence at search-finish.
    # Path math MUST mirror _save_evidence_file() so the post-success save overwrites this file.
    if save_evidence and output_path is not None and qid is not None:
        output_dir = os.path.dirname(os.path.abspath(output_path))
        output_stem = os.path.splitext(os.path.basename(output_path))[0]
        evidence_dir = os.path.join(output_dir, f"evidence_{output_stem}")
        agent.partial_evidence_path = os.path.join(evidence_dir, f"evidence_{qid}.json")

    # Reset usage tracker for this query
    reset_tracker()

    result_holder: Dict = {}
    error_holder: List = []

    def partial_artifacts():
        if not save_evidence:
            return {}
        pipeline_data = extract_pipeline_data(agent)
        if not pipeline_data:
            return {}
        snapshot = dict(pipeline_data)
        # The writer may finish another section while this thread handles its deadline.
        snapshot["section_texts"] = dict(snapshot.get("section_texts", {}))
        snapshot = deepcopy(snapshot)
        references = result_holder.get("ref2url") or snapshot.get("cited_references", {})
        return {"pipeline_data": snapshot, "ref2url": deepcopy(references)}

    def _worker():
        try:
            final_event = None
            for event in agent(query):
                if event.type == "research.completed":
                    final_event = event
                elif event.type == "research.failed":
                    error_holder.append(str(event.data.get("error", "Research failed")))
                elif event.type == "research.cancelled":
                    error_holder.append("Research cancelled")
            if final_event is None:
                if not error_holder:
                    error_holder.append("Agent returned no completed research result")
                return
            report = extract_report(final_event)
            article = report["article"]
            if not article or len(article.strip()) < 50:
                error_holder.append("Report is empty or too short")
                return
            result_holder["article"] = article
            result_holder["cited_references"] = report["cited_references"]
            result_holder["ref2url"] = report.get("ref2url", {})

            # Capture pipeline intermediate data for evidence collection
            if save_evidence:
                pipeline_data = extract_pipeline_data(agent)
                if pipeline_data:
                    result_holder["pipeline_data"] = pipeline_data
        except Exception as e:
            logging.exception("Research worker failed (query_id=%s)", qid)
            error_holder.append(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

    thread = threading.Thread(target=copy_context().run, args=(_worker,), daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        agent.cancel()
        logging.warning("Research cancelled after %ss timeout", timeout)
        finish_session()
        return {"status": "error", "error": f"Timeout after {timeout}s", "article": "",
                "usage_stats": get_usage_summary(), **partial_artifacts()}

    # Capture usage stats
    finish_session()
    usage_stats = get_usage_summary()

    if error_holder:
        return {"status": "error", "error": error_holder[0], "article": "",
                "usage_stats": usage_stats, **partial_artifacts()}

    ret = {
        "status": "success",
        "error": None,
        "article": result_holder.get("article", ""),
        "cited_references": result_holder.get("cited_references", []),
        "usage_stats": usage_stats,
    }
    if save_evidence:
        ret["ref2url"] = result_holder.get("ref2url", {})
        ret["pipeline_data"] = result_holder.get("pipeline_data")
    return ret


# ---------------------------------------------------------------------------
# Rewrite from evidence (writing pipeline only, no search)
# ---------------------------------------------------------------------------

def rewrite_from_evidence(evidence_path: str, lang: str = "en") -> Dict:
    """Load evidence JSON from a previous run and re-run only the writing pipeline.

    This allows testing different writing models on the same collected evidence.
    Returns result dict with article/status/error.
    """
    from veritrace.agent.core import create_adapter
    from veritrace.agent.writing import WritingPipeline

    with open(evidence_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    question = data["question"]
    cognitive_graph = data.get("cognitive_graph", {})
    evidence_table = data.get("evidence_table", {})
    ref2url = data.get("ref2url", {})

    if not evidence_table:
        return {"status": "error", "error": "No evidence_table in file", "article": ""}
    if not cognitive_graph:
        return {"status": "error", "error": "No cognitive_graph in file (collected before this feature was added?)", "article": ""}

    # Reconstruct EvidenceStore
    evidence_store = EvidenceStore.from_evidence_table(evidence_table)
    logging.info(f"Loaded {len(evidence_store)} evidence records from {len(evidence_table)} sources")

    # Build cited_references from ref2url
    cited_references = {}
    for k, v in ref2url.items():
        idx = int(k)
        if isinstance(v, dict):
            cited_references[idx] = {"url": v.get("url", ""), "title": v.get("title", "")}
        else:
            cited_references[idx] = {"url": str(v), "title": ""}

    # Resolve models (same logic as veritrace_agent._run_section_writer_pipeline)
    writer_model = os.getenv("WRITER_MODEL") or os.getenv("SUMMARIZER_MODEL") or os.getenv("SILICON_MODEL", "Qwen/Qwen3-32B")
    provider = os.getenv("WRITER_PROVIDER") or os.getenv("LLM_PROVIDER") or "siliconflow"
    writer_llm = create_adapter(provider, writer_model)

    pipeline = WritingPipeline(writer_llm, provider, lang, include_search_refs=True)
    report = pipeline.run(question, cognitive_graph, evidence_store, cited_references)

    return {"status": "success", "error": None, "article": report}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run(argv=None):
    parser = argparse.ArgumentParser(description="VERITRACE Terminal Benchmark Runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", type=str, help="Single query string")
    group.add_argument("--input", type=str, help="Input JSONL file (each line: {\"id\": N, \"prompt\": \"...\"})")
    group.add_argument("--rewrite", type=str, help="Re-run writing pipeline from evidence JSON (no search, writing only)")

    _default_output = str(VERITRACE_DIR / "eval_data" / "terminal" / "results.jsonl")
    parser.add_argument("--output", type=str, default=_default_output, help="Output JSONL path (default: eval_data/terminal/results.jsonl)")
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated IDs to run (default: all)")
    parser.add_argument("--lang", type=str, default="en", choices=["en", "cn"], help="Language (default: en)")
    parser.add_argument("--writer-lang", type=str, default=None, choices=["en", "cn"], help="Override writer language (default: same as --lang). Allows e.g. cn search + en writing.")
    parser.add_argument("--search-engine", type=str, default=None, help="Search engine (default: from env SEARCH_ENGINE or 'google')")
    parser.add_argument("--max-turn", type=int, default=None, help="Max planner turns (default: from env PLANNER_MAX_TURN or 20)")
    parser.add_argument("--timeout", type=int, default=6000, help="Per-query timeout in seconds (default: 6000 = 100min)")
    parser.add_argument("--log-file", type=str, default=None, help="Log file path (default: run_terminal_<timestamp>.log)")
    parser.add_argument("--save-evidence", action="store_true", default=False,
                        help="Save pipeline intermediate data (evidence_store + outline + section_texts) as evidence_<id>.json")
    args = parser.parse_args(argv)

    # Defaults from env
    search_engine = args.search_engine or os.environ.get("SEARCH_ENGINE", "google")
    max_turn = args.max_turn or int(os.environ.get("PLANNER_MAX_TURN", "20"))

    # Logging setup
    _default_log = str(VERITRACE_DIR / "benchmark" / "logs" / f"run_terminal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    log_file = args.log_file or _default_log
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )
    logging.info(f"Log file: {log_file}")
    logging.info(f"Config: lang={args.lang}, search_engine={search_engine}, max_turn={max_turn}, timeout={args.timeout}s")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    # --- Single query mode ---
    if args.query:
        logging.info("[1/1] Running single query...")
        t0 = time.time()
        try:
            result = run_single_query(args.query, args.lang, search_engine, max_turn, args.timeout,
                                      save_evidence=args.save_evidence, writer_lang=args.writer_lang,
                                      output_path=args.output, qid=0)
        except Exception as e:
            result = {"status": "error", "error": f"Runner error: {e}\n{traceback.format_exc()}", "article": ""}
        duration = time.time() - t0
        record = {
            "id": 0,
            "prompt": args.query,
            "article": result.get("article", ""),
            "cited_references": result.get("cited_references", []),
            "status": result["status"],
            "error": result.get("error"),
            "duration_seconds": round(duration, 1),
            "usage_stats": result.get("usage_stats"),
        }
        append_result(args.output, record)
        logging.info(f"[1/1] Done in {duration:.0f}s — status={result['status']}")
        if result.get("usage_stats"):
            logging.info(f"  Usage: {result['usage_stats'].get('summary', '')}")
        if result["status"] == "error":
            logging.error("Error: %s", result.get("error", ""))
        logging.info(f"Output saved to {args.output}")

        # Save evidence data if requested
        if args.save_evidence and result.get("pipeline_data"):
            _save_evidence_file(args.output, 0, args.query, result)
        return 0 if result["status"] == "success" else 1

    # --- Rewrite mode ---
    if args.rewrite:
        evidence_path = args.rewrite
        if not os.path.exists(evidence_path):
            logging.error(f"Evidence file not found: {evidence_path}")
            return 1
        logging.info(f"Rewrite mode: loading evidence from {evidence_path}")
        t0 = time.time()
        try:
            result = rewrite_from_evidence(evidence_path, args.lang)
        except Exception as e:
            result = {"status": "error", "error": f"Rewrite error: {e}\n{traceback.format_exc()}", "article": ""}
        duration = time.time() - t0

        record = {
            "id": 0,
            "prompt": f"[rewrite] {evidence_path}",
            "article": result.get("article", ""),
            "status": result["status"],
            "error": result.get("error"),
            "duration_seconds": round(duration, 1),
        }
        append_result(args.output, record)
        if result["status"] == "success":
            logging.info(f"Rewrite completed in {duration:.0f}s ({len(result['article'])} chars)")
            logging.info(f"Output saved to {args.output}")
        else:
            logging.error(f"Rewrite failed: {result.get('error', '')}")
        return 0 if result["status"] == "success" else 1

    # --- Batch mode ---
    queries = load_queries(args.input)
    if not queries:
        logging.error(f"No valid queries found in {args.input}")
        return 1

    # Filter by IDs if specified
    if args.ids:
        selected_ids = set()
        for part in args.ids.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                try:
                    selected_ids.update(range(int(lo), int(hi) + 1))
                except ValueError:
                    selected_ids.add(part)  # string ID with hyphen
            else:
                try:
                    selected_ids.add(int(part))
                except ValueError:
                    selected_ids.add(part)  # string ID like "L001"
        queries = [q for q in queries if q["id"] in selected_ids]
        logging.info(f"Filtered to {len(queries)} queries by --ids")

    total = len(queries)
    logging.info(f"Loaded {total} queries from {args.input}")

    # Load existing results for resume logic
    existing = load_existing_results(args.output)
    skipped = 0
    success = 0
    failed = 0

    for i, q in enumerate(queries, 1):
        qid = q["id"]
        prompt = q["prompt"]

        # Resume logic: skip successful, retry errors
        if qid in existing and existing[qid].get("status") == "success":
            skipped += 1
            logging.info(f"[{i}/{total}] Skipping id={qid} (already succeeded)")
            continue

        logging.info(f"[{i}/{total}] Running query id={qid}: {prompt[:80]}...")
        t0 = time.time()

        try:
            result = run_single_query(prompt, args.lang, search_engine, max_turn, args.timeout,
                                      save_evidence=args.save_evidence, writer_lang=args.writer_lang,
                                      output_path=args.output, qid=qid)
        except Exception as e:
            result = {"status": "error", "error": f"Runner error: {e}\n{traceback.format_exc()}", "article": ""}

        duration = time.time() - t0
        record = {
            "id": qid,
            "prompt": prompt,
            "article": result.get("article", ""),
            "cited_references": result.get("cited_references", []),
            "status": result["status"],
            "error": result.get("error"),
            "duration_seconds": round(duration, 1),
            "usage_stats": result.get("usage_stats"),
        }

        # Update results dict and persist
        existing[qid] = record
        rewrite_results_file(args.output, existing)

        if result["status"] == "success":
            success += 1
            usage_summary = result.get("usage_stats", {}).get("summary", "")
            logging.info(f"[{i}/{total}] id={qid} SUCCESS in {duration:.0f}s | {usage_summary}")
        else:
            failed += 1
            logging.error("[%s/%s] id=%s FAILED in %.0fs — %s", i, total, qid, duration, result.get("error", ""))

        if args.save_evidence and result.get("pipeline_data"):
            _save_evidence_file(args.output, qid, prompt, result)

    # Summary
    logging.info("=" * 60)
    logging.info(f"  BATCH COMPLETE: {success} success / {failed} failed / {skipped} skipped")
    logging.info(f"  Output: {args.output}")
    logging.info(f"  Log: {log_file}")
    logging.info("=" * 60)
    return 1 if failed else 0


def main(argv=None):
    try:
        return _run(argv)
    except KeyboardInterrupt:
        logging.warning("Interrupted; completed results remain saved.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
