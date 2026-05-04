"""Unit tests for MoMComparisonEngine.

All tests use synthetic pandas DataFrames — no golden fixture needed.
"""

import pandas as pd
import numpy as np
import pytest

from modules.mom_comparison_engine import MoMComparisonEngine

pytestmark = pytest.mark.no_fixture

PERIODS = ["2025-01", "2025-02", "2025-03"]
INV_LINE = "04. Inventory"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inv_df(materials, periods=None, line_type=INV_LINE):
    """Build a minimal DataFrame with inventory rows."""
    ps = periods or PERIODS
    rows = []
    for mat_num, values in materials.items():
        row = {
            "Material number": mat_num,
            "Material name": f"Mat {mat_num}",
            "Product type": "Bulk Product",
            "Line type": line_type,
        }
        for p, v in zip(ps, values):
            row[p] = v
        rows.append(row)
    return pd.DataFrame(rows)


def _sequential_df(materials, periods=None):
    """Build a DataFrame suitable for calculate_sequential()."""
    return _inv_df(materials, periods=periods)


# ---------------------------------------------------------------------------
# _extract_inventory (static helper)
# ---------------------------------------------------------------------------

class TestExtractInventory:
    def test_filters_to_inventory_line(self):
        df = _inv_df({"MAT-1": [100.0, 200.0, 300.0]})
        df2 = df.copy()
        df2["Line type"] = "01. Demand forecast"
        combined = pd.concat([df, df2], ignore_index=True)
        result = MoMComparisonEngine._extract_inventory(combined)
        assert list(result["Line type"].unique()) == [INV_LINE]
        assert len(result) == 1

    def test_returns_empty_when_no_line_type_column(self):
        df = pd.DataFrame({"Material number": ["MAT-1"], "2025-01": [100.0]})
        result = MoMComparisonEngine._extract_inventory(df)
        assert result.empty

    def test_returns_empty_when_no_inventory_rows(self):
        df = _inv_df({"MAT-1": [100.0, 0.0, 0.0]})
        df["Line type"] = "01. Demand forecast"
        result = MoMComparisonEngine._extract_inventory(df)
        assert result.empty


# ---------------------------------------------------------------------------
# _period_columns (static helper)
# ---------------------------------------------------------------------------

class TestPeriodColumns:
    def test_returns_only_period_columns(self):
        df = _inv_df({"MAT-1": [100.0, 200.0, 300.0]})
        df["Not a period"] = 0
        df["short"] = 0     # too short — not matched
        df["2025-AB"] = 0   # non-numeric month — not matched
        periods = MoMComparisonEngine._period_columns(df)
        assert set(periods) == {"2025-01", "2025-02", "2025-03"}

    def test_sorted_lexicographically(self):
        df = _inv_df({"MAT-1": [1.0, 2.0, 3.0]}, periods=["2025-03", "2025-01", "2025-02"])
        periods = MoMComparisonEngine._period_columns(df)
        assert periods == ["2025-01", "2025-02", "2025-03"]

    def test_empty_df_returns_empty(self):
        assert MoMComparisonEngine._period_columns(pd.DataFrame()) == []


# ---------------------------------------------------------------------------
# calculate_sequential (main sequential path)
# ---------------------------------------------------------------------------

