"""Extract reports and evidence artifacts from completed research."""

import re
from typing import Dict, List, Optional

from .runtime.events import ResearchEvent


def extract_report(final_message: ResearchEvent) -> Dict:
    """Keep report citations and export their source metadata."""
    if final_message.type != "research.completed":
        raise ValueError("A completed research event is required")
    content = final_message.data.get("article", "") or ""
    ref2url: Dict = final_message.data.get("ref2url", {})

    # --- locate and strip the "Search Process References" section ---
    article = _strip_search_process_refs(content)

    # --- extract all [[n]] indices cited in the article ---
    cited_indices = sorted({int(n) for n in re.findall(r"\[\[(\d+)\]\]", article)})

    # --- build cited_references list ---
    cited_references: List[Dict] = []
    for idx in cited_indices:
        info = ref2url.get(idx) or ref2url.get(str(idx))
        if info is None:
            continue
        if isinstance(info, dict):
            cited_references.append(
                {
                    "ref_idx": idx,
                    "url": info.get("url", ""),
                    "title": info.get("title", ""),
                }
            )
        elif isinstance(info, str):
            cited_references.append(
                {
                    "ref_idx": idx,
                    "url": info,
                    "title": "",
                }
            )

    return {
        "article": article.strip(),
        "cited_references": cited_references,
        "ref2url": ref2url,
    }


# Internal helpers


# Patterns for the "Search Process References" header (both languages, with or
# without emoji, with or without markdown heading level).
_SEARCH_PROCESS_PATTERNS = [
    r"^#{1,3}\s*(?:\U0001f4da\s*)?Search Process References",  # English
    r"^#{1,3}\s*(?:\U0001f4da\s*)?搜索过程参考来源",  # Chinese
]


def extract_pipeline_data(agent) -> Optional[Dict]:
    """Return the saved writing-stage artifacts, if available."""
    return getattr(agent, "_pipeline_data", None) or None


def _strip_search_process_refs(content: str) -> str:
    """Remove the search-process reference appendix."""
    for pattern in _SEARCH_PROCESS_PATTERNS:
        match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
        if match:
            return content[: match.start()].rstrip()
    return content
