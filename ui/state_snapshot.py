"""Snapshot and restore helpers for live planning engine state."""

import copy

from modules.models import LineType, PlanningRow
from ui.parsers import format_purchased_and_produced, valuation_params_from_config
from ui.serializers import row_payload


def row_key_from_obj(obj):
    """Stable row key for matching scenario snapshots back onto live rows."""
    if hasattr(obj, 'material_number'):
        return (
            str(getattr(obj, 'material_number', '')),
            str(getattr(obj, 'line_type', '')),
            str(getattr(obj, 'aux_column', '') if getattr(obj, 'aux_column', None) is not None else ''),
            str(getattr(obj, 'aux_2_column', '') if getattr(obj, 'aux_2_column', None) is not None else ''),
            str(getattr(obj, 'material_name', '')),
        )
    return (
        str(obj.get('material_number', '')),
        str(obj.get('line_type', '')),
        str(obj.get('aux_column', '') if obj.get('aux_column', None) is not None else ''),
        str(obj.get('aux_2_column', '') if obj.get('aux_2_column', None) is not None else ''),
        str(obj.get('material_name', '')),
    )


def planning_row_from_snapshot(snap, fallback_line_type=''):
    """Rebuild a PlanningRow from a persisted scenario snapshot dict."""
    raw_values = snap.get('values', {}) or {}
    values = {}
    for p, v in raw_values.items():
        try:
            values[str(p)] = float(v or 0)
        except (TypeError, ValueError):
            values[str(p)] = 0.0

    raw_edits = snap.get('manual_edits', {}) or {}
    manual_edits = {}
    for p, e in raw_edits.items():
        if not isinstance(e, dict):
            continue
        try:
            original = float(e.get('original', 0))
            new_val = float(e.get('new', original))
        except (TypeError, ValueError):
            continue
        manual_edits[str(p)] = {'original': original, 'new': new_val}

    try:
        starting_stock = float(snap.get('starting_stock', 0) or 0)
    except (TypeError, ValueError):
        starting_stock = 0.0

    return PlanningRow(
        material_number=str(snap.get('material_number', '')),
        material_name=str(snap.get('material_name', '')),
        product_type=str(snap.get('product_type', '')),
        product_family=str(snap.get('product_family', '')),
        spc_product=str(snap.get('spc_product', '')),
        product_cluster=str(snap.get('product_cluster', '')),
        product_name=str(snap.get('product_name', '')),
        line_type=str(snap.get('line_type', '') or fallback_line_type),
        aux_column=snap.get('aux_column', None),
        aux_2_column=snap.get('aux_2_column', None),
        starting_stock=starting_stock,
        values=values,
        manual_edits=manual_edits,
    )


def build_pending_edits_from_results_snapshot(results_snapshot) -> dict:
    """Derive pending_edits payload from scenario result rows."""
    pending = {}
    for lt, rows in (results_snapshot or {}).items():
        for row in rows or []:
            line_type = str(row.get('line_type', '') or lt)
            material = str(row.get('material_number', ''))
            aux = str(row.get('aux_column', '') or '')
            edits = row.get('manual_edits', {}) or {}
            for period, edit in edits.items():
                if not isinstance(edit, dict):
                    continue
                try:
                    original = float(edit.get('original', 0))
                    new_value = float(edit.get('new', original))
                except (TypeError, ValueError):
                    continue
                key = f'{line_type}||{material}||{aux}||{period}'
                pending[key] = {'original': original, 'new_value': new_value}
    return pending


def rebuild_volume_caches_from_results(current_engine) -> None:
    """Keep cache dicts in sync after scenario loads/restores."""
    current_engine.all_production_plans = {
        r.material_number: dict(r.values)
        for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
    }
    current_engine.all_purchase_receipts = {
        r.material_number: dict(r.values)
        for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
    }
    if hasattr(current_engine, 'rebuild_machine_output_caches'):
        current_engine.rebuild_machine_output_caches()
    current_engine._iq_cache = None


def snapshot_engine_state(engine, shift_hours_lookup) -> dict:
    vp = getattr(getattr(engine, 'data', None), 'valuation_params', None)
    vp_snapshot = None
    if vp is not None:
        vp_snapshot = {
            '1': vp.direct_fte_cost_per_month,
            '2': vp.indirect_fte_cost_per_month,
            '3': vp.overhead_cost_per_month,
            '4': vp.sga_cost_per_month,
            '5': vp.depreciation_per_year,
            '6': vp.net_book_value,
            '7': vp.days_sales_outstanding,
            '8': vp.days_payable_outstanding,
        }
    pap = getattr(getattr(engine, 'data', None), 'purchased_and_produced', None)
    pap_snapshot = dict(pap) if pap else {}
    machines_snapshot = {}
    machines = getattr(getattr(engine, 'data', None), 'machines', None)
    if machines:
        for mc_code, machine in machines.items():
            sho = getattr(machine, 'shift_hours_override', None)
            machines_snapshot[mc_code] = {
                'oee': float(machine.oee),
                'availability_by_period': dict(getattr(machine, 'availability_by_period', {}) or {}),
                'shift_hours_override': float(sho) if sho is not None else None,
                'shift_hours_computed': shift_hours_lookup(machine, engine.data),
            }
    return {
        'results': {
            lt: [copy.deepcopy(row_payload(r)) for r in rows]
            for lt, rows in (engine.results or {}).items()
        },
        'value_results': {
            lt: [copy.deepcopy(row_payload(r)) for r in rows]
            for lt, rows in (engine.value_results or {}).items()
        },
        'valuation_params': vp_snapshot,
        'purchased_and_produced': pap_snapshot,
        'machines': machines_snapshot,
    }


