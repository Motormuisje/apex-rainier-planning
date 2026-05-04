from types import SimpleNamespace

import pytest
from flask import Flask

from modules.models import LineType
from ui.routes.read import create_read_blueprint
from ui.serializers import moq_warnings_payload, row_payload


@pytest.fixture
def read_route_app(planning_engine_result):
    engine = SimpleNamespace(
        data=planning_engine_result.data,
        results={line_type: list(rows) for line_type, rows in planning_engine_result.results.items()},
        value_results={
            line_type: list(rows)
            for line_type, rows in planning_engine_result.value_results.items()
        },
        all_purch_raw_needs=dict(getattr(planning_engine_result, "all_purch_raw_needs", {}) or {}),
        _iq_cache=None,
    )
    sess = {"id": "read-route-session", "engine": engine}
    active = {"sess": sess}

    def get_active():
        current = active["sess"]
        return current, current.get("engine") if current else None

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_read_blueprint(
        get_active,
        row_payload,
        moq_warnings_payload,
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        engine=engine,
        sess=sess,
        clear_engine=lambda: active["sess"].update({"engine": None}),
    )


def test_results_returns_periods_results_and_moq_payload(read_route_app):
    response = read_route_app.client.get("/api/results")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["periods"] == read_route_app.engine.data.periods
    assert LineType.DEMAND_FORECAST.value in payload["results"]
    assert payload["results"][LineType.DEMAND_FORECAST.value]
    assert "moq_raw_needs" in payload


def test_value_results_returns_results_and_consolidation(read_route_app):
    response = read_route_app.client.get("/api/value_results")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["periods"] == read_route_app.engine.data.periods
    assert "results" in payload
    assert "consolidation" in payload
    assert isinstance(payload["consolidation"], list)


def test_dashboard_returns_kpis_and_chart_shapes(read_route_app):
    response = read_route_app.client.get("/api/dashboard")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["periods"] == read_route_app.engine.data.periods
    assert set(payload["kpis"]) >= {"materials", "avg_utilization", "total_fte", "total_overstock"}
    assert isinstance(payload["utilization_by_machine"], list)
    assert isinstance(payload["fte_by_group"], list)
    assert isinstance(payload["financials"], dict)
    assert isinstance(payload["demand_trend"], dict)
    assert isinstance(payload["inventory_trend"], dict)
    assert isinstance(payload["target_trend"], dict)


def test_dashboard_kpi_values_are_numeric_and_in_range(read_route_app):
    response = read_route_app.client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()
    kpis = payload["kpis"]
    periods = payload["periods"]

    assert isinstance(kpis["materials"], int) and kpis["materials"] > 0
    assert 0.0 <= kpis["avg_utilization"] <= 100.0
    assert kpis["total_fte"] >= 0.0
    assert kpis["total_overstock"] >= 0.0

    for trend_key in ("demand_trend", "inventory_trend", "target_trend"):
        trend = payload[trend_key]
        assert set(trend.keys()) == set(periods), f"{trend_key} periods mismatch"
        for val in trend.values():
            assert isinstance(val, (int, float)), f"{trend_key} value is not numeric: {val}"


def test_dashboard_inventory_quality_structure(read_route_app):
    response = read_route_app.client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.get_json()
    iq = payload["inventory_quality"]
    assert isinstance(iq, list)

    for entry in iq:
        assert "material_number" in entry
        assert "total_overstock" in entry
        assert "starting_overstock" in entry
        assert "periods" in entry
        assert "Starting stock" in entry["periods"], (
            f"'Starting stock' missing from periods for {entry['material_number']} "
            "— regression of starting stock categorization bug"
        )
        assert isinstance(entry["total_overstock"], (int, float))


def test_capacity_returns_utilization_rows(read_route_app):
    response = read_route_app.client.get("/api/capacity")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["periods"] == read_route_app.engine.data.periods
    assert isinstance(payload["utilization"], list)


def test_inventory_returns_summary_and_rows(read_route_app):
    response = read_route_app.client.get("/api/inventory")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["periods"] == read_route_app.engine.data.periods
    assert set(payload["summary"]) == {"healthy", "low", "high"}
    assert isinstance(payload["data"], list)


def test_inventory_quality_returns_payload(read_route_app):
    response = read_route_app.client.get("/api/inventory_quality")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert isinstance(payload, dict)


@pytest.mark.parametrize(
    "path",
    [
        "/api/results",
        "/api/value_results",
        "/api/dashboard",
        "/api/capacity",
        "/api/inventory",
        "/api/inventory_quality",
    ],
)
def test_read_routes_return_400_without_engine(read_route_app, path):
    read_route_app.clear_engine()

    response = read_route_app.client.get(path)

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"
