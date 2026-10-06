"""`impact run-tests`: runs test ids with each module's configured command and returns clean JSON."""
from __future__ import annotations

import glob
import os
import shlex
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import Config, Module
from .index import db_path
from .sandbox import worktree_path


def _lookup_module(cfg: Config, test_id: str) -> Module | None:
    """Finds the module of a test id: via the index first, then by file path in the id."""
    if db_path(cfg.root).exists():
        import sqlite3
        con = sqlite3.connect(db_path(cfg.root))
        row = con.execute("SELECT path FROM symbols WHERE test_id=? LIMIT 1", (test_id,)).fetchone()
        con.close()
        if row:
            return cfg.module_for(row[0])
    file_part = test_id.split("::")[0]
    for m in sorted(cfg.modules, key=lambda m: -len(m.path)):
        if m.path and (m.contains(file_part) or (Path(cfg.root) / m.path / file_part).exists()):
            return m
    candidates = [m for m in cfg.modules if m.test_command]
    return candidates[0] if len(candidates) == 1 else None


def _parse_junit(paths: list[str], started: float) -> list[dict]:
    results = []
    for p in paths:
        try:
            if os.path.getmtime(p) < started - 1:
                continue  # stale report from an earlier run
            tree = ET.parse(p)
        except (OSError, ET.ParseError):
            continue
        for case in tree.iter("testcase"):
            name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
            status, message = "passed", None
            for tag in ("failure", "error"):
                el = case.find(tag)
                if el is not None:
                    status = "failed" if tag == "failure" else "error"
                    message = (el.get("message") or el.text or "").strip()[:1500]
            if case.find("skipped") is not None:
                status = "skipped"
            results.append({"test": name, "status": status, "message": message,
                            "seconds": float(case.get("time") or 0)})
    return results


def run_tests(cfg: Config, test_ids: list[str], module_path: str | None = None,
              sandbox: bool = False, timeout: int = 900) -> dict:
    base_dir = worktree_path(cfg.root) if sandbox else cfg.root
    if sandbox and not base_dir.exists():
        return {"error": "Sandbox does not exist. Run `impact sandbox create --branch <branch>` first."}

    groups: dict[str, list[str]] = {}
    unresolved = []
    for tid in test_ids:
        m = cfg.module_by_path(module_path) if module_path is not None else _lookup_module(cfg, tid)
        if m is None or not m.test_command:
            unresolved.append(tid)
            continue
        groups.setdefault(m.path, []).append(tid)

    started_all = time.time()
    module_runs, results = [], []
    for mpath, ids in groups.items():
        m = cfg.module_by_path(mpath)
        cwd = base_dir / m.path if m.path else base_dir
        junit_tmp = tempfile.NamedTemporaryFile(prefix="impact-junit-", suffix=".xml", delete=False).name
        joined = m.test_separator.join(ids)
        tests_arg = shlex.quote(joined) if m.test_separator == "," else " ".join(shlex.quote(i) for i in ids)
        cmd = m.test_command.replace("{tests}", tests_arg).replace("{junit}", junit_tmp)
        env = {**os.environ, **{k: str(v).replace("{junit}", junit_tmp) for k, v in m.env.items()}}

        started = time.time()
        try:
            proc = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            exit_code, output = proc.returncode, (proc.stdout + "\n" + proc.stderr)
        except subprocess.TimeoutExpired as e:
            exit_code, output = -1, f"Timed out after {timeout}s\n{e.stdout or ''}"
        elapsed = round(time.time() - started, 2)

        junit_paths = [junit_tmp] if os.path.getsize(junit_tmp) > 0 else []
        if m.junit:
            jp = cwd / m.junit
            junit_paths += glob.glob(str(jp / "*.xml")) if jp.is_dir() else [str(jp)]
        parsed = _parse_junit(junit_paths, started)
        os.unlink(junit_tmp)

        run = {"module": m.path or ".", "command": cmd, "exit_code": exit_code, "seconds": elapsed,
               "tests_requested": ids, "parsed_results": len(parsed)}
        if not parsed or exit_code != 0:
            run["output_tail"] = "\n".join(output.strip().splitlines()[-60:])
        module_runs.append(run)
        if parsed:
            results += parsed
        else:
            status = "passed" if exit_code == 0 else "failed"
            results += [{"test": i, "status": status, "message": None if exit_code == 0 else "See output_tail",
                         "seconds": None} for i in ids]

    count = lambda st: sum(1 for r in results if r["status"] == st)
    return {
        "sandbox": sandbox,
        "summary": {"requested": len(test_ids), "passed": count("passed"), "failed": count("failed"),
                    "errors": count("error"), "skipped": count("skipped"),
                    "seconds": round(time.time() - started_all, 2)},
        "results": results,
        "modules": module_runs,
        "unresolved": unresolved,
        "hint": ("Some test ids had no module with a test_command. Pass --module <path> or add it to impact.yaml."
                 if unresolved else None),
    }
