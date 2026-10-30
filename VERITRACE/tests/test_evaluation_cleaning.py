"""Long-article fallback never drops source characters or returns partial output."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def cleaner_type(monkeypatch):
    benchmark = Path(__file__).resolve().parents[2] / "deep_research_bench"
    monkeypatch.syspath_prepend(str(benchmark))
    spec = importlib.util.spec_from_file_location("evaluation_cleaner_contract", benchmark / "utils/clean_article.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ArticleCleaner


@pytest.mark.parametrize("boundary", [".", "。", "\n", "?"])
def test_chunk_boundaries_preserve_all_text_exactly_once(cleaner_type, boundary):
    original = "a" * 100 + boundary + "b" * 79 + "c" * 300
    calls = []

    class Echo:
        def generate(self, user_prompt, system_prompt):
            calls.append(user_prompt)
            return user_prompt

    cleaner = cleaner_type(Echo())
    cleaner._get_clean_prompt = lambda language: "{article}"
    assert cleaner.chunk_clean_article(original) == original
    assert "".join(calls) == original
    assert len(calls) == 2
    assert calls[0].endswith(boundary)


def test_failed_second_chunk_returns_failure_instead_of_a_partial_article(cleaner_type):
    class FailSecond:
        def generate(self, user_prompt, system_prompt):
            if user_prompt.startswith("b"):
                raise RuntimeError("Provider timeout")
            return user_prompt

    cleaner = cleaner_type(FailSecond())
    cleaner._get_clean_prompt = lambda language: "{article}"
    assert cleaner.chunk_clean_article("a" * 200 + "." + "b" * 399) is None
