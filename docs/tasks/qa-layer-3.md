# Task: QA Layer 3 — Browser Automation Tests Sprint

## Goal

Add Playwright-based end-to-end tests that exercise the actual browser UI. Layers 1 and 2 cover engine correctness and HTTP orchestration. Layer 3 covers what neither of those can: rendered output, visual state (heatmap colors, button enabled/disabled), and client-side interactions (cell editing, undo/redo, session switching in the sidebar).

Three UI areas are tested in three separate segments, each a separate branch and PR:

1. Page load and calculate — server starts, upload, calculate, table renders with real data
2. Edit interactions — cell editing, pending-state indicator, heatmap color updates, undo
3. Session management UI — session sidebar, switch, delete, rename

By the end of this sprint, the highest-risk browser behaviors have automated regression coverage, and the overall QA pyramid covers all three layers.

## Scope

In scope:

- New directory `tests/browser/` for Playwright tests
- Shared server fixture in `tests/browser/conftest.py` that starts the real Flask server and pre-loads a session
- Each test asserts rendered DOM state, CSS class presence, or visible text — not just HTTP responses
- One observations entry per segment summarizing findings
- New entries in `docs/tasks/qa-coverage-baseline.md` with a "Post Layer 3 Sprint" section

Out of scope:

- JavaScript unit tests (`sop_planning.js` internals)
- Visual regression / screenshot comparison (pixel-diff tooling)
- Performance or load testing
- Cross-browser coverage — Chromium only for this sprint
- Mobile viewport testing
- New production code changes — if a UI behavior is broken, stop and report, do not fix

## Rules

Standard rules from `AGENTS.md` apply plus these specific ones:

- One segment per branch. Three branches total, one per segment.
- Conventional commit messages: `test:` for new tests, `chore:` for infrastructure.
- Stop at every checkpoint. Do not continue to the next segment without explicit approval.
- If a UI behavior is wrong (button stays enabled when it should be disabled, wrong color class, table empty after calculate), stop and report. Do not fix as part of this sprint.
- Playwright tests are **not** added to the `no_fixture` marker set and are **not** run by the pre-commit hook. They run locally on demand and in a separate CI job (to be wired up separately).
- The server must be started fresh per test session — do not assume a server is already running.
- Tests must clean up after themselves: delete uploaded files, clear session state if the server persists it between test runs.

## Dependencies

Before any code is written, verify that `playwright` and `pytest-playwright` are not already in `requirements.txt`. If they are not:

```powershell
pip install playwright pytest-playwright
playwright install chromium
```

Add `playwright` and `pytest-playwright` to `requirements.txt` under a `# browser tests` comment. Do not mix them with existing dependencies.

## Architecture notes

Read these before planning:

- `ui/app.py` — how the Flask app is initialized, which port it listens on (`SOP_PORT`, default 5000), and how sessions are loaded from `sessions_store.json` on startup. The test server must start with a clean sessions store to avoid leaking state from manual runs.
- `ui/static/sop_planning.js` — where DOM IDs and CSS classes are defined. These are the selectors Playwright tests must target. Do not invent selectors; read the actual JS.
- `ui/templates/` — confirm which HTML templates are served and which JS events map to which DOM mutations.

The central challenge is bootstrapping a real session before browser tests can assert anything meaningful. Your server fixture must:

1. Set `SOP_APP_DATA_DIR` to a `tmp_path`-equivalent directory so the server writes session data there instead of `%LOCALAPPDATA%\SOPPlanningEngine`
2. Start `python main.py` as a subprocess (via `subprocess.Popen`) with that env var set
3. Wait for the server to be ready (poll `GET /` until 200, timeout 30s)
4. POST the golden fixture to `/api/upload` and POST to `/api/calculate` via `requests` (not Playwright — faster)
5. Yield the base URL and session ID to tests
6. Terminate the subprocess and clean up `SOP_APP_DATA_DIR` after the session

Use `scope="session"` for the server fixture — starting the full pipeline per test is too slow. Tests that mutate state (edits, deletes) must restore it after themselves or each use a fresh upload.

---

## Segment 1 — Page load and calculate renders table

**Branch:** `test/browser-load`

### Purpose

Verify that the most basic user flow works end-to-end in a real browser: the page loads, a workbook can be uploaded, calculate runs, and the planning table renders with real data rows.

### Selectors to find before writing tests

Read `ui/static/sop_planning.js` and `ui/templates/` to find the actual selectors for:

- The main planning table or grid container
- A table row or cell that contains a `LineType` value (e.g., `"01. Demand forecast"`)
- The calculate button
- Any loading spinner or progress indicator that appears while calculate runs
- The period header row (to verify the correct planning months render)

