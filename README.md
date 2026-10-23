# ActionBridge AI

**A persistent personal action agent that remembers commitments, detects how changes affect a plan, proposes targeted replans, and applies them only after explicit user confirmation.**

ActionBridge AI is submitted as a **simulated Alexa+ agentic experience** in a **web application**. It is not a chatbot and not a to-do list: it keeps persistent context, reasons over that context through an MCP tool workflow, and only acts (changes your real data) after a human confirms.

**Core flow:** Understand → Remember → Reason → Act → Adapt

> A normal assistant may *tell* you that a deadline changed. ActionBridge determines **what that change affects**, proposes an **updated plan**, asks for **approval**, applies it **only after approval**, and **remembers the result**.

---

## Quick Start for Judges / Reviewers (no API key required)

The **recommended reviewer path is deterministic offline Demo Mode**. It needs **no Gemini API key and no AWS credentials** and runs the full agent → MCP → services → SQLite loop end to end.

> Demo Mode is honestly described as a **deterministic offline / reviewer demo mode**. It is a rule-based provider, **not** a full LLM.

```bash
# 1. Clone
git clone https://github.com/Soha-025/ActionBridge-AI.git
cd ActionBridge-AI

# 2. Backend — create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

# 3. Start the backend in Demo Mode (no keys needed), real current date
cd backend
DEMO_FALLBACK=true python -m uvicorn main:app --port 8000   # Windows (PowerShell): $env:DEMO_FALLBACK="true"; python -m uvicorn main:app --port 8000

# 4. Frontend — install and run (separate terminal, from the repo root)
cd ../frontend
npm install
npm run dev
```

Open the URL printed by Vite (default `http://localhost:5173`). The frontend talks to the backend on `http://localhost:8000` out of the box (via the Vite dev proxy and CORS). No `.env` file is required for this path. `DEMO_TODAY` is **not** needed — Demo Mode uses the **real current system date**.

### Run the exact demo scenario

Type these into the **Assistant** in order:

1. `I have a Statistics quiz Thursday, an AI assignment Friday and a presentation Saturday.`
   → three commitments are created and persisted.

2. **New session / restart the backend (keep the same DB):**
   `What should I work on today?`
   → the plan is rebuilt from the *saved* commitments (persistent memory across sessions).

3. `My AI assignment deadline changed from Friday to Wednesday.`
   → the agent detects the affected commitment, checks conflicts, and **proposes a replan** (nothing is applied yet).

4. Review the affected tasks / conflict / proposed diff.

5. The **confirmation modal** appears showing old → new dates and "Nothing has been applied yet."

6. Click **Apply changes**.

7. Verify the dashboard updates and the change is marked applied (it persists after refresh).

Also demonstrate **Cancel**: repeat step 3 and choose **Cancel proposal** instead — the existing plan stays **unchanged**.

---

## Providers

ActionBridge uses a **provider abstraction**: the same agent, MCP tools, services, and SQLite layer run unchanged regardless of which language-model provider is selected. Providers are chosen by **explicit configuration only** — there is **no automatic failover after an API failure**.

Precedence (highest first):

```text
DEMO_FALLBACK=true   ->  deterministic DemoProvider   (A. recommended reviewer / no-key mode)
LLM_PROVIDER=gemini  ->  live GeminiProvider          (B. optional live AI mode)
otherwise            ->  BedrockProvider              (C. Amazon Bedrock)
```

