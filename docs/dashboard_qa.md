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
| `npm run typecheck` (`tsc --noEmit`) | **Passed**, exit 0 — this is the only frontend "test" that exists |
| `npm run build` (`tsc --noEmit && vite build`) | **Passed**, exit 0. 41 modules, `dist` 185.9 kB JS / 12.31 kB CSS |
| `python -m pytest -q` (backend) | **Passed** — 160 passed, 1 non-blocking Starlette deprecation warning |
| Lint | **Not available** — no linter is configured in `frontend/package.json` |
| Frontend unit tests | **Not available** — no test runner is configured |
| Browser console during the whole sweep | clean (only Vite connect debug + React DevTools info) |

Re-run on 2026-10-22 against `742f625` (all three green), and again after the
`VITE_DEMO_LATENCY_MS` change described in §8.

Run pytest with `backend/.venv` — collection needs `boto3`, which system Python here does
not have. The "109 tests" line that used to sit in `docs/test_evidence.md` §1 was reconciled
by the docs merge `742f625`; that file now reports 160 in both §1 and §7, matching this table.

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
| 8 | Empty states | Real mode against an **empty** database (`GET` returns `{"commitments":[]}`, `{"plan_items":[]}`) | `📌 Nothing scheduled here yet` and `🗂️ No commitments to show` on Today; the Upcoming mount shows the same with its own `emptyHint`; with nothing to pick, Conflicts shows `🧭 No commitments to check` and renders **no** select, date field or button |
| 9 | Error states + retry | Backend stopped, then `Refresh` | Top bar badge flips `2 active` → **`Offline`** (2.2 s), both panels keep their last rows and add a `role="alert"` `ab-banner--error` — "Cannot reach the ActionBridge backend… The list below is the last data loaded." — with **Retry**; after the server returns, Retry cleared both alerts in 206 ms and the badge went back to `2 active` |
| 10 | Conflict IDs matched to plan tasks | Conflicts view | Each conflict resolves `commitment_id` against `/api/plan` and shows `plan #10: AI quiz · planned`, plus `Unresolved — awaiting your confirmation` |
| 11 | Responsive layout | Same-origin iframe viewports (media queries follow the iframe) | `.ab-grid--two` is two columns **only above 1024px** — the CSS rule is `@media (max-width: 1024px)`, so 1024 itself is single-column (measured `536px 383px` at 1280, `681px` at 1024). Rows switch to `flex-wrap: wrap` and badges to `max-width: 100% / flex-start` at **860px and below** (`nowrap / 46% / flex-end` at 861). No horizontal overflow and no element crossing the viewport at 390 / 600 / 860 / 861 / 900 / 1023 / 1024 / 1280 |
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
- **Initial-load skeletons — resolved 2026-10-22**, no longer unverified. See §8:
  7 first-paint skeletons (3 plan + 4 commitments) and the conflicts-view skeleton
  were captured in the browser using `VITE_DEMO_LATENCY_MS`. The local real API
  still answers faster than a human can sample the DOM, so that mode remains the
  reproducible way to observe the same `loading` prop path.
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

## 8. 2026-10-22 completeness audit

Everything below was re-run on `742f625`. Items 1–5 were the ones previously
listed as unverified; they are now observed.

Environment used (local only, all git-ignored):

| Port | What |
| --- | --- |
| `:8000` | `DB_PATH=./actionbridge_flow.db DEMO_TODAY=2026-10-26 DEMO_FALLBACK=true uvicorn main:app` |
| `:8002` | same binary, **empty** database (`actionbridge_empty.db`) |
| `:5173` | frontend, real API |
| `:5175` | frontend, `VITE_API_BASE_URL=http://127.0.0.1:8002` (empty-state case) |
| `:5174` | frontend, `VITE_DEMO_MODE=true` |
| `:5176` | frontend, `VITE_DEMO_MODE=true VITE_DEMO_LATENCY_MS=60000` (skeleton case) |

1. **First-paint skeletons — captured.** On `:5176` the loading window is long
   enough to sample: `7` `.ab-skeleton` elements (3 from `TodayPlan`, 4 from
   `CommitmentsPanel`), visually-hidden labels `Loading your plan` and
   `Loading your commitments`, both containers `role="status"` +
   `aria-live="polite"`, card hints `loading…`, `0` rows. Switching to Conflicts
   inside the same window gave `2` skeletons + `Loading commitments for the
   conflict check` with the picker not yet rendered.
2. **Empty states — captured against a real empty backend** (`:5175`, server
   literally returned `{"commitments":[]}` / `{"plan_items":[]}`): results are in
   §3 case 8. The conflicts picker is absent, not merely disabled, when there is
   nothing to select.
