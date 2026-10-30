"""Provider extraction preserves application imports and request behavior."""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from veritrace.agent.core import llm_adapter
from veritrace.agent.providers import policy, rotating
from veritrace.agent.providers.anthropic import AnthropicAdapter
from veritrace.agent.providers.generic import GenericPromptAdapter
from veritrace.agent.providers.openai import OpenAIAdapter
from veritrace.agent.providers.types import Message


class RateLimited(Exception):
    status_code = 429


@pytest.mark.parametrize("adapter_class", [AnthropicAdapter, GenericPromptAdapter])
@pytest.mark.parametrize("use_async", [False, True])
def test_non_openai_providers_retry_transient_errors(adapter_class, use_async, monkeypatch):
    adapter = adapter_class(model="fixture", api_key="fixture-key")
    calls = []
    if adapter_class is AnthropicAdapter:
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="completed")], stop_reason="end_turn", usage=None
        )
    else:
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="completed"), finish_reason="stop")]
        )

    def request(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RateLimited("rate limited")
        return response

    async def async_request(**kwargs):
        return request(**kwargs)

    create = async_request if use_async else request
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create), chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(adapter, "_get_async_client" if use_async else "_get_client", lambda: client)
    monkeypatch.setattr(adapter, "_get_retry_delay", lambda *args: 0)
    messages = [Message("system", "system rules"), Message("user", "question")]
    result = asyncio.run(adapter.achat(messages)) if use_async else adapter.chat(messages)
    assert result.content == "completed"
    assert len(calls) == 2 and calls[0] == calls[1]
    if adapter_class is AnthropicAdapter:
        assert calls[0]["system"] == "system rules"
        assert calls[0]["messages"] == [{"role": "user", "content": "question"}]
    else:
        assert calls[0]["messages"] == [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "question"},
        ]


def test_existing_import_paths_share_classes_and_concurrency_state():
    assert llm_adapter.OpenAIAdapter is OpenAIAdapter
    assert llm_adapter.Message is Message
    assert llm_adapter._llm_semaphore is policy._llm_semaphore is rotating._llm_semaphore


def test_selected_baseline_timeouts_and_generic_prompt_are_preserved():
    assert policy._get_model_timeout("deepseek-v3-pro") == 300
    assert policy._get_model_timeout("deepseek-v4-pro") == 300
    assert policy._get_model_timeout("fixture") == 180
    assert (
        hashlib.sha256(GenericPromptAdapter.TOOL_CALL_FORMAT.encode()).hexdigest()
        == "2ed714a0a25f698c78a7e2556369a367a460c818a122c3932354bf390107f987"
    )
