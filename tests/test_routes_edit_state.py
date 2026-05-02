"""Tests for ui/routes/edit_state.py — the three thin persistence routes."""

from types import SimpleNamespace

import pytest
from flask import Flask

from ui.routes.edit_state import create_edit_state_blueprint


@pytest.fixture
def edit_state_app():
    sessions = {}
    save_calls = []

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(
        create_edit_state_blueprint(
            sessions,
            {"01. Demand forecast", "02. Total demand"},
            lambda: save_calls.append(True),
        )
    )

    def add_session(session_id="test-session"):
        sessions[session_id] = {"id": session_id, "pending_edits": {}}
        return session_id

    return SimpleNamespace(
        client=flask_app.test_client(),
        sessions=sessions,
        save_calls=save_calls,
        add_session=add_session,
    )


# ---------------------------------------------------------------------------
# GET /api/editable_line_types (line 17)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_get_editable_line_types_returns_sorted_list(edit_state_app):
    response = edit_state_app.client.get("/api/editable_line_types")

    assert response.status_code == 200
    assert response.get_json()["editable"] == sorted(
        ["01. Demand forecast", "02. Total demand"]
    )


# ---------------------------------------------------------------------------
# POST /api/sessions/edits/persist — error paths (lines 25, 28)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_persist_edit_unknown_session_returns_404(edit_state_app):
    response = edit_state_app.client.post(
        "/api/sessions/edits/persist",
        json={
            "session_id": "no-such-session",
            "key": "01. Demand forecast||MAT-1||||2025-12",
            "original": 0,
            "new_value": 1,
        },
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


@pytest.mark.no_fixture
def test_persist_edit_missing_session_id_returns_404(edit_state_app):
    response = edit_state_app.client.post(
        "/api/sessions/edits/persist",
        json={"session_id": "", "key": "some||key||x||2025-12", "original": 0, "new_value": 1},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


@pytest.mark.no_fixture
def test_persist_edit_empty_key_returns_400(edit_state_app):
    edit_state_app.add_session("test-session")

    response = edit_state_app.client.post(
        "/api/sessions/edits/persist",
        json={"session_id": "test-session", "key": "", "original": 0, "new_value": 1},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Cell key required"


# ---------------------------------------------------------------------------
# POST /api/sessions/edits/persist — happy paths (lines 29-37)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_persist_edit_stores_edit_when_value_differs(edit_state_app):
    sid = edit_state_app.add_session()

    response = edit_state_app.client.post(
        "/api/sessions/edits/persist",
        json={
            "session_id": sid,
            "key": "01. Demand forecast||MAT-1||||2025-12",
            "original": 10.0,
            "new_value": 15.5,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    pending = edit_state_app.sessions[sid]["pending_edits"]
    assert pending["01. Demand forecast||MAT-1||||2025-12"] == {
        "original": pytest.approx(10.0),
        "new_value": pytest.approx(15.5),
    }
    assert edit_state_app.save_calls


@pytest.mark.no_fixture
def test_persist_edit_removes_edit_when_value_equals_original(edit_state_app):
    sid = edit_state_app.add_session()
    key = "01. Demand forecast||MAT-1||||2025-12"
    edit_state_app.sessions[sid]["pending_edits"][key] = {
        "original": 10.0,
        "new_value": 15.5,
    }

    response = edit_state_app.client.post(
        "/api/sessions/edits/persist",
        json={"session_id": sid, "key": key, "original": 10.0, "new_value": 10.0},
    )

    assert response.status_code == 200
    assert key not in edit_state_app.sessions[sid]["pending_edits"]


# ---------------------------------------------------------------------------
# POST /api/sessions/edits/sync (lines 42-55)
# ---------------------------------------------------------------------------


@pytest.mark.no_fixture
def test_sync_edits_unknown_session_returns_404(edit_state_app):
    response = edit_state_app.client.post(
        "/api/sessions/edits/sync",
        json={"session_id": "no-such-session", "edits": {}},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


@pytest.mark.no_fixture
def test_sync_edits_non_dict_edits_returns_400(edit_state_app):
    edit_state_app.add_session()

    response = edit_state_app.client.post(
        "/api/sessions/edits/sync",
        json={"session_id": "test-session", "edits": "not-a-dict"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "edits must be an object"


@pytest.mark.no_fixture
def test_sync_edits_replaces_pending_edits_and_saves(edit_state_app):
    sid = edit_state_app.add_session()
    edit_state_app.sessions[sid]["pending_edits"] = {
        "old-key": {"original": 1.0, "new_value": 2.0}
    }

    response = edit_state_app.client.post(
        "/api/sessions/edits/sync",
        json={
            "session_id": sid,
            "edits": {
                "new-key||a||b||2025-12": {"original": 10.0, "new_value": 15.0},
            },
        },
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    pending = edit_state_app.sessions[sid]["pending_edits"]
    assert "old-key" not in pending
    assert pending["new-key||a||b||2025-12"] == {
        "original": pytest.approx(10.0),
        "new_value": pytest.approx(15.0),
    }
    assert edit_state_app.save_calls


@pytest.mark.no_fixture
def test_sync_edits_skips_non_dict_values(edit_state_app):
    sid = edit_state_app.add_session()

    response = edit_state_app.client.post(
        "/api/sessions/edits/sync",
        json={
            "session_id": sid,
            "edits": {
                "valid-key": {"original": 1.0, "new_value": 2.0},
                "invalid-key": "not-a-dict",
            },
        },
    )

    assert response.status_code == 200
    pending = edit_state_app.sessions[sid]["pending_edits"]
    assert "valid-key" in pending
    assert "invalid-key" not in pending


@pytest.mark.no_fixture
def test_sync_edits_coerces_floats(edit_state_app):
    sid = edit_state_app.add_session()

    response = edit_state_app.client.post(
        "/api/sessions/edits/sync",
        json={
            "session_id": sid,
            "edits": {
                "k||a||b||2025-12": {"original": "5", "new_value": "7.5"},
            },
        },
    )

    assert response.status_code == 200
    pending = edit_state_app.sessions[sid]["pending_edits"]
    entry = pending["k||a||b||2025-12"]
    assert entry["original"] == pytest.approx(5.0)
    assert entry["new_value"] == pytest.approx(7.5)
