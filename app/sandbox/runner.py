"""Run generated tests against submitted code in an isolated temp directory.

Python tests run with pytest, JavaScript tests with Node's built-in test runner.

SANDBOX_MODE=subprocess : hardened local run. Temp dir, run timeout, per-test timeout, secrets scrubbed from the
                          environment, (POSIX) resource limits, and a guard that applies to the SUBMITTED code too:
                          - Python: an audit hook blocks network, new processes, ctypes, and file access
                            outside the sandbox folder (reads of the Python install are allowed).
                          - JavaScript: Node's permission model (file system limited to the sandbox folder,
                            no child processes / workers / addons) plus a network guard.
                          Good defence in depth, but not a hard security boundary.
SANDBOX_MODE=docker     : the same inside a container with no network, read-only root, dropped capabilities,
                          memory/CPU/pid limits. Recommended for untrusted code. If Docker is not available the
                          run fails (it never silently falls back to subprocess mode).
"""
import functools
import os
import re
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from app.core.config import settings

BLOCKED_PY = re.compile(
    r"\b(subprocess|socket|shutil|requests|urllib|http\.client|ctypes|os\.system|"
    r"os\.remove|os\.rmdir|os\.unlink|rmtree|__import__)\b"
)
BLOCKED_JS = re.compile(
    r"(child_process|require\(\s*['\"](?:node:)?(?:net|http|https|http2|dgram|dns|tls|worker_threads|cluster|vm)['\"]\s*\)"
    r"|process\.exit|process\.kill|\beval\(|new Function|\bfetch\(|import\s*\("
    r"|fs\.(?:rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync|writeFile|writeFileSync|appendFile|appendFileSync))"
)
_NAME_RE = re.compile(r"test_(f\d+)_", re.IGNORECASE)
CATEGORIES = ["normal", "boundary", "empty", "invalid_type", "large_input", "malicious"]
_SECRET_VAR = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE)

# Loaded by pytest before the code under test is imported, so the guard covers the submitted code as well.
# 1) An audit hook blocks network, new processes, native code and file access outside the sandbox folder.
# 2) Every Python test gets its own time limit (works on Windows too, unlike signal.alarm).
CONFTEST = '''
import os
import site
import sys
import threading

import pytest

_LIMIT = float(os.environ.get("PER_TEST_TIMEOUT", "5"))


def _forms(p):
    return {os.path.normcase(os.path.abspath(p)), os.path.normcase(os.path.realpath(p))}


_BOX = _forms(os.getcwd())
_NULL = _forms(os.devnull)
_READ_ROOTS = set(_BOX)
for _p in [sys.prefix, sys.base_prefix, sys.exec_prefix, *sys.path, *site.getsitepackages(),
           site.getusersitepackages()]:
    if _p and os.path.isabs(_p):
        _READ_ROOTS |= _forms(_p)

_DENY = ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "subprocess.Popen", "os.system",
         "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.fork", "os.forkpty", "os.kill", "pty.spawn",
         "_winapi.CreateProcess", "ctypes.dlopen", "ctypes.dlsym", "winreg.")
_MUTATE = ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate", "os.symlink",
           "os.link", "os.utime", "shutil.rmtree", "shutil.copyfile", "shutil.move")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _inside(path, roots):
    if isinstance(path, int):
        return True  # an already-open file descriptor
    try:
        forms = _forms(os.fsdecode(os.fspath(path)))
    except (TypeError, ValueError):
        return True
    if forms & _NULL:
        return True
    return any(f == r or f.startswith(r.rstrip(os.sep) + os.sep) for f in forms for r in roots)


def _deny(what):
    raise PermissionError(f"{what} is blocked by the sandbox")


def _guard(event, args):
    if event.startswith(_DENY):
        _deny(event)
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \\
                  (mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS)
        if path is not None and not _inside(path, _BOX if writing else _READ_ROOTS):
            _deny(f"{'writing' if writing else 'reading'} {path!r}")
    elif event in ("os.listdir", "os.scandir") and args and args[0] is not None:
        if not _inside(args[0], _READ_ROOTS):
            _deny(f"listing {args[0]!r}")
    elif event in _MUTATE:
        for a in args:
            if isinstance(a, (str, bytes, os.PathLike)) and not _inside(a, _BOX):
                _deny(f"{event} on {a!r}")


sys.addaudithook(_guard)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    original = item.runtest

    def guarded():
        box = {}

        def run():
            try:
                original()
            except BaseException as exc:  # noqa: BLE001
                box["exc"] = exc

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(_LIMIT)
        if worker.is_alive():
            raise TimeoutError("per-test timeout after %ss" % _LIMIT)
        if "exc" in box:
            raise box["exc"]

    item.runtest = guarded
'''

