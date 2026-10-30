"""Plain Planner node dictionaries and native task snapshot conversion."""

_MEMORY_FIELDS = {
    "agent.memory": "messages", "ref2url": "references",
    "quality_results": "quality_results", "feedback": "feedback",
    "evidence_records": "evidence_records",
}
_NODE_FIELDS = {"id", "question", "result", "content", "type", "response", "memory", "error"}


def task_to_node(task):
    """Convert a native task to plain node dictionaries; callers own copying."""
    node = {key: value for key, value in task.items() if key not in _NODE_FIELDS}
    node.update(content=task.get("question", ""), type="searcher")
    result = task.get("result")
    if isinstance(result, dict):
        node["response"] = {"stream_state": 6}
        if "answer" in result:
            node["response"]["content"] = result["answer"]
        if any(new in result for new in _MEMORY_FIELDS.values()):
            node["memory"] = {old: result[new] for old, new in _MEMORY_FIELDS.items() if new in result}
        if "error" in result:
            node["error"] = result["error"]
    return node


def node_to_task(task_id, node):
    """Project a plain node to the native task schema without copying payloads."""
    task = {key: value for key, value in node.items() if key not in _NODE_FIELDS}
    status, result = node.get("status", "pending"), None
    if "response" in node:
        response, memory = node["response"], node.get("memory") or {}
        result = {}
        if isinstance(response, dict):
            if "content" in response:
                result["answer"] = response["content"]
        else:
            result["answer"] = str(response or "")
        if "error" in node:
            result["error"] = node["error"]
        if isinstance(memory, dict):
            result.update({new: memory[old] for old, new in _MEMORY_FIELDS.items() if old in memory})
        if result.get("error") and status != "cancelled":
            status = "failed"
        elif status not in ("failed", "cancelled"):
            status = "completed"
    elif status == "completed":
        status = "pending"
    task.update(id=task_id, question=node.get("content", ""), status=status, result=result)
    return task


class PlannerGraph:
    """Expose the scheduler's actual plain nodes using baseline Planner names."""

    def __init__(self, scheduler, emit=None):
        object.__setattr__(self, "_scheduler", scheduler)
        object.__setattr__(self, "_emit", emit)

    def __getattr__(self, name):
        return getattr(self._scheduler, name)

    def __setattr__(self, name, value):
        if name in ("_scheduler", "_emit"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._scheduler, name, value)

    def _command(self, name, *args, **kwargs):
        result = getattr(self._scheduler, name)(*args, **kwargs)
        if self._emit is not None and isinstance(result, str) and ("[ERROR]" in result or "[WARNING]" in result):
            self._emit(result)
        return result

    def add_task(self, *args, **kwargs):
        return self._command("add_task", *args, **kwargs)

    add_node = add_task

    def propose_restructure(self, *args, **kwargs):
        return self._command("propose_restructure", *args, **kwargs)

    def add_root_node(self, node_content, node_name="root"):
        self._scheduler.set_question(node_content)

    def finish(self):
        self._scheduler.finish()

    add_response_node = finish

    def add_edge(self, start_node, end_node):
        pass

    def node(self, node_name):
        return self.nodes.get(node_name, {}).copy()

    @property
    def adjacency_list(self):
        groups, children = {}, []
        for name, task in self.nodes.items():
            if task.get("type") in ("root", "end"):
                continue
            entry = {"id": name, "name": name, "state": 3 if "response" in task else 1}
            target = task.get("cognitive_target_id")
            if target:
                groups.setdefault(target, []).append(entry)
            else:
                children.append(entry)
        manager = self._scheduler.cognitive_manager
        for target, tasks in groups.items():
            node = manager.graph.get_node(target) if manager and manager.graph else None
            children.append({"id": target, "name": node.name if node and node.name else target,
                             "state": 3 if all(task["state"] == 3 for task in tasks) else 1})
        return {**groups, "root": children}
