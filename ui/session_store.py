"""Persistence helpers for UI planning-session metadata."""

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Callable


def save_sessions_to_disk(
    sessions: dict,
    active_session_id: str | None,
    sessions_store: Path,
    machine_overrides_from_engine: Callable[[dict, object], dict],
) -> None:
    """Persist session metadata without engine objects."""
    serializable = {}
    for sid, sess in sessions.items():
        # Persist current valuation_params per-session so rebuilds after
        # restart use the correct per-session values, not the shared global config.
        engine = sess.get('engine')
        vp_obj = getattr(getattr(engine, 'data', None), 'valuation_params', None)
        if vp_obj is not None:
            sess_vp = {
                '1': vp_obj.direct_fte_cost_per_month,
                '2': vp_obj.indirect_fte_cost_per_month,
                '3': vp_obj.overhead_cost_per_month,
                '4': vp_obj.sga_cost_per_month,
                '5': vp_obj.depreciation_per_year,
                '6': vp_obj.net_book_value,
                '7': vp_obj.days_sales_outstanding,
                '8': vp_obj.days_payable_outstanding,
            }
        else:
            sess_vp = (sess.get('reset_baseline') or {}).get('valuation_params') or sess.get('valuation_params')
        serializable[sid] = {
            'id': sess.get('id', sid),
            'file_path': sess.get('file_path', ''),
            'extract_files': sess.get('extract_files'),
            'filename': sess.get('filename', ''),
            'custom_name': sess.get('custom_name'),
            'is_snapshot': sess.get('is_snapshot', False),
            'metadata': sess.get('metadata', {}),
            'uploaded_at': sess.get('uploaded_at', ''),
            'parameters': sess.get('parameters'),
            'pending_edits': sess.get('pending_edits', {}),
            'value_aux_overrides': sess.get('value_aux_overrides', {}),
            'machine_overrides': (
                machine_overrides_from_engine(sess, engine)
                if engine is not None
                else sess.get('machine_overrides', {})
            ),
            'valuation_params': sess_vp,
        }
    store = {
        'active_session_id': active_session_id,
        'sessions': serializable,
    }
    tmp_path = sessions_store.with_name(f'{sessions_store.name}.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(store, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, sessions_store)


def load_sessions_from_disk(sessions_store: Path) -> tuple[dict, str | None]:
    """Restore session metadata from sessions_store.json on startup."""
    loaded_sessions = {}
    if not sessions_store.exists():
        return loaded_sessions, None

    try:
        with open(sessions_store, 'r', encoding='utf-8') as f:
            store = json.load(f)
        for sid, data in store.get('sessions', {}).items():
            loaded_sessions[sid] = {
                'id': data.get('id', sid),
                'file_path': data.get('file_path', ''),
                'extract_files': data.get('extract_files'),
                'filename': data.get('filename', ''),
                'custom_name': data.get('custom_name'),
                'is_snapshot': data.get('is_snapshot', False),
                'engine': None,
                'value_results': {},
                'metadata': data.get('metadata', {}),
                'uploaded_at': data.get('uploaded_at', ''),
                'parameters': data.get('parameters'),
                'pending_edits': data.get('pending_edits', {}),
                'value_aux_overrides': data.get('value_aux_overrides', {}),
                'machine_overrides': data.get('machine_overrides', {}),
                'valuation_params': data.get('valuation_params'),
                'undo_stack': [],
                'redo_stack': [],
            }
        saved_active = store.get('active_session_id')
        if saved_active and saved_active in loaded_sessions:
            active_session_id = saved_active
        elif loaded_sessions:
            active_session_id = next(iter(loaded_sessions))
        else:
            active_session_id = None
        return loaded_sessions, active_session_id
    except Exception as exc:
        print(f'[sessions] load error: {exc}')
        try:
            corrupt_path = sessions_store.with_name(
                f'{sessions_store.name}.corrupt-{datetime.now().strftime("%Y%m%d%H%M%S")}'
            )
            sessions_store.replace(corrupt_path)
            print(f'[sessions] corrupt store moved to: {corrupt_path}')
        except Exception as move_exc:
            print(f'[sessions] corrupt store could not be moved: {move_exc}')
        return {}, None
