import io
from datetime import date as _date
from types import SimpleNamespace

import modules.data_loader as data_loader_module
import pytest
from flask import Flask

from modules.models import LineType
import ui.routes.workflow as workflow_module
from ui.routes.workflow import (
    _missing_required_loader_data,
    _session_payload,
    _upload_planning_params,
    _upload_summary,
    create_workflow_blueprint,
)


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


# ---------------------------------------------------------------------------
# Fixture: multi-file upload (no golden fixture needed)
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_multi_app(tmp_path):
    sessions = {}
    active = {"session_id": None}
    save_calls = []
    upload_path = tmp_path / "uploads"
    master_file = tmp_path / "master.xlsm"
    master_file.write_bytes(b"fake master")
    global_config = {"master_file": str(master_file)}

    def set_active_session_id(session_id):
        active["session_id"] = session_id

    def get_active():
        session_id = active["session_id"]
        sess = sessions.get(session_id) if session_id else None
        return sess, (sess.get("engine") if sess else None)

    class _Noop:
        def has_previous_cycle(self):
            return True

        def save_current_as_previous(self, *a, **kw):
            pass  # no-op; multi-upload path never calls cycle_manager

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(
        create_workflow_blueprint(
            sessions,
            set_active_session_id,
            get_active,
            lambda: upload_path,
            global_config,
            lambda exc, ctx: {"error": str(exc), "context": ctx},
            lambda: {},
            lambda: _Noop(),
            lambda sess, engine: None,
            lambda sess, engine: None,
            lambda: save_calls.append(True),
            lambda: flask_app.app_context(),
        )
    )

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        sessions=sessions,
        active=active,
        save_calls=save_calls,
        master_file=master_file,
    )


# ---------------------------------------------------------------------------
# Fixture: calculate route with mock PlanningEngine (no golden fixture needed)
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_calculate_app(tmp_path, monkeypatch):
    sessions = {}
    active = {"session_id": None}
    save_calls = []
    cycle_calls = []
    has_previous = {"value": True}

    class _MockEngine:
        def __init__(self, *args, **kwargs):
            self.data = SimpleNamespace(
                valuation_params=None,
                purchased_and_produced={},
                machines={},
            )
            self.results = {}
            self.value_results = {}

        def run(self):
            pass  # mock; no engine computation needed

        def get_summary(self):
            return {"total_rows": 5, "line_types": {}}

        def to_dataframe(self):
            return []

    class _MockCycleManager:
        def has_previous_cycle(self):
            return has_previous["value"]

        def save_current_as_previous(self, df, planning_month=None):
            cycle_calls.append(("saved", planning_month))

    monkeypatch.setattr(workflow_module, "PlanningEngine", _MockEngine)

    def set_active_session_id(session_id):
        active["session_id"] = session_id

    def get_active():
        session_id = active["session_id"]
        sess = sessions.get(session_id) if session_id else None
        engine = sess.get("engine") if sess else None
        return sess, engine

    upload_path = tmp_path / "uploads"
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(
        create_workflow_blueprint(
            sessions,
            set_active_session_id,
            get_active,
            lambda: upload_path,
            {},
            lambda exc, ctx: {"error": str(exc)},
            lambda: {},
            lambda: _MockCycleManager(),
            lambda sess, engine: sess.setdefault("reset_baseline", {"machines": {}}),
            lambda sess, engine: None,
            lambda: save_calls.append(True),
            lambda: flask_app.app_context(),
        )
    )

    def add_session(file_path="workbook.xlsm", engine=None):
        sid = "calc-session"
        sessions[sid] = {
            "id": sid,
            "file_path": str(file_path),
            "filename": "workbook.xlsm",
            "metadata": {"planning_month": "2025-12"},
            "pending_edits": {},
            "value_aux_overrides": {},
            "machine_overrides": {},
            "engine": engine,
        }
        set_active_session_id(sid)
        return sid

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        sessions=sessions,
        active=active,
        save_calls=save_calls,
        cycle_calls=cycle_calls,
        has_previous=has_previous,
        add_session=add_session,
    )


