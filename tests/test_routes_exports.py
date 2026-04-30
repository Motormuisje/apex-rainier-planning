from types import SimpleNamespace

import pandas as pd
import pytest
from flask import Flask

import ui.routes.exports as exports_module
from ui.routes.exports import create_exports_blueprint


pytestmark = pytest.mark.no_fixture


class FakeMoMEngine:
    def to_dataframe(self):
        return pd.DataFrame([
            {
                "Line type": "04. Inventory",
                "Material number": "MAT-1",
                "Material name": "Material 1",
                "Product type": "Bulk Product",
                "2025-12": 10,
                "2026-01": 12,
                "2026-02": 9,
            },
            {
                "Line type": "04. Inventory",
                "Material number": "MAT-2",
                "Material name": "Material 2",
                "Product type": "Raw Material",
                "2025-12": 5,
                "2026-01": 7,
                "2026-02": 8,
            },
        ])


class FakeCycleManager:
    def __init__(self, previous_df=None):
        self.previous_df = previous_df

    def has_previous_cycle(self):
        return self.previous_df is not None

    def load_previous_cycle(self):
        return self.previous_df


class FakeExportEngine(FakeMoMEngine):
    def __init__(self):
        self.data = SimpleNamespace(config=SimpleNamespace(site="NLX1", initial_date="2025-12-01"))
        self.results = {}
        self.value_results = {}
        self.excel_calls = []

    def to_excel_with_values(self, path, inventory_quality_engine=None, previous_cycle_df=None):
        self.excel_calls.append({
            "path": path,
            "inventory_quality_engine": inventory_quality_engine,
            "previous_cycle_df": previous_cycle_df,
        })
        with open(path, "wb") as handle:
            handle.write(b"fake planning workbook")


@pytest.fixture
def exports_route_app(tmp_path):
    active = {"engine": None}
    state = {
        "cycle_manager": FakeCycleManager(),
        "highlights": [],
    }

    def get_active():
        return {"id": "exports-session"}, active["engine"]

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_exports_blueprint(
        get_active,
        lambda: tmp_path / "exports",
        lambda: state["cycle_manager"],
        lambda path, engine: state["highlights"].append((path, engine)),
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        active=active,
        state=state,
        export_dir=tmp_path / "exports",
    )


def test_mom_returns_unavailable_without_engine(exports_route_app):
    response = exports_route_app.client.get("/api/mom")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["available"] is False
    assert "Run calculations first" in payload["message"]


def test_mom_returns_sequential_comparison_from_dataframe(exports_route_app):
    exports_route_app.active["engine"] = FakeMoMEngine()

    response = exports_route_app.client.get("/api/mom?num_months=2")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["available"] is True
    assert payload["periods"] == ["2025-12", "2026-01", "2026-02"]
    assert payload["material_count"] == 2
    assert payload["num_transitions"] == 2
    assert set(payload["scatter"]) == {"materials", "start", "end", "colors"}
    assert len(payload["summary"]) == 2
    assert len(payload["transitions"]) == 2


def test_export_requires_engine(exports_route_app):
    response = exports_route_app.client.get("/api/export")

    assert response.status_code == 400
    assert response.get_json() == {"error": "No calculations run"}


def test_export_writes_workbook_and_applies_highlights(exports_route_app):
    previous_df = pd.DataFrame([{"Line type": "04. Inventory", "2025-12": 1}])
    engine = FakeExportEngine()
    exports_route_app.active["engine"] = engine
    exports_route_app.state["cycle_manager"] = FakeCycleManager(previous_df)

    response = exports_route_app.client.get("/api/export")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert len(engine.excel_calls) == 1
    assert engine.excel_calls[0]["previous_cycle_df"].equals(previous_df)
    export_path = engine.excel_calls[0]["path"]
    assert export_path.endswith(".xlsx")
    assert exports_route_app.state["highlights"] == [(export_path, engine)]


def test_export_db_requires_engine(exports_route_app):
    response = exports_route_app.client.post("/api/export_db")

    assert response.status_code == 400
    assert response.get_json() == {"error": "No calculations run"}


def test_export_db_returns_400_when_exporter_has_no_rows(exports_route_app, monkeypatch):
    class EmptyExporter:
        def __init__(self, planning_df, site, initial_date):
            self.planning_df = planning_df
            self.site = site
            self.initial_date = initial_date

        def export_to_dataframe(self):
            return pd.DataFrame()

    exports_route_app.active["engine"] = FakeExportEngine()
    monkeypatch.setattr(exports_module, "DatabaseExporter", EmptyExporter)

    response = exports_route_app.client.post("/api/export_db")

    assert response.status_code == 400
    assert response.get_json() == {"error": "No data to export (no matching line types)"}


def test_export_db_writes_sanitized_filename(exports_route_app, monkeypatch):
    class OneRowExporter:
        def __init__(self, planning_df, site, initial_date):
            self.planning_df = planning_df
            self.site = site
            self.initial_date = initial_date

        def export_to_dataframe(self):
            return pd.DataFrame([{
                "site": self.site,
                "initial_date": self.initial_date,
                "rows": len(self.planning_df),
            }])

    exports_route_app.active["engine"] = FakeExportEngine()
    monkeypatch.setattr(exports_module, "DatabaseExporter", OneRowExporter)

    response = exports_route_app.client.post(
        "/api/export_db",
        json={"filename": "DB Export?!"},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.headers["Content-Disposition"] == "attachment; filename=\"DB Export.xlsx\""
    assert (exports_route_app.export_dir / "DB Export.xlsx").exists()
