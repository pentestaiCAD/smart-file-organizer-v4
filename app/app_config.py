"""Shared configuration + the fixed set of per-user data paths for Smart File
Organizer 4.0 ("Findable First").

Everything the app owns lives under %APPDATA%\\SmartFileOrganizer\\ so it
survives reinstalls and is separate from the managed Home root (which holds
ONLY the six visible folders). Keeping operational files here — not in the
root — is what lets the root stay exactly six folders deep (decision D7).

Data files
----------
  config.json    settings (home root, buckets, AI, naming, desk thresholds)
  rules.json     editable extension -> type-signal map (seeded on first run)
  journal.json   undo journal (every batch of moves/renames/created dirs)
  index.db       SQLite FTS5 search index
  usage.json     per-file open ledger ("opened 2+ times") + Recent imports
  pins.json      pinned (untouchable) files
  ai-calls.log   append-only log of every outbound AI request (constraint)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "SmartFileOrganizer"
CONFIG_PATH = CONFIG_DIR / "config.json"
RULES_PATH = CONFIG_DIR / "rules.json"
JOURNAL_PATH = CONFIG_DIR / "journal.json"
INDEX_PATH = CONFIG_DIR / "index.db"
USAGE_PATH = CONFIG_DIR / "usage.json"
PINS_PATH = CONFIG_DIR / "pins.json"
AI_LOG_PATH = CONFIG_DIR / "ai-calls.log"

APP_NAME = "Smart File Organizer"
APP_VERSION = "4.0"

# The six top-level folders. Two are derived shortcut VIEWS, one is the
# never-guess bucket, and up to three are routable real buckets.
DESK = "_DESK"
RECENT = "_RECENT"
UNSORTED = "_UNSORTED"
VIEW_FOLDERS = (DESK, RECENT)
DEFAULT_ROUTABLE = ["Work", "Personal", "Archive"]
MAX_TOP_LEVEL = 6

DEFAULTS = {
    "setup_complete": False,
    "home_root": "",                      # resolved on first run (default_home_root)
    "buckets": list(DEFAULT_ROUTABLE),    # routable buckets besides _UNSORTED (<=3)
    "ai": {
        "provider": "none",               # none | openrouter | openai | gemini | custom
        "api_key": "",
        "model": "",
        "base_url": "",
        "enabled": False,
    },
    "downloads": {
        "folder": "",                     # landing strip: watched, never auto-moved
        "watch_enabled": False,
    },
    "naming": {
        "enabled": True,                  # propose purpose-based names (offline)
        "auto_apply": True,               # apply high-confidence names without a prompt
        "min_confidence": 0.75,           # below this, keep the original name (D5)
        "ai_names": False,                # call the AI model to improve names DURING a scan.
                                          # Off by default: it makes one network call per file,
                                          # so a big folder (or an exhausted quota) makes scans
                                          # crawl. Offline naming stays instant.
    },
    "desk": {
        "hot_days": 3,                    # modified within N days -> _DESK
        "recent_days": 7,                 # modified within N days -> _RECENT (auto-expires)
        "open_count": 2,                  # opened >= N times ...
        "open_window_days": 30,           # ... within N days -> _DESK
        "include_downloads": True,        # surface hot Downloads files on the desk (by ref)
    },
    "routing": {
        "min_confidence": 0.55,           # below this a file goes to _UNSORTED (never guess)
        "archive_after_days": 180,        # old installers/archives drift to Archive
    },
}


def bundled_dir() -> Path:
    """Read-only files shipped with the app (source tree in dev, the
    PyInstaller extraction dir when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _merge(defaults: dict, loaded: dict) -> dict:
    out = dict(defaults)
    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(defaults.get(k), dict):
            out[k] = _merge(defaults[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    if CONFIG_PATH.exists():
        try:
            return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")))
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULTS))


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def default_home_root() -> Path:
    return Path.home() / "Organized"


def default_downloads_folder() -> Path:
    onedrive_dl = Path.home() / "OneDrive" / "Downloads"
    return onedrive_dl if onedrive_dl.is_dir() else Path.home() / "Downloads"


def default_desktop_folder() -> Path:
    onedrive_dt = Path.home() / "OneDrive" / "Desktop"
    return onedrive_dt if onedrive_dt.is_dir() else Path.home() / "Desktop"


def routable_buckets(cfg: dict) -> list[str]:
    """The real buckets a file can be routed into (besides _UNSORTED), capped
    so the total top-level folder count never exceeds six."""
    buckets = [b for b in cfg.get("buckets", DEFAULT_ROUTABLE) if b and b not in VIEW_FOLDERS and b != UNSORTED]
    limit = MAX_TOP_LEVEL - len(VIEW_FOLDERS) - 1   # minus the two views and _UNSORTED
    return buckets[:limit]


def all_top_level(cfg: dict) -> list[str]:
    return [*VIEW_FOLDERS, *routable_buckets(cfg), UNSORTED]


def home_root(cfg: dict) -> Path:
    return Path(cfg.get("home_root") or default_home_root())


def ensure_rules_seeded() -> Path:
    """Make sure a live, editable rules file exists, seeded from the bundled
    default the first time the app runs."""
    if not RULES_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        bundled = bundled_dir() / "rules.json"
        RULES_PATH.write_text(bundled.read_text(encoding="utf-8-sig"), encoding="utf-8")
    return RULES_PATH
