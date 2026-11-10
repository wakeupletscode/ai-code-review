import asyncio
import re

from app.agent.modes import flags
from app.agent.nodes.common import timed
from app.rag.store import retrieve

_CONSTRUCT_HINTS = [
    (r"/\s*len\(|\.length\b", "division by zero empty list boundary condition"),
    (r"\b(eval|exec|compile|Function)\b", "code injection arbitrary code execution eval exec"),
    (r"\b(SELECT|INSERT|UPDATE|DELETE)\b", "sql injection parameterized query cursor execute"),
    (r"\b(subprocess|os\.system|popen|child_process)\b", "command injection shell subprocess"),
    (r"\b(password|secret|api_key|token|passwd)\b", "hardcoded secret credential api key"),
    (r"def\s+\w+\([^)]*=\s*(\[\]|\{\}|set\(\))", "mutable default argument python list dict"),
    (r"\bexcept\s*:", "bare except exception handling maintainability"),
    (r"\bopen\s*\(", "resource leak file context manager with open"),
    (r"[^=!]==[^=]", "javascript loose equality eqeqeq type coercion"),
]


def _build_query(state: dict) -> str:
    code = state["code"]
    language = state.get("language", "python")
    static = state.get("static_findings", [])
    titles = " ".join(
        f"{f.get('category', '')} {f.get('title', '')} {f.get('explanation', '')}" for f in static
    )
    hints = " ".join(label for pat, label in _CONSTRUCT_HINTS if re.search(pat, code, re.IGNORECASE))
    funcs = " ".join(re.findall(r"\b(?:def|function|class)\s+(\w+)", code))
    return f"{language} {titles[:500]} {hints} {funcs[:200]} {code[:600]}".strip()


@timed("rag_retrieve")
async def rag_retrieve_node(state):
    if not flags(state)["rag"]:
        return {"rag_context": []}
    query = _build_query(state)
    chunks = await asyncio.to_thread(retrieve, query)
    return {"rag_context": chunks}
