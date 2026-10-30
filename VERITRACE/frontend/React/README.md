# VeriTrace browser client

From this directory, with Node.js and npm installed:

```bash
npm ci
npm start
```

Open `http://localhost:8080`. Start the API from the project root with `python -m veritrace.app --host 127.0.0.1 --port 8002`. Set `VERITRACE_API_URL` when the API uses another address; Vite proxies both `/solve` and `/api`.

```bash
npm test
npm run build
```

`src/research/state.ts` defines native research events, state updates, and the task-tree projection. `src/research/history.ts` is the isolated reader for older saved conversations. New saves use schema version 2; old SQLite and browser records remain readable. `useResearchSession` owns streaming and cancellation; `useConversationHistory` serializes debounced saves.

The optional offline browser check runs against a local Vite server and intercepts all API calls:

```bash
python ../tests/browser_smoke.py --url http://127.0.0.1:8080
```

For production-bundle verification, build the frontend and run from the project root:

```bash
python frontend/tests/run_browser_suite.py
```

Select another installed Playwright browser with `--browser firefox` or `--browser webkit`.

This requires Playwright and its Chromium browser. The runner starts the real API with an isolated SQLite database and deterministic research events, then stops its local server. It covers controls, 2D/3D views, cognitive details, conversation deletion and recovery, language controls, mobile layout, interrupted streams, storage retries, research continuation, partial downloads, report recovery, and WebGL fallback. External browser requests are blocked; no model credentials are used. Logs, screenshots, and temporary databases stay in `frontend/tests/artifacts/`.

The suite also verifies saved-session formats, usage display, safe report HTML, and citation links. Report rendering filters active HTML while retaining tables, images, and internal links.

If conversation persistence fails, the page retains pending writes and displays **Retry save**. Keep the tab open until the warning clears.

The **Available findings** panel opens completed search answers before the final report is ready. **Download partial findings** saves the material currently available, clearly marked incomplete. After a failure or stop, **Continue research** restores saved work; **Write report from findings** uses collected evidence without starting more searches. If writing has already begun, **Resume report** preserves completed sections. Repair provider configuration and restart the backend when necessary before retrying. Saved checkpoints remain in the API's SQLite database. The UI reports checkpoint saving failures separately from conversation saving failures.

Recovery first checks the server: a completed report is displayed immediately, while a still-running job can be followed without starting another request. Run metadata is also sent in HTTP headers so that an interrupted stream can still be located.
