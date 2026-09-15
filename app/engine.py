"""The organizing engine: plan -> preview -> execute, with a journal that
makes every batch undoable to the byte.

Design rules enforced here (from the v4 mandate):
  * Six top-level folders, at most two levels deep. _UNSORTED is flat.
  * Sort almost nothing: only clear-signal files are routed; the rest pool in
    _UNSORTED with their original names (never guessed).
  * Pinned, in-use, sensitive, hidden/system, denylisted and already-placed
    files are never moved.
  * Never overwrite (name (1).ext). Never touch desktop.ini / thumbs.db / our
    own data. Downloads is a source you sweep on demand, never auto-moved.
  * Every move/rename/created-dir is journalled; Undo reverses the last batch
    exactly and rebuilds the _DESK / _RECENT views.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import app_config
import classify as classify_mod
import namer
import pins as pins_mod

NEVER_TOUCH = {"desktop.ini", "thumbs.db", "quicksort-history.json"}
_ATTR_HIDDEN = 0x2
_ATTR_SYSTEM = 0x4


@dataclass
class PlannedMove:
    src: Path
    bucket: str
    subfolder: str | None
    new_name: str            # filename incl. extension
    action: str              # "route" | "unsorted"
    route_conf: float
    name_conf: float
    reason: str
    ftype: str | None
    is_rename: bool

    def dest(self, root: Path) -> Path:
        parts = [root, self.bucket]
        if self.subfolder:
            parts.append(self.subfolder)
        parts.append(self.new_name)
        return Path(*parts)


@dataclass
class Plan:
    root: Path
    moves: list[PlannedMove] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------

def _deny_roots() -> list[Path]:
    roots = [
        Path(os.environ.get("SystemRoot", r"C:\Windows")),
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)"),
        app_config.CONFIG_DIR,
    ]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent)
    return roots


def is_denied(path: Path) -> bool:
    rp = str(Path(path).resolve()).lower()
    if "$recycle.bin" in rp or "\\windows\\" in rp:
        return True
    for r in _deny_roots():
        try:
            rr = str(r.resolve()).lower()
        except OSError:
            continue
        if rp == rr or rp.startswith(rr + os.sep):
            return True
    return False


def _is_hidden_system(path: Path) -> bool:
    try:
        attrs = path.stat().st_file_attributes           # Windows only
    except (OSError, AttributeError):
        return False
    return bool(attrs & (_ATTR_HIDDEN | _ATTR_SYSTEM))


def _is_view_folder(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return rel.parts and rel.parts[0] in app_config.VIEW_FOLDERS


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def unique_destination(dst: Path) -> Path:
    if not dst.exists():
        return dst
    for n in range(1, 10000):
        cand = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"no free name for {dst.name}")


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

def _load_rules() -> dict:
    path = app_config.ensure_rules_seeded()
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _ai_cfg(cfg: dict, use_ai: bool) -> dict | None:
    ai = cfg.get("ai", {})
    if use_ai and ai.get("enabled") and ai.get("provider", "none") != "none":
        return ai
    return None


def plan(sources, cfg: dict, *, include_subfolders: bool = False,
         skip_dupes: bool = False, use_ai: bool = True) -> Plan:
    if isinstance(sources, (str, Path)):
        sources = [sources]
    rules = _load_rules()
    root = app_config.home_root(cfg)
    ai_cfg = _ai_cfg(cfg, use_ai)
    pin_set = pins_mod.as_set()
    now = time.time()
    result = Plan(root=root)
    seen_hashes: dict[str, Path] = {}

    for source in sources:
        source = Path(source)
        if not source.is_dir():
            result.skipped.append((source, "not a folder"))
            continue
        if is_denied(source):
            result.skipped.append((source, "protected location — refused"))
            continue
        walker = source.rglob("*") if include_subfolders else source.glob("*")
        for path in sorted(walker):
            if not path.is_file():
                continue
            reason = _screen(path, root, pin_set)
            if reason:
                result.skipped.append((path, reason))
                continue
            decision = classify_mod.classify(path, rules, cfg, now)
            if decision.action == "leave":
                result.skipped.append((path, decision.reason))
                continue

            bucket = decision.bucket if decision.action == "route" else app_config.UNSORTED
            subfolder = decision.subfolder if decision.action == "route" else None

            new_name, name_conf = _decide_name(path, decision, rules, cfg, ai_cfg)

            if skip_dupes and _safe_size(path) > 0 and not decision.sensitive:
                digest = sha256_of(path)
                if digest in seen_hashes:
                    bucket, subfolder = app_config.UNSORTED, None
                    new_name = path.name
                    decision.reason = f"duplicate of {seen_hashes[digest].name}"
                    decision.action = "unsorted"
                else:
                    seen_hashes[digest] = path

            mv = PlannedMove(
                src=path, bucket=bucket, subfolder=subfolder, new_name=new_name,
                action="route" if bucket != app_config.UNSORTED else "unsorted",
                route_conf=decision.confidence, name_conf=name_conf,
                reason=decision.reason, ftype=decision.ftype,
                is_rename=(new_name != path.name),
            )
            # already sitting exactly where it would go?
            if mv.dest(root).resolve() == path.resolve():
                result.skipped.append((path, "already in place"))
                continue
            result.moves.append(mv)
    return result


def _screen(path: Path, root: Path, pin_set: set[str]) -> str | None:
    """Return a skip-reason, or None if the file is eligible to be planned."""
    if not path.is_file():
        return "not a regular file"
    name_low = path.name.lower()
    if name_low in NEVER_TOUCH or name_low.startswith("~$"):
        return "protected system file"
    if _is_view_folder(path, root):
        return "inside a _DESK/_RECENT view"
    if is_denied(path):
        return "protected location"
    if _is_hidden_system(path):
        return "hidden/system file"
    if pins_mod.is_pinned(path, pin_set):
        return "pinned — untouchable"
    import usage
    if usage.is_in_use(path):
        return "open in another app right now"
    return None


def _decide_name(path: Path, decision, rules: dict, cfg: dict,
                 ai_cfg: dict | None) -> tuple[str, float]:
    naming = cfg.get("naming", {})
    if not naming.get("enabled", True) or decision.sensitive:
        return path.name, 0.0
    # AI naming is OFF the scan hot-path unless explicitly enabled: it makes one
    # network call per file, which is what makes big scans crawl. Offline naming
    # (dates, versions, subject, kind) is instant and needs no snippet.
    use_ai_names = bool(ai_cfg) and naming.get("ai_names", False)
    snippet = None
    if use_ai_names:
        import index
        content = index.extract_text(path, decision.ftype, decision.sensitive)
        snippet = index.snippet_of(content)
    ai_cfg = ai_cfg if use_ai_names else None
    base, conf, _src = namer.propose_name(path, decision.ftype, rules, cfg, snippet, ai_cfg)
    min_conf = naming.get("min_confidence", 0.75)
    if not naming.get("auto_apply", True) or conf < min_conf:
        return path.name, conf              # keep original name (D5)
    return base + path.suffix, conf


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# --------------------------------------------------------------------------
# journal + execute + undo
# --------------------------------------------------------------------------

def _load_journal() -> list[dict]:
    if app_config.JOURNAL_PATH.exists():
        try:
            return json.loads(app_config.JOURNAL_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    return []


def _save_journal(batches: list[dict]) -> None:
    app_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_config.JOURNAL_PATH.write_text(json.dumps(batches, indent=2), encoding="utf-8")


def execute(plan_obj: Plan, cfg: dict, selected: list[PlannedMove] | None = None,
            kind: str = "tidy", rebuild: bool = True, log=lambda s: None) -> dict:
    moves = selected if selected is not None else plan_obj.moves
    root = plan_obj.root
    ops: list[dict] = []
    done = 0
    for mv in moves:
        dst = mv.dest(root)
        created = _mkdir_tracking(dst.parent, ops)
        final = unique_destination(dst)
        try:
            import shutil
            shutil.move(str(mv.src), str(final))
        except OSError as exc:
            log(f"  FAILED {mv.src.name}: {exc}")
            for d in reversed(created):
                _rmdir_if_empty(d)
            continue
        ops.append({"t": "move", "from": str(mv.src), "to": str(final)})
        done += 1
        label = mv.bucket + ("\\" + mv.subfolder if mv.subfolder else "")
        log(f"  {mv.src.name}  ->  {label}\\{final.name}")
        _index_one(final, mv, cfg)
    if ops:
        batches = _load_journal()
        batches.append({"when": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "root": str(root), "kind": kind, "ops": ops})
        _save_journal(batches)
    if rebuild:
        _rebuild_views(cfg)
    return {"moved": done, "ops": len(ops)}


def _mkdir_tracking(folder: Path, ops: list[dict]) -> list[Path]:
    created: list[Path] = []
    cur = folder
    stack: list[Path] = []
    while not cur.exists() and cur != cur.parent:
        stack.append(cur)
        cur = cur.parent
    for d in reversed(stack):
        try:
            d.mkdir(exist_ok=True)
            ops.append({"t": "mkdir", "path": str(d)})
            created.append(d)
        except OSError:
            break
    return created


def _rmdir_if_empty(folder: Path) -> None:
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass


def undo(cfg: dict, log=lambda s: None) -> dict:
    batches = _load_journal()
    if not batches:
        log("Nothing to undo.")
        return {"restored": 0, "failed": 0, "empty": True}
    batch = batches.pop()
    restored, failed = 0, 0
    import shutil
    for op in reversed(batch["ops"]):
        if op["t"] == "move":
            src, dst = Path(op["to"]), Path(op["from"])
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(unique_destination(dst)))
                _reindex_removed(src)
                restored += 1
                log(f"  restored: {dst.name}")
            except OSError as exc:
                failed += 1
                log(f"  FAILED to restore {op['to']}: {exc}")
        elif op["t"] == "mkdir":
            _rmdir_if_empty(Path(op["path"]))
    _save_journal(batches)
    _rebuild_views(cfg)
    log(f"Undid batch from {batch['when']}: {restored} restored, {failed} failed.")
    return {"restored": restored, "failed": failed, "empty": False, "when": batch["when"]}


def last_batch_summary() -> str | None:
    batches = _load_journal()
    if not batches:
        return None
    b = batches[-1]
    n = sum(1 for op in b["ops"] if op["t"] == "move")
    return f"{n} file(s) — {b['when']}"


# --------------------------------------------------------------------------
# indexing + views (thin wrappers, imported lazily to avoid cycles)
# --------------------------------------------------------------------------

def _index_one(path: Path, mv: PlannedMove, cfg: dict) -> None:
    try:
        import index
        con = index.connect()
        sensitive = classify_mod.is_sensitive(path.name, mv.ftype, _load_rules())
        content = index.extract_text(path, mv.ftype, sensitive)
        st = path.stat()
        index.upsert(con, path=path, name=path.name, content=content, description="",
                     bucket=mv.bucket, mtime=st.st_mtime, size=st.st_size, sensitive=sensitive)
        con.commit()
        con.close()
    except Exception:
        pass


def _bucket_of(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
        return rel.parts[0] if rel.parts else ""
    except (ValueError, OSError):
        return "Downloads"


def reindex(cfg: dict, sources=None, with_ai_description: bool = False,
            log=lambda s: None) -> int:
    """(Re)build the search index over the home root, the Downloads landing
    strip, and any extra sources. AI descriptions are opt-in (they cost calls);
    filename + locally-extracted content are always indexed."""
    import index
    rules = _load_rules()
    root = app_config.home_root(cfg)
    ai_cfg = _ai_cfg(cfg, with_ai_description)
    con = index.connect()
    roots = [root / b for b in [*app_config.routable_buckets(cfg), app_config.UNSORTED]]
    if sources:
        roots += [Path(s) for s in sources]
    dl = Path(cfg.get("downloads", {}).get("folder") or app_config.default_downloads_folder())
    if dl.is_dir():
        roots.append(dl)
    seen: set[str] = set()
    count = 0

    def index_file(p: Path) -> bool:
        nonlocal count
        if not p.is_file():
            return False
        key = str(p.resolve()).lower()
        if key in seen:
            return False
        seen.add(key)
        low = p.name.lower()
        if low in NEVER_TOUCH or low.startswith("~$") or _is_view_folder(p, root):
            return False
        ftype = classify_mod.type_of(p.suffix.lower().lstrip("."), rules)
        sensitive = classify_mod.is_sensitive(p.name, ftype, rules)
        content = index.extract_text(p, ftype, sensitive)
        desc = ""
        if ai_cfg and content and not sensitive:
            import ai_provider
            desc = ai_provider.describe(ai_provider.resolve_config(ai_cfg),
                                        p.name, index.snippet_of(content)) or ""
        try:
            st = p.stat()
        except OSError:
            return False
        index.upsert(con, path=p, name=p.name, content=content, description=desc,
                     bucket=_bucket_of(p, root), mtime=st.st_mtime, size=st.st_size,
                     sensitive=sensitive)
        count += 1
        if count % 200 == 0:
            con.commit()
            log(f"  indexed {count}…")
        return True

    for r in roots:
        if not r.is_dir():
            continue
        for p in r.rglob("*"):
            index_file(p)
    # pinned files can live anywhere (they never move) but must stay findable
    for p in pins_mod.load():
        index_file(Path(p))
    con.commit()
    con.close()
    return count


def _reindex_removed(path: Path) -> None:
    try:
        import index
        con = index.connect()
        index.remove(con, path)
        con.commit()
        con.close()
    except Exception:
        pass


def _rebuild_views(cfg: dict) -> None:
    try:
        import desk
        desk.rebuild(cfg)
    except Exception:
        pass
