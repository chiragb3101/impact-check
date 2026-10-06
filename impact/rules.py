"""Natural-language rules ("memory") the agent follows when preparing reports.

Rules are plain Markdown bullets, so people can also edit the files by hand. Three scopes:

  user   ~/.config/impact/rules.md   personal, applies in every repo (default)
  local  <repo>/.impact/rules.md     personal, this repo only (not committed)
  team   <repo>/impact-rules.md      shared with everyone on the repo (commit it)

A bullet may start with `[id]` and end with `<!-- by <author> on <date> -->`; both are optional,
so a hand-written `- Treat billing/ as high risk` works too.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from .gitutil import git

SCOPES = ("user", "local", "team")
# Later scopes win when rules conflict: personal rules for this repo override team defaults.
PRECEDENCE = ("team", "user", "local")

HEADERS = {
    "user": "# impact rules: personal (all repos)",
    "local": "# impact rules: personal (this repo only, not committed)",
    "team": "# impact rules: team (commit this file)",
}
INTRO = ("Write one rule per bullet in plain language. The impact-check agent reads these before every\n"
         "report and lists the ones it applied. Example:\n"
         "- Treat any change under billing/ as at least medium risk.\n")

LINE_RE = re.compile(r"^\s*[-*]\s+(?:\[(?P<id>[\w-]+)\]\s+)?(?P<text>.*?)\s*(?:<!--\s*(?P<meta>.*?)\s*-->)?\s*$")


def path_for(scope: str, root: Path) -> Path:
    if scope == "user":
        if os.environ.get("IMPACT_HOME"):
            return Path(os.environ["IMPACT_HOME"]) / "rules.md"
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "impact" / "rules.md"
    if scope == "local":
        return root / ".impact" / "rules.md"
    if scope == "team":
        return root / "impact-rules.md"
    raise ValueError(f"Unknown scope {scope!r}; use one of {', '.join(SCOPES)}")


def _parse(path: Path, scope: str) -> list[dict]:
    if not path.exists():
        return []
    rules, in_comment = [], False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "<!--" in line and "-->" not in line:
            in_comment = True
        if in_comment:
            in_comment = "-->" not in line
            continue
        m = LINE_RE.match(line)
        if not m or not m.group("text"):
            continue
        meta = m.group("meta") or ""
        author = re.search(r"by\s+(\S+)", meta)
        date = re.search(r"on\s+(\d{4}-\d{2}-\d{2})", meta)
        rules.append({"id": m.group("id") or f"{scope[0]}{n}", "scope": scope, "text": m.group("text"),
                      "author": author.group(1) if author else None, "added": date.group(1) if date else None,
                      "file": str(path), "line": n})
    return rules


def load(root: Path, scopes: tuple[str, ...] = PRECEDENCE) -> list[dict]:
    out = []
    for s in scopes:
        out += _parse(path_for(s, root), s)
    return out


def _author(root: Path) -> str:
    email = git(root, "config", "user.email", check=False).strip()
    return email or os.environ.get("USER", "unknown")


def _ensure_file(path: Path, scope: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{HEADERS[scope]}\n\n<!--\n{INTRO}-->\n\n", encoding="utf-8")


def add(root: Path, text: str, scope: str = "user") -> dict:
    text = " ".join(text.split())
    if len(text) < 5:
        return {"error": "Rule is too short. Write it as a full sentence."}
    path = path_for(scope, root)
    _ensure_file(path, scope)
    existing = {r["text"].lower() for r in _parse(path, scope)}
    if text.lower() in existing:
        return {"ok": True, "duplicate": True, "file": str(path), "rules": load(root)}
    rid = f"{scope[0]}-{secrets.token_hex(2)}"
    line = f"- [{rid}] {text}  <!-- by {_author(root)} on {time.strftime('%Y-%m-%d')} -->\n"
    body = path.read_text(encoding="utf-8")
    path.write_text(body + ("" if body.endswith("\n") else "\n") + line, encoding="utf-8")
    result = {"ok": True, "id": rid, "scope": scope, "file": str(path)}
    if scope == "team":
        result["note"] = "Commit impact-rules.md so teammates get this rule."
    return result


def remove(root: Path, rule_id: str) -> dict:
    for scope in SCOPES:
        path = path_for(scope, root)
        for r in _parse(path, scope):
            if r["id"] == rule_id:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                del lines[r["line"] - 1]
                path.write_text("".join(lines), encoding="utf-8")
                return {"ok": True, "removed": r}
    return {"error": f"No rule with id {rule_id}. Run `impact rules` to see ids."}


def summary(root: Path) -> dict:
    """What `impact rules` and `impact facts` show the agent."""
    rules = load(root)
    return {
        "rules": [{k: r[k] for k in ("id", "scope", "text", "author")} for r in rules],
        "files": {s: str(path_for(s, root)) for s in SCOPES},
        "precedence": "If rules conflict, local beats user beats team.",
        "instruction": ("Follow these rules while judging risk and writing the report. They can change emphasis, "
                        "severity and what to check, but never override test results or facts read from code. "
                        "List every rule that changed the report in findings.rules_applied."),
    }
