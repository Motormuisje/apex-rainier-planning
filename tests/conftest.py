"""Shared pytest fixtures for Apex Rainier tests.

Test data is deliberately kept OUT of the Git repo. Set the env var
SOP_GOLDEN_FIXTURE to point at a local .xlsm file. See tests/README.md.
"""

import os
from pathlib import Path

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
