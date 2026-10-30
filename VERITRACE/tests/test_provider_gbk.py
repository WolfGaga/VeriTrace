"""Retry diagnostics must not interrupt calls on a GBK stdout stream."""

import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
from types import SimpleNamespace

import pytest

from veritrace.agent.providers import create_adapter
from veritrace.agent.providers.types import Message


class SyntheticHTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


def run_scenario(provider, use_async, failure):
    calls = []
    options = {} if provider == "openai" else {"backup_api_keys": ["synthetic-backup"]}
    adapter = create_adapter(provider, "synthetic-model", api_key="synthetic-primary", **options)
    adapter._get_retry_delay = lambda *args: 0
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="completed", tool_calls=None, reasoning_content=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )

    def request(key, kwargs):
        calls.append({"key": key, "arguments": deepcopy(kwargs)})
        if failure == "exhausted":
            raise SyntheticHTTPError(503, "synthetic service unavailable")
        if failure == "fatal":
            raise SyntheticHTTPError(400, "synthetic invalid request")
        if key == "synthetic-primary" and (provider != "openai" or len(calls) == 1):
            if failure == "balance":
                raise SyntheticHTTPError(400, "synthetic Arrearage")
            raise SyntheticHTTPError(429, "synthetic rate limited")
        return response

    def client(key):
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: request(key, kwargs)))
        )

    def async_client(key):
        async def create(**kwargs):
            return request(key, kwargs)

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    if provider == "openai":
        adapter._get_client = lambda: client("synthetic-primary")
        adapter._get_async_client = lambda: async_client("synthetic-primary")
    else:
        adapter._get_client_for_key = client
        adapter._get_async_client_for_key = async_client

    outcome = {"content": None, "error_type": None, "error_message": None}
    messages = [Message("user", "Synthetic request")]
    try:
        reply = (
            asyncio.run(adapter.achat(messages, temperature=0.2))
            if use_async
            else adapter.chat(messages, temperature=0.2)
        )
        outcome["content"] = reply.content
    except Exception as exc:
        outcome.update(error_type=type(exc).__name__, error_message=str(exc))
    return {"requests": calls, "outcome": outcome}


CASES = [
    (provider, use_async, failure)
    for provider in ("siliconflow", "dashscope")
    for use_async in (False, True)
    for failure in ("rate_limit", "balance", "exhausted")
]
CASES += [("openai", False, "rate_limit"), ("openai", False, "fatal")]


@pytest.mark.parametrize("provider,use_async,failure", CASES)
def test_gbk_stdout_does_not_change_retry_outcome(provider, use_async, failure, caplog):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="gbk", errors="strict")
    with redirect_stdout(stream):
        result = run_scenario(provider, use_async, failure)
    if failure in ("exhausted", "fatal"):
        assert result["outcome"]["error_type"] == "SyntheticHTTPError"
    else:
        assert result["outcome"] == {"content": "completed", "error_type": None, "error_message": None}
        assert result["requests"][-1]["key"] == ("synthetic-primary" if provider == "openai" else "synthetic-backup")
    if provider == "dashscope":
        diagnostics = [record.getMessage() for record in caplog.records]
        assert any("DashScope" in message for message in diagnostics)
        assert not any("[SiliconFlow]" in message for message in diagnostics)
    stream.close()
