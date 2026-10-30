"""Exercise interrupted requests and persistence recovery through the browser."""

import argparse
import asyncio
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as BrowserTimeout, async_playwright, expect
from browser_support import launch_browser


async def run(url, artifacts, browser_name="chromium"):
    artifacts.mkdir(parents=True, exist_ok=True)
    faults, checks, errors, expected_errors = {}, [], [], []
    console_messages = []
    expected_webgl_failure = False
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, browser_name)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(10000)
        def record_error(error):
            text = str(error)
            if expected_webgl_failure and "Error creating WebGL context" in text:
                expected_errors.append(text)
            else:
                errors.append(text)

        page.on("pageerror", record_error)
        page.on("console", lambda event: console_messages.append({"type": event.type, "text": event.text}))

        async def intercept(route):
            request = route.request
            address = urlparse(request.url)
            fault = faults.get((request.method, address.path))
            if address.hostname not in ("localhost", "127.0.0.1"):
                await route.abort()
            elif fault == "abort":
                await route.abort("internetdisconnected")
            elif fault == "after_commit":
                await route.fetch()
                await route.fulfill(status=503, json={"error": "Response interrupted after creation"})
            elif fault is not None:
                await route.fulfill(**fault)
            else:
                await route.continue_()

        await page.route("**/*", intercept)

        async def new_chat():
            await page.locator("header").get_by_role("button", name="New Chat").click()
            await expect(page.get_by_text("Integration report", exact=True)).to_have_count(0)

        async def submit(question):
            await page.get_by_placeholder("Ask a question...").fill(question)
            await page.get_by_role("button", name="Send question", exact=True).click()

        async def checked(label):
            assert not errors, errors
            checks.append(label)
            print(f"PASS {label}", flush=True)

        try:
            await page.goto(url)
            await expect(page.get_by_placeholder("Ask a question...")).to_be_visible()
            for name, fault, message in (
                ("HTTP error", {"status": 503, "json": {"error": "Controlled outage"}}, "Research request failed (503)"),
                ("validation error", {"status": 422, "json": {"detail": [{"loc": ["body", "unsupported_option"],
                    "msg": "Extra inputs are not permitted"}]}}, "Research request failed (422)"),
                ("stalled error body", {"status": 504, "json": {"error": "Unavailable body"}}, "Research request failed (504)"),
                ("network interruption", "abort", re.compile("Failed to fetch|NetworkError|Load failed")),
                ("incomplete stream", {"body": "event: response\ndata: " + json.dumps({"schema_version": 2, "type": "research.started", "data": {}}) + "\n\n", "content_type": "text/event-stream"}, "Research stream closed before completion"),
                ("invalid event", {"body": "event: response\ndata: {}\n\n", "content_type": "text/event-stream"}, "Unsupported research event"),
            ):
                await new_chat()
                if name == "stalled error body":
                    await page.evaluate("""() => {
                        window.__responseText = Response.prototype.text;
                        Response.prototype.text = function() {
                            return this.status === 504 ? new Promise(() => {}) : window.__responseText.call(this);
                        };
                    }""")
                faults[("POST", "/solve")] = fault
                await submit(name)
                await expect(page.get_by_text(message, exact=False).first).to_be_visible()
                await expect(page.get_by_role("button", name="Stop research")).not_to_be_visible()
                assert any("[VERITRACE] Research stream failed:" in item["text"] for item in console_messages)
                if name == "HTTP error":
                    assert any("Research request rejected (503):" in item["text"] and "Controlled outage" in item["text"]
                               for item in console_messages)
                if name == "validation error":
                    assert any("Research request rejected (422):" in item["text"] and "unsupported_option" in item["text"]
                               for item in console_messages)
                if name == "stalled error body":
                    assert any("Response body unavailable after 2 seconds" in item["text"] for item in console_messages)
                    await page.evaluate("() => { Response.prototype.text = window.__responseText; }")
                console_messages.clear()
                faults.clear()
                await submit("Recovered " + name)
                await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
                await checked(name + " and recovery")

            await new_chat()
            await submit("Browser integration question")
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            faults[("POST", "/api/export-eval")] = {"status": 500, "json": {"error": "Controlled export failure"}}
            await page.get_by_role("button", name="Export for Eval", exact=True).click()
            await expect(page.get_by_text("Controlled export failure", exact=True)).to_be_visible()
            faults.clear()
            await page.get_by_role("button", name="Failed", exact=True).click()
            await expect(page.get_by_role("button", name="Exported", exact=True)).to_be_visible()
            await checked("export failure is visible and can be retried")

            faults[("GET", "/api/admin/deleted-conversations")] = {"status": 503, "json": {"error": "Controlled outage"}}
            await page.get_by_title("Trash", exact=True).click()
            await expect(page.get_by_text("Failed to load deleted conversations", exact=True)).to_be_visible()
            await page.get_by_role("button", name="Close trash").click()
            faults.clear()
            await page.get_by_title("Trash", exact=True).click()
            await expect(page.get_by_text("Trash is empty", exact=True)).to_be_visible()
            await page.get_by_role("button", name="Close trash").click()
            await checked("trash load failure and reopening after recovery")

            faults[("POST", "/api/conversations")] = {"status": 503, "json": {"error": "Controlled storage outage"}}
            await new_chat()
            await submit("Report during storage outage")
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await page.wait_for_timeout(750)
            await expect(page.get_by_role("alert").filter(has_text="Conversation could not be saved")).to_be_visible()
            assert any("Failed to create conversation:" in item["text"] for item in console_messages)
            conversation = await page.evaluate("localStorage.getItem('veritrace_current_conversation')")
            assert (await page.request.get(url + "/api/conversations/" + conversation)).status == 404
            faults.clear()
            await submit("Report after storage recovery")
            await expect(page.get_by_text("Integration report", exact=True)).to_have_count(2)
            await page.wait_for_timeout(750)
            response = await page.request.get(url + "/api/conversations/" + conversation)
            assert response.status == 200, "Conversation creation never recovers after a transient failure"
            saved = await response.json()
            assert saved["data"]["rounds"][0]["question"] == "Report during storage outage"
            assert saved["data"]["current"]["question"] == "Report after storage recovery"
            await checked("failed conversation creation retries without losing report history")

            faults[("PUT", "/api/conversations/" + conversation)] = {"status": 503, "json": {"error": "Save unavailable"}}
            await submit("Report awaiting save retry")
            await expect(page.get_by_text("Integration report", exact=True)).to_have_count(3)
            alert = page.get_by_role("alert").filter(has_text="Conversation could not be saved")
            await expect(alert).to_be_visible()
            assert any("Failed to save conversation:" in item["text"] for item in console_messages)
            faults.clear()
            await page.get_by_role("button", name="Retry save", exact=True).click()
            await expect(alert).not_to_be_visible()
            saved = await (await page.request.get(url + "/api/conversations/" + conversation)).json()
            assert saved["data"]["current"]["question"] == "Report awaiting save retry"
            await page.reload()
            await expect(page.get_by_text("Integration report", exact=True)).to_have_count(3)
            await checked("visible save failure, explicit retry and persisted reload")

            faults[("POST", "/api/conversations")] = "after_commit"
            await new_chat()
            await submit("Report after interrupted creation response")
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await expect(alert).to_be_visible()
            faults.clear()
            try:
                await page.get_by_role("button", name="Retry save", exact=True).click(timeout=1500)
            except BrowserTimeout:
                # A queued write can recover before the click is delivered.
                await expect(alert).not_to_be_visible()
            await expect(alert).not_to_be_visible()
            await page.reload()
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await checked("creation response loss recovers through the existing conversation")

            expected_webgl_failure = True
            disable_webgl = """() => {
                window.__originalContext = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function(type, ...options) {
                    return type.includes('webgl') ? null : window.__originalContext.call(this, type, ...options);
                };
            }"""
            await page.evaluate(disable_webgl)
            await page.get_by_title("Switch to 3D View").click()
            await expect(page.get_by_role("button", name="Try Again")).to_be_visible()
            await page.evaluate("() => { HTMLCanvasElement.prototype.getContext = window.__originalContext; }")
            await page.get_by_role("button", name="Try Again").click()
            await expect(page.get_by_role("slider", name="Visualization depth")).to_be_visible()
            expected_webgl_failure = False
            await checked("visualization error boundary retries a transient renderer failure")

            await page.get_by_title("Switch to 2D View").click()
            await page.get_by_title("Switch to 3D View").click()
            canvas = page.locator("canvas").last
            await expect(canvas).to_be_visible()
            await canvas.evaluate("element => element.dispatchEvent(new Event('webglcontextlost', {cancelable: true}))")
            await expect(page.get_by_role("button", name="TaskAlpha")).to_be_visible()
            await checked("WebGL context loss falls back to the task view")

            expected_webgl_failure = True
            await page.add_init_script("(" + disable_webgl + ")()")
            await page.reload()
            await expect(page.get_by_placeholder("Ask a question...")).to_be_visible()
            await new_chat()
            await submit("Research without WebGL")
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await page.get_by_title("Switch to 3D View").click()
            await expect(page.get_by_role("button", name="Try Again")).to_be_visible()
            await page.get_by_title("Switch to 2D View").click()
            await expect(page.get_by_role("button", name="TaskAlpha")).to_be_visible()
            await checked("WebGL unavailable at startup preserves the research interface")
        finally:
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(artifacts / "failure-controls.png"), full_page=True)
            (artifacts / "failures.json").write_text(json.dumps({"passed": checks, "page_errors": errors,
                                                               "expected_renderer_errors": expected_errors,
                                                               "console_diagnostics": console_messages}, indent=2) + "\n", encoding="utf-8")
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    parser.add_argument("--artifacts", type=Path, default=Path(__file__).parent / "artifacts/failures")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.artifacts, args.browser))
