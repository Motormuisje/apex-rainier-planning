import pytest

from modules.models import LineType
from ui.volume_change import apply_volume_change


def _response_parts(response):
    if isinstance(response, tuple):
        body, status = response
        return status, body.get_json()
    return response.status_code, response.get_json()


def _editable_demand_cell(engine):
    total_demand_materials = {
        row.material_number
        for row in engine.results.get(LineType.TOTAL_DEMAND.value, [])
    }
    for row in engine.results.get(LineType.DEMAND_FORECAST.value, []):
        if row.material_number not in total_demand_materials:
            continue
        for period, value in row.values.items():
            if float(value or 0) > 0:
                return row, period
    raise AssertionError("No positive demand forecast cell with total demand row found")


def _total_demand_row(engine, material_number):
    return next(
        row
        for row in engine.results.get(LineType.TOTAL_DEMAND.value, [])
        if row.material_number == material_number
    )


def test_apply_volume_change_demand_forecast_updates_value(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]
    target_row, period = _editable_demand_cell(engine)
    new_value = float(target_row.get_value(period)) + 100.0

    with edit_route_app.app.app_context():
        response = apply_volume_change(
            sess,
            engine,
            LineType.DEMAND_FORECAST.value,
            target_row.material_number,
            period,
            new_value,
        )

    status, payload = _response_parts(response)
    assert status == 200
    assert payload["success"] is True
    assert target_row.get_value(period) == pytest.approx(new_value)


def test_apply_volume_change_demand_forecast_cascades_downstream(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]
    target_row, period = _editable_demand_cell(engine)
    total_row = _total_demand_row(engine, target_row.material_number)
    old_total = total_row.get_value(period)
    new_value = float(target_row.get_value(period)) + 100.0

    with edit_route_app.app.app_context():
        response = apply_volume_change(
            sess,
            engine,
            LineType.DEMAND_FORECAST.value,
            target_row.material_number,
            period,
            new_value,
        )

    status, payload = _response_parts(response)
    assert status == 200
    assert payload["success"] is True
    updated_total_row = _total_demand_row(engine, target_row.material_number)
    assert updated_total_row.get_value(period) != pytest.approx(old_total)


def test_apply_volume_change_invalid_line_type_returns_403(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]

    with edit_route_app.app.app_context():
        response = apply_volume_change(
            sess,
            engine,
            LineType.TOTAL_DEMAND.value,
            "MAT-1",
            engine.data.periods[0],
            999.0,
        )

    status, payload = _response_parts(response)
    assert status == 403
    assert 'not editable' in payload["error"]


def test_apply_volume_change_missing_row_returns_404(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]

    with edit_route_app.app.app_context():
        response = apply_volume_change(
            sess,
            engine,
            LineType.DEMAND_FORECAST.value,
            "NO_SUCH_MATERIAL",
            engine.data.periods[0],
            999.0,
        )

    status, payload = _response_parts(response)
    assert status == 404
    assert "Row not found" in payload["error"]


def test_apply_volume_change_pushes_undo_entry(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]
    target_row, period = _editable_demand_cell(engine)
    old_value = float(target_row.get_value(period))
    new_value = old_value + 100.0

    with edit_route_app.app.app_context():
        response = apply_volume_change(
            sess,
            engine,
            LineType.DEMAND_FORECAST.value,
            target_row.material_number,
            period,
            new_value,
            push_undo=True,
        )

    status, payload = _response_parts(response)
    assert status == 200
    assert payload["success"] is True
    assert len(sess["undo_stack"]) == 1
    assert sess["undo_stack"][0]["old_value"] == pytest.approx(old_value)
    assert sess["undo_stack"][0]["new_value"] == pytest.approx(new_value)


def test_apply_volume_change_skips_undo_when_push_undo_false(edit_route_app):
    sess = edit_route_app.make_session()
    engine = sess["engine"]
    target_row, period = _editable_demand_cell(engine)
    new_value = float(target_row.get_value(period)) + 100.0

    with edit_route_app.app.app_context():
        response = apply_volume_change(
            sess,
            engine,
            LineType.DEMAND_FORECAST.value,
            target_row.material_number,
            period,
            new_value,
            push_undo=False,
        )

    status, payload = _response_parts(response)
    assert status == 200
    assert payload["success"] is True
    assert sess["undo_stack"] == []
