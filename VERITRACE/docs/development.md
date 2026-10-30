# Development and validation

## Repository layout

Keep this layout when cloning or extracting a source package:

```text
workspace/
  VERITRACE/             # Research API, engine, React client, tests and guides
  deep_research_bench/   # FACT/RACE evaluation components and assets
```

The [code map](architecture.md) identifies module responsibilities and event flow. Start with [configuration](configuration.md), [diagnostics](diagnostics.md) and [troubleshooting](troubleshooting.md) before changing runtime behavior.

## Local setup

Use Python 3.10 or later and a Node.js installation compatible with the frontend. Run from `VERITRACE`:

```bash
python -m venv .venv
# Activate .venv with your shell's activation command.
python -m pip install -e ".[documents,dev,browser]"
python -m playwright install chromium
```

Copy `.env.example` to `.env`, fill your own model and search keys, and verify the selected account's model access. Browser/PDF fallbacks require the relevant optional dependencies; a model key alone does not configure web search. Do not copy another developer's private environment into a handoff package.

Start the backend in a terminal where its output remains visible:

```bash
python -m veritrace.app --host 127.0.0.1 --port 8002 --lang cn --log-level info --log-file logs/backend.log --progress-log-interval 30
```

In a second terminal:

```bash
cd frontend/React
npm ci
npm start
```

Open `http://127.0.0.1:8080`. On PowerShell, use `npm.cmd` if script execution policy blocks `npm.ps1`. Select the intended prompt language in the page before submitting a new question. The browser selection overrides the backend's default language.

The API is a trusted-workspace service, with history/administrative endpoints and no application authentication layer. Keep the local binding or place authentication/access control in front of a shared deployment. See the [Docker guide](../docker/README.md) for the supplied proxy and persistent volumes. A source/build check is not certification of a particular Docker host or network deployment.

## Checks before proposing a change

Run the default offline checks from `VERITRACE`:

```bash
python -m pytest -q
python -m ruff check .
```

Then from `frontend/React`:

```bash
npm test
npm run build
```

After the production build, run from `VERITRACE`:

```bash
python frontend/tests/run_browser_suite.py
```

The browser suite uses isolated API/database instances and controlled research events. It covers controls, visualization, stage/status display, safe rendering, persistence, stream failures and recovery actions. It does not use paid model keys or prove external-provider quality. Default pytest also blocks external network; opt-in live tests are separate.

## Actual-service acceptance

Use an isolated database/output location through `create_app(..., database_path=..., export_dir=...)` in a private validation launcher, or a dedicated checkout. Do not run failure-injection tests against a colleague's active database or stop their ongoing job.

Record revision, effective configuration and prompt language, then verify one representative question through the production browser:

1. New question reaches the API; initialization and subsequent stages remain visible while model calls wait.
2. Independent searches/readers run as configured; completed findings and source details are usable before the final report.
3. A controlled stop retains work. Continue or write from findings uses the saved run rather than silently starting a new question.
4. Draft sections and their references appear during writing. Final completion clears running indicators and recovered errors.
5. Download, completed-report evaluation export and reload preserve the intended result. Keep exports clearly separate from incomplete material.
6. Inspect backend and browser logs for genuine errors, recoverable warnings, cancellations, retries and the final state. An HTTP 200 or absence of a traceback is not sufficient acceptance evidence.

Real research, reading fallbacks and evaluation can consume provider quota and take substantial time. Validate credentials and provider access before launching a batch. Do not change prompts or select easier replacement questions after seeing an unfavorable result.

## Assessing quality or performance changes

Retain original reports and all failed attempts. Compare the same query IDs using a common judge route, criteria, reference reports, cleaning procedure and score equations. Keep the requested and provider-reported judge names with the results. RACE scores are relative measures, not factual-accuracy percentages. A small fixed set is useful for regression screening, not proof of exact quality equivalence.

For performance, record stage timings, source/task counts and retries. Use the same prompt language and effective model configuration. Separate provider/network latency from local scheduling, serialization, database and UI costs. Preserve explicit unknowns when the evidence cannot identify an interruption's cause.

## Source package checklist

- Include runtime code, tests, benchmark components, dependency manifests, licenses, the architecture map and developer guides.
- Include blank credential fields in `.env.example`; the recipient supplies their own keys. Keep a source revision identifier and, when supplied, verify `MANIFEST.sha256`.
- Exclude `.env`, worker credentials, Git history, production databases, logs, research reports/results, caches, installed dependencies and temporary test artifacts.
- Do not include a preconfigured paid account, local drive paths or a developer's browser/session state.
- Extract the archive into a fresh directory and validate its contents and imports. Run the offline suite from that extraction as well as the checkout.
- Supply a separate validation record naming the revision, checks actually run, service/provider scope and material limits. Keep internal comparison/history artifacts outside the distributable source.

Checkpoint recovery requires the deployment's saved SQLite data. Excluding a production database from the source archive does not remove persistence from the application.
