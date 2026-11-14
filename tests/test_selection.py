"""Regression tests for the shared selection rules. Run from /tmp/pkgtest with: python -m pytest tests -q"""
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Stub langsmith if the sandbox lacks it; selection does not use tracing.
try:
    import langsmith  # noqa: F401
except ImportError:
    stub = types.ModuleType("langsmith")
    stub.traceable = lambda *a, **k: (lambda f: f)
    sys.modules["langsmith"] = stub

from app import selection  # noqa: E402
from app.models import CandidateArticle, ResearchBrief, ResearchItem, ResearchScore, TopicCandidate  # noqa: E402

NOW = datetime(2026, 11, 12, tzinfo=UTC)
SETTINGS = {"research_window_days": 30, "minimum_audience_fit_score": 4}


def article(url, published, title="t"):
    return CandidateArticle(
        source_id="s", topic_id="governance", title=title, url=url, publisher="p",
        text="body", authority_score=5, published_at=published,
    )


def item(item_id, url):
    return ResearchItem(id=item_id, title=item_id, url=url, pillar="AI GOVERNANCE, COMPLIANCE AND ENTERPRISE RISK",
                        source_type="official", summary="s", score=ResearchScore(impact=4, novelty=3, evidence=4, shareability=3))


def topic(rank, item_ids, fit=5, why="why now", decision="decision", reader="CISO"):
    return TopicCandidate(rank=rank, headline=f"h{rank}", claim="c", pillar="p", target_reader=reader,
                          audience_fit_score=fit, why_now=why, decision_relevance=decision, item_ids=item_ids)


def test_2013_article_is_rejected_by_date_gate():
    kept, rejected = selection.date_gate([article("https://x/2013", "2013-03-14")], 30, NOW)
    assert kept == []
    assert len(rejected) == 1
    assert "older than the 30-day" in rejected[0]["reason"]


def test_undated_article_is_rejected():
    kept, rejected = selection.date_gate([article("https://x/u", None)], 30, NOW)
    assert kept == [] and "No publication date" in rejected[0]["reason"]


def test_unreadable_date_is_rejected():
    kept, rejected = selection.date_gate([article("https://x/b", "sometime last spring")], 30, NOW)
    assert kept == [] and "could not be read" in rejected[0]["reason"]


def test_recent_iso_and_rfc822_dates_are_kept():
    kept, rejected = selection.date_gate([
        article("https://x/iso", "2026-11-01T09:00:00Z"),
        article("https://x/rfc", "Tue, 10 Nov 2026 08:00:00 GMT"),
    ], 30, NOW)
    assert [c.url for c in kept] == ["https://x/iso", "https://x/rfc"]
    assert rejected == []


def test_screen_never_sends_old_article_to_analyst(monkeypatch):
    seen = []

    def fake_research(topic_id, topic_name, candidates, window, covered):
        seen.extend(c.url for c in candidates)
        return ResearchBrief(run_date="2026-11-12", window_days=window, items=[item("i1", "https://x/new")],
                             topics=[topic(1, ["i1"])])

    monkeypatch.setattr(selection, "research_topic", fake_research)
    candidates = [article("https://x/old", "2013-03-14"), article("https://x/new", "2026-11-05")]
    result = selection.screen_candidates("governance", "AI governance", candidates, SETTINGS, [], now=NOW)
    assert seen == ["https://x/new"]
    assert result.status == "VALID"
    assert [c.url for c in result.eligible] == ["https://x/new"]
    assert any("older than" in r["reason"] for r in result.rejections)


def test_eligible_follows_topic_rank_and_drops_weak_topics(monkeypatch):
    brief = ResearchBrief(
        run_date="2026-11-12", window_days=30,
        items=[item("a", "https://x/a"), item("b", "https://x/b"), item("c", "https://x/c")],
        topics=[topic(2, ["b"]), topic(1, ["a"]), topic(3, ["c"], fit=2)],
    )
    monkeypatch.setattr(selection, "research_topic", lambda *a, **k: brief)
    candidates = [article(u, "2026-11-05") for u in ("https://x/a", "https://x/b", "https://x/c")]
    result = selection.screen_candidates("governance", "AI governance", candidates, SETTINGS, [], now=NOW)
    assert [c.url for c in result.eligible] == ["https://x/a", "https://x/b"]
    assert "https://x/c" not in [c.url for c in result.eligible]


def test_analyst_error_produces_no_drafts(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(selection, "research_topic", boom)
    result = selection.screen_candidates("governance", "AI governance", [article("https://x/a", "2026-11-05")], SETTINGS, [], now=NOW)
    assert result.status == "ANALYST_ERROR" and result.eligible == []


def test_invalid_brief_produces_no_drafts(monkeypatch):
    brief = ResearchBrief(run_date="2026-11-12", window_days=30, items=[item("a", "https://x/a")],
                          topics=[topic(1, ["missing-id"])])
    monkeypatch.setattr(selection, "research_topic", lambda *a, **k: brief)
    result = selection.screen_candidates("governance", "AI governance", [article("https://x/a", "2026-11-05")], SETTINGS, [], now=NOW)
    assert result.status == "ANALYST_INVALID" and result.eligible == []


def test_no_dated_articles_skips_analyst(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("analyst must not run without dated articles")

    monkeypatch.setattr(selection, "research_topic", fail)
    result = selection.screen_candidates("governance", "AI governance", [article("https://x/2013", "2013-03-14")], SETTINGS, [], now=NOW)
    assert result.status == "NO_DATED_CANDIDATES" and result.eligible == []
