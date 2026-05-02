from types import SimpleNamespace

import pytest
from flask import Flask

from modules.models import LineType, PlanningRow
from ui.routes.machines import create_machines_blueprint
from ui.state_snapshot import machine_overrides_from_engine


pytestmark = pytest.mark.no_fixture


class FakeMachine:
    def __init__(self, code="M1", *, oee=0.85, availability=None, shift_hours_override=None):
        self.code = code
        self.name = f"Machine {code}"
        self.machine_group = "Group A"
        self.oee = oee
        self.availability_by_period = availability or {"2025-12": 0.95, "2026-01": 0.80}
        self.shift_hours_override = shift_hours_override

    def get_availability(self, period):
        return self.availability_by_period.get(period, 0.0)


def _row(line_type, *, material_number="MAT-1", material_name="M1", product_type="Machine", values=None):
    return PlanningRow(
        material_number=material_number,
        material_name=material_name,
        product_type=product_type,
        product_family="",
        spc_product="",
        product_cluster="",
        product_name="",
        line_type=line_type,
        values=values or {"2025-12": 1.0, "2026-01": 2.0},
    )


@pytest.fixture
def machines_route_app():
    periods = ["2025-12", "2026-01"]
    machine = FakeMachine()
    routing = SimpleNamespace(work_center="M1", base_quantity=100.0, standard_time=10.0)
    data = SimpleNamespace(
        periods=periods,
        machines={"M1": machine},
        materials={"MAT-1": SimpleNamespace()},
        get_all_routings=lambda material_number: [routing] if material_number == "MAT-1" else [],
    )
    engine = SimpleNamespace(
        data=data,
        results={
            LineType.UTILIZATION_RATE.value: [
                _row(LineType.UTILIZATION_RATE.value, material_name="M1", values={"2025-12": 0.50, "2026-01": 0.75}),
            ],
            LineType.CAPACITY_UTILIZATION.value: [
                _row(LineType.CAPACITY_UTILIZATION.value, material_name="M1", values={"2025-12": 10.0, "2026-01": 20.0}),
            ],
            LineType.FTE_REQUIREMENTS.value: [
                _row(LineType.FTE_REQUIREMENTS.value, material_number="Group A", values={"2025-12": 1.25, "2026-01": 1.75}),
            ],
        },
        all_production_plans={"MAT-1": {"2025-12": 100.0, "2026-01": 300.0}},
        value_results={},
    )
    sess = {
        "id": "machines-session",
        "engine": engine,
        "machine_overrides": {},
        "machine_undo": [],
        "machine_redo": [],
        "reset_baseline": {
            "machines": {
                "M1": {
                    "oee": 0.80,
                    "availability_by_period": {"2025-12": 0.90, "2026-01": 0.90},
                    "shift_hours_override": None,
                    "shift_hours_computed": 500.0,
                },
            },
        },
    }
    state = SimpleNamespace(
        sess=sess,
        engine=engine,
        baseline_calls=[],
        recalc_calls=[],
        save_calls=[],
    )

    def get_active():
        return (state.sess, state.engine) if state.sess is not None else (None, None)

    def shift_hours_lookup(machine, data):
        return float(getattr(machine, "shift_hours_override", None) or 520.0)

    def ensure_reset_baseline(sess, engine):
        state.baseline_calls.append((sess, engine))

    def recalculate_capacity_and_values(engine, sess):
        state.recalc_calls.append((engine, sess))

    def planning_value_payload(engine):
        return {"value_results": {}, "consolidation": []}

    def save_sessions_to_disk():
        state.save_calls.append(True)

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_machines_blueprint(
        get_active,
        machine_overrides_from_engine,
        shift_hours_lookup,
        ensure_reset_baseline,
        recalculate_capacity_and_values,
        planning_value_payload,
        save_sessions_to_disk,
    ))
    state.app = flask_app
    state.client = flask_app.test_client()
    return state


def test_get_machines_returns_list_with_overrides(machines_route_app):
    response = machines_route_app.client.get("/api/machines")

    assert response.status_code == 200, response.get_json(silent=True)
    payload = response.get_json()
    assert payload["periods"] == ["2025-12", "2026-01"]
    assert payload["machine_overrides"]["M1"]["oee"] == pytest.approx(0.85)
    assert payload["machines"][0]["code"] == "M1"
    assert payload["machines"][0]["has_edits"] is True
    assert payload["machines"][0]["edit_meta"]["oee"]["original_display"] == "80.0%"


