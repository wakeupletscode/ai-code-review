"""Quality gate for generated tests.

Being syntactically valid is not enough. Before a generated test file is run, it must:
  - contain at least one test,
  - link every test to a reported finding through its name (test_<finding_id>_<category>_...),
  - call the code under test through `target`,
  - only call functions/classes that exist in the submitted code,
  - check something in every test (an assert, pytest.raises / assert.throws, ...).
A rejected file goes back to the LLM once with the exact reason.
"""
import ast
import re

_JS_TEST = re.compile(r"\btest\s*\(\s*(['\"`])(.+?)\1")
_JS_ASSERT = re.compile(r"\bassert(?:\.\w+)?\s*\(")
_JS_DEFS = (r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)",
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=",
            r"^\s*(?:export\s+)?class\s+(\w+)",
            r"(?:module\.)?exports\.(\w+)\s*=")
_LINK = re.compile(r"^test_(f\d+)_", re.IGNORECASE)


def _py_names(code: str) -> set:
    """Top-level names a test may use as target.<name>."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {t.id for t in targets if isinstance(t, ast.Name)}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
    return names


def _js_names(code: str) -> set:
    names = set()
    for pattern in _JS_DEFS:
        names |= set(re.findall(pattern, code, re.MULTILINE))
    m = re.search(r"module\.exports\s*=\s*\{([^}]*)\}", code)
    if m:
        names |= set(re.findall(r"(\w+)\s*(?:[:,]|$)", m.group(1)))
    return names


def _has_check(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "pytest" \
                and node.attr in ("raises", "fail", "warns", "approx"):
            return True
    return False


def _check_python(test_code: str, code: str) -> tuple:
    tree = ast.parse(test_code)
    tests = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith("test_")]
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name) and n.value.id == "target"}
    touches = any(isinstance(n, ast.Name) and n.id == "target" for n in ast.walk(tree))
    for n in ast.walk(tree):  # also accept `import target` or `from target import name`
        if isinstance(n, ast.Import) and any(a.name == "target" or a.asname == "target" for a in n.names):
            touches = True
        elif isinstance(n, ast.ImportFrom) and n.module == "target":
            used |= {a.name for a in n.names if a.name != "*"}
            touches = True
    used = {u for u in used if not u.startswith("__")}  # target.__file__ etc. always exist
    unchecked = [t.name for t in tests if not _has_check(t)] if used else []
    return [t.name for t in tests], used, touches, unchecked, _py_names(code)


def _check_javascript(test_code: str, code: str) -> tuple:
    matches = list(_JS_TEST.finditer(test_code))
    names = [m.group(2) for m in matches]
    used = {u for u in re.findall(r"\btarget\.(\w+)", test_code) if not u.startswith("__")}
    touches = bool(re.search(r"\btarget\b", test_code.split("require('./target.js')", 1)[-1]))
    # Each test's body runs from its test( call to the next one.
    ends = [m.start() for m in matches[1:]] + [len(test_code)]
    unchecked = [m.group(2) for m, end in zip(matches, ends, strict=True)
                 if not _JS_ASSERT.search(test_code, m.end(), end)]
    return names, used, touches, unchecked, _js_names(code)


def check_tests(test_code: str, code: str, language: str, finding_ids: list) -> tuple:
    """Return (problem or "", info). `problem` is a short instruction for the LLM when the file must be rewritten."""
    check = _check_javascript if language == "javascript" else _check_python
    names, used, touches, unchecked, defined = check(test_code, code)
    known = {f.lower() for f in finding_ids}
    linked = [n for n in names if (m := _LINK.match(n)) and m.group(1).lower() in known]
    info = {"tests": len(names), "linked": len(linked),
            "findings_covered": len({_LINK.match(n).group(1).lower() for n in linked}),
            "findings_total": len(known)}

    if not names:
        return "it contains no test functions", info
    if not linked:
        return (f"no test is linked to a finding; name every test test_<finding_id>_<category>_<description> "
                f"using these ids: {', '.join(sorted(known))}"), info
    if not touches:
        return "no test uses the code under test; call it as target.<function>(...)", info
    missing = sorted(used - defined) if defined else []
    if missing:
        return (f"it calls target.{missing[0]}, which does not exist in the code; "
                f"available names: {', '.join(sorted(defined)) or 'none'}"), info
    if unchecked:
        return f"test {unchecked[0]} checks nothing; every test needs an assertion or an expected exception", info
    return "", info
