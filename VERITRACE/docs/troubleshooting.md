# Troubleshooting and recovery

First check the backend URL, source revision, prompt language and current run/attempt. Keep the available findings downloaded if persistence is uncertain. Do not delete the SQLite database to clear a UI problem: it contains recovery state.

## Common messages

| Message or symptom | Interpretation | Action |
|---|---|---|
| `Waiting ... events=...` repeats | No new substantive research event; a model or page request may still be pending. | Check the last stage and model retry/timeout records. This line alone proves neither progress nor deadlock. |
| `APITimeoutError`, read timeout, HTTP 504 | An upstream request did not finish under its timeout policy. | Check which model and key slot timed out, provider health and network path. Allow configured retries or stop and recover; increasing the timeout can make failure take longer. |
| HTTP 401 / invalid API key | Authentication rejected. | Check the chosen provider, account/region and credential variable. Restart after fixing the process environment. |
| HTTP 403 / permission denied | Access, region or account policy refused. | Confirm account/model access and endpoint region. It is not automatically a balance error. |
| HTTP 429 | Provider rate or quota restriction. | Inspect provider details; reduce concurrency if rate-related and verify quota separately. Repeated immediate retries may worsen rate limits. |
| `model_not_found` / no available channel | The selected alias has no route available to that account/group. | Check the provider's current model catalog. A listed or successful alias is not proof of upstream model identity. |
| `sensitive_words_detected` | Gateway input filter rejected the request. | Retain the failed attempt and ask the provider to resolve the false positive or choose an appropriate common route. Do not silently alter benchmark reports to evade a filter. |
| Confirmed insufficient balance | The adapter's verification supports a balance failure for that key. | Check that service's account. Model, search, reader and judge balances are separate. Token limits and account wallet balance are also distinct. |
| `CancelledError: Research cancelled` | Cancellation control flow; in-flight HTTP requests can still return afterward. | Inspect the earlier request/stop records and saved status. The engine discards late task results. |
| `Stream consumer cancelled; stop_requested=False` | The stream consumer ended without the explicit stop flag. | Inspect browser, proxy and server shutdown evidence; recover from the saved run. This record alone does not prove the user clicked Stop. |
| `Stream ended without a terminal research event` / stream closed before completion | Transport or execution ended without a final event. | Query saved state before restarting research: the server may already have completed. Record browser/network errors. |
| HTTP 409 `Previous attempt is still stopping` | An attempt still owns that run in the current API process. | Wait briefly and query its status. Do not issue repeated fresh jobs to bypass ownership. |
| HTTP 400 `Recovery must use the original question and language` | Recovery inputs disagree with the saved run. | Open the original conversation and use its recovery action; avoid changing language mid-recovery. |
| No saved checkpoint | No valid recovery point exists, often before initialization completed. | Download any local findings and use Retry, which starts a new run. It cannot reconstruct unsaved work. |
| No collected evidence available for writing | The checkpoint has no usable evidence for the writer. | Continue research rather than forcing an empty report. |
| Checkpoint could not be saved | Server persistence failed; browser material may still be available. | Download findings before leaving, inspect disk/permissions/SQLite errors and the printed database path. |
| Conversation could not be saved / Retry save | Conversation-history persistence failed; this is distinct from research checkpoints. | Keep the tab open, repair API/storage connectivity and use Retry save. |
| Planner NameError / TypeError | Model-generated planning code failed. | Inspect the code and traceback. The model can receive feedback and recover; evaluate the final status rather than one intermediate exception. |
| Reader finding discarded: no evidence quote | The extraction failed the evidence requirement. | Inspect the source and Reader output. Do not turn an unsupported statement into a retained fact merely to suppress the warning. |
| Missing browser/PDF/OCR dependency | An optional acquisition route cannot run. | Install the corresponding extras/browser or disable only that optional feature. Other reading fallbacks may still succeed. |
| Export cannot match question to benchmark query | The completed report does not match a known benchmark prompt. | Supply the intended benchmark query ID through the export API, or use a benchmark task. Export does not automatically invoke RACE/FACT. |

## Recovery choices

- **Download partial findings** saves the current answers, sources, cognitive graph and draft, marked incomplete.
- **Continue research** restores completed tasks and retries eligible unfinished tasks. It starts a fresh planning budget and may add new refinement tasks. It is not a CPU-stack continuation or a promise that every subsequent search query will be unique.
- **Write report from findings** runs the complete report writer from collected evidence without requiring more retrieval.
- **Resume report** reuses the outline/plans and the completed chapter prefix, then continues remaining writing and summary work.
- **Retry research** starts a new run when no usable checkpoint is available. Earlier conversation material stays accessible.

Recovery first checks the server. A completed report is loaded without starting a duplicate model request; an active run can be followed through saved-state polling. A stopped or disconnected run with a checkpoint requires the available recovery action. Repair credentials/configuration and restart the backend if required before continuing.

## Frontend cannot reach the backend

For local development, React defaults to port 8080 and proxies `/api` and `/solve` to `http://127.0.0.1:8002`. Set `VERITRACE_API_URL` before starting Vite to change the target. A Vite page returning 200 does not prove the backend is running.

Check the browser's request URL, HTTP status and content type. `/solve` must return `text/event-stream`. Inspect rejected response details in Console, and verify that a proxy did not replace the response with HTML. The backend log should contain the matching request. Use one API worker and retain the same database path when restarting.

## Slow runs

Compare prompt language and actual work, not only the question title. Models can select different tasks and sources on independent runs. A batch's duration follows slow requests and retries even when its workers are parallel. INFO adapter records reveal key rotation and per-request timeouts; the stage monitor does not show token-by-token generation.

Inspect `LLM_MAX_CONCURRENCY`, reader/prefilter workers, source counts and request failures before changing algorithms. Preserve a measured baseline. Reducing reading limits, turning off three-stage evidence processing or changing quality rules can change report quality and requires a new comparison.

## Reporting an unresolved issue

Use this compact template in an issue or an agreed private support channel:

```text
Source revision / operating system / Python and Node versions:
Entry point and exact startup command (no credentials):
Provider, role models, search engine and prompt language:
Run ID / attempt / session ID:
Expected behavior and actual behavior:
First relevant timestamp / last stage / terminal status:
Backend error and traceback:
Browser Console/Network or proxy error:
Recovery actions tried and whether findings remain available:
```

State what was directly observed. For example, a cancellation without an explicit stop request needs investigation; it should not be reported as proven user cancellation, a proven provider failure, or a fixed network cause without supporting evidence.
