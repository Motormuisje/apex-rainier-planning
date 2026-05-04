"""Top-level UI, upload, and calculation routes."""

import io
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, render_template, request

from modules.planning_engine import PlanningEngine, invalidate_data_cache


def create_workflow_blueprint(
    sessions: dict,
    set_active_session_id: Callable[[str], None],
    get_active: Callable[[], tuple],
    upload_dir: Callable[[], object],
    global_config: dict,
    classify_upload_exception: Callable[[Exception, str], dict],
    get_config_overrides: Callable[[], dict],
    cycle_manager: Callable[[], object],
    install_clean_engine_baseline: Callable[[dict, object], None],
    replay_pending_edits: Callable[[dict, object], None],
    save_sessions_to_disk: Callable[[], None],
    app_context: Callable[[], object],
) -> Blueprint:
    bp = Blueprint('workflow', __name__)

    @bp.route('/')
    def index():
        return render_template('index.html')

    @bp.route('/api/upload', methods=['POST'])
    def upload_file():
        out_upload_dir = upload_dir()
        out_upload_dir.mkdir(exist_ok=True)
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

        extract_keys = ['bom_file', 'routing_file', 'stock_file', 'forecast_file']
        is_multi = all(key in request.files for key in extract_keys)

        if is_multi:
            return _upload_multi_file(
                out_upload_dir,
                requested_name,
                requested_planning_month,
                requested_actuals,
                requested_forecast,
                sessions,
                set_active_session_id,
                global_config,
                classify_upload_exception,
                get_config_overrides,
                save_sessions_to_disk,
            )

        return _upload_single_file(
            out_upload_dir,
            requested_name,
            requested_planning_month,
            requested_actuals,
            requested_forecast,
            sessions,
            set_active_session_id,
            classify_upload_exception,
            save_sessions_to_disk,
        )

    @bp.route('/api/calculate', methods=['POST'])
    def run_calculations():
        sess, _ = get_active()
        if sess is None:
            return jsonify({'error': 'No file uploaded'}), 400

        log_buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = log_buf

        try:
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

            cm = cycle_manager()
            existing_engine = sess.get('engine')
            bootstrap_snapshot = existing_engine is None and not cm.has_previous_cycle()
            if existing_engine is not None:
                try:
                    previous_planning_month = (sess.get('parameters') or {}).get('planning_month')
                    cm.save_current_as_previous(
                        existing_engine.to_dataframe(),
                        planning_month=previous_planning_month,
                    )
                    print('[cycle_manager] pre-run: saved existing engine as previous cycle snapshot')
                except Exception as exc:
                    import traceback
                    print(f'[cycle_manager] pre-run snapshot ERROR (MoM will not work): {exc}\n{traceback.format_exc()}')

            engine = PlanningEngine(
                sess['file_path'],
                planning_month=planning_month,
                months_actuals=months_actuals,
                months_forecast=months_forecast,
                extract_files=sess.get('extract_files'),
                config_overrides=get_config_overrides(),
            )
            engine.run()
            install_clean_engine_baseline(sess, engine)
            with app_context():
                replay_pending_edits(sess, engine)
            sess['engine'] = engine
            sess['parameters'] = {
                'planning_month': planning_month,
                'months_actuals': months_actuals,
                'months_forecast': months_forecast,
            }
            if planning_month and sess.get('metadata') is not None:
                sess['metadata']['planning_month'] = planning_month

            if bootstrap_snapshot:
                try:
                    cm.save_current_as_previous(engine.to_dataframe(), planning_month=planning_month)
                    print('[cycle_manager] bootstrap: saved first-ever snapshot')
                except Exception as exc:
                    import traceback
                    print(f'[cycle_manager] bootstrap snapshot ERROR (MoM will not work): {exc}\n{traceback.format_exc()}')

            save_sessions_to_disk()

            sys.stdout = old_stdout
            return jsonify({
                'success': True,
                'summary': engine.get_summary(),
                'log': log_buf.getvalue(),
                'parameters': {
                    'planning_month': planning_month,
                    'months_actuals': months_actuals,
                    'months_forecast': months_forecast,
                }
            })
        except Exception as exc:
            sys.stdout = old_stdout
            import traceback
            return jsonify({'error': str(exc), 'trace': traceback.format_exc()}), 500

    return bp


