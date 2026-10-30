#!/usr/bin/env python3
"""
VERITRACE Parallel Benchmark Runner

Dynamic task queue: workers pull one query at a time from a shared queue.
When a worker finishes a query, it immediately grabs the next — no idle slots.

Supports two workflows:
  - research (default): full VERITRACE pipeline via run_terminal.py
  - rewrite: the same report writer applied to saved evidence via run_rewrite.py

Config file format (YAML):
    workers:
      - dashscope_key: sk-xxx
        silicon_key: sk-yyy          # optional, omit = dashscope only
      - dashscope_key: sk-aaa

Examples:
    # === Complete research workflow (default) ===
    python run_parallel.py --input queries.jsonl --output results.jsonl \\
        --config workers.yaml --lang cn --save-evidence

    # Quick mode with comma-separated keys
    python run_parallel.py --input queries.jsonl --output results.jsonl \\
        --dashscope-keys sk-key1,sk-key2,sk-key3

    # === Rewrite saved evidence ===
    python run_parallel.py --mode rewrite \\
        --evidence-dir benchmark/evidence_results \\
        --output rewrite_results.jsonl \\
        --config workers.yaml --lang cn

    # === Merge only (no running) ===
    python run_parallel.py --merge-only --output benchmark/results.jsonl \\
        --config workers.yaml
"""

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

VERITRACE_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = Path(__file__).resolve().parent


def _verify_key_dead(api_key: str) -> bool:
    """Send a minimal API call to confirm a key is truly exhausted.

    Returns True only if the verification ping also gets a balance/permission error.
    Transient failures (timeout, rate limit) return False to avoid false positives.
    """
    import requests as _requests
    try:
        resp = _requests.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "qwen-turbo", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            print("    [verify] Key ping OK - error was transient, continuing")
            return False
        body = resp.json() if resp.text else {}
        code = body.get("error", {}).get("code", "")
        if code in ("Arrearage", "AccessDenied.Unpurchased") or resp.status_code == 401:
            return True  # Confirmed dead
        # Other errors (rate limit, server error) — don't assume dead
        print(f"    [verify] Key verification inconclusive ({resp.status_code} {code}), not marking as dead")
        return False
    except Exception:
        # Timeout or network error — don't assume dead
        print("    [verify] Key verification ping failed (timeout/network), not marking as dead")
        return False


def load_queries(path: str) -> List[Dict]:
    """Load queries from JSONL."""
    queries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "id" in obj and "prompt" in obj:
                    queries.append(obj)
            except json.JSONDecodeError:
                continue
    return queries


def discover_evidence_ids(evidence_dir: str) -> List[int]:
    """Discover available evidence IDs from evidence_*.json files in a directory."""
    ids = []
    for f in Path(evidence_dir).glob("evidence_*.json"):
        stem = f.stem  # e.g. "evidence_2"
        eid = stem.replace("evidence_", "")
        if eid.isdigit():
            ids.append(int(eid))
    return sorted(ids)


def load_existing_results(path: str) -> Dict[int, Dict]:
    """Load existing results, keyed by id."""
    results = {}
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


def parse_ids_string(ids_str: str) -> List[int]:
    """Parse '1,2,5-10' into [1,2,5,6,7,8,9,10]."""
    result = []
    for part in ids_str.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            result.extend(range(int(lo), int(hi) + 1))
        else:
            result.append(int(part))
    return result


def load_yaml_config(config_path: str) -> tuple:
    """Load worker config from YAML file. Returns (workers, global_backup_keys)."""
    try:
        import yaml
    except ImportError:
        print("PyYAML not installed, trying simple parser...")
        return _parse_simple_yaml(config_path), []

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    workers = data.get("workers", [])
    global_backup_keys = data.get("global_backup_keys", [])
    return workers, global_backup_keys


def _parse_simple_yaml(config_path: str) -> List[Dict]:
    """Minimal YAML parser for our worker config format."""
    workers = []
    current = None
    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("- "):
                if current is not None:
                    workers.append(current)
                current = {}
                kv = stripped[2:].strip()
                if ":" in kv:
                    k, v = kv.split(":", 1)
                    current[k.strip()] = v.strip().strip('"').strip("'")
            elif current is not None and ":" in stripped and not stripped.startswith("#"):
                k, v = stripped.split(":", 1)
                current[k.strip()] = v.strip().strip('"').strip("'")
    if current is not None:
        workers.append(current)
    return workers


