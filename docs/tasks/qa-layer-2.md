# Task: QA Layer 2 — Flask Route Tests Sprint

## Goal

Add Flask route tests that exercise the HTTP layer of the application. Current state-model tests call Python functions directly; these tests go through the Flask test client and exercise routes as an HTTP client would.

Three route groups are tested in three separate segments, each a separate branch and PR:

1. Workflow routes — upload, calculate, status
2. Edit routes — update_volume, machines reset, edits persistence
3. Session routes — switch, create, list

By the end of this sprint, the Flask HTTP layer has its own test coverage independent from the state-model layer, and overall coverage improves measurably from the 48% baseline.

## Scope

In scope:

- New file `tests/test_routes_*.py` per segment
- Shared test infrastructure in `tests/conftest.py` or a helper module
- Fixture setup for seeded session state, in-memory persistence, mocked file uploads
- Each test asserts HTTP status, response shape, and resulting state mutation
- One observations entry at the end of each segment summarizing findings

Out of scope:

- Playwright or browser-based tests
- JavaScript behavior verification
- Template rendering verification beyond response status
- New production code changes — if a route has bugs revealed by tests, stop and report, do not fix
- Route testing for `ui/routes/scenarios.py`, `ui/routes/pap.py`, `ui/routes/config.py`, `ui/routes/license.py`, `ui/routes/read.py`, `ui/routes/exports.py` — these are deferred to future sprints

## Rules

Standard rules from `AGENTS.md` apply plus these specific ones:

- One segment per branch. Three branches total, one per segment.
- Conventional commit messages: `test:` for new tests, `chore:` for infrastructure.
- Stop at every checkpoint. Do not continue to the next segment without explicit approval.
- If a route has a real bug that tests expose (for example: returns 200 but did not actually mutate state), stop and report. Do not fix the bug as part of this sprint.
- All new tests require `SOP_GOLDEN_FIXTURE`. They are not added to the `no_fixture` marker set — they run locally and in the pre-commit hook, not in GitHub Actions CI.
- Test fixtures must not depend on disk writes. Redirect any session persistence to an in-memory mock or tmp_path for the duration of each test.

## Architecture notes

Reading these before planning is essential:

- `ui/app.py` — how `sessions`, `active_session_id`, and `_global_config` globals are initialized and used. How blueprints are registered with their callback dependencies.
- `ui/routes/workflow.py`, `ui/routes/edits.py`, `ui/routes/sessions.py` — the three route modules under test.
- `tests/test_state_model.py` — specifically the cross-tab test, which already creates a Flask test app with `create_machines_blueprint(...)`. The same pattern applies here; reuse it.

The central challenge is seeding realistic session state without triggering real file I/O or session persistence. Your fixtures must:

1. Create a PlanningEngine from the golden fixture (reuse existing `golden_fixture_path`)
2. Register only the blueprint under test, with explicit-crash stubs for any callbacks that mutation tests do not exercise
3. Monkey-patch or inject a no-op `save_sessions_to_disk` so tests do not pollute real session state
4. Restore global `sessions` dict to an empty state before and after each test

---

## Segment 1 — Workflow routes

**Branch:** `test/routes-workflow`

### Purpose

Test the HTTP interface for the three entry-point routes: upload a workbook, trigger calculation, check status.

### Routes under test

Read `ui/routes/workflow.py` to get exact paths. Expected routes:

- `POST /api/upload` — receives a multipart file, creates a new session, triggers initial calculation
- `POST /api/calculate` — recalculates the active session
- `GET /api/status` — returns current session state summary

### Tests to write

At minimum:

1. `test_upload_creates_session_and_runs_pipeline` — POST a multipart file (use the golden fixture as upload payload), assert 200, assert new session in `sessions` dict, assert engine has `results`
2. `test_calculate_triggers_pipeline_on_active_session` — seed a session, POST to `/api/calculate`, assert 200, assert engine results updated
3. `test_status_returns_session_summary` — seed a session, GET `/api/status`, assert 200, assert response shape contains expected keys (session ID, line types present, period count)

If the routes have authentication or license checks that fail without a fake license: stop and report.

### Fixture infrastructure

Create a new fixture `flask_test_app` in `tests/conftest.py` that:

- Imports necessary modules from `ui/app.py` (engine rebuild, blueprints, callbacks)
- Creates a minimal Flask app with only the `workflow` blueprint registered
- Seeds `sessions` and `active_session_id` to an empty state
- Yields the test client
- Cleans up after each test

This fixture must be reusable for segments 2 and 3 with minimal adjustment.

### Stop conditions

- A route has authentication/license logic that cannot be bypassed in tests without production code changes
- A route has a real bug where it returns success but does not mutate expected state — stop, report, do not fix
- Tests require more than two new helpers to set up — if infrastructure grows beyond that, the architecture is telling us something and we should stop and discuss

### Verification

- `pytest tests/test_routes_workflow.py -v` — all new tests pass
- `pytest -v` — 10 existing plus new tests, all green
- `pytest -m no_fixture -v` — still 3 tests only (new tests are fixture-dependent)
- `python main.py --test` — smoke test unaffected

### Commit

- `test: add workflow route tests`
- Body lists which routes are now covered and notes any infrastructure added to `conftest.py`

### Push

- `git push -u origin test/routes-workflow`

---

## 🛑 CHECKPOINT 1

**Stop here. Report:**

