# Complete Guide: How to Run and Use AI Code Review & Test Generation

Welcome! This guide explains everything you need to know to run, configure, evaluate, and share this project.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Prerequisites](#2-prerequisites)
3. [Quick Start (3 Simple Steps)](#3-quick-start-3-simple-steps)
4. [Configuring LLM Providers (.env)](#4-configuring-llm-providers-env)
5. [Using the Web Interface](#5-using-the-web-interface)
6. [Running Reviews via Terminal or Python Script](#6-running-reviews-via-terminal-or-python-script)
7. [Running the Test Suite](#7-running-the-test-suite)
8. [Running the Evaluation Benchmark](#8-running-the-evaluation-benchmark)
9. [Running Sandbox Security Probes](#9-running-sandbox-security-probes)
10. [How to Push to GitHub & Share with Friends](#10-how-to-push-to-github--share-with-friends)
11. [Troubleshooting & FAQ](#11-troubleshooting--faq)

---

## 1. Project Overview

This is an AI-powered code review and automated test generation system. Unlike typical AI reviewers that merely guess whether code has bugs, this system:
1. **Performs static analysis** using Python Bandit & Ruff and JavaScript ESLint (run standalone or via an MCP tool server).
2. **Retrieves domain knowledge** using ChromaDB vector RAG over curated software defect references.
3. **Generates targeted pytest/Node test suites** specifically probing suspected edge-case vulnerabilities.
4. **Executes tests inside an isolated sandbox** to mark findings as:
   - **🔴 confirmed**: A generated test failed or crashed on the original code, proving the defect is real.
   - **🟡 suspected**: Static or LLM finding where generated tests did not trigger a hard failure.
5. **Generates and verifies fixes**: Proposes corrected source code and runs the exact same test suite to ensure the failing tests now pass with zero regressions.
6. **Supports regression tracking**: Remembers previous reviews of a file to highlight new, fixed, and persisting defects.

---

## 2. Prerequisites

- **Python 3.12+** (installed at `py -3.12` or `python`)
- **Node.js 18+ & npm** (for JavaScript ESLint static analysis)
- **Git** (for version control and pushing to GitHub)

Verify in PowerShell or Terminal:
```powershell
py -3.12 --version
node -v
git --version
```

---

## 3. Quick Start (3 Simple Steps)

### Step 1: Install Python Dependencies
```powershell
py -3.12 -m pip install -r requirements.txt
```

### Step 2: Install JavaScript Analysis Tools
```powershell
cd tools/js
npm.cmd install    # on Linux/macOS: npm install
cd ../..
```

### Step 3: Start the Web Application
```powershell
py -3.12 -m uvicorn app.main:app --reload
```

Open your browser and navigate to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 4. Configuring LLM Providers (`.env`)

The project includes an active `.env` file in the root directory. You can choose any of the supported providers below.

### Option A: Free Cloud AI with Groq (Recommended — Fastest)
Groq provides free high-speed cloud inference (Llama 3.3 70B) with no credit card required.
1. Get a free API key at [console.groq.com/keys](https://console.groq.com/keys).
2. In `.env`, set:
   ```env
   LLM_PROVIDER=groq
   GROQ_API_KEY=gsk_your_actual_key_here
   ```
3. Reviews and fix verifications will complete in 2–3 seconds.

### Option B: Free Cloud AI with Google Gemini
1. Get a free API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
2. In `.env`, set:
   ```env
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=AIzaSy_your_actual_key_here
   ```

### Option C: 100% Offline Local AI with Ollama
If you want to run completely offline without sending any code to the cloud:
1. Download Ollama from [ollama.com](https://ollama.com).
2. In terminal, download the coding model:
   ```powershell
   ollama run qwen2.5-coder:7b
   ```
3. Keep Ollama open and in `.env` set:
   ```env
   LLM_PROVIDER=ollama
   OLLAMA_BASE_URL=http://localhost:11434/v1
   ```

### Option D: OpenAI or Anthropic
- **OpenAI**: Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY=sk-...` (defaults to `gpt-4o-mini`).
- **Anthropic**: Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=sk-ant-...` (defaults to `claude-3-5-sonnet-20241022`).

### Option E: Offline Static Analysis Only (Zero AI / No API Key)
If you do not want to configure an LLM at all:
- In the web UI, select **Mode: `static tools only`**.
- It runs Bandit, Ruff, and ESLint instantly with zero external calls.

---

## 5. Using the Web Interface

1. **Editor**: Paste your Python or JavaScript code into the main editor.
   - Press **`Ctrl+Enter`** (or `Cmd+Enter` on Mac) to quickly trigger the review.
   - Press **`Tab`** to insert 4 spaces without losing focus.
2. **Mode Dropdown**:
   - `full`: Static analysis + RAG context + LLM review + generated tests + fix verification.
   - `llm_static`: Static tools + LLM review without sandbox test execution.
   - `llm_only`: Pure LLM review.
   - `static_only`: Fast, local security/syntax scan (Bandit + Ruff + ESLint).
3. **Verify Fix Checkbox**:
   - When checked, automatically generates fixed code and validates it against the generated tests.
4. **File Name (Regression Mode)**:
   - Provide a file name (e.g. `utils.py`).
   - When you review modified code under the same name later, it displays a diff of **fixed**, **new**, and **persisting** issues.
5. **Inspect Generated Tests**:
   - Click the expandable **"▶ View generated test code"** to view the full pytest or Node.js test script.
6. **Export Findings**:
   - **Download Markdown**: Downloads a formatted `.md` report of findings, tests, and fixes.
   - **Print / PDF**: Opens browser print preview styled cleanly for saving as a PDF.

---

## 6. Running Reviews via Terminal or Python Script

### Running from Python code:
```powershell
py -3.12 -c "import asyncio; from app.agent.graph import run_review; res = asyncio.run(run_review('def bad(): return eval(1)', mode='static_only')); print(res['findings'])"
```

### Running via cURL / HTTP API:
With the server running on port 8000:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/review" -Method Post -ContentType "application/json" -Body '{"code": "def divide(a, b):\n return a / b\n", "mode": "static_only"}'
```

---

## 7. Running the Test Suite

The project includes an extensive test suite covering API endpoints, scoring logic, LangGraph pipeline, MCP tools, and sandbox execution.

Run all tests:
```powershell
py -3.12 -m pytest -v
```

Run specific test files:
```powershell
# Test the MCP tool server (Bandit, Ruff, ESLint over stdio)
py -3.12 -m pytest tests/test_mcp.py -v

# Test pipeline & fix verification
py -3.12 -m pytest tests/test_pipeline.py -v

# Test sandbox timeout & isolation
py -3.12 -m pytest tests/test_sandbox.py -v
```

---

## 8. Running the Evaluation Benchmark

The benchmark comprises **50 curated source files** (14 clean, 36 with 42 labelled defects) across 8 categories (injection, boundary errors, secrets, crypto, deserialization, etc.).

### Evaluate Static Analysis (Offline, ~30s):
```powershell
py -3.12 -m eval.run_eval --modes static_only
```
Output: Reaches **88% precision, 50% recall, 64% F1** with **0% false alarms on clean files**.

### Evaluate LLM Models (Requires configured `.env`):
```powershell
# Quick 10-file trial
py -3.12 -m eval.run_eval --modes full --limit 10

# Full run with Groq or Ollama
py -3.12 -m eval.run_eval --models groq:llama-3.3-70b-versatile --runs 1
```
Results and cost/time metrics are saved to `eval/results.md`.

---

## 9. Running Sandbox Security Probes

To verify that generated tests cannot access environment secrets, breach file boundaries, or hang in infinite loops:

```powershell
py -3.12 -m eval.sandbox_isolation --mode subprocess --skip-memory
```
This probes:
- Secret environment variable scrubbing (**ISOLATED**)
- Hanging and infinite loops (**CONTAINED** via per-test timeout)
- Forbidden import blocking (**BLOCKED** before execution)

---

## 10. How to Push to GitHub & Share with Friends

Because you downloaded this codebase as a ZIP file, it is not yet connected to a Git remote. Follow these steps to push it to your GitHub account:

### Step 1: Initialize Git and Commit
```powershell
# Open terminal in the project folder
cd c:\Users\KIIT0001\Downloads\ai-code-review-main\ai-code-review-main

# Initialize local git repository
git init

# Rename default branch to main
git branch -M main

# Stage all files (secrets and node_modules are automatically ignored by .gitignore)
git add .

# Create initial commit
git commit -m "Complete AI code reviewer: MCP fixes, web UI polish, and full test suite"
```

### Step 2: Create a New Repository on GitHub
1. Go to **[github.com/new](https://github.com/new)**.
2. Name the repository (e.g. `ai-code-review`).
3. Leave **"Add a README"**, **".gitignore"**, and **"license"** **UNCHECKED**.
4. Click **Create repository**.
5. Copy the HTTPS link (e.g. `https://github.com/your-username/ai-code-review.git`).

### Step 3: Link and Push
```powershell
git remote add origin https://github.com/your-username/ai-code-review.git
git push -u origin main
```

### Step 4: Share with Your Friend
- Send your friend your repository link.
- Or, to let them push directly: In your GitHub repo, go to **Settings → Collaborators → Add people** and enter their username.

---

## 11. Troubleshooting & FAQ

### Q1: I see `⚠️ LLM review failed: Connection error.`
**Reason:** `.env` defaults to `LLM_PROVIDER=ollama`, but Ollama is not installed or running at `localhost:11434`.
**Solution:**
- Get a free Groq key from [console.groq.com/keys](https://console.groq.com/keys) and set `LLM_PROVIDER=groq` and `GROQ_API_KEY=gsk_...` in `.env`.
- Or switch the web UI dropdown to **Mode: `static tools only`**.

### Q2: Why use `py -3.12` instead of `python` on Windows?
On systems with multiple Python installations (e.g. Python 3.13 and Python 3.12), `py -3.12` guarantees the command runs with Python 3.12 where all required packages were installed.

### Q3: `npm : File ... cannot be loaded because running scripts is disabled`
PowerShell restricts `.ps1` script execution by default. Use `npm.cmd` instead:
```powershell
cd tools/js
npm.cmd install
```

### Q4: Are my API keys safe when pushing to GitHub?
Yes. `.env` and `.env.*` are explicitly listed in `.gitignore`. Only `.env.example` (which contains placeholder keys) is tracked by Git.
