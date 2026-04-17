# AGENTS.md — UI Refactor

**Scope of this file:** governs the ongoing stepwise split of `ui/app.py`
into smaller route blueprints and helper modules. Goal: improve readability
without changing calculation logic, contracts, or session behavior. For
general project context, read `CLAUDE.md` first. In particular, the six
sync/rebuild points defined there remain authoritative for anything that
touches session, config, or engine state.

---

## Rules — read first, follow always

### Scope discipline

- Work on ONE route group or ONE helper function at a time.
- ONE change = ONE commit. No batching, no squashing "while you're at it."
- Do NOT modify function bodies when moving them. Copy byte-for-byte. If a
  body needs changes to work in the new location (missing imports,
  unreachable globals), make the minimum change required — do not "clean
  up," "improve," or "modernize" logic.
- Do NOT rename functions during a move. Renames are a separate task.
- Do NOT touch functions not listed in the group you are working on.
- Preserve endpoint paths, HTTP methods, and response shapes exactly.

### Do-not-touch list for this refactor

These functions contain cascade / calculation logic and are out of scope
for structural moves. They stay where they are unless a dedicated task
says otherwise:

- `_recalc_one_material`
- `_recalculate_value_results`
- `_recalculate_capacity_and_values`
- `_apply_volume_change` — see "Stop and ask" below
- Anything inside `modules/` (engines are not touched by the UI refactor)

### Stop and ask — do not proceed

Stop and report to the user if any of these are true:

- The move requires a circular import (e.g., module A imports from B which
  imports from A). Describe the cycle; do not try to break it yourself.
- The move requires changing a function signature that has more than two
  call sites. List every caller first; wait for approval.
- A test fails after your change. Do not "fix forward" — revert the commit
  and describe what failed.
- You discover a bug in a function you are moving. Do NOT fix it as part
  of the move. Log it in `docs/observations.md`, commit the move as-is,
  and flag it in your reply.
- `_apply_volume_change` turns out to pull in more than two other
  functions when extracted. Stop, describe the dependency graph, wait for
  guidance.
- Anything surprises you. Surprise is information; honor it.

### Commit protocol

Format: conventional commits.

```
refactor(ui): extract <thing> to ui/<module>.py

<one-line description of what moved and why the change is safe>
```

Between commits: run all verification steps below. If any fails, do NOT
commit. Report the failure and wait.

### Verification after every change

1. `python -m py_compile` on every modified file.
2. `python main.py --test` must pass.
3. `pytest -v` must pass (the golden test stays green).
4. For route-moving commits: confirm the route map still contains the same
   endpoints with the same methods.

### State discipline (from CLAUDE.md, repeated because it matters)

When a move touches session / config / engine state, explicitly answer:

- Is it snapshotted in `_ensure_reset_baseline`?
- Is it copied by `_sync_global_config_from_engine` on session switch?
- Is it applied by `_get_session_config_overrides` on rebuild?
- Is it replayed or recomputed after rebuild?
- Does it trigger the correct downstream recalculation?
- Is it serialized in `_save_sessions_to_disk` and restored by
  `_load_sessions_from_disk`?

If any answer is "no" or "not sure," stop.

### Required report at end of each change

Every reply that includes a commit must state:

1. **What moved:** function or route and its new module.
2. **Signature change:** before → after, or "none."
3. **Call sites updated:** files and line numbers touched.
4. **Globals touched:** which of `sessions`, `active_session_id`,
   `_global_config` the code reads or writes, and how the new module
   accesses them (parameter, import, or other).
5. **Verification results:** `py_compile`, `main.py --test`, `pytest`.
6. **Unexpected decisions:** anything you decided without explicit
   guidance. Flag these so the user can veto.

If you cannot produce this report honestly, say so. Do not fabricate.

### Out of scope for this refactor

Do not do any of the following, even if asked in the middle of an
extraction:

- Adding new tests.
- Improving any existing function's logic.
- Renaming variables or functions.
- Updating docstrings beyond relocating them.
- Adding or removing type hints.
- Reorganizing internal structure of target modules.
- Refactoring engine code under `modules/`.

If the user requests any of these mid-task, complete the current
extraction, commit, then start a separate task.

---

## Current state of the refactor

### Already moved

- License routes → `ui/routes/license.py`
- Config / folder routes → `ui/routes/config.py`
- Read-only result routes → `ui/routes/read.py`
- Machine routes → `ui/routes/machines.py`
- PAP routes → `ui/routes/pap.py`

### Still in `ui/app.py`

- Start / upload / calculate routes
- Export / MoM routes
- Edit routes
- Scenario routes
- Session routes
- Seven helper functions (see below)

### Helper extraction (parallel track)

Seven helper functions currently defined in `ui/app.py` need to move to
the sibling modules that already exist. This can happen before, during,
or after the route moves — in its own commits.

| Function                          | Target module            |
|-----------------------------------|--------------------------|
| `_snapshot_engine_state`          | `ui/state_snapshot.py`   |
| `_ensure_reset_baseline`          | `ui/state_snapshot.py`   |
| `_sync_global_config_from_engine` | `ui/config_store.py`     |
| `_get_session_config_overrides`   | `ui/config_store.py`     |
| `_build_clean_engine_for_session` | `ui/engine_rebuild.py`   |
| `_replay_pending_edits`           | `ui/replay.py`           |
| `_apply_volume_change`            | see "Stop and ask"       |

Prefer parameter passing over cross-module imports when a helper
references `sessions`, `active_session_id`, or `_global_config`. If
parameter passing is impractical, import explicitly and flag it in your
report.

Never duplicate global state across modules.

---

## Route group reference

Use this section when working on a specific group. The state / risk /
helpers lists describe what must be preserved.

### Sessions

**Routes**
- `POST /api/sessions/snapshot`
- `GET /api/sessions`
- `POST /api/sessions/rename`
- `POST /api/sessions/switch`
- `DELETE /api/sessions/<session_id>`

**State touched**
- `sessions`
- `active_session_id`
- `_global_config`
- per-session `engine`
- `reset_baseline`
- `pending_edits`
- `machine_overrides`
- `value_aux_overrides`
- `valuation_params`
- `parameters`

**Risks**
- Wrong active session after switch/delete.
- `_global_config` shows values from previous instance.
- Rebuild after restart misses session-specific config.
- Pending edits are not replayed.
- Reset baseline belongs to the wrong engine.

**Helpers / contracts to preserve**
- `_sync_global_config_from_engine`
- `_get_session_config_overrides`
- `_build_clean_engine_for_session`
- `_install_clean_engine_baseline`
- `_replay_pending_edits`
- `_save_sessions_to_disk`
- `_load_sessions_from_disk`
- `_ensure_reset_baseline`

**Minimal verification**
- Route map contains the same session endpoints.
- Create and snapshot a new session.
- Switch between sessions both directions.
- Rename and delete.
- `python main.py --test` passes.

### Scenarios

**Routes**
- `GET /api/scenarios`
- `POST /api/scenarios/save`
- `POST /api/scenarios/load`
- `DELETE /api/scenarios/<scenario_id>`
- `POST /api/scenarios/compare`
- `GET /api/scenarios/compare/export`

**State touched**
- `scenarios`
- `sessions[active_session_id]`
- `engine.results`
- `engine.value_results`
- `pending_edits`
- `value_aux_overrides`
- `machine_overrides`
- `purchased_and_produced`

**Risks**
- Scenario load restores tables but not all derived chart data.
- Pending edits snapshot diverges from live / replay behavior.
- PAP or machine overrides make it into the scenario but not into
  rebuild.
- Compare / export uses stale or wrong scenario selection.

**Helpers / contracts to preserve**
- `_snapshot_engine_state`
- `restore_engine_state`
- `_build_pending_edits_from_results_snapshot`
- `_planning_value_payload`
- `_moq_warnings_payload`
- `_parse_purchased_and_produced`
- `_format_purchased_and_produced`

**Minimal verification**
- Save, load, and delete a scenario.
- Compare two scenarios.
- Call scenario compare export.
- After scenario load: table, dashboard, and value results agree.
- `python main.py --test` passes.

### Edits

