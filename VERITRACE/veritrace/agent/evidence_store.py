"""
EvidenceStore - Mechanical storage for Reader atomic finding-evidence pairs.

This module provides a side-channel for evidence that bypasses LLM synthesis,
preserving verbatim quotes from source pages for use in section writing.

Key design:
- Records are indexed by global ref_idx (page-level, compatible with [[n]] system)
- One page can have multiple findings (one per criterion)
- No LLM processing - purely mechanical storage and retrieval
"""

import logging
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class EvidenceRecord:
    """A single atomic finding-evidence pair from a Reader."""

    ref_idx: int  # Global reference index (remapped)
    url: str
    title: str
    evidence_quote: str  # Verbatim quote from the page
    finding_answer: str  # Reader's finding summary
    criterion: str  # Acceptance criterion this answers
    page_idx: int  # Original local page index (before remap)
    search_node: str  # Search task node name that produced this


class EvidenceStore:
    """
    Evidence side-channel - mechanical storage for Reader atomic finding-evidence pairs.

    Records are grouped by global ref_idx (page-level).
    One page can produce multiple records (one per criterion found).
    """

    def __init__(self):
        self.records: Dict[int, List[EvidenceRecord]] = {}

    def add_record(self, record: EvidenceRecord) -> None:
        """Add a single evidence record, grouped by ref_idx."""
        if record.ref_idx not in self.records:
            self.records[record.ref_idx] = []
        self.records[record.ref_idx].append(record)

    def add_from_reader_results(
        self,
        all_findings: Dict[str, List],
        ref2url: Dict,
        search_node: str,
        local_to_global: Dict[int, int],
    ) -> int:
        """
        Import evidence from Reader merged findings with index remapping.

        Args:
            all_findings: {criterion: [(page_idx, {answer, evidence}), ...]}
            ref2url: {local_page_idx: {url, title, ...}}
            search_node: Name of the search task node
            local_to_global: {local_idx: global_idx} mapping

        Returns:
            Number of records added
        """
        count = 0
        for criterion, findings_list in all_findings.items():
            for page_idx, finding_data in findings_list:
                if not isinstance(finding_data, dict):
                    continue
                evidence = finding_data.get("evidence", "")
                if not evidence or not evidence.strip():
                    continue

                # Remap to global index
                global_idx = local_to_global.get(page_idx)
                if global_idx is None:
                    logging.debug(
                        f"[EvidenceStore] Skipping record: local idx {page_idx} not in mapping for node {search_node}"
                    )
                    continue

                page_info = ref2url.get(page_idx, {})
                url = page_info.get("url", "") if isinstance(page_info, dict) else ""
                title = page_info.get("title", "") if isinstance(page_info, dict) else ""

                record = EvidenceRecord(
                    ref_idx=global_idx,
                    url=url,
                    title=title,
                    evidence_quote=evidence.strip(),
                    finding_answer=str(finding_data.get("answer", "")),
                    criterion=criterion,
                    page_idx=page_idx,
                    search_node=search_node,
                )
                self.add_record(record)
                count += 1

        if count > 0:
            logging.info(f"[EvidenceStore] Added {count} records from node '{search_node}'")
        return count

    def get_evidence_for_refs(self, ref_indices: List[int]) -> Dict[int, List[EvidenceRecord]]:
        """Get evidence subset by reference indices."""
        return {idx: self.records[idx] for idx in ref_indices if idx in self.records}

    def get_evidence_for_nodes(self, node_names: List[str]) -> Dict[int, List[EvidenceRecord]]:
        """Get evidence subset by search node names."""
        result: Dict[int, List[EvidenceRecord]] = {}
        for idx, records in self.records.items():
            matching = [r for r in records if r.search_node in node_names]
            if matching:
                result[idx] = matching
        return result

    def get_all_ref_indices(self) -> List[int]:
        """Get all reference indices that have evidence."""
        return sorted(self.records.keys())

    def __len__(self) -> int:
        """Total number of evidence records across all refs."""
        return sum(len(recs) for recs in self.records.values())

    def to_dict(self) -> Dict:
        """Serialize for debugging/logging."""
        return {
            str(idx): [
                {
                    "ref_idx": r.ref_idx,
                    "url": r.url,
                    "title": r.title,
                    "evidence_quote": str(r.evidence_quote or ""),
                    "finding_answer": str(r.finding_answer or ""),
                    "criterion": r.criterion,
                    "search_node": r.search_node,
                }
                for r in records
            ]
            for idx, records in self.records.items()
        }

    @classmethod
    def from_evidence_table(cls, table: Dict) -> "EvidenceStore":
        """Reconstruct EvidenceStore from evidence_table JSON (inverse of to_evidence_table).

        Args:
            table: {ref_idx_str_or_int: {url, title, entries: [{criterion, quote, finding_answer, search_node}]}}
        """
        es = cls()
        for ref_idx_key, source_data in table.items():
            ref_idx = int(ref_idx_key)
            url = source_data.get("url", "")
            title = source_data.get("title", "")
            for entry in source_data.get("entries", []):
                record = EvidenceRecord(
                    ref_idx=ref_idx,
                    url=url,
                    title=title,
                    evidence_quote=entry.get("quote", ""),
                    finding_answer=entry.get("finding_answer", ""),
                    criterion=entry.get("criterion", ""),
                    page_idx=0,
                    search_node=entry.get("search_node", ""),
                )
                es.add_record(record)
        return es

    def to_evidence_table(self) -> Dict:
        """Export as EVIDENCE_TABLE format for test fixture.

        Returns:
            {ref_idx_int: {url, title, entries: [{criterion, quote, finding_answer, search_node}]}}
        """
        return {
            int(idx): {
                "url": records[0].url if records else "",
                "title": records[0].title if records else "",
                "entries": [
                    {
                        "criterion": r.criterion,
                        "quote": r.evidence_quote,
                        "finding_answer": r.finding_answer,
                        "search_node": r.search_node,
                    }
                    for r in records
                ],
            }
            for idx, records in self.records.items()
        }
