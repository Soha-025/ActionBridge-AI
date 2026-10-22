# ActionBridge AI — Friction Log

## 1. Local Environment Setup
**Issue:** Setting up the Python environment and installing backend dependencies required several terminal steps.

**Resolution:** Installed the dependencies and pytest, then successfully started the FastAPI server locally at `http://127.0.0.1:8000`.

**Status:** Resolved for local backend development.

## 2. Backend Test Verification
**Issue:** The backend needed to be tested before integration.

**Resolution:** Ran `python -m pytest -q`.

**Result:** 109 tests passed, with one non-blocking Starlette deprecation warning.

**Status:** Backend tests passed in the local environment.

## 3. Repeatable Demo Data
**Issue:** Demo data needed to be reproducible without creating duplicate commitments on repeated runs.

**Resolution:** Set `DEMO_TODAY=2026-10-26` and used a separate test database through `DB_PATH`. Ran `seed.py` twice.

**Result:** The first run created the expected three commitments; the second detected existing records instead of duplicating them.

**Status:** Verified locally using the isolated test database.

## 4. AWS Bedrock Access
**Issue:** No AWS account or hackathon-provided AWS credits are currently available. AWS CLI is also not installed.

**Impact:** Live Amazon Bedrock model access and the cloud-based agent flow have not been verified.

**Next step:** Confirm whether the team can provide an authorized AWS environment or whether an approved alternative is available. Check any applicable charges before using paid services.

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
