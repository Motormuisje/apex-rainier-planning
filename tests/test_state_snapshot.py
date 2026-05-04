from types import SimpleNamespace

import pytest

from modules.models import LineType, PlanningRow
import ui.state_snapshot as state_snapshot


pytestmark = pytest.mark.no_fixture


class FakeMachine:
    def __init__(self, *, oee=0.8, availability=None, shift_hours_override=None):
        self.oee = oee
        self.availability_by_period = availability or {"2025-12": 0.9}
        self.shift_hours_override = shift_hours_override


def _planning_row(line_type=LineType.DEMAND_FORECAST.value, *, manual_edits=None, values=None):
    return PlanningRow(
        material_number="MAT-1",
        material_name="Material 1",
        product_type="Bulk Product",
        product_family="Family",
        spc_product="SPC",
        product_cluster="Cluster",
        product_name="Product",
        line_type=line_type,
        aux_column="Aux 1",
        aux_2_column="Aux 2",
        starting_stock=12.5,
        values=values or {"2025-12": 10.0, "2026-01": 20.0},
        manual_edits=manual_edits or {},
    )


def _engine(machine=None):
    return SimpleNamespace(
        data=SimpleNamespace(
            machines={"M1": machine or FakeMachine()},
            purchased_and_produced={"MAT-1": 0.25},
            valuation_params=None,
        ),
        results={
            LineType.DEMAND_FORECAST.value: [_planning_row()],
            LineType.PRODUCTION_PLAN.value: [
                _planning_row(LineType.PRODUCTION_PLAN.value, values={"2025-12": 5.0}),
            ],
            LineType.PURCHASE_RECEIPT.value: [
                _planning_row(LineType.PURCHASE_RECEIPT.value, values={"2025-12": 3.0}),
            ],
        },
        value_results={},
    )


def test_row_key_from_obj_handles_object_and_dict():
    row = _planning_row()
    snap = state_snapshot.row_payload(row)

    assert state_snapshot.row_key_from_obj(row) == (
        "MAT-1",
        LineType.DEMAND_FORECAST.value,
        "Aux 1",
        "Aux 2",
        "Material 1",
    )
    assert state_snapshot.row_key_from_obj(snap) == state_snapshot.row_key_from_obj(row)


def test_ensure_reset_baseline_keeps_existing_clean_baseline(monkeypatch):
    sess = {"reset_baseline": {"results": {}, "value_results": {}}}
    calls = []
    monkeypatch.setattr(state_snapshot, "snapshot_engine_state", lambda engine, lookup: calls.append(True) or {})

    state_snapshot.ensure_reset_baseline(sess, _engine(), lambda machine, data: 520.0)

    assert sess["reset_baseline"] == {"results": {}, "value_results": {}}
    assert calls == []


def test_ensure_reset_baseline_captures_when_unset(monkeypatch):
    captured = {"results": {"x": []}, "value_results": {}}
    monkeypatch.setattr(state_snapshot, "snapshot_engine_state", lambda engine, lookup: captured)
    sess = {}

    state_snapshot.ensure_reset_baseline(sess, _engine(), lambda machine, data: 520.0)

    assert sess["reset_baseline"] is captured


def test_ensure_reset_baseline_recaptures_dirty_baseline_for_clean_engine(monkeypatch):
    captured = {"results": {"fresh": []}, "value_results": {}}
    sess = {
        "reset_baseline": {
            "results": {
                "lt": [{"manual_edits": {"2025-12": {"original": 1, "new": 2}}}],
            },
            "value_results": {},
        },
    }
    monkeypatch.setattr(state_snapshot, "snapshot_engine_state", lambda engine, lookup: captured)

    state_snapshot.ensure_reset_baseline(sess, _engine(), lambda machine, data: 520.0)

    assert sess["reset_baseline"] is captured


def test_snapshot_engine_state_captures_results_config_and_machines():
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
    engine = _engine(FakeMachine(shift_hours_override=610.0))
    engine.data.valuation_params = vp

    snapshot = state_snapshot.snapshot_engine_state(engine, lambda machine, data: 612.5)

    assert snapshot["valuation_params"]["1"] == pytest.approx(1.0)
    assert snapshot["purchased_and_produced"] == {"MAT-1": 0.25}
    assert snapshot["machines"]["M1"]["shift_hours_override"] == pytest.approx(610.0)
    assert snapshot["machines"]["M1"]["shift_hours_computed"] == pytest.approx(612.5)
    assert snapshot["results"][LineType.DEMAND_FORECAST.value][0]["material_number"] == "MAT-1"


def test_machine_overrides_from_engine_returns_existing_without_baseline():
    sess = {"machine_overrides": {"M1": {"oee": 0.7}}}

    assert state_snapshot.machine_overrides_from_engine(sess, _engine()) == {"M1": {"oee": 0.7}}


def test_machine_overrides_from_engine_detects_changed_machine_fields():
    machine = FakeMachine(oee=0.75, availability={"2025-12": 0.8}, shift_hours_override=600.0)
    sess = {
        "reset_baseline": {
            "machines": {
                "M1": {
                    "oee": 0.8,
                    "availability_by_period": {"2025-12": 0.9},
                    "shift_hours_override": None,
                },
            },
        },
    }

    overrides = state_snapshot.machine_overrides_from_engine(sess, _engine(machine))

    assert overrides == {
        "M1": {
            "oee": pytest.approx(0.75),
            "availability_by_period": {"2025-12": 0.8},
            "shift_hours_override": pytest.approx(600.0),
        },
    }


