"""Unit tests for ForecastEngine.

All tests use synthetic data — no golden fixture needed.
"""

from types import SimpleNamespace

import pytest

from modules.forecast_engine import ForecastEngine
from modules.models import Material, ProductType, LineType

pytestmark = pytest.mark.no_fixture

PERIODS = ["2026-01", "2026-02", "2026-03"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_material(mat_num, name="Mat"):
    return Material(
        material_number=mat_num, name=name,
        product_type=ProductType.BULK_PRODUCT, product_family="FAM",
        spc_product="", product_cluster="", product_name="",
    )


def _make_data(forecasts, periods=None, first_period=None, materials=None):
    mats = materials or {m: _make_material(m) for m in forecasts}
    return SimpleNamespace(
        periods=PERIODS if periods is None else periods,
        forecasts=forecasts,
        materials=mats,
        forecast_first_period=first_period,
    )


def _engine(data, months_actuals=3, months_forecast=3):
    return ForecastEngine(data, months_actuals=months_actuals, months_forecast=months_forecast)


# ---------------------------------------------------------------------------
# _offset_period (static helper)
# ---------------------------------------------------------------------------

class TestOffsetPeriod:
    def test_zero_offset(self):
        assert ForecastEngine._offset_period("2025-12", 0) == "2025-12"

    def test_positive_offset_within_year(self):
        assert ForecastEngine._offset_period("2025-01", 2) == "2025-03"

    def test_positive_offset_crossing_year(self):
        assert ForecastEngine._offset_period("2025-11", 2) == "2026-01"

    def test_large_offset(self):
        assert ForecastEngine._offset_period("2025-01", 12) == "2026-01"

    def test_month_zero_padding(self):
        result = ForecastEngine._offset_period("2025-01", 0)
        assert result[5:] == "01"  # month is zero-padded


# ---------------------------------------------------------------------------
# _calculate_aux_columns
# ---------------------------------------------------------------------------

class TestCalculateAuxColumns:
    def _build(self, first_period, months_actuals, months_forecast, forecast_data):
        """Construct a minimal engine and call _calculate_aux_columns."""
        mat_num = "MAT-1"
        data = _make_data(
            forecasts={mat_num: forecast_data},
            periods=[ForecastEngine._offset_period(
                ForecastEngine._offset_period(first_period, months_actuals + 1), i
            ) for i in range(months_forecast)],
            first_period=first_period,
        )
        eng = _engine(data, months_actuals=months_actuals, months_forecast=months_forecast)
        return eng._calculate_aux_columns(mat_num, forecast_data)

    def test_aux1_averages_actuals_period(self):
        # first_period="2024-01", months_actuals=3 → aux1 avg of 2024-01, 2024-02, 2024-03
        fc = {"2024-01": 100.0, "2024-02": 200.0, "2024-03": 300.0,
              "2024-05": 150.0, "2024-06": 150.0, "2024-07": 150.0}
        aux1, aux2, vals = self._build("2024-01", months_actuals=3, months_forecast=3, forecast_data=fc)
        assert float(aux1) == pytest.approx(200.0)  # (100+200+300)/3

    def test_aux2_averages_forecast_period(self):
        fc = {"2024-01": 100.0, "2024-02": 200.0, "2024-03": 300.0,
              "2024-05": 120.0, "2024-06": 180.0, "2024-07": 240.0}
        aux1, aux2, vals = self._build("2024-01", months_actuals=3, months_forecast=3, forecast_data=fc)
        assert float(aux2) == pytest.approx(180.0)  # (120+180+240)/3

    def test_vals_mapped_to_planning_periods(self):
        # Planning periods align to aux2_anchor offset
        fc = {"2024-01": 0.0, "2024-02": 0.0, "2024-03": 0.0,
              "2024-05": 42.0, "2024-06": 99.0, "2024-07": 0.0}
        aux1, aux2, vals = self._build("2024-01", months_actuals=3, months_forecast=3, forecast_data=fc)
        # vals[0] → planning period 0 = forecast_data[aux2_anchor+0] = forecast_data["2024-05"]
        assert vals[0][1] == pytest.approx(42.0)
        assert vals[1][1] == pytest.approx(99.0)

    def test_missing_periods_skipped_in_avg(self):
        # aux1 only counts periods that exist in forecast_data
        fc = {"2024-01": 100.0, "2024-03": 300.0,  # 2024-02 missing
              "2024-05": 150.0, "2024-06": 150.0, "2024-07": 150.0}
        aux1, aux2, vals = self._build("2024-01", months_actuals=3, months_forecast=3, forecast_data=fc)
        assert float(aux1) == pytest.approx(200.0)  # (100+300)/2

    def test_empty_periods_returns_zeros(self):
        data = _make_data(forecasts={"MAT-1": {}}, periods=[], first_period=None)
        eng = _engine(data)
        aux1, aux2, vals = eng._calculate_aux_columns("MAT-1", {})
        assert float(aux1) == pytest.approx(0.0)
        assert float(aux2) == pytest.approx(0.0)
        assert vals == []

    def test_no_first_period_falls_back_to_sorted_keys(self):
        # Without forecast_first_period, engine sorts forecast_data keys and uses first
        fc = {"2024-03": 300.0, "2024-01": 100.0, "2024-02": 200.0,
              "2024-05": 50.0, "2024-06": 60.0, "2024-07": 70.0}
        data = _make_data(
            forecasts={"MAT-1": fc},
            periods=["2024-05", "2024-06", "2024-07"],
            first_period=None,  # No explicit first period
        )
        eng = _engine(data, months_actuals=3, months_forecast=3)
        aux1, aux2, vals = eng._calculate_aux_columns("MAT-1", fc)
        # Should not crash; aux1 derived from sorted keys starting at "2024-01"
        assert float(aux1) >= 0.0


# ---------------------------------------------------------------------------
# calculate() — full pipeline
# ---------------------------------------------------------------------------

class TestForecastEngineCalculate:
    def _full_run(self, months_actuals=3, months_forecast=3):
        """Build a simple 3-period forecast and run calculate()."""
        first = "2024-01"
        # months_actuals=3: aux1 from 2024-01..2024-03
        # months_forecast=3: aux2 from 2024-05..2024-07, planning periods 2024-05/06/07
        fc = {
            "2024-01": 100.0, "2024-02": 150.0, "2024-03": 200.0,
            "2024-05": 300.0, "2024-06": 350.0, "2024-07": 400.0,
        }
        data = _make_data(
            forecasts={"MAT-1": fc},
            periods=["2024-05", "2024-06", "2024-07"],
            first_period=first,
        )
        eng = _engine(data, months_actuals=months_actuals, months_forecast=months_forecast)
        rows = eng.calculate()
        return rows, eng

    def test_returns_one_row_per_material(self):
        rows, _ = self._full_run()
        assert len(rows) == 1

    def test_row_has_correct_line_type(self):
        rows, _ = self._full_run()
        assert rows[0].line_type == LineType.DEMAND_FORECAST.value

    def test_row_values_match_forecast_data(self):
        rows, _ = self._full_run()
        row = rows[0]
        assert row.values["2024-05"] == pytest.approx(300.0)
        assert row.values["2024-06"] == pytest.approx(350.0)
        assert row.values["2024-07"] == pytest.approx(400.0)

    def test_aux1_set_on_row(self):
        rows, _ = self._full_run()
        assert float(rows[0].aux_column) == pytest.approx((100.0 + 150.0 + 200.0) / 3)

    def test_aux2_set_on_row(self):
        rows, _ = self._full_run()
        assert float(rows[0].aux_2_column) == pytest.approx((300.0 + 350.0 + 400.0) / 3)

    def test_material_not_in_master_skipped(self):
        fc = {"2024-01": 100.0, "2024-05": 300.0}
        data = _make_data(forecasts={"GHOST": fc}, periods=["2024-05"], first_period="2024-01")
        data.materials = {}  # no materials in master
        eng = _engine(data)
        rows = eng.calculate()
        assert rows == []

    def test_get_forecast_returns_value(self):
        rows, eng = self._full_run()
        assert eng.get_forecast("MAT-1", "2024-05") == pytest.approx(300.0)

    def test_get_forecast_missing_returns_zero(self):
        rows, eng = self._full_run()
        assert eng.get_forecast("UNKNOWN", "2024-05") == pytest.approx(0.0)

    def test_results_accessible_after_calculate(self):
        rows, eng = self._full_run()
        assert "MAT-1" in eng.get_all_forecasts()
