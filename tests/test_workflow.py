from app.agent import _has_state
from app.config import load_topics
from app.evaluators import claims_gate, credibility_gate, editorial_gate, faithfulness_gate
from app.generation import _strict_schema, research_prompt
from app.infographic import create_infographic
from app.models import CandidateArticle, Draft, InsightExtraction, ResearchBrief
from app.publishing import publish_after_approval
from app.research import (
    _feed_entries,
    _is_weak_page,
    _plain_text,
    _response_text,
    validate_research_brief,
)
from app.workflow_state import write_state


def test_publish_requires_explicit_approval():
    draft = Draft(
        draft_id="test-1",
        topic_id="ai_agents",
        topic_name="AI Agents",
        title="Test",
        abstract="Test abstract",
        content="Test content",
        sources=[],
    )
    try:
        publish_after_approval(draft)
    except ValueError:
        return
    raise AssertionError("unapproved draft was allowed to publish")


def test_approved_draft_requires_manual_confirmation():
    draft = Draft(
        draft_id="test-2",
        topic_id="ai_agents",
        topic_name="AI Agents",
        title="Test",
        abstract="Test abstract",
        content="Test content",
        sources=[],
        decision="APPROVED",
    )
    result = publish_after_approval(draft)
    assert result.publication_status == "MANUAL_REQUIRED"


def test_credibility_gate_rejects_low_authority_source():
    candidate = CandidateArticle(
        source_id="blog", topic_id="ai_agents", title="Blog", url="https://example.com",
        publisher="Blog", text="Evidence text", authority_score=2.0,
    )
    result = credibility_gate(candidate, minimum=4.0)
    assert result.status == "FAIL"


def test_faithfulness_gate_rejects_unverifiable_quotes():
    candidate = CandidateArticle(
        source_id="source", topic_id="ai_agents", title="Source", url="https://example.com",
        publisher="Source", text="The system reduced latency by ten percent.", authority_score=5.0,
    )
    extraction = InsightExtraction(
        core_contribution="Latency reduction", enterprise_impact="Faster systems",
        contrarian_point="None", factual_claims=["It doubled throughput"],
        evidence_quotes=["It doubled throughput"],
    )
    result = faithfulness_gate(candidate, extraction, minimum=.90)
    assert result.status == "FAIL"


def test_faithfulness_gate_accepts_verbatim_quote_with_boundary_artifacts():
    candidate = CandidateArticle(
        source_id="source", topic_id="cybersecurity", title="Source", url="https://example.com",
        publisher="Source", text="The advisory says memory limits can trigger the vulnerable path.", authority_score=5.0,
    )
    extraction = InsightExtraction(
        core_contribution="A warning", enterprise_impact="A control risk",
        contrarian_point="A limit can trigger the path",
        factual_claims=["Memory limits can trigger the vulnerable path"],
        evidence_quotes=["�The advisory says memory limits can trigger the vulnerable path.�"],
    )
    result = faithfulness_gate(candidate, extraction, minimum=.90)
    assert result.status == "PASS"


def test_rss_entries_are_parsed_as_separate_candidates():
    xml = "<rss><channel><item><title>One</title><link>https://example.test/one</link><description>Evidence one</description></item></channel></rss>"
    assert _feed_entries(xml) == [("One", "https://example.test/one", "Evidence one", "")]


def test_html_body_is_used_as_evidence_not_only_title():
    title, text = _plain_text("<html><head><title>Article title</title></head><body><p>" + "Evidence " * 80 + "</p></body></html>")
    assert title == "Article title"
    assert len(text) > 250


def test_utf8_source_bytes_are_decoded_without_replacement_characters():
    class Response:
        content = b"The advisory says: memory limits can trigger the vulnerable path."
        encoding = "windows-1252"

    decoded = _response_text(Response())
    assert "�" not in decoded
    assert "memory limits can trigger" in decoded


