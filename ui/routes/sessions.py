"""Planning session management routes."""

import contextlib
import copy
import io
import uuid
from datetime import datetime
from typing import Callable

from flask import Blueprint, jsonify, request


def create_sessions_blueprint(
    sessions: dict,
    get_active_session_id: Callable[[], str | None],
    set_active_session_id: Callable[[str | None], None],
    get_active: Callable[[], tuple],
    global_config: dict,
    machine_overrides_from_engine: Callable[[dict, object], dict],
    save_sessions_to_disk: Callable[[], None],
    sync_global_config_from_engine: Callable[[object], None],
    build_clean_engine_for_session: Callable[..., object],
    install_clean_engine_baseline: Callable[[dict, object], None],
    replay_pending_edits: Callable[[dict, object], None],
    snapshot_has_manual_edits: Callable[[dict], bool],
    engine_has_manual_edits: Callable[[object], bool],
    app_context: Callable[[], contextlib.AbstractContextManager],
) -> Blueprint:
    bp = Blueprint('sessions', __name__)

    @bp.route('/api/sessions/snapshot', methods=['POST'])
    def snapshot_session():
        """Duplicate the active session, including all edits, as a named instance."""
        req = request.get_json() or {}
        name = req.get('name', '').strip()
        if not name:
            return jsonify({'error': 'Name cannot be empty'}), 400

        sess, _ = get_active()
        if sess is None:
            return jsonify({'error': 'No active session'}), 400

        new_id = str(uuid.uuid4())
        engine_copy = None
        if sess.get('engine') is not None:
            try:
                engine_copy = copy.deepcopy(sess['engine'])
            except Exception as exc:
                return jsonify({'success': False, 'error': f'Could not copy session state: {exc}'}), 500
        new_sess = {
            'id': new_id,
            'file_path': sess.get('file_path', ''),
            'extract_files': sess.get('extract_files'),
            'filename': sess.get('filename', ''),
            'custom_name': name,
            'is_snapshot': True,
            'engine': engine_copy,
            'value_results': copy.deepcopy(sess.get('value_results', {})),
            'metadata': copy.deepcopy(sess.get('metadata', {})),
            'uploaded_at': datetime.now().isoformat(),
            'parameters': copy.deepcopy(sess.get('parameters')),
            'pending_edits': copy.deepcopy(sess.get('pending_edits', {})),
            'value_aux_overrides': copy.deepcopy(sess.get('value_aux_overrides', {})),
            'machine_overrides': (
                machine_overrides_from_engine(sess, sess.get('engine'))
                if sess.get('engine') is not None
                else copy.deepcopy(sess.get('machine_overrides', {}))
            ),
            'reset_baseline': copy.deepcopy(sess.get('reset_baseline')),
            'undo_stack': [],
            'redo_stack': [],
        }
        sessions[new_id] = new_sess
        save_sessions_to_disk()

        meta = new_sess.get('metadata', {})
        site = meta.get('site', 'Unknown')
        planning_month = str(meta.get('planning_month', '')) or 'Unknown'
        return jsonify({
            'success': True,
            'session': {
                'id': new_id,
                'filename': new_sess['filename'],
                'custom_name': name,
                'site': site,
                'planning_month': planning_month,
                'uploaded_at': new_sess['uploaded_at'],
                'calculated': engine_copy is not None,
                'is_snapshot': True,
                'active': False,
                'metadata': meta,
            }
        })

    @bp.route('/api/sessions')
    def list_sessions():
        """Return all sessions grouped by year/month/site."""
        active_session_id = get_active_session_id()
        grouped: dict = {}
        for sid, sess in sessions.items():
            meta = sess.get('metadata', {})
            site = meta.get('site', 'Unknown')
            planning_month = str(meta.get('planning_month', '')) or 'Unknown'
            year = planning_month[:4] if len(planning_month) >= 4 else 'Unknown'
            month = planning_month[5:7] if len(planning_month) >= 7 else 'Unknown'
            key = f'{year}/{month}/{site}'
            grouped.setdefault(key, [])
            grouped[key].append({
                'id': sid,
                'filename': sess.get('filename', ''),
                'custom_name': sess.get('custom_name'),
                'site': site,
                'planning_month': planning_month,
                'uploaded_at': sess.get('uploaded_at', ''),
                'calculated': sess.get('engine') is not None,
                'is_snapshot': sess.get('is_snapshot', False),
                'active': sid == active_session_id,
                'metadata': meta,
            })
        return jsonify({'active_session_id': active_session_id, 'groups': grouped})

    @bp.route('/api/sessions/rename', methods=['POST'])
    def rename_session():
        data = request.get_json() or {}
        session_id = data.get('session_id', '')
        new_name = data.get('name', '').strip()
        if not session_id or session_id not in sessions:
            return jsonify({'error': 'Session not found'}), 404
        if not new_name:
            return jsonify({'error': 'Name cannot be empty'}), 400
        sessions[session_id]['custom_name'] = new_name
        save_sessions_to_disk()
        sess = sessions[session_id]
        return jsonify({
            'success': True,
            'session_id': session_id,
            'custom_name': new_name,
            'session': {
                'id': sess.get('id', session_id),
                'filename': sess.get('filename', ''),
                'custom_name': sess.get('custom_name'),
                'metadata': sess.get('metadata', {}),
                'uploaded_at': sess.get('uploaded_at', ''),
                'planning_month': (sess.get('metadata') or {}).get('planning_month', ''),
                'calculated': sess.get('engine') is not None,
            }
        })

    @bp.route('/api/sessions/switch', methods=['POST'])
    def switch_session():
        """Set a different session as active. Returns session metadata."""
        req = request.get_json() or {}
        sid = req.get('session_id')
        if not sid or sid not in sessions:
            return jsonify({'error': 'Session not found'}), 404
        sess = sessions[sid]

        if sess.get('engine') is not None:
            sync_global_config_from_engine(sess.get('engine'))
        if sess.get('engine') is None and sess.get('parameters') is not None:
            try:
                params = sess['parameters']
                with contextlib.redirect_stdout(io.StringIO()):
                    engine = build_clean_engine_for_session(sess, params)
                    install_clean_engine_baseline(sess, engine, clear_machine_overrides=False)
                    with app_context():
                        replay_pending_edits(sess, engine)
                sess['engine'] = engine
            except Exception as exc:
                return jsonify({'error': f'Could not restore calculations for this session: {exc}'}), 500
        if sess.get('engine') is not None and (
            sess.get('reset_baseline') is None or snapshot_has_manual_edits(sess.get('reset_baseline'))
        ):
            try:
                clean_engine = build_clean_engine_for_session(sess)
                if clean_engine is not None:
                    install_clean_engine_baseline(sess, clean_engine, clear_machine_overrides=False)
                elif not engine_has_manual_edits(sess['engine']):
                    install_clean_engine_baseline(sess, sess['engine'], clear_machine_overrides=False)
            except Exception:
                pass

        set_active_session_id(sid)
        sync_global_config_from_engine(sess.get('engine'))

        return jsonify({
            'success': True,
            'active_session_id': sid,
            'filename': sess.get('filename', ''),
            'custom_name': sess.get('custom_name'),
            'metadata': sess.get('metadata', {}),
            'calculated': sess.get('engine') is not None,
            'pending_edits': sess.get('pending_edits', {}),
            'value_aux_overrides': sess.get('value_aux_overrides', {}),
            'machine_overrides': sess.get('machine_overrides', {}),
            'parameters': sess.get('parameters', {}),
            'valuation_params': global_config.get('valuation_params', {}),
            'purchased_and_produced': global_config.get('purchased_and_produced', ''),
        })

    @bp.route('/api/sessions/<session_id>', methods=['DELETE'])
    def delete_session(session_id):
        """Remove a session. Activates the next available session if deleted was active."""
        if session_id not in sessions:
            return jsonify({'error': 'Session not found'}), 404
        del sessions[session_id]
        if get_active_session_id() == session_id:
            set_active_session_id(next(iter(sessions), None))
        save_sessions_to_disk()
        return jsonify({'success': True, 'active_session_id': get_active_session_id()})

    return bp
