# ActionBridge AI — Test Evidence

## 1. Backend Test Suite
- Command: `python -m pytest -q`
- Result: 109 tests passed.
- Warning: One non-blocking Starlette deprecation warning.
- Status: Passed in the local environment.

## 2. Local API Startup
- Command: `python -m uvicorn main:app --reload`
- Result: FastAPI application started successfully.
- API documentation: http://127.0.0.1:8000/docs
- Status: Verified locally.

## 3. Demo Seed Data
- Reference date: 2026-10-26
- Database: Isolated test database (`actionbridge_test.db`)
- First run: Three expected commitments created.
- Second run: Existing commitments detected without duplication.
- Status: Verified locally.

## 4. Documentation
- README.md: Created and pushed.
- docs/architecture.png: Generated and pushed.
- docs/demo_script.md: Created and pushed.
- FRICTION_LOG.md: Created and pushed.
- docs/integration_checklist.md: Created and pushed.

## 5. Pending Verification
- Frontend integration and browser-based end-to-end flow.
- Live Amazon Bedrock access and agent/tool execution.
- Final demo recording and submission.

## Notes
These results reflect local checks performed so far. Pending items must be tested before being marked complete.

## 6. Member 5 — Dashboard QA (2026-10-22)
Full commands, reproduction steps and the unverified list: `docs/dashboard_qa.md`.

- Frontend build: `npm run build` (`tsc --noEmit && vite build`) — **passed**, exit 0.
- Backend suite re-run on this checkout: `python -m pytest -q` — **160 passed**, 1 non-blocking warning.
  (Supersedes the 109 in section 1: 147 on this branch's base, 160 after Member 3's fallback
  tests arrived with the integration merge below. Use `backend/.venv` — collection needs boto3.)
- Integration merge of `origin/main` into `feature/member-5-dashboard` — **passed**. Member 1's
  deep-link mirroring, `applied` → resync and 422 error flattening are preserved; their six
  superseded inline views were replaced by the M5 panels, and the two behaviours unique to
  those views ("Next up" banner, same-date guard note) were re-implemented in the panels.
  Re-verified in the browser: `?change=<id>` opens the modal, Apply persists to SQLite, strips
  the URL parameter and refreshes the dashboard. Details in `docs/dashboard_qa.md` §7.
- Lint / frontend unit tests: **not available** — neither is configured in `frontend/package.json`.
- Browser check of all three panels in both modes (real API + `VITE_DEMO_MODE`): load, multiple
  rows, completed vs pending, priority, deadline, empty, error+retry, conflict→plan-step matching,
  responsive down to 390px — **passed**, console clean.
- End-to-end flow with `DEMO_FALLBACK=true`: create → plan rebuild → `GET /api/conflicts` →
  `propose_replan` → `GET /api/changes/{id}` → cancel (dates unchanged) → apply after cancel
  (**HTTP 409**) → UI confirm modal apply → new dates read back from SQLite in a separate process
  — **passed**.
- Still **not verified**: live Amazon Bedrock (section 5 / `FRICTION_LOG.md` §4), estimated hours
  (no backend field), plan-step completion transitions (no endpoint), physical devices, screen
  readers, non-Chromium browsers.
