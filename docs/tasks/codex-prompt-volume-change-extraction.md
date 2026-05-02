# Codex prompt — Extract apply_volume_change to ui/volume_change.py

## Goal

Move the volume-change logic out of `ui/app.py` into a new standalone module
`ui/volume_change.py` so it can be imported and unit-tested directly. This is a
pure structural refactor — no behavior changes. `python main.py --test` and the
golden pipeline test must pass before and after with identical results.

## Read these files before writing any code

- `ui/app.py` lines 118–130 (`EDITABLE_LINE_TYPES`, `VALUE_AUX_EDITABLE_LINE_TYPES`)
- `ui/app.py` lines 409–428 (`SHIFT_HOURS_LOOKUP_FALLBACK`)
- `ui/app.py` lines 521–760 (`_apply_volume_change` and inner logic)
- `ui/app.py` lines 762–780 (`_fixed_manual_values`)
- `ui/app.py` lines 815–940 (`_recalc_one_material`)
- `ui/app.py` lines 941–1000 (`_recalc_material_subtree`, `_recalculate_capacity_and_values`)
- `ui/app.py` lines 280–340 (how the blueprint is wired and `_recalculate_value_results`)
- `ui/replay.py` (`recalculate_value_results` — already standalone, importable)
- `ui/state_snapshot.py` line 288 (`ensure_reset_baseline` definition)
- `ui/routes/edits.py` (current injection pattern for `apply_volume_change`)
- `tests/conftest.py` (`edit_route_app` fixture — base for new tests)
- `tests/test_routes_edits.py` (existing edit route tests — do not break these)

## Step 1 — Create ui/volume_change.py

Create a new file `ui/volume_change.py` with exactly these contents in order:

1. **Constants** (move verbatim from `ui/app.py`):
   - `EDITABLE_LINE_TYPES`
   - `VALUE_AUX_EDITABLE_LINE_TYPES`

2. **`SHIFT_HOURS_LOOKUP_FALLBACK`** (move verbatim from `ui/app.py`)

3. **`fixed_manual_values`** (move from `_fixed_manual_values`, rename only —
   no logic change)

4. **`recalc_one_material`** (move from `_recalc_one_material`, rename only)

5. **`recalc_material_subtree`** (move from `_recalc_material_subtree`, rename
   only; update internal call `_recalc_one_material` → `recalc_one_material`)

6. **`recalculate_capacity_and_values`** (move from
   `_recalculate_capacity_and_values`, rename only; replace the call to
   `_recalculate_value_results(engine, sess)` with a direct import and call:
   `from ui.replay import recalculate_value_results` at the top of the file,
   then call `recalculate_value_results(engine, sess)` directly — this removes
   the app.py wrapper)

7. **`apply_volume_change`** (move from `_apply_volume_change`, rename only):
   - Replace `ensure_reset_baseline(sess, current_engine, SHIFT_HOURS_LOOKUP_FALLBACK)`
     with a direct call — `ensure_reset_baseline` is imported from
     `ui/state_snapshot` and `SHIFT_HOURS_LOOKUP_FALLBACK` is now in the same
     module, so the call stays identical in shape.
   - Replace all `_fixed_manual_values(...)` → `fixed_manual_values(...)`
   - Replace all `_recalc_material_subtree(...)` → `recalc_material_subtree(...)`
   - Replace all `_recalculate_capacity_and_values(...)` →
     `recalculate_capacity_and_values(...)`
   - Replace `_recalculate_value_results(engine, sess)` →
     `recalculate_value_results(engine, sess)` (imported from `ui/replay`)
   - Leave the Flask `jsonify` import — this function still returns Flask responses.
   - **Do not change the function signature or any logic.**

Imports needed at the top of `ui/volume_change.py`:
```python
from flask import jsonify
from modules.models import LineType
from ui.replay import recalculate_value_results
from ui.state_snapshot import ensure_reset_baseline
```

The lazy imports inside the function body (`from modules.inventory_engine import ...`,
`from modules.bom_engine import ...`, etc.) stay exactly as they are — do not
hoist them.

## Step 2 — Update ui/app.py

1. Remove from `ui/app.py`:
   - `EDITABLE_LINE_TYPES` and `VALUE_AUX_EDITABLE_LINE_TYPES` constants
   - `SHIFT_HOURS_LOOKUP_FALLBACK` function
   - `_fixed_manual_values` function
   - `_recalc_one_material` function
   - `_recalc_material_subtree` function
   - `_recalculate_capacity_and_values` function
   - `_apply_volume_change` function
   - The thin `_recalculate_value_results` wrapper function (also removed — its
     only caller was `_apply_volume_change`)