# Preloaded before the JavaScript tests (and the code under test). Node's permission model already limits the
# file system and blocks child processes; this closes network access as well.
JS_GUARD = """
const blocked = (what) => () => { const e = new Error(`${what} is blocked by the sandbox`); e.code = 'ERR_SANDBOX'; throw e; };
const net = require('node:net');
net.Socket.prototype.connect = blocked('network access');
net.Server.prototype.listen = blocked('opening a server');
require('node:dgram').createSocket = blocked('network access');
const dns = require('node:dns');
dns.lookup = blocked('DNS lookup');
dns.resolve = blocked('DNS lookup');
globalThis.fetch = blocked('fetch');
"""


@functools.lru_cache(maxsize=4)
def _node_major(binary: str) -> int:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False, timeout=10).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def is_blocked(test_code: str, language: str = "python"):
    """Return the offending text if the generated tests use a disallowed module/call, else None."""
    m = (BLOCKED_JS if language == "javascript" else BLOCKED_PY).search(test_code)
    return m.group(0) if m else None


def count_tests(test_code: str, language: str = "python") -> int:
    pattern = r"\btest\s*\(" if language == "javascript" else r"^\s*(?:async\s+)?def\s+test_"
    return len(re.findall(pattern, test_code, re.MULTILINE))


def _finding_id(test_name: str):
    m = _NAME_RE.search(test_name)
    return m.group(1).lower() if m else None


def _category(test_name: str) -> str:
    """Read the category from the name pattern test_<id>_<category>_<description>.
    The category must come right after the finding id, so a word like 'empty' inside a description
    cannot override it."""
    low = test_name.lower()
    m = re.match(r"test_f\d+_(\w+?)(?=_|$)", low)
    if not m:
        return "normal"
    rest = low[m.end(1) - len(m.group(1)):]
    for c in sorted(CATEGORIES, key=len, reverse=True):
        if rest.startswith(c + "_") or rest == c:
            return c
    return "normal"


def _sandbox_row(status: str, msg: str) -> list:
    return [{"name": "sandbox", "finding_id": None, "category": "normal", "status": status, "message": msg}]


def _error(msg: str) -> list:
    return _sandbox_row("error", msg)


def _clean_env() -> dict:
    """Copy of the environment without anything that looks like a secret (API keys, tokens, passwords)."""
    return {k: v for k, v in os.environ.items() if not _SECRET_VAR.search(k)}


def _limits(language: str):  # POSIX only
    def apply():
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (settings.sandbox_timeout, settings.sandbox_timeout))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 << 20, 10 << 20))
        if language == "python":  # V8 reserves a lot of address space, so no RLIMIT_AS for Node
            resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    return apply


_PY_FRAME = re.compile(r"^(\S+?\.py):\d+: (\w[\w.]*)", re.MULTILINE)
_JS_FRAME = re.compile(r"\(?((?:[A-Za-z]:)?[^\s()]+?\.js):\d+:\d+\)?")


def classify_failure(text: str, language: str) -> str:
    """What does a failing test actually prove?

    assertion        - the code returned/raised something other than the correct behaviour  -> valid evidence
    target_exception - the code under test crashed inside its own source                    -> valid evidence
    test_error       - the generated test itself is broken (wrong call, missing name, ...)  -> no evidence
    sandbox_blocked  - something tried to do what the sandbox forbids                       -> no evidence
    """
    if "blocked by the sandbox" in text or "ERR_ACCESS_DENIED" in text or "ERR_SANDBOX" in text:
        return "sandbox_blocked"
    if language == "javascript":
        if "ERR_ASSERTION" in text or "AssertionError" in text:
            return "assertion"
        cause = text.split("cause:", 1)[-1]
        for path in _JS_FRAME.findall(cause):
            name = Path(path.replace("\\", "/")).name
            if name in ("target.js", "test_target.js"):
                return "target_exception" if name == "target.js" else "test_error"
        return "test_error"
    frames = [(Path(f).name, exc) for f, exc in _PY_FRAME.findall(text)]
    frames = [(f, exc) for f, exc in frames if f in ("target.py", "test_target.py")]
    if not frames:
        return "assertion" if text.lstrip().startswith(("assert", "AssertionError", "Failed: DID NOT RAISE")) \
            else "test_error"
    where, exc = frames[-1]  # the deepest frame of our own files is where it went wrong
    if where == "target.py":
        return "target_exception"
    return "assertion" if exc in ("AssertionError", "Failed") else "test_error"


