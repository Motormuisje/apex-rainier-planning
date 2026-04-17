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
from collections import defaultdict

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
from ui.routes.license import create_license_blueprint
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
from modules.mom_comparison_engine import MoMComparisonEngine
from modules.database_exporter import DatabaseExporter
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


_load_global_config()
_apply_folder_config()
_load_sessions_from_disk()


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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config/folders', methods=['GET'])
def get_folder_config():
    defs = _default_folders()
    saved = _global_config.get('folders', {})
    return jsonify({
        'uploads':  saved.get('uploads')  or defs['uploads'],
        'exports':  saved.get('exports')  or defs['exports'],
        'sessions': saved.get('sessions') or defs['sessions'],
        'defaults': defs,
    })


@app.route('/api/config/folders', methods=['POST'])
def save_folder_config():
    global APP_UPLOADS_DIR, APP_EXPORTS_DIR, SESSIONS_STORE, _cycle_manager
    data = request.get_json(force=True) or {}
    defs = _default_folders()

    uploads  = (data.get('uploads')  or '').strip() or defs['uploads']
    exports  = (data.get('exports')  or '').strip() or defs['exports']
    sessions_dir = (data.get('sessions') or '').strip() or defs['sessions']

    errors = []
    for label, path in [('uploads', uploads), ('exports', exports), ('sessions', sessions_dir)]:
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f'{label}: {e}')
    if errors:
        return jsonify({'success': False, 'errors': errors}), 400

    _global_config.setdefault('folders', {})
    _global_config['folders']['uploads']  = uploads
    _global_config['folders']['exports']  = exports
    _global_config['folders']['sessions'] = sessions_dir
    _save_global_config()

    APP_UPLOADS_DIR = Path(uploads)
    APP_EXPORTS_DIR = Path(exports)
    SESSIONS_STORE  = Path(sessions_dir) / 'sessions_store.json'
    _cycle_manager  = CycleManager(str(APP_EXPORTS_DIR))

    return jsonify({'success': True})


@app.route('/api/config', methods=['GET'])
def get_global_config():
    fd = _global_config.get('file_defaults', {})
    return jsonify({
        'master_filename': _global_config.get('master_filename'),
        'master_uploaded_at': _global_config.get('master_uploaded_at'),
        'master_file_exists': bool(
            _global_config.get('master_file') and
            Path(_global_config['master_file']).exists()
        ),
        'site': _global_config.get('site', ''),
        'forecast_months': _global_config.get('forecast_months', ''),
        'unlimited_machines': _global_config.get('unlimited_machines', ''),
        'purchased_and_produced': _global_config.get('purchased_and_produced', ''),
        'valuation_params': _global_config.get('valuation_params', {}),
        'file_defaults': {
            'site': fd.get('site', ''),
            'forecast_months': fd.get('forecast_months', 12),
            'unlimited_machines': fd.get('unlimited_machines', ''),
            'purchased_and_produced': fd.get('purchased_and_produced', ''),
            'valuation_params': fd.get('valuation_params', {}),
        },
    })


@app.route('/api/config/master-file', methods=['POST'])
def upload_master_file():
    global _global_config
    upload_dir = APP_UPLOADS_DIR
    upload_dir.mkdir(exist_ok=True)

    if 'master_file' not in request.files or request.files['master_file'].filename == '':
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['master_file']
    if not f.filename.lower().endswith(('.xlsm', '.xlsx')):
        return jsonify({'error': 'Only .xlsm or .xlsx files are accepted'}), 400

    dest = upload_dir / f.filename
    f.save(str(dest))

    # Quick validation: try loading config sheet
    try:
        _devnull = io.StringIO()
        sys.stdout, _saved = _devnull, sys.stdout
        try:
            from modules.data_loader import DataLoader
            loader = DataLoader(excel_file=str(dest))
            loader.load_all()
        finally:
            sys.stdout = _saved
    except Exception as exc:
        return jsonify({'error': 'Could not read file. Check that it contains the required sheets.'}), 400

    _global_config['master_file'] = str(dest)
    _global_config['master_filename'] = f.filename
    _global_config['master_uploaded_at'] = datetime.now().isoformat()

    # Store the values read from the file so the Config tab can show them as defaults
    site = getattr(loader.config, 'site', '') if loader.config else ''
    forecast_months = getattr(loader.config, 'forecast_months', 12) if loader.config else 12
    unlimited = ','.join(getattr(loader.config, 'unlimited_capacity_machine', []))
    pp = loader.purchased_and_produced or {}
    purchased_and_produced_str = ', '.join(f'{k}:{v}' for k, v in pp.items())
    vp = loader.valuation_params
    vp_dict = {}
    if vp:
        vp_dict = {
            '1': vp.direct_fte_cost_per_month,
            '2': vp.indirect_fte_cost_per_month,
            '3': vp.overhead_cost_per_month,
            '4': vp.sga_cost_per_month,
            '5': vp.depreciation_per_year,
            '6': vp.net_book_value,
            '7': vp.days_sales_outstanding,
            '8': vp.days_payable_outstanding,
        }
    _global_config['file_defaults'] = {
        'site': site,
        'forecast_months': forecast_months,
        'unlimited_machines': unlimited,
        'purchased_and_produced': purchased_and_produced_str,
        'valuation_params': vp_dict,
    }
    _save_global_config()

    return jsonify({
        'success': True,
        'master_filename': f.filename,
        'master_uploaded_at': _global_config['master_uploaded_at'],
        'file_defaults': _global_config['file_defaults'],
        'summary': {
            'materials': len(loader.materials),
            'machines': len(loader.machines),
        }
    })


