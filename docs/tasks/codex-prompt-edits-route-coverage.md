# Codex prompt — Expand test_routes_edits.py coverage

## Goal

`ui/routes/edits.py` currently sits at **17% coverage** (`211` lines, `176`
uncovered). The existing `tests/test_routes_edits.py` only exercises
`/api/update_volume` happy path, the machines reset, and `edits/persist`.

All the remaining route handlers are untested:

- `/api/update_volume` — no-engine 400, no-JSON 400
- `/api/update_value_aux` — no-engine 400, invalid value 400, non-editable
  line type 403, row not found 404, happy path
- `/api/reset_value_planning_edits` — no-engine 400, happy path (with and
  without a baseline that has valuation_params)
- `/api/undo` — no-engine 400, empty stack 400, happy path
- `/api/redo` — no-engine 400, empty stack 400, happy path
- `/api/edits/export` — no-engine 400, happy path (manual edits + value_aux)
- `/api/edits/import` — no-engine 400, no-body 400, happy path with edits,
  happy path with value_aux_edits, propagated apply error
- `/api/reset_edits` — no-engine 400, clean baseline restores state, dirty
  baseline builds clean engine, no clean engine returns 400

This is a **pure test-addition task**. Do not change any production code.

## Read these files before writing any code

- `ui/routes/edits.py` — the full file (211 lines)
- `tests/test_routes_edits.py` — existing tests (do not break any)
- `tests/conftest.py` (the `edit_route_app` fixture — understand it but do
  NOT modify conftest.py)
- `modules/models.py` (`PlanningRow` — needed to build realistic rows)

## What the existing conftest fixture gives you

`edit_route_app` (conftest.py) wires all twelve `create_edits_blueprint`
params. It uses:
- `value_aux_editable_line_types = set()` (empty) → every `update_value_aux`
  call gets 403 from the current fixture
- Most callbacks → `crash_callback` (raises RuntimeError)
- `apply_volume_change` → records call to `volume_calls` and returns
  `jsonify({success: True, ...})`

This fixture is fine for the three existing tests. **Do not use it for the
new tests** — the crash_callbacks and empty `value_aux_editable_line_types`
make it wrong for the new routes. Create a separate local fixture instead.

## Step 1 — Add `edits_mock_app` fixture in test_routes_edits.py

Create a **local** pytest fixture (inside `tests/test_routes_edits.py`, not
in conftest.py). It must:

1. **Be marked `pytestmark = pytest.mark.no_fixture`** at the top of the new
   tests that use it, OR mark the fixture itself to not require the golden
   fixture. The cleanest approach: define it at module level with no
   `golden_fixture_path` dependency — just build a small synthetic engine.

2. **Engine**: build a `SimpleNamespace`-based engine with:
   - `results` — dict with at least:
     - `LineType.DEMAND_FORECAST.value` → one real `PlanningRow` (material
       `"MAT-1"`, values `{"2025-12": 50.0}`, manual_edits `{}`)
     - `LineType.TOTAL_DEMAND.value` → one real `PlanningRow` same material,
       values `{"2025-12": 50.0}`, manual_edits `{}`
   - `value_results` — dict with:
     - `LineType.DEMAND_FORECAST.value` → one real `PlanningRow` (material
       `"MAT-1"`, `aux_column="2.5"`, values `{"2025-12": 50.0}`)
     - `LineType.CONSOLIDATION.value` → empty list
   - `data` — `SimpleNamespace(valuation_params={"1": 1.0})`

3. **Session** (`sess`): dict with:
   ```python
   {
       "id": "mock-session",
       "pending_edits": {},
       "value_aux_overrides": {},
       "undo_stack": [],
       "redo_stack": [],
       "reset_baseline": {
           "results": True,   # truthy so reset_edits sees a baseline
           "valuation_params": {"1": 1.0},
       },
   }
   ```

