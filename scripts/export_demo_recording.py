"""Export selected public-facing events from a completed research recording."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re


def references(values):
    result = {}
    for key, value in (values or {}).items():
        value = {"url": value} if isinstance(value, str) else value
        url = value.get("url", "")
        if url.startswith(("https://", "http://")):
            result[str(key)] = {"url": url, "title": value.get("title", "Source")}
    return result


def task(value):
    clean = {
        key: value[key]
        for key in (
            "id",
            "question",
            "status",
            "cognitive_target_id",
            "cognitive_edge_id",
            "task_type",
        )
        if key in value
    }
    payload = value.get("result")
    clean["result"] = (
        {
            "answer": payload.get("answer", ""),
            "references": references(payload.get("references")),
        }
        if payload is not None
        else None
    )
    return clean


def graph(value):
    clean = deepcopy(value or {})
    for node in clean.get("nodes", {}).values():
        node.pop("search_history", None)
        quality = node.get("quality_profile") or {}
        quality.pop("ref_quality", None)
    for edge in clean.get("edges", {}).values():
        edge.pop("search_history", None)
    return clean


def export(events_path, result_path, destination, title, question_english):
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "success"
    events, previous_progress, previous_saved = [], None, None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        kind, data = event["type"], event.get("data", {})
        clean = None
        if kind == "research.started":
            clean = {
                "question": data["question"],
                "cognitive_graph": graph(data.get("cognitive_graph")),
            }
        elif kind == "research.recovery":
            saved = data.get("saved_sections", 0)
            if saved != previous_saved:
                clean = {
                    "saved_sections": saved,
                    "checkpoint_saved": data.get("checkpoint_saved", True),
                }
                previous_saved = saved
        elif kind == "research.progress":
            signature = (data.get("stage"), data.get("section_index"))
            if signature != previous_progress:
                clean = {
                    key: data[key]
                    for key in (
                        "stage",
                        "title",
                        "section_index",
                        "section_total",
                        "task_id",
                        "turn",
                    )
                    if key in data
                }
            previous_progress = signature
        elif kind == "tasks.updated":
            clean = {
                "tasks": {
                    key: task(value) for key, value in data.get("tasks", {}).items()
                }
            }
        elif kind == "task.completed":
            clean = {"task": task(data["task"])}
        elif kind == "cognitive.updated":
            clean = {
                "cognitive_graph": graph(data.get("cognitive_graph")),
                "content": data.get("content", ""),
            }
        elif kind == "research.partial":
            clean = {
                "partial_article": data.get("partial_article", ""),
                "ref2url": references(data.get("ref2url")),
            }
        elif kind == "research.completed":
            clean = {
                "article": data["article"],
                "ref2url": references(data.get("ref2url")),
                "tasks": {
                    key: task(value) for key, value in data.get("tasks", {}).items()
                },
                "cognitive_graph": graph(data.get("cognitive_graph")),
            }
        elif kind == "usage.updated":
            value = data.get("usage", {})
            clean = {
                "usage": {
                    "tokens": value.get("tokens", {}),
                    "llm": {
                        key: value.get("llm", {}).get(key)
                        for key in ("calls", "models", "per_model")
                    },
                    "search": {
                        key: value.get("search", {}).get(key)
                        for key in ("api_calls", "results_count")
                    },
                    "timing": {"duration_seconds": result["duration_seconds"]},
                }
            }
        if clean is not None:
            events.append(
                {
                    "schema_version": 2,
                    "type": kind,
                    "task_id": event.get("task_id"),
                    "data": clean,
                }
            )
    completed = next(
        event["data"] for event in events if event["type"] == "research.completed"
    )
    initial = next(
        event["data"] for event in events if event["type"] == "research.started"
    )
    metadata = {
        "title": title,
        "query_id": result["id"],
        "question": result["prompt"],
        "recorded": "October 2026",
        "language": "cn",
        "question_english": question_english,
        "original_duration_seconds": round(result["duration_seconds"]),
        "completed_tasks": len(completed["tasks"]),
        "initial_graph_nodes": len(initial["cognitive_graph"].get("nodes", {})),
        "final_graph_nodes": len(completed["cognitive_graph"].get("nodes", {})),
        "description": "Selected user-facing events from a completed real-network run, in recorded order. Idle periods are removed. Internal model messages and execution code are excluded.",
    }
    payload = json.dumps(
        {"metadata": metadata, "events": events},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert not re.search(
        r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b|\bAIza[A-Za-z0-9_-]{20,}\b", payload
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload + "\n", encoding="utf-8")
    print(
        f"Exported {len(events)} selected events ({destination.stat().st_size:,} bytes)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--title", default="Recorded research")
    parser.add_argument("--question-english", default="")
    options = parser.parse_args()
    export(
        options.events,
        options.result,
        options.output,
        options.title,
        options.question_english,
    )