class TestCalculateSequential:
    def test_returns_available_true_with_valid_data(self):
        df = _sequential_df({"MAT-1": [100.0, 200.0, 300.0]})
        result = MoMComparisonEngine.calculate_sequential(df)
        assert result["available"] is True

    def test_returns_available_false_when_no_inventory_rows(self):
        df = pd.DataFrame({"Material number": ["MAT-1"], "Line type": ["01. Demand forecast"]})
        result = MoMComparisonEngine.calculate_sequential(df)
        assert result["available"] is False

    def test_returns_available_false_with_only_one_period(self):
        df = _inv_df({"MAT-1": [100.0]}, periods=["2025-01"])
        result = MoMComparisonEngine.calculate_sequential(df)
        assert result["available"] is False

    def test_summary_has_one_entry_per_material(self):
        df = _sequential_df({"MAT-A": [100.0, 200.0], "MAT-B": [50.0, 75.0]},
                            periods=["2025-01", "2025-02"])
        result = MoMComparisonEngine.calculate_sequential(df, num_months=1)
        assert len(result["summary"]) == 2

    def test_summary_delta_computed_correctly(self):
        df = _sequential_df({"MAT-1": [100.0, 150.0, 200.0]})
        result = MoMComparisonEngine.calculate_sequential(df, num_months=2)
        s = result["summary"][0]
        assert s["start"] == pytest.approx(100.0)
        assert s["end"] == pytest.approx(200.0)
        assert s["delta"] == pytest.approx(100.0)

    def test_delta_pct_is_none_when_start_is_zero(self):
        df = _sequential_df({"MAT-1": [0.0, 100.0, 200.0]})
        result = MoMComparisonEngine.calculate_sequential(df, num_months=2)
        assert result["summary"][0]["delta_pct"] is None

    def test_delta_pct_computed_when_start_nonzero(self):
        df = _sequential_df({"MAT-1": [100.0, 150.0, 200.0]})
        result = MoMComparisonEngine.calculate_sequential(df, num_months=2)
        # delta = 100, start = 100 → pct = 100%
        assert result["summary"][0]["delta_pct"] == pytest.approx(100.0)

    def test_num_transitions_equals_num_months(self):
        df = _sequential_df({"MAT-1": [100.0, 200.0, 300.0]})
        result = MoMComparisonEngine.calculate_sequential(df, num_months=2)
        assert result["num_transitions"] == 2

    def test_transitions_structure(self):
        df = _sequential_df({"MAT-1": [100.0, 200.0, 300.0]})
        result = MoMComparisonEngine.calculate_sequential(df, num_months=2)
        t = result["transitions"][0]
        assert "from_period" in t
        assert "to_period" in t
        assert "rows" in t
        assert t["rows"][0]["delta"] == pytest.approx(100.0)

    def test_num_months_capped_at_available_periods(self):
        df = _sequential_df({"MAT-1": [10.0, 20.0]}, periods=["2025-01", "2025-02"])
        result = MoMComparisonEngine.calculate_sequential(df, num_months=99)
        # Only 2 periods available → 1 transition
        assert result["num_transitions"] == 1

    def test_scatter_colors_green_for_positive_to_positive(self):
        df = _sequential_df({"MAT-1": [100.0, 200.0]}, periods=["2025-01", "2025-02"])
        result = MoMComparisonEngine.calculate_sequential(df, num_months=1)
        assert result["scatter"]["colors"][0] == "C6EFCE"

    def test_scatter_colors_red_for_negative_to_positive(self):
        df = _sequential_df({"MAT-1": [-100.0, 200.0]}, periods=["2025-01", "2025-02"])
        result = MoMComparisonEngine.calculate_sequential(df, num_months=1)
        assert result["scatter"]["colors"][0] == "FFC7CE"

    def test_scatter_colors_orange_for_positive_to_negative(self):
        df = _sequential_df({"MAT-1": [100.0, -50.0]}, periods=["2025-01", "2025-02"])
        result = MoMComparisonEngine.calculate_sequential(df, num_months=1)
        assert result["scatter"]["colors"][0] == "FFC896"

    def test_material_count_matches_rows(self):
        df = _sequential_df({"A": [1.0, 2.0], "B": [3.0, 4.0], "C": [5.0, 6.0]},
                            periods=["2025-01", "2025-02"])
        result = MoMComparisonEngine.calculate_sequential(df, num_months=1)
        assert result["material_count"] == 3


# ---------------------------------------------------------------------------
# calculate() — two-cycle comparison
# ---------------------------------------------------------------------------

