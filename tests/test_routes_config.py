from types import SimpleNamespace

import pytest
from flask import Flask

from ui.parsers import parse_purchased_and_produced, valuation_params_from_config
from ui.routes.config import create_config_blueprint


pytestmark = pytest.mark.no_fixture


@pytest.fixture
def config_route_app(tmp_path):
    global_config = {
        "master_filename": "master.xlsm",
        "master_uploaded_at": "2026-04-21T12:00:00",
        "site": "NLX1",
        "forecast_months": 12,
        "unlimited_machines": "PBA99",
        "purchased_and_produced": "MAT-1:0.5",
        "valuation_params": {"1": 10.0},
        "file_defaults": {
            "site": "DEF",
            "forecast_months": 6,
            "unlimited_machines": "PBA01",
            "purchased_and_produced": "MAT-2:1",
            "valuation_params": {"1": 1.0},
        },
    }
    active = {"sess": None, "engine": None}
    save_calls = []
    apply_calls = []

    defaults = {
        "uploads": str(tmp_path / "default_uploads"),
        "exports": str(tmp_path / "default_exports"),
        "sessions": str(tmp_path / "default_sessions"),
    }

    def crash_callback(*args, **kwargs):
        raise RuntimeError("unexpected callback called in config route test")

    def get_active():
        return active["sess"], active["engine"]

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_config_blueprint(
        lambda: defaults,
        global_config,
        lambda: save_calls.append(dict(global_config)),
        lambda uploads, exports, sessions: apply_calls.append((uploads, exports, sessions)),
        lambda: tmp_path / "uploads",
        get_active,
        parse_purchased_and_produced,
        valuation_params_from_config,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
        crash_callback,
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        global_config=global_config,
        active=active,
        defaults=defaults,
        save_calls=save_calls,
        apply_calls=apply_calls,
        tmp_path=tmp_path,
    )


@pytest.fixture
def config_engine_app(tmp_path):
    engine = SimpleNamespace(
        data=SimpleNamespace(
            purchased_and_produced={"MAT-1": 0.5},
            valuation_params=SimpleNamespace(
                direct_fte_cost_per_month=1.0,
                indirect_fte_cost_per_month=2.0,
                overhead_cost_per_month=3.0,
                sga_cost_per_month=4.0,
                depreciation_per_year=5.0,
                net_book_value=6.0,
                days_sales_outstanding=7.0,
                days_payable_outstanding=8.0,
            ),
            periods=["2025-12"],
        ),
        results={},
        value_results={},
        all_purch_raw_needs={},
    )
    sess = {
        "id": "config-engine-session",
        "reset_baseline": {"valuation_params": {"1": 5.0}},
        "pending_edits": {},
        "engine": engine,
    }
    active = {"sess": sess, "engine": engine}
    global_config = {
        "site": "NLX1",
        "forecast_months": 12,
        "unlimited_machines": "",
        "purchased_and_produced": "MAT-1:0.5",
        "valuation_params": {"1": 1.0},
    }
    defaults = {
        "uploads": str(tmp_path / "uploads"),
        "exports": str(tmp_path / "exports"),
        "sessions": str(tmp_path / "sessions"),
    }
    baseline_calls = []
    pap_calls = []
    finish_calls = []
    recalc_calls = []
    rebuild_calls = []
    install_calls = []
    replay_calls = []
    save_calls = []
    state = SimpleNamespace(clean_engine=engine)

    def get_active():
        return active["sess"], active["engine"]

    def ensure_reset_baseline(sess, engine):
        baseline_calls.append((sess, engine))

    def recalc_pap_material(engine, material_number):
        pap_calls.append((engine, material_number))

    def finish_pap_recalc(engine):
        finish_calls.append(engine)

    def recalculate_value_results(engine, sess):
        recalc_calls.append((engine, sess))

    def build_clean_engine_for_session(sess):
        rebuild_calls.append(sess)
        return state.clean_engine

    def install_clean_engine_baseline(sess, engine, clear_machine_overrides=True):
        install_calls.append((sess, engine, clear_machine_overrides))

    def replay_pending_edits(sess, engine):
        replay_calls.append((sess, engine))

    def moq_warnings_payload(engine):
        return {"moq_raw_needs": {}}

    def value_results_payload(engine):
        return {"value_results": {}, "consolidation": []}

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(create_config_blueprint(
        lambda: defaults,
        global_config,
        lambda: save_calls.append(dict(global_config)),
        lambda uploads, exports, sessions: None,
        lambda: tmp_path / "uploads",
        get_active,
        parse_purchased_and_produced,
        valuation_params_from_config,
        ensure_reset_baseline,
        recalc_pap_material,
        finish_pap_recalc,
        recalculate_value_results,
        build_clean_engine_for_session,
        install_clean_engine_baseline,
        replay_pending_edits,
        moq_warnings_payload,
        value_results_payload,
    ))

    return SimpleNamespace(
        app=flask_app,
        client=flask_app.test_client(),
        engine=engine,
        sess=sess,
        active=active,
        global_config=global_config,
        state=state,
        baseline_calls=baseline_calls,
        pap_calls=pap_calls,
        finish_calls=finish_calls,
        recalc_calls=recalc_calls,
        rebuild_calls=rebuild_calls,
        install_calls=install_calls,
        replay_calls=replay_calls,
        save_calls=save_calls,
    )


