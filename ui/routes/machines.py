"""Machine read/edit routes."""

from typing import Callable

from flask import Blueprint, jsonify, request

from modules.models import LineType


def create_machines_blueprint(
    get_active: Callable[[], tuple],
    machine_overrides_from_engine: Callable[[dict, object], dict],
    shift_hours_lookup: Callable[[object, object], float],
    ensure_reset_baseline: Callable[[dict, object], None],
    recalculate_capacity_and_values: Callable[[object, dict], None],
    planning_value_payload: Callable[[object], dict],
    save_sessions_to_disk: Callable[[], None],
) -> Blueprint:
    bp = Blueprint('machines', __name__)

    @bp.route('/api/machines')
    def get_machines():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        data = current_engine.data
        periods = data.periods
        baseline_machines = (sess.get('reset_baseline') or {}).get('machines') or {}
        machine_overrides = machine_overrides_from_engine(sess, current_engine)
        sess['machine_overrides'] = machine_overrides

        def _avg(values_by_period):
            vals = [value for value in values_by_period.values() if value is not None]
            return sum(vals) / len(vals) if vals else 0.0

        def _avg_percent_from_fraction_map(values_by_period):
            vals = []
            for period in periods:
                if period not in (values_by_period or {}):
                    continue
                try:
                    vals.append(float(values_by_period[period]) * 100.0)
                except (TypeError, ValueError):
                    continue
            return sum(vals) / len(vals) if vals else None

        def _machine_edit_meta(mc_code, machine, avail_p):
            base = baseline_machines.get(mc_code) or {}
            meta = {}

            base_oee = base.get('oee')
            if base_oee is not None:
                original = float(base_oee) * 100.0
                current = float(machine.oee) * 100.0
                delta = current - original
                if abs(delta) > 0.0005:
                    meta['oee'] = {
                        'original': round(original, 3),
                        'new': round(current, 3),
                        'delta_points': round(delta, 3),
                        'original_display': f'{original:.1f}%',
                        'new_display': f'{current:.1f}%',
                        'delta_display': f'{delta:+.1f}pp',
                        'direction': 'up' if delta > 0 else 'down',
                    }

            base_avail = _avg_percent_from_fraction_map(base.get('availability_by_period') or {})
            if base_avail is not None:
                current_avail = _avg(avail_p)
                delta = current_avail - base_avail
                if abs(delta) > 0.0005:
                    meta['availability'] = {
                        'original': round(base_avail, 3),
                        'new': round(current_avail, 3),
                        'delta_points': round(delta, 3),
                        'original_display': f'{base_avail:.1f}%',
                        'new_display': f'{current_avail:.1f}%',
                        'delta_display': f'{delta:+.1f}pp',
                        'direction': 'up' if delta > 0 else 'down',
                    }

            base_shift_computed = base.get('shift_hours_computed')
            if base_shift_computed is not None:
                current_shift = shift_hours_lookup(machine, data)
                delta_sh = current_shift - float(base_shift_computed)
                if abs(delta_sh) > 0.05:
                    meta['shift_hours'] = {
                        'original': round(float(base_shift_computed), 1),
                        'new': round(current_shift, 1),
                        'delta_points': round(delta_sh, 1),
                        'original_display': f'{float(base_shift_computed):.1f}h',
                        'new_display': f'{current_shift:.1f}h',
                        'delta_display': f'{delta_sh:+.1f}h',
                        'direction': 'up' if delta_sh > 0 else 'down',
                    }

            return meta

        util_rows = current_engine.results.get(LineType.UTILIZATION_RATE.value, [])
        util_by_machine = {row.material_name: row.values for row in util_rows}

        cap_rows = current_engine.results.get(LineType.CAPACITY_UTILIZATION.value, [])
        req_hours_by_machine = {}
        for row in cap_rows:
            if row.product_type == 'Machine' and row.material_name in data.machines:
                req_hours_by_machine[row.material_name] = row.values

        # Deduplicate FTE rows by material_number; duplicates are a BOM artifact — keep last (most downstream)
        _fte_dedup = {}
        for row in current_engine.results.get(LineType.FTE_REQUIREMENTS.value, []):
            _fte_dedup[row.material_number] = row
        fte_rows = list(_fte_dedup.values())
        fte_by_group = {row.material_number: row.values for row in fte_rows}

        if not hasattr(current_engine, 'machine_throughput_theo') or not hasattr(current_engine, 'output_by_machine_period'):
            if hasattr(current_engine, 'rebuild_machine_output_caches'):
                current_engine.rebuild_machine_output_caches()
            else:
                machine_throughput_theo = {}
                theo_lists = {}
                for mat_num in list(data.materials.keys()):
                    try:
                        routings = data.get_all_routings(mat_num)
                    except Exception:
                        continue
                    for routing in routings:
                        wc = routing.work_center
                        if routing.base_quantity > 0 and routing.standard_time > 0:
                            theo_lists.setdefault(wc, []).append(routing.base_quantity / routing.standard_time)
                for wc, values in theo_lists.items():
                    machine_throughput_theo[wc] = sum(values) / len(values) if values else 0.0

                prod_plan = current_engine.all_production_plans if hasattr(current_engine, 'all_production_plans') else {}
                output_by_machine_period = {mc: {period: 0.0 for period in periods} for mc in data.machines}
                for mat_num, plan_data in prod_plan.items():
                    try:
                        routings = data.get_all_routings(mat_num)
                    except Exception:
                        continue
                    for routing in routings:
                        wc = routing.work_center
                        if wc not in output_by_machine_period:
                            continue
                        for period in periods:
                            qty = plan_data.get(period, 0.0)
                            if qty > 0:
                                output_by_machine_period[wc][period] += qty
                current_engine.machine_throughput_theo = machine_throughput_theo
                current_engine.output_by_machine_period = output_by_machine_period

        machine_throughput_theo = getattr(current_engine, 'machine_throughput_theo', {}) or {}
        output_by_machine_period = getattr(current_engine, 'output_by_machine_period', {}) or {}

        def _effective_throughput_period(mc_code):
            out_p = output_by_machine_period.get(mc_code, {})
            req_p = req_hours_by_machine.get(mc_code, {})
            result = {}
            for period in periods:
                hours = req_p.get(period, 0.0)
                result[period] = (out_p.get(period, 0.0) / hours) if hours > 0 else 0.0
            return result

        machines_out = []
        for mc_code, machine in data.machines.items():
            req_p = {period: round(req_hours_by_machine.get(mc_code, {}).get(period, 0.0), 2) for period in periods}
            util_p = {period: round(util_by_machine.get(mc_code, {}).get(period, 0.0) * 100, 1) for period in periods}
            avail_p = {period: round(machine.get_availability(period) * 100, 1) for period in periods}
            eff_p = {period: round(value, 2) for period, value in _effective_throughput_period(mc_code).items()}
            shift_hours = shift_hours_lookup(machine, data)
            edit_meta = _machine_edit_meta(mc_code, machine, avail_p)

            machines_out.append({
                'code': mc_code,
                'name': machine.name,
                'group': machine.machine_group or '',
                'oee': round(machine.oee, 3),
                'shift_hours': round(shift_hours, 1),
                'avg_availability': round(_avg(avail_p), 1),
                'req_hours_avg': round(_avg(req_p), 2),
                'util_avg': round(_avg(util_p), 1),
                'throughput_theoretical': round(machine_throughput_theo.get(mc_code, 0.0), 2),
                'throughput_effective': round(_avg(eff_p), 2),
                'req_hours_by_period': req_p,
                'util_by_period': util_p,
                'availability_by_period': avail_p,
                'throughput_effective_by_period': eff_p,
                'edit_meta': edit_meta,
                'has_edits': bool(edit_meta),
            })

        groups_map = {}
        for machine in machines_out:
            group = machine['group'] or '(no group)'
            groups_map.setdefault(group, []).append(machine)

        groups_out = []
        for group, machine_list in groups_map.items():
            req_p = {period: round(sum(machine['req_hours_by_period'][period] for machine in machine_list), 2) for period in periods}
            util_p = {}
            for period in periods:
                total_avail = sum(
                    (machine['availability_by_period'][period] / 100.0) * machine['shift_hours']
                    for machine in machine_list
                )
                total_req = sum(machine['req_hours_by_period'][period] for machine in machine_list)
                util_p[period] = round((total_req / total_avail * 100) if total_avail > 0 else 0.0, 1)
            eff_p = {}
            for period in periods:
                total_out = sum(output_by_machine_period.get(machine['code'], {}).get(period, 0.0) for machine in machine_list)
                total_h = sum(machine['req_hours_by_period'][period] for machine in machine_list)
                eff_p[period] = round((total_out / total_h) if total_h > 0 else 0.0, 2)
            theo_avg = sum(machine['throughput_theoretical'] for machine in machine_list) / len(machine_list) if machine_list else 0.0
            oee_avg = sum(machine['oee'] for machine in machine_list) / len(machine_list) if machine_list else 0.0

            fte_p = fte_by_group.get(group, {})
            fte_p_rounded = {period: round(fte_p.get(period, 0.0), 2) for period in periods}

            groups_out.append({
                'group': group,
                'machines': [machine['code'] for machine in machine_list],
                'avg_oee': round(oee_avg, 3),
                'sum_req_hours_avg': round(_avg(req_p), 2),
                'util_avg': round(_avg(util_p), 1),
                'throughput_theoretical': round(theo_avg, 2),
                'throughput_effective': round(_avg(eff_p), 2),
                'req_hours_by_period': req_p,
                'util_by_period': util_p,
                'throughput_effective_by_period': eff_p,
                'fte_by_period': fte_p_rounded,
                'fte_avg': round(_avg(fte_p_rounded), 2),
            })

        fte_totals = {period: 0.0 for period in periods}
        for row in fte_rows:
            for period in periods:
                fte_totals[period] += row.values.get(period, 0.0)
        fte_totals = {period: round(value, 2) for period, value in fte_totals.items()}

        # FTE rows for truck groups / control room (not covered by any machine group)
        known_groups = {g['group'] for g in groups_out}
        fte_extra = []
        for row in fte_rows:
            if row.material_number not in known_groups:
                fte_p = {period: round(row.values.get(period, 0.0), 2) for period in periods}
                fte_extra.append({
                    'group': row.material_number,
                    'fte_by_period': fte_p,
                    'fte_avg': round(_avg(fte_p), 2),
                })

        return jsonify({
            'periods': periods,
            'machines': machines_out,
            'groups': groups_out,
            'fte_extra': fte_extra,
            'fte_totals_by_period': fte_totals,
            'machine_overrides': machine_overrides,
            'undo_depth': len(sess.get('machine_undo') or []),
            'redo_depth': len(sess.get('machine_redo') or []),
        })

    @bp.route('/api/machines/update', methods=['POST'])
    def update_machine_param():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        req = request.get_json() or {}
        mc_code = (req.get('machine') or '').strip()
        field = (req.get('field') or '').strip()
        try:
            new_value = float(req.get('value'))
        except (TypeError, ValueError):
            return jsonify({'error': 'value must be numeric'}), 400

        machine = current_engine.data.machines.get(mc_code)
        if machine is None:
            return jsonify({'error': f'unknown machine {mc_code}'}), 404

        ensure_reset_baseline(sess, current_engine)

        undo_stack = sess.setdefault('machine_undo', [])
        redo_stack = sess.setdefault('machine_redo', [])
        machine_overrides = sess.setdefault('machine_overrides', {})
        if field == 'oee':
            if not (0 < new_value <= 1.5):
                return jsonify({'error': 'oee must be between 0 and 1.5'}), 400
            old_val = float(machine.oee)
            undo_stack.append({'machine': mc_code, 'field': 'oee', 'old_value': old_val, 'new_value': new_value})
            machine.oee = new_value
            machine_overrides.setdefault(mc_code, {})['oee'] = float(new_value)
        elif field == 'availability':
            if not (0 <= new_value <= 150):
                return jsonify({'error': 'availability must be 0-150'}), 400
            factor = new_value / 100.0
            old_map = dict(machine.availability_by_period or {})
            new_map = {period: factor for period in current_engine.data.periods}
            undo_stack.append({'machine': mc_code, 'field': 'availability',
                               'old_value': old_map, 'new_value': new_map})
            machine.availability_by_period = new_map
            machine_overrides.setdefault(mc_code, {})['availability_by_period'] = dict(machine.availability_by_period)
        elif field == 'shift_hours':
            if not (0 < new_value <= 8760):
                return jsonify({'error': 'shift_hours must be between 0 and 8760'}), 400
            old_override = getattr(machine, 'shift_hours_override', None)
            undo_stack.append({'machine': mc_code, 'field': 'shift_hours',
                               'old_value': old_override, 'new_value': new_value})
            machine.shift_hours_override = new_value
            machine_overrides.setdefault(mc_code, {})['shift_hours_override'] = new_value
        else:
            return jsonify({'error': f'unsupported field {field}'}), 400

        redo_stack.clear()
        if len(undo_stack) > 50:
            del undo_stack[:-50]

        recalculate_capacity_and_values(current_engine, sess)
        sess['machine_overrides'] = machine_overrides_from_engine(sess, current_engine)
        save_sessions_to_disk()

        return jsonify({
            'success': True,
            'undo_depth': len(undo_stack),
            'redo_depth': 0,
            **planning_value_payload(current_engine),
        })

    @bp.route('/api/machines/undo', methods=['POST'])
    def undo_machine_param():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        undo_stack = sess.get('machine_undo') or []
        redo_stack = sess.setdefault('machine_redo', [])
        if not undo_stack:
            return jsonify({'success': False, 'message': 'Nothing to undo', 'undo_depth': 0,
                            'redo_depth': len(redo_stack)})

        entry = undo_stack.pop()
        machine = current_engine.data.machines.get(entry['machine'])
        if machine is None:
            return jsonify({'error': f'unknown machine {entry["machine"]}'}), 404

        if entry['field'] == 'oee':
            machine.oee = float(entry['old_value'])
        elif entry['field'] == 'availability':
            old_map = entry['old_value'] or {}
            machine.availability_by_period = dict(old_map)
        elif entry['field'] == 'shift_hours':
            old_override = entry['old_value']
            machine.shift_hours_override = float(old_override) if old_override is not None else None
        else:
            return jsonify({'error': f'unsupported undo field {entry["field"]}'}), 400

        redo_stack.append(entry)
        if len(redo_stack) > 50:
            del redo_stack[:-50]

        sess['machine_overrides'] = machine_overrides_from_engine(sess, current_engine)
        recalculate_capacity_and_values(current_engine, sess)
        save_sessions_to_disk()

        return jsonify({
            'success': True,
            'undo_depth': len(undo_stack),
            'redo_depth': len(redo_stack),
            'restored': entry,
            **planning_value_payload(current_engine),
        })

    @bp.route('/api/machines/reset', methods=['POST'])
    def reset_machine_params():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        baseline = sess.get('reset_baseline') or {}
        machines_snap = baseline.get('machines') or {}
        if not machines_snap:
            return jsonify({'success': False, 'message': 'No baseline available'}), 400

        for mc_code, snap in machines_snap.items():
            machine = current_engine.data.machines.get(mc_code)
            if machine is None:
                continue
            machine.oee = float(snap.get('oee', machine.oee))
            machine.availability_by_period = dict(snap.get('availability_by_period') or {})
            raw_sho = snap.get('shift_hours_override')
            machine.shift_hours_override = float(raw_sho) if raw_sho is not None else None

        sess['machine_undo'] = []
        sess['machine_redo'] = []
        sess['machine_overrides'] = {}
        recalculate_capacity_and_values(current_engine, sess)
        save_sessions_to_disk()
        return jsonify({'success': True, 'undo_depth': 0, 'redo_depth': 0, **planning_value_payload(current_engine)})

    @bp.route('/api/machines/redo', methods=['POST'])
    def redo_machine_param():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        redo_stack = sess.get('machine_redo') or []
        undo_stack = sess.setdefault('machine_undo', [])
        if not redo_stack:
            return jsonify({'success': False, 'message': 'Nothing to redo', 'redo_depth': 0,
                            'undo_depth': len(undo_stack)})

        entry = redo_stack.pop()
        machine = current_engine.data.machines.get(entry['machine'])
        if machine is None:
            return jsonify({'error': f'unknown machine {entry["machine"]}'}), 404

        new_val = entry.get('new_value')
        if new_val is None:
            return jsonify({'error': 'Cannot redo: entry missing new_value (old session data)'}), 400

        if entry['field'] == 'oee':
            machine.oee = float(new_val)
        elif entry['field'] == 'availability':
            machine.availability_by_period = dict(new_val)
        elif entry['field'] == 'shift_hours':
            machine.shift_hours_override = float(new_val)
        else:
            return jsonify({'error': f'unsupported redo field {entry["field"]}'}), 400

        undo_stack.append(entry)
        if len(undo_stack) > 50:
            del undo_stack[:-50]

        sess['machine_overrides'] = machine_overrides_from_engine(sess, current_engine)
        recalculate_capacity_and_values(current_engine, sess)
        save_sessions_to_disk()

        return jsonify({
            'success': True,
            'undo_depth': len(undo_stack),
            'redo_depth': len(redo_stack),
            'restored': entry,
            **planning_value_payload(current_engine),
        })

    return bp
