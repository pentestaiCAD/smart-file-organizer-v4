# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [4.0.0] — 2026-09-15

The "Findable First" rewrite: optimize for *finding* files, not filing them.

### Added
- **`_DESK`** — an always-visible view of the few files that matter now
  (modified in the last 3 days, opened 2+ times recently, or pinned).
  Rendered as `.lnk` shortcuts — the real files never move.
- **`_RECENT`** — a last-7-days view that auto-expires.
- **Semantic Find** over filename + local file contents + optional AI
  description, backed by SQLite FTS5.
- **Pin / rescue** — pinned files are untouchable: never moved, renamed,
  expired, or cleaned up.
- **Purpose-based renaming** — previewed, editable, high-confidence-only, and
  fully reversible.
- **Outbound AI-call log** (`ai-calls.log`) — every remote call is recorded.
- **Persistent Undo button** in the main window.
- Circuit breaker so a failing/rate-limited AI provider can't fire a call per
  file during a scan.

### Changed
- **Six top-level folders, max two levels deep** — replaces v3's deep tree of
  ~13 type-folders.
- **Never guess** — low-confidence files stay in `_UNSORTED` with their
  original names instead of being routed into a likely-wrong bucket.
- **AI is for naming and search only**, never for choosing a folder. Naming no
  longer calls the model during a scan by default (`naming.ai_names`), keeping
  scans instant and offline.
- Operational data (config, rules, journal, index, usage, pins, logs) moved to
  `%APPDATA%\SmartFileOrganizer\`, keeping the Home root at exactly six folders.

### Kept from 3.0
- The Inno Setup installer, the journal + one-click undo, the `quicksort.py`
  CLI sibling, the notice-only Downloads watcher, and the optional AI provider.

### Security / safety
- Credential-like files (`.pem`, `id_ed25519`, `*accessKeys*`, wallets, …) are
  left in place, never read, and never sent to an AI.
- Denylist for protected locations; hidden/system files and files open in
  another app are skipped; never overwrites.
