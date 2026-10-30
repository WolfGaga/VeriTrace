# Configuration and tuning

Start from `.env.example`, copy it to `.env` in the `VERITRACE` directory, and supply your own credentials. Keep `VERITRACE` and `deep_research_bench` as sibling directories. The example selects a Config-27B research profile; it does not guarantee that a particular provider account has access to every named model.

Restart the backend after changing `.env`. Configuration and model defaults are read during import or process initialization. Editing the file does not reconfigure an existing run. Preserve the original question and prompt language when recovering a run.

## Precedence and entry points

1. Existing process environment values take precedence over `.env` values. `python-dotenv` does not overwrite them by default.
2. Explicit API CLI options override their corresponding environment defaults. Run `python -m veritrace.app --help` for supported options.
3. A `/solve` request's `lang` overrides the server's default `--lang`. The React language button stores `en` or `cn` in browser storage and sends it with new requests. It selects prompt language; it is not a full translation of all interface labels.
4. Python constructors can override component configuration explicitly. Such overrides must be recorded when comparing experiments.

Use one backend worker. Active-request ownership and stop controls are process-local. Running several API workers against the same database does not provide shared active-run coordination.

Research requests may include `agent_cfg`, which is accepted without overriding server configuration. Request-level `writer_mode` accepts `research`, an empty string or `null`; all requests use the same three-stage report pipeline.

## Credentials and services

| Service | Settings | Notes |
|---|---|---|
| DashScope research models | `DASHSCOPE_API_KEY`, optional comma-separated `DASHSCOPE_BACKUP_API_KEYS` | The `dashscope` adapter uses the China compatible-mode endpoint. Keys and model access must match that region. It does not read a regional endpoint from `OPENAI_BASE_URL`. |
| SiliconFlow research models | `SILICON_API_KEY`, optional `SILICON_BACKUP_API_KEYS` | These names differ from the optional OCR credential below. |
| OpenAI-compatible research provider | `OPENAI_API_KEY`, optional valid `OPENAI_BASE_URL` | Select `LLM_PROVIDER=openai`. Omit the base URL for the SDK's default endpoint; set it to a complete compatible endpoint for a gateway. |
| Anthropic research provider | `ANTHROPIC_API_KEY` | Select `LLM_PROVIDER=anthropic` and a model accepted by that account. |
| Serper web search | `SERPER_API_KEY`, optional comma-separated `SERPER_BACKUP_API_KEYS` | Selected by the example profile. Model and search accounts are separate. |
| Google Custom Search | `GOOGLE_SEARCH_API_KEY`, `GOOGLE_CX` | Select `SEARCH_ENGINE=google`; both values are required. |
| Bing search | `BING_API_KEY` | Select `SEARCH_ENGINE=bing`; verify service availability for your account. |
| DuckDuckGo search | No API key in this implementation | Select `SEARCH_ENGINE=duckduckgo`. Remote rate limits and availability still apply. |
| Optional Jina page fallback | `JINA_API_KEY` | Leave blank for anonymous reader requests. A paid reader error concerns that reader account, not the research model or judge balance. |
| Optional image OCR | `ENABLE_PAGE_OCR=true`, `SILICONFLOW_API_KEY` | Separate service from the main model adapter. Requires an OCR-capable route and may add calls and latency. |

The `qwen` adapter is a simpler compatibility route, without the `dashscope` adapter's key-rotation behavior. Prefer the explicitly configured `dashscope` route for the supplied profile. `generic` and local-model deployment are described in the [Docker guide](../docker/README.md); validate a chosen provider before a long run.

## Model roles

| Setting | Role and fallback |
|---|---|
| `LLM_PROVIDER` | Main provider. With no environment or CLI configuration, the API defaults to `siliconflow`. |
| `SILICON_MODEL` / `--llm-model` | Planner model. The name is retained for compatibility and also applies when the provider is DashScope. The example sets `qwen3.5-27b`; the unconfigured API fallback is `Qwen/Qwen3-Next-80B-A3B-Thinking`. |
| `SEARCHER_MODEL` | Task-level search synthesis; falls back to the main model if omitted. |
| `READER_MODEL` | Page reading and inline source-quality scoring; falls back to the task model if omitted. |
| `PREFILTER_MODEL` | Search-result classification; falls back to the task model if omitted. |
| `COGNITIVE_MODEL` | Cognitive-graph parsing and updates; falls back to the main model if omitted. |
| `WRITER_MODEL` | Base report writer; falls back to the main model if omitted. |
| `SEARCHER_PROVIDER`, `READER_PROVIDER`, `PREFILTER_PROVIDER`, `COGNITIVE_PROVIDER`, `WRITER_PROVIDER` | Optional role-provider overrides; otherwise use `LLM_PROVIDER`. Each selected provider needs its own credentials. |
| `OUTLINE_MODEL` | Outline model; falls back to the base writer. |
| `SECTION_PLANNER_MODEL` | Evidence planning model; falls back to `OUTLINE_MODEL`, then the base writer. |
| `SECTION_WRITER_MODEL` | Chapter and executive-summary model; falls back to the base writer. |

