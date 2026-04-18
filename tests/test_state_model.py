"""State model tests.

These tests pin down the session-level invariants that the web UI depends on
but that the golden pipeline test cannot see. They exercise the Python helpers
directly — no Flask, no browser — so they run in milliseconds and stay robust
across route refactors.

First test in this file covers the partial-reset invariant: resetting machine
state must not touch planning edits (pending_edits, edited rows). From the
Excel scenario matrix: "Click Reset on Machines tab — ALL machine OEE /
availability back to original; planning tab demand edits STILL present."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.planning_engine import PlanningEngine
from ui.state_snapshot import (
    ensure_reset_baseline,
    snapshot_engine_state,
)


# --- The shift-hours lookup used by snapshot/reset ------------------------
# `ui/app.py` defines this as a local function. Reproducing it here keeps the
# test decoupled from Flask bootstrapping. If the definition in app.py ever
# changes, this will drift — update both sides.

def _shift_hours_lookup_fallback(machine, data):
    if machine is None:
        return 520.0
    sho = getattr(machine, 'shift_hours_override', None)
    if sho is not None:
        return float(sho)
    from modules.models import SHIFT_HOURS
    try:
        key = machine.shift_system.value if hasattr(machine.shift_system, 'value') else machine.shift_system
        if isinstance(key, str) and key in data.shift_hours:
            return data.shift_hours[key]
    except Exception:
        pass
    return SHIFT_HOURS.get(machine.shift_system, 520.0)


# --- Fixtures -------------------------------------------------------------

@pytest.fixture(scope="module")
def fresh_engine(golden_fixture_path):
    """Build a fresh PlanningEngine from the golden fixture.

    Reuses `golden_fixture_path` from tests/conftest.py so we inherit the skip
    behaviour when SOP_GOLDEN_FIXTURE isn't set.
    """
    engine = PlanningEngine(
        str(golden_fixture_path),
        planning_month="2025-12",
        months_actuals=11,
        months_forecast=12,
    )
    engine.run()
    return engine


@pytest.fixture
def session_with_engine(fresh_engine):
    """Return (sess dict, engine) with a reset_baseline already captured.

    Each test gets its own session dict so state doesn't leak between tests,
    but they share the engine — it's expensive to build and we don't mutate
    the engine in a way that invalidates it between tests.
    """
    sess: dict = {
        'pending_edits': {},
        'machine_overrides': {},
        'machine_undo': [],
        'machine_redo': [],
    }
    ensure_reset_baseline(sess, fresh_engine, _shift_hours_lookup_fallback)
    return sess, fresh_engine


# --- Helpers that reproduce production logic -----------------------------
# We reproduce (not import) the mutating logic from the production routes so
# the test remains stable across blueprint refactors. The shapes below MUST
# match what ui/routes/machines.py does — if they drift, the test is wrong.

def _apply_machine_oee_override(sess, engine, machine_code: str, new_oee: float) -> None:
    """Mimic `/api/machines/update` with field='oee' — mutation only, no cascade."""
    machine = engine.data.machines[machine_code]
    sess.setdefault('machine_overrides', {}).setdefault(machine_code, {})['oee'] = float(new_oee)
    machine.oee = float(new_oee)


def _reset_machines(sess, engine) -> None:
    """Mimic `/api/machines/reset` — replay baseline into engine machines and
    clear sess machine_* fields. Does NOT touch pending_edits or edited rows.

    This mirrors ui/routes/machines.py:reset_machine_params minus the
    capacity/value recalc and the HTTP response.
    """
    baseline = sess.get('reset_baseline') or {}
    machines_snap = baseline.get('machines') or {}
    for mc_code, snap in machines_snap.items():
        machine = engine.data.machines.get(mc_code)
        if machine is None:
            continue
        machine.oee = float(snap.get('oee', machine.oee))
        machine.availability_by_period = dict(snap.get('availability_by_period') or {})
        raw_sho = snap.get('shift_hours_override')
        machine.shift_hours_override = float(raw_sho) if raw_sho is not None else None
    sess['machine_undo'] = []
    sess['machine_redo'] = []
    sess['machine_overrides'] = {}


# --- The test ------------------------------------------------------------

def test_partial_reset_preserves_planning_edits(session_with_engine):
    """Resetting machine state must not touch planning edits.

    From CLAUDE.md state model: reset_baseline + machine_overrides live in
    the session dict alongside pending_edits. The /api/machines/reset path
    must only clear the machine-related entries. This test pins down that
    separation.
    """
    sess, engine = session_with_engine

    # Pick any real machine and capture its pre-mutation OEE so we can
    # verify reset truly restored it.
    machine_code = next(iter(engine.data.machines))
    original_oee = float(engine.data.machines[machine_code].oee)

    # --- 1. Mutate a machine parameter -----------------------------------
    new_oee = 0.42  # arbitrary, distinctly different from any real baseline
    assert original_oee != new_oee, "Pick a different new_oee so mutation is detectable"
    _apply_machine_oee_override(sess, engine, machine_code, new_oee)

    # Sanity: the mutation landed where we expect
    assert sess['machine_overrides'].get(machine_code, {}).get('oee') == new_oee
    assert engine.data.machines[machine_code].oee == new_oee

    # --- 2. Stage a planning edit ----------------------------------------
    # We write directly into pending_edits rather than calling
    # _apply_volume_change, because that function is Flask-bound. We only
    # care here that reset does NOT touch this dict, so an unrelated-looking
    # entry is enough.
    planning_edit_key = "01. Demand forecast||TESTMAT-001||parent-x||2026-03"
    planning_edit_value = {'original': 100.0, 'new_value': 250.0}
    sess['pending_edits'][planning_edit_key] = dict(planning_edit_value)

    # --- 3. Reset machines -----------------------------------------------
    _reset_machines(sess, engine)

    # --- 4. Machine state MUST be back at baseline -----------------------
    assert sess['machine_overrides'] == {}, "machine_overrides should be cleared by reset"
    assert sess['machine_undo'] == [], "machine_undo should be cleared by reset"
    assert sess['machine_redo'] == [], "machine_redo should be cleared by reset"
    assert engine.data.machines[machine_code].oee == pytest.approx(original_oee), (
        f"Machine {machine_code} OEE not restored: expected {original_oee}, "
        f"got {engine.data.machines[machine_code].oee}"
    )

    # --- 5. Planning edit MUST be untouched ------------------------------
    assert planning_edit_key in sess['pending_edits'], (
        "Reset leaked into pending_edits — planning edit was cleared "
        "when only machine state should have been reset."
    )
    assert sess['pending_edits'][planning_edit_key] == planning_edit_value, (
        "pending_edits entry was mutated by machine reset. This is the "
        "'reset only affects machines' invariant broken."
    )


# --- Engine-results normaliser (copied from tests/generate_baseline.py) -----
# Not imported because generate_baseline.py is a script, not a module. Both
# copies must stay in sync if the serialisation format ever changes.

def _engine_to_comparable(engine) -> dict:
    out: dict = {}
    for line_type, rows in engine.results.items():
        per_line: dict = {}
        for row in rows:
            per_line[row.material_number] = {
                period: round(value, 6)
                for period, value in sorted(row.values.items())
            }
        out[line_type] = dict(sorted(per_line.items()))
    return dict(sorted(out.items()))


def _rounded_utilization_by_machine(engine) -> dict:
    from modules.models import LineType

    return {
        row.material_name: {
            period: round(value * 100.0, 1)
            for period, value in sorted(row.values.items())
        }
        for row in engine.results.get(LineType.UTILIZATION_RATE.value, [])
    }


def _fte_totals_by_period(engine) -> dict:
    from modules.models import LineType

    periods = engine.data.periods
    totals = {period: 0.0 for period in periods}
    for row in engine.results.get(LineType.FTE_REQUIREMENTS.value, []):
        for period in periods:
            totals[period] += row.values.get(period, 0.0)
    return {period: round(value, 2) for period, value in totals.items()}


def _first_nonzero_period(row):
    return next((period for period, value in sorted(row.values.items()) if value > 0), None)


def _has_routing(engine, material_number: str) -> bool:
    try:
        return bool(engine.data.get_all_routings(material_number))
    except Exception:
        return False


def _find_routed_nonzero_row(engine, line_type: str, excluded_materials=None):
    excluded_materials = set(excluded_materials or ())
    for row in engine.results.get(line_type, []):
        if row.material_number in excluded_materials:
            continue
        if _first_nonzero_period(row) is None:
            continue
        if _has_routing(engine, row.material_number):
            return row
    return None


def _find_result_row(engine, line_type: str, material_number: str, aux_column: str):
    material_rows = [
        row for row in engine.results.get(line_type, [])
        if str(getattr(row, 'material_number', '')) == str(material_number)
    ]
    target = next(
        (row for row in material_rows
         if str(getattr(row, 'aux_column', '') or '').strip() == aux_column),
        None,
    )
    if target is None and len(material_rows) == 1:
        return material_rows[0]
    return target


def _mutation_callback_called(*args, **kwargs):
    raise RuntimeError("mutation callback called in read-only test")


def test_cross_tab_consistency(golden_fixture_path):
    """Machines-tab values must match the same engine state used by Planning."""
    from flask import Flask

    from modules.models import LineType
    from modules.planning_engine import PlanningEngine
    from ui.app import (
        SHIFT_HOURS_LOOKUP_FALLBACK,
        _apply_volume_change,
        _machine_overrides_from_engine,
        app,
    )
    from ui.routes.machines import create_machines_blueprint

    engine = PlanningEngine(
        str(golden_fixture_path),
        planning_month="2025-12",
        months_actuals=11,
        months_forecast=12,
    )
    engine.run()

    l01_row = _find_routed_nonzero_row(engine, LineType.DEMAND_FORECAST.value)
    if l01_row is None:
        pytest.skip("No routed L01 material with non-zero values found in golden fixture")
    l01_period = _first_nonzero_period(l01_row)
    l01_orig = l01_row.values[l01_period]
    l01_aux = str(getattr(l01_row, 'aux_column', '') or '').strip()

    l06_row = _find_routed_nonzero_row(
        engine,
        LineType.PRODUCTION_PLAN.value,
        excluded_materials={l01_row.material_number},
    )
    if l06_row is None:
        pytest.skip("No routed L06 material distinct from L01 with non-zero values found in golden fixture")
    l06_period = _first_nonzero_period(l06_row)
    l06_orig = l06_row.values[l06_period]
    l06_aux = str(getattr(l06_row, 'aux_column', '') or '').strip()

    before_utilization = _rounded_utilization_by_machine(engine)
    sess_cross_tab: dict = {'pending_edits': {}, 'undo_stack': [], 'redo_stack': []}

    with app.app_context():
        _apply_volume_change(
            sess_cross_tab,
            engine,
            LineType.DEMAND_FORECAST.value,
            l01_row.material_number,
            l01_period,
            l01_orig * 1.5,
            aux_column=l01_aux,
        )
        _apply_volume_change(
            sess_cross_tab,
            engine,
            LineType.PRODUCTION_PLAN.value,
            l06_row.material_number,
            l06_period,
            l06_orig * 2,
            aux_column=l06_aux,
        )

    l06_after_row = _find_result_row(
        engine,
        LineType.PRODUCTION_PLAN.value,
        l06_row.material_number,
        l06_aux,
    )
    if l06_after_row is None:
        pytest.fail(
            "Line 06 edit target disappeared after edit: "
            f"{l06_row.material_number} / {l06_period} / aux {l06_aux!r}"
        )
    if l06_after_row.values.get(l06_period, 0.0) == pytest.approx(l06_orig, abs=0.000001):
        with app.app_context():
            _apply_volume_change(
                sess_cross_tab,
                engine,
                LineType.PRODUCTION_PLAN.value,
                l06_row.material_number,
                l06_period,
                l06_orig * 10,
                aux_column=l06_aux,
            )
        l06_after_row = _find_result_row(
            engine,
            LineType.PRODUCTION_PLAN.value,
            l06_row.material_number,
            l06_aux,
        )
        if l06_after_row is None or l06_after_row.values.get(l06_period, 0.0) == pytest.approx(l06_orig, abs=0.000001):
            pytest.skip(
                "Routed L06 material did not change after ceiling-rounded edits: "
                f"{l06_row.material_number} / {l06_period}"
            )

    after_utilization = _rounded_utilization_by_machine(engine)
    assert after_utilization != before_utilization, (
        "Sanity check failed: utilization did not change after routed L01/L06 edits.\n"
        f"Edit 1: {LineType.DEMAND_FORECAST.value} / {l01_row.material_number}"
        f" / {l01_period}: {l01_orig} -> {l01_orig * 1.5}\n"
        f"Edit 2: {LineType.PRODUCTION_PLAN.value} / {l06_row.material_number}"
        f" / {l06_period}: {l06_orig} -> {l06_after_row.values.get(l06_period)}"
    )

    def get_active_cross_tab():
        return sess_cross_tab, engine

    test_app = Flask(__name__)
    test_app.register_blueprint(create_machines_blueprint(
        get_active_cross_tab,
        _machine_overrides_from_engine,
        lambda machine, data: SHIFT_HOURS_LOOKUP_FALLBACK(machine, data),
        _mutation_callback_called,
        _mutation_callback_called,
        _mutation_callback_called,
        _mutation_callback_called,
    ))

    response = test_app.test_client().get('/api/machines')
    assert response.status_code == 200, response.get_json(silent=True) or response.get_data(as_text=True)
    payload = response.get_json()

    expected_utilization = _rounded_utilization_by_machine(engine)
    for machine in payload['machines']:
        machine_code = machine['code']
        assert machine_code in expected_utilization, (
            f"Machines response included {machine_code}, but no "
            f"{LineType.UTILIZATION_RATE.value} row exists in engine.results"
        )
        for period in payload['periods']:
            assert machine['util_by_period'][period] == pytest.approx(
                expected_utilization[machine_code].get(period, 0.0),
                abs=0.05,
            ), (
                f"Utilization mismatch for machine {machine_code} / {period}: "
                "Machines tab payload drifted from planning engine results"
            )

    expected_fte_totals = _fte_totals_by_period(engine)
    for period in payload['periods']:
        assert payload['fte_totals_by_period'][period] == pytest.approx(
            expected_fte_totals[period],
            abs=0.005,
        ), (
            f"FTE total mismatch for {period}: Machines tab payload drifted "
            f"from {LineType.FTE_REQUIREMENTS.value} engine results"
        )


def test_replay_matches_live_edits(golden_fixture_path):
    """Replay invariant: replay_pending_edits must produce the same results as live edits.

    From CLAUDE.md: 'replay path is the source of truth — if live behavior
    diverges from replay, the live behavior is wrong.'

    We build engine A, apply two edits via _apply_volume_change (the real live
    path, with Flask context), capture pending_edits and results, then replay
    those same pending_edits on a fresh engine B and assert identical results.

    Both engines are built inside this function rather than shared fixtures
    because _apply_volume_change mutates engine state in-place. Sharing engine A
    with the module-scoped fresh_engine would contaminate other tests.
    """
    import copy

    from modules.models import LineType
    from modules.planning_engine import PlanningEngine
    from ui.app import (
        _apply_machine_overrides,
        _apply_volume_change,
        _recalculate_capacity_and_values,
        app,
    )
    from ui.replay import replay_pending_edits

    # --- Build engine A ---------------------------------------------------
    engine_a = PlanningEngine(
        str(golden_fixture_path),
        planning_month="2025-12",
        months_actuals=11,
        months_forecast=12,
    )
    engine_a.run()

    # --- Select materials -------------------------------------------------
    # Edit 1: L01 demand forecast — prefer a BOM child so the cascade is exercised.
    bom_components = {item.component_material for item in engine_a.data.bom}
    l01_rows = engine_a.results.get(LineType.DEMAND_FORECAST.value, [])

    edit1_row = next(
        (r for r in l01_rows
         if r.material_number in bom_components
         and any(v > 0 for v in r.values.values())),
        None,
    ) or next(
        (r for r in l01_rows if any(v > 0 for v in r.values.values())),
        None,
    )
    if edit1_row is None:
        pytest.skip("No L01 material with non-zero values found in golden fixture")

    edit1_period = next(p for p, v in sorted(edit1_row.values.items()) if v > 0)
    edit1_orig = edit1_row.values[edit1_period]
    edit1_aux = str(getattr(edit1_row, 'aux_column', '') or '').strip()

    # Edit 2: L06 production plan — different material from edit 1.
    l06_rows = engine_a.results.get(LineType.PRODUCTION_PLAN.value, [])
    edit2_row = next(
        (r for r in l06_rows
         if r.material_number != edit1_row.material_number
         and any(v > 0 for v in r.values.values())),
        None,
    )
    if edit2_row is None:
        pytest.skip("No L06 material (distinct from edit 1) found in golden fixture")

    edit2_period = next(p for p, v in sorted(edit2_row.values.items()) if v > 0)
    edit2_orig = edit2_row.values[edit2_period]
    edit2_aux = str(getattr(edit2_row, 'aux_column', '') or '').strip()

    # --- Snapshot baseline (before edits) ---------------------------------
    baseline_results = _engine_to_comparable(engine_a)

    # --- Apply edits live on engine A ------------------------------------
    sess_a: dict = {'pending_edits': {}, 'undo_stack': [], 'redo_stack': []}

    with app.app_context():
        _apply_volume_change(
            sess_a, engine_a,
            LineType.DEMAND_FORECAST.value,
            edit1_row.material_number,
            edit1_period,
            edit1_orig * 1.5,
            aux_column=edit1_aux,
        )
        _apply_volume_change(
            sess_a, engine_a,
            LineType.PRODUCTION_PLAN.value,
            edit2_row.material_number,
            edit2_period,
            edit2_orig + 100,
            aux_column=edit2_aux,
        )

    live_results = _engine_to_comparable(engine_a)

    # --- Build engine B (fresh) and replay --------------------------------
    engine_b = PlanningEngine(
        str(golden_fixture_path),
        planning_month="2025-12",
        months_actuals=11,
        months_forecast=12,
    )
    engine_b.run()

    sess_b: dict = {
        'pending_edits': copy.deepcopy(sess_a['pending_edits']),
        'undo_stack': [],
        'redo_stack': [],
        'value_aux_overrides': {},   # replay checks bool(value_aux_overrides)
        'machine_overrides': {},     # replay checks bool(machine_overrides)
    }

    with app.app_context():
        replay_pending_edits(
            sess_b,
            engine_b,
            _apply_volume_change,
            _apply_machine_overrides,
            _recalculate_capacity_and_values,
        )

    replayed_results = _engine_to_comparable(engine_b)

    # --- Assertions -------------------------------------------------------
    assert live_results == replayed_results, (
        "Replay invariant broken: replayed results differ from live results.\n"
        f"Edit 1: {LineType.DEMAND_FORECAST.value} / {edit1_row.material_number}"
        f" / {edit1_period}: {edit1_orig} -> {edit1_orig * 1.5}\n"
        f"Edit 2: {LineType.PRODUCTION_PLAN.value} / {edit2_row.material_number}"
        f" / {edit2_period}: {edit2_orig} -> {edit2_orig + 100}\n"
        f"pending_edits keys: {list(sess_a['pending_edits'].keys())}"
    )
    # Sanity: if this triggers, replay made no changes — the test would be vacuous.
    assert replayed_results != baseline_results, (
        "Sanity check failed: replay results equal baseline — "
        "replay_pending_edits made no changes despite non-empty pending_edits. "
        f"pending_edits keys: {list(sess_a['pending_edits'].keys())}"
    )