def ids_to_string(ids: List[int]) -> str:
    """Convert [1,2,3,5,6,7] to '1-3,5-7' for compact CLI argument."""
    if not ids:
        return ""
    ids = sorted(ids)
    ranges = []
    start = ids[0]
    end = ids[0]
    for i in ids[1:]:
        if i == end + 1:
            end = i
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = end = i
    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ",".join(ranges)


def ids_to_csv(ids: List[int]) -> str:
    """Convert [1,2,3] to '1,2,3' for run_rewrite.py --batch."""
    return ",".join(str(i) for i in sorted(ids))


def merge_results(worker_outputs: List[str], final_output: str):
    """Merge worker output JSONL files into a single file, sorted by id."""
    all_results: Dict[int, Dict] = {}

    if os.path.exists(final_output):
        existing = load_existing_results(final_output)
        all_results.update(existing)

    for path in worker_outputs:
        if not os.path.exists(path):
            continue
        worker_results = load_existing_results(path)
        all_results.update(worker_results)

    with open(final_output, "w", encoding="utf-8") as f:
        for qid in sorted(all_results.keys()):
            f.write(json.dumps(all_results[qid], ensure_ascii=False) + "\n")

    return all_results


def build_env(worker_config: Dict) -> Dict[str, str]:
    """Build env overrides for a worker subprocess."""
    # Load .env first so all model names are present before remapping
    from dotenv import dotenv_values
    dotenv_path = VERITRACE_DIR / ".env"
    dotenv_vars = dotenv_values(dotenv_path) if dotenv_path.exists() else {}

    env = os.environ.copy()
    # Force worker subprocess std streams to UTF-8 so emoji in print()/logging
    # never crash on GBK-default consoles (Windows). Belt to the entry-script
    # reconfigure in run_terminal.py / run_rewrite.py.
    env["PYTHONIOENCODING"] = "utf-8"
    # Fill in any .env vars not already in os.environ (same as load_dotenv override=False)
    for k, v in dotenv_vars.items():
        if k not in env and v is not None:
            env[k] = v

    env["DASHSCOPE_API_KEY"] = worker_config["dashscope_key"]

    if "silicon_key" in worker_config:
        # Worker has SiliconFlow — keep all env as-is
        env["SILICON_API_KEY"] = worker_config["silicon_key"]
    else:
        # DashScope-only: override all providers
        env["LLM_PROVIDER"] = "dashscope"
        env["READER_PROVIDER"] = "dashscope"
        env["PREFILTER_PROVIDER"] = "dashscope"
        env["VERIFIER_PROVIDER"] = "dashscope"
        env["QUALITY_PROVIDER"] = "dashscope"
        env["WRITER_PROVIDER"] = "dashscope"
        if "SUMMARIZER_PROVIDER" in env:
            env["SUMMARIZER_PROVIDER"] = "dashscope"

        # Remap SiliconFlow model names → DashScope equivalents
        SILICON_TO_DASHSCOPE = {
            "Qwen/Qwen3-30B-A3B-Instruct-2507": "qwen3-30b-a3b-instruct-2507",
        }
        for env_key in ["READER_MODEL", "PREFILTER_MODEL", "VERIFIER_MODEL", "QUALITY_MODEL",
                        "SEARCHER_MODEL", "COGNITIVE_MODEL", "WRITER_MODEL", "SUMMARIZER_MODEL", "SILICON_MODEL"]:
            val = env.get(env_key, "")
            if val in SILICON_TO_DASHSCOPE:
                env[env_key] = SILICON_TO_DASHSCOPE[val]

    if worker_config.get("backup_keys"):
        env["DASHSCOPE_BACKUP_API_KEYS"] = worker_config["backup_keys"]
    else:
        # Clear stale backup keys from .env to prevent dead keys leaking in
        env.pop("DASHSCOPE_BACKUP_API_KEYS", None)

    return env


def build_research_cmd(args, worker_ids: List[int], worker_output: str, worker_log: str) -> List[str]:
    """Build the complete research command."""
    cmd = [
        sys.executable, str(BENCHMARK_DIR / "run_terminal.py"),
        "--input", args.input,
        "--output", worker_output,
        "--ids", ids_to_string(worker_ids),
        "--lang", args.lang,
        "--timeout", str(args.timeout),
        "--log-file", worker_log,
    ]
    if args.writer_lang:
        cmd.extend(["--writer-lang", args.writer_lang])
    if args.search_engine:
        cmd.extend(["--search-engine", args.search_engine])
    if args.max_turn:
        cmd.extend(["--max-turn", str(args.max_turn)])
    if args.save_evidence:
        cmd.append("--save-evidence")
    return cmd