INFO startup records show adapter models, key counts and timeouts. The shared writer logs a resolved role-model summary. These are requested model names; gateway response metadata alone does not independently establish the upstream model's identity.

`SUMMARIZER_MODEL` is accepted as an alias when `WRITER_MODEL` is unset or empty. The API accepts both `--writer-model` and `--summarizer-model`; explicit CLI values override the environment. Python callers may pass `summarizer_model=...`; an explicit `writer_model` takes precedence. Direct Python factories and constructors do not read these model environment variables automatically.

For online research selected through `summarizer_model`, `SUMMARIZER_MODEL` or `--summarizer-model`, the base report adapter uses `SUMMARIZER_PROVIDER`, then the main provider. Selection through `writer_model`, `WRITER_MODEL` or `--writer-model` uses `SUMMARIZER_PROVIDER` if supplied, otherwise `WRITER_PROVIDER`, then the main provider. `WRITER_PROVIDER` continues to select separately configured outline and section adapters, and saved-evidence replay. The model-setting origin is retained so a section-provider setting cannot silently redirect a separately configured base writer.

Use keyword arguments for Python configuration. `summary_prompt` remains an accepted constructor placeholder and does not override the three-stage writer prompts. `writer_mode` accepts `research` or an unset value; other writing workflows are unsupported. In the parallel benchmark CLI, `search` selects complete `research`, and `writer-only` selects saved-evidence `rewrite`.

`benchmark/run_writer_only.py` and `python -m benchmark.run_writer_only` forward to the same saved-evidence pipeline as `run_rewrite.py`, with the same CLI arguments.

## Research budgets and concurrency

The values below are effective controls. The example profile explicitly sets total model concurrency to 35; the code fallback without that setting is 40.

| Setting | Default / example | Effect and tradeoff |
|---|---:|---|
| `PLANNER_MAX_TURN` | API 20 | Planner search-round budget. Higher values allow more investigation and may increase duration and cost. Internal planning iterations also have a limit of three times this budget. |
| `SEARCH_MAX_TURN` | 6 | Tool/reasoning rounds within one Searcher task; must be at least 1. A final synthesis call may follow the round budget. This is distinct from Planner rounds. |
| `SEARCH_MAX_RESULTS` | 10 | Results requested per search query. Explicit `init_agent(max_results=...)` overrides it; provider-specific limits still apply. |
| `PREFILTER_BATCH_SIZE` | 12 | Search results per classification call. Larger batches reduce call count but increase prompt size. |
| `PREFILTER_MAX_WORKERS` | 4 | Concurrent classification calls. Raising it does not bypass the total model-call limit or provider rate limits. |
| `READER_MAX_WORKERS` | 15 | Concurrent page-reading calls within a reader executor. |
| `LLM_MAX_CONCURRENCY` | Code 40; example 35 | Process-wide model-call semaphore used by the pooled compatible adapters. It is a cap, not a target. Some other provider adapters do not use this pool. |
| `SOFT_DEADLINE_MINUTES` | 70 | Soft exploration deadline, followed by writing from collected work. `0` disables it. It is not a hard end-to-end timeout or an immediate abort of in-flight calls. |

The task scheduler's default pool is 10; Python callers can set `TaskScheduler(max_workers=...)`. Multi-query searches use a pool sized to the query list. Independent tasks, filtering, page acquisition and reading can overlap. Section evidence planning and chapter writing remain sequential because later sections use accumulated context; increasing a worker-count compatibility field does not parallelize them.

Do not assume higher concurrency is faster: four simultaneous provider timeouts still make a batch wait for retries. Compare the same prompt language, models, query set and service conditions when measuring performance.

For comparisons using the supplied paper profile, keep `PLANNER_MAX_TURN=20`, `SEARCH_MAX_TURN=6` and `SEARCH_MAX_RESULTS=10` fixed. Planner isolation does not add or change research budgets.

## Planner execution

`PLANNER_ISOLATION=false` is the default. Normal mode supports the Planner's task graph and command API, including `graph.node(...)`, `graph.nodes`, task state, and the `add_task`, `finish` and `propose_restructure` commands. Its restricted builtin set includes `getattr`, `hasattr`, `id`, `setattr` and `type`. Ordinary import statements remain unavailable because the builtin set does not include Python's import function.

