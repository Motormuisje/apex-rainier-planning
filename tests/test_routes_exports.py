from types import SimpleNamespace

import pandas as pd
import pytest
from flask import Flask

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


@pytest.fixture
def exports_route_app(tmp_path):
    active = {"engine": None}

    def crash_callback(*args, **kwargs):
        raise RuntimeError("unexpected callback called in exports route test")

    def get_active():
        return {"id": "exports-session"}, active["engine"]

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_exports_blueprint(
        get_active,
        lambda: tmp_path / "exports",
        crash_callback,
        crash_callback,
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        active=active,
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


@pytest.mark.skip(reason="GET /api/export writes and returns a binary .xlsx file.")
def test_export_skipped_by_design(exports_route_app):
    exports_route_app.client.get("/api/export")


@pytest.mark.skip(reason="POST /api/export_db writes and returns a binary .xlsx file.")
def test_export_db_skipped_by_design(exports_route_app):
    exports_route_app.client.post("/api/export_db")