4. **Callbacks** (all recordable, none crashing):
   - `apply_volume_change(sess, engine, line_type, mat, period, new_value,
     aux_column='', push_undo=True)` → appends to `app.volume_calls`, returns
     `jsonify({'success': True, 'results': {}, 'value_results': {},
     'consolidation': [], 'edit_meta': {'old_value': 0.0, 'new_value':
     new_value, 'original_value': 0.0, 'delta_pct': 0.0}})`. Add a
     `app.fail_next_volume_call` flag (default False): when True, return
     `jsonify({'error': 'injected failure'}), 400` instead and reset the flag.
   - `ensure_reset_baseline(sess, engine)` → appends to `app.baseline_calls`
   - `recalculate_value_results(engine, sess)` → appends to `app.recalc_calls`
   - `save_sessions_to_disk()` → appends to `app.save_calls`
   - `valuation_params_from_config(config)` → returns `dict(config)`
   - `restore_engine_state(engine, baseline)` → appends to `app.restore_calls`
   - `snapshot_has_manual_edits(baseline)` → returns
     `baseline.get("has_manual_edits", False)` from the baseline dict
   - `build_clean_engine_for_session(sess)` → returns
     `app.clean_engine` (initially the same engine object — tests can swap it
     to `None` to test the "no clean engine" branch)
   - `install_clean_engine_baseline(sess, engine)` → appends to
     `app.install_calls`
   - `value_aux_editable_line_types = {LineType.DEMAND_FORECAST.value}`

5. **`make_session(engine=<default engine>)`** helper that replaces the active
   session's engine. Calling `make_session(engine=None)` should set the
   engine key to `None` so `get_active()` returns `(sess, None)` — this is
   how we test no-engine paths without rebuilding the full app.

6. **Return** `SimpleNamespace` with at least: `app`, `client`, `sess`,
   `engine`, `volume_calls`, `baseline_calls`, `recalc_calls`, `save_calls`,
   `restore_calls`, `install_calls`, `clean_engine`, `fail_next_volume_call`
   (on the SimpleNamespace, mutable by tests), `make_session`.

Mark ALL tests that use `edits_mock_app` with `@pytest.mark.no_fixture`.

## Step 2 — Write the tests

Add the tests below to `tests/test_routes_edits.py`. Use `edits_mock_app`
for all of them.

### `/api/update_volume`

```
test_update_volume_no_engine_returns_400
    make_session(engine=None)
    POST /api/update_volume with any JSON body
    assert 400, error "No calculations run"

test_update_volume_no_json_returns_400
    make_session()
    POST /api/update_volume with no body (content_type not json)
    assert 400, error "No JSON body"
```

### `/api/update_value_aux`

```
test_update_value_aux_no_engine_returns_400
    make_session(engine=None)
    POST /api/update_value_aux with {}
    assert 400

test_update_value_aux_invalid_value_returns_400
    make_session()
    POST with line_type=DEMAND_FORECAST, material_number="MAT-1",
         new_value="not-a-number"
    assert 400, error "Invalid aux value"

test_update_value_aux_non_editable_line_type_returns_403
    make_session()
    POST with line_type=LineType.TOTAL_DEMAND.value, material_number="MAT-1",
         new_value=3.0
    assert 403, error contains "not editable"

test_update_value_aux_missing_row_returns_404
    make_session()
    POST with line_type=DEMAND_FORECAST, material_number="NO_SUCH_MAT",
         new_value=3.0
    assert 404, error "Value row not found"

test_update_value_aux_updates_override_and_recalcs
    make_session()
    POST with line_type=DEMAND_FORECAST, material_number="MAT-1",
         new_value=5.0
    assert 200, success True
    assert app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"]
           == {"original": 2.5, "new_value": 5.0}
    assert len(app.recalc_calls) == 1
    assert len(app.save_calls) == 1
    assert "edit_meta" in payload
    assert "value_aux_overrides" in payload

test_update_value_aux_removes_override_when_restored_to_original
    make_session()
    # First set an override
    app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"] = {
        "original": 2.5, "new_value": 5.0
    }
    # Now send new_value == original (2.5)
    POST with line_type=DEMAND_FORECAST, material_number="MAT-1",
         new_value=2.5
    assert 200
    assert "01. Demand forecast||MAT-1" not in app.sess["value_aux_overrides"]
```

### `/api/reset_value_planning_edits`

