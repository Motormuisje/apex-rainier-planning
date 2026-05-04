"""Unit tests for InventoryEngine and ceiling_multiple.

All tests use synthetic data (SimpleNamespace stubs) — no golden fixture needed.
"""

from types import SimpleNamespace

import pytest

from modules.inventory_engine import InventoryEngine, ceiling_multiple
from modules.models import Material, ProductType, SafetyStockConfig, BOMItem, LineType

pytestmark = pytest.mark.no_fixture

PERIODS = ["2025-01", "2025-02", "2025-03"]


# ---------------------------------------------------------------------------
# ceiling_multiple (standalone helper)
# ---------------------------------------------------------------------------

class TestCeilingMultiple:
    def test_exact_multiple_unchanged(self):
        assert ceiling_multiple(100.0, 50.0) == pytest.approx(100.0)

    def test_rounds_up_to_next_multiple(self):
        assert ceiling_multiple(101.0, 50.0) == pytest.approx(150.0)

    def test_zero_value_returns_zero(self):
        assert ceiling_multiple(0.0, 50.0) == pytest.approx(0.0)

    def test_negative_value_returns_zero(self):
        assert ceiling_multiple(-10.0, 50.0) == pytest.approx(0.0)

    def test_zero_multiple_returns_zero(self):
        assert ceiling_multiple(100.0, 0.0) == pytest.approx(0.0)

    def test_multiple_of_one(self):
        assert ceiling_multiple(3.7, 1.0) == pytest.approx(4.0)

    def test_float_near_boundary(self):
        # 50.000000001 should not round up to 100 due to float tolerance
        assert ceiling_multiple(50.0 + 1e-11, 50.0) == pytest.approx(50.0)

    def test_fractional_multiple(self):
        # CEILING(10, 2.5) = 10
        assert ceiling_multiple(10.0, 2.5) == pytest.approx(10.0)
        # CEILING(11, 2.5) = 12.5
        assert ceiling_multiple(11.0, 2.5) == pytest.approx(12.5)


# ---------------------------------------------------------------------------
# Stub factories
# ---------------------------------------------------------------------------

def _make_material(mat_num, name="Mat", product_type=ProductType.BULK_PRODUCT):
    return Material(
        material_number=mat_num,
        name=name,
        product_type=product_type,
        product_family="FAM",
        spc_product="",
        product_cluster="",
        product_name="",
    )


def _make_data(
    mat_num="MAT-1",
    periods=None,
    safety_stock=100.0,
    strategic_stock=0.0,
    lot_size=50.0,
    initial_stock=0.0,
    product_type=ProductType.BULK_PRODUCT,
    is_pap=False,
    pap_fraction=0.0,
    production_ceiling=50.0,
    purchase_moq=1.0,
    has_routing=True,
    is_bom_parent=True,  # when True, a dummy BOM item is added so engine sees mat_num as a parent
    lead_time=0,
    purchase_actuals=None,
    bom_items=None,
    in_purchase_sheet=False,
):
    material = _make_material(mat_num, product_type=product_type)
    ss = SafetyStockConfig(
        material_number=mat_num,
        safety_stock=safety_stock,
        lot_size=lot_size,
        strategic_stock=strategic_stock,
    )
    # InventoryEngine computes is_bom_parent from data.bom, not a method.
    # Provide a dummy child entry so the engine treats this material as a BOM parent.
    if bom_items is not None:
        bom = bom_items
    elif is_bom_parent:
        bom = [BOMItem(
            plant="NLI1", parent_material=mat_num, parent_name="Mat",
            component_material="CHILD-1", component_name="Child",
            quantity_per=1.0, is_coproduct=False,
        )]
    else:
        bom = []

    return SimpleNamespace(
        periods=periods or PERIODS,
        materials={mat_num: material},
        safety_stock={mat_num: ss},
        stock_levels={mat_num: initial_stock},
        bom=bom,
        purchase_sheet_materials={mat_num} if in_purchase_sheet else set(),
        purchase_actuals=purchase_actuals or {},
        is_purchased_and_produced=lambda m: is_pap if m == mat_num else False,
        get_purchase_fraction=lambda m: pap_fraction if m == mat_num else 0.0,
        get_production_ceiling=lambda m: production_ceiling if m == mat_num else 1.0,
        get_purchase_moq=lambda m: purchase_moq if m == mat_num else 1.0,
        get_all_routings=lambda m: [SimpleNamespace(work_center='MC1')] if (m == mat_num and has_routing) else [],
        get_lead_time=lambda m: lead_time if m == mat_num else 0,
    )


def _engine(data):
    return InventoryEngine(data)


def _calc(data, mat_num="MAT-1", forecast=None, dep_demand=None):
    eng = _engine(data)
    return eng.calculate_for_material(
        mat_num,
        forecast=forecast or {p: 0.0 for p in data.periods},
        dependent_demand_agg=dep_demand or {p: 0.0 for p in data.periods},
        dependent_demand_by_parent={},
    )


# ---------------------------------------------------------------------------
# Line 03: Total Demand
# ---------------------------------------------------------------------------

