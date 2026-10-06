"""`impact facts`: changed symbols, callers, reachable tests, schema changes and risk for a branch."""
from __future__ import annotations

from .config import Config
from .gitutil import diff, file_history, merge_base, show_file
from .graph import Graph
from .index import require_index
from .parsing import innermost_symbol, language_for, parse_source
from .risk import overall, score_symbol
from .sqlschema import db_usage, parse_schema_changes


def _changed_symbols(fd, base_parsed, branch_parsed):
    """Maps changed lines to the innermost enclosing symbol on each side."""
    out, module_lines = {}, []
    base_syms = {s.qualname: s for s in (base_parsed.symbols if base_parsed else [])}
    branch_syms = {s.qualname: s for s in (branch_parsed.symbols if branch_parsed else [])}

    if branch_parsed:
        for a, b in fd.new_ranges:
            for line in range(a, b + 1):
                s = innermost_symbol(branch_parsed.symbols, line)
                if s is None:
                    if line <= branch_parsed.line_count:
                        module_lines.append(line)
                    continue
                if s.qualname not in out:
                    old = base_syms.get(s.qualname)
                    out[s.qualname] = {
                        "symbol": s, "status": "modified" if old else "added",
                        "signature_changed": bool(old and old.header != s.header),
                        "old_header": old.header if old and old.header != s.header else None,
                    }
    if base_parsed:
        for a, b in fd.old_ranges:
            for line in range(a, b + 1):
                s = innermost_symbol(base_parsed.symbols, line)
                if s and s.qualname not in branch_syms and s.qualname not in out:
                    out[s.qualname] = {"symbol": s, "status": "deleted", "signature_changed": False, "old_header": None}
    return list(out.values()), sorted(set(module_lines))


def _compress(lines: list[int]) -> list[list[int]]:
    ranges = []
    for ln in lines:
        if ranges and ln == ranges[-1][1] + 1:
            ranges[-1][1] = ln
        else:
            ranges.append([ln, ln])
    return ranges


def build_facts(cfg: Config, branch: str, base: str, depth: int = 2, test_depth: int = 3) -> dict:
    root = cfg.root
    con = require_index(root)
    graph = Graph(con, cfg)
    mb = merge_base(root, base, branch)
    file_diffs = diff(root, mb, branch)

    files_changed, parsed_pairs, schema_changes, notes = [], [], [], []
    for fd in file_diffs:
        path = fd.path
        if cfg.is_ignored(path):
            continue
        entry = {"file": path, "status": fd.status, "language": language_for(path) or path.rsplit(".", 1)[-1]}
        files_changed.append(entry)
        if path.lower().endswith(".sql"):
            schema_changes += parse_schema_changes("\n".join(fd.added_text), path)
            continue
        if language_for(path) is None:
            continue
        base_src = show_file(root, mb, fd.old_path) if fd.old_path else None
        branch_src = show_file(root, branch, fd.new_path) if fd.new_path else None
        base_parsed = parse_source(path, base_src) if base_src is not None else None
        branch_parsed = parse_source(path, branch_src) if branch_src is not None else None
        graph.add_overlay(path, branch_parsed)
        if fd.old_path and fd.old_path != path:
            graph.add_overlay(fd.old_path, None)
        parsed_pairs.append((fd, base_parsed, branch_parsed))

    changed, module_level, history_cache = [], [], {}
    selected_tests: dict[str, dict] = {}
    for fd, base_parsed, branch_parsed in parsed_pairs:
        path = fd.path
        syms, mod_lines = _changed_symbols(fd, base_parsed, branch_parsed)
        if mod_lines:
            module_level.append({"file": path, "lines": _compress(mod_lines)})
        if path not in history_cache:
            history_cache[path] = file_history(root, path, cfg.history_days)
        hist = history_cache[path]

        for item in syms:
            s = item["symbol"]
            if s.kind == "test_file":
                continue
            tree = graph.caller_tree(s.name, max(depth, test_depth))
            direct = [n for n in tree["nodes"] if n["depth"] == 1 and not n["is_test"]]
            indirect = [n for n in tree["nodes"] if 1 < n["depth"] <= depth and not n["is_test"]]
            tests = list(tree["tests"])
            if s.is_test:
                info = graph.symbol(path, s.qualname)
                if info and info.get("test_id"):
                    tests.append({"id": info["test_id"], "file": path, "depth": 0})
            module = cfg.module_for(path)
            for t in tests:
                tm = cfg.module_for(t["file"])
                selected_tests.setdefault(t["id"], {**t, "module": tm.path if tm else ""})
            defs = graph.definitions_named(s.name)
            risk = score_symbol(item["status"], item["signature_changed"], len(direct), len(indirect),
                                len(tests), hist, len(defs))
            changed.append({
                "symbol": s.name, "qualname": s.qualname, "kind": s.kind, "file": path,
                "module": module.path if module else "",
                "lines": [s.start_line, s.end_line], "status": item["status"],
                "is_test": s.is_test,
                "signature_changed": item["signature_changed"],
                "old_signature": item["old_header"], "signature": s.header,
                "definitions_with_same_name": len(defs),
                "direct_callers": [{k: n[k] for k in ("caller", "file", "line")} for n in direct],
                "indirect_callers": [{k: n[k] for k in ("caller", "file", "line", "calls", "depth")} for n in indirect],
                "callers_truncated": tree["truncated_callers"],
                "tests": sorted({t["id"] for t in tests}),
                "risk": risk,
            })
        if not syms and not mod_lines and fd.status == "renamed":
            notes.append(f"{path} was renamed without content changes; imports of the old path may break.")

    for sc in schema_changes:
        code_files = [r["path"] for r in con.execute("SELECT path FROM files")]
        sc["code_usage"] = db_usage(root, code_files, sc["table"], sc.get("column"), limit=15)

    changed.sort(key=lambda c: -c["risk"]["score"])
    total_tests = graph.test_unit_count()
    if any(c["definitions_with_same_name"] > 3 for c in changed):
        notes.append("Some changed symbols have common names; verify callers by reading code before judging them.")
    if not selected_tests and changed:
        notes.append("No tests reach the changed code through the call graph. Consider writing one in the sandbox.")

    con.close()
    return {
        "branch": branch, "base": base, "merge_base": mb,
        "files_changed": files_changed,
        "changed_symbols": changed,
        "module_level_changes": module_level,
        "schema_changes": schema_changes,
        "file_history": list(history_cache.values()),
        "selected_tests": sorted(selected_tests.values(), key=lambda t: (t["module"], t["id"])),
        "test_totals": {"selected": len(selected_tests), "total_in_index": total_tests},
        "overall_risk": overall([c["risk"] for c in changed], schema_changes),
        "next_steps": {
            "create_sandbox": f"impact sandbox create --branch {branch}",
            "run_tests": ("impact run-tests " + " ".join(sorted(selected_tests)) + " --sandbox") if selected_tests else None,
            "drill_down": "impact callers <symbol> --depth 3 --branch " + branch,
        },
        "notes": notes,
    }