```
test_reset_value_planning_edits_no_engine_returns_400
    make_session(engine=None)
    POST /api/reset_value_planning_edits
    assert 400

test_reset_value_planning_edits_clears_overrides_and_recalcs
    make_session()
    app.sess["value_aux_overrides"] = {"some||key": {"original": 1.0, "new_value": 2.0}}
    # No valuation_params in baseline (set reset_baseline to {} so branch
    # that restores vp is not taken)
    app.sess["reset_baseline"] = {}
    POST /api/reset_value_planning_edits
    assert 200, success True
    assert app.sess["value_aux_overrides"] == {}
    assert len(app.recalc_calls) == 1
    assert len(app.save_calls) == 1
    assert "restored_valuation_params" not in payload

test_reset_value_planning_edits_restores_valuation_params_from_baseline
    make_session()
    app.sess["reset_baseline"] = {
        "results": True,
        "valuation_params": {"1": 2.0},
    }
    POST /api/reset_value_planning_edits
    assert 200
    assert payload["restored_valuation_params"] == {"1": 2.0}
    assert app.engine.data.valuation_params == {"1": 2.0}
```

Note: `valuation_params_from_config` in the fixture returns `dict(config)`, so
`current_engine.data.valuation_params` gets set to `{"1": 2.0}`. The engine's
`data` is a `SimpleNamespace` — you can set attributes on it freely.

### `/api/undo`

```
test_undo_no_engine_returns_400
    make_session(engine=None)
    POST /api/undo
    assert 400

test_undo_empty_stack_returns_400
    make_session()   # undo_stack is []
    POST /api/undo
    assert 400, error "Nothing to undo"

test_undo_pops_stack_and_calls_apply_volume_change
    make_session()
    app.sess["undo_stack"] = [{
        "line_type": "01. Demand forecast",
        "material_number": "MAT-1",
        "period": "2025-12",
        "old_value": 40.0,
        "new_value": 50.0,
        "aux_column": "",
    }]
    POST /api/undo
    assert 200, success True
    assert app.sess["undo_stack"] == []
    assert len(app.sess["redo_stack"]) == 1
    assert len(app.volume_calls) == 1
    call = app.volume_calls[0]
    assert call["new_value"] == pytest.approx(40.0)   # old_value applied
    assert call["push_undo"] is False
```

### `/api/redo`

```
test_redo_no_engine_returns_400
    make_session(engine=None)
    POST /api/redo
    assert 400

test_redo_empty_stack_returns_400
    make_session()   # redo_stack is []
    POST /api/redo
    assert 400, error "Nothing to redo"

test_redo_pops_stack_and_calls_apply_volume_change
    make_session()
    app.sess["redo_stack"] = [{
        "line_type": "01. Demand forecast",
        "material_number": "MAT-1",
        "period": "2025-12",
        "old_value": 40.0,
        "new_value": 50.0,
        "aux_column": "",
    }]
    POST /api/redo
    assert 200, success True
    assert app.sess["redo_stack"] == []
    assert len(app.sess["undo_stack"]) == 1
    call = app.volume_calls[0]
    assert call["new_value"] == pytest.approx(50.0)   # new_value applied
    assert call["push_undo"] is False
```

### `/api/edits/export`

```
test_export_edits_no_engine_returns_400
    make_session(engine=None)
    GET /api/edits/export
    assert 400

test_export_edits_returns_json_with_manual_edits_and_value_aux
    make_session()
    # Give the DEMAND_FORECAST row a manual edit
    from modules.models import LineType
    row = app.engine.results[LineType.DEMAND_FORECAST.value][0]
    row.manual_edits = {"2025-12": {"original": 40.0, "new": 50.0}}
    # Give the session a value_aux override
    app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"] = {
        "original": 2.0, "new_value": 3.5
    }
    GET /api/edits/export
    assert 200
    assert response.content_type == "application/json"
    data = json.loads(response.data)
    assert len(data["edits"]) == 1
    edit = data["edits"][0]
    assert edit["line_type"] == LineType.DEMAND_FORECAST.value
    assert edit["material_number"] == "MAT-1"
    assert edit["period"] == "2025-12"
    assert edit["original"] == pytest.approx(40.0)
    assert edit["new"] == pytest.approx(50.0)
    assert len(data["value_aux_edits"]) == 1
    assert "exported_at" in data
```