**Routes**
- `GET /api/editable_line_types`
- `POST /api/sessions/edits/persist`
- `POST /api/sessions/edits/sync`
- `POST /api/update_volume`
- `POST /api/update_value_aux`
- `POST /api/reset_value_planning_edits`
- `POST /api/undo`
- `POST /api/redo`
- `GET /api/edits/export`
- `POST /api/edits/import`
- `POST /api/reset_edits`

**State touched**
- `engine.results`
- `engine.value_results`
- `pending_edits`
- `value_aux_overrides`
- `reset_baseline`
- `undo_stack`
- `redo_stack`
- Downstream machine / capacity / value results.

**Risks**
- Live edit and replay edit produce different results. Per CLAUDE.md,
  replay is the source of truth; if they disagree, live is wrong.
- Line 01 / Line 06 edit cascade misses dependent demand, inventory, or
  capacity.
- Undo / redo updates the table but not value results or charts.
- Import / export of edits changes order or overwrites the original
  baseline.
- Reset appears to work but leaves a new state field untouched.

**Helpers / contracts to preserve**
- `_apply_volume_change`
- `_recalc_one_material`
- `_recalculate_value_results`
- `_recalculate_capacity_and_values`
- `_replay_pending_edits`
- `_ensure_reset_baseline`
- `_planning_value_payload`
- `_value_results_payload`

**Minimal verification**
- Edit Line 01; check downstream volume, capacity, value.
- Edit Line 06; check inventory, capacity, value.
- Undo, redo, reset.
- Export and import edits.
- Simulate restart / rebuild path with pending edits.
- `python main.py --test` passes.

### Exports and MoM

**Routes**
- `GET /api/export`
- `POST /api/export_db`
- `GET /api/mom`

**State touched**
- `engine.results`
- `engine.value_results`
- `_cycle_manager`
- `APP_EXPORTS_DIR`
- Optional DB export payload.

**Risks**
- Runtime files written into the repo instead of the app-data directory.
- Export uses stale active session.
- MoM snapshot or compare uses wrong cycle folder.
- Export missing value planning or consolidation rows.

**Helpers / contracts to preserve**
- `PlanningEngine.to_excel_with_values`
- `DatabaseExporter`
- `MoMComparisonEngine`
- `_cycle_manager`
- `_json_safe`

**Minimal verification**
- Download a planning export.
- Run a DB export.
- Call the MoM endpoint.
- Output lands in the correct exports folder.
- `python main.py --test` passes.

### Start, upload, and calculate

**Routes**
- `GET /`
- `POST /api/upload`
- `POST /api/calculate`

**State touched**
- `sessions`
- `active_session_id`
- `_global_config`
- Uploaded workbook path.
- Per-session engine.
- Reset baseline.
- Runtime folders.

**Risks**
- New upload overwrites the wrong session.
- Calculate builds the engine from global config instead of session
  config.
- Error classification for upload / calculate becomes less precise.
- Initial baseline is snapshotted too early or too late.

**Helpers / contracts to preserve**
- `_classify_upload_exception`
- `_get_session_config_overrides`
- `_sync_global_config_from_engine`
- `_ensure_reset_baseline`
- `_save_sessions_to_disk`
- `_moq_warnings_payload`

**Minimal verification**
- Web app opens.
- Upload a workbook.
- Calculate.
- A new session appears and is active.
- `python main.py --test` passes.

---

## Per-step checklist

For every route move:

- Move the route handlers to `ui/routes/<group>.py`.
- Inject dependencies through `create_<group>_blueprint(...)`.
- Leave numeric / cascade helpers in place.
- Preserve endpoint paths, methods, and response shapes.
- Confirm the route map.
- Run `python -m py_compile` on every modified module.
- Run `python main.py --test`.
- Run `pytest -v`.
- Commit as a separate small step.
- Produce the end-of-task report described above.

For every helper extraction:

- Copy the function body byte-for-byte to the target module.
- Replace the original in `ui/app.py` with an import.
- Update call sites if the signature changed (prefer parameter passing).
- Confirm no circular import was introduced.
- Run `python -m py_compile`, `python main.py --test`, and `pytest -v`.
- Commit as a separate small step.
- Produce the end-of-task report.