"""Declared research request options retain their original scope."""

import asyncio

import httpx
import pytest

from veritrace.app import create_app, parse_arguments
from veritrace.agent.runtime.events import ResearchEvent


@pytest.fixture(autouse=True)
def isolated_sse_lifetime(monkeypatch):
    from sse_starlette.sse import AppStatus

    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)


@pytest.mark.parametrize("writer_mode", [None, "", "research"])
@pytest.mark.parametrize("agent_cfg", [{}, {"llm_model": "ignored-model", "writer_mode": "skip"}])
def test_declared_request_options_preserve_research_and_server_configuration(tmp_path, writer_mode, agent_cfg):
    seen = []

    class Agent:
        def __call__(self, question, session_id):
            seen.append((question, session_id))
            yield ResearchEvent("research.completed", {"article": "Research report"})

    def factory(**options):
        seen.append(options)
        return Agent()

    settings = parse_arguments(["--llm-provider", "fixture", "--llm-model", "server-model"])
    app = create_app(settings, database_path=tmp_path / "runs.sqlite", agent_factory=factory)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/solve", json={"inputs": "Question", "session_id": 17,
                                                     "agent_cfg": agent_cfg, "writer_mode": writer_mode})

    response = asyncio.run(request())
    assert response.status_code == 200
    assert "research.completed" in response.text
    assert seen[0]["llm_model"] == "server-model"
    assert "agent_cfg" not in seen[0]
    assert seen[-1] == ("Question", 17)


@pytest.mark.parametrize("writer_mode", ["skip", "unsupported_mode", "independent"])
def test_request_cannot_select_excluded_writing_workflows(tmp_path, writer_mode):
    seen = []
    app = create_app(parse_arguments([]), database_path=tmp_path / "runs.sqlite",
                     agent_factory=lambda **options: seen.append(options))

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/solve", json={"inputs": "Question", "writer_mode": writer_mode})

    response = asyncio.run(request())
    assert response.status_code == 422
    assert not seen
