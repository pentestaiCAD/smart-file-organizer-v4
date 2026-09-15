"""_DESK and _RECENT — the views that fix "filed but unfindable".

Neither holds real files: both are folders of .lnk shortcuts pointing at where
files actually live (in a bucket, in _UNSORTED, or still in Downloads). We wipe
and rebuild them every run, so _RECENT "auto-expires" for free and _DESK never
drifts. Real files are never moved by anything in this module.

_DESK  = pinned  ∪  modified within `hot_days`  ∪  opened >= `open_count`
         times within `open_window_days`.
_RECENT = modified within `recent_days`.
"""
from __future__ import annotations

import time
from pathlib import Path

import app_config
import pins as pins_mod
import shortcuts
import usage

_DESK_CAP = 50
_RECENT_CAP = 80
_SKIP_NAMES = {"desktop.ini", "thumbs.db"}
_ATTR_HIDDEN_SYSTEM = 0x2 | 0x4


def _skip_from_view(path: Path) -> bool:
    name = path.name.lower()
    if name in _SKIP_NAMES or name.startswith("~$"):
        return True
    try:
        if path.stat().st_file_attributes & _ATTR_HIDDEN_SYSTEM:   # Windows only
            return True
    except (OSError, AttributeError):
        pass
    return False


def _candidates(cfg: dict, root: Path) -> list[tuple[Path, str]]:
    """Collect (resolved_path, bucket_label) for every real file the desk may
    surface, de-duplicated. First label seen for a path wins."""
    seen: set[str] = set()
    out: list[tuple[Path, str]] = []

    def emit(p: Path, label: str) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        key = str(rp).lower()
        if key in seen or not rp.is_file() or _skip_from_view(rp):
            return
        seen.add(key)
        out.append((rp, label))

    for bucket in [*app_config.routable_buckets(cfg), app_config.UNSORTED]:
        d = root / bucket
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                emit(p, bucket)
    if cfg.get("desk", {}).get("include_downloads", True):
        dl = Path(cfg.get("downloads", {}).get("folder") or app_config.default_downloads_folder())
        if dl.is_dir():
            for p in dl.glob("*"):
                if p.is_file():
                    emit(p, "Downloads")
    for pinned in pins_mod.load():
        emit(Path(pinned), "pinned")
    return out


def compute(cfg: dict) -> dict:
    """Return {'desk': [item...], 'recent': [item...]} without touching disk.
    Each item: {path, name, bucket, mtime, last_open, opens, pinned}."""
    root = app_config.home_root(cfg)
    d = cfg.get("desk", {})
    hot_days = d.get("hot_days", 3)
    recent_days = d.get("recent_days", 7)
    need_opens = d.get("open_count", 2)
    window = d.get("open_window_days", 30)
    now = time.time()
    pin_set = pins_mod.as_set()
    ledger = usage.snapshot()

    desk_items, recent_items = [], []
    for path, bucket in _candidates(cfg, root):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        age_days = (now - mtime) / 86400
        opens = usage.open_count(path, window, ledger)
        last = usage.last_open(path, ledger)
        pinned = str(path).lower() in pin_set
        item = {"path": str(path), "name": path.name, "bucket": bucket,
                "mtime": mtime, "last_open": last, "opens": opens, "pinned": pinned}
        if pinned or age_days <= hot_days or opens >= need_opens:
            desk_items.append(item)
        if age_days <= recent_days:
            recent_items.append(item)

    desk_items.sort(key=lambda it: (not it["pinned"], -(it["last_open"] or it["mtime"])))
    recent_items.sort(key=lambda it: -it["mtime"])
    return {"desk": desk_items[:_DESK_CAP], "recent": recent_items[:_RECENT_CAP]}


def _materialize(folder: Path, items: list[dict]) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    shortcuts.clear_shortcuts(folder)
    pairs: list[tuple[Path, Path]] = []
    used: set[str] = set()
    for it in items:
        target = Path(it["path"])
        base = target.name
        link_name = base
        n = 1
        while link_name.lower() in used:
            link_name = f"{target.stem} ({n}){target.suffix}"
            n += 1
        used.add(link_name.lower())
        pairs.append((folder / (link_name + ".lnk"), target))
    return shortcuts.write_shortcuts(pairs)


def rebuild(cfg: dict) -> dict:
    """Recompute both views and rewrite their shortcut folders."""
    root = app_config.home_root(cfg)
    views = compute(cfg)
    made_desk = _materialize(root / app_config.DESK, views["desk"])
    made_recent = _materialize(root / app_config.RECENT, views["recent"])
    return {"desk": made_desk, "recent": made_recent,
            "desk_items": views["desk"], "recent_items": views["recent"]}
