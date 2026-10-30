"""SiliconFlow and DashScope key rotation and provider clients."""

import logging
import time
from typing import List, Optional, Type
from ..core.base_tool import BaseTool
from ..concurrency import check_cancelled
from .types import Message, LLMResponse, ToolCall, PayloadTooLargeError
from .openai import OpenAIAdapter
from .policy import _get_model_timeout, _warn_on_degraded_response, _llm_semaphore


class SiliconFlowAdapter(OpenAIAdapter):
    """Adapter for the SiliconFlow API."""

    def __init__(
        self,
        model: str = "Qwen/Qwen2.5-7B-Instruct",
        api_key: Optional[str] = None,
        backup_api_keys: Optional[List[str]] = None,
        base_url: Optional[str] = None,
        timeout_override: Optional[float] = None,
        **kwargs,
    ):
        import os

        api_key = api_key or os.environ.get("SILICON_API_KEY")

        if not api_key:
            raise ValueError("SiliconFlow API key required. Set SILICON_API_KEY env var or pass api_key parameter.")

        self._all_api_keys = [api_key]
        if backup_api_keys:
            self._all_api_keys.extend(backup_api_keys)

        backup_env = os.environ.get("SILICON_BACKUP_API_KEYS", "")
        if backup_env:
            self._all_api_keys.extend([k.strip() for k in backup_env.split(",") if k.strip()])

        self._model_timeout = timeout_override if timeout_override is not None else _get_model_timeout(model)

        import threading

        self._consecutive_exhaustions = 0
        self._exhaustion_lock = threading.Lock()
        self._max_consecutive_exhaustions = 10

        self._provider_name = "SiliconFlow"

        super().__init__(
            model=model,
            api_key=api_key,
            base_url="https://api.siliconflow.cn/v1",
            **kwargs,
        )

        logging.info(
            f"🔑 {self._provider_name} initialized: model={model}, "
            f"{len(self._all_api_keys)} API key(s), "
            f"timeout={self._model_timeout:.0f}s ({'thinking' if self._model_timeout > 60 else 'instruct'})"
        )

    def _get_client_for_key(self, api_key: str):
        """Create a new OpenAI client for a specific API key (thread-safe)."""
        from openai import OpenAI

        return OpenAI(
            api_key=api_key,
            base_url="https://api.siliconflow.cn/v1",
            timeout=self._model_timeout,
            max_retries=0,
        )

    def _get_async_client_for_key(self, api_key: str):
        """Create a new async OpenAI client for a specific API key (thread-safe)."""
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.siliconflow.cn/v1",
            timeout=self._model_timeout,
            max_retries=0,
        )

    def _get_client(self):
        """Override parent: use model-specific timeout."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url="https://api.siliconflow.cn/v1",
                timeout=self._model_timeout,
                max_retries=0,
            )
        return self._client

    def _get_async_client(self):
        """Override parent: use model-specific timeout."""
        if self._async_client is None:
            from openai import AsyncOpenAI

            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url="https://api.siliconflow.cn/v1",
                timeout=self._model_timeout,
                max_retries=0,
            )
        return self._async_client

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        """Thread-safe chat with key rotation and per-call semaphore."""
        request_kwargs = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            **kwargs,
        }

        if tools:
            request_kwargs["tools"] = self.convert_tools(tools)
            request_kwargs["tool_choice"] = kwargs.get("tool_choice", "auto")

        return self._chat_with_retry(request_kwargs)

    @staticmethod
    def _build_key_sequence(n_keys: int) -> list:
        """Build retry key sequence: main key (index 0) prioritized."""
        if n_keys == 1:
            return [0, 0, 0]
        seq = list(range(n_keys))
        seq.append(0)
        seq.extend(range(n_keys))
        return seq

    @staticmethod
    def _looks_like_balance_error(error: Exception) -> bool:
        """Check if error message suggests balance-insufficient (may be transient)."""
        error_msg = str(error).lower()
        return "balance is insufficient" in error_msg or "30001" in error_msg or "arrearage" in error_msg

    def _is_balance_error(self, error: Exception, key: str) -> bool:
        """Verify balance error with a lightweight ping to avoid false positives."""
        if not self._looks_like_balance_error(error):
            return False

        try:
            client = self._get_client_for_key(key)
            check_cancelled()
            client.chat.completions.create(
                model="qwen-turbo",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )

            logging.info("[%s] Balance check succeeded; retrying normally", self._provider_name)
            return False
        except Exception as verify_err:
            if self._looks_like_balance_error(verify_err):
                return True

            logging.warning(
                "[%s] Balance check inconclusive (%s); keeping key available",
                self._provider_name,
                type(verify_err).__name__,
            )
            return False

    def _chat_with_retry(self, request_kwargs: dict) -> LLMResponse:
        """Thread-safe retry with key rotation. All state is local."""
        all_keys = self._all_api_keys
        n_keys = len(all_keys)
        key_sequence = self._build_key_sequence(n_keys)
        max_attempts = len(key_sequence)
        model_short = self.model.split("/")[-1] if "/" in self.model else self.model

        dead_keys = set()
        last_error = None
        for attempt, key_index in enumerate(key_sequence):
            if key_index in dead_keys:
                continue

            current_key = all_keys[key_index]

            logging.info(
                f"🔄 [{self._provider_name}] attempt {attempt + 1}/{max_attempts} "
                f"(key #{key_index + 1}/{n_keys}, "
                f"model={model_short}, timeout={self._model_timeout:.0f}s)"
            )

            client = self._get_client_for_key(current_key)

            retryable_error = None
            _llm_semaphore.acquire()
            try:
                check_cancelled()
                response = client.chat.completions.create(**request_kwargs)
            except Exception as e:
                last_error = e

                if hasattr(e, "status_code") and e.status_code == 400 and "<html>" in str(e).lower():
                    raise PayloadTooLargeError("Request payload too large (ALB 400)") from e
                if not self._should_retry(e):
                    raise
                retryable_error = e
            finally:
                _llm_semaphore.release()

            if retryable_error is None:
                with self._exhaustion_lock:
                    self._consecutive_exhaustions = 0
                return self._parse_response(response)

            if self._is_balance_error(retryable_error, current_key):
                dead_keys.add(key_index)
                logging.warning(
                    "[%s] Key #%d has insufficient balance; skipping this key", self._provider_name, key_index + 1
                )

                if len(dead_keys) >= n_keys:
                    logging.error("[%s] All %d keys have insufficient balance", self._provider_name, n_keys)
                    raise retryable_error
                continue

            logging.warning(
                "[%s] Retryable %s on key #%d, attempt %d/%d: %s",
                self._provider_name,
                type(retryable_error).__name__,
                key_index + 1,
                attempt + 1,
                max_attempts,
                str(retryable_error)[:300],
            )

            if attempt + 1 < max_attempts and key_sequence[attempt + 1] == 0:
                delay = self._get_retry_delay(1, retryable_error)
                logging.warning("[%s] All keys tried; waiting %.1fs before next rotation", self._provider_name, delay)
                time.sleep(delay)

        with self._exhaustion_lock:
            self._consecutive_exhaustions += 1
            count = self._consecutive_exhaustions
        if count >= self._max_consecutive_exhaustions:
            logging.error("[%s] %d consecutive retry exhaustions; aborting query", self._provider_name, count)
            raise RuntimeError(
                f"API persistently unavailable: {count} consecutive full-retry failures. Last error: {last_error}"
            )
        logging.error(
            "[%s] All %d attempts exhausted for model=%s (consecutive: %d/%d)",
            self._provider_name,
            max_attempts,
            model_short,
            count,
            self._max_consecutive_exhaustions,
        )
        raise last_error

    async def achat(
        self,
        messages: List[Message],
        tools: Optional[List[Type[BaseTool]]] = None,
        **kwargs,
    ) -> LLMResponse:
        """Thread-safe async chat with key rotation."""
        import asyncio

        request_kwargs = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            **kwargs,
        }

        if tools:
            request_kwargs["tools"] = self.convert_tools(tools)
            request_kwargs["tool_choice"] = kwargs.get("tool_choice", "auto")

        all_keys = self._all_api_keys
        n_keys = len(all_keys)
        key_sequence = self._build_key_sequence(n_keys)
        max_attempts = len(key_sequence)
        model_short = self.model.split("/")[-1] if "/" in self.model else self.model

        dead_keys = set()
        last_error = None
        for attempt, key_index in enumerate(key_sequence):
            if key_index in dead_keys:
                continue

            current_key = all_keys[key_index]

            logging.info(
                f"🔄 [{self._provider_name}] async attempt {attempt + 1}/{max_attempts} "
                f"(key #{key_index + 1}/{n_keys}, model={model_short}, timeout={self._model_timeout:.0f}s)"
            )

            client = self._get_async_client_for_key(current_key)

            try:
                check_cancelled()
                response = await client.chat.completions.create(**request_kwargs)
                with self._exhaustion_lock:
                    self._consecutive_exhaustions = 0
                return self._parse_response(response)
            except Exception as e:
                last_error = e
                if hasattr(e, "status_code") and e.status_code == 400 and "<html>" in str(e).lower():
                    raise PayloadTooLargeError("Request payload too large (ALB 400)") from e
                if not self._should_retry(e):
                    raise

                if self._is_balance_error(e, current_key):
                    dead_keys.add(key_index)
                    logging.warning(
                        "[%s] Key #%d has insufficient balance; skipping this key", self._provider_name, key_index + 1
                    )
                    if len(dead_keys) >= n_keys:
                        logging.error("[%s] All %d keys have insufficient balance", self._provider_name, n_keys)
                        raise
                    continue

                logging.warning(
                    f"⚠️ [{self._provider_name}] async retryable error on key #{key_index + 1}, "
                    f"attempt {attempt + 1}/{max_attempts}: {type(e).__name__}: {str(e)[:200]}"
                )

                if attempt + 1 < max_attempts and key_sequence[attempt + 1] == 0:
                    delay = self._get_retry_delay(1, e)
                    logging.warning(f"⏳ All keys tried, waiting {delay:.1f}s before next rotation...")
                    await asyncio.sleep(delay)
                continue

        with self._exhaustion_lock:
            self._consecutive_exhaustions += 1
            count = self._consecutive_exhaustions
        if count >= self._max_consecutive_exhaustions:
            logging.error("[%s] %d consecutive retry exhaustions; aborting query", self._provider_name, count)
            raise RuntimeError(
                f"API persistently unavailable: {count} consecutive full-retry failures. Last error: {last_error}"
            )
        logging.error(
            "[%s] All %d async attempts exhausted for model=%s (consecutive: %d/%d)",
            self._provider_name,
            max_attempts,
            model_short,
            count,
            self._max_consecutive_exhaustions,
        )
        raise last_error

    def _parse_response(self, response) -> LLMResponse:
        """Parse SiliconFlow/DashScope responses, including reasoning_content."""
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


class DashScopeAdapter(SiliconFlowAdapter):
    """Adapter for Alibaba Cloud DashScope."""

    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(
        self,
        model: str = "qwen3-32b",
        api_key: Optional[str] = None,
        backup_api_keys: Optional[List[str]] = None,
        base_url: Optional[str] = None,
        timeout_override: Optional[float] = None,
        **kwargs,
    ):
        import os

        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")

        if not api_key:
            raise ValueError("DashScope API key required. Set DASHSCOPE_API_KEY env var or pass api_key parameter.")

        self._all_api_keys = [api_key]
        if backup_api_keys:
            self._all_api_keys.extend(backup_api_keys)
        backup_env = os.environ.get("DASHSCOPE_BACKUP_API_KEYS", "")
        if backup_env:
            self._all_api_keys.extend([k.strip() for k in backup_env.split(",") if k.strip()])

        self._model_timeout = timeout_override if timeout_override is not None else _get_model_timeout(model)
        self._provider_name = "DashScope"

        import threading

        self._consecutive_exhaustions = 0
        self._exhaustion_lock = threading.Lock()
        self._max_consecutive_exhaustions = 10

        OpenAIAdapter.__init__(
            self,
            model=model,
            api_key=api_key,
            base_url=self.DASHSCOPE_BASE_URL,
            **kwargs,
        )

        logging.info(
            f"🔑 DashScope initialized: model={model}, "
            f"{len(self._all_api_keys)} API key(s), "
            f"timeout={self._model_timeout:.0f}s"
        )

    def _get_client_for_key(self, api_key: str):
        from openai import OpenAI

        return OpenAI(
            api_key=api_key,
            base_url=self.DASHSCOPE_BASE_URL,
            timeout=self._model_timeout,
            max_retries=0,
        )

    def _get_async_client_for_key(self, api_key: str):
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=api_key,
            base_url=self.DASHSCOPE_BASE_URL,
            timeout=self._model_timeout,
            max_retries=0,
        )

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.DASHSCOPE_BASE_URL,
                timeout=self._model_timeout,
                max_retries=0,
            )
        return self._client

    def _get_async_client(self):
        if self._async_client is None:
            from openai import AsyncOpenAI

            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.DASHSCOPE_BASE_URL,
                timeout=self._model_timeout,
                max_retries=0,
            )
        return self._async_client
