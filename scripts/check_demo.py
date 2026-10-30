"""Check the static replay under a project URL prefix without external requests."""

import asyncio
import functools
import http.server
from pathlib import Path
import threading

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]


class ProjectHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def translate_path(self, path):
        if path.startswith("/VeriTrace/"):
            path = path[len("/VeriTrace") :]
        return super().translate_path(path)


async def verify(port):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        for width, height in [(1440, 1000), (390, 844)]:
            page = await browser.new_page(viewport={"width": width, "height": height})
            errors, external = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def allow_local(route):
                if route.request.url.startswith(f"http://127.0.0.1:{port}/"):
                    await route.continue_()
                else:
                    external.append(route.request.url)
                    await route.abort()

            await page.route("**/*", allow_local)
            await page.goto(f"http://127.0.0.1:{port}/VeriTrace/demo/")
            await page.wait_for_function("!document.querySelector('#play').disabled")
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            )
            await page.locator("#play").click()
            await page.wait_for_function("!document.querySelector('#pause').disabled")
            frame = next(frame for frame in page.frames if frame != page.main_frame)
            assert "/VeriTrace/demo/app/" in frame.url
            await frame.wait_for_function("window.__VERITRACE_REPLAY__.state.index > 1")
            await page.locator("#pause").click()
            await page.wait_for_timeout(150)
            index = await frame.evaluate("window.__VERITRACE_REPLAY__.state.index")
            await page.wait_for_timeout(400)
            assert (
                await frame.evaluate("window.__VERITRACE_REPLAY__.state.index") == index
            )
            await page.locator("#finish").click()
            await page.wait_for_function(
                "document.querySelector('#status').textContent.startsWith('Recorded run complete')"
            )
            await page.wait_for_timeout(700)
            assert (await page.locator("#status").inner_text()).startswith(
                "Recorded run complete"
            )
            await frame.get_by_role(
                "heading", name="Executive Summary", exact=True
            ).first.wait_for()
            first_task = await frame.evaluate(
                "window.__VERITRACE_REPLAY__.state.data.events.find(event=>event.type==='task.completed').data.task.id"
            )
            await frame.get_by_role("button", name=first_task).first.click()
            pane = frame.locator("div.p-5.space-y-5.overflow-auto:visible").first
            await pane.wait_for()
            await page.wait_for_timeout(400)
            bounds = await pane.evaluate(
                "element=>({height:element.clientHeight,content:element.scrollHeight,viewport:innerHeight})"
            )
            assert 100 < bounds["height"] < bounds["viewport"], bounds
            assert bounds["content"] > bounds["height"], bounds
            before = await frame.evaluate(
                "document.querySelector('div.flex-1.overflow-auto').scrollTop"
            )
            await pane.hover()
            await page.mouse.wheel(0, 400)
            await page.wait_for_timeout(500)
            assert await pane.evaluate("element=>element.scrollTop") > 0
            assert (
                abs(
                    await frame.evaluate(
                        "document.querySelector('div.flex-1.overflow-auto').scrollTop"
                    )
                    - before
                )
                < 2
            )
            await frame.get_by_role("button", name="Close task details").first.click()
            assert not errors, errors
            assert not external, external
            await page.close()
        await browser.close()


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(ProjectHandler, directory=str(ROOT / ".site")),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        asyncio.run(verify(server.server_port))
    finally:
        server.shutdown()
        server.server_close()
    print(
        "Recorded demo passed: project prefix, desktop/mobile layout, pause, completion, independent task-detail scrolling and offline requests."
    )
