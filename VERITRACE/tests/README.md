Run the offline regression suite from the project directory:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -B -X utf8 -m pytest -p no:cacheprovider
```

Tests disable dotenv loading and socket connections by default. Synthetic provider
responses exercise real parsing, evidence mapping, writing and graph updates.
The Windows asyncio wakeup socket pair is allowed; other connections remain blocked.

The suite covers source-to-citation attribution, graph restructuring and rollback,
writer fallbacks and partial output, provider retries, task cancellation, native
events, conversation storage, export formats, concurrent usage accounting, and CLI
result handling. The research-flow harness runs the real engine and writing pipeline
with deterministic providers and a synthetic searcher, including forced completion.

Recovery tests verify that report continuation reuses completed sections and search
results, reference IDs remain stable, SQLite checkpoints survive API restart, stale
attempt leases reject late saves, and storage failures remain visible to clients.
Browser recovery tests exercise partial downloads, page refresh, continuation, and
report generation through the production UI and the local HTTP API.

Real model tests are explicitly marked `live_llm`. To run only those tests with
your configured provider, use `python -m pytest --run-live-llm -m live_llm`.
These calls consume provider quota. `test_citation_verifier.py` remains a manual
experiment requiring its external datasets; it is excluded from pytest collection.

Passing offline tests establishes the checked deterministic behavior. It does not
establish report quality, live search availability or equivalent benchmark scores.

The default command also runs Python frontend, benchmark entry-point, and Docker configuration contracts. Optional Gradio/Streamlit cases are skipped if those packages are absent. React and browser checks remain separate commands in frontend/React/README.md.
