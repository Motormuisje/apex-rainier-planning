"""S&OP Planning Engine - Flask Web UI"""

from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from pathlib import Path
from datetime import datetime
import sys
import io
import json
import copy
import os
import contextlib

from ui.parsers import (
    format_purchased_and_produced as _format_purchased_and_produced,
    parse_purchased_and_produced as _parse_purchased_and_produced,
    valuation_params_from_config as _valuation_params_from_config,
)
from ui.paths import default_app_data_root, default_folders, resource_root
from ui.serializers import (
    json_safe as _json_safe,
    moq_warnings_payload as _moq_warnings_payload,
    planning_value_payload as _planning_value_payload,
    row_payload as _row_payload,
    value_results_payload as _value_results_payload,
)
from ui.config_store import (
    apply_folder_config,
    load_global_config,
    save_global_config,
)
from ui.errors import classify_upload_exception as _classify_upload_exception
from ui.replay import (
    get_value_aux_override_values,
    recalculate_value_results,
    replay_pending_edits,
)
from ui.routes.config import create_config_blueprint
from ui.routes.exports import create_exports_blueprint
from ui.routes.license import create_license_blueprint
from ui.routes.machines import create_machines_blueprint
from ui.routes.pap import create_pap_blueprint
from ui.routes.read import create_read_blueprint
from ui.routes.scenarios import create_scenarios_blueprint
from ui.routes.sessions import create_sessions_blueprint
from ui.engine_rebuild import (
    build_clean_engine_for_session,
    get_config_overrides,
    get_session_config_overrides,
    install_clean_engine_baseline,
)
from ui.session_store import (
    load_sessions_from_disk,
    save_sessions_to_disk,
)
from ui.state_snapshot import (
    apply_machine_overrides as _apply_machine_overrides,
    build_pending_edits_from_results_snapshot as _build_pending_edits_from_results_snapshot,
    engine_has_manual_edits as _engine_has_manual_edits,
    ensure_reset_baseline as _ensure_reset_baseline_impl,
    machine_overrides_from_engine as _machine_overrides_from_engine,
    planning_row_from_snapshot as _planning_row_from_snapshot,
    rebuild_volume_caches_from_results as _rebuild_volume_caches_from_results,
    restore_engine_state,
    row_key_from_obj as _row_key_from_obj,
    snapshot_engine_state,
    snapshot_has_manual_edits as _snapshot_has_manual_edits,
)

RESOURCE_ROOT = resource_root()
APP_DATA_ROOT = default_app_data_root()
APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)

# Default folder paths â€” overridden by _apply_folder_config() after config loads
APP_UPLOADS_DIR = APP_DATA_ROOT / 'uploads'
APP_EXPORTS_DIR = APP_DATA_ROOT / 'exports'
APP_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
APP_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

def _default_folders() -> dict:
    return default_folders(APP_DATA_ROOT)

sys.path.insert(0, str(RESOURCE_ROOT))

from modules.planning_engine import PlanningEngine
from modules.models import LineType
from modules.cycle_manager import CycleManager
from modules.license_manager import LicenseManager

_license = LicenseManager(APP_DATA_ROOT)

app = Flask(
    __name__,
    template_folder=str(RESOURCE_ROOT / 'ui' / 'templates'),
    static_folder=str(RESOURCE_ROOT / 'ui' / 'static'),
)
# Allow large Excel uploads (512 MB). Without this, Flask returns a 413 that
# often surfaces on the client as "TypeError: Failed to fetch".
app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024
app.register_blueprint(create_license_blueprint(_license))


@app.errorhandler(413)
def _too_large(e):
    return jsonify({
        'error': 'Bestand is te groot voor upload (limiet 512 MB). Comprimeer of splits het bestand.',
        'error_kind': 'too_large',
    }), 413

import uuid as _uuid

sessions: dict = {}           # session_id -> session dict
active_session_id: str = None  # currently selected session
scenarios: dict = {}          # scenario_id -> scenario snapshot

# Shared CycleManager â€” stores previous-cycle snapshots in the writable app-data exports folder
_CYCLE_STORAGE_DIR = APP_EXPORTS_DIR
_cycle_manager = CycleManager(str(_CYCLE_STORAGE_DIR))

# Line types that users are permitted to edit directly.
# Computed lines (03, 04, 07-12) are intentionally excluded.
EDITABLE_LINE_TYPES = {
    '01. Demand forecast',
    '05. Minimum target stock',
    '06. Production plan',
    '06. Purchase receipt',
}

# Value-planning rows whose Aux Column acts as the editable financial factor.
VALUE_AUX_EDITABLE_LINE_TYPES = {
    '01. Demand forecast',
    '03. Total demand',
    '04. Inventory',
    '06. Purchase receipt',
    '07. Capacity utilization',
    '12. FTE requirements',
}

SESSIONS_STORE = APP_DATA_ROOT / 'sessions_store.json'
GLOBAL_CONFIG_FILE = APP_DATA_ROOT / 'global_config.json'

_global_config: dict = {}
_VERBOSE_STARTUP = os.getenv('SOP_VERBOSE_STARTUP', '').strip().lower() in ('1', 'true', 'yes', 'on')
_DISABLE_AUTORUN = os.getenv('SOP_DISABLE_AUTORUN', '').strip().lower() in ('1', 'true', 'yes', 'on')


def _snapshot_engine_state(engine) -> dict:
    return snapshot_engine_state(engine, SHIFT_HOURS_LOOKUP_FALLBACK)


def _sync_global_config_from_engine(engine) -> None:
    """Pull the active session's engine state back into _global_config so all
    subsequent reads/writes use values that belong to the active session."""
    global _global_config
    if engine is None or getattr(engine, 'data', None) is None:
        return
    vp = getattr(engine.data, 'valuation_params', None)
    if vp is not None:
        _global_config['valuation_params'] = {
            '1': vp.direct_fte_cost_per_month,
            '2': vp.indirect_fte_cost_per_month,
            '3': vp.overhead_cost_per_month,
            '4': vp.sga_cost_per_month,
            '5': vp.depreciation_per_year,
            '6': vp.net_book_value,
            '7': vp.days_sales_outstanding,
            '8': vp.days_payable_outstanding,
        }
    pap = getattr(engine.data, 'purchased_and_produced', None)
    if pap is not None:
        _global_config['purchased_and_produced'] = _format_purchased_and_produced(pap)


def _restore_engine_state(engine, snapshot: dict) -> None:
    restore_engine_state(engine, snapshot, _global_config)


def _ensure_reset_baseline(sess, engine) -> None:
    _ensure_reset_baseline_impl(sess, engine, SHIFT_HOURS_LOOKUP_FALLBACK)


def _get_session_config_overrides(sess=None) -> dict:
    return get_session_config_overrides(sess, _global_config)


def _build_clean_engine_for_session(sess, params=None):
    return build_clean_engine_for_session(sess, _global_config, params)


def _install_clean_engine_baseline(sess, engine, clear_machine_overrides: bool = True) -> None:
    install_clean_engine_baseline(
        sess,
        engine,
        _snapshot_engine_state,
        clear_machine_overrides=clear_machine_overrides,
    )


def _load_global_config():
    global _global_config
    _global_config = load_global_config(GLOBAL_CONFIG_FILE)


def _save_global_config():
    save_global_config(GLOBAL_CONFIG_FILE, _global_config)


def _get_config_overrides() -> dict:
    return get_config_overrides(_global_config)


def _save_sessions_to_disk():
    try:
        save_sessions_to_disk(
            sessions,
            active_session_id,
            SESSIONS_STORE,
            _machine_overrides_from_engine,
        )
    except Exception as exc:
        print(f'[sessions] save error: {exc}')


