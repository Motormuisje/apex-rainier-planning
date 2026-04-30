"""Edit metadata and pending-edit persistence routes."""

from flask import Blueprint, jsonify, request

from ui.pending_edits import canonical_pending_edit_key


def create_edit_state_blueprint(
    sessions: dict,
    editable_line_types: set,
    save_sessions_to_disk,
) -> Blueprint:
    bp = Blueprint('edit_state', __name__)

    @bp.route('/api/editable_line_types')
    def get_editable_line_types():
        return jsonify({'editable': sorted(editable_line_types)})

    @bp.route('/api/sessions/edits/persist', methods=['POST'])
    def persist_session_edit():
        """Save a single cell edit into the session's persistent pending_edits store."""
        req = request.get_json() or {}
        session_id = req.get('session_id', '')
        if not session_id or session_id not in sessions:
            return jsonify({'error': 'Session not found'}), 404
        key = canonical_pending_edit_key(req.get('key', ''))
        if not key:
            return jsonify({'error': 'Cell key required'}), 400
        original = float(req.get('original', 0))
        new_value = float(req.get('new_value', 0))
        pending = sessions[session_id].setdefault('pending_edits', {})
        if abs(new_value - original) < 0.0001:
            pending.pop(key, None)
        else:
            pending[key] = {'original': original, 'new_value': new_value}
        save_sessions_to_disk()
        return jsonify({'success': True})

    @bp.route('/api/sessions/edits/sync', methods=['POST'])
    def sync_session_edits():
        """Replace the pending_edits store for a session after undo/redo/reset/import."""
        req = request.get_json() or {}
        session_id = req.get('session_id', '')
        if not session_id or session_id not in sessions:
            return jsonify({'error': 'Session not found'}), 404
        edits = req.get('edits', {})
        if not isinstance(edits, dict):
            return jsonify({'error': 'edits must be an object'}), 400
        sessions[session_id]['pending_edits'] = {
            key: {'original': float(value.get('original', 0)), 'new_value': float(value.get('new_value', 0))}
            for key, value in edits.items()
            if isinstance(value, dict)
        }
        save_sessions_to_disk()
        return jsonify({'success': True})

    return bp
