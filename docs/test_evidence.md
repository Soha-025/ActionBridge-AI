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
