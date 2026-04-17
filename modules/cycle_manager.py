"""
S&OP Planning Engine - Cycle Manager
Saves and loads previous cycle planning results so that
Month-over-Month comparisons can be computed on the next run.

Storage format: Parquet (compact, typed, fast).
"""

import os
import json
import pandas as pd
from pathlib import Path


class CycleManager:
    """Persist and retrieve the previous planning cycle's DataFrame."""

    DEFAULT_FILENAME = "previous_cycle_values.parquet"
    META_FILENAME = "previous_cycle_meta.json"

    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self._path = self.storage_dir / self.DEFAULT_FILENAME
        self._meta_path = self.storage_dir / self.META_FILENAME

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_previous_cycle(self) -> bool:
        """Return True if a previous-cycle snapshot exists on disk."""
        return self._path.is_file()

    def load_previous_cycle(self) -> pd.DataFrame:
        """Load the previous cycle DataFrame.

        Returns an empty DataFrame if the file is missing or corrupt.
        """
        if not self.has_previous_cycle():
            return pd.DataFrame()
        try:
            return pd.read_parquet(self._path)
        except Exception as exc:
            print(f"  Warning: could not read previous cycle ({exc}); starting fresh.")
            return pd.DataFrame()

    def save_current_as_previous(self, df: pd.DataFrame, planning_month: str = None) -> None:
        """Overwrite the previous-cycle snapshot with the current results.

        Creates the storage directory if it does not exist.
        """
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        parquet_df = df.copy()
        for column in parquet_df.select_dtypes(include=["object"]).columns:
            parquet_df[column] = parquet_df[column].map(
                lambda value: None
                if pd.api.types.is_scalar(value) and pd.isna(value)
                else str(value)
            )
        parquet_df.to_parquet(self._path, index=False)
        meta = {"planning_month": planning_month or ""}
        with open(self._meta_path, "w") as f:
            json.dump(meta, f)
        print(f"  Previous-cycle snapshot saved -> {self._path}")

    def load_metadata(self) -> dict:
        """Return metadata dict for the stored previous cycle (e.g. planning_month)."""
        if not self._meta_path.is_file():
            return {}
        try:
            with open(self._meta_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def clear(self) -> None:
        """Remove the stored snapshot (useful for testing)."""
        if self._path.is_file():
            self._path.unlink()
        if self._meta_path.is_file():
            self._meta_path.unlink()
