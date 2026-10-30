"""Check that report markup remains readable without executing embedded code."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_support import launch_browser


async def run(url, artifacts, browser_name):
    artifacts.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, browser_name)
        page = await browser.new_page()
        await page.add_init_script("window.__openedUrls = []; window.open = url => { window.__openedUrls.push(url); return null; };")
        async def local_only(route):
            if urlparse(route.request.url).hostname in ("localhost", "127.0.0.1"):
                await route.continue_()
            else:
                await route.abort()
        await page.route("**/*", local_only)
        try:
            await page.goto(url)
            await page.get_by_placeholder("Ask a question...").fill("Markup safety")
            await page.get_by_role("button", name="Send question").click()
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            await expect(page.get_by_role("cell", name="Safe HTML table")).to_be_visible()
            picture = page.get_by_role("img", name="Local test image")
            await expect(picture).to_be_visible()
            await expect(picture).to_have_js_property("complete", True)
            assert await picture.evaluate("image => image.naturalWidth > 0")
            await page.wait_for_timeout(250)
            executed = await page.evaluate("window.__untrusted_report_code === true")
            (artifacts / "markup.json").write_text(json.dumps({"embedded_code_executed": executed}), encoding="utf-8")
            assert not executed, "Report HTML executed code in the application origin"
            await expect(page.locator("iframe")).to_have_count(0)
            link = page.get_by_role("link", name="Details link")
            fragment = (await link.get_attribute("href")).lstrip("#")
            await expect(page.locator("[id='" + fragment + "']")).to_have_text("Details heading")
            await link.click()
            await expect(page.get_by_role("link", name="Back to top")).to_have_attribute("href", "#top")
            await page.locator(".reference-citation").last.hover()
            await expect(page.get_by_text("Unsafe source", exact=True).last).to_be_visible()
            await page.get_by_text("Unsafe source", exact=True).last.click()
            assert await page.evaluate("window.__openedUrls.length") == 0
            await page.mouse.move(0, 0)
            await page.get_by_role("button", name="TaskAlpha").click()
            unsafe_source = page.locator("a").filter(has_text="Unsafe source").first
            assert await unsafe_source.get_attribute("href") is None
            (artifacts / "markup.json").write_text(json.dumps({"embedded_code_executed": False,
                "safe_table_image_and_anchor": True, "unsafe_source_links_blocked": True}), encoding="utf-8")
            print("PASS safe tables/images/anchors, embedded-code isolation and source URL filtering", flush=True)
        finally:
            await page.screenshot(path=str(artifacts / "markup.png"), full_page=True)
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--browser", choices=("chromium", "firefox", "webkit"), default="chromium")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.artifacts, args.browser))
