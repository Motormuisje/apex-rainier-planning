"""User-facing error payload helpers for the Flask UI."""

import zipfile


def classify_upload_exception(exc: Exception, stage: str) -> dict:
    """Map a raised exception during upload/load to a user-facing payload."""
    tname = type(exc).__name__
    raw = str(exc) or tname
    if isinstance(exc, zipfile.BadZipFile):
        msg = 'Het bestand is geen geldig Excel-bestand (corrupt of verkeerd formaat).'
        kind = 'bad_zip'
    elif isinstance(exc, FileNotFoundError):
        msg = f'Bestand niet gevonden tijdens {stage}: {raw}'
        kind = 'not_found'
    elif isinstance(exc, PermissionError):
        msg = f'Geen toegang tot bestand tijdens {stage}: {raw}'
        kind = 'permission'
    elif isinstance(exc, OSError) and 'No space' in raw:
        msg = 'Schijf vol - kan upload niet opslaan.'
        kind = 'disk_full'
    elif isinstance(exc, MemoryError):
        msg = f'Onvoldoende geheugen tijdens {stage}.'
        kind = 'memory'
    elif isinstance(exc, KeyError):
        msg = f'Ontbrekende sheet of kolom tijdens {stage}: {raw}'
        kind = 'missing_key'
    elif isinstance(exc, ValueError):
        msg = f'Ongeldige data tijdens {stage}: {raw}'
        kind = 'value_error'
    else:
        msg = f'Onverwachte fout tijdens {stage}: {tname}: {raw}'
        kind = 'unknown'
    return {'error': msg, 'error_kind': kind, 'stage': stage, 'exception': tname}
