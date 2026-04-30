import pytest
from types import SimpleNamespace


def _flatten_session_groups(payload):
    sessions = []
    for group_sessions in payload["groups"].values():
        sessions.extend(group_sessions)
    return sessions


def test_sessions_delete_removes_session_and_promotes_next_active(
    session_route_app,
    planning_engine_result,
):
    session_route_app.make_session(
        "session-a",
        engine=planning_engine_result,
        custom_name="Session A",
    )
    session_route_app.make_session(
        "session-b",
        engine=planning_engine_result,
        custom_name="Session B",
    )
    session_route_app.set_active_session_id("session-a")

    response = session_route_app.client.delete("/api/sessions/session-a")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["active_session_id"] == "session-b"
    assert "session-a" not in session_route_app.sessions
    assert "session-b" in session_route_app.sessions
    assert session_route_app.get_active_session_id() == "session-b"
    assert session_route_app.save_calls


def test_sessions_switch_updates_active_session_and_list_payload(
    session_route_app,
    planning_engine_result,
):
    session_route_app.make_session(
        "session-a",
        engine=planning_engine_result,
        custom_name="Session A",
        metadata={
            "materials": 10,
            "periods": 12,
            "site": "SITE-A",
            "planning_month": "2025-12",
        },
    )
    session_route_app.make_session(
        "session-b",
        engine=planning_engine_result,
        custom_name="Session B",
        metadata={
            "materials": 20,
            "periods": 12,
            "site": "SITE-B",
            "planning_month": "2026-01",
        },
        parameters={
            "planning_month": "2026-01",
            "months_actuals": 11,
            "months_forecast": 12,
        },
    )
    session_route_app.set_active_session_id("session-a")

    response = session_route_app.client.post(
        "/api/sessions/switch",
        json={"session_id": "session-b"},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["active_session_id"] == "session-b"
    assert payload["custom_name"] == "Session B"
    assert payload["metadata"]["site"] == "SITE-B"
    assert payload["parameters"]["planning_month"] == "2026-01"
    assert payload["calculated"] is True
    assert session_route_app.get_active_session_id() == "session-b"
    assert len(session_route_app.sync_calls) == 2
    assert session_route_app.sync_calls == [planning_engine_result, planning_engine_result]

    list_response = session_route_app.client.get("/api/sessions")
    assert list_response.status_code == 200
    list_payload = list_response.get_json()
    assert list_payload["active_session_id"] == "session-b"
    listed = {item["id"]: item for item in _flatten_session_groups(list_payload)}
    assert listed["session-a"]["active"] is False
    assert listed["session-b"]["active"] is True
    assert listed["session-b"]["metadata"]["site"] == "SITE-B"


def test_sessions_list_returns_all_sessions_with_metadata(
    session_route_app,
    planning_engine_result,
):
    session_route_app.make_session(
        "session-a",
        engine=planning_engine_result,
        custom_name="Session A",
        filename="a.xlsm",
        metadata={
            "materials": 10,
            "periods": 12,
            "site": "SITE-A",
            "planning_month": "2025-12",
        },
    )
    session_route_app.make_session(
        "session-b",
        engine=planning_engine_result,
        custom_name="Session B",
        filename="b.xlsm",
        metadata={
            "materials": 20,
            "periods": 6,
            "site": "SITE-B",
            "planning_month": "2026-01",
        },
    )
    session_route_app.set_active_session_id("session-a")

    response = session_route_app.client.get("/api/sessions")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["active_session_id"] == "session-a"
    listed = {item["id"]: item for item in _flatten_session_groups(payload)}
    assert set(listed) == {"session-a", "session-b"}

    assert listed["session-a"]["filename"] == "a.xlsm"
    assert listed["session-a"]["custom_name"] == "Session A"
    assert listed["session-a"]["metadata"]["materials"] == 10
    assert listed["session-a"]["metadata"]["periods"] == 12
    assert listed["session-a"]["calculated"] is True
    assert listed["session-a"]["active"] is True

    assert listed["session-b"]["filename"] == "b.xlsm"
    assert listed["session-b"]["custom_name"] == "Session B"
    assert listed["session-b"]["metadata"]["materials"] == 20
    assert listed["session-b"]["metadata"]["periods"] == 6
    assert listed["session-b"]["calculated"] is True
    assert listed["session-b"]["active"] is False


def test_sessions_snapshot_does_not_deepcopy_engine_with_open_buffer(
    session_route_app,
    planning_engine_result,
):
    with open(__file__, "rb") as file_handle:
        planning_engine_result.open_buffer = file_handle
        session_route_app.make_session(
            "session-a",
            engine=planning_engine_result,
            custom_name="Session A",
        )
        session_route_app.set_active_session_id("session-a")

        response = session_route_app.client.post(
            "/api/sessions/snapshot",
            json={"name": "Buffered snapshot"},
        )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["session"]["calculated"] is True
    new_id = payload["session"]["id"]
    assert set(session_route_app.sessions) == {"session-a", new_id}
    assert session_route_app.sessions[new_id]["engine"] is None
    assert session_route_app.sessions[new_id]["parameters"] == session_route_app.sessions["session-a"]["parameters"]
    assert session_route_app.save_calls


def test_snapshot_with_engine_copies_pending_edits(
    session_route_app,
):
    fake_engine = SimpleNamespace(
        data=SimpleNamespace(
            materials={"MAT-1": object()},
            periods=["2025-12"],
            config=SimpleNamespace(site="TEST"),
            machines={},
            purchased_and_produced={},
            valuation_params=None,
        ),
        results={},
        value_results={},
    )
    session_route_app.make_session(
        "session-a",
        engine=fake_engine,
        custom_name="Session A",
        pending_edits={
            "01. Demand forecast||MAT-1||||2025-12": {
                "original": 10.0,
                "new_value": 12.0,
            },
        },
        value_aux_overrides={
            "01. Demand forecast||MAT-1": {
                "original": 1.0,
                "new_value": 2.0,
            },
        },
        machine_overrides={"M1": {"oee": 0.9}},
    )
    session_route_app.set_active_session_id("session-a")

    response = session_route_app.client.post(
        "/api/sessions/snapshot",
        json={"name": "Snapshot A"},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    new_id = payload["session"]["id"]
    assert new_id in session_route_app.sessions
    new_session = session_route_app.sessions[new_id]
    assert new_session["custom_name"] == "Snapshot A"
    assert new_session["is_snapshot"] is True
    assert new_session["engine"] is None
    assert payload["session"]["calculated"] is True
    assert new_session["pending_edits"] == {
        "01. Demand forecast||MAT-1||||2025-12": {
            "original": 10.0,
            "new_value": 12.0,
        },
    }
    assert new_session["value_aux_overrides"] == {
        "01. Demand forecast||MAT-1": {
            "original": 1.0,
            "new_value": 2.0,
        },
    }
    assert session_route_app.save_calls


def test_delete_session_removes_from_dict_and_saves(
    session_route_app,
    planning_engine_result,
):
    session_route_app.make_session("session-a", engine=planning_engine_result)
    session_route_app.make_session("session-b", engine=planning_engine_result)
    session_route_app.set_active_session_id("session-b")

    response = session_route_app.client.delete("/api/sessions/session-a")

    assert response.status_code == 200, response.get_json(silent=True)
    assert response.get_json()["active_session_id"] == "session-b"
    assert "session-a" not in session_route_app.sessions
    assert "session-b" in session_route_app.sessions
    assert session_route_app.save_calls


def test_rename_session_updates_custom_name(
    session_route_app,
    planning_engine_result,
):
    session_route_app.make_session("session-a", engine=planning_engine_result, custom_name="Old")

    response = session_route_app.client.post(
        "/api/sessions/rename",
        json={"session_id": "session-a", "name": "New Name"},
    )

    assert response.status_code == 200, response.get_json(silent=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["session_id"] == "session-a"
    assert payload["custom_name"] == "New Name"
    assert payload["session"]["custom_name"] == "New Name"
    assert session_route_app.sessions["session-a"]["custom_name"] == "New Name"
    assert session_route_app.save_calls


def test_switch_session_calls_sync_global_config(
    session_route_app,
    planning_engine_result,
):
    session_route_app.make_session("session-a", engine=planning_engine_result)
    session_route_app.make_session("session-b", engine=planning_engine_result)
    session_route_app.set_active_session_id("session-a")

    response = session_route_app.client.post(
        "/api/sessions/switch",
        json={"session_id": "session-b"},
    )

    assert response.status_code == 200, response.get_json(silent=True)
    assert session_route_app.get_active_session_id() == "session-b"
    assert session_route_app.sync_calls == [planning_engine_result, planning_engine_result]


@pytest.mark.no_fixture
def test_rename_session_missing_session_returns_404(session_route_app):
    response = session_route_app.client.post(
        "/api/sessions/rename",
        json={"session_id": "missing", "name": "Nope"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


@pytest.mark.no_fixture
def test_snapshot_requires_name(session_route_app):
    response = session_route_app.client.post("/api/sessions/snapshot", json={"name": " "})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Name cannot be empty"


@pytest.mark.no_fixture
def test_snapshot_requires_active_session(session_route_app):
    response = session_route_app.client.post("/api/sessions/snapshot", json={"name": "Snapshot"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No active session"


def test_rename_session_requires_name(session_route_app, planning_engine_result):
    session_route_app.make_session("session-a", engine=planning_engine_result)

    response = session_route_app.client.post(
        "/api/sessions/rename",
        json={"session_id": "session-a", "name": " "},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Name cannot be empty"


@pytest.mark.no_fixture
def test_switch_session_missing_session_returns_404(session_route_app):
    response = session_route_app.client.post(
        "/api/sessions/switch",
        json={"session_id": "missing"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


@pytest.mark.no_fixture
def test_delete_session_missing_session_returns_404(session_route_app):
    response = session_route_app.client.delete("/api/sessions/missing")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"
