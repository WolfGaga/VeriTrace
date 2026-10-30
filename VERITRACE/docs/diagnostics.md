# Logs and runtime diagnostics

Start the API from the `VERITRACE` directory:

```bash
python -m veritrace.app --host 127.0.0.1 --port 8002 --lang cn --log-level info --log-file logs/backend.log --progress-log-interval 30
```

The terminal and the configured UTF-8 file receive Python logging records, including Uvicorn startup/access logs and exceptions. The directory is created automatically. Files rotate at 10 MiB with three backups. The default file is `logs/backend.log`; `VERITRACE_LOG_FILE` and `VERITRACE_LOG_LEVEL` can set defaults. `--log-file` is not a byte-for-byte transcript of arbitrary `print` output.

## Levels

| Level | Typical content |
|---|---|
| DEBUG | Prompt/reply excerpts, generated Planner code, task context, search arguments, reference mappings and internal state snapshots. Existing truncation limits still apply; this is not a complete transcript of every model call. |
| INFO | Request/adapter configuration, stages, task start/end status and elapsed time, writer role models, database lifecycle and PDF extraction summaries. |
| WARNING | Retries, incomplete/invalid source output, fallbacks and recoverable conditions. |
| ERROR | Failed provider calls/tasks, Planner execution errors, persistence errors and unexpected stream termination. Exception sites retain tracebacks. |
| CRITICAL | Reserved for severe failures if a component emits that level. |

`--log-level info` includes INFO, WARNING, ERROR and CRITICAL; it excludes DEBUG. A task ERROR can be recovered later, so use the final run/task state to determine the outcome. Cancellation is a control signal, not a provider failure.

INFO is the normal operating level. Enable DEBUG temporarily when reproducing a problem locally:

```bash
python -m veritrace.app --log-level debug --log-file logs/debug.log
```

Log levels control verbosity, not confidentiality. Operational records can include source URLs, section titles, criteria and exception text; DEBUG adds research content and generated code. Review and redact logs before sharing them. Runtime logs are created by each installation and are excluded from the source distribution.

## Correlating a run

`[Research:<session_id>]` correlates API request, stage, wait and task lifecycle records. The durable research run has a separate 32-character ID, returned in the first event and the `X-Research-Run-ID` response header; `X-Research-Attempt` identifies the attempt. Recovery reuses the durable run ID and increments the attempt. Explicit stops log `[ResearchRun:<run_id>] Stop requested`.

Useful records include:

- `Started; provider=... model=... search=... lang=...`: effective request configuration.
- `Stage=... task=... section=...`: work currently starting, including initial graph parsing, source acquisition, reading and writing.
- `Task '...' started` and `ended; status=... elapsed=...`: individual task execution. A late response after Stop is not a successful task.
- `[DashScope] attempt ... key #...`: model, credential slot and timeout; the key value is not intentionally printed.
- `[WritingPipeline] Configured models`: resolved writer-stage selection, including fallback choices.
- `[ConversationDB]`: initialized database path, schema migration and committed create/delete/restore actions. A failed transaction should not have a success record.
- `[PageReader] Browser PDF ...`: render start and page/character/duration summary.
- `Finished; status=...`: terminal request state and elapsed time. `research.completed` means the engine produced a final report.

## Understanding Waiting

Example:

```text
[Research:123] Waiting; last_event=research.progress idle=75.0s elapsed=300.0s events=12
```

- `last_event`: most recent non-heartbeat research event processed by the API.
- `idle`: seconds since that event, not seconds since every possible log or HTTP response.
- `elapsed`: duration of this request/attempt, not the sum of earlier attempts or time spent while the backend was stopped.
- `events`: non-heartbeat research events processed, not task count, model-call count or percentage completion.

This message shows that the monitoring coroutine can run. It does not establish that an upstream model is healthy. Model retries may occur while `events` remains unchanged. The record is emitted every `--progress-log-interval` seconds during a request; 0 disables only this periodic log. Frontend heartbeats are a separate mechanism and retain the last actual stage.

Before calling a request stuck, locate the last stage, model attempt, timeout/retry and subsequent terminal state. Inspect whether the task is awaiting a model, web page, evidence planning or report writing. Changing the progress-log interval does not speed up a provider call.

## Files and saved state

| Location | Purpose |
|---|---|
| `logs/backend.log` or selected `--log-file` | API console/file diagnostics. |
| `benchmark/logs/` or batch `--log-file` | Batch research logs; output JSONL also retains failure details. |
| `database/conversations.db` | Default SQLite conversation data, research checkpoints and attempt history. Keep it for recovery; do not distribute it as source. |
| `eval_data/frontend/` | Completed-report exports prepared for evaluation. |
| `frontend/tests/artifacts/` or browser runner `--artifacts` | Isolated browser-test logs/screenshots/databases. These are test evidence, not deployment data. |

The path printed by database initialization is authoritative when diagnosing an empty history. Python callers and tests can choose another database path. Checkpoints include completed tasks, cognitive state, reference allocation and writing progress. Planner internals remain server-side; the public status endpoint returns a user-facing view and recovery availability.

## Browser-side evidence

Use developer tools Console and Network. Collect the `/solve` status/response headers and whether the stream ended unexpectedly, plus `/api/research/<run_id>` status. The current UI reports rejected HTTP responses, invalid/interrupted streams and failed conversation writes. A successful model HTTP 200 is not equivalent to a completed research run.

After an unexplained interruption, record whether a stop was requested. `Stream consumer cancelled; stop_requested=False` means the stream consumer was cancelled without the API's explicit stop flag; it does not identify browser, network, proxy or server shutdown as the specific cause. Do not attribute it to user action without evidence.

For shared deployments, configure the reverse proxy for long SSE responses and disable response buffering on `/solve`. The supplied Nginx example is in `docker/`. Verify your proxy's own logs as well; the API cannot diagnose a request that never reaches it.

## Sharing a diagnostic bundle

Include the source revision, redacted effective configuration, session/run ID and attempt, relevant timestamps and stage, backend traceback, browser Console/Network errors, and exact recovery steps. Share the relevant interval rather than an entire unrelated history. Remove credentials and unrelated research/customer content. Never include `.env`, provider key files or a production database in a source package.
