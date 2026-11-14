"""Optional Google OAuth guard for the local review dashboard."""

from __future__ import annotations

import os


def auth_required() -> bool:
    return os.getenv("REQUIRE_GOOGLE_AUTH", "false").lower() == "true"


def allowed_email(email: str) -> bool:
    configured = {item.strip().lower() for item in os.getenv("ALLOWED_GOOGLE_EMAILS", "").split(",") if item.strip()}
    return not configured or email.lower() in configured
