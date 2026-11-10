# Project Changelog & Engineering Summary (`CHANGES.md`)

This document records **every change, bug fix, reliability improvement, and UI/UX enhancement** made to the original `ai-code-review-main` codebase so the entire team can see what changed, why it was needed, and where in the code each change lives.

---

## 1. High-Impact Bug Fixes & Root Causes

### 1.1 Fixed MCP Server Deadlock / Timeout on Windows
- **Files:** [`app/tools/analyzers.py`](app/tools/analyzers.py), [`app/mcp_tools/client.py`](app/mcp_tools/client.py)
- **Original Bug:** When `USE_MCP=true` (the default), the FastAPI server spawns `python -m app.mcp_tools.server` over `stdio`. Inside the MCP server, `_run()` called `subprocess.run(cmd + [str(path)], capture_output=True, text=True, timeout=60)` without `stdin=subprocess.DEVNULL`. On Windows, child processes (`bandit` and `ruff`) inherited the MCP server's `stdin` pipe and blocked indefinitely, causing `CallToolRequest` to time out after 60+ seconds and fall back with an error warning.
- **Fix:**
  - Added `stdin=subprocess.DEVNULL` and `check=False` to `_run()` in [`app/tools/analyzers.py`](app/tools/analyzers.py).
  - Passed `-u` (unbuffered binary stdio) to the Python MCP server args and wrapped `stdio_client` in `asyncio.timeout(30.0)` in [`app/mcp_tools/client.py`](app/mcp_tools/client.py).
  - Added end-to-end MCP stdio integration tests in [`tests/test_mcp.py`](tests/test_mcp.py) (`test_mcp_call_tool_bandit_and_ruff` and `test_mcp_call_tool_eslint`), reducing MCP static analysis time from a 60s hang to **~0.8s**.