def test_index_and_directory_pages_are_not_article_candidates():
    assert _is_weak_page("https://arxiv.org/list/cs.AI/recent", "Recent submissions", "Displaying 1 - 50")
    assert _is_weak_page("https://nist.gov/tools", "Tools", "Browse all tools and filter by topic")
    assert not _is_weak_page("https://arxiv.org/abs/1234.5678", "A measured study", "We evaluate the method and report results.")


def test_research_prompt_is_configured_with_required_contract():
    prompt = research_prompt()
    assert "EVIDENCE BRIEF" in prompt
    assert "Do not draft LinkedIn posts" in prompt
    assert "{{WINDOW_DAYS}}" in prompt


def test_research_schema_is_strict_and_evidence_anchored():
    schema = _strict_schema(ResearchBrief)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    brief = ResearchBrief(run_date="2026-11-08", window_days=14)
    errors = validate_research_brief(brief, [])
    assert errors == []


def test_configuration_offers_three_topics_with_cybersecurity_default_and_targets_executives():
    topics, settings = load_topics()
    assert [topic.id for topic in topics] == ["cybersecurity", "governance_risk", "ai_announcements"]
    assert settings["default_topic"] == "cybersecurity"
    assert settings["drafts_per_topic"] == 2
    assert settings["research_window_days"] == 30
    assert settings["minimum_audience_fit_score"] >= 4
    assert "executives" in settings["target_audience"].lower()


def test_editorial_gate_rejects_shallow_summary():
    draft = Draft(
        draft_id="quality-1", topic_id="ai_agents", topic_name="AI Agents", title="Summary",
        abstract="A summary", content="A brief observation about a webpage.",
        sources=[{
            "source_id": "s", "title": "Article", "url": "https://example.test/article",
            "publisher": "Publisher", "excerpt": "Evidence", "authority_score": 5,
        }],
    )
    result = editorial_gate(
        draft, 1400, [], 700,
        ["HOOK", "WHAT THE SOURCE SHOWS", "WHY IT MATTERS", "PRACTICAL TAKEAWAY", "QUESTION FOR READERS"],
    )
    assert result.status == "FAIL"


def test_editorial_gate_rejects_generic_executive_advice():
    draft = Draft(
        draft_id="quality-2", topic_id="governance_risk", topic_name="Governance and Risk", title="Advice",
        abstract="Advice", content=("HOOK\n" + "This highlights the importance of reviewing controls. " * 20 +
        "\nWHAT THE SOURCE SHOWS\nEvidence\nWHY IT MATTERS\nRisk\nPRACTICAL TAKEAWAY\nAction\nQUESTION FOR READERS\nWhat changes?"),
        sources=[{"source_id": "s", "title": "Article", "url": "https://example.test/article", "publisher": "Publisher", "excerpt": "Evidence", "authority_score": 5}],
    )
    result = editorial_gate(draft, 1400, [], 700, ["HOOK", "WHAT THE SOURCE SHOWS", "WHY IT MATTERS", "PRACTICAL TAKEAWAY", "QUESTION FOR READERS"])
    assert result.status == "FAIL"


def test_infographic_requires_approval_and_is_evidence_grounded(tmp_path, monkeypatch):
    from app import infographic
    monkeypatch.setattr(infographic, "ROOT", tmp_path)
    draft = Draft(
        draft_id="visual-1", topic_id="ai_agents", topic_name="AI Agents",
        title="Evidence insight", abstract="Useful abstract",
        content="A concrete risk for leaders to act on. Owners should verify the fix in production. Which team will confirm it?",
        decision="APPROVED",
        sources=[{"source_id": "s", "title": "Primary source", "url": "https://example.test/source", "publisher": "Test", "excerpt": "Evidence", "authority_score": 5}],
        extraction=InsightExtraction(
            core_contribution="A concrete idea", enterprise_impact="A practical impact",
            contrarian_point="A nuance", reader_value="A useful takeaway",
            factual_claims=["A concrete idea"], evidence_quotes=["Evidence"],
        ),
    )
    result = create_infographic(draft)
    assert result.infographic_status == "READY"
    assert result.infographic_qa and result.infographic_qa.status == "PASS"
    assert (tmp_path / "infographics" / "visual-1.png").exists()


