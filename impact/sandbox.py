"""Temporary git worktree where the agent may write and run generated tests.
The developer's own checkout is never modified."""
from __future__ import annotations

import shutil
from pathlib import Path

from .gitutil import git


def worktree_path(root: Path) -> Path:
    return root / ".impact" / "worktree"


def _gitignore_note(root: Path) -> str | None:
    gi = root / ".gitignore"
    text = gi.read_text() if gi.exists() else ""
    if ".impact" not in text or "impact-reports" not in text:
        return "Add `.impact/` and `impact-reports/` to .gitignore so sandbox files and reports are never committed."
    return None


def create(root: Path, branch: str) -> dict:
    path = worktree_path(root)
    if path.exists():
        cleanup(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "--detach", str(path), branch)
    return {"sandbox_path": str(path), "branch": branch,
            "rules": "Write generated tests only inside sandbox_path. Run them with `impact run-tests <ids> --sandbox`.",
            "note": _gitignore_note(root)}


def cleanup(root: Path) -> dict:
    path = worktree_path(root)
    existed = path.exists()
    git(root, "worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    git(root, "worktree", "prune", check=False)
    return {"removed": existed, "sandbox_path": str(path)}


def changed_files(root: Path) -> list[str]:
    """Files the agent created or edited in the sandbox (for the report)."""
    path = worktree_path(root)
    if not path.exists():
        return []
    out = git(path, "status", "--porcelain", "--untracked-files=all", check=False)
    return [ln[3:] for ln in out.splitlines() if ln.strip()]
