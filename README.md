# AI Code Review and Test Generation

An AI-assisted code reviewer that does more than guess. For each suspected bug it generates tests, runs them in a
sandbox, and reports whether a test **confirmed** the defect. It also proposes a fix and checks it against the same tests.

Built with FastAPI, LangGraph, ChromaDB (RAG), an MCP tool server (bandit, ruff, ESLint) and any LLM provider
(local Ollama, Groq, Google Gemini, OpenAI or Anthropic).

> **New here?** Read the step-by-step [HOW_TO_RUN.md](HOW_TO_RUN.md) guide (setup on Windows/macOS/Linux,
> choosing an LLM, using the web page, troubleshooting, and pushing to GitHub) and [CHANGES.md](CHANGES.md)
> for a full engineering log of all bug fixes and improvements made to the original project.

## Features

- **Line-level findings** with the exact source line, a failure scenario and a suggested fix, combining static analysis (`bandit`, `ruff`, `ESLint`) with LLM review via deterministic deduplication so tool findings are never dropped.
- **Evidence-classified defect confirmation.** Every test failure is classified (`assertion`, `target_exception`, `test_error`, `sandbox_blocked`, `timeout`). A finding is marked **confirmed** only when a test fails on an assertion or crashes inside the reviewed code—never when a generated test itself is broken.
- **Test quality gate (`app/test_quality.py`).** Before execution, generated tests are checked for AST syntax, finding linkage, `target` calls to existing functions, and real assertions/expected-exception checks; low-quality tests are rejected and retried with specific feedback.
- **Proof-of-Bug & fix verification (`app/fix_logic.py`).** A proposed patch is verified against the test suite and re-scanned with static analysis. Once verified, a **counterfactual mutant** (disabling the patch's guard or re-injecting the defective hunk) is tested to prove the reproduction test specifically catches the defect (`proof_of_bug`).
- **Hardened runtime sandbox (`app/sandbox/runner.py`).** Even in `subprocess` mode, Python's `sys.addaudithook` and Node's Permission Model + network guard restrict **both generated tests and submitted code** from network access, spawning processes, or reading/writing files outside the temporary sandbox directory.
- **Python and JavaScript** support with extension-, AST-, and token-aware language detection.
- **Regression mode.** Review the same file again and see which issues are new, fixed or still present.
- **Web page** with built-in example presets, colour-coded findings, Proof-of-Bug badges, a Markdown download and print-to-PDF.
- **Evaluation harness** that compares four pipeline variants on a 50-file labelled benchmark.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # Windows: copy .env.example .env
uvicorn app.main:app --reload                          # then open http://127.0.0.1:8000
```

The default LLM is [Ollama](https://ollama.com) with a free local model:

```bash
ollama pull qwen2.5-coder:7b
```

To use a free cloud model instead, set `LLM_PROVIDER=groq` and `GROQ_API_KEY` (or `LLM_PROVIDER=gemini` and
`GEMINI_API_KEY`) in `.env`. Leave `LLM_MODEL` empty to use each provider's default model.

JavaScript review needs Node.js and one extra step: `cd tools/js && npm install` (on Windows PowerShell use `npm.cmd install`).

Run the offline tests (no API key or model needed):

```bash
python -m pytest
```

## Evaluation

The benchmark has 50 files: 14 clean files and 36 with 42 labelled defects across 8 categories, plus JavaScript
files. Each defect's line number is recorded when the benchmark is built, and findings are matched to defects by line.

```bash
python -m eval.run_eval --modes static_only              # no LLM, runs in seconds
python -m eval.run_eval --runs 1 --limit 10 --no-fix     # quick trial with the configured model
```

The evaluation reports precision, recall and F1, recall per defect category, false alarms on clean files, cost and
time, test-generation quality, and fix-verification rate.

Without any LLM, the static tools alone reach 88% precision and 50% recall on the benchmark, with no false alarms on
clean files. Boundary defects are missed entirely, which is the gap the LLM and generated tests are meant to close.

## Sandbox

Generated tests run in a temporary directory with a per-test and whole-run timeout, and API keys are removed from
their environment.

```bash
python -m eval.sandbox_isolation --mode subprocess
```

The subprocess mode is **not a security boundary**: a test can write to paths outside the sandbox. For stronger
isolation, use the Docker mode, which runs with no network, a read-only root filesystem and limited resources:

```bash
docker build -f Dockerfile.sandbox -t code-review-sandbox .
python -m eval.sandbox_isolation --mode docker
```

## Limitations

- Supports Python and JavaScript only. Each review covers one file.
- A generated test can fail for the wrong reason, so a "confirmed" label is evidence, not proof. Spot-check important results.
- The quality score uses fixed severity weights, so it is only as good as the findings behind it.
- Resource limits in subprocess mode do not apply on Windows.
- The Docker sandbox has not been validated across all platforms.
- The evaluation benchmark is small and was written for this project, except where noted in the benchmark builder.