3. **Cancel proposal through the UI — captured.** Chat "My AI quiz deadline
   changed from Wednesday to Monday." produced change
   `8380688e0bac4b0392febdf373cc0b21`; the modal opened itself with the diff
   `2026-10-28 → 2026-10-26` and `?change=` mirrored in the URL. **Cancel
   proposal** closed the modal, and 254 ms later the page showed
   `ab-banner--info ab-flash`: "Proposal cancelled on the server. Nothing was
   applied — your plan is unchanged." `GET /api/changes/{id}` then reported
   `status: cancelled`, `/api/commitments` still had `AI quiz` on `2026-10-28`,
   the URL parameter was stripped and `Review proposal` left the top bar. A
   second proposal (`780f126b…`, `stats assignment` `2026-10-22 → 2026-10-27`)
   carried the backend's own wording "This creates schedule pressure with AI
   quiz." and was cancelled the same way. Applying the first (cancelled) id from
   the CLI returned **HTTP 409** `… is not pending (status='cancelled'); it
   cannot be applied again.`
4. **Offline badge + recovery — captured.** §3 case 9.
5. **Responsive — re-measured after the container refactor.** §3 case 11. The
   refactored picker stacks to full width at 390px (`select` 301px, date field
   191px, button 125px, all on their own line, button `disabled`) without
   overflowing.
6. **One source change came out of this audit** — `demoData.ts` now reads
   `VITE_DEMO_LATENCY_MS` (default unchanged at 350 ms, documented in
   `frontend/.env.example`). It is demo-only: the real API path never touches it,
   and it is what makes item 1 reproducible for anyone. `npm run typecheck` and
   `npm run build` were re-run after it and are green.
7. **Tooling caveat worth knowing when you repeat this.** The browser
   accessibility tree reported in this environment contains content that is not
   in the live DOM — strings such as `No dashboard data yet` and `Seed demo
   data`, which `grep` finds nowhere in `frontend/src`. Every observation in this
   section therefore comes from `document.querySelectorAll` and computed-style
   reads, not from the snapshot text.
8. **Console** stayed clean: Vite connect debug and the React DevTools notice
   only.
9. **Artefact and secret scan.** `git status --ignored` shows the three scratch
   databases, `backend/.venv`, `__pycache__`, `.pytest_cache`, `frontend/dist` and
   `node_modules` all ignored and none tracked; no screenshot was added by this
   work; scanning the changed files for credentials found only the sentence in
   `.env.example` that says no secrets belong in the frontend.

## 9. Traceability to `docs/integration_checklist.md`

That checklist is Member 1's file and carries their "only mark a pending item
complete after observing and recording its actual result" rule, so **I have not
ticked any of their boxes.** This table says where the evidence for each already
exists, so they can apply their own rule against it.

| Their item | M5 evidence |
| --- | --- |
| `POST /api/chat` | §4.1, §4.2, §8.3 |
| `GET /api/commitments` / `GET /api/plan` | §3 cases 1–2, §8.2 |
| `GET /api/conflicts` | §7 (request timing, abort, error + Retry) |
| `GET /api/changes/{id}` | §4.5, §8.3 |
| `POST /api/changes/{id}/apply` | §4.8, §7 (apply → resync request order) |
| `POST /api/changes/{id}/cancel` | §4.6 (HTTP) and §8.3 (**through the UI**) |
| Frontend starts / API base URL | §1, §8 environment table (`:5175` proves the override) |
| Commitments and plan display correctly | §3 cases 1–7 |
| Conflicts display correctly | §3 case 3 and 10, §7 |
| Apply/cancel show clear confirmation and results | §4.8, §8.3 |
| Empty states and API errors handled | §3 cases 8–9, §8.1–2 |
| No secrets in frontend code | §8.9 |
| End-to-end scenarios (§4) | §4.1–4.10, §8.3 |
| Bedrock access / live agent execution / demo without workarounds (§5) | **still open** — §6 |

Two team-level discrepancies this audit surfaced, both in files I do not own and
therefore did not edit:

- `README.md` "Development Status" still says the frontend and end-to-end
  integration "still require verification in the shared checkout". §4, §7 and §8
  contradict that, but Member 1 owns the README.
- `step1_today_initial.png` sits at the repository root (300×912, added by
  `603a488`, also present on `origin/main`) and is referenced by nothing. It
  looks like a stray QA screenshot; it is not mine, so I have left it alone.

## 10. Branch state after this audit

`HEAD = 742f625` on `feature/member-5-dashboard`, equal to its remote. The working
tree holds the four files from §8 (my `demoData.ts` and `.env.example` change from
§8.6, this document, and `docs/test_evidence.md` §7).

`origin/main` has moved since the docs merge: it is now `f0ba2fc` ("Update
actionbridge styling and ChatPanel component" — Member 2's voice work,
`ChatPanel.tsx` +163, `actionbridge.css` +46, new `hooks/useSpeech.ts` +364).
A non-destructive `git merge-tree --write-tree` dry run reports exactly one
conflict, `frontend/src/components/actionbridge.css`, because both sides append
their own block after line 453. Both are purely additive with **no shared class
names** (theirs `ab-chat__controls`, `ab-msg__actions`, `ab-speak-btn`,
`ab-mic-btn--live`, `ab-voice-*`; mine `ab-dot--*`, `ab-row__*`, `ab-conflict*`),
so the resolution is to keep both blocks verbatim. That file carries my panel
styles, so it is the one merge where "just take main's version" would have
destroyed M5 work.

