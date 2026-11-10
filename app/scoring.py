"""Deterministic scoring helpers shared by the aggregate node and the fix verifier."""

ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
# Points deducted per finding. Fixed weights make the score reproducible and easy to explain.
PENALTY = {"critical": 20, "high": 12, "medium": 6, "low": 2, "info": 0}


def score_breakdown(findings: list) -> tuple:
    counts = {s: 0 for s in PENALTY}
    for f in findings:
        sev = f.get("severity", "medium")
        if sev in counts:
            counts[sev] += 1
        else:
            counts["medium"] += 1  # treat unknown severities as medium
    score = max(0, 100 - sum(counts[s] * PENALTY[s] for s in PENALTY))
    return score, counts


# Errors that mean the GENERATED TEST is broken, not that the reviewed code is wrong.
# Only used for results that carry no "evidence" classification (for example older stored results).
TEST_BUG_MARKERS = ("NameError", "ImportError", "ModuleNotFoundError", "SyntaxError", "AttributeError: module 'target'")
# A failing test proves a defect only when it failed on an assertion or crashed inside the reviewed code.
VALID_EVIDENCE = ("assertion", "target_exception")


def is_evidence(r: dict) -> bool:
    """True if this test result is real evidence that its finding is a defect."""
    if not r.get("finding_id") or r.get("status") not in ("failed", "error"):
        return False
    evidence = r.get("evidence")
    if not evidence:  # unclassified result: fall back to the known test-bug markers
        return not any(marker in r.get("message", "") for marker in TEST_BUG_MARKERS)
    return evidence in VALID_EVIDENCE


def failing_tests_by_finding(results: list) -> dict:
    """finding id -> names of its tests that prove the defect (assertion failure or crash in the reviewed code).
    Tests that broke because of their own mistakes, the sandbox or a timeout prove nothing and are excluded."""
    out = {}
    for r in results:
        if is_evidence(r):
            out.setdefault(r["finding_id"], []).append(r["name"])
    return out
