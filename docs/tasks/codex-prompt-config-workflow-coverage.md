# Codex prompt — Expand config and workflow route test coverage

## Goal

Two route files have significant uncovered paths. All tests in this sprint
that do not use the golden fixture must be `@pytest.mark.no_fixture`.

Current gaps:
- `ui/routes/config.py` — 51%. Uncovered: `save_config_settings` with an
  active engine (PAP recalc, valuation-params recalc, structural rebuild),
  and `reset_vp_params` happy path + no-baseline 400.
- `ui/routes/workflow.py` — 51%. Uncovered: `calculate` no-session 400,
  `upload` validation errors, and the three private helper functions.

Do **not** change any production code.

## Read these files before writing any code

- `ui/routes/config.py` — full file
- `ui/routes/workflow.py` — full file
- `tests/test_routes_config.py` — existing tests (do not duplicate or break)
- `tests/test_routes_workflow.py` — existing tests (do not duplicate or break)
- `tests/conftest.py` — `flask_test_app` fixture (for workflow no-engine tests)

---

## Part 1 — tests/test_routes_config.py additions

### New fixture: `config_engine_app`

Add a **second local fixture** directly in `tests/test_routes_config.py`.
The existing `config_route_app` uses crash callbacks for all engine-touching
paths — do not modify it.

The new fixture wires up the same `create_config_blueprint` factory but with
a mock engine and real (recording) callbacks for all the engine paths:

```python
@pytest.fixture
def config_engine_app(tmp_path):
    from flask import Flask
    from ui.parsers import parse_purchased_and_produced, valuation_params_from_config
    from ui.routes.config import create_config_blueprint

    # Mock engine
    engine = SimpleNamespace(
        data=SimpleNamespace(
            purchased_and_produced={"MAT-1": 0.5},
            valuation_params=SimpleNamespace(
                direct_fte_cost_per_month=1.0, indirect_fte_cost_per_month=2.0,
                overhead_cost_per_month=3.0, sga_cost_per_month=4.0,
                depreciation_per_year=5.0, net_book_value=6.0,
                days_sales_outstanding=7.0, days_payable_outstanding=8.0,
            ),
            periods=["2025-12"],
        ),
        results={},
        value_results={},
        all_purch_raw_needs={},
    )
    sess = {
        "id": "config-engine-session",
        "reset_baseline": {"valuation_params": {"1": 5.0}},
        "pending_edits": {},
        "engine": engine,
    }
    active = {"sess": sess, "engine": engine}
    global_config = {
        "site": "NLX1",
        "forecast_months": 12,
        "unlimited_machines": "",
        "purchased_and_produced": "MAT-1:0.5",
        "valuation_params": {"1": 1.0},
    }
    defaults = {
        "uploads": str(tmp_path / "uploads"),
        "exports": str(tmp_path / "exports"),
        "sessions": str(tmp_path / "sessions"),
    }

    baseline_calls, pap_calls, finish_calls = [], [], []
    recalc_calls, rebuild_calls, install_calls = [], [], []
    replay_calls, save_calls = [], []
    state = SimpleNamespace(clean_engine=engine)

    def get_active():
        return active["sess"], active["engine"]

    def ensure_reset_baseline(s, e):
        baseline_calls.append((s, e))

    def recalc_pap_material(e, mat):
        pap_calls.append((e, mat))

    def finish_pap_recalc(e):
        finish_calls.append(e)

    def recalculate_value_results(e, s):
        recalc_calls.append((e, s))

    def build_clean_engine_for_session(s):
        rebuild_calls.append(s)
        return state.clean_engine

    def install_clean_engine_baseline(s, e, clear_machine_overrides=True):
        install_calls.append((s, e))

    def replay_pending_edits(s, e):
        replay_calls.append((s, e))

    def moq_warnings_payload(e):
        return {"moq_raw_needs": {}}

    def value_results_payload(e):
        return {"value_results": {}, "consolidation": []}

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_config_blueprint(
        lambda: defaults,
        global_config,
        lambda: save_calls.append(1),
        lambda u, ex, s: None,
        lambda: tmp_path / "uploads",
        get_active,
        parse_purchased_and_produced,
        valuation_params_from_config,
        ensure_reset_baseline,
        recalc_pap_material,
        finish_pap_recalc,
        recalculate_value_results,
        build_clean_engine_for_session,
        install_clean_engine_baseline,
        replay_pending_edits,
        moq_warnings_payload,
        value_results_payload,
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        engine=engine,
        sess=sess,
        active=active,
        global_config=global_config,
        state=state,
        baseline_calls=baseline_calls,
        pap_calls=pap_calls,
        finish_calls=finish_calls,
        recalc_calls=recalc_calls,
        rebuild_calls=rebuild_calls,
        install_calls=install_calls,
        replay_calls=replay_calls,
        save_calls=save_calls,
    )
```

