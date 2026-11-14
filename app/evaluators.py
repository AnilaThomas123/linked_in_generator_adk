"""Deterministic safety gates plus an abstaining faithfulness checker."""

from __future__ import annotations

import re
import unicodedata

from .models import CandidateArticle, Draft, EvaluationResult, InsightExtraction

TEMPLATE_LABELS = [
    "HOOK", "WHAT THE SOURCE SHOWS", "WHY IT MATTERS", "PRACTICAL TAKEAWAY", "QUESTION FOR READERS",
]
# Capitalized words that are not names or evidence terms when they start a sentence.
_CAPITAL_ALLOWLIST = {"I", "We", "Our", "My", "The", "This", "That", "You", "Your", "It", "If", "When"}

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorate(function):
            return function
        return decorate


def _normalize_quote(value: str) -> str:
    """Normalize formatting noise while preserving the quoted words."""
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    boundary_chars = " \t\r\n\"'" + "\u00ab\u00bb\u201c\u201d\u2018\u2019\ufffd"
    return value.strip(boundary_chars)


@traceable(name="credibility_gate", run_type="chain")
def credibility_gate(candidate: CandidateArticle, minimum: float) -> EvaluationResult:
    ok = candidate.authority_score >= minimum and bool(candidate.url.startswith("http"))
    return EvaluationResult(
        gate="CREDIBILITY", status="PASS" if ok else "FAIL",
        score=candidate.authority_score,
        reasons=[] if ok else ["Source did not meet the configured authority threshold."],
        evidence_checked=[candidate.url],
    )


@traceable(name="faithfulness_gate", run_type="chain")
def faithfulness_gate(candidate: CandidateArticle, extraction: InsightExtraction, minimum: float) -> EvaluationResult:
    source = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", candidate.text)).casefold()
    supported = []
    for quote in extraction.evidence_quotes:
        normalized_quote = _normalize_quote(quote)
        if normalized_quote and normalized_quote.casefold() in source:
            supported.append(quote)
    score = len(supported) / max(1, len(extraction.evidence_quotes))
    status = "PASS" if extraction.evidence_quotes and score >= minimum else "FAIL"
    return EvaluationResult(
        gate="FAITHFULNESS", status=status, score=score,
        reasons=[] if status == "PASS" else ["At least one evidence quote was missing or not verbatim."],
        evidence_checked=supported,
    )


HOOK_CONSEQUENCE_TERMS = [
    "must", "deadline", "cost", "exposure", "breach", "exploit", "patch",
    "decide", "fails", "outage", "fine", "liability", "%", "$",
]
ROLE_TERMS = [
    "ciso", "cto", "cio", "cro", "board", "risk", "compliance", "security leader",
    "engineering lead", "head of", "vp ", "audit", "procurement",
]


def closing_question_ok(draft: Draft) -> bool:
    """The question must sit in the last 220 characters and name a role the reader holds."""
    tail = draft.content[-220:].lower()
    return "?" in tail and any(term in tail for term in ROLE_TERMS)


def hook_gate(draft: Draft) -> EvaluationResult:
    """First line must carry a number or a consequence, so it reads well as a feed preview."""
    first = next((line.strip() for line in draft.content.splitlines() if line.strip()), "")
    lowered = first.lower()
    ok = bool(re.search(r"\d", first)) or any(term in lowered for term in HOOK_CONSEQUENCE_TERMS)
    return EvaluationResult(
        gate="HOOK", status="PASS" if ok else "FAIL", score=5.0 if ok else 0.0,
        reasons=[] if ok else [
            "The first line must state a number, a deadline, a cost, or a consequence. "
            "Open with the consequence, not the background.",
        ],
    )


def stance_gate(draft: Draft) -> EvaluationResult:
    """A stance must be written and be one sentence. This is a rough check; a model judge is better."""
    stance = draft.stance.strip()
    ok = bool(stance) and len(stance) <= 220 and stance.count(". ") == 0
    return EvaluationResult(
        gate="STANCE", status="PASS" if ok else "FAIL", score=5.0 if ok else 0.0,
        reasons=[] if ok else ["Write one stance sentence (max 220 characters) that a CISO or CTO could disagree with."],
    )


