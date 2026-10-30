/* Serve a recorded research stream to the unmodified production frontend. */
(() => {
  const base = new URL(".", document.currentScript.src);
  const nativeFetch = window.fetch.bind(window);
  const recording = nativeFetch(new URL("recording.json", base), {
    cache: "no-store",
  }).then((response) => response.json());
  const records = new Map();
  const encoder = new TextEncoder();
  const replay = {
    index: 0,
    paused: false,
    speed: 1,
    timer: null,
    controller: null,
    cancelled: false,
    data: null,
  };
  const json = (value) =>
    new Response(JSON.stringify(value), {
      headers: { "Content-Type": "application/json" },
    });
  const notify = (type, details = {}) =>
    parent.postMessage(
      { source: "veritrace-replay", type, ...details },
      location.origin,
    );
  for (const key of ["veritrace_current_conversation", "veritrace_create_new"])
    localStorage.removeItem(key);
  localStorage.setItem("veritrace_lang", "en");

  function delay(event) {
    if (event.type === "cognitive.updated")
      return event.data.content.includes("restructured") ? 2400 : 1400;
    if (event.type === "research.partial") return 1800;
    if (event.type === "research.completed") return 1500;
    if (event.type === "task.completed") return 1300;
    return 650;
  }

  function emitNext() {
    const event = replay.data.events[replay.index++];
    replay.controller.enqueue(
      encoder.encode(`event: response\ndata: ${JSON.stringify(event)}\n\n`),
    );
    notify("event", {
      index: replay.index,
      total: replay.data.events.length,
      eventType: event.type,
      stage: event.data.stage,
      detail: event.data.content,
      nodes: Object.keys(event.data.cognitive_graph?.nodes || {}).length,
    });
    return event;
  }

  function tick() {
    if (replay.cancelled || !replay.controller) return;
    if (replay.paused) {
      replay.timer = setTimeout(tick, 100);
      return;
    }
    if (replay.index >= replay.data.events.length) {
      replay.controller.close();
      replay.controller = null;
      notify("complete");
      return;
    }
    const event = emitNext();
    replay.timer = setTimeout(tick, delay(event) / replay.speed);
  }

  function finish() {
    if (!replay.controller || !replay.data) return;
    clearTimeout(replay.timer);
    replay.paused = false;
    while (replay.index < replay.data.events.length) {
      emitNext();
    }
    replay.controller.close();
    replay.controller = null;
    notify("complete");
  }

  function advanceTo(count) {
    if (!replay.controller || !replay.data) return;
    clearTimeout(replay.timer);
    replay.paused = true;
    const stop = Math.min(
      replay.data.events.length,
      Math.max(0, Number(count)),
    );
    while (replay.index < stop) emitNext();
    if (replay.index === replay.data.events.length) {
      replay.controller.close();
      replay.controller = null;
      notify("complete");
    } else replay.timer = setTimeout(tick, 100);
  }

  window.fetch = async (input, init = {}) => {
    const address = new URL(
      typeof input === "string" ? input : input.url,
      location.href,
    );
    const path = address.pathname;
    const method = (init.method || "GET").toUpperCase();
    if (path === "/solve") {
      replay.data = await recording;
      if (
        JSON.parse(init.body || "{}").inputs !== replay.data.metadata.question
      ) {
        notify("example-only");
        return new Response(
          "This recorded demo supports the fixed example only. Reset the walkthrough or install VeriTrace to research a new question.",
          { status: 422 },
        );
      }
      replay.index = 0;
      replay.cancelled = false;
      replay.paused = false;
      const stream = new ReadableStream({
        start(controller) {
          replay.controller = controller;
          replay.timer = setTimeout(tick, 250);
        },
        cancel() {
          if (!replay.controller) return;
          replay.cancelled = true;
          clearTimeout(replay.timer);
          replay.controller = null;
          notify("stopped");
        },
      });
      init.signal?.addEventListener(
        "abort",
        () => {
          if (!replay.controller) return;
          replay.cancelled = true;
          clearTimeout(replay.timer);
          try {
            replay.controller?.close();
          } catch {
            /* The stream may already be closed. */
          }
          replay.controller = null;
          notify("stopped");
        },
        { once: true },
      );
      notify("started", { total: replay.data.events.length });
      return new Response(stream, {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
        },
      });
    }
    if (
      path === "/api/conversations" ||
      path.startsWith("/api/conversations/")
    ) {
      const id = path.split("/")[3];
      const body = init.body ? JSON.parse(init.body) : null;
      if (method === "POST" && !id) {
        records.set(body.id, {
          ...body,
          created_at: Date.now(),
          updated_at: Date.now(),
        });
        return json({ success: true, id: body.id });
      }
      if (id && ["POST", "PUT"].includes(method)) {
        records.set(id, {
          ...records.get(id),
          ...body,
          id,
          updated_at: Date.now(),
        });
        return json({ success: true });
      }
      if (id && method === "DELETE") {
        records.delete(id);
        return json({ success: true });
      }
      return json(id ? records.get(id) || {} : [...records.values()]);
    }
    if (path === "/api/export-eval") {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(
        new Blob([init.body + "\n"], { type: "application/x-ndjson" }),
      );
      link.download = "veritrace-recorded-example.jsonl";
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      return json({
        success: true,
        query_id: replay.data.metadata.query_id,
        file_path: "Downloaded recorded example",
      });
    }
    if (path.startsWith("/api/")) return json([]);
    if (address.origin !== location.origin)
      throw new Error("External requests are disabled in the recorded demo.");
    return nativeFetch(input, init);
  };
  if (navigator.sendBeacon) navigator.sendBeacon = () => true;

  function start() {
    const input = document.querySelector(
      'input[placeholder="Ask a question..."]',
    );
    if (!input || !replay.data) return false;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    ).set;
    setter.call(input, replay.data.metadata.question);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    setTimeout(
      () =>
        document.querySelector('button[aria-label="Send question"]')?.click(),
      100,
    );
    return true;
  }

  window.__VERITRACE_REPLAY__ = {
    start,
    finish,
    advanceTo,
    pause() {
      replay.paused = !replay.paused;
      return replay.paused;
    },
    speed(value) {
      replay.speed = Math.max(0.25, Math.min(8, Number(value) || 1));
    },
    state: replay,
  };
  window.addEventListener("message", (event) => {
    if (
      event.origin !== location.origin ||
      event.data?.source !== "veritrace-demo-controls"
    )
      return;
    if (event.data.action === "start") start();
    if (event.data.action === "pause")
      notify("paused", { paused: window.__VERITRACE_REPLAY__.pause() });
    if (event.data.action === "finish") finish();
    if (event.data.action === "speed")
      window.__VERITRACE_REPLAY__.speed(event.data.value);
  });
  recording.then((data) => {
    replay.data = data;
    const ready = setInterval(() => {
      if (document.querySelector('input[placeholder="Ask a question..."]')) {
        clearInterval(ready);
        notify("ready", { metadata: data.metadata });
      }
    }, 200);
  });
  const localized = new WeakSet();
  new MutationObserver(() => {
    if (replay.data?.metadata.language !== "en") return;
    for (const button of document.querySelectorAll(
      'button[title="Switch to English"]',
    )) {
      if (!localized.has(button)) {
        localized.add(button);
        button.click();
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
