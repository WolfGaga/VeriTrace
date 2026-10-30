"""Research streaming, conversation history and evaluation export API."""

import asyncio
import json
import logging
import os
import random
import sqlite3
import time
from contextlib import suppress
from pathlib import Path
from typing import Dict, List, Optional, Union

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from .conversation_db import ConversationStore, DEFAULT_DATABASE
from .logging_config import build_log_config
from .streaming import stream_events, stop_aware_events
from .research_runs import ResearchRunStore, RunSession, recovery_info


PROJECT_DIR = Path(__file__).resolve().parent.parent


def parse_arguments(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="VeriTrace API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8002, type=int)
    parser.add_argument("--lang", default="cn", choices=("cn", "en"))
    parser.add_argument("--llm-provider", default=os.getenv("LLM_PROVIDER", "siliconflow"))
    parser.add_argument("--llm-model", default=os.getenv("SILICON_MODEL", "Qwen/Qwen3-Next-80B-A3B-Thinking"))
    for role in ("searcher", "reader", "prefilter", "quality", "cognitive"):
        parser.add_argument(f"--{role}-model", default=os.getenv(f"{role.upper()}_MODEL") or None)
    parser.add_argument("--writer-model", default=None)
    parser.add_argument("--summarizer-model", default=None)
    parser.add_argument("--search-engine", default=os.getenv("SEARCH_ENGINE", "google"))
    parser.add_argument("--asy", default=False, action="store_true", help="Use asynchronous agent execution")
    parser.add_argument("--log-file", default=os.getenv("VERITRACE_LOG_FILE", "logs/backend.log"))
    parser.add_argument("--log-level", choices=("debug", "info", "warning", "error"),
                        default=os.getenv("VERITRACE_LOG_LEVEL", "info").lower())
    parser.add_argument("--progress-log-interval", type=float, default=30.0,
                        help="Seconds between request progress logs; 0 disables periodic logging")
    settings = parser.parse_args(argv)
    if settings.writer_model is None and settings.summarizer_model is None:
        settings.writer_model = os.getenv("WRITER_MODEL") or None
        settings.summarizer_model = os.getenv("SUMMARIZER_MODEL") or None
    return settings


class GenerationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: Union[str, List[Dict]]
    session_id: int = Field(default_factory=lambda: random.randint(0, 999999))
    agent_cfg: Dict = Field(default_factory=dict)
    lang: Optional[str] = None
    writer_mode: Optional[str] = Field(default=None, pattern="^(research)?$")
    resume_run_id: Optional[str] = None
    recovery_action: Optional[str] = Field(default=None, pattern="^(continue|write_report)$")


class ConversationCreate(BaseModel):
    id: str
    title: str = "New Conversation"
    data: Dict = Field(default_factory=dict)


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    data: Optional[Dict] = None
    skip_timestamp_update: bool = False


class ExportEvalRequest(BaseModel):
    question: str
    article: str
    query_id: Optional[int] = None


def _sse(event):
    payload = event if isinstance(event, dict) else event.to_dict()
    return {"event": "response", "data": json.dumps(payload, ensure_ascii=False)}


def _event(kind, **data):
    return {"schema_version": 2, "type": kind, "task_id": None, "data": data}


def _error(message, status=404, **extra):
    return JSONResponse({"error": message, **extra}, status_code=status)


async def _log_request_progress(session_id, progress, interval):
    while True:
        await asyncio.sleep(interval)
        now = time.monotonic()
        logging.info(
            "[Research:%s] Waiting; last_event=%s idle=%.1fs elapsed=%.1fs events=%s",
            session_id, progress["last_event"], now - progress["last_update"],
            now - progress["started"], progress["events"],
        )


def create_app(settings=None, *, database_path=None, export_dir=None, query_file=None, agent_factory=None):
    load_dotenv()
    settings = settings or parse_arguments([])
    store = ConversationStore(database_path or DEFAULT_DATABASE)
    runs = ResearchRunStore(database_path or DEFAULT_DATABASE)
    active_runs = {}
    export_dir = Path(export_dir or PROJECT_DIR / "eval_data" / "frontend")
    query_file = Path(query_file or PROJECT_DIR.parent / "deep_research_bench" / "data" / "prompt_data" / "query.jsonl")
    app = FastAPI(title="VeriTrace", docs_url="/")
    app.state.conversation_store = store
    app.state.settings = settings
    app.state.research_runs = runs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Research-Run-ID", "X-Research-Attempt"],
    )

    @app.post("/solve")
    async def solve(body: GenerationParams, request: Request):
        question = body.inputs if isinstance(body.inputs, str) else json.dumps(body.inputs, ensure_ascii=False)
        language = body.lang or settings.lang
        if body.resume_run_id:
            previous = runs.get(body.resume_run_id)
            if previous is None:
                return _error("Research run not found", 404)
            if body.resume_run_id in active_runs:
                return _error("Previous attempt is still stopping. Retry shortly.", 409)
            if not previous.get("checkpoint"):
                return _error("No saved checkpoint. Download available findings and start a new research run.", 400)
            if previous["question"] != question or previous["language"] != language:
                return _error("Recovery must use the original question and language", 400)
            if body.recovery_action == "write_report" and not recovery_info(previous)["can_write_report"]:
                return _error("No collected evidence is available for report writing yet", 400)
            run = runs.claim(body.resume_run_id)
        else:
            if body.recovery_action:
                return _error("Recovery requires a run ID", 400)
            run = runs.create(question, language)
        tracking = RunSession(runs, run)
        active_runs[run["id"]] = {"agent": None, "stop": False, "tracking": tracking, "stop_event": asyncio.Event()}

        async def generate():
            from .agent.usage_tracker import finish_session, get_usage_summary, reset_tracker

            reset_tracker()
            agent = None
            events = None
            started = time.monotonic()
            progress = {"started": started, "last_update": started, "last_event": "initializing", "events": 0}
            status = "running"
            usage = None
            interval = getattr(settings, "progress_log_interval", 30.0)
            monitor = asyncio.create_task(_log_request_progress(body.session_id, progress, interval)) if interval > 0 else None
            logging.info(
                "[Research:%s] Started; provider=%s model=%s search=%s lang=%s async=%s",
                body.session_id, settings.llm_provider, settings.llm_model,
                settings.search_engine, body.lang or settings.lang, settings.asy,
            )
            try:
                yield _sse(_event("research.run", **tracking.info(), language=language))
                factory = agent_factory
                if factory is None:
                    from .agent import init_agent

                    factory = init_agent
                options = {
                    "lang": body.lang or settings.lang,
                    "llm_provider": settings.llm_provider,
                    "llm_model": settings.llm_model,
                    "search_engine": settings.search_engine,
                    "use_async": settings.asy,
                }
                for role in ("searcher", "reader", "prefilter", "quality", "cognitive", "writer"):
                    options[f"{role}_model"] = getattr(settings, f"{role}_model")
                if settings.writer_model is None and getattr(settings, "summarizer_model", None) is not None:
                    options["summarizer_model"] = settings.summarizer_model
                agent = factory(**options)
                active_runs[run["id"]]["agent"] = agent
                if body.resume_run_id:
                    agent.restore_checkpoint(run["checkpoint"], body.recovery_action or "continue")
                if active_runs[run["id"]]["stop"]:
                    status = "cancelled"
                    yield _sse(_event("research.cancelled"))
                    return
                events = (
                    agent(body.inputs, session_id=body.session_id)
                    if settings.asy
                    else stream_events(agent, body.inputs, session_id=body.session_id)
                )
                events = stop_aware_events(events, active_runs[run["id"]]["stop_event"])
                async for event in events:
                    if await request.is_disconnected():
                        status = "disconnected"
                        logging.info("[Research:%s] Client disconnected", body.session_id)
                        break
                    payload = event if isinstance(event, dict) else event.to_dict()
                    payload = tracking.observe(payload)
                    kind = payload["type"]
                    if kind == "research.progress":
                        activity = payload.get("data", {})
                        logging.info("[Research:%s] Stage=%s task=%s section=%s/%s", body.session_id,
                                     activity.get("stage"), payload.get("task_id"),
                                     activity.get("section_index"), activity.get("section_total"))
                    if kind != "research.heartbeat":
                        progress.update(last_event=kind, last_update=time.monotonic(), events=progress["events"] + 1)
                    if kind in ("research.completed", "research.failed", "research.cancelled"):
                        status = kind.removeprefix("research.")
                        logging.info("[Research:%s] %s", body.session_id, kind)
                    if kind in ("task.failed", "research.failed"):
                        data = payload.get("data", {})
                        task = data.get("task", {})
                        error = data.get("error") or (task.get("result") or {}).get("error")
                        logging.error("[Research:%s] %s task=%s error=%s", body.session_id, kind,
                                      payload.get("task_id") or task.get("id"), error)
                    yield _sse(payload)
                if status == "running":
                    status = "incomplete"
                    logging.error("[Research:%s] Stream ended without a terminal research event", body.session_id)
            except asyncio.CancelledError:
                status = "cancelled"
                active = active_runs.get(run["id"])
                stop_requested = bool(active and active["stop"])
                if active:
                    active["stop"] = True
                logging.info("[Research:%s] Stream consumer cancelled; stop_requested=%s last_event=%s stage=%s",
                             body.session_id, stop_requested, progress["last_event"],
                             tracking.view.get("activity", {}).get("stage"))
                raise
            except Exception as exc:
                status = "failed"
                logging.exception("[Research:%s] Research request failed; last_event=%s",
                                  body.session_id, progress["last_event"])
                failure = _event("research.failed", error=str(exc))
                tracking.observe(failure)
                yield _sse(failure)
            finally:
                if monitor is not None:
                    monitor.cancel()
                    with suppress(asyncio.CancelledError):
                        await monitor
                try:
                    if agent is not None:
                        cancel = getattr(agent, "cancel", None)
                        if cancel is not None:
                            cancel()
                except Exception:
                    logging.exception("[Research:%s] Agent cancellation failed", body.session_id)
                try:
                    if events is not None and hasattr(events, "aclose"):
                        await events.aclose()
                except Exception:
                    logging.exception("[Research:%s] Stream cleanup failed", body.session_id)
                finally:
                    checkpoint = None
                    try:
                        if agent is not None and hasattr(agent, "latest_checkpoint"):
                            checkpoint = agent.latest_checkpoint()
                    except Exception:
                        logging.exception("[Research:%s] Failed to read final checkpoint", body.session_id)
                    try:
                        finish_session()
                    except Exception:
                        logging.warning("[Research:%s] Failed to finish usage accounting", body.session_id, exc_info=True)
                    try:
                        usage = tracking.record_usage(get_usage_summary())
                    except Exception:
                        logging.warning("[Research:%s] Failed to send usage stats", body.session_id, exc_info=True)
                    tracking.save(checkpoint=checkpoint or None, status=status)
                    active_runs.pop(run["id"], None)
                    logging.info(
                        "[Research:%s] Finished; status=%s elapsed=%.1fs events=%s last_event=%s",
                        body.session_id, status, time.monotonic() - started, progress["events"], progress["last_event"],
                    )
            if not await request.is_disconnected():
                try:
                    yield _sse(_event("research.recovery", **tracking.info()))
                    if usage is not None:
                        yield _sse(_event("usage.updated", usage=usage))
                except Exception:
                    logging.warning("[Research:%s] Failed to send usage stats", body.session_id, exc_info=True)

        return EventSourceResponse(generate(), ping=300, headers={"X-Research-Run-ID": run["id"],
                                                                 "X-Research-Attempt": str(run["attempt"])})

    @app.get("/api/research/{run_id}")
    async def get_research_run(run_id: str):
        run = runs.get(run_id)
        if run is None:
            return _error("Research run not found")
        active = active_runs.get(run_id)
        if active:
            tracking = active["tracking"]
            info = tracking.info()
            if active["stop"] and info["status"] != "completed":
                info["status"] = "stopping"
            return {"question": run["question"], "language": run["language"], "error": tracking.run.get("error"),
                    "view": tracking.view, "recovery": info}
        if run["status"] == "running":
            run["status"] = "interrupted"
        return {"question": run["question"], "language": run["language"], "error": run["error"] if run["status"] != "completed" else None,
                "view": run["view"], "recovery": recovery_info(run)}

    @app.post("/api/research/{run_id}/stop")
    async def stop_research_run(run_id: str):
        active = active_runs.get(run_id)
        if active:
            logging.info("[ResearchRun:%s] Stop requested; attempt=%s", run_id, active["tracking"].run["attempt"])
            active["stop"] = True
            active["stop_event"].set()
            if active["agent"] is not None:
                active["agent"].cancel()
            return {"status": "stopping"}
        return {"status": "stopped"} if runs.get(run_id) else _error("Research run not found")

    @app.get("/api/conversations")
    def list_conversations():
        return store.list_conversations()

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        result = store.get_conversation(conversation_id)
        return result if result is not None else _error("Conversation not found")

    @app.post("/api/conversations")
    def create_conversation(body: ConversationCreate):
        try:
            return store.create_conversation(body.id, body.title, body.data)
        except sqlite3.IntegrityError:
            return _error("Conversation already exists", 409)

    @app.put("/api/conversations/{conversation_id}")
    @app.post("/api/conversations/{conversation_id}")
    def update_conversation(conversation_id: str, body: ConversationUpdate):
        success = store.update_conversation(conversation_id, body.title, body.data, body.skip_timestamp_update)
        return {"success": True} if success else _error("Conversation not found")

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str):
        return {"success": True} if store.delete_conversation(conversation_id) else _error("Conversation not found")

    @app.get("/api/conversations-stats")
    def get_conversation_stats():
        return store.get_database_stats()

    @app.get("/api/admin/deleted-conversations")
    def list_deleted_conversations():
        return store.list_deleted_conversations()

    @app.post("/api/admin/conversations/{conversation_id}/restore")
    def restore_conversation(conversation_id: str):
        if not store.restore_conversation(conversation_id):
            return _error("Conversation not found or not deleted")
        return {"success": True, "message": f"Conversation {conversation_id} restored"}

    @app.delete("/api/admin/conversations/{conversation_id}/permanent")
    def permanently_delete_conversation(conversation_id: str):
        if not store.permanently_delete_conversation(conversation_id):
            return _error("Conversation not found")
        return {"success": True, "message": f"Conversation {conversation_id} permanently deleted"}

    @app.delete("/api/admin/deleted-conversations/all")
    def permanently_delete_all():
        count = store.permanently_delete_all_deleted()
        return {"success": True, "deleted_count": count, "message": f"Permanently deleted {count} conversations"}

    @app.post("/api/export-eval")
    def export_for_eval(body: ExportEvalRequest):
        article = body.article.strip()
        if len(article) < 50:
            return _error("Article too short for evaluation", 400)
        queries = {}
        if query_file.exists():
            with query_file.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        record = json.loads(line)
                        queries[record["prompt"]] = record["id"]
        query_id = body.query_id
        question = body.question.strip()
        if query_id is None:
            query_id = next((qid for prompt, qid in queries.items() if prompt.strip() == question), None)
        if query_id is None and len(question) >= 20:
            query_id = next((qid for prompt, qid in queries.items() if prompt.strip()[:50] == question[:50]), None)
        if query_id is None:
            return _error(
                "Could not match question to benchmark query. Please provide query_id.",
                400,
                available_queries={v: k[:60] + "..." for k, v in list(queries.items())[:10]},
            )
        record = {
            "id": query_id,
            "prompt": question,
            "article": article,
            "cited_references": [],
            "status": "success",
            "error": None,
            "duration_seconds": 0,
        }
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / f"q{query_id}.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        logging.info("[ExportEval] Saved article (query_id=%s, chars=%s) to %s", query_id, len(article), export_dir)
        relative_path = f"eval_data/frontend/q{query_id}.jsonl"
        return {
            "success": True,
            "query_id": query_id,
            "file_path": relative_path,
            "article_length": len(article),
            "eval_command": f"python benchmark/run_eval.py --input {relative_path} --name frontend_q{query_id}",
        }

    return app


app = create_app()


def main():
    import uvicorn

    settings = parse_arguments()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level,
                log_config=build_log_config(settings.log_file, settings.log_level))


if __name__ == "__main__":
    main()
