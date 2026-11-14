"""OpenAI Responses API adapters with source-grounded prompts."""

from __future__ import annotations

import json
import os
from datetime import date
from typing import TypeVar

from pydantic import BaseModel

from .config import ROOT
from .models import (
    CandidateArticle,
    Draft,
    InsightExtraction,
    ResearchBrief,
    SourceEvidence,
)

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorate(function):
            return function
        return decorate


class GeneratedDraft(BaseModel):
    title: str
    abstract: str
    content: str
    # One sentence, max 220 characters: the opinion this post defends. Must match the claim on line 2.
    stance: str


# Shared post rules for drafting and revision. The post is clean text: no section
# labels, because the labels are internal structure and must not be published.
POST_RULES = (
    "Write one LinkedIn post in the first person, as the named author speaking to senior leaders. "
    "Use only the supplied extraction and verbatim evidence. Do not add facts, metrics, names, dates, "
    "or outcomes. "
    "Output plain text: no headings, no section labels, no markdown, no hashtags. "
    "Use short paragraphs separated by blank lines. "
    "Write like a column that an editor has approved, not a summary of the source. "
    "Line 1 is the hook: a standalone sentence of 40 to 150 characters that states the most important "
    "consequence, number, cost, or deadline in the evidence. Never start with background, with the "
    "source's name, or with the word 'The'. "
    "Line 2 onward: state the one stance in plain sentences, the claim a CISO or CTO could reasonably "
    "disagree with, tied to a decision the reader must make. "
    "Then the evidence that supports the stance, with technical terms explained in plain English. "
    "Then one practical action with a named owner or a decision point. "
    "Then one limitation the source states or clearly implies. "
    "End with one question addressed to a named role, such as CISO, CTO, or head of compliance. "
    "Do not end with 'thoughts?' or a generic question. "
    "Do not describe the webpage. "
    "Target 900 to 1,200 characters and never exceed 1,300 characters. "
    "The title is 8 to 14 words, specific, tension-based, and not a copy of the source title."
)


def build_first_comment(candidate: CandidateArticle) -> str:
    """Source line for the first comment. Built from fetched metadata, never generated."""
    return f"Source: {candidate.title} ({candidate.publisher})\n{candidate.url}"


def research_prompt() -> str:
    path = ROOT / "config" / "research_prompt.txt"
    return path.read_text(encoding="utf-8")

T = TypeVar("T", bound=BaseModel)


def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _strict_schema(model_type: type[T]) -> dict:
    """Make Pydantic's JSON schema acceptable to OpenAI strict mode."""
    schema = model_type.model_json_schema()

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                if isinstance(node.get("properties"), dict):
                    node["required"] = list(node["properties"].keys())
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


@traceable(name="openai_structured_generation", run_type="llm")
def _structured(model_type: type[T], instructions: str, payload: str) -> T:
    """Call Responses with strict JSON schema; reject malformed output."""
    client = _client()
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-6-luna"),
        input=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": payload},
        ],
        text={"format": {
            "type": "json_schema",
            "name": model_type.__name__.lower(),
            "strict": True,
            "schema": _strict_schema(model_type),
        }},
    )
    return model_type.model_validate_json(response.output_text)


def extract_insights(candidate: CandidateArticle) -> InsightExtraction:
    return _structured(
        InsightExtraction,
        "Extract only facts supported by SOURCE TEXT. Never fill gaps from memory. "
        "Every factual claim must have a verbatim evidence quote. Return only the 3-5 strongest "
        "source sentences as evidence_quotes; never return navigation labels, vendor directory "
        "rows, page chrome, or partial fragments. If unsupported, "
        "return an empty claim list. Do not invent metrics, names, dates, or outcomes. "
        "Also identify the practical reader value and any limitation or missing context. "
        "Explicitly explain what the affected technology or deployment is, who may be exposed, "
        "what the source establishes, what it does not establish, and what decision a senior "
        "leader should require from the responsible team. Do not assume readers know the jargon.",
        f"SOURCE URL: {candidate.url}\nSOURCE TITLE: {candidate.title}\nSOURCE TEXT:\n{candidate.text}",
    )


