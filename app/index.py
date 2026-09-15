"""Full-text search index — the safety net that makes anything findable.

Built on SQLite FTS5 (bundled with Python; no third-party dependency for the
index itself). Indexes filename + locally-extracted content + an optional AI
description. Content extraction is local-first:

  text/code/markdown/csv/json/log : read directly (stdlib)
  pdf                             : pypdf, if installed
  docx                            : python-docx, if installed
  images/audio/video/unknown     : filename only (no bytes leave the machine)

Credential-like files are never read (their `content` stays empty). Only text
that was extracted locally is ever eligible to be sent to the AI describer, and
every such call is logged by ai_provider.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import app_config

_TEXT_TYPES = {"text", "code", "spreadsheet"}          # spreadsheet = csv/ods; xlsx handled as none
_PLAINTEXT_EXT = {"txt", "md", "log", "csv", "json", "yaml", "yml", "py", "js", "ts",
                  "tsx", "jsx", "html", "css", "ps1", "bat", "sh", "psm1", "sql",
                  "java", "cpp", "c", "h", "go", "rs", "rb", "php", "ini", "cfg", "toml"}
_MAX_READ = 1_000_000        # bytes to read from a text file
_MAX_STORE = 100_000         # chars of content kept in the index


def connect() -> sqlite3.Connection:
    app_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(app_config.INDEX_PATH))
    con.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
            path UNINDEXED, name, content, description,
            bucket UNINDEXED, mtime UNINDEXED, size UNINDEXED,
            sensitive UNINDEXED, tokenize='unicode61'
        )
    """)
    return con


def _read_plaintext(path: Path) -> str | None:
    try:
        raw = path.open("rb").read(_MAX_READ)
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            return None
    if not text:
        return None
    printable = sum(1 for c in text[:4000] if c.isprintable() or c in "\n\r\t")
    if printable / min(len(text), 4000) < 0.7:
        return None
    return text


def _read_pdf(path: Path) -> str | None:
    try:
        import pypdf
    except ImportError:
        return None
    try:
        reader = pypdf.PdfReader(str(path))
        pages = reader.pages[:10]
        return "\n".join((p.extract_text() or "") for p in pages) or None
    except Exception:
        return None


def _read_docx(path: Path) -> str | None:
    try:
        import docx
    except ImportError:
        return None
    try:
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs) or None
    except Exception:
        return None


def extract_text(path: Path, ftype: str | None, sensitive: bool) -> str | None:
    """Local content extraction. Returns None (never raises) when the file type
    isn't text-bearing or the file is sensitive."""
    if sensitive:
        return None
    ext = path.suffix.lower().lstrip(".")
    if ext in _PLAINTEXT_EXT:
        return _read_plaintext(path)
    if ext == "pdf":
        return _read_pdf(path)
    if ext == "docx":
        return _read_docx(path)
    return None


def snippet_of(content: str | None, limit: int = 1500) -> str | None:
    if not content:
        return None
    return content[:limit]


def upsert(con: sqlite3.Connection, *, path: Path, name: str, content: str | None,
           description: str | None, bucket: str, mtime: float, size: int,
           sensitive: bool) -> None:
    p = str(Path(path).resolve())
    con.execute("DELETE FROM docs WHERE path = ?", (p,))
    con.execute(
        "INSERT INTO docs(path,name,content,description,bucket,mtime,size,sensitive) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (p, name, (content or "")[:_MAX_STORE], description or "", bucket,
         str(mtime), str(size), "1" if sensitive else "0"),
    )


def remove(con: sqlite3.Connection, path) -> None:
    con.execute("DELETE FROM docs WHERE path = ?", (str(Path(path).resolve()),))


def _fts_query(query: str) -> str | None:
    terms = re.findall(r"[0-9A-Za-z]+", query)
    if not terms:
        return None
    return " ".join(f'{t}*' for t in terms)      # implicit AND, prefix match


def search(con: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    fq = _fts_query(query)
    rows: list[dict] = []
    if fq:
        try:
            cur = con.execute(
                "SELECT path,name,description,bucket,mtime,sensitive "
                "FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?", (fq, limit))
            rows = [_row(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            rows = []
    if not rows:                                  # fallback: plain name substring
        like = f"%{query}%"
        cur = con.execute(
            "SELECT path,name,description,bucket,mtime,sensitive "
            "FROM docs WHERE name LIKE ? LIMIT ?", (like, limit))
        rows = [_row(r) for r in cur.fetchall()]
    return rows


def _row(r) -> dict:
    path, name, desc, bucket, mtime, sensitive = r
    try:
        mt = float(mtime)
    except (TypeError, ValueError):
        mt = 0.0
    return {"path": path, "name": name, "description": desc, "bucket": bucket,
            "mtime": mt, "sensitive": sensitive == "1"}


def stats(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM docs").fetchone()[0]
