# ActionBridge AI — API Integration Checklist

## Purpose
Track frontend/backend integration and end-to-end verification.
Do not mark a check complete until it has been tested.

## 1. Backend Availability
- [x] FastAPI server starts locally.
- [x] API documentation opens at http://127.0.0.1:8000/docs.
- [x] Existing backend test suite passes locally (109 passed).
- [ ] Verify all required routes with the integrated frontend.

## 2. API Endpoints

| Endpoint | Expected check | Status |
|---|---|---|
| POST /api/chat | Send a chat request and verify the response | Pending |
| GET /api/commitments | Load saved commitments | Pending |
| GET /api/plan | Load the plan | Pending |
| GET /api/conflicts | Load scheduling conflicts | Pending |
| GET /api/changes/{change_id} | Retrieve a proposed change | Pending |
| POST /api/changes/{change_id}/apply | Apply a valid proposed change | Pending |
| POST /api/changes/{change_id}/cancel | Cancel a proposed change | Pending |

## 3. Frontend Checks
- [ ] Frontend starts successfully.
- [ ] API base URL is configured correctly.
- [ ] Chat loading and error states work.
- [ ] Commitments and plan display correctly.
- [ ] Conflicts display correctly when present.
- [ ] Apply/cancel actions show clear confirmation and results.
- [ ] Empty states and API errors are handled.
- [ ] No secrets are exposed in frontend code.

## 4. End-to-End Scenarios
- [ ] Send a message through the UI.
- [ ] Verify the expected commitment or response.
- [ ] Refresh the page and verify persisted data where applicable.
- [ ] Review a scheduling conflict.
- [ ] Test apply and cancel flows with appropriate demo data.
- [ ] Verify the UI reflects changes after an action.

## 5. External Dependencies
- [ ] Confirm authorized AWS Bedrock access and model configuration.
- [ ] Verify live agent/tool execution.
- [ ] Run the complete demo without manual workarounds.

## Current Blockers
- Frontend implementation has not yet been verified in the current checkout.
- Live Bedrock access has not been verified because AWS access is unavailable.

## Completion Rule
Only mark a pending item complete after observing and recording its actual result.