def build_rewrite_cmd(args, worker_ids: List[int], worker_output: str, worker_idx=0) -> List[str]:
    """Build the saved-evidence rewrite command."""
    cmd = [
        sys.executable, str(BENCHMARK_DIR / "run_rewrite.py"),
        "--batch", ids_to_csv(worker_ids),
        "--evidence-dir", args.evidence_dir,
        "--output", worker_output,
        "--lang", args.lang,
    ]
    if args.no_refs:
        cmd.append("--no-refs")
    if args.save_evidence_dir:
        # Place per-worker evidence dir next to the worker output jsonl
        # so _consolidate_evidence can find it alongside other run artifacts.
        worker_output_dir = os.path.dirname(os.path.abspath(worker_output))
        worker_evidence_dir = os.path.join(
            worker_output_dir, f"{args.save_evidence_dir}_worker{worker_idx}"
        )
        cmd.extend(["--save-evidence", worker_evidence_dir])
    return cmd


# ---------------------------------------------------------------------------
#  Dynamic task queue: each slot thread pulls one query at a time
# ---------------------------------------------------------------------------

def _slot_worker(
    slot_idx: int,
    worker_config: dict,
    task_queue: queue.Queue,
    args,
    output_dir: str,
    output_stem: str,
    log_dir: Path,
    timestamp: str,
    tracker: dict,
    lock: threading.Lock,
    global_backup_keys: list,
):
    """Thread function: one per worker slot. Pulls tasks from queue until empty."""
    env = build_env(worker_config)
    provider_label = "DashScope+SiliconFlow" if "silicon_key" in worker_config else "DashScope-only"
    key_preview = worker_config["dashscope_key"][:8] + "..."

    task_count = 0
    while True:
        try:
            qid = task_queue.get_nowait()
        except queue.Empty:
            break

        task_count += 1
        task_output = os.path.join(output_dir, f"{output_stem}_slot{slot_idx}_q{qid}.jsonl")
        task_log = str(log_dir / f"parallel_{timestamp}_slot{slot_idx}_q{qid}.log")

        with lock:
            remaining = task_queue.qsize()
            print(f"  Slot {slot_idx}: starting Q{qid} (task #{task_count}) | {provider_label} | key={key_preview} | {remaining} queued")

        if args.mode == "research":
            cmd = build_research_cmd(args, [qid], task_output, task_log)
        else:
            cmd = build_rewrite_cmd(args, [qid], task_output, worker_idx=f"slot{slot_idx}_q{qid}")

        t0 = time.time()
        proc = subprocess.Popen(cmd, env=env)
        proc.wait()
        elapsed = time.time() - t0

        # Check result
        task_results = load_existing_results(task_output) if os.path.exists(task_output) else {}
        success = any(r.get("status") == "success" for r in task_results.values())
        status_str = "OK" if success else "FAIL"

        # Detect key exhaustion (Access denied / 400 / 403) — verify with ping
        key_dead = False
        if not success:
            suspected_dead = False
            for r in task_results.values():
                err = str(r.get("error", ""))
                if "Access denied" in err or "balance insufficient" in err or ("403" in err and "PermissionDenied" in err):
                    suspected_dead = True
                    break
            if suspected_dead:
                key_dead = _verify_key_dead(worker_config["dashscope_key"])

        with lock:
            tracker["outputs"].append(task_output)
            tracker["completed"] += 1
            if success:
                tracker["success"] += 1
            else:
                tracker["errors"] += 1
            done = tracker["completed"]
            total = tracker["total"]
            print(f"  Slot {slot_idx}: Q{qid} {status_str} | {elapsed:.0f}s | progress: {done}/{total}")

            # Try to swap in a global backup key
            if key_dead:
                dead_key = worker_config["dashscope_key"]
                print(f"  💀 Slot {slot_idx}: key {dead_key[:8]}... exhausted (after Q{qid}, task #{task_count})")
                new_key = None
                if global_backup_keys:
                    new_key = global_backup_keys.pop(0)
                if new_key:
                    print(f"  🔄 Slot {slot_idx}: swapping to backup {new_key[:8]}...")
                    worker_config["dashscope_key"] = new_key
                    env = build_env(worker_config)
                    key_preview = new_key[:8] + "..."
                    key_dead = False  # Continue with new key
                else:
                    print(f"  ⚠️ Slot {slot_idx}: no backup keys left. Stopping slot.")

        task_queue.task_done()

        if key_dead:
            break  # No backup available, stop this slot

    with lock:
        print(f"  Slot {slot_idx}: finished ({task_count} tasks processed)")


