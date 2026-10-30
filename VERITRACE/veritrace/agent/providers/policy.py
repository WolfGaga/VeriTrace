"""Shared timeout, retry, and request concurrency policy."""

import logging
import os
import random
import threading

MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 30.0
REQUEST_TIMEOUT = 180.0


REQUEST_TIMEOUT_THINKING = 180.0
REQUEST_TIMEOUT_LARGE = 180.0
REQUEST_TIMEOUT_INSTRUCT = 180.0


REQUEST_TIMEOUT_PLUS = float(os.getenv("LLM_REQUEST_TIMEOUT_PLUS", "180"))


def _is_thinking_model(model: str) -> bool:
    """Check if model is a thinking/reasoning model that produces reasoning_content."""
    model_lower = model.lower()
    return "thinking" in model_lower or "-r1" in model_lower or "qwen3.5" in model_lower or "qwen3-" in model_lower


def _get_model_timeout(model: str) -> float:
    """Return appropriate timeout based on model name."""
    model_lower = model.lower()

    if model_lower.startswith("gpt-5.4"):
        return 300.0

    if model_lower.startswith("deepseek-v3-pro") or model_lower.startswith("deepseek-v4-pro"):
        return 300.0

    if model_lower.startswith("qwen3.7-max"):
        return 300.0

    if model_lower.startswith("glm-5"):
        return 300.0

    if model_lower.startswith("gemini-3"):
        return 300.0

    if model_lower == "qwen3.5-plus":
        return REQUEST_TIMEOUT_PLUS
    if _is_thinking_model(model):
        return REQUEST_TIMEOUT_THINKING
    if "80b" in model_lower or "70b" in model_lower or "72b" in model_lower:
        return REQUEST_TIMEOUT_LARGE
    return REQUEST_TIMEOUT_INSTRUCT


_LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "40"))
_llm_semaphore = threading.Semaphore(_LLM_MAX_CONCURRENCY)
logging.info(f"🔧 LLM global concurrency limit: {_LLM_MAX_CONCURRENCY}")


def _warn_on_degraded_response(content, finish_reason, model):
    """Log truncated or empty successful responses without retrying."""
    try:
        if finish_reason in ("length", "max_tokens"):
            logging.warning(
                f"[LLM] ⚠️ output truncated at token limit (finish_reason={finish_reason}, "
                f"model={model}, {len(content or '')} chars) — content may be incomplete"
            )
        if not (content or "").strip():
            logging.warning(
                f"[LLM] ⚠️ empty content in non-error response (finish_reason={finish_reason}, "
                f"model={model}) — downstream will treat this as a valid empty answer"
            )
    except Exception:
        pass


def should_retry(error: Exception) -> bool:
    """Check if error is retryable (429 rate limit, timeout, or balance insufficient)."""
    error_type = type(error).__name__

    if error_type == "RateLimitError":
        return True

    if "timeout" in error_type.lower() or "Timeout" in error_type:
        return True

    if hasattr(error, "status_code") and error.status_code == 401:
        return True

    if hasattr(error, "status_code") and error.status_code == 429:
        return True

    error_msg = str(error).lower()
    if "rate limit" in error_msg or "429" in error_msg or "too many requests" in error_msg:
        return True

    if "quota exceeded" in error_msg or "rate increased too quickly" in error_msg:
        return True
    if "timeout" in error_msg or "timed out" in error_msg:
        return True

    if (
        "30001" in error_msg
        or "30011" in error_msg
        or "balance is insufficient" in error_msg
        or "arrearage" in error_msg
    ):
        return True

    if hasattr(error, "status_code") and error.status_code in (500, 502, 503, 504):
        return True
    if "500" in error_msg or "502" in error_msg or "503" in error_msg or "504" in error_msg:
        if (
            "internal server error" in error_msg
            or "bad gateway" in error_msg
            or "service unavailable" in error_msg
            or "gateway timeout" in error_msg
        ):
            return True

    if "connectionerror" in error_type.lower() or "ConnectionError" in error_type:
        return True
    if "connection" in error_msg and ("refused" in error_msg or "reset" in error_msg or "aborted" in error_msg):
        return True
    return False


def retry_delay(attempt: int, error: Exception) -> float:
    """Calculate retry delay with exponential backoff + jitter."""

    if hasattr(error, "response") and error.response:
        retry_after = error.response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), MAX_DELAY)
            except ValueError:
                pass

    delay = BASE_DELAY * (2**attempt)

    delay += random.uniform(0, 1)
    return min(delay, MAX_DELAY)
