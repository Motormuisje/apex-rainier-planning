"""Unit tests for ValuePlanningEngine.

All tests use synthetic stubs — no golden fixture needed.
"""

from types import SimpleNamespace
from dataclasses import dataclass
from typing import Optional

import pytest

from modules.value_planning_engine import ValuePlanningEngine
from modules.models import PlanningRow, LineType, ValuationParameters

pytestmark = pytest.mark.no_fixture

PERIODS = ["2025-01", "2025-02", "2025-03"]


# ---------------------------------------------------------------------------
# Helpers / stub factories
# ---------------------------------------------------------------------------

def _row(line_type, mat_num="MAT-1", mat_name="Mat", product_type="Bulk Product",
         values=None, starting_stock=0.0, aux_column=None, aux_2_column=None):
    r = PlanningRow(
        material_number=mat_num, material_name=mat_name, product_type=product_type,
        product_family="FAM", spc_product="", product_cluster="", product_name="",
        line_type=line_type, aux_column=aux_column, aux_2_column=aux_2_column,
        starting_stock=starting_stock,
    )
    for p, v in (values or {p: 0.0 for p in PERIODS}).items():
        r.set_value(p, v)
    return r


def _sales_price(price):
    return SimpleNamespace(price_per_unit=price)


def _material_cost(cost):
    return SimpleNamespace(cost_per_unit=cost)


def _machine_cost(rate):
    return SimpleNamespace(variable_cost_per_hour=rate)


def _vp(turnover=0.0, raw_material=0.0, machine=0.0, direct_fte=0.0,
        indirect_fte=100_000.0, overhead=50_000.0, sga=20_000.0,
        depreciation=240_000.0, nbv=1_000_000.0,
        dso=30.0, dpo=30.0, fte_cost=5_000.0):
    return ValuationParameters(
        direct_fte_cost_per_month=fte_cost,
        indirect_fte_cost_per_month=indirect_fte,
        overhead_cost_per_month=overhead,
        sga_cost_per_month=sga,
        depreciation_per_year=depreciation,
        net_book_value=nbv,
        days_sales_outstanding=dso,
        days_payable_outstanding=dpo,
    )


def _make_data(
    periods=None,
    sales_prices=None,
    material_costs=None,
    machine_costs=None,
    valuation_params=None,
    materials=None,
    stock=None,
    purchased_and_produced=None,
):
    return SimpleNamespace(
        periods=periods or PERIODS,
        sales_prices=sales_prices or {},
        material_costs=material_costs or {},
        machine_costs=machine_costs or {},
        valuation_params=valuation_params or _vp(),
        materials=materials or {},
        stock=stock or {},
        purchased_and_produced=purchased_and_produced or {},
    )


def _engine(planning_results, data=None, aux_overrides=None):
    return ValuePlanningEngine(
        data or _make_data(), planning_results, aux_overrides=aux_overrides
    )


# ---------------------------------------------------------------------------
# _convert_demand_forecast (Line 01 → revenue)
# ---------------------------------------------------------------------------