### `/api/edits/import`

```
test_import_edits_no_engine_returns_400
    make_session(engine=None)
    POST /api/edits/import with {}
    assert 400

test_import_edits_no_body_returns_400
    make_session()
    POST /api/edits/import with no body
    assert 400, error "No JSON body"

test_import_edits_applies_edits_and_returns_results
    make_session()
    body = {"edits": [
        {"line_type": "01. Demand forecast", "material_number": "MAT-1",
         "period": "2025-12", "new": 99.0, "aux_column": ""},
    ], "value_aux_edits": []}
    POST /api/edits/import with body
    assert 200, success True
    assert len(app.volume_calls) == 1
    assert app.volume_calls[0]["new_value"] == pytest.approx(99.0)
    assert app.volume_calls[0]["push_undo"] is False
    assert len(app.recalc_calls) == 1
    assert len(app.save_calls) == 1

test_import_edits_applies_value_aux_overrides
    make_session()
    body = {
        "edits": [],
        "value_aux_edits": [
            {"line_type": "01. Demand forecast", "material_number": "MAT-1",
             "original": 2.0, "new": 4.0},
        ],
    }
    POST /api/edits/import with body
    assert 200
    assert app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"] \
           == {"original": 2.0, "new_value": 4.0}

test_import_edits_propagates_apply_error
    make_session()
    app.fail_next_volume_call = True
    body = {"edits": [
        {"line_type": "01. Demand forecast", "material_number": "MAT-1",
         "period": "2025-12", "new": 99.0, "aux_column": ""},
    ], "value_aux_edits": []}
    POST /api/edits/import with body
    assert 400
    payload = response.get_json()
    assert "Could not import edit" in payload["error"]
```

### `/api/reset_edits`

```
test_reset_edits_no_engine_returns_400
    make_session(engine=None)
    POST /api/reset_edits
    assert 400

test_reset_edits_clean_baseline_restores_engine_state
    make_session()
    # Default reset_baseline has {"results": True} and has_manual_edits=False
    # → snapshot_has_manual_edits returns False → "clean baseline" branch
    app.sess["pending_edits"] = {"some_key": {"original": 1.0, "new_value": 2.0}}
    app.sess["undo_stack"] = [{"dummy": "entry"}]
    POST /api/reset_edits
    assert 200, success True
    assert app.sess["pending_edits"] == {}
    assert app.sess["undo_stack"] == []
    assert app.sess["redo_stack"] == []
    assert app.sess["value_aux_overrides"] == {}
    assert len(app.restore_calls) == 1
    assert len(app.install_calls) == 1
    assert len(app.recalc_calls) == 1
    assert len(app.save_calls) == 1

test_reset_edits_dirty_baseline_builds_clean_engine
    make_session()
    # Mark baseline as having manual edits → "dirty baseline" branch
    app.sess["reset_baseline"]["has_manual_edits"] = True
    # clean_engine is the same engine object (build_clean_engine returns it)
    POST /api/reset_edits
    assert 200
    # restore_engine_state should NOT be called (we went through build path)
    assert len(app.restore_calls) == 0
    assert app.sess["engine"] is app.clean_engine

test_reset_edits_no_clean_engine_returns_400
    make_session()
    app.sess["reset_baseline"]["has_manual_edits"] = True
    app.clean_engine = None   # build_clean_engine_for_session will return None
    POST /api/reset_edits
    assert 400, error "No clean reset baseline available"
```

## Step 3 — Verify

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/test_routes_edits.py -v
pytest -v --ignore=tests/browser
```

All previously passing tests must still pass. The new tests must pass.
`ui/routes/edits.py` coverage should climb from 17% to at least 65%.

## Commit

Branch: `test/routes-edits-coverage` from main.

```
test: expand edit route tests to cover undo/redo, value_aux, export/import, reset_edits
```

One commit, one file changed (`tests/test_routes_edits.py` only).

## Stop conditions

- Any previously passing test breaks → stop, report.
- Any import error or crash in the new fixture → stop, report. Do not
  silence errors with try/except.
- Coverage of `ui/routes/edits.py` stays below 60% after the run → stop and
  report which lines are still uncovered.
