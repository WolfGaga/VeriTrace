"""Supported LLM providers and adapter factory."""

from typing import Optional, Union
from .types import BaseLLMAdapter, LLMProvider, LLMResponse, Message, PayloadTooLargeError, ToolCall
from .openai import OpenAIAdapter, QwenAdapter
from .anthropic import AnthropicAdapter
from .generic import GenericPromptAdapter
from .rotating import SiliconFlowAdapter, DashScopeAdapter

__all__ = [
    "BaseLLMAdapter",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "PayloadTooLargeError",
    "ToolCall",
    "OpenAIAdapter",
    "QwenAdapter",
    "AnthropicAdapter",
    "GenericPromptAdapter",
    "SiliconFlowAdapter",
    "DashScopeAdapter",
    "create_adapter",
]


def create_adapter(
    provider: Union[str, LLMProvider],
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs,
) -> BaseLLMAdapter:
    """Factory function to create the appropriate LLM adapter."""
    if isinstance(provider, str):
        provider = LLMProvider(provider.lower())

    adapters = {
        LLMProvider.OPENAI: OpenAIAdapter,
        LLMProvider.ANTHROPIC: AnthropicAdapter,
        LLMProvider.QWEN: QwenAdapter,
        LLMProvider.SILICONFLOW: SiliconFlowAdapter,
        LLMProvider.DASHSCOPE: DashScopeAdapter,
        LLMProvider.GENERIC: GenericPromptAdapter,
    }

    adapter_cls = adapters.get(provider)
    if not adapter_cls:
        raise ValueError(f"Unknown provider: {provider}")

    return adapter_cls(model=model, api_key=api_key, base_url=base_url, **kwargs)
