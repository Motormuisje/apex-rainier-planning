"""Configuration-related Flask routes."""

from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, request


def create_config_folders_blueprint(
    default_folders: Callable[[], dict],
    global_config: dict,
    save_global_config: Callable[[], None],
    apply_folder_paths: Callable[[Path, Path, Path], None],
) -> Blueprint:
    bp = Blueprint('config_folders', __name__)

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

    return bp
