import zipfile

import pytest

from ui.errors import classify_upload_exception


pytestmark = pytest.mark.no_fixture


@pytest.mark.parametrize(
    ("exc", "stage", "kind", "message_part"),
    [
        (zipfile.BadZipFile("bad zip"), "inlezen Excel", "bad_zip", "geen geldig Excel-bestand"),
        (FileNotFoundError("missing.xlsm"), "opslaan upload", "not_found", "Bestand niet gevonden"),
        (PermissionError("locked.xlsm"), "opslaan upload", "permission", "Geen toegang"),
        (OSError("No space left on device"), "opslaan upload", "disk_full", "Schijf vol"),
        (MemoryError("out"), "inlezen Excel", "memory", "Onvoldoende geheugen"),
        (KeyError("BOM"), "inlezen Excel", "missing_key", "Ontbrekende sheet"),
        (ValueError("bad number"), "inlezen Excel", "value_error", "Ongeldige data"),
        (RuntimeError("boom"), "sessie aanmaken", "unknown", "Onverwachte fout"),
    ],
)
def test_classify_upload_exception_returns_user_facing_payload(exc, stage, kind, message_part):
    payload = classify_upload_exception(exc, stage)

    assert payload["error_kind"] == kind
    assert payload["stage"] == stage
    assert payload["exception"] == type(exc).__name__
    assert message_part in payload["error"]
