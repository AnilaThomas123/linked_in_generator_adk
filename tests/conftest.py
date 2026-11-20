"""Keep every test off the real run state, so a test run cannot overwrite a live run's files."""

import pytest

from app import topic_selection, workflow_state


@pytest.fixture(autouse=True)
def isolated_run_state(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_state, "STATE_ROOT", tmp_path / "adk_workflow")
    monkeypatch.setattr(topic_selection, "SELECTED_PATH", tmp_path / "selected_topic.json")
