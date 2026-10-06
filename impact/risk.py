"""Transparent, rule-based risk score (0 to 100). Every point comes with a human-readable reason,
so the agent and reviewers can see why something is flagged."""
from __future__ import annotations


def level_for(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def score_symbol(status: str, signature_changed: bool, direct_callers: int, indirect_callers: int,
                 tests: int, history: dict, ambiguous_definitions: int) -> dict:
    score, reasons = 0, []

    if status == "deleted":
        score += 35; reasons.append("Symbol was deleted; any remaining caller will break.")
    elif signature_changed:
        score += 25; reasons.append("Signature changed (parameters, return type or modifiers).")
    elif status == "modified":
        score += 10; reasons.append("Body changed.")
    else:
        score += 5; reasons.append("New symbol.")

    if direct_callers:
        pts = min(20, round(direct_callers * 2.5))
        score += pts; reasons.append(f"{direct_callers} direct caller(s).")
    if indirect_callers:
        pts = min(10, indirect_callers)
        score += pts; reasons.append(f"{indirect_callers} indirect caller(s).")

    if tests == 0 and status != "added":
        score += 20; reasons.append("No tests reach this code.")

    bugfixes = history.get("bugfix_commits", 0)
    if bugfixes:
        score += min(20, bugfixes * 4)
        reasons.append(f"File had {bugfixes} bug-fix commit(s) in the last {history.get('days')} days.")
    commits = history.get("commits", 0)
    if commits >= 5:
        score += min(10, commits // 2)
        reasons.append(f"High churn: {commits} commits in the last {history.get('days')} days.")

    if ambiguous_definitions > 1:
        reasons.append(f"{ambiguous_definitions} symbols share this name; caller matches may include false positives.")

    score = min(100, score)
    return {"score": score, "level": level_for(score), "reasons": reasons}


def overall(symbol_risks: list[dict], schema_changes: list[dict]) -> dict:
    top = max((r["score"] for r in symbol_risks), default=0)
    reasons = []
    if top:
        reasons.append(f"Highest symbol risk: {top}.")
    severe = [c for c in schema_changes if c["severity"] == "high"]
    medium = [c for c in schema_changes if c["severity"] == "medium"]
    if severe:
        top = max(top, 70); reasons.append(f"{len(severe)} breaking schema change(s) (drop or rename).")
    elif medium:
        top = max(top, 40); reasons.append(f"{len(medium)} schema change(s) that need care.")
    return {"score": top, "level": level_for(top), "reasons": reasons}