def test_reset_machine_params_clears_overrides(machines_route_app):
    machine = machines_route_app.engine.data.machines["M1"]
    machine.oee = 0.85
    machine.availability_by_period = {"2025-12": 0.70, "2026-01": 0.70}
    machine.shift_hours_override = 640.0
    machines_route_app.sess["machine_overrides"] = {
        "M1": {
            "oee": 0.85,
            "availability_by_period": {"2025-12": 0.70, "2026-01": 0.70},
            "shift_hours_override": 640.0,
        },
    }
    machines_route_app.sess["machine_undo"] = [{"machine": "M1"}]
    machines_route_app.sess["machine_redo"] = [{"machine": "M1"}]

    response = machines_route_app.client.post("/api/machines/reset")

    assert response.status_code == 200, response.get_json(silent=True)
    assert response.get_json()["success"] is True
    assert machines_route_app.sess["machine_overrides"] == {}
    assert machines_route_app.sess["machine_undo"] == []
    assert machines_route_app.sess["machine_redo"] == []
    assert machine.oee == pytest.approx(0.80)
    assert machine.availability_by_period == {"2025-12": 0.90, "2026-01": 0.90}
    assert machine.shift_hours_override is None
    assert machines_route_app.recalc_calls == [(machines_route_app.engine, machines_route_app.sess)]
    assert machines_route_app.save_calls == [True]


def test_update_machine_param_applies_override(machines_route_app):
    response = machines_route_app.client.post(
        "/api/machines/update",
        json={"machine": "M1", "field": "oee", "value": 0.90},
    )

    assert response.status_code == 200, response.get_json(silent=True)
    assert response.get_json()["success"] is True
    assert machines_route_app.engine.data.machines["M1"].oee == pytest.approx(0.90)
    assert machines_route_app.sess["machine_overrides"]["M1"]["oee"] == pytest.approx(0.90)
    assert machines_route_app.baseline_calls == [(machines_route_app.sess, machines_route_app.engine)]
    assert machines_route_app.recalc_calls == [(machines_route_app.engine, machines_route_app.sess)]
    assert machines_route_app.save_calls == [True]


