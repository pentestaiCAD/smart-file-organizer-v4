"""Windows .lnk shortcut helpers.

_DESK and _RECENT are VIEWS, not locations: they hold shortcuts that point at
the real file wherever it actually lives (decision D2). We create shortcuts in
one batched PowerShell call (no admin, no pywin32 dependency) and regenerate
the whole view each run, so cleanup is just "delete every .lnk we own and
rebuild". We never put a real file in these folders, so clearing them can
never lose data.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000


def _run_powershell(script: str) -> subprocess.CompletedProcess:
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = _CREATE_NO_WINDOW
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, startupinfo=startupinfo,
        creationflags=creationflags, timeout=120,
    )


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def write_shortcuts(pairs: list[tuple[Path, Path]]) -> int:
    """Create each (link_path, target_path) shortcut. Returns how many were
    created. One PowerShell process for the whole batch."""
    pairs = [(Path(lnk), Path(tgt)) for lnk, tgt in pairs if Path(tgt).exists()]
    if not pairs:
        return 0
    lines = ["$sh = New-Object -ComObject WScript.Shell"]
    for lnk, tgt in pairs:
        lnk = lnk if lnk.suffix.lower() == ".lnk" else lnk.with_suffix(lnk.suffix + ".lnk")
        lines.append(
            f"$s = $sh.CreateShortcut({_ps_quote(lnk)}); "
            f"$s.TargetPath = {_ps_quote(tgt)}; "
            f"$s.WorkingDirectory = {_ps_quote(tgt.parent)}; "
            f"$s.Save()"
        )
    try:
        proc = _run_powershell("\n".join(lines))
    except (OSError, subprocess.SubprocessError):
        return 0
    if proc.returncode != 0:
        return 0
    return sum(1 for lnk, _ in pairs
               if (lnk if lnk.suffix.lower() == ".lnk" else lnk.with_suffix(lnk.suffix + ".lnk")).exists())


def clear_shortcuts(folder: Path) -> int:
    """Delete every .lnk directly inside `folder` (an app-owned view). Real
    files are never stored here, so this is always safe."""
    folder = Path(folder)
    removed = 0
    if not folder.is_dir():
        return 0
    for lnk in folder.glob("*.lnk"):
        try:
            lnk.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def resolve_target(lnk: Path) -> str | None:
    """Best-effort read of a shortcut's target (used to import Windows Recent)."""
    try:
        proc = _run_powershell(
            "$sh = New-Object -ComObject WScript.Shell; "
            f"$sh.CreateShortcut({_ps_quote(Path(lnk))}).TargetPath"
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    return out or None
