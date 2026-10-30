"""
Source Quality Evaluator based on CRAAP Framework.

CRAAP Dimensions:
- Currency: Temporal alignment with query's information needs
- Relevance: Citable specifics, coverage of acceptance criteria
- Authority: Institutional accountability, editorial oversight
- Accuracy: Evidence support, cross-verification potential, objectivity
- Purpose: Writing intent, commercial/political bias detection

Composite Scores:
- CR = 0.3×Currency + 0.7×Relevance (info density)
- AAP = 0.4×Authority + 0.35×Accuracy + 0.25×Purpose (trustworthiness)
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SourceQuality:
    """Quality evaluation result for a single source."""

    index: int
    url: str
    title: str
    currency: float  # 1-5
    relevance: float  # 1-5
    authority: float  # 1-5
    accuracy: float  # 1-5
    purpose: float  # 1-5
    overall: float  # Average of 5 dimensions
    reasoning: str
    cr: float = 0.0  # Composite: 0.3*Currency + 0.7*Relevance (info density)
    aap: float = 0.0  # Composite: 0.4*Authority + 0.35*Accuracy + 0.25*Purpose (trustworthiness)


def compute_cr_aap(quality_scores: Dict[str, float]) -> Dict[str, float]:
    """
    Compute CR (info density) and AAP (trustworthiness) composite scores.

    CR = 0.3×Currency + 0.7×Relevance
    AAP = 0.4×Authority + 0.35×Accuracy + 0.25×Purpose

    Args:
        quality_scores: Dict with keys currency, relevance, authority, accuracy, purpose.

    Returns:
        Same dict with added 'cr' and 'aap' keys.
    """
    c = float(quality_scores.get("currency", 3))
    r = float(quality_scores.get("relevance", 3))
    a = float(quality_scores.get("authority", 3))
    ac = float(quality_scores.get("accuracy", 3))
    p = float(quality_scores.get("purpose", 3))

    cr = 0.3 * c + 0.7 * r
    aap = 0.4 * a + 0.35 * ac + 0.25 * p

    result = dict(quality_scores)
    result["cr"] = round(cr, 2)
    result["aap"] = round(aap, 2)
    return result


class SourceQualityEvaluator:
    """Format Reader quality scores and apply CR/AAP quality and retry rules."""

    def __init__(self, language: str = "en"):
        self.language = language

    def format_quality_summary(self, results: List[SourceQuality]) -> str:
        """Format quality results for display in final answer.

        Shows CR (info density) and AAP (trustworthiness) composite scores.
        """
        if not results:
            return ""

        lines = ["## Source Quality Assessment"]

        for r in sorted(results, key=lambda x: -(x.cr + x.aap)):
            lines.append(f"- [[{r.index}]] CR={r.cr:.1f} AAP={r.aap:.1f}")

        return "\n".join(lines)

    def get_quality_level(self, results: List[SourceQuality]) -> str:
        """Get overall quality level based on CR/AAP composite scores.

        HIGH: ≥2 sources with CR≥4.0 and AAP≥3.5
        MEDIUM: ≥1 source with CR≥3.5, or ≥2 sources with CR≥3.0
        LOW: otherwise
        """
        if not results:
            return "low"

        high_count = sum(1 for r in results if r.cr >= 4.0 and r.aap >= 3.5)
        mid_count = sum(1 for r in results if r.cr >= 3.0)

        if high_count >= 2:
            return "high"
        elif high_count >= 1 or mid_count >= 2:
            return "medium"
        else:
            return "low"

    def should_force_retry(self, results: List[SourceQuality]) -> bool:
        """Check if all sources lack usable information (CR < 3.0)."""
        if not results:
            return True
        return all(r.cr < 3.0 for r in results)
