"""
S&OP Planning Engine - Database Exporter
Exports the planning results to a flat 14-column DataFrame suitable
for loading into a database, and optionally to an Excel sheet.

VBA reference: ExportPlanningToDatabase / ExportSheetData

Exported line types:
    01. Demand forecast
    03. Total demand
    04. Inventory
    06. Production plan  |  06. Purchase receipt
"""

import pandas as pd
from typing import List, Optional
from datetime import datetime


class DatabaseExporter:
    """Flatten planning results into a database-ready table."""

    # Line types to include (VBA: starts with "01", "03", "04", "06. Purchase")
    # Plus production plan ("06. Production plan")
    INCLUDED_PREFIXES = ("01.", "03.", "04.", "06.")

    COLUMNS = [
        "Site",
        "InitialDate",
        "Material number",
        "Material name",
        "Product type",
        "Product family",
        "Line type",
        "Aux Column",
        "Aux 2 Column",
        "Starting stock",
        "Period",
        "Value",
        "ExportTimestamp",
        "CycleId",
    ]

    def __init__(self, planning_df: pd.DataFrame, site: str, initial_date: datetime):
        """
        Parameters
        ----------
        planning_df : DataFrame
            Full planning output (from PlanningEngine.to_dataframe()).
        site : str
            Plant/site code (from PlanningConfig.site, e.g. 'NLX1').
        initial_date : datetime
            The planning cycle's initial date.
        """
        self.planning_df = planning_df
        self.site = site
        self.initial_date = initial_date
        self.cycle_id = initial_date.strftime("%Y-%m")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_to_dataframe(self) -> pd.DataFrame:
        """Return a flat, long-form DataFrame with one row per material × period.

        An extra pre-month row is added for each 04. Inventory material using
        its Starting stock value (VBA ExportSheetData behaviour).

        Deduplication: the result is deduplicated by (Site, InitialDate,
        Material number, Line type, Period) keeping the last occurrence.
        """
        if self.planning_df.empty:
            return pd.DataFrame(columns=self.COLUMNS)

        # Filter to relevant line types
        mask = self.planning_df["Line type"].str.startswith(self.INCLUDED_PREFIXES)
        filtered = self.planning_df[mask].copy()

        if filtered.empty:
            return pd.DataFrame(columns=self.COLUMNS)

        period_cols = self._period_columns(filtered)
        if not period_cols:
            return pd.DataFrame(columns=self.COLUMNS)

        timestamp = datetime.now().isoformat(timespec="seconds")
        rows: List[dict] = []

        for _, src_row in filtered.iterrows():
            base = {
                "Site": self.site,
                "InitialDate": self.initial_date.strftime("%Y-%m-%d"),
                "Material number": src_row.get("Material number", ""),
                "Material name": src_row.get("Material name", ""),
                "Product type": src_row.get("Product type", ""),
                "Product family": src_row.get("Product family", ""),
                "Line type": src_row.get("Line type", ""),
                "Aux Column": src_row.get("Aux Column", ""),
                "Aux 2 Column": src_row.get("Aux 2 Column", ""),
                "Starting stock": src_row.get("Starting stock", 0),
                "ExportTimestamp": timestamp,
                "CycleId": self.cycle_id,
            }

            # VBA: extra pre-month row for Inventory with Starting stock
            if str(src_row.get("Line type", "")).startswith("04."):
                pre = dict(base)
                pre["Period"] = "Pre-month"
                pre["Value"] = src_row.get("Starting stock", 0)
                rows.append(pre)

            for period in period_cols:
                rec = dict(base)
                rec["Period"] = period
                rec["Value"] = src_row.get(period, 0)
                rows.append(rec)

        result = pd.DataFrame(rows, columns=self.COLUMNS)

        # Dedup by Site + InitialDate + Material number + Line type + Period
        dedup_keys = ["Site", "InitialDate", "Material number", "Line type", "Period"]
        result = result.drop_duplicates(subset=dedup_keys, keep="last").reset_index(drop=True)
        return result

    def export_to_excel(self, writer, sheet_name: str = "DB Export") -> bool:
        """Write the DB-export sheet into an open ExcelWriter.

        Returns True if at least one row was written.
        """
        df = self.export_to_dataframe()
        if df.empty:
            return False
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
