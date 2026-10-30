/* Presentation chrome and a visible pointer for the guided recording only. */
(() => {
  if (!new URLSearchParams(location.search).has("capture")) return;
  document.body.classList.add("capture-mode");
  const header = document.createElement("div");
  header.className = "film-header";
  header.innerHTML =
    '<div class="film-brand">VeriTrace</div><div class="film-heading"><div class="film-kicker">GUIDED PRODUCT WALKTHROUGH</div><div class="film-title">Research that revises its understanding.</div></div><div class="film-subtitle">Follow the evidence and inspect how the research evolves.</div>';
  document.body.prepend(header);
  const footer = document.createElement("div");
  footer.className = "film-footer";
  footer.innerHTML =
    "<span>Recorded research · waits compressed</span><span>Original research duration: 68m 10s</span>";
  document.body.append(footer);
  const slate = document.createElement("div");
  slate.className = "film-slate";
  slate.innerHTML =
    '<div class="film-slate-copy"><div class="film-kicker">FINDINGS OF EMNLP 2026</div><h1>VeriTrace</h1><div class="film-rule"></div><h2>Research that revises<br>its understanding.</h2><p>Follow a question through source evidence, cognitive-graph revision and a cited report.</p><p class="film-link">A guided product walkthrough</p></div><img class="film-poster" src="../assets/poster-preview.png" alt="VeriTrace research poster"><div class="film-music-credit"></div>';
  document.body.append(slate);
  const cursor = document.createElement("div");
  cursor.className = "film-cursor";
  cursor.innerHTML =
    '<svg viewBox="0 0 24 30"><path d="M3 2v23l6-6 5 9 4-2-5-9h8Z" fill="white" stroke="#173047" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  const ring = document.createElement("div");
  ring.className = "film-click";
  document.body.append(cursor, ring);
  let point = { x: 80, y: 120 };
  window.__VERITRACE_FILM__ = {
    chapter(number, title, description) {
      header.querySelector(".film-kicker").textContent =
        `CHAPTER ${number} / 05`;
      header.querySelector(".film-title").textContent = title;
      header.querySelector(".film-subtitle").textContent = description;
    },
    introDone() {
      slate.classList.add("hidden");
    },
    outro() {
      cursor.style.opacity = "0";
      slate.querySelector("h2").innerHTML = "Research you can inspect.";
      slate.querySelector("p").textContent =
        "Explore the graph. Follow the sources. Read the report.";
      slate.querySelector(".film-link").textContent =
        "github.com/WolfGaga/VeriTrace";
      slate.querySelector(".film-music-credit").textContent =
        "Music: “Cylinder Five” by Chris Zabriskie, from Cylinders. CC BY 4.0 · chriszabriskie.com · creativecommons.org/licenses/by/4.0/ · Excerpted, level-adjusted and faded.";
      slate.classList.remove("hidden");
    },
    async cursorTo(x, y) {
      cursor.style.opacity = "1";
      await cursor.animate(
        [
          { transform: `translate(${point.x}px,${point.y}px)` },
          { transform: `translate(${x}px,${y}px)` },
        ],
        {
          duration: 550,
          easing: "cubic-bezier(.22,.61,.36,1)",
          fill: "forwards",
        },
      ).finished;
      point = { x, y };
    },
    click() {
      ring.style.left = `${point.x - 20}px`;
      ring.style.top = `${point.y - 20}px`;
      ring.animate(
        [
          { opacity: 0.75, transform: "scale(.35)" },
          { opacity: 0, transform: "scale(1.2)" },
        ],
        { duration: 650, easing: "ease-out" },
      );
    },
    hideCursor() {
      cursor.style.opacity = "0";
    },
  };
  addEventListener("message", (event) => {
    if (
      event.origin !== location.origin ||
      event.data?.source !== "veritrace-replay" ||
      event.data.type !== "ready"
    )
      return;
    const meta = event.data.metadata;
    footer.firstElementChild.textContent = `${meta.presentation || "Recorded research"} · waits compressed`;
  });
})();
