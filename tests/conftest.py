"""Shared pytest fixtures for Apex Rainier tests.

Test data is deliberately kept OUT of the Git repo. Set the env var
SOP_GOLDEN_FIXTURE to point at a local .xlsm file. See tests/README.md.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def _fixture_dir() -> Path:
    """Directory where the baseline JSON is stored alongside the Excel fixture."""
    fixture = os.environ.get("SOP_GOLDEN_FIXTURE")
    if not fixture:
        pytest.skip(
            "SOP_GOLDEN_FIXTURE env var not set — see tests/README.md. "
            "Point it at a local golden MS_RECONC .xlsm file."
        )
    path = Path(fixture)
    if not path.exists():
        pytest.skip(f"Golden fixture not found at {path}")
    return path.parent


@pytest.fixture(scope="session")
def golden_fixture_path() -> Path:
    """Absolute path to the golden .xlsm file."""
    fixture = os.environ.get("SOP_GOLDEN_FIXTURE")
    if not fixture:
        pytest.skip(
            "SOP_GOLDEN_FIXTURE env var not set — see tests/README.md. "
            "Point it at a local golden MS_RECONC .xlsm file."
        )
    path = Path(fixture)
    if not path.exists():
        pytest.skip(f"Golden fixture not found at {path}")
    return path


@pytest.fixture(scope="session")
def baseline_path() -> Path:
    """Absolute path to the golden baseline JSON (lives next to the .xlsm)."""
    return _fixture_dir() / "golden_baseline.json"


@pytest.fixture(scope="session")
def planning_engine_result(golden_fixture_path):
    """Run the full pipeline once per test session on the golden fixture.

    Parameters here must match what generate_baseline.py used when freezing
    the baseline. If you change them, regenerate the baseline.
    """
    from modules.planning_engine import PlanningEngine

    engine = PlanningEngine(
        str(golden_fixture_path),
        planning_month="2025-12",
        months_actuals=11,
        months_forecast=12,
    )
    engine.run()
    return engine


@pytest.fixture
def flask_test_app(tmp_path):
    """Minimal Flask app for route-blueprint tests without persisted sessions."""
    from flask import Flask

    from ui.routes.workflow import create_workflow_blueprint
    from ui.state_snapshot import snapshot_engine_state

    sessions = {}
    active = {"session_id": None}
    save_calls = []
    upload_path = tmp_path / "uploads"

    class NoopCycleManager:
        def has_previous_cycle(self):
            return True

        def save_current_as_previous(self, *args, **kwargs):
            return None

    def set_active_session_id(session_id):
        active["session_id"] = session_id

    def get_active():
        session_id = active["session_id"]
        sess = sessions.get(session_id) if session_id else None
        return sess, sess.get("engine") if sess else None

    def save_sessions_to_disk():
        save_calls.append(list(sessions.keys()))

    def install_clean_engine_baseline(sess, engine):
        sess["reset_baseline"] = snapshot_engine_state(
            engine,
            lambda machine, data: float(getattr(machine, "shift_hours_override", None) or 0.0),
        )

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_workflow_blueprint(
        sessions,
        set_active_session_id,
        get_active,
        lambda: upload_path,
        {},
        lambda exc, context: {"error": str(exc), "context": context},
        lambda: {},
        lambda: NoopCycleManager(),
        install_clean_engine_baseline,
        lambda sess, engine: None,
        save_sessions_to_disk,
        lambda: flask_app.app_context(),
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        sessions=sessions,
        set_active_session_id=set_active_session_id,
        get_active_session_id=lambda: active["session_id"],
        save_calls=save_calls,
        upload_path=upload_path,
    )


@pytest.fixture
def edit_route_app(golden_fixture_path):
    """Minimal Flask app for edit-route tests with in-memory session state."""
    from flask import Flask, jsonify

    from modules.planning_engine import PlanningEngine
    from ui.routes.edit_state import create_edit_state_blueprint
    from ui.routes.edits import create_edits_blueprint
    from ui.routes.machines import create_machines_blueprint

    sessions = {}
    active = {"session_id": None}
    save_calls = []
    volume_calls = []
    recalc_calls = []

    def crash_callback(*args, **kwargs):
        raise RuntimeError("unexpected callback called in edit route test")

    def make_session(session_id="edit-route-session", engine=None, **overrides):
        if engine is None:
            engine = PlanningEngine(
                str(golden_fixture_path),
                planning_month="2025-12",
                months_actuals=11,
                months_forecast=12,
            )
            engine.run()
        sess = {
            "id": session_id,
            "file_path": str(golden_fixture_path),
            "filename": golden_fixture_path.name,
            "custom_name": "Edit route test",
            "engine": engine,
            "metadata": {
                "planning_month": "2025-12",
                "materials": len(engine.data.materials) if engine else 0,
                "periods": len(engine.data.periods) if engine else 0,
            },
            "parameters": {
                "planning_month": "2025-12",
                "months_actuals": 11,
                "months_forecast": 12,
            },
            "pending_edits": {},
            "value_aux_overrides": {},
            "machine_overrides": {},
            "undo_stack": [],
            "redo_stack": [],
            "machine_undo": [],
            "machine_redo": [],
        }
        sess.update(overrides)
        sessions[session_id] = sess
        active["session_id"] = session_id
        return sess

    def get_active():
        session_id = active["session_id"]
        sess = sessions.get(session_id) if session_id else None
        return sess, sess.get("engine") if sess else None

    def save_sessions_to_disk():
        save_calls.append(list(sessions.keys()))

    def apply_volume_change(sess, engine, line_type, material_number, period, new_value, aux_column="", push_undo=True):
        volume_calls.append({
            "sess": sess,
            "engine": engine,
            "line_type": line_type,
            "material_number": material_number,
            "period": period,
            "new_value": new_value,
            "aux_column": aux_column,
            "push_undo": push_undo,
        })
        return jsonify({
            "success": True,
            "results": {"callback": "results"},
            "value_results": {"callback": "value_results"},
            "consolidation": [],
            "edit_meta": {
                "old_value": 0.0,
                "new_value": new_value,
                "original_value": 0.0,
                "delta_pct": 0.0,
            },
        })

    def recalculate_capacity_and_values(engine, sess):
        recalc_calls.append({"engine": engine, "sess": sess})

    def planning_value_payload(engine):
        return {
            "value_results": {},
            "consolidation": [],
        }

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    flask_app.register_blueprint(create_edits_blueprint(
        get_active,
        set(),
        {},
        apply_volume_change,
        crash_callback,
        crash_callback,
        save_sessions_to_disk,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
    ))
    flask_app.register_blueprint(create_machines_blueprint(
        get_active,
        lambda sess, engine: sess.get("machine_overrides", {}),
        lambda machine, data: float(getattr(machine, "shift_hours_override", None) or 0.0),
        crash_callback,
        recalculate_capacity_and_values,
        planning_value_payload,
        save_sessions_to_disk,
    ))
    flask_app.register_blueprint(create_edit_state_blueprint(
        sessions,
        set(),
        save_sessions_to_disk,
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        sessions=sessions,
        make_session=make_session,
        save_calls=save_calls,
        volume_calls=volume_calls,
        recalc_calls=recalc_calls,
    )


@pytest.fixture
def session_route_app(golden_fixture_path):
    """Minimal Flask app for session-route tests with in-memory session state."""
    from flask import Flask

    from ui.config_store import sync_global_config_from_engine
    from ui.parsers import format_purchased_and_produced
    from ui.routes.sessions import create_sessions_blueprint
    from ui.state_snapshot import (
        engine_has_manual_edits,
        machine_overrides_from_engine,
        snapshot_engine_state,
        snapshot_has_manual_edits,
    )

    sessions = {}
    active = {"session_id": None}
    global_config = {}
    save_calls = []
    sync_calls = []

    def crash_callback(*args, **kwargs):
        raise RuntimeError("unexpected callback called in session route test")

    def shift_hours_lookup(machine, data):
        return float(getattr(machine, "shift_hours_override", None) or 0.0)

    def make_session(session_id, engine=None, **overrides):
        sess = {
            "id": session_id,
            "file_path": str(golden_fixture_path),
            "filename": golden_fixture_path.name,
            "custom_name": session_id,
            "engine": engine,
            "value_results": {},
            "metadata": {
                "materials": len(engine.data.materials) if engine else 0,
                "periods": len(engine.data.periods) if engine else 0,
                "site": getattr(engine.data.config, "site", "") if engine else "NLX1",
                "planning_month": "2025-12",
            },
            "uploaded_at": "2026-04-19T00:00:00",
            "parameters": {
                "planning_month": "2025-12",
                "months_actuals": 11,
                "months_forecast": 12,
            },
            "pending_edits": {},
            "value_aux_overrides": {},
            "machine_overrides": {},
            "undo_stack": [],
            "redo_stack": [],
        }
        if engine is not None:
            sess["reset_baseline"] = snapshot_engine_state(engine, shift_hours_lookup)
        sess.update(overrides)
        sessions[session_id] = sess
        if active["session_id"] is None:
            active["session_id"] = session_id
        return sess

    def get_active_session_id():
        return active["session_id"]

    def set_active_session_id(session_id):
        active["session_id"] = session_id

    def get_active():
        session_id = active["session_id"]
        sess = sessions.get(session_id) if session_id else None
        return sess, sess.get("engine") if sess else None

    def save_sessions_to_disk():
        save_calls.append(list(sessions.keys()))

    def sync_config(engine):
        sync_calls.append(engine)
        sync_global_config_from_engine(engine, global_config, format_purchased_and_produced)

    def install_clean_engine_baseline(sess, engine, clear_machine_overrides=True):
        sess["reset_baseline"] = snapshot_engine_state(engine, shift_hours_lookup)
        if clear_machine_overrides:
            sess["machine_overrides"] = {}

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_sessions_blueprint(
        sessions,
        get_active_session_id,
        set_active_session_id,
        get_active,
        global_config,
        machine_overrides_from_engine,
        save_sessions_to_disk,
        sync_config,
        crash_callback,
        install_clean_engine_baseline,
        lambda sess, engine: None,
        snapshot_has_manual_edits,
        engine_has_manual_edits,
        lambda: flask_app.app_context(),
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        sessions=sessions,
        make_session=make_session,
        get_active_session_id=get_active_session_id,
        set_active_session_id=set_active_session_id,
        global_config=global_config,
        save_calls=save_calls,
        sync_calls=sync_calls,
    )