Document the selectors you find at the top of `tests/browser/test_load.py` as a comment block before the tests.

### Tests to write

At minimum:

1. `test_page_loads_without_errors` — navigate to `/`, assert 200, assert no JavaScript console errors (use `page.on("console", ...)` to capture errors), assert the page title or main heading is present
2. `test_calculate_renders_planning_table` — using the pre-seeded session from the server fixture, assert that after calculate the planning table container is visible, contains at least one row with a demand forecast line type, and the period headers match the `planning_month` used during upload
3. `test_period_headers_match_planning_month` — assert that the column headers in the table correspond to the correct `YYYY-MM` periods for the configured planning month (12 periods, starting from the planning month)

If the page has a login or license gate that blocks access, stop and report. Do not add a fake license bypass to production code.

### Infrastructure

Create `tests/browser/conftest.py` with:

- `server` fixture (`scope="session"`) — starts Flask subprocess, pre-loads session, yields `{"base_url": ..., "session_id": ...}`, tears down
- `page` fixture wraps `pytest-playwright`'s built-in `page` and navigates to `base_url` before yielding

The `golden_fixture_path` from `tests/conftest.py` is still needed here. Import it or duplicate the path resolution logic — do not import from `tests/conftest.py` directly; pytest fixture scoping across directories is fragile.

### Stop conditions

- License or auth gate blocks page load and requires production code changes to bypass
- The planning table uses a JavaScript framework (React, Vue) with dynamic class names that make stable selectors impossible — stop and document which selector strategy would work
- Server startup takes more than 60 seconds on the fixture machine — document and ask whether to proceed

### Verification

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/browser/test_load.py -v
pytest -v  # all existing tests still pass; browser tests also run if env var set
```

### Commit

- `chore: add playwright infrastructure for browser tests`
- `test: add page load and table render browser tests`
- Body documents which selectors are used and notes any selector fragility

### Push

- `git push -u origin test/browser-load`

---

## 🛑 CHECKPOINT 1

**Stop here. Report:**

- Server startup time (seconds from subprocess launch to first 200 on `/`)
- Which selectors were used and how stable they appear (ID, class, data attribute, text content)
- Which of the three tests passed/failed and why
- Any console errors captured by `page.on("console", ...)`
- Whether the period headers matched — if not, what was rendered vs expected
- Lines added to `tests/browser/conftest.py` and whether the fixture is segment-2 ready
- Verification results
- Branch pushed, awaiting PR and merge by user

**Wait for user approval before starting Segment 2.**

---

## Segment 2 — Edit interactions

**Branch:** `test/browser-edits` (from main after segment 1 is merged)

### Purpose

Verify that cell editing works end-to-end in the browser: clicking a cell enters edit mode, entering a new value triggers the update, the table reflects the change, the pending-edits indicator appears, the heatmap color updates, and the undo button becomes enabled.

### Selectors to find before writing tests

Read `ui/static/sop_planning.js` to find:

- How a table cell becomes editable (click handler, `contenteditable`, input element)
- The CSS class or data attribute that marks a cell as "edited" (heatmap color)
- The pending-edits indicator (badge, count, or label)
- The undo button and its disabled/enabled state
- How the undo action is triggered (button click, keyboard shortcut)

Document these at the top of `tests/browser/test_edits.py`.

### Tests to write

At minimum:

1. `test_cell_edit_updates_value_and_marks_pending` — click a demand forecast cell, enter a new numeric value, submit (Enter or click away), assert the cell now shows the new value, assert the pending-edits indicator shows at least one pending change
2. `test_edited_cell_has_heatmap_color_class` — after a cell edit, assert that the cell element has the CSS class or inline style that marks it as edited (different from an unedited cell in the same row)
3. `test_undo_reverts_last_edit` — perform a cell edit, assert undo button is enabled, click undo, assert cell value reverts to original, assert pending-edits count decreases or indicator disappears

Each test that mutates state must either undo its own changes before returning or start from a fresh session upload. Prefer undo-to-clean over fresh upload for speed; use fresh upload only if undo is unreliable.

### Stop conditions

- Cell editing is implemented via a custom widget that requires a specific double-click sequence or modal interaction not discoverable from `sop_planning.js` alone — stop, document the sequence, ask before proceeding
- The heatmap uses dynamically generated class names (e.g., CSS modules hash) that make stable assertions impossible — stop and flag as selector design issue
- Undo triggers a full page reload rather than a DOM patch — stop and flag as performance/UX observation

### Verification

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/browser/test_edits.py -v
pytest -v
```

### Commit

- `test: add cell edit and undo browser tests`

### Push

- `git push -u origin test/browser-edits`

---