def _load_sessions_from_disk():
    global sessions, active_session_id
    sessions, active_session_id = load_sessions_from_disk(SESSIONS_STORE)


def _apply_folder_config():
    """Apply folder paths from _global_config, update globals and CycleManager."""
    global APP_UPLOADS_DIR, APP_EXPORTS_DIR, SESSIONS_STORE, _cycle_manager
    APP_UPLOADS_DIR, APP_EXPORTS_DIR, SESSIONS_STORE = apply_folder_config(
        _global_config,
        _default_folders(),
    )
    _cycle_manager = CycleManager(str(APP_EXPORTS_DIR))


def _apply_folder_paths(uploads_dir: Path, exports_dir: Path, sessions_dir: Path) -> None:
    global APP_UPLOADS_DIR, APP_EXPORTS_DIR, SESSIONS_STORE, _cycle_manager
    APP_UPLOADS_DIR = uploads_dir
    APP_EXPORTS_DIR = exports_dir
    SESSIONS_STORE = sessions_dir / 'sessions_store.json'
    _cycle_manager = CycleManager(str(APP_EXPORTS_DIR))


_load_global_config()
_apply_folder_config()
_load_sessions_from_disk()
app.register_blueprint(create_config_blueprint(
    _default_folders,
    _global_config,
    _save_global_config,
    _apply_folder_paths,
    lambda: APP_UPLOADS_DIR,
    lambda: _get_active(),
    _parse_purchased_and_produced,
    _valuation_params_from_config,
    _ensure_reset_baseline,
    lambda engine, material_number: _recalc_pap_material(engine, material_number),
    lambda engine: _finish_pap_recalc(engine),
    lambda engine, sess=None: _recalculate_value_results(engine, sess),
    _build_clean_engine_for_session,
    _install_clean_engine_baseline,
    lambda sess, engine: _replay_pending_edits(sess, engine),
    _moq_warnings_payload,
    _value_results_payload,
))
app.register_blueprint(create_read_blueprint(
    lambda: _get_active(),
    _row_payload,
    _moq_warnings_payload,
))
app.register_blueprint(create_machines_blueprint(
    lambda: _get_active(),
    _machine_overrides_from_engine,
    lambda machine, data: SHIFT_HOURS_LOOKUP_FALLBACK(machine, data),
    _ensure_reset_baseline,
    lambda engine, sess: _recalculate_capacity_and_values(engine, sess),
    _planning_value_payload,
    _save_sessions_to_disk,
))
app.register_blueprint(create_pap_blueprint(
    lambda: _get_active(),
    _global_config,
    _format_purchased_and_produced,
    _ensure_reset_baseline,
    lambda engine, material_number: _recalc_pap_material(engine, material_number),
    lambda engine: _finish_pap_recalc(engine),
    _save_global_config,
    _moq_warnings_payload,
))
app.register_blueprint(create_sessions_blueprint(
    sessions,
    lambda: active_session_id,
    lambda session_id: _set_active_session_id(session_id),
    lambda: _get_active(),
    _global_config,
    _machine_overrides_from_engine,
    _save_sessions_to_disk,
    _sync_global_config_from_engine,
    _build_clean_engine_for_session,
    _install_clean_engine_baseline,
    lambda sess, engine: _replay_pending_edits(sess, engine),
    _snapshot_has_manual_edits,
    _engine_has_manual_edits,
    lambda: app.app_context(),
))
app.register_blueprint(create_scenarios_blueprint(
    scenarios,
    sessions,
    lambda: active_session_id,
    lambda: _get_active(),
    _global_config,
    lambda: APP_EXPORTS_DIR,
    _build_pending_edits_from_results_snapshot,
    _planning_row_from_snapshot,
    _rebuild_volume_caches_from_results,
    _valuation_params_from_config,
    _parse_purchased_and_produced,
    _format_purchased_and_produced,
    _row_key_from_obj,
))
app.register_blueprint(create_exports_blueprint(
    lambda: _get_active(),
    lambda: APP_EXPORTS_DIR,
    lambda: _cycle_manager,
    lambda path, engine: _apply_edit_highlights(path, engine),
))


def _replay_pending_edits(sess, engine):
    replay_pending_edits(
        sess,
        engine,
        _apply_volume_change,
        _apply_machine_overrides,
        _recalculate_capacity_and_values,
    )


def _get_value_aux_override_values(sess) -> dict:
    return get_value_aux_override_values(sess)


def _recalculate_value_results(engine, sess=None):
    recalculate_value_results(engine, sess)


def _autorun_sessions():
    """Background thread: re-execute the planning engine for every session that
    was previously run (identified by a non-None 'parameters' field).

    Runs silently and does not block server startup.
    """
    import threading

    def _worker():
        candidates = [
            (sid, sess) for sid, sess in sessions.items()
            if sess.get('parameters') is not None and (
                sess.get('extract_files') or Path(sess.get('file_path', '')).exists()
            )
        ]
        if not candidates:
            return
        for sid, sess in candidates:
            label = sess.get('custom_name') or sess.get('filename', sid)
            try:
                params = sess['parameters']
                planning_month  = params.get('planning_month')
                months_actuals  = int(params.get('months_actuals', 0) or 0)
                months_forecast = int(params.get('months_forecast', 12) or 12)
                engine = PlanningEngine(
                    sess['file_path'],
                    planning_month=planning_month,
                    months_actuals=months_actuals,
                    months_forecast=months_forecast,
                    extract_files=sess.get('extract_files'),
                    config_overrides=_get_session_config_overrides(sess),
                )
                if _VERBOSE_STARTUP:
                    engine.run()
                    _install_clean_engine_baseline(sess, engine, clear_machine_overrides=False)
                    with app.app_context():
                        _replay_pending_edits(sess, engine)
                else:
                    with contextlib.redirect_stdout(io.StringIO()):
                        engine.run()
                        _install_clean_engine_baseline(sess, engine, clear_machine_overrides=False)
                        with app.app_context():
                            _replay_pending_edits(sess, engine)
                sess['engine'] = engine
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error(f'autorun FAIL "{label}": {exc}')

    t = threading.Thread(target=_worker, name='autorun-sessions', daemon=True)
    t.start()


if not _DISABLE_AUTORUN:
    _autorun_sessions()


def _get_active():
    """Return (session_dict, engine) for the active session, or (None, None)."""
    sess = sessions.get(active_session_id)
    if not sess:
        return None, None
    return sess, sess.get('engine')


