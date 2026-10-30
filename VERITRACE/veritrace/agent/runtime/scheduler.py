"""Task scheduling independent of Planner state and presentation."""

import logging
import os
import queue
import re
import threading
import time
from concurrent.futures import CancelledError
from ..concurrency import ThreadPoolExecutor
from copy import deepcopy
from typing import List, Optional

from ..core.llm_adapter import create_adapter
from ..searcher import SearcherAgent
from .events import ResearchEvent, TaskResult
from .planner_graph import task_to_node, node_to_task
from .progress import report_progress, task_progress


class TaskScheduler:
    def __init__(
        self,
        llm=None,
        llm_provider=None,
        llm_model="Qwen/Qwen3-Next-80B-A3B-Thinking",
        searcher_cfg=None,
        searcher_llm=None,
        reader_llm=None,
        prefilter_llm=None,
        cognitive_manager=None,
        searcher_factory=None,
        max_workers=10,
        session_id=None,
    ):
        self.llm = (
            llm
            if llm is not None
            else create_adapter(llm_provider or os.environ.get("LLM_PROVIDER", "siliconflow"), llm_model)
        )
        self.searcher_llm = searcher_llm
        self.reader_llm = reader_llm
        self.prefilter_llm = prefilter_llm
        self.searcher_cfg = searcher_cfg or {}
        self.cognitive_manager = cognitive_manager
        self.searcher_factory = searcher_factory or SearcherAgent
        self.start_time = None
        self._tasks = {}
        self._pending = set()
        self._futures = {}
        self._task_started_at = {}
        self._session_id = session_id if session_id is not None else "-"
        self._lock = threading.RLock()
        self._completed = queue.Queue()
        self._cancelled = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="veritrace-search")
        self._closed = False
        self._pending_restructure = None

    @property
    def tasks(self):
        with self._lock:
            return {name: deepcopy(node_to_task(name, node)) for name, node in self._tasks.items()
                    if name not in ("root", "response") and node.get("type") not in ("root", "end")}

    @property
    def nodes(self):
        return self._tasks

    @nodes.setter
    def nodes(self, value):
        with self._lock:
            self._tasks = value

    @property
    def question(self):
        return self._tasks.get("root", {}).get("content", "")

    @question.setter
    def question(self, value):
        self._tasks["root"] = {"content": value, "type": "root"}

    @property
    def finish_requested(self):
        return "response" in self._tasks

    @finish_requested.setter
    def finish_requested(self, value):
        if value:
            self._tasks["response"] = {"type": "end"}
        else:
            self._tasks.pop("response", None)

    @property
    def pending_count(self):
        with self._lock:
            return len(self._pending)

    def task(self, task_id):
        with self._lock:
            node = self._tasks.get(task_id)
            if node is None or task_id in ("root", "response"):
                return {}
            return deepcopy(node_to_task(task_id, node))

    def set_question(self, question):
        self.question = question

    def restore_tasks(self, tasks, *, retry_unfinished=True):
        retry = []
        with self._lock:
            if self.tasks or self._pending:
                raise RuntimeError("Restore requires an empty task scheduler")
            for task_id, source in (tasks or {}).items():
                task = deepcopy(source)
                if task.get("status") == "completed" and task.get("result"):
                    self._tasks[task_id] = task_to_node(task)
                elif retry_unfinished and task.get("cognitive_edge_id") and task.get("cognitive_target_id"):
                    retry.append((task_id, task))
                else:
                    task["status"] = "failed" if task.get("status") == "failed" else "cancelled"
                    self._tasks[task_id] = task_to_node(task)
        fields = ("task_objective", "attention_notes", "is_verification", "cognitive_relevance", "cognitive_edge_id",
                  "cognitive_target_id", "task_type", "specified_source", "core_criteria", "supplementary_criteria")
        for task_id, task in retry:
            self.add_task(task_name=task_id, node_content=task["question"], **{key: task[key] for key in fields if key in task})
        return [task_id for task_id, _ in retry]

    def finish(self):
        self.finish_requested = True

    def resume(self):
        self.finish_requested = False

    def _complete(self, task_id, future, submitted_task):
        with self._lock:
            if self._futures.get(task_id) is not future:
                return
        try:
            result = future.result()
            status = "failed" if result is not None and result.error else "completed" if result is not None else "cancelled"
        except CancelledError:
            result, status = None, "cancelled"
        except Exception as exc:
            logging.exception("[TaskScheduler] Search task '%s' failed", task_id)
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            result = TaskResult(answer=f"[Search failed: {error}]", error=error)
            status = "failed"
        with self._lock:
            if self._futures.get(task_id) is not future:
                return
            self._pending.discard(task_id)
            started_at = self._task_started_at.pop(task_id, None)
            if self._tasks.get(task_id) is not submitted_task:
                logging.info("[Research:%s] [TaskScheduler] Task '%s' was removed before completion",
                             self._session_id, task_id)
                return
            cancelled = self._cancelled.is_set()
            if cancelled:
                self._tasks[task_id]["status"] = "cancelled"
                status = "cancelled"
            else:
                task = node_to_task(task_id, submitted_task)
                task.update(status=status, result=result.to_dict() if result else None)
                submitted_task.clear()
                submitted_task.update(task_to_node(task))
            elapsed = time.monotonic() - started_at if started_at is not None else 0.0
            logging.info("[Research:%s] [TaskScheduler] Task '%s' ended; status=%s elapsed=%.2fs",
                         self._session_id, task_id, status, elapsed)
            if not cancelled:
                self._completed.put((task_id, future, submitted_task))

    def iter_completed(self, timeout=910.0, poll_timeout=10.0, progress_log_interval=60.0):
        """Wait for results; only unanswered queue polls consume the wait budget."""
        if poll_timeout <= 0:
            raise ValueError("poll_timeout must be positive")
        idle_wait = 0.0
        last_completion = time.monotonic()
        last_progress_log = last_completion
        while not self._cancelled.is_set():
            with self._lock:
                try:
                    event = self._completed.get_nowait()
                except queue.Empty:
                    event = None
                    if not self._pending:
                        return
                except CancelledError:
                    raise
                except Exception:
                    logging.exception("Unable to receive search task results; continuing with collected evidence")
                    return
            if event is None:
                now = time.monotonic()
                if idle_wait >= timeout:
                    with self._lock:
                        pending = sorted(self._pending)
                    logging.error(
                        "Search tasks stopped reporting progress for %.1fs; pending=%s; "
                        "continuing with collected evidence", timeout, pending,
                    )
                    return
                if progress_log_interval > 0 and now - last_progress_log >= progress_log_interval:
                    with self._lock:
                        pending = sorted(self._pending)
                    logging.info("[TaskScheduler] Waiting %.1fs for search completion; pending=%s",
                                 now - last_completion, pending)
                    last_progress_log = now
                wait_timeout = min(poll_timeout, timeout - idle_wait)
                try:
                    event = self._completed.get(timeout=wait_timeout)
                except queue.Empty:
                    idle_wait += wait_timeout
                    continue
                except CancelledError:
                    raise
                except Exception:
                    logging.exception("Unable to receive search task results; continuing with collected evidence")
                    return
            if self._cancelled.is_set():
                return
            if isinstance(event, tuple):
                task_id, future, submitted_task = event
                with self._lock:
                    task = self._tasks.get(task_id)
                    if self._futures.get(task_id) is not future or task is not submitted_task:
                        continue
                    snapshot = node_to_task(task_id, task)
                    if snapshot.get("status") not in ("completed", "failed"):
                        continue
                    kind = "task.failed" if snapshot["status"] == "failed" else "task.completed"
                    event = ResearchEvent(kind, {"task": deepcopy(snapshot)}, task_id)
            idle_wait = 0.0
            last_completion = time.monotonic()
            yield event

    def cancel(self):
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        self._completed.put(ResearchEvent("research.cancelled"))
        with self._lock:
            for task_id, future in self._futures.items():
                task = self._tasks.get(task_id)
                if task is not None and task.get("status") in ("pending", "running"):
                    task["status"] = "cancelled"
                future.cancel()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def add_task(
        self,
        task_name: str = None,
        node_content: str = None,
        *,
        node_name: str = None,
        task_objective: Optional[str] = None,
        attention_notes: Optional[List[str]] = None,
        is_verification: bool = False,
        cognitive_relevance: Optional[str] = None,
        cognitive_edge_id: Optional[str] = None,
        cognitive_target_id: Optional[str] = None,
        task_type: str = "open",
        specified_source: Optional[str] = None,
        core_criteria: Optional[List[str]] = None,
        supplementary_criteria: Optional[List[str]] = None,
    ) -> str:
        """Create a search task and trigger search."""
        if self._closed or self._cancelled.is_set():
            raise RuntimeError("Research session is closed")

        node_name = (task_name or node_name or "").strip()

        if re.match(r"^(e_dyn_|r_dyn_|e\d+$|r\d+$)", node_name):
            error_msg = (
                f"[ERROR] Task name '{node_name}' rejected: looks like a cognitive graph node/edge ID.\n"
                f"Task names must be descriptive, e.g. 'task_search_income_data' or 'task_verify_mckinsey_report'.\n"
                f"Do NOT use cognitive graph IDs (e_dyn_X, r_dyn_X, eN, rN) as task names."
            )
            logging.error(f"[TaskScheduler] {error_msg}")
            print(error_msg)
            return error_msg

        if not cognitive_edge_id or not cognitive_target_id:
            error_msg = (
                f"[ERROR] Task '{node_name}' rejected: missing cognitive graph mapping.\n"
                f"You MUST provide both cognitive_edge_id and cognitive_target_id.\n"
                f"Get these from the cognitive graph's '待解决的询问目标' or '调查中' section:\n"
                f"  edge_id: rXX, target_id: eXX\n"
                f"Without these, search results cannot update the cognitive graph."
            )
            logging.error(f"🚫 [TaskScheduler] {error_msg}")
            print(error_msg)
            return error_msg

        if self.start_time is not None:
            import time as _time
            from ..config import SOFT_DEADLINE_MINUTES

            elapsed_min = (_time.monotonic() - self.start_time) / 60
            if SOFT_DEADLINE_MINUTES > 0 and elapsed_min >= SOFT_DEADLINE_MINUTES:
                reject_msg = (
                    f"⏰ [SOFT_DEADLINE] Task '{node_name}' rejected: soft deadline reached "
                    f"({elapsed_min:.1f}min >= {SOFT_DEADLINE_MINUTES}min). "
                    f"No new search tasks accepted. Call finish() to write the report."
                )
                logging.warning(reject_msg)
                print(reject_msg)
                return reject_msg

        logging.debug("🔍 [TaskScheduler] ADD_TASK called")
        logging.debug(f"   task_name: {node_name}")
        logging.debug(f"   node_content: {node_content[:100]}...")
        if task_objective:
            logging.debug(f"   task_objective: {task_objective[:80]}...")
        if attention_notes:
            logging.debug(f"   attention_notes: {len(attention_notes)} items")
        if is_verification:
            logging.debug("   is_verification: True")
        if cognitive_relevance:
            logging.debug(f"   cognitive_relevance: {cognitive_relevance[:60]}...")
        logging.debug(f"   task_type: {task_type}")
        if specified_source:
            logging.debug(f"   specified_source: {specified_source}")
        if core_criteria:
            logging.debug(f"   core_criteria: {core_criteria}")
        if supplementary_criteria:
            logging.debug(f"   supplementary_criteria: {supplementary_criteria}")

        with self._lock:
            if node_name in self._tasks:
                dup_msg = f"[WARNING] Task '{node_name}' already exists, skipping duplicate creation"
                logging.warning(f"⚠️ [TaskScheduler] {dup_msg}")
                print(dup_msg)
                return dup_msg

            self._tasks[node_name] = task_to_node(dict(
                id=node_name,
                question=node_content,
                status="pending",
                result=None,
                task_objective=task_objective,
                attention_notes=attention_notes or [],
                is_verification=is_verification,
                cognitive_relevance=cognitive_relevance,
                cognitive_edge_id=cognitive_edge_id,
                cognitive_target_id=cognitive_target_id,
                task_type=task_type,
                specified_source=specified_source,
                core_criteria=core_criteria or [],
                supplementary_criteria=supplementary_criteria or [],
            ))
            submitted_task = self._tasks[node_name]
        logging.info(f"✅ [TaskScheduler] Task '{node_name}' created, triggering search...")

        def run_search():
            if self._cancelled.is_set():
                return None
            with self._lock:
                if self._tasks.get(node_name) is not submitted_task:
                    return None
                self._tasks[node_name]["status"] = "running"
                self._task_started_at[node_name] = time.monotonic()
            logging.info("[Research:%s] [TaskScheduler] Task '%s' started", self._session_id, node_name)
            background = self._get_cognitive_background(cognitive_edge_id)
            template = self._build_enhanced_input_template(
                base_template=self.searcher_cfg.get("user_input_template", "## Current Question\n{question}"),
                task_objective=task_objective,
                attention_notes=attention_notes,
                is_verification=is_verification,
                cognitive_relevance=cognitive_relevance,
                task_type=task_type,
                specified_source=specified_source,
                cognitive_background=background,
            )
            searcher = self.searcher_factory(
                llm=self.searcher_llm or self.llm,
                search_engine=self.searcher_cfg.get("search_engine", "google"),
                max_results=self.searcher_cfg.get("max_results"),
                max_turn=self.searcher_cfg.get("max_turn"),
                user_input_template=template,
                user_context_template=self.searcher_cfg.get(
                    "user_context_template", "## Previous Q&A\nQ: {question}\nA: {answer}"
                ),
                language=self.searcher_cfg.get("language", "cn"),
                reader_llm=self.reader_llm,
                prefilter_llm=self.prefilter_llm,
            )
            with task_progress(node_name):
                report_progress("searching")
                result = searcher.search_and_summarize(
                    question=node_content,
                    topic=self.question,
                    history=None,
                    cognitive_background=background,
                    original_question=self.cognitive_manager.graph.original_question if self.cognitive_manager else "",
                )
            return TaskResult.from_search(result)

        with self._lock:
            if self._closed or self._cancelled.is_set():
                self._tasks[node_name]["status"] = "cancelled"
                raise RuntimeError("Research session is closed")
            self._pending.add(node_name)
            try:
                future = self._executor.submit(run_search)
            except BaseException:
                self._pending.discard(node_name)
                self._tasks[node_name]["status"] = "failed"
                raise
            self._futures[node_name] = future
            future.add_done_callback(lambda done: self._complete(node_name, done, submitted_task))
        return f"[Searching: {node_content}]"

    def propose_restructure(self, op_type: str, reasoning: str) -> str:
        """Record the Planner's cognitive-graph restructuring proposal."""
        valid_ops = ("R_conc", "R_aug", "R_pivot", "R_prune", "R_correct")
        if op_type not in valid_ops:
            return f"[ERROR] Invalid op_type '{op_type}'. Must be one of: {', '.join(valid_ops)}"

        logging.info(f"🔄 [TaskScheduler] PROPOSE_RESTRUCTURE called (op_type={op_type})")
        logging.debug(f"   reasoning: {reasoning[:200]}...")

        self._pending_restructure = {"op_type": op_type, "reasoning": reasoning}
        return f"[Restructure proposal recorded (type={op_type}). Cognitive graph will be updated before next planning round.]"

    def has_pending_restructure(self) -> bool:
        """Check whether a restructuring proposal is pending."""
        return self._pending_restructure is not None

    def consume_restructure_proposal(self) -> Optional[dict]:
        """Return and clear the pending {op_type, reasoning} proposal, or return None."""
        proposal = self._pending_restructure
        self._pending_restructure = None
        return proposal

    def _get_cognitive_background(self, cognitive_edge_id: Optional[str]) -> Optional[str]:
        """Get background knowledge from the cognitive graph's source node."""
        if not cognitive_edge_id or not self.cognitive_manager:
            return None

        graph = getattr(self.cognitive_manager, "graph", None)
        if not graph:
            return None

        edge = graph.get_edge(cognitive_edge_id)
        if not edge:
            return None

        source_node = graph.nodes.get(edge.source_node_id)
        if not source_node:
            return None

        import re

        def _strip_refs(text: str) -> str:
            """Strip [[n]] citation markers."""
            return re.sub(r"\s*\[\[\d+\]\]", "", text).strip()

        lines = []

        def _unwrap(entry):
            if isinstance(entry, dict) and "value" in entry:
                return entry["value"]
            return entry

        if source_node.cross_item_findings:
            for aspect, entry in source_node.cross_item_findings.items():
                description = _unwrap(entry)
                if isinstance(description, list):
                    items = [_strip_refs(str(item)) for item in description if _strip_refs(str(item))]
                    if items:
                        lines.append(f"- {aspect}:")
                        for i, item in enumerate(items):
                            lines.append(f"  {i + 1}. {item}")
                else:
                    clean = _strip_refs(str(description))
                    if clean:
                        lines.append(f"- {aspect}: {clean}")

        if source_node.item_findings:
            for entity_name, attrs in source_node.item_findings.items():
                entity_lines = []
                for attr, entry in attrs.items():
                    val = _unwrap(entry)
                    clean = _strip_refs(str(val))
                    if clean:
                        entity_lines.append(f"  - {attr}: {clean}")
                if entity_lines:
                    lines.append(f"- {entity_name}:")
                    lines.extend(entity_lines)

        if not lines:
            return None

        return f"## Background Knowledge (from previous research on: {source_node.name})\n" + "\n".join(lines)

    def _build_enhanced_input_template(
        self,
        base_template: str,
        task_objective: Optional[str] = None,
        attention_notes: Optional[List[str]] = None,
        is_verification: bool = False,
        cognitive_relevance: Optional[str] = None,
        task_type: str = "open",
        specified_source: Optional[str] = None,
        cognitive_background: Optional[str] = None,
    ) -> str:
        """Build enhanced user input template with task context."""
        parts = []

        def literal(value):
            return str(value).replace("{", "{{").replace("}", "}}")

        if cognitive_background:
            parts.append(literal(cognitive_background))

        parts.append(base_template)

        if task_objective:
            parts.append(f"\n\n## Task Objective\n{literal(task_objective)}")

        if attention_notes:
            notes_text = "\n".join(f"- {literal(note)}" for note in attention_notes)
            parts.append(f"\n\n## Attention Notes\n{notes_text}")

        if task_type == "specified" and specified_source:
            parts.append(f"\n\n## Task Type: SPECIFIED SOURCE\n指定来源: {literal(specified_source)}")
        elif task_type == "open":
            parts.append("\n\n## Task Type: OPEN EXPLORATION")

        if is_verification:
            parts.append("""

## ⚠️ Verification Task
This is a VERIFICATION search. Focus on:
- Finding multiple independent sources to confirm claims
- Looking for contradictory evidence
- Prioritizing authoritative and official sources
- Being skeptical of single-source claims""")

        if cognitive_relevance:
            parts.append(f"\n\n## Cognitive Graph Context\n{literal(cognitive_relevance)}")

        return "".join(parts)
