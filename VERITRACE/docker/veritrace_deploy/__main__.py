import argparse
import json
from pathlib import Path
import subprocess

ROLE_MODELS = (
    "SILICON_MODEL", "SEARCHER_MODEL", "READER_MODEL", "PREFILTER_MODEL", "QUALITY_MODEL",
    "COGNITIVE_MODEL", "WRITER_MODEL", "OUTLINE_MODEL", "SECTION_PLANNER_MODEL", "SECTION_WRITER_MODEL",
)
ROLE_PROVIDERS = (
    "LLM_PROVIDER", "SEARCHER_PROVIDER", "READER_PROVIDER", "PREFILTER_PROVIDER",
    "QUALITY_PROVIDER", "COGNITIVE_PROVIDER", "WRITER_PROVIDER",
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Configure or run VeriTrace with Docker Compose")
    parser.add_argument("action", nargs="?", choices=["config", "up", "down"], default="config")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--mode", choices=["cloud", "local"], default="cloud")
    parser.add_argument("--provider", choices=["siliconflow", "dashscope", "openai", "anthropic", "qwen", "generic"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--lang", choices=["en", "cn"], default="cn")
    parser.add_argument("--search-engine", choices=["google", "serper", "duckduckgo", "bing"], default=None)
    parser.add_argument("--api-port", type=int, default=8002)
    parser.add_argument("--frontend-port", type=int, default=8080)
    parser.add_argument("--local-image", default="openmmlab/lmdeploy:latest-cu12")
    parser.add_argument("--detach", action="store_true")
    return parser.parse_args(argv)


def build_compose(args):
    root = args.project_root.resolve()
    if not (root / "veritrace/app.py").is_file():
        raise ValueError(f"Not a VeriTrace checkout: {root}")
    if args.mode == "local" and not args.model:
        raise ValueError("Local mode requires --model (model ID or mounted model path)")
    provider = args.provider or ("generic" if args.mode == "local" else None)
    if args.mode == "local" and provider not in ("generic", "openai"):
        raise ValueError("Local mode requires the generic or openai provider")
    command = ["python", "-m", "veritrace.app", "--host", "0.0.0.0", "--port", "8002", "--lang", args.lang]
    if provider:
        command.extend(["--llm-provider", provider])
    if args.search_engine:
        command.extend(["--search-engine", args.search_engine])
    if args.model:
        command.extend(["--llm-model", args.model])
    backend = {
        "build": {"context": str(root), "dockerfile": "Dockerfile"},
        "command": command, "ports": [f"{args.api_port}:8002"],
        "environment": {"PYTHONUNBUFFERED": "1"},
        "volumes": ["conversation-data:/app/database", "research-exports:/app/eval_data", "research-logs:/app/logs"],
        "restart": "unless-stopped",
    }
    env_file = args.env_file or root / ".env"
    if env_file.exists():
        backend["env_file"] = [str(env_file.resolve())]
    elif args.env_file:
        raise ValueError(f"Environment file does not exist: {env_file}")
    services = {
        "backend": backend,
        "frontend": {
            "build": {"context": str(root), "dockerfile": "docker/frontend.Dockerfile"},
            "ports": [f"{args.frontend_port}:8080"], "depends_on": ["backend"],
            "restart": "unless-stopped",
        },
    }
    volumes = {"conversation-data": {}, "research-exports": {}, "research-logs": {}}
    if args.mode == "local":
        backend["environment"].update(OPENAI_BASE_URL="http://model:23333/v1", OPENAI_API_KEY="local-model")
        backend["environment"].update({name: args.model for name in ROLE_MODELS})
        backend["environment"].update({name: provider for name in ROLE_PROVIDERS})
        backend["depends_on"] = ["model"]
        services["model"] = {
            "image": args.local_image,
            "command": ["lmdeploy", "serve", "api_server", args.model, "--server-name", "0.0.0.0", "--server-port", "23333"],
            "volumes": ["model-cache:/root/.cache"],
            "deploy": {"resources": {"reservations": {"devices": [
                {"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}
            ]}}},
        }
        volumes["model-cache"] = {}
    return {"name": "veritrace", "services": services, "volumes": volumes}


def main(argv=None):
    args = parse_args(argv)
    root = args.project_root.resolve()
    output = (args.output or root / "docker/temp/compose.json").resolve()
    if args.action != "down":
        compose = build_compose(args)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(compose, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.action == "config":
        print(output)
        return 0
    if not output.exists():
        raise ValueError(f"Compose configuration not found: {output}")
    command = ["docker", "compose", "-f", str(output), args.action]
    if args.action == "up":
        command.append("--build")
        if args.detach:
            command.append("--detach")
    return subprocess.call(command, cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
