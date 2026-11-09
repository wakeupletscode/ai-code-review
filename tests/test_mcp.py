import pytest

from app.mcp_tools.client import call_tool
from app.tools.analyzers import eslint_available


async def test_mcp_call_tool_bandit_and_ruff():
    """Verify that MCP server runs over stdio and accurately analyzes code."""
    code = "def bad():\n    return eval('1 + 1')\n"
    res = await call_tool("analyze_code", {"code": code, "language": "python"})
    assert isinstance(res, list)
    sources = {item.get("source") for item in res}
    assert "ruff" in sources or "bandit" in sources
    titles = [item.get("title", "").lower() for item in res]
    assert any("eval" in t or "s307" in t or "b307" in t for t in titles)


@pytest.mark.skipif(not eslint_available(), reason="ESLint not available")
async def test_mcp_call_tool_eslint():
    """Verify that MCP server handles ESLint for JavaScript."""
    code = "const x = eval('1 + 1');\n"
    res = await call_tool("eslint_scan", {"code": code})
    assert isinstance(res, list)
    assert any(item.get("title") == "no-eval" for item in res)
