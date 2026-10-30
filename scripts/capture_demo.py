"""Record a deliberate two-minute product walkthrough with visible interactions."""

import argparse
import asyncio
import functools
import http.server
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"
CAPTURE = ROOT / ".demo-capture"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class Director:
    def __init__(self, page, frame):
        self.page, self.frame = page, frame
        self.started = time.monotonic()
        self.chapters = []

    async def hold(self, seconds):
        await self.page.wait_for_timeout(seconds * 1000)

    async def click(self, target):
        await target.wait_for(state="visible")
        await target.scroll_into_view_if_needed()
        box = await target.bounding_box()
        point = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
        await self.page.evaluate(
            "([x,y]) => window.__VERITRACE_FILM__.cursorTo(x,y)", point
        )
        await self.page.mouse.move(*point)
        await self.page.evaluate("window.__VERITRACE_FILM__.click()")
        await target.click()
        await self.hold(0.35)

    async def show(self, target, margin=80):
        await target.evaluate(
            "(element,margin) => { element.style.scrollMarginTop=margin+'px'; element.scrollIntoView({block:'start',behavior:'smooth'}); }",
            margin,
        )
        await self.hold(0.85)

    async def scroll(self, target, delta, horizontal=False):
        box = await target.bounding_box()
        if box:
            await self.page.evaluate(
                "([x,y]) => window.__VERITRACE_FILM__.cursorTo(x,y)",
                [
                    box["x"] + box["width"] * 0.75,
                    box["y"] + min(box["height"] * 0.5, 250),
                ],
            )
        await target.evaluate(
            """(element,options) => new Promise(resolve => {
          const property=options.horizontal?'scrollLeft':'scrollTop';
          let node=element;
          if(!options.horizontal) while(node.parentElement && node.scrollHeight<=node.clientHeight+2) node=node.parentElement;
          const start=node[property], maximum=options.horizontal?node.scrollWidth-node.clientWidth:node.scrollHeight-node.clientHeight;
          const end=Math.min(maximum,Math.max(0,start+options.delta)), begin=performance.now();
          function step(now){const p=Math.min(1,(now-begin)/1200);node[property]=start+(end-start)*(.5-.5*Math.cos(Math.PI*p));if(p<1)requestAnimationFrame(step);else resolve();}
          requestAnimationFrame(step);
        })""",
            {"delta": delta, "horizontal": horizontal},
        )

    async def advance(self, count):
        await self.frame.evaluate(
            "count => window.__VERITRACE_REPLAY__.advanceTo(count)", count
        )
        await self.hold(0.8)

    async def chapter(self, number, title, description, duration, actions):
        start = time.monotonic()
        await self.page.evaluate(
            "values => window.__VERITRACE_FILM__.chapter(...values)",
            [number, title, description],
        )
        await actions()
        await self.hold(max(0, duration - (time.monotonic() - start)))
        self.chapters.append(
            {
                "start": round(start - self.started, 2),
                "end": round(time.monotonic() - self.started, 2),
                "title": title,
                "description": description,
            }
        )


