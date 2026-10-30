"""Run the real HTTP and storage layer with deterministic research events."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import threading

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from veritrace.app import create_app
from veritrace.logging_config import build_log_config
from veritrace.agent.runtime.progress import report_progress


PROGRESS_GATE = None


GRAPH = {
    "original_question": "Browser integration question",
    "nodes": {
        "e1": {"id": "e1", "name": "Alpha entity", "cognitive_state": "KNOWN", "hop_distance": 0,
               "findings": {"Overview": "Verified alpha finding"},
               "item_findings": {"Alpha": {"Attribute": "Alpha detail"}},
               "unexpected_discoveries": ["Unexpected alpha finding"]},
        "e2": {"id": "e2", "name": "Beta entity", "cognitive_state": "PARTIAL", "hop_distance": 1,
               "findings": {"Overview": "Verified beta finding"}},
    },
    "edges": {"r1": {"id": "r1", "source_node_id": "e1", "target_node_id": "e2",
                      "relation_type": "Supports", "state": "solved", "attempt_count": 2,
                      "findings": "Verified relation finding"}},
}
REFERENCES = {"0": {"url": "https://source.test/alpha", "title": "Alpha source", "snippet": "Alpha excerpt"},
              "1": {"url": "https://source.test/beta", "title": "Beta source", "snippet": "Beta excerpt"}}


def event(kind, **data):
    return {"schema_version": 2, "type": kind, "data": deepcopy(data)}


class ControlledAgent:
    def __init__(self, **options):
        self.options = options
        self.stopped = threading.Event()
        self.progress_gate = threading.Event()
        self.restored = None

    def restore_checkpoint(self, checkpoint, action):
        self.restored = (checkpoint, action)

    def cancel(self):
        self.stopped.set()
        self.progress_gate.set()

    def __call__(self, question, session_id=0):
        global PROGRESS_GATE
        yield event("research.started", question=question, cognitive_graph=GRAPH)
        if question == "Completed recovery fixture":
            task = {"id": "TaskAlpha", "question": "Saved finding", "status": "completed",
                    "result": {"answer": "Saved evidence", "references": REFERENCES, "evidence_records": [{"ref_idx": 0}]}}
            yield event("research.checkpoint", checkpoint={"schema_version": 1, "question": question,
                                                          "tasks": {"TaskAlpha": task}})
            yield event("research.completed", article="# Saved backend report\nThe completed report was retained.",
                        tasks={"TaskAlpha": task}, ref2url=REFERENCES, cognitive_graph=GRAPH)
            return
        if question.startswith("Recovery fixture"):
            task = {"id": "TaskAlpha", "question": "Verify retained evidence", "status": "completed",
                    "cognitive_target_id": "e1", "result": {"answer": "Retained useful evidence [[0]]", "references": REFERENCES,
                    "evidence_records": [{"ref_idx": 0}]}}
            yield event("tasks.updated", tasks=[task])
            checkpoint = {"schema_version": 1, "question": question, "tasks": {"TaskAlpha": task},
                          "messages": [{"role": "system", "content": "Private planner checkpoint"}]}
            if self.restored:
                yield event("research.restored", tasks={"TaskAlpha": task}, cognitive_graph=GRAPH)
                if self.restored[1] == "write_report":
                    checkpoint["writing"] = {"section_texts": {"1": "## Retained draft\nUseful partial text [[0]]."}}
                    yield event("research.checkpoint", checkpoint=checkpoint)
                    yield event("research.partial", partial_article="## Retained draft\nUseful partial text [[0]].", ref2url=REFERENCES)
                yield event("research.completed", article="# Recovered final report\nEvidence preserved [[0]].", tasks={"TaskAlpha": task}, ref2url=REFERENCES)
            else:
                yield event("research.checkpoint", checkpoint=checkpoint)
                yield event("research.partial", partial_article="## Retained draft\nUseful partial text [[0]].", ref2url=REFERENCES)
                if question.endswith("active"):
                    self.stopped.wait(30)
                    return
                yield event("research.failed", error="Controlled provider interruption")
            return
        if question.startswith("Failure"):
            raise RuntimeError("Controlled research failure")
        task = {"id": "TaskAlpha", "question": "Verify alpha evidence", "status": "running",
                "cognitive_target_id": "e1", "result": None}
        yield event("tasks.updated", tasks=[task])
        if question.startswith("Progress fixture"):
            PROGRESS_GATE = self.progress_gate
            phases = [
                ("initializing", {}), ("planning", {}), ("searching", {}), ("filtering", {}),
                ("reading", {}), ("updating_graph", {}), ("planning_outline", {}),
                ("planning_sections", {"section_index": 1, "section_total": 2, "title": "Findings"}),
                ("writing_section", {"section_index": 1, "section_total": 2, "title": "Findings"}),
                ("writing_section", {"section_index": 2, "section_total": 2, "title": "Analysis"}),
                ("writing_summary", {}), ("finalizing", {}),
            ]
            for phase, detail in phases:
                self.progress_gate.clear()
                if phase == "updating_graph":
                    task.update(status="completed", result={"answer": "Alpha finding", "references": REFERENCES})
                    yield event("task.completed", task=task)
                report_progress(phase, **detail)
                if not self.progress_gate.wait(45) or self.stopped.is_set():
                    return
        elif question == "Task failure fixture":
            PROGRESS_GATE = self.progress_gate
            self.progress_gate.clear()
            task.update(status="failed", result={"answer": "Search unavailable", "error": "Controlled task failure"})
            yield event("task.failed", task=task)
            report_progress("planning")
            if not self.progress_gate.wait(45) or self.stopped.is_set():
                return
            yield event("research.failed", error="Controlled research failure after a failed task")
            return
        if question.startswith("Slow"):
            self.stopped.wait(30)
            if self.stopped.is_set():
                return
        task.update(status="completed", result={"answer": "Alpha task answer [[0]]", "references": REFERENCES,
                    "messages": [{"role": "assistant", "content": "Alpha search trace"}],
                    "feedback": {"reason": "Alpha search feedback"}})
        yield event("task.completed", task=task)
        article = (f"# Integration report\n\nVerified report for {question}. This deterministic report contains enough text for evaluation export. [[0]] [[1]]\n\n"
                   f"Language: {self.options['lang']}.\n")
        if question == "Markup safety":
            article += '\n<table><tr><td>Safe HTML table</td></tr></table>\n\n'
            article += '![Local test image](/api/_test/image.svg)\n\n[Details link](#details)\n<h2 id="details">Details heading</h2>\n'
            article += '\n[Back to top](#top)\n\n'
            article += '<iframe srcdoc="<script>parent.__untrusted_report_code = true</script>"></iframe>\n'
            article += '\nUnsafe citation [[2]].\n'
            references = {**REFERENCES, "2": {"url": "javascript:opener.__untrusted_report_code=true", "title": "Unsafe source"}}
            task["result"]["references"] = references
        else:
            references = REFERENCES
        yield event("research.completed", cognitive_graph=GRAPH, tasks=[task], ref2url=references, article=article)


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--frontend-dist", type=Path)
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    queries = args.state_dir / "queries.jsonl"
    queries.write_text(json.dumps({"id": 1, "prompt": "Browser integration question"}) + "\n", encoding="utf-8")
    app = create_app(database_path=args.state_dir / "conversations.db", export_dir=args.state_dir / "exports",
                     query_file=queries, agent_factory=ControlledAgent)
    from fastapi.responses import Response

    @app.post("/api/_test/advance-progress")
    def advance_progress():
        if PROGRESS_GATE is not None:
            PROGRESS_GATE.set()
        return {"success": True}

    @app.get("/api/_test/image.svg")
    def image_fixture():
        return Response('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="16"><rect width="32" height="16" fill="blue"/></svg>', media_type="image/svg+xml")
    if args.frontend_dist:
        from fastapi.staticfiles import StaticFiles

        if not (args.frontend_dist / "index.html").is_file():
            parser.error("Build the React frontend before serving its production bundle")
        app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/"]
        app.mount("/", StaticFiles(directory=args.frontend_dist, html=True), name="frontend")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info",
                log_config=build_log_config(args.state_dir / "backend.log"))
