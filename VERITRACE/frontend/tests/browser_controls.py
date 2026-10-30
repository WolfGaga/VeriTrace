"""Exercise browser controls against the local deterministic API server."""

import argparse
import asyncio
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_support import launch_browser


async def run(url, artifacts, browser_name="chromium"):
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, browser_name)
        context = await browser.new_context(viewport={"width": 1440, "height": 1100})
        await context.add_init_script("window.__openedUrls = []; window.open = url => { window.__openedUrls.push(url); return null; };")

        async def local_only(route):
            if urlparse(route.request.url).hostname in ("127.0.0.1", "localhost"):
                await route.continue_()
            else:
                await route.abort()

        await context.route("**/*", local_only)
        page = await context.new_page()
        page.set_default_timeout(10000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def checked(label):
            assert not errors, errors
            checks.append(label)
            print(f"PASS {label}", flush=True)
            (artifacts / "controls.json").write_text(json.dumps({"passed": checks, "page_errors": errors}, indent=2) + "\n", encoding="utf-8")

        async def new_chat():
            await page.locator("header").get_by_role("button", name="New Chat").click()
            await expect(page.get_by_placeholder("Ask a question...")).to_be_enabled()
            await expect(page.get_by_text("Integration report", exact=True)).to_have_count(0)

        async def submit(question, expected="Integration report"):
            await page.get_by_placeholder("Ask a question...").fill(question)
            await page.get_by_role("button", name="Send question", exact=True).click()
            if expected:
                await expect(page.get_by_text(expected, exact=True).last).to_be_visible(timeout=20000)

        try:
            await page.goto(url)
            await expect(page.get_by_role("button", name="Send question", exact=True)).to_be_disabled()
            await page.get_by_title("Expand sidebar").click()
            await expect(page.get_by_text("History", exact=True)).to_be_visible()
            await page.get_by_title("Collapse sidebar").click()
            await expect(page.get_by_text("History", exact=True)).not_to_be_visible()
            await page.locator("aside").get_by_title("New Chat", exact=True).click()
            await checked("empty input and sidebar expand/collapse")

            await submit("Browser integration question")
            await expect(page.get_by_text("Language: en.", exact=True)).to_be_visible()
            await page.get_by_role("button", name="TaskAlpha").click()
            await expect(page.get_by_text("Verify alpha evidence", exact=True).first).to_be_visible()
            await page.get_by_text("Search trace", exact=True).first.click()
            await expect(page.get_by_text("Alpha search trace", exact=True).first).to_be_visible()
            await page.get_by_text("Search feedback", exact=True).first.click()
            await expect(page.get_by_text(re.compile("Alpha search feedback")).first).to_be_visible()
            await page.get_by_role("button", name="Close task details").first.click()
            await page.get_by_role("button", name="Collapse visualization").click()
            await page.get_by_role("button", name="View Thinking Process").click()
            await expect(page.get_by_role("button", name="TaskAlpha")).to_be_visible()
            await checked("send button, streamed task details, trace, feedback, close and collapse")

            await page.get_by_role("button", name=re.compile("Export for Eval")).click()
            await expect(page.get_by_role("button", name="Exported", exact=True)).to_be_visible()
            await page.locator(".reference-citation").last.hover()
            await expect(page.get_by_text("Alpha source", exact=True).last).to_be_visible()
            await expect(page.get_by_text("Beta source", exact=True).last).to_be_visible()
            await page.get_by_text("Alpha source", exact=True).last.click()
            assert await page.evaluate("window.__openedUrls.includes('https://source.test/alpha')")
            await page.mouse.move(10, 10)
            await checked("real evaluation export and citation popover navigation")

            await page.get_by_title("Show Cognitive Graph").click()
            await page.get_by_text("Alpha entity", exact=True).first.click()
            dialog = page.get_by_role("dialog", name="Cognitive entity details")
            await expect(dialog).to_be_visible()
            await expect(dialog.get_by_text("Alpha detail", exact=True)).to_be_visible()
            await dialog.get_by_role("button", name="Close graph details").click()
            await page.get_by_role("button", name="Supports", exact=True).click()
            await expect(page.get_by_role("dialog", name="Cognitive relation details")).to_be_visible()
            await page.get_by_role("button", name="Close graph details").click()
            graph_language = page.get_by_title("Switch to English")
            if await graph_language.count():
                await graph_language.click()
                await expect(page.get_by_role("heading", name="Cognitive Graph", exact=True)).to_be_visible()
                await page.get_by_title("切换到中文").click()
            else:
                await page.get_by_title("切换到中文").click()
                await page.get_by_title("Switch to English").click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(artifacts / "cognitive-controls.png"), full_page=True)
            await checked("cognitive entity/relation dialogs and graph language toggle")

            await page.get_by_title("Show Thinking Process").click()
            await page.get_by_title("Switch to 3D View").click()
            slider = page.get_by_role("slider", name="Visualization depth")
            await expect(slider).to_be_visible(timeout=20000)
            label = page.get_by_text("TaskAlpha", exact=True).last
            position = await label.bounding_box()
            assert position is not None
            for offset in (35, 25, 45, 55, 65):
                x, y = position["x"] + position["width"] / 2, position["y"] - offset
                await page.mouse.move(x, y)
                if await page.evaluate("document.body.style.cursor === 'pointer'"):
                    await page.mouse.click(x, y)
                    break
            await expect(page.get_by_text("Verify alpha evidence", exact=True).first).to_be_visible()
            await page.get_by_role("button", name="Close task details").first.click()
            await slider.press("Home")
            await expect(slider).to_have_value("0")
            await page.get_by_role("button", name="Next depth").click()
            assert float(await slider.input_value()) > 0
            await page.get_by_role("button", name="Previous depth").click()
            await expect(slider).to_have_value("0")
            await page.get_by_title("Jump to latest").click()
            assert float(await slider.input_value()) > 0
            canvas = page.locator("canvas").last
            await expect(canvas).to_be_visible()
            assert await canvas.evaluate("element => !!(element.getContext('webgl2') || element.getContext('webgl'))")
            bounds = await canvas.bounding_box()
            await page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
            await page.mouse.down()
            await page.mouse.move(bounds["x"] + bounds["width"] / 2 + 60, bounds["y"] + bounds["height"] / 2 + 30, steps=8)
            await page.mouse.up()
            await page.mouse.wheel(0, -120)
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(artifacts / "three-dimensional-controls.png"), full_page=True)
            await page.get_by_title("Switch to 2D View").click()
            await expect(page.get_by_role("button", name="TaskAlpha")).to_be_visible()
            await checked("2D/3D switching, WebGL rendering, depth controls, drag and wheel")

            await page.reload()
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await page.get_by_title("Expand sidebar").click()
            history_item = page.locator("aside").get_by_text("Browser integration question", exact=True)
            await history_item.click(button="right")
            await page.get_by_role("button", name="Delete", exact=True).click()
            await expect(history_item).not_to_be_visible()
            await page.get_by_title("Trash", exact=True).click()
            trash = page.get_by_role("dialog", name="Trash", exact=True)
            await expect(trash.get_by_text("Browser integration question", exact=True)).to_be_visible()
            await trash.get_by_role("button", name="Restore", exact=True).click()
            await expect(trash.get_by_text("Trash is empty", exact=True)).to_be_visible()
            await trash.get_by_role("button", name="Close trash").click()
            await history_item.click()
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await checked("database persistence, history selection, soft deletion and restore")

            await history_item.click(button="right")
            await page.get_by_role("button", name="Delete", exact=True).click()
            await page.get_by_title("Trash", exact=True).click()
            await trash.get_by_role("button", name="Delete", exact=True).click()
            confirm = page.get_by_role("dialog", name="Permanently Delete?", exact=True)
            await confirm.get_by_role("button", name="Cancel", exact=True).click()
            await expect(trash.get_by_text("Browser integration question", exact=True)).to_be_visible()
            await trash.get_by_role("button", name="Delete", exact=True).click()
            await confirm.get_by_role("button", name="Delete Forever", exact=True).click()
            await expect(trash.get_by_text("Trash is empty", exact=True)).to_be_visible()
            await trash.get_by_role("button", name="Close trash").click()
            await checked("permanent deletion cancel/confirm")

            for number in (1, 2):
                await new_chat()
                await submit(f"Trash fixture {number}")
                item = page.locator("aside").get_by_text(f"Trash fixture {number}", exact=True)
                await item.click(button="right")
                await page.get_by_role("button", name="Delete", exact=True).click()
            await page.get_by_title("Trash", exact=True).click()
            await trash.get_by_role("button", name="Empty Trash", exact=True).click()
            empty = page.get_by_role("dialog", name="Empty Trash?", exact=True)
            await empty.get_by_role("button", name="Cancel", exact=True).click()
            await expect(trash.get_by_text("Trash fixture 1", exact=True)).to_be_visible()
            await trash.get_by_role("button", name="Empty Trash", exact=True).click()
            await empty.get_by_role("button", name="Delete Forever", exact=True).click()
            await expect(trash.get_by_text("Trash is empty", exact=True)).to_be_visible()
            await trash.get_by_role("button", name="Close trash").click()
            await checked("empty trash cancel/confirm")

            await new_chat()
            await page.get_by_title("Switch to Chinese prompts").click()
            await submit("Chinese research question")
            await expect(page.get_by_text("Language: cn.", exact=True)).to_be_visible()
            await page.reload()
            assert await page.evaluate("localStorage.getItem('veritrace_lang')") == "cn"
            await page.get_by_title("切换为英文提示词").click()
            await checked("prompt language reaches the real API and persists")

            await new_chat()
            await submit("Failure fixture", expected=None)
            await expect(page.get_by_text("Controlled research failure", exact=False).first).to_be_visible()
            await expect(page.get_by_role("button", name="Stop research")).not_to_be_visible()
            await submit("Recover after failure")
            await checked("failed research restores controls and supports another request")
            await new_chat()
            await submit("Slow fixture", expected=None)
            await page.get_by_placeholder("Ask a question...").fill("A concurrent question")
            await expect(page.get_by_role("button", name="Send question", exact=True)).to_be_disabled()
            await page.get_by_role("button", name="Stop research", exact=True).click()
            await expect(page.get_by_role("button", name="Stop research", exact=True)).not_to_be_visible()
            await submit("Recover after cancellation")
            await checked("stream cancellation and subsequent research")

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.reload()
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await page.get_by_role("button", name="TaskAlpha").last.click()
            await expect(page.get_by_text("Verify alpha evidence", exact=True).last).to_be_visible()
            await page.get_by_role("button", name="Close task details").last.click()
            await page.get_by_role("button", name="Open task details").click()
            await expect(page.get_by_text("Verify alpha evidence", exact=True).last).to_be_visible()
            await page.get_by_role("button", name="Close task details").last.click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(artifacts / "mobile-controls.png"), full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Page overflows the mobile viewport"
            await checked("mobile task drawer, close/reopen and viewport width")

            await page.set_viewport_size({"width": 1440, "height": 1100})
            await new_chat()
            for number in range(5):
                await page.get_by_placeholder("Ask a question...").fill(f"Round {number + 1}")
                await page.get_by_role("button", name="Send question", exact=True).click()
                await expect(page.get_by_text("Integration report", exact=True)).to_have_count(number + 1)
            await expect(page.get_by_text("Conversation limit reached.", exact=False)).to_be_visible()
            await page.get_by_placeholder("Ask a question...").fill("A sixth question")
            await expect(page.get_by_role("button", name="Send question", exact=True)).to_be_disabled()
            await page.get_by_role("button", name="new chat", exact=True).click()
            await expect(page.get_by_text("Integration report", exact=True)).to_have_count(0)
            await checked("five-round limit and its new-chat control")
        finally:
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(artifacts / "last-state.png"), full_page=True)
            (artifacts / "controls.json").write_text(json.dumps({"passed": checks, "page_errors": errors}, indent=2) + "\n", encoding="utf-8")
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    parser.add_argument("--artifacts", type=Path, default=Path(__file__).parent / "artifacts/controls")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.artifacts, args.browser))