def _consolidate_evidence(tracker: dict, output_dir: str, output_stem: str,
                          save_evidence_dir: str = None):
    """Move per-task evidence files into a single consolidated directory.

    Handles both workflows:
    - research (--save-evidence): scans `evidence_{output_stem}_slot*_q*`
    - rewrite (--save-evidence-dir <name>): scans `{name}_worker*`

    Falls back to scanning tracker's known per-task outputs in case interrupt
    or pattern mismatch left dirs that the stem-based glob missed.
    """
    dir_name = save_evidence_dir if save_evidence_dir else output_stem
    final_evidence_dir = os.path.join(output_dir, f"evidence_{dir_name}")
    os.makedirs(final_evidence_dir, exist_ok=True)

    candidate_dirs = set()
    # From tracker (in case interrupted before pattern scan)
    for task_output in tracker.get("outputs", []):
        task_stem = os.path.splitext(os.path.basename(task_output))[0]
        candidate_dirs.add(os.path.join(output_dir, f"evidence_{task_stem}"))

    # Scan output_dir for both naming patterns
    for d in os.listdir(output_dir):
        dp = os.path.join(output_dir, d)
        if not os.path.isdir(dp):
            continue
        if d.startswith(f"evidence_{output_stem}_slot"):
            candidate_dirs.add(dp)
        if save_evidence_dir and d.startswith(f"{save_evidence_dir}_worker"):
            candidate_dirs.add(dp)

    moved = 0
    for task_evidence_dir in sorted(candidate_dirs):
        if not os.path.isdir(task_evidence_dir):
            continue
        for ef in Path(task_evidence_dir).glob("evidence_*.json"):
            dest = os.path.join(final_evidence_dir, ef.name)
            shutil.move(str(ef), dest)
            moved += 1
        try:
            os.rmdir(task_evidence_dir)
        except OSError:
            pass

    if moved:
        print(f"  Evidence: {moved} files consolidated into {final_evidence_dir}")