def test_infographic_rejects_unapproved_draft():
    draft = Draft(draft_id="visual-2", topic_id="ai_agents", topic_name="AI Agents", title="T", abstract="A", content="C", sources=[])
    try:
        create_infographic(draft)
    except ValueError:
        return
    raise AssertionError("unapproved draft generated an infographic")


def test_empty_upstream_state_guards_downstream_agents():
    write_state("credibility", {"approved": [], "rejected": []})
    write_state("insights", [])
    write_state("drafts_ai_agents", [])
    for topic_id in ("ai_agents", "cybersecurity", "governance_risk"):
        write_state(f"faithfulness_{topic_id}", [])
    assert not _has_state("approved_candidates")
    assert not _has_state("insights:ai_agents")
    assert not _has_state("drafts:ai_agents")
    assert not _has_state("any_faithfulness_drafts")


_SOURCE = CandidateArticle(
    source_id="s1", topic_id="cybersecurity", title="Advisory on PHP runtime flaws",
    url="https://example.test/advisory", publisher="CERT/CC",
    text="Versions before 8.2.35 are affected. Update to the fixed release.", authority_score=5.0,
)


def _post(content: str) -> Draft:
    return Draft(
        draft_id="post-1", topic_id="cybersecurity", topic_name="Cybersecurity", title="A title here",
        abstract="Abstract", content=content, sources=[],
    )


def test_claims_gate_rejects_invented_number():
    draft = _post("Our review found 40% of servers exposed.\nWhat will your team check first?")
    result = claims_gate(draft, _SOURCE)
    assert result.status == "FAIL"
    assert "40%" in result.reasons[0]


def test_claims_gate_accepts_numbers_from_source():
    draft = _post("Versions before 8.2.35 are affected.\nWhich owner will confirm the fix?")
    assert claims_gate(draft, _SOURCE).status == "PASS"


def test_editorial_gate_rejects_template_labels_in_post():
    body = "A specific opening line about a concrete risk for leaders today.\n\nHOOK\nWhy it matters and what to do.\n\nWhat will your team check first?"
    result = editorial_gate(_post(body), 1300, [], 40)
    assert result.status == "FAIL"
    assert any("Template labels" in reason for reason in result.reasons)


def test_editorial_gate_requires_hook_and_question():
    body = "Short.\n\nThe patch decision belongs to the platform owner and the budget owner together."
    result = editorial_gate(_post(body), 1300, [], 40)
    assert result.status == "FAIL"
    assert any("hook" in reason for reason in result.reasons)
    assert any("question" in reason for reason in result.reasons)


def test_build_first_comment_uses_fetched_metadata_only():
    from app.generation import build_first_comment
    text = build_first_comment(_SOURCE)
    assert "https://example.test/advisory" in text
    assert "CERT/CC" in text


def test_approve_refuses_unknown_and_failed_drafts(tmp_path, monkeypatch):
    import app.agent as agent
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    assert agent.approve_draft_for_posting("missing-1")["status"] == "NOT_FOUND"
    failed = _post("Body text that failed the gates.")
    failed.evaluator_status = "FAIL"
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "post-1.json").write_text(failed.model_dump_json(), encoding="utf-8")
    assert agent.approve_draft_for_posting("post-1")["status"] == "REFUSED"


def test_replay_without_snapshot_makes_no_live_calls(tmp_path, monkeypatch):
    import app.replay as replay
    monkeypatch.setattr(replay, "SNAPSHOT_ROOT", tmp_path / "none")
    assert replay.replay_summaries("cybersecurity")["mode"] == "NO_SNAPSHOT"


def test_topic_resolution_accepts_plain_names_and_defaults_are_explicit():
    from app.topic_selection import DEFAULT_TOPIC, resolve_topic
    assert DEFAULT_TOPIC == "cybersecurity"
    assert resolve_topic("cybersecurity") == "cybersecurity"
    assert resolve_topic("AI Governance") == "governance_risk"
    assert resolve_topic("ai_governance") == "governance_risk"
    assert resolve_topic("latest AI announcements") == "ai_announcements"
    assert resolve_topic("weather") is None
    assert resolve_topic("") is None


