"""Regression tests for the workflow changes: topic choice from chat, gate reporting, and approval blocking."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

import app.agent as agent
import app.dashboard as dashboard
import app.generation as generation
import app.selection as selection
from app import workflow_state
from app.evaluators import claims_gate, verbatim_quotes
from app.models import (
    CandidateArticle,
    InsightExtraction,
    Draft,
    EvaluationResult,
    ResearchBrief,
    ResearchItem,
    ResearchScore,
    TopicCandidate,
)
from app.topic_selection import topic_in_text

NOW = datetime(2026, 11, 12, tzinfo=UTC)
SETTINGS = {"research_window_days": 30, "minimum_audience_fit_score": 4}


def _draft(content: str = "A 40% risk needs a decision.\n\nWhat will the CISO approve first?") -> Draft:
    return Draft(
        draft_id="post-1", topic_id="cybersecurity", topic_name="Cybersecurity", title="A specific title for review",
        abstract="Abstract", content=content, sources=[], evaluator_status="PASS",
        evaluations=[EvaluationResult(gate=gate, status="PASS", score=5.0, reasons=[]) for gate in dashboard.POST_GATES],
    )


def _failing_infographic(draft: Draft) -> Draft:
    return draft.model_copy(update={"infographic_status": "FAILED", "infographic_error": "layout check failed"})


def test_topic_in_text_finds_a_named_topic_and_nothing_else():
    assert topic_in_text("run the cybersecurity topic") == "cybersecurity"
    assert topic_in_text("please run ai governance") == "governance_risk"
    assert topic_in_text("latest ai announcements, please") == "ai_announcements"
    assert topic_in_text("run the pipeline") is None
    assert topic_in_text("runaway cybersecurityish plans") is None
    assert topic_in_text("") is None


def test_revision_does_not_relabel_earlier_results(monkeypatch):
    monkeypatch.setattr(generation, "_structured", lambda *a, **k: generation.GeneratedDraft(
        title="A specific title for review", abstract="x", content="Body text", stance="A stance."))
    draft = _draft()
    draft.evaluations = [EvaluationResult(gate="HOOK", status="FAIL", score=0.0, reasons=["r"], revision=0)]
    generation.revise_draft(draft, ["r"], 1)
    assert draft.evaluations[0].revision == 0


def test_claims_gate_allows_role_titles_and_plain_english_but_not_invented_names():
    source = CandidateArticle(
        source_id="s", topic_id="cybersecurity", title="Patch notice", url="https://example.com/a",
        publisher="Pub", text="The vendor released a patch for the library.", authority_score=5.0,
    )
    assert claims_gate(_draft("Plain English: the patch matters.\n\nThe CISO should decide.\n\nWhat will the CTO approve?"),
                       source).status == "PASS"
    assert claims_gate(_draft("The review by Globex found the flaw.\n\nWhat will the CISO approve?"), source).status == "FAIL"


def test_agent_approval_is_reverted_when_the_infographic_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    monkeypatch.setattr(agent, "save_draft", lambda draft: None)
    monkeypatch.setattr(agent, "create_infographic", _failing_infographic)
    (tmp_path / "drafts").mkdir()
    draft = _draft()
    path = tmp_path / "drafts" / f"{draft.draft_id}.json"
    path.write_text(draft.model_dump_json(), encoding="utf-8")

    result = agent.approve_draft_for_posting(draft.draft_id)

    assert result["status"] == "BLOCKED"
    saved = Draft.model_validate_json(path.read_text(encoding="utf-8"))
    assert saved.decision == "PENDING"
    assert saved.approved_at is None


def test_dashboard_approval_returns_409_when_the_infographic_fails(monkeypatch):
    draft = _draft()
    monkeypatch.setattr(dashboard, "_require_user", lambda request: None)
    monkeypatch.setattr(dashboard, "PENDING", {draft.draft_id: draft})
    monkeypatch.setattr(dashboard, "create_infographic", _failing_infographic)
    monkeypatch.setattr(dashboard, "_persist", lambda draft: None)
    monkeypatch.setattr(dashboard, "save_draft", lambda draft: None)

    with pytest.raises(HTTPException) as error:
        dashboard.approve(None, draft.draft_id)

    assert error.value.status_code == 409
    assert "infographic" in error.value.detail
    assert dashboard.PENDING[draft.draft_id].decision == "PENDING"
    assert dashboard.PENDING[draft.draft_id].approved_at is None


def test_research_brief_is_saved_before_validation(monkeypatch):
    def item(item_id, url):
        return ResearchItem(id=item_id, title=item_id, url=url,
                            pillar="AI GOVERNANCE, COMPLIANCE AND ENTERPRISE RISK", source_type="official",
                            summary="s", score=ResearchScore(impact=4, novelty=3, evidence=4, shareability=3))

    brief = ResearchBrief(
        run_date="2026-11-12", window_days=30, items=[item("i1", "https://x/new")],
        topics=[TopicCandidate(rank=1, headline="h", claim="c", pillar="p", target_reader="CISO",
                               audience_fit_score=5, why_now="now", decision_relevance="decide", item_ids=["i1"])],
    )
    monkeypatch.setattr(selection, "research_topic", lambda *a, **k: brief)
    candidates = [CandidateArticle(
        source_id="s", topic_id="governance", title="t", url="https://x/new", publisher="p", text="body",
        authority_score=5, published_at="2026-11-05",
    )]

    selection.screen_candidates("governance_risk", "AI governance", candidates, SETTINGS, [], now=NOW)

    saved = workflow_state.state_path("research_raw_governance_risk")
    assert saved.exists()
    assert saved.is_relative_to(workflow_state.STATE_ROOT)


def _brief_item(item_id: str, url: str) -> ResearchItem:
    return ResearchItem(id=item_id, title=item_id, url=url,
                        pillar="AI GOVERNANCE, COMPLIANCE AND ENTERPRISE RISK", source_type="official",
                        summary="s", score=ResearchScore(impact=4, novelty=3, evidence=4, shareability=3))


def _brief_topic(rank: int, item_ids: list[str]) -> TopicCandidate:
    return TopicCandidate(rank=rank, headline=f"h{rank}", claim="c", pillar="p", target_reader="CISO",
                          audience_fit_score=5, why_now="now", decision_relevance="decide", item_ids=item_ids)


def test_repair_drops_invented_ids_and_keeps_real_evidence():
    brief = ResearchBrief(run_date="2026-11-12", window_days=30, items=[_brief_item("real", "https://x/new")],
                          topics=[_brief_topic(1, ["real", "invented"]), _brief_topic(2, ["invented"])])
    repaired, repairs = selection.repair_brief_references(brief)
    assert [topic.item_ids for topic in repaired.topics] == [["real"]]
    assert len(repairs) == 3


def test_invalid_brief_is_retried_once_and_a_valid_retry_is_used(monkeypatch):
    candidates = [CandidateArticle(
        source_id="s", topic_id="governance", title="t", url="https://x/new", publisher="p", text="body",
        authority_score=5, published_at="2026-11-05",
    )]
    invalid = ResearchBrief(run_date="2026-11-12", window_days=30, items=[_brief_item("i1", "https://evil/x")],
                            topics=[_brief_topic(1, ["i1"])])
    valid = ResearchBrief(run_date="2026-11-12", window_days=30, items=[_brief_item("i1", "https://x/new")],
                          topics=[_brief_topic(1, ["i1"])])
    responses = iter([invalid, valid])
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(1)
        return next(responses)

    monkeypatch.setattr(selection, "research_topic", fake_research)
    result = selection.screen_candidates("governance_risk", "AI governance", candidates, SETTINGS, [], now=NOW)
    assert len(calls) == 2
    assert result.status == "VALID"


def test_brief_that_stays_invalid_after_a_retry_produces_no_drafts(monkeypatch):
    candidates = [CandidateArticle(
        source_id="s", topic_id="governance", title="t", url="https://x/new", publisher="p", text="body",
        authority_score=5, published_at="2026-11-05",
    )]
    invalid = ResearchBrief(run_date="2026-11-12", window_days=30, items=[_brief_item("i1", "https://evil/x")],
                            topics=[_brief_topic(1, ["i1"])])
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(1)
        return invalid

    monkeypatch.setattr(selection, "research_topic", fake_research)
    result = selection.screen_candidates("governance_risk", "AI governance", candidates, SETTINGS, [], now=NOW)
    assert len(calls) == 2
    assert result.status == "ANALYST_INVALID"
    assert result.eligible == []


def test_verbatim_quotes_keeps_exact_quotes_and_drops_paraphrases():
    source = CandidateArticle(
        source_id="s", topic_id="cybersecurity", title="Patch notice", url="https://example.com/a",
        publisher="Pub", text="The vendor released a patch for the library.", authority_score=5.0,
    )
    kept, dropped = verbatim_quotes(source, ["The vendor released a patch for the library.", "The vendor shipped a fix."])
    assert kept == ["The vendor released a patch for the library."]
    assert dropped == ["The vendor shipped a fix."]


def _extraction(quotes: list[str]) -> InsightExtraction:
    return InsightExtraction(core_contribution="c", enterprise_impact="e", contrarian_point="p",
                             factual_claims=["f"], evidence_quotes=quotes)


def test_extractor_drops_paraphrased_quotes_and_skips_articles_with_too_few_exact_quotes(monkeypatch):
    def approved(url: str, text: str) -> dict:
        candidate = CandidateArticle(source_id=url, topic_id="cybersecurity", title="Patch notice", url=url,
                                     publisher="Pub", text=text, authority_score=5.0)
        return {"candidate": candidate.model_dump(), "evaluation": {}}

    responses = iter([
        _extraction(["The vendor released a patch for the library.", "Admins should update before Friday.", "Invented sentence."]),
        _extraction(["The vendor shipped a fix.", "Also invented."]),
    ])
    monkeypatch.setattr(agent, "extract_insights", lambda candidate: next(responses))
    workflow_state.write_state("credibility", {"approved": [
        approved("https://x/one", "The vendor released a patch for the library. Admins should update before Friday."),
        approved("https://x/two", "A different article with its own wording."),
    ], "rejected": []})
    workflow_state.write_state("assignments", [])

    agent.run_insight_extractor()

    insights = workflow_state.read_state("insights", [])
    assert len(insights) == 1
    assert insights[0]["extraction"]["evidence_quotes"] == [
        "The vendor released a patch for the library.", "Admins should update before Friday.",
    ]
    pruning = workflow_state.read_state("quote_pruning", [])
    assert [entry["dropped"] for entry in pruning] == [["Invented sentence."], ["The vendor shipped a fix.", "Also invented."]]
