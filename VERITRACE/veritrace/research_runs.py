"""Durable research checkpoints and attempt history, separate from browser saves."""

import json
import logging
from copy import deepcopy
import threading
import time
from uuid import uuid4

from .conversation_db import ConversationStore


class ResearchRunStore:
    def __init__(self, path):
        self.database = ConversationStore(path)
        self._initialized = False
        self._lock = threading.Lock()

    def initialize(self):
        with self._lock:
            if self._initialized:
                return
            with self.database._connection() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS research_runs (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL, language TEXT NOT NULL,
                    status TEXT NOT NULL, lease TEXT NOT NULL, attempt INTEGER NOT NULL,
                    checkpoint TEXT NOT NULL DEFAULT '{}', view TEXT NOT NULL DEFAULT '{}',
                    error TEXT, updated_at REAL NOT NULL
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS research_attempts (
                    run_id TEXT NOT NULL, attempt INTEGER NOT NULL, checkpoint TEXT NOT NULL,
                    view TEXT NOT NULL, status TEXT NOT NULL, error TEXT, updated_at REAL NOT NULL,
                    PRIMARY KEY (run_id, attempt)
                )""")
            self._initialized = True

    def create(self, question, language):
        self.initialize()
        run_id, lease = uuid4().hex, uuid4().hex
        with self.database._connection() as conn:
            conn.execute("INSERT INTO research_runs (id,question,language,status,lease,attempt,updated_at) VALUES (?,?,?,?,?,?,?)",
                         (run_id, question, language, "running", lease, 1, time.time()))
        return self.get(run_id)

    def get(self, run_id):
        self.initialize()
        with self.database._connection() as conn:
            row = conn.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["checkpoint"] = json.loads(result["checkpoint"])
        result["view"] = json.loads(result["view"])
        return result

    def claim(self, run_id):
        self.initialize()
        with self.database._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            conn.execute("""INSERT OR REPLACE INTO research_attempts
                (run_id,attempt,checkpoint,view,status,error,updated_at) VALUES (?,?,?,?,?,?,?)""",
                (run_id, row["attempt"], row["checkpoint"], row["view"], row["status"], row["error"], row["updated_at"]))
            conn.execute("UPDATE research_runs SET status='running', lease=?, attempt=attempt+1, error=NULL, updated_at=? WHERE id=?",
                         (uuid4().hex, time.time(), run_id))
        return self.get(run_id)

    def save(self, run_id, lease, *, checkpoint=None, view=None, status=None, error=None):
        self.initialize()
        updates, values = ["updated_at=?"], [time.time()]
        for column, value in (("checkpoint", checkpoint), ("view", view), ("status", status), ("error", error)):
            if value is not None:
                updates.append(f"{column}=?")
                values.append(json.dumps(value, ensure_ascii=False) if column in ("checkpoint", "view") else value)
        with self.database._connection() as conn:
            changed = conn.execute(f"UPDATE research_runs SET {', '.join(updates)} WHERE id=? AND lease=?",
                                   (*values, run_id, lease)).rowcount
        return bool(changed)


def recovery_info(run):
    checkpoint = run.get("checkpoint") or {}
    tasks = checkpoint.get("tasks") or {}
    sources = sum(len((task.get("result") or {}).get("evidence_records") or [])
                  for task in tasks.values() if task.get("status") == "completed")
    writing = checkpoint.get("writing") or {}
    return {
        "run_id": run["id"], "attempt": run["attempt"], "status": run["status"],
        "can_continue": bool(checkpoint),
        "can_write_report": bool(sources or writing.get("evidence_store")),
        "saved_sections": len(writing.get("section_texts") or {}), "updated_at": run["updated_at"],
    }


class RunSession:
    """Persist safe points while exposing only user-facing results to clients."""

    def __init__(self, store, run):
        self.store, self.run = store, run
        self.view = dict(run.get("view") or {})
        self.saved = True

    def record_usage(self, usage):
        attempts = self.view.setdefault("attempt_usage", {})
        if not attempts and self.run["attempt"] > 1 and self.view.get("usage"):
            attempts[str(self.run["attempt"] - 1)] = self.view["usage"]
        attempts[str(self.run["attempt"])] = deepcopy(usage)
        self.view["usage"] = combine_usage(list(attempts.values()))
        return self.view["usage"]

    def info(self):
        return {**recovery_info(self.run), "checkpoint_saved": self.saved}

    def save(self, checkpoint=None, status=None, error=None):
        if checkpoint:
            self.run["checkpoint"] = checkpoint
        if status:
            self.run["status"] = status
        if status == "completed":
            self.run["error"] = None
            error = ""
        if error:
            self.run["error"] = error
        self.run["updated_at"] = time.time()
        try:
            self.saved = self.store.save(self.run["id"], self.run["lease"], checkpoint=checkpoint,
                                         view=self.view, status=status, error=error)
        except Exception:
            self.saved = False
            logging.exception("[Research:%s] Checkpoint save failed", self.run["id"])

    def observe(self, payload):
        kind, data = payload["type"], payload.get("data", {})
        if kind == "research.checkpoint":
            checkpoint = data["checkpoint"]
            self.view["tasks"] = checkpoint.get("tasks", {})
            self.view["cognitive_graph"] = checkpoint.get("cognitive_graph", {})
            self.save(checkpoint=checkpoint)
            return {"schema_version": 2, "type": "research.recovery", "data": self.info()}
        if kind == "research.progress":
            self.view["activity"] = {**data, "task_id": payload.get("task_id")}
            self.view["waiting_seconds"] = 0
            return payload
        if kind == "research.heartbeat":
            self.view["waiting_seconds"] = data.get("waiting_seconds", 0)
            return payload
        for field in ("tasks", "cognitive_graph", "ref2url", "article", "partial_article", "section_texts", "usage"):
            if field in data:
                value = data[field]
                self.view[field] = {item["id"]: item for item in value} if field == "tasks" and isinstance(value, list) else value
        if data.get("task"):
            self.view.setdefault("tasks", {})[data["task"]["id"]] = data["task"]
        terminal = kind.removeprefix("research.") if kind in (
            "research.completed", "research.failed", "research.cancelled") else None
        self.save(status=terminal, error=data.get("error") if kind == "research.failed" else None)
        return payload


def combine_usage(attempts):
    if len(attempts) == 1:
        return deepcopy(attempts[0])
    result = {"tokens": {}, "search": {}, "llm": {"per_model": {}}, "timing": {}}
    for group, fields in (("tokens", ("input", "output", "total")),
                          ("search", ("api_calls", "results_count")), ("llm", ("calls",)),
                          ("timing", ("duration_seconds",))):
        for field in fields:
            result[group][field] = sum(item.get(group, {}).get(field, 0) for item in attempts)
    result["search"]["queries"] = [query for item in attempts for query in item.get("search", {}).get("queries", [])][-10:]
    result["llm"]["models"] = sorted({model for item in attempts for model in item.get("llm", {}).get("models", [])})
    for item in attempts:
        for model, counts in item.get("llm", {}).get("per_model", {}).items():
            combined = result["llm"]["per_model"].setdefault(model, {})
            for field, count in counts.items():
                combined[field] = combined.get(field, 0) + count
    result["summary"] = f"Usage across {len(attempts)} research attempts"
    return result
