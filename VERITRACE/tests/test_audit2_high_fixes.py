"""Keep search caches isolated and report degraded model responses."""

import logging

from veritrace.agent.searcher import SearcherAgent
from veritrace.agent.core.tools import _SEARCH_RESULTS_CACHE, WebSearchTool
from veritrace.agent.core.base_tool import ToolResult


def _agent():
    return SearcherAgent.__new__(SearcherAgent)


# #2 — _execute_search isolates results (concurrency-safe with C1 rebase)


def test_execute_search_isolates_results(monkeypatch):
    a = _agent()
    a.search_engine = "google"
    a.max_results = 5

    # The shared engine:query cache entry that WebSearchTool would populate.
    shared = [{"index": i, "url": f"https://q.example/{i}", "title": f"T{i}", "snippet": "s"} for i in range(3)]
    _SEARCH_RESULTS_CACHE["google:Q"] = shared
    monkeypatch.setattr(
        WebSearchTool,
        "execute",
        lambda self: ToolResult(success=True, output={"cache_key": "google:Q"}),
    )

    # Two concurrent tasks issuing the SAME query.
    t1, k1, r1 = a._execute_search("Q")
    t2, k2, r2 = a._execute_search("Q")

    # Each call gets a unique key, distinct from the shared engine:query key.
    assert k1 != k2
    assert k1 != "google:Q" and k2 != "google:Q"
    # Private copies — not the same list, not the same dicts, not the shared objects.
    assert r1 is not r2
    assert r1[0] is not r2[0]
    assert r1[0] is not shared[0]
    # Each call's results are cached under its own key (so select_pages resolves them).
    assert _SEARCH_RESULTS_CACHE.get(k1) is r1
    assert _SEARCH_RESULTS_CACHE.get(k2) is r2

    # The exact corruption C1+race caused: rebase task1 in place; task2 and the
    # shared entry must be UNTOUCHED.
    a._rebase_search_indices(r1, k1, 10, t1)
    assert [r["index"] for r in r1] == [10, 11, 12]
    assert [r["index"] for r in r2] == [0, 1, 2]
    assert [r["index"] for r in shared] == [0, 1, 2]


# #6/#7 — _warn_on_degraded_response is log-only (no behavioral change)


def test_warn_on_truncation(caplog):
    from veritrace.agent.core.llm_adapter import _warn_on_degraded_response

    with caplog.at_level(logging.WARNING):
        _warn_on_degraded_response("partial output", "length", "qwen3.5-27b")
    assert any("truncated" in r.message for r in caplog.records)


def test_warn_on_empty_content(caplog):
    from veritrace.agent.core.llm_adapter import _warn_on_degraded_response

    with caplog.at_level(logging.WARNING):
        _warn_on_degraded_response("", "stop", "qwen3.5-27b")
    assert any("empty content" in r.message for r in caplog.records)


def test_warn_on_anthropic_max_tokens(caplog):
    from veritrace.agent.core.llm_adapter import _warn_on_degraded_response

    with caplog.at_level(logging.WARNING):
        _warn_on_degraded_response("partial", "max_tokens", "claude")
    assert any("truncated" in r.message for r in caplog.records)


def test_no_warning_on_normal_response(caplog):
    from veritrace.agent.core.llm_adapter import _warn_on_degraded_response

    with caplog.at_level(logging.WARNING):
        _warn_on_degraded_response("a complete answer", "stop", "qwen3.5-27b")
    assert not caplog.records


def test_warn_never_raises_on_bad_input():
    from veritrace.agent.core.llm_adapter import _warn_on_degraded_response

    # Must never break the response path even on unexpected types.
    _warn_on_degraded_response(None, None, None)
    _warn_on_degraded_response(123, object(), 456)
