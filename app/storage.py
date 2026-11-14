"""Small local SQLite store for durable draft and audit workflow state."""

from __future__ import annotations

import json
import sqlite3

from .config import ROOT
from .models import Draft

DB_PATH = ROOT / "data" / "editorial.db"


def _connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS drafts (draft_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    return connection


def save_draft(draft: Draft) -> None:
    payload = json.dumps(draft.model_dump(), ensure_ascii=False)
    with _connection() as connection:
        connection.execute(
            "INSERT INTO drafts(draft_id,payload,updated_at) VALUES(?,?,datetime('now')) "
            "ON CONFLICT(draft_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (draft.draft_id, payload),
        )


def load_drafts() -> list[Draft]:
    with _connection() as connection:
        rows = connection.execute("SELECT payload FROM drafts ORDER BY updated_at DESC").fetchall()
    result: list[Draft] = []
    for (payload,) in rows:
        try:
            result.append(Draft.model_validate_json(payload))
        except ValueError:
            continue
    return result


def migrate_json_drafts() -> int:
    """Import existing draft JSON artifacts without overwriting current state."""
    count = 0
    for path in (ROOT / "drafts").glob("*.json"):
        try:
            draft = Draft.model_validate_json(path.read_text(encoding="utf-8"))
            save_draft(draft)
            count += 1
        except (OSError, ValueError):
            continue
    return count


def clear_drafts() -> int:
    """Remove workflow draft records while preserving audit history."""
    with _connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
        connection.execute("DELETE FROM drafts")
    return int(count)
