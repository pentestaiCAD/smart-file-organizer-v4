"""File-usage signals that decide what belongs on the _DESK.

Windows can't reliably tell us how many times a file was opened (last-access
time is disabled by default and there is no per-file open counter), so the app
keeps its own ledger of opens it can actually observe — files opened through
_DESK, Find, or the Tidy preview — and augments it, best-effort, from the
Windows "Recent" list (decision D3). "Opened 2+ times" is therefore exact for
opens the app saw and approximate otherwise, which we state plainly.

Also here: `is_in_use`, the conservative "don't move a file that's open right
now" probe (decision D4).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import app_config


def _load() -> dict:
    if app_config.USAGE_PATH.exists():
        try:
            return json.loads(app_config.USAGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"opens": {}, "recent_imported": 0}


def _save(data: dict) -> None:
    app_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_config.USAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _key(path) -> str:
    return str(Path(path).resolve()).lower()


def record_open(path) -> None:
    """Note that the user just opened this file (call from _DESK / Find / open)."""
    data = _load()
    opens = data.setdefault("opens", {})
    entry = opens.setdefault(_key(path), {"count": 0, "stamps": []})
    entry["count"] += 1
    entry["stamps"].append(int(time.time()))
    entry["stamps"] = entry["stamps"][-50:]         # keep the ledger bounded
    _save(data)


def open_count(path, within_days: int = 30, _data: dict | None = None) -> int:
    data = _data if _data is not None else _load()
    entry = data.get("opens", {}).get(_key(path))
    if not entry:
        return 0
    cutoff = time.time() - within_days * 86400
    return sum(1 for s in entry.get("stamps", []) if s >= cutoff)


def last_open(path, _data: dict | None = None) -> int | None:
    data = _data if _data is not None else _load()
    entry = data.get("opens", {}).get(_key(path))
    if not entry or not entry.get("stamps"):
        return None
    return max(entry["stamps"])


def snapshot() -> dict:
    """Load the ledger once for a whole planning pass (avoids re-reading)."""
    return _load()


def import_windows_recent(max_age_days: int = 30) -> int:
    """Fold recently-opened files from %APPDATA%\\Microsoft\\Windows\\Recent
    into the ledger as one open each, at the shortcut's timestamp. Best-effort
    and safe to call repeatedly."""
    recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    if not recent.is_dir():
        return 0
    import shortcuts  # local import: optional Windows-only helper
    data = _load()
    opens = data.setdefault("opens", {})
    cutoff = time.time() - max_age_days * 86400
    added = 0
    for lnk in recent.glob("*.lnk"):
        try:
            mtime = lnk.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        target = shortcuts.resolve_target(lnk)
        if not target or not Path(target).is_file():
            continue
        entry = opens.setdefault(_key(target), {"count": 0, "stamps": []})
        stamp = int(mtime)
        if stamp not in entry["stamps"]:
            entry["stamps"].append(stamp)
            entry["stamps"] = sorted(entry["stamps"])[-50:]
            entry["count"] = max(entry["count"], len(entry["stamps"]))
            added += 1
    data["recent_imported"] = int(time.time())
    _save(data)
    return added


def is_in_use(path) -> bool:
    """Conservative 'open in another app right now' probe. False positives are
    fine (we simply skip the file); false negatives are what we avoid."""
    p = Path(path)
    # Microsoft Office keeps a ~$name lock sidecar next to open documents.
    if p.with_name("~$" + p.name).exists():
        return True
    try:
        with open(p, "rb+"):
            return False
    except PermissionError:
        return True      # locked, or read-only — either way, leave it alone
    except OSError:
        return True
