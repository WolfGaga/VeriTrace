"""Browser selection shared by the UI verification scripts."""


async def launch_browser(playwright, name):
    try:
        return await getattr(playwright, name).launch(headless=True)
    except Exception:
        if name != "chromium":
            raise
        return await playwright.chromium.launch(channel="msedge", headless=True)
