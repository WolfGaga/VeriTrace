"""Exercise recovery controls through the production UI and real local API."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect
from browser_support import launch_browser


async def run(url, artifacts, browser_name):
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, browser_name)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(url)
            await page.get_by_placeholder("Ask a question...").fill("Recovery fixture")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await expect(page.get_by_role("status", name="Research status")).to_contain_text("Failed")
            await expect(page.get_by_text("Retained draft", exact=True)).to_be_visible()
            await expect(page.get_by_role("button", name="Continue research", exact=True)).to_be_visible()
            await expect(page.get_by_role("button", name="Export for Eval", exact=True)).to_have_count(0)
            await page.get_by_text("Browse collected findings and sources").click()
            await expect(page.get_by_text("Retained useful evidence [[0]]", exact=True)).to_be_visible()
            checks.append("failed research retains draft, findings, and usable recovery controls without evaluation export")
            async with page.expect_download() as pending:
                await page.get_by_role("button", name="Download partial findings", exact=True).click()
            download = await pending.value
            target = artifacts / "partial-findings.json"
            await download.save_as(target)
            content = json.loads(target.read_text(encoding="utf-8"))
            assert content["incomplete"] is True and content["tasks"]["TaskAlpha"]["result"]["answer"]
            assert "Private planner checkpoint" not in target.read_text(encoding="utf-8")
            checks.append("download preserves useful material and labels it incomplete")
            await page.screenshot(path=str(artifacts / "failed-recoverable.png"), full_page=True)
            await page.reload()
            await expect(page.get_by_role("button", name="Continue research", exact=True)).to_be_visible()
            await expect(page.get_by_text("Retained draft", exact=True)).to_be_visible()
            checks.append("page refresh restores saved partial research")
            await page.get_by_role("button", name="Write report from findings", exact=True).click()
            await expect(page.get_by_text("Recovered final report", exact=True)).to_be_visible()
            await expect(page.get_by_role("status", name="Research status")).to_contain_text("Completed")
            await expect(page.get_by_role("button", name="Export for Eval", exact=True)).to_be_visible()
            assert await page.get_by_role("status", name="Research status").count() == 1
            checks.append("report recovery completes in the same conversation round")
            await page.screenshot(path=str(artifacts / "recovered-report.png"), full_page=True)
            await page.locator("header").get_by_role("button", name="New Chat").click()
            await page.get_by_placeholder("Ask a question...").fill("Recovery fixture continue")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await page.get_by_role("button", name="Continue research", exact=True).click()
            await expect(page.get_by_text("Recovered final report", exact=True)).to_be_visible()
            checks.append("continue research restores completed findings")
            await page.locator("header").get_by_role("button", name="New Chat").click()
            await page.get_by_placeholder("Ask a question...").fill("Recovery fixture active")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await expect(page.get_by_text("Retained draft", exact=True)).to_be_visible()
            await page.locator('.reference-citation').last.hover()
            await expect(page.get_by_text('Alpha source',exact=True).last).to_be_visible()
            await page.mouse.move(10,10)
            checks.append("running partial reports expose their collected citation sources before completion")
            await page.get_by_role("button", name="Stop search and write report", exact=True).click()
            await expect(page.get_by_text("Recovered final report", exact=True)).to_be_visible()
            checks.append("active research can stop and write from retained findings without trapping the user")
            await page.locator("header").get_by_role("button", name="New Chat").click()
            async def unavailable(route):
                await route.fulfill(status=503, content_type='application/json', body='{"error":"Temporary service outage"}')
            await page.route('**/solve', unavailable)
            await page.get_by_placeholder("Ask a question...").fill("Retry fixture")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await expect(page.get_by_role("button", name="Retry research", exact=True)).to_be_visible()
            await page.unroute('**/solve', unavailable)
            await page.get_by_role("button", name="Retry research", exact=True).click()
            await expect(page.get_by_text("Integration report", exact=True)).to_be_visible()
            checks.append("failure before checkpoint creation offers one-click retry and preserves the failed attempt")
            await page.locator("header").get_by_role("button", name="New Chat").click()
            solve_requests = []
            page.on('request', lambda request: solve_requests.append(request.url) if request.url.endswith('/solve') else None)
            async def lose_completed_frame(route):
                response = await route.fetch()
                wire = await response.text()
                blocks = [block for block in wire.replace('\r\n', '\n').split('\n\n') if block.strip()]
                kept = []
                for block in blocks:
                    data = next((line[6:] for line in block.splitlines() if line.startswith('data: ')), None)
                    if data and json.loads(data)['type'] == 'research.completed':
                        continue
                    kept.append(block)
                await route.fulfill(status=200, content_type='text/event-stream', body='\n\n'.join(kept)+'\n\n')
            await page.route('**/solve', lose_completed_frame)
            await page.get_by_placeholder("Ask a question...").fill("Completed recovery fixture")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await expect(page.get_by_role("button", name="Continue research", exact=True)).to_be_visible()
            await page.unroute('**/solve', lose_completed_frame)
            await page.get_by_role("button", name="Continue research", exact=True).click()
            await expect(page.get_by_text('Saved backend report',exact=True)).to_be_visible()
            assert len(solve_requests) == 1, 'A completed server result must not start another paid research attempt'
            checks.append("lost final stream frame restores the completed server report without starting another request")
            await page.locator("header").get_by_role("button", name="New Chat").click()
            header_requests = []
            page.on('request', lambda request: header_requests.append(request.url) if request.url.endswith('/solve') else None)
            async def lose_all_frames(route):
                response = await route.fetch()
                await route.fulfill(status=200, headers={'Content-Type':'text/event-stream',
                    'X-Research-Run-ID':response.headers['x-research-run-id'],
                    'X-Research-Attempt':response.headers['x-research-attempt']}, body='')
            await page.route('**/solve', lose_all_frames)
            await page.get_by_placeholder("Ask a question...").fill("Completed recovery fixture")
            await page.get_by_role("button", name="Send question", exact=True).click()
            await expect(page.get_by_role("button", name="Retry research", exact=True)).to_be_visible()
            await page.unroute('**/solve', lose_all_frames)
            await page.get_by_role("button", name="Retry research", exact=True).click()
            await expect(page.get_by_text('Saved backend report',exact=True)).to_be_visible()
            assert len(header_requests) == 1
            checks.append("response headers recover a completed report even when every stream frame is lost")
            assert not errors, errors
            print("PASS " + "\nPASS ".join(checks), flush=True)
        finally:
            (artifacts / "recovery.json").write_text(json.dumps({"passed": checks, "page_errors": errors}, indent=2), encoding="utf-8")
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--browser', default='chromium')
    args = parser.parse_args()
    asyncio.run(run(args.url, args.artifacts, args.browser))