def _set_active_session_id(session_id):
    global active_session_id
    active_session_id = session_id


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    global sessions, active_session_id

    upload_dir = APP_UPLOADS_DIR
    upload_dir.mkdir(exist_ok=True)
    requested_name = (request.form.get('custom_name') or '').strip() or None
    requested_planning_month = (request.form.get('planning_month') or '').strip()
    try:
        requested_actuals = int(request.form.get('months_actuals')) if request.form.get('months_actuals') not in (None, '') else None
    except (TypeError, ValueError):
        requested_actuals = None
    try:
        requested_forecast = int(request.form.get('months_forecast')) if request.form.get('months_forecast') not in (None, '') else None
    except (TypeError, ValueError):
        requested_forecast = None

    # Detect multi-file mode vs single-file mode
    extract_keys = ['bom_file', 'routing_file', 'stock_file', 'forecast_file']
    is_multi = all(k in request.files for k in extract_keys)

    if is_multi:
        # --- Multi-file upload mode ---
        # Use master file from global config; optional per-request base_file overrides it
        if 'base_file' in request.files and request.files['base_file'].filename != '':
            base_f = request.files['base_file']
            base_file_path = upload_dir / base_f.filename
            try:
                base_f.save(str(base_file_path))
            except Exception as e:
                return jsonify(_classify_upload_exception(e, 'opslaan base-file')), 400
        elif _global_config.get('master_file') and Path(_global_config['master_file']).exists():
            base_file_path = Path(_global_config['master_file'])
        else:
            return jsonify({'error': 'No master data file configured. Upload a base file in the Config tab first.'}), 400

        # Keywords that must appear in the filename for each file type (case-insensitive)
        filename_keywords = {
            'bom_file':      ['bom'],
            'routing_file':  ['routing'],
            'stock_file':    ['stock'],
            'forecast_file': ['forecast'],
        }
        field_labels = {
            'bom_file': 'BOM', 'routing_file': 'Routing',
            'stock_file': 'Stock', 'forecast_file': 'Forecast',
        }

        saved_paths = {}
        key_map = {'bom_file': 'bom', 'routing_file': 'routing',
                    'stock_file': 'stock', 'forecast_file': 'forecast'}
        for form_key, dict_key in key_map.items():
            f = request.files[form_key]
            if f.filename == '':
                return jsonify({'error': f'No file selected for {form_key}'}), 400
            fname_lower = f.filename.lower()
            required_kws = filename_keywords[form_key]
            if not any(kw in fname_lower for kw in required_kws):
                label = field_labels[form_key]
                return jsonify({
                    'error': (
                        f'Wrong file for {label} field: "{f.filename}" does not look like a {label} file. '
                        f'Expected the filename to contain: {", ".join(required_kws)}.'
                    )
                }), 400
            fp = upload_dir / f.filename
            try:
                f.save(str(fp))
            except Exception as e:
                return jsonify(_classify_upload_exception(e, f'opslaan {form_key}')), 400
            saved_paths[dict_key] = str(fp)

        try:
            import io as _io
            _devnull = _io.StringIO()
            sys.stdout, _saved = _devnull, sys.stdout
            try:
                from modules.data_loader import DataLoader
                loader = DataLoader(
                    excel_file=str(base_file_path),
                    extract_files=saved_paths,
                    config_overrides=_get_config_overrides(),
                )
                loader.load_all()
            finally:
                sys.stdout = _saved
        except Exception as e:
            import traceback
            print(f'[upload-multi] load error: {traceback.format_exc()}')
            return jsonify(_classify_upload_exception(e, 'inlezen extract-bestanden')), 400

        try:
            # Validate required data after successful load
            missing = []
            if not getattr(loader, 'materials', None):
                missing.append('materials')
            if not getattr(loader, 'bom', None):
                missing.append('bom')
            if not getattr(loader, 'routing', None):
                missing.append('routing')
            if not getattr(loader, 'machines', None):
                missing.append('machines')
            if not getattr(loader, 'forecasts', None):
                missing.append('forecasts')
            if not getattr(loader, 'periods', None):
                missing.append('periods')
            if getattr(loader, 'config', None) is None:
                missing.append('config')
            if missing:
                return jsonify({
                    'error': 'The uploaded file is missing required data. Please check that all expected sheets and columns are present.',
                    'missing': missing
                }), 400

            site = getattr(loader.config, 'site', '') or ''
            _idate = getattr(loader.config, 'initial_date', None)
            planning_month = requested_planning_month or (_idate.strftime('%Y-%m') if _idate else '')
            months_actuals = requested_actuals if requested_actuals is not None else getattr(loader, 'forecast_actuals_months', 12)
            months_forecast = requested_forecast if requested_forecast is not None else getattr(loader.config, 'forecast_months', 12)

            bom_filename = request.files['bom_file'].filename
            session_id = str(_uuid.uuid4())
            sessions[session_id] = {
                'id': session_id,
                'file_path': str(base_file_path),
                'extract_files': saved_paths,
                'filename': bom_filename,
                'custom_name': requested_name,
                'engine': None,
                'value_results': {},
                'metadata': {
                    'materials': len(loader.materials),
                    'bom_items': len(loader.bom),
                    'machines': len(loader.machines),
                    'periods': len(loader.periods),
                    'site': site,
                    'planning_month': planning_month,
                },
                'uploaded_at': datetime.now().isoformat(),
            }
            sessions[session_id]['undo_stack'] = []
            sessions[session_id]['redo_stack'] = []
            sessions[session_id]['pending_edits'] = {}
            sessions[session_id]['value_aux_overrides'] = {}
            sessions[session_id]['machine_overrides'] = {}
            active_session_id = session_id
            _save_sessions_to_disk()

            return jsonify({
                'success': True,
                'session_id': session_id,
                'filename': bom_filename,
                'custom_name': requested_name,
                'planning_month': planning_month,
                'months_actuals': months_actuals,
                'months_forecast': months_forecast,
                'summary': {
                    'materials': len(loader.materials),
                    'bom_items': len(loader.bom),
                    'machines': len(loader.machines),
                    'periods': len(loader.periods),
                }
            })
        except Exception as e:
            import traceback
            print(f'[upload-multi] session error: {traceback.format_exc()}')
            return jsonify(_classify_upload_exception(e, 'sessie aanmaken (multi)')), 400

    # --- Single-file upload mode (existing behavior) ---
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    file_path = upload_dir / file.filename
    try:
        file.save(str(file_path))
    except Exception as e:
        return jsonify(_classify_upload_exception(e, 'opslaan upload')), 400

    try:
        _devnull = io.StringIO()
        sys.stdout, _saved = _devnull, sys.stdout
        try:
            from modules.data_loader import DataLoader
            loader = DataLoader(str(file_path))
            loader.load_all()
        finally:
            sys.stdout = _saved
    except Exception as e:
        import traceback
        print(f'[upload-single] load error: {traceback.format_exc()}')
        return jsonify(_classify_upload_exception(e, 'inlezen Excel')), 400

    try:
        # Validate required data after successful load
        missing = []
        if not getattr(loader, 'materials', None):
            missing.append('materials')
        if not getattr(loader, 'bom', None):
            missing.append('bom')
        if not getattr(loader, 'routing', None):
            missing.append('routing')
        if not getattr(loader, 'machines', None):
            missing.append('machines')
        if not getattr(loader, 'forecasts', None):
            missing.append('forecasts')
        if not getattr(loader, 'periods', None):
            missing.append('periods')
        if getattr(loader, 'config', None) is None:
            missing.append('config')
        if missing:
            return jsonify({
                'error': 'The uploaded file is missing required data. Please check that all expected sheets and columns are present.',
                'missing': missing
            }), 400

        site = getattr(loader.config, 'site', '') or ''
        _idate = getattr(loader.config, 'initial_date', None)
        planning_month = requested_planning_month or (_idate.strftime('%Y-%m') if _idate else '')
        months_actuals = requested_actuals if requested_actuals is not None else getattr(loader, 'forecast_actuals_months', 12)
        months_forecast = requested_forecast if requested_forecast is not None else getattr(loader.config, 'forecast_months', 12)

        session_id = str(_uuid.uuid4())
        sessions[session_id] = {
            'id': session_id,
            'file_path': str(file_path),
            'filename': file.filename,
            'custom_name': requested_name,
            'engine': None,
            'value_results': {},
            'metadata': {
                'materials': len(loader.materials),
                'bom_items': len(loader.bom),
                'machines': len(loader.machines),
                'periods': len(loader.periods),
                'site': site,
                'planning_month': planning_month,
            },
            'uploaded_at': datetime.now().isoformat(),
        }
        sessions[session_id]['undo_stack'] = []
        sessions[session_id]['redo_stack'] = []
        sessions[session_id]['pending_edits'] = {}
        sessions[session_id]['value_aux_overrides'] = {}
        sessions[session_id]['machine_overrides'] = {}
        active_session_id = session_id
        _save_sessions_to_disk()

        return jsonify({
            'success': True,
            'session_id': session_id,
            'filename': file.filename,
            'custom_name': requested_name,
            'planning_month': planning_month,
            'months_actuals': months_actuals,
            'months_forecast': months_forecast,
            'summary': {
                'materials': len(loader.materials),
                'bom_items': len(loader.bom),
                'machines': len(loader.machines),
                'periods': len(loader.periods),
            }
        })
    except Exception as e:
        import traceback
        print(f'[upload-single] session error: {traceback.format_exc()}')
        return jsonify(_classify_upload_exception(e, 'sessie aanmaken')), 400