- Which routes tested, which skipped (if any) and why
- Number of new tests added and total test count
- Any route behavior that surprised you (stop-worthy or merely unusual)
- Lines added to `tests/conftest.py` and whether the fixture is segment-2 ready
- Verification results
- Branch pushed, awaiting PR and merge by user

**Wait for user approval before starting Segment 2.**

---

## Segment 2 — Edit routes

**Branch:** `test/routes-edits` (from main after segment 1 is merged)

### Purpose

Test the HTTP interface for routes that mutate session state. These are the most-used routes in production and cross-cut with the existing state-model tests.

### Routes under test

Read `ui/routes/edits.py` to get exact paths. Expected routes:

- `POST /api/update_volume` — apply a volume edit
- `POST /api/machines/reset` — reset machine state only (planning edits preserved)
- `POST /api/sessions/edits/persist` — save pending edits to disk

### Tests to write

At minimum:

1. `test_update_volume_returns_updated_results` — seed session, POST edit for Line 01, assert 200, assert response contains updated values, assert `pending_edits` has new entry
2. `test_machines_reset_clears_only_machine_state` — seed session with machine overrides and pending planning edits, POST to reset, assert machine overrides cleared but `pending_edits` intact (this is exactly what the state-model test verifies, now at HTTP level)
3. `test_edits_persist_returns_success_without_real_disk_write` — seed session with pending edits, POST persist, assert 200, verify the mocked disk write was called

If edit routes return 200 but do not actually persist changes, that is a bug-find. Stop and report; do not fix.

### Fixture reuse

Extend the `flask_test_app` fixture from segment 1 or create a sibling fixture that registers the `edits` blueprint. The pattern should be copy-paste equivalent; if it is not, the fixture design in segment 1 was wrong and we should refactor before proceeding.

### Stop conditions

Same as segment 1, plus:

- Edit routes reference cascade logic (`_recalc_material_subtree` or `_recalc_pap_material`) that cannot be exercised without the full engine — if so, flag it but proceed; these routes will have modest coverage
- A route's response shape is significantly different from what `ui/static/sop_planning.js` expects — the frontend is out of scope but if you see a clear contract mismatch, flag it as an observation

### Verification

Same as segment 1 plus:

- After all tests pass, do one final `pytest -v` on main branch with this segment merged, confirming the cross-tab state-model test and the new edit-routes tests are not redundantly asserting the same thing. If they are, note which should be kept and which should be considered for deletion in a future cleanup.

### Commit

- `test: add edit route tests`
- Body notes cross-cutting with state-model tests

### Push

- `git push -u origin test/routes-edits`

---

## 🛑 CHECKPOINT 2

**Stop here. Report:**

- Which routes tested, which skipped and why
- Any cross-cutting observations with state-model tests
- Whether the workflow fixture extended cleanly or required rework
- Any observations entries to add
- Verification results
- Branch pushed, awaiting PR and merge

**Wait for user approval before starting Segment 3.**

---

## Segment 3 — Session routes

**Branch:** `test/routes-sessions` (from main after segment 2 is merged)

### Purpose

Test multi-session management. This is foundational for the concurrent-tester scenarios in the Excel test matrix that led to the state-model tests.

### Routes under test

Read `ui/routes/sessions.py` to get exact paths. Expected routes:

- `POST /api/sessions/create` — create a new session from workbook
- `POST /api/sessions/switch` — switch active session
- `GET /api/sessions/list` — list available sessions

### Tests to write

At minimum:

1. `test_sessions_create_registers_new_session_without_overwriting_existing` — seed with one session, POST to create, assert two sessions exist, assert original session untouched
2. `test_sessions_switch_updates_active_session_id` — seed with two sessions, POST to switch, assert `active_session_id` changed, assert GET status returns new session data
3. `test_sessions_list_returns_all_sessions_with_metadata` — seed with two sessions, GET list, assert response contains both with expected fields (ID, name, material count, period count)

### Fixture reuse

By this point the fixture should be copy-paste ready. If segment 3 requires a third rewrite of the Flask test app setup, document this explicitly as infrastructure debt to address in a follow-up.

### Stop conditions

Same pattern as previous segments.

### Commit

- `test: add session management route tests`
- Body notes that sprint covers the three highest-risk route groups and lists which route modules remain uncovered

### Push

- `git push -u origin test/routes-sessions`

---

## 🛑 CHECKPOINT 3

**Stop here. Report:**

- Which routes tested, which skipped and why
- Total new route tests across all three segments
- Coverage delta: re-run `pytest --cov=ui --cov=modules --cov-report=term` and report new overall percentage versus the 48% baseline
- Update `docs/tasks/qa-coverage-baseline.md` with the new numbers in a "Post Layer 2 Sprint" section; keep the original baseline section intact
- Add a final observations entry dated today noting QA Layer 2 sprint completion, what coverage improved, and what remains (other route modules, browser tests, ruff cleanup)
- Branch pushed, awaiting PR and merge

**After this checkpoint, the sprint is complete.**

---

## Final deliverable

At the end of this sprint, the following has landed on main through three separate PRs:

1. `tests/test_routes_workflow.py`, updated `tests/conftest.py` (segment 1)
2. `tests/test_routes_edits.py` (segment 2)
3. `tests/test_routes_sessions.py`, updated `docs/tasks/qa-coverage-baseline.md` and `docs/tasks/observations.md` (segment 3)

Plus one final observations entry noting the sprint completion, coverage delta, and remaining gaps for future sprints.
