"""Offline provider construction and SDK timeout regression tests."""

from types import SimpleNamespace
import inspect
import sys

import pytest

from veritrace.agent.providers import create_adapter
from veritrace.agent import init_agent


PROVIDERS = ("openai", "anthropic", "qwen", "siliconflow", "dashscope", "generic")


def test_public_agent_factory_has_one_report_pipeline():
    assert list(inspect.signature(init_agent).parameters) == [
        "lang", "writer_lang", "llm_provider", "llm_model", "searcher_model", "reader_model",
        "prefilter_model", "quality_model", "cognitive_model", "summarizer_model", "llm_api_key",
        "search_engine", "max_results", "use_async", "include_search_refs", "writer_mode", "writer_model",
    ]
    assert inspect.signature(init_agent).parameters["writer_model"].kind == inspect.Parameter.KEYWORD_ONLY


@pytest.fixture(autouse=True)
def isolated_provider_environment(monkeypatch):
    for name in ("SILICON_API_KEY", "SILICON_BACKUP_API_KEYS", "DASHSCOPE_API_KEY", "DASHSCOPE_BACKUP_API_KEYS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sdk_constructors(monkeypatch):
    calls = []

    def constructor(kind):
        def create(**kwargs):
            client = SimpleNamespace(kind=kind, options=kwargs)
            calls.append(client)
            return client

        return create

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(
            OpenAI=constructor("openai-sync"),
            AsyncOpenAI=constructor("openai-async"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(
            Anthropic=constructor("anthropic-sync"),
            AsyncAnthropic=constructor("anthropic-async"),
        ),
    )
    return calls


@pytest.mark.parametrize("provider", PROVIDERS)
def test_every_factory_route_constructs(provider):
    adapter = create_adapter(provider, "synthetic-model", api_key="synthetic-key")
    assert adapter.model == "synthetic-model"
    assert adapter.api_key == "synthetic-key"


@pytest.mark.parametrize("base_url", [None, "https://synthetic.invalid/v1"])
def test_qwen_factory_forwards_one_base_url(base_url, sdk_constructors):
    adapter = create_adapter("qwen", "synthetic-model", api_key="synthetic-key", base_url=base_url)
    expected = "https://dashscope.aliyuncs.com/compatible-mode/v1" if base_url is None else base_url
    assert adapter.base_url == expected
    assert adapter._get_client().options["base_url"] == expected
    assert adapter._get_async_client().options["base_url"] == expected


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "constructor_timeout,instance_timeout,expected",
    [
        (None, None, 180.0),
        (300.0, None, 300.0),
        (300.0, 450.0, 450.0),
        (None, 0.0, 0.0),
        (0.0, None, 0.0),
    ],
)
def test_sdk_timeout_precedence(provider, constructor_timeout, instance_timeout, expected, sdk_constructors):
    adapter = create_adapter(provider, "synthetic-model", api_key="synthetic-key", timeout_override=constructor_timeout)
    if instance_timeout is not None:
        adapter._model_timeout = instance_timeout
    sync_client = adapter._get_client()
    async_client = adapter._get_async_client()
    assert sync_client.options["timeout"] == expected
    assert async_client.options["timeout"] == expected
    assert adapter._get_client() is sync_client
    assert adapter._get_async_client() is async_client
    assert len(sdk_constructors) == 2


@pytest.mark.parametrize("provider", PROVIDERS)
def test_existing_model_defaults_are_unchanged(provider, sdk_constructors):
    adapter = create_adapter(provider, "deepseek-v4-pro", api_key="synthetic-key")
    expected = 180.0 if provider == "anthropic" else 300.0
    assert adapter._get_client().options["timeout"] == expected
    assert adapter._get_async_client().options["timeout"] == expected


@pytest.mark.parametrize("provider", ["siliconflow", "dashscope"])
def test_rotating_provider_state_and_key_clients_are_preserved(provider, sdk_constructors):
    adapter = create_adapter(
        provider,
        "deepseek-v4-pro",
        api_key="synthetic-key",
        backup_api_keys=["synthetic-backup"],
        timeout_override=315.0,
    )
    assert adapter._model_timeout == 315.0
    assert adapter._all_api_keys == ["synthetic-key", "synthetic-backup"]
    assert adapter._build_key_sequence(2) == [0, 1, 0, 0, 1]
    for getter in (adapter._get_client_for_key, adapter._get_async_client_for_key):
        client = getter("synthetic-backup")
        assert client.options["api_key"] == "synthetic-backup"
        assert client.options["timeout"] == 315.0
        assert client.options["max_retries"] == 0
