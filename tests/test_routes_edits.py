import json
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify

from modules.models import LineType, PlanningRow
from ui.state_snapshot import snapshot_engine_state


def _mock_planning_row(line_type, aux_column=None):
    return PlanningRow(
        material_number="MAT-1",
        material_name="Mock material",
        product_type="Bulk Product",
        product_family="Mock family",
        spc_product="Mock SPC",
        product_cluster="Mock cluster",
        product_name="Mock product",
        line_type=line_type,
        aux_column=aux_column,
        values={"2025-12": 50.0},
        manual_edits={},
    )


@pytest.fixture
def edits_mock_app():
    """Synthetic edit route app for route orchestration tests only."""
    from ui.routes.edits import create_edits_blueprint

    engine = SimpleNamespace(
        results={
            LineType.DEMAND_FORECAST.value: [_mock_planning_row(LineType.DEMAND_FORECAST.value)],
            LineType.TOTAL_DEMAND.value: [_mock_planning_row(LineType.TOTAL_DEMAND.value)],
        },
        value_results={
            LineType.DEMAND_FORECAST.value: [_mock_planning_row(LineType.DEMAND_FORECAST.value, aux_column="2.5")],
            LineType.CONSOLIDATION.value: [],
        },
        data=SimpleNamespace(valuation_params={"1": 1.0}),
    )
    sess = {
        "id": "mock-session",
        "engine": engine,
        "pending_edits": {},
        "value_aux_overrides": {},
        "undo_stack": [],
        "redo_stack": [],
        "reset_baseline": {
            "results": True,
            "valuation_params": {"1": 1.0},
        },
    }
    sessions = {"mock-session": sess}
    active = {"session_id": "mock-session"}
    volume_calls = []
    baseline_calls = []
    recalc_calls = []
    save_calls = []
    restore_calls = []
    install_calls = []
    state = SimpleNamespace(
        clean_engine=engine,
        fail_next_volume_call=False,
    )

    def get_active():
        active_sess = sessions.get(active["session_id"])
        return active_sess, active_sess.get("engine") if active_sess else None

    sentinel = object()

    def make_session(engine=sentinel):
        sess["engine"] = state.engine if engine is sentinel else engine
        sessions[sess["id"]] = sess
        active["session_id"] = sess["id"]
        return sess

    def apply_volume_change(sess, engine, line_type, material_number, period, new_value, aux_column="", push_undo=True):
        if state.fail_next_volume_call:
            state.fail_next_volume_call = False
            response = jsonify({"error": "injected failure"})
            response.status_code = 400
            return response
        volume_calls.append({
            "sess": sess,
            "engine": engine,
            "line_type": line_type,
            "material_number": material_number,
            "period": period,
            "new_value": new_value,
            "aux_column": aux_column,
            "push_undo": push_undo,
        })
        return jsonify({
            "success": True,
            "results": {},
            "value_results": {},
            "consolidation": [],
            "edit_meta": {
                "old_value": 0.0,
                "new_value": new_value,
                "original_value": 0.0,
                "delta_pct": 0.0,
            },
        })

    def ensure_reset_baseline(sess, engine):
        baseline_calls.append({"sess": sess, "engine": engine})

    def recalculate_value_results(engine, sess):
        recalc_calls.append({"engine": engine, "sess": sess})

    def save_sessions_to_disk():
        save_calls.append(list(sessions.keys()))

    def valuation_params_from_config(config):
        return dict(config)

    def restore_engine_state(engine, baseline):
        restore_calls.append({"engine": engine, "baseline": baseline})

    def snapshot_has_manual_edits(baseline):
        return baseline.get("has_manual_edits", False)

    def build_clean_engine_for_session(sess):
        return state.clean_engine

    def install_clean_engine_baseline(sess, engine):
        install_calls.append({"sess": sess, "engine": engine})

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_edits_blueprint(
        get_active,
        {LineType.DEMAND_FORECAST.value},
        {},
        apply_volume_change,
        ensure_reset_baseline,
        recalculate_value_results,
        save_sessions_to_disk,
        valuation_params_from_config,
        restore_engine_state,
        snapshot_has_manual_edits,
        build_clean_engine_for_session,
        install_clean_engine_baseline,
    ))

    state.app = flask_app
    state.client = flask_app.test_client()
    state.sess = sess
    state.engine = engine
    state.volume_calls = volume_calls
    state.baseline_calls = baseline_calls
    state.recalc_calls = recalc_calls
    state.save_calls = save_calls
    state.restore_calls = restore_calls
    state.install_calls = install_calls
    state.make_session = make_session
    return state


