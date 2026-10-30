"""Provider-independent messages, responses, and adapter interface."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Type
from ..core.base_tool import BaseTool
from .policy import should_retry, retry_delay


class PayloadTooLargeError(Exception):
    """Raised when the request payload is too large (ALB returns HTML 400)."""

    pass


class LLMProvider(Enum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    QWEN = "qwen"
    SILICONFLOW = "siliconflow"
    DASHSCOPE = "dashscope"
    GENERIC = "generic"


@dataclass
class Message:
    """Unified message format across providers."""

    role: str
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None

    def to_openai(self) -> Dict[str, Any]:
        """Convert to OpenAI message format."""
        msg = {"role": self.role, "content": self.content}
        if self.name:
            msg["name"] = self.name
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg

    def to_anthropic(self) -> Dict[str, Any]:
        """Convert to Anthropic message format."""
        if self.role == "system":
            return {"role": "user", "content": f"[System]: {self.content}"}
        return {"role": self.role, "content": self.content}


@dataclass
class ToolCall:
    """Represents a tool call extracted from LLM response."""

    id: str
    name: str
    arguments: Dict[str, Any]

    @classmethod
    def from_openai(cls, tool_call: Dict) -> "ToolCall":
        """Create from OpenAI tool call format."""
        return cls(
            id=tool_call.get("id", ""),
            name=tool_call["function"]["name"],
            arguments=json.loads(tool_call["function"]["arguments"]),
        )

    @classmethod
    def from_anthropic(cls, tool_use: Dict) -> "ToolCall":
        """Create from Anthropic tool use format."""
        return cls(
            id=tool_use.get("id", ""),
            name=tool_use["name"],
            arguments=tool_use["input"],
        )


@dataclass
class LLMResponse:
    """Unified response format from LLM."""

    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    raw_response: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class BaseLLMAdapter(ABC):
    """Abstract base class for LLM adapters."""

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra_config = kwargs

    def _resolve_timeout(self, default: float) -> float:
        instance_timeout = getattr(self, "_model_timeout", None)
        if instance_timeout is not None:
            return instance_timeout
        configured_timeout = self.extra_config.get("timeout_override")
        return default if configured_timeout is None else configured_timeout

    @abstractmethod
    def convert_tools(self, tools: List[Type[BaseTool]]) -> Any:
        """Convert tool classes to provider-specific format."""
        pass

    @abstractmethod
    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        """Synchronous chat completion."""
        pass

    @abstractmethod
    async def achat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        """Asynchronous chat completion."""
        pass

    def stream_chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> Iterator[LLMResponse]:
        """Streaming chat completion (default: non-streaming fallback)."""
        yield self.chat(messages, tools, **kwargs)

    async def astream_chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> AsyncIterator[LLMResponse]:
        """Async streaming chat completion (default: non-streaming fallback)."""
        yield await self.achat(messages, tools, **kwargs)

    def _should_retry(self, error):
        return should_retry(error)

    def _get_retry_delay(self, attempt, error):
        return retry_delay(attempt, error)