async def record(port):
    data = json.loads(
        (ROOT / "website/demo/recording.json").read_text(encoding="utf-8")
    )
    events = data["events"]

    def find(kind):
        return next(
            index for index, event in enumerate(events) if event["type"] == kind
        )

    revisions = [
        index
        for index, event in enumerate(events)
        if event["type"] == "cognitive.updated"
        and "restructured" in event["data"].get("content", "")
    ]
    revision = revisions[0]
    first_task_event = next(
        event for event in events if event["type"] == "task.completed"
    )
    first_task = first_task_event["data"]["task"]["id"]
    original_graph = events[find("research.started")]["data"]["cognitive_graph"]
    revised_graph = events[revision]["data"]["cognitive_graph"]
    new_node = next(
        key for key in revised_graph["nodes"] if key not in original_graph["nodes"]
    )
    new_relation = next(
        edge
        for edge in revised_graph["edges"].values()
        if edge.get("target_node_id") == new_node
    )
    next_batch = next(
        index
        for index in range(revision + 1, len(events))
        if events[index]["type"] == "tasks.updated"
    )
    regional_update = next(
        index
        for index in range(next_batch + 1, len(events))
        if events[index]["type"] == "cognitive.updated"
    )
    later_batch = next(
        index
        for index in range(regional_update + 1, len(events))
        if events[index]["type"] == "tasks.updated"
    )
    errors, external = [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            record_video_dir=str(CAPTURE),
            record_video_size={"width": 1440, "height": 1000},
        )
        page = await context.new_page()
        page.set_default_timeout(7000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: external.append(request.url)
            if request.url.startswith(("http://", "https://"))
            and not request.url.startswith(f"http://127.0.0.1:{port}/")
            else None,
        )
        try:
            await page.goto(f"http://127.0.0.1:{port}/demo/?capture=1")
            await page.wait_for_function(
                "!document.querySelector('#play').disabled", timeout=30000
            )
            await page.evaluate("document.fonts.ready")
            frame = next(frame for frame in page.frames if frame != page.main_frame)
            director = Director(page, frame)
            await director.hold(6)
            await page.evaluate("window.__VERITRACE_FILM__.introDone()")
            await frame.evaluate("window.__VERITRACE_REPLAY__.start()")
            await frame.wait_for_function(
                "!!window.__VERITRACE_REPLAY__.state.controller"
            )
            await director.advance(find("tasks.updated") + 1)

            async def scope():
                await director.hold(5)
                await director.click(frame.get_by_title("Show Cognitive Graph").first)
                switch = frame.get_by_title("Switch to English")
                if await switch.count():
                    await director.click(switch.first)
                await director.show(
                    frame.get_by_role(
                        "heading", name="Cognitive Graph", exact=True
                    ).first
                )
                await director.hold(3)

            await director.chapter(
                "01",
                "Set the research scope",
                "The initial graph makes the concepts and open questions visible.",
                14,
                scope,
            )

            async def findings():
                await director.advance(find("task.completed") + 1)
                await director.click(frame.get_by_title("Show Thinking Process").first)
                await director.click(frame.get_by_role("button", name=first_task).first)
                await director.show(
                    frame.get_by_role("heading", name="Findings", exact=True).last
                )
                await director.hold(6)
                pane = frame.locator("div.p-5.space-y-5.overflow-auto:visible").first
                await director.scroll(pane, 360)
                await director.hold(5)
                for citation in await pane.locator(".reference-citation").all():
                    box = await citation.bounding_box()
                    if box and 180 < box["y"] < 850:
                        await page.evaluate(
                            "([x,y]) => window.__VERITRACE_FILM__.cursorTo(x,y)",
                            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
                        )
                        await citation.hover()
                        await director.hold(4)
                        break
                await page.screenshot(path=str(ASSETS / "research.png"))
                await director.click(
                    frame.get_by_role("button", name="Close task details").first
                )

            await director.chapter(
                "02",
                "Inspect the first findings",
                "Open a completed task and read the evidence while the investigation continues.",
                24,
                findings,
            )

            async def revision_scene():
                await director.advance(revision)
                await director.click(frame.get_by_title("Show Cognitive Graph").first)
                switch = frame.get_by_title("Switch to English")
                if await switch.count():
                    await director.click(switch.first)
                heading = frame.get_by_role(
                    "heading", name="Cognitive Graph", exact=True
                ).first
                await director.show(heading)
                await director.hold(4)
                await director.advance(revision + 1)
                await director.hold(3)
                await page.screenshot(path=str(ASSETS / "cognitive-graph.png"))
                label = revised_graph["nodes"][new_node]["name"]
                await director.click(frame.get_by_text(label, exact=True).first)
                dialog = frame.get_by_role("dialog", name="Cognitive entity details")
                await dialog.wait_for()
                await director.hold(5)
                await director.scroll(dialog.locator("div.overflow-y-auto").first, 260)
                await director.hold(3)
                await director.click(
                    dialog.get_by_role("button", name="Close graph details")
                )
                await director.click(
                    frame.get_by_role(
                        "button", name=new_relation["relation_type"], exact=True
                    ).first
                )
                await frame.get_by_role(
                    "dialog", name="Cognitive relation details"
                ).wait_for()
                await director.hold(4)
                await director.click(
                    frame.get_by_role("button", name="Close graph details")
                )

            await director.chapter(
                "03",
                "Watch the graph evolve",
                "Five concepts become eight. New branches turn broad inquiry into targeted research.",
                28,
                revision_scene,
            )

            async def followup():
                await director.advance(next_batch + 1)
                await director.click(frame.get_by_title("Show Thinking Process").first)
                await director.show(
                    frame.get_by_role(
                        "heading", name="Thinking Process", exact=True
                    ).first
                )
                await director.hold(5)
                await director.advance(regional_update + 1)
                await director.click(frame.get_by_title("Show Cognitive Graph").first)
                switch = frame.get_by_title("Switch to English")
                if await switch.count():
                    await director.click(switch.first)
                node = events[regional_update]["data"]["cognitive_graph"]["nodes"][
                    new_node
                ]
                label = (node.get("discovered_items") or [node["name"]])[0]
                await director.click(frame.get_by_text(label, exact=True).first)
                dialog = frame.get_by_role("dialog", name="Cognitive entity details")
                await dialog.wait_for()
                await dialog.get_by_text("Item Attributes", exact=False).wait_for()
                await director.hold(5)
                await director.scroll(dialog.locator("div.overflow-y-auto").first, 330)
                await director.hold(4)
                await director.click(
                    dialog.get_by_role("button", name="Close graph details")
                )
                await director.advance(later_batch + 1)

            await director.chapter(
                "04",
                "Follow the revised investigation",
                "New findings fill the regional branches; remaining gaps stay visible.",
                22,
                followup,
            )

            async def report_scene():
                await director.advance(find("research.partial") + 2)
                draft = frame.get_by_text("Draft report", exact=False).first
                if await draft.count():
                    await director.show(draft)
                await director.hold(3)
                await director.advance(len(events))
                summary = frame.get_by_role(
                    "heading", name="Executive Summary", exact=True
                ).first
                await director.show(summary)
                await director.hold(6)
                await page.screenshot(path=str(ASSETS / "report.png"))
                await director.scroll(summary, 380)
                await director.hold(5)
                citation = frame.locator(".reference-citation:visible").first
                if await citation.count():
                    box = await citation.bounding_box()
                    await page.evaluate(
                        "([x,y]) => window.__VERITRACE_FILM__.cursorTo(x,y)",
                        [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
                    )
                    await citation.hover()
                    await director.hold(3)
                    await page.screenshot(path=str(ASSETS / "citations.png"))

            await director.chapter(
                "05",
                "Read the report and trace its sources",
                "A structured report retains the links from conclusions to collected evidence.",
                22,
                report_scene,
            )
            await page.evaluate("window.__VERITRACE_FILM__.outro()")
            await director.hold(6)
            elapsed = time.monotonic() - director.started
            video = page.video
            await context.close()
            video_path = await video.path()
        except Exception:
            await page.screenshot(path=str(CAPTURE / "capture-failure.png"))
            (CAPTURE / "capture-failure.json").write_text(
                json.dumps({"errors": errors, "external": external}), encoding="utf-8"
            )
            await context.close()
            raise
        finally:
            await browser.close()
    assert not errors, errors
    assert not external, external
    return {
        "page_errors": errors,
        "external_requests": external,
        "video": video_path,
        "presentation_seconds": elapsed,
        "chapters": director.chapters,
        "original_duration_seconds": data["metadata"]["original_duration_seconds"],
        "query_id": data["metadata"]["query_id"],
        "presentation": data["metadata"].get("presentation", "Recorded research"),
    }


def duration(ffmpeg, path):
    probe = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(path)], capture_output=True, text=True
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not match:
        raise ValueError("Unable to read media duration")
    return 3600 * int(match[1]) + 60 * int(match[2]) + float(match[3])