def _first_result_row(engine, line_type):
    rows = engine.results.get(line_type, [])
    assert rows, f"No rows for {line_type}"
    return rows[0]


def _first_period(row):
    assert row.values, "Row has no period values"
    return next(iter(row.values))


def test_update_volume_calls_injected_callback(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]
    row = _first_result_row(engine, LineType.DEMAND_FORECAST.value)
    period = _first_period(row)
    aux_column = str(getattr(row, "aux_column", "") or "")

    response = edit_route_app.client.post(
        "/api/update_volume",
        json={
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": row.material_number,
            "period": period,
            "new_value": 123.45,
            "aux_column": aux_column,
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert set(payload) >= {"success", "results", "value_results", "consolidation", "edit_meta"}

    assert len(edit_route_app.volume_calls) == 1
    call = edit_route_app.volume_calls[0]
    assert call["sess"] is sess
    assert call["engine"] is engine
    assert call["line_type"] == LineType.DEMAND_FORECAST.value
    assert call["material_number"] == row.material_number
    assert call["period"] == period
    assert call["new_value"] == pytest.approx(123.45)
    assert call["aux_column"] == aux_column
    assert call["push_undo"] is True


def test_machines_reset_clears_only_machine_state(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]

    def shift_hours_lookup(machine, data):
        return float(getattr(machine, "shift_hours_override", None) or 0.0)

    sess["reset_baseline"] = snapshot_engine_state(engine, shift_hours_lookup)
    machine_code, machine = next(iter(engine.data.machines.items()))
    baseline = sess["reset_baseline"]["machines"][machine_code]
    pending_edits = {
        "01. Demand forecast||MAT-1||||2025-12": {
            "original": 10.0,
            "new_value": 12.0,
        },
    }
    sess["pending_edits"] = dict(pending_edits)

    machine.oee = float(baseline["oee"]) + 0.1
    machine.availability_by_period = {period: 0.25 for period in engine.data.periods}
    machine.shift_hours_override = 123.0
    sess["machine_overrides"] = {
        machine_code: {
            "oee": machine.oee,
            "availability_by_period": dict(machine.availability_by_period),
            "shift_hours_override": machine.shift_hours_override,
        },
    }

    response = edit_route_app.client.post("/api/machines/reset")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["undo_depth"] == 0
    assert payload["redo_depth"] == 0
    assert set(payload) >= {"success", "undo_depth", "redo_depth", "value_results", "consolidation"}

    assert machine.oee == pytest.approx(float(baseline["oee"]))
    assert machine.availability_by_period == baseline["availability_by_period"]
    expected_shift = baseline["shift_hours_override"]
    assert getattr(machine, "shift_hours_override", None) == (
        pytest.approx(float(expected_shift)) if expected_shift is not None else None
    )
    assert sess["machine_overrides"] == {}
    assert sess["pending_edits"] == pending_edits
    assert len(edit_route_app.recalc_calls) == 1
    assert edit_route_app.recalc_calls[0]["engine"] is engine
    assert edit_route_app.recalc_calls[0]["sess"] is sess
    assert edit_route_app.save_calls


def test_edits_persist_writes_and_removes_pending_edit_without_disk(edit_route_app):
    session_id = "persist-session"
    sess = edit_route_app.make_session(session_id=session_id)
    key = "01. Demand forecast||MAT-1||||2025-12"

    response = edit_route_app.client.post(
        "/api/sessions/edits/persist",
        json={
            "session_id": session_id,
            "key": key,
            "original": 10,
            "new_value": 12.5,
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.get_json() == {"success": True}
    assert sess["pending_edits"][key] == {"original": 10.0, "new_value": 12.5}
    assert len(edit_route_app.save_calls) == 1

    response = edit_route_app.client.post(
        "/api/sessions/edits/persist",
        json={
            "session_id": session_id,
            "key": key,
            "original": 10,
            "new_value": 10,
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.get_json() == {"success": True}
    assert key not in sess["pending_edits"]
    assert len(edit_route_app.save_calls) == 2


@pytest.mark.no_fixture
def test_update_volume_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.post(
        "/api/update_volume",
        json={
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "MAT-1",
            "period": "2025-12",
            "new_value": 10,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_update_volume_no_json_returns_400(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post(
        "/api/update_volume",
        data="null",
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "No JSON body"


@pytest.mark.no_fixture
def test_update_value_aux_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.post("/api/update_value_aux", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_update_value_aux_invalid_value_returns_400(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post(
        "/api/update_value_aux",
        json={
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "MAT-1",
            "new_value": "not-a-number",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid aux value"


@pytest.mark.no_fixture
def test_update_value_aux_non_editable_line_type_returns_403(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post(
        "/api/update_value_aux",
        json={
            "line_type": LineType.TOTAL_DEMAND.value,
            "material_number": "MAT-1",
            "new_value": 3.0,
        },
    )

    assert response.status_code == 403
    assert "not editable" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_update_value_aux_missing_row_returns_404(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post(
        "/api/update_value_aux",
        json={
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "NO_SUCH_MAT",
            "new_value": 3.0,
        },
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Value row not found"


@pytest.mark.no_fixture
def test_update_value_aux_updates_override_and_recalcs(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post(
        "/api/update_value_aux",
        json={
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "MAT-1",
            "new_value": 5.0,
        },
    )

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["success"] is True
    assert edits_mock_app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"] == {
        "original": 2.5,
        "new_value": 5.0,
    }
    assert len(edits_mock_app.recalc_calls) == 1
    assert len(edits_mock_app.save_calls) == 1
    assert "edit_meta" in payload
    assert "value_aux_overrides" in payload


@pytest.mark.no_fixture
def test_update_value_aux_removes_override_when_restored_to_original(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"] = {
        "original": 2.5,
        "new_value": 5.0,
    }

    response = edits_mock_app.client.post(
        "/api/update_value_aux",
        json={
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "MAT-1",
            "new_value": 2.5,
        },
    )

    assert response.status_code == 200, response.get_json()
    assert "01. Demand forecast||MAT-1" not in edits_mock_app.sess["value_aux_overrides"]


@pytest.mark.no_fixture
def test_reset_value_planning_edits_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.post("/api/reset_value_planning_edits")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_reset_value_planning_edits_clears_overrides_and_recalcs(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["value_aux_overrides"] = {
        "some||key": {"original": 1.0, "new_value": 2.0},
    }
    edits_mock_app.sess["reset_baseline"] = {}

    response = edits_mock_app.client.post("/api/reset_value_planning_edits")

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["success"] is True
    assert edits_mock_app.sess["value_aux_overrides"] == {}
    assert len(edits_mock_app.recalc_calls) == 1
    assert len(edits_mock_app.save_calls) == 1
    assert "restored_valuation_params" not in payload


@pytest.mark.no_fixture
def test_reset_value_planning_edits_restores_valuation_params_from_baseline(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["reset_baseline"] = {
        "results": True,
        "valuation_params": {"1": 2.0},
    }

    response = edits_mock_app.client.post("/api/reset_value_planning_edits")

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["restored_valuation_params"] == {"1": 2.0}
    assert edits_mock_app.engine.data.valuation_params == {"1": 2.0}


@pytest.mark.no_fixture
def test_undo_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.post("/api/undo")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_undo_empty_stack_returns_400(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post("/api/undo")

    assert response.status_code == 400
    assert response.get_json()["error"] == "Nothing to undo"


@pytest.mark.no_fixture
def test_undo_pops_stack_and_calls_apply_volume_change(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["undo_stack"] = [{
        "line_type": LineType.DEMAND_FORECAST.value,
        "material_number": "MAT-1",
        "period": "2025-12",
        "old_value": 40.0,
        "new_value": 50.0,
        "aux_column": "",
    }]

    response = edits_mock_app.client.post("/api/undo")

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["success"] is True
    assert edits_mock_app.sess["undo_stack"] == []
    assert len(edits_mock_app.sess["redo_stack"]) == 1
    assert len(edits_mock_app.volume_calls) == 1
    call = edits_mock_app.volume_calls[0]
    assert call["new_value"] == pytest.approx(40.0)
    assert call["push_undo"] is False


@pytest.mark.no_fixture
def test_redo_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.post("/api/redo")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_redo_empty_stack_returns_400(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post("/api/redo")

    assert response.status_code == 400
    assert response.get_json()["error"] == "Nothing to redo"


@pytest.mark.no_fixture
def test_redo_pops_stack_and_calls_apply_volume_change(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["redo_stack"] = [{
        "line_type": LineType.DEMAND_FORECAST.value,
        "material_number": "MAT-1",
        "period": "2025-12",
        "old_value": 40.0,
        "new_value": 50.0,
        "aux_column": "",
    }]

    response = edits_mock_app.client.post("/api/redo")

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["success"] is True
    assert edits_mock_app.sess["redo_stack"] == []
    assert len(edits_mock_app.sess["undo_stack"]) == 1
    assert len(edits_mock_app.volume_calls) == 1
    call = edits_mock_app.volume_calls[0]
    assert call["new_value"] == pytest.approx(50.0)
    assert call["push_undo"] is False


@pytest.mark.no_fixture
def test_export_edits_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.get("/api/edits/export")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_export_edits_returns_json_with_manual_edits_and_value_aux(edits_mock_app):
    edits_mock_app.make_session()
    row = edits_mock_app.engine.results[LineType.DEMAND_FORECAST.value][0]
    row.manual_edits = {"2025-12": {"original": 40.0, "new": 50.0}}
    edits_mock_app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"] = {
        "original": 2.0,
        "new_value": 3.5,
    }

    response = edits_mock_app.client.get("/api/edits/export")

    assert response.status_code == 200, response.get_json(silent=True)
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


@pytest.mark.no_fixture
def test_import_edits_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.post("/api/edits/import", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_import_edits_no_body_returns_400(edits_mock_app):
    edits_mock_app.make_session()

    response = edits_mock_app.client.post(
        "/api/edits/import",
        data="null",
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "No JSON body"


@pytest.mark.no_fixture
def test_import_edits_applies_edits_and_returns_results(edits_mock_app):
    edits_mock_app.make_session()
    body = {
        "edits": [{
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "MAT-1",
            "period": "2025-12",
            "new": 99.0,
            "aux_column": "",
        }],
        "value_aux_edits": [],
    }

    response = edits_mock_app.client.post("/api/edits/import", json=body)

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["success"] is True
    assert len(edits_mock_app.volume_calls) == 1
    assert edits_mock_app.volume_calls[0]["new_value"] == pytest.approx(99.0)
    assert edits_mock_app.volume_calls[0]["push_undo"] is False
    assert len(edits_mock_app.recalc_calls) == 1
    assert len(edits_mock_app.save_calls) == 1


@pytest.mark.no_fixture
def test_import_edits_applies_value_aux_overrides(edits_mock_app):
    edits_mock_app.make_session()
    body = {
        "edits": [],
        "value_aux_edits": [{
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "MAT-1",
            "original": 2.0,
            "new": 4.0,
        }],
    }

    response = edits_mock_app.client.post("/api/edits/import", json=body)

    assert response.status_code == 200, response.get_json()
    assert edits_mock_app.sess["value_aux_overrides"]["01. Demand forecast||MAT-1"] == {
        "original": 2.0,
        "new_value": 4.0,
    }


@pytest.mark.no_fixture
def test_import_edits_propagates_apply_error(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.fail_next_volume_call = True
    body = {
        "edits": [{
            "line_type": LineType.DEMAND_FORECAST.value,
            "material_number": "MAT-1",
            "period": "2025-12",
            "new": 99.0,
            "aux_column": "",
        }],
        "value_aux_edits": [],
    }

    response = edits_mock_app.client.post("/api/edits/import", json=body)

    assert response.status_code == 400
    assert "Could not import edit" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_reset_edits_no_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session(engine=None)

    response = edits_mock_app.client.post("/api/reset_edits")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_reset_edits_clean_baseline_restores_engine_state(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["pending_edits"] = {
        "some_key": {"original": 1.0, "new_value": 2.0},
    }
    edits_mock_app.sess["undo_stack"] = [{"dummy": "entry"}]

    response = edits_mock_app.client.post("/api/reset_edits")

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["success"] is True
    assert edits_mock_app.sess["pending_edits"] == {}
    assert edits_mock_app.sess["undo_stack"] == []
    assert edits_mock_app.sess["redo_stack"] == []
    assert edits_mock_app.sess["value_aux_overrides"] == {}
    assert len(edits_mock_app.restore_calls) == 1
    assert len(edits_mock_app.install_calls) == 1
    assert len(edits_mock_app.recalc_calls) == 1
    assert len(edits_mock_app.save_calls) == 1


@pytest.mark.no_fixture
def test_reset_edits_dirty_baseline_builds_clean_engine(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["reset_baseline"]["has_manual_edits"] = True

    response = edits_mock_app.client.post("/api/reset_edits")

    assert response.status_code == 200, response.get_json()
    assert len(edits_mock_app.restore_calls) == 0
    assert edits_mock_app.sess["engine"] is edits_mock_app.clean_engine


@pytest.mark.no_fixture
def test_reset_edits_no_clean_engine_returns_400(edits_mock_app):
    edits_mock_app.make_session()
    edits_mock_app.sess["reset_baseline"]["has_manual_edits"] = True
    edits_mock_app.clean_engine = None

    response = edits_mock_app.client.post("/api/reset_edits")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No clean reset baseline available. Recalculate this session first."
