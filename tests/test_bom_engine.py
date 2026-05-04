"""Unit tests for BOMEngine.

All tests use synthetic data — no golden fixture needed.
"""

from types import SimpleNamespace
from collections import defaultdict

import pytest

from modules.bom_engine import BOMEngine
from modules.models import Material, ProductType, BOMItem, LineType

pytestmark = pytest.mark.no_fixture

PERIODS = ["2025-01", "2025-02", "2025-03"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mat(mat_num, name="Mat"):
    return Material(
        material_number=mat_num, name=name,
        product_type=ProductType.BULK_PRODUCT, product_family="FAM",
        spc_product="", product_cluster="", product_name="",
    )


def _bom_item(parent, child, qty_per=1.0, is_coproduct=False):
    return BOMItem(
        plant="NLI1", parent_material=parent, parent_name="Parent",
        component_material=child, component_name="Child",
        quantity_per=qty_per, is_coproduct=is_coproduct,
    )


def _make_data(bom_items, materials=None, periods=None):
    all_mats = {}
    for b in bom_items:
        all_mats[b.parent_material] = _mat(b.parent_material, "Parent")
        all_mats[b.component_material] = _mat(b.component_material, "Child")
    if materials:
        all_mats.update(materials)
    return SimpleNamespace(
        periods=periods or PERIODS,
        bom=bom_items,
        materials=all_mats,
    )


def _engine(data):
    return BOMEngine(data)


# ---------------------------------------------------------------------------
# compute_dependent_requirements
# ---------------------------------------------------------------------------

class TestComputeDependentRequirements:
    def test_single_child_scaled_by_qty_per(self):
        data = _make_data([_bom_item("PARENT", "CHILD", qty_per=2.0)])
        eng = _engine(data)
        plan = {"2025-01": 100.0, "2025-02": 50.0, "2025-03": 0.0}
        result = eng.compute_dependent_requirements("PARENT", plan)
        assert result["CHILD"]["2025-01"] == pytest.approx(200.0)
        assert result["CHILD"]["2025-02"] == pytest.approx(100.0)
        assert result["CHILD"]["2025-03"] == pytest.approx(0.0)

    def test_multiple_children(self):
        data = _make_data([
            _bom_item("PARENT", "CHILD-A", qty_per=1.0),
            _bom_item("PARENT", "CHILD-B", qty_per=3.0),
        ])
        eng = _engine(data)
        plan = {"2025-01": 10.0, "2025-02": 0.0, "2025-03": 0.0}
        result = eng.compute_dependent_requirements("PARENT", plan)
        assert result["CHILD-A"]["2025-01"] == pytest.approx(10.0)
        assert result["CHILD-B"]["2025-01"] == pytest.approx(30.0)

    def test_coproduct_produces_negative_demand(self):
        # Coproducts have negative qty_per — they reduce demand on the child
        data = _make_data([_bom_item("PARENT", "COPROD", qty_per=-0.5, is_coproduct=True)])
        eng = _engine(data)
        plan = {"2025-01": 100.0, "2025-02": 0.0, "2025-03": 0.0}
        result = eng.compute_dependent_requirements("PARENT", plan)
        assert result["COPROD"]["2025-01"] == pytest.approx(-50.0)

    def test_no_children_returns_empty(self):
        data = _make_data([_bom_item("OTHER", "CHILD")])
        eng = _engine(data)
        result = eng.compute_dependent_requirements("PARENT", {"2025-01": 100.0})
        assert result == {}

    def test_zero_production_gives_zero_demand(self):
        data = _make_data([_bom_item("PARENT", "CHILD", qty_per=5.0)])
        eng = _engine(data)
        plan = {p: 0.0 for p in PERIODS}
        result = eng.compute_dependent_requirements("PARENT", plan)
        assert all(v == 0.0 for v in result["CHILD"].values())

    def test_fractional_qty_per(self):
        data = _make_data([_bom_item("PARENT", "CHILD", qty_per=1.5)])
        eng = _engine(data)
        plan = {"2025-01": 10.0, "2025-02": 0.0, "2025-03": 0.0}
        result = eng.compute_dependent_requirements("PARENT", plan)
        assert result["CHILD"]["2025-01"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# create_dependent_demand_rows (Line 02)
# ---------------------------------------------------------------------------

class TestCreateDependentDemandRows:
    def test_one_row_per_parent(self):
        data = _make_data([_bom_item("P1", "CHILD"), _bom_item("P2", "CHILD")])
        eng = _engine(data)
        demand_by_parent = {
            "P1": {"2025-01": 50.0, "2025-02": 0.0, "2025-03": 0.0},
            "P2": {"2025-01": 30.0, "2025-02": 0.0, "2025-03": 0.0},
        }
        rows = eng.create_dependent_demand_rows("CHILD", demand_by_parent)
        assert len(rows) == 2

    def test_row_aux_column_is_parent_material_number(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        rows = eng.create_dependent_demand_rows("CHILD", {"PARENT": {"2025-01": 10.0}})
        assert rows[0].aux_column == "PARENT"

    def test_row_line_type_is_dependent_demand(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        rows = eng.create_dependent_demand_rows("CHILD", {"PARENT": {"2025-01": 10.0}})
        assert rows[0].line_type == LineType.DEPENDENT_DEMAND.value

    def test_row_values_match_demand_data(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        demand = {"2025-01": 42.0, "2025-02": 99.0, "2025-03": 0.0}
        rows = eng.create_dependent_demand_rows("CHILD", {"PARENT": demand})
        assert rows[0].values == demand

    def test_row_generated_even_for_all_zero_demand(self):
        # VBA generates rows even if all zeros
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        rows = eng.create_dependent_demand_rows("CHILD", {"PARENT": {"2025-01": 0.0}})
        assert len(rows) == 1

    def test_material_number_is_child(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        rows = eng.create_dependent_demand_rows("CHILD", {"PARENT": {"2025-01": 10.0}})
        assert rows[0].material_number == "CHILD"


# ---------------------------------------------------------------------------
# create_dependent_requirements_rows (Line 08)
# ---------------------------------------------------------------------------

class TestCreateDependentRequirementsRows:
    def test_one_row_per_child(self):
        data = _make_data([
            _bom_item("PARENT", "CHILD-A", qty_per=1.0),
            _bom_item("PARENT", "CHILD-B", qty_per=2.0),
        ])
        eng = _engine(data)
        children_demand = {
            "CHILD-A": {"2025-01": 10.0},
            "CHILD-B": {"2025-01": 20.0},
        }
        rows = eng.create_dependent_requirements_rows("PARENT", children_demand)
        assert len(rows) == 2

    def test_material_number_is_parent(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        rows = eng.create_dependent_requirements_rows("PARENT", {"CHILD": {"2025-01": 10.0}})
        assert rows[0].material_number == "PARENT"

    def test_aux_column_is_child_material(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        rows = eng.create_dependent_requirements_rows("PARENT", {"CHILD": {"2025-01": 10.0}})
        assert rows[0].aux_column == "CHILD"

    def test_aux2_column_is_qty_per(self):
        data = _make_data([_bom_item("PARENT", "CHILD", qty_per=1.5)])
        eng = _engine(data)
        rows = eng.create_dependent_requirements_rows("PARENT", {"CHILD": {"2025-01": 10.0}})
        assert rows[0].aux_2_column == "1.5"

    def test_line_type_is_dependent_requirements(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        rows = eng.create_dependent_requirements_rows("PARENT", {"CHILD": {"2025-01": 10.0}})
        assert rows[0].line_type == LineType.DEPENDENT_REQUIREMENTS.value

    def test_values_match_demand(self):
        data = _make_data([_bom_item("PARENT", "CHILD")])
        eng = _engine(data)
        demand = {"2025-01": 77.0, "2025-02": 88.0}
        rows = eng.create_dependent_requirements_rows("PARENT", {"CHILD": demand})
        assert rows[0].values == demand


# ---------------------------------------------------------------------------
# parent_children dict built in __init__
# ---------------------------------------------------------------------------

class TestParentChildrenInit:
    def test_coproducts_included_in_parent_children(self):
        data = _make_data([_bom_item("PARENT", "COPROD", qty_per=-0.5, is_coproduct=True)])
        eng = _engine(data)
        # Coproducts ARE included (they create negative dependent demand)
        assert "COPROD" in [child for child, _ in eng.parent_children["PARENT"]]

    def test_multiple_bom_items_grouped_under_parent(self):
        data = _make_data([
            _bom_item("PARENT", "C1"),
            _bom_item("PARENT", "C2"),
        ])
        eng = _engine(data)
        children = [child for child, _ in eng.parent_children["PARENT"]]
        assert "C1" in children
        assert "C2" in children

    def test_different_parents_dont_cross_contaminate(self):
        data = _make_data([
            _bom_item("P1", "C1"),
            _bom_item("P2", "C2"),
        ])
        eng = _engine(data)
        assert [c for c, _ in eng.parent_children.get("P1", [])] == ["C1"]
        assert [c for c, _ in eng.parent_children.get("P2", [])] == ["C2"]
