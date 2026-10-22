# Dashboard QA — how to run and test the panels

Owner: Member 5 (Dashboard UI + QA). Covers `TodayPlan`, `CommitmentsPanel`,
`ConflictsPanel` and their integration in `App.tsx`.

Every "Observed" line below was actually run in this environment. Anything that
was not run is listed in section 6 as **not verified** — do not demo it as proven.

## 1. Two run modes

The dashboard can render from two data sources. They are never mixed, and the
mode is always visible in the top bar.

| Mode | Source | Top bar | When to use |
| --- | --- | --- | --- |
| Real API | `src/api.ts` → FastAPI | `N active` (or `Offline`) | normal development and the demo |
| Browser demo | `src/demoData.ts` fixtures | `Demo data` badge + persistent banner | no backend available, layout checks |

Demo mode is opt-in. It only turns on when `VITE_DEMO_MODE` is truthy
(`1`, `true`, `yes`, `on`), it labels its hours as invented, and it does **not**
mock the Assistant — chat still needs the backend.

### Real API mode

```powershell
# terminal 1 — backend
cd backend
python -m venv .venv && .\.venv\Scripts\pip.exe install -r requirements.txt
$env:DB_PATH = ".\actionbridge.db"      # or an isolated file for testing
$env:DEMO_TODAY = "2026-10-26"          # optional: fixed reference date
$env:DEMO_FALLBACK = "true"             # optional: offline agent, see 3.1
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
python seed.py                          # optional: three sample commitments

# terminal 2 — frontend
cd frontend
npm install
npm run dev                              # http://localhost:5173
```

`api.ts` resolves the base URL as `VITE_API_BASE_URL` → else
`http://<host>:8000`. Set `VITE_API_BASE_URL=""` to use the Vite `/api` proxy.

### Browser demo mode

```powershell
cd frontend
npm install
$env:VITE_DEMO_MODE = "true"
npm run dev                              # http://localhost:5173
```

No backend, no `DEMO_FALLBACK`, no network.

## 2. Automated checks

| Command | Result observed here |
| --- | --- |
| `npm run build` (`tsc --noEmit && vite build`) | **Passed**, exit 0. 41 modules, `dist` 185.8 kB JS / 12.3 kB CSS |
| `python -m pytest -q` (backend) | **Passed** — 160 passed, 1 non-blocking Starlette deprecation warning |
| Lint | **Not available** — no linter is configured in `frontend/package.json` |
| Frontend unit tests | **Not available** — no test runner is configured |
| Browser console during the whole sweep | clean (only Vite connect debug + React DevTools info) |

Run pytest with `backend/.venv` — collection needs `boto3`, which system Python here does
not have. `docs/test_evidence.md` still quotes 109 tests; that predates both the 147 this
branch was built on and the 160 that include Member 3's fallback tests.

## 3. Dashboard UI cases

Reproduce each by opening the URL for the mode you want, then following the
"Trigger" column. "Observed" is what actually appeared.

| # | Case | Trigger | Observed |
| --- | --- | --- | --- |
| 1 | Commitments load from API | Real mode, backend seeded | Both panels render server rows; top bar `N active` |
| 2 | Plan load | Real mode | `Today's focus` lists steps whose `planned_date` is the reference date |
| 3 | Conflicts load | Conflicts view, pick a commitment + a new date, `Check conflicts` | Server `reason` string shown verbatim; skeletons + `Checking…` while in flight |
| 4 | Multiple commitments render | Demo mode (7 fixtures) | All rows render, deadline-ordered, `Showing the first N of M` when capped |
| 5 | Completed vs pending distinguishable | Demo mode | Dot colour **and** strikethrough title **and** a text status badge (not colour-only) |
| 6 | Priority displayed | Any mode | `High/Medium` badge; missing priority shows `No priority`, never a guess |
| 7 | Deadline displayed | Any mode | `due Oct 22 (today)` / `(tomorrow)` / `(in 6d)` / `Oct 20 (overdue 2d)` |
| 8 | Empty states | Empty DB, or demo with nothing left | Informational copy in each panel; Conflicts panel explains a check needs a proposed move |
| 9 | Error states + retry | Stop the backend, then Refresh | `role="alert"` error with a Retry button that re-fetches successfully |
| 10 | Conflict IDs matched to plan tasks | Conflicts view | Each conflict resolves `commitment_id` against `/api/plan` and shows `plan #10: AI quiz · planned`, plus `Unresolved — awaiting your confirmation` |
| 11 | Responsive layout | Narrow the window / DevTools widths | Two-column grid at ≥1024px, single column below 860px, badges wrap, no horizontal scroll on 390px |
| 12 | Demo vs real is unmistakable | Toggle `VITE_DEMO_MODE` | Demo: `Demo data` badge + banner naming the data as invented. Real: neither |

### 3.1 What each panel promises

