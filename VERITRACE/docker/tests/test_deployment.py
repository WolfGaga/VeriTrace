"""Deployment contracts validated without Docker, registries, or model services."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("veritrace_deploy_cli", ROOT / "docker/veritrace_deploy/__main__.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def arguments(*options):
    return deploy.parse_args(["--project-root", str(ROOT), *options])


def test_cloud_preserves_environment_defaults_and_data_volumes():
    compose = deploy.build_compose(arguments())
    backend = compose["services"]["backend"]
    assert "--llm-provider" not in backend["command"]
    assert "--llm-model" not in backend["command"]
    assert "--search-engine" not in backend["command"]
    assert backend["environment"] == {"PYTHONUNBUFFERED": "1"}
    assert "conversation-data:/app/database" in backend["volumes"]
    assert "research-exports:/app/eval_data" in backend["volumes"]
    assert "research-logs:/app/logs" in backend["volumes"]
    assert "research-logs" in compose["volumes"]
    assert "model" not in compose["services"]


def test_explicit_cloud_configuration_matches_api_parser(monkeypatch):
    from veritrace.app import parse_arguments

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    compose = deploy.build_compose(arguments("--provider", "dashscope", "--model", "fixture-model", "--search-engine", "serper"))
    parsed = parse_arguments(compose["services"]["backend"]["command"][3:])
    assert (parsed.llm_provider, parsed.llm_model, parsed.search_engine) == ("dashscope", "fixture-model", "serper")


@pytest.mark.parametrize("provider", ["generic", "openai"])
def test_local_roles_cannot_inherit_cloud_provider_or_model(provider, tmp_path, monkeypatch):
    from veritrace.app import parse_arguments

    env_file = tmp_path / "cloud.env"
    env_file.write_text("WRITER_PROVIDER=anthropic\nREADER_MODEL=cloud-reader\n", encoding="utf-8")
    compose = deploy.build_compose(arguments("--mode", "local", "--provider", provider, "--model", "local-model",
                                            "--env-file", str(env_file)))
    backend = compose["services"]["backend"]
    environment = {name: "cloud-model" for name in deploy.ROLE_MODELS}
    environment.update({name: "dashscope" for name in deploy.ROLE_PROVIDERS})
    environment.update(backend["environment"])
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    parsed = parse_arguments(backend["command"][3:])
    assert parsed.llm_model == "local-model"
    for role in ("searcher", "reader", "prefilter", "quality", "cognitive", "writer"):
        assert getattr(parsed, f"{role}_model") == "local-model"
    assert all(environment[name] == provider for name in deploy.ROLE_PROVIDERS)
    assert all(environment[name] == "local-model" for name in deploy.ROLE_MODELS)
    assert environment["OPENAI_BASE_URL"] == "http://model:23333/v1"
    assert backend["depends_on"] == ["model"]
    assert backend["env_file"] == [str(env_file.resolve())]
    assert compose["services"]["model"]["command"][3] == "local-model"


@pytest.mark.parametrize("options", [("--mode", "local"), ("--mode", "local", "--model", "test", "--provider", "anthropic")])
def test_invalid_local_configuration_fails_before_deployment(options):
    with pytest.raises(ValueError):
        deploy.build_compose(arguments(*options))


def test_config_is_offline_and_down_keeps_volumes(tmp_path, monkeypatch):
    output = tmp_path / "compose.json"
    calls = []
    monkeypatch.setattr(deploy.subprocess, "call", lambda command, **kwargs: calls.append(command) or 0)
    assert deploy.main(["config", "--project-root", str(ROOT), "--output", str(output)]) == 0
    assert calls == []
    assert json.loads(output.read_text(encoding="utf-8"))["name"] == "veritrace"
    assert deploy.main(["down", "--project-root", str(ROOT), "--output", str(output)]) == 0
    assert calls == [["docker", "compose", "-f", str(output.resolve()), "down"]]


def test_backend_image_has_build_metadata_and_source_before_install():
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    install = next(index for index, line in enumerate(lines) if line.startswith("RUN pip install"))
    assert 'COPY pyproject.toml README.md LICENSE ./' in lines[:install]
    assert 'COPY veritrace ./veritrace' in lines[:install]
    assert '.[documents]' in lines[install]
    assert 'requirements.txt' not in lines[install]
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignored.index("!README.md") > ignored.index("**/README.md")
    assert ignored.index("!LICENSE") > ignored.index("**/LICENSE")


def test_frontend_image_serves_production_bundle_and_streaming_proxy():
    dockerfile = (ROOT / "docker/frontend.Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:22-alpine AS build" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert "FROM nginx:" in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile
    assert '"npm", "start"' not in dockerfile
    nginx = (ROOT / "docker/nginx.conf").read_text(encoding="utf-8")
    assert "location = /solve" in nginx
    assert "proxy_buffering off;" in nginx
    assert "proxy_read_timeout 7200s;" in nginx
    assert "location /api/" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx
