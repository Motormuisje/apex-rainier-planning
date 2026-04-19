"- ui/app.py has UTF-8 BOM at start of file. Works at runtime (Python accepts it), but breaks ast.parse. Fix with one command: (Get-Content ui/app.py -Raw -Encoding UTF8) | Set-Content ui/app.py -Encoding UTF8. Separate commit when convenient." | Add-Content docs/observations.md

# Observations

Running log of code observations noticed during refactors and reviews.
These are NOT tasks — they're signals worth preserving so future-you or
an agent can decide whether to investigate. Each entry: date, severity,
description, context where it was noticed.

---

## 2026-04-18 — UTF-8 BOM in `ui/app.py`

**Severity:** low (cosmetic; works at runtime)

`ui/app.py` starts with a UTF-8 BOM (U+FEFF). Python's runtime accepts
it, but `ast.parse` and some static analysers reject it. Likely introduced
by an agent or editor that saved the file as "UTF-8 with BOM" at some
point.

**Fix when convenient** (separate commit, not part of any refactor):
```powershell
(Get-Content ui/app.py -Raw -Encoding UTF8) | Set-Content ui/app.py -Encoding UTF8
```

Check other Python files too — a scan in this session found only
`ui/app.py`, but a new agent contribution could reintroduce it.

---

## 2026-04-18 — Mojibake in `ui/app.py` comments

**Severity:** cosmetic

Several comments contain `â€"` and `â€™` artefacts — double-encoded
em-dashes and apostrophes. Likely a side effect of the same editor round
that introduced the BOM. Will clean up when the BOM is fixed; the same
UTF-8 round-trip above should resolve the visible ones if the file is
opened in a BOM-aware editor first.

---

## 2026-04-18 — `pending_edits` has two writers

**Severity:** medium (no observed bug, but fragile)

`pending_edits` is written from two places:

1. **Server-side, inline in `_apply_volume_change`** (`ui/app.py`).
   Writes a key of the form `"{line_type}||{material_number}||{aux_column}||{period}"`
   with value `{'original': <baseline>, 'new_value': <value>}`.
2. **Client-driven via `/api/sessions/edits/persist`** in
   `ui/routes/edit_state.py`. Writes under the same schema, triggered
   by the frontend.

If the client-side persist call and the server-side inline write ever
construct the key differently (e.g., one trims `aux_column`, the other
doesn't; one stringifies `material_number`, the other leaves it as int),
duplicate or ghost entries can accumulate in `pending_edits`. Because
`pending_edits` is what `replay_pending_edits` consumes after a restart,
this would silently change restart behaviour.

**Investigation task for later:** verify both paths produce byte-identical
keys for the same logical edit. If they diverge, designate one as
canonical and have the other funnel through it.

---

## 2026-04-18 — `reset_machine_params` clears state rather than rebuilding it

**Severity:** low (current behaviour is correct; future-refactor hazard)

In `ui/routes/machines.py`, `reset_machine_params` sets
`sess['machine_overrides'] = {}` explicitly, whereas every other route
that mutates machine state assigns
`sess['machine_overrides'] = machine_overrides_from_engine(sess, current_engine)`.

This asymmetry is intentional right now — after reset there's nothing
left to derive — but it means a future refactor that "normalises" how
machine_overrides is kept in sync could easily replace the empty-dict
assignment with the `machine_overrides_from_engine` call, and subtly
break reset.

**If refactoring this area:** preserve the explicit empty assignment,
or document why the derive-from-engine path returns empty after reset.

---

## 2026-04-18 — `_recalc_pap_material` and `_recalc_material_subtree` live in `ui/app.py`

**Severity:** low (style)

These helpers contain real recalculation logic (not thin wrappers) and
live in `ui/app.py` rather than a sibling module. They would fit
`ui/engine_rebuild.py` or a new `ui/cascade.py`. Moving them would
reduce `ui/app.py` further and make the cascade logic independently
testable.

**Out of scope for current refactor;** revisit once state-model tests
are in place to verify behaviour preservation.

"2026-04-19 — _replay_pending_edits blijft als wrapper. Andere helpers zijn geëxtraheerd omdat ze thin wrappers waren met onthulbare afhankelijkheden. _replay_pending_edits injecteert drie callbacks en is daarmee een factory callback, geen indirection voor een global. Verwijderen zou cosmetisch zijn. Kan mee in een toekomstige bredere herstructurering die factory-params voor alle blueprint injecties invoert, niet nu."

## 2026-04-19 — Ruff pre-commit hook uitgesteld

Severity: low (tooling gap, geen runtime impact)

Eerste ruff-run (F-only ruleset) rapporteerde 40 errors over 13 bestanden.
Errors zijn een mix van unused imports en redefined names die deels
false positives zijn in de context van Flask blueprints en side-effect
imports. Ruff toevoegen vereist eerst een bewuste clean-up pass, niet
als bijproduct van een hook-installatie. Pre-commit hook draait
voorlopig alleen pytest. Ruff toevoegen als apart chore-ticket wanneer
er bandbreedte is.

---

## 2026-04-19 — QA Layer 1 sprint afgerond

Severity: low (QA infrastructure; no runtime impact)

QA Layer 1 now has three pieces of infrastructure: a local pytest
pre-commit hook, GitHub Actions CI for the fixture-free `no_fixture` test
subset, and coverage measurement with a recorded baseline.

The current coverage baseline is 48% overall: `modules` is stronger at 59%
because the golden pipeline exercises the core engines, while `ui` is 36%
because Flask route behavior is mostly outside Layer 1. The expected next
QA gaps are Layer 2 Flask route tests, targeted tests for route error paths,
and a dedicated Ruff cleanup pass before Ruff is reintroduced into
pre-commit or CI.

---

## 2026-04-19 — QA Layer 2 segment 1 workflow route findings

Severity: low (test coverage note; no runtime impact)

Workflow route tests cover the current `/api/upload` and `/api/calculate`
HTTP behavior. The Layer 2 sprint spec mentioned `/api/status`, but the
current workflow blueprint does not define that route.

The current upload route creates a session with metadata and marks it active,
but it does not attach an engine or run the full planning pipeline. The
pipeline is run by `/api/calculate`. Tests document this current split rather
than changing production behavior.

---

## 2026-04-19 — QA Layer 2 segment 2 edit route boundaries

Severity: low (test coverage note; no runtime impact)

Edit route tests cover HTTP orchestration across three blueprints:
`/api/update_volume`, `/api/machines/reset`, and
`/api/sessions/edits/persist`.

The `/api/update_volume` test intentionally verifies request-body extraction,
callback invocation, and response propagation only. It does not assert
`pending_edits` key schema, undo-stack format, or cascade correctness because
the real `_apply_volume_change` callback still lives in `ui/app.py` and cannot
be imported without initializing app-level globals.

The isolated blueprint route does not call `save_sessions_to_disk()` directly;
production save behavior comes from `ui.app`'s app-level `after_request`
autosave hook. Those behavior details remain covered by the state-model tests
until `_apply_volume_change` is extracted into a standalone helper module.

---

## 2026-04-19 — Snapshot route silently drops engine on deepcopy failure

Route: `POST /api/sessions/snapshot`

Severity: medium

Behavior: route returns 200 / `success: True` when `copy.deepcopy(engine)`
raises `TypeError: cannot pickle '_io.BufferedReader' object`. The new session
is created with `engine = None`, `is_snapshot: True`, and the route docstring
promise ("duplicate the active session, including all edits") is silently
broken.

Root cause: unpickleable file handle somewhere in the engine object graph. The
`except Exception: engine_copy = None` branch swallows the failure with no log,
no error flag in the response, and no indication to the caller that the
snapshot is not calculated.

Fix direction (do not implement now): either replace `deepcopy` with a manual
engine rebuild from the session's `file_path` plus `pending_edits` (the same
path used after restart), or surface the failure with a 500 or a
`calculated: False` flag in the response so callers can react.

