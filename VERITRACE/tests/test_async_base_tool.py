"""Exercise the default async wrapper using synthetic tools only."""

import asyncio
from contextvars import ContextVar
import threading

from pydantic import Field
import pytest

from veritrace.agent.core.base_tool import BaseTool, ToolResult


request_tag = ContextVar("synthetic_tool_request", default="unset")
execution_threads = []


class SyntheticTool(BaseTool):
    """A local arithmetic fixture."""

    value: int = Field(description="Synthetic input")

    @property
    def name(self):
        return "synthetic"

    @property
    def description(self):
        return "Return local fixture data"

    def execute(self):
        execution_threads.append(threading.get_ident())
        return ToolResult(success=True, output={"value": self.value * 2, "request": request_tag.get()})


def test_default_async_tool_wrapper_runs_in_worker_and_preserves_context():
    execution_threads.clear()
    token = request_tag.set("synthetic-request")
    try:
        result = asyncio.run(SyntheticTool(value=4).aexecute())
    finally:
        request_tag.reset(token)
    assert result.success
    assert result.output == {"value": 8, "request": "synthetic-request"}
    assert len(execution_threads) == 1 and execution_threads[0] != threading.get_ident()


def test_default_async_tool_wrapper_preserves_execute_errors():
    class FailingTool(SyntheticTool):
        def execute(self):
            raise ValueError("synthetic-failure")

    with pytest.raises(ValueError, match="synthetic-failure"):
        asyncio.run(FailingTool(value=1).aexecute())
