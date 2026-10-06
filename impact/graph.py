"""Caller lookups over the index, with an in-memory overlay of files as they exist on the branch.

The index reflects the working tree when `impact index` last ran. Files changed on the branch are
re-parsed from git and overlaid, so new or removed calls on the branch are taken into account.
"""
from __future__ import annotations

import sqlite3
from collections import deque

from .config import Config
from .index import JS_FILE_UNIT, rows_for
from .parsing import ParseResult

MAX_CALLERS_PER_NODE = 25


class Graph:
    def __init__(self, con: sqlite3.Connection, cfg: Config):
        self.con = con
        self.cfg = cfg
        self.overlay_syms: dict[str, list[tuple]] = {}
        self.overlay_refs: dict[str, list[tuple]] = {}

    def add_overlay(self, path: str, parsed: ParseResult | None):
        """Replace a file's indexed data with the branch version (None means the file is deleted)."""
        if parsed is None:
            self.overlay_syms[path], self.overlay_refs[path] = [], []
            return
        syms, refs = rows_for(self.cfg, path, parsed)
        self.overlay_syms[path], self.overlay_refs[path] = syms, refs

    def _excluded(self) -> tuple[str, list[str]]:
        paths = list(self.overlay_refs)
        if not paths:
            return "", []
        return f" AND path NOT IN ({','.join('?' * len(paths))})", paths

    def callers_of(self, name: str) -> list[dict]:
        clause, params = self._excluded()
        rows = self.con.execute(f"SELECT path, line, caller FROM refs WHERE callee=?{clause}", [name, *params]).fetchall()
        out = [{"file": r["path"], "line": r["line"], "caller": r["caller"]} for r in rows]
        for path, refs in self.overlay_refs.items():
            out += [{"file": path, "line": line, "caller": caller} for (_, callee, line, caller) in refs if callee == name]
        return out

    def symbol(self, path: str, qualname: str) -> dict | None:
        if path in self.overlay_syms:
            for s in self.overlay_syms[path]:
                if s[2] == qualname:
                    return _sym_dict(s)
            return None
        r = self.con.execute("SELECT * FROM symbols WHERE path=? AND qualname=?", (path, qualname)).fetchone()
        return _sym_dict(tuple(r)) if r else None

    def definitions_named(self, name: str) -> list[dict]:
        clause, params = self._excluded()
        rows = self.con.execute(f"SELECT * FROM symbols WHERE name=?{clause}", [name, *params]).fetchall()
        out = [_sym_dict(tuple(r)) for r in rows]
        for syms in self.overlay_syms.values():
            out += [_sym_dict(s) for s in syms if s[1] == name]
        return [d for d in out if d["kind"] != "test_file"]

    def test_unit_count(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM symbols WHERE is_test=1").fetchone()[0]

    def caller_tree(self, name: str, depth: int) -> dict:
        """BFS up the call chain. Returns flat nodes plus reached tests."""
        nodes, tests = [], {}
        seen = set()
        queue = deque([(name, 0, None)])
        truncated = 0
        while queue:
            cur, d, via = queue.popleft()
            if d >= depth:
                continue
            callers = self.callers_of(cur)
            if len(callers) > MAX_CALLERS_PER_NODE:
                truncated += len(callers) - MAX_CALLERS_PER_NODE
                callers = callers[:MAX_CALLERS_PER_NODE]
            for c in callers:
                key = (c["file"], c["caller"])
                if c["caller"].split(".")[-1] == cur or key in seen:  # skip recursion and repeats
                    continue
                seen.add(key)
                info = self.symbol(c["file"], c["caller"]) or {}
                node = {
                    "caller": c["caller"], "file": c["file"], "line": c["line"],
                    "calls": cur, "depth": d + 1, "kind": info.get("kind", "module_code"),
                    "is_test": bool(info.get("is_test")),
                }
                if node["is_test"] and info.get("test_id"):
                    node["test_id"] = info["test_id"]
                    tests[info["test_id"]] = {"id": info["test_id"], "file": c["file"], "depth": d + 1}
                nodes.append(node)
                # keep climbing through production code only; module-level code has no name to search for
                if not node["is_test"] and c["caller"] not in ("<module>", JS_FILE_UNIT):
                    queue.append((c["caller"].split(".")[-1], d + 1, cur))
        return {"nodes": nodes, "tests": list(tests.values()), "truncated_callers": truncated}


def _sym_dict(s: tuple) -> dict:
    return {"file": s[0], "name": s[1], "qualname": s[2], "kind": s[3], "start_line": s[4],
            "end_line": s[5], "header": s[6], "is_test": bool(s[7]), "test_id": s[8]}