def test_topic_choice_applies_to_one_run_then_defaults(tmp_path, monkeypatch):
    import app.topic_selection as selection
    monkeypatch.setattr(selection, "SELECTED_PATH", tmp_path / "selected_topic.json")
    assert selection.consume_selected_topic() == "cybersecurity"
    selection.save_selected_topic("governance_risk")
    assert selection.consume_selected_topic() == "governance_risk"
    assert selection.consume_selected_topic() == "cybersecurity"


def test_choose_topic_refuses_unknown_topics(tmp_path, monkeypatch):
    import app.agent as agent
    import app.topic_selection as selection
    monkeypatch.setattr(selection, "SELECTED_PATH", tmp_path / "selected_topic.json")
    assert agent.choose_topic("sports")["status"] == "REFUSED"
    assert agent.choose_topic("ai announcements")["topic_id"] == "ai_announcements"


def test_selected_topic_guards_follow_the_run_choice():
    write_state("run", {"run_id": "x", "topics": [], "settings": {}, "selected_topic": "governance_risk"})
    write_state("insights", [{"candidate": {"topic_id": "governance_risk"}, "extraction": {}}])
    assert _has_state("insights:selected")
    assert not _has_state("insights:cybersecurity")
    write_state("run", {"run_id": "x", "topics": [], "settings": {}, "selected_topic": "cybersecurity"})
    assert not _has_state("insights:selected")


def test_discarded_drafts_are_hidden_by_default_but_audit_is_untouched(monkeypatch):
    import app.dashboard as dashboard
    monkeypatch.setattr(dashboard, "_require_user", lambda request: None)
    monkeypatch.setattr(dashboard, "_load_persisted", lambda: None)
    kept = _post("Kept post.")
    discarded = _post("Discarded post.")
    discarded.draft_id = "post-2"
    discarded.decision = "DISCARDED"
    monkeypatch.setattr(dashboard, "PENDING", {kept.draft_id: kept, discarded.draft_id: discarded})
    visible = dashboard.list_drafts(None, include_discarded=False)
    assert [d.draft_id for d in visible] == ["post-1"]
    everything = dashboard.list_drafts(None, include_discarded=True)
    assert {d.draft_id for d in everything} == {"post-1", "post-2"}


def test_decision_card_uses_post_text_only(tmp_path, monkeypatch):
    from app import infographic
    monkeypatch.setattr(infographic, "ROOT", tmp_path)
    content = (
        "Unpatched PHP servers may run attacker code before your application loads.\n\n"
        "CERT/CC reports two PHP flaws. Versions before 8.2.35 are affected.\n\n"
        "Infrastructure owners should inventory PHP versions this week.\n\n"
        "Which team in your organization can verify the fix today?"
    )
    draft = Draft(
        draft_id="card-1", topic_id="cybersecurity", topic_name="Cybersecurity",
        title="Title", abstract="Abstract", content=content, decision="APPROVED",
        sources=[{"source_id": "s", "title": "PHP advisory", "url": "https://example.test/adv",
                  "publisher": "CERT/CC", "excerpt": "x", "authority_score": 5}],
    )
    result = create_infographic(draft)
    assert result.infographic_status == "READY", result.infographic_error
    assert result.infographic_path.endswith("card-1.png")
    assert (tmp_path / "infographics" / "card-1.png").exists()


def test_every_configured_topic_has_a_source_and_urls_are_https():
    import yaml
    from app.config import ROOT
    topics, _ = load_topics()
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    for topic in topics:
        matching = [source for source in sources if source["topic"] == topic.id]
        assert matching, f"no sources configured for {topic.id}"
    assert all(source["url"].startswith("https://") for source in sources)
    assert all(source["authority_weight"] >= 4 for source in sources if source["topic"] == "ai_announcements")
