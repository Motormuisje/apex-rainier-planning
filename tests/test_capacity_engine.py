"""Unit tests for CapacityEngine.

All tests use synthetic stubs — no golden fixture needed.
"""

from types import SimpleNamespace

import pytest

from modules.capacity_engine import CapacityEngine
from modules.models import (
    Material, ProductType, Machine, MachineGroup,
    ShiftSystem, LineType, SafetyStockConfig
)

pytestmark = pytest.mark.no_fixture

PERIODS = ["2025-01", "2025-02", "2025-03"]


# ---------------------------------------------------------------------------
# Stub factories
# ---------------------------------------------------------------------------

def _mat(mat_num, name="Mat", product_type=ProductType.BULK_PRODUCT,
         grouped_production_line=None, production_line=None,
         mill_machine_group=None, packaging_machine_group=None,
         fte_requirements=1.0):
    return Material(
        material_number=mat_num, name=name, product_type=product_type,
        product_family="FAM", spc_product="", product_cluster="", product_name="",
        grouped_production_line=grouped_production_line, production_line=production_line,
        mill_machine_group=mill_machine_group, packaging_machine_group=packaging_machine_group,
        fte_requirements=fte_requirements,
    )


def _machine(code, oee=1.0, group="ZZ_GROUP01", shift=ShiftSystem.THREE_SHIFT,
             availability=None):
    avail = availability or {p: 1.0 for p in PERIODS}
    return Machine(
        machine_id=f"Z_MACH_{code}", machine_code=code, name=f"Machine {code}",
        oee=oee, machine_group=group, availability_by_period=avail, shift_system=shift,
    )


def _routing(work_center, base_qty=100.0, std_time=1.0):
    return SimpleNamespace(work_center=work_center, base_quantity=base_qty, standard_time=std_time)


def _make_data(
    machines, groups, materials, production_plan, routings_map,
    periods=None, shift_hours=None, fte_hours_per_year=1492,
    config=None, bom=None,
):
    return SimpleNamespace(
        periods=periods or PERIODS,
        machines=machines,
        machine_groups=groups,
        materials=materials,
        config=config,
        bom=bom or [],
        shift_hours=shift_hours or {
            "2-shift system": 347.0,
            "3-shift system": 520.0,
            "24/7 production": 730.0,
        },
        fte_hours_per_year=fte_hours_per_year,
        get_all_routings=lambda m: routings_map.get(m, []),
        default_shift_name="3-shift system",
    )


def _minimal_setup(
    oee=1.0, availability=None, shift=ShiftSystem.THREE_SHIFT,
    production_plan=None, std_time=1.0, base_qty=100.0,
):
    """Single machine, single material, single group."""
    mc = _machine("MC1", oee=oee, group="ZZ_GROUP01", shift=shift,
                  availability=availability or {p: 1.0 for p in PERIODS})
    grp = MachineGroup("ZZ_GROUP01", ["MC1"])
    mat = _mat("ZZ_GROUP01", name="Group Material", fte_requirements=1.0)
    prod_mat = _mat("MAT-1")
    machines = {"MC1": mc}
    materials = {"ZZ_GROUP01": mat, "MAT-1": prod_mat}
    groups = {"ZZ_GROUP01": grp}
    plan = production_plan or {"MAT-1": {p: 100.0 for p in PERIODS}}
    routings = {"MAT-1": [_routing("MC1", base_qty=base_qty, std_time=std_time)]}
    data = _make_data(machines, groups, materials, plan, routings)
    return data, plan


# ---------------------------------------------------------------------------
# _calculate_capacity_utilization
# ---------------------------------------------------------------------------

class TestCapacityUtilization:
    def test_hours_computed_from_production_and_routing(self):
        # AUX2 = base_qty / std_time = 100/2 = 50. Hours = prod_qty / AUX2 = 100/50 = 2
        data, plan = _minimal_setup(base_qty=100.0, std_time=2.0)
        eng = CapacityEngine(data, plan)
        eng._calculate_capacity_utilization()
        # Find the machine-level row for MC1
        mc_rows = [r for r in eng.rows_07_cap if r.material_name == "MC1"]
        assert mc_rows, "No machine-level row for MC1"
        assert mc_rows[0].values["2025-01"] == pytest.approx(2.0)

    def test_zero_production_gives_zero_hours(self):
        data, _ = _minimal_setup()
        plan = {"MAT-1": {p: 0.0 for p in PERIODS}}
        eng = CapacityEngine(data, plan)
        eng._calculate_capacity_utilization()
        mc_rows = [r for r in eng.rows_07_cap if r.material_name == "MC1"]
        assert mc_rows[0].values["2025-01"] == pytest.approx(0.0)

    def test_oee_divides_raw_hours(self):
        # OEE=0.8: machine uses raw_hours / 0.8 more time
        data, plan = _minimal_setup(oee=0.8, base_qty=100.0, std_time=1.0)
        eng = CapacityEngine(data, plan)
        eng._calculate_capacity_utilization()
        mc_rows = [r for r in eng.rows_07_cap if r.material_name == "MC1"]
        # raw_hours = 100/100 = 1; machine_hours = 1 / 0.8 = 1.25
        assert mc_rows[0].values["2025-01"] == pytest.approx(1.25)

    def test_group_row_emitted(self):
        data, plan = _minimal_setup()
        eng = CapacityEngine(data, plan)
        eng._calculate_capacity_utilization()
        group_rows = [r for r in eng.rows_07_cap if r.material_number == "ZZ_GROUP01"]
        assert group_rows, "No group-level row"

    def test_material_without_routing_skipped(self):
        data, _ = _minimal_setup()
        plan = {"GHOST": {p: 100.0 for p in PERIODS}}  # no routing
        data.get_all_routings = lambda m: []
        eng = CapacityEngine(data, plan)
        eng._calculate_capacity_utilization()
        mat_rows = [r for r in eng.rows_07_cap if r.material_number == "GHOST"]
        assert mat_rows == []


