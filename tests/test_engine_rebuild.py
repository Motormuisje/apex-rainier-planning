from types import SimpleNamespace

import pytest

from modules.models import LineType, PlanningRow
from ui.engine_rebuild import (
    build_clean_engine_for_session,
    get_config_overrides,
    get_session_config_overrides,
    install_clean_engine_baseline,
)
import ui.engine_rebuild as engine_rebuild


pytestmark = pytest.mark.no_fixture


def _row():
    return PlanningRow(
        material_number="MAT-1",
        material_name="Material 1",
        product_type="Bulk Product",
        product_family="Family",
        spc_product="SPC",
        product_cluster="Cluster",
        product_name="Product",
        line_type=LineType.DEMAND_FORECAST.value,
        values={"2025-12": 10.0},
    )


def _engine():
    machine = SimpleNamespace(
        oee=0.8,
        availability_by_period={"2025-12": 0.9},
        shift_hours_override=None,
    )
    return SimpleNamespace(
        data=SimpleNamespace(
            machines={"M1": machine},
            purchased_and_produced={"MAT-1": 0.5},
            valuation_params=None,
        ),
        results={LineType.DEMAND_FORECAST.value: [_row()]},
        value_results={},
    )


def test_get_config_overrides_returns_known_keys():
    global_config = {
        "site": "NLX1",
        "forecast_months": 12,
        "unlimited_machines": "M1",
        "purchased_and_produced": "MAT-1:0.5",
    }

    overrides = get_config_overrides(global_config)

    assert overrides["site"] == "NLX1"
    assert overrides["forecast_months"] == 12
    assert overrides["unlimited_machines"] == "M1"
    assert overrides["purchased_and_produced"] == "MAT-1:0.5"


def test_get_config_overrides_omits_missing_keys():
    overrides = get_config_overrides({})

    assert overrides == {}


def test_get_config_overrides_includes_nonzero_valuation_params():
    overrides = get_config_overrides({"valuation_params": {"1": "0", "2": "12.5"}})

    assert overrides["valuation_params"] == {"1": "0", "2": "12.5"}


def test_get_session_config_overrides_merges_session_engine_state_into_global():
    vp = SimpleNamespace(
        direct_fte_cost_per_month=1.0,
        indirect_fte_cost_per_month=2.0,
        overhead_cost_per_month=3.0,
        sga_cost_per_month=4.0,
        depreciation_per_year=5.0,
        net_book_value=6.0,
        days_sales_outstanding=7.0,
        days_payable_outstanding=8.0,
    )
    sess = {
        "engine": SimpleNamespace(
            data=SimpleNamespace(
                valuation_params=vp,
                purchased_and_produced={"MAT-1": 0.5},
            ),
        ),
    }
    global_config = {"site": "NLX1", "forecast_months": 12}

    overrides = get_session_config_overrides(sess, global_config)

    assert overrides["site"] == "NLX1"
    assert overrides["forecast_months"] == 12
    assert overrides["valuation_params"]["1"] == pytest.approx(1.0)
    assert overrides["purchased_and_produced"] == "MAT-1:0.5"


def test_get_session_config_overrides_uses_session_valuation_without_engine():
    sess = {"parameters": None, "valuation_params": {"1": 2.0}}

    overrides = get_session_config_overrides(sess, {"site": "NLX2"})

    assert overrides["site"] == "NLX2"
    assert overrides["valuation_params"] == {"1": 2.0}


def test_get_session_config_overrides_uses_global_without_session_params():
    sess = {"parameters": None}

    overrides = get_session_config_overrides(sess, {"site": "NLX2"})

    assert overrides["site"] == "NLX2"


def test_get_session_config_overrides_accepts_none_session():
    assert get_session_config_overrides(None, {"site": "NLX2"}) == {"site": "NLX2"}


def test_build_clean_engine_for_session_returns_none_without_params():
    assert build_clean_engine_for_session({"file_path": "unused"}, {}) is None


def test_build_clean_engine_for_session_constructs_and_runs_planning_engine(monkeypatch):
    calls = []

    class RecordingPlanningEngine:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.run_called = False
            calls.append(self)

        def run(self):
            self.run_called = True

    monkeypatch.setattr(engine_rebuild, "PlanningEngine", RecordingPlanningEngine)
    sess = {
        "file_path": "workbook.xlsm",
        "extract_files": ["extract.xlsx"],
        "parameters": {
            "planning_month": "2026-01",
            "months_actuals": "6",
            "months_forecast": "9",
        },
    }

    engine = build_clean_engine_for_session(sess, {"site": "NLX1", "forecast_months": 12})

    assert engine is calls[0]
    assert engine.run_called is True
    assert engine.args == ("workbook.xlsm",)
    assert engine.kwargs["planning_month"] == "2026-01"
    assert engine.kwargs["months_actuals"] == 6
    assert engine.kwargs["months_forecast"] == 12
    assert engine.kwargs["extract_files"] == ["extract.xlsx"]
    assert engine.kwargs["config_overrides"]["site"] == "NLX1"


def test_install_clean_engine_baseline_captures_snapshot_and_clears_overrides():
    sess = {
        "machine_overrides": {"M1": {"oee": 0.9}},
        "machine_undo": [{"machine": "M1"}],
        "machine_redo": [{"machine": "M1"}],
    }

    install_clean_engine_baseline(sess, _engine(), lambda machine, data: 520.0, clear_machine_overrides=True)

    assert "reset_baseline" in sess
    assert sess["reset_baseline"]["machines"]["M1"]["shift_hours_computed"] == pytest.approx(520.0)
    assert sess["machine_overrides"] == {}
    assert sess["machine_undo"] == []
    # Current production behavior invalidates machine_undo only; machine_redo is untouched.
    assert sess["machine_redo"] == [{"machine": "M1"}]


def test_install_clean_engine_baseline_preserves_overrides_when_flag_false():
    sess = {
        "machine_overrides": {"M1": {"oee": 0.9}},
        "machine_undo": [{"machine": "M1"}],
    }

    install_clean_engine_baseline(sess, _engine(), lambda machine, data: 520.0, clear_machine_overrides=False)

    assert "reset_baseline" in sess
    assert sess["machine_overrides"] == {"M1": {"oee": 0.9}}
    assert sess["machine_undo"] == []
