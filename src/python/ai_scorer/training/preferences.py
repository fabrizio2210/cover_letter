from __future__ import annotations

import json
import os
import random

_DEFAULT_PREF_FILE = os.path.join(os.path.dirname(__file__), "data", "training_preferences.seed.json")


def default_preferences_path() -> str:
    return _DEFAULT_PREF_FILE


def generate_random_preferences(count: int = 10, seed: int = 1337) -> list[dict]:
    rng = random.Random(seed)

    topics = [
        "remote collaboration",
        "backend systems",
        "api design",
        "data pipelines",
        "test automation",
        "platform reliability",
        "security practices",
        "developer tooling",
        "incident response",
        "product delivery",
        "mentorship",
        "architecture ownership",
        "performance tuning",
        "cloud infrastructure",
        "cross-team communication",
    ]
    emphasis = [
        "hands-on execution",
        "clear ownership",
        "pragmatic decisions",
        "measurable outcomes",
        "production impact",
        "engineering quality",
        "long-term maintainability",
    ]

    selected_topics = rng.sample(topics, k=min(count, len(topics)))
    preferences: list[dict] = []
    for idx, topic in enumerate(selected_topics, start=1):
        focus = rng.choice(emphasis)
        key = f"pref_{idx:02d}_{topic.lower().replace(' ', '_')}"
        guidance = (
            f"Strong preference for roles with {topic}, emphasizing {focus}. "
        )
        preferences.append({"key": key, "guidance": guidance})

    return preferences


def save_preferences(preferences: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(preferences, handle, indent=2, ensure_ascii=False)


def load_preferences(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise ValueError(f"Expected array in {path}")
    for item in raw:
        if "key" not in item or "guidance" not in item:
            raise ValueError(f"Each preference requires key/guidance fields: {item!r}")
    return raw


def ensure_seed_preferences(path: str, count: int = 10, seed: int = 1337) -> list[dict]:
    if os.path.exists(path):
        return load_preferences(path)
    preferences = generate_random_preferences(count=count, seed=seed)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    save_preferences(preferences, path)
    return preferences
