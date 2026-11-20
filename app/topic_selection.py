"""Topic choice for one pipeline run. Without a choice, the run uses cybersecurity."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from .config import ROOT, load_topics

DEFAULT_TOPIC = "cybersecurity"
# Kept outside data/adk_workflow, because initialize_workflow() clears that folder.
SELECTED_PATH = ROOT / "data" / "selected_topic.json"
TOPIC_CHOICES = ["cybersecurity", "ai governance", "ai announcements"]

_ALIASES = {
    "cybersecurity": "cybersecurity",
    "cyber security": "cybersecurity",
    "security": "cybersecurity",
    "ai governance": "governance_risk",
    "governance": "governance_risk",
    "governance and risk": "governance_risk",
    "governance risk": "governance_risk",
    "ai announcements": "ai_announcements",
    "latest ai announcements": "ai_announcements",
    "announcements": "ai_announcements",
    "ai announcement": "ai_announcements",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", text or "")).strip().lower()


def resolve_topic(text: str | None) -> str | None:
    """Map what the user said to a topic id. Returns None for an unknown topic."""
    if not text or not text.strip():
        return None
    key = _normalize(text)
    known_ids = {_normalize(topic.id): topic.id for topic in load_topics()[0]}
    if key in known_ids:
        return known_ids[key]
    return _ALIASES.get(key)


def topic_in_text(text: str | None) -> str | None:
    """Find a topic named anywhere in a message, such as 'run the cybersecurity topic'. None when no topic is named."""
    if not text or not text.strip():
        return None
    # Punctuation is dropped, so "ai announcements, please" still matches.
    padded = " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
    phrases = set(_ALIASES) | {_normalize(topic.id) for topic in load_topics()[0]}
    phrases = {" ".join(re.findall(r"[a-z0-9]+", phrase)) for phrase in phrases}
    for phrase in sorted(phrases, key=len, reverse=True):
        if f" {phrase} " in padded:
            return resolve_topic(phrase)
    return None


def topic_name(topic_id: str) -> str:
    for topic in load_topics()[0]:
        if topic.id == topic_id:
            return topic.name
    raise ValueError(f"Unknown topic: {topic_id}")


def save_selected_topic(topic_id: str) -> None:
    SELECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SELECTED_PATH.write_text(
        json.dumps({"topic_id": topic_id, "saved_at": datetime.now(UTC).isoformat()}, indent=2),
        encoding="utf-8",
    )


def consume_selected_topic() -> str:
    """Return the chosen topic for the next run, then clear the choice so it applies to one run only."""
    if not SELECTED_PATH.exists():
        return DEFAULT_TOPIC
    try:
        topic_id = json.loads(SELECTED_PATH.read_text(encoding="utf-8")).get("topic_id", DEFAULT_TOPIC)
    except (OSError, ValueError):
        topic_id = DEFAULT_TOPIC
    SELECTED_PATH.unlink(missing_ok=True)
    return topic_id if resolve_topic(topic_id) == topic_id else DEFAULT_TOPIC
