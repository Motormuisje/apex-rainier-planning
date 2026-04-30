import io
from types import SimpleNamespace

import pytest
from flask import Flask

from modules.models import LineType
from ui.routes.workflow import create_workflow_blueprint


@pytest.fixture
def workflow_error_app(tmp_path):
    sessions = {}
    active = {"session_id": None}
    save_calls = []
    upload_path = tmp_path / "uploads"

    class NoopCycleManager:
        def has_previous_cycle(self):
            return True

    def set_active_session_id(session_id):
        active["session_id"] = session_id

    def get_active():
        session_id = active["session_id"]
        sess = sessions.get(session_id) if session_id else None
        return sess, sess.get("engine") if sess else None

    def classify_upload_exception(exc, context):
        return {"error": str(exc), "context": context}

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_workflow_blueprint(
        sessions,
        set_active_session_id,
        get_active,
        lambda: upload_path,
        {},
        classify_upload_exception,
        lambda: {},
        lambda: NoopCycleManager(),
        lambda sess, engine: None,
        lambda sess, engine: None,
        lambda: save_calls.append(list(sessions.keys())),
        lambda: flask_app.app_context(),
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        sessions=sessions,
        get_active_session_id=lambda: active["session_id"],
        save_calls=save_calls,
    )


@pytest.mark.no_fixture
def test_upload_requires_file(workflow_error_app):
    response = workflow_error_app.client.post("/api/upload", data={})

    assert response.status_code == 400
    assert response.get_json() == {"error": "No file provided"}
    assert workflow_error_app.sessions == {}
    assert workflow_error_app.get_active_session_id() is None
    assert workflow_error_app.save_calls == []


@pytest.mark.no_fixture
def test_upload_rejects_missing_required_loader_data(workflow_error_app, monkeypatch):
    class EmptyLoader:
        def __init__(self, *args, **kwargs):
            self.materials = []
            self.bom = []
            self.routing = []
            self.machines = []
            self.forecasts = []
            self.periods = []
            self.config = SimpleNamespace(initial_date=None, forecast_months=12)

        def load_all(self):
            return None

    import modules.data_loader as data_loader_module

    monkeypatch.setattr(data_loader_module, "DataLoader", EmptyLoader)

    response = workflow_error_app.client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"not a real workbook"), "empty.xlsm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"].startswith("The uploaded file is missing required data")
    assert payload["missing"] == [
        "materials",
        "bom",
        "routing",
        "machines",
        "forecasts",
        "periods",
    ]
    assert workflow_error_app.sessions == {}
    assert workflow_error_app.get_active_session_id() is None
    assert workflow_error_app.save_calls == []


@pytest.mark.no_fixture
def test_multi_file_upload_requires_configured_master_file(workflow_error_app):
    response = workflow_error_app.client.post(
        "/api/upload",
        data={
            "bom_file": (io.BytesIO(b"bom"), "bom.xlsx"),
            "routing_file": (io.BytesIO(b"routing"), "routing.xlsx"),
            "stock_file": (io.BytesIO(b"stock"), "stock.xlsx"),
            "forecast_file": (io.BytesIO(b"forecast"), "forecast.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "No master data file configured. Upload a base file in the Config tab first."
    }
    assert workflow_error_app.sessions == {}
    assert workflow_error_app.get_active_session_id() is None
    assert workflow_error_app.save_calls == []


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
    baseline = sess["reset_baseline"]
    assert set(baseline) >= {
        "results",
        "value_results",
        "valuation_params",
        "purchased_and_produced",
        "machines",
    }
    assert baseline["results"]
    assert baseline["machines"]
    assert flask_test_app.save_calls
