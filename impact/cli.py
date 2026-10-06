"""impact: change impact analysis CLI. Every command prints JSON so an agent can read it reliably."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .gitutil import GitError, diff, file_history, merge_base, repo_root, show_file


def _out(data: dict, code: int = 0):
    print(json.dumps(data, indent=2, default=str))
    sys.exit(code)


def cmd_index(cfg, args):
    from .index import build_index
    _out(build_index(cfg, full=args.full))


def cmd_facts(cfg, args):
    from .facts import build_facts
    _out(build_facts(cfg, args.branch, args.base, depth=args.depth))


def cmd_callers(cfg, args):
    from .graph import Graph
    from .index import require_index
    from .parsing import language_for, parse_source
    con = require_index(cfg.root)
    g = Graph(con, cfg)
    if args.branch:
        mb = merge_base(cfg.root, args.base, args.branch)
        for fd in diff(cfg.root, mb, args.branch):
            if fd.new_path and language_for(fd.new_path):
                src = show_file(cfg.root, args.branch, fd.new_path)
                g.add_overlay(fd.new_path, parse_source(fd.new_path, src) if src is not None else None)
            elif fd.old_path and not fd.new_path:
                g.add_overlay(fd.old_path, None)
    name = args.symbol.split(".")[-1]
    defs = g.definitions_named(name)
    tree = g.caller_tree(name, args.depth)
    _out({
        "symbol": args.symbol, "searched_name": name,
        "definitions": [{k: d[k] for k in ("qualname", "file", "start_line", "kind", "header")} for d in defs],
        "callers": tree["nodes"], "tests_reached": tree["tests"],
        "truncated_callers": tree["truncated_callers"],
        "note": ("Name-based match across multiple definitions; read the caller code to confirm which one it calls."
                 if len(defs) > 1 else None),
    })


def cmd_run_tests(cfg, args):
    from .testrun import run_tests
    if not args.tests:
        _out({"error": "Pass one or more test ids, for example from `impact facts` selected_tests."}, 2)
    result = run_tests(cfg, args.tests, module_path=args.module, sandbox=args.sandbox, timeout=args.timeout)
    _out(result, 2 if "error" in result else 0)


def cmd_sandbox(cfg, args):
    from . import sandbox
    if args.action == "create":
        if not args.branch:
            _out({"error": "--branch is required for `sandbox create`"}, 2)
        _out(sandbox.create(cfg.root, args.branch))
    elif args.action == "cleanup":
        _out(sandbox.cleanup(cfg.root))
    else:
        path = sandbox.worktree_path(cfg.root)
        _out({"exists": path.exists(), "sandbox_path": str(path), "changed_files": sandbox.changed_files(cfg.root)})


def cmd_report(cfg, args):
    from .report import render
    result = render(cfg.root, Path(args.findings), Path(args.out_dir) if args.out_dir else None)
    _out(result, 0 if result["ok"] else 2)


def cmd_schema(cfg, args):
    from .report import load_schema
    _out(load_schema())


def cmd_history(cfg, args):
    _out(file_history(cfg.root, args.file, args.days))


def cmd_db_usage(cfg, args):
    from .index import require_index
    from .sqlschema import db_usage
    con = require_index(cfg.root)
    files = [r[0] for r in con.execute("SELECT path FROM files")]
    table, _, column = args.target.partition(".")
    _out(db_usage(cfg.root, files, table, column or None, limit=args.limit))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="impact", description="Change impact analysis for monorepos. Output is JSON.")
    p.add_argument("--root", help="Repo root (default: git top-level of current directory)")
    p.add_argument("--config", help="Path to impact.yaml (default: <root>/impact.yaml)")
    p.add_argument("--version", action="version", version=f"impact {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("index", help="Build or refresh the code index")
    s.add_argument("--full", action="store_true", help="Rebuild from scratch")
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("facts", help="Changed symbols, callers, tests and risk for a branch")
    s.add_argument("--branch", required=True)
    s.add_argument("--base", default="main")
    s.add_argument("--depth", type=int, default=2, help="Caller depth to report (tests are searched one level deeper)")
    s.set_defaults(func=cmd_facts)

    s = sub.add_parser("callers", help="Who calls a symbol, N levels up")
    s.add_argument("symbol", help="Function, method or class name (Class.method is accepted)")
    s.add_argument("--depth", type=int, default=2)
    s.add_argument("--branch", help="Include call changes made on this branch")
    s.add_argument("--base", default="main")
    s.set_defaults(func=cmd_callers)

    s = sub.add_parser("run-tests", help="Run test ids with each module's configured command")
    s.add_argument("tests", nargs="*")
    s.add_argument("--module", help="Module path from impact.yaml, for ids not in the index (new tests)")
    s.add_argument("--sandbox", action="store_true", help="Run inside the sandbox worktree")
    s.add_argument("--timeout", type=int, default=900)
    s.set_defaults(func=cmd_run_tests)

    s = sub.add_parser("sandbox", help="Temporary worktree for generated tests")
    s.add_argument("action", choices=["create", "cleanup", "status"])
    s.add_argument("--branch")
    s.set_defaults(func=cmd_sandbox)

    s = sub.add_parser("report", help="Validate findings JSON and render the HTML report")
    s.add_argument("findings")
    s.add_argument("--out-dir")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("schema", help="Print the findings JSON schema")
    s.set_defaults(func=cmd_schema)

    s = sub.add_parser("history", help="Recent commits and bug fixes for a file")
    s.add_argument("file")
    s.add_argument("--days", type=int, default=180)
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("db-usage", help="Code that references a table or table.column")
    s.add_argument("target", help="table or table.column")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_db_usage)
    return p


def main(argv: list[str] | None = None):
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.root).resolve() if args.root else repo_root(Path.cwd())
        cfg = load_config(root, Path(args.config) if args.config else None)
        args.func(cfg, args)
    except (GitError, RuntimeError) as e:
        _out({"error": str(e)}, 2)


if __name__ == "__main__":
    main()
