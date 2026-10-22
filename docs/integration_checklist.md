# ActionBridge AI — API Integration Checklist

## Purpose
Track frontend/backend integration and end-to-end verification. Items are marked
complete only when actually observed.

## 1. Backend Availability
- [x] FastAPI server starts locally (Demo Mode, no AWS/Gemini keys).
- [x] API documentation opens at http://127.0.0.1:8000/docs.
- [x] Backend test suite passes: **197 passed** (one non-blocking warning).
- [x] All routes exercised with the integrated frontend / HTTP client.

## 2. API Endpoints (verified at the HTTP/service layer)

| Endpoint | Expected check | Status |
|---|---|---|
| POST /api/chat | Chat returns grounded reply + tool trace | Verified |
| GET /api/commitments | Returns saved commitments | Verified |
| GET /api/plan | Returns rebuilt plan | Verified |
| GET /api/conflicts | Returns what-if conflicts for a move | Verified |
| GET /api/changes/{change_id} | Returns pending diff | Verified |
| POST /api/changes/{change_id}/apply | Applies once; duplicate → 409 | Verified |
| POST /api/changes/{change_id}/cancel | Cancels; plan unchanged | Verified |

## 3. Frontend Checks
- [x] Frontend builds (`tsc --noEmit && vite build`) and starts (`npm run dev`).
- [x] API base URL resolves correctly (Vite proxy / CORS; `VITE_API_BASE_URL` override supported).
- [x] Chat loading and error states implemented; tool-call badges render.
- [x] Commitments, plan, conflicts and confirmation modal wired to real endpoints.
- [x] Apply/Cancel are explicit user actions; Close/Esc/backdrop are UI-only.
- [x] No secrets exposed in frontend code.

## 4. End-to-End Scenarios
- [x] Message → correct tool effect (verified in the suite + HTTP lifecycle).
- [x] Proposal does not auto-apply; reload does not auto-apply/cancel.
- [x] Persistence across sessions/restart (SQLite).
- [x] Apply updates state once; Cancel preserves state.
- [~] Physical browser click-through is limited by the automation environment
      (hidden native view); equivalent behavior is verified at the HTTP layer
      and confirmed by the confirmation-flow investigation.

## 5. Optional Providers
- [x] Deterministic Demo Mode works with no credentials (reviewer path).
- [~] Gemini implemented; exercised live but returned HTTP 429 — optional, not
      required for judging.
- [~] Bedrock implemented (Converse + tool calling); requests reached AWS but hit
      token throttling — not claimed as the recorded demo engine.

## Current Blockers
- None within the repository for the no-key reviewer path.
- External submission items only: `<3` min video, public URL, Devpost fields,
  teammates (tracked outside this repo).

## Completion Rule
Only mark a pending item complete after observing and recording its actual result.