## 🛑 CHECKPOINT 2

**Stop here. Report:**

- Which edit interaction selectors were used and how they were found
- Whether cell editing required a specific event sequence (click, dblclick, focus, keydown)
- Whether the heatmap color change was via CSS class or inline style — and what the class/property name is
- Whether undo is a single-step DOM patch or a full reload
- Any flakiness observed (tests passing/failing non-deterministically) — document the root cause if found
- Verification results
- Branch pushed, awaiting PR and merge

**Wait for user approval before starting Segment 3.**

---

## Segment 3 — Session management UI

**Branch:** `test/browser-sessions` (from main after segment 2 is merged)

### Purpose

Verify that the session sidebar works: multiple sessions are listed, switching sessions updates the table with the new session's data, and deleting a session removes it from the sidebar.

### Selectors to find before writing tests

Read `ui/static/sop_planning.js` and `ui/templates/` to find:

- The session sidebar or panel container
- Individual session list items and how the active one is marked
- The switch session trigger (click on item, button, link)
- The delete session button and any confirmation dialog
- The session name as displayed in the UI

Document at top of `tests/browser/test_sessions.py`.

### Tests to write

At minimum:

1. `test_session_list_shows_uploaded_session` — after upload and calculate, assert the session sidebar lists the session with its custom name and is marked active
2. `test_switch_session_updates_table` — create two sessions (two uploads + two calculates), switch from session A to session B via the UI, assert the active marker moves in the sidebar and the table updates (assert a distinguishing metadata value — e.g., different site or planning month — is now shown)
3. `test_delete_session_removes_from_sidebar` — with two sessions, delete session A via the UI, assert the sidebar no longer lists session A, assert session B is now the active session

Creating two sessions in a browser test is slow. If the server fixture pre-loads two sessions at startup (via two sequential upload+calculate calls), the tests can assert against the pre-seeded state instead of creating sessions themselves. Prefer pre-seeding over in-test creation if the server fixture supports it.

### Stop conditions

- Session delete requires a confirmation dialog that Playwright must interact with — note the dialog type (browser native `confirm()` vs custom modal) and handle it; do not skip the test
- Switching sessions reloads the full page rather than a partial DOM update — note this as a performance observation but do not stop; assert against the reloaded state
- The sidebar is hidden or collapsed by default and requires a UI gesture to open — document the gesture and add it to the fixture navigation step

### Verification

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/browser/ -v
pytest -v
```

### Coverage delta

After all three segments are merged, re-run with coverage:

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest --cov=ui --cov=modules --cov-report=term
```

Note: Playwright tests hit the server over HTTP rather than through Flask's test client, so `pytest-cov` will not capture coverage from the server subprocess. Report the number from the unit + route test suite only, and note this limitation explicitly.

### Commit

- `test: add session sidebar browser tests`
- Body: notes remaining browser behaviors not covered (scenario flows, PAP tab, export, config panel)

### Push

- `git push -u origin test/browser-sessions`

---

## 🛑 CHECKPOINT 3

**Stop here. Report:**

- Which session UI selectors were used
- Whether the server fixture was extended to pre-seed two sessions, and if so how (two upload+calculate calls, or seeded via direct API calls)
- Whether delete triggered a confirmation dialog, and how it was handled
- Whether any test was flaky (intermittent selector miss, race condition between DOM update and assertion) and what timeout strategy resolved it
- Total new browser tests across all three segments
- Coverage note: record the unit + route coverage from `pytest --cov` (same suite as Layer 2 baseline), note it is unchanged by browser tests, and document the subprocess coverage gap
- Update `docs/tasks/qa-coverage-baseline.md` with a "Post Layer 3 Sprint" section noting the new browser tests and the subprocess coverage limitation
- Add a final observations entry dated today: Layer 3 sprint completion, selector fragility notes, remaining manual-only behaviors
- Branch pushed, awaiting PR and merge

**After this checkpoint, the sprint is complete.**

---

## Final deliverable

At the end of this sprint, the following has landed on main through three separate PRs:

1. `tests/browser/conftest.py`, `tests/browser/test_load.py`, updated `requirements.txt` (segment 1)
2. `tests/browser/test_edits.py` (segment 2)
3. `tests/browser/test_sessions.py`, updated `docs/tasks/qa-coverage-baseline.md` and `docs/tasks/observations.md` (segment 3)

The QA pyramid now covers all three layers:

- **Layer 1**: Engine correctness (golden pipeline + unit helpers, `no_fixture` subset in CI)
- **Layer 2**: HTTP orchestration (Flask route tests, full suite in pre-commit)
- **Layer 3**: Browser behavior (Playwright, run locally and in a separate CI job)
