# ActionBridge AI — Demo Script

## Goal

Show how ActionBridge AI **understands** commitments, **remembers** them across
sessions, **reasons** about a schedule change, **proposes** a replan, and
**adapts** the real plan only after the human confirms.

Core flow: **Understand → Remember → Reason → Act → Adapt**

## Recommended recording mode

Run the app in **deterministic offline Demo Mode** for a reliable, reproducible
recording:

```
DEMO_FALLBACK=true        # no Gemini key, no AWS credentials
```

- Use the **real current system date**. Do **not** set a fixed `DEMO_TODAY` —
  Demo Mode anchors relative dates (`today`, `tonight`, `tomorrow`, weekdays) to
  the actual current date.
- **Gemini** (`LLM_PROVIDER=gemini` + `GEMINI_API_KEY`) is an *optional* live AI
  mode. It has worked live but can hit rate limits (HTTP 429), so do not depend
  on it for the recording.
- **Amazon Bedrock** is an implemented provider (Bedrock Converse + tool calling),
  but live requests have hit account/runtime token throttling. Do **not** claim
  the recorded demo is powered by Bedrock unless it genuinely is.

## 1. Pre-Recording Checks

- [ ] Clean demo DB / state (use a fresh or isolated `DB_PATH` so the real demo
      database is not disturbed).
- [ ] Backend running (`DEMO_FALLBACK=true python -m uvicorn main:app --port 8000`).
- [ ] Frontend running (`npm run dev`) and able to reach the backend.
- [ ] Real current date is being used (no `DEMO_TODAY`, no `VITE_DEMO_TODAY`).
- [ ] Exact scenario rehearsed (below).
- [ ] **Cancel** path checked (plan unchanged).
- [ ] **Apply** path checked (plan updates once, then persists).
- [ ] **Persistence** checked (restart / new session keeps data).
- [ ] Rehearse the complete successful run **three times**.
- [ ] Keep the final recording **under 3 minutes**, in English.

## 2. Demo Walkthrough

### Step 1 — Understand: add commitments
In the Assistant, type:

> I have a Statistics quiz Thursday, an AI assignment Friday and a presentation Saturday.

Expected: three commitments are created and persisted; a plan is built from the
stored deadlines; the commitments appear in the dashboard.

### Step 2 — Remember: retrieve saved information
Restart the backend or open a new session **without deleting the database**, then ask:

> What should I work on today?

Expected: the answer is grounded in the previously **saved** commitments/plan —
demonstrating persistent context across sessions.

### Step 3 — Reason: change a deadline
Type:

> My AI assignment deadline changed from Friday to Wednesday.

Expected: the agent identifies the affected commitment, runs a conflict check,
and **proposes** an updated plan — **without applying it**. A pending change is
created; the live plan is not yet mutated.

### Step 4 — Review the impact
Open the confirmation modal (and/or the Conflicts view).

Expected: the diff shows the old date → new date and any affected/rebalanced
tasks, with the message "Nothing has been applied yet." The user reviews before
confirming.

### Step 5 — Act: confirm the change
Click **Apply changes** in the modal.

Expected: the backend applies the pending change once (atomically); the
dashboard refreshes to the updated plan; the change is marked **applied**. The
model cannot perform this step — it is an explicit human action.

### Step 6 — Verify the result
Check the updated plan/commitments and refresh the page.

Expected: only the intended change applied; the new deadline is reflected; data
persists after refresh.

## 3. Cancellation Check

Repeat Step 3 and choose **Cancel proposal** instead of Apply.

Expected: the proposed change is cancelled and the existing plan remains
**unchanged**. (Closing the modal with the X / Esc / backdrop also dismisses the
UI only — it does **not** cancel or apply the server-side proposal.)

## 4. Technical Points to Explain

- **Frontend:** React, Vite, TypeScript, styled with **plain CSS** (no Tailwind).
- **Backend:** FastAPI REST API.
- **Providers:** one abstraction over Amazon Bedrock (Converse + tool calling),
  Google Gemini, and a deterministic DemoProvider — selected by explicit config
  only, never auto-switched on failure.
- **Tools:** an in-process **MCP** server exposing exactly five model-facing
  tools: `create_commitment`, `get_commitments`, `create_plan`,
  `detect_conflicts`, `propose_replan`.
- **Persistence:** SQLite stores commitments, plan items, and pending changes.
- **Human approval:** `apply_changes` / `cancel_change` are **not** reachable by
  the model; they run only through user-triggered HTTP endpoints.

Describe only what is verified in the current build. In Demo Mode the deterministic
provider runs the real agent → MCP → services → SQLite loop end to end.

## Closing Line

> "ActionBridge doesn't just tell you what to do. It helps your plan adapt when
> life changes — and only after you say so."