@app.route('/api/calculate', methods=['POST'])
def run_calculations():
    global sessions
    sess, _ = _get_active()
    if sess is None:
        return jsonify({'error': 'No file uploaded'}), 400

    _log_buf = io.StringIO()
    _old_stdout = sys.stdout
    sys.stdout = _log_buf

    try:
        # Get user input parameters from request (handle both JSON and form data)
        if request.is_json:
            req_data = request.get_json() or {}
        else:
            req_data = request.form.to_dict() or {}

        planning_month = req_data.get('planning_month', None)
        months_actuals = int(req_data.get('months_actuals', 0) or 0)
        months_forecast = int(req_data.get('months_forecast', 12) or 12)

        print("\nUser Input Parameters:")
        print(f"  Planning Month: {planning_month}")
        print(f"  Months of Actuals: {months_actuals}")
        print(f"  Months of Forecast: {months_forecast}")

        # --- MoM: preserve the previous results BEFORE running the new calculation ---
        # If the active session already has an engine, save its results as the
        # "previous cycle" snapshot so MoM compares new vs. old (not new vs. new).
        # If there is no previous snapshot at all (very first calculation ever),
        # bootstrap one after running so the user only needs one calculation to
        # enable MoM on the next run.
        _existing_engine = sess.get('engine')
        _bootstrap_snapshot = (_existing_engine is None and not _cycle_manager.has_previous_cycle())
        if _existing_engine is not None:
            try:
                _prev_pm = (sess.get('parameters') or {}).get('planning_month')
                _cycle_manager.save_current_as_previous(_existing_engine.to_dataframe(), planning_month=_prev_pm)
                print('[cycle_manager] pre-run: saved existing engine as previous cycle snapshot')
            except Exception as _cm_exc:
                import traceback
                print(f'[cycle_manager] pre-run snapshot ERROR (MoM will not work): {_cm_exc}\n{traceback.format_exc()}')

        engine = PlanningEngine(
            sess['file_path'],
            planning_month=planning_month,
            months_actuals=months_actuals,
            months_forecast=months_forecast,
            extract_files=sess.get('extract_files'),
            config_overrides=_get_config_overrides(),
        )
        engine.run()
        _install_clean_engine_baseline(sess, engine)
        with app.app_context():
            _replay_pending_edits(sess, engine)
        sess['engine'] = engine
        sess['parameters'] = {
            'planning_month':  planning_month,
            'months_actuals':  months_actuals,
            'months_forecast': months_forecast,
        }
        # Keep metadata in sync so the Files tab shows the calculated planning month
        if planning_month and sess.get('metadata') is not None:
            sess['metadata']['planning_month'] = planning_month

        # --- MoM bootstrap: on the very first calculation ever, seed the snapshot ---
        # (so MoM becomes available after the second calculation without needing
        # to calculate twice in the same app session)
        if _bootstrap_snapshot:
            try:
                _cycle_manager.save_current_as_previous(engine.to_dataframe(), planning_month=planning_month)
                print('[cycle_manager] bootstrap: saved first-ever snapshot')
            except Exception as _cm_exc:
                import traceback
                print(f'[cycle_manager] bootstrap snapshot ERROR (MoM will not work): {_cm_exc}\n{traceback.format_exc()}')

        _save_sessions_to_disk()

        sys.stdout = _old_stdout
        return jsonify({
            'success': True,
            'summary': engine.get_summary(),
            'log': _log_buf.getvalue(),
            'parameters': {
                'planning_month': planning_month,
                'months_actuals': months_actuals,
                'months_forecast': months_forecast
            }
        })
    except Exception as e:
        sys.stdout = _old_stdout
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500




def SHIFT_HOURS_LOOKUP_FALLBACK(machine, data):
    """Resolve shift hours for a machine, tolerating minor API drift."""
    if machine is None:
        return 520.0
    sho = getattr(machine, 'shift_hours_override', None)
    if sho is not None:
        return float(sho)
    from modules.models import SHIFT_HOURS, ShiftSystem
    try:
        key = machine.shift_system.value if hasattr(machine.shift_system, 'value') else machine.shift_system
        # shift_hours dict in data_loader uses human-readable keys ('3-shift system' etc.)
        if isinstance(key, str) and key in data.shift_hours:
            return data.shift_hours[key]
    except Exception:
        pass
    return SHIFT_HOURS.get(machine.shift_system, 520.0)



def _apply_edit_highlights(path: str, engine):
    """Open the exported workbook and apply edit highlights + summary sheet."""
    import openpyxl
    from openpyxl.styles import PatternFill, Font
    from openpyxl.comments import Comment

    # Collect all edit
    all_edits = []
    for _, rows in engine.results.items():
        for row in rows:
            if row.manual_edits:
                for period, edit_data in row.manual_edits.items():
                    original = edit_data.get('original', 0.0)
                    new_val = edit_data.get('new', 0.0)
                    delta_pct = round((new_val - original) / abs(original) * 100, 2) if original != 0 else 0.0
                    all_edits.append({
                        'line_type': row.line_type,
                        'material_number': row.material_number,
                        'material_name': row.material_name,
                        'period': period,
                        'original': original,
                        'new': new_val,
                        'delta_pct': delta_pct,
                    })

    if not all_edits:
        return

    wb = openpyxl.load_workbook(path)
    ws = wb['Planning sheet']

    # Build column lookups from header row
    header = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    period_col = {}
    mat_col_idx = None
    lt_col_idx = None
    for i, val in enumerate(header, start=1):
        if val is None:
            continue
        s = str(val)
        period_col[s] = i
        if s == 'Material number':
            mat_col_idx = i
        elif s == 'Line type':
            lt_col_idx = i

    # Build row lookup: (material_number, line_type) -> row_idx
    row_lookup = {}
    if mat_col_idx and lt_col_idx:
        for row_idx, row_data in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            mat_val = row_data[mat_col_idx - 1]
            lt_val = row_data[lt_col_idx - 1]
            if mat_val and lt_val:
                row_lookup[(str(mat_val), str(lt_val))] = row_idx

    # Fill styles
    yellow_fill = PatternFill(start_color='FFEB3B', end_color='FFEB3B', fill_type='solid')
    green_fill = PatternFill(start_color='C8E6C9', end_color='C8E6C9', fill_type='solid')
    red_fill = PatternFill(start_color='FFCDD2', end_color='FFCDD2', fill_type='solid')
    bold_font = Font(bold=True)

    for edit in all_edits:
        row_idx = row_lookup.get((edit['material_number'], edit['line_type']))
        col_idx = period_col.get(edit['period'])
        if row_idx is None or col_idx is None:
            continue
        cell = ws.cell(row=row_idx, column=col_idx)
        original = edit['original']
        new_val = edit['new']
        delta_pct = edit['delta_pct']
        if new_val > original:
            cell.fill = green_fill
            cell.font = bold_font
        elif new_val < original:
            cell.fill = red_fill
            cell.font = bold_font
        else:
            cell.fill = yellow_fill
        cell.comment = Comment(f"Original: {original}\nNew: {new_val}\nDelta: {delta_pct}%", 'SOP Engine')

    # Edits Summary sheet
    if 'Edits Summary' in wb.sheetnames:
        del wb['Edits Summary']
    ws_edits = wb.create_sheet('Edits Summary')
    ws_edits.append(['Line Type', 'Material Number', 'Material Name', 'Period',
                     'Original Value', 'New Value', 'Delta %'])
    for edit in all_edits:
        ws_edits.append([edit['line_type'], edit['material_number'], edit['material_name'],
                         edit['period'], edit['original'], edit['new'], edit['delta_pct']])

    wb.save(path)


