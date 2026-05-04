from datetime import datetime

import pandas as pd
import pytest

from modules.database_exporter import DatabaseExporter


pytestmark = pytest.mark.no_fixture


def test_database_exporter_flattens_included_lines_and_inventory_premonth():
    planning_df = pd.DataFrame([
        {
            "Material number": "MAT-1",
            "Material name": "Material 1",
            "Product type": "Finished",
            "Product family": "Family A",
            "Line type": "01. Demand forecast",
            "Aux Column": "avg",
            "Aux 2 Column": "forecast",
            "Starting stock": 0,
            "2025-12": 10,
            "2026-01": 12,
        },
        {
            "Material number": "MAT-2",
            "Material name": "Material 2",
            "Product type": "Raw",
            "Product family": "Family B",
            "Line type": "04. Inventory",
            "Aux Column": "",
            "Aux 2 Column": "",
            "Starting stock": 7,
            "2025-12": 8,
            "2026-01": 9,
        },
        {
            "Material number": "MAT-X",
            "Material name": "Ignored",
            "Product type": "Other",
            "Product family": "Other",
            "Line type": "12. FTE requirements",
            "Starting stock": 0,
            "2025-12": 99,
            "2026-01": 99,
        },
    ])

    exporter = DatabaseExporter(planning_df, "NLX1", datetime(2025, 12, 1))
    result = exporter.export_to_dataframe()

    assert list(result.columns) == DatabaseExporter.COLUMNS
    assert len(result) == 5
    assert set(result["Line type"]) == {"01. Demand forecast", "04. Inventory"}
    assert set(result["CycleId"]) == {"2025-12"}
    assert set(result["InitialDate"]) == {"2025-12-01"}

    inventory_rows = result[result["Material number"] == "MAT-2"]
    assert inventory_rows["Period"].tolist() == ["Pre-month", "2025-12", "2026-01"]
    assert inventory_rows["Value"].tolist() == [7, 8, 9]


def test_database_exporter_deduplicates_by_site_material_line_and_period():
    planning_df = pd.DataFrame([
        {
            "Material number": "MAT-1",
            "Material name": "Old",
            "Line type": "03. Total demand",
            "Starting stock": 0,
            "2025-12": 10,
        },
        {
            "Material number": "MAT-1",
            "Material name": "New",
            "Line type": "03. Total demand",
            "Starting stock": 0,
            "2025-12": 20,
        },
    ])

    result = DatabaseExporter(planning_df, "NLX1", datetime(2025, 12, 1)).export_to_dataframe()

    assert len(result) == 1
    assert result.iloc[0]["Material name"] == "New"
    assert result.iloc[0]["Value"] == 20


@pytest.mark.parametrize(
    "planning_df",
    [
        pd.DataFrame(),
        pd.DataFrame([{"Line type": "12. FTE requirements", "2025-12": 1}]),
        pd.DataFrame([{"Line type": "01. Demand forecast", "not-a-period": 1}]),
    ],
)
def test_database_exporter_returns_empty_with_no_exportable_rows(planning_df):
    result = DatabaseExporter(planning_df, "NLX1", datetime(2025, 12, 1)).export_to_dataframe()

    assert result.empty
    assert list(result.columns) == DatabaseExporter.COLUMNS


def test_database_exporter_period_columns_are_sorted_and_validated():
    df = pd.DataFrame(columns=["2026-02", "not-date", "2025-12", "2026-AA", "2026-01"])

    assert DatabaseExporter._period_columns(df) == ["2025-12", "2026-01", "2026-02"]
