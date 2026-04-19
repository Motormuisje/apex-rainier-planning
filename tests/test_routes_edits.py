import pytest

from modules.models import LineType
from ui.state_snapshot import snapshot_engine_state


def _first_result_row(engine, line_type):
    rows = engine.results.get(line_type, [])
    assert rows, f"No rows for {line_type}"
    return rows[0]


def _first_period(row):
    assert row.values, "Row has no period values"
    return next(iter(row.values))


def test_update_volume_calls_injected_callback_and_autosaves(edit_route_app):
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
    assert edit_route_app.save_calls


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
