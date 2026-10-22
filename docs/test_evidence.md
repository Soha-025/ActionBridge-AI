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