@app.route('/api/editable_line_types')
def get_editable_line_types():
    return jsonify({'editable': sorted(EDITABLE_LINE_TYPES)})


@app.route('/api/sessions/edits/persist', methods=['POST'])
def persist_session_edit():
    """Save a single cell edit into the session's persistent pending_edits store."""
    req = request.get_json() or {}
    session_id = req.get('session_id', '')
    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    key = req.get('key', '').strip()
    if not key:
        return jsonify({'error': 'Cell key required'}), 400
    original = float(req.get('original', 0))
    new_value = float(req.get('new_value', 0))
    pending = sessions[session_id].setdefault('pending_edits', {})
    if abs(new_value - original) < 0.0001:
        pending.pop(key, None)   # edit reverted to original â€” remove entry
    else:
        pending[key] = {'original': original, 'new_value': new_value}
    _save_sessions_to_disk()
    return jsonify({'success': True})


@app.route('/api/sessions/edits/sync', methods=['POST'])
def sync_session_edits():
    """Replace the entire pending_edits store for a session (used after undo/redo/reset/import)."""
    req = request.get_json() or {}
    session_id = req.get('session_id', '')
    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    edits = req.get('edits', {})
    if not isinstance(edits, dict):
        return jsonify({'error': 'edits must be an object'}), 400
    sessions[session_id]['pending_edits'] = {
        k: {'original': float(v.get('original', 0)), 'new_value': float(v.get('new_value', 0))}
        for k, v in edits.items()
        if isinstance(v, dict)
    }
    _save_sessions_to_disk()
    return jsonify({'success': True})


@app.route('/api/update_volume', methods=['POST'])
def update_volume():
    sess, current_engine = _get_active()

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

    return _apply_volume_change(sess, current_engine, line_type, material_number, period, new_value,
                                 aux_column=aux_column,
                                 push_undo=True)


@app.route('/api/update_value_aux', methods=['POST'])
def update_value_aux():
    sess, current_engine = _get_active()

    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    data = request.get_json() or {}
    line_type = data.get('line_type')
    material_number = data.get('material_number')
    try:
        new_value = float(data.get('new_value', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid aux value'}), 400

    if line_type not in VALUE_AUX_EDITABLE_LINE_TYPES:
        return jsonify({'error': f'Value aux for line type "{line_type}" is not editable'}), 403

    rows = current_engine.value_results.get(line_type, [])
    target_row = next((r for r in rows if r.material_number == material_number), None)
    if target_row is None:
        return jsonify({'error': 'Value row not found'}), 404
    _ensure_reset_baseline(sess, current_engine)

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

    _recalculate_value_results(current_engine, sess)
    _save_sessions_to_disk()

    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]
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


@app.route('/api/reset_value_planning_edits', methods=['POST'])
def reset_value_planning_edits():
    global _global_config
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    sess['value_aux_overrides'] = {}

    # Restore valuation parameters from the pre-edit baseline so that global param
    # changes (NBV, DSO, etc.) are also rolled back, not just per-material aux overrides.
    baseline_vp = (sess.get('reset_baseline') or {}).get('valuation_params')
    restored_vp = None
    if baseline_vp and getattr(current_engine, 'data', None) is not None:
        current_engine.data.valuation_params = _valuation_params_from_config(baseline_vp)
        _global_config.setdefault('valuation_params', {})
        _global_config['valuation_params'] = {str(k): float(v) for k, v in baseline_vp.items()}
        restored_vp = baseline_vp

    _recalculate_value_results(current_engine, sess)
    _save_sessions_to_disk()

    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]

    resp = {
        'success': True,
        'value_results': value_results_dict,
        'consolidation': consolidation,
        'value_aux_overrides': {},
    }
    if restored_vp is not None:
        resp['restored_valuation_params'] = restored_vp
    return jsonify(resp)


@app.route('/api/undo', methods=['POST'])
def undo_edit():
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    undo_stack = sess.get('undo_stack', [])
    redo_stack = sess.setdefault('redo_stack', [])
    if not undo_stack:
        return jsonify({'error': 'Nothing to undo'}), 400

    entry = undo_stack.pop()
    line_type = entry['line_type']
    material_number = entry['material_number']
    period = entry['period']
    aux_column = entry.get('aux_column', '')
    restore_value = entry['old_value']

    # Push onto redo stack before restoring
    redo_stack.append(entry)
    if len(redo_stack) > 50:
        redo_stack.pop(0)

    # Apply restored value by delegating to update_volume logic (via internal helper)
    return _apply_volume_change(sess, current_engine, line_type, material_number, period, restore_value,
                                 aux_column=aux_column,
                                 push_undo=False)


@app.route('/api/redo', methods=['POST'])
def redo_edit():
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    undo_stack = sess.setdefault('undo_stack', [])
    redo_stack = sess.get('redo_stack', [])
    if not redo_stack:
        return jsonify({'error': 'Nothing to redo'}), 400

    entry = redo_stack.pop()
    line_type = entry['line_type']
    material_number = entry['material_number']
    period = entry['period']
    aux_column = entry.get('aux_column', '')
    redo_value = entry['new_value']

    undo_stack.append(entry)
    if len(undo_stack) > 50:
        undo_stack.pop(0)

    return _apply_volume_change(sess, current_engine, line_type, material_number, period, redo_value,
                                 aux_column=aux_column,
                                 push_undo=False)