@traceable(name="research_analyst", run_type="chain")
def research_topic(topic_id: str, topic_name: str, candidates: list[CandidateArticle], window_days: int, covered_topics: list[str] | None = None) -> ResearchBrief:
    """Turn fetched, opened source pages into a ranked evidence brief.

    This is deliberately a separate LLM call from drafting. The model receives only
    evidence that the deterministic collector fetched; it cannot claim to have opened
    an arbitrary URL or perform unsupported web research.
    """
    evidence_packet = []
    for candidate in candidates:
        evidence_packet.append({
            "source_id": candidate.source_id,
            "topic_id": candidate.topic_id,
            "title": candidate.title,
            "url": candidate.url,
            "publisher": candidate.publisher,
            "source_type": candidate.source_type,
            "published_at": candidate.published_at or "",
            "authority_score": candidate.authority_score,
            "retrieved_at": candidate.retrieved_at,
            "opened_text_excerpt": candidate.text[:7000],
        })
    instruction = research_prompt().replace("{{WINDOW_DAYS}}", str(window_days)).replace("{{TODAY}}", date.today().isoformat())
    instruction = instruction.replace("{{COVERED_TOPICS}}", ", ".join(covered_topics or []) or "none provided")
    return _structured(
        ResearchBrief,
        instruction,
        json.dumps({
            "pillar": topic_name,
            "topic_id": topic_id,
            "evidence_packet": evidence_packet,
            "constraints": {"window_days": window_days, "max_items": 15, "max_topics": 8},
        }),
    )


def write_draft(candidate: CandidateArticle, extraction: InsightExtraction, draft_number: int) -> Draft:
    evidence_quotes = extraction.evidence_quotes[:5]
    # One source entry per candidate (not one per quote), so the source list is not repeated.
    sources = [SourceEvidence(
        source_id=candidate.source_id,
        title=candidate.title,
        url=candidate.url,
        publisher=candidate.publisher,
        excerpt=evidence_quotes[0] if evidence_quotes else "",
        authority_score=candidate.authority_score,
        content_hash="",
    )]
    result = _structured(
        GeneratedDraft,
        POST_RULES + f" This is proposal variant {draft_number}; make its hook meaningfully different "
        "from other variants while preserving the same evidence.",
        json.dumps({
            "topic_id": candidate.topic_id,
            "topic_name": candidate.topic_id.replace("_", " ").title(),
            "draft_number": draft_number,
            "source": candidate.url,
            "extraction": extraction.model_dump(),
            "evidence": evidence_quotes,
        }),
    )
    return Draft(
        draft_id=f"{candidate.topic_id}-{draft_number}",
        topic_id=candidate.topic_id,
        topic_name=candidate.topic_id.replace("_", " ").title(),
        title=result.title,
        abstract=result.abstract,
        content=result.content,
        stance=result.stance,
        sources=sources,
        first_comment=build_first_comment(candidate),
    )


def revise_draft(draft: Draft, reasons: list[str], revision: int) -> Draft:
    """Rewrite only editorial form; source claims and evidence remain fixed."""
    result = _structured(
        GeneratedDraft,
        "Revise the supplied LinkedIn post. Remove or rephrase any claim listed in "
        "failed_editorial_checks that is not supported by the evidence. "
        + POST_RULES,
        json.dumps({
            "draft": {"title": draft.title, "abstract": draft.abstract, "content": draft.content},
            "failed_editorial_checks": reasons,
            "evidence": [source.model_dump() for source in draft.sources],
        }),
    )
    draft.title, draft.abstract, draft.content = result.title, result.abstract, result.content
    draft.stance = result.stance
    for evaluation in draft.evaluations:
        evaluation.revision = revision
    return draft
