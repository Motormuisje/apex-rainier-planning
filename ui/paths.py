"""Filesystem paths for the Flask UI runtime."""

import os
import sys
from pathlib import Path


def resource_root() -> Path:
    """Return the directory that contains bundled app resources.

    In source mode this is the repository root.
    In a PyInstaller build this resolves to the extraction/bundle root.
    """
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def default_app_data_root() -> Path:
    """Return a user-writable data directory for runtime state."""
    env_override = os.getenv('SOP_APP_DATA_DIR', '').strip()
    if env_override:
        return Path(env_override).expanduser()

    if os.name == 'nt':
        base = Path(os.getenv('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    else:
        base = Path(os.getenv('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return base / 'SOPPlanningEngine'


def default_folders(app_data_root: Path) -> dict:
    return {
        'uploads': str(app_data_root / 'uploads'),
        'exports': str(app_data_root / 'exports'),
        'sessions': str(app_data_root),
    }