def _apply_volume_change(sess, current_engine, line_type, material_number, period, new_value,
                          aux_column='',
                          push_undo=True):
    """Internal helper: apply a volume change + cascade and return jsonify result.

    Used by /api/update_volume (via direct code), /api/undo, /api/redo.
    """
    if line_type not in EDITABLE_LINE_TYPES:
        return jsonify({'error': f'Line type "{line_type}" is not editable'}), 403
    rows = current_engine.results.get(line_type, [])
    material_number = str(material_number)
    aux_column = str(aux_column or '').strip()
    material_rows = [r for r in rows if str(getattr(r, 'material_number', '')) == material_number]
    target_row = next(
        (r for r in material_rows
         if str(getattr(r, 'aux_column', '') or '').strip() == aux_column),
        None
    )
    if target_row is None and len(material_rows) == 1:
        # Backward-compatible fallback for older clients and harmless formatting drift.
        target_row = material_rows[0]
    if target_row is None:
        available_aux = sorted({str(getattr(r, 'aux_column', '') or '').strip() for r in material_rows})
        detail = f'Row not found for {line_type} / {material_number}'
        if aux_column:
            detail += f' / aux "{aux_column}"'
        if available_aux:
            detail += f'. Available aux: {", ".join(available_aux[:6])}'
        return jsonify({'error': detail}), 404
    _ensure_reset_baseline(sess, current_engine)

    # Enforce ceiling rounding for L06 line types so the stored value always
    # respects the configured lot multiple (BOM header qty for production plan,
    # MOQ for purchase receipt). Only applies when new_value > 0; setting to 0
    # is allowed unconditionally (manual clearance).
    if new_value > 0 and line_type in (LineType.PRODUCTION_PLAN.value, LineType.PURCHASE_RECEIPT.value):
        from modules.inventory_engine import ceiling_multiple as _ceil_mult
        if line_type == LineType.PRODUCTION_PLAN.value:
            _multiple = current_engine.data.get_production_ceiling(material_number)
        else:
            _multiple = current_engine.data.get_purchase_moq(material_number)
        if _multiple and _multiple > 0:
            new_value = _ceil_mult(new_value, _multiple)

    old_value = target_row.get_value(period)

    if push_undo:
        undo_stack = sess.setdefault('undo_stack', [])
        sess.setdefault('redo_stack', []).clear()
        undo_stack.append({'line_type': line_type, 'material_number': material_number,
                           'aux_column': str(getattr(target_row, 'aux_column', '') or ''),
                           'period': period, 'old_value': old_value, 'new_value': new_value})
        if len(undo_stack) > 50:
            undo_stack.pop(0)

    # Update manual_edits tracking
    if period not in target_row.manual_edits:
        target_row.manual_edits[period] = {'original': old_value, 'new': new_value}
    else:
        target_row.manual_edits[period]['new'] = new_value
    # If restored to original, remove the edit tracking entry
    original_val = target_row.manual_edits[period].get('original', old_value)
    if new_value == original_val:
        target_row.manual_edits.pop(period, None)

    target_row.set_value(period, new_value)

    # Keep pending_edits in sync server-side so the edit survives a server restart
    # without needing the frontend's separate /api/sessions/edits/persist call.
    _edit_key = f"{line_type}||{material_number}||{aux_column}||{period}"
    _pending = sess.setdefault('pending_edits', {})
    if new_value == original_val:
        _pending.pop(_edit_key, None)
    else:
        # Preserve the original baseline from any existing entry so repeated edits
        # of the same cell don't overwrite the true pre-edit value.
        _baseline = _pending.get(_edit_key, {}).get('original', original_val)
        _pending[_edit_key] = {'original': _baseline, 'new_value': new_value}

    if line_type == LineType.MIN_TARGET_STOCK.value:
        target_stock_values = dict(target_row.values)
        _recalc_material_subtree(
            current_engine,
            material_number,
            override_root_forecast=False,
            root_override_target_stock_values=target_stock_values,
            preserve_root_l05=True,
        )
        _recalculate_capacity_and_values(current_engine, sess)

    elif line_type == LineType.DEMAND_FORECAST.value:
        _recalc_material_subtree(
            current_engine,
            material_number,
            override_root_forecast=True,
            root_override_target_stock=None,
            preserve_root_l05=True,
        )
        _recalculate_capacity_and_values(current_engine, sess)

    elif line_type in (LineType.PRODUCTION_PLAN.value, LineType.PURCHASE_RECEIPT.value):
        from modules.bom_engine import BOMEngine
        from modules.inventory_engine import InventoryEngine

        periods_list = current_engine.data.periods

        # Gather current rows (target_row already has new_value applied)
        prod_row  = next((r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
                          if r.material_number == material_number), None)
        purch_row = next((r for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
                          if r.material_number == material_number), None)
        l05_row   = next((r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
                          if r.material_number == material_number), None)

        # Recalculate this material's own inventory and L06/L07 using fixed manual overrides for edited periods.
        # This avoids silently overwriting the user's manual edit while still refreshing later periods.
        inv_eng = InventoryEngine(current_engine.data)
        fc_row = next(
            (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
             if r.material_number == material_number), None
        )
        forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}

        mat_l02 = [r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                   if r.material_number == material_number]
        dep_demand_agg = {p: 0.0 for p in periods_list}
        dep_demand_by_parent = {}
        for r in mat_l02:
            parent = r.aux_column
            if parent:
                dep_demand_by_parent[parent] = dict(r.values)
                for p in periods_list:
                    dep_demand_agg[p] = dep_demand_agg.get(p, 0.0) + r.values.get(p, 0.0)

        inv_result = inv_eng.calculate_for_material(
            material_number,
            forecast_vals,
            dep_demand_agg,
            dep_demand_by_parent,
            override_target_stock_values=dict(l05_row.values) if l05_row else None,
            fixed_production_plan=_fixed_manual_values(prod_row),
            fixed_purchase_receipt=_fixed_manual_values(purch_row),
        )

        inv_line_types = [
            LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
            LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
            LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
        ]
        # Preserve manual_edits markers for L06/L07 across the rebuild â€” otherwise
        # a subsequent edit would forget prior edited months and recompute them heuristically.
        prior_prod_edits = dict(prod_row.manual_edits) if prod_row and getattr(prod_row, 'manual_edits', None) else {}
        prior_purch_edits = dict(purch_row.manual_edits) if purch_row and getattr(purch_row, 'manual_edits', None) else {}
        for lt in inv_line_types:
            current_engine.results[lt] = [
                r for r in current_engine.results.get(lt, []) if r.material_number != material_number
            ]

        for row in inv_result['rows']:
            if row.line_type == LineType.MIN_TARGET_STOCK.value and l05_row:
                row.values = dict(l05_row.values)
                row.manual_edits = dict(l05_row.manual_edits)
            elif row.line_type == LineType.PRODUCTION_PLAN.value and prior_prod_edits:
                row.manual_edits = prior_prod_edits
            elif row.line_type == LineType.PURCHASE_RECEIPT.value and prior_purch_edits:
                row.manual_edits = prior_purch_edits
            if row.line_type in current_engine.results:
                current_engine.results[row.line_type].append(row)

        if inv_result['production_plan'] is not None:
            current_engine.all_production_plans[material_number] = inv_result['production_plan']
        else:
            current_engine.all_production_plans.pop(material_number, None)
        if inv_result['purchase_receipt'] is not None:
            current_engine.all_purchase_receipts[material_number] = inv_result['purchase_receipt']
        else:
            current_engine.all_purchase_receipts.pop(material_number, None)

        # Rebuild L08 dependent requirements from the (now updated) production plan
        bom_eng = BOMEngine(current_engine.data)
        current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
            if r.material_number != material_number
        ]
        children_demand = {}
        prod_row = next((r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
                          if r.material_number == material_number), None)
        if prod_row is not None:
            children_demand = bom_eng.compute_dependent_requirements(
                material_number, dict(prod_row.values)
            )
            if children_demand:
                dr_rows = bom_eng.create_dependent_requirements_rows(material_number, children_demand)
                current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

        # Propagate to child materials: refresh direct L02 links from edited parent
        for child_mat, child_period_demand in children_demand.items():
            current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if not (r.material_number == child_mat and r.aux_column == material_number)
            ]
            child_l02_new = bom_eng.create_dependent_demand_rows(
                child_mat, {material_number: child_period_demand}
            )
            current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)

        # Full downstream cascade: recalculate every affected child (and deeper levels)
        inv_eng = InventoryEngine(current_engine.data)
        queue = list(children_demand.keys())
        visited = {material_number}
        while queue:
            child_mat = queue.pop(0)
            if child_mat in visited:
                continue
            visited.add(child_mat)
            grandchildren = _recalc_one_material(
                current_engine,
                child_mat,
                inv_eng,
                bom_eng,
                periods_list,
                override_forecast=False,
            )
            queue.extend(gc for gc in grandchildren if gc not in visited)

        _recalculate_capacity_and_values(current_engine, sess)

    else:
        _recalculate_value_results(current_engine, sess)

    delta_pct = round((new_value - original_val) / abs(original_val) * 100, 2) if original_val != 0 else 0.0
    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]
    return jsonify({
        'success': True,
        'results': results_dict,
        'value_results': value_results_dict,
        'consolidation': consolidation,
        'edit_meta': {
            'old_value': old_value,
            'new_value': new_value,
            'original_value': original_val,
            'delta_pct': delta_pct,
        },
    })


