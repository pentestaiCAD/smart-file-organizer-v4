"""Pin registry — the list of files the organizer is forbidden to touch.

A pinned file is never moved, renamed, expired, or cleaned up (Rule 7). Pins
are stored by resolved path; since pinned files never move, the path stays
valid. The engine consults `is_pinned` before planning any action, and pinned
files are always shown on the _DESK.
"""
from __future__ import annotations

import json
from pathlib import Path

import app_config


def _key(path) -> str:
    return str(Path(path).resolve()).lower()


def load() -> list[str]:
    if app_config.PINS_PATH.exists():
        try:
            data = json.loads(app_config.PINS_PATH.read_text(encoding="utf-8"))
            return list(data.get("pins", []))
        except Exception:
            pass
    return []


def _save(pins: list[str]) -> None:
    app_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_config.PINS_PATH.write_text(json.dumps({"pins": pins}, indent=2), encoding="utf-8")


def as_set() -> set[str]:
    """Lowercased set for case-insensitive membership tests."""
    return {p.lower() for p in load()}


def is_pinned(path, _cache: set[str] | None = None) -> bool:
    cache = _cache if _cache is not None else as_set()
    return _key(path) in cache


def add(path) -> bool:
    """Store the real (original-case) resolved path so the file stays
    displayable, but de-duplicate case-insensitively."""
    real = str(Path(path).resolve())
    pins = load()
    if real.lower() in {p.lower() for p in pins}:
        return False
    pins.append(real)
    _save(pins)
    return True


def remove(path) -> bool:
    pins = load()
    k = _key(path)
    kept = [p for p in pins if p.lower() != k]
    if len(kept) == len(pins):
        return False
    _save(kept)
    return True
