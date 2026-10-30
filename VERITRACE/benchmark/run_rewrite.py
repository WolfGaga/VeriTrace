#!/usr/bin/env python3
"""
Re-run the three-stage report writer from saved evidence.

Uses the shared WritingPipeline model selection:
  OUTLINE_MODEL env → writer_llm
  SECTION_PLANNER_MODEL env → OUTLINE_MODEL env → writer_llm
  SECTION_WRITER_MODEL env → writer_llm
  writer_llm = WRITER_MODEL env → SUMMARIZER_MODEL env → SILICON_MODEL env → "Qwen/Qwen3-32B"

Usage:
    python benchmark/run_rewrite.py --evidence benchmark/evidence_2.json
    python benchmark/run_rewrite.py --evidence benchmark/evidence_2.json --output benchmark/rewrite_q2.jsonl
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Force UTF-8 on stdout/stderr so emoji in print()/logging never crash on a
# GBK-default console (Windows). errors="replace" guarantees no encode crash.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env BEFORE importing config (config reads env vars at import time)
from dotenv import load_dotenv
load_dotenv()

from veritrace.agent.evidence_store import EvidenceStore
from veritrace.agent.writing import WritingPipeline
from veritrace.agent.core.llm_adapter import create_adapter
from veritrace.agent.usage_tracker import (
    reset_tracker,
    finish_session,
    get_usage_summary,
)


def _merge_usage(src: dict, delta: dict, duration: float) -> dict:
    """Sum two UsageStats.to_dict() snapshots into one (tokens, calls, models, timing)."""
    src = src or {}
    delta = delta or {}

    s_tokens = src.get("tokens", {}) or {}
    d_tokens = delta.get("tokens", {}) or {}
    tokens = {
        "input": int(s_tokens.get("input", 0)) + int(d_tokens.get("input", 0)),
        "output": int(s_tokens.get("output", 0)) + int(d_tokens.get("output", 0)),
        "total": int(s_tokens.get("total", 0)) + int(d_tokens.get("total", 0)),
    }

    s_search = src.get("search", {}) or {}
    search = {
        "api_calls": int(s_search.get("api_calls", 0)),
        "results_count": int(s_search.get("results_count", 0)),
        "queries": list(s_search.get("queries", []) or []),
    }

    s_llm = src.get("llm", {}) or {}
    d_llm = delta.get("llm", {}) or {}
    models_union = list({*(s_llm.get("models", []) or []), *(d_llm.get("models", []) or [])})

    # Merge per_model: sum same-keyed entries
    per_model: dict = {}
    for src_pm in (s_llm.get("per_model", {}) or {}, d_llm.get("per_model", {}) or {}):
        for model, vals in src_pm.items():
            if model not in per_model:
                per_model[model] = {"input": 0, "output": 0, "total": 0, "calls": 0}
            per_model[model]["input"] += int(vals.get("input", 0))
            per_model[model]["output"] += int(vals.get("output", 0))
            per_model[model]["total"] += int(vals.get("total", 0))
            per_model[model]["calls"] += int(vals.get("calls", 0))

    llm = {
        "calls": int(s_llm.get("calls", 0)) + int(d_llm.get("calls", 0)),
        "models": models_union,
        "per_model": per_model,
    }

    s_timing = src.get("timing", {}) or {}
    src_duration = float(s_timing.get("duration_seconds", 0) or 0)
    timing = {
        "duration_seconds": round(src_duration + float(duration or 0), 2),
    }

    summary = (
        f"Token Usage: {tokens['total']:,} total ({tokens['input']:,} in / {tokens['output']:,} out)"
        f" | Search Calls: {search['api_calls']} ({search['results_count']} results)"
        f" | LLM Calls: {llm['calls']}"
        f" | Duration: {timing['duration_seconds']:.1f}s"
    )

    return {
        "tokens": tokens,
        "search": search,
        "llm": llm,
        "timing": timing,
        "summary": summary,
    }


def _writer_provider() -> str:
    """Get the LLM provider for writing pipeline: WRITER_PROVIDER → LLM_PROVIDER → 'dashscope'."""
    return os.getenv("WRITER_PROVIDER") or os.getenv("LLM_PROVIDER", "dashscope")


def load_evidence(path: str) -> dict:
    """Load saved evidence JSON."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_writing_pipeline(
    evidence_data: dict,
    language: str = "cn",
    include_search_refs: bool = True,
    track_usage: bool = True,
) -> dict:
    """Run 3-layer writing pipeline from saved evidence.

    Returns dict with: article, outline, section_texts, duration_seconds
    """
    # Reconstruct objects
    evidence_store = EvidenceStore.from_evidence_table(evidence_data["evidence_table"])
    cognitive_graph = evidence_data["cognitive_graph"]
    ref2url = evidence_data["ref2url"]
    original_question = evidence_data["question"]

    # Build cited_references from ref2url — must be {int: {url, title}} dicts
    # In normal pipeline, cited_references only contains pages actually cited by Searcher
    # (i.e., pages with evidence), NOT all 1000+ pages in ref2url.
    # Use evidence_table keys as the filter: only include refs that have evidence.
    evidence_ref_indices = set(int(k) for k in evidence_data["evidence_table"].keys())
    cited_references = {}
    for idx_str, info in ref2url.items():
        idx = int(idx_str)
        if idx not in evidence_ref_indices:
            continue
        if isinstance(info, dict):
            cited_references[idx] = info
        else:
            cited_references[idx] = {"url": str(info), "title": ""}

    logging.info(f"📦 Loaded: {len(evidence_store.records)} evidence sources, "
                 f"{len(cognitive_graph.get('nodes', {}))} cognitive nodes, "
                 f"{len(cited_references)} references")

    # Each query owns its usage context.
    if track_usage:
        reset_tracker()

    start = time.time()

    # Build the base writer using the same fallback as the online pipeline.
    # Explicit writer settings take precedence over the main research model.
    provider = _writer_provider()
    logging.info(f"🔧 Writer provider: {provider}")
    writer_model = os.getenv("WRITER_MODEL") or os.getenv("SUMMARIZER_MODEL") or None
    base_model = os.getenv("SILICON_MODEL", "Qwen/Qwen3-32B")
    if writer_model:
        writer_llm = create_adapter(provider, writer_model)
        logging.info(f"🔧 writer_llm: {writer_model} (from writer model configuration)")
    else:
        writer_llm = create_adapter(provider, base_model)
        logging.info(f"🔧 writer_llm: {base_model} (from SILICON_MODEL)")

    pipeline = WritingPipeline(writer_llm, provider, language, include_search_refs)
    article = pipeline.run(original_question, cognitive_graph, evidence_store, cited_references)
    duration = time.time() - start

    # Capture rewrite usage delta when tracking is reliable.
    if track_usage:
        finish_session()
        rewrite_usage = get_usage_summary()
    else:
        rewrite_usage = None

    logging.info(f"✅ Writing complete: {len(article)} chars in {duration:.1f}s")

    return {
        "article": article,
        "outline": pipeline.data["outline"],
        "overall_theme": pipeline.data["overall_theme"],
        "section_texts": pipeline.data["section_texts"],
        "duration_seconds": duration,
        "rewrite_usage": rewrite_usage,
    }