@app.route('/api/config/settings', methods=['POST'])
def save_config_settings():
    global _global_config
    data = request.get_json() or {}
    sess, current_engine = _get_active()
    value_recalculated = False
    planning_recalculated = False
    old_config = {
        'site': str(_global_config.get('site', '') or ''),
        'forecast_months': int(_global_config.get('forecast_months', 0) or 0),
        'unlimited_machines': str(_global_config.get('unlimited_machines', '') or ''),
    }

    if 'site' in data:
        _global_config['site'] = data['site'].strip()
    if 'forecast_months' in data:
        _global_config['forecast_months'] = int(data['forecast_months'] or 12)
    if 'unlimited_machines' in data:
        _global_config['unlimited_machines'] = data['unlimited_machines'].strip()

    structural_config_changed = (
        str(_global_config.get('site', '') or '') != old_config['site']
        or int(_global_config.get('forecast_months', 0) or 0) != old_config['forecast_months']
        or str(_global_config.get('unlimited_machines', '') or '') != old_config['unlimited_machines']
    )

    if 'purchased_and_produced' in data:
        _global_config['purchased_and_produced'] = data['purchased_and_produced'].strip()
        if current_engine is not None and not structural_config_changed:
            old_pap = dict(getattr(current_engine.data, 'purchased_and_produced', {}) or {})
            new_pap = _parse_purchased_and_produced(_global_config['purchased_and_produced'])
            changed_mats = sorted({
                mat for mat in set(old_pap) | set(new_pap)
                if abs(float(old_pap.get(mat, -999999999)) - float(new_pap.get(mat, -999999999))) > 1e-9
            })
            current_engine.data.purchased_and_produced = new_pap
            if changed_mats:
                _ensure_reset_baseline(sess, current_engine)
                for mat in changed_mats:
                    _recalc_pap_material(current_engine, mat)
                _finish_pap_recalc(current_engine)
                planning_recalculated = True
            else:
                _recalculate_value_results(current_engine, sess)
            value_recalculated = True
    if 'valuation_params' in data:
        _global_config['valuation_params'] = {
            str(k): float(v) for k, v in data['valuation_params'].items() if v is not None
        }
        if current_engine is not None and not structural_config_changed and getattr(current_engine, 'data', None) is not None:
            current_engine.data.valuation_params = _valuation_params_from_config(
                _global_config['valuation_params']
            )
            _recalculate_value_results(current_engine, sess)
            value_recalculated = True

    if current_engine is not None and structural_config_changed:
        rebuilt = _build_clean_engine_for_session(sess)
        if rebuilt is None:
            return jsonify({'error': 'Could not rebuild active session for the changed config. Recalculate this session first.'}), 400
        _install_clean_engine_baseline(sess, rebuilt, clear_machine_overrides=False)
        with app.app_context():
            _replay_pending_edits(sess, rebuilt)
        sess['engine'] = rebuilt
        current_engine = rebuilt
        planning_recalculated = True
        value_recalculated = True

    _save_global_config()
    payload = {'success': True}
    if current_engine is not None and planning_recalculated:
        payload['periods'] = list(getattr(current_engine.data, 'periods', []) or [])
        payload['results'] = {
            lt: [row.to_dict() for row in rows]
            for lt, rows in (getattr(current_engine, 'results', {}) or {}).items()
        }
        payload.update(_moq_warnings_payload(current_engine))
    if current_engine is not None and value_recalculated:
        payload.update(_value_results_payload(current_engine))
    return jsonify(payload)


@app.route('/api/config/reset_vp_params', methods=['POST'])
def reset_vp_params_to_defaults():
    """Reset valuation parameters to the Excel default values from the session baseline."""
    global _global_config
    sess, current_engine = _get_active()
    if sess is None:
        return jsonify({'error': 'No active session'}), 400
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    baseline_vp = (sess.get('reset_baseline') or {}).get('valuation_params')
    if not baseline_vp:
        return jsonify({'error': 'No baseline available â€” run calculations first'}), 400

    current_engine.data.valuation_params = _valuation_params_from_config(baseline_vp)
    _global_config['valuation_params'] = {str(k): float(v) for k, v in baseline_vp.items()}
    _recalculate_value_results(current_engine, sess)
    _save_global_config()

    payload = {'success': True, 'valuation_params': baseline_vp}
    payload.update(_value_results_payload(current_engine))
    return jsonify(payload)


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


@app.route('/api/results')
def get_results():
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    results = {}
    for lt, rows in current_engine.results.items():
        results[lt] = [_row_payload(row) for row in rows]

    # Include compact baseline (pre-edit) values so the frontend can restore
    # cascade highlights after a session reload or instance switch.
    baseline = sess.get('reset_baseline') if sess else None
    baseline_results = None
    if baseline and baseline.get('results'):
        baseline_results = {
            lt: [
                {'material_number': r.get('material_number', ''),
                 'aux_column': r.get('aux_column', ''),
                 'values': r.get('values', {})}
                for r in rows
            ]
            for lt, rows in baseline['results'].items()
        }

    resp = {'periods': current_engine.data.periods, 'results': results}
    if baseline_results:
        resp['baseline_results'] = baseline_results
    resp.update(_moq_warnings_payload(current_engine))
    return jsonify(resp)


@app.route('/api/value_results')
def get_value_results():
    """Return value planning results (financial)."""
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    if not current_engine.value_results:
        return jsonify({'error': 'No value planning results available'}), 400

    results = {}
    for lt, rows in current_engine.value_results.items():
        results[lt] = [_row_payload(row) for row in rows]

    # Extract consolidation rows separately for the financial overview
    consolidation = []
    for row in current_engine.value_results.get(LineType.CONSOLIDATION.value, []):
        consolidation.append(_row_payload(row))

    # Include compact baseline value-results so the frontend can restore
    # VP cascade highlights after a session reload or instance switch.
    baseline = sess.get('reset_baseline') if sess else None
    baseline_value_results = None
    if baseline and baseline.get('value_results'):
        baseline_value_results = {
            lt: [
                {'material_number': r.get('material_number', ''),
                 'aux_column': r.get('aux_column', ''),
                 'values': r.get('values', {})}
                for r in rows
            ]
            for lt, rows in baseline['value_results'].items()
        }

    resp = {
        'periods': current_engine.data.periods,
        'results': results,
        'consolidation': consolidation,
    }
    if baseline_value_results:
        resp['baseline_value_results'] = baseline_value_results
    return jsonify(resp)


