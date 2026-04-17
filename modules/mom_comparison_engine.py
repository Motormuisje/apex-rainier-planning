"""
S&OP Planning Engine - Month-over-Month Comparison
Compares inventory period-over-period within a single planning run, or
optionally between two planning cycles.

VBA reference: CreateMoMComparison / CreateMoMScatterChart_color_markers
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


class MoMComparisonEngine:
    """Calculate month-over-month deltas — either sequential (within one run)
    or between two planning cycles."""

    INVENTORY_LINE = "04. Inventory"

    def __init__(self, current_df: pd.DataFrame, previous_df: pd.DataFrame = None):
        self.current_df = current_df
        self.previous_df = previous_df if previous_df is not None else pd.DataFrame()

    # ------------------------------------------------------------------
    # Sequential comparison (single run, period T -> period T+1)
    # ------------------------------------------------------------------

    @classmethod
    def calculate_sequential(cls, df: pd.DataFrame, num_months: int = 6) -> Dict:
        """Calculate period-over-period inventory changes within one planning run.

        Parameters
        ----------
        df : DataFrame
            Full output from PlanningEngine.to_dataframe().
        num_months : int
            Number of month-to-month transitions to compute.
            Uses periods[0..num_months] (i.e. num_months+1 columns).

        Returns
        -------
        dict with keys:
            available   – bool
            message     – str (only when available=False)
            periods     – list[str]  selected periods (len = num_months+1)
            material_count – int
            num_transitions – int
            scatter     – {materials, start, end, colors}
            transitions – list[{from_period, to_period, rows:[...]}]
            summary     – list[{material_number, material_name, start, end, delta, delta_pct}]
        """
        inv = cls._extract_inventory(df)
        if inv.empty:
            return {'available': False, 'message': 'No inventory (04. Inventory) rows in results'}

        periods = cls._period_columns(inv)
        if len(periods) < 2:
            return {'available': False, 'message': 'Need at least 2 forecast periods for comparison'}

        # Select periods[0 .. num_months] (capped at available columns)
        selected = periods[:min(num_months + 1, len(periods))]

        # Build per-material summary (first period -> last period)
        first_p, last_p = selected[0], selected[-1]
        summary = []
        for _, row in inv.iterrows():
            mat_id   = str(row.get('Material number', ''))
            mat_name = str(row.get('Material name', ''))
            start    = float(row[first_p]) if pd.notna(row.get(first_p)) else 0.0
            end      = float(row[last_p])  if pd.notna(row.get(last_p))  else 0.0
            delta    = end - start
            if start != 0:
                delta_pct = round(delta / start * 100, 2)
            else:
                delta_pct = None  # undefined (avoid inf in JSON)
            summary.append({
                'material_number': mat_id,
                'material_name':   mat_name,
                'start':           round(start, 2),
                'end':             round(end,   2),
                'delta':           round(delta, 2),
                'delta_pct':       delta_pct,
            })

        # Scatter colour (quadrant logic from VBA)
        scatter_colors = []
        for s in summary:
            c, p = s['end'], s['start']
            if c >= 0 and p < 0:
                scatter_colors.append('FFC7CE')   # red
            elif c >= 0 and p >= 0:
                scatter_colors.append('C6EFCE')   # green
            else:
                scatter_colors.append('FFC896')   # orange

        scatter = {
            'materials': [s['material_number'] for s in summary],
            'start':     [s['start']           for s in summary],
            'end':       [s['end']             for s in summary],
            'colors':    scatter_colors,
        }

        # Build period-over-period transitions
        transitions = []
        for i in range(len(selected) - 1):
            from_p = selected[i]
            to_p   = selected[i + 1]
            rows_data = []
            for _, row in inv.iterrows():
                mat_id   = str(row.get('Material number', ''))
                mat_name = str(row.get('Material name', ''))
                from_val = float(row[from_p]) if pd.notna(row.get(from_p)) else 0.0
                to_val   = float(row[to_p])   if pd.notna(row.get(to_p))   else 0.0
                delta    = to_val - from_val
                delta_pct = round(delta / from_val * 100, 2) if from_val != 0 else None
                rows_data.append({
                    'material_number': mat_id,
                    'material_name':   mat_name,
                    'from_inventory':  round(from_val, 2),
                    'to_inventory':    round(to_val,   2),
                    'delta':           round(delta,    2),
                    'delta_pct':       delta_pct,
                })
            transitions.append({
                'from_period': from_p,
                'to_period':   to_p,
                'rows':        rows_data,
            })

        return {
            'available':        True,
            'periods':          selected,
            'material_count':   len(summary),
            'num_transitions':  len(transitions),
            'scatter':          scatter,
            'transitions':      transitions,
            'summary':          summary,
        }

    # ------------------------------------------------------------------
    # Main comparison
    # ------------------------------------------------------------------

    def calculate(self) -> pd.DataFrame:
        """Return a DataFrame with columns:

        Material number | Material name | Product type | Period |
        Current Inventory | Previous Inventory | Delta | Delta %

        Only materials present in both cycles are compared.
        Returns an empty DataFrame if either input is empty.
        """
        if self.current_df.empty or self.previous_df.empty:
            return pd.DataFrame()

        cur_inv = self._extract_inventory(self.current_df)
        prev_inv = self._extract_inventory(self.previous_df)

        if cur_inv.empty or prev_inv.empty:
            return pd.DataFrame()

        # Identify overlapping period columns
        cur_periods = self._period_columns(cur_inv)
        prev_periods = self._period_columns(prev_inv)
        common_periods = sorted(set(cur_periods) & set(prev_periods))

        if not common_periods:
            return pd.DataFrame()

        # Melt to long form for easy merge
        id_cols = ["Material number", "Material name", "Product type"]
        cur_long = cur_inv[id_cols + common_periods].melt(
            id_vars=id_cols, var_name="Period", value_name="Current Inventory"
        )
        prev_long = prev_inv[id_cols + common_periods].melt(
            id_vars=id_cols, var_name="Period", value_name="Previous Inventory"
        )

        merged = cur_long.merge(prev_long, on=id_cols + ["Period"], how="inner")
        merged["Delta"] = merged["Current Inventory"] - merged["Previous Inventory"]
        merged["Delta %"] = np.where(
            merged["Previous Inventory"] != 0,
            merged["Delta"] / merged["Previous Inventory"] * 100,
            np.where(merged["Delta"] != 0, np.inf, 0.0),
        )
        merged = merged.sort_values(["Material number", "Period"]).reset_index(drop=True)
        return merged

    # ------------------------------------------------------------------
    # Scatter-chart helpers
    # ------------------------------------------------------------------

    def create_scatter_data(self) -> Dict:
        """Return data suitable for an openpyxl scatter chart.

        Returns dict with keys:
            materials  – list[str]        material numbers
            current    – list[float]      summed current inventory per material
            previous   – list[float]      summed previous inventory per material
            colors     – list[str]        hex colour per material (quadrant-based)

        Quadrant colouring (VBA CreateMoMScatterChart_color_markers):
            Q1 (cur>0, prev<0) → red    FFC7CE
            Q2 (cur<0, prev>0) → orange FFC896
            Q3 (cur<0, prev<0) → orange FFC896
            Q4 (cur>0, prev>0) → green  C6EFCE
        """
        comparison = self.calculate()
        if comparison.empty:
            return {"materials": [], "current": [], "previous": [], "colors": []}

        agg = comparison.groupby("Material number").agg(
            current=("Current Inventory", "sum"),
            previous=("Previous Inventory", "sum"),
        ).reset_index()

        colors = []
        for _, row in agg.iterrows():
            c, p = row["current"], row["previous"]
            if c >= 0 and p < 0:
                colors.append("FFC7CE")   # red – Q1
            elif c >= 0 and p >= 0:
                colors.append("C6EFCE")   # green – Q4
            else:
                colors.append("FFC896")   # orange – Q2/Q3

        return {
            "materials": agg["Material number"].tolist(),
            "current": agg["current"].tolist(),
            "previous": agg["previous"].tolist(),
            "colors": colors,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_inventory(df: pd.DataFrame) -> pd.DataFrame:
        """Filter to 04. Inventory rows only."""
        if "Line type" not in df.columns:
            return pd.DataFrame()
        return df[df["Line type"] == MoMComparisonEngine.INVENTORY_LINE].copy()

    @staticmethod
    def _period_columns(df: pd.DataFrame) -> List[str]:
        """Return column names that look like YYYY-MM period headers."""
        periods = []
        for col in df.columns:
            col_str = str(col)
            if len(col_str) == 7 and col_str[4] == "-":
                try:
                    int(col_str[:4])
                    int(col_str[5:])
                    periods.append(col)
                except ValueError:
                    pass
        return sorted(periods)
