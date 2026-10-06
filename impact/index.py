"""Local SQLite index of symbols, call references and test units (.impact/index.db)."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path, PurePosixPath

from .config import Config
from .gitutil import tracked_files
from .parsing import ParseResult, family_for, is_test_file, language_for, parse_source

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, language TEXT, module TEXT, sha TEXT, is_test INTEGER, lines INTEGER
);
CREATE TABLE IF NOT EXISTS symbols (
  path TEXT, name TEXT, qualname TEXT, kind TEXT, start_line INTEGER, end_line INTEGER,
  header TEXT, is_test INTEGER, test_id TEXT
);
CREATE TABLE IF NOT EXISTS refs (path TEXT, callee TEXT, line INTEGER, caller TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_sym_path ON symbols(path);
CREATE INDEX IF NOT EXISTS idx_refs_callee ON refs(callee);
CREATE INDEX IF NOT EXISTS idx_refs_path ON refs(path);
"""

JS_FILE_UNIT = "<module>"


def db_path(root: Path) -> Path:
    return root / ".impact" / "index.db"


def connect(root: Path) -> sqlite3.Connection:
    p = db_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def require_index(root: Path) -> sqlite3.Connection:
    if not db_path(root).exists():
        raise RuntimeError("No index found. Run `impact index` first.")
    return connect(root)


def test_id_for(cfg: Config, path: str, qualname: str, kind: str) -> str | None:
    """How a test unit is passed to its runner. Python ids are relative to the module folder,
    because the test command runs with the module folder as working directory."""
    fam = family_for(path)
    module = cfg.module_for(path)
    rel = path
    if module and module.path:
        rel = str(PurePosixPath(path).relative_to(module.path))
    if fam == "python":
        return f"{rel}::{qualname.replace('.', '::')}"
    if fam == "java":
        parts = qualname.split(".")
        if len(parts) >= 2:
            return f"{parts[-2]}#{parts[-1]}"
        return None
    if fam == "js":
        return rel
    return None


def rows_for(cfg: Config, path: str, parsed: ParseResult):
    """Converts a parse result into symbol and ref rows, adding the file-level test unit for JS/TS."""
    test_file = is_test_file(path)
    sym_rows = []
    for s in parsed.symbols:
        tid = test_id_for(cfg, path, s.qualname, s.kind) if s.is_test else None
        sym_rows.append((path, s.name, s.qualname, s.kind, s.start_line, s.end_line, s.header, int(s.is_test), tid))
    if test_file and family_for(path) == "js":
        sym_rows.append((path, PurePosixPath(path).name, JS_FILE_UNIT, "test_file", 1, parsed.line_count,
                         "", 1, test_id_for(cfg, path, JS_FILE_UNIT, "test_file")))
    ref_rows = [(path, r.callee, r.line, r.caller) for r in parsed.refs]
    return sym_rows, ref_rows


def build_index(cfg: Config, full: bool = False) -> dict:
    root = cfg.root
    con = connect(root)
    if full:
        con.executescript("DELETE FROM files; DELETE FROM symbols; DELETE FROM refs;")
    started = time.time()
    known = {r["path"]: r["sha"] for r in con.execute("SELECT path, sha FROM files")}
    seen, parsed_count, skipped, failed = set(), 0, 0, []

    for path in tracked_files(root):
        if cfg.is_ignored(path) or language_for(path) is None or cfg.module_for(path) is None:
            continue
        fp = root / path
        if not fp.is_file():
            continue
        data = fp.read_bytes()
        sha = hashlib.sha1(data).hexdigest()
        seen.add(path)
        if known.get(path) == sha:
            skipped += 1
            continue
        try:
            parsed = parse_source(path, data.decode("utf-8", errors="replace"))
        except Exception as e:  # keep indexing even if one file is weird
            failed.append({"file": path, "error": str(e)})
            continue
        if parsed is None:
            continue
        con.execute("DELETE FROM symbols WHERE path=?", (path,))
        con.execute("DELETE FROM refs WHERE path=?", (path,))
        sym_rows, ref_rows = rows_for(cfg, path, parsed)
        con.executemany("INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?)", sym_rows)
        con.executemany("INSERT INTO refs VALUES (?,?,?,?)", ref_rows)
        module = cfg.module_for(path)
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                    (path, parsed.language, module.path if module else "", sha,
                     int(is_test_file(path)), parsed.line_count))
        parsed_count += 1

    removed = [p for p in known if p not in seen]
    for p in removed:
        con.execute("DELETE FROM files WHERE path=?", (p,))
        con.execute("DELETE FROM symbols WHERE path=?", (p,))
        con.execute("DELETE FROM refs WHERE path=?", (p,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('indexed_at', ?)", (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    con.commit()

    stats = {
        "files_indexed": con.execute("SELECT COUNT(*) FROM files").fetchone()[0],
        "files_parsed_now": parsed_count,
        "files_unchanged": skipped,
        "files_removed": len(removed),
        "symbols": con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
        "references": con.execute("SELECT COUNT(*) FROM refs").fetchone()[0],
        "test_units": con.execute("SELECT COUNT(*) FROM symbols WHERE is_test=1").fetchone()[0],
        "seconds": round(time.time() - started, 2),
        "parse_failures": failed[:20],
    }
    con.close()
    return stats
