import { configurations } from "./cost-data.js";

const byId = (id) => document.getElementById(id);
const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
let activeConfiguration = configurations[0];

function updateBudget() {
  const count = Number(byId("cost-queries").value);
  byId("cost-query-count").textContent = `${count} ${count === 1 ? "query" : "queries"}`;
  byId("cost-budget").textContent = currency.format(activeConfiguration.usd * count);
  byId("cost-budget-formula").textContent = `${count} × ${currency.format(activeConfiguration.usd)}`;
}

function selectConfiguration(id) {
  const profile = configurations.find((item) => item.id === id);
  if (!profile) return;
  activeConfiguration = profile;
  document.querySelectorAll("[data-config]").forEach((control) => {
    const selected = control.dataset.config === id;
    if (control.getAttribute("role") === "tab") {
      control.setAttribute("aria-selected", String(selected));
      control.tabIndex = selected ? 0 : -1;
    } else {
      control.setAttribute("aria-pressed", String(selected));
    }
  });
  byId("cost-profile").setAttribute("aria-labelledby", `config-${id}`);
  byId("cost-config-name").textContent = profile.label;
  byId("cost-price").textContent = currency.format(profile.usd);
  byId("cost-quality").textContent = profile.overall.toFixed(2);
  byId("cost-note").textContent = profile.note;
  byId("cost-roles").textContent = profile.roles;
  byId("cost-input").textContent = `${(profile.inputK / 1000).toFixed(3)}M`;
  byId("cost-output").textContent = `${(profile.outputK / 1000).toFixed(3)}M`;
  byId("cost-calls").textContent = String(profile.calls);
  byId("cost-searches").textContent = profile.searches.toFixed(2);
  byId("cost-minutes").textContent = `${profile.minutes.toFixed(2)} min`;
  updateBudget();
}

function svgElement(name, attributes, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  if (text) element.textContent = text;
  return element;
}

configurations.forEach((profile) => {
  const x = 62 + (profile.usd / 2.2) * 430;
  const y = 240 - (profile.overall - 50) * 33;
  const point = svgElement("g", {
    class: "cost-point", role: "button", tabindex: "0", "data-config": profile.id,
    "aria-label": `${profile.label}: ${currency.format(profile.usd)} per query, RACE Overall ${profile.overall.toFixed(2)}`,
    transform: `translate(${x.toFixed(2)} ${y.toFixed(2)})`,
  });
  point.append(svgElement("circle", { r: "22", class: "point-target" }));
  point.append(svgElement("circle", { r: "14", class: "point-halo" }));
  point.append(svgElement("circle", { r: "6", class: "point-core" }));
  const left = profile.id === "27b" || profile.id === "deepseek";
  point.append(svgElement("text", {
    x: left ? "-13" : "13", y: profile.id === "3b" ? "17" : "-13",
    "text-anchor": left ? "end" : "start", class: "point-label",
  }, profile.short));
  point.addEventListener("click", () => selectConfiguration(profile.id));
  point.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectConfiguration(profile.id);
    }
  });
  byId("cost-points").append(point);
});

document.querySelectorAll("button[data-config]").forEach((button) => {
  button.addEventListener("click", () => selectConfiguration(button.dataset.config));
});
byId("cost-queries").addEventListener("input", updateBudget);
selectConfiguration("27b");

const stages = {
  evidence: {
    image: "assets/research.png", title: "Read useful findings while research continues.",
    description: "Open completed tasks, follow their sources and retain partial findings before the final report is ready.",
    detail: "12 search tasks in the recorded run", alt: "Task findings and source evidence during the recorded research",
  },
  graph: {
    image: "assets/cognitive-graph.png", title: "Inspect how the research question develops.",
    description: "The recorded cognitive graph grows from five to eight concepts. Open the full replay to inspect its nodes, relations and findings.",
    detail: "5 initial concepts → 8 final concepts", alt: "The revised cognitive graph with eight concepts in the recorded run",
  },
  report: {
    image: "assets/report.png", title: "Follow the evidence into the final report.",
    description: "Review the executive summary, section-level findings and source citations. Live research also retains partial findings and recovery controls.",
    detail: "A structured report with source citations", alt: "English translation of the completed research report with citations",
  },
};