### New tests using `config_engine_app`

All marked `@pytest.mark.no_fixture`.

```
test_save_config_settings_pap_change_recalcs_changed_materials
    # engine.data.purchased_and_produced = {"MAT-1": 0.5}
    POST /api/config/settings with purchased_and_produced = "MAT-1:0.75"
    assert 200, success True
    assert baseline_calls has 1 entry
    assert pap_calls == [(engine, "MAT-1")]
    assert len(finish_calls) == 1
    # planning_recalculated=True → periods in payload
    payload = response.get_json()
    assert "periods" in payload
    assert "value_results" in payload
    assert "moq_raw_needs" in payload
    assert app.global_config["purchased_and_produced"] == "MAT-1:0.75"

test_save_config_settings_pap_unchanged_does_not_recalc_pap
    # Same value as current: 0.5
    POST /api/config/settings with purchased_and_produced = "MAT-1:0.5"
    assert 200
    assert pap_calls == []
    assert finish_calls == []
    # else branch: recalculate_value_results IS called
    assert len(recalc_calls) == 1
    # planning_recalculated=False → no periods in payload
    assert "periods" not in response.get_json()
    assert "value_results" in response.get_json()

test_save_config_settings_valuation_params_update_recalcs_value_results
    POST /api/config/settings with valuation_params = {"1": "20.0"}
    assert 200
    assert len(recalc_calls) == 1
    assert app.global_config["valuation_params"] == {"1": 20.0}
    # value_recalculated=True, planning_recalculated=False → no periods
    assert "periods" not in response.get_json()
    assert "value_results" in response.get_json()

test_save_config_settings_structural_change_rebuilds_engine
    # site differs from current "NLX1"
    POST /api/config/settings with site = "NLX2"
    assert 200, success True
    assert len(rebuild_calls) == 1
    assert len(install_calls) == 1
    assert len(replay_calls) == 1
    assert app.active["sess"]["engine"] is app.state.clean_engine
    payload = response.get_json()
    assert "periods" in payload
    assert app.global_config["site"] == "NLX2"

test_save_config_settings_structural_change_rebuild_fails_returns_400
    app.state.clean_engine = None
    POST /api/config/settings with site = "NLX2"
    assert 400
    assert "Could not rebuild" in response.get_json()["error"]

test_reset_vp_params_no_baseline_returns_400
    app.sess["reset_baseline"] = {}   # no valuation_params key
    POST /api/config/reset_vp_params
    assert 400
    assert "No baseline available" in response.get_json()["error"]

test_reset_vp_params_restores_baseline_and_recalcs
    # sess["reset_baseline"] = {"valuation_params": {"1": 5.0}} (default in fixture)
    POST /api/config/reset_vp_params
    assert 200, success True
    payload = response.get_json()
    assert payload["valuation_params"] == {"1": 5.0}
    assert "value_results" in payload
    assert "consolidation" in payload
    assert app.global_config["valuation_params"] == {"1": 5.0}
    assert len(app.recalc_calls) == 1
    assert app.save_calls
```

---

## Part 2 — tests/test_routes_workflow.py additions

### Error path tests using `flask_test_app`

Add these to `tests/test_routes_workflow.py`. The `flask_test_app` fixture
from conftest takes only `tmp_path` — no golden fixture needed. Mark each
`@pytest.mark.no_fixture`.