def _parse_junit(path: Path, language: str = "python") -> list:
    results = []
    root = ET.parse(path).getroot()
    for tc in root.findall(".//testcase"):
        name = tc.get("name", "")
        failure, error = tc.find("failure"), tc.find("error")
        node = failure if failure is not None else error
        evidence = ""
        if node is None:
            status, msg = "passed", ""
        else:
            status, msg = ("failed" if failure is not None else "error"), node.get("message") or ""
            evidence = classify_failure(f"{msg}\n{node.text or ''}", language)
        if status != "passed" and ("per-test timeout" in msg or "timed out" in msg.lower()):
            status, evidence = "timeout", "timeout"  # inconclusive: the test hung, it neither confirms nor clears
        results.append({"name": name, "finding_id": _finding_id(name), "category": _category(name),
                        "status": status, "evidence": evidence, "message": msg[:300]})
    return results


def _ensure_exports(code: str) -> str:
    """Make top-level functions importable from the test file if the code has no exports."""
    if "module.exports" in code or re.search(r"\bexports\.", code):
        return code
    names = re.findall(r"^\s*(?:async\s+)?function\s+(\w+)", code, re.MULTILINE)
    names += re.findall(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|function|\w+\s*=>)", code, re.MULTILINE)
    names = list(dict.fromkeys(names))
    return code + (f"\nmodule.exports = {{ {', '.join(names)} }};\n" if names else "")


def _command(language: str, tmp: str, container_name: str) -> list:
    docker = settings.sandbox_mode == "docker"
    if language == "javascript":
        # The test file is run directly (not via --test), so everything stays in one guarded process.
        node = "node" if docker else settings.node_binary
        inner = [node]
        if not docker:
            major = _node_major(settings.node_binary)
            if major >= 22:
                inner += ["--permission", f"--allow-fs-read={tmp}", f"--allow-fs-write={tmp}"]
            elif major >= 20:
                inner += ["--experimental-permission", f"--allow-fs-read={tmp}", f"--allow-fs-write={tmp}"]
        inner += ["--require", "./sandbox_guard.js", "--test-reporter=junit",
                  "--test-reporter-destination=report.xml", "test_target.js"]
    else:
        inner = ["python" if docker else sys.executable, "-m", "pytest", "-q", "--junitxml=report.xml",
                 "-p", "no:cacheprovider", "test_target.py"]
    if not docker:
        return inner
    return ["docker", "run", "--rm", "--name", container_name, "--network", "none", "--read-only",
            "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
            "-e", f"PER_TEST_TIMEOUT={settings.per_test_timeout}",
            "-v", f"{tmp}:/work", "-w", "/work", settings.sandbox_image, *inner]


def run_tests(code: str, test_code: str, language: str = "python", check_blocked: bool = True) -> list:
    """Run generated tests against `code`. Returns one dict per test, or a single 'sandbox' row on failure."""
    if check_blocked:
        bad = is_blocked(test_code, language)
        if bad:
            return _error(f"Generated tests were blocked: they use a disallowed module or call ({bad.strip()[:40]}).")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if language == "javascript":
            (tmp_path / "target.js").write_text(_ensure_exports(code), encoding="utf-8")
            (tmp_path / "test_target.js").write_text(test_code, encoding="utf-8")
            (tmp_path / "sandbox_guard.js").write_text(JS_GUARD, encoding="utf-8")
        else:
            (tmp_path / "target.py").write_text(code, encoding="utf-8")
            (tmp_path / "test_target.py").write_text(test_code, encoding="utf-8")
            (tmp_path / "conftest.py").write_text(CONFTEST, encoding="utf-8")

        container = f"cr-sandbox-{uuid.uuid4().hex[:8]}"
        cmd = _command(language, tmp, container)
        kwargs = {}
        if settings.sandbox_mode != "docker":
            env = _clean_env()
            env["PER_TEST_TIMEOUT"] = str(settings.per_test_timeout)
            for var in ("TMP", "TEMP", "TMPDIR"):
                env[var] = tmp  # temp files of the test run stay inside the sandbox folder
            if language == "python":
                env["PYTHONPATH"] = tmp
                env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                env["PYTHONDONTWRITEBYTECODE"] = "1"
            kwargs["env"] = env
            if os.name == "posix":
                kwargs["preexec_fn"] = _limits(language)

        try:
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True,
                                  check=False, timeout=settings.sandbox_timeout, **kwargs)
        except subprocess.TimeoutExpired:
            if settings.sandbox_mode == "docker":
                subprocess.run(["docker", "kill", container], capture_output=True, check=False)
            return _sandbox_row("timeout", f"Test run exceeded {settings.sandbox_timeout}s and was killed.")
        except FileNotFoundError as e:
            return _error(f"Sandbox could not start: {e}")

        report = tmp_path / "report.xml"
        if not report.exists():
            return _error(f"No report written (exit {proc.returncode}): {(proc.stdout + proc.stderr)[-300:]}")
        try:
            results = _parse_junit(report, language)
        except ET.ParseError as e:
            return _error(f"Could not parse report: {e}")
        return results or _error("Report contained no test cases.")
