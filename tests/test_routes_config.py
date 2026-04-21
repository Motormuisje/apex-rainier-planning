from types import SimpleNamespace

import pytest
from flask import Flask

from ui.parsers import parse_purchased_and_produced, valuation_params_from_config
from ui.routes.config import create_config_blueprint


pytestmark = pytest.mark.no_fixture


@pytest.fixture
def config_route_app(tmp_path):
    global_config = {
        "master_filename": "master.xlsm",
        "master_uploaded_at": "2026-04-21T12:00:00",
        "site": "NLX1",
        "forecast_months": 12,
        "unlimited_machines": "PBA99",
        "purchased_and_produced": "MAT-1:0.5",
        "valuation_params": {"1": 10.0},
        "file_defaults": {
            "site": "DEF",
            "forecast_months": 6,
            "unlimited_machines": "PBA01",
            "purchased_and_produced": "MAT-2:1",
            "valuation_params": {"1": 1.0},
        },
    }
    active = {"sess": None, "engine": None}
    save_calls = []
    apply_calls = []

    defaults = {
        "uploads": str(tmp_path / "default_uploads"),
        "exports": str(tmp_path / "default_exports"),
        "sessions": str(tmp_path / "default_sessions"),
    }

    def crash_callback(*args, **kwargs):
        raise RuntimeError("unexpected callback called in config route test")

    def get_active():
        return active["sess"], active["engine"]

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_config_blueprint(
        lambda: defaults,
        global_config,
        lambda: save_calls.append(dict(global_config)),
        lambda uploads, exports, sessions: apply_calls.append((uploads, exports, sessions)),
        lambda: tmp_path / "uploads",
        get_active,
        parse_purchased_and_produced,
        valuation_params_from_config,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        global_config=global_config,
        active=active,
        defaults=defaults,
        save_calls=save_calls,
        apply_calls=apply_calls,
        tmp_path=tmp_path,
    )


def test_get_folder_config_returns_saved_values_and_defaults(config_route_app):
    config_route_app.global_config["folders"] = {
        "uploads": "C:/saved/uploads",
        "exports": "C:/saved/exports",
    }

    response = config_route_app.client.get("/api/config/folders")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["uploads"] == "C:/saved/uploads"
    assert payload["exports"] == "C:/saved/exports"
    assert payload["sessions"] == config_route_app.defaults["sessions"]
    assert payload["defaults"] == config_route_app.defaults


def test_save_folder_config_persists_paths_and_applies_them(config_route_app):
    uploads = config_route_app.tmp_path / "custom_uploads"
    exports = config_route_app.tmp_path / "custom_exports"
    sessions = config_route_app.tmp_path / "custom_sessions"

    response = config_route_app.client.post(
        "/api/config/folders",
        json={
            "uploads": str(uploads),
            "exports": str(exports),
            "sessions": str(sessions),
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.get_json() == {"success": True}
    assert config_route_app.global_config["folders"] == {
        "uploads": str(uploads),
        "exports": str(exports),
        "sessions": str(sessions),
    }
    assert uploads.is_dir()
    assert exports.is_dir()
    assert sessions.is_dir()
    assert config_route_app.save_calls
    assert config_route_app.apply_calls == [(uploads, exports, sessions)]


def test_save_folder_config_returns_400_for_uncreatable_path(config_route_app):
    not_a_dir = config_route_app.tmp_path / "not_a_dir"
    not_a_dir.write_text("already a file", encoding="utf-8")

    response = config_route_app.client.post(
        "/api/config/folders",
        json={"uploads": str(not_a_dir)},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["errors"]
    assert not config_route_app.save_calls


def test_get_global_config_returns_public_config_shape(config_route_app):
    master_file = config_route_app.tmp_path / "master.xlsm"
    master_file.write_text("placeholder", encoding="utf-8")
    config_route_app.global_config["master_file"] = str(master_file)

    response = config_route_app.client.get("/api/config")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["master_filename"] == "master.xlsm"
    assert payload["master_file_exists"] is True
    assert payload["site"] == "NLX1"
    assert payload["forecast_months"] == 12
    assert payload["file_defaults"]["forecast_months"] == 6


def test_save_config_settings_without_engine_updates_global_config(config_route_app):
    response = config_route_app.client.post(
        "/api/config/settings",
        json={
            "site": "NEW",
            "forecast_months": "18",
            "unlimited_machines": "PBA01, PBA02",
            "purchased_and_produced": "MAT-9:0.25",
            "valuation_params": {"1": "12.5", "2": None},
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.get_json() == {"success": True}
    assert config_route_app.global_config["site"] == "NEW"
    assert config_route_app.global_config["forecast_months"] == 18
    assert config_route_app.global_config["unlimited_machines"] == "PBA01, PBA02"
    assert config_route_app.global_config["purchased_and_produced"] == "MAT-9:0.25"
    assert config_route_app.global_config["valuation_params"] == {"1": 12.5}
    assert config_route_app.save_calls


def test_reset_vp_params_requires_active_session(config_route_app):
    response = config_route_app.client.post("/api/config/reset_vp_params")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No active session"


def test_reset_vp_params_requires_calculated_engine(config_route_app):
    config_route_app.active["sess"] = {"id": "config-session"}

    response = config_route_app.client.post("/api/config/reset_vp_params")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.skip(reason="POST /api/config/master-file requires a real .xlsm/.xlsx upload fixture.")
def test_upload_master_file_skipped_by_design(config_route_app):
    config_route_app.client.post("/api/config/master-file")
