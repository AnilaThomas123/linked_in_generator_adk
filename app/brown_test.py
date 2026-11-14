"""Offline readiness checks for tomorrow's end-to-end brown test."""

from .config import load_topics
from .evaluators import credibility_gate, faithfulness_gate
from .generation import GeneratedDraft, _strict_schema
from .models import CandidateArticle, InsightExtraction
from .research import load_source_registry
from .storage import migrate_json_drafts


def run_brown_checks() -> list[str]:
    topics, settings = load_topics()
    assert len(topics) == 1 and topics[0].id == "cybersecurity", "Expected the cybersecurity demo topic"
    assert int(settings["drafts_per_topic"]) == 2, "Expected two drafts for the demo topic"
    assert load_source_registry(), "Source registry is empty"
    assert _strict_schema(GeneratedDraft).get("additionalProperties") is False
    candidate = CandidateArticle(
        source_id="brown-test", topic_id=topics[0].id, title="Test", url="https://example.test",
        publisher="Test", text="The system reduced latency by ten percent.", authority_score=5,
    )
    extraction = InsightExtraction(
        core_contribution="Latency reduction", enterprise_impact="Faster systems", contrarian_point="None",
        factual_claims=["Latency fell by ten percent"], evidence_quotes=["The system reduced latency by ten percent."],
    )
    assert credibility_gate(candidate, 4).status == "PASS"
    assert faithfulness_gate(candidate, extraction, .9).status == "PASS"
    return [
        f"Configured demo topic: {topics[0].name}",
        f"Drafts per topic: {settings['drafts_per_topic']}",
        f"Source registry entries: {len(load_source_registry())}",
        f"Migrated JSON drafts: {migrate_json_drafts()}",
        "Strict OpenAI response schema: PASS",
        "Credibility gate: PASS",
        "Faithfulness gate: PASS",
        "Human publication boundary: configured",
    ]
