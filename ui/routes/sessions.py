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
    start_session_warmup: Callable[[str], bool] | None = None,
    wait_for_session_warmup: Callable[[str, float], bool] | None = None,
) -> Blueprint:
    bp = Blueprint('sessions', __name__)

    def _session_is_calculated(sess: dict) -> bool:
        return sess.get('engine') is not None or sess.get('parameters') is not None

    def _session_restore_status(sess: dict) -> str:
        if sess.get('engine') is not None:
            return 'ready'
        return sess.get('restore_status') or ('cold' if sess.get('parameters') is not None else 'pending')

    def _session_meta_payload(sid: str, sess: dict, active_session_id: str | None = None) -> dict:
        meta = sess.get('metadata', {})
        site = meta.get('site', 'Unknown')
        planning_month = str(meta.get('planning_month', '')) or 'Unknown'
        return {
            'id': sid,
            'filename': sess.get('filename', ''),
            'custom_name': sess.get('custom_name'),
            'site': site,
            'planning_month': planning_month,
            'uploaded_at': sess.get('uploaded_at', ''),
            'calculated': _session_is_calculated(sess),
            'is_snapshot': sess.get('is_snapshot', False),
            'active': sid == active_session_id,
            'metadata': meta,
            'restore_status': _session_restore_status(sess),
            'restore_error': sess.get('restore_error'),
        }

    def _switch_payload(sid: str, sess: dict) -> dict:
        return {
            'success': True,
            'active_session_id': sid,
            'filename': sess.get('filename', ''),
            'custom_name': sess.get('custom_name'),
            'metadata': sess.get('metadata', {}),
            'calculated': sess.get('engine') is not None,
            'restore_status': _session_restore_status(sess),
            'restore_error': sess.get('restore_error'),
            'pending_edits': sess.get('pending_edits', {}),
            'value_aux_overrides': sess.get('value_aux_overrides', {}),
            'machine_overrides': sess.get('machine_overrides', {}),
            'parameters': sess.get('parameters', {}),
            'valuation_params': global_config.get('valuation_params', {}),
            'purchased_and_produced': global_config.get('purchased_and_produced', ''),
        }

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
        new_sess = {
            'id': new_id,
            'file_path': sess.get('file_path', ''),
            'extract_files': sess.get('extract_files'),
            'filename': sess.get('filename', ''),
            'custom_name': name,
            'is_snapshot': True,
            'engine': None,
            'value_results': copy.deepcopy(sess.get('value_results', {})),
            'metadata': copy.deepcopy(sess.get('metadata', {})),
            'uploaded_at': datetime.now().isoformat(),
            'parameters': copy.deepcopy(sess.get('parameters')),
            'pending_edits': copy.deepcopy(sess.get('pending_edits', {})),
            'value_aux_overrides': copy.deepcopy(sess.get('value_aux_overrides', {})),
            'valuation_params': copy.deepcopy(
                sess.get('valuation_params')
                or (sess.get('reset_baseline') or {}).get('valuation_params')
            ),
            'machine_overrides': (
                machine_overrides_from_engine(sess, sess.get('engine'))
                if sess.get('engine') is not None
                else copy.deepcopy(sess.get('machine_overrides', {}))
            ),
            'reset_baseline': copy.deepcopy(sess.get('reset_baseline')),
            'undo_stack': [],
            'redo_stack': [],
            'restore_status': 'cold',
            'restore_error': None,
        }
        sessions[new_id] = new_sess
        if start_session_warmup is not None and new_sess.get('parameters') is not None:
            new_sess['restore_status'] = 'warming'
            try:
                if not start_session_warmup(new_id):
                    new_sess['restore_status'] = 'cold'
            except Exception as exc:
                new_sess['restore_status'] = 'failed'
                new_sess['restore_error'] = str(exc)
        save_sessions_to_disk()

        session_payload = _session_meta_payload(new_id, new_sess)
        session_payload['custom_name'] = name
        return jsonify({
            'success': True,
            'session': session_payload,
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
            grouped[key].append(_session_meta_payload(sid, sess, active_session_id))
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
                'calculated': _session_is_calculated(sess),
                'restore_status': _session_restore_status(sess),
                'restore_error': sess.get('restore_error'),
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
        if sess.get('engine') is None and _session_restore_status(sess) == 'warming':
            if wait_for_session_warmup is not None:
                wait_for_session_warmup(sid, 0.1)
            if sess.get('engine') is None:
                set_active_session_id(sid)
                return jsonify(_switch_payload(sid, sess))
        if sess.get('engine') is None and sess.get('parameters') is not None:
            try:
                sess['restore_status'] = 'warming'
                sess['restore_error'] = None
                params = sess['parameters']
                with contextlib.redirect_stdout(io.StringIO()):
                    engine = build_clean_engine_for_session(sess, params)
                    install_clean_engine_baseline(sess, engine, clear_machine_overrides=False)
                    with app_context():
                        replay_pending_edits(sess, engine)
                sess['engine'] = engine
                sess['restore_status'] = 'ready'
            except Exception as exc:
                sess['restore_status'] = 'failed'
                sess['restore_error'] = str(exc)
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

        return jsonify(_switch_payload(sid, sess))

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
