"""Purchased-and-produced material split routes."""

from typing import Callable

from flask import Blueprint, jsonify, request

from modules.models import LineType


def create_pap_blueprint(
    get_active: Callable[[], tuple],
    global_config: dict,
    format_purchased_and_produced: Callable[[dict], str],
    ensure_reset_baseline: Callable[[dict, object], None],
    recalc_pap_material: Callable[[object, str], None],
    finish_pap_recalc: Callable[[object], None],
    save_global_config: Callable[[], None],
    moq_warnings_payload: Callable[[object], dict],
) -> Blueprint:
    bp = Blueprint('pap', __name__)

    def _pap_response(current_engine):
        results_dict = {
            line_type: [row.to_dict() for row in rows]
            for line_type, rows in current_engine.results.items()
        }
        value_results_dict = {
            line_type: [row.to_dict() for row in rows]
            for line_type, rows in current_engine.value_results.items()
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
            **moq_warnings_payload(current_engine),
        })

    @bp.route('/api/pap', methods=['GET'])
    def get_pap():
        _, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400
        return jsonify({'pap': dict(current_engine.data.purchased_and_produced)})

    @bp.route('/api/pap', methods=['POST'])
    def set_pap():
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400
        req = request.get_json() or {}
        mat = req.get('material_number', '').strip()
        fraction = req.get('fraction')
        if not mat:
            return jsonify({'error': 'material_number is required'}), 400
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            return jsonify({'error': 'fraction must be a number'}), 400

        ensure_reset_baseline(sess, current_engine)
        current_engine.data.purchased_and_produced[mat] = fraction
        global_config['purchased_and_produced'] = format_purchased_and_produced(
            current_engine.data.purchased_and_produced
        )
        recalc_pap_material(current_engine, mat)
        finish_pap_recalc(current_engine)
        save_global_config()
        return _pap_response(current_engine)

    @bp.route('/api/pap/<material_number>', methods=['DELETE'])
    def delete_pap(material_number):
        sess, current_engine = get_active()
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        ensure_reset_baseline(sess, current_engine)
        current_engine.data.purchased_and_produced.pop(material_number, None)
        global_config['purchased_and_produced'] = format_purchased_and_produced(
            current_engine.data.purchased_and_produced
        )
        recalc_pap_material(current_engine, material_number)
        finish_pap_recalc(current_engine)
        save_global_config()
        return _pap_response(current_engine)

    return bp
