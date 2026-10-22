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
required for reviewer reproduction (Demo Mode covers the full loop).