# ---------------------------------------------------------------------------
# Index route
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_index_route_returns_200(workflow_error_app, monkeypatch):
    monkeypatch.setattr(workflow_module, "render_template", lambda tmpl, **kw: tmpl)
    response = workflow_error_app.client.get("/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Upload form-parsing edge cases (lines 43-44, 47-48)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_upload_invalid_actuals_and_forecast_falls_back_to_none(workflow_error_app):
    # Sends non-numeric values; code catches ValueError and falls back to None.
    # Still reaches "No file provided" because no file is attached.
    response = workflow_error_app.client.post(
        "/api/upload",
        data={"months_actuals": "abc", "months_forecast": "xyz"},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "No file provided"}


# ---------------------------------------------------------------------------
# Single-file upload error paths (lines 349, 366-369)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_upload_single_empty_filename_returns_error(workflow_error_app):
    response = workflow_error_app.client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b""), "")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "No file selected"}


@pytest.mark.no_fixture
def test_upload_single_dataloader_exception_returns_400(workflow_error_app, monkeypatch):
    class _BrokenLoader:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("corrupt workbook")

        def load_all(self):
            pass  # never reached; __init__ always raises

    monkeypatch.setattr(data_loader_module, "DataLoader", _BrokenLoader)

    response = workflow_error_app.client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"data"), "workbook.xlsm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "corrupt workbook" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Calculate route (lines 84, 94, 109-118, 142-147, 162-165)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_calculate_returns_400_when_no_active_session(workflow_calculate_app):
    response = workflow_calculate_app.client.post("/api/calculate", json={})
    assert response.status_code == 400
    assert response.get_json() == {"error": "No file uploaded"}


