from modules.models import LineType


def test_upload_creates_session_with_metadata(flask_test_app, golden_fixture_path):
    with golden_fixture_path.open("rb") as workbook:
        response = flask_test_app.client.post(
            "/api/upload",
            data={
                "file": (workbook, golden_fixture_path.name),
                "custom_name": "Workflow route test",
                "planning_month": "2025-12",
                "months_actuals": "11",
                "months_forecast": "12",
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()

    assert payload["success"] is True
    assert payload["session_id"] in flask_test_app.sessions
    assert payload["summary"]["materials"] > 0
    assert payload["summary"]["periods"] > 0
    assert payload["planning_month"] == "2025-12"
    assert payload["months_actuals"] == 11
    assert payload["months_forecast"] == 12

    session_id = payload["session_id"]
    sess = flask_test_app.sessions[session_id]
    assert flask_test_app.get_active_session_id() == session_id
    assert sess["custom_name"] == "Workflow route test"
    assert sess["engine"] is None
    assert sess["metadata"]["materials"] == payload["summary"]["materials"]
    assert sess["metadata"]["periods"] == payload["summary"]["periods"]
    assert flask_test_app.save_calls


def test_calculate_triggers_pipeline_on_active_session(flask_test_app, golden_fixture_path):
    session_id = "workflow-calculate-session"
    flask_test_app.sessions[session_id] = {
        "id": session_id,
        "file_path": str(golden_fixture_path),
        "filename": golden_fixture_path.name,
        "custom_name": "Workflow calculate test",
        "metadata": {"planning_month": "2025-12"},
        "pending_edits": {},
        "value_aux_overrides": {},
        "machine_overrides": {},
        "undo_stack": [],
        "redo_stack": [],
    }
    flask_test_app.set_active_session_id(session_id)

    response = flask_test_app.client.post(
        "/api/calculate",
        json={
            "planning_month": "2025-12",
            "months_actuals": 11,
            "months_forecast": 12,
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()

    assert payload["success"] is True
    assert payload["summary"]["total_rows"] > 0
    assert payload["summary"]["line_types"][LineType.DEMAND_FORECAST.value] > 0
    assert payload["parameters"] == {
        "planning_month": "2025-12",
        "months_actuals": 11,
        "months_forecast": 12,
    }

    sess = flask_test_app.sessions[session_id]
    assert sess["engine"] is not None
    assert sess["engine"].results
    assert sess["parameters"] == payload["parameters"]
    assert sess["metadata"]["planning_month"] == "2025-12"
    assert sess["reset_baseline"] == {"installed": True}
    assert flask_test_app.save_calls