class TestCalculate:
    def test_returns_empty_df_when_current_empty(self):
        prev = _inv_df({"MAT-1": [100.0, 200.0]}, periods=["2025-01", "2025-02"])
        eng = MoMComparisonEngine(pd.DataFrame(), prev)
        assert eng.calculate().empty

    def test_returns_empty_df_when_previous_empty(self):
        cur = _inv_df({"MAT-1": [100.0, 200.0]}, periods=["2025-01", "2025-02"])
        eng = MoMComparisonEngine(cur)
        assert eng.calculate().empty

    def test_returns_empty_df_when_no_common_periods(self):
        cur = _inv_df({"MAT-1": [100.0]}, periods=["2025-01"])
        prev = _inv_df({"MAT-1": [200.0]}, periods=["2025-02"])
        eng = MoMComparisonEngine(cur, prev)
        assert eng.calculate().empty

    def test_delta_is_current_minus_previous(self):
        cur = _inv_df({"MAT-1": [150.0, 0.0, 0.0]})
        prev = _inv_df({"MAT-1": [100.0, 0.0, 0.0]})
        eng = MoMComparisonEngine(cur, prev)
        df = eng.calculate()
        row = df[df["Period"] == "2025-01"].iloc[0]
        assert row["Delta"] == pytest.approx(50.0)

    def test_delta_pct_correct(self):
        cur = _inv_df({"MAT-1": [150.0, 0.0, 0.0]})
        prev = _inv_df({"MAT-1": [100.0, 0.0, 0.0]})
        eng = MoMComparisonEngine(cur, prev)
        df = eng.calculate()
        row = df[df["Period"] == "2025-01"].iloc[0]
        assert row["Delta %"] == pytest.approx(50.0)

    def test_zero_previous_gives_inf_delta_pct_when_delta_nonzero(self):
        cur = _inv_df({"MAT-1": [50.0, 0.0, 0.0]})
        prev = _inv_df({"MAT-1": [0.0, 0.0, 0.0]})
        eng = MoMComparisonEngine(cur, prev)
        df = eng.calculate()
        row = df[df["Period"] == "2025-01"].iloc[0]
        assert np.isinf(row["Delta %"])

    def test_only_common_materials_appear(self):
        cur = _inv_df({"MAT-1": [100.0, 0.0, 0.0], "MAT-ONLY-CUR": [50.0, 0.0, 0.0]})
        prev = _inv_df({"MAT-1": [80.0, 0.0, 0.0], "MAT-ONLY-PREV": [30.0, 0.0, 0.0]})
        eng = MoMComparisonEngine(cur, prev)
        df = eng.calculate()
        mat_nums = set(df["Material number"].unique())
        assert "MAT-1" in mat_nums
        assert "MAT-ONLY-CUR" not in mat_nums
        assert "MAT-ONLY-PREV" not in mat_nums

    def test_output_has_expected_columns(self):
        cur = _inv_df({"MAT-1": [100.0, 200.0, 300.0]})
        prev = _inv_df({"MAT-1": [80.0, 160.0, 240.0]})
        eng = MoMComparisonEngine(cur, prev)
        df = eng.calculate()
        for col in ["Material number", "Material name", "Period",
                    "Current Inventory", "Previous Inventory", "Delta", "Delta %"]:
            assert col in df.columns


# ---------------------------------------------------------------------------
# create_scatter_data
# ---------------------------------------------------------------------------

class TestCreateScatterData:
    def test_returns_empty_when_no_previous(self):
        cur = _inv_df({"MAT-1": [100.0, 200.0, 300.0]})
        eng = MoMComparisonEngine(cur)
        result = eng.create_scatter_data()
        assert result == {"materials": [], "current": [], "previous": [], "colors": []}

    def test_materials_match_compared_materials(self):
        cur = _inv_df({"MAT-1": [100.0], "MAT-2": [50.0]}, periods=["2025-01"])
        prev = _inv_df({"MAT-1": [80.0], "MAT-2": [40.0]}, periods=["2025-01"])
        eng = MoMComparisonEngine(cur, prev)
        result = eng.create_scatter_data()
        assert set(result["materials"]) == {"MAT-1", "MAT-2"}

    def test_colors_list_length_matches_materials(self):
        cur = _inv_df({"A": [10.0], "B": [20.0], "C": [30.0]}, periods=["2025-01"])
        prev = _inv_df({"A": [5.0], "B": [15.0], "C": [25.0]}, periods=["2025-01"])
        eng = MoMComparisonEngine(cur, prev)
        result = eng.create_scatter_data()
        assert len(result["colors"]) == len(result["materials"])

    def test_green_color_for_positive_current_and_previous(self):
        cur = _inv_df({"MAT-1": [200.0]}, periods=["2025-01"])
        prev = _inv_df({"MAT-1": [100.0]}, periods=["2025-01"])
        eng = MoMComparisonEngine(cur, prev)
        result = eng.create_scatter_data()
        assert result["colors"][0] == "C6EFCE"

    def test_red_color_for_positive_current_negative_previous(self):
        cur = _inv_df({"MAT-1": [200.0]}, periods=["2025-01"])
        prev = _inv_df({"MAT-1": [-100.0]}, periods=["2025-01"])
        eng = MoMComparisonEngine(cur, prev)
        result = eng.create_scatter_data()
        assert result["colors"][0] == "FFC7CE"
