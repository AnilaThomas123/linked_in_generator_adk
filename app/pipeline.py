"""Orchestration boundary for research, generation, and evaluation."""

from __future__ import annotations

import json
import os
from uuid import uuid4

from .audit import record
from .config import ROOT, load_topics
from .evaluators import evaluate_draft
from .generation import extract_insights, revise_draft, write_draft
from .models import Draft
from .notifications import notify
from .research import collect_candidates
from .selection import screen_candidates
from .storage import save_draft
from .topic_selection import DEFAULT_TOPIC, resolve_topic

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorate(function):
            return function
        return decorate


LAST_RUN_REPORT: dict = {}
REVISABLE_GATES = {"EDITORIAL", "CLAIMS"}


def get_last_run_report() -> dict:
    return json.loads(json.dumps(LAST_RUN_REPORT))


@traceable(name="editorial_pipeline_run", run_type="chain")
def run_once(topic_id: str | None = None) -> list[Draft]:
    """Run the evidence-first pipeline.

    Failed or unavailable sources are never replaced with invented content, so
    a run may return fewer than the configured nine drafts.
    """
    global LAST_RUN_REPORT
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for live research and generation")
    selected = resolve_topic(topic_id) if topic_id else DEFAULT_TOPIC
    if selected is None:
        raise ValueError(f"Unknown topic: {topic_id}")
    all_topics, settings = load_topics()
    topics = [topic for topic in all_topics if topic.id == selected]
    covered_topic_names = [topic.name for topic in all_topics if topic.id != selected]
    trace_id = f"run-{uuid4().hex}"
    candidates = collect_candidates(topic_id=selected)
    report = {
        "trace_id": trace_id,
        "topics": [],
        "candidate_count": len(candidates),
        "drafts_requested": len(topics) * int(settings["drafts_per_topic"]),
        "drafts_created": 0,
        "rejections": [],
    }
    by_topic: dict[str, list] = {topic.id: [] for topic in topics}
    for candidate in candidates:
        if candidate.topic_id in by_topic:
            by_topic[candidate.topic_id].append(candidate)

    drafts: list[Draft] = []
    out = ROOT / "drafts"
    out.mkdir(parents=True, exist_ok=True)
    for topic in topics:
        screen = screen_candidates(topic.id, topic.name, by_topic[topic.id], settings, covered_topic_names)
        # Only articles that pass the date gate and the executive-relevance brief may become drafts.
        candidates_for_topic = screen.eligible
        topic_report = {
            "topic_id": topic.id,
            "topic_name": topic.name,
            "candidate_count": len(by_topic[topic.id]),
            "eligible_count": len(candidates_for_topic),
            "selection_status": screen.status,
            "selection_error": screen.error,
            "drafts_requested": int(settings["drafts_per_topic"]),
            "drafts_created": 0,
            "rejections": [],
        }
        for rejection in screen.rejections:
            topic_report["rejections"].append(rejection)
            report["rejections"].append({"topic_id": topic.id, **rejection})
        report["topics"].append(topic_report)
        if not candidates_for_topic:
            reason = {
                "stage": "selection",
                "reason": screen.error or "No article passed the date window and executive-relevance checks.",
            }
            topic_report["rejections"].append(reason)
            report["rejections"].append({"topic_id": topic.id, **reason})
        for index in range(1, int(settings["drafts_per_topic"]) + 1):
            if not candidates_for_topic:
                continue
            # Eligible articles are ordered by the analyst's topic rank; the strongest comes first.
            candidate = candidates_for_topic[(index - 1) % len(candidates_for_topic)]
            try:
                extraction = extract_insights(candidate)
                draft = write_draft(candidate, extraction, index)
            except Exception as exc:  # noqa: BLE001 - report failed agent stage per draft
                reason = {"stage": "generation", "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}
                topic_report["rejections"].append(reason)
                report["rejections"].append({"topic_id": topic.id, **reason})
                continue
            draft.draft_id = f"{topic.id}-{uuid4().hex[:8]}"
            draft.trace_id = trace_id
            draft.topic_name = topic.name
            draft.extraction = extraction
            for revision in range(int(settings.get("maximum_revisions", 0)) + 1):
                draft = evaluate_draft(draft, candidate, extraction, settings)
                if draft.evaluator_status == "PASS":
                    break
                failed = [item for item in draft.evaluations if item.status != "PASS"]
                if any(item.gate not in REVISABLE_GATES for item in failed) or revision >= int(settings.get("maximum_revisions", 0)):
                    break
                draft = revise_draft(draft, [reason for item in failed for reason in item.reasons], revision + 1)
            if draft.evaluator_status != "PASS":
                failed = [
                    {"gate": item.gate, "status": item.status, "reasons": item.reasons}
                    for item in draft.evaluations if item.status != "PASS"
                ]
                reason = {"stage": "evaluation", "reason": "Draft rejected by one or more gates.", "gates": failed}
                topic_report["rejections"].append(reason)
                report["rejections"].append({"topic_id": topic.id, **reason})
                continue
            (out / f"{draft.draft_id}.txt").write_text(draft.content, encoding="utf-8")
            (out / f"{draft.draft_id}.json").write_text(
                json.dumps(draft.model_dump(), indent=2), encoding="utf-8"
            )
            drafts.append(draft)
            topic_report["drafts_created"] += 1
            save_draft(draft)
            record(draft, method="pipeline_generated")
    report["drafts_created"] = len(drafts)
    LAST_RUN_REPORT = report
    if os.getenv("DESKTOP_NOTIFICATIONS", "false").lower() == "true":
        notify("LinkedIn Editorial Generator", f"{len(drafts)} drafts are ready for individual review.")
    return drafts
