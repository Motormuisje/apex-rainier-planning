import json
from types import SimpleNamespace

import pytest

from ui.config_store import (
    apply_folder_config,
    load_global_config,
    save_global_config,
    sync_global_config_from_engine,
)


pytestmark = pytest.mark.no_fixture


def test_load_global_config_returns_defaults_when_file_missing(tmp_path):
    config = load_global_config(tmp_path / "nope.json")

    assert config == {}


def test_load_global_config_reads_existing_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"site": "TEST", "forecast_months": 6}', encoding="utf-8")

    config = load_global_config(path)

    assert config["site"] == "TEST"
    assert config["forecast_months"] == 6


def test_load_global_config_returns_empty_on_invalid_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not-json", encoding="utf-8")

    assert load_global_config(path) == {}


def test_save_global_config_writes_json(tmp_path):
    path = tmp_path / "config.json"

    save_global_config(path, {"site": "NLX1", "forecast_months": 12})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["site"] == "NLX1"
    assert data["forecast_months"] == 12


def test_sync_global_config_from_engine_updates_config_fields():
    vp = SimpleNamespace(
        direct_fte_cost_per_month=1.0,
        indirect_fte_cost_per_month=2.0,
        overhead_cost_per_month=3.0,
        sga_cost_per_month=4.0,
        depreciation_per_year=5.0,
        net_book_value=6.0,
        days_sales_outstanding=7.0,
        days_payable_outstanding=8.0,
    )
    engine = SimpleNamespace(
        data=SimpleNamespace(
            valuation_params=vp,
            purchased_and_produced={"MAT-1": 0.5},
        ),
    )
    global_config = {}

    sync_global_config_from_engine(
        engine,
        global_config,
        lambda pap: ", ".join(f"{key}:{value}" for key, value in sorted(pap.items())),
    )

    assert global_config["valuation_params"]["1"] == pytest.approx(1.0)
    assert global_config["valuation_params"]["8"] == pytest.approx(8.0)
    assert global_config["purchased_and_produced"] == "MAT-1:0.5"


def test_sync_global_config_from_engine_ignores_missing_engine():
    global_config = {"site": "NLX1"}

    sync_global_config_from_engine(None, global_config, lambda pap: "unused")
    sync_global_config_from_engine(SimpleNamespace(data=None), global_config, lambda pap: "unused")

    assert global_config == {"site": "NLX1"}


def test_apply_folder_config_returns_paths_from_config(tmp_path):
    global_config = {
        "folders": {
            "uploads": str(tmp_path / "uploads"),
            "exports": str(tmp_path / "exports"),
            "sessions": str(tmp_path / "sessions"),
        },
    }

    uploads, exports, sessions_store = apply_folder_config(global_config, {})

    assert uploads == tmp_path / "uploads"
    assert exports == tmp_path / "exports"
    assert sessions_store == tmp_path / "sessions" / "sessions_store.json"
    assert uploads.exists()
    assert exports.exists()
    assert sessions_store.parent.exists()


def test_apply_folder_config_falls_back_to_defaults_when_config_empty(tmp_path):
    defaults = {
        "uploads": str(tmp_path / "default_uploads"),
        "exports": str(tmp_path / "default_exports"),
        "sessions": str(tmp_path / "default_sessions"),
    }

    uploads, exports, sessions_store = apply_folder_config({}, defaults)

    assert uploads == tmp_path / "default_uploads"
    assert exports == tmp_path / "default_exports"
    assert sessions_store == tmp_path / "default_sessions" / "sessions_store.json"