# ---------------------------------------------------------------------------
# _calculate_utilization_rate
# ---------------------------------------------------------------------------

class TestUtilizationRate:
    def test_utilization_rate_computed(self):
        # shift_hours=520, availability=1.0, machine hours=2 → rate = 2/520
        data, plan = _minimal_setup(base_qty=100.0, std_time=0.5)
        # AUX2 = 100/0.5 = 200, hours = 100/200 = 0.5 per period
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        util_rows = results[LineType.UTILIZATION_RATE.value]
        assert util_rows, "No utilization rate rows"
        rate = util_rows[0].values["2025-01"]
        # rate = 0.5 / (520 * 1.0) ≈ 0.000962
        assert 0.0 <= rate <= 1.0

    def test_unlimited_machine_has_rate_one(self):
        mc = _machine("UNLIMITED", oee=1.0, group="ZZ_GROUP01",
                      shift=ShiftSystem.UNLIMITED)
        grp = MachineGroup("ZZ_GROUP01", ["UNLIMITED"])
        mat = _mat("ZZ_GROUP01", fte_requirements=1.0)
        prod_mat = _mat("MAT-1")
        data = _make_data(
            machines={"UNLIMITED": mc}, groups={"ZZ_GROUP01": grp},
            materials={"ZZ_GROUP01": mat, "MAT-1": prod_mat},
            production_plan={"MAT-1": {p: 100.0 for p in PERIODS}},
            routings_map={"MAT-1": [_routing("UNLIMITED")]},
        )
        eng = CapacityEngine(data, {"MAT-1": {p: 100.0 for p in PERIODS}})
        results = eng.calculate()
        util_rows = results[LineType.UTILIZATION_RATE.value]
        assert util_rows[0].values["2025-01"] == pytest.approx(1.0)

    def test_zero_availability_gives_zero_rate(self):
        data, plan = _minimal_setup(
            availability={p: 0.0 for p in PERIODS},
        )
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        util_rows = results[LineType.UTILIZATION_RATE.value]
        assert util_rows[0].values["2025-01"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _calculate_fte_requirements
# ---------------------------------------------------------------------------

class TestFTERequirements:
    def test_fte_row_emitted_per_group(self):
        data, plan = _minimal_setup()
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        fte_rows = results[LineType.FTE_REQUIREMENTS.value]
        group_fte = [r for r in fte_rows if r.material_number == "ZZ_GROUP01"]
        assert group_fte, "No FTE row for ZZ_GROUP01"

    def test_fte_calculated_as_hours_per_monthly_fte(self):
        # With known hours and FTE hours, verify the ratio
        data, plan = _minimal_setup(base_qty=100.0, std_time=1.0)
        # hours = 100/100 = 1 per period per machine (OEE=1)
        # FTE = hours / (1492/12)
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        fte_rows = results[LineType.FTE_REQUIREMENTS.value]
        group_fte = [r for r in fte_rows if r.material_number == "ZZ_GROUP01"]
        fte_val = group_fte[0].values["2025-01"]
        expected = 1.0 / (1492 / 12)
        assert fte_val == pytest.approx(expected, rel=0.01)

    def test_zero_production_gives_zero_fte(self):
        data, _ = _minimal_setup()
        plan = {"MAT-1": {p: 0.0 for p in PERIODS}}
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        fte_rows = results[LineType.FTE_REQUIREMENTS.value]
        group_fte = [r for r in fte_rows if r.material_number == "ZZ_GROUP01"]
        assert group_fte[0].values["2025-01"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _calculate_shift_availability (Line 11)
# ---------------------------------------------------------------------------

class TestShiftAvailability:
    def test_shift_availability_row_emitted_per_group(self):
        data, plan = _minimal_setup()
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        avail_rows = results[LineType.SHIFT_AVAILABILITY.value]
        assert any(r.material_number == "ZZ_GROUP01" for r in avail_rows)

    def test_shift_hours_value_matches_default(self):
        data, plan = _minimal_setup()
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        avail_rows = results[LineType.SHIFT_AVAILABILITY.value]
        grp_row = next(r for r in avail_rows if r.material_number == "ZZ_GROUP01")
        # Default is 3-shift = 520 hours/month
        assert grp_row.values["2025-01"] == pytest.approx(520.0)


# ---------------------------------------------------------------------------
# Full calculate() output structure
# ---------------------------------------------------------------------------

class TestCalculateOutputStructure:
    def test_all_expected_line_types_returned(self):
        data, plan = _minimal_setup()
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        expected = {
            LineType.CAPACITY_UTILIZATION.value,
            LineType.AVAILABLE_CAPACITY.value,
            LineType.UTILIZATION_RATE.value,
            LineType.SHIFT_AVAILABILITY.value,
            LineType.FTE_REQUIREMENTS.value,
        }
        assert set(results.keys()) == expected

    def test_all_result_lists_are_lists(self):
        data, plan = _minimal_setup()
        eng = CapacityEngine(data, plan)
        results = eng.calculate()
        for key, rows in results.items():
            assert isinstance(rows, list), f"{key} is not a list"