document.querySelectorAll("[data-stage]").forEach((button) => {
  button.addEventListener("click", () => {
    const stage = stages[button.dataset.stage];
    document.querySelectorAll("[data-stage]").forEach((tab) => {
      const selected = tab === button;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    byId("showcase-panel").setAttribute("aria-labelledby", button.id);
    const image = byId("showcase-image");
    image.src = stage.image;
    image.alt = stage.alt;
    byId("showcase-title").textContent = stage.title;
    byId("showcase-description").textContent = stage.description;
    byId("showcase-detail").textContent = stage.detail;
    byId("open-showcase").setAttribute("aria-label", `Open full-size image: ${stage.alt}`);
    if (!motionPreference.matches) {
      image.animate([{ opacity: 0.45 }, { opacity: 1 }], { duration: 240, easing: "ease-out" });
    }
  });
});

document.querySelectorAll("[data-tab-group]").forEach((group) => {
  group.addEventListener("keydown", (event) => {
    if (!event.target.matches("[role=tab]")) return;
    const tabs = [...group.querySelectorAll("[role=tab]")];
    let index = tabs.indexOf(event.target);
    if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") index = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = tabs.length - 1;
    else return;
    event.preventDefault();
    tabs[index].focus();
    tabs[index].click();
  });
});

const dialog = byId("image-dialog");
byId("open-showcase").addEventListener("click", () => {
  byId("lightbox-image").src = byId("showcase-image").src;
  byId("lightbox-image").alt = byId("showcase-image").alt;
  byId("image-dialog-title").textContent = byId("showcase-title").textContent;
  dialog.showModal();
  dialog.querySelector(".lightbox-scroll").scrollTop = 0;
  dialog.querySelector(".lightbox-scroll").scrollLeft = 0;
  document.documentElement.classList.add("modal-open");
  byId("close-image").focus();
});
function closeImage() {
  dialog.close();
  document.documentElement.classList.remove("modal-open");
}
byId("close-image").addEventListener("click", closeImage);
dialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeImage();
});
dialog.addEventListener("close", () => document.documentElement.classList.remove("modal-open"));
dialog.addEventListener("click", (event) => {
  const rect = dialog.getBoundingClientRect();
  if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) closeImage();
});

async function copyText(value, button, status, fallback, successMessage) {
  button.dataset.copied = "false";
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(value);
    button.dataset.copied = "true";
    status.textContent = successMessage;
  } catch {
    fallback();
    status.textContent = "Automatic copying is unavailable. Select the text and copy it manually.";
  }
}

byId("copy-email").addEventListener("click", () => {
  byId("email-fallback").hidden = true;
  copyText("hz496@cam.ac.uk", byId("copy-email"), byId("email-copy-status"), () => {
    const input = byId("email-fallback");
    input.hidden = false;
    input.focus();
    input.select();
  }, "Email address copied.");
});
byId("copy-citation").addEventListener("click", () => {
  const citation = byId("citation-text");
  copyText(citation.textContent.trim(), byId("copy-citation"), byId("citation-copy-status"), () => {
    const selection = getSelection();
    if (selection) {
      const range = document.createRange();
      range.selectNodeContents(citation);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    citation.focus();
  }, "BibTeX citation copied.");
});
document.querySelectorAll('a[href="#citation"]').forEach((link) => {
  link.addEventListener("click", () => { byId("citation").open = true; });
});

let scrollQueued = false;
function updateScrollProgress() {
  const range = document.documentElement.scrollHeight - innerHeight;
  const progress = range > 0 ? Math.min(1, Math.max(0, scrollY / range)) : 0;
  const nav = document.querySelector(".nav");
  nav.style.setProperty("--reading-progress", `${progress * 100}%`);
  const threshold = nav.getBoundingClientRect().height + 40;
  const reached = [...document.querySelectorAll("main > section[id]")].filter((section) => section.getBoundingClientRect().top <= threshold);
  const active = reached.length ? reached[reached.length - 1].id : "";
  nav.querySelectorAll('a[href^="#"]').forEach((link) => {
    if (link.getAttribute("href") === `#${active}`) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  });
  scrollQueued = false;
}
addEventListener("scroll", () => {
  if (!scrollQueued) {
    scrollQueued = true;
    requestAnimationFrame(updateScrollProgress);
  }
}, { passive: true });
addEventListener("resize", updateScrollProgress);
document.querySelectorAll("details").forEach((details) => details.addEventListener("toggle", updateScrollProgress));
updateScrollProgress();

if ("IntersectionObserver" in window && !motionPreference.matches) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("arrived");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  document.querySelectorAll(".cost-explorer, .cost-principles article, .showcase-panel, .poster-overview, .contact-card").forEach((element) => observer.observe(element));
}
