"""Unit tests for InventoryQualityEngine.

All tests use synthetic data (SimpleNamespace stubs) — no golden fixture needed.
"""

from types import SimpleNamespace

import pytest

from modules.inventory_quality_engine import InventoryQualityEngine

pytestmark = pytest.mark.no_fixture

PERIODS = ["2025-01", "2025-02", "2025-03"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data(periods=None, materials=None, safety_stock=None, stock=None):
    return SimpleNamespace(
        periods=periods or PERIODS,
        materials=materials or {},
        safety_stock=safety_stock or {},
        stock=stock or {},
    )


def _make_row(mat_num, values, starting_stock=0.0, aux_column="10.0"):
    return SimpleNamespace(
        material_number=mat_num,
        aux_column=aux_column,
        values=values,
        starting_stock=starting_stock,
    )


def _make_ss(safety=100.0, strategic=50.0, lot=200.0):
    return SimpleNamespace(safety_stock=safety, strategic_stock=strategic, lot_size=lot)


def _engine(rows, periods=None, ss_cfg=None, mat_num="MAT-1"):
    data = _make_data(
        periods=periods or PERIODS,
        safety_stock={mat_num: ss_cfg} if ss_cfg else {},
    )
    value_results = {"04. Inventory": rows}
    return InventoryQualityEngine(data, {}, value_results)


# ---------------------------------------------------------------------------
# _categorize_period tests
# ---------------------------------------------------------------------------

class TestCategorizePeriod:
    def _eng(self):
        return _engine([], ss_cfg=_make_ss())

    def test_inventory_below_target_produces_understock(self):
        eng = self._eng()
        # target_val = 100*10 + 50*10 = 1500; inventory_value = 1000 < 1500
        result = eng._categorize_period(
            inventory_value=1000.0,
            safety_val=1000.0, strategic_val=500.0, target_val=1500.0, lot_val=2000.0,
            mat_num="MAT-1", period="2025-01",
        )
        assert result['under'] == pytest.approx(-500.0)
        assert result['normal'] == 0.0
        assert result['overstock'] == 0.0
        assert result['safety'] == pytest.approx(1000.0)
        assert result['strategic'] == pytest.approx(500.0)
        assert result['inventory'] == pytest.approx(1000.0)

    def test_inventory_between_target_and_target_plus_lot(self):
        eng = self._eng()
        # inventory = 1700, target = 1500, lot = 2000 → excess = 200, normal = min(200,2000) = 200, overstock = 0
        result = eng._categorize_period(
            inventory_value=1700.0,
            safety_val=1000.0, strategic_val=500.0, target_val=1500.0, lot_val=2000.0,
            mat_num="MAT-1", period="2025-01",
        )
        assert result['under'] == 0.0
        assert result['normal'] == pytest.approx(200.0)
        assert result['overstock'] == 0.0
        assert result['inventory'] == pytest.approx(1700.0)

    def test_inventory_above_target_plus_lot_produces_overstock(self):
        eng = self._eng()
        # inventory = 4000, target = 1500, lot = 2000 → excess = 2500, normal = 2000, overstock = 500
        result = eng._categorize_period(
            inventory_value=4000.0,
            safety_val=1000.0, strategic_val=500.0, target_val=1500.0, lot_val=2000.0,
            mat_num="MAT-1", period="2025-01",
        )
        assert result['under'] == 0.0
        assert result['normal'] == pytest.approx(2000.0)
        assert result['overstock'] == pytest.approx(500.0)
        assert result['inventory'] == pytest.approx(4000.0)

    def test_all_zero_config_whole_inventory_is_normal_or_overstock(self):
        eng = self._eng()
        # safety=0, strategic=0, lot=0, target=0 → excess = inventory, s_normal = min(inv, 0) = 0, s_overstock = inv
        result = eng._categorize_period(
            inventory_value=500.0,
            safety_val=0.0, strategic_val=0.0, target_val=0.0, lot_val=0.0,
            mat_num="MAT-1", period="2025-01",
        )
        assert result['under'] == 0.0
        assert result['safety'] == 0.0
        assert result['strategic'] == 0.0
        assert result['normal'] == 0.0
        assert result['overstock'] == pytest.approx(500.0)
        assert result['inventory'] == pytest.approx(500.0)

    def test_invariant_holds_for_all_cases(self):
        """Sum of categories must always equal inventory_value."""
        eng = self._eng()
        cases = [
            (500.0,  1000.0, 500.0, 1500.0, 2000.0),  # understock
            (1700.0, 1000.0, 500.0, 1500.0, 2000.0),  # normal
            (4000.0, 1000.0, 500.0, 1500.0, 2000.0),  # overstock
            (0.0,    0.0,    0.0,   0.0,    0.0),      # all zero
            (1500.0, 1000.0, 500.0, 1500.0, 2000.0),  # exactly at target
        ]
        for inv, s, st, t, lot in cases:
            r = eng._categorize_period(inv, s, st, t, lot, "MAT-1", "p")
            total = r['under'] + r['safety'] + r['strategic'] + r['normal'] + r['overstock']
            assert abs(total - r['inventory']) < 0.01, (
                f"Invariant broken for inventory={inv}: {total} != {inv}"
            )

    def test_zero_inventory_all_understock(self):
        eng = self._eng()
        result = eng._categorize_period(
            inventory_value=0.0,
            safety_val=1000.0, strategic_val=500.0, target_val=1500.0, lot_val=2000.0,
            mat_num="MAT-1", period="2025-01",
        )
        assert result['under'] == pytest.approx(-1500.0)
        assert result['overstock'] == 0.0


# ---------------------------------------------------------------------------
# _process_material tests
# ---------------------------------------------------------------------------

class TestProcessMaterial:
    def test_starting_stock_key_present_in_periods_data(self):
        """Regression: starting stock must be categorized (bug was it was missing)."""
        ss = _make_ss(safety=10.0, strategic=5.0, lot=20.0)
        data = _make_data(safety_stock={"MAT-1": ss})
        row = _make_row("MAT-1", {"2025-01": 150.0, "2025-02": 200.0, "2025-03": 100.0},
                        starting_stock=80.0, aux_column="1.0")
        eng = InventoryQualityEngine(data, {}, {"04. Inventory": [row]})

        result = eng._process_material(row)

        assert 'Starting stock' in result['periods'], (
            "'Starting stock' key missing from periods — regression of starting stock bug"
        )
        ss_cat = result['periods']['Starting stock']
        assert set(ss_cat.keys()) == {'under', 'safety', 'strategic', 'normal', 'overstock', 'inventory'}

    def test_starting_stock_in_overstock_by_period(self):
        ss = _make_ss(safety=10.0, strategic=5.0, lot=20.0)
        data = _make_data(safety_stock={"MAT-1": ss})
        row = _make_row("MAT-1", {"2025-01": 100.0, "2025-02": 100.0, "2025-03": 100.0},
                        starting_stock=200.0, aux_column="1.0")
        eng = InventoryQualityEngine(data, {}, {"04. Inventory": [row]})

        result = eng._process_material(row)

        expected_overstock = result['periods']['Starting stock']['overstock']
        assert result['overstock_by_period']['Starting stock'] == pytest.approx(expected_overstock)

    def test_zero_starting_stock_no_keyerror(self):
        ss = _make_ss(safety=10.0, strategic=5.0, lot=20.0)
        data = _make_data(safety_stock={"MAT-1": ss})
        row = _make_row("MAT-1", {"2025-01": 100.0, "2025-02": 100.0, "2025-03": 100.0},
                        starting_stock=0.0, aux_column="1.0")
        eng = InventoryQualityEngine(data, {}, {"04. Inventory": [row]})

        result = eng._process_material(row)

        assert 'Starting stock' in result['periods']
        assert result['periods']['Starting stock']['inventory'] == pytest.approx(0.0)

    def test_no_safety_stock_config_treated_as_zero(self):
        data = _make_data(safety_stock={})  # no config for MAT-1
        row = _make_row("MAT-1", {"2025-01": 500.0, "2025-02": 500.0, "2025-03": 500.0},
                        starting_stock=0.0, aux_column="1.0")
        eng = InventoryQualityEngine(data, {}, {"04. Inventory": [row]})

        # Should not raise; with target=0 all inventory becomes overstock
        result = eng._process_material(row)

        for p in PERIODS:
            assert result['periods'][p]['overstock'] == pytest.approx(500.0)
            assert result['periods'][p]['safety'] == 0.0


# ---------------------------------------------------------------------------
# calculate() tests
# ---------------------------------------------------------------------------

class TestCalculate:
    def _simple_engine(self, n_materials=1, starting_stock=0.0):
        ss = _make_ss(safety=10.0, strategic=5.0, lot=20.0)
        rows = [
            _make_row(f"MAT-{i}", {p: 100.0 for p in PERIODS},
                      starting_stock=starting_stock, aux_column="1.0")
            for i in range(n_materials)
        ]
        safety_stock = {f"MAT-{i}": ss for i in range(n_materials)}
        data = _make_data(safety_stock=safety_stock)
        return InventoryQualityEngine(data, {}, {"04. Inventory": rows})

    def test_period_totals_keys_match_data_periods(self):
        eng = self._simple_engine()
        result = eng.calculate()
        assert set(result['period_totals'].keys()) == set(PERIODS)

    def test_period_totals_exclude_starting_stock(self):
        eng = self._simple_engine(starting_stock=500.0)
        result = eng.calculate()
        assert 'Starting stock' not in result['period_totals']

    def test_total_overstock_equals_sum_of_per_material(self):
        eng = self._simple_engine(n_materials=3, starting_stock=0.0)
        result = eng.calculate()
        expected = sum(m['total_overstock'] for m in result['per_material'])
        assert result['total_overstock'] == pytest.approx(expected, abs=0.05)

    def test_empty_inventory_rows_returns_empty_per_material(self):
        data = _make_data()
        eng = InventoryQualityEngine(data, {}, {"04. Inventory": []})
        result = eng.calculate()
        assert result['per_material'] == []
        assert result['total_overstock'] == 0.0
        assert result['top_10_overstocks'] == []

    def test_top_10_sorted_by_starting_overstock_descending(self):
        ss = _make_ss(safety=0.0, strategic=0.0, lot=0.0)
        # Create 12 materials with known starting_stock values; overstock = starting_stock (lot=0 → all overstock)
        rows = [
            _make_row(f"MAT-{i}", {p: 0.0 for p in PERIODS},
                      starting_stock=float(i * 100), aux_column="1.0")
            for i in range(12)
        ]
        safety_stock = {f"MAT-{i}": ss for i in range(12)}
        data = _make_data(safety_stock=safety_stock)
        eng = InventoryQualityEngine(data, {}, {"04. Inventory": rows})

        result = eng.calculate()

        top10 = result['top_10_overstocks']
        assert len(top10) == 10
        starting_overstocks = [m['starting_overstock'] for m in top10]
        assert starting_overstocks == sorted(starting_overstocks, reverse=True)
        # MAT-11 (1100.0) should be first, MAT-2 (200.0) should be last
        assert top10[0]['material_number'] == "MAT-11"
        assert top10[-1]['material_number'] == "MAT-2"

    def test_period_totals_sum_correctly_across_materials(self):
        ss = _make_ss(safety=0.0, strategic=0.0, lot=0.0)
        # Two materials with 500.0 each → overstock per period = 1000.0
        rows = [
            _make_row(f"MAT-{i}", {p: 500.0 for p in PERIODS}, aux_column="1.0")
            for i in range(2)
        ]
        data = _make_data(safety_stock={f"MAT-{i}": ss for i in range(2)})
        eng = InventoryQualityEngine(data, {}, {"04. Inventory": rows})

        result = eng.calculate()

        for p in PERIODS:
            assert result['period_totals'][p]['overstock'] == pytest.approx(1000.0, abs=0.05)
            assert result['period_totals'][p]['inventory'] == pytest.approx(1000.0, abs=0.05)


# ---------------------------------------------------------------------------
# _get_unit_value fallback chain tests
# ---------------------------------------------------------------------------

class TestGetUnitValue:
    def _eng_with_row(self, mat_num, aux_column, stock=None, materials=None):
        row = _make_row(mat_num, {}, aux_column=aux_column)
        data = _make_data(stock=stock or {}, materials=materials or {})
        return InventoryQualityEngine(data, {}, {"04. Inventory": [row]})

    def test_uses_aux_column_first(self):
        eng = self._eng_with_row("MAT-1", "42.5")
        assert eng._get_unit_value("MAT-1") == pytest.approx(42.5)

    def test_falls_back_to_stock_sheet_when_aux_unparseable(self):
        stock = {"MAT-1": {"Total Stock": 100.0, "Total Value": 3000.0}}
        eng = self._eng_with_row("MAT-1", "not-a-number", stock=stock)
        assert eng._get_unit_value("MAT-1") == pytest.approx(30.0)

    def test_falls_back_to_material_master_when_no_stock_sheet(self):
        mat = SimpleNamespace(default_inventory_value=15.0)
        eng = self._eng_with_row("MAT-1", "bad", materials={"MAT-1": mat})
        assert eng._get_unit_value("MAT-1") == pytest.approx(15.0)

    def test_returns_zero_when_all_fallbacks_missing(self):
        eng = self._eng_with_row("MAT-1", "bad")
        assert eng._get_unit_value("MAT-1") == pytest.approx(0.0)

    def test_stock_sheet_skipped_when_total_stock_is_zero(self):
        stock = {"MAT-1": {"Total Stock": 0, "Total Value": 5000.0}}
        mat = SimpleNamespace(default_inventory_value=7.0)
        eng = self._eng_with_row("MAT-1", "bad", stock=stock, materials={"MAT-1": mat})
        # Total Stock = 0 → skip stock sheet, fall to material master
        assert eng._get_unit_value("MAT-1") == pytest.approx(7.0)
