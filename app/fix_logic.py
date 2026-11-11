"""Fix verification & Proof-of-Bug counterfactual mutation engine.

A fix is VERIFIED only when all four checks hold:
  1. At least one previously failing evidence test now passes.
  2. None of the originally failing tests still fails.
  3. No previously passing test regressed.
  4. Static analysis issues did not increase on the patched code.

When verified, a counterfactual mutation (disabling the newly added guard or reintroducing the defective hunk)
is tested against the same suite: if the reproduction test fails again on the mutant, `proof_of_bug` is True.
"""
import difflib
import re

from app.scoring import failing_tests_by_finding, is_evidence, score_breakdown
from app.tools.syntax import syntax_error

_BAD = ("failed", "error")
_PY_IF = re.compile(r"^(\s*)if\s+.+:\s*(?:#.*)?$")
_JS_IF = re.compile(r"^(\s*)if\s*\([^)]+\)")


def _tests(results: list) -> dict:
    return {r["name"]: r["status"] for r in results if r["name"] != "sandbox"}


def _counts(results: list) -> dict:
    c = {"passed": 0, "failed": 0, "error": 0, "timeout": 0}
    for status in _tests(results).values():
        c[status] += 1
    return c


def counterfactual_mutant(original_code: str, fixed_code: str, language: str = "python") -> str:
    """Create a counterfactual mutant from `fixed_code` that disables the patch's guard or reverts the patch hunk."""
    orig_lines = original_code.splitlines()
    fixed_lines = fixed_code.splitlines()
    opcodes = difflib.SequenceMatcher(None, orig_lines, fixed_lines).get_opcodes()
    changed_indices = [j for tag, _i1, _i2, j1, j2 in opcodes if tag in ("insert", "replace") for j in range(j1, j2)]

    # 1) Try disabling newly added/modified `if` guards in the patched lines (`if cond:` -> `if False:`)
    mutated = list(fixed_lines)
    any_guard = False
    for idx in changed_indices:
        line = mutated[idx]
        if language == "javascript":
            m = _JS_IF.match(line)
            new_line = _JS_IF.sub(r"\1if (false)", line, count=1) if m else None
        else:
            m = _PY_IF.match(line)
            new_line = f"{m.group(1)}if False:" if m else None
        if new_line and new_line != line:
            mutated[idx] = new_line
            any_guard = True
    if any_guard:
        cand = "\n".join(mutated) + "\n"
        if syntax_error(cand, language) is None:
            return cand

    # 2) Otherwise revert the first changed hunk back to the original defective lines
    for tag, i1, i2, j1, j2 in opcodes:
        if tag in ("replace", "insert", "delete"):
            cand = "\n".join([*fixed_lines[:j1], *orig_lines[i1:i2], *fixed_lines[j2:]]) + "\n"
            if syntax_error(cand, language) is None:
                return cand
    return original_code


def evaluate_fix(before: list, after: list, findings: list, static_before: int = 0, static_after: int = 0,
                 counterfactual: list | None = None) -> dict:
    """A fix is VERIFIED only if at least one failing evidence test now passes, none of the originally failing
    tests still fails, no passing test broke, and static analysis issues did not increase."""
    b, a = _tests(before), _tests(after)
    by_name = {r["name"]: r for r in before if r["name"] != "sandbox"}
    evidence_bad = {n for n, s in b.items() if s in _BAD and is_evidence(by_name[n])}

    fixed = [n for n in b if n in evidence_bad and a.get(n) == "passed"]
    still = [n for n, s in b.items() if s in _BAD and a.get(n) != "passed"]
    regress = [n for n, s in b.items() if s == "passed" and a.get(n) != "passed"]
    no_new_static = static_after <= static_before

    verified = bool(fixed) and not still and not regress and no_new_static
    killed = []
    if verified and counterfactual is not None:
        cf_by_name = {r["name"]: r for r in counterfactual if r["name"] != "sandbox"}
        killed = [n for n in fixed if n in cf_by_name and is_evidence(cf_by_name[n])]
    proof_of_bug = verified and bool(killed)

    resolved = [fid for fid, names in failing_tests_by_finding(before).items()
                if not regress and no_new_static and all(a.get(n) == "passed" for n in names)]
    score_before, _ = score_breakdown(findings)
    score_after, _ = score_breakdown([f for f in findings if f["id"] not in resolved])
    return {"verified": verified, "proof_of_bug": proof_of_bug, "counterfactual_killed": killed,
            "fixed_tests": fixed, "still_failing": still, "regressions": regress,
            "resolved_findings": resolved, "before": _counts(before), "after": _counts(after),
            "score_before": score_before, "score_after": score_after}