@traceable(name="editorial_gate", run_type="chain")
def editorial_gate(
    draft: Draft, max_chars: int, banned: list[str], min_chars: int = 700,
    template_labels: list[str] | None = None,
    executive_signal_terms: list[str] | None = None,
) -> EvaluationResult:
    """Checks the clean post text. Template labels must NOT appear in the published text."""
    lowered = draft.content.lower()
    found = [word for word in banned if word.lower() in lowered]
    generic_markers = [
        "can help organizations",
        "this highlights the importance of",
        "verify the details",
        "it is important to consider",
    ]
    generic_found = [phrase for phrase in generic_markers if phrase in lowered]
    labels = template_labels if template_labels is not None else TEMPLATE_LABELS
    leaked_labels = [
        label for label in labels
        if re.search(rf"(?im)^\s*{re.escape(label)}\s*:?\s*$", draft.content)
    ]
    first_line = next((line.strip() for line in draft.content.splitlines() if line.strip()), "")
    bad_hook = not (40 <= len(first_line) <= 150)
    has_question = closing_question_ok(draft)
    title_words = draft.title.split()
    generic_titles = {"what leaders need to know", "an important update", "security update", "summary", "advice"}
    weak_title = len(title_words) < 5 or len(title_words) > 16 or draft.title.strip().lower() in generic_titles
    signal_terms = executive_signal_terms or [
        "decision", "risk", "control", "deployment", "investment", "compliance",
        "audit", "procurement", "budget", "board", "workforce", "operating model",
    ]
    missing_executive_signal = not any(term.lower() in lowered for term in signal_terms)
    ok = (
        min_chars <= len(draft.content) <= max_chars
        and not found
        and not generic_found
        and not leaked_labels
        and not bad_hook
        and has_question
        and not missing_executive_signal
        and not weak_title
        and bool(draft.sources)
    )
    reasons = []
    if found:
        reasons.append(f"Banned wording: {', '.join(found)}")
    if generic_found:
        reasons.append(f"Generic executive-value language: {', '.join(generic_found)}")
    if missing_executive_signal:
        reasons.append("Draft lacks a concrete executive decision, risk, control, investment, or operating consequence.")
    if weak_title:
        reasons.append("Title is too generic or not specific enough for an executive audience.")
    if leaked_labels:
        reasons.append(f"Template labels must not appear in the post: {', '.join(leaked_labels)}")
    if bad_hook:
        reasons.append("The first line must be a standalone hook of 40 to 150 characters.")
    if not has_question:
        reasons.append("The post must end with a specific question for a named role.")
    if len(draft.content) < min_chars:
        reasons.append(f"Draft is too short ({len(draft.content)} characters).")
    if len(draft.content) > max_chars:
        reasons.append(f"Draft exceeds the {max_chars}-character limit.")
    if not draft.sources:
        reasons.append("Draft has no source evidence.")
    return EvaluationResult(
        gate="EDITORIAL", status="PASS" if ok else "FAIL",
        score=5.0 if ok else 0.0,
        reasons=[] if ok else reasons,
        evidence_checked=[source.url for source in draft.sources],
    )


def claims_gate(draft: Draft, candidate: CandidateArticle) -> EvaluationResult:
    """Checks the post itself: every number and mid-sentence proper name must appear in the source.

    Heuristic by design: it catches invented figures, dates, and names. It cannot judge
    whether a true-looking sentence is a fair reading of the source; a human still reviews.
    """
    source = unicodedata.normalize("NFKC", f"{candidate.title} {candidate.text}").casefold()
    unsupported: list[str] = []
    for token in re.findall(r"(?<![\w.])\$?\d[\d,.]*%?", draft.content):
        core = token.rstrip(".,")
        if core and core.casefold() not in source:
            unsupported.append(core)
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", draft.content):
        for word in re.findall(r"[A-Za-z][A-Za-z\-]+", sentence)[1:]:
            if word[0].isupper() and len(word) > 2 and word not in _CAPITAL_ALLOWLIST and word.casefold() not in source:
                unsupported.append(word)
    unsupported = sorted(set(unsupported))
    ok = not unsupported
    return EvaluationResult(
        gate="CLAIMS", status="PASS" if ok else "FAIL", score=5.0 if ok else 0.0,
        reasons=[] if ok else [
            "Numbers or names in the post were not found in the source: " + ", ".join(unsupported[:10])
        ],
        evidence_checked=[candidate.url],
    )


def evaluate_draft(draft: Draft, candidate: CandidateArticle, extraction: InsightExtraction, settings: dict) -> Draft:
    results = [
        credibility_gate(candidate, float(settings.get("min_authority_score", 4.0))),
        faithfulness_gate(candidate, extraction, float(settings.get("minimum_faithfulness_score", .90))),
        editorial_gate(
            draft,
            int(settings.get("max_post_characters", 1300)),
            settings.get("banned_buzzwords", []),
            int(settings.get("minimum_post_characters", 700)),
            settings.get("template_labels", TEMPLATE_LABELS),
            settings.get("executive_signal_terms", []),
        ),
        claims_gate(draft, candidate),
        hook_gate(draft),
        stance_gate(draft),
    ]
    draft.evaluations = results
    draft.evaluator_status = "PASS" if all(item.status == "PASS" for item in results) else "FAIL"
    return draft
