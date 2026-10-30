# VeriTrace

VeriTrace researches questions through an evolving cognitive graph, source-grounded evidence and a three-stage report writing pipeline.

- Search tasks run concurrently and feed structured Reader findings into the cognitive graph.
- Evidence retains source URLs, quoted text and global citation indices.
- Outline planning, section evidence planning and report writing share one pipeline across interactive and batch entry points.
- React, Gradio and Streamlit consume the same research event protocol.
- Conversation history supports persistence, recovery and export.

The public runtime exposes one workflow: complete deep research followed by the three-stage report writer. `benchmark/run_rewrite.py` applies that same writer to saved evidence for reproducibility; it does not define a second research or report mode.

[Code architecture and diagrams](docs/architecture.md) | [Testing](tests/README.md) | [Benchmark evaluation](benchmark/DATA_README.md)

Development guides: [Configuration and tuning](docs/configuration.md) | [Logs and diagnostics](docs/diagnostics.md) | [Troubleshooting](docs/troubleshooting.md) | [Development and handoff checks](docs/development.md)

## Install

Use Python 3.10 or later. From this directory:

```shell
python -m pip install -e ".[documents,dev]"
```

Optional interfaces and browser-assisted page reading:

```shell
python -m pip install -e ".[ui,browser]"
python -m playwright install chromium
```

Copy `.env.example` to `.env` and configure the provider and search credentials. The example contains the Config-27B profile. Model choices remain configurable; the runtime does not require a particular model vendor.

| Setting | Purpose |
|---|---|
| `LLM_PROVIDER`, `SILICON_MODEL` | Planner provider and model |
| `SEARCHER_MODEL`, `READER_MODEL`, `PREFILTER_MODEL` | Search and reading models |
| `COGNITIVE_MODEL`, `WRITER_MODEL` | Cognitive updates and report writing |
| `WRITER_PROVIDER`, `OUTLINE_MODEL`, `SECTION_PLANNER_MODEL`, `SECTION_WRITER_MODEL` | Writing-stage overrides |
| `SEARCH_ENGINE` | `google`, `serper`, `duckduckgo` or `bing` |
| `PLANNER_MAX_TURN`, `SEARCH_MAX_TURN` | Research and search limits |
| `PLANNER_ISOLATION` | Optional Planner checks; `false` by default |

Provider keys use their corresponding environment variables: `DASHSCOPE_API_KEY`, `SILICON_API_KEY`, `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Optional image OCR separately uses `SILICONFLOW_API_KEY`. Search keys depend on the selected engine. See the [configuration guide](docs/configuration.md) for precedence, regional endpoints, effective controls and compatibility-only options.

Normal Planner execution supports task graph and task-state access with a restricted builtin set; ordinary import statements remain unavailable. `PLANNER_ISOLATION=true` opts into additional syntax and access checks and copied task-query results. It can reject valid normal-mode snippets and needs separate research-effect evaluation. Both modes run in the backend process; these checks do not provide an operating-system sandbox. See [Planner execution](docs/configuration.md#planner-execution).

## Run

```shell
python -m veritrace.app --host 127.0.0.1 --port 8002 --lang cn
```

In `frontend/React`, install dependencies with `npm ci`, then run `npm start`. The frontend API target is configured through Vite environment settings. Alternative interfaces:

```shell
python frontend/veritrace_gradio.py
streamlit run frontend/veritrace_streamlit.py
python -m veritrace.terminal
```

See [Docker deployment](docker/README.md) for container configuration.

The browser displays the current research stage, active/completed/failed task counts, and the current report section. Stages are emitted by the backend before the corresponding work begins, including outline planning, section evidence planning, section writing, and executive summary generation. The stream sends a heartbeat after 15 seconds without a research update so the page can distinguish a connected waiting request from missing server updates. Heartbeats do not imply that the model has completed more work. No percentage completion is estimated. Completed, failed, stopped, and restored historical runs do not retain active task indicators. Gradio and Streamlit display the same stage events through their task-progress views.

## Diagnostics

The API writes to the console and `logs/backend.log`. It creates the directory automatically and rotates the file at 10 MiB, keeping three backups. Uvicorn startup, access logs, and application exceptions use the same destinations. `start_server.py` also uses this logging configuration.

INFO retains operational diagnostics. Prompt/reply excerpts, generated Planner code and internal state snapshots use DEBUG; enable `--log-level debug` for local troubleshooting. These records can contain research content. Review and redact them before sharing. Runtime logs are excluded from the source distribution.

```shell
python -m veritrace.app --log-file logs/backend.log --log-level info
```

`VERITRACE_LOG_FILE` and `VERITRACE_LOG_LEVEL` set the API defaults. Request logs include the session ID, selected provider/model/search engine, start, completion, failure, and cancellation. Waiting requests report their last research event and idle time every 30 seconds; `--progress-log-interval SECONDS` changes the interval, and `0` disables periodic messages. These messages describe waiting; they do not impose a new request timeout.

Search-task and Planner execution failures retain backend tracebacks. Batch research creates `benchmark/logs/` automatically; `benchmark/run_terminal.py --log-file PATH` selects another destination. Failed batch records also retain the complete error in the output JSONL.

INFO logs include task start/end status and elapsed time, database initialization and committed conversation changes, browser-PDF extraction summaries, and resolved writing-stage model configuration. A task wait that ends with unfinished work reports the remaining task IDs rather than claiming completion. Direct console output is separate from logging; `--log-file` does not capture arbitrary `print` output.

Cancellation is distinct from a provider failure. Stream-cancellation records include whether a stop was requested and the last research stage; the stop endpoint also logs explicit stop requests. Responses arriving after cancellation do not become successful task results.

For a stalled browser request, collect the backend log and the browser developer tools Console and Network entry for `/solve`. The Console retains request errors, rejected HTTP response details, stream failures, and conversation persistence errors. A browser request that never reaches the API must be investigated through its network/proxy path. Existing task, retry, and timeout messages remain available in the backend log.

## Research and evaluation

```shell
python benchmark/run_terminal.py --input queries.jsonl --output eval_data/terminal/results.jsonl --save-evidence
python benchmark/run_rewrite.py --evidence evidence.json --output report.jsonl
python benchmark/run_parallel.py --help
python benchmark/run_eval.py --help
```

Saved evidence enables the same three-stage report writer to be rerun without repeating retrieval. Use distinct output directories for independent experiments. FACT and RACE evaluation use the sibling `deep_research_bench` project and its configured providers.

## Validate

```shell
python -m pytest
python -m ruff check .
```

The default test suite blocks external network access. Real model tests require `--run-live-llm`. In `frontend/React`, run `npm run build` for TypeScript and production compilation.

## License and attribution

VeriTrace is licensed under [Apache 2.0](LICENSE), including commercial use subject to its terms. Third-party components retain their respective licenses and copyright notices. For research or industry collaboration, contact [hz496@cam.ac.uk](mailto:hz496@cam.ac.uk?subject=VeriTrace%20collaboration).
