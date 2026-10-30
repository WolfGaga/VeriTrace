import asyncio
import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest

from veritrace.agent.core.llm_adapter import DashScopeAdapter, Message, PayloadTooLargeError
from veritrace.agent.concurrency import cancellation_scope


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


def response(text="Completed"):
    message = SimpleNamespace(content=text, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_BACKUP_API_KEYS", raising=False)
    return DashScopeAdapter(model="test-model", api_key="test-primary", backup_api_keys=["test-backup"])


def install_clients(monkeypatch, adapter, handler):
    def client(key):
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: handler(key, kwargs),
                )
            )
        )

    def async_client(key):
        async def create(**kwargs):
            return handler(key, kwargs)

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(adapter, "_get_client_for_key", client)
    monkeypatch.setattr(adapter, "_get_async_client_for_key", async_client)


def chat(adapter, use_async):
    messages = [Message(role="user", content="Fixture question")]
    if use_async:
        return asyncio.run(adapter.achat(messages, temperature=0.2))
    return adapter.chat(messages, temperature=0.2)


@pytest.mark.parametrize("use_async", [False, True])
def test_rate_limit_rotates_key_and_preserves_request(monkeypatch, adapter, use_async):
    calls = []

    def handle(key, kwargs):
        calls.append((key, kwargs))
        if key == "test-primary":
            raise HTTPError(429, "rate limited")
        return response()

    install_clients(monkeypatch, adapter, handle)
    result = chat(adapter, use_async)
    assert result.content == "Completed"
    assert result.finish_reason == "stop"
    assert [key for key, _ in calls] == ["test-primary", "test-backup"]
    assert (
        calls[0][1]
        == calls[1][1]
        == {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Fixture question"}],
            "temperature": 0.2,
        }
    )


@pytest.mark.parametrize("use_async", [False, True])
def test_retry_budget_is_bounded_for_exhausted_keys(monkeypatch, adapter, use_async):
    calls = []
    delays = []

    def handle(key, kwargs):
        calls.append(key)
        raise HTTPError(503, "service unavailable")

    async def sleep(delay):
        delays.append(delay)

    install_clients(monkeypatch, adapter, handle)
    monkeypatch.setattr(adapter, "_get_retry_delay", lambda *args: 0)
    monkeypatch.setattr("veritrace.agent.core.llm_adapter.time.sleep", delays.append)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(HTTPError):
        chat(adapter, use_async)
    assert calls == ["test-primary", "test-backup", "test-primary", "test-primary", "test-backup"]
    assert delays == [0, 0]
    assert adapter._consecutive_exhaustions == 1


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize(
    "error,expected",
    [
        (HTTPError(400, "invalid request field"), HTTPError),
        (HTTPError(400, "<html>Request too large</html>"), PayloadTooLargeError),
    ],
)
def test_fatal_and_payload_errors_do_not_spend_backup_keys(monkeypatch, adapter, use_async, error, expected):
    calls = []

    def handle(key, kwargs):
        calls.append(key)
        raise error

    install_clients(monkeypatch, adapter, handle)
    with pytest.raises(expected):
        chat(adapter, use_async)
    assert calls == ["test-primary"]


@pytest.mark.parametrize("use_async", [False, True])
def test_confirmed_balance_failure_skips_key_after_verification(monkeypatch, adapter, use_async):
    calls = []

    def handle(key, kwargs):
        calls.append((key, kwargs.get("max_tokens")))
        if key == "test-primary":
            raise HTTPError(400, "Arrearage")
        return response()

    install_clients(monkeypatch, adapter, handle)
    assert chat(adapter, use_async).content == "Completed"
    assert calls == [("test-primary", None), ("test-primary", 1), ("test-backup", None)]


@pytest.mark.parametrize("use_async", [False, True])
def test_cancelled_session_makes_no_provider_call(monkeypatch, adapter, use_async):
    calls = []
    install_clients(monkeypatch, adapter, lambda key, kwargs: calls.append(key) or response())
    cancelled = threading.Event()
    cancelled.set()
    with cancellation_scope(cancelled), pytest.raises(CancelledError):
        chat(adapter, use_async)
    assert calls == []
