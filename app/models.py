from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Topic(BaseModel):
    id: str
    name: str


class SourceEvidence(BaseModel):
    source_id: str
    title: str
    url: str
    publisher: str
    excerpt: str
    authority_score: float = Field(ge=0, le=5)
    retrieved_at: str = Field(default_factory=now_iso)
    content_hash: str = ""


class CandidateArticle(BaseModel):
    """A fetched source candidate. No claim may be made without its evidence."""
    source_id: str
    topic_id: str
    title: str
    url: str
    publisher: str
    text: str
    authority_score: float = Field(ge=0, le=5)
    retrieved_at: str = Field(default_factory=now_iso)
    published_at: str | None = None
    source_type: str = "UNKNOWN"


class ResearchFigure(BaseModel):
    value: str
    measures: str
    source: str
    date: str = ""
    status: str = "unverified"


class ResearchScore(BaseModel):
    impact: int = Field(ge=1, le=5)
    novelty: int = Field(ge=1, le=5)
    evidence: int = Field(ge=1, le=5)
    shareability: int = Field(ge=1, le=5)


class ResearchItem(BaseModel):
    id: str
    title: str
    url: str
    published: str = ""
    verified: str = ""
    pillar: str
    source_type: str
    summary: str
    figures: list[ResearchFigure] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    why_it_matters: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    score: ResearchScore


class TopicCandidate(BaseModel):
    rank: int
    headline: str
    claim: str
    pillar: str
    target_reader: str = ""
    audience_fit_score: int = Field(default=1, ge=1, le=5)
    why_now: str = ""
    decision_relevance: str = ""
    item_ids: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    counterpoint: str = ""
    format: str = "text post"
    infographic_points: list[ResearchFigure] = Field(default_factory=list)
    virality_rationale: str = ""
    risks: list[str] = Field(default_factory=list)
    peak_date: str = ""


class ResearchBrief(BaseModel):
    run_date: str
    window_days: int
    items: list[ResearchItem] = Field(default_factory=list)
    topics: list[TopicCandidate] = Field(default_factory=list)
    watch_list: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class InsightExtraction(BaseModel):
    core_contribution: str
    enterprise_impact: str
    contrarian_point: str
    factual_claims: list[str]
    evidence_quotes: list[str] = Field(max_length=5)
    reader_value: str = ""
    limitations: str = ""


class EvaluationResult(BaseModel):
    gate: Literal["CREDIBILITY", "FAITHFULNESS", "EDITORIAL", "CLAIMS", "VISUAL", "HOOK", "STANCE"]
    status: Literal["PASS", "FAIL", "ABSTAIN"]
    score: float = Field(ge=0, le=5)
    reasons: list[str] = Field(default_factory=list)
    evidence_checked: list[str] = Field(default_factory=list)
    revision: int = 0


class Draft(BaseModel):
    draft_id: str
    topic_id: str
    topic_name: str
    title: str
    abstract: str
    content: str
    first_comment: str = ""
    stance: str = ""
    post_impressions: int | None = None
    post_comments: int | None = None
    post_reposts: int | None = None
    metrics_recorded_at: str | None = None
    sources: list[SourceEvidence]
    evaluator_status: Literal["PASS", "FAIL", "ABSTAIN"] = "PASS"
    evaluations: list[EvaluationResult] = Field(default_factory=list)
    extraction: InsightExtraction | None = None
    decision: Literal["PENDING", "APPROVED", "DISCARDED"] = "PENDING"
    publication_status: Literal["NOT_PUBLISHED", "PUBLISHED", "MANUAL_REQUIRED", "MANUALLY_PUBLISHED"] = "NOT_PUBLISHED"
    created_at: str = Field(default_factory=now_iso)
    approved_at: str | None = None
    published_at: str | None = None
    linkedin_post_id: str | None = None
    publish_error: str | None = None
    trace_id: str | None = None
    infographic_status: Literal["NOT_REQUESTED", "GENERATING", "READY", "FAILED"] = "NOT_REQUESTED"
    infographic_path: str | None = None
    infographic_qa: EvaluationResult | None = None
    infographic_error: str | None = None
    ready_to_post_dir: str | None = None