### 10.1 The merge, executed with `--no-commit` (2026-10-22)

Before merging, the four uncommitted files were copied to a temp directory outside
the repo and a ref was created at the old tip
(`refs/heads/backup/member-5-before-f0ba2fc-merge` → `742f625`), so both the
uncommitted edits and the pre-merge branch state are recoverable.

`git merge --no-commit --no-ff f0ba2fc` produced the predicted single conflict.
Resolving it needed one detail: because both sides end their last `@media` block
with a `}` on the same logical line, git collapsed the two closing braces into one
shared line below the `>>>>>>>` marker. Keeping both blocks therefore means adding
one `}` back — resolve by deleting only the three markers and re-closing my
`(max-width: 860px)` block. Never `--ours` / `--theirs`, which would drop a side.

Committed as its own merge commit; the staged set at that moment was exactly these
three files and nothing else:

| File | Change |
| --- | --- |
| `frontend/src/components/ChatPanel.tsx` | Member 2's voice work, taken byte-identical from `f0ba2fc` |
| `frontend/src/hooks/useSpeech.ts` | new, Member 2's, 364 lines, taken as-is |
| `frontend/src/components/actionbridge.css` | keep-both: 455 shared + 53 mine + 46 theirs = 554 lines |

Proof that nothing of anyone else's was altered, all measured on the merged tree:

- `git diff --cached f0ba2fc -- ChatPanel.tsx useSpeech.ts` → **empty** (Member 2's
  implementation is exactly main's).
- `git diff HEAD -- App.tsx types.ts api.ts ConfirmModal.tsx TodayPlan.tsx` →
  **empty** (Member 1's deep link, proposal modal, apply/resync and shared types are
  untouched).
- `git diff --cached HEAD -- actionbridge.css` → **46 insertions, 0 deletions**;
  `git diff --cached f0ba2fc -- actionbridge.css` → **53 insertions, 0 deletions**.
  Each side's block survives as a pure addition over the other.
- Conflict-marker scan over tracked files (`git grep -E "^(<<<<<<<|=======|>>>>>>>)$"`)
  → no matches. `git status --porcelain -uall` → no untracked files, so no `.env`,
  database, `dist/` or screenshot entered the tree.

Verification after the merge:

- `npm run typecheck` (`tsc --noEmit`) — **passed**, exit 0.
- `npm run build` — **passed**, exit 0, **42 modules** (was 41: `useSpeech.ts`),
  `dist/assets/index-*.js` **191.14 kB** / gzip 60.57 kB, `index-*.css`
  **13.20 kB** / gzip 3.27 kB, built in 1.61 s.
- `backend/.venv/Scripts/python.exe -m pytest -q` — **160 passed**, 1 pre-existing
  Starlette/httpx deprecation warning from `test_main.py:17`, 7.46 s.
- Merged tree rendered in Chromium on the running dev server (`localhost:5173`;
  Vite binds `::1` only, so `127.0.0.1:5173` does not answer). Asserted from the DOM
  and `document.styleSheets`, not accessibility snapshots (see §8.7): 3 dashboard
  rows with `.ab-dot--pending` and `.ab-row__badges`; `.ab-chat__controls`,
  `.ab-voice-bar`, the "Auto-read new replies" `.ab-voice-toggle` and the mic button
  present; 136 loaded rules include all ten of my `.ab-conflict*`/`.ab-row__badges`/
  `.ab-dot--overdue` selectors **and** all of Member 2's `.ab-voice*`/`.ab-mic-btn--live`/
  `.ab-speak-btn` selectors plus `@keyframes ab-mic-pulse`; the two nested media blocks
  survive as `(max-width: 860px){.ab-row__badges}`, `.ab-row` and
  `(prefers-reduced-motion: reduce){.ab-mic-btn--live}`; computed styles resolve
  (`display: flex`, `gap: 8px`). Console: no errors or warnings.
- Live backend data read during the check: `GET /api/commitments`, `GET /api/plan`
  (`:8000`, `DEMO_TODAY=2026-10-26`, `DEMO_FALLBACK=true`).

Approval was given on 2026-10-22 and the merge landed as `bb954e5`, a two-parent merge
commit (`742f625`, `f0ba2fc`) whose diff against its first parent is exactly the three
files above. This document and the other Member 5 files follow as a separate commit on
top of it. Neither commit touches `main`, and the pre-merge state stays recoverable
through `refs/heads/backup/member-5-before-f0ba2fc-merge` → `742f625`.