| Mode | How to enable | Requirements | Notes |
|---|---|---|---|
| **A. Reviewer / No-Key (recommended)** | `DEMO_FALLBACK=true` | none | Deterministic offline demo. No Gemini key, no AWS credentials. Real current date. |
| **B. Optional Live Gemini** | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=…`, `GEMINI_MODEL=…` | Google Gemini API key | Exercised live successfully; subject to provider rate limits (e.g. HTTP 429). Not required for judging. |
| **C. Amazon Bedrock** | unset both above; `AWS_REGION=…`, `BEDROCK_MODEL_ID=…` + standard Boto3 credentials | AWS credentials + tool-capable Bedrock model | Implemented via the Bedrock Converse / tool-calling API. Requests reached AWS during testing but hit account/runtime token throttling, so a live Bedrock run is **not** required to reproduce the demo. Do **not** claim the recorded demo is powered by Bedrock unless it actually is. |

Copy `backend/.env.example` to `backend/.env` and set only the variables for the mode you want. `backend/.env` is git-ignored.

---

## Technology Stack (verified against the code)

| Component | Technology |
|---|---|
| Frontend | React + Vite + TypeScript, styled with **plain CSS** (`frontend/src/components/actionbridge.css`) — **no Tailwind / no CSS framework** |
| Backend API | FastAPI (Uvicorn) |
| Tool layer | Python **MCP SDK** (in-process MCP server exposing the safe tools) |
| Model providers | Amazon Bedrock (Converse + tool calling), Google Gemini, and a deterministic DemoProvider — behind one abstraction |
| Persistence | SQLite (standard library, no ORM) |
| Language | Python 3.10+ (developed/tested on 3.13), Node.js + npm |

---

## Architecture Overview

```text
Frontend (React/Vite/TS)
  → FastAPI (main.py)
    → ActionBridge Agent (agent.py)            # multi-round tool-use loop
      → Provider abstraction (Bedrock / Gemini / Demo)
        → MCP safe tools (mcp_server.py)        # only 5 model-facing tools
          → deterministic services (services.py)
            → SQLite (commitments, plan_items, pending_changes)
```

**Human-confirmation boundary (safety invariant):**

- The model/agent can only call **five** tools: `create_commitment`, `get_commitments`, `create_plan`, `detect_conflicts`, `propose_replan`.
- The model **cannot** call `apply_changes` or `cancel_change`. Those (plus `get_change`) are reachable **only** through plain HTTP endpoints that call the service layer directly: `POST /api/changes/{id}/apply` and `POST /api/changes/{id}/cancel`.
- `propose_replan` **never** mutates a commitment — it records a **pending** change. Applying it is a separate, server-side, human-triggered step.
- Duplicate applies are rejected (HTTP 409); reload never auto-applies or auto-cancels.

---

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/chat` | Send a message to the assistant (agent + MCP tools) |
| GET | `/api/commitments` | Retrieve commitments |
| GET | `/api/plan` | Retrieve the current plan |
| GET | `/api/conflicts` | Contextual "what-if" conflict check for a proposed move |
| GET | `/api/changes/{change_id}` | Retrieve details/diff of a proposed change |
| POST | `/api/changes/{change_id}/apply` | Apply a proposed change (human confirmation) |
| POST | `/api/changes/{change_id}/cancel` | Cancel a proposed change (human confirmation) |

Interactive docs: `http://localhost:8000/docs` while the backend is running.

---

## Running the Tests

`pytest` and `httpx` are **test-only** dependencies (not needed to run the app). Install them, then run the suite:

```bash
cd backend
pip install -r requirements-dev.txt
DEMO_FALLBACK=true python -m pytest -q
```

Latest evidence:

- **Backend:** **236 tests passed** with one non-blocking Starlette/httpx deprecation warning.
- **Frontend:** `npm run build` (TypeScript check + Vite production build) **passes**.

> Note: set `DEMO_FALLBACK=true` (or otherwise leave `LLM_PROVIDER` unset) so the suite does not pick up a `LLM_PROVIDER=gemini` from a local `.env`, which would make the provider-selection test fail for environmental reasons.

---

## Optional Demo Seeding

`backend/seed.py` creates the three sample commitments idempotently (a second run detects existing records instead of duplicating them). Use an isolated database so you never touch the real demo DB:

```bash
cd backend
DB_PATH=./actionbridge_demo.db DEMO_FALLBACK=true python seed.py
```

Seeding uses the real current date by default. `DEMO_TODAY` is an **optional** service-level override for controlled tests only and is **not** required for normal use.

---

## Security & Repo Hygiene

- `.env`, all cloud credentials, and API keys are kept out of version control (`.env` and `*.db` are git-ignored; database backups such as `*.db.backup-*` are ignored too).
- `.env.example` files document variable **names** only — never real secrets.
- No AWS credentials or Gemini keys belong in the frontend.
- Review and explicitly confirm any proposed change before it is applied.

---

## License

Released under the **MIT License** (see `LICENSE`).

## Team

ActionBridge AI is developed collaboratively for the Amazon "Build, Ship, Shape" Developer Hackathon (Alexa+ track) as a simulated Alexa+ agentic experience: backend/AI integration, frontend, planning logic, testing, documentation, and demo.