- `TodayPlan` never shows estimated hours while `GET /api/plan` has no effort
  field; it prints why instead of inventing a number.
- `CommitmentsPanel` sorts by deadline first, then id, matching
  `services.get_commitments`.
- `ConflictsPanel` is read-only. It only displays conflicts the backend
  returned for a specific proposed move, and it cannot apply or cancel anything.

## 4. Full application flow (real backend)

These were run against a live FastAPI + SQLite instance using
`DEMO_FALLBACK=true`. That is the **real** agent loop, MCP boundary, services
layer and database — only the model is the deterministic offline provider
(section 6 explains why Bedrock is unavailable).

Sequence and observed evidence:

1. **Create** — `POST /api/chat` "I have an AI quiz Thursday and a stats
   assignment Friday" → tool calls `create_commitment` ×2, rows `1 AI quiz
   2026-10-22`, `2 stats assignment 2026-10-23` in `GET /api/commitments`.
2. **Plan rebuild** — `GET /api/plan` returns one `planned` step per active
   commitment with `planned_date == due_date`.
3. **Conflict detection** — `GET /api/conflicts?commitment_id=2&new_due_date=2026-10-22`
   → `type: collision` with the reason naming `AI quiz`.
4. **Proposed change** — `POST /api/chat` "Move my stats assignment deadline to
   Thursday" → agent called `get_commitments` → `detect_conflicts` →
   `propose_replan`, returned a real `change_id` and `pending_change_id`, reply
   says "has NOT been applied".
5. **Inspect before confirming** — `GET /api/changes/{id}` → `status: pending`,
   `summary`, `old_due_date`/`new_due_date`, and the rebalanced `moved` list.
6. **Cancel** — `POST /api/changes/{id}/cancel` → `status: cancelled`, and
   `GET /api/commitments` still shows the original dates. Nothing leaked.
7. **Guard on a dead change** — applying the cancelled id → **HTTP 409**
   `… is not pending (status='cancelled'); it cannot be applied again.`
   Cancelling an already-cancelled change is idempotent (HTTP 200).
8. **Apply (the confirmation boundary)** — a fresh proposal was opened in the UI
   through **Assistant → Review proposal → Apply changes** in `ConfirmModal`,
   which is the only path that mutates: `pending_changes` row became `applied`,
   `commitments` now `1 AI quiz 2026-10-28`, and `plan_items` rebuilt.
9. **Persistence** — read from SQLite in a **separate process** after each step:
   the dates and change statuses are on disk, not just in memory. The dashboard
   reflected it immediately (`due Oct 28 (in 6d)`, and the step left
   `Today's focus`).
10. **Chat cannot confirm** — `POST /api/chat` "apply it" replies that changes
    are never applied through chat, with `tool_calls: []`.

### 4.1 Manual walkthrough for the demo

1. Real mode, seeded DB. **Assistant** → "I have a Statistics quiz Thursday, an
   AI assignment Friday and a presentation Saturday".
2. **Today** → three steps; **Upcoming** → deadlines sorted, priority badges.
3. **Assistant** → "My AI assignment deadline changed from Friday to Wednesday"
   → proposal card → **Review proposal** → diff modal → **Apply changes**.
4. **Today/Upcoming** refresh shows the new date. Repeat the same sentence and
   click **Cancel proposal** to show that the dates stay put.
5. **Conflicts** → pick the moved commitment + a date that collides → warning
   card naming the affected plan step, and the explicit "Nothing has been
   applied." line.

## 5. Known limits of the current backend

These constrain what the UI can honestly show; they are backend gaps, not
dashboard bugs.

| Gap | Effect on the dashboard |
| --- | --- |
| `GET /api/plan` has no effort/hours field | Estimated hours cannot be shown in real mode |
| No endpoint marks a plan step completed | Completion styling is exercised by demo fixtures only |
| Conflicts exist only for a proposed move | There is no "all current conflicts" list to render |
| Conflict objects carry `commitment_id`, not `affected_plan_item_ids` | The panel derives affected plan steps by matching `commitment_id` against the plan |
| IDs are integers, not `comm_01` strings | The UI shows the server's real ids |

If the team wants any of these, it is an API-contract change and needs Member 1
or 2's agreement first.

## 6. Not verified — do not claim these

- **Live Amazon Bedrock.** All chat evidence above used `DEMO_FALLBACK=true`.
  Bedrock is still blocked per `FRICTION_LOG.md` §4, so nothing has been proven
  against the real model.
- **Initial-load skeletons.** `LoadingRows` is proven through the conflict check
  (2 skeletons + `Checking…` → resolved card). The first paint of the Today view
  refreshes in place instead of skeletonising, and the API answers too fast to
  catch it by hand — so that path is code-verified only, not visually captured.
- **Physical devices.** Responsiveness was checked with emulated widths, not a
  real phone or tablet.
