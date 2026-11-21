"""Load and save the job-seeker profile."""

from __future__ import annotations

import json

from core.paths import PROFILE_PATH


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        example = PROFILE_PATH.with_name("profile.example.json")
        PROFILE_PATH.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def save_profile(profile: dict) -> None:
    profile["projects"] = profile.get("projects") or []
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
