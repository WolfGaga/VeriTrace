"""Keep source indices consistent across searches, page reading and global citations."""

from veritrace.agent.searcher import SearcherAgent
from veritrace.agent.core.tools import _SEARCH_RESULTS_CACHE


def _agent():
    """SearcherAgent without running __init__ (the helper needs no init state)."""
    return SearcherAgent.__new__(SearcherAgent)


def _results(n, prefix):
    return [
        {
            "index": i,
            "url": f"https://{prefix}.example/{i}",
            "title": f"{prefix} title {i}",
            "snippet": f"{prefix} snippet {i}",
        }
        for i in range(n)
    ]


def test_base_zero_is_noop():
    a = _agent()
    raw = _results(3, "s1")
    text, new_base = a._rebase_search_indices(raw, "ck_s1", 0, "ORIGINAL_TEXT")
    # first search: indices untouched, text untouched, base advances by len
    assert [r["index"] for r in raw] == [0, 1, 2]
    assert text == "ORIGINAL_TEXT"
    assert new_base == 3


def test_second_search_is_offset_and_recached():
    a = _agent()
    raw2 = _results(4, "s2")
    _SEARCH_RESULTS_CACHE["ck_s2"] = raw2  # mimic the search tool caching it
    text, new_base = a._rebase_search_indices(raw2, "ck_s2", 3, "old")
    # indices shifted past the first search (which used 0..2)
    assert [r["index"] for r in raw2] == [3, 4, 5, 6]
    assert new_base == 7
    # re-formatted display uses the shifted indices the LLM will cite
    assert "[3]" in text and "[6]" in text
    assert "[0]" not in text
    # PageSelectTool resolves by the `index` field from this cache_key
    cached = _SEARCH_RESULTS_CACHE["ck_s2"]
    idx_field = {r["index"] for r in cached}
    assert idx_field == {3, 4, 5, 6}


def test_two_searches_never_collide_in_ref2url():
    """Preserve every source when merging two searches in one task."""
    a = _agent()
    ref2url = {}
    base = 0

    # search 1
    raw1 = _results(5, "s1")
    _SEARCH_RESULTS_CACHE["ck1"] = raw1
    _, base = a._rebase_search_indices(raw1, "ck1", base, "t1")
    for r in raw1:
        ref2url[r["index"]] = {"url": r["url"], "title": r["title"]}

    keys_after_s1 = set(ref2url)

    # search 2
    raw2 = _results(4, "s2")
    _SEARCH_RESULTS_CACHE["ck2"] = raw2
    _, base = a._rebase_search_indices(raw2, "ck2", base, "t2")
    for r in raw2:
        # no collision: these keys must be brand new
        assert r["index"] not in keys_after_s1
        ref2url[r["index"]] = {"url": r["url"], "title": r["title"]}

    # 5 + 4 distinct entries, every URL preserved
    assert len(ref2url) == 9
    assert ref2url[0]["url"] == "https://s1.example/0"  # first search intact
    assert ref2url[5]["url"] == "https://s2.example/0"  # second search appended
    urls = {v["url"] for v in ref2url.values()}
    assert len(urls) == 9


def test_empty_results_advances_base_by_zero():
    a = _agent()
    text, new_base = a._rebase_search_indices([], "ck_empty", 7, "txt")
    assert new_base == 7
    assert text == "txt"


def test_full_citation_chain_no_scramble(monkeypatch):
    """Preserve source identity through cached page selection and global citation allocation."""
    import threading
    from veritrace.agent.core.tools import PageSelectTool, _SEARCH_RESULTS_CACHE
    from veritrace.agent.veritrace_agent import VeritraceAgent

    a = _agent()
    ref2url = {}
    base = 0

    raw1 = _results(5, "s1")
    _SEARCH_RESULTS_CACHE["ck1"] = raw1
    _, base = a._rebase_search_indices(raw1, "ck1", base, "t1")
    for r in raw1:
        ref2url[r["index"]] = {"url": r["url"], "title": r["title"], "snippet": r.get("snippet", "")}

    raw2 = _results(4, "s2")
    _SEARCH_RESULTS_CACHE["ck2"] = raw2
    _, base = a._rebase_search_indices(raw2, "ck2", base, "t2")
    for r in raw2:
        ref2url[r["index"]] = {"url": r["url"], "title": r["title"], "snippet": r.get("snippet", "")}

    # LINK 1 — real PageSelectTool resolves OFFSET indices to the RIGHT pages.
    monkeypatch.setattr(PageSelectTool, "_fetch_page", lambda self, url: f"CONTENT::{url}")
    tool = PageSelectTool(indices=[5, 7], cache_key="ck2")  # = search-2 local 0 and 2
    pages = tool.execute().output["pages_raw"]
    got = {(p["index"], p["url"]) for p in pages}
    assert got == {(5, "https://s2.example/0"), (7, "https://s2.example/2")}, got

    # LINK 2 — evidence url is taken from ref2url[page_idx]; must match the
    # fetched page's url for every offset index (the way graph.py builds it).
    for p in pages:
        assert ref2url[p["index"]]["url"] == p["url"]

    # LINK 3 — the global allocator must map a final answer citing BOTH
    # searches' offset indices to DISTINCT, CORRECT urls. [[2]] (search 1) and
    # [[7]] (search 2) are exactly the pair that collided before the fix.
    agent = VeritraceAgent.__new__(VeritraceAgent)
    agent._global_url_to_idx = {}
    agent._global_references_url = {}
    agent._global_ptr = 0
    agent._global_ref_lock = threading.Lock()
    answer = "From the early search [[2]] and the later search [[7]]."
    new_answer, lmap = agent._allocate_globals_for_task("task", answer, ref2url)
    g2, g7 = lmap[2], lmap[7]
    assert g2 != g7
    assert agent._global_references_url[g2]["url"] == "https://s1.example/2"  # search 1
    assert agent._global_references_url[g7]["url"] == "https://s2.example/2"  # search 2 (offset 7)
    assert f"[[{g2}]]" in new_answer and f"[[{g7}]]" in new_answer
