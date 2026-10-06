"""Loads impact.yaml: which folders are which language and how to run their tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_IGNORE = ["node_modules", "build", "dist", "target", ".venv", "venv", "__pycache__", ".impact", "impact-reports"]

# How test ids are joined into one command, per language
TEST_SEPARATORS = {"java": ",", "python": " ", "javascript": " ", "typescript": " "}


@dataclass
class Module:
    path: str                      # repo-relative folder, "" means whole repo
    language: str = "auto"         # java | python | javascript | typescript | sql | auto
    test_command: str | None = None  # template with {tests} and optional {junit}
    junit: str | None = None       # junit xml file or folder written by the runner (relative to module)
    env: dict = field(default_factory=dict)

    def contains(self, rel_path: str) -> bool:
        if not self.path:
            return True
        return rel_path == self.path or rel_path.startswith(self.path.rstrip("/") + "/")

    @property
    def test_separator(self) -> str:
        return TEST_SEPARATORS.get(self.language, " ")


@dataclass
class Config:
    root: Path
    modules: list[Module]
    ignore: list[str]
    history_days: int = 180

    def module_for(self, rel_path: str) -> Module | None:
        matches = [m for m in self.modules if m.contains(rel_path)]
        if not matches:
            return None
        return max(matches, key=lambda m: len(m.path))

    def module_by_path(self, path: str) -> Module | None:
        for m in self.modules:
            if m.path.rstrip("/") == path.rstrip("/"):
                return m
        return None

    def is_ignored(self, rel_path: str) -> bool:
        parts = rel_path.split("/")
        return any(p in self.ignore for p in parts)


def load_config(root: Path, config_path: Path | None = None) -> Config:
    path = config_path or (root / "impact.yaml")
    if not path.exists():
        return Config(root=root, modules=[Module(path="")], ignore=DEFAULT_IGNORE)

    raw = yaml.safe_load(path.read_text()) or {}
    modules = []
    for m in raw.get("modules", []):
        modules.append(Module(
            path=str(m.get("path", "")).strip("/"),
            language=m.get("language", "auto"),
            test_command=m.get("test_command"),
            junit=m.get("junit"),
            env=m.get("env", {}) or {},
        ))
    if not modules:
        modules = [Module(path="")]
    ignore = DEFAULT_IGNORE + list(raw.get("ignore", []))
    return Config(root=root, modules=modules, ignore=ignore,
                  history_days=int(raw.get("history_days", 180)))
