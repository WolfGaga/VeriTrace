"""OpenAI-compatible native tool requests."""

import logging
import time
from typing import Dict, Iterator, List, Optional, Type
from ..core.base_tool import BaseTool
from ..concurrency import check_cancelled
from .types import BaseLLMAdapter, Message, LLMResponse, ToolCall
from .policy import MAX_RETRIES, _get_model_timeout, _warn_on_degraded_response


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter for OpenAI API (GPT-4, GPT-3.5, etc.)."""

    def __init__(
        self,
        model: str = "gpt-4-turbo-preview",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(model, api_key, base_url, **kwargs)
        self._client = None
        self._async_client = None

    def _get_client(self):
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self._resolve_timeout(_get_model_timeout(self.model)),
                )
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")
        return self._client

    def _get_async_client(self):
        """Lazy initialization of async OpenAI client."""
        if self._async_client is None:
            try:
                from openai import AsyncOpenAI

                self._async_client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self._resolve_timeout(_get_model_timeout(self.model)),
                )
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")
        return self._async_client

    def convert_tools(self, tools: List[Type[BaseTool]]) -> List[Dict]:
        """Convert to OpenAI function calling format."""
        return [tool.to_openai_schema() for tool in tools]

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        client = self._get_client()

        request_kwargs = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            **kwargs,
        }

        if tools:
            request_kwargs["tools"] = self.convert_tools(tools)
            request_kwargs["tool_choice"] = kwargs.get("tool_choice", "auto")

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                check_cancelled()
                response = client.chat.completions.create(**request_kwargs)
                return self._parse_response(response)
            except Exception as e:
                if self._should_retry(e) and attempt < MAX_RETRIES - 1:
                    delay = self._get_retry_delay(attempt, e)
                    logging.warning(
                        "[LLM] %s: %s; retry %d/%d in %.1fs (model=%s)",
                        type(e).__name__,
                        str(e)[:300],
                        attempt + 1,
                        MAX_RETRIES,
                        delay,
                        self.model,
                    )
                    time.sleep(delay)
                    last_error = e
                else:
                    logging.error("[LLM] Fatal %s: %s (model=%s)", type(e).__name__, str(e)[:300], self.model)
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

        request_kwargs = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            **kwargs,
        }

        if tools:
            request_kwargs["tools"] = self.convert_tools(tools)
            request_kwargs["tool_choice"] = kwargs.get("tool_choice", "auto")

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                check_cancelled()
                response = await client.chat.completions.create(**request_kwargs)
                return self._parse_response(response)
            except Exception as e:
                if self._should_retry(e) and attempt < MAX_RETRIES - 1:
                    delay = self._get_retry_delay(attempt, e)
                    logging.warning(
                        f"⏳ Rate limit hit (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    last_error = e
                else:
                    raise

        raise last_error

    def stream_chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> Iterator[LLMResponse]:
        client = self._get_client()

        request_kwargs = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "stream": True,
            **kwargs,
        }

        if tools:
            request_kwargs["tools"] = self.convert_tools(tools)
            request_kwargs["tool_choice"] = kwargs.get("tool_choice", "auto")

        check_cancelled()
        stream = client.chat.completions.create(**request_kwargs)

        accumulated_content = ""
        accumulated_tool_calls = []

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta:
                if delta.content:
                    accumulated_content += delta.content
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        accumulated_tool_calls.append(tc)

                yield LLMResponse(
                    content=accumulated_content,
                    tool_calls=[],
                    finish_reason=chunk.choices[0].finish_reason if chunk.choices else None,
                    raw_response=chunk,
                )

    def _parse_response(self, response) -> LLMResponse:
        """Parse OpenAI response into unified format."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall.from_openai(
                        {
                            "id": tc.id,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    )
                )

        try:
            from ..usage_tracker import track_llm_usage

            if hasattr(response, "usage") and response.usage:
                track_llm_usage(
                    input_tokens=response.usage.prompt_tokens or 0,
                    output_tokens=response.usage.completion_tokens or 0,
                    model=self.model,
                )
        except Exception:
            pass

        _warn_on_degraded_response(message.content, choice.finish_reason, self.model)
        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            raw_response=response,
        )


class QwenAdapter(OpenAIAdapter):
    """Adapter for Qwen models via DashScope API."""

    def __init__(
        self,
        model: str = "qwen-max",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            model=model,
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1" if base_url is None else base_url,
            **kwargs,
        )
