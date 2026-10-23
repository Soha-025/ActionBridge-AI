# ActionBridge AI — Friction Log

Genuine developer feedback from building this project. Kept honest, not marketing.

## 1. Local Environment / Setup
Setting up the Python virtual environment and installing backend dependencies took several terminal steps (and a `python3.13` interpreter on macOS). `pytest` and `httpx` are **test-only** and are not part of the runtime `requirements.txt`, which was initially confusing when running the suite.

**Resolution:** documented the runtime vs. test split and added `backend/requirements-dev.txt`. The FastAPI backend starts cleanly with no AWS/Gemini credentials when run in Demo Mode.

## 2. Backend Verification
Ran the full suite with provider selection kept deterministic (`DEMO_FALLBACK=true`, so a stray `LLM_PROVIDER=gemini` in a local `.env` can't skew the provider-selection test).

**Result:** **197 tests passed**, with one non-blocking Starlette/httpx deprecation warning.

## 3. Frontend Integration
The frontend is fully integrated (React + Vite + TypeScript, plain CSS — **not** Tailwind). `npm run build` (TypeScript check + Vite production build) passes, and the UI reaches the backend via the dev proxy / CORS.

## 4. Amazon Bedrock Integration
`BedrockProvider` is implemented using the Bedrock **Converse / tool-calling** API and plugs into the same agent loop as the other providers. During live testing, Bedrock requests **reached AWS** but ran into account/runtime **token throttling**, which prevented a reliable final live Bedrock run.

**Honesty note:** Bedrock *is* implemented; only the live throttled demo could not be relied on. We do not claim the recorded demo is powered by Bedrock.

## 5. Provider Abstraction
`GeminiProvider` (and the deterministic `DemoProvider`) were added **without changing** the MCP tools, agent loop, or service/DB architecture — evidence that the provider abstraction holds. Selection is by **explicit configuration only**, with **no automatic failover** after an API error.

## 6. Gemini
The live Gemini provider was successfully exercised, but a later attempt returned **HTTP 429 Too Many Requests**. Gemini is therefore offered as an **optional** live mode, not required for reviewer reproduction.

## 7. Deterministic Fallback (Reviewer Mode)
`DEMO_FALLBACK=true` provides a rule-based, offline **DemoProvider** so a judge/teammate can reproduce the entire agent → MCP → services → SQLite flow with **no keys and no cloud quota**. There is **no automatic provider switching**. DemoProvider now uses the **real current system date** (no hardcoded `DEMO_TODAY` dependency) and handles generic date-bearing commitments, greetings, thanks, capability questions, missing-date clarification, and safe unsupported input.

## 8. Human-Confirmation Investigation
A prior QA run appeared to show `/apply` or `/cancel` firing automatically. A **clean reproduction** (isolated backend + temp DB, verified through the server access log and direct DB reads, without browser instrumentation) found **no automatic apply/cancel path**:

- Creating a proposal does **not** mutate the plan — it stays `pending`.
- Pending proposals remain `pending` until an **explicit** Apply/Cancel click.
- Reload produces only reads; it neither auto-applies nor auto-cancels.
- **Duplicate apply is protected** (HTTP 409); applying a cancelled change is also rejected.

The earlier report was attributed to HMR/reload plus `fetch` instrumentation, not an application defect.

**Status:** Blocked pending access.

## 5. Frontend and End-to-End Integration
**Issue:** The frontend files in the current repository were found to be empty when inspected.

**Impact:** The complete browser-to-backend user flow cannot yet be verified from this checkout.

**Next step:** Check with the frontend teammate for the latest implementation and integrate it after reviewing their branch.

**Status:** Pending team integration.

> **Update (Member 5, 2026-10-22):** Resolved in `feature/member-5-dashboard`.
> The frontend is no longer empty, and the browser-to-backend flow was exercised
> end-to-end (create → plan → conflict detection → proposal → confirm modal →
> apply → SQLite persistence, plus the cancel path) against a live FastAPI
> instance running `DEMO_FALLBACK=true`. Commands and evidence:
> `docs/dashboard_qa.md`. Live Bedrock is still open in §4.

## 6. Documentation and Submission Preparation
**Work completed:** Updated the project README, created the architecture diagram, and prepared the demo script. These files were committed and pushed to the `main` branch.

**Next step:** Validate the final demo against the integrated application, record the demo, and prepare the submission materials.

**Status:** Documentation committed; final demo and submission remain pending.

## 9. Integration / Testing
Backend and frontend final integration is verified (suite green + production build green + safety lifecycle confirmed at the HTTP/service layer). Remaining submission items (video, Devpost fields, teammates) are handled outside the repository.
