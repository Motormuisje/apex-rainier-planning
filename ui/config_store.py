"""Persistence and folder resolution helpers for UI global config."""

import json
import logging
from pathlib import Path


def load_global_config(config_file: Path) -> dict:
    if not config_file.exists():
        return {}
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        logging.getLogger(__name__).error(f'global_config load error: {exc}')
        return {}


def save_global_config(config_file: Path, global_config: dict) -> None:
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(global_config, f, indent=2, default=str)
    except Exception as exc:
        print(f'[global_config] save error: {exc}')


def resolve_folder_paths(global_config: dict, default_folders: dict) -> tuple[Path, Path, Path]:
    folders = global_config.get('folders', {})
    uploads = folders.get('uploads') or default_folders['uploads']
    exports = folders.get('exports') or default_folders['exports']
    sessions = folders.get('sessions') or default_folders['sessions']
    return Path(uploads), Path(exports), Path(sessions)


def ensure_folder_paths(uploads_dir: Path, exports_dir: Path, sessions_dir: Path) -> None:
    uploads_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)


def apply_folder_config(global_config: dict, default_folders: dict) -> tuple[Path, Path, Path]:
    uploads_dir, exports_dir, sessions_dir = resolve_folder_paths(global_config, default_folders)
    ensure_folder_paths(uploads_dir, exports_dir, sessions_dir)
    return uploads_dir, exports_dir, sessions_dir / 'sessions_store.json'
