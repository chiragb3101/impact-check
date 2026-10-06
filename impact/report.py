"""`impact report`: validates findings JSON against the schema and renders a self-contained HTML file."""
from __future__ import annotations

import json
import re
import time
from importlib import resources
from pathlib import Path

from jinja2 import Environment, select_autoescape
from jsonschema import Draft202012Validator

from . import __version__

VERDICT_ORDER = {"breaks": 0, "unclear": 1, "safe": 2}
STATUS_ORDER = {"failed": 0, "error": 1, "skipped": 2, "passed": 3}


def load_schema() -> dict:
    return json.loads(resources.files("impact").joinpath("schema/findings.schema.json").read_text())


def validate(findings: dict) -> list[dict]:
    v = Draft202012Validator(load_schema())
    return [{"path": "/".join(str(p) for p in e.absolute_path) or "(root)", "error": e.message}
            for e in sorted(v.iter_errors(findings), key=lambda e: list(e.absolute_path))]


def render(root: Path, findings_path: Path, out_dir: Path | None = None) -> dict:
    try:
        findings = json.loads(findings_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "errors": [{"path": str(findings_path), "error": f"Could not read JSON: {e}"}]}

    errors = validate(findings)
    if errors:
        return {"ok": False, "errors": errors,
                "hint": "Fix these fields in the findings file and run `impact report` again. Schema: `impact schema`."}

    findings["callers"] = sorted(findings["callers"], key=lambda c: VERDICT_ORDER[c["verdict"]])
    findings["tests"]["results"] = sorted(findings["tests"]["results"], key=lambda t: STATUS_ORDER[t["status"]])

    env = Environment(autoescape=select_autoescape(["html", "j2"]))
    template = env.from_string(resources.files("impact").joinpath("templates/report.html.j2").read_text())
    html = template.render(f=findings, version=__version__, generated_at=time.strftime("%Y-%m-%d %H:%M"))

    out_dir = out_dir or (root / "impact-reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_branch = re.sub(r"[^\w.-]+", "-", findings["branch"]).strip("-")
    out = out_dir / f"{safe_branch}-{time.strftime('%Y%m%d-%H%M%S')}.html"
    out.write_text(html, encoding="utf-8")
    return {"ok": True, "report_path": str(out), "risk": findings["risk"]["level"]}
