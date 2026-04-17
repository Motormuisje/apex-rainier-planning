"""Planning edit routes.

The volume cascade itself stays in ui.app for now and is injected here as
apply_volume_change. This keeps this module as route orchestration only.
"""

import io
import json
from datetime import datetime
from typing import Callable

from flask import Blueprint, jsonify, request, send_file

from modules.models import LineType


def create_edits_blueprint(
    get_active: Callable[[], tuple],
    value_aux_editable_line_types: set,
    global_config: dict,
    apply_volume_change: Callable[..., object],
    ensure_reset_baseline: Callable[[dict, object], None],
    recalculate_value_results: Callable[[object, dict], None],
    save_sessions_to_disk: Callable[[], None],
    valuation_params_from_config: Callable[[object], object],
    restore_engine_state: Callable[[object, dict], None],
    snapshot_has_manual_edits: Callable[[dict], bool],
    build_clean_engine_for_session: Callable[[dict], object],
    install_clean_engine_baseline: Callable[[dict, object], None],
) -> Blueprint:
    bp = Blueprint('edits', __name__)

    @bp.route('/api/update_volume', methods=['POST'])
    def update_volume():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON body'}), 400

        line_type = data.get('line_type')
        material_number = data.get('material_number')
        period = data.get('period')
        aux_column = str(data.get('aux_column', '') or '')
        new_value = float(data.get('new_value', 0))

        return apply_volume_change(
            sess,
            current_engine,
            line_type,
            material_number,
            period,
            new_value,
            aux_column=aux_column,
            push_undo=True,
        )

    @bp.route('/api/update_value_aux', methods=['POST'])
    def update_value_aux():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        data = request.get_json() or {}
        line_type = data.get('line_type')
        material_number = data.get('material_number')
        try:
            new_value = float(data.get('new_value', 0))
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid aux value'}), 400

        if line_type not in value_aux_editable_line_types:
            return jsonify({'error': f'Value aux for line type "{line_type}" is not editable'}), 403

        rows = current_engine.value_results.get(line_type, [])
        target_row = next((row for row in rows if row.material_number == material_number), None)
        if target_row is None:
            return jsonify({'error': 'Value row not found'}), 404
        ensure_reset_baseline(sess, current_engine)

        try:
            old_value = float(target_row.aux_column or 0)
        except (TypeError, ValueError):
            return jsonify({'error': 'Current aux value is not numeric'}), 400

        override_key = f'{line_type}||{material_number}'
        overrides = sess.setdefault('value_aux_overrides', {})
        existing = overrides.get(override_key, {})
        original_value = float(existing.get('original', old_value)) if isinstance(existing, dict) else old_value

        if abs(new_value - original_value) < 1e-9:
            overrides.pop(override_key, None)
        else:
            overrides[override_key] = {
                'original': original_value,
                'new_value': new_value,
            }

        recalculate_value_results(current_engine, sess)
        save_sessions_to_disk()

        value_results_dict = {
            line_type_key: [row.to_dict() for row in rows_value]
            for line_type_key, rows_value in current_engine.value_results.items()
        }
        consolidation = [
            row.to_dict()
            for row in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])
        ]
        delta_pct = round((new_value - original_value) / abs(original_value) * 100, 2) if original_value != 0 else 0.0

        return jsonify({
            'success': True,
            'value_results': value_results_dict,
            'consolidation': consolidation,
            'edit_meta': {
                'old_value': old_value,
                'new_value': new_value,
                'original_value': original_value,
                'delta_pct': delta_pct,
            },
            'value_aux_overrides': sess.get('value_aux_overrides', {}),
        })

    @bp.route('/api/reset_value_planning_edits', methods=['POST'])
    def reset_value_planning_edits():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        sess['value_aux_overrides'] = {}

        baseline_vp = (sess.get('reset_baseline') or {}).get('valuation_params')
        restored_vp = None
        if baseline_vp and getattr(current_engine, 'data', None) is not None:
            current_engine.data.valuation_params = valuation_params_from_config(baseline_vp)
            global_config.setdefault('valuation_params', {})
            global_config['valuation_params'] = {str(k): float(v) for k, v in baseline_vp.items()}
            restored_vp = baseline_vp

        recalculate_value_results(current_engine, sess)
        save_sessions_to_disk()

        value_results_dict = {
            line_type_key: [row.to_dict() for row in rows_value]
            for line_type_key, rows_value in current_engine.value_results.items()
        }
        consolidation = [
            row.to_dict()
            for row in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])
        ]

        resp = {
            'success': True,
            'value_results': value_results_dict,
            'consolidation': consolidation,
            'value_aux_overrides': {},
        }
        if restored_vp is not None:
            resp['restored_valuation_params'] = restored_vp
        return jsonify(resp)

    @bp.route('/api/undo', methods=['POST'])
    def undo_edit():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400
        undo_stack = sess.get('undo_stack', [])
        redo_stack = sess.setdefault('redo_stack', [])
        if not undo_stack:
            return jsonify({'error': 'Nothing to undo'}), 400

        entry = undo_stack.pop()
        redo_stack.append(entry)
        if len(redo_stack) > 50:
            redo_stack.pop(0)

        return apply_volume_change(
            sess,
            current_engine,
            entry['line_type'],
            entry['material_number'],
            entry['period'],
            entry['old_value'],
            aux_column=entry.get('aux_column', ''),
            push_undo=False,
        )

    @bp.route('/api/redo', methods=['POST'])
    def redo_edit():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400
        undo_stack = sess.setdefault('undo_stack', [])
        redo_stack = sess.get('redo_stack', [])
        if not redo_stack:
            return jsonify({'error': 'Nothing to redo'}), 400

        entry = redo_stack.pop()
        undo_stack.append(entry)
        if len(undo_stack) > 50:
            undo_stack.pop(0)

        return apply_volume_change(
            sess,
            current_engine,
            entry['line_type'],
            entry['material_number'],
            entry['period'],
            entry['new_value'],
            aux_column=entry.get('aux_column', ''),
            push_undo=False,
        )

    @bp.route('/api/edits/export')
    def export_edits():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        edits = []
        for _, rows in current_engine.results.items():
            for row in rows:
                if row.manual_edits:
                    for period, edit_data in row.manual_edits.items():
                        original = edit_data.get('original', 0.0)
                        new_value = edit_data.get('new', 0.0)
                        delta_pct = round((new_value - original) / abs(original) * 100, 2) if original != 0 else 0.0
                        edits.append({
                            'line_type': row.line_type,
                            'material_number': row.material_number,
                            'aux_column': getattr(row, 'aux_column', '') or '',
                            'period': period,
                            'original': original,
                            'new': new_value,
                            'delta_pct': delta_pct,
                        })

        value_aux_edits = []
        for key, item in (sess or {}).get('value_aux_overrides', {}).items():
            try:
                line_type, material_number = key.split('||', 1)
                original = float(item.get('original', 0))
                new_value = float(item.get('new_value', original))
            except (AttributeError, TypeError, ValueError):
                continue
            delta_pct = round((new_value - original) / abs(original) * 100, 2) if original != 0 else 0.0
            value_aux_edits.append({
                'line_type': line_type,
                'material_number': material_number,
                'original': original,
                'new': new_value,
                'delta_pct': delta_pct,
            })

        export_data = {
            'exported_at': datetime.now().isoformat(),
            'edits': edits,
            'value_aux_edits': value_aux_edits,
        }
        buf = io.BytesIO(json.dumps(export_data, indent=2).encode('utf-8'))
        buf.seek(0)
        return send_file(buf, mimetype='application/json', as_attachment=True, download_name='edits.json')

    @bp.route('/api/edits/import', methods=['POST'])
    def import_edits():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No JSON body'}), 400

            edits = data.get('edits', [])
            for edit in edits:
                line_type = edit.get('line_type')
                material_number = edit.get('material_number')
                period = edit.get('period')
                new_value = float(edit.get('new', 0))
                aux_column = str(edit.get('aux_column', '') or '')
                resp = apply_volume_change(
                    sess,
                    current_engine,
                    line_type,
                    material_number,
                    period,
                    new_value,
                    aux_column=aux_column,
                    push_undo=False,
                )
                if resp.status_code >= 400:
                    payload = resp.get_json(silent=True) or {}
                    return jsonify({'error': f'Could not import edit: {payload.get("error", "unknown error")}'}), resp.status_code

            value_aux_edits = data.get('value_aux_edits', [])
            if value_aux_edits:
                overrides = sess.setdefault('value_aux_overrides', {})
                for edit in value_aux_edits:
                    line_type = edit.get('line_type')
                    material_number = edit.get('material_number')
                    if line_type not in value_aux_editable_line_types:
                        continue
                    try:
                        original = float(edit.get('original', 0))
                        new_value = float(edit.get('new', original))
                    except (TypeError, ValueError):
                        continue
                    key = f'{line_type}||{material_number}'
                    if abs(new_value - original) < 1e-9:
                        overrides.pop(key, None)
                    else:
                        overrides[key] = {
                            'original': original,
                            'new_value': new_value,
                        }

            recalculate_value_results(current_engine, sess)
            save_sessions_to_disk()

            results_dict = {
                line_type_key: [row.to_dict() for row in rows]
                for line_type_key, rows in current_engine.results.items()
            }
            value_results_dict = {
                line_type_key: [row.to_dict() for row in rows]
                for line_type_key, rows in current_engine.value_results.items()
            }
            consolidation = [
                row.to_dict()
                for row in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])
            ]

            return jsonify({
                'success': True,
                'results': results_dict,
                'value_results': value_results_dict,
                'consolidation': consolidation,
                'value_aux_overrides': sess.get('value_aux_overrides', {}),
            })
        except Exception as exc:
            import traceback
            return jsonify({'error': str(exc), 'trace': traceback.format_exc()}), 500

    @bp.route('/api/reset_edits', methods=['POST'])
    def reset_edits():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        baseline = sess.get('reset_baseline')
        baseline_is_clean = (
            isinstance(baseline, dict)
            and baseline.get('results')
            and not snapshot_has_manual_edits(baseline)
        )
        if baseline_is_clean:
            restore_engine_state(current_engine, baseline)
            engine = current_engine
        else:
            engine = build_clean_engine_for_session(sess)
            if engine is None:
                return jsonify({'error': 'No clean reset baseline available. Recalculate this session first.'}), 400
            sess['engine'] = engine

        sess['pending_edits'] = {}
        sess['value_aux_overrides'] = {}
        sess['undo_stack'] = []
        sess['redo_stack'] = []
        recalculate_value_results(engine, sess)
        install_clean_engine_baseline(sess, engine)
        save_sessions_to_disk()

        results_dict = {
            line_type_key: [row.to_dict() for row in rows]
            for line_type_key, rows in engine.results.items()
        }
        value_results_dict = {
            line_type_key: [row.to_dict() for row in rows]
            for line_type_key, rows in engine.value_results.items()
        }
        consolidation = [
            row.to_dict()
            for row in engine.value_results.get(LineType.CONSOLIDATION.value, [])
        ]

        resp = {
            'success': True,
            'results': results_dict,
            'value_results': value_results_dict,
            'consolidation': consolidation,
        }
        restored_vp = global_config.get('valuation_params')
        if restored_vp:
            resp['restored_valuation_params'] = restored_vp
        return jsonify(resp)

    return bp