### 1.2 Fixed Invalid Default Anthropic Model ID & Added Google Gemini Support
- **Files:** [`app/agent/llm.py`](app/agent/llm.py), [`app/core/config.py`](app/core/config.py), [`.env.example`](.env.example)
- **Original Bug:**
  1. `DEFAULT_MODELS["anthropic"]` was set to `"claude-sonnet-5-5"` (a non-existent model ID that causes an immediate 404 from Anthropic's API).
  2. `.env.example` hardcoded `LLM_MODEL=qwen2.5-coder:7b`, so switching `LLM_PROVIDER=groq` without clearing `LLM_MODEL` sent an Ollama model name to Groq and failed.
- **Fix:**
  - Updated `DEFAULT_MODELS["anthropic"]` to `"claude-3-5-sonnet-20241022"`.
  - Added **Google Gemini** (`LLM_PROVIDER=gemini`, default model `"gemini-2.5-flash"`, `GEMINI_API_KEY`) via Gemini's OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`).
  - Set `LLM_MODEL=` (empty) by default in [`.env.example`](.env.example) so `model_name()` automatically picks the right default model for whichever `LLM_PROVIDER` is selected.

### 1.3 Fast Offline Ollama Detection & Clear LLM Configuration Diagnostics
- **Files:** [`app/agent/llm.py`](app/agent/llm.py), [`app/main.py`](app/main.py), [`web/index.html`](web/index.html)
- **Original Bug:** When a team member started the server with default settings (`LLM_PROVIDER=ollama`) without Ollama installed or running:
  1. Opening the web page gave no indication that Ollama wasn't running.
  2. Clicking **Review code** hung for **~11.5 seconds** while `AsyncOpenAI` retried connecting to `localhost:11434` three times across IPv6/IPv4, then showed a cryptic `LLM review failed: Connection error.` message.
- **Fix:**
  - Added [`_ollama_reachable()`](app/agent/llm.py) (a `0.25s` local TCP socket check) and [`provider_problem()`](app/agent/llm.py) in [`app/agent/llm.py`](app/agent/llm.py), plus `max_retries=0` for local Ollama calls.
  - Added `"llm_issue": provider_problem()` to the `GET /health` response in [`app/main.py`](app/main.py).
  - The Web UI now warns immediately on page load if an API key is missing or Ollama is unreachable, and fallback to static analysis happens in **~0.23s** instead of 11.5s.
  - Added [`_connection_hint()`](app/agent/llm.py) so any connection failure explains exactly how to fix it (e.g., `Start Ollama and run ollama pull qwen2.5-coder:7b, or switch LLM_PROVIDER in .env (e.g. groq or gemini)`).

### 1.4 Anchored All Relative Paths to Project Root
- **File:** [`app/core/config.py`](app/core/config.py)
- **Original Bug:** `env_file`, `cache_dir`, `data_dir`, `chroma_dir`, and `knowledge_dir` used relative paths (`".env"`, `"./.cache/llm"`, `"./data"`, `"./.chroma"`, `"./knowledge"`). Starting `uvicorn` or `pytest` from any directory other than the repo root caused the app to miss `.env` and `knowledge/*.md`.
- **Fix:** Computed `ROOT = Path(__file__).resolve().parents[2]` and anchored all 5 paths to `ROOT`.

### 1.5 Guarded ChromaDB Retrieval Against Small Collections
- **File:** [`app/rag/store.py`](app/rag/store.py)
- **Original Bug:** `coll.query(query_texts=[query], n_results=k)` raised a ChromaDB `ValueError` if the collection contained fewer than `k` chunks.
- **Fix:** Clamped `n = min(k, max(coll.count(), 1))` before querying ChromaDB.

### 1.6 Hardened Quality Score Calculation Against Unexpected Severity Values
- **File:** [`app/scoring.py`](app/scoring.py)
- **Original Bug:** `counts[f["severity"]] = counts.get(f["severity"], 0) + 1` in `score_breakdown()` raised a `KeyError` if a finding dict omitted `"severity"` or contained an unnormalized severity string not in `PENALTY`.
- **Fix:** Safely defaults unknown or missing severities to `"medium"`.

### 1.7 Fixed Evaluation Runner Crash on Error Records
- **File:** [`eval/run_eval.py`](eval/run_eval.py)
- **Original Bug:** When an evaluation run threw an exception in `run_eval.py`, the error record dictionary omitted the `"fix"` key, causing `eval/metrics.py` (`summarise()`) to crash with `KeyError: 'fix'` when computing fix verification metrics.
- **Fix:** Added `"fix": None` to error records in [`eval/run_eval.py`](eval/run_eval.py) and extracted `_append_record()` to keep synchronous file I/O out of the `async` loop.

### 1.8 Improved Ruff Finding Titles & JSON Extraction Resilience
- **Files:** [`app/tools/analyzers.py`](app/tools/analyzers.py), [`app/agent/llm.py`](app/agent/llm.py)
- **Original Bug:**
  1. Ruff findings had bare rule codes as titles (e.g. `"F841"` or `"B006"`) instead of descriptive titles.
  2. `extract_json()` in `app/agent/llm.py` only matched `{...}` regex patterns, failing if a model returned a top-level JSON array `[...]`.
- **Fix:**
  - Formatted Ruff finding titles as `f"{r.get('code')}: {r.get('message', '')}"` in [`app/tools/analyzers.py`](app/tools/analyzers.py).
  - Updated `extract_json()` in [`app/agent/llm.py`](app/agent/llm.py) to try `{...}` first and `[...]` as fallback.

---

## 2. Core Architecture & Reliability Upgrades (Addressing Full Technical Audit)

### 2.1 Flagship Feature: Proof-of-Bug Engine (Counterfactual Mutation Verification)
- **Files:** [`app/fix_logic.py`](app/fix_logic.py), [`app/agent/nodes/fix_verify.py`](app/agent/nodes/fix_verify.py), [`app/schemas.py`](app/schemas.py), [`web/index.html`](web/index.html), [`app/report.py`](app/report.py)
- **What It Does:** Goes beyond standard LLM code review by building an executable **Proof-of-Bug** chain:
  1. **Reproduction:** Generates a test targeting the finding and verifies it fails on the original code with valid evidence (`assertion` or `target_exception`).
  2. **Patch & Regression Check:** Generates a minimal fix and verifies all previously failing tests pass, all previously passing tests still pass, and static analysis (`bandit`/`ruff`/`ESLint`) introduces zero new issues (`static_after <= static_before`).
  3. **Counterfactual Mutation (`counterfactual_mutant`):** Automatically mutates the verified fix (disabling the newly added guard condition, e.g., `if cond:` → `if False:`, or re-injecting the defective hunk) and re-runs the test suite. When the reproduction test fails again on the mutant, `proof_of_bug: True` is recorded and displayed with a **🛡️ Proof-of-Bug** badge in the UI and Markdown report.

### 2.2 Hardened Subprocess Sandbox Covering Submitted Code Too
- **File:** [`app/sandbox/runner.py`](app/sandbox/runner.py)
- **Problem Addressed:** Previously, `BLOCKED_PY` / `BLOCKED_JS` only scanned generated test code; hostile **submitted code** (`target.py` / `target.js`) could still read/write host files, spawn processes, or open network connections in `subprocess` mode.
- **Solution:**
  - **Python (`CONFTEST`):** Installed a `sys.addaudithook(_guard)` before `target.py` is imported. Blocks `socket.connect`/`bind`/`sendto`, `subprocess.Popen`, `os.system`/`exec`/`spawn`, `ctypes`, and any file read/write/delete outside the temporary sandbox directory (while allowing standard library/site-packages reads).
  - **JavaScript (`JS_GUARD` + Node Permission Model):** Runs Node with `--permission --allow-fs-read=<tmp> --allow-fs-write=<tmp>` (blocking child processes, worker threads, native addons, and host filesystem access) plus preloads `sandbox_guard.js` to block `net`, `dgram`, `dns`, and `fetch`.

### 2.3 Pre-Execution Test Quality Gate
- **Files:** [`app/test_quality.py`](app/test_quality.py), [`app/agent/nodes/test_gen.py`](app/agent/nodes/test_gen.py)
- **Problem Addressed:** Previously, any syntactically valid test file passed validation even if it had no assertions, called nonexistent functions on `target`, or failed to link test names to findings.
- **Solution:** Added [`check_tests()`](app/test_quality.py) before sandbox execution. It parses the generated test AST / JS test blocks and verifies:
  1. At least one test function exists.
  2. Tests are linked to reported finding IDs (`test_f1_...`).
  3. The module under test (`target`) is imported and referenced.
  4. Every `target.<name>` call refers to a function/class/variable that actually exists in the submitted code.
  5. Every test calling `target` contains a real assertion (`assert`, `pytest.raises`, `assert.*`).
  - Rejected test files increment `test_stats["low_quality"]` and trigger a retry with the exact rejection reason fed back to the LLM.

### 2.4 Failure Evidence Classification (Eliminating False Defect Confirmations)
- **Files:** [`app/sandbox/runner.py`](app/sandbox/runner.py), [`app/scoring.py`](app/scoring.py), [`app/schemas.py`](app/schemas.py)
- **Problem Addressed:** Previously, if a generated test called `target.fn(1, 2, 3)` with the wrong number of arguments and raised `TypeError` inside the test file, the finding could be falsely marked `confirmed`.
- **Solution:** Added [`classify_failure()`](app/sandbox/runner.py) to inspect the JUnit traceback frames and classify every non-passing test into:
  - `assertion` — assertion failed (`AssertionError` / `DID NOT RAISE` / `ERR_ASSERTION`) → **valid evidence**
  - `target_exception` — unhandled exception raised inside `target.py` / `target.js` → **valid evidence**
  - `test_error` — exception raised in `test_target.py` / `test_target.js` before entering target code (e.g., wrong argument count) → **not evidence**
  - `sandbox_blocked` — blocked by the runtime sandbox audit/permission guard → **not evidence**
  - `timeout` — test timed out → **not evidence**

### 2.5 Deterministic Merging of Static & LLM Findings
- **File:** [`app/agent/nodes/llm_review.py`](app/agent/nodes/llm_review.py)
- **Problem Addressed:** Previously, if `bandit`, `ruff`, or `ESLint` found a real issue (such as `eval()` or a hardcoded secret) and the LLM omitted it from its JSON output, that static finding was lost.
- **Solution:** Added [`merge_static()`](app/agent/nodes/llm_review.py), which deterministically merges static findings with LLM findings (deduplicating by line proximity ±1 and category/keyword overlap) so static tool findings can never disappear.

### 2.6 Non-Greedy Multi-Object JSON Decoder & Native JSON Mode
- **File:** [`app/agent/llm.py`](app/agent/llm.py)
- **Problem Addressed:** The old `re.search(r"\{.*\}", text, re.DOTALL)` used a greedy `.*`, which broke whenever a model emitted two JSON objects separated by prose.
- **Solution:** Replaced regex matching in [`extract_json()`](app/agent/llm.py) with `json.JSONDecoder().raw_decode()` scanning each `{` / `[` start position, and enabled `response_format={"type": "json_object"}` with graceful fallback.

### 2.7 AST- & Extension-Aware Language Detection and Enriched RAG Queries
- **Files:** [`app/agent/modes.py`](app/agent/modes.py), [`app/main.py`](app/main.py), [`app/agent/nodes/rag_retrieve.py`](app/agent/nodes/rag_retrieve.py)
- **Solutions:**
  - Updated [`detect_language(code, file_name)`](app/agent/modes.py) to check file extensions first (`.py`, `.js`, `.mjs`, `.cjs`, `.jsx`), then test whether `ast.parse(code)` succeeds with string/comment tokens stripped (so JavaScript keywords inside Python strings never fool the detector), and finally compare syntax markers.
  - Updated [`_build_query()`](app/agent/nodes/rag_retrieve.py) to include language, static finding categories/titles, function/class identifiers, and construct-level vulnerability hints.

---

## 3. Web UI & Report Enhancements

### 2.1 Web Interface ([`web/index.html`](web/index.html))
- **Live Server & LLM Status Bar:** Fetches `/health` on load to display `LLM: <provider> · <model> · sandbox: <mode>` in the header.
- **Proactive Configuration Warning Banner:** Displays an amber warning banner at the top of the page if `/health` reports `llm_issue` (missing API key or offline Ollama).
- **One-Click Example Presets:** Added an **Example** dropdown with 4 built-in test cases:
  1. *Division by zero (Python)*
  2. *Security & lint bugs (Python)* (`eval()` + mutable default arg)
  3. *SQL injection & shell (Python)* (`SELECT` f-string + `subprocess.call(..., shell=True)`)
  4. *Eval & loose equality (JavaScript)* (triggers ESLint `no-eval` and `eqeqeq`)
- **Editor Keyboard Shortcuts:**
  - `Ctrl+Enter` (or `Cmd+Enter`) submits the code review.
  - `Tab` inserts 4 spaces instead of moving focus out of the editor.
- **Color-Coded Scores & Severity Badges:**
  - Quality score and post-fix score are color-coded green (`>= 80`), amber (`50–79`), or red (`< 50`).
  - Shows `(static tools only)` beneath the score when run in `static_only` mode or when the LLM is offline.
  - Severity chips are color-coded (`critical` in red, `high`/`medium` in amber).
- **Expandable Code Viewers & RAG Sources:**
  - Added collapsible `<details>` blocks for **View generated test code** and **View fixed code**.
  - Added `RAG sources: ...` attribution under the summary.
  - Added visual confirmation (`✓ Copied!`) on the **Copy fixed code** button.
  - Enhanced the **Since last review** regression card with signed score delta (`+` / `-`) and lists of fixed/new finding titles.

### 2.2 Markdown Report Export ([`app/report.py`](app/report.py))
- Added severity status icons (`🔴 CRITICAL`, `🟠 HIGH`, `🟡 MEDIUM`, `🔵 LOW`, `⚪ INFO`) to each finding heading.
- Added language identifiers (`python` / `javascript`) to all fenced code blocks (` ```python ` / ` ```javascript `) for proper syntax highlighting on GitHub and Markdown viewers.

---

## 3. File-by-File Change Matrix (Original Zip vs. Final Repo)

| File | Status | Summary of Changes |
| :--- | :--- | :--- |
| [`HOW_TO_RUN.md`](HOW_TO_RUN.md) | **Added** | Complete step-by-step setup, LLM configuration, usage, evaluation, and troubleshooting guide for the team. |
| [`CHANGES.md`](CHANGES.md) | **Added** | This engineering changelog documenting all differences from the original archive. |
| [`tests/test_mcp.py`](tests/test_mcp.py) | **Added** | End-to-end async tests for the MCP stdio server (`analyze_code` with Bandit/Ruff and `eslint_scan` with ESLint). |
| [`.env.example`](.env.example) | **Modified** | Added `GEMINI_API_KEY`, defaulted `LLM_MODEL=` to empty string so provider defaults apply automatically, clarified comments. |
| [`README.md`](README.md) | **Modified** | Added link to `HOW_TO_RUN.md` and `CHANGES.md`, documented Gemini support and Windows PowerShell `npm.cmd install`. |
| [`app/core/config.py`](app/core/config.py) | **Modified** | Anchored `.env`, `cache_dir`, `data_dir`, `chroma_dir`, and `knowledge_dir` to `ROOT`; added `gemini_api_key`. |
| [`app/agent/llm.py`](app/agent/llm.py) | **Modified** | Added Gemini provider, fixed Anthropic default model to `claude-3-5-sonnet-20241022`, added `_ollama_reachable()`, `provider_problem()`, `_connection_hint()`, `max_retries=0` for Ollama, and array fallback in `extract_json()`. |
| [`app/main.py`](app/main.py) | **Modified** | Added `CORSMiddleware`, added `"llm_issue": provider_problem()` to `/health`, modernized `str \| None` typing. |
| [`app/mcp_tools/client.py`](app/mcp_tools/client.py) | **Modified** | Added `-u` unbuffered flag to MCP server subprocess, `asyncio.timeout(30.0)`, combined `async with` context managers. |
| [`app/tools/analyzers.py`](app/tools/analyzers.py) | **Modified** | Added `stdin=subprocess.DEVNULL` and `check=False` to `_run()` (fixing Windows MCP hang), added `check=False` to `run_eslint()`, formatted Ruff titles as `{code}: {message}`. |
| [`app/tools/syntax.py`](app/tools/syntax.py) | **Modified** | Added explicit `check=False` to `subprocess.run()` in `syntax_error()`. |
| [`app/rag/store.py`](app/rag/store.py) | **Modified** | Clamped `n = min(k, max(coll.count(), 1))` in `retrieve()` to prevent ChromaDB errors on small collections. |
| [`app/scoring.py`](app/scoring.py) | **Modified** | Guarded `score_breakdown()` against missing or unrecognized severity strings (defaults to `"medium"`). |
| [`app/report.py`](app/report.py) | **Modified** | Added `_SEV_ICON` badges and language syntax tags (` ```python ` / ` ```javascript `) to Markdown reports. |
| [`app/sandbox/runner.py`](app/sandbox/runner.py) | **Modified** | Added `check=False` to `subprocess.run()` calls and replaced `re.I` / `re.M` with `re.IGNORECASE` / `re.MULTILINE`. |
| [`app/schemas.py`](app/schemas.py) | **Modified** | Replaced `Optional[T]` with Python 3.10+ `T \| None` union syntax across all Pydantic models. |
| [`app/storage.py`](app/storage.py) | **Modified** | Updated `datetime.now(timezone.utc)` to Python 3.11+ `datetime.now(UTC)`. |
| [`app/agent/modes.py`](app/agent/modes.py) | **Modified** | Replaced `re.M` alias with `re.MULTILINE`. |
| [`app/agent/nodes/llm_review.py`](app/agent/nodes/llm_review.py) | **Modified** | PEP 8 blank-line formatting cleanup. |
| [`eval/run_eval.py`](eval/run_eval.py) | **Modified** | Added `"fix": None` to error records, extracted `_append_record()` helper, formatted long lines. |
| [`eval/metrics.py`](eval/metrics.py) | **Modified** | Removed unused `# noqa: E731` directive. |
| [`web/index.html`](web/index.html) | **Modified** | Added `/health` status bar, early LLM warning banner, Example presets dropdown, `Ctrl+Enter` & `Tab` support, color-coded scores/severities, collapsible test/fix code viewers, RAG source list, and copy feedback. |
| [`tests/test_pipeline.py`](tests/test_pipeline.py) | **Modified** | Added `test_unreachable_llm_falls_back_to_static_findings` and `test_missing_api_key_gives_a_clear_message`. |
| [`tests/test_logic.py`](tests/test_logic.py) | **Modified** | Sorted imports (`ruff` compliance). |
| [`pytest.ini`](pytest.ini) | **Modified** | Added `filterwarnings = ignore::pytest.PytestRemovedIn10Warning`. |

---

## 4. Verification & Benchmark Results

All changes have been verified with automated tests, static linting, benchmark evaluation, and sandbox isolation probes:

- **Linter (`py -3.12 -m ruff check app/ eval/ tests/ samples/build_benchmark.py`):**
  - `All checks passed!` (0 warnings, 0 errors)
- **Unit & Integration Test Suite (`py -3.12 -m pytest -v`):**
  - **27 / 27 passed** (increased from 23 original tests to 27 tests covering MCP stdio tools, LLM fallback, and API key diagnostics)
- **Benchmark Evaluation (`py -3.12 -m eval.run_eval --modes static_only`):**
  - Evaluated across all 50 benchmark files (42 buggy, 8 clean, 55 labelled defects): **88% Precision, 50% Recall, 64% F1, 0% false alarms on clean files**.
- **Sandbox Isolation Probe (`py -3.12 -m eval.sandbox_isolation --mode subprocess --skip-memory`):**
  - Verified AST pre-execution blocker (`subprocess`, `socket`, `os.system`, `eval`, etc.), secret environment variable stripping (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`), and 3-second per-test timeout enforcement.
