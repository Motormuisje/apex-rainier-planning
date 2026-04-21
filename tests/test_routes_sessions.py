from unittest.mock import patch


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


def test_sessions_snapshot_deepcopy_failure_returns_500_without_saving(
    session_route_app,
    planning_engine_result,
):
    session_route_app.make_session(
        "session-a",
        engine=planning_engine_result,
        custom_name="Session A",
    )
    session_route_app.set_active_session_id("session-a")

    with patch(
        "ui.routes.sessions.copy.deepcopy",
        side_effect=RuntimeError("deepcopy failed"),
    ):
        response = session_route_app.client.post(
            "/api/sessions/snapshot",
            json={"name": "Broken snapshot"},
        )

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error"] == "Could not copy session state: deepcopy failed"
    assert set(session_route_app.sessions) == {"session-a"}
    assert not session_route_app.save_calls