- **Screen-reader walkthrough.** Landmarks, `role="status"`, `role="alert"` and
  visually-hidden labels are in the markup; nobody has listened to it.
- **Cross-browser.** Only Chromium was exercised.

## 7. Integration with Member 1's `main`

`feature/member-5-dashboard` merges `origin/main` rather than rebasing it, because the
dashboard commit was already published. Both members' work is present in the result:

| Area | What happened |
| --- | --- |
| `App.tsx` | Member 1's deep-link mirroring in `openProposal`, their `applied` → `load("refresh")` resync and their dependency-array fix merged in untouched; my panel mounts replaced their six superseded inline views |
| `api.ts`, `types.ts` | Auto-merged — Member 1's 422 `formatValidationIssues` and M5 props block sit beside my `hours` shaping |
| `ConfirmModal.tsx` | Entirely Member 1's; my QA re-ran the apply flow against their version |
| `package-lock.json` | Member 1's version kept verbatim. `package.json` is byte-identical on both branches and both lockfiles resolve the same 116 packages, so nothing was lost — only 14 npm `libc` platform lines differ |

Two user-visible behaviours that lived inside Member 1's deleted inline views were
re-implemented in **my** files rather than dropped:

- "Next up: …" `role="status"` banner → `CommitmentsPanel.tsx` behind the new opt-in
  `showNextUp` prop, enabled only on the Today mount.
- The same-date guard note ("nothing would move") → `ConflictsPanel.tsx`, using
  `isIsoDate` from `format.ts`.

Post-merge browser evidence, real API mode: `Next up: stats assignment · due Oct 22`
rendered; selecting a target date equal to the current due date produced the same-date
note; `/?change=<id>` opened Member 1's modal directly; **Apply changes** persisted
(`AI quiz` moved to `2026-10-26` in SQLite), closed the modal, stripped `?change=` from the
address bar and resynced the panel to `due Oct 26 (in 4d)`. Console stayed clean. Demo mode
still shows its banner, badge and synthetic hours.

### Contract mismatch — resolved on this branch

Closed without touching Member 1's files. `types.ts`, `api.ts` and `ConfirmModal.tsx` are
byte-identical to `origin/main`; everything is inside M5's own components.

- **`components/ConflictChecker.tsx` (new, container)** owns the picker state, the one
  contextual `GET /api/conflicts` call and its `AbortController` lifecycle. It fires only
  when a real `commitment_id` and a valid `new_due_date` both exist — there is still no
  "list all conflicts" request anywhere, because no such endpoint exists.
- **`components/ConflictsPanel.tsx` (presentational)** now declares
  `interface ConflictsPanelProps extends ConflictsPanelContract`, importing Member 1's
  `ConflictsPanelProps { conflicts: Conflict[] }` from `../types`. `conflicts` carries the
  last successful check (`[]` before one has run or after a failure) and
  `checked: ConflictsQuery | null` names the move it describes. No fetching remains in the
  file, which is what their rule asked for.
- **`App.tsx`** changed by two lines: the import and the `<ConflictChecker>` element at the
  `view === "conflicts"` mount. Its seven props are unchanged, so demo mode still injects
  `DashboardSource.getConflicts` and the offline path keeps working.

`TodayPlanProps` and `CommitmentsPanelProps` needed nothing: `TodayPlan.tsx` and
`CommitmentsPanel.tsx` already use exactly those names and types and merely add optional
props, so Member 1's declared shape is a subset of what is rendered. One caveat stays on
record — their comment also says panels must not *derive*. The panels still sort rows and
pick tone classes locally; moving that into `App.tsx` is a design call for Member 1, not a
defect, and I have not made it for them.

Evidence after the split, real API on `:5173`: no `/api/conflicts` request existed before
the button was clicked (the network list held only the two dashboard reads); one call,
`commitment_id=1&new_due_date=2026-10-22`, returned the collision row with the backend's own
reason text and its matched `plan #33: stats assignment · planned`; choosing a target equal
to the current due date still printed the same-date note; a zero-conflict date replaced the
earlier results instead of stacking them; stopping the backend showed `ab-banner--error`
("Cannot reach the ActionBridge backend…") with **Retry**, which recovered to the
zero-conflict banner once the server was back up; switching views mid-request aborted
cleanly. Demo mode on `:5174` produced two collision rows with plan steps through the same
container, and a non-existent option value left the button correctly disabled. Console was
clean apart from the one `ERR_CONNECTION_REFUSED` I caused on purpose. Member 1's flow on the
same build: the chat proposal mirrored `?change=<id>`, that link reopened their modal on a
fresh load, **Apply changes** persisted (`AI quiz` → `2026-10-28` in SQLite), cleared the
param, and the resync fired — `POST …/apply` was followed by
`GET /api/commitments` + `GET /api/plan`.
