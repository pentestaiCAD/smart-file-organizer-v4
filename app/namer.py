"""Purpose-based renaming.

Turns `Resume_Micheal_Davey_AI_Engineer_2026-09 (3).pdf` into
`Micheal-Davey-AI-Engineer_2026-09_resume_v3.pdf`. Fully offline it can
usually manage subject + date + kind + version from the filename and mtime;
with AI enabled it can do better from a snippet of locally-extracted text.

Nothing here is ever destructive on its own: the engine previews every
old -> new name, only auto-applies above a confidence bar (decision D5), and
every rename is journalled so Undo restores the original name exactly.
Credential-like files are never passed in here.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

_DATE_RE = re.compile(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])(?:[-_.]?(0[1-9]|[12]\d|3[01]))?")
_VERSION_PAREN = re.compile(r"\s*\((\d{1,3})\)\s*$")
_VERSION_V = re.compile(r"[ _-]v(\d{1,3})$", re.IGNORECASE)
_ID_TOKEN = re.compile(r"^(?=.*\d)[0-9a-f]{12,}$", re.IGNORECASE)   # long hex-ish download ids
_NOISE = {"final", "copy", "draft", "new", "latest", "download", "downloadid",
          "export", "untitled", "document", "file", "the", "and"}
_SEP = re.compile(r"[ _\.\-]+")


def sanitize_base(text: str) -> str:
    """Filesystem-safe base name. Keeps '-' and '_' (so dates like 2026-03-14
    survive) and turns spaces/dots into underscores."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", "_", text.strip())
    text = text.replace(".", "_")
    text = re.sub(r"_{2,}", "_", text)
    text = re.sub(r"-{2,}", "-", text)
    return text[:80].strip("_-")


def _extract_date(stem: str, mtime: float) -> tuple[str, bool]:
    m = _DATE_RE.search(stem)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return (f"{y}-{mo}-{d}" if d else f"{y}-{mo}"), True
    return time.strftime("%Y-%m-%d", time.localtime(mtime)), False


def _detect_version(stem: str) -> tuple[str, int | None]:
    m = _VERSION_PAREN.search(stem)
    if m:
        return stem[:m.start()].rstrip(), int(m.group(1))
    m = _VERSION_V.search(stem)
    if m:
        return stem[:m.start()].rstrip(), int(m.group(1))
    return stem, None


def _clean_subject(stem: str) -> str:
    stem = _DATE_RE.sub(" ", stem)
    tokens = [t for t in _SEP.split(stem) if t]
    kept = [t for t in tokens if t.lower() not in _NOISE and not _ID_TOKEN.match(t)]
    return "-".join(kept)


def _kind_from(name_low: str, rules: dict, ftype: str | None) -> str | None:
    for kw in rules.get("work_keywords", []) + rules.get("personal_keywords", []):
        if kw in name_low and " " not in kw:
            return kw
    return None


def propose_name(path: Path, ftype: str | None, rules: dict,
                 cfg: dict, snippet: str | None = None,
                 ai_cfg: dict | None = None) -> tuple[str, float, str]:
    """Return (new_base_name_without_extension, confidence, source)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = time.time()
    stem = path.stem
    core, version = _detect_version(stem)
    date, explicit_date = _extract_date(core, mtime)
    subject = _clean_subject(core)
    kind = _kind_from(stem.lower(), rules, ftype)
    subject_has_year = bool(re.search(r"20\d{2}", subject))

    parts: list[str] = []
    if subject and subject.lower() != (kind or "").lower():
        parts.append(subject)
    if explicit_date or not subject_has_year:      # avoid a redundant second year
        parts.append(date)
    if kind and kind not in subject.lower():
        parts.append(kind)
    if version is not None:
        parts.append(f"v{version}")
    base = sanitize_base("_".join(parts))
    # never let the name collapse to just a date/number — fall back to the stem
    if not base or re.fullmatch(r"[\d_\-]+", base):
        base = sanitize_base(stem)

    conf = 0.4
    if subject and len(subject) >= 3 and subject.lower() != (kind or ""):
        conf += 0.25
    if explicit_date:
        conf += 0.15
    if kind:
        conf += 0.15
    if sanitize_base(base).lower() == sanitize_base(stem).lower():
        conf = min(conf, 0.35)          # no real improvement over the original
    conf = min(conf, 0.95)
    source = "offline"

    # Optional AI enhancer — only with a screened, locally-extracted snippet.
    if ai_cfg and cfg.get("naming", {}).get("enabled", True):
        import ai_provider
        suggestion = ai_provider.suggest_name(ai_provider.resolve_config(ai_cfg), path.name, snippet)
        if suggestion:
            ai_base = sanitize_base(suggestion)
            if ai_base and len(ai_base) >= 3:
                if version is not None and f"v{version}" not in ai_base.lower():
                    ai_base = f"{ai_base}_v{version}"
                return ai_base, max(conf, 0.85), "ai"

    return base, conf, source
