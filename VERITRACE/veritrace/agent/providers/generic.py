"""Prompt-based tool calling over an OpenAI-compatible transport."""

import json
import re
import logging
import time
from typing import List, Optional, Type
from ..core.base_tool import BaseTool
from ..concurrency import check_cancelled
from .types import BaseLLMAdapter, Message, LLMResponse, ToolCall
from .policy import MAX_RETRIES, _get_model_timeout


class GenericPromptAdapter(BaseLLMAdapter):
    """Generic adapter for models without native tool support."""

    TOOL_CALL_FORMAT = """
When you need to use a tool, output it in the following JSON format:
```json
{
    "tool": "tool_name",
    "arguments": {
        "param1": "value1",
        "param2": "value2"
    }
}
```

Or for code execution, wrap your code in:
```python
# your code here
```
"""

    def __init__(
        self,
        model: str,
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

    def convert_tools(self, tools: List[Type[BaseTool]]) -> str:
        """Convert tools to prompt description."""
        descriptions = []
        for tool in tools:
            schema = tool.to_openai_schema()
            func = schema["function"]
            desc = f"### {func['name']}\n"
            desc += f"{func['description']}\n\n"
            desc += f"Parameters:\n```json\n{json.dumps(func['parameters'], indent=2)}\n```\n"
            descriptions.append(desc)
        return "\n".join(descriptions)

    def _inject_tools_into_messages(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]],
    ) -> List[Message]:
        """Add tool descriptions to system message."""
        if not tools:
            return messages

        tool_desc = self.convert_tools(tools)
        tool_prompt = f"\n\n## Available Tools\n\n{tool_desc}\n\n{self.TOOL_CALL_FORMAT}"

        new_messages = []
        system_updated = False

        for msg in messages:
            if msg.role == "system" and not system_updated:
                new_messages.append(
                    Message(
                        role="system",
                        content=msg.content + tool_prompt,
                    )
                )
                system_updated = True
            else:
                new_messages.append(msg)

        if not system_updated:
            new_messages.insert(
                0,
                Message(
                    role="system",
                    content=f"You are a helpful assistant.{tool_prompt}",
                ),
            )

        return new_messages

    def _extract_tool_calls(self, content: str) -> List[ToolCall]:
        """Extract tool calls from LLM response content."""
        tool_calls = []

        json_pattern = r"```json\s*\n({.*?})\s*\n```"
        matches = re.findall(json_pattern, content, re.DOTALL)

        for i, match in enumerate(matches):
            try:
                data = json.loads(match)
                if "tool" in data and "arguments" in data:
                    tool_calls.append(
                        ToolCall(
                            id=f"call_{i}",
                            name=data["tool"],
                            arguments=data["arguments"],
                        )
                    )
            except json.JSONDecodeError:
                continue

        return tool_calls

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        client = self._get_client()

        processed_messages = self._inject_tools_into_messages(messages, tools)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                check_cancelled()
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[m.to_openai() for m in processed_messages],
                    **kwargs,
                )

                content = response.choices[0].message.content or ""
                tool_calls = self._extract_tool_calls(content) if tools else []

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=response.choices[0].finish_reason,
                    raw_response=response,
                )
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

        processed_messages = self._inject_tools_into_messages(messages, tools)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                check_cancelled()
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[m.to_openai() for m in processed_messages],
                    **kwargs,
                )

                content = response.choices[0].message.content or ""
                tool_calls = self._extract_tool_calls(content) if tools else []

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=response.choices[0].finish_reason,
                    raw_response=response,
                )
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
