"""Saved-run replay for classroom demos. One snapshot per topic, from a successful live run."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .config import ROOT
from .models import Draft
from .topic_selection import DEFAULT_TOPIC

SNAPSHOT_ROOT = ROOT / "data" / "replay"


def snapshot_dir(topic_id: str) -> Path:
    return SNAPSHOT_ROOT / topic_id / "latest"


def snapshot_replay(topic_id: str = DEFAULT_TOPIC) -> dict:
    """Copy this topic's passing drafts into its replay snapshot. Run after a successful live run."""
    target_root = snapshot_dir(topic_id)
    if target_root.exists():
        shutil.rmtree(target_root)
    (target_root / "drafts").mkdir(parents=True, exist_ok=True)
    included: list[str] = []
    for path in sorted((ROOT / "drafts").glob("*.json")):
        try:
            draft = Draft.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if draft.topic_id == topic_id and draft.evaluator_status == "PASS":
            shutil.copyfile(path, target_root / "drafts" / path.name)
            included.append(draft.draft_id)
    meta = {"topic_id": topic_id, "created_at": datetime.now(UTC).isoformat(), "draft_ids": included}
    (target_root / "snapshot.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def replay_summaries(topic_id: str = DEFAULT_TOPIC) -> dict:
    """Summaries of one topic's snapshot. Makes no network or OpenAI calls."""
    root = snapshot_dir(topic_id)
    meta_path = root / "snapshot.json"
    if not meta_path.exists():
        return {
            "mode": "NO_SNAPSHOT",
            "topic_id": topic_id,
            "message": f"No saved run exists for {topic_id}. Run one successful live pipeline for this topic, "
                       f"then run `python -m app.cli snapshot-replay {topic_id}`.",
        }
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    summaries = []
    for path in sorted((root / "drafts").glob("*.json")):
        draft = Draft.model_validate_json(path.read_text(encoding="utf-8"))
        summaries.append({
            "draft_id": draft.draft_id,
            "title": draft.title,
            "hook": next((line.strip() for line in draft.content.splitlines() if line.strip()), ""),
            "evaluator_status": draft.evaluator_status,
        })
    return {
        "mode": "REPLAY",
        "topic_id": topic_id,
        "label": f"Replay of a saved live run for {topic_id} from {meta.get('created_at', 'unknown time')}. "
                 "No live research or OpenAI calls were made.",
        "drafts": summaries,
    }