def _session_payload(
    session_id,
    file_path,
    filename,
    requested_name,
    loader,
    planning_month,
    extract_files=None,
):
    payload = {
        'id': session_id,
        'file_path': str(file_path),
        'filename': filename,
        'custom_name': requested_name,
        'engine': None,
        'value_results': {},
        'metadata': {
            'materials': len(loader.materials),
            'bom_items': len(loader.bom),
            'machines': len(loader.machines),
            'periods': len(loader.periods),
            'site': getattr(loader.config, 'site', '') or '',
            'planning_month': planning_month,
        },
        'uploaded_at': datetime.now().isoformat(),
        'undo_stack': [],
        'redo_stack': [],
        'pending_edits': {},
        'value_aux_overrides': {},
        'machine_overrides': {},
    }
    if extract_files is not None:
        payload['extract_files'] = extract_files
    return payload


def _upload_multi_file(
    out_upload_dir,
    requested_name,
    requested_planning_month,
    requested_actuals,
    requested_forecast,
    sessions,
    set_active_session_id,
    global_config,
    classify_upload_exception,
    get_config_overrides,
    save_sessions_to_disk,
):
    if 'base_file' in request.files and request.files['base_file'].filename != '':
        base_file = request.files['base_file']
        base_file_path = out_upload_dir / base_file.filename
        try:
            base_file.save(str(base_file_path))
            invalidate_data_cache(str(base_file_path))
        except Exception as exc:
            return jsonify(classify_upload_exception(exc, 'opslaan base-file')), 400
    elif global_config.get('master_file') and Path(global_config['master_file']).exists():
        base_file_path = Path(global_config['master_file'])
    else:
        return jsonify({'error': 'No master data file configured. Upload a base file in the Config tab first.'}), 400

    filename_keywords = {
        'bom_file': ['bom'],
        'routing_file': ['routing'],
        'stock_file': ['stock'],
        'forecast_file': ['forecast'],
    }
    field_labels = {
        'bom_file': 'BOM',
        'routing_file': 'Routing',
        'stock_file': 'Stock',
        'forecast_file': 'Forecast',
    }
    key_map = {
        'bom_file': 'bom',
        'routing_file': 'routing',
        'stock_file': 'stock',
        'forecast_file': 'forecast',
    }
    saved_paths = {}
    for form_key, dict_key in key_map.items():
        upload = request.files[form_key]
        if upload.filename == '':
            return jsonify({'error': f'No file selected for {form_key}'}), 400
        fname_lower = upload.filename.lower()
        required_keywords = filename_keywords[form_key]
        if not any(keyword in fname_lower for keyword in required_keywords):
            label = field_labels[form_key]
            return jsonify({
                'error': (
                    f'Wrong file for {label} field: "{upload.filename}" does not look like a {label} file. '
                    f'Expected the filename to contain: {", ".join(required_keywords)}.'
                )
            }), 400
        file_path = out_upload_dir / upload.filename
        try:
            upload.save(str(file_path))
        except Exception as exc:
            return jsonify(classify_upload_exception(exc, f'opslaan {form_key}')), 400
        saved_paths[dict_key] = str(file_path)

    try:
        devnull = io.StringIO()
        sys.stdout, saved_stdout = devnull, sys.stdout
        try:
            from modules.data_loader import DataLoader
            loader = DataLoader(
                excel_file=str(base_file_path),
                extract_files=saved_paths,
                config_overrides=get_config_overrides(),
            )
            loader.load_all()
        finally:
            sys.stdout = saved_stdout
    except Exception as exc:
        import traceback
        print(f'[upload-multi] load error: {traceback.format_exc()}')
        return jsonify(classify_upload_exception(exc, 'inlezen extract-bestanden')), 400

    try:
        missing = _missing_required_loader_data(loader)
        if missing:
            return jsonify({
                'error': 'The uploaded file is missing required data. Please check that all expected sheets and columns are present.',
                'missing': missing,
            }), 400

        planning_month, months_actuals, months_forecast = _upload_planning_params(
            loader,
            requested_planning_month,
            requested_actuals,
            requested_forecast,
        )
        bom_filename = request.files['bom_file'].filename
        session_id = str(uuid.uuid4())
        sessions[session_id] = _session_payload(
            session_id,
            base_file_path,
            bom_filename,
            requested_name,
            loader,
            planning_month,
            extract_files=saved_paths,
        )
        set_active_session_id(session_id)
        save_sessions_to_disk()

        return jsonify({
            'success': True,
            'session_id': session_id,
            'filename': bom_filename,
            'custom_name': requested_name,
            'planning_month': planning_month,
            'months_actuals': months_actuals,
            'months_forecast': months_forecast,
            'summary': _upload_summary(loader),
        })
    except Exception as exc:
        import traceback
        print(f'[upload-multi] session error: {traceback.format_exc()}')
        return jsonify(classify_upload_exception(exc, 'sessie aanmaken (multi)')), 400


