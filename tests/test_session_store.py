import json
from types import SimpleNamespace

import pytest

from ui.session_store import load_sessions_from_disk, save_sessions_to_disk


pytestmark = pytest.mark.no_fixture


def _valuation_params():
    return SimpleNamespace(
        direct_fte_cost_per_month=1.0,
        indirect_fte_cost_per_month=2.0,
        overhead_cost_per_month=3.0,
        sga_cost_per_month=4.0,
        depreciation_per_year=5.0,
        net_book_value=6.0,
        days_sales_outstanding=7.0,
        days_payable_outstanding=8.0,
    )


def test_save_sessions_to_disk_persists_metadata_without_engine(tmp_path):
    store_path = tmp_path / "sessions_store.json"
    engine = SimpleNamespace(data=SimpleNamespace(valuation_params=_valuation_params()))
    sessions = {
        "s1": {
            "id": "s1",
            "file_path": "C:/fixtures/golden.xlsm",
            "extract_files": {"bom": "bom.xlsx"},
            "filename": "golden.xlsm",
            "custom_name": "Golden",
            "is_snapshot": True,
            "engine": engine,
            "metadata": {"materials": 2},
            "uploaded_at": "2026-04-22T07:00:00",
            "parameters": {"planning_month": "2025-12"},
            "pending_edits": {"edit-1": {"value": 10}},
            "value_aux_overrides": {"MAT-1": {"2025-12": 5}},
        }
    }

    save_sessions_to_disk(
        sessions,
        "s1",
        store_path,
        lambda sess, current_engine: {"PBA01": {"oee": 0.9}},
    )

    raw = json.loads(store_path.read_text(encoding="utf-8"))
    saved = raw["sessions"]["s1"]
    assert raw["active_session_id"] == "s1"
    assert "engine" not in saved
    assert saved["machine_overrides"] == {"PBA01": {"oee": 0.9}}
    assert saved["valuation_params"] == {
        "1": 1.0,
        "2": 2.0,
        "3": 3.0,
        "4": 4.0,
        "5": 5.0,
        "6": 6.0,
        "7": 7.0,
        "8": 8.0,
    }

    loaded, active = load_sessions_from_disk(store_path)
    assert active == "s1"
    assert loaded["s1"]["engine"] is None
    assert loaded["s1"]["extract_files"] == {"bom": "bom.xlsx"}
    assert loaded["s1"]["machine_overrides"] == {"PBA01": {"oee": 0.9}}
    assert loaded["s1"]["valuation_params"] == saved["valuation_params"]
    assert loaded["s1"]["undo_stack"] == []
    assert loaded["s1"]["redo_stack"] == []


def test_save_sessions_to_disk_uses_baseline_valuation_when_engine_missing(tmp_path):
    store_path = tmp_path / "sessions_store.json"
    sessions = {
        "s1": {
            "id": "s1",
            "reset_baseline": {"valuation_params": {"1": 10.0}},
            "machine_overrides": {"PBA02": {"availability": 0.8}},
        }
    }

    save_sessions_to_disk(sessions, None, store_path, lambda sess, engine: {})

    saved = json.loads(store_path.read_text(encoding="utf-8"))["sessions"]["s1"]
    assert saved["valuation_params"] == {"1": 10.0}
    assert saved["machine_overrides"] == {"PBA02": {"availability": 0.8}}


def test_load_sessions_from_disk_returns_empty_when_store_missing(tmp_path):
    loaded, active = load_sessions_from_disk(tmp_path / "missing.json")

    assert loaded == {}
    assert active is None


def test_load_sessions_from_disk_falls_back_to_first_session_when_active_missing(tmp_path):
    store_path = tmp_path / "sessions_store.json"
    store_path.write_text(
        json.dumps({
            "active_session_id": "missing",
            "sessions": {
                "s1": {"filename": "one.xlsm"},
                "s2": {"filename": "two.xlsm"},
            },
        }),
        encoding="utf-8",
    )

    loaded, active = load_sessions_from_disk(store_path)

    assert list(loaded) == ["s1", "s2"]
    assert active == "s1"


def test_load_sessions_from_disk_moves_corrupt_store_aside(tmp_path):
    store_path = tmp_path / "sessions_store.json"
    store_path.write_text("{not json", encoding="utf-8")

    loaded, active = load_sessions_from_disk(store_path)

    assert loaded == {}
    assert active is None
    assert not store_path.exists()
    assert list(tmp_path.glob("sessions_store.json.corrupt-*"))