def test_update_machine_param_missing_session_returns_400(machines_route_app):
    machines_route_app.sess = None
    machines_route_app.engine = None

    response = machines_route_app.client.post(
        "/api/machines/update",
        json={"machine": "M1", "field": "oee", "value": 0.90},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


def test_update_machine_param_unknown_machine_returns_404(machines_route_app):
    response = machines_route_app.client.post(
        "/api/machines/update",
        json={"machine": "NOPE", "field": "oee", "value": 0.90},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "unknown machine NOPE"


def test_update_machine_param_rejects_unsupported_field(machines_route_app):
    response = machines_route_app.client.post(
        "/api/machines/update",
        json={"machine": "M1", "field": "speed", "value": 1.0},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "unsupported field speed"


def test_update_machine_param_applies_availability_override(machines_route_app):
    response = machines_route_app.client.post(
        "/api/machines/update",
        json={"machine": "M1", "field": "availability", "value": 70},
    )

    assert response.status_code == 200, response.get_json(silent=True)
    assert machines_route_app.engine.data.machines["M1"].availability_by_period == {
        "2025-12": 0.70,
        "2026-01": 0.70,
    }
    assert machines_route_app.sess["machine_overrides"]["M1"]["availability_by_period"] == {
        "2025-12": 0.70,
        "2026-01": 0.70,
    }


def test_update_machine_param_allows_source_availability_above_100(machines_route_app):
    response = machines_route_app.client.post(
        "/api/machines/update",
        json={"machine": "M1", "field": "availability", "value": 140},
    )

    assert response.status_code == 200, response.get_json(silent=True)
    assert machines_route_app.engine.data.machines["M1"].availability_by_period == {
        "2025-12": pytest.approx(1.40),
        "2026-01": pytest.approx(1.40),
    }
    assert machines_route_app.sess["machine_overrides"]["M1"]["availability_by_period"] == {
        "2025-12": pytest.approx(1.40),
        "2026-01": pytest.approx(1.40),
    }


def test_update_machine_param_applies_shift_hours_override(machines_route_app):
    response = machines_route_app.client.post(
        "/api/machines/update",
        json={"machine": "M1", "field": "shift_hours", "value": 640},
    )

    assert response.status_code == 200, response.get_json(silent=True)
    assert machines_route_app.engine.data.machines["M1"].shift_hours_override == pytest.approx(640.0)
    assert machines_route_app.sess["machine_overrides"]["M1"]["shift_hours_override"] == pytest.approx(640.0)


def test_get_machines_returns_computed_shift_hours(machines_route_app):
    machines_route_app.engine.data.machines["M1"].shift_hours_override = 612.5

    response = machines_route_app.client.get("/api/machines")

    assert response.status_code == 200, response.get_json(silent=True)
    machine = response.get_json()["machines"][0]
    assert machine["shift_hours"] == pytest.approx(612.5)
    assert machine["edit_meta"]["shift_hours"]["new_display"] == "612.5h"


def test_get_machines_uses_precomputed_machine_output_cache(machines_route_app):
    machines_route_app.engine.machine_throughput_theo = {"M1": 42.0}
    machines_route_app.engine.output_by_machine_period = {
        "M1": {"2025-12": 420.0, "2026-01": 1000.0},
    }

    def fail_if_called(material_number):
        raise AssertionError("route should not recompute routings when engine cache is present")

    machines_route_app.engine.data.get_all_routings = fail_if_called

    response = machines_route_app.client.get("/api/machines")

    assert response.status_code == 200, response.get_json(silent=True)
    payload = response.get_json()
    machine = payload["machines"][0]
    assert machine["throughput_theoretical"] == pytest.approx(42.0)
    assert machine["throughput_effective_by_period"] == {
        "2025-12": 42.0,
        "2026-01": 50.0,
    }


def test_reset_machine_params_without_baseline_returns_400(machines_route_app):
    machines_route_app.sess["reset_baseline"] = {}

    response = machines_route_app.client.post("/api/machines/reset")

    assert response.status_code == 400
    assert response.get_json() == {"success": False, "message": "No baseline available"}


def test_undo_machine_param_empty_stack_returns_noop(machines_route_app):
    response = machines_route_app.client.post("/api/machines/undo")

    assert response.status_code == 200
    assert response.get_json() == {"success": False, "message": "Nothing to undo", "undo_depth": 0, "redo_depth": 0}


def test_undo_machine_param_restores_oee(machines_route_app):
    machine = machines_route_app.engine.data.machines["M1"]
    machine.oee = 0.90
    machines_route_app.sess["machine_undo"] = [
        {"machine": "M1", "field": "oee", "old_value": 0.80, "new_value": 0.90},
    ]

    response = machines_route_app.client.post("/api/machines/undo")

    assert response.status_code == 200, response.get_json(silent=True)
    assert response.get_json()["success"] is True
    assert machine.oee == pytest.approx(0.80)
    assert machines_route_app.sess["machine_undo"] == []
    assert machines_route_app.sess["machine_redo"] == [
        {"machine": "M1", "field": "oee", "old_value": 0.80, "new_value": 0.90},
    ]
    assert machines_route_app.recalc_calls == [(machines_route_app.engine, machines_route_app.sess)]
    assert machines_route_app.save_calls == [True]


def test_redo_machine_param_empty_stack_returns_noop(machines_route_app):
    response = machines_route_app.client.post("/api/machines/redo")

    assert response.status_code == 200
    assert response.get_json() == {"success": False, "message": "Nothing to redo", "redo_depth": 0, "undo_depth": 0}


def test_redo_machine_param_reapplies_shift_hours(machines_route_app):
    machine = machines_route_app.engine.data.machines["M1"]
    machine.shift_hours_override = None
    machines_route_app.sess["machine_redo"] = [
        {"machine": "M1", "field": "shift_hours", "old_value": None, "new_value": 700.0},
    ]

    response = machines_route_app.client.post("/api/machines/redo")

    assert response.status_code == 200, response.get_json(silent=True)
    assert response.get_json()["success"] is True
    assert machine.shift_hours_override == pytest.approx(700.0)
    assert machines_route_app.sess["machine_redo"] == []
    assert machines_route_app.sess["machine_undo"] == [
        {"machine": "M1", "field": "shift_hours", "old_value": None, "new_value": 700.0},
    ]
