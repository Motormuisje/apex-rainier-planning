from types import SimpleNamespace

import pytest
from flask import Flask, jsonify

from modules.license_manager import LicenseStatus
from ui.routes.license import create_license_blueprint


pytestmark = pytest.mark.no_fixture


@pytest.fixture
def license_route_app():
    class LicenseManagerDouble:
        def __init__(self):
            self.status = LicenseStatus.OK
            self.info = {"days_left": 10}
            self.activate_result = True
            self.activate_calls = 0

        def check(self):
            return self.status, self.info

        def activate(self):
            self.activate_calls += 1
            if self.activate_result:
                self.status = LicenseStatus.OK
                self.info = {"days_left": 14}
            return self.activate_result

    manager = LicenseManagerDouble()
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_license_blueprint(manager))

    @flask_app.route("/api/protected")
    def protected_api():
        return jsonify({"ok": True})

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        manager=manager,
    )


def test_license_status_returns_manager_status_and_info(license_route_app):
    response = license_route_app.client.get("/api/license/status")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": LicenseStatus.OK,
        "info": {"days_left": 10},
    }


def test_license_activate_success_returns_updated_info(license_route_app):
    license_route_app.manager.status = LicenseStatus.NOT_ACTIVATED
    license_route_app.manager.info = None

    response = license_route_app.client.post("/api/license/activate")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["info"] == {"days_left": 14}
    assert license_route_app.manager.activate_calls == 1


def test_license_activate_rejects_expired_trial(license_route_app):
    license_route_app.manager.status = LicenseStatus.EXPIRED
    license_route_app.manager.info = {"days_over": 1}

    response = license_route_app.client.post("/api/license/activate")

    assert response.status_code == 403
    payload = response.get_json()
    assert payload["success"] is False
    assert "expired" in payload["error"]
    assert license_route_app.manager.activate_calls == 0


def test_license_activate_rejects_tampered_record(license_route_app):
    license_route_app.manager.status = LicenseStatus.TAMPERED
    license_route_app.manager.info = None

    response = license_route_app.client.post("/api/license/activate")

    assert response.status_code == 403
    payload = response.get_json()
    assert payload["success"] is False
    assert "corrupt" in payload["error"]
    assert license_route_app.manager.activate_calls == 0


def test_protected_api_allows_valid_license(license_route_app):
    response = license_route_app.client.get("/api/protected")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_protected_api_requires_activation(license_route_app):
    license_route_app.manager.status = LicenseStatus.NOT_ACTIVATED
    license_route_app.manager.info = None

    response = license_route_app.client.get("/api/protected")

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "license_required",
        "message": "Trial not yet activated.",
    }


def test_protected_api_rejects_expired_license(license_route_app):
    license_route_app.manager.status = LicenseStatus.EXPIRED
    license_route_app.manager.info = {"days_over": 1}

    response = license_route_app.client.get("/api/protected")

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "license_expired",
        "message": "Trial period has ended.",
    }
