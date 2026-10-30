"""Structural changes preserve the established model instructions."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest


PROMPTS = json.loads((Path(__file__).parent / "fixtures" / "prompt_digests.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("record", PROMPTS, ids=lambda record: record["name"])
def test_prompt_text_matches_research_baseline(record):
    value = getattr(importlib.import_module(record["module"]), record["name"])
    assert hashlib.sha256(value.encode("utf-8")).hexdigest() == record["sha256"]
