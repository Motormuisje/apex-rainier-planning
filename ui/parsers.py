"""Small parsing and formatting helpers for UI request/config values."""


def parse_purchased_and_produced(value) -> dict:
    pap = {}
    for entry in str(value or '').split(','):
        parts = entry.strip().split(':')
        if len(parts) != 2:
            continue
        mat = parts[0].strip()
        if not mat:
            continue
        try:
            pap[mat] = float(parts[1].strip())
        except ValueError:
            pass
    return pap


def format_purchased_and_produced(value: dict) -> str:
    return ', '.join(f'{mat}:{frac}' for mat, frac in sorted((value or {}).items()))
