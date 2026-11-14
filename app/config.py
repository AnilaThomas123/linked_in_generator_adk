import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .models import Topic

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def load_topics() -> tuple[list[Topic], dict]:
    data = yaml.safe_load((ROOT / "config/topics.yaml").read_text(encoding="utf-8"))
    topics = [Topic.model_validate(item) for item in data["topics"]]
    settings = data.get("settings", {})
    if not topics:
        raise ValueError("At least one topic is required")
    if settings.get("drafts_per_topic", 0) < 1:
        raise ValueError("drafts_per_topic must be positive")
    return topics, settings


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