class TestTotalDemand:
    def test_sum_of_forecast_and_dependent(self):
        data = _make_data()
        result = _calc(
            data,
            forecast={"2025-01": 100.0, "2025-02": 200.0, "2025-03": 150.0},
            dep_demand={"2025-01": 10.0, "2025-02": 0.0, "2025-03": 20.0},
        )
        assert result['total_demand'] == {"2025-01": 110.0, "2025-02": 200.0, "2025-03": 170.0}

    def test_zero_demand_when_no_inputs(self):
        data = _make_data()
        result = _calc(data)
        assert all(v == 0.0 for v in result['total_demand'].values())

    def test_returns_empty_when_material_missing(self):
        data = _make_data()
        data.materials = {}
        result = _calc(data)
        assert result['total_demand'] == {}


# ---------------------------------------------------------------------------
# Line 06: Production Plan (produced material)
# ---------------------------------------------------------------------------

class TestProductionPlan:
    def test_produced_material_has_production_plan(self):
        data = _make_data(
            is_bom_parent=True, has_routing=True, in_purchase_sheet=False,
            initial_stock=0.0, safety_stock=0.0, production_ceiling=50.0,
        )
        result = _calc(data, forecast={"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0})
        assert result['production_plan'] is not None
        assert result['purchase_receipt'] is None
        # ceiling(100, 50) = 100
        assert result['production_plan']['2025-01'] == pytest.approx(100.0)

    def test_ceiling_applied_to_production(self):
        data = _make_data(
            is_bom_parent=True, has_routing=True, production_ceiling=50.0,
            safety_stock=0.0, initial_stock=0.0,
        )
        result = _calc(data, forecast={"2025-01": 60.0, "2025-02": 0.0, "2025-03": 0.0})
        # ceiling(60, 50) = 100
        assert result['production_plan']['2025-01'] == pytest.approx(100.0)

    def test_zero_demand_gives_zero_production(self):
        data = _make_data(is_bom_parent=True, has_routing=True, safety_stock=0.0, initial_stock=0.0)
        result = _calc(data)
        assert all(v == 0.0 for v in result['production_plan'].values())

    def test_initial_stock_reduces_production_need(self):
        # initial_stock=200, demand=100, safety_stock=0 → need=100-200+100=0 → no production
        data = _make_data(
            is_bom_parent=True, has_routing=True, safety_stock=0.0,
            initial_stock=200.0, production_ceiling=50.0,
        )
        result = _calc(data, forecast={"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0})
        assert result['production_plan']['2025-01'] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Line 06: Purchase Receipt (purchased material)
# ---------------------------------------------------------------------------

class TestPurchaseReceipt:
    def test_purchased_material_has_purchase_receipt(self):
        data = _make_data(
            product_type=ProductType.RAW_MATERIAL,
            is_bom_parent=False, has_routing=False, in_purchase_sheet=True,
            purchase_moq=1.0, safety_stock=0.0, initial_stock=0.0,
        )
        result = _calc(data, forecast={"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0})
        assert result['purchase_receipt'] is not None
        assert result['production_plan'] is None
        assert result['purchase_receipt']['2025-01'] == pytest.approx(100.0)

    def test_purchase_moq_ceiling_applied(self):
        data = _make_data(
            product_type=ProductType.RAW_MATERIAL,
            is_bom_parent=False, has_routing=False, in_purchase_sheet=True,
            purchase_moq=500.0, safety_stock=0.0, initial_stock=0.0,
        )
        result = _calc(data, forecast={"2025-01": 60.0, "2025-02": 0.0, "2025-03": 0.0})
        # ceiling(60, 500) = 500
        assert result['purchase_receipt']['2025-01'] == pytest.approx(500.0)

    def test_lead_time_delays_purchase(self):
        # lead_time=1: no purchase in period 0, purchases in period 1+ for period 1+ needs
        data = _make_data(
            product_type=ProductType.RAW_MATERIAL,
            is_bom_parent=False, has_routing=False, in_purchase_sheet=True,
            purchase_moq=1.0, safety_stock=0.0, initial_stock=0.0, lead_time=1,
        )
        result = _calc(data, forecast={"2025-01": 100.0, "2025-02": 100.0, "2025-03": 100.0})
        # First period (i=0) is frozen by lead_time=1: i < lead_time → 0
        assert result['purchase_receipt']['2025-01'] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Line 04: Inventory (running balance)
# ---------------------------------------------------------------------------

class TestInventory:
    def test_inventory_starts_from_initial_stock(self):
        data = _make_data(
            is_bom_parent=True, has_routing=True, safety_stock=0.0,
            initial_stock=500.0, production_ceiling=1.0,
        )
        result = _calc(data, forecast={"2025-01": 0.0, "2025-02": 0.0, "2025-03": 0.0})
        # No demand, no production needed → inventory stays at 500
        assert result['inventory']['2025-01'] == pytest.approx(500.0)

    def test_inventory_can_go_negative(self):
        # Purchased material with lead_time=1: first period is frozen (no purchase).
        # If demand > initial_stock in that first period, inventory goes negative.
        data = _make_data(
            product_type=ProductType.RAW_MATERIAL,
            is_bom_parent=False, has_routing=False, in_purchase_sheet=True,
            purchase_moq=1.0, safety_stock=0.0, initial_stock=50.0, lead_time=1,
        )
        result = _calc(data, forecast={"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0})
        # Period 0 is frozen (i < lead_time=1): no purchase → stock = 50 - 100 = -50
        assert result['inventory']['2025-01'] == pytest.approx(-50.0)

    def test_inventory_accumulates_correctly_over_periods(self):
        data = _make_data(
            is_bom_parent=True, has_routing=True, safety_stock=0.0,
            initial_stock=0.0, production_ceiling=1.0,
        )
        result = _calc(
            data,
            forecast={"2025-01": 100.0, "2025-02": 100.0, "2025-03": 100.0},
        )
        inv = result['inventory']
        # Each period: produce to cover demand, inventory ≈ 0
        for p in PERIODS:
            assert inv[p] == pytest.approx(0.0, abs=1.0)  # within 1 unit (ceiling rounding)

    def test_starting_stock_set_on_inventory_row(self):
        data = _make_data(initial_stock=250.0, safety_stock=0.0, is_bom_parent=True, has_routing=True)
        result = _calc(data)
        inv_row = next(r for r in result['rows'] if r.line_type == LineType.INVENTORY.value)
        assert inv_row.starting_stock == pytest.approx(250.0)


# ---------------------------------------------------------------------------
# Line 05: Minimum Target Stock
# ---------------------------------------------------------------------------

class TestMinTargetStock:
    def test_target_stock_equals_safety_plus_strategic(self):
        data = _make_data(safety_stock=200.0, strategic_stock=50.0)
        result = _calc(data)
        ts_row = next(r for r in result['rows'] if r.line_type == LineType.MIN_TARGET_STOCK.value)
        assert all(v == pytest.approx(250.0) for v in ts_row.values.values())

    def test_override_target_stock_values_used(self):
        data = _make_data(safety_stock=200.0)
        eng = _engine(data)
        overrides = {"2025-01": 999.0, "2025-02": 888.0, "2025-03": 777.0}
        result = eng.calculate_for_material(
            "MAT-1",
            forecast={p: 0.0 for p in PERIODS},
            dependent_demand_agg={p: 0.0 for p in PERIODS},
            dependent_demand_by_parent={},
            override_target_stock_values=overrides,
        )
        ts_row = next(r for r in result['rows'] if r.line_type == LineType.MIN_TARGET_STOCK.value)
        assert ts_row.values['2025-01'] == pytest.approx(999.0)
        assert ts_row.values['2025-02'] == pytest.approx(888.0)


# ---------------------------------------------------------------------------
# Line 07: Purchase Plan (lead time shift)
# ---------------------------------------------------------------------------

class TestPurchasePlan:
    def test_purchase_plan_shifts_by_lead_time(self):
        data = _make_data(
            product_type=ProductType.RAW_MATERIAL,
            is_bom_parent=False, has_routing=False, in_purchase_sheet=True,
            purchase_moq=1.0, safety_stock=0.0, initial_stock=0.0, lead_time=1,
        )
        # Force a purchase receipt by giving enough future demand
        result = _calc(data, forecast={"2025-01": 0.0, "2025-02": 100.0, "2025-03": 0.0})
        # Purchase receipt: period 1 (i=1) gets 100
        # Purchase plan: period 0 = receipt[period 1] = 100
        if result['purchase_plan']:
            assert result['purchase_plan']['2025-01'] == pytest.approx(result['purchase_receipt']['2025-02'])

    def test_produced_material_has_no_purchase_plan(self):
        data = _make_data(is_bom_parent=True, has_routing=True, in_purchase_sheet=False)
        result = _calc(data)
        assert result['purchase_plan'] is None


# ---------------------------------------------------------------------------
# Rows emitted
# ---------------------------------------------------------------------------

class TestRowsEmitted:
    def test_produced_material_emits_correct_line_types(self):
        data = _make_data(is_bom_parent=True, has_routing=True, in_purchase_sheet=False)
        result = _calc(data)
        line_types = {r.line_type for r in result['rows']}
        assert LineType.TOTAL_DEMAND.value in line_types
        assert LineType.MIN_TARGET_STOCK.value in line_types
        assert LineType.PRODUCTION_PLAN.value in line_types
        assert LineType.INVENTORY.value in line_types
        assert LineType.PURCHASE_RECEIPT.value not in line_types
        assert LineType.PURCHASE_PLAN.value not in line_types

    def test_purchased_material_emits_correct_line_types(self):
        data = _make_data(
            product_type=ProductType.RAW_MATERIAL,
            is_bom_parent=False, has_routing=False, in_purchase_sheet=True,
        )
        result = _calc(data)
        line_types = {r.line_type for r in result['rows']}
        assert LineType.TOTAL_DEMAND.value in line_types
        assert LineType.PURCHASE_RECEIPT.value in line_types
        assert LineType.PURCHASE_PLAN.value in line_types
        assert LineType.PRODUCTION_PLAN.value not in line_types
