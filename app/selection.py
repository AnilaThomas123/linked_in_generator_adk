"""Shared article selection rules for the dashboard pipeline and the ADK workflow.

Both paths call ``screen_candidates`` so the same rules decide which fetched
articles may become drafts:

1. Date gate (deterministic, no model call): an article must carry a readable
   publication date inside the research window. Undated articles are dropped.
2. Research brief (model call, only for dated articles): the analyst ranks
   topics for executives. Only topics that pass the audience-fit, why-now and
   decision-relevance checks, and that cite evidence the collector really
   fetched, may select articles.

Every article that is dropped gets a human-readable reason so the dashboard can
show why a run produced few drafts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from .generation import research_topic
from .models import CandidateArticle, ResearchBrief
from .research import validate_research_brief


@dataclass
class ScreenResult:
    status: str
    error: str = ""
    eligible: list[CandidateArticle] = field(default_factory=list)
    brief: ResearchBrief | None = None
    rejections: list[dict] = field(default_factory=list)


def parse_published(value: str | None) -> datetime | None:
    """Return a UTC datetime for ISO 8601 or RFC 822 dates, or None if unreadable."""
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def date_gate(
    candidates: list[CandidateArticle],
    window_days: int,
    now: datetime | None = None,
) -> tuple[list[CandidateArticle], list[dict]]:
    """Keep only articles dated inside the window. Everything else gets a reason."""
    now = now or datetime.now(UTC)
    earliest = now - timedelta(days=window_days)
    latest = now + timedelta(days=1)
    kept: list[CandidateArticle] = []
    rejected: list[dict] = []

    def reject(candidate: CandidateArticle, reason: str) -> None:
        rejected.append({"stage": "date_gate", "reason": reason, "url": candidate.url, "title": candidate.title})

    for candidate in candidates:
        if not candidate.published_at:
            reject(candidate, "No publication date was found on the page or feed, so the article cannot be checked against the research window.")
            continue
        published = parse_published(candidate.published_at)
        if published is None:
            reject(candidate, f"The publication date '{candidate.published_at[:40]}' could not be read.")
        elif published < earliest:
            reject(candidate, f"Published {published.date().isoformat()}, which is older than the {window_days}-day research window.")
        elif published > latest:
            reject(candidate, f"Dated {published.date().isoformat()}, which is in the future.")
        else:
            kept.append(candidate)
    return kept, rejected


def screen_candidates(
    topic_id: str,
    topic_name: str,
    candidates: list[CandidateArticle],
    settings: dict,
    covered_topics: list[str] | None = None,
    now: datetime | None = None,
) -> ScreenResult:
    """Apply the date gate, then the executive-relevance brief, and return the articles that may become drafts.

    ``eligible`` is ordered by the analyst's topic rank, so the first article is
    the strongest candidate.
    """
    window = int(settings.get("research_window_days", 30))
    if not candidates:
        return ScreenResult(status="NO_CANDIDATES", error="No article-level candidates were collected for this topic.")

    dated, rejections = date_gate(candidates, window, now)
    if not dated:
        return ScreenResult(
            status="NO_DATED_CANDIDATES",
            error=f"None of the {len(candidates)} collected articles has a usable date inside the {window}-day window.",
            rejections=rejections,
        )

    try:
        brief = research_topic(topic_id, topic_name, dated, window, covered_topics or [])
    except Exception as exc:  # noqa: BLE001 - report the analyst failure, never draft without a brief
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
        rejections.extend({"stage": "research", "reason": f"Research analyst failed: {error}", "url": c.url, "title": c.title} for c in dated)
        return ScreenResult(status="ANALYST_ERROR", error=error, rejections=rejections)

    errors = validate_research_brief(brief, dated)
    item_ids = {item.id for item in brief.items}
    url_by_item = {item.id: item.url for item in brief.items}
    url_to_candidate = {candidate.url: candidate for candidate in dated}
    minimum_fit = int(settings.get("minimum_audience_fit_score", 4))

    eligible_topics = [
        topic for topic in sorted(brief.topics, key=lambda t: t.rank)
        if topic.audience_fit_score >= minimum_fit
        and topic.target_reader.strip()
        and topic.why_now.strip()
        and topic.decision_relevance.strip()
        and topic.item_ids
        and set(topic.item_ids) <= item_ids
    ]

    selected: list[CandidateArticle] = []
    seen: set[str] = set()
    if not errors:
        for topic in eligible_topics:
            for item_id in topic.item_ids:
                candidate = url_to_candidate.get(url_by_item.get(item_id, ""))
                if candidate and candidate.url not in seen:
                    seen.add(candidate.url)
                    selected.append(candidate)

    if errors:
        status = "ANALYST_INVALID"
        error = "; ".join(errors[:3])
        not_selected_reason = f"The research brief failed validation, so no article was selected: {error}"
    elif not selected:
        status = "NO_EXECUTIVE_TOPIC"
        error = "No topic candidate met the executive-audience relevance requirements."
        not_selected_reason = error
    else:
        status = "VALID"
        error = ""
        not_selected_reason = "Not cited by an executive-relevant topic in the research brief."

    for candidate in dated:
        if candidate.url not in seen:
            rejections.append({"stage": "relevance", "reason": not_selected_reason, "url": candidate.url, "title": candidate.title})

    brief = brief.model_copy(update={"topics": eligible_topics if status == "VALID" else []})
    return ScreenResult(status=status, error=error, eligible=selected, brief=brief, rejections=rejections)
