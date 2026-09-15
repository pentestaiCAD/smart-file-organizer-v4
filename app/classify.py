"""Offline, deterministic bucket router.

Decides which of the six folders a file belongs to using only extension +
filename cues + age — never its unread content, and never AI (the AI is for
naming and search, not for guessing a life-domain). Anything without a clear
signal returns a low confidence and the engine drops it into _UNSORTED, so the
app "sorts almost nothing" and never guesses.

Credential-like files are LEFT IN PLACE (action="leave") and flagged: they are
never moved on a hunch, never opened, never sent to AI. They still show up in
Find and on the _DESK, and are prime candidates to pin.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import app_config


@dataclass
class Decision:
    action: str          # "route" | "unsorted" | "leave"
    bucket: str | None    # target bucket when action == "route"
    subfolder: str | None
    confidence: float
    ftype: str | None
    sensitive: bool
    reason: str


def type_of(ext: str, rules: dict) -> str | None:
    ext = ext.lower().lstrip(".")
    for ftype, exts in rules.get("types", {}).items():
        if ext in exts:
            return ftype
    return None


def is_sensitive(name: str, ftype: str | None, rules: dict) -> bool:
    if ftype in rules.get("sensitive_types", []):
        return True
    low = name.lower()
    for pat in rules.get("sensitive_name_patterns", []):
        if re.search(pat, low):
            return True
    return False


def _first_keyword(name_low: str, keywords: list[str]) -> str | None:
    return next((kw for kw in keywords if kw in name_low), None)


def classify(path: Path, rules: dict, cfg: dict, now: float | None = None) -> Decision:
    now = now or time.time()
    name = path.name
    name_low = name.lower()
    ext = path.suffix.lower().lstrip(".")
    ftype = type_of(ext, rules)
    routable = app_config.routable_buckets(cfg)
    min_conf = cfg.get("routing", {}).get("min_confidence", 0.55)
    archive_days = cfg.get("routing", {}).get("archive_after_days", 180)
    hints = rules.get("subfolder_hints", {})

    sensitive = is_sensitive(name, ftype, rules)
    if sensitive:
        return Decision("leave", None, None, 1.0, ftype, True,
                        "looks like a credential/secret — left in place, never read or sent to AI")

    try:
        age_days = (now - path.stat().st_mtime) / 86400
    except OSError:
        age_days = 0.0

    # Propose a (bucket, subfolder, confidence) from the strongest signal.
    proposal: tuple[str, str | None, float, str] | None = None

    screenshot_re = re.compile(rules.get("screenshot_pattern", "screenshot"), re.IGNORECASE)
    work_kw = _first_keyword(name_low, rules.get("work_keywords", []))
    personal_kw = _first_keyword(name_low, rules.get("personal_keywords", []))

    if work_kw and "Work" in routable:
        proposal = ("Work", hints.get(work_kw), 0.8, f"filename cue '{work_kw}'")
    elif personal_kw and "Personal" in routable:
        proposal = ("Personal", hints.get(personal_kw), 0.75, f"filename cue '{personal_kw}'")
    elif ftype == "image" and screenshot_re.search(name) and "Personal" in routable:
        proposal = ("Personal", "Screenshots", 0.5, "screenshot")   # deliberately below the bar
    elif ftype in ("installer", "archive") and "Archive" in routable:
        sub = "Installers" if ftype == "installer" else "Archives"
        conf = 0.7 if age_days >= archive_days else 0.55
        proposal = ("Archive", sub, conf, f"{ftype}, {int(age_days)}d old")

    if proposal is None:
        return Decision("unsorted", None, None, 0.2, ftype, False,
                        "no clear Work/Personal/Archive signal — not guessed")

    bucket, subfolder, conf, why = proposal
    if conf >= min_conf and bucket in routable:
        return Decision("route", bucket, subfolder, conf, ftype, False, why)
    return Decision("unsorted", None, None, conf, ftype, False,
                    f"low confidence ({conf:.2f}) — {why}; left in _UNSORTED")