def machine_overrides_from_engine(sess, engine) -> dict:
    """Return machine OEE/availability values that differ from the session baseline."""
    data = getattr(engine, 'data', None)
    machines = getattr(data, 'machines', None)
    if not machines:
        return {}
    baseline_machines = ((sess or {}).get('reset_baseline') or {}).get('machines') or {}
    existing_overrides = copy.deepcopy((sess or {}).get('machine_overrides') or {})
    if not baseline_machines:
        return existing_overrides
    overrides = {}

    def _availability_differs(base_avail, cur_avail):
        keys = set((base_avail or {}).keys()) | set((cur_avail or {}).keys())
        for key in keys:
            try:
                if abs(float((base_avail or {}).get(key, 0.0)) - float((cur_avail or {}).get(key, 0.0))) > 1e-9:
                    return True
            except (TypeError, ValueError):
                if (base_avail or {}).get(key) != (cur_avail or {}).get(key):
                    return True
        return False

    for mc_code, machine in machines.items():
        base = baseline_machines.get(mc_code) or {}
        if not base:
            if mc_code in existing_overrides:
                overrides[mc_code] = existing_overrides[mc_code]
            continue
        item = {}
        base_oee = base.get('oee')
        if base_oee is not None and abs(float(machine.oee) - float(base_oee)) > 1e-9:
            item['oee'] = float(machine.oee)
        base_avail = dict(base.get('availability_by_period') or {})
        cur_avail = dict(getattr(machine, 'availability_by_period', {}) or {})
        if base_avail and _availability_differs(base_avail, cur_avail):
            item['availability_by_period'] = cur_avail
        base_sho = base.get('shift_hours_override')
        cur_sho = getattr(machine, 'shift_hours_override', None)
        sho_differs = (base_sho is None) != (cur_sho is None) or (
            base_sho is not None and cur_sho is not None
            and abs(float(base_sho) - float(cur_sho)) > 1e-9
        )
        if sho_differs:
            item['shift_hours_override'] = float(cur_sho) if cur_sho is not None else None
        if item:
            overrides[mc_code] = item
    return overrides


def apply_machine_overrides(engine, machine_overrides: dict) -> bool:
    data = getattr(engine, 'data', None)
    machines = getattr(data, 'machines', None)
    if not machines or not machine_overrides:
        return False
    changed = False
    for mc_code, override in (machine_overrides or {}).items():
        machine = machines.get(mc_code)
        if machine is None:
            continue
        if 'oee' in override:
            new_oee = float(override['oee'])
            if abs(float(machine.oee) - new_oee) > 1e-9:
                machine.oee = new_oee
                changed = True
        if 'availability_by_period' in override:
            new_map = dict(override.get('availability_by_period') or {})
            if dict(getattr(machine, 'availability_by_period', {}) or {}) != new_map:
                machine.availability_by_period = new_map
                changed = True
        if 'shift_hours_override' in override:
            raw = override['shift_hours_override']
            new_sho = float(raw) if raw is not None else None
            if getattr(machine, 'shift_hours_override', None) != new_sho:
                machine.shift_hours_override = new_sho
                changed = True
    return changed


def snapshot_has_manual_edits(snapshot: dict) -> bool:
    for section in ('results', 'value_results'):
        for rows in (snapshot or {}).get(section, {}).values():
            for row in rows or []:
                if (row or {}).get('manual_edits'):
                    return True
    return False


def engine_has_manual_edits(engine) -> bool:
    for rows in (getattr(engine, 'results', {}) or {}).values():
        for row in rows or []:
            if getattr(row, 'manual_edits', None):
                return True
    return False


def restore_engine_state(engine, snapshot: dict, global_config: dict) -> None:
    restored_results = {}
    for lt, snap_rows in (snapshot.get('results') or {}).items():
        restored_results[lt] = [
            planning_row_from_snapshot(snap, fallback_line_type=lt)
            for snap in (snap_rows or [])
        ]
    for lt in (engine.results or {}).keys():
        restored_results.setdefault(lt, [])
    engine.results = restored_results

    restored_value_results = {}
    for lt, snap_rows in (snapshot.get('value_results') or {}).items():
        restored_value_results[lt] = [
            planning_row_from_snapshot(snap, fallback_line_type=lt)
            for snap in (snap_rows or [])
        ]
    for lt in (engine.value_results or {}).keys():
        restored_value_results.setdefault(lt, [])
    engine.value_results = restored_value_results
    rebuild_volume_caches_from_results(engine)

    vp_snap = snapshot.get('valuation_params')
    if vp_snap and getattr(engine, 'data', None) is not None:
        engine.data.valuation_params = valuation_params_from_config(vp_snap)
        global_config['valuation_params'] = {str(k): float(v) for k, v in vp_snap.items()}

    pap_snap = snapshot.get('purchased_and_produced')
    if pap_snap is not None and getattr(engine, 'data', None) is not None:
        engine.data.purchased_and_produced = dict(pap_snap)
        global_config['purchased_and_produced'] = format_purchased_and_produced(pap_snap)

    machines_snap = snapshot.get('machines')
    if machines_snap and getattr(engine, 'data', None) is not None:
        for mc_code, snap in machines_snap.items():
            machine = engine.data.machines.get(mc_code)
            if machine is None:
                continue
            machine.oee = float(snap.get('oee', machine.oee))
            machine.availability_by_period = dict(snap.get('availability_by_period') or {})


def ensure_reset_baseline(sess, engine, shift_hours_lookup) -> None:
    baseline = sess.get('reset_baseline')
    if baseline is None or (snapshot_has_manual_edits(baseline) and not engine_has_manual_edits(engine)):
        sess['reset_baseline'] = snapshot_engine_state(engine, shift_hours_lookup)