def _upload_single_file(
    out_upload_dir,
    requested_name,
    requested_planning_month,
    requested_actuals,
    requested_forecast,
    sessions,
    set_active_session_id,
    classify_upload_exception,
    save_sessions_to_disk,
):
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    upload = request.files['file']
    if upload.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    file_path = out_upload_dir / upload.filename
    try:
        upload.save(str(file_path))
        invalidate_data_cache(str(file_path))
    except Exception as exc:
        return jsonify(classify_upload_exception(exc, 'opslaan upload')), 400

    try:
        devnull = io.StringIO()
        sys.stdout, saved_stdout = devnull, sys.stdout
        try:
            from modules.data_loader import DataLoader
            loader = DataLoader(str(file_path))
            loader.load_all()
        finally:
            sys.stdout = saved_stdout
    except Exception as exc:
        import traceback
        print(f'[upload-single] load error: {traceback.format_exc()}')
        return jsonify(classify_upload_exception(exc, 'inlezen Excel')), 400

    try:
        missing = _missing_required_loader_data(loader)
        if missing:
            return jsonify({
                'error': 'The uploaded file is missing required data. Please check that all expected sheets and columns are present.',
                'missing': missing,
            }), 400

        planning_month, months_actuals, months_forecast = _upload_planning_params(
            loader,
            requested_planning_month,
            requested_actuals,
            requested_forecast,
        )
        session_id = str(uuid.uuid4())
        sessions[session_id] = _session_payload(
            session_id,
            file_path,
            upload.filename,
            requested_name,
            loader,
            planning_month,
        )
        set_active_session_id(session_id)
        save_sessions_to_disk()

        return jsonify({
            'success': True,
            'session_id': session_id,
            'filename': upload.filename,
            'custom_name': requested_name,
            'planning_month': planning_month,
            'months_actuals': months_actuals,
            'months_forecast': months_forecast,
            'summary': _upload_summary(loader),
        })
    except Exception as exc:
        import traceback
        print(f'[upload-single] session error: {traceback.format_exc()}')
        return jsonify(classify_upload_exception(exc, 'sessie aanmaken')), 400


def _missing_required_loader_data(loader):
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
    return missing


def _upload_planning_params(loader, requested_planning_month, requested_actuals, requested_forecast):
    initial_date = getattr(loader.config, 'initial_date', None)
    planning_month = requested_planning_month or (initial_date.strftime('%Y-%m') if initial_date else '')
    months_actuals = requested_actuals if requested_actuals is not None else getattr(loader, 'forecast_actuals_months', 12)
    months_forecast = requested_forecast if requested_forecast is not None else getattr(loader.config, 'forecast_months', 12)
    return planning_month, months_actuals, months_forecast


def _upload_summary(loader):
    return {
        'materials': len(loader.materials),
        'bom_items': len(loader.bom),
        'machines': len(loader.machines),
        'periods': len(loader.periods),
    }
