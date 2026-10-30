"""Shared outline, evidence planning and report writing pipeline."""

from copy import deepcopy
from dataclasses import asdict
import logging

from .config import (
    OUTLINE_MODEL,
    SECTION_PLANNER_MODEL,
    SECTION_PLANNER_MAX_WORKERS,
    SECTION_WRITER_MODEL,
)
from .core.llm_adapter import create_adapter
from .outline_planner import OutlinePlanner, ReportOutline, SectionPlan, InsightWithEvidence, UserRequirement
from .section_planner import GlobalSectionPlanner
from .section_writer import SectionWriter
from .concurrency import check_cancelled
from .runtime.progress import report_progress


class WritingPipeline:
    def __init__(
        self,
        writer_llm,
        provider,
        language="en",
        include_search_refs=True,
        *,
        adapter_timeout=None,
        adapter_factory=None,
        outline_model=OUTLINE_MODEL,
        section_planner_model=SECTION_PLANNER_MODEL,
        section_writer_model=SECTION_WRITER_MODEL,
        max_workers=SECTION_PLANNER_MAX_WORKERS,
        cancellation_check=None,
    ):
        self.writer_llm = writer_llm
        self.provider = provider
        self.language = language
        self.include_search_refs = include_search_refs
        self.adapter_timeout = adapter_timeout
        self.adapter_factory = adapter_factory or create_adapter
        self.outline_model = outline_model
        self.section_planner_model = section_planner_model
        self.section_writer_model = section_writer_model
        self.max_workers = max_workers
        self.data = {}
        self.check_cancelled = cancellation_check or check_cancelled
        self.on_checkpoint = None

    def _adapter(self, model):
        if not model:
            return self.writer_llm
        options = {} if self.adapter_timeout is None else {"timeout_override": self.adapter_timeout}
        return self.adapter_factory(self.provider, model, **options)

    def run(self, question, cognitive_graph, evidence_store, cited_references, *, resume=None):
        self.data.clear()
        self.check_cancelled()
        base_model = getattr(self.writer_llm, "model", None) or type(self.writer_llm).__name__
        logging.info("[WritingPipeline] Configured models; provider=%s outline=%s section_planner=%s section_writer=%s",
                     self.provider, self.outline_model or base_model,
                     self.section_planner_model or self.outline_model or base_model,
                     self.section_writer_model or base_model)
        report_progress("planning_outline")
        resume = resume or {}
        if resume.get("outline"):
            sections = []
            for saved in resume["outline"]:
                section = dict(saved)
                section["insights"] = [InsightWithEvidence(**item) for item in section.get("insights", [])]
                sections.append(SectionPlan(**section))
            outline = ReportOutline(sections, resume["overall_theme"],
                                    [UserRequirement(**item) for item in resume.get("user_requirements", [])])
        else:
            outline_planner = OutlinePlanner(self._adapter(self.outline_model), language=self.language)
            outline = outline_planner.plan_outline(question, cognitive_graph, evidence_store)
        self.data.update({"cognitive_graph": cognitive_graph, "evidence_store": evidence_store.to_evidence_table(),
                          "cited_references": cited_references, "section_texts": dict(resume.get("section_texts", {})),
                          "planned_sections": list(resume.get("planned_sections", []))})

        def save():
            self.data.update({"outline": [asdict(section) for section in outline.sections],
                              "overall_theme": outline.overall_theme,
                              "user_requirements": [asdict(item) for item in outline.user_requirements]})
            if self.on_checkpoint:
                self.on_checkpoint(deepcopy(self.data))

        save()
        self.check_cancelled()
        report_progress("planning_sections", section_total=len(outline.sections))
        planner = GlobalSectionPlanner(
            self._adapter(self.section_planner_model or self.outline_model),
            language=self.language,
            max_workers=self.max_workers,
        )
        planner.completed_section_ids = set(self.data["planned_sections"])

        def save_plan():
            self.data["planned_sections"] = sorted(planner.completed_section_ids)
            save()

        planner.on_section = save_plan
        outline = planner.plan_all_sections(outline, cognitive_graph, evidence_store, question)
        self.check_cancelled()
        writer = SectionWriter(
            self._adapter(self.section_writer_model),
            language=self.language,
            include_search_refs=self.include_search_refs,
        )
        # A section depends on all preceding text; only a complete prefix can be reused.
        for section in outline.sections:
            text = self.data["section_texts"].get(section.section_id)
            if not text:
                break
            writer.completed_sections[section.section_id] = text

        def save_sections(texts):
            self.data["section_texts"] = writer.section_texts
            save()

        writer.on_section = save_sections
        self.data.update(
            {
                "cognitive_graph": cognitive_graph,
                "evidence_store": evidence_store.to_evidence_table(),
                "outline": [
                    {
                        "section_id": section.section_id,
                        "title": section.title,
                        "description": section.description,
                        "answers_aspect": section.answers_aspect,
                        "relevant_node_ids": section.relevant_node_ids,
                        "relevant_criteria": list(section.relevant_criteria),
                        "insights": [insight.to_dict() for insight in section.insights],
                    }
                    for section in outline.sections
                ],
                "overall_theme": outline.overall_theme,
                "user_requirements": [requirement.to_dict() for requirement in outline.user_requirements],
                "section_texts": writer.section_texts,
                "cited_references": cited_references,
            }
        )
        try:
            return writer.write_report(
                outline=outline,
                cognitive_graph=cognitive_graph,
                evidence_store=evidence_store,
                original_question=question,
                cited_references=cited_references,
            )
        finally:
            self.data["section_texts"] = getattr(writer, "section_texts", {})