@app.route('/api/edits/export')
def export_edits():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    edits = []
    for _, rows in current_engine.results.items():
        for row in rows:
            if row.manual_edits:
                for period, edit_data in row.manual_edits.items():
                    original = edit_data.get('original', 0.0)
                    new_val = edit_data.get('new', 0.0)
                    delta_pct = round((new_val - original) / abs(original) * 100, 2) if original != 0 else 0.0
                    edits.append({
                        'line_type': row.line_type,
                        'material_number': row.material_number,
                        'aux_column': getattr(row, 'aux_column', '') or '',
                        'period': period,
                        'original': original,
                        'new': new_val,
                        'delta_pct': delta_pct,
                    })

    value_aux_edits = []
    sess = sessions.get(active_session_id) if active_session_id else None
    for key, item in (sess or {}).get('value_aux_overrides', {}).items():
        try:
            line_type, material_number = key.split('||', 1)
            original = float(item.get('original', 0))
            new_val = float(item.get('new_value', original))
        except (AttributeError, TypeError, ValueError):
            continue
        delta_pct = round((new_val - original) / abs(original) * 100, 2) if original != 0 else 0.0
        value_aux_edits.append({
            'line_type': line_type,
            'material_number': material_number,
            'original': original,
            'new': new_val,
            'delta_pct': delta_pct,
        })

    export_data = {
        'exported_at': datetime.now().isoformat(),
        'edits': edits,
        'value_aux_edits': value_aux_edits,
    }
    buf = io.BytesIO(json.dumps(export_data, indent=2).encode('utf-8'))
    buf.seek(0)
    return send_file(buf, mimetype='application/json', as_attachment=True,
                     download_name='edits.json')


@app.route('/api/edits/import', methods=['POST'])
def import_edits():
    sess, current_engine = _get_active()

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
            resp = _apply_volume_change(
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
                if line_type not in VALUE_AUX_EDITABLE_LINE_TYPES:
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

        # Re-run value planning
        _recalculate_value_results(current_engine, sess)
        _save_sessions_to_disk()

        results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
        value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
        consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]

        return jsonify({
            'success': True,
            'results': results_dict,
            'value_results': value_results_dict,
            'consolidation': consolidation,
            'value_aux_overrides': sess.get('value_aux_overrides', {}),
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/reset_edits', methods=['POST'])
def reset_edits():
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    baseline = sess.get('reset_baseline')
    baseline_is_clean = (
        isinstance(baseline, dict)
        and baseline.get('results')
        and not _snapshot_has_manual_edits(baseline)
    )
    if baseline_is_clean:
        _restore_engine_state(current_engine, baseline)
        engine = current_engine
    else:
        # Fallback: rebuild from source file when no clean baseline exists.
        engine = _build_clean_engine_for_session(sess)
        if engine is None:
            return jsonify({'error': 'No clean reset baseline available. Recalculate this session first.'}), 400
        sess['engine'] = engine

    # Clear all edit tracking
    sess['pending_edits'] = {}
    sess['value_aux_overrides'] = {}
    sess['undo_stack'] = []
    sess['redo_stack'] = []
    # Recompute values from restored planning state to guarantee consistency.
    _recalculate_value_results(engine, sess)
    _install_clean_engine_baseline(sess, engine)
    _save_sessions_to_disk()

    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in engine.value_results.items()}
    consolidation = [r.to_dict() for r in engine.value_results.get(LineType.CONSOLIDATION.value, [])]

    resp = {
        'success': True,
        'results': results_dict,
        'value_results': value_results_dict,
        'consolidation': consolidation,
    }
    restored_vp = _global_config.get('valuation_params')
    if restored_vp:
        resp['restored_valuation_params'] = restored_vp
    return jsonify(resp)


# ---- Prod/Purch Split endpoints ----

def _fixed_manual_values(row):
    if not row or not getattr(row, 'manual_edits', None):
        return {}
    return {
        period: float(edit.get('new', row.values.get(period, 0.0) or 0.0))
        for period, edit in row.manual_edits.items()
    }


def _recalc_one_material(
    current_engine,
    mat,
    inv_eng,
    bom_eng,
    periods_list,
    override_forecast=False,
    override_target_stock=None,
    override_target_stock_values=None,
    preserve_l05=True,
):
    """Recalculate inventory + BOM for one material. Updates results in-place.
    Returns {child_mat: child_period_demand} so the caller can cascade further."""
    fc_row = next(
        (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
         if r.material_number == mat), None
    )
    forecast_vals = dict(fc_row.values) if fc_row else {p: 0.0 for p in periods_list}

    mat_l02 = [r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
               if r.material_number == mat]
    dep_demand_agg = {p: 0.0 for p in periods_list}
    dep_demand_by_parent = {}
    for r in mat_l02:
        parent = r.aux_column
        if parent:
            dep_demand_by_parent[parent] = dict(r.values)
            for p in periods_list:
                dep_demand_agg[p] = dep_demand_agg.get(p, 0.0) + r.values.get(p, 0.0)

    l05_row = next(
        (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
         if r.material_number == mat), None
    )
    l05_saved_values = dict(l05_row.values) if l05_row else {}
    l05_saved_edits = dict(l05_row.manual_edits) if l05_row else {}

    # Preserve manually edited periods for production plan and purchase receipt so
    # cascade recalculations (demand change, BOM cascade) honour sticky overrides â€”
    # identical to the direct-edit path in _apply_volume_change.
    prod_row_pre = next(
        (r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
         if r.material_number == mat), None
    )
    purch_row_pre = next(
        (r for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
         if r.material_number == mat), None
    )
    prior_prod_edits  = dict(prod_row_pre.manual_edits)  if prod_row_pre  and getattr(prod_row_pre,  'manual_edits', None) else {}
    prior_purch_edits = dict(purch_row_pre.manual_edits) if purch_row_pre and getattr(purch_row_pre, 'manual_edits', None) else {}
    fixed_prod  = _fixed_manual_values(prod_row_pre)  or None
    fixed_purch = _fixed_manual_values(purch_row_pre) or None

    kwargs = {}
    if override_forecast:
        kwargs['override_forecast'] = forecast_vals
    if override_target_stock_values is not None:
        kwargs['override_target_stock_values'] = override_target_stock_values
    if override_target_stock is not None:
        kwargs['override_target_stock'] = override_target_stock
    if fixed_prod:
        kwargs['fixed_production_plan'] = fixed_prod
    if fixed_purch:
        kwargs['fixed_purchase_receipt'] = fixed_purch
    inv_result = inv_eng.calculate_for_material(
        mat, forecast_vals, dep_demand_agg, dep_demand_by_parent, **kwargs
    )

    inv_line_types = [
        LineType.TOTAL_DEMAND.value, LineType.INVENTORY.value,
        LineType.MIN_TARGET_STOCK.value, LineType.PRODUCTION_PLAN.value,
        LineType.PURCHASE_RECEIPT.value, LineType.PURCHASE_PLAN.value,
    ]
    for lt in inv_line_types:
        current_engine.results[lt] = [
            r for r in current_engine.results.get(lt, []) if r.material_number != mat
        ]
    for row in inv_result['rows']:
        if row.line_type in current_engine.results:
            current_engine.results[row.line_type].append(row)

    if inv_result.get('purch_raw_need'):
        current_engine.all_purch_raw_needs[mat] = inv_result['purch_raw_need']
    else:
        current_engine.all_purch_raw_needs.pop(mat, None)

    new_l05 = next(
        (r for r in current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
         if r.material_number == mat), None
    )
    if new_l05 and preserve_l05:
        new_l05.values = l05_saved_values
        new_l05.manual_edits = l05_saved_edits

    # Restore manual_edits markers on the rebuilt L06 rows so the UI can still
    # tell which periods were manually set (edit indicators, undo stack, etc.).
    new_prod_row = next(
        (r for r in current_engine.results.get(LineType.PRODUCTION_PLAN.value, [])
         if r.material_number == mat), None
    )
    if new_prod_row and prior_prod_edits:
        new_prod_row.manual_edits = prior_prod_edits
    new_purch_row = next(
        (r for r in current_engine.results.get(LineType.PURCHASE_RECEIPT.value, [])
         if r.material_number == mat), None
    )
    if new_purch_row and prior_purch_edits:
        new_purch_row.manual_edits = prior_purch_edits

    if inv_result['production_plan'] is not None:
        current_engine.all_production_plans[mat] = inv_result['production_plan']
    else:
        current_engine.all_production_plans.pop(mat, None)
    if inv_result['purchase_receipt'] is not None:
        current_engine.all_purchase_receipts[mat] = inv_result['purchase_receipt']
    else:
        current_engine.all_purchase_receipts.pop(mat, None)

    # Compute dependent requirements and push updated L02/L03 to children
    current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value] = [
        r for r in current_engine.results.get(LineType.DEPENDENT_REQUIREMENTS.value, [])
        if r.material_number != mat
    ]
    children_demand = {}
    if inv_result['production_plan'] is not None:
        children_demand = bom_eng.compute_dependent_requirements(mat, inv_result['production_plan'])
        if children_demand:
            dr_rows = bom_eng.create_dependent_requirements_rows(mat, children_demand)
            current_engine.results[LineType.DEPENDENT_REQUIREMENTS.value].extend(dr_rows)

    for child_mat, child_period_demand in children_demand.items():
        current_engine.results[LineType.DEPENDENT_DEMAND.value] = [
            r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
            if not (r.material_number == child_mat and r.aux_column == mat)
        ]
        child_l02_new = bom_eng.create_dependent_demand_rows(
            child_mat, {mat: child_period_demand}
        )
        current_engine.results[LineType.DEPENDENT_DEMAND.value].extend(child_l02_new)

        child_l01_row = next(
            (r for r in current_engine.results.get(LineType.DEMAND_FORECAST.value, [])
             if r.material_number == child_mat), None
        )
        child_l03_row = next(
            (r for r in current_engine.results.get(LineType.TOTAL_DEMAND.value, [])
             if r.material_number == child_mat), None
        )
        if child_l03_row:
            child_all_l02 = [
                r for r in current_engine.results.get(LineType.DEPENDENT_DEMAND.value, [])
                if r.material_number == child_mat
            ]
            for p in periods_list:
                fc_val = child_l01_row.values.get(p, 0.0) if child_l01_row else 0.0
                dep_val = sum(r.values.get(p, 0.0) for r in child_all_l02)
                child_l03_row.values[p] = fc_val + dep_val

    return children_demand


