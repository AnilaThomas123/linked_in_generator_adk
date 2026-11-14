"""Filesystem-backed state shared by explicit ADK workflow stages."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import ROOT

STATE_ROOT = ROOT / "data" / "adk_workflow"


def reset_state() -> Path:
    if STATE_ROOT.exists():
        shutil.rmtree(STATE_ROOT)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    return STATE_ROOT


def state_path(name: str) -> Path:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    return STATE_ROOT / f"{name}.json"


def write_state(name: str, value: object) -> None:
    state_path(name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_state(name: str, default: object) -> object:
    path = state_path(name)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
