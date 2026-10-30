"""Values shared by the research engine and its entry points."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResearchEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    schema_version: int = field(default=2, init=False)

    def to_dict(self) -> dict:
        event = {"schema_version": 2, "type": self.type, "data": deepcopy(self.data)}
        if self.task_id is not None:
            event["task_id"] = self.task_id
        return event


@dataclass
class TaskResult:
    answer: str = ""
    references: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    quality_results: list = field(default_factory=list)
    feedback: dict | None = None
    evidence_records: list = field(default_factory=list)
    error: str | None = None

    @classmethod
    def from_search(cls, result: dict) -> "TaskResult":
        return cls(
            answer=result["content"],
            references=result.get("ref2url", {}),
            messages=result.get("memory", []),
            quality_results=result.get("quality_results", []),
            feedback=result.get("feedback"),
            evidence_records=result.get("evidence_records", []),
            error=result.get("error"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResearchResult:
    article: str
    ref2url: dict = field(default_factory=dict)
    tasks: dict = field(default_factory=dict)
    cognitive_graph: dict = field(default_factory=dict)

    def to_event(self) -> ResearchEvent:
        return ResearchEvent("research.completed", asdict(self))