2. Add at the top of `ui/app.py` (with the other `ui.*` imports):
   ```python
   from ui.volume_change import (
       EDITABLE_LINE_TYPES,
       VALUE_AUX_EDITABLE_LINE_TYPES,
       SHIFT_HOURS_LOOKUP_FALLBACK,
       apply_volume_change,
       recalculate_capacity_and_values,
   )
   ```

3. Update the blueprint registration for `create_edits_blueprint`:
   - Change `lambda *args, **kwargs: _apply_volume_change(*args, **kwargs)` →
     `lambda *args, **kwargs: apply_volume_change(*args, **kwargs)`
   - Everything else in that block stays the same.

4. Update `_replay_pending_edits` in `ui/app.py`:
   - It calls `replay_pending_edits(sess, engine, _apply_volume_change, ...)` →
     change to `apply_volume_change`
   - It passes `_recalculate_capacity_and_values` → change to
     `recalculate_capacity_and_values`

5. Verify that every remaining reference to the removed names in `ui/app.py`
   now uses the imported version. Search for: `_apply_volume_change`,
   `_recalc_material_subtree`, `_recalculate_capacity_and_values`,
   `_recalculate_value_results`, `_fixed_manual_values`, `_recalc_one_material`,
   `EDITABLE_LINE_TYPES`, `VALUE_AUX_EDITABLE_LINE_TYPES`,
   `SHIFT_HOURS_LOOKUP_FALLBACK` — none should be defined in `ui/app.py` anymore.

## Step 3 — Update ui/routes/edits.py (optional but preferred)

The edits blueprint currently receives `apply_volume_change` as an injected
callback. You may leave the injection pattern in place (it still works) OR
import directly from `ui.volume_change`. Either is acceptable. If you change
the injection, update `tests/test_routes_edits.py` accordingly — but do not
break any existing test.

## Step 4 — Add tests/test_volume_change.py

Write unit tests that call `apply_volume_change` directly (no Flask, no HTTP).
Import it from `ui.volume_change`.

Use the `edit_route_app` fixture from `tests/conftest.py` to get a real engine.
The engine already has `planning_engine_result` run and all results populated.

Tests to write (all require `golden_fixture_path` — do NOT mark `no_fixture`):

1. `test_apply_volume_change_demand_forecast_updates_value` — edit a L01
   (demand forecast) cell; assert `target_row.get_value(period)` equals the new
   value and `success` is True in the returned Flask response JSON.

2. `test_apply_volume_change_demand_forecast_cascades_downstream` — after a L01
   edit, assert that L03 (total demand) for the same material and period has
   also changed from its pre-edit value. This verifies the cascade fires.

3. `test_apply_volume_change_invalid_line_type_returns_403` — pass a non-editable
   line type; assert the response status is 403.

4. `test_apply_volume_change_missing_row_returns_404` — pass a valid line type
   but a material number that does not exist; assert 404.

5. `test_apply_volume_change_pushes_undo_entry` — after an edit with
   `push_undo=True`, assert `sess['undo_stack']` has one entry with the
   correct `old_value` and `new_value`.

6. `test_apply_volume_change_skips_undo_when_push_undo_false` — with
   `push_undo=False`, assert `sess['undo_stack']` is empty.

For tests 1–2 and 5–6, pick a demand forecast row from the engine where the
raw value is > 0 so the cascade is non-trivial. Get a valid material_number
and period from `engine.data.materials` and `engine.data.periods`.

The test fixture does NOT need Flask app setup — call `apply_volume_change`
directly inside a Flask app context:

```python
from ui.volume_change import apply_volume_change

def test_...(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]
    period = engine.data.periods[0]
    material_number = engine.data.materials[0].material_number

    with edit_route_app.app.app_context():
        response = apply_volume_change(
            sess, engine, "01. Demand forecast", material_number, period, 999.0
        )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
```

## Verification (run in this order)

```powershell
python main.py --test
```

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/test_volume_change.py -v
pytest tests/test_routes_edits.py -v
pytest -v --ignore=tests/browser
```

All previously passing tests must still pass. The new tests must pass.

## Commit

Branch: `refactor/extract-volume-change` from main.

```
refactor: extract apply_volume_change and cascade helpers to ui/volume_change.py
test: add unit tests for apply_volume_change
```

Two commits. No production behavior changes — only file and import structure.

## Stop conditions

- Any `python main.py --test` failure → stop, report the exact assertion that
  failed. Do not proceed.
- Any existing test failure after the move → stop, report. Do not patch tests
  to hide the failure.
- A circular import emerges (e.g., `ui/volume_change` → `ui/app`) → stop and
  report. The dependency direction must be:
  `ui/app` → `ui/volume_change` → `ui/replay`, `ui/state_snapshot`, `modules/*`
  Never the reverse.
