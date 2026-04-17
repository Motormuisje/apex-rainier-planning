"""
License Manager — Apex Rainier Planning Tool
Stores an encrypted trial activation record on first accept.
Trial period: 21 days from activation.
"""

import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


# --- Internal constants (keep these private in the obfuscated build) ---
_SALT = b'\x4a\x70\x52\x61\x69\x6e\x69\x65\x72\x41\x50\x45\x58\x32\x30\x32\x35'
_TRIAL_DAYS = 14
_LICENSE_FILENAME = 'lic.dat'


def _app_data_dir() -> Path:
    env = os.getenv('SOP_APP_DATA_DIR', '').strip()
    if env:
        return Path(env).expanduser()
    if os.name == 'nt':
        base = Path(os.getenv('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    else:
        base = Path(os.getenv('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return base / 'SOPPlanningEngine'


def _machine_id() -> str:
    """Best-effort stable machine fingerprint."""
    try:
        if platform.system() == 'Windows':
            out = subprocess.check_output(
                ['wmic', 'csproduct', 'get', 'uuid'],
                stderr=subprocess.DEVNULL, timeout=3
            ).decode('utf-8', errors='ignore')
            for line in out.splitlines():
                line = line.strip()
                if line and line.upper() != 'UUID':
                    return line
    except Exception:
        pass
    # Fallback: username + node
    return f"{platform.node()}:{os.getlogin() if hasattr(os, 'getlogin') else 'user'}"


def _derive_key(machine_id: str) -> bytes:
    return hashlib.pbkdf2_hmac(
        'sha256',
        machine_id.encode('utf-8'),
        _SALT,
        iterations=100_000,
        dklen=32,
    )


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _encrypt(payload: dict, key: bytes) -> str:
    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    checksum = hashlib.sha256(raw + key).hexdigest()[:16]
    blob = json.dumps({'d': raw.decode('utf-8'), 'c': checksum}).encode('utf-8')
    return base64.urlsafe_b64encode(_xor_bytes(blob, key)).decode('ascii')


def _decrypt(token: str, key: bytes) -> dict | None:
    try:
        raw_blob = _xor_bytes(base64.urlsafe_b64decode(token.encode('ascii')), key)
        wrapper = json.loads(raw_blob.decode('utf-8'))
        inner_raw = wrapper['d'].encode('utf-8')
        expected_checksum = hashlib.sha256(inner_raw + key).hexdigest()[:16]
        if wrapper['c'] != expected_checksum:
            return None
        return json.loads(inner_raw.decode('utf-8'))
    except Exception:
        return None


class LicenseStatus:
    OK = 'ok'
    NOT_ACTIVATED = 'not_activated'
    EXPIRED = 'expired'
    TAMPERED = 'tampered'


class LicenseManager:
    def __init__(self, data_dir: Path | None = None):
        self._dir = data_dir or _app_data_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / _LICENSE_FILENAME
        self._machine_id = _machine_id()
        self._key = _derive_key(self._machine_id)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def activate(self) -> bool:
        """Write a new activation record. Returns True on success."""
        if self._path.exists():
            # Already activated — do not reset clock
            status, _ = self.check()
            if status == LicenseStatus.OK:
                return True
            # If expired or tampered, allow re-write only if explicitly cleared
            # (for this deployment we simply return False to prevent clock reset)
            return False
        now = datetime.now(timezone.utc)
        payload = {
            'activated': now.isoformat(),
            'expires': (now + timedelta(days=_TRIAL_DAYS)).isoformat(),
            'mid': self._machine_id,
        }
        token = _encrypt(payload, self._key)
        self._path.write_text(token, encoding='utf-8')
        return True

    def check(self) -> tuple[str, dict | None]:
        """
        Returns (LicenseStatus, info_dict).
        info_dict keys: activated, expires, days_left (when status == OK)
        """
        if not self._path.exists():
            return LicenseStatus.NOT_ACTIVATED, None

        token = self._path.read_text(encoding='utf-8').strip()
        payload = _decrypt(token, self._key)

        if payload is None:
            return LicenseStatus.TAMPERED, None

        # Machine-ID check (soft — warns but still allows; remove to make hard)
        # if payload.get('mid') != self._machine_id:
        #     return LicenseStatus.TAMPERED, None

        try:
            expires_dt = datetime.fromisoformat(payload['expires'])
            activated_dt = datetime.fromisoformat(payload['activated'])
        except (KeyError, ValueError):
            return LicenseStatus.TAMPERED, None

        now = datetime.now(timezone.utc)
        if now > expires_dt:
            days_over = (now - expires_dt).days
            return LicenseStatus.EXPIRED, {
                'activated': activated_dt.strftime('%Y-%m-%d'),
                'expires': expires_dt.strftime('%Y-%m-%d'),
                'days_over': days_over,
            }

        days_left = (expires_dt - now).days
        return LicenseStatus.OK, {
            'activated': activated_dt.strftime('%Y-%m-%d'),
            'expires': expires_dt.strftime('%Y-%m-%d'),
            'days_left': days_left,
        }

    def days_left(self) -> int | None:
        """Returns days remaining, or None if not activated / expired."""
        status, info = self.check()
        if status == LicenseStatus.OK and info:
            return info.get('days_left')
        return None