def _load_query_map(query_path: Path) -> dict:
    """Load query.jsonl into {prompt_prefix: {id, prompt}} lookup."""
    qmap = {}
    if query_path.exists():
        with open(query_path, "r", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line.strip())
                qmap[obj["prompt"][:50]] = obj
    return qmap


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run writing pipeline from saved evidence")
    parser.add_argument("--evidence", type=str, default=None, help="Path to single evidence_*.json")
    parser.add_argument("--batch", type=str, default=None, help="Comma-separated ids, e.g. 2,4,6,11")
    parser.add_argument("--evidence-dir", type=str, default="benchmark", help="Directory containing evidence_*.json (default: benchmark)")
    parser.add_argument("--output", type=str, default=None, help="Output JSONL path")
    parser.add_argument("--lang", type=str, default="cn", choices=["en", "cn"], help="Language (default: cn)")
    parser.add_argument("--no-refs", action="store_true", help="Disable search reference links in output")
    parser.add_argument("--save-evidence", type=str, default=None, help="Save updated evidence files (with new outline, section_texts, overall_theme, article) to this directory")
    parser.add_argument("--parallel", type=int, default=1, help="Process N queries concurrently in one process (shares single API token). Default: 1 (sequential).")
    args = parser.parse_args(argv)

    if not args.evidence and not args.batch:
        parser.error("Either --evidence or --batch is required")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    query_path = Path(__file__).resolve().parent.parent.parent / "deep_research_bench" / "data" / "prompt_data" / "query.jsonl"
    qmap = _load_query_map(query_path)

    # Build list of (evidence_path, qid) to process
    if args.batch:
        ids = [int(x.strip()) for x in args.batch.split(",")]
        evidence_dir = Path(args.evidence_dir)
        tasks = []
        for qid in ids:
            epath = evidence_dir / f"evidence_{qid}.json"
            if not epath.exists():
                logging.warning(f"⚠️ evidence_{qid}.json not found, skipping")
                continue
            tasks.append((str(epath), qid))
    else:
        stem = Path(args.evidence).stem
        eid = stem.replace("evidence_", "") if stem.startswith("evidence_") else stem
        tasks = [(args.evidence, int(eid) if eid.isdigit() else 0)]

    # Determine output path
    if args.output:
        out_path = args.output
    elif args.batch:
        out_path = "benchmark/rewrite_batch.jsonl"
    else:
        out_path = f"benchmark/rewrite_{tasks[0][1]}.jsonl"

    total_start = time.time()
    success = 0
    failed = 0

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    write_lock = threading.Lock()

    track_usage = True

    def _process_one(idx, epath, qid):
        logging.info(f"[{idx+1}/{len(tasks)}] Processing evidence_{qid}.json")
        try:
            evidence_data = load_evidence(epath)
            question = evidence_data.get("question", "")

            result = run_writing_pipeline(
                evidence_data,
                language=args.lang,
                include_search_refs=not args.no_refs,
                track_usage=track_usage,
            )

            query_id = qid
            query_prompt = question
            for prefix, qobj in qmap.items():
                if question[:50] == prefix:
                    query_id = qobj["id"]
                    query_prompt = qobj["prompt"]
                    break

            cited_refs_list = []
            ref2url_src = evidence_data.get("ref2url", {}) or {}
            for _k, _v in ref2url_src.items():
                if isinstance(_v, dict):
                    cited_refs_list.append({"url": _v.get("url", ""), "title": _v.get("title", "")})
                else:
                    cited_refs_list.append({"url": str(_v), "title": ""})

            if track_usage:
                merged_usage = _merge_usage(
                    evidence_data.get("usage_stats") or {},
                    result.get("rewrite_usage") or {},
                    result["duration_seconds"],
                )
                top_duration = merged_usage["timing"]["duration_seconds"]
            else:
                merged_usage = None
                top_duration = result["duration_seconds"]

            output_record = {
                "id": query_id,
                "prompt": query_prompt,
                "article": result["article"],
                "cited_references": cited_refs_list,
                "status": "success",
                "error": None,
                "duration_seconds": top_duration,
                "usage_stats": merged_usage,
            }
            with write_lock:
                fout.write(json.dumps(output_record, ensure_ascii=False) + "\n")
                fout.flush()

            if args.save_evidence:
                save_dir = Path(args.save_evidence)
                save_dir.mkdir(parents=True, exist_ok=True)
                evidence_data["article"] = result["article"]
                evidence_data["outline"] = result["outline"]
                evidence_data["overall_theme"] = result["overall_theme"]
                evidence_data["section_texts"] = result["section_texts"]
                evidence_data["usage_stats"] = merged_usage
                save_path = save_dir / f"evidence_{query_id}.json"
                with open(save_path, "w", encoding="utf-8") as ef:
                    json.dump(evidence_data, ef, ensure_ascii=False, indent=2)
                logging.info(f"📁 Saved evidence: {save_path}")

            logging.info(f"✅ q{query_id}: {len(result['article'])} chars, {result['duration_seconds']:.1f}s")
            return True
        except Exception as exc:
            logging.error(f"❌ q{qid} failed: {exc}")
            return False

    with open(out_path, "w", encoding="utf-8") as fout:
        if args.parallel > 1:
            logging.info(f"Running with {args.parallel} parallel threads")
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                futures = [pool.submit(_process_one, i, epath, qid) for i, (epath, qid) in enumerate(tasks)]
                for fut in as_completed(futures):
                    if fut.result():
                        success += 1
                    else:
                        failed += 1
        else:
            for i, (epath, qid) in enumerate(tasks):
                if _process_one(i, epath, qid):
                    success += 1
                else:
                    failed += 1

    total_duration = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"Writing complete! {success} success / {failed} failed")
    print(f"  Total duration: {total_duration:.1f}s")
    print(f"  Output: {out_path}")
    print("\nTo evaluate:")
    print(f"  python benchmark/run_eval.py --input {out_path} --name rewrite_test --race-only")
    print(f"{'='*60}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
