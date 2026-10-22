# ActionBridge AI — Test Evidence

## 1. Backend Test Suite
- Command: `python -m pytest -q`
- Result: 160 tests passed.
- Warning: One non-blocking Starlette deprecation warning.
- Status: Passed in the local environment.

## 2. Frontend Production Build
- Command: `npm.cmd run build`
- Result: TypeScript check and Vite production build completed successfully.
- Status: Passed in the local environment.

## 3. Local API Startup
- Command: `python -m uvicorn main:app --reload`
- Result: FastAPI application started successfully in a previous local check.
- API documentation: http://127.0.0.1:8000/docs
- Status: Previously verified locally.

## 4. Demo Seed Data
- Reference date: 2026-10-26
- Database: Isolated test database (`actionbridge_test.db`)
- First run: Three expected commitments created.
- Second run: Existing commitments detected without duplication.
- Status: Previously verified locally.

## 5. Documentation
- README.md
- docs/architecture.png
- docs/demo_script.md
- FRICTION_LOG.md
- docs/integration_checklist.md

## 6. Pending Verification
- Frontend integration and browser-based end-to-end flow.
- Live Amazon Bedrock access and agent/tool execution.
- Final demo recording and submission.
- Final demo database preparation, coordinated with the team.

## Notes
The backend test suite and frontend production build passed in the current local checkout. Previously verified checks are identified as such. Pending items must be tested before being marked complete.

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
- Still **not verified**: live Amazon Bedrock (§6 / `FRICTION_LOG.md` §4), estimated hours
  (no backend field), plan-step completion transitions (no endpoint), physical devices, screen
  readers, non-Chromium browsers.

The "Frontend integration and browser-based end-to-end flow" item in §6 is covered for the
dashboard panels by this section; live Bedrock in §6 remains genuinely open.
