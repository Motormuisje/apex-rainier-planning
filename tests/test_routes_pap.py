from types import SimpleNamespace

import pytest
from flask import Flask

from modules.models import LineType, PlanningRow
from ui.parsers import format_purchased_and_produced
from ui.routes.pap import create_pap_blueprint


pytestmark = pytest.mark.no_fixture


def _row(material_number="MAT-1", line_type=LineType.DEMAND_FORECAST.value):
    return PlanningRow(
        material_number=material_number,
        material_name=f"Name {material_number}",
        product_type="Bulk Product",
        product_family="Family",
        spc_product="SPC",
        product_cluster="Cluster",
        product_name="Product",
        line_type=line_type,
        values={"2025-12": 10.0},
    )


@pytest.fixture
def pap_route_app():
    engine = SimpleNamespace(
        data=SimpleNamespace(purchased_and_produced={"MAT-1": 0.25}),
        results={LineType.DEMAND_FORECAST.value: [_row()]},
        value_results={LineType.CONSOLIDATION.value: [_row("ZZZZZZ_REVENUE", LineType.CONSOLIDATION.value)]},
    )
    sess = {"id": "pap-session"}
    active = {"sess": sess, "engine": engine}
    global_config = {}
    baseline_calls = []
    recalc_calls = []
    finish_calls = []
    save_calls = []

    def get_active():
        return active["sess"], active["engine"]

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_pap_blueprint(
        get_active,
        global_config,
        format_purchased_and_produced,
        lambda sess_arg, engine_arg: baseline_calls.append((sess_arg, engine_arg)),
        lambda engine_arg, mat: recalc_calls.append((engine_arg, mat)),
        lambda engine_arg: finish_calls.append(engine_arg),
        lambda: save_calls.append(dict(global_config)),
        lambda engine_arg: {"moq_raw_needs": {"source": "pap-test"}},
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        active=active,
        engine=engine,
        sess=sess,
        global_config=global_config,
        baseline_calls=baseline_calls,
        recalc_calls=recalc_calls,
        finish_calls=finish_calls,
        save_calls=save_calls,
    )


def test_get_pap_returns_current_mapping(pap_route_app):
    response = pap_route_app.client.get("/api/pap")

    assert response.status_code == 200
    assert response.get_json() == {"pap": {"MAT-1": 0.25}}


def test_get_pap_requires_engine(pap_route_app):
    pap_route_app.active["engine"] = None

    response = pap_route_app.client.get("/api/pap")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


def test_set_pap_requires_material_number(pap_route_app):
    response = pap_route_app.client.post("/api/pap", json={"fraction": 0.5})

    assert response.status_code == 400
    assert response.get_json()["error"] == "material_number is required"


def test_set_pap_requires_numeric_fraction(pap_route_app):
    response = pap_route_app.client.post(
        "/api/pap",
        json={"material_number": "MAT-2", "fraction": "not-a-number"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "fraction must be a number"


def test_set_pap_updates_mapping_and_returns_recalculated_payload(pap_route_app):
    response = pap_route_app.client.post(
        "/api/pap",
        json={"material_number": "MAT-2", "fraction": "0.75"},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert set(payload) >= {"results", "value_results", "consolidation", "moq_raw_needs"}
    assert pap_route_app.engine.data.purchased_and_produced["MAT-2"] == pytest.approx(0.75)
    assert pap_route_app.global_config["purchased_and_produced"] == "MAT-1:0.25, MAT-2:0.75"
    assert pap_route_app.baseline_calls == [(pap_route_app.sess, pap_route_app.engine)]
    assert pap_route_app.recalc_calls == [(pap_route_app.engine, "MAT-2")]
    assert pap_route_app.finish_calls == [pap_route_app.engine]
    assert pap_route_app.save_calls


def test_delete_pap_removes_mapping_and_returns_recalculated_payload(pap_route_app):
    response = pap_route_app.client.delete("/api/pap/MAT-1")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert "MAT-1" not in pap_route_app.engine.data.purchased_and_produced
    assert pap_route_app.global_config["purchased_and_produced"] == ""
    assert pap_route_app.baseline_calls == [(pap_route_app.sess, pap_route_app.engine)]
    assert pap_route_app.recalc_calls == [(pap_route_app.engine, "MAT-1")]
    assert pap_route_app.finish_calls == [pap_route_app.engine]
    assert pap_route_app.save_calls
