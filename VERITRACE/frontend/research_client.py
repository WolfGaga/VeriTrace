"""Shared native-event client for the Python frontends."""

from dataclasses import dataclass, field
import html
import json
import os
from typing import Iterator

import requests

PHASE_LABELS = {
    "initializing": "Preparing research", "planning": "Planning research", "searching": "Searching sources",
    "filtering": "Selecting sources", "reading": "Reading sources", "updating_graph": "Updating cognitive graph",
    "planning_outline": "Planning report outline", "planning_sections": "Planning section evidence",
    "writing_section": "Writing report", "writing_summary": "Writing executive summary",
    "finalizing": "Finalizing report and references",
}


def normalize_references(references: dict | None) -> dict:
    normalized = {}
    for ref_id, source in (references or {}).items():
        if isinstance(source, str):
            normalized[ref_id] = {"url": source}
        elif isinstance(source, dict):
            normalized[ref_id] = {**source, "url": source.get("url") or ""}
    return normalized


def _normalize_task(task: dict) -> dict:
    result = task.get("result")
    return {**task, "result": {**result, "references": normalize_references(result.get("references"))} if result else result}


@dataclass
class ResearchView:
    question: str = ""
    status: str = "idle"
    article: str = ""
    progress: str = ""
    tasks: dict = field(default_factory=dict)
    references: dict = field(default_factory=dict)
    cognitive_graph: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    error: str = ""
    activity: dict = field(default_factory=dict)
    waiting_seconds: float = 0

    @property
    def status_text(self):
        if self.status != "running":
            return {"completed": "Completed", "failed": "Failed", "cancelled": "Stopped"}.get(self.status, "Ready")
        label = PHASE_LABELS.get(self.activity.get("stage"), "Researching")
        if self.activity.get("section_index"):
            label += f" — section {self.activity['section_index']}/{self.activity.get('section_total', '?')}"
        if self.waiting_seconds:
            label += f" — connected, waiting {self.waiting_seconds:.0f}s"
        return label

    def apply(self, event: dict) -> None:
        if event.get("schema_version") != 2:
            raise ValueError("Unsupported research event version")
        data = event.get("data", {})
        kind = event["type"]
        if self.status in ("completed", "failed", "cancelled") and kind in ("research.progress", "research.heartbeat"):
            return
        tasks = data.get("tasks", {})
        for task in tasks.values() if isinstance(tasks, dict) else tasks:
            self.tasks[task["id"]] = _normalize_task(task)
        if data.get("task"):
            self.tasks[data["task"]["id"]] = _normalize_task(data["task"])
        if data.get("cognitive_graph"):
            self.cognitive_graph = data["cognitive_graph"]
        if "ref2url" in data:
            self.references = normalize_references(data["ref2url"])
        if kind == "research.started":
            self.question = data.get("question", self.question)
            self.status = "running"
        elif kind == "research.progress":
            self.activity = {**data, "task_id": event.get("task_id")}
            self.waiting_seconds = 0
        elif kind == "research.heartbeat":
            self.waiting_seconds = data.get("waiting_seconds", 0)
        elif kind.startswith("planner.") or kind == "research.updated":
            self.progress = data.get("content", self.progress)
        elif kind == "research.completed":
            self.article = data.get("article", "")
            self.status = "completed"
        elif kind == "research.failed":
            self.error = str(data.get("error", "Research failed"))
            self.status = "failed"
        elif kind == "research.cancelled":
            self.status = "cancelled"
        elif kind == "usage.updated":
            self.usage = data.get("usage", {})
        if self.status in ("completed", "failed", "cancelled"):
            self.tasks = {key: {**task, "status": "cancelled"} if task.get("status") in ("pending", "running") else task
                          for key, task in self.tasks.items()}


def parse_sse(lines) -> Iterator[dict]:
    data = []
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if not line:
            if data:
                yield json.loads("\n".join(data))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield json.loads("\n".join(data))


def research_events(question: str, *, language="en", backend_url=None):
    url = backend_url or os.getenv("VERITRACE_BACKEND_URL", "http://127.0.0.1:8002/solve")
    with requests.post(url, json={"inputs": question, "lang": language},
                       stream=True, timeout=(10, 1800)) as response:
        response.raise_for_status()
        for event in parse_sse(response.iter_lines()):
            if event.get("schema_version") != 2:
                raise ValueError("Unsupported research event version")
            yield event


def task_details(task: dict | None) -> str:
    if not task:
        return "Select a search task to view its findings."
    result = task.get("result") or {}
    lines = [f"### {task.get('question', task['id'])}", f"Status: {task.get('status', 'pending')}"]
    if task.get("task_objective"):
        lines.append(task["task_objective"])
    if result.get("answer"):
        lines.append(result["answer"])
    if result.get("error"):
        lines.append(result["error"])
    if result.get("messages"):
        lines.append("### Search trace\n\n```json\n" + json.dumps(result["messages"], ensure_ascii=False, indent=2) + "\n```")
    if result.get("feedback"):
        lines.append("### Search feedback\n\n```json\n" + json.dumps(result["feedback"], ensure_ascii=False, indent=2) + "\n```")
    references = normalize_references(result.get("references"))
    if references:
        lines.append("### Sources")
        for ref_id, source in references.items():
            lines.append(f"- [{ref_id}] [{source.get('title') or source.get('url', '')}]({source.get('url', '')})")
    return "\n\n".join(lines)


def task_graph(view: ResearchView) -> str:
    """Render task progress without adding synthetic nodes to task state."""
    tasks = list(view.tasks.values())
    height = max(100, 72 + len(tasks) * 62)
    rows = [f'<svg viewBox="0 0 620 {height}" role="img" aria-label="Research task progress" style="width:100%">',
            '<rect x="10" y="10" width="600" height="38" rx="8" fill="#eef2ff"/>',
            '<text x="20" y="35" font-size="14" fill="#1e293b">' + html.escape(view.question[:75]) + '</text>',
            '<text x="20" y="62" font-size="12" fill="#475569">' + html.escape(view.status_text[:90]) + '</text>']
    for index, task in enumerate(tasks):
        y = 70 + index * 62
        question = str(task.get("question", task["id"]))
        status = task.get("status", "pending")
        color = "#059669" if status == "completed" else "#dc2626" if status == "failed" else "#d97706" if status in ("pending", "running") else "#64748b"
        rows.extend([
            f'<path d="M30 66 V{y + 20} H60" stroke="#94a3b8" fill="none"/>',
            f'<rect x="60" y="{y}" width="550" height="44" rx="6" fill="#f8fafc" stroke="{color}"/>',
            f'<text x="70" y="{y + 18}" font-size="12" fill="#1e293b"><title>{html.escape(question)}</title>{html.escape(question[:80])}</text>',
            f'<text x="70" y="{y + 35}" font-size="10" fill="{color}">{html.escape(status)}</text>',
        ])
    return ''.join(rows) + '</svg>'