@app.route('/api/dashboard')
def get_dashboard():
    """Aggregated dashboard endpoint â€” single call returns all KPIs + chart data."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    periods = current_engine.data.periods

    # â”€â”€ KPI: materials count â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    materials_count = len(current_engine.data.materials)

    # â”€â”€ KPI: avg utilization from Line 10 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    util_rows = current_engine.results.get(LineType.UTILIZATION_RATE.value, [])
    all_util_vals = [v * 100 for row in util_rows for v in row.values.values() if v is not None]
    avg_utilization = round(sum(all_util_vals) / len(all_util_vals), 1) if all_util_vals else 0.0

    # â”€â”€ KPI: total FTE from Line 12, sum across all groups for latest period â”€
    fte_rows = current_engine.results.get(LineType.FTE_REQUIREMENTS.value, [])
    latest_period = periods[-1] if periods else None
    total_fte = round(
        sum(row.values.get(latest_period, 0.0) for row in fte_rows), 2
    ) if latest_period else 0.0

    # â”€â”€ utilization_by_machine (Line 10 rows, values as %) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    utilization_by_machine = []
    for row in util_rows:
        utilization_by_machine.append({
            'machine': row.material_name,
            'group': row.aux_column or '',
            'values': {p: round(v * 100, 1) for p, v in row.values.items()},
        })

    # â”€â”€ fte_by_group (Line 12 rows) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    fte_by_group = []
    for row in fte_rows:
        fte_by_group.append({
            'group': row.material_name,
            'values': {p: round(v, 2) for p, v in row.values.items()},
        })

    # â”€â”€ financials from consolidation rows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    financials = {}
    for row in current_engine.value_results.get(LineType.CONSOLIDATION.value, []):
        key = row.material_number.replace('ZZZZZZ_', '')
        # ROCE is a ratio (e.g. 0.042 = 4.2%) â€” preserve precision so the
        # frontend can multiply by 100.  All other P&L rows are large EUR
        # amounts that round cleanly to 0 decimal places.
        decimals = 6 if key == 'ROCE' else 0
        financials[key] = {p: round(v, decimals) for p, v in row.values.items()}

    # â”€â”€ inventory quality â€” cached in engine, recomputed only after an edit â”€â”€
    inventory_quality: list = []
    top_10_overstocks: list = []
    total_overstock = 0.0
    try:
        from modules.inventory_quality_engine import InventoryQualityEngine
        if not getattr(current_engine, '_iq_cache', None):
            iq_result = InventoryQualityEngine(
                current_engine.data,
                current_engine.results,
                current_engine.value_results,
            ).calculate()
            current_engine._iq_cache = iq_result
        else:
            iq_result = current_engine._iq_cache
        inventory_quality = iq_result.get('per_material', [])
        top_10_overstocks = iq_result.get('top_10_overstocks', [])
        total_overstock = iq_result.get('total_overstock', 0.0)
    except (ImportError, Exception):
        pass

    # Aggregate total demand (Line 03) across all materials per period
    demand_trend = {}
    for row in current_engine.results.get('03. Total demand', []):
        for p in periods:
            demand_trend[p] = round(demand_trend.get(p, 0.0) + row.values.get(p, 0.0), 1)

    # Aggregate inventory (Line 04) and target stock (Line 05) per period
    inventory_trend = {}
    target_trend = {}
    for row in current_engine.results.get('04. Inventory', []):
        for p in periods:
            inventory_trend[p] = round(inventory_trend.get(p, 0.0) + row.values.get(p, 0.0), 1)
    for row in current_engine.results.get('05. Minimum target stock', []):
        for p in periods:
            target_trend[p] = round(target_trend.get(p, 0.0) + row.values.get(p, 0.0), 1)

    return jsonify({
        'periods': periods,
        'kpis': {
            'materials': materials_count,
            'avg_utilization': avg_utilization,
            'total_fte': total_fte,
            'total_overstock': total_overstock,
        },
        'utilization_by_machine': utilization_by_machine,
        'fte_by_group': fte_by_group,
        'financials': financials,
        'inventory_quality': inventory_quality,
        'top_10_overstocks': top_10_overstocks,
        'demand_trend': demand_trend,
        'inventory_trend': inventory_trend,
        'target_trend': target_trend,
    })


@app.route('/api/capacity')
def get_capacity():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    utilization = []
    for row in current_engine.results.get(LineType.UTILIZATION_RATE.value, []):
        utilization.append({
            'machine': row.material_name,
            'group': row.product_family or '',
            'values': {p: round(v * 100, 1) for p, v in row.values.items()}
        })

    return jsonify({
        'periods': current_engine.data.periods,
        'utilization': utilization
    })


@app.route('/api/machines')
def get_machines():
    """Rich payload for the Machines tab: per-machine and per-group data.

    machines:  [{code, name, group, oee, shift_hours, avg_availability,
                 req_hours_avg, util_avg, throughput_theoretical, throughput_effective,
                 req_hours_by_period, util_by_period, throughput_effective_by_period,
                 availability_by_period}]
    groups:    [{group, machines, avg_oee, sum_req_hours_avg, util_avg,
                 throughput_theoretical, throughput_effective, req_hours_by_period,
                 util_by_period, throughput_effective_by_period, fte_by_period, fte_avg}]
    fte_totals_by_period: {period: total_fte}  (sum across all groups+trucks+control room)
    """
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    data = current_engine.data
    periods = data.periods
    baseline_machines = (sess.get('reset_baseline') or {}).get('machines') or {}
    machine_overrides = _machine_overrides_from_engine(sess, current_engine)
    sess['machine_overrides'] = machine_overrides

    def _avg(d):
        vals = [v for v in d.values() if v is not None]
        return sum(vals) / len(vals) if vals else 0.0

    def _avg_percent_from_fraction_map(d):
        vals = []
        for p in periods:
            if p not in (d or {}):
                continue
            try:
                vals.append(float(d[p]) * 100.0)
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
            current_shift = SHIFT_HOURS_LOOKUP_FALLBACK(machine, data)
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

    # --- Lookups from calculated lines ---
    util_rows = current_engine.results.get(LineType.UTILIZATION_RATE.value, [])
    util_by_machine = {r.material_name: r.values for r in util_rows}

    cap_rows = current_engine.results.get(LineType.CAPACITY_UTILIZATION.value, [])
    # Machine-level cap_util rows: product_type == 'Machine', material_name = machine code
    req_hours_by_machine = {}
    for r in cap_rows:
        if r.product_type == 'Machine' and r.material_name in data.machines:
            req_hours_by_machine[r.material_name] = r.values

    fte_rows = current_engine.results.get(LineType.FTE_REQUIREMENTS.value, [])
    fte_by_group = {r.material_number: r.values for r in fte_rows}

    # --- Throughput (theoretical) per machine: avg of base_qty/std_time across routings ---
    machine_throughput_theo = {}
    theo_lists = {}
    for mat_num in list(data.materials.keys()):
        try:
            routings = data.get_all_routings(mat_num)
        except Exception:
            continue
        for rt in routings:
            wc = rt.work_center
            if rt.base_quantity > 0 and rt.standard_time > 0:
                theo_lists.setdefault(wc, []).append(rt.base_quantity / rt.standard_time)
    for wc, lst in theo_lists.items():
        machine_throughput_theo[wc] = sum(lst) / len(lst) if lst else 0.0

    # --- Throughput (effective) per machine per period: production / machine_hours_used ---
    # Use Line 02 production plan as output units; divide by machine req hours.
    prod_plan = current_engine.all_production_plans if hasattr(current_engine, 'all_production_plans') else {}
    output_by_machine_period = {mc: {p: 0.0 for p in periods} for mc in data.machines}
    for mat_num, plan_data in prod_plan.items():
        try:
            routings = data.get_all_routings(mat_num)
        except Exception:
            continue
        for rt in routings:
            wc = rt.work_center
            if wc not in output_by_machine_period:
                continue
            for p in periods:
                qty = plan_data.get(p, 0.0)
                if qty > 0:
                    output_by_machine_period[wc][p] += qty

    def _effective_throughput_period(mc_code):
        out_p = output_by_machine_period.get(mc_code, {})
        req_p = req_hours_by_machine.get(mc_code, {})
        res = {}
        for p in periods:
            h = req_p.get(p, 0.0)
            res[p] = (out_p.get(p, 0.0) / h) if h > 0 else 0.0
        return res

    # --- Build per-machine list ---
    machines_out = []
    for mc_code, machine in data.machines.items():
        req_p = {p: round(req_hours_by_machine.get(mc_code, {}).get(p, 0.0), 2) for p in periods}
        util_p = {p: round(util_by_machine.get(mc_code, {}).get(p, 0.0) * 100, 1) for p in periods}
        avail_p = {p: round(machine.get_availability(p) * 100, 1) for p in periods}
        eff_p = {p: round(v, 2) for p, v in _effective_throughput_period(mc_code).items()}
        shift_hours = SHIFT_HOURS_LOOKUP_FALLBACK(machine, data)
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

    # --- Build per-group aggregation ---
    groups_map = {}
    for m in machines_out:
        grp = m['group'] or '(no group)'
        groups_map.setdefault(grp, []).append(m)

    groups_out = []
    for grp, mlist in groups_map.items():
        req_p = {p: round(sum(m['req_hours_by_period'][p] for m in mlist), 2) for p in periods}
        # util: weighted by availability*shift_hours (same as machine-level definition)
        util_p = {}
        for p in periods:
            total_avail = sum((m['availability_by_period'][p] / 100.0) * m['shift_hours'] for m in mlist)
            total_req = sum(m['req_hours_by_period'][p] for m in mlist)
            util_p[p] = round((total_req / total_avail * 100) if total_avail > 0 else 0.0, 1)
        eff_p = {}
        for p in periods:
            total_out = sum(output_by_machine_period.get(m['code'], {}).get(p, 0.0) for m in mlist)
            total_h = sum(m['req_hours_by_period'][p] for m in mlist)
            eff_p[p] = round((total_out / total_h) if total_h > 0 else 0.0, 2)
        theo_avg = sum(m['throughput_theoretical'] for m in mlist) / len(mlist) if mlist else 0.0
        oee_avg = sum(m['oee'] for m in mlist) / len(mlist) if mlist else 0.0

        fte_p = fte_by_group.get(grp, {})
        fte_p_rounded = {p: round(fte_p.get(p, 0.0), 2) for p in periods}

        groups_out.append({
            'group': grp,
            'machines': [m['code'] for m in mlist],
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

    # --- FTE totals per period (sum over ALL FTE rows: groups + trucks + control room) ---
    fte_totals = {p: 0.0 for p in periods}
    for r in fte_rows:
        for p in periods:
            fte_totals[p] += r.values.get(p, 0.0)
    fte_totals = {p: round(v, 2) for p, v in fte_totals.items()}

    return jsonify({
        'periods': periods,
        'machines': machines_out,
        'groups': groups_out,
        'fte_totals_by_period': fte_totals,
        'machine_overrides': machine_overrides,
        'undo_depth': len(sess.get('machine_undo') or []),
        'redo_depth': len(sess.get('machine_redo') or []),
    })


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


@app.route('/api/machines/update', methods=['POST'])
def update_machine_param():
    """Edit OEE or availability for a machine, then recalc capacity + values."""
    sess, current_engine = _get_active()
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

    _ensure_reset_baseline(sess, current_engine)

    # Capture the "before" value for the undo stack
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
        # Treated as percentage (0-100) applied uniformly to all periods
        if not (0 <= new_value <= 100):
            return jsonify({'error': 'availability must be 0-100'}), 400
        factor = new_value / 100.0
        # Snapshot existing per-period values so undo can restore non-uniform availability
        old_map = dict(machine.availability_by_period or {})
        new_map = {p: factor for p in current_engine.data.periods}
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

    # New edit clears the redo history
    redo_stack.clear()

    # Cap undo history so sessions don't grow unbounded
    if len(undo_stack) > 50:
        del undo_stack[:-50]

    _recalculate_capacity_and_values(current_engine, sess)
    sess['machine_overrides'] = _machine_overrides_from_engine(sess, current_engine)
    _save_sessions_to_disk()

    return jsonify({
        'success': True,
        'undo_depth': len(undo_stack),
        'redo_depth': 0,
        **_planning_value_payload(current_engine),
    })


@app.route('/api/machines/undo', methods=['POST'])
def undo_machine_param():
    sess, current_engine = _get_active()
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

    sess['machine_overrides'] = _machine_overrides_from_engine(sess, current_engine)
    _recalculate_capacity_and_values(current_engine, sess)
    _save_sessions_to_disk()

    return jsonify({
        'success': True,
        'undo_depth': len(undo_stack),
        'redo_depth': len(redo_stack),
        'restored': entry,
        **_planning_value_payload(current_engine),
    })


@app.route('/api/machines/reset', methods=['POST'])
def reset_machine_params():
    """Reset all machine OEE + availability to the baseline snapshot and recalc."""
    sess, current_engine = _get_active()
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
    _recalculate_capacity_and_values(current_engine, sess)
    _save_sessions_to_disk()
    return jsonify({'success': True, 'undo_depth': 0, 'redo_depth': 0, **_planning_value_payload(current_engine)})


@app.route('/api/machines/redo', methods=['POST'])
def redo_machine_param():
    """Re-apply a previously undone machine edit (OEE or availability)."""
    sess, current_engine = _get_active()
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

    sess['machine_overrides'] = _machine_overrides_from_engine(sess, current_engine)
    _recalculate_capacity_and_values(current_engine, sess)
    _save_sessions_to_disk()

    return jsonify({
        'success': True,
        'undo_depth': len(undo_stack),
        'redo_depth': len(redo_stack),
        'restored': entry,
        **_planning_value_payload(current_engine),
    })


@app.route('/api/inventory')
def get_inventory():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    
    inv_rows = current_engine.results.get(LineType.INVENTORY.value, [])
    tgt_rows = current_engine.results.get(LineType.MIN_TARGET_STOCK.value, [])
    
    target_lookup = {r.material_number: r.values for r in tgt_rows}
    periods = current_engine.data.periods
    
    data = []
    ok, low, high = 0, 0, 0
    
    for row in inv_rows:
        target = target_lookup.get(row.material_number, {})
        avg_inv = sum(row.values.get(p, 0) for p in periods) / len(periods) if periods else 0
        avg_tgt = sum(target.get(p, 0) for p in periods) / len(periods) if target and periods else 0
        
        status = 'OK'
        if avg_inv <= 0:
            status = 'LOW'
            low += 1
        elif avg_tgt > 0:
            if avg_inv < avg_tgt * 0.5:
                status = 'LOW'
                low += 1
            elif avg_inv > avg_tgt * 2:
                status = 'HIGH'
                high += 1
            else:
                ok += 1
        else:
            ok += 1
        
        data.append({
            'material_number': row.material_number,
            'material_name': row.material_name,
            'status': status,
            'values': row.values
        })
    
    return jsonify({
        'periods': current_engine.data.periods,
        'summary': {'healthy': ok, 'low': low, 'high': high},
        'data': data
    })


@app.route('/api/inventory_quality')
def get_inventory_quality():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    from modules.inventory_quality_engine import InventoryQualityEngine
    engine = InventoryQualityEngine(
        current_engine.data,
        current_engine.results,
        current_engine.value_results,
    )
    return jsonify(engine.calculate())


@app.route('/api/export')
def export():
    _, current_engine = _get_active()

    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    export_dir = APP_EXPORTS_DIR
    export_dir.mkdir(exist_ok=True)

    export_path = export_dir / f'SOP_Python_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'

    # Build inventory quality engine to pass for Top 10 sheet
    _iq_engine_export = None
    try:
        from modules.inventory_quality_engine import InventoryQualityEngine
        _iq_engine_export = InventoryQualityEngine(
            current_engine.data,
            current_engine.results,
            current_engine.value_results,
        )
    except Exception:
        pass

    # --- Load previous cycle for MoM comparison sheet ---
    _prev_df = None
    try:
        if _cycle_manager.has_previous_cycle():
            _prev_df = _cycle_manager.load_previous_cycle()
            if _prev_df.empty:
                _prev_df = None
                print('[export] Previous cycle loaded but empty - MoM sheet skipped')
            else:
                print(f'[export] Previous cycle loaded ({len(_prev_df)} rows) - MoM sheet will be included')
        else:
            print('[export] No previous cycle on disk - MoM sheet skipped (will be available after next calculation)')
    except Exception as _prev_exc:
        print(f'[export] Could not load previous cycle: {_prev_exc}')

    current_engine.to_excel_with_values(
        str(export_path),
        inventory_quality_engine=_iq_engine_export,
        previous_cycle_df=_prev_df,
    )

    # Apply edit highlights and summary sheet if there are any edits
    _apply_edit_highlights(str(export_path), current_engine)

    return send_file(str(export_path), as_attachment=True)


@app.route('/api/export_db', methods=['POST'])
def export_db():
    """Export planning results to a flat DB-ready Excel file via DatabaseExporter."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    try:
        planning_df = current_engine.to_dataframe()
        site = getattr(current_engine.data.config, 'site', 'NLX1')
        initial_date = current_engine.data.config.initial_date

        exporter = DatabaseExporter(planning_df, site, initial_date)
        db_df = exporter.export_to_dataframe()

        if db_df.empty:
            return jsonify({'error': 'No data to export (no matching line types)'}), 400

        export_dir = APP_EXPORTS_DIR
        export_dir.mkdir(exist_ok=True)

        # Allow caller to override filename via JSON body
        req_data = request.get_json(silent=True) or {}
        filename = req_data.get('filename', '').strip()
        if not filename:
            filename = f'SOP_DB_Export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        # Sanitise â€” keep only safe characters
        safe_name = ''.join(c for c in filename if c.isalnum() or c in '._- ')
        if not safe_name.endswith('.xlsx'):
            safe_name += '.xlsx'

        export_path = export_dir / safe_name
        db_df.to_excel(str(export_path), index=False)
        print(f'[export_db] {len(db_df)} rows written -> {export_path}')

        return send_file(str(export_path), as_attachment=True, download_name=safe_name)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/mom')
