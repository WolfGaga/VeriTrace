"""Exercise the browser with in-memory API fixtures and no external requests."""

import argparse
import asyncio
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_support import launch_browser


async def main(url, artifacts=None, browser_name="chromium"):
    records = {"legacy": {"id": "legacy", "title": "Old question", "created_at": 1, "updated_at": 1, "data": {
        "qaList": [{"question": "Old question", "response": "Old report", "nodes": {
            "OldTask": {"subQuestion": "Old task question", "conclusion": "Old task answer", "conclusionRef": {}}
        }}], "formatted": {}, "stashedNodes": {},
    }}}
    errors = []
    posted = []

    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, browser_name)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route_request(route):
            request = route.request
            address = urlparse(request.url)
            path = address.path
            if address.hostname not in ("localhost", "127.0.0.1"):
                await route.abort()
                return
            if path.startswith("/api/conversations"):
                key = path.removeprefix("/api/conversations").strip("/")
                data = request.post_data_json if request.method in ("POST", "PUT") else None
                if request.method == "POST" and not key:
                    records[data["id"]] = {**data, "created_at": 2, "updated_at": 2}
                    result = {"success": True, "id": data["id"]}
                elif request.method in ("PUT", "POST") and key:
                    records[key].update(data)
                    result = {"success": True}
                elif request.method == "DELETE":
                    records.pop(key, None)
                    result = {"success": True}
                else:
                    result = records[key] if key else list(records.values())
                await route.fulfill(json=result)
                return
            if path == "/api/export-eval":
                await route.fulfill(json={"success": True, "query_id": 1, "file_path": "fixture.jsonl"})
                return
            if path == "/solve":
                payload = request.post_data_json
                posted.append(payload)
                if payload["inputs"] == "Slow question":
                    await asyncio.sleep(2)
                    try:
                        await route.fulfill(body="", content_type="text/event-stream")
                    except Exception:
                        pass
                    return
                task = {"id": "TaskA", "question": "Find the source", "status": "completed", "cognitive_target_id": "e1", "result": {
                    "answer": "Verified task answer [[0]]", "references": {"0": {"url": "https://source.test", "title": "Synthetic Source", "snippet": "Source excerpt"}, "1": "https://string.test/source?key=value#section"},
                    "messages": [{"role": "assistant", "content": "Synthetic search trace"}], "feedback": {"reason": "Synthetic feedback"},
                }}
                events = [
                    ("research.started", {"question": payload["inputs"], "cognitive_graph": {"nodes": {"e1": {"id": "e1", "name": "Entity"}}, "edges": {}}}),
                    ("tasks.updated", {"tasks": [{**task, "status": "running", "result": None}]}),
                    ("task.completed", {"task": task}),
                    ("research.completed", {"article": "Verified report: synthetic fixture.", "ref2url": task["result"]["references"], "tasks": [task]}),
                    ("usage.updated", {"usage": {"tokens": {"total": 42}, "llm": {"calls": 2}}}),
                ]
                wire = ''.join('event: response\ndata: ' + json.dumps({"schema_version": 2, "type": kind, "data": data}) + '\n\n' for kind, data in events)
                await route.fulfill(body=wire, content_type="text/event-stream")
                return
            await route.continue_()

        await page.route("**/*", route_request)
        await page.goto(url)
        await page.get_by_placeholder("Ask a question...").fill("Synthetic question")
        await page.get_by_placeholder("Ask a question...").press("Enter")
        await expect(page.get_by_text("Verified report: synthetic fixture.", exact=True)).to_be_visible(timeout=30000)
        await page.get_by_text("TaskA", exact=True).click()
        await expect(page.get_by_text("Find the source", exact=True).first).to_be_visible()
        await expect(page.get_by_text("Synthetic Source", exact=True).first).to_be_visible()
        await expect(page.get_by_role("link", name="https://string.test/source?key=value#section", exact=True).first).to_have_attribute("href", "https://string.test/source?key=value#section")
        await page.get_by_text("Search trace", exact=True).first.click()
        await expect(page.get_by_text("Synthetic search trace", exact=True).first).to_be_visible()
        await page.get_by_text("Search feedback", exact=True).first.click()
        await expect(page.get_by_text(re.compile("Synthetic feedback")).first).to_be_visible()
        await expect(page.get_by_text("42", exact=True)).to_be_visible()
        await page.get_by_role("button", name=re.compile("Export for Eval")).click()
        await expect(page.get_by_text(re.compile("fixture.jsonl"))).to_be_visible()
        await page.wait_for_timeout(700)
        native_id = await page.evaluate("localStorage.getItem('veritrace_current_conversation')")
        assert records[native_id]["data"]["schema_version"] == 2
        assert records[native_id]["data"]["current"]["tasks"]["TaskA"]["result"]["answer"].startswith("Verified task")
        artifact = (artifacts or Path(__file__).parent / "artifacts") / "native-ui.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(artifact), full_page=True)
        await page.reload()
        await expect(page.get_by_text("Verified report: synthetic fixture.", exact=True)).to_be_visible()
        await page.evaluate("localStorage.setItem('veritrace_current_conversation', 'legacy')")
        await page.reload()
        await expect(page.get_by_text("Old report", exact=True)).to_be_visible()
        await page.get_by_text("OldTask", exact=True).click()
        await expect(page.get_by_text("Old task answer", exact=True).filter(visible=True).first).to_be_visible()
        await page.get_by_role("button", name="New Chat", exact=True).first.click()
        await page.get_by_placeholder("Ask a question...").fill("Slow question")
        await page.get_by_placeholder("Ask a question...").press("Enter")
        await page.get_by_role("button", name="Stop research", exact=True).click()
        await expect(page.get_by_role("button", name="Stop research", exact=True)).not_to_be_visible()
        await page.wait_for_timeout(700)
        cancelled_id = await page.evaluate("localStorage.getItem('veritrace_current_conversation')")
        assert records[cancelled_id]["data"]["current"]["status"] == "cancelled"
        assert posted[0]["lang"] == "en"
        assert not errors, errors
        await browser.close()
    print("Browser passed: native stream, task details, usage, export, persisted reload, v1 history, cancellation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8097")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    args = parser.parse_args()
    asyncio.run(main(args.url, args.artifacts, args.browser))
