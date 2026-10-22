# ActionBridge AI — Demo Script

## Goal

Demonstrate how ActionBridge AI understands commitments, remembers them,
reasons about schedule changes, proposes a replan, and adapts after confirmation.

Core flow: Understand → Remember → Reason → Act → Adapt

## 1. Pre-Demo Checklist

- Start the backend and confirm that the API is available.
- Set the demo date consistently using `DEMO_TODAY`.
- Load the demo commitments using `backend/seed.py`.
- Confirm that the commitments and plan are available through the API.
- Verify AWS credentials and Amazon Bedrock model access before attempting
  the live AI demo.
- Keep a clean backup recording in case the live demo encounters an issue.

## 2. Demo Walkthrough

### Step 1 — Understand: Add commitments

Enter this message in the chat:

"I have a Statistics quiz Thursday, AI assignment Friday and presentation Saturday."

Expected result:
- The agent identifies three commitments.
- The commitments appear in the commitments panel.
- A plan is generated from the stored commitments.

### Step 2 — Remember: Retrieve saved information

Restart the backend or open a new session without deleting the database.

Ask:

"What should I work on today?"

Expected result:
- The answer is based on previously stored commitments and plan data.
- The commitments remain available after the restart.

### Step 3 — Adapt: Change a deadline

Enter:

"My AI assignment deadline changed from Friday to Wednesday."

Expected result:
- The agent identifies the affected commitment.
- The system checks for conflicts and affected plan items.
- The agent proposes an updated plan rather than applying it immediately.

### Step 4 — Reason: Review the impact

Review the Conflicts panel and the proposed changes.

Expected result:
- Relevant conflicts or affected tasks are explained.
- The proposed plan shows what will change.
- The user can review the proposed update before confirming it.

### Step 5 — Human control: Confirm the changes

When the confirmation modal appears, review the old and proposed plan.

Choose **Apply Changes** to confirm the update.

Expected result:
- The backend applies the pending change.
- The dashboard refreshes to show the updated plan.
- The pending change is marked as applied.

Important: proposing a replan must not apply it automatically.
The user confirmation triggers the apply endpoint.

### Step 6 — Verify the result

Check the updated plan and commitments.

Expected result:
- The new deadline is reflected correctly.
- Only the intended changes are applied.
- Stored data remains available after refreshing the dashboard.

## 3. Cancellation Check

Repeat the deadline-change scenario and choose **Cancel** instead of
Apply Changes.

Expected result:
- The proposed change is cancelled.
- The existing plan remains unchanged.

## 4. Technical Points to Explain

- **Frontend:** React, Vite and Tailwind CSS (planned stack).
- **Backend:** FastAPI REST API.
- **AI:** Amazon Bedrock Converse API for agent interactions and tool use.
- **Tools:** MCP-based tools for commitments, planning, conflict detection
  and proposed changes.
- **Persistence:** SQLite stores commitments, plan items and pending changes.
- **Human approval:** Applying a proposed replan requires an explicit user action.

Describe only the components and integrations that have been verified in the
current build. Do not claim a live Bedrock or complete frontend integration
unless it has been tested.

## 5. Recording Checklist

- Reset or prepare the demo data before recording.
- Confirm the displayed dates match the configured demo date.
- Rehearse the complete flow at least three times.
- Check both Apply and Cancel paths.
- Keep the final recording concise and easy to follow.
- Save a backup recording of a successful run.

## Closing Line

"ActionBridge doesn't just tell you what to do. It helps your plan adapt
when life changes."