def get_mom_comparison():
    """Return sequential period-over-period MoM comparison from the current run."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'available': False, 'message': 'No calculations run yet. Run calculations first.'})

    try:
        num_months = int(request.args.get('num_months', 6))
        num_months = max(1, min(num_months, 24))

        current_df = current_engine.to_dataframe()
        result = MoMComparisonEngine.calculate_sequential(current_df, num_months)
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


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


@app.route('/api/pap', methods=['GET'])
def get_pap():
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    return jsonify({'pap': dict(current_engine.data.purchased_and_produced)})


@app.route('/api/pap', methods=['POST'])
def set_pap():
    global _global_config
    sess, current_engine = _get_active()
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
    _ensure_reset_baseline(sess, current_engine)
    current_engine.data.purchased_and_produced[mat] = fraction
    _global_config['purchased_and_produced'] = _format_purchased_and_produced(
        current_engine.data.purchased_and_produced
    )
    _recalc_pap_material(current_engine, mat)
    _finish_pap_recalc(current_engine)
    _save_global_config()
    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]
    return jsonify({'success': True, 'results': results_dict, 'value_results': value_results_dict,
                    'consolidation': consolidation, **_moq_warnings_payload(current_engine)})


@app.route('/api/pap/<material_number>', methods=['DELETE'])
def delete_pap(material_number):
    global _global_config
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400
    _ensure_reset_baseline(sess, current_engine)
    current_engine.data.purchased_and_produced.pop(material_number, None)
    _global_config['purchased_and_produced'] = _format_purchased_and_produced(
        current_engine.data.purchased_and_produced
    )
    _recalc_pap_material(current_engine, material_number)
    _finish_pap_recalc(current_engine)
    _save_global_config()
    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]
    return jsonify({'success': True, 'results': results_dict, 'value_results': value_results_dict,
                    'consolidation': consolidation, **_moq_warnings_payload(current_engine)})


# ---- Scenario endpoints ----

@app.route('/api/scenarios', methods=['GET'])
def list_scenarios():
    """List saved scenarios for the active session."""
    result = [
        {
            'id':         sid,
            'name':       sc['name'],
            'session_id': sc['session_id'],
            'timestamp':  sc['timestamp'],
            'edit_count': sc['edit_count'],
        }
        for sid, sc in scenarios.items()
        if sc['session_id'] == active_session_id
    ]
    result.sort(key=lambda x: x['timestamp'])
    return jsonify({'scenarios': result})


@app.route('/api/scenarios/save', methods=['POST'])
def save_scenario():
    """Deep-copy current volumes + cascaded values into a named scenario."""
    _, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    req = request.get_json() or {}
    name = req.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Scenario name is required'}), 400

    # Snapshot results: {lt: [{material_number, line_type, values, manual_edits, ...}]}
    results_snapshot = {}
    total_edits = 0
    for lt, rows in current_engine.results.items():
        results_snapshot[lt] = []
        for row in rows:
            results_snapshot[lt].append({
                'material_number': row.material_number,
                'material_name':   row.material_name,
                'product_type':    row.product_type,
                'product_family':  row.product_family,
                'spc_product':     row.spc_product,
                'product_cluster': row.product_cluster,
                'product_name':    row.product_name,
                'line_type':       row.line_type,
                'aux_column':      row.aux_column,
                'aux_2_column':    row.aux_2_column,
                'starting_stock':  row.starting_stock,
                'values':          dict(row.values),
                'manual_edits':    {p: dict(e) for p, e in row.manual_edits.items()},
            })
            total_edits += len(row.manual_edits)

    # Snapshot value_results
    value_snapshot = {}
    for lt, rows in current_engine.value_results.items():
        value_snapshot[lt] = []
        for row in rows:
            value_snapshot[lt].append({
                'material_number': row.material_number,
                'material_name':   row.material_name,
                'product_type':    row.product_type,
                'product_family':  row.product_family,
                'spc_product':     row.spc_product,
                'product_cluster': row.product_cluster,
                'product_name':    row.product_name,
                'line_type':       row.line_type,
                'aux_column':      row.aux_column,
                'aux_2_column':    row.aux_2_column,
                'starting_stock':  row.starting_stock,
                'values':          dict(row.values),
                'manual_edits':    {},
            })

    pending_snapshot = json.loads(json.dumps(sessions.get(active_session_id, {}).get('pending_edits', {})))
    if not pending_snapshot:
        pending_snapshot = _build_pending_edits_from_results_snapshot(results_snapshot)

    scenario_id = str(_uuid.uuid4())
    scenarios[scenario_id] = {
        'id':             scenario_id,
        'name':           name,
        'session_id':     active_session_id,
        'timestamp':      datetime.now().isoformat(),
        'edit_count':     total_edits,
        'results':        results_snapshot,
        'value_results':  value_snapshot,
        'pending_edits':  pending_snapshot,
        'value_aux_overrides': json.loads(json.dumps(sessions.get(active_session_id, {}).get('value_aux_overrides', {}))),
        'valuation_params': {str(k): float(v) for k, v in (_global_config.get('valuation_params') or {}).items()},
        'purchased_and_produced': _global_config.get('purchased_and_produced', ''),
    }
    return jsonify({'success': True, 'scenario_id': scenario_id, 'name': name, 'edit_count': total_edits})


@app.route('/api/scenarios/load', methods=['POST'])
def load_scenario():
    """Restore a saved scenario into the active engine."""
    sess, current_engine = _get_active()
    if current_engine is None:
        return jsonify({'error': 'No calculations run'}), 400

    req = request.get_json() or {}
    scenario_id = req.get('scenario_id', '')
    if not scenario_id or scenario_id not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404

    sc = scenarios[scenario_id]
    if sc['session_id'] != active_session_id:
        return jsonify({'error': 'Scenario belongs to a different session'}), 403

    # Restore results atomically from snapshot so added/removed dependent rows
    # are also restored exactly (not only matching rows).
    restored_results = {}
    for lt, snap_rows in (sc.get('results') or {}).items():
        restored_results[lt] = [
            _planning_row_from_snapshot(snap, fallback_line_type=lt)
            for snap in (snap_rows or [])
        ]
    for lt in current_engine.results.keys():
        restored_results.setdefault(lt, [])
    current_engine.results = restored_results

    restored_value_results = {}
    for lt, snap_rows in (sc.get('value_results') or {}).items():
        restored_value_results[lt] = [
            _planning_row_from_snapshot(snap, fallback_line_type=lt)
            for snap in (snap_rows or [])
        ]
    for lt in current_engine.value_results.keys():
        restored_value_results.setdefault(lt, [])
    current_engine.value_results = restored_value_results

    # Keep mutable session state aligned with loaded scenario so edits remain editable/persistable.
    restored_pending = json.loads(json.dumps(sc.get('pending_edits', {})))
    if not restored_pending:
        restored_pending = _build_pending_edits_from_results_snapshot(sc.get('results', {}))
    sess['pending_edits'] = restored_pending
    sess['value_aux_overrides'] = json.loads(json.dumps(sc.get('value_aux_overrides', {})))
    sess['undo_stack'] = []
    sess['redo_stack'] = []
    _rebuild_volume_caches_from_results(current_engine)

    # Restore valuation params and prod/purch split that were active when the scenario was saved
    # so any subsequent recalculations use the correct context.
    sc_vp = sc.get('valuation_params')
    restored_vp = None
    if sc_vp and getattr(current_engine, 'data', None) is not None:
        current_engine.data.valuation_params = _valuation_params_from_config(sc_vp)
        _global_config['valuation_params'] = {str(k): float(v) for k, v in sc_vp.items()}
        restored_vp = sc_vp

    sc_pap = sc.get('purchased_and_produced')
    if sc_pap is not None and getattr(current_engine, 'data', None) is not None:
        pap_dict = _parse_purchased_and_produced(sc_pap) if isinstance(sc_pap, str) else dict(sc_pap)
        current_engine.data.purchased_and_produced = pap_dict
        _global_config['purchased_and_produced'] = _format_purchased_and_produced(pap_dict)

    results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.results.items()}
    value_results_dict = {lt: [r.to_dict() for r in rs] for lt, rs in current_engine.value_results.items()}
    consolidation = [r.to_dict() for r in current_engine.value_results.get(LineType.CONSOLIDATION.value, [])]

    # Include session's pre-edit baseline so the frontend can show cascade highlights.
    baseline = sess.get('reset_baseline')
    baseline_results = None
    baseline_value_results = None
    if baseline:
        if baseline.get('results'):
            baseline_results = {
                lt: [{'material_number': r.get('material_number', ''),
                      'aux_column': r.get('aux_column', ''),
                      'values': r.get('values', {})} for r in rows]
                for lt, rows in baseline['results'].items()
            }
        if baseline.get('value_results'):
            baseline_value_results = {
                lt: [{'material_number': r.get('material_number', ''),
                      'aux_column': r.get('aux_column', ''),
                      'values': r.get('values', {})} for r in rows]
                for lt, rows in baseline['value_results'].items()
            }

    resp = {
        'success':       True,
        'scenario_id':   scenario_id,
        'name':          sc['name'],
        'results':       results_dict,
        'value_results': value_results_dict,
        'consolidation': consolidation,
        'pending_edits': sess.get('pending_edits', {}),
        'value_aux_overrides': sess.get('value_aux_overrides', {}),
    }
    if restored_vp is not None:
        resp['restored_valuation_params'] = restored_vp
    if baseline_results:
        resp['baseline_results'] = baseline_results
    if baseline_value_results:
        resp['baseline_value_results'] = baseline_value_results
    return jsonify(resp)


@app.route('/api/scenarios/<scenario_id>', methods=['DELETE'])
def delete_scenario(scenario_id):
    if scenario_id not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404
    if scenarios[scenario_id]['session_id'] != active_session_id:
        return jsonify({'error': 'Scenario belongs to a different session'}), 403
    del scenarios[scenario_id]
    return jsonify({'success': True})


@app.route('/api/scenarios/compare', methods=['POST'])
def compare_scenarios():
    req = request.get_json() or {}
    id_a = req.get('scenario_a_id', '')
    id_b = req.get('scenario_b_id', '')
    if id_a not in scenarios or id_b not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404
    sc_a = scenarios[id_a]
    sc_b = scenarios[id_b]
    if sc_a['session_id'] != active_session_id or sc_b['session_id'] != active_session_id:
        return jsonify({'error': 'Scenarios belong to a different session'}), 403

    diff_rows = []
    for lt, rows_a in sc_a['results'].items():
        rows_b_map = defaultdict(list)
        for r in sc_b['results'].get(lt, []):
            rows_b_map[_row_key_from_obj(r)].append(r)
        for row_a in rows_a:
            mat = row_a['material_number']
            key = _row_key_from_obj(row_a)
            bucket = rows_b_map.get(key, [])
            row_b = bucket.pop(0) if bucket else None
            if not row_b:
                continue
            diff = {p: round(row_a['values'].get(p, 0) - row_b['values'].get(p, 0), 4)
                    for p in row_a['values']}
            if any(abs(v) > 0.01 for v in diff.values()):
                diff_rows.append({
                    'material_number': mat,
                    'line_type': lt,
                    'values_a': row_a['values'],
                    'values_b': row_b['values'],
                    'diff': diff,
                })

    def _sum_diff(lt_key):
        rows = [r for r in diff_rows if r['line_type'] == lt_key]
        first = sc_a['results'].get(lt_key, [])
        periods = list(first[0].get('values', {}).keys()) if first else []
        return {p: round(sum(r['diff'].get(p, 0) for r in rows), 2) for p in periods}

    summary = {
        'scenario_a_name': sc_a['name'],
        'scenario_b_name': sc_b['name'],
        'total_demand_diff': _sum_diff('03. Total demand'),
        'inventory_diff':    _sum_diff('04. Inventory'),
        'changed_rows':      len(diff_rows),
    }
    return jsonify({'summary': summary, 'rows': diff_rows})


@app.route('/api/scenarios/compare/export')
def export_scenario_comparison():
    id_a = request.args.get('a', '')
    id_b = request.args.get('b', '')
    if id_a not in scenarios or id_b not in scenarios:
        return jsonify({'error': 'Scenario not found'}), 404
    sc_a = scenarios[id_a]
    sc_b = scenarios[id_b]
    if sc_a['session_id'] != active_session_id or sc_b['session_id'] != active_session_id:
        return jsonify({'error': 'Scenarios belong to a different session'}), 403

    # â”€â”€ Reuse compare logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _build_diff_rows(res_a, res_b):
        rows = []
        for lt, rows_a in res_a.items():
            rows_b_map = defaultdict(list)
            for r in res_b.get(lt, []):
                rows_b_map[_row_key_from_obj(r)].append(r)
            for row_a in rows_a:
                mat = row_a['material_number']
                key = _row_key_from_obj(row_a)
                bucket = rows_b_map.get(key, [])
                row_b = bucket.pop(0) if bucket else None
                if not row_b:
                    continue
                diff = {p: round(row_a['values'].get(p, 0) - row_b['values'].get(p, 0), 4)
                        for p in row_a['values']}
                if any(abs(v) > 0.01 for v in diff.values()):
                    rows.append({
                        'material_number': mat,
                        'material_name':   row_a.get('material_name', ''),
                        'line_type':       lt,
                        'values_a':        row_a['values'],
                        'values_b':        row_b['values'],
                        'diff':            diff,
                    })
        return rows

    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter

    GREEN_FILL = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
    RED_FILL   = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
    GREY_FILL  = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
    HDR_FILL   = PatternFill(start_color='1F3864', end_color='1F3864', fill_type='solid')
    ROW_A_FILL = PatternFill(start_color='EBF3FB', end_color='EBF3FB', fill_type='solid')
    ROW_B_FILL = PatternFill(start_color='FEF9EE', end_color='FEF9EE', fill_type='solid')
    HDR_FONT   = Font(bold=True, color='FFFFFF')
    BOLD_FONT  = Font(bold=True)

    def _write_sheet(ws, diff_rows):
        if not diff_rows:
            ws.append(['No differences found.'])
            return
        periods = list(diff_rows[0]['values_a'].keys())
        # Header row
        headers = ['Material Number', 'Material Name', 'Line Type', 'Row'] + periods + ['Total Diff']
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.alignment = Alignment(horizontal='center')

        for dr in diff_rows:
            mat  = dr['material_number']
            name = dr['material_name']
            lt   = dr['line_type']
            va   = dr['values_a']
            vb   = dr['values_b']
            dv   = dr['diff']
            # Row A (Scenario A values)
            row_a_data = [mat, name, lt, sc_a['name']] + [va.get(p, 0) for p in periods] + [round(sum(va.get(p,0) for p in periods), 2)]
            ws.append(row_a_data)
            for cell in ws[ws.max_row]:
                cell.fill = ROW_A_FILL
            # Row B (Scenario B values)
            row_b_data = [mat, name, lt, sc_b['name']] + [vb.get(p, 0) for p in periods] + [round(sum(vb.get(p,0) for p in periods), 2)]
            ws.append(row_b_data)
            for cell in ws[ws.max_row]:
                cell.fill = ROW_B_FILL
            # Diff row
            total_diff = round(sum(dv.get(p, 0) for p in periods), 2)
            diff_data  = [mat, name, lt, 'Diff (Aâˆ’B)'] + [dv.get(p, 0) for p in periods] + [total_diff]
            ws.append(diff_data)
            diff_excel_row = ws.max_row
            period_start_col = 5  # columns 1-4 are fixed metadata
            for col_idx, p in enumerate(periods, start=period_start_col):
                cell = ws.cell(row=diff_excel_row, column=col_idx)
                cell.font = BOLD_FONT
                v = dv.get(p, 0)
                if v > 0.01:
                    cell.fill = GREEN_FILL
                elif v < -0.01:
                    cell.fill = RED_FILL
            # Total diff cell
            total_cell = ws.cell(row=diff_excel_row, column=len(headers))
            total_cell.font = BOLD_FONT
            if total_diff > 0.01:
                total_cell.fill = GREEN_FILL
            elif total_diff < -0.01:
                total_cell.fill = RED_FILL
            # Grey separator row
            ws.append([''] * len(headers))
            for cell in ws[ws.max_row]:
                cell.fill = GREY_FILL

        # Auto-width for first 4 columns
        for col_idx in range(1, 5):
            max_len = max((len(str(ws.cell(r, col_idx).value or '')) for r in range(1, ws.max_row + 1)), default=10)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 40)

    wb = openpyxl.Workbook()
    # Volume sheet
    ws_vol = wb.active
    ws_vol.title = 'Volume Comparison'
    vol_diff = _build_diff_rows(sc_a['results'], sc_b['results'])
    _write_sheet(ws_vol, vol_diff)

    # Value sheet
    ws_val = wb.create_sheet('Value Comparison')
    val_diff = _build_diff_rows(sc_a.get('value_results', {}), sc_b.get('value_results', {}))
    _write_sheet(ws_val, val_diff)

    export_dir = APP_EXPORTS_DIR
    export_dir.mkdir(exist_ok=True)
    safe_a = ''.join(c for c in sc_a['name'] if c.isalnum() or c in ' _-')[:30]
    safe_b = ''.join(c for c in sc_b['name'] if c.isalnum() or c in ' _-')[:30]
    filename = f'Comparison_{safe_a}_vs_{safe_b}.xlsx'
    export_path = export_dir / filename
    wb.save(str(export_path))
    return send_file(str(export_path), as_attachment=True, download_name=filename)


# ---- Session management endpoints ----

@app.route('/api/sessions/snapshot', methods=['POST'])
def snapshot_session():
    """Duplicate the active session (including all edits) as a new named instance."""
    global sessions, active_session_id
    req = request.get_json() or {}
    name = req.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name cannot be empty'}), 400

    sess, _ = _get_active()
    if sess is None:
        return jsonify({'error': 'No active session'}), 400

    new_id = str(_uuid.uuid4())
    try:
        engine_copy = copy.deepcopy(sess.get('engine')) if sess.get('engine') is not None else None
    except Exception:
        engine_copy = None
    new_sess = {
        'id':                 new_id,
        'file_path':          sess.get('file_path', ''),
        'extract_files':      sess.get('extract_files'),
        'filename':           sess.get('filename', ''),
        'custom_name':        name,
        'is_snapshot':        True,
        'engine':             engine_copy,
        'value_results':      copy.deepcopy(sess.get('value_results', {})),
        'metadata':           copy.deepcopy(sess.get('metadata', {})),
        'uploaded_at':        datetime.now().isoformat(),
        'parameters':         copy.deepcopy(sess.get('parameters')),
        'pending_edits':      copy.deepcopy(sess.get('pending_edits', {})),
        'value_aux_overrides': copy.deepcopy(sess.get('value_aux_overrides', {})),
        'machine_overrides':  _machine_overrides_from_engine(sess, sess.get('engine')) if sess.get('engine') is not None else copy.deepcopy(sess.get('machine_overrides', {})),
        'reset_baseline':     copy.deepcopy(sess.get('reset_baseline')),
        'undo_stack':         [],
        'redo_stack':         [],
    }
    sessions[new_id] = new_sess
    _save_sessions_to_disk()

    meta = new_sess.get('metadata', {})
    site = meta.get('site', 'Unknown')
    pm   = str(meta.get('planning_month', '')) or 'Unknown'
    return jsonify({
        'success': True,
        'session': {
            'id':             new_id,
            'filename':       new_sess['filename'],
            'custom_name':    name,
            'site':           site,
            'planning_month': pm,
            'uploaded_at':    new_sess['uploaded_at'],
            'calculated':     engine_copy is not None,
            'is_snapshot':    True,
            'active':         False,
            'metadata':       meta,
        }
    })


@app.route('/api/sessions')
def list_sessions():
    """Return all sessions grouped by year/month/site."""
    grouped: dict = {}
    for sid, sess in sessions.items():
        meta = sess.get('metadata', {})
        site = meta.get('site', 'Unknown')
        pm   = str(meta.get('planning_month', '')) or 'Unknown'
        year = pm[:4] if len(pm) >= 4 else 'Unknown'
        month = pm[5:7] if len(pm) >= 7 else 'Unknown'
        key = f"{year}/{month}/{site}"
        grouped.setdefault(key, [])
        grouped[key].append({
            'id':             sid,
            'filename':       sess.get('filename', ''),
            'custom_name':    sess.get('custom_name'),
            'site':           site,
            'planning_month': pm,
            'uploaded_at':    sess.get('uploaded_at', ''),
            'calculated':     sess.get('engine') is not None,
            'is_snapshot':    sess.get('is_snapshot', False),
            'active':         sid == active_session_id,
            'metadata':       meta,
        })
    return jsonify({'active_session_id': active_session_id, 'groups': grouped})


@app.route('/api/sessions/rename', methods=['POST'])
def rename_session():
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    new_name = data.get('name', '').strip()
    if not session_id or session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    if not new_name:
        return jsonify({'error': 'Name cannot be empty'}), 400
    sessions[session_id]['custom_name'] = new_name
    _save_sessions_to_disk()
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


@app.route('/api/sessions/switch', methods=['POST'])
def switch_session():
    """Set a different session as active. Returns session metadata."""
    global active_session_id
    req = request.get_json() or {}
    sid = req.get('session_id')
    if not sid or sid not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    sess = sessions[sid]
    # Sync _global_config from the target session's engine BEFORE any rebuilds so
    # that _build_clean_engine_for_session picks up the correct per-session PAP and
    # VP params instead of the previous session's values from _global_config.
    if sess.get('engine') is not None:
        _sync_global_config_from_engine(sess.get('engine'))
    if sess.get('engine') is None and sess.get('parameters') is not None:
        try:
            params = sess['parameters']
            with contextlib.redirect_stdout(io.StringIO()):
                engine = _build_clean_engine_for_session(sess, params)
                _install_clean_engine_baseline(sess, engine, clear_machine_overrides=False)
                with app.app_context():
                    _replay_pending_edits(sess, engine)
            sess['engine'] = engine
        except Exception as exc:
            return jsonify({'error': f'Could not restore calculations for this session: {exc}'}), 500
    if sess.get('engine') is not None and (
        sess.get('reset_baseline') is None or _snapshot_has_manual_edits(sess.get('reset_baseline'))
    ):
        try:
            clean_engine = _build_clean_engine_for_session(sess)
            if clean_engine is not None:
                _install_clean_engine_baseline(sess, clean_engine, clear_machine_overrides=False)
            elif not _engine_has_manual_edits(sess['engine']):
                _install_clean_engine_baseline(sess, sess['engine'], clear_machine_overrides=False)
        except Exception:
            pass
    # Set active session only after all setup succeeds â€” avoids leaving a broken
    # session active if the engine rebuild above raised an exception and returned early.
    active_session_id = sid
    _sync_global_config_from_engine(sess.get('engine'))

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
        'valuation_params': _global_config.get('valuation_params', {}),
        'purchased_and_produced': _global_config.get('purchased_and_produced', ''),
    })


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """Remove a session. Activates the next available session if deleted was active."""
    global active_session_id, sessions
    if session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    del sessions[session_id]
    if active_session_id == session_id:
        active_session_id = next(iter(sessions), None)
    _save_sessions_to_disk()
    return jsonify({'success': True, 'active_session_id': active_session_id})


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

