"""Self-contained smoke test for the Smart File Organizer 4.0 engine.

Runs entirely inside a throwaway temp directory with its own %APPDATA%, so it
never reads or writes any real user data. Exercises the whole pipeline:
routing, confidence gating, purpose renaming, never-overwrite, sensitive
"leave in place", pinning, the six-folder / two-level invariants, search, and
exact undo.

    python tests/smoke_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# ---- isolate: point every app data path at a temp dir BEFORE importing app ----
_TMP = Path(tempfile.mkdtemp(prefix="sfo4_smoke_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))

import app_config          # noqa: E402
import engine              # noqa: E402
import desk                # noqa: E402
import index               # noqa: E402
import pins as pins_mod    # noqa: E402

SANDBOX = _TMP / "sandbox"
SOURCE = SANDBOX / "source"
HOME = SANDBOX / "home"
DOWNLOADS = SANDBOX / "downloads"

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILURES.append(label)


def touch(path: Path, content: str = "x", days_old: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if days_old:
        t = time.time() - days_old * 86400
        os.utime(path, (t, t))
    return path


def build_source() -> None:
    for d in (SOURCE, HOME, DOWNLOADS):
        d.mkdir(parents=True, exist_ok=True)
    # Work: resumes (versioned), invoice, agreement (findable by content)
    for n in (1, 2, 3):
        touch(SOURCE / f"Resume_Jane_Doe_2026-09 ({n}).pdf", "resume")
    touch(SOURCE / "invoice_staples_2026-03-14.txt", "Staples invoice paid 42 dollars")
    touch(SOURCE / "SCE_Agreement_v3_final.txt",
          "This is the energy contract with Southern California Edison for electricity service.")
    touch(SOURCE / "report_final.txt", "quarterly report ONE")
    touch(SOURCE / "report_copy.txt", "quarterly report TWO different bytes")
    # never-overwrite: two identically-named files in different subfolders
    # (neutral name with no routing cue -> both land in _UNSORTED)
    touch(SOURCE / "dupA" / "blobdata.xyz", "AAAA")
    touch(SOURCE / "dupB" / "blobdata.xyz", "BBBB")
    # Personal + Archive (old)
    touch(SOURCE / "family_vacation_2026.jpg", "jpegbytes")
    touch(SOURCE / "OldSetup-1.0.exe", "MZ", days_old=800)
    touch(SOURCE / "backup_2019.zip", "PK", days_old=800)
    # _UNSORTED: no cue / screenshot below the bar / unknown ext
    touch(SOURCE / "screenshot_2026-09-10.png", "png")
    touch(SOURCE / "random_notes.bin", "\x00\x01binary")
    # Sensitive: left in place, never read
    touch(SOURCE / "mike_accessKeys.csv", "AKIAEXAMPLE,secret")
    touch(SOURCE / "server.pem", "-----BEGIN KEY-----")
    touch(SOURCE / "id_ed25519", "PRIVATE")
    # Protected
    touch(SOURCE / "desktop.ini", "[.ShellClassInfo]")


def make_cfg() -> dict:
    cfg = app_config.load()
    cfg.update({
        "setup_complete": True,
        "home_root": str(HOME),
        "buckets": ["Work", "Personal", "Archive"],
    })
    cfg["downloads"]["folder"] = str(DOWNLOADS)
    cfg["ai"]["enabled"] = False
    cfg["naming"]["enabled"] = True
    cfg["naming"]["auto_apply"] = True
    app_config.save(cfg)
    return cfg


def files_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def rel_depths(root: Path) -> int:
    depths = []
    for p in files_under(root):
        depths.append(len(p.relative_to(root).parts) - 1)   # folder levels above the file
    return max(depths) if depths else 0


def main() -> int:
    print(f"\nSandbox: {SANDBOX}\n")
    engine._rebuild_views = lambda cfg: None   # skip PowerShell in core asserts
    build_source()
    cfg = make_cfg()
    original_source_count = len(files_under(SOURCE))

    # --- pin one file: it must be left in place ---
    pins_mod.add(SOURCE / "SCE_Agreement_v3_final.txt")

    plan = engine.plan([SOURCE], cfg, include_subfolders=True, use_ai=False)
    planned_names = {mv.src.name for mv in plan.moves}
    print("Planned moves:")
    for mv in plan.moves:
        label = mv.bucket + ("\\" + mv.subfolder if mv.subfolder else "")
        print(f"   {mv.src.name:38} -> {label}\\{mv.new_name}")
    print()

    check("SCE_Agreement_v3_final.txt" not in planned_names, "pinned file is not planned for a move")
    check(all(n not in planned_names for n in
              ("mike_accessKeys.csv", "server.pem", "id_ed25519")),
          "sensitive files are left in place (not moved)")
    check("desktop.ini" not in planned_names, "desktop.ini is never touched")

    engine.execute(plan, cfg, rebuild=False, log=lambda s: None)

    # --- six-folder + two-level invariants ---
    top = sorted(d.name for d in HOME.iterdir() if d.is_dir())
    allowed = set(app_config.all_top_level(cfg))
    check(set(top).issubset(allowed), f"top-level folders within the six ({top})")
    check(len(top) <= 6, "at most six top-level folders")
    check(rel_depths(HOME) <= 2, "nothing deeper than two levels")

    # --- routing + renaming ---
    resumes = list((HOME / "Work" / "Resumes").glob("*.pdf")) if (HOME / "Work" / "Resumes").is_dir() else []
    check(len(resumes) == 3, "3 resumes routed to Work\\Resumes")
    check(any("_v1" in p.name for p in resumes) and any("_v3" in p.name for p in resumes),
          "resume versions preserved in the new names")
    check((HOME / "Work" / "Invoices").is_dir(), "invoice routed to Work\\Invoices")
    check((HOME / "Archive" / "Installers").is_dir(), "old installer routed to Archive\\Installers")

    # --- never-overwrite: identical names collapse to name + name (1) ---
    dups = sorted(p.name for p in (HOME / app_config.UNSORTED).glob("blobdata*.xyz"))
    check(dups == ["blobdata (1).xyz", "blobdata.xyz"],
          f"two identically-named files both kept ({dups})")

    # --- _UNSORTED holds the low-confidence pile, flat ---
    unsorted = files_under(HOME / app_config.UNSORTED)
    unsorted_names = {p.name for p in unsorted}
    check("screenshot_2026-09-10.png" in unsorted_names, "screenshot fell below the bar -> _UNSORTED")
    check("random_notes.bin" in unsorted_names, "unknown type -> _UNSORTED")
    check(rel_depths(HOME / app_config.UNSORTED) <= 1, "_UNSORTED is flat")

    # --- sensitive files really stayed in the source ---
    check((SOURCE / "server.pem").exists() and (SOURCE / "mike_accessKeys.csv").exists(),
          "sensitive files physically remain in the source folder")

    # --- search finds by CONTENT even when the name doesn't say so ---
    engine.reindex(cfg, with_ai_description=False, log=lambda s: None)
    con = index.connect()
    hits = index.search(con, "energy contract")
    con.close()
    check(any("Agreement" in h["name"] or "energy" in (h["description"] or "") for h in hits),
          "find 'energy contract' locates SCE_Agreement by content")
    check(all(not h["sensitive"] or h["path"] for h in hits), "search returns rows with locations")

    # --- desk membership (recent + pinned), computed without PowerShell ---
    views = desk.compute(cfg)
    desk_names = {it["name"] for it in views["desk"]}
    check(any("Resume" in n for n in desk_names), "recent resume appears on _DESK")
    check(any(it["pinned"] for it in views["desk"]), "pinned file appears on _DESK")

    # --- undo restores everything exactly ---
    engine.undo(cfg, log=lambda s: None)
    after = len(files_under(SOURCE))
    home_files = len(files_under(HOME))
    check(after == original_source_count, f"undo restored source ({after} == {original_source_count})")
    check(home_files == 0, f"undo emptied the home buckets ({home_files} files left)")

    # --- unique_destination unit ---
    tmpf = touch(SANDBOX / "collide.txt", "a")
    check(engine.unique_destination(tmpf).name == "collide (1).txt", "unique_destination avoids overwrite")

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    code = main()
    # best-effort cleanup
    try:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
    except Exception:
        pass
    sys.exit(code)
