"""Configured search limits reach the public factory and actual search loop."""

from types import SimpleNamespace

import pytest

import veritrace.agent as agents
from veritrace.agent import searcher as searcher_module
from veritrace.agent.config import SearcherConfig


def test_default_search_budgets_preserve_the_paper_profile(monkeypatch, scripted_llm):
    from veritrace.agent import config

    for name in ("PLANNER_MAX_TURN", "SEARCH_MAX_TURN", "SEARCH_MAX_RESULTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "DEFAULT_CONFIG", config._create_default_config())
    monkeypatch.setattr(agents, "VeritraceAgent", lambda **kwargs: kwargs)
    settings = agents.init_agent(search_engine="duckduckgo")
    assert settings["max_turn"] == 20
    assert settings["searcher_cfg"]["max_results"] == 10

    model = scripted_llm(["Unknown tool"] * 6 + ["Final research answer."])
    searcher = searcher_module.SearcherAgent(model)
    assert (searcher.max_turn, searcher.max_results) == (6, 10)
    monkeypatch.setattr(searcher, "_parse_tool_call_with_error", lambda text: ({"tool": "unsupported"}, None))
    result = searcher.search_and_summarize("Question", "Topic")
    assert result["content"] == "Final research answer."
    assert len(model.calls) == 7


@pytest.mark.parametrize("explicit, expected", [(None, 7), (3, 3)])
def test_factory_respects_result_limit_and_explicit_override(monkeypatch, explicit, expected):
    monkeypatch.setattr(agents, "get_searcher_config", lambda: SimpleNamespace(max_results=7))
    monkeypatch.setattr(agents, "VeritraceAgent", lambda **kwargs: kwargs)
    result = agents.init_agent(search_engine="duckduckgo", max_results=explicit)
    assert result["searcher_cfg"]["max_results"] == expected


@pytest.mark.parametrize("explicit, expected", [(None, 2), (1, 1)])
def test_search_round_limit_controls_calls_before_final_synthesis(monkeypatch, scripted_llm, explicit, expected):
    monkeypatch.setattr(searcher_module, "get_searcher_config", lambda: SearcherConfig(max_turn=2))
    model = scripted_llm(["Unknown tool"] * expected + ["Final research answer."])
    searcher = searcher_module.SearcherAgent(model, max_turn=explicit)
    monkeypatch.setattr(searcher, "_parse_tool_call_with_error", lambda text: ({"tool": "unsupported"}, None))
    result = searcher.search_and_summarize("Question", "Topic")
    assert result["content"] == "Final research answer."
    assert len(model.calls) == expected + 1


@pytest.mark.parametrize("max_turn", [0, -1])
def test_nonpositive_search_round_limit_is_rejected(max_turn, scripted_llm):
    with pytest.raises(ValueError, match="SEARCH_MAX_TURN"):
        searcher_module.SearcherAgent(scripted_llm([]), max_turn=max_turn)
