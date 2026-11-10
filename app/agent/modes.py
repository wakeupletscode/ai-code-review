"""Pipeline variants. They exist so the evaluation can measure what each component adds."""
import ast
import io
import re
import tokenize
from pathlib import PurePath

MODES = {
    "full":        {"static": True,  "rag": True,  "llm": True,  "tests": True},
    "llm_static":  {"static": True,  "rag": False, "llm": True,  "tests": False},
    "llm_only":    {"static": False, "rag": False, "llm": True,  "tests": False},
    "static_only": {"static": True,  "rag": False, "llm": False, "tests": False},
}


def flags(state) -> dict:
    return MODES.get(state.get("mode") or "full", MODES["full"])


_JS = re.compile(r"\b(function|const|let|var)\b|=>|console\.|require\(|module\.exports|===|!==")
_PY = re.compile(r"^\s*(def |class |import |from \w+ import )|:\s*$|\bself\b|\bNone\b|\bTrue\b|\belif\b",
                 re.MULTILINE)
_EXT = {".py": "python", ".pyw": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".jsx": "javascript"}
_SKIP = {tokenize.STRING, tokenize.COMMENT, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}


def _without_strings(code: str) -> str:
    """Python source with strings and comments removed, so 'function' inside a string is not counted."""
    try:
        return " ".join(t.string for t in tokenize.generate_tokens(io.StringIO(code).readline) if t.type not in _SKIP)
    except (tokenize.TokenError, SyntaxError):
        return code


def detect_language(code: str, file_name: str | None = None) -> str:
    """1) a file extension decides; 2) code that Python can parse is Python unless JavaScript-only syntax
    clearly dominates outside strings (e.g. `console.log(x)` parses as Python too); 3) code that does not parse
    as Python is JavaScript if it has any JavaScript markers; 4) otherwise compare marker counts."""
    if file_name:
        lang = _EXT.get(PurePath(file_name).suffix.lower())
        if lang:
            return lang
    try:
        ast.parse(code)
        parses = True
    except (SyntaxError, ValueError):
        parses = False
    if parses:
        bare = _without_strings(code)
        return "javascript" if len(_JS.findall(bare)) > len(_PY.findall(bare)) else "python"
    js, py = len(_JS.findall(code)), len(_PY.findall(code))
    if js:
        return "javascript" if js >= py or not py else "python"
    return "python"