def write_captions(chapters, path):
    def timestamp(seconds):
        minutes, milliseconds = divmod(round(seconds * 1000), 60000)
        hours, minutes = divmod(minutes, 60)
        seconds, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"

    cues = ["WEBVTT\n"]
    for chapter in chapters:
        cues.append(
            f"{timestamp(chapter['start'])} --> {timestamp(chapter['end'])}\n"
            f"{chapter['title']}. {chapter['description']}\n"
        )
    path.write_text("\n".join(cues), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument(
        "--music", required=True, type=Path,
        help="Path to Cylinder Five by Chris Zabriskie; see docs/media-credits.md",
    )
    parser.add_argument("--output", type=Path, default=ASSETS / "walkthrough.mp4")
    options = parser.parse_args()
    CAPTURE.mkdir(exist_ok=True)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / ".site"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = asyncio.run(record(server.server_port))
    finally:
        server.shutdown()
        server.server_close()
    length = result["presentation_seconds"]
    offset = max(0, duration(options.ffmpeg, result["video"]) - length - 0.15)
    filters = (
        f"[0:v]fade=t=in:st=0:d=0.45:color=0x142c42,fade=t=out:st={length - 0.6:.3f}:d=0.6:color=0x142c42[v];"
        f"[1:a]loudnorm=I=-24:TP=-2:LRA=7,afade=t=in:d=2,afade=t=out:st={length - 5:.3f}:d=5[a]"
    )
    subprocess.run(
        [
            str(options.ffmpeg),
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{offset:.3f}",
            "-i",
            result["video"],
            "-i",
            str(options.music),
            "-filter_complex",
            filters,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{length:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "4",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(options.output),
        ],
        check=True,
    )
    gif = ASSETS / "demo-preview.gif"
    subprocess.run(
        [
            str(options.ffmpeg),
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "43",
            "-t",
            "18",
            "-i",
            str(options.output),
            "-filter_complex",
            "fps=4,scale=850:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer",
            str(gif),
        ],
        check=True,
    )
    result.update(
        video_duration_seconds=duration(options.ffmpeg, options.output),
        mp4_bytes=options.output.stat().st_size,
        gif_bytes=gif.stat().st_size,
        music="Cylinder Five by Chris Zabriskie, CC BY 4.0",
        music_target_lufs=-24,
    )
    write_captions(result["chapters"], options.output.with_suffix(".vtt"))
    (CAPTURE / "capture.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
