"""Thin wrappers around git. Everything here is read-only except worktree handling in sandbox.py."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

BUGFIX_PATTERN = r"fix|bug|hotfix|revert|regression|patch|incident"


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel").strip())


def merge_base(root: Path, base: str, branch: str) -> str:
    return git(root, "merge-base", base, branch).strip()


def show_file(root: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def tracked_files(root: Path) -> list[str]:
    return [f for f in git(root, "ls-files").splitlines() if f]


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    new_ranges: list[tuple[int, int]] = field(default_factory=list)   # inclusive line ranges on branch side
    old_ranges: list[tuple[int, int]] = field(default_factory=list)   # inclusive line ranges on base side
    added_text: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def status(self) -> str:
        if self.old_path is None:
            return "added"
        if self.new_path is None:
            return "deleted"
        if self.old_path != self.new_path:
            return "renamed"
        return "modified"


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def diff(root: Path, base_ref: str, branch: str) -> list[FileDiff]:
    out = git(root, "diff", "--unified=0", "--find-renames", "--no-color", base_ref, branch)
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    for line in out.splitlines():
        if line.startswith("diff --git "):
            cur = FileDiff(old_path=None, new_path=None)
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("--- "):
            p = line[4:]
            cur.old_path = None if p == "/dev/null" else p[2:]
        elif line.startswith("+++ "):
            p = line[4:]
            cur.new_path = None if p == "/dev/null" else p[2:]
        elif line.startswith("@@"):
            m = HUNK_RE.match(line)
            if not m:
                continue
            a, b = int(m.group(1)), int(m.group(2) or 1)
            c, d = int(m.group(3)), int(m.group(4) or 1)
            if b > 0:
                cur.old_ranges.append((a, a + b - 1))
            if d > 0:
                cur.new_ranges.append((c, c + d - 1))
            else:
                # pure deletion: mark the surrounding point so the enclosing symbol counts as changed
                cur.new_ranges.append((max(c, 1), c + 1))
        elif line.startswith("+") and not line.startswith("+++"):
            cur.added_text.append(line[1:])
    # renames with no content change still produce a FileDiff with no hunks, keep them
    return files


def file_history(root: Path, path: str, days: int) -> dict:
    commits = git(root, "log", f"--since={days}.days", "--format=%h", "--", path, check=False).split()
    bugfix_lines = git(root, "log", f"--since={days}.days", "-i", "-E", f"--grep={BUGFIX_PATTERN}",
                       "--format=%h|%ad|%an|%s", "--date=short", "--", path, check=False).splitlines()
    bugfixes = []
    for ln in bugfix_lines:
        parts = ln.split("|", 3)
        if len(parts) == 4:
            bugfixes.append({"commit": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3]})
    return {"file": path, "days": days, "commits": len(commits),
            "bugfix_commits": len(bugfixes), "bugfixes": bugfixes[:20]}
