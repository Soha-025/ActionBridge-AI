# ActionBridge AI — Test Evidence

All results below were re-run on the current `main` checkout (HEAD `4a09cfe`) and
reflect the actual code, not assumptions.

## 1. Backend Test Suite
- Command: `LLM_PROVIDER=bedrock python -m pytest -q` (provider selection kept
  neutral so a local `.env` cannot force Gemini and skew the provider test).
- Result: **197 tests passed**, one non-blocking Starlette/httpx deprecation warning.
- Status: Passed.

## 2. Frontend Production Build
- Command: `npm run build` (`tsc --noEmit && vite build`).
- Result: TypeScript check + Vite production build completed successfully
  (`dist/` emitted; 35 modules transformed).
- Dependency check: `package.json` and `package-lock.json` present and tracked;
  `npm ls --depth=0` reports a clean, consistent tree. `npm install` / `npm ci`
  and `npm run build` work.
- Status: Passed.

## 3. Model-Facing Tool / Safety Boundary
- Model-facing MCP tools are **exactly five**:
  `create_commitment`, `get_commitments`, `create_plan`, `detect_conflicts`,
  `propose_replan`.
- `apply_changes`, `cancel_change`, `get_change` are **not** model-facing; they
  are reachable only through the human-triggered HTTP endpoints.

## 4. Human-Confirmation Lifecycle (clean reproduction, isolated temp DB)
- Creating a proposal does **not** mutate the live plan — it stays `pending`.
- Apply performs the change once; a duplicate apply is rejected (**HTTP 409**);
  applying a cancelled change is also rejected.
- Cancel leaves the plan **unchanged**.
- Reload produces only reads; it neither auto-applies nor auto-cancels.
- Verified via the server access log + direct SQLite reads, without browser
  instrumentation.

## 5. Local API Startup
- Command: `DEMO_FALLBACK=true python -m uvicorn main:app --port 8000`.
- Result: FastAPI starts and serves requests with **no AWS credentials and no
  Gemini key** (deterministic offline Demo Mode); backend uses the real current date.

## 6. Demo Seed Data
- `backend/seed.py` creates the three sample commitments idempotently (a second
  run detects existing records instead of duplicating them).
- Uses the real current date by default; `DEMO_TODAY` is an optional
  service-level override for controlled tests only.

## 7. Documentation
- README.md, FRICTION_LOG.md, docs/demo_script.md, docs/architecture.png,
  docs/integration_checklist.md, `LICENSE` (MIT).

## Remaining / Non-Repository Items
- `<3` minute demo video, public video URL, Devpost fields, teammates, and the
  final submission are external (Devpost) tasks, tracked outside this repo.
- Physical browser UI-click end-to-end is limited by the automation environment
  (hidden native view); the equivalent flows are verified at the HTTP/service
  layer and by the test suite.

## Notes
The backend suite and frontend production build pass in the current checkout.
Live Bedrock/Gemini runs depend on external quota and are intentionally **not**
required for reviewer reproduction (Demo Mode covers the full loop). Previously
verified checks are identified as such; pending items must be tested before being
marked complete.

## 7. Member 5 — Dashboard QA (2026-10-22)
Full commands, reproduction steps and the unverified list: `docs/dashboard_qa.md`.

- Backend suite re-run on this checkout: `backend/.venv/Scripts/python.exe -m pytest -q` —
  **160 passed**, agreeing with §1 (this branch's base had 147; Member 3's fallback tests
  arrived with the integration merge below). Run it from `backend/.venv` — collection needs boto3.
- Frontend build: `npm run build` (`tsc --noEmit && vite build`) — exit 0, 41 modules,
  185.8 kB JS / 12.3 kB CSS; the same check §2 records, with the dashboard bundle numbers.
- Integration merge of `origin/main` into `feature/member-5-dashboard` — **passed**. Member 1's
  deep-link mirroring, `applied` → resync and 422 error flattening are preserved; their six
  superseded inline views were replaced by the M5 panels, and the two behaviours unique to
  those views ("Next up" banner, same-date guard note) were re-implemented in the panels.
  Re-verified in the browser: `?change=<id>` opens the modal, Apply persists to SQLite, strips
  the URL parameter and refreshes the dashboard. Details in `docs/dashboard_qa.md` §7.
- Lint / frontend unit tests: **not available** — neither is configured in `frontend/package.json`.
- Panel API-contract alignment (`member 5` follow-up) — **passed**. `types.ts`, `api.ts` and
  `ConfirmModal.tsx` are unchanged; a new `components/ConflictChecker.tsx` container owns the
  contextual `GET /api/conflicts` call, and `ConflictsPanel.tsx` now extends Member 1's declared
  `ConflictsPanelProps { conflicts }` instead of fetching. Re-verified in the browser: no request
  before the button click, collision row + matched plan step, same-date note, no stacked results,
  error state + **Retry** recovery, abort on unmount; Member 1's deep link, modal and
  apply → resync still work. `npm run build` exit 0 and `pytest -q` 160 passed on the same tree.
- Browser check of all three panels in both modes (real API + `VITE_DEMO_MODE`): load, multiple
  rows, completed vs pending, priority, deadline, empty, error+retry, conflict→plan-step matching,
  responsive down to 390px — **passed**, console clean.
- End-to-end flow with `DEMO_FALLBACK=true`: create → plan rebuild → `GET /api/conflicts` →
  `propose_replan` → `GET /api/changes/{id}` → cancel (dates unchanged) → apply after cancel
  (**HTTP 409**) → UI confirm modal apply → new dates read back from SQLite in a separate process
  — **passed**.
- Completeness audit on `742f625` — `npm run typecheck` exit 0, `npm run build` exit 0
  (41 modules, 185.9 kB JS / 12.31 kB CSS), `pytest -q` **160 passed**, browser console clean.
  Four items that were previously only code-verified are now **observed**: first-paint skeletons
  (7 on Today, 2 on Conflicts, correct `role="status"` + `aria-live` and labels), empty states
  against a genuinely empty database (`{"commitments":[]}` / `{"plan_items":[]}`) in all three
  panels, `Cancel proposal` driven through the UI (server reports `cancelled`, dates unchanged,
  `?change=` stripped, applying the dead id gives **HTTP 409**), and the top-bar `Offline` badge
  with **Retry** recovery. Responsive behaviour was re-measured with same-origin iframes and the
  recorded breakpoints corrected: two columns require **>1024px**, row wrap requires **≤860px**.
  One demo-only source change came out of it — `demoData.ts` now reads `VITE_DEMO_LATENCY_MS`
  (default still 350 ms), which is what makes the skeleton check reproducible. Full table of
  results in `docs/dashboard_qa.md` §8.
- Still **not verified**: live Amazon Bedrock (§6 / `FRICTION_LOG.md` §4), estimated hours
  (no backend field), plan-step completion transitions (no endpoint), physical devices, screen
  readers, non-Chromium browsers.

The "Frontend integration and browser-based end-to-end flow" item in §6 is covered for the
dashboard panels by this section; live Bedrock in §6 remains genuinely open.
