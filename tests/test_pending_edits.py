import pytest

from ui.pending_edits import canonical_pending_edit_key, pending_edit_key


pytestmark = pytest.mark.no_fixture


def test_pending_edit_key_joins_four_parts():
    key = pending_edit_key("01. Demand forecast", "MAT-1", "Aux", "2025-12")

    assert key == "01. Demand forecast||MAT-1||Aux||2025-12"


def test_pending_edit_key_strips_aux_column_whitespace():
    key = pending_edit_key("01. Demand forecast", "MAT-1", "  Aux  ", "2025-12")

    assert key == "01. Demand forecast||MAT-1||Aux||2025-12"


def test_pending_edit_key_empty_aux_column():
    key = pending_edit_key("01. Demand forecast", "MAT-1", None, "2025-12")

    assert key == "01. Demand forecast||MAT-1||||2025-12"


def test_canonical_pending_edit_key_normalizes_whitespace():
    drifted = "01. Demand forecast||MAT-1|| Aux ||2025-12"

    assert canonical_pending_edit_key(drifted) == "01. Demand forecast||MAT-1||Aux||2025-12"


def test_canonical_pending_edit_key_malformed_returns_stripped_input():
    assert canonical_pending_edit_key("not||a||valid") == "not||a||valid"
    assert canonical_pending_edit_key("") == ""
    assert canonical_pending_edit_key(None) == ""
