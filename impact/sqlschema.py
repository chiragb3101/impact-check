"""Detects schema changes in SQL migration text and finds code that touches tables and columns."""
from __future__ import annotations

import re
from pathlib import Path

IDENT = r"[`\"\[]?([\w.]+)[`\"\]]?"

CREATE_TABLE = re.compile(rf"create\s+table\s+(?:if\s+not\s+exists\s+)?{IDENT}", re.I)
DROP_TABLE = re.compile(rf"drop\s+table\s+(?:if\s+exists\s+)?{IDENT}", re.I)
ALTER_TABLE = re.compile(rf"alter\s+table\s+(?:only\s+)?(?:if\s+exists\s+)?{IDENT}\s+(.*)", re.I | re.S)
CREATE_INDEX = re.compile(rf"create\s+(?:unique\s+)?index\s+(concurrently\s+)?(?:if\s+not\s+exists\s+)?{IDENT}?\s*on\s+{IDENT}", re.I)

ALTER_OPS = [
    ("rename_column", re.compile(rf"rename\s+column\s+{IDENT}\s+to\s+{IDENT}", re.I), "high"),
    ("rename_table", re.compile(rf"rename\s+to\s+{IDENT}", re.I), "high"),
    ("drop_column", re.compile(rf"drop\s+(?:column\s+)?(?:if\s+exists\s+)?{IDENT}", re.I), "high"),
    ("change_column", re.compile(rf"change\s+(?:column\s+)?{IDENT}\s+{IDENT}", re.I), "high"),
    ("alter_column", re.compile(rf"(?:alter|modify)\s+(?:column\s+)?{IDENT}", re.I), "medium"),
    ("add_column", re.compile(rf"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?{IDENT}(.*)", re.I | re.S), "low"),
]

SQL_KEYWORDS = {"constraint", "primary", "foreign", "unique", "index", "key", "check", "column"}


def _short(name: str) -> str:
    return name.split(".")[-1].strip('`"[]').lower()


def parse_schema_changes(sql_text: str, file: str) -> list[dict]:
    changes = []
    for stmt in sql_text.split(";"):
        s = " ".join(stmt.split())
        if not s or s.startswith("--"):
            continue
        if m := CREATE_TABLE.search(s):
            changes.append({"file": file, "operation": "create_table", "table": _short(m.group(1)), "severity": "low"})
            continue
        if m := DROP_TABLE.search(s):
            changes.append({"file": file, "operation": "drop_table", "table": _short(m.group(1)), "severity": "high"})
            continue
        if m := CREATE_INDEX.search(s):
            concurrent = bool(m.group(1))
            changes.append({"file": file, "operation": "create_index", "table": _short(m.group(3)),
                            "severity": "low" if concurrent else "medium",
                            "note": None if concurrent else "Index created without CONCURRENTLY can lock the table on large data (Postgres)."})
            continue
        if m := ALTER_TABLE.search(s):
            table, rest = _short(m.group(1)), m.group(2)
            for clause in re.split(r",(?![^()]*\))", rest):
                clause = clause.strip()
                for op, rx, sev in ALTER_OPS:
                    om = rx.match(clause)
                    if not om:
                        continue
                    col = _short(om.group(1))
                    if col in SQL_KEYWORDS:
                        break
                    entry = {"file": file, "operation": op, "table": table, "column": col, "severity": sev}
                    if op in ("rename_column", "change_column"):
                        entry["new_name"] = _short(om.group(2))
                    if op == "rename_table":
                        entry = {"file": file, "operation": op, "table": table, "new_name": col, "severity": sev}
                    if op == "add_column":
                        tail = om.group(2).lower()
                        if "not null" in tail and "default" not in tail:
                            entry["severity"] = "medium"
                            entry["note"] = "NOT NULL column without DEFAULT fails on tables that already have rows."
                    changes.append(entry)
                    break
    return changes


def db_usage(root: Path, code_files: list[str], table: str, column: str | None = None,
             limit: int = 50) -> dict:
    t_rx = re.compile(rf"\b{re.escape(table)}\b", re.I)
    c_rx = re.compile(rf"\b{re.escape(column)}\b", re.I) if column else None
    hits, total = [], 0
    for f in code_files:
        try:
            text = (root / f).read_text(errors="replace")
        except OSError:
            continue
        if not t_rx.search(text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            match = c_rx.search(line) if c_rx else t_rx.search(line)
            if match:
                total += 1
                if len(hits) < limit:
                    hits.append({"file": f, "line": i, "code": line.strip()[:200]})
    return {"table": table, "column": column, "total_hits": total, "hits": hits,
            "note": "Text match in files that mention the table. ORM mappings with different names may be missed."}