def _recalc_material_subtree(
    current_engine,
    root_material,
    override_root_forecast=False,
    root_override_target_stock=None,
    root_override_target_stock_values=None,
    preserve_root_l05=True,
):
    """Recalculate one edited material and recursively all impacted descendants."""
    from modules.inventory_engine import InventoryEngine
    from modules.bom_engine import BOMEngine

    inv_eng = InventoryEngine(current_engine.data)
    bom_eng = BOMEngine(current_engine.data)
    periods_list = current_engine.data.periods

    root_children = _recalc_one_material(
        current_engine,
        root_material,
        inv_eng,
        bom_eng,
        periods_list,
        override_forecast=override_root_forecast,
        override_target_stock=root_override_target_stock,
        override_target_stock_values=root_override_target_stock_values,
        preserve_l05=preserve_root_l05,
    )

    queue = list(root_children.keys())
    visited = {root_material}
    while queue:
        child_mat = queue.pop(0)
        if child_mat in visited:
            continue
        visited.add(child_mat)
        grandchildren = _recalc_one_material(
            current_engine,
            child_mat,
            inv_eng,
            bom_eng,
            periods_list,
            override_forecast=False,
            preserve_l05=True,
        )
        queue.extend(gc for gc in grandchildren if gc not in visited)


def _recalculate_capacity_and_values(current_engine, sess):
    """Run capacity + value planning after volume cascades."""
    from modules.capacity_engine import CapacityEngine

    _all_line_data = {
        lt: {r.material_number: r.values for r in rows}
        for lt, rows in current_engine.results.items() if rows
    }
    cap_eng = CapacityEngine(current_engine.data, current_engine.all_production_plans, _all_line_data)
    cap_results = cap_eng.calculate()
    for lt, cap_rows in cap_results.items():
        current_engine.results[lt] = cap_rows
    _recalculate_value_results(current_engine, sess)


def _recalc_pap_material(current_engine, material_number):
    """Re-run inventory + full BOM cascade for a PAP material change.
    Uses BFS so every child (and grandchild, etc.) gets its inventory recalculated
    after its dependent demand is updated â€” not just L02/L03."""
    from modules.inventory_engine import InventoryEngine
    from modules.bom_engine import BOMEngine

    inv_eng = InventoryEngine(current_engine.data)
    bom_eng = BOMEngine(current_engine.data)
    periods_list = current_engine.data.periods

    # Recalculate the PAP material itself (override_forecast keeps the PAP split intact)
    children_demand = _recalc_one_material(
        current_engine, material_number, inv_eng, bom_eng, periods_list,
        override_forecast=True,
    )

    # BFS: recalculate every affected child's inventory so the cascade is complete
    queue = list(children_demand.keys())
    visited = {material_number}
    while queue:
        child_mat = queue.pop(0)
        if child_mat in visited:
            continue
        visited.add(child_mat)
        grandchildren_demand = _recalc_one_material(
            current_engine, child_mat, inv_eng, bom_eng, periods_list,
            override_forecast=False,
        )
        queue.extend(gc for gc in grandchildren_demand if gc not in visited)


def _finish_pap_recalc(current_engine):
    """Run capacity + value engines after a PAP fraction change."""
    sess = sessions.get(active_session_id) if active_session_id else None
    _recalculate_capacity_and_values(current_engine, sess)


_SESSION_SAVE_PATHS = {
    '/api/sessions/rename',
    '/api/sessions/switch',
    '/api/sessions/snapshot',
    '/api/sessions/edits/persist',
    '/api/sessions/edits/sync',
    '/api/upload',
    '/api/calculate',
    '/api/update_volume',
    '/api/undo',
    '/api/redo',
    '/api/reset_edits',
    '/api/reset_value_planning_edits',
    '/api/scenarios/save',
    '/api/scenarios/load',
    '/api/export_db',
}

_SESSION_SAVE_METHODS = {'POST', 'DELETE'}


@app.after_request
def _after_request_save(response):
    """Auto-save sessions to disk after any mutating request."""
    if request.method in _SESSION_SAVE_METHODS and (
        request.path in _SESSION_SAVE_PATHS
        or (request.method == 'DELETE' and request.path.startswith('/api/sessions/'))
        or (request.method == 'DELETE' and request.path.startswith('/api/scenarios/'))
    ):
        if response.status_code < 500:
            _save_sessions_to_disk()
    return response


if __name__ == '__main__':
    host = os.getenv('SOP_HOST', '127.0.0.1')
    port = int(os.getenv('SOP_PORT', '5000'))
    app.run(debug=False, host=host, port=port)



