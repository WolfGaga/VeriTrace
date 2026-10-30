"""Verify actual backend progress reaches the page before blocked work returns."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_support import launch_browser


async def run(url, artifacts, browser_name):
    artifacts.mkdir(parents=True, exist_ok=True)
    checks = []
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, browser_name)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(10000)
        errors = []
        saves = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: saves.append(request.url) if request.method == "PUT" else None)

        async def intercept(route):
            if urlparse(route.request.url).hostname not in ("127.0.0.1", "localhost"):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", intercept)
        try:
            await page.goto(url)
            await page.get_by_placeholder("Ask a question...").fill("Progress fixture")
            await page.get_by_role("button", name="Send question", exact=True).click()
            status = page.get_by_role("status", name="Research status").last
            for label, detail in [
                ("Preparing research", None), ("Planning research", None), ("Searching sources", None),
                ("Selecting sources", None), ("Reading sources", None), ("Updating cognitive graph", None),
                ("Planning report outline", None), ("Planning section evidence", "Section 1/2: Findings"),
                ("Writing report", "Section 1/2: Findings"), ("Writing report", "Section 2/2: Analysis"),
                ("Writing executive summary", None), ("Finalizing report and references", None),
            ]:
                await expect(status).to_contain_text(label)
                await expect(page.get_by_text("Analyzing...", exact=True)).to_have_count(0)
                if detail:
                    await expect(status).to_contain_text(detail)
                if label == "Writing report" and detail == "Section 1/2: Findings":
                    await expect(status).to_contain_text("1 completed · 0 active")
                    await page.wait_for_timeout(750)
                    previous_saves = len(saves)
                    await expect(status).to_contain_text("Connected; waiting for the current step", timeout=20000)
                    assert len(saves) == previous_saves, "Heartbeats must not trigger repeated full-session saves"
                    checks.append("server heartbeat remains visible while a writing call is blocked")
                    await page.screenshot(path=str(artifacts / "progress-writing.png"), full_page=True)
                await expect(page.get_by_role("button", name="Stop research", exact=True)).to_be_visible()
                checks.append(label + (f" — {detail}" if detail else ""))
                response = await page.request.post(url + "/api/_test/advance-progress")
                assert response.ok
            await expect(status).to_contain_text("Completed")
            await expect(status).to_contain_text("0 active")
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await expect(page.get_by_role("button", name="Stop research", exact=True)).not_to_be_visible()
            checks.append("completed report stops active indicators")
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(artifacts / "progress-completed.png"), full_page=True)

            await page.locator("header").get_by_role("button", name="New Chat").click()
            await page.get_by_placeholder("Ask a question...").fill("Progress fixture cancellation")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await expect(status).to_contain_text("Preparing research")
            await page.get_by_role("button", name="Stop research", exact=True).click()
            await expect(status).to_contain_text("Stopped")
            await expect(status).to_contain_text("0 active")
            checks.append("cancellation stops stage and task indicators")
            await page.locator("header").get_by_role("button", name="New Chat").click()
            await page.get_by_placeholder("Ask a question...").fill("Task failure fixture")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await expect(status).to_contain_text("1 failed")
            await expect(page.get_by_role("button", name="TaskAlpha Failed", exact=True)).to_be_visible()
            await page.request.post(url + "/api/_test/advance-progress")
            await expect(status).to_contain_text("Failed")
            await expect(status).to_contain_text("0 active")
            await expect(page.get_by_role("button", name="Stop research", exact=True)).not_to_be_visible()
            checks.append("task and request failures remain explicit in the graph and status")
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(artifacts / "progress-failed.png"), full_page=True)
            assert not errors, errors
            print("PASS " + "\nPASS ".join(checks), flush=True)
        finally:
            (artifacts / "progress.json").write_text(json.dumps({"passed": checks, "page_errors": errors}, indent=2), encoding="utf-8")
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--browser", default="chromium")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.artifacts, args.browser))