@pytest.mark.no_fixture
def test_calculate_accepts_form_encoded_body(workflow_calculate_app, tmp_path):
    workflow_calculate_app.add_session(file_path=str(tmp_path / "workbook.xlsm"))
    response = workflow_calculate_app.client.post(
        "/api/calculate",
        data={"planning_month": "2025-12", "months_actuals": "6", "months_forecast": "12"},
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True


@pytest.mark.no_fixture
def test_calculate_saves_existing_engine_as_previous_cycle(workflow_calculate_app, tmp_path):
    existing_engine = SimpleNamespace(
        to_dataframe=lambda: [],
        data=SimpleNamespace(valuation_params=None, purchased_and_produced={}, machines={}),
        results={},
        value_results={},
    )
    workflow_calculate_app.add_session(
        file_path=str(tmp_path / "workbook.xlsm"),
        engine=existing_engine,
    )

    workflow_calculate_app.client.post(
        "/api/calculate", json={"planning_month": "2025-12"}
    )

    assert any(call[0] == "saved" for call in workflow_calculate_app.cycle_calls)


@pytest.mark.no_fixture
def test_calculate_bootstrap_snapshot_when_no_previous_cycle(workflow_calculate_app, tmp_path):
    workflow_calculate_app.has_previous["value"] = False
    workflow_calculate_app.add_session(file_path=str(tmp_path / "workbook.xlsm"))

    workflow_calculate_app.client.post(
        "/api/calculate", json={"planning_month": "2025-12"}
    )

    assert any(call[0] == "saved" for call in workflow_calculate_app.cycle_calls)


@pytest.mark.no_fixture
def test_calculate_returns_500_on_engine_exception(workflow_calculate_app, tmp_path, monkeypatch):
    class _BrokenEngine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("engine crash")

    monkeypatch.setattr(workflow_module, "PlanningEngine", _BrokenEngine)
    workflow_calculate_app.add_session(file_path=str(tmp_path / "workbook.xlsm"))

    response = workflow_calculate_app.client.post("/api/calculate", json={})
    assert response.status_code == 500
    payload = response.get_json()
    assert "engine crash" in payload["error"]
    assert "trace" in payload


# ---------------------------------------------------------------------------
# Direct helper tests — _missing_required_loader_data (line 428)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_missing_required_loader_data_returns_empty_when_all_present():
    loader = SimpleNamespace(
        materials=["m"],
        bom=["b"],
        routing=["r"],
        machines=["mc"],
        forecasts=["f"],
        periods=["p"],
        config=object(),
    )
    assert _missing_required_loader_data(loader) == []


@pytest.mark.no_fixture
def test_missing_required_loader_data_flags_all_when_empty_and_config_none():
    loader = SimpleNamespace(
        materials=[],
        bom=[],
        routing=[],
        machines=[],
        forecasts=[],
        periods=[],
        config=None,
    )
    missing = _missing_required_loader_data(loader)
    assert set(missing) == {
        "materials",
        "bom",
        "routing",
        "machines",
        "forecasts",
        "periods",
        "config",
    }


@pytest.mark.no_fixture
def test_missing_required_loader_data_selective_missing():
    loader = SimpleNamespace(
        materials=["m"],
        bom=[],
        routing=["r"],
        machines=["mc"],
        forecasts=["f"],
        periods=["p"],
        config=object(),
    )
    assert _missing_required_loader_data(loader) == ["bom"]


# ---------------------------------------------------------------------------
# Direct helper tests — _upload_planning_params
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_upload_planning_params_uses_requested_values():
    loader = SimpleNamespace(
        config=SimpleNamespace(initial_date=None, forecast_months=6),
        forecast_actuals_months=3,
    )
    pm, actuals, forecast = _upload_planning_params(loader, "2025-06", 2, 4)
    assert (pm, actuals, forecast) == ("2025-06", 2, 4)


@pytest.mark.no_fixture
def test_upload_planning_params_falls_back_to_loader_values():
    loader = SimpleNamespace(
        config=SimpleNamespace(initial_date=_date(2025, 6, 1), forecast_months=9),
        forecast_actuals_months=7,
    )
    pm, actuals, forecast = _upload_planning_params(loader, "", None, None)
    assert pm == "2025-06"
    assert actuals == 7
    assert forecast == 9


@pytest.mark.no_fixture
def test_upload_planning_params_no_initial_date_gives_empty_planning_month():
    loader = SimpleNamespace(
        config=SimpleNamespace(initial_date=None, forecast_months=12),
        forecast_actuals_months=6,
    )
    pm, _, _ = _upload_planning_params(loader, "", None, None)
    assert pm == ""


# ---------------------------------------------------------------------------
# Direct helper tests — _upload_summary and _session_payload (line 202)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_upload_summary_returns_correct_counts():
    loader = SimpleNamespace(
        materials=["a", "b"],
        bom=["x"],
        machines=["m1", "m2", "m3"],
        periods=["2025-01", "2025-02"],
    )
    assert _upload_summary(loader) == {
        "materials": 2,
        "bom_items": 1,
        "machines": 3,
        "periods": 2,
    }


@pytest.mark.no_fixture
def test_session_payload_without_extract_files_omits_key():
    loader = SimpleNamespace(
        materials=["m"],
        bom=["b"],
        machines=["mc"],
        periods=["p"],
        config=SimpleNamespace(site="NLX1"),
    )
    payload = _session_payload("sid", "/path/wb.xlsm", "wb.xlsm", "Plan", loader, "2025-12")
    assert "extract_files" not in payload
    assert payload["metadata"]["site"] == "NLX1"
    assert payload["id"] == "sid"


@pytest.mark.no_fixture
def test_session_payload_with_extract_files_includes_key():
    loader = SimpleNamespace(
        materials=["m"],
        bom=["b"],
        machines=["mc"],
        periods=["p"],
        config=SimpleNamespace(site=""),
    )
    extract = {"bom": "/path/bom.xlsx"}
    payload = _session_payload(
        "sid-2", "/path/base.xlsm", "bom.xlsx", None, loader, "2025-12", extract_files=extract
    )
    assert payload["extract_files"] == extract


# ---------------------------------------------------------------------------
# Multi-file upload tests (lines 220-330)
# ---------------------------------------------------------------------------


def _make_good_loader():
    class _GoodLoader:
        def __init__(self, *args, **kwargs):
            self.materials = ["m1"]
            self.bom = ["b1"]
            self.routing = ["r1"]
            self.machines = ["mc1"]
            self.forecasts = ["f1"]
            self.periods = ["2025-12"]
            self.config = SimpleNamespace(
                site="NLX1", initial_date=None, forecast_months=12
            )
            self.forecast_actuals_months = 6

        def load_all(self):
            pass  # data already populated in __init__; nothing to load

    return _GoodLoader


@pytest.mark.no_fixture
def test_multi_upload_uses_global_config_master_file(workflow_multi_app, monkeypatch):
    monkeypatch.setattr(data_loader_module, "DataLoader", _make_good_loader())

    response = workflow_multi_app.client.post(
        "/api/upload",
        data={
            "bom_file": (io.BytesIO(b"bom"), "bom.xlsx"),
            "routing_file": (io.BytesIO(b"routing"), "routing.xlsx"),
            "stock_file": (io.BytesIO(b"stock"), "stock.xlsx"),
            "forecast_file": (io.BytesIO(b"forecast"), "forecast.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert workflow_multi_app.save_calls


@pytest.mark.no_fixture
def test_multi_upload_with_base_file_saves_and_uses_it(workflow_multi_app, monkeypatch):
    monkeypatch.setattr(data_loader_module, "DataLoader", _make_good_loader())

    response = workflow_multi_app.client.post(
        "/api/upload",
        data={
            "base_file": (io.BytesIO(b"base"), "base.xlsm"),
            "bom_file": (io.BytesIO(b"bom"), "bom.xlsx"),
            "routing_file": (io.BytesIO(b"routing"), "routing.xlsx"),
            "stock_file": (io.BytesIO(b"stock"), "stock.xlsx"),
            "forecast_file": (io.BytesIO(b"forecast"), "forecast.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True


@pytest.mark.no_fixture
def test_multi_upload_rejects_wrong_filename_keyword(workflow_multi_app):
    response = workflow_multi_app.client.post(
        "/api/upload",
        data={
            "bom_file": (io.BytesIO(b"bom"), "wrong_name.xlsx"),
            "routing_file": (io.BytesIO(b"routing"), "routing.xlsx"),
            "stock_file": (io.BytesIO(b"stock"), "stock.xlsx"),
            "forecast_file": (io.BytesIO(b"forecast"), "forecast.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "Wrong file for BOM field" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_multi_upload_rejects_empty_extract_filename(workflow_multi_app):
    response = workflow_multi_app.client.post(
        "/api/upload",
        data={
            "bom_file": (io.BytesIO(b""), ""),
            "routing_file": (io.BytesIO(b"routing"), "routing.xlsx"),
            "stock_file": (io.BytesIO(b"stock"), "stock.xlsx"),
            "forecast_file": (io.BytesIO(b"forecast"), "forecast.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "No file selected for bom_file" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_multi_upload_dataloader_exception_returns_400(workflow_multi_app, monkeypatch):
    class _BrokenLoader:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("load failure")

        def load_all(self):
            pass  # never reached; __init__ always raises

    monkeypatch.setattr(data_loader_module, "DataLoader", _BrokenLoader)

    response = workflow_multi_app.client.post(
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
    assert "load failure" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_multi_upload_missing_loader_data_returns_400(workflow_multi_app, monkeypatch):
    class _EmptyLoader:
        def __init__(self, *args, **kwargs):
            self.materials = []
            self.bom = []
            self.routing = []
            self.machines = []
            self.forecasts = []
            self.periods = []
            self.config = SimpleNamespace(site="", initial_date=None, forecast_months=12)
            self.forecast_actuals_months = 6

        def load_all(self):
            pass  # stub; test exercises the missing-data validation path

    monkeypatch.setattr(data_loader_module, "DataLoader", _EmptyLoader)

    response = workflow_multi_app.client.post(
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
    assert "missing required data" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Calculate cycle-manager snapshot errors are logged but non-fatal (116-118, 145-147)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_calculate_prerun_cycle_snapshot_error_is_logged_not_fatal(
    workflow_calculate_app, tmp_path
):
    def _raise_df():
        raise RuntimeError("df prerun fail")

    existing_engine = SimpleNamespace(
        to_dataframe=_raise_df,
        data=SimpleNamespace(valuation_params=None, purchased_and_produced={}, machines={}),
        results={},
        value_results={},
    )
    workflow_calculate_app.add_session(
        file_path=str(tmp_path / "workbook.xlsm"), engine=existing_engine
    )

    response = workflow_calculate_app.client.post(
        "/api/calculate", json={"planning_month": "2025-12"}
    )

    # Error is written to log_buf (sys.stdout is redirected), not fatal
    assert response.status_code == 200
    assert "pre-run snapshot ERROR" in response.get_json()["log"]


@pytest.mark.no_fixture
def test_calculate_bootstrap_cycle_snapshot_error_is_logged_not_fatal(
    workflow_calculate_app, tmp_path, monkeypatch
):
    workflow_calculate_app.has_previous["value"] = False

    class _RaisingEngine:
        def __init__(self, *args, **kwargs):
            self.data = SimpleNamespace(
                valuation_params=None, purchased_and_produced={}, machines={}
            )
            self.results = {}
            self.value_results = {}

        def run(self):
            pass  # mock; no engine computation needed

        def get_summary(self):
            return {"total_rows": 0, "line_types": {}}

        def to_dataframe(self):
            raise RuntimeError("df bootstrap fail")

    monkeypatch.setattr(workflow_module, "PlanningEngine", _RaisingEngine)
    workflow_calculate_app.add_session(file_path=str(tmp_path / "workbook.xlsm"))

    response = workflow_calculate_app.client.post(
        "/api/calculate", json={"planning_month": "2025-12"}
    )

    # Error is written to log_buf (sys.stdout is redirected), not fatal
    assert response.status_code == 200
    assert "bootstrap snapshot ERROR" in response.get_json()["log"]


# ---------------------------------------------------------------------------
# File-save exception paths (lines 224-225, 267-268, 354-355)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_upload_single_file_save_exception_returns_400(workflow_error_app, monkeypatch):
    from werkzeug.datastructures import FileStorage

    def _raise_save(self, dst, buffer_size=16384):
        raise OSError("disk full")

    monkeypatch.setattr(FileStorage, "save", _raise_save)

    response = workflow_error_app.client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"data"), "workbook.xlsm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "disk full" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_multi_upload_extract_file_save_exception_returns_400(
    workflow_multi_app, monkeypatch
):
    from werkzeug.datastructures import FileStorage

    def _raise_save(self, dst, buffer_size=16384):
        raise OSError("disk full")

    monkeypatch.setattr(FileStorage, "save", _raise_save)

    response = workflow_multi_app.client.post(
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
    assert "disk full" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_multi_upload_base_file_save_exception_returns_400(
    workflow_multi_app, monkeypatch
):
    from werkzeug.datastructures import FileStorage

    def _raise_save(self, dst, buffer_size=16384):
        raise OSError("disk full base")

    monkeypatch.setattr(FileStorage, "save", _raise_save)

    response = workflow_multi_app.client.post(
        "/api/upload",
        data={
            "base_file": (io.BytesIO(b"base"), "base.xlsm"),
            "bom_file": (io.BytesIO(b"bom"), "bom.xlsx"),
            "routing_file": (io.BytesIO(b"routing"), "routing.xlsx"),
            "stock_file": (io.BytesIO(b"stock"), "stock.xlsx"),
            "forecast_file": (io.BytesIO(b"forecast"), "forecast.xlsx"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "disk full base" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Session-creation exception paths (lines 327-330, 407-410)
# ---------------------------------------------------------------------------


def _make_failing_save_app(tmp_path):
    """A workflow app whose save_sessions_to_disk raises, to exercise the
    outer except block in both single and multi upload paths."""
    sessions = {}
    active = {"session_id": None}
    master_file = tmp_path / "master.xlsm"
    master_file.write_bytes(b"fake")
    global_config = {"master_file": str(master_file)}

    def set_active_session_id(session_id):
        active["session_id"] = session_id

    def get_active():
        session_id = active["session_id"]
        sess = sessions.get(session_id) if session_id else None
        return sess, (sess.get("engine") if sess else None)

    def _failing_save():
        raise RuntimeError("save failed")

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(
        create_workflow_blueprint(
            sessions,
            set_active_session_id,
            get_active,
            lambda: tmp_path / "uploads",
            global_config,
            lambda exc, ctx: {"error": str(exc), "context": ctx},
            lambda: {},
            lambda: SimpleNamespace(
                has_previous_cycle=lambda: True,
                save_current_as_previous=lambda *a, **kw: None,
            ),
            lambda sess, engine: None,
            lambda sess, engine: None,
            _failing_save,
            lambda: flask_app.app_context(),
        )
    )
    return flask_app.test_client()


@pytest.mark.no_fixture
def test_upload_single_session_creation_exception_returns_400(tmp_path, monkeypatch):
    monkeypatch.setattr(data_loader_module, "DataLoader", _make_good_loader())
    client = _make_failing_save_app(tmp_path)

    response = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"data"), "workbook.xlsm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "save failed" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_upload_multi_session_creation_exception_returns_400(tmp_path, monkeypatch):
    monkeypatch.setattr(data_loader_module, "DataLoader", _make_good_loader())
    client = _make_failing_save_app(tmp_path)

    response = client.post(
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
    assert "save failed" in response.get_json()["error"]
