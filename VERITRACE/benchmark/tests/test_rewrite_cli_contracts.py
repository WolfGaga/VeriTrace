"""Offline provider-selection and exit-status contracts for report rewrites."""

import json
from pathlib import Path
import runpy
import sys

import pytest

from benchmark import run_rewrite, run_terminal


class EvidenceFixture(dict):
    @property
    def records(self):
        return self


def write_evidence(path, question="Fixture question"):
    path.write_text(json.dumps({
        "question": question, "cognitive_graph": {"nodes": {}}, "evidence_table": {"1": []},
        "ref2url": {"1": {"url": "https://source.test", "title": "Fixture"}},
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("writer, general, writer_model, base, expected_provider, expected_model", [
    ("dashscope", "openai", "writer-model", "base-model", "dashscope", "writer-model"),
    (None, "anthropic", None, "base-model", "anthropic", "base-model"),
    (None, None, None, None, "siliconflow", "Qwen/Qwen3-32B"),
    ("", "openai", "", "base-model", "openai", "base-model"),
    ("", "", None, None, "siliconflow", "Qwen/Qwen3-32B"),
])
def test_terminal_rewrite_respects_writer_provider_and_existing_model_defaults(
    tmp_path, monkeypatch, writer, general, writer_model, base, expected_provider, expected_model,
):
    import veritrace.agent.core as core
    import veritrace.agent.writing as writing

    for name, value in {"WRITER_PROVIDER": writer, "LLM_PROVIDER": general,
                        "WRITER_MODEL": writer_model, "SILICON_MODEL": base}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    calls = {}
    adapter = object()

    def create_adapter(provider, model, **kwargs):
        calls["adapter"] = (provider, model, kwargs)
        return adapter

    class Pipeline:
        def __init__(self, writer_llm, provider, language, **kwargs):
            calls["pipeline"] = (writer_llm, provider, language, kwargs)

        def run(self, question, graph, evidence, references):
            calls["references"] = references
            return "Fixture report"

    monkeypatch.setattr(core, "create_adapter", create_adapter)
    monkeypatch.setattr(writing, "WritingPipeline", Pipeline)
    monkeypatch.setattr(run_terminal.EvidenceStore, "from_evidence_table", EvidenceFixture)
    path = write_evidence(tmp_path / "evidence.json")
    result = run_terminal.rewrite_from_evidence(str(path), lang="cn")
    assert result == {"status": "success", "error": None, "article": "Fixture report"}
    assert calls["adapter"] == (expected_provider, expected_model, {})
    assert calls["pipeline"] == (adapter, expected_provider, "cn", {"include_search_refs": True})
    assert calls["references"] == {1: {"url": "https://source.test", "title": "Fixture"}}


def writing_result():
    return {"article": "Fixture report", "outline": [], "overall_theme": "Fixture", "section_texts": {},
            "duration_seconds": 1, "rewrite_usage": {}}


@pytest.mark.parametrize("parallel", [1, 2])
def test_rewrite_main_failed_batch_keeps_success_output_and_missing_file_skip(tmp_path, monkeypatch, parallel):
    for qid in (1, 3, 4):
        write_evidence(tmp_path / f"evidence_{qid}.json", f"Q{qid}")
    called = []

    def pipeline(evidence, **kwargs):
        called.append(evidence["question"])
        if evidence["question"] == "Q3":
            raise RuntimeError("Fixture writing failure")
        return writing_result()

    monkeypatch.setattr(run_rewrite, "run_writing_pipeline", pipeline)
    monkeypatch.setattr(run_rewrite, "_load_query_map", lambda path: {})
    output = tmp_path / "reports.jsonl"
    code = run_rewrite.main(["--batch", "1,2,3,4", "--evidence-dir", str(tmp_path), "--output", str(output),
                                 "--parallel", str(parallel)])
    assert code == 1
    assert sorted(called) == ["Q1", "Q3", "Q4"]
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert {record["id"] for record in records} == {1, 4}
    assert all(record["status"] == "success" for record in records)
    assert all(set(record) == {"id", "prompt", "article", "cited_references", "status", "error", "duration_seconds", "usage_stats"}
               for record in records)


def test_rewrite_missing_batch_files_remain_successful_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(run_rewrite, "_load_query_map", lambda path: {})
    monkeypatch.setattr(run_rewrite, "run_writing_pipeline", lambda *args, **kwargs: pytest.fail("Skipped files must not run"))
    output = tmp_path / "reports.jsonl"
    assert run_rewrite.main(["--batch", "1,2", "--evidence-dir", str(tmp_path), "--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("fail, expected", [(False, 0), (True, 1)])
def test_rewrite_actual_entrypoint_propagates_exit_status(tmp_path, monkeypatch, fail, expected):
    import dotenv
    import veritrace.agent.core.llm_adapter as adapters
    import veritrace.agent.writing as writing

    class Pipeline:
        def __init__(self, *args, **kwargs):
            self.data = {"outline": [], "overall_theme": "Fixture", "section_texts": {}}

        def run(self, *args):
            if fail:
                raise RuntimeError("Fixture writing failure")
            return "Fixture report"

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(adapters, "create_adapter", lambda *args, **kwargs: object())
    monkeypatch.setattr(writing, "WritingPipeline", Pipeline)
    monkeypatch.setattr(run_rewrite.EvidenceStore, "from_evidence_table", EvidenceFixture)
    evidence = write_evidence(tmp_path / "evidence_1.json")
    output = tmp_path / "reports.jsonl"
    script = Path(run_rewrite.__file__)
    monkeypatch.setattr(sys, "argv", [str(script), "--evidence", str(evidence), "--output", str(output)])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(script), run_name="__main__")
    assert exit_info.value.code == expected
    records = output.read_text(encoding="utf-8").splitlines()
    assert len(records) == (0 if fail else 1)


def test_rewrite_argument_errors_keep_exit_two():
    with pytest.raises(SystemExit) as exit_info:
        run_rewrite.main([])
    assert exit_info.value.code == 2