def test_apply_machine_overrides_updates_known_machines_only():
    engine = _engine()

    changed = state_snapshot.apply_machine_overrides(
        engine,
        {
            "M1": {
                "oee": 0.7,
                "availability_by_period": {"2025-12": 0.6},
                "shift_hours_override": 500.0,
            },
            "NOPE": {"oee": 0.1},
        },
    )

    machine = engine.data.machines["M1"]
    assert changed is True
    assert machine.oee == pytest.approx(0.7)
    assert machine.availability_by_period == {"2025-12": 0.6}
    assert machine.shift_hours_override == pytest.approx(500.0)
    assert state_snapshot.apply_machine_overrides(engine, {}) is False


def test_snapshot_has_manual_edits_true_and_false():
    clean = {"results": {"lt": [{"manual_edits": {}}]}, "value_results": {}}
    dirty = {"results": {}, "value_results": {"lt": [{"manual_edits": {"2025-12": {"new": 1}}}]}}

    assert state_snapshot.snapshot_has_manual_edits(clean) is False
    assert state_snapshot.snapshot_has_manual_edits(dirty) is True


def test_engine_has_manual_edits_true_and_false():
    clean_engine = SimpleNamespace(results={"lt": [_planning_row()]})
    dirty_engine = SimpleNamespace(results={"lt": [_planning_row(manual_edits={"2025-12": {"original": 1, "new": 2}})]})

    assert state_snapshot.engine_has_manual_edits(clean_engine) is False
    assert state_snapshot.engine_has_manual_edits(dirty_engine) is True


def test_planning_row_from_snapshot_round_trips_fields():
    original = _planning_row(manual_edits={"2025-12": {"original": 10, "new": 12}})
    snap = state_snapshot.row_payload(original)

    restored = state_snapshot.planning_row_from_snapshot(snap)

    assert restored.material_number == original.material_number
    assert restored.material_name == original.material_name
    assert restored.line_type == original.line_type
    assert restored.aux_column == original.aux_column
    assert restored.aux_2_column == original.aux_2_column
    assert restored.starting_stock == pytest.approx(original.starting_stock)
    assert restored.values == original.values
    assert restored.manual_edits == {"2025-12": {"original": 10.0, "new": 12.0}}


def test_rebuild_volume_caches_from_results_updates_engine():
    engine = _engine()
    engine.all_production_plans = {}
    engine.all_purchase_receipts = {}
    engine._iq_cache = object()

    state_snapshot.rebuild_volume_caches_from_results(engine)

    assert engine.all_production_plans == {"MAT-1": {"2025-12": 5.0}}
    assert engine.all_purchase_receipts == {"MAT-1": {"2025-12": 3.0}}
    assert engine._iq_cache is None


def test_build_pending_edits_from_results_snapshot_uses_canonical_key():
    snap = {
        LineType.DEMAND_FORECAST.value: [
            {
                "line_type": LineType.DEMAND_FORECAST.value,
                "material_number": "MAT-1",
                "aux_column": "Aux",
                "manual_edits": {"2025-12": {"original": "10", "new": "12.5"}},
            },
        ],
    }

    pending = state_snapshot.build_pending_edits_from_results_snapshot(snap)

    assert pending == {
        "01. Demand forecast||MAT-1||Aux||2025-12": {
            "original": 10.0,
            "new_value": 12.5,
        },
    }


def test_restore_engine_state_restores_rows_config_and_machine_state():
    engine = _engine(FakeMachine(oee=0.1, availability={"2025-12": 0.2}))
    snapshot = {
        "results": {
            LineType.DEMAND_FORECAST.value: [
                {
                    **state_snapshot.row_payload(_planning_row()),
                    "values": {"2025-12": 42.0},
                },
            ],
        },
        "value_results": {
            LineType.CONSOLIDATION.value: [
                state_snapshot.row_payload(_planning_row(LineType.CONSOLIDATION.value)),
            ],
        },
        "valuation_params": {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8},
        "purchased_and_produced": {"MAT-2": 0.4},
        "machines": {
            "M1": {
                "oee": 0.95,
                "availability_by_period": {"2025-12": 0.88},
            },
        },
    }
    global_config = {}

    state_snapshot.restore_engine_state(engine, snapshot, global_config)

    assert engine.results[LineType.DEMAND_FORECAST.value][0].values == {"2025-12": 42.0}
    assert LineType.PRODUCTION_PLAN.value in engine.results
    assert engine.value_results[LineType.CONSOLIDATION.value][0].line_type == LineType.CONSOLIDATION.value
    assert engine.all_production_plans == {}
    assert engine.data.purchased_and_produced == {"MAT-2": 0.4}
    assert global_config["purchased_and_produced"] == "MAT-2:0.4"
    assert global_config["valuation_params"]["1"] == pytest.approx(1.0)
    assert engine.data.machines["M1"].oee == pytest.approx(0.95)
    assert engine.data.machines["M1"].availability_by_period == {"2025-12": 0.88}
