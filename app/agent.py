"""Proposal-compliant Google ADK multi-agent editorial workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from google.adk.workflow import START, Workflow, node

from .audit import record
from .config import ROOT, load_topics
from .evaluators import (
    TEMPLATE_LABELS,
    claims_gate,
    credibility_gate,
    editorial_gate,
    MIN_VERBATIM_QUOTES,
    faithfulness_gate,
    hook_gate,
    stance_gate,
    verbatim_quotes,
)
from .generation import extract_insights, revise_draft, write_draft
from .infographic import create_infographic
from .models import CandidateArticle, Draft, InsightExtraction, now_iso
from .publishing import publish_after_approval
from .replay import replay_summaries
from .topic_selection import (
    DEFAULT_TOPIC,
    TOPIC_CHOICES,
    consume_selected_topic,
    resolve_topic,
    save_selected_topic,
    topic_in_text,
    topic_name,
)
from .research import collect_candidates
from .selection import screen_candidates
from .storage import save_draft
from .workflow_state import read_state, reset_state, write_state

try:
    from google.adk.integrations.openai import OpenAIResponsesLlm
except ImportError:  # pragma: no cover
    OpenAIResponsesLlm = None

try:
    from langsmith.integrations.google_adk import configure_google_adk
except ImportError:  # pragma: no cover
    configure_google_adk = None


MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
ADK_MODEL = OpenAIResponsesLlm(model=MODEL) if OpenAIResponsesLlm else MODEL

if configure_google_adk and os.getenv("LANGSMITH_API_KEY"):
    configure_google_adk(
        project_name=os.getenv("LANGSMITH_PROJECT", "linked-in-generator-adk"),
        tags=["google-adk", "editorial-pipeline", "proposal-workflow"],
    )


def _selected() -> str:
    return read_state("run", {}).get("selected_topic", DEFAULT_TOPIC)


def _selected_topics(run: dict) -> list[dict]:
    return [topic for topic in run.get("topics", []) if topic["id"] == _selected()]


def _resolve_guard_topic(topic: str) -> str:
    return _selected() if topic == "selected" else topic


def initialize_workflow() -> dict:
    topics, settings = load_topics()
    selected = consume_selected_topic()
    reset_state()
    run_id = f"adk-run-{uuid4().hex[:10]}"
    write_state("run", {
        "run_id": run_id,
        "topics": [topic.model_dump() for topic in topics],
        "settings": settings,
        "selected_topic": selected,
    })
    return {"stage": "initialized", "run_id": run_id, "selected_topic": selected, "topics": [topic.id for topic in topics]}


def _scout(topic_id: str, topic_name: str) -> dict:
    candidates = collect_candidates(topic_id=topic_id)
    diagnostics_path = Path(__file__).resolve().parents[1] / "data" / "adk_workflow" / f"source_diagnostics_{topic_id}.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8")) if diagnostics_path.exists() else []
    run = read_state("run", {})
    covered = [topic.get("name", "") for topic in run.get("topics", []) if topic.get("id") != topic_id]
    # Same selection rules as the dashboard pipeline: date gate, then executive-relevance brief.
    screen = screen_candidates(topic_id, topic_name, candidates, run.get("settings", {}), covered)
    write_state(f"scout_{topic_id}", [candidate.model_dump() for candidate in screen.eligible])
    write_state(f"selection_{topic_id}", {
        "status": screen.status,
        "error": screen.error,
        "candidate_count": len(candidates),
        "eligible_count": len(screen.eligible),
        "rejections": screen.rejections,
    })
    brief_state: dict = {"status": screen.status}
    if screen.status == "VALID" and screen.brief is not None:
        brief_state["brief"] = screen.brief.model_dump()
    if screen.error:
        brief_state["error"] = screen.error
    if screen.status == "NO_CANDIDATES":
        brief_state["diagnostics"] = diagnostics
    write_state(f"research_brief_{topic_id}", brief_state)
    usable = screen.status == "VALID" and screen.brief is not None
    return {
        "stage": "research_analyst",
        "topic": topic_name,
        "candidate_count": len(candidates),
        "eligible_count": len(screen.eligible),
        "ranked_topic_count": len(screen.brief.topics) if usable else 0,
        "evidence_item_count": len(screen.brief.items) if usable else 0,
        "analyst_error": screen.error,
        "rejections": screen.rejections,
        "source_diagnostics": diagnostics,
    }


def scout_selected() -> dict:
    return _scout(_selected(), topic_name(_selected()))


def scout_ai_agents() -> dict:
    return _scout("ai_agents", "AI Agents")


def scout_cybersecurity() -> dict:
    return _scout("cybersecurity", "Cybersecurity")


def scout_governance() -> dict:
    return _scout("governance_risk", "Governance and Risk")


def run_credibility_gate() -> dict:
    run = read_state("run", {})
    minimum = float(run.get("settings", {}).get("min_authority_score", 4.0))
    approved: list[dict] = []
    rejected: list[dict] = []
    for topic in _selected_topics(run):
        topic_id = topic["id"]
        raw_candidates = read_state(f"scout_{topic_id}", [])
        brief_state = read_state(f"research_brief_{topic_id}", {})
        if brief_state.get("status") == "VALID":
            brief = brief_state.get("brief", {})
            selected_item_ids = {
                item_id
                for candidate_topic in brief.get("topics", [])
                for item_id in candidate_topic.get("item_ids", [])
            }
            selected_urls = {
                item.get("url")
                for item in brief.get("items", [])
                if item.get("id") in selected_item_ids
            }
            raw_candidates = [raw for raw in raw_candidates if raw.get("url") in selected_urls]
        else:
            # Only a VALID brief releases articles; any other status means nothing may become a draft.
            raw_candidates = []
        for raw in raw_candidates:
            candidate = CandidateArticle.model_validate(raw)
            result = credibility_gate(candidate, minimum)
            item = {"candidate": candidate.model_dump(), "evaluation": result.model_dump()}
            (approved if result.status == "PASS" else rejected).append(item)
    write_state("credibility", {"approved": approved, "rejected": rejected})
    return {"stage": "credibility_gate", "approved": len(approved), "rejected": len(rejected)}


def run_insight_extractor() -> dict:
    approved = read_state("credibility", {}).get("approved", [])
    assignments = read_state("assignments", [])
    assigned_sources = {source_id for assignment in assignments for source_id in assignment.get("source_ids", [])}
    extractions: list[dict] = []
    pruning: list[dict] = []
    for item in approved:
        candidate = CandidateArticle.model_validate(item["candidate"])
        if assigned_sources and candidate.source_id not in assigned_sources:
            continue
        extraction = extract_insights(candidate)
        # Keep only quotes found verbatim in the fetched text; a paraphrased quote would fail the faithfulness gate.
        kept, dropped = verbatim_quotes(candidate, extraction.evidence_quotes)
        hook_kept, _ = verbatim_quotes(candidate, [extraction.hook_fact]) if extraction.hook_fact.strip() else ([], [])
        pruning.append({"url": candidate.url, "kept": len(kept), "dropped": dropped, "hook_verbatim": bool(hook_kept)})
        if len(kept) < MIN_VERBATIM_QUOTES or not hook_kept:
            continue
        extraction = extraction.model_copy(update={"evidence_quotes": kept})
        extractions.append({"candidate": candidate.model_dump(), "extraction": extraction.model_dump()})
    write_state("quote_pruning", pruning)
    write_state("insights", extractions)
    return {"stage": "insight_extractor", "extractions": len(extractions)}


def run_editorial_assignment_gate() -> dict:
    """Select one executive-relevant, evidence-backed assignment per topic."""
    run = read_state("run", {})
    approved = read_state("credibility", {}).get("approved", [])
    approved_by_url = {item["candidate"]["url"]: item["candidate"]["source_id"] for item in approved}
    assignments: list[dict] = []
    for topic in _selected_topics(run):
        brief_state = read_state(f"research_brief_{topic['id']}", {})
        if brief_state.get("status") != "VALID":
            continue
        brief = brief_state.get("brief", {})
        for candidate_topic in sorted(brief.get("topics", []), key=lambda item: item.get("rank", 999)):
            item_ids = set(candidate_topic.get("item_ids", []))
            evidence_items = [item for item in brief.get("items", []) if item.get("id") in item_ids]
            source_ids = [approved_by_url[item["url"]] for item in evidence_items if item.get("url") in approved_by_url]
            if not source_ids or not candidate_topic.get("decision_relevance") or not candidate_topic.get("why_now"):
                continue
            assignments.append({
                "topic_id": topic["id"],
                "headline": candidate_topic["headline"],
                "claim": candidate_topic["claim"],
                "why_now": candidate_topic["why_now"],
                "decision_relevance": candidate_topic["decision_relevance"],
                "item_ids": list(item_ids),
                "source_ids": source_ids,
            })
            break
    write_state("assignments", assignments)
    return {"stage": "editorial_assignment_editor", "assignment_count": len(assignments), "assignments": assignments}


def write_selected_drafts() -> dict:
    topic_id = _selected()
    drafts = _drafts_for_topic(topic_id, topic_name(topic_id))
    write_state(f"drafts_{topic_id}", [draft.model_dump() for draft in drafts])
    return {"stage": "ghostwriter", "topic": topic_id, "draft_count": len(drafts)}


def _drafts_for_topic(topic_id: str, topic_name: str) -> list[Draft]:
    run = read_state("run", {})
    requested = int(run.get("settings", {}).get("drafts_per_topic", 3))
    insights = [item for item in read_state("insights", []) if item["candidate"]["topic_id"] == topic_id]
    drafts: list[Draft] = []
    for index in range(1, requested + 1):
        if not insights:
            break
        item = insights[(index - 1) % len(insights)]
        candidate = CandidateArticle.model_validate(item["candidate"])
        extraction = InsightExtraction.model_validate(item["extraction"])
        draft = write_draft(candidate, extraction, index)
        draft.draft_id = f"{topic_id}-{uuid4().hex[:8]}"
        draft.topic_name = topic_name
        draft.extraction = extraction
        drafts.append(draft)
    return drafts


def write_ai_agent_drafts() -> dict:
    drafts = _drafts_for_topic("ai_agents", "AI Agents")
    write_state("drafts_ai_agents", [draft.model_dump() for draft in drafts])
    return {"stage": "ghostwriter", "topic": "AI Agents", "draft_count": len(drafts)}


def write_cybersecurity_drafts() -> dict:
    drafts = _drafts_for_topic("cybersecurity", "Cybersecurity")
    write_state("drafts_cybersecurity", [draft.model_dump() for draft in drafts])
    return {"stage": "ghostwriter", "topic": "Cybersecurity", "draft_count": len(drafts)}


def write_governance_drafts() -> dict:
    drafts = _drafts_for_topic("governance_risk", "Governance and Risk")
    write_state("drafts_governance_risk", [draft.model_dump() for draft in drafts])
    return {"stage": "ghostwriter", "topic": "Governance and Risk", "draft_count": len(drafts)}


def _evaluate_topic_faithfulness(topic_id: str) -> dict:
    run = read_state("run", {})
    minimum = float(run.get("settings", {}).get("minimum_faithfulness_score", 0.90))
    drafts = [Draft.model_validate(raw) for raw in read_state(f"drafts_{topic_id}", [])]
    candidates = [CandidateArticle.model_validate(item["candidate"]) for item in read_state("insights", []) if item["candidate"]["topic_id"] == topic_id]
    by_source = {candidate.source_id: candidate for candidate in candidates}
    for draft in drafts:
        candidate = by_source.get(draft.sources[0].source_id if draft.sources else "")
        if candidate and draft.extraction:
            result = faithfulness_gate(candidate, draft.extraction, minimum)
            draft.evaluations = [result]
            draft.evaluator_status = "PASS" if result.status == "PASS" else "FAIL"
    write_state(f"faithfulness_{topic_id}", [draft.model_dump() for draft in drafts])
    return {"stage": "faithfulness_gate", "topic": topic_id, "draft_count": len(drafts), "passed": sum(d.evaluator_status == "PASS" for d in drafts)}


def evaluate_selected_faithfulness() -> dict:
    return _evaluate_topic_faithfulness(_selected())


def evaluate_ai_agent_faithfulness() -> dict:
    return _evaluate_topic_faithfulness("ai_agents")


def evaluate_cybersecurity_faithfulness() -> dict:
    return _evaluate_topic_faithfulness("cybersecurity")


def evaluate_governance_faithfulness() -> dict:
    return _evaluate_topic_faithfulness("governance_risk")


def run_editorial_optimizer() -> dict:
    run = read_state("run", {})
    settings = run.get("settings", {})
    all_drafts: list[Draft] = []
    for topic_id in (_selected(),):
        all_drafts.extend(Draft.model_validate(raw) for raw in read_state(f"faithfulness_{topic_id}", []))
    insights = read_state("insights", [])
    candidates = {item["candidate"]["source_id"]: CandidateArticle.model_validate(item["candidate"]) for item in insights}
    maximum_revisions = int(settings.get("maximum_revisions", 3))
    minimum_faithfulness = float(settings.get("minimum_faithfulness_score", 0.90))
    labels = settings.get("template_labels", TEMPLATE_LABELS)
    results: list[Draft] = []
    for draft in all_drafts:
        candidate = candidates.get(draft.sources[0].source_id if draft.sources else "")
        if not candidate or not draft.extraction or draft.evaluator_status != "PASS":
            results.append(draft)
            continue
        for revision in range(maximum_revisions + 1):
            # Re-verify after every revision: the post text itself (claims and editorial)
            # and the extraction faithfulness gate. Revised text is never passed unchecked.
            faithfulness = faithfulness_gate(candidate, draft.extraction, minimum_faithfulness)
            editorial = editorial_gate(
                draft, int(settings.get("max_post_characters", 1300)), settings.get("banned_buzzwords", []),
                int(settings.get("minimum_post_characters", 700)), labels,
                settings.get("executive_signal_terms", []),
            )
            claims = claims_gate(draft, candidate)
            hook = hook_gate(draft)
            stance = stance_gate(draft)
            current = [faithfulness, editorial, claims, hook, stance]
            for item in current:
                item.revision = revision
            # Keep only the latest results, so reasons from earlier revisions are not reported as current.
            draft.evaluations = current
            if all(item.status == "PASS" for item in current):
                draft.evaluator_status = "PASS"
                break
            draft.evaluator_status = "FAIL"
            # A failed faithfulness check cannot be fixed by rewording, so stop there.
            if revision >= maximum_revisions or faithfulness.status != "PASS":
                break
            reasons = [reason for item in current if item.status != "PASS" for reason in item.reasons]
            draft = revise_draft(
                draft, reasons, revision + 1,
                int(settings.get("minimum_post_characters", 700)), int(settings.get("max_post_characters", 1300)),
            )
        results.append(draft)
    write_state("optimized_drafts", [draft.model_dump() for draft in results])
    return {"stage": "editorial_optimizer", "draft_count": len(results), "passed": sum(d.evaluator_status == "PASS" for d in results)}


def finalize_drafts() -> dict:
    run = read_state("run", {})
    optimized = read_state("optimized_drafts", [])
    drafts = [Draft.model_validate(raw) for raw in optimized if raw.get("evaluator_status") == "PASS"]
    rejected = []
    for raw in optimized:
        if raw.get("evaluator_status") == "PASS":
            continue
        reasons = []
        for evaluation in raw.get("evaluations", []):
            if evaluation.get("status") != "PASS":
                reasons.extend(evaluation.get("reasons", []))
        rejected.append({
            "draft_id": raw.get("draft_id", "unknown"),
            "status": raw.get("evaluator_status", "UNKNOWN"),
            "reasons": list(dict.fromkeys(reasons)) or ["Draft did not reach a passing evaluator state."],
        })
    upstream_blockers = []
    if not optimized:
        for topic in _selected_topics(run):
            topic_id = topic["id"]
            brief = read_state(f"research_brief_{topic_id}", {})
            if brief.get("status") and brief.get("status") != "VALID":
                upstream_blockers.append({
                    "topic_id": topic_id,
                    "stage": "research_analyst",
                    "status": brief.get("status"),
                    "reason": brief.get("error") or (
                        "No executive-relevant topic candidate met the configured evidence, "
                        "date-window, and audience-fit requirements."
                    ),
                    "diagnostics": brief.get("diagnostics", []),
                })
    output = []
    draft_dir = Path(__file__).resolve().parents[1] / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    for draft in drafts:
        draft.trace_id = run.get("run_id")
        (draft_dir / f"{draft.draft_id}.txt").write_text(draft.content, encoding="utf-8")
        (draft_dir / f"{draft.draft_id}.json").write_text(json.dumps(draft.model_dump(), indent=2), encoding="utf-8")
        save_draft(draft)
        record(draft, method="adk_workflow_generated")
        output.append(draft.draft_id)
    report = {
        "stage": "final_validation",
        "draft_count": len(output),
        "draft_ids": output,
        "human_review_required": True,
        "optimized_draft_count": len(optimized),
        "rejected_drafts": rejected,
        "upstream_blockers": upstream_blockers,
    }
    write_state("final_validation_report", report)
    return report


def _has_state(guard: str) -> bool:
    if guard == "approved_candidates":
        return bool(read_state("credibility", {}).get("approved", []))
    if guard == "assignments":
        return bool(read_state("assignments", []))
    if guard.startswith("insights:"):
        topic_id = _resolve_guard_topic(guard.split(":", 1)[1])
        return any(item["candidate"]["topic_id"] == topic_id for item in read_state("insights", []))
    if guard.startswith("drafts:"):
        return bool(read_state(f"drafts_{_resolve_guard_topic(guard.split(':', 1)[1])}", []))
    if guard == "any_faithfulness_drafts":
        return any(bool(read_state(f"faithfulness_{topic_id}", [])) for topic_id in (_selected(),))
    return True


_STAGE_FUNCTIONS = {
    "scout_selected": scout_selected,
    "credibility_gate": run_credibility_gate,
    "editorial_assignment_gate": run_editorial_assignment_gate,
    "insight_extractor": run_insight_extractor,
    "write_selected": write_selected_drafts,
    "faithfulness_selected": evaluate_selected_faithfulness,
    "editorial_optimizer": run_editorial_optimizer,
    "final_validation": finalize_drafts,
}


def _message_text(node_input) -> str:
    parts = getattr(node_input, "parts", None) or []
    return " ".join(part.text for part in parts if getattr(part, "text", None))


def _start_run(node_input=None) -> dict:
    """Start a run. A topic named in the chat message applies to this run; otherwise the default topic is used."""
    requested = topic_in_text(_message_text(node_input))
    if requested:
        save_selected_topic(requested)
    return initialize_workflow()


def _stage(stage: str, guard: str = ""):
    """One deterministic stage as a Workflow node. Every node runs and returns a result so the graph continues;
    a node whose upstream evidence is missing records that it was skipped."""

    def run(node_input=None) -> dict:
        if guard and not _has_state(guard):
            return {"stage": stage, "skipped": guard}
        return _STAGE_FUNCTIONS[stage]()

    run.__name__ = stage
    return node(run, name=stage)


workflow_initializer = node(_start_run, name="workflow_initializer")
scout_node = _stage("scout_selected")
credibility_node = _stage("credibility_gate")
assignment_node = _stage("editorial_assignment_gate", "approved_candidates")
extractor_node = _stage("insight_extractor", "assignments")
writer_node = _stage("write_selected", "insights:selected")
faithfulness_node = _stage("faithfulness_selected", "drafts:selected")
# Runs once: the optimizer already retries revisions up to maximum_revisions internally.
optimizer_node = _stage("editorial_optimizer", "any_faithfulness_drafts")
final_node = _stage("final_validation")


def build_workflow() -> Workflow:
    return Workflow(name="editorial_pipeline_workflow", edges=[
        (START, workflow_initializer),
        (workflow_initializer, scout_node),
        (scout_node, credibility_node),
        (credibility_node, assignment_node),
        (assignment_node, extractor_node),
        (extractor_node, writer_node),
        (writer_node, faithfulness_node),
        (faithfulness_node, optimizer_node),
        (optimizer_node, final_node),
    ])


editorial_pipeline_workflow = build_workflow()


def _load_draft(draft_id: str) -> Draft | None:
    path = ROOT / "drafts" / f"{draft_id}.json"
    if not path.exists():
        return None
    return Draft.model_validate_json(path.read_text(encoding="utf-8"))


def approve_draft_for_posting(draft_id: str) -> dict:
    """Approve one draft by its draft_id, then build its PNG infographic and ready-to-post folder.

    Call this ONLY when the human reviewer explicitly approves this named draft_id in the
    conversation. Never call it on your own initiative. This does not publish anything:
    the reviewer copies the post to LinkedIn by hand.

    Args:
        draft_id: The draft_id of the draft to approve, for example "cybersecurity-9fc2d2fc".

    Returns:
        A status object with the decision, infographic status, and ready-to-post folder.
    """
    draft = _load_draft(draft_id)
    if draft is None:
        return {"status": "NOT_FOUND", "draft_id": draft_id}
    if draft.decision == "DISCARDED":
        return {"status": "REFUSED", "draft_id": draft_id, "reason": "Discarded drafts cannot be approved."}
    if draft.evaluator_status != "PASS":
        reasons = [reason for item in draft.evaluations if item.status != "PASS" for reason in item.reasons]
        return {"status": "REFUSED", "draft_id": draft_id,
                "reason": "Only drafts that passed all gates can be approved.", "gate_reasons": reasons}
    draft.decision = "APPROVED"
    draft.approved_at = now_iso()
    draft = create_infographic(draft)
    if draft.infographic_status != "READY":
        # A failed infographic blocks approval: undo the decision and keep the draft pending.
        draft.decision = "PENDING"
        draft.approved_at = None
        save_draft(draft)
        (ROOT / "drafts" / f"{draft.draft_id}.json").write_text(
            json.dumps(draft.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {"status": "BLOCKED", "draft_id": draft_id,
                "reason": "The infographic failed its checks, so the draft was not approved.",
                "infographic_error": draft.infographic_error}
    draft = publish_after_approval(draft)
    save_draft(draft)
    (ROOT / "drafts" / f"{draft.draft_id}.json").write_text(
        json.dumps(draft.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "status": "APPROVED",
        "draft_id": draft.draft_id,
        "publication_status": draft.publication_status,
        "infographic_status": draft.infographic_status,
        "infographic_file": draft.infographic_path,
        "ready_to_post_folder": draft.ready_to_post_dir,
        "next_step": "Copy the post into LinkedIn by hand, add the first comment, attach the image, then confirm in the dashboard.",
    }


def choose_topic(topic: str) -> dict:
    """Choose the topic for the next pipeline run. Call this before the pipeline runs, only when the
    user names a topic. The choice applies to one run. Without a choice, the run uses cybersecurity.

    Args:
        topic: "cybersecurity", "ai governance", or "ai announcements".

    Returns:
        SELECTED with the topic_id, or REFUSED with the list of valid choices.
    """
    topic_id = resolve_topic(topic)
    if topic_id is None:
        return {"status": "REFUSED", "reason": "Unknown topic.", "choices": TOPIC_CHOICES}
    save_selected_topic(topic_id)
    return {"status": "SELECTED", "topic_id": topic_id, "topic_name": topic_name(topic_id),
            "note": "The next pipeline run uses this topic."}


def replay_saved_run(topic: str = "") -> dict:
    """Show the saved snapshot of an earlier successful live run for one topic. Makes no live research
    or OpenAI calls. Use only when the presenter asks for a replay or a live run cannot complete.
    Always tell the audience that the output is a replay of saved results.

    Args:
        topic: "cybersecurity" (default), "ai governance", or "ai announcements".
    """
    topic_id = resolve_topic(topic) if topic else DEFAULT_TOPIC
    if topic_id is None:
        return {"status": "REFUSED", "reason": "Unknown topic.", "choices": TOPIC_CHOICES}
    return replay_summaries(topic_id)


# The pipeline is the root agent, so `adk web` runs it directly from the chat message.
root_agent = editorial_pipeline_workflow