---

## 2026-04-19 — QA Layer 2 sprint afgerond

Severity: low (QA infrastructure; no runtime impact)

QA Layer 2 added Flask route coverage for workflow, edit, machine reset,
pending-edit persistence, and session management routes. The snapshot route was
skipped after tests exposed the medium-severity bug above.

Known fixture simplification: `flask_test_app`, `edit_route_app`, and
`session_route_app` use a simplified shift-hours lookup in snapshot baselines.
Machines without `shift_hours_override` snapshot as `0.0` in tests, while
production uses the real data-model fallback. Current route tests do not
assert computed shift-hour values, but this should be revisited if future
route tests depend on machine baseline details.

Remaining QA gaps: other route modules (`scenarios`, `pap`, `config`,
`license`, `read`, `exports`), browser/JavaScript behavior, the snapshot bug
fix, and the dedicated Ruff cleanup pass before Ruff returns to pre-commit or
CI.

---

## 2026-04-19 — Browser period header assertion slice

Severity: low (test maintenance; no runtime impact)

`test_period_headers_match_planning_month` uses hardcoded column slice — `header_texts[6:]` assumes exactly 6 fixed columns (Material, Name, Line Type, Aux, Aux 2, Start) before the period columns. Currently correct per `index.html:4511`. If a column is added, the slice shifts silently. Future fix: filter on YYYY-MM pattern instead of slicing.

---

## 2026-04-19 — QA Layer 3 browser sprint afgerond

Severity: low (QA infrastructure; no runtime impact)

Layer 3 adds 9 Playwright browser tests across three segments: page load,
cell edit interactions, and session sidebar management.

Selector strategy summary:
- Load tests: `#planBody tr[data-material][data-linetype]`, `#planHead th`
  (slice `[6:]` fragility documented separately)
- Edit tests: `#planBody td.editable-cell[data-tt="val"][data-lt="01. Demand
  forecast"][data-period]`, `#editSummaryBar`, `.cell-increased`
- Session tests: `.session-item`, `.session-item.active`, `.session-name-edit`,
  `.session-delete`, `#planningMonth`

All selectors are stable IDs, data attributes, or semantic class names from
the template/JS source. No dynamically generated class names (CSS modules
hashes) were encountered.

Edit cleanup strategy: `_drain_edits()` calls `/api/undo` in a loop at the
start of each edit test. Preferred over in-test undo because it handles
failures in prior tests that left the server in an edited state.

Session state isolation: the `server` fixture is session-scoped (one Flask
subprocess per test run). Edit tests drain state via API. Session tests
restore the active session via `/api/sessions/switch` before each page load.
Throwaway session in delete test is created and destroyed within the test.

`deleteSession()` uses `window.confirm()` (native browser dialog). Playwright
handles this with `page.once("dialog", lambda d: d.accept())` registered
before the click. No production code change was needed.

`switchSession()` does not call `setBusy()` directly — it uses an internal
`_isSwitchingSession` flag. The `wait_for_load_state("networkidle")` pattern
is sufficient to wait for the full fetch chain (switch → loadResults →
loadValueResults → renderDashboard → loadSessions).

Remaining manual-only behaviors not covered by any automated test:
- Scenario flows (PAP tab, scenario creation and comparison)
- Export to Excel and DB export
- Config panel (folder path changes, master file upload)
- Rename session via UI (contenteditable blur handler)
- MoM comparison tab
- Mobile viewport / sidebar collapse gesture
- License expiry and reactivation flow