def main():
    parser = argparse.ArgumentParser(description="VERITRACE Parallel Benchmark Runner")
    parser.add_argument("--mode", default="research", choices=["research", "rewrite"],
                        type=lambda value: {"search": "research", "writer-only": "rewrite"}.get(value, value),
                        help="Workflow: complete research or report rewrite from saved evidence")
    parser.add_argument("--output", required=True, help="Final merged output JSONL path")

    # Worker config
    key_group = parser.add_mutually_exclusive_group(required=True)
    key_group.add_argument("--config", help="YAML config file with worker definitions")
    key_group.add_argument("--dashscope-keys",
                           help="Comma-separated DashScope API keys (quick mode, all DashScope-only)")

    # Complete research inputs
    parser.add_argument("--input", help="Input JSONL file (required for research)")
    parser.add_argument("--search-engine", default=None)
    parser.add_argument("--max-turn", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=6000, help="Per-query timeout in seconds (default: 6000 = 100min)")
    parser.add_argument("--save-evidence", action="store_true", default=False)

    # Saved-evidence rewrite inputs
    parser.add_argument("--evidence-dir", default=None,
                        help="Directory containing evidence_*.json (required for rewrite)")
    parser.add_argument("--no-refs", action="store_true", help="Disable search reference links in writer output")
    parser.add_argument("--save-evidence-dir", default=None,
                        help="Save updated evidence files to this directory (rewrite)")

    # Shared
    parser.add_argument("--lang", default="en", choices=["en", "cn"])
    parser.add_argument("--writer-lang", default=None, choices=["en", "cn"], help="Override writer language (default: same as --lang). Allows e.g. cn search + en writing.")
    parser.add_argument("--ids", default=None, help="Only run specific IDs (e.g. '1-5,8,10')")
    args = parser.parse_args()

    # Validate mode-specific args
    if args.mode == "research" and not args.input:
        parser.error("--input is required for research")
    if args.mode == "rewrite" and not args.evidence_dir:
        parser.error("--evidence-dir is required for rewrite")

    # Discover all available IDs
    if args.mode == "research":
        queries = load_queries(args.input)
        if not queries:
            print(f"No valid queries in {args.input}")
            sys.exit(1)
        all_ids = [q["id"] for q in queries]
    else:
        all_ids = discover_evidence_ids(args.evidence_dir)
        if not all_ids:
            print(f"No evidence_*.json files found in {args.evidence_dir}")
            sys.exit(1)

    # Filter by --ids if specified
    if args.ids:
        selected = set(parse_ids_string(args.ids))
        all_ids = [i for i in all_ids if i in selected]

    # Skip already-successful IDs (resume)
    if os.path.exists(args.output):
        existing = load_existing_results(args.output)
        done_ids = {qid for qid, r in existing.items() if r.get("status") == "success"}
        pending_ids = [qid for qid in all_ids if qid not in done_ids]
        if done_ids:
            print(f"Resuming: {len(done_ids)} already done, {len(pending_ids)} pending")
    else:
        pending_ids = all_ids

    if not pending_ids:
        print("All queries already completed!")
        sys.exit(0)

    # Build worker configs
    if args.dashscope_keys:
        keys = [k.strip() for k in args.dashscope_keys.split(",") if k.strip()]
        workers = [{"dashscope_key": k} for k in keys]
        global_backup_keys = []
    else:
        workers, global_backup_keys = load_yaml_config(args.config)

    if not workers:
        print("No workers configured!")
        sys.exit(1)

    # Prepare dirs
    output_dir = os.path.dirname(os.path.abspath(args.output))
    output_stem = os.path.splitext(os.path.basename(args.output))[0]
    os.makedirs(output_dir, exist_ok=True)
    log_dir = BENCHMARK_DIR / "logs"
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Fill task queue
    task_queue = queue.Queue()
    for qid in pending_ids:
        task_queue.put(qid)

    tracker = {
        "outputs": [],
        "completed": 0,
        "success": 0,
        "errors": 0,
        "total": len(pending_ids),
    }
    lock = threading.Lock()

    mode_label = "Research" if args.mode == "research" else "Rewrite"
    print(f"\n{'=' * 60}")
    print(f"  Dynamic Task Queue [{mode_label}]")
    print(f"  {len(pending_ids)} queries | {len(workers)} worker slots | {len(global_backup_keys)} global backup keys")
    print(f"{'=' * 60}")

    # Launch one thread per worker slot
    t0 = time.time()
    slot_threads = []
    for i, w in enumerate(workers):
        provider_label = "DashScope+SiliconFlow" if "silicon_key" in w else "DashScope-only"
        key_preview = w["dashscope_key"][:8] + "..."
        print(f"  Slot {i}: {provider_label} | key={key_preview}")

        t = threading.Thread(
            target=_slot_worker,
            args=(i, w, task_queue, args, output_dir, output_stem, log_dir, timestamp, tracker, lock, global_backup_keys),
            daemon=True,
        )
        slot_threads.append(t)
        t.start()

    print("\n  All slots launched. Workers pull queries dynamically.\n")

    # Wait for all slot threads (with timeout to prevent hang on Ctrl+C)
    try:
        while any(t.is_alive() for t in slot_threads):
            for t in slot_threads:
                t.join(timeout=5)
    except KeyboardInterrupt:
        print("\n  ⚠️ KeyboardInterrupt — stopping. Merging completed results...")

    # Always consolidate evidence and merge results, even after interrupt
    if args.save_evidence or args.save_evidence_dir:
        _consolidate_evidence(
            tracker, output_dir, output_stem,
            save_evidence_dir=args.save_evidence_dir,
        )

    # Merge results
    print("\nMerging results...")
    all_results = merge_results(tracker["outputs"], args.output)

    total_success = sum(1 for r in all_results.values() if r.get("status") == "success")
    total_error = sum(1 for r in all_results.values() if r.get("status") != "success")
    total_duration = time.time() - t0

    # Clean up per-task output files
    for task_output in tracker["outputs"]:
        try:
            os.remove(task_output)
        except OSError:
            pass

    print(f"\n{'=' * 60}")
    print(f"  PARALLEL BENCHMARK COMPLETE [{mode_label}]")
    print(f"{'=' * 60}")
    print(f"  Slots: {len(workers)} | Queries: {len(pending_ids)}")
    print(f"  Success: {total_success} | Error: {total_error} | Total: {len(all_results)}")
    print(f"  Wall time: {total_duration:.0f}s")
    print(f"  Output: {args.output}")
    if args.mode == "rewrite":
        print("\n  To evaluate:")
        print(f"    python benchmark/run_eval.py --input {args.output} --name rewrite_test --race-only")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
