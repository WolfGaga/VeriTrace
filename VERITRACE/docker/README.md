# Docker deployment

Run from the VeriTrace checkout. Keep provider/search credentials in the project's `.env`; the launcher passes the file to Compose without embedding credentials in generated configuration or images.

```bash
pip install -e docker
veritrace-deploy config --provider siliconflow --model Qwen/Qwen3-32B
veritrace-deploy up --provider siliconflow --model Qwen/Qwen3-32B --detach
veritrace-deploy down
```

`config` writes `docker/temp/compose.json` without invoking Docker. `up` builds the local checkout and starts the API on 8002 and React on 8080. `down` preserves conversation, export, and model-cache volumes.

Supported providers: `siliconflow`, `dashscope`, `openai`, `anthropic`, `qwen`, `generic`. Optional flags include `--lang en|cn`, `--search-engine google|serper|duckduckgo|bing`, `--env-file PATH`, `--api-port PORT`, `--frontend-port PORT`, and `--project-root PATH`.

For a GPU-hosted model, local mode starts an LMDeploy API service and connects the `generic` adapter to it:

```bash
veritrace-deploy up --mode local --model internlm/internlm2_5-7b-chat --detach
```

Local mode requires an NVIDIA-compatible container runtime. `--local-image` selects the model-server image appropriate to the host. Model acquisition happens when that service starts.

The frontend builds with Node.js 22 and serves the production bundle through Nginx. `/solve` streams without proxy buffering, `/api/` forwards history and export requests, and browser routes fall back to `index.html`. Database state, exports, and backend logs use named volumes. Backend logs are written to `/app/logs/backend.log` and the container console; the `research-logs` volume preserves log files when containers are recreated.

Local mode overrides every role's provider and model, including writing roles, so cloud role settings retained in `.env` cannot redirect model calls. Cloud mode keeps the existing environment defaults unless a CLI option overrides them.