class TestConvertDemandForecast:
    def test_revenue_equals_price_times_volume(self):
        fc_row = _row(LineType.DEMAND_FORECAST.value, values={"2025-01": 100.0, "2025-02": 200.0, "2025-03": 300.0})
        data = _make_data(sales_prices={"MAT-1": _sales_price(10.0)})
        eng = _engine({LineType.DEMAND_FORECAST.value: [fc_row]}, data)
        results = eng.calculate()
        rev_rows = results[LineType.DEMAND_FORECAST.value]
        assert rev_rows[0].values["2025-01"] == pytest.approx(1000.0)
        assert rev_rows[0].values["2025-02"] == pytest.approx(2000.0)

    def test_zero_price_gives_zero_revenue(self):
        fc_row = _row(LineType.DEMAND_FORECAST.value, values={"2025-01": 500.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data(sales_prices={})  # no price
        eng = _engine({LineType.DEMAND_FORECAST.value: [fc_row]}, data)
        results = eng.calculate()
        assert results[LineType.DEMAND_FORECAST.value][0].values["2025-01"] == pytest.approx(0.0)

    def test_aux_override_replaces_price(self):
        fc_row = _row(LineType.DEMAND_FORECAST.value, values={"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data(sales_prices={"MAT-1": _sales_price(10.0)})
        overrides = {f"{LineType.DEMAND_FORECAST.value}||MAT-1": 25.0}
        eng = _engine({LineType.DEMAND_FORECAST.value: [fc_row]}, data, aux_overrides=overrides)
        results = eng.calculate()
        assert results[LineType.DEMAND_FORECAST.value][0].values["2025-01"] == pytest.approx(2500.0)

    def test_aux_column_stores_price(self):
        fc_row = _row(LineType.DEMAND_FORECAST.value, values={p: 100.0 for p in PERIODS})
        data = _make_data(sales_prices={"MAT-1": _sales_price(7.5)})
        eng = _engine({LineType.DEMAND_FORECAST.value: [fc_row]}, data)
        results = eng.calculate()
        assert results[LineType.DEMAND_FORECAST.value][0].aux_column == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# _convert_total_demand (Line 03 → raw material cost)
# ---------------------------------------------------------------------------

class TestConvertTotalDemand:
    def test_only_materials_with_purchase_plan_included(self):
        demand_row = _row(LineType.TOTAL_DEMAND.value, values={"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data(material_costs={"MAT-1": _material_cost(5.0)})
        # No purchase plan row → total demand not converted
        eng = _engine({
            LineType.TOTAL_DEMAND.value: [demand_row],
        }, data)
        results = eng.calculate()
        assert results.get(LineType.TOTAL_DEMAND.value, []) == []

    def test_material_with_purchase_plan_converted(self):
        demand_row = _row(LineType.TOTAL_DEMAND.value, values={"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0})
        pp_row = _row(LineType.PURCHASE_PLAN.value)
        data = _make_data(material_costs={"MAT-1": _material_cost(5.0)})
        eng = _engine({
            LineType.TOTAL_DEMAND.value: [demand_row],
            LineType.PURCHASE_PLAN.value: [pp_row],
        }, data)
        results = eng.calculate()
        td_rows = results.get(LineType.TOTAL_DEMAND.value, [])
        assert len(td_rows) == 1
        assert td_rows[0].values["2025-01"] == pytest.approx(500.0)  # 100 * 5.0


# ---------------------------------------------------------------------------
# _convert_inventory (Line 04 → inventory value)
# ---------------------------------------------------------------------------

class TestConvertInventory:
    def test_inventory_value_equals_stock_times_unit_cost(self):
        inv_row = _row(LineType.INVENTORY.value,
                       values={"2025-01": 200.0, "2025-02": 300.0, "2025-03": 100.0})
        data = _make_data(stock={"MAT-1": {"Total Stock": 1000.0, "Total Value": 10_000.0}})
        eng = _engine({LineType.INVENTORY.value: [inv_row]}, data)
        results = eng.calculate()
        inv_val_rows = results[LineType.INVENTORY.value]
        # unit_cost = 10000/1000 = 10.0
        assert inv_val_rows[0].values["2025-01"] == pytest.approx(2000.0)

    def test_starting_stock_value_computed(self):
        inv_row = _row(LineType.INVENTORY.value, starting_stock=50.0,
                       values={p: 0.0 for p in PERIODS})
        data = _make_data(stock={"MAT-1": {"Total Stock": 100.0, "Total Value": 1000.0}})
        eng = _engine({LineType.INVENTORY.value: [inv_row]}, data)
        results = eng.calculate()
        # starting_stock_value = 50 * 10.0 = 500.0
        assert results[LineType.INVENTORY.value][0].starting_stock == pytest.approx(500.0)

    def test_zero_unit_cost_gives_zero_values(self):
        inv_row = _row(LineType.INVENTORY.value, values={"2025-01": 1000.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data()  # no stock data → unit_cost=0
        eng = _engine({LineType.INVENTORY.value: [inv_row]}, data)
        results = eng.calculate()
        assert results[LineType.INVENTORY.value][0].values["2025-01"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _convert_capacity_utilization (Line 07 → machine cost)
# ---------------------------------------------------------------------------

class TestConvertCapacityUtilization:
    def _machine_row(self, machine_code="MC1", values=None):
        # Machine-level rows have product_type='Machine', material_name=machine_code
        return _row(
            LineType.CAPACITY_UTILIZATION.value,
            mat_num="Z_MACH_MC1", mat_name=machine_code,
            product_type="Machine",
            values=values or {"2025-01": 5.0, "2025-02": 3.0, "2025-03": 0.0},
        )

    def test_machine_cost_applied(self):
        mc_row = self._machine_row("MC1", values={"2025-01": 5.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data(machine_costs={"MC1": _machine_cost(100.0)})
        eng = _engine({LineType.CAPACITY_UTILIZATION.value: [mc_row]}, data)
        results = eng.calculate()
        mc_val = results.get(LineType.CAPACITY_UTILIZATION.value, [])
        assert mc_val[0].values["2025-01"] == pytest.approx(500.0)  # 5 hours * 100/h

    def test_group_rows_excluded(self):
        # product_type='Machine Group' → NOT converted (VBA only converts Machine rows)
        grp_row = _row(LineType.CAPACITY_UTILIZATION.value, product_type="Machine Group",
                       values={"2025-01": 10.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data(machine_costs={"MC1": _machine_cost(100.0)})
        eng = _engine({LineType.CAPACITY_UTILIZATION.value: [grp_row]}, data)
        results = eng.calculate()
        assert results.get(LineType.CAPACITY_UTILIZATION.value, []) == []

    def test_unknown_machine_code_skipped(self):
        mc_row = self._machine_row("GHOST", values={"2025-01": 5.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data(machine_costs={})  # no machine_costs
        eng = _engine({LineType.CAPACITY_UTILIZATION.value: [mc_row]}, data)
        results = eng.calculate()
        assert results.get(LineType.CAPACITY_UTILIZATION.value, []) == []


# ---------------------------------------------------------------------------
# _convert_fte_requirements (Line 12 → direct FTE cost)
# ---------------------------------------------------------------------------

class TestConvertFTERequirements:
    def test_fte_cost_applied(self):
        fte_row = _row(LineType.FTE_REQUIREMENTS.value,
                       values={"2025-01": 2.0, "2025-02": 1.5, "2025-03": 1.0})
        data = _make_data(valuation_params=_vp(fte_cost=5_000.0))
        eng = _engine({LineType.FTE_REQUIREMENTS.value: [fte_row]}, data)
        results = eng.calculate()
        fte_val = results[LineType.FTE_REQUIREMENTS.value]
        assert fte_val[0].values["2025-01"] == pytest.approx(10_000.0)  # 2 FTE * 5000

    def test_no_valuation_params_skips_fte(self):
        fte_row = _row(LineType.FTE_REQUIREMENTS.value,
                       values={"2025-01": 2.0, "2025-02": 0.0, "2025-03": 0.0})
        data = _make_data()
        data.valuation_params = None  # bypass the `or _vp()` default
        eng = _engine({LineType.FTE_REQUIREMENTS.value: [fte_row]}, data)
        results = eng.calculate()
        assert results.get(LineType.FTE_REQUIREMENTS.value, []) == []


# ---------------------------------------------------------------------------
# _create_consolidation_rows (Line 13)
# ---------------------------------------------------------------------------

class TestConsolidationRows:
    def _run_with_known_values(self):
        """Run engine with simple known inputs to verify consolidation math."""
        fc_row = _row(LineType.DEMAND_FORECAST.value, values={p: 10_000.0 for p in PERIODS})
        inv_row = _row(LineType.INVENTORY.value, starting_stock=5_000.0,
                       values={p: 8_000.0 for p in PERIODS})
        fte_row = _row(LineType.FTE_REQUIREMENTS.value, values={p: 1.0 for p in PERIODS})
        pp_row = _row(LineType.PURCHASE_PLAN.value, values={p: 0.0 for p in PERIODS})
        demand_row = _row(LineType.TOTAL_DEMAND.value, values={p: 5_000.0 for p in PERIODS})

        data = _make_data(
            sales_prices={"MAT-1": _sales_price(1.0)},
            material_costs={"MAT-1": _material_cost(0.0)},
            stock={"MAT-1": {"Total Stock": 100.0, "Total Value": 100.0}},
            valuation_params=_vp(
                fte_cost=1_000.0, indirect_fte=500.0, overhead=200.0, sga=100.0,
                depreciation=12 * 300.0, nbv=1_000.0, dso=30.0, dpo=30.0,
            ),
        )
        planning = {
            LineType.DEMAND_FORECAST.value: [fc_row],
            LineType.INVENTORY.value: [inv_row],
            LineType.FTE_REQUIREMENTS.value: [fte_row],
            LineType.PURCHASE_PLAN.value: [pp_row],
            LineType.TOTAL_DEMAND.value: [demand_row],
        }
        eng = ValuePlanningEngine(data, planning)
        results = eng.calculate()
        return results

    def test_consolidation_rows_emitted(self):
        results = self._run_with_known_values()
        consol = results.get(LineType.CONSOLIDATION.value, [])
        assert len(consol) > 0

    def test_turnover_row_present(self):
        results = self._run_with_known_values()
        consol = results[LineType.CONSOLIDATION.value]
        mat_nums = {r.material_number for r in consol}
        assert "ZZZZZZ_TURNOVER" in mat_nums

    def test_roce_row_present(self):
        results = self._run_with_known_values()
        consol = results[LineType.CONSOLIDATION.value]
        mat_nums = {r.material_number for r in consol}
        assert "ZZZZZZ_ROCE" in mat_nums

    def test_cogs_is_sum_of_components(self):
        results = self._run_with_known_values()
        consol = results[LineType.CONSOLIDATION.value]
        cogs_row = next(r for r in consol if r.material_number == "ZZZZZZ_COST OF GOODS")
        # COGS = raw_material + machine + direct_fte + indirect_fte + overhead
        # raw_material=0 (total_demand not converted since no purchase plan for MAT-1... wait)
        # direct_fte = 1 FTE * 1000/month
        # indirect_fte = 500/month, overhead = 200/month
        # COGS = 0 + 0 + 1000 + 500 + 200 = 1700
        assert cogs_row.values["2025-01"] == pytest.approx(1700.0)

    def test_no_valuation_params_skips_consolidation(self):
        fc_row = _row(LineType.DEMAND_FORECAST.value, values={p: 0.0 for p in PERIODS})
        data = _make_data()
        data.valuation_params = None  # bypass the `or _vp()` default
        eng = _engine({LineType.DEMAND_FORECAST.value: [fc_row]}, data)
        results = eng.calculate()
        assert results.get(LineType.CONSOLIDATION.value, []) == []

    def test_ebit_equals_ebitda_minus_da(self):
        results = self._run_with_known_values()
        consol = results[LineType.CONSOLIDATION.value]
        ebitda = next(r for r in consol if "EBITDA" in r.material_number)
        ebit = next(r for r in consol if r.material_number == "ZZZZZZ_EBIT")
        da = next(r for r in consol if "D&A" in r.material_number)
        for p in PERIODS:
            assert ebit.values[p] == pytest.approx(ebitda.values[p] - da.values[p], abs=0.01)
