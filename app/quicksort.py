"""QuickSort — the Smart File Organizer's scriptable engine. No window.

  quicksort.py tidy   "C:\\Users\\you\\Downloads" --dry-run
  quicksort.py tidy   "C:\\Users\\you\\Downloads" "C:\\Users\\you\\Desktop"
  quicksort.py undo
  quicksort.py find   "energy contract"
  quicksort.py pin    "C:\\path\\to\\file.pdf"
  quicksort.py unpin  "C:\\path\\to\\file.pdf"
  quicksort.py desk                         # rebuild + print the _DESK / _RECENT views
  quicksort.py index  --rebuild             # rebuild the search index
  quicksort.py watch  "C:\\Users\\you\\Downloads"              # notify-only
  quicksort.py watch  "C:\\Users\\you\\Downloads" --auto-move  # old-style auto-sort
  quicksort.py rules

Files move into the managed Home root's six folders (Work / Personal / Archive
/ _UNSORTED, plus the _DESK and _RECENT shortcut views). Everything the app
owns — rules, journal, search index, usage ledger, pins, AI-call log — lives in
%APPDATA%\\SmartFileOrganizer\\. The GUI and this CLI share all of it, so `undo`
works no matter which one made the change.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import app_config
import classify as classify_mod
import engine
import pins as pins_mod


def _cfg() -> dict:
    return app_config.load()


def cmd_tidy(args) -> int:
    cfg = _cfg()
    if args.home:
        cfg["home_root"] = args.home
    sources = args.sources or [str(app_config.default_downloads_folder())]
    plan = engine.plan(sources, cfg, include_subfolders=args.include_subfolders,
                       skip_dupes=args.skip_dupes, use_ai=not args.no_ai)
    root = app_config.home_root(cfg)
    if not plan.moves:
        print(f"Nothing to organize into {root}. (Sorted almost nothing on purpose.)")
        _print_skips(plan)
        return 0
    print(f"\n{len(plan.moves)} file(s) would move into {root}:\n")
    for mv in plan.moves:
        label = mv.bucket + ("\\" + mv.subfolder if mv.subfolder else "")
        rename = f"   (renamed: {mv.src.name} -> {mv.new_name})" if mv.is_rename else ""
        print(f"  {mv.src.name}  ->  {label}\\{rename}")
    _print_skips(plan)
    if args.dry_run:
        print("\n(dry run — nothing moved. Re-run without --dry-run to apply.)")
        return 0
    print()
    res = engine.execute(plan, cfg, log=print)
    print(f"\nDone. {res['moved']} file(s) organized. Undo any time: quicksort.py undo")
    return 0


def _print_skips(plan) -> None:
    if not plan.skipped:
        return
    shown = plan.skipped[:12]
    print(f"\nLeft untouched ({len(plan.skipped)}):")
    for path, why in shown:
        print(f"  - {Path(path).name}: {why}")
    if len(plan.skipped) > len(shown):
        print(f"  … and {len(plan.skipped) - len(shown)} more")


def cmd_undo(args) -> int:
    res = engine.undo(_cfg(), log=print)
    return 0 if not res.get("failed") else 1


def cmd_find(args) -> int:
    import index
    con = index.connect()
    if index.stats(con) == 0:
        print("Index is empty — run: quicksort.py index --rebuild")
    rows = index.search(con, args.query, limit=args.limit)
    con.close()
    if not rows:
        print(f"No matches for {args.query!r}.")
        return 1
    print(f"\n{len(rows)} match(es) for {args.query!r}:\n")
    for r in rows:
        loc = str(Path(r["path"]).parent)
        flag = "  [sensitive]" if r["sensitive"] else ""
        when = time.strftime("%Y-%m-%d", time.localtime(r["mtime"])) if r["mtime"] else "?"
        print(f"  {r['name']}{flag}")
        print(f"      {loc}   (modified {when})")
        if r["description"]:
            print(f"      {r['description']}")
    return 0


def cmd_pin(args) -> int:
    p = Path(args.path)
    if not p.exists():
        print(f"No such file: {p}")
        return 1
    print("Pinned — the organizer will never move this file." if pins_mod.add(p)
          else "Already pinned.")
    engine._rebuild_views(_cfg())
    return 0


def cmd_unpin(args) -> int:
    print("Unpinned." if pins_mod.remove(Path(args.path)) else "Wasn't pinned.")
    engine._rebuild_views(_cfg())
    return 0


def cmd_desk(args) -> int:
    cfg = _cfg()
    res = engine._rebuild_views  # noqa: F841 (kept for symmetry)
    import desk
    built = desk.rebuild(cfg)
    print(f"_DESK: {len(built['desk_items'])} item(s), _RECENT: {len(built['recent_items'])} item(s)")
    print(f"(shortcuts written under {app_config.home_root(cfg)})\n")
    print("On the desk:")
    for it in built["desk_items"][:20]:
        tag = " [pinned]" if it["pinned"] else ""
        print(f"  {it['name']}  <- {it['bucket']}{tag}")
    return 0


def cmd_index(args) -> int:
    cfg = _cfg()
    if args.rebuild:
        try:
            app_config.INDEX_PATH.unlink()
        except OSError:
            pass
    print("Indexing… (this reads file contents locally)")
    n = engine.reindex(cfg, sources=args.source, with_ai_description=args.describe, log=print)
    print(f"Indexed {n} file(s). Try: quicksort.py find \"<words>\"")
    return 0


def cmd_watch(args) -> int:
    cfg = _cfg()
    rules = engine._load_rules()
    source = Path(args.folder)
    known = {str(p) for p in source.glob("*")}
    mode = "auto-move" if args.auto_move else "notify-only — nothing moves automatically"
    print(f"Watching {source} [{mode}]. Ctrl+C to stop.")
    pending = 0
    try:
        while True:
            for path in sorted(source.glob("*")):
                if not path.is_file() or str(path) in known:
                    continue
                known.add(str(path))
                if path.name.lower() in engine.NEVER_TOUCH:
                    continue
                if not _wait_stable(path):
                    continue
                decision = classify_mod.classify(path, rules, cfg)
                dest = decision.bucket if decision.action == "route" else app_config.UNSORTED
                if args.auto_move:
                    plan = engine.plan([source], cfg, use_ai=not args.no_ai)
                    one = [m for m in plan.moves if m.src.resolve() == path.resolve()]
                    if one:
                        engine.execute(plan, cfg, selected=one, kind="watch", log=print)
                else:
                    pending += 1
                    print(f"  NEW: {path.name}  (would go to {dest}\\)  [{pending} waiting — not moved]")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nWatch stopped.")
    return 0


def _wait_stable(path: Path, interval: float = 1.5, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last:
            return True
        last = size
        time.sleep(interval)
    return path.exists()


def cmd_rules(args) -> int:
    rules = engine._load_rules()
    print(f"Rules from {app_config.RULES_PATH}:\n")
    print("Type signals (extension -> type):")
    for ftype, exts in rules.get("types", {}).items():
        print(f"  {ftype:<12} .{'  .'.join(exts)}")
    print("\nSensitive (left in place, never read/sent):", ", ".join(rules.get("sensitive_types", [])))
    print("Work cues:", ", ".join(rules.get("work_keywords", [])[:12]), "…")
    print("Personal cues:", ", ".join(rules.get("personal_keywords", [])[:12]), "…")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="QuickSort 4.0 — findable-first file organizer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tidy", help="sort a folder into the six home-root folders")
    p.add_argument("sources", nargs="*", help="folders to sweep (default: your Downloads)")
    p.add_argument("--home", help="home root that holds the six folders")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include-subfolders", action="store_true")
    p.add_argument("--skip-dupes", action="store_true")
    p.add_argument("--no-ai", action="store_true", help="skip the AI namer even if enabled")
    p.set_defaults(func=cmd_tidy)

    p = sub.add_parser("undo", help="restore the most recent batch exactly")
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("find", help="search filename + content + description")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("pin", help="pin a file so the organizer never touches it")
    p.add_argument("path")
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser("unpin", help="remove a pin")
    p.add_argument("path")
    p.set_defaults(func=cmd_unpin)

    p = sub.add_parser("desk", help="rebuild and print the _DESK / _RECENT views")
    p.set_defaults(func=cmd_desk)

    p = sub.add_parser("index", help="build the search index")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--source", action="append", help="extra folder to index (repeatable)")
    p.add_argument("--describe", action="store_true", help="also generate AI descriptions (costs calls)")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("watch", help="watch a folder; notify-only unless --auto-move")
    p.add_argument("folder")
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--auto-move", action="store_true")
    p.add_argument("--no-ai", action="store_true")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("rules", help="show the active rule set")
    p.set_defaults(func=cmd_rules)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
