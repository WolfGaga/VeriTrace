"""Writer settings reach the same research pipeline through supported entry points."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

import veritrace.agent as agents
from veritrace.agent import veritrace_agent as engine
from veritrace.app import create_app, parse_arguments


@pytest.fixture(autouse=True)
def writer_environment(monkeypatch):
    for name in ("WRITER_MODEL", "SUMMARIZER_MODEL", "WRITER_PROVIDER", "SUMMARIZER_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


def benchmark_module(name):
    path = Path(__file__).resolve().parents[1] / "benchmark" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"writer_config_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("modern, expected", [(None, "report-model"), ("explicit-model", "explicit-model")])
def test_factory_accepts_report_model_alias_without_reading_model_environment(monkeypatch, modern, expected):
    monkeypatch.setenv("WRITER_MODEL", "environment-model")
    monkeypatch.setattr(agents, "VeritraceAgent", lambda **kwargs: kwargs)
    settings = agents.init_agent(search_engine="duckduckgo", writer_model=modern,
                                 summarizer_model="report-model", writer_mode="research")
    assert (settings["writer_model"] or settings["summarizer_model"]) == expected
    assert agents.init_agent(search_engine="duckduckgo")["writer_model"] is None


@pytest.mark.parametrize("modern, expected", [(None, "report-model"), ("explicit-model", "explicit-model")])
def test_constructor_preserves_dedicated_base_writer_provider(monkeypatch, modern, expected):
    calls = []

    def adapter(provider, model, **kwargs):
        calls.append((provider, model, kwargs))
        return SimpleNamespace(provider=provider, model=model)

    monkeypatch.setattr(engine, "create_adapter", adapter)
    monkeypatch.setenv("SUMMARIZER_PROVIDER", "base-provider")
    monkeypatch.setenv("WRITER_PROVIDER", "section-provider")
    monkeypatch.setenv("WRITER_MODEL", "environment-model")
    agent = engine.VeritraceAgent(llm_provider="main-provider", llm_model="main-model",
                                 writer_model=modern, summarizer_model="report-model",
                                 enable_cognitive_graph=False, writer_mode="research")
    assert (agent.writer_llm.provider, agent.writer_llm.model) == ("base-provider", expected)
    assert calls[-1][2] == {"timeout_override": 300.0}
    assert engine.VeritraceAgent(llm_provider="main-provider", enable_cognitive_graph=False).writer_llm is None


def test_constructor_preserves_positional_report_and_research_settings(monkeypatch):
    monkeypatch.setenv("WRITER_PROVIDER", "section-provider")
    monkeypatch.setattr(engine, "create_adapter",
                        lambda provider, model, **kwargs: SimpleNamespace(provider=provider, model=model))
    agent = engine.VeritraceAgent(
        "fixture", "main", None, None, None, None, None, "report", None, {},
        "Unused summary text", 7, "cn", "en", False, False, "research",
    )
    assert agent.writer_llm.model == "report"
    assert agent.writer_llm.provider == "fixture"
    assert (agent.max_turn, agent.language, agent.writer_language) == (7, "cn", "en")
    assert not agent.enable_cognitive_graph and not agent.include_search_refs


@pytest.mark.parametrize("mode", ["skip", "unsupported_mode", "independent"])
def test_report_mode_alias_does_not_enable_other_workflows(mode, monkeypatch):
    monkeypatch.setattr(engine, "create_adapter", lambda *args, **kwargs: object())
    with pytest.raises(ValueError, match="research"):
        engine.VeritraceAgent(writer_mode=mode, enable_cognitive_graph=False)


@pytest.mark.parametrize("modern, expected", [(None, "report-model"), ("explicit-model", "explicit-model")])
def test_api_model_alias_and_environment_precedence(monkeypatch, modern, expected):
    monkeypatch.setenv("SUMMARIZER_MODEL", "report-model")
    if modern:
        monkeypatch.setenv("WRITER_MODEL", modern)
    settings = parse_arguments([])
    assert (settings.writer_model or settings.summarizer_model) == expected
    settings = parse_arguments(["--summarizer-model", "cli-model"])
    assert settings.writer_model is None and settings.summarizer_model == "cli-model"
    assert parse_arguments(["--writer-model", "cli-model"]).writer_model == "cli-model"


@pytest.mark.parametrize("modern, expected", [(None, "report-model"), ("explicit-model", "explicit-model")])
def test_terminal_passes_resolved_writer_model_to_research(monkeypatch, modern, expected):
    runner = benchmark_module("run_terminal")
    monkeypatch.setenv("SUMMARIZER_MODEL", "report-model")
    if modern:
        monkeypatch.setenv("WRITER_MODEL", modern)
    seen = []

    class Agent:
        def __call__(self, question):
            yield engine.ResearchEvent("research.completed", {"article": "Research findings. " * 10})

    monkeypatch.setattr(runner, "init_agent", lambda **kwargs: seen.append(kwargs) or Agent())
    result = runner.run_single_query("Question", timeout=2)
    assert result["status"] == "success"
    assert (seen[0]["writer_model"] or seen[0]["summarizer_model"]) == expected


@pytest.mark.parametrize("modern, expected", [(None, "report-model"), ("explicit-model", "explicit-model")])
def test_replay_uses_resolved_writer_model(monkeypatch, modern, expected):
    runner = benchmark_module("run_rewrite")
    monkeypatch.setenv("SUMMARIZER_MODEL", "report-model")
    if modern:
        monkeypatch.setenv("WRITER_MODEL", modern)
    seen = []

    class Pipeline:
        def __init__(self, llm, *args):
            self.data = {"outline": [], "overall_theme": "Theme", "section_texts": {}}

        def run(self, *args):
            return "Report"

    monkeypatch.setattr(runner, "WritingPipeline", Pipeline)
    monkeypatch.setattr(runner, "create_adapter", lambda provider, model: seen.append((provider, model)) or object())
    result = runner.run_writing_pipeline(
        {"question": "Question", "cognitive_graph": {}, "evidence_table": {}, "ref2url": {}}, track_usage=False)
    assert result["article"] == "Report"
    assert seen[0][1] == expected


def test_dashscope_worker_remaps_report_alias_and_provider(monkeypatch, tmp_path):
    runner = benchmark_module("run_parallel")
    monkeypatch.setattr(runner, "VERITRACE_DIR", tmp_path)
    monkeypatch.setenv("SUMMARIZER_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")
    monkeypatch.setenv("SUMMARIZER_PROVIDER", "siliconflow")
    env = runner.build_env({"dashscope_key": "fixture-key"})
    assert env["SUMMARIZER_MODEL"] == "qwen3-30b-a3b-instruct-2507"
    assert env["SUMMARIZER_PROVIDER"] == env["WRITER_PROVIDER"] == "dashscope"


@pytest.mark.parametrize("mode, option, discovery", [
    ("search", "--input", "load_queries"),
    ("writer-only", "--evidence-dir", "discover_evidence_ids"),
])
def test_parallel_cli_aliases_select_research_or_evidence_replay(monkeypatch, tmp_path, mode, option, discovery):
    runner = benchmark_module("run_parallel")
    seen = []
    monkeypatch.setattr(runner, discovery, lambda path: seen.append(path) or [])
    monkeypatch.setattr(runner.sys, "argv", ["run_parallel", "--mode", mode, option, "fixture-input",
                                           "--output", str(tmp_path / "results.jsonl"),
                                           "--dashscope-keys", "fixture-key"])
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 1
    assert seen == ["fixture-input"]


def test_saved_evidence_entrypoints_share_one_pipeline():
    from benchmark import run_rewrite, run_writer_only

    assert run_writer_only.run_writing_pipeline is run_rewrite.run_writing_pipeline
    assert run_writer_only.load_evidence is run_rewrite.load_evidence
    assert run_writer_only.main is run_rewrite.main


@pytest.mark.parametrize("entrypoint", ["constructor", "factory", "api_env", "api_cli", "terminal"])
@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("dedicated_provider", [False, True])
def test_model_setting_origin_preserves_base_provider_through_entrypoints(
    monkeypatch, tmp_path, entrypoint, modern, dedicated_provider
):
    from sse_starlette.sse import AppStatus

    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    monkeypatch.setenv("LLM_PROVIDER", "main-provider")
    monkeypatch.setenv("SILICON_MODEL", "main-model")
    monkeypatch.setenv("WRITER_PROVIDER", "section-provider")
    if dedicated_provider:
        monkeypatch.setenv("SUMMARIZER_PROVIDER", "dedicated-provider")
    expected = "dedicated-provider" if dedicated_provider else "section-provider" if modern else "main-provider"
    calls = []

    def adapter(provider, model, **kwargs):
        result = SimpleNamespace(provider=provider, model=model)
        calls.append(result)
        return result

    def research(self, question, **kwargs):
        yield engine.ResearchEvent("research.completed", {"article": "Research findings. " * 10})

    monkeypatch.setattr(engine, "create_adapter", adapter)
    monkeypatch.setattr(engine.VeritraceAgent, "__call__", research)
    setting = "writer_model" if modern else "summarizer_model"
    model_kwargs = {setting: "report-model"}
    if entrypoint == "constructor":
        engine.VeritraceAgent(llm_provider="main-provider", enable_cognitive_graph=False, **model_kwargs)
    elif entrypoint == "factory":
        agents.init_agent(llm_provider="main-provider", search_engine="duckduckgo", **model_kwargs)
    elif entrypoint in ("api_env", "api_cli"):
        args = ["--search-engine", "duckduckgo", "--progress-log-interval", "0"]
        if entrypoint == "api_env":
            monkeypatch.setenv(setting.upper(), "report-model")
        else:
            monkeypatch.setenv("SUMMARIZER_MODEL" if modern else "WRITER_MODEL", "environment-model")
            args += ["--" + setting.replace("_", "-"), "report-model"]
        app = create_app(parse_arguments(args), database_path=tmp_path / "research.sqlite")

        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await client.post("/solve", json={"inputs": "Question"})

        response = asyncio.run(request())
        assert response.status_code == 200 and '"type": "research.completed"' in response.text
    else:
        monkeypatch.setenv(setting.upper(), "report-model")
        runner = benchmark_module("run_terminal")
        assert runner.run_single_query("Question", search_engine="duckduckgo", timeout=2)["status"] == "success"
    report_adapters = [item for item in calls if item.model == "report-model"]
    assert len(report_adapters) == 1
    assert report_adapters[0].provider == expected
