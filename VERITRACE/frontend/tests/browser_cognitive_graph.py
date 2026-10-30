"""Offline graph rendering checks using a supplied public evidence snapshot."""

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


async def check_graph(browser, url, graph, references, label, expected_values, node_id):
    page = await browser.new_page(viewport={"width": 1440, "height": 1100})
    errors, records = [], {}
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def intercept(route):
        request = route.request
        address = urlparse(request.url)
        if address.hostname not in ("127.0.0.1", "localhost"):
            await route.abort()
            return
        if address.path.startswith("/api/conversations"):
            key = address.path.removeprefix("/api/conversations").strip("/")
            if request.method == "POST" and not key:
                data = request.post_data_json
                records[data["id"]] = {**data, "created_at": 1, "updated_at": 1}
                result = {"success": True}
            elif request.method in ("PUT", "POST"):
                records[key].update(request.post_data_json)
                result = {"success": True}
            else:
                result = records[key] if key else list(records.values())
            await route.fulfill(json=result)
            return
        if address.path == "/solve":
            events = [
                ("research.started", {"question": graph.get("original_question", label), "cognitive_graph": graph}),
                ("research.completed", {"article": "Offline graph-rendering fixture.", "tasks": [], "cognitive_graph": graph, "ref2url": references}),
            ]
            wire = ''.join('event: response\ndata: ' + json.dumps({"schema_version": 2, "type": kind, "data": data}, ensure_ascii=False) + '\n\n' for kind, data in events)
            await route.fulfill(body=wire, content_type="text/event-stream")
            return
        await route.continue_()

    await page.route("**/*", intercept)
    await page.goto(url)
    await page.get_by_placeholder("Ask a question...").fill(label)
    await page.get_by_placeholder("Ask a question...").press("Enter")
    await expect(page.get_by_text("Offline graph-rendering fixture.", exact=True)).to_be_visible(timeout=30000)
    await page.get_by_title("Show Cognitive Graph").click()
    entities = graph.get("nodes") or {item["id"]: item for item in graph["entities"]}
    for node in entities.values():
        name = (node.get("discovered_items") or [node["name"].lstrip('?')])[0]
        await expect(page.get_by_text(name, exact=True).first).to_be_visible()
    artifacts = Path(__file__).parent / "artifacts"
    artifacts.mkdir(exist_ok=True)
    await page.screenshot(path=str(artifacts / f"{label}-graph.png"), full_page=True)
    selected = entities[node_id]
    name = (selected.get("discovered_items") or [selected["name"].lstrip('?')])[0]
    await page.get_by_text(name, exact=True).first.click()
    for text in expected_values:
        finding = page.get_by_text(text, exact=False).first
        await expect(finding).to_be_visible()
        await finding.scroll_into_view_if_needed()
    await page.screenshot(path=str(artifacts / f"{label}-findings.png"), full_page=True)
    await page.wait_for_timeout(650)
    key = await page.evaluate("localStorage.getItem('veritrace_current_conversation')")
    assert records[key]["data"]["current"]["cognitiveGraph"] == graph, "Rendering mutated stored graph data"
    assert not errors, errors
    await page.close()


async def main(url, evidence):
    snapshot = json.loads(evidence.read_text(encoding="utf-8"))
    graph = snapshot["cognitive_graph"]
    refs = snapshot.get("ref2url", {})
    node_id, node = next((key, value) for key, value in graph["nodes"].items()
                         if value.get("item_findings") and value.get("unexpected_discoveries"))
    actual_value = next(iter(next(iter(node["item_findings"].values())).values()))
    actual_unexpected = node["unexpected_discoveries"][0]
    assert isinstance(actual_value, str) and isinstance(actual_unexpected, str), "Expected current flat-string graph"
    historical = deepcopy(graph)
    historical["nodes"][node_id].update({
        "item_findings": {"Historical item": {"Attribute": {"value": "Historical item finding", "task": "original-task"}}},
        "cross_item_findings": {"Ranked": {"value": ["Historical rank A", {"value": "Historical rank B", "task": "second-task"}], "task": "original-task"}},
        "unexpected_discoveries": [{"value": "Historical unexpected discovery", "task": "original-task"}],
    })
    historical_entities = {
        "entities": list(historical["nodes"].values()),
        "relations": [{**edge, "source": edge.get("source_node_id", ""), "target": edge.get("target_node_id", "")} for edge in historical["edges"].values()],
        "original_question": historical.get("original_question"),
    }
    legacy_values = ["Historical item finding", "Historical rank A", "Historical rank B", "Historical unexpected discovery"]
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception:
            browser = await playwright.chromium.launch(channel="msedge", headless=True)
        await check_graph(browser, url, graph, refs, "actual-cognitive", [actual_value, actual_unexpected], node_id)
        await check_graph(browser, url, historical, refs, "historical-cognitive", legacy_values, node_id)
        await check_graph(browser, url, historical_entities, refs, "historical-entities", legacy_values, node_id)
        await browser.close()
    summary = {"evidence_file": str(evidence), "nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "references": len(refs),
               "cases": ["actual flat-string graph", "historical tagged node dictionary", "historical tagged entity array"],
               "stored_graph_unchanged": True, "external_requests": 0}
    (Path(__file__).parent / "artifacts/cognitive-browser-qa.json").write_text(json.dumps(summary, indent=2) + '\n', encoding="utf-8")
    print(f"Graph browser QA passed: {summary['nodes']} nodes, {summary['edges']} edges, current/historical findings; raw attribution unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8097")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.url, args.evidence))