`graph.nodes` contains the scheduler's actual node dictionaries. `graph.node(...)` returns a shallow outer copy: changing an outer field does not persist, while edits to nested answers, references and evidence reach the stored node. Answers and memory are ordinary dictionaries, including their normal replacement, alias and JSON-serialization behavior. Native events and checkpoints receive separate task snapshots.

Set `PLANNER_ISOLATION=true` to opt into additional syntax and attribute checks. These checks restrict private attributes, frame access and string-formatting access paths, and task queries return isolated copies of task data. They can reject otherwise valid normal-mode Planner snippets. Evaluate research effects separately before comparing results across the two modes; unchanged prompts and budgets do not establish equivalent research behavior.

Both modes execute Python in the backend process. The optional checks are not an operating-system sandbox or a boundary for running untrusted remote code. Restart the backend after changing this setting.

## Content and document limits

| Setting | Default | Effect |
|---|---:|---|
| `READER_MAX_CONTENT_LENGTH` | 15000 characters | Page-content limit used by page acquisition and Reader configuration. Truncation can reduce evidence coverage. |
| `PDF_MAX_PAGES` | 20 | Pages considered by PDF extraction, including browser-rendered PDF fallback. |
| `EXCEL_MAX_SHEETS` | 5 | Maximum sheets loaded from a workbook. |
| `PREVIOUS_TEXT_MAX_CHARS` | 15000 characters | Preceding chapter text supplied during sequential writing. |
| `ENABLE_PAGE_OCR` | false / opt-in | Enables optional image-text extraction. Keep the OCR credential separate from research-model credentials. |

## Timeout and retry policy

The API's `--progress-log-interval` changes logging frequency, not timeouts. General model timeout constants and retry rules live in `veritrace/agent/providers/policy.py`; the supplied Qwen research models use 180-second SDK timeouts. The writing adapter uses a 300-second override. These are request/transport settings, not a hard bound on the entire research run.

`LLM_REQUEST_TIMEOUT_PLUS` applies specifically to `qwen3.5-plus` and defaults to 180 seconds. It does not change the timeout of `qwen3.5-27b` or `qwen3.5-35b-a3b`. Changing another model's timeout requires an explicit adapter override in Python or a reviewed policy change.

DashScope/SiliconFlow key rotation is separate from the simple adapter's retry count. With two keys, the sequence is primary, backup, primary, primary, backup. Error classification, backoff and confirmed-balance checks affect which attempts occur. In-flight requests can finish after Stop; their results are discarded. See [diagnostics](diagnostics.md).

## Compatibility fields and research-level changes

Do not present every accepted field as an effective tuning control:

- `QUALITY_MODEL` and `--quality-model` are compatibility inputs. Reader performs source scoring inline; the current engine does not create a separate quality-model agent from this option.
- `SEARCH_QUALITY_THRESHOLD` and `SEARCH_HIGH_QUALITY_RATIO` are parsed into a compatibility configuration object, but do not drive the current mechanical quality/retry decisions. Actual rules are in `SourceQualityEvaluator.get_quality_level()` and `should_force_retry()` in `quality_checker.py`.
- `READER_SNIPPET_LENGTH` is stored in compatibility configuration but is not consumed by the current acquisition/reading path. It is not an effective truncation control; use `READER_MAX_CONTENT_LENGTH` for page content.
- `SECTION_PLANNER_MAX_WORKERS` is retained for interface compatibility; global section planning is sequential.
- `ENABLE_THREE_STAGE` selects task-level filtering/reading behavior. Keep it enabled for the standard DeepResearch workflow; changing it is an algorithmic experiment, not a harmless speed setting.

Changes to quality rules, prompts, evidence handling, section order or these algorithmic branches require regression and effect evaluation. Keep them separate from operational changes such as log paths or ports.

## Evaluation configuration

FACT/RACE use the sibling `deep_research_bench` project. For a compatible judge gateway, set `GEMINI_BASE_URL`, `GEMINI_API_KEY` and `RACE_MODEL`; for other routes follow the evaluator's supported configuration. `FACT_MODEL` is independent. Research, search and evaluation keys can belong to different accounts.

Verify the chosen judge alias with your provider before a run. Re-score compared reports using the same judge route, criteria, references and preprocessing. Record requested and provider-reported model names. A report export only prepares evaluator input; it does not prove that scoring has run. See [benchmark instructions](../benchmark/DATA_README.md).

## Practical changes

- To reduce rate-limit pressure: lower `LLM_MAX_CONCURRENCY` first, then reader/prefilter workers. Preserve other settings when assessing the effect.
- To investigate more sources: raise query-result or research-round budgets cautiously; this changes work volume and may change quality.
- To reduce console noise: use `--progress-log-interval 60` or `0`. Keep `--log-level info` while diagnosing a partner deployment.
- To reproduce a run: record prompt language, all role models/providers, effective limits, credential slot selection without key values, source revision, and the evaluator route separately.