def test_get_folder_config_returns_saved_values_and_defaults(config_route_app):
    config_route_app.global_config["folders"] = {
        "uploads": "C:/saved/uploads",
        "exports": "C:/saved/exports",
    }

    response = config_route_app.client.get("/api/config/folders")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["uploads"] == "C:/saved/uploads"
    assert payload["exports"] == "C:/saved/exports"
    assert payload["sessions"] == config_route_app.defaults["sessions"]
    assert payload["defaults"] == config_route_app.defaults


def test_save_folder_config_persists_paths_and_applies_them(config_route_app):
    uploads = config_route_app.tmp_path / "custom_uploads"
    exports = config_route_app.tmp_path / "custom_exports"
    sessions = config_route_app.tmp_path / "custom_sessions"

    response = config_route_app.client.post(
        "/api/config/folders",
        json={
            "uploads": str(uploads),
            "exports": str(exports),
            "sessions": str(sessions),
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.get_json() == {"success": True}
    assert config_route_app.global_config["folders"] == {
        "uploads": str(uploads),
        "exports": str(exports),
        "sessions": str(sessions),
    }
    assert uploads.is_dir()
    assert exports.is_dir()
    assert sessions.is_dir()
    assert config_route_app.save_calls
    assert config_route_app.apply_calls == [(uploads, exports, sessions)]


def test_save_folder_config_returns_400_for_uncreatable_path(config_route_app):
    not_a_dir = config_route_app.tmp_path / "not_a_dir"
    not_a_dir.write_text("already a file", encoding="utf-8")

    response = config_route_app.client.post(
        "/api/config/folders",
        json={"uploads": str(not_a_dir)},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["errors"]
    assert not config_route_app.save_calls


def test_get_global_config_returns_public_config_shape(config_route_app):
    master_file = config_route_app.tmp_path / "master.xlsm"
    master_file.write_text("placeholder", encoding="utf-8")
    config_route_app.global_config["master_file"] = str(master_file)

    response = config_route_app.client.get("/api/config")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["master_filename"] == "master.xlsm"
    assert payload["master_file_exists"] is True
    assert payload["site"] == "NLX1"
    assert payload["forecast_months"] == 12
    assert payload["file_defaults"]["forecast_months"] == 6


def test_save_config_settings_without_engine_updates_global_config(config_route_app):
    response = config_route_app.client.post(
        "/api/config/settings",
        json={
            "site": "NEW",
            "forecast_months": "18",
            "unlimited_machines": "PBA01, PBA02",
            "purchased_and_produced": "MAT-9:0.25",
            "valuation_params": {"1": "12.5", "2": None},
        },
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    assert response.get_json() == {"success": True}
    assert config_route_app.global_config["site"] == "NEW"
    assert config_route_app.global_config["forecast_months"] == 18
    assert config_route_app.global_config["unlimited_machines"] == "PBA01, PBA02"
    assert config_route_app.global_config["purchased_and_produced"] == "MAT-9:0.25"
    assert config_route_app.global_config["valuation_params"] == {"1": 12.5}
    assert config_route_app.save_calls


def test_reset_vp_params_requires_active_session(config_route_app):
    response = config_route_app.client.post("/api/config/reset_vp_params")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No active session"


def test_reset_vp_params_requires_calculated_engine(config_route_app):
    config_route_app.active["sess"] = {"id": "config-session"}

    response = config_route_app.client.post("/api/config/reset_vp_params")

    assert response.status_code == 400
    assert response.get_json()["error"] == "No calculations run"


@pytest.mark.no_fixture
def test_save_config_settings_pap_change_recalcs_changed_materials(config_engine_app):
    response = config_engine_app.client.post(
        "/api/config/settings",
        json={"purchased_and_produced": "MAT-1:0.75"},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert config_engine_app.baseline_calls == [(config_engine_app.sess, config_engine_app.engine)]
    assert config_engine_app.pap_calls == [(config_engine_app.engine, "MAT-1")]
    assert config_engine_app.finish_calls == [config_engine_app.engine]
    assert "periods" in payload
    assert "value_results" in payload
    assert "moq_raw_needs" in payload
    assert config_engine_app.global_config["purchased_and_produced"] == "MAT-1:0.75"


@pytest.mark.no_fixture
def test_save_config_settings_pap_unchanged_does_not_recalc_pap(config_engine_app):
    response = config_engine_app.client.post(
        "/api/config/settings",
        json={"purchased_and_produced": "MAT-1:0.5"},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert config_engine_app.pap_calls == []
    assert config_engine_app.finish_calls == []
    assert len(config_engine_app.recalc_calls) == 1
    assert "periods" not in payload
    assert "value_results" in payload


@pytest.mark.no_fixture
def test_save_config_settings_valuation_params_update_recalcs_value_results(config_engine_app):
    response = config_engine_app.client.post(
        "/api/config/settings",
        json={"valuation_params": {"1": "20.0"}},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert len(config_engine_app.recalc_calls) == 1
    assert config_engine_app.global_config["valuation_params"] == {"1": 20.0}
    assert "periods" not in payload
    assert "value_results" in payload


@pytest.mark.no_fixture
def test_save_config_settings_structural_change_rebuilds_engine(config_engine_app):
    response = config_engine_app.client.post(
        "/api/config/settings",
        json={"site": "NLX2"},
    )

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert len(config_engine_app.rebuild_calls) == 1
    assert len(config_engine_app.install_calls) == 1
    assert len(config_engine_app.replay_calls) == 1
    assert config_engine_app.sess["engine"] is config_engine_app.state.clean_engine
    assert "periods" in payload
    assert config_engine_app.global_config["site"] == "NLX2"


@pytest.mark.no_fixture
def test_save_config_settings_structural_change_rebuild_fails_returns_400(config_engine_app):
    config_engine_app.state.clean_engine = None

    response = config_engine_app.client.post(
        "/api/config/settings",
        json={"site": "NLX2"},
    )

    assert response.status_code == 400
    assert "Could not rebuild" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_reset_vp_params_no_baseline_returns_400(config_engine_app):
    config_engine_app.sess["reset_baseline"] = {}

    response = config_engine_app.client.post("/api/config/reset_vp_params")

    assert response.status_code == 400
    assert "No baseline available" in response.get_json()["error"]


@pytest.mark.no_fixture
def test_reset_vp_params_restores_baseline_and_recalcs(config_engine_app):
    response = config_engine_app.client.post("/api/config/reset_vp_params")

    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["valuation_params"] == {"1": 5.0}
    assert "value_results" in payload
    assert "consolidation" in payload
    assert config_engine_app.global_config["valuation_params"] == {"1": 5.0}
    assert len(config_engine_app.recalc_calls) == 1
    assert config_engine_app.save_calls


@pytest.mark.skip(reason="POST /api/config/master-file requires a real .xlsm/.xlsx upload fixture.")
def test_upload_master_file_skipped_by_design(config_route_app):
    config_route_app.client.post("/api/config/master-file")
