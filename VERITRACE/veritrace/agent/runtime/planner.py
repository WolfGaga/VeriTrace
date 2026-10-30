"""Read-only task access for Planner programs."""

from copy import deepcopy

_COMPLETED_RESPONSE = 6


class TaskQuery:
    __slots__ = ("__lookup", "__question", "__finished")

    def __init__(self, lookup, question=None, finished=None):
        self.__lookup = lookup
        self.__question = question
        self.__finished = finished

    def node(self, node_name: str) -> dict:
        node = deepcopy(self.__lookup(node_name))
        if not node and node_name == "root" and self.__question is not None:
            return {"content": self.__question, "type": "root"}
        if not node and node_name == "response" and self.__finished is not None and self.__finished():
            return {"type": "end"}
        if "question" not in node:
            return node
        node["content"] = node["question"]
        node["type"] = "searcher"
        result = node.get("result")
        if isinstance(result, dict):
            node["response"] = {"content": result.get("answer", ""), "stream_state": _COMPLETED_RESPONSE}
            if node.get("status") != "failed":
                node["memory"] = {
                    "agent.memory": result.get("messages", []),
                    "ref2url": result.get("references", {}),
                    "quality_results": result.get("quality_results", []),
                    "feedback": result.get("feedback"),
                    "evidence_records": result.get("evidence_records", []),
                }
        return node
