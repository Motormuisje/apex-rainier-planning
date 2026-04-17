"""Configuration-related Flask routes."""

from datetime import datetime
import io
from pathlib import Path
import sys
from typing import Callable

from flask import Blueprint, current_app, jsonify, request

from modules.data_loader import DataLoader


def create_config_blueprint(
    default_folders: Callable[[], dict],
    global_config: dict,
    save_global_config: Callable[[], None],
    apply_folder_paths: Callable[[Path, Path, Path], None],
    get_upload_dir: Callable[[], Path],
    get_active: Callable[[], tuple],
    parse_purchased_and_produced: Callable[[object], dict],
    valuation_params_from_config: Callable[[object], object],
    ensure_reset_baseline: Callable[[dict, object], None],
    recalc_pap_material: Callable[[object, str], None],
    finish_pap_recalc: Callable[[object], None],
    recalculate_value_results: Callable[[object, dict], None],
    build_clean_engine_for_session: Callable[[dict], object],
    install_clean_engine_baseline: Callable[..., None],
    replay_pending_edits: Callable[[dict, object], None],
    moq_warnings_payload: Callable[[object], dict],
    value_results_payload: Callable[[object], dict],
) -> Blueprint:
    bp = Blueprint('config', __name__)

    @bp.route('/api/config/folders', methods=['GET'])
    def get_folder_config():
        defs = default_folders()
        saved = global_config.get('folders', {})
        return jsonify({
            'uploads': saved.get('uploads') or defs['uploads'],
            'exports': saved.get('exports') or defs['exports'],
            'sessions': saved.get('sessions') or defs['sessions'],
            'defaults': defs,
        })

    @bp.route('/api/config/folders', methods=['POST'])
    def save_folder_config():
        data = request.get_json(force=True) or {}
        defs = default_folders()

        uploads = (data.get('uploads') or '').strip() or defs['uploads']
        exports = (data.get('exports') or '').strip() or defs['exports']
        sessions_dir = (data.get('sessions') or '').strip() or defs['sessions']

        errors = []
        for label, path in [('uploads', uploads), ('exports', exports), ('sessions', sessions_dir)]:
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                errors.append(f'{label}: {exc}')
        if errors:
            return jsonify({'success': False, 'errors': errors}), 400

        global_config.setdefault('folders', {})
        global_config['folders']['uploads'] = uploads
        global_config['folders']['exports'] = exports
        global_config['folders']['sessions'] = sessions_dir
        save_global_config()

        apply_folder_paths(Path(uploads), Path(exports), Path(sessions_dir))
        return jsonify({'success': True})

    @bp.route('/api/config', methods=['GET'])
    def get_global_config():
        fd = global_config.get('file_defaults', {})
        return jsonify({
            'master_filename': global_config.get('master_filename'),
            'master_uploaded_at': global_config.get('master_uploaded_at'),
            'master_file_exists': bool(
                global_config.get('master_file') and
                Path(global_config['master_file']).exists()
            ),
            'site': global_config.get('site', ''),
            'forecast_months': global_config.get('forecast_months', ''),
            'unlimited_machines': global_config.get('unlimited_machines', ''),
            'purchased_and_produced': global_config.get('purchased_and_produced', ''),
            'valuation_params': global_config.get('valuation_params', {}),
            'file_defaults': {
                'site': fd.get('site', ''),
                'forecast_months': fd.get('forecast_months', 12),
                'unlimited_machines': fd.get('unlimited_machines', ''),
                'purchased_and_produced': fd.get('purchased_and_produced', ''),
                'valuation_params': fd.get('valuation_params', {}),
            },
        })

    @bp.route('/api/config/master-file', methods=['POST'])
    def upload_master_file():
        upload_dir = get_upload_dir()
        upload_dir.mkdir(exist_ok=True)

        if 'master_file' not in request.files or request.files['master_file'].filename == '':
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['master_file']
        if not file.filename.lower().endswith(('.xlsm', '.xlsx')):
            return jsonify({'error': 'Only .xlsm or .xlsx files are accepted'}), 400

        dest = upload_dir / file.filename
        file.save(str(dest))

        try:
            devnull = io.StringIO()
            sys.stdout, saved_stdout = devnull, sys.stdout
            try:
                loader = DataLoader(excel_file=str(dest))
                loader.load_all()
            finally:
                sys.stdout = saved_stdout
        except Exception:
            return jsonify({'error': 'Could not read file. Check that it contains the required sheets.'}), 400

        global_config['master_file'] = str(dest)
        global_config['master_filename'] = file.filename
        global_config['master_uploaded_at'] = datetime.now().isoformat()

        site = getattr(loader.config, 'site', '') if loader.config else ''
        forecast_months = getattr(loader.config, 'forecast_months', 12) if loader.config else 12
        unlimited = ','.join(getattr(loader.config, 'unlimited_capacity_machine', []))
        purchased_and_produced = loader.purchased_and_produced or {}
        purchased_and_produced_str = ', '.join(f'{k}:{v}' for k, v in purchased_and_produced.items())
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
        global_config['file_defaults'] = {
            'site': site,
            'forecast_months': forecast_months,
            'unlimited_machines': unlimited,
            'purchased_and_produced': purchased_and_produced_str,
            'valuation_params': vp_dict,
        }
        save_global_config()

        return jsonify({
            'success': True,
            'master_filename': file.filename,
            'master_uploaded_at': global_config['master_uploaded_at'],
            'file_defaults': global_config['file_defaults'],
            'summary': {
                'materials': len(loader.materials),
                'machines': len(loader.machines),
            }
        })

    @bp.route('/api/config/settings', methods=['POST'])
    def save_config_settings():
        data = request.get_json() or {}
        sess, current_engine = get_active()
        value_recalculated = False
        planning_recalculated = False
        old_config = {
            'site': str(global_config.get('site', '') or ''),
            'forecast_months': int(global_config.get('forecast_months', 0) or 0),
            'unlimited_machines': str(global_config.get('unlimited_machines', '') or ''),
        }

        if 'site' in data:
            global_config['site'] = data['site'].strip()
        if 'forecast_months' in data:
            global_config['forecast_months'] = int(data['forecast_months'] or 12)
        if 'unlimited_machines' in data:
            global_config['unlimited_machines'] = data['unlimited_machines'].strip()

        structural_config_changed = (
            str(global_config.get('site', '') or '') != old_config['site']
            or int(global_config.get('forecast_months', 0) or 0) != old_config['forecast_months']
            or str(global_config.get('unlimited_machines', '') or '') != old_config['unlimited_machines']
        )

        if 'purchased_and_produced' in data:
            global_config['purchased_and_produced'] = data['purchased_and_produced'].strip()
            if current_engine is not None and not structural_config_changed:
                old_pap = dict(getattr(current_engine.data, 'purchased_and_produced', {}) or {})
                new_pap = parse_purchased_and_produced(global_config['purchased_and_produced'])
                changed_mats = sorted({
                    mat for mat in set(old_pap) | set(new_pap)
                    if abs(float(old_pap.get(mat, -999999999)) - float(new_pap.get(mat, -999999999))) > 1e-9
                })
                current_engine.data.purchased_and_produced = new_pap
                if changed_mats:
                    ensure_reset_baseline(sess, current_engine)
                    for mat in changed_mats:
                        recalc_pap_material(current_engine, mat)
                    finish_pap_recalc(current_engine)
                    planning_recalculated = True
                else:
                    recalculate_value_results(current_engine, sess)
                value_recalculated = True
        if 'valuation_params' in data:
            global_config['valuation_params'] = {
                str(k): float(v) for k, v in data['valuation_params'].items() if v is not None
            }
            if current_engine is not None and not structural_config_changed and getattr(current_engine, 'data', None) is not None:
                current_engine.data.valuation_params = valuation_params_from_config(
                    global_config['valuation_params']
                )
                recalculate_value_results(current_engine, sess)
                value_recalculated = True

        if current_engine is not None and structural_config_changed:
            rebuilt = build_clean_engine_for_session(sess)
            if rebuilt is None:
                return jsonify({'error': 'Could not rebuild active session for the changed config. Recalculate this session first.'}), 400
            install_clean_engine_baseline(sess, rebuilt, clear_machine_overrides=False)
            with current_app.app_context():
                replay_pending_edits(sess, rebuilt)
            sess['engine'] = rebuilt
            current_engine = rebuilt
            planning_recalculated = True
            value_recalculated = True

        save_global_config()
        payload = {'success': True}
        if current_engine is not None and planning_recalculated:
            payload['periods'] = list(getattr(current_engine.data, 'periods', []) or [])
            payload['results'] = {
                lt: [row.to_dict() for row in rows]
                for lt, rows in (getattr(current_engine, 'results', {}) or {}).items()
            }
            payload.update(moq_warnings_payload(current_engine))
        if current_engine is not None and value_recalculated:
            payload.update(value_results_payload(current_engine))
        return jsonify(payload)

    @bp.route('/api/config/reset_vp_params', methods=['POST'])
    def reset_vp_params_to_defaults():
        sess, current_engine = get_active()
        if sess is None:
            return jsonify({'error': 'No active session'}), 400
        if current_engine is None:
            return jsonify({'error': 'No calculations run'}), 400

        baseline_vp = (sess.get('reset_baseline') or {}).get('valuation_params')
        if not baseline_vp:
            return jsonify({'error': 'No baseline available - run calculations first'}), 400

        current_engine.data.valuation_params = valuation_params_from_config(baseline_vp)
        global_config['valuation_params'] = {str(k): float(v) for k, v in baseline_vp.items()}
        recalculate_value_results(current_engine, sess)
        save_global_config()

        payload = {'success': True, 'valuation_params': baseline_vp}
        payload.update(value_results_payload(current_engine))
        return jsonify(payload)

    return bp
