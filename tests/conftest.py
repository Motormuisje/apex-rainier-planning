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
        lambda sess, engine: sess.update({"reset_baseline": {"installed": True}}),
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
