"""Offline tests by default; real provider calls require an explicit option."""

import copy
import json
import os
import socket
import threading
from collections import deque
from types import SimpleNamespace

import pytest


_original_dotenv_disabled = os.environ.get("PYTHON_DOTENV_DISABLED")
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# This manual experiment loads external datasets during import.
collect_ignore = ["tests/test_citation_verifier.py"]


def pytest_addoption(parser):
    parser.addoption(
        "--run-live-llm",
        action="store_true",
        default=False,
        help="Allow tests marked live_llm to use configured model providers.",
    )


def pytest_configure(config):
    if config.getoption("--run-live-llm"):
        if _original_dotenv_disabled is None:
            os.environ.pop("PYTHON_DOTENV_DISABLED", None)
        else:
            os.environ["PYTHON_DOTENV_DISABLED"] = _original_dotenv_disabled


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live-llm"):
        return
    skip = pytest.mark.skip(reason="Use --run-live-llm to enable real provider calls")
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def offline_network(monkeypatch, request):
    if request.node.get_closest_marker("live_llm") and request.config.getoption("--run-live-llm"):
        return

    def blocked(*args, **kwargs):
        raise RuntimeError("Network access is disabled in offline tests")

    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    local = threading.local()

    def socketpair(*args, **kwargs):
        # Windows asyncio creates its wakeup pipe using a loopback socket pair.
        local.creating_socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            local.creating_socketpair = False

    def connect(sock, address):
        if getattr(local, "creating_socketpair", False) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        return blocked()

    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


class ScriptedLLM:
    """Record requests and return deterministic responses at the provider boundary."""

    def __init__(self, responses):
        self.responses = responses if callable(responses) else deque(responses)
        self.calls = []
        self._lock = threading.Lock()

    def chat(self, messages, **kwargs):
        with self._lock:
            self.calls.append((copy.deepcopy(messages), copy.deepcopy(kwargs)))
            if callable(self.responses):
                response = self.responses(messages, **kwargs)
            else:
                if not self.responses:
                    raise AssertionError("Unexpected extra model call")
                response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            response = json.dumps(response, ensure_ascii=False)
        return SimpleNamespace(content=response)


@pytest.fixture
def scripted_llm():
    return ScriptedLLM