```
test_calculate_no_session_returns_400
    # No active session set — flask_test_app starts with active_session_id=None
    POST /api/calculate with json={"planning_month": "2025-12", ...}
    assert 400
    assert response.get_json()["error"] == "No file uploaded"

test_upload_no_file_returns_400
    POST /api/upload with multipart body that has NO "file" field
    assert 400
    assert response.get_json()["error"] == "No file provided"
    # Hint: use data={} with content_type="multipart/form-data"

test_upload_empty_filename_returns_400
    POST /api/upload with multipart body where file field has an empty filename
    assert 400
    assert response.get_json()["error"] == "No file selected"
    # Hint: use data={"file": (io.BytesIO(b""), "")} content_type="multipart/form-data"
```

### Helper function tests (pure Python, no Flask)

Import the private helpers directly:
```python
from ui.routes.workflow import (
    _missing_required_loader_data,
    _upload_planning_params,
    _upload_summary,
)
```

Mark each `@pytest.mark.no_fixture`.

```
test_missing_required_loader_data_all_present_returns_empty_list
    loader = SimpleNamespace(
        materials=["M"], bom=["B"], routing=["R"], machines=["MC"],
        forecasts=["F"], periods=["2025-12"],
        config=SimpleNamespace(site="NLX1"),
    )
    assert _missing_required_loader_data(loader) == []

test_missing_required_loader_data_reports_each_missing_attribute
    loader = SimpleNamespace(
        materials=[], bom=None, routing=None, machines=None,
        forecasts=None, periods=None, config=None,
    )
    missing = _missing_required_loader_data(loader)
    assert "materials" in missing
    assert "bom" in missing
    assert "routing" in missing
    assert "machines" in missing
    assert "forecasts" in missing
    assert "periods" in missing
    assert "config" in missing

test_missing_required_loader_data_empty_collection_is_missing
    loader = SimpleNamespace(
        materials=[], bom=["B"], routing=["R"], machines=["MC"],
        forecasts=["F"], periods=["2025-12"], config=SimpleNamespace(),
    )
    missing = _missing_required_loader_data(loader)
    assert "materials" in missing
    assert len(missing) == 1

test_upload_planning_params_uses_requested_values_when_provided
    from datetime import date
    loader = SimpleNamespace(
        config=SimpleNamespace(initial_date=date(2025, 12, 1), forecast_months=12),
        forecast_actuals_months=11,
    )
    pm, actuals, forecast = _upload_planning_params(loader, "2026-01", 6, 9)
    assert pm == "2026-01"
    assert actuals == 6
    assert forecast == 9

test_upload_planning_params_falls_back_to_loader_config_when_not_requested
    from datetime import date
    loader = SimpleNamespace(
        config=SimpleNamespace(initial_date=date(2025, 11, 1), forecast_months=18),
        forecast_actuals_months=10,
    )
    pm, actuals, forecast = _upload_planning_params(loader, "", None, None)
    assert pm == "2025-11"
    assert actuals == 10
    assert forecast == 18

test_upload_summary_returns_correct_counts
    loader = SimpleNamespace(
        materials=["M1", "M2"],
        bom=["B1"],
        machines=["MC1", "MC2", "MC3"],
        periods=["2025-12", "2026-01"],
    )
    summary = _upload_summary(loader)
    assert summary == {
        "materials": 2,
        "bom_items": 1,
        "machines": 3,
        "periods": 2,
    }
```

---

## Verification

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/test_routes_config.py -v
pytest tests/test_routes_workflow.py -v
pytest -v --ignore=tests/browser
```

All previously passing tests must still pass.

Coverage targets after this sprint:
- `ui/routes/config.py` ≥ 75%
- `ui/routes/workflow.py` ≥ 65%

## Commit

Branch: `test/config-workflow-coverage` from main.

```
test: expand config and workflow route coverage
```

One commit, two files changed (`tests/test_routes_config.py` and
`tests/test_routes_workflow.py`).

## Stop conditions

- Any previously passing test fails → stop, report.
- Any crash in the new fixture → stop, report. Do not silence errors.
- `config_engine_app` tests use `crash_callback` accidentally and trigger
  RuntimeError → stop, the fixture has a wiring error, report which callback
  position is wrong.
- Coverage of `ui/routes/config.py` stays below 70% → stop and report
  which lines remain uncovered and why.
