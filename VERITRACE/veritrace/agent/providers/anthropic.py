"""Anthropic messages and native tool use."""

import logging
import time
from typing import Dict, List, Optional, Type
from ..core.base_tool import BaseTool
from ..concurrency import check_cancelled
from .types import BaseLLMAdapter, Message, LLMResponse, ToolCall
from .policy import MAX_RETRIES, REQUEST_TIMEOUT, _warn_on_degraded_response


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter for Anthropic API (Claude)."""

    def __init__(
        self,
        model: str = "claude-3-opus-20240229",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(model, api_key, base_url, **kwargs)
        self._client = None
        self._async_client = None

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import Anthropic

                self._client = Anthropic(api_key=self.api_key, timeout=self._resolve_timeout(REQUEST_TIMEOUT))
            except ImportError:
                raise ImportError("anthropic package not installed. Run: pip install anthropic")
        return self._client

    def _get_async_client(self):
        if self._async_client is None:
            try:
                from anthropic import AsyncAnthropic

                self._async_client = AsyncAnthropic(
                    api_key=self.api_key, timeout=self._resolve_timeout(REQUEST_TIMEOUT)
                )
            except ImportError:
                raise ImportError("anthropic package not installed. Run: pip install anthropic")
        return self._async_client

    def convert_tools(self, tools: List[Type[BaseTool]]) -> List[Dict]:
        """Convert to Anthropic tool format."""
        return [tool.to_anthropic_schema() for tool in tools]

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        client = self._get_client()

        system_msg = ""
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                chat_messages.append(m.to_anthropic())

        request_kwargs = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

        if system_msg:
            request_kwargs["system"] = system_msg

        if tools:
            request_kwargs["tools"] = self.convert_tools(tools)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                check_cancelled()
                response = client.messages.create(**request_kwargs)
                return self._parse_response(response)
            except Exception as e:
                if self._should_retry(e) and attempt < MAX_RETRIES - 1:
                    delay = self._get_retry_delay(attempt, e)
                    logging.warning(
                        f"⏳ Rate limit/timeout hit (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    last_error = e
                else:
                    raise

        raise last_error

    async def achat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        import asyncio

        client = self._get_async_client()

        system_msg = ""
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                chat_messages.append(m.to_anthropic())

        request_kwargs = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

        if system_msg:
            request_kwargs["system"] = system_msg

        if tools:
            request_kwargs["tools"] = self.convert_tools(tools)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                check_cancelled()
                response = await client.messages.create(**request_kwargs)
                return self._parse_response(response)
            except Exception as e:
                if self._should_retry(e) and attempt < MAX_RETRIES - 1:
                    delay = self._get_retry_delay(attempt, e)
                    logging.warning(
                        f"⏳ Rate limit/timeout hit (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    last_error = e
                else:
                    raise

        raise last_error

    def _parse_response(self, response) -> LLMResponse:
        """Parse Anthropic response into unified format."""
        content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall.from_anthropic(
                        {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                )

        try:
            from ..usage_tracker import track_llm_usage

            if hasattr(response, "usage") and response.usage:
                track_llm_usage(
                    input_tokens=response.usage.input_tokens or 0,
                    output_tokens=response.usage.output_tokens or 0,
                    model=self.model,
                )
        except Exception:
            pass

        _warn_on_degraded_response(content, response.stop_reason, self.model)
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason,
            raw_response=response,
        )
