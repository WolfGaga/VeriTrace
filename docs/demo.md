# Recorded research demo

[Interactive replay](https://wolfgaga.github.io/VeriTrace/demo/) · [Video](https://wolfgaga.github.io/VeriTrace/#demo)

The demo uses the production React interface and selected user-facing events from a completed real-network run. It investigates research teams, findings, collaborations, funding and future directions at the intersection of mathematics and quantum computing, using DeepResearch Bench question 14.

- Original duration: **68 minutes 10 seconds**.
- Completed search tasks: **12**.
- Cognitive graph: **5 initial concepts, 8 final concepts**.
- Presentation: English translation of the recorded Chinese-language research.
- Video: approximately two minutes, with visible clicks, deliberate scrolling, chapter captions and a restrained instrumental soundtrack.

The guided video pauses at meaningful states so viewers can inspect task findings, node and relation details, graph revision and report citations. Waiting periods are removed; reading and interaction are given time. The elapsed indicator inside the interface measures playback time. It is not a claim that live research completed in the video's duration. Original duration and usage counters refer to the source run, not translation or playback. No model or search provider is called, and no API key is required.

Pause to inspect findings, switch 2D/3D views, open cognitive details or inspect citations. You can jump to the report or reset the replay. Input, history and export controls operate on the recorded demonstration only; they do not submit new research or save to a server. Install the application to research a new question.

The published recording excludes credentials, model messages, generated execution code, raw backend logs and original run identifiers. Selected questions, findings, graph state, public source URLs and report text are retained in recorded order. Presentation text is translated into English; task/graph IDs, state transitions, citations and source URLs are preserved. Numerical quantities are retained while dates and Chinese number units use English notation. Original claims and unresolved gaps remain; this is not a new fact-checking run. This is a product walkthrough, not a benchmark evaluation.

## Build locally

```bash
cd VERITRACE/frontend/React
npm ci
cd ../../..
python scripts/build_site.py
python -m http.server 8098 --directory .site
```

Open `http://localhost:8098/demo/`. The build adds the replay bridge only to the generated site and does not modify the research runtime. `.site/` is ignored by Git.

With Playwright and Chromium installed, `python scripts/check_demo.py` checks playback, completion and mobile layout under a project URL prefix. `python scripts/capture_demo.py --ffmpeg /path/to/ffmpeg --music /path/to/cylinders-05.m4a` regenerates the screenshots, MP4, chapter subtitles and preview GIF from the same recording. Download the credited track from the artist's album page; see [media credits](media-credits.md).

`scripts/export_demo_recording.py` prepares a recording from an events JSONL file and its completed result. Inspect and redact recordings before publishing; only share topics and results you are permitted to disclose.

## Soundtrack and poster

The walkthrough uses a shortened, level-adjusted and faded excerpt of "Cylinder Five" by Chris Zabriskie, licensed under CC BY 4.0. The poster is the supplied EMNLP 2026 document, retained unchanged. See [media credits](media-credits.md).
