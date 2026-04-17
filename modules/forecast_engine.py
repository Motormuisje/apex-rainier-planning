"""
S&OP Planning Engine - Forecast Engine
Calculates Line 01: Demand Forecast

Aux Column 1: Average demand over actuals period (HISTORICAL - before planning month)
Aux Column 2: Average demand over forecast period (FUTURE - planning horizon)
"""

from typing import Dict, List
from modules.models import PlanningRow, LineType
from modules.data_loader import DataLoader


class ForecastEngine:
    def __init__(self, data: DataLoader, months_actuals: int = 12, months_forecast: int = 12):
        self.data = data
        self.periods = data.periods
        self.months_actuals = months_actuals
        self.months_forecast = months_forecast
        self.results: Dict[str, Dict[str, float]] = {}
        self.rows: List[PlanningRow] = []

    def calculate(self) -> List[PlanningRow]:
        print("  [01] Calculating Demand Forecast...")

        for mat_num, forecast_data in self.data.forecasts.items():
            material = self.data.materials.get(mat_num)
            if not material:
                continue  # Skip materials not in material master

            # VBA DemandForecast: copies Forecast sheet column ForecastStartClmn+i
            # (where ForecastStartClmn = ForecastActualStartClmn + ForecastActualsMonths + 1)
            # to Planning column PlanningStartForecast+i.
            # We use key-based lookup anchored to the first period in the Forecast sheet
            # so every material is positioned correctly regardless of how many historical
            # columns it happens to have populated.
            aux_1, aux_2, ordered_vals = self._calculate_aux_columns(mat_num, forecast_data)

            self.results[mat_num] = {period: val for period, val in ordered_vals}

            row = PlanningRow(
                material_number=mat_num,
                material_name=material.name if material else '',
                product_type=material.product_type.value if material else '',
                product_family=material.product_family if material else '',
                spc_product=material.spc_product if material else '',
                product_cluster=material.product_cluster if material else '',
                product_name=material.product_name if material else '',
                line_type=LineType.DEMAND_FORECAST.value,
                aux_column=aux_1,
                aux_2_column=aux_2,
                # starting_stock intentionally omitted: VBA leaves it blank for Line 01
                values=self.results[mat_num].copy()
            )
            self.rows.append(row)

        print(f"       -> {len(self.rows)} materials with forecast")
        return self.rows

    @staticmethod
    def _offset_period(base_period: str, offset: int) -> str:
        """Return YYYY-MM for base_period + offset months."""
        year, month = int(base_period[:4]), int(base_period[5:7])
        total = year * 12 + (month - 1) + offset
        y = total // 12
        m = (total % 12) + 1
        return f"{y}-{str(m).zfill(2)}"

    def _calculate_aux_columns(self, mat_num: str, forecast_data: Dict[str, float]) -> tuple:
        """
        Key-based lookup anchored to data.forecast_first_period:

        first_col_period = forecast_first_period  (e.g. "2024-11")
        aux2_anchor      = first_col_period + (months_actuals + 1) months  (e.g. "2025-12")

        AUX1  = AVERAGE of months_actuals periods starting from first_col_period
                ("2024-11".."2025-10")
        AUX2  = AVERAGE of months_forecast periods starting from aux2_anchor
                ("2025-12".."2026-11")
        vals  = list of (period, value) for each planning period i,
                where value = forecast_data[aux2_anchor + i months]
                i=0 → "2025-12" = 536 for 600010662 → planning column "2026-01" ✓
        """
        if not self.periods:
            return "0", "0", []

        first = getattr(self.data, 'forecast_first_period', None)
        if not first:
            # Fallback: derive from sorted keys
            sorted_keys = sorted(forecast_data.keys())
            first = sorted_keys[0] if sorted_keys else None
        if not first:
            return "0", "0", [(p, 0.0) for p in self.periods]

        # AUX1: average of months_actuals periods starting from first_col_period
        # VBA AVERAGE ignores blank cells — only include periods present in forecast_data
        aux1_values = [
            forecast_data[self._offset_period(first, i)]
            for i in range(self.months_actuals)
            if self._offset_period(first, i) in forecast_data
        ]
        aux_1 = round(sum(aux1_values) / len(aux1_values), 2) if aux1_values else 0.0

        # AUX2: average of months_forecast periods starting from aux2_anchor (months_actuals+1)
        # This is the Config initial_date period onward (e.g. "2025-12".."2026-11")
        aux2_anchor = self._offset_period(first, self.months_actuals + 1)
        aux2_values = [
            forecast_data[self._offset_period(aux2_anchor, i)]
            for i in range(self.months_forecast)
            if self._offset_period(aux2_anchor, i) in forecast_data
        ]
        aux_2 = round(sum(aux2_values) / len(aux2_values), 2) if aux2_values else 0.0

        # Monthly values: planning period i → forecast_data[aux2_anchor + i]
        # aux2_anchor = offset("2024-11", 13) = "2025-12" → sorted[13]
        # So planning "2026-01" (i=0) = forecast_data["2025-12"] = 536 ✓ (matches Excel)
        ordered_vals = [
            (period, forecast_data.get(self._offset_period(aux2_anchor, i), 0.0))
            for i, period in enumerate(self.periods)
        ]

        # FIX 7: return exact float values; #,##0 formatting applied in Excel writer
        return aux_1, aux_2, ordered_vals

    def get_forecast(self, material: str, period: str) -> float:
        return self.results.get(material, {}).get(period, 0.0)

    def get_all_forecasts(self) -> Dict[str, Dict[str, float]]:
        return self.results
