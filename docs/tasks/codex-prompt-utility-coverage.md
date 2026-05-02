# Codex prompt — Unit tests for errors.py, serializers.py, session_store.py

## Goal

Three utility modules are under-tested. All are pure Python — no Flask app
context, no golden fixture, no `PlanningEngine` run required. Every test in
this sprint must be marked `pytestmark = pytest.mark.no_fixture`.

Current coverage:
- `ui/errors.py` — 7% (26/28 lines uncovered)
- `ui/serializers.py` — 49% (19/37 lines uncovered)
- `ui/session_store.py` — 39% (28/46 lines uncovered)

Target: each file at ≥ 90% after this sprint.

## Read these files before writing any code

- `ui/errors.py` (35 lines)
- `ui/serializers.py` (72 lines)
- `ui/session_store.py` (115 lines)

Do not read any other file. Do not change any production code.

---

## File 1 — tests/test_errors.py (new file)

```python
pytestmark = pytest.mark.no_fixture
```

Import: `from ui.errors import classify_upload_exception`

Write one test per exception branch. Each test calls
`classify_upload_exception(exc, stage)` and asserts:
- The returned `kind` string
- The returned `stage` equals what was passed in
- The returned `exception` equals `type(exc).__name__`
- The `error` string contains a meaningful substring

| Test name | Exception | Expected `kind` |
|---|---|---|
| `test_classify_bad_zip_file` | `zipfile.BadZipFile()` | `"bad_zip"` |
| `test_classify_file_not_found` | `FileNotFoundError("missing.xlsm")` | `"not_found"` |
| `test_classify_permission_error` | `PermissionError("denied")` | `"permission"` |
| `test_classify_disk_full` | `OSError("No space left on device")` | `"disk_full"` |
| `test_classify_memory_error` | `MemoryError()` | `"memory"` |
| `test_classify_key_error` | `KeyError("Sheet1")` | `"missing_key"` |
| `test_classify_value_error` | `ValueError("bad data")` | `"value_error"` |
| `test_classify_unknown_error` | `RuntimeError("unexpected")` | `"unknown"` |

All tests pass `stage="upload"` (or any consistent string).

---

## File 2 — tests/test_serializers.py (new file)

```python
pytestmark = pytest.mark.no_fixture
```

Import: `from ui.serializers import json_safe, moq_warnings_payload, value_results_payload, planning_value_payload, row_payload`

### `json_safe` tests

Write one test per type branch:

| Test name | Input | Expected output |
|---|---|---|
| `test_json_safe_none` | `None` | `None` |
| `test_json_safe_string` | `"hello"` | `"hello"` |
| `test_json_safe_bool` | `True` | `True` |
| `test_json_safe_int` | `42` | `42` |
| `test_json_safe_float` | `3.14` | `3.14` (approx) |
| `test_json_safe_nan_returns_none` | `float('nan')` | `None` |
| `test_json_safe_inf_returns_none` | `float('inf')` | `None` |
| `test_json_safe_neg_inf_returns_none` | `float('-inf')` | `None` |
| `test_json_safe_datetime` | `datetime(2025, 12, 1, 9, 0)` | `"2025-12-01T09:00:00"` |
| `test_json_safe_dict` | `{"a": 1, "b": float('nan')}` | `{"a": 1, "b": None}` |
| `test_json_safe_list` | `[1, float('nan'), "x"]` | `[1, None, "x"]` |
| `test_json_safe_tuple` | `(1, 2)` | `[1, 2]` |
| `test_json_safe_set` | `{42}` | `[42]` |
| `test_json_safe_enum` | An `Enum` whose `.value` is `"demand"` | `"demand"` |
| `test_json_safe_numpy_like_item_callable` | An object with `.item()` returning `7` | `7` |
| `test_json_safe_unknown_object_falls_back_to_str` | An object with `__str__` returning `"custom"` and no `.item` | `"custom"` |

For the Enum test: define a throwaway enum inline in the test:
```python
from enum import Enum
class _E(Enum):
    X = "demand"
assert json_safe(_E.X) == "demand"
```

For the numpy-like test: use a `SimpleNamespace` or a small class with `.item`
as a callable returning an int — no numpy import needed:
```python
class _NumpyLike:
    def item(self):
        return 7
assert json_safe(_NumpyLike()) == 7
```

For unknown object fallback: an object whose `.item` is not callable (or
absent) and `__str__` returns a predictable value:
```python
class _Unknown:
    def __str__(self):
        return "custom"
assert json_safe(_Unknown()) == "custom"
```

### Payload builder tests

```
test_moq_warnings_payload_returns_purch_raw_needs
    engine = SimpleNamespace(all_purch_raw_needs={"MAT-1": {"2025-12": 5.0}})
    result = moq_warnings_payload(engine)
    assert result == {"moq_raw_needs": {"MAT-1": {"2025-12": 5.0}}}

test_moq_warnings_payload_engine_without_attribute
    engine = SimpleNamespace()   # no all_purch_raw_needs attr
    result = moq_warnings_payload(engine)
    assert result == {"moq_raw_needs": {}}

test_value_results_payload_builds_dict_and_consolidation
    # Use a real PlanningRow so .to_dict() works
    from modules.models import LineType, PlanningRow
    row = PlanningRow(
        material_number="MAT-1", material_name="M", product_type="Bulk Product",
        product_family="F", spc_product="S", product_cluster="C",
        product_name="P", line_type=LineType.CONSOLIDATION.value,
        values={"2025-12": 100.0},
    )
    engine = SimpleNamespace(
        value_results={LineType.CONSOLIDATION.value: [row]},
    )
    result = value_results_payload(engine)
    assert LineType.CONSOLIDATION.value in result["value_results"]
    assert len(result["consolidation"]) == 1
    assert result["consolidation"][0]["material_number"] == "MAT-1"

test_planning_value_payload_combines_all_parts
    from modules.models import LineType, PlanningRow
    row = PlanningRow(
        material_number="MAT-1", material_name="M", product_type="Bulk Product",
        product_family="F", spc_product="S", product_cluster="C",
        product_name="P", line_type=LineType.DEMAND_FORECAST.value,
        values={"2025-12": 10.0},
    )
    engine = SimpleNamespace(
        results={LineType.DEMAND_FORECAST.value: [row]},
        value_results={},
        data=SimpleNamespace(periods=["2025-12"]),
        all_purch_raw_needs={},
    )
    result = planning_value_payload(engine)
    assert result["periods"] == ["2025-12"]
    assert LineType.DEMAND_FORECAST.value in result["results"]
    assert "value_results" in result
    assert "moq_raw_needs" in result

test_row_payload_returns_json_safe_dict
    from modules.models import LineType, PlanningRow
    row = PlanningRow(
        material_number="MAT-1", material_name="M", product_type="Bulk Product",
        product_family="F", spc_product="S", product_cluster="C",
        product_name="P", line_type=LineType.DEMAND_FORECAST.value,
        values={"2025-12": float('nan')},
    )
    result = row_payload(row)
    # nan in values should be sanitised to None by json_safe
    assert result["values"]["2025-12"] is None
```

---

## File 3 — tests/test_session_store.py (new file)

```python
pytestmark = pytest.mark.no_fixture
```

Import: `from ui.session_store import save_sessions_to_disk, load_sessions_from_disk`

All tests receive `tmp_path` from pytest (built-in fixture — does not require
golden fixture).

### `save_sessions_to_disk` tests

```
test_save_creates_json_file_at_sessions_store_path(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    sessions = {
        "s1": {
            "id": "s1", "file_path": "/f.xlsm", "filename": "f.xlsm",
            "custom_name": "Test", "is_snapshot": False, "metadata": {},
            "uploaded_at": "2026-04-21T00:00:00", "parameters": None,
            "pending_edits": {}, "value_aux_overrides": {},
            "machine_overrides": {}, "extract_files": None,
            "reset_baseline": None, "valuation_params": None,
            "engine": None,
        }
    }
    save_sessions_to_disk(sessions, "s1", store_path, lambda sess, eng: {})
    assert store_path.exists()
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["active_session_id"] == "s1"
    assert "s1" in data["sessions"]

test_save_extracts_valuation_params_from_engine(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    # Build a mock valuation_params object with the eight expected attributes
    vp = SimpleNamespace(
        direct_fte_cost_per_month=1.0,
        indirect_fte_cost_per_month=2.0,
        overhead_cost_per_month=3.0,
        sga_cost_per_month=4.0,
        depreciation_per_year=5.0,
        net_book_value=6.0,
        days_sales_outstanding=7.0,
        days_payable_outstanding=8.0,
    )
    engine = SimpleNamespace(data=SimpleNamespace(valuation_params=vp))
    sessions = {
        "s1": {
            "id": "s1", "file_path": "", "filename": "", "custom_name": None,
            "is_snapshot": False, "metadata": {}, "uploaded_at": "",
            "parameters": None, "pending_edits": {}, "value_aux_overrides": {},
            "machine_overrides": {}, "extract_files": None,
            "reset_baseline": None, "valuation_params": None,
            "engine": engine,
        }
    }
    save_sessions_to_disk(sessions, "s1", store_path, lambda sess, eng: {})
    data = json.loads(store_path.read_text(encoding="utf-8"))
    saved_vp = data["sessions"]["s1"]["valuation_params"]
    assert saved_vp["1"] == pytest.approx(1.0)
    assert saved_vp["8"] == pytest.approx(8.0)

test_save_falls_back_to_baseline_valuation_params_when_no_engine(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    sessions = {
        "s1": {
            "id": "s1", "file_path": "", "filename": "", "custom_name": None,
            "is_snapshot": False, "metadata": {}, "uploaded_at": "",
            "parameters": None, "pending_edits": {}, "value_aux_overrides": {},
            "machine_overrides": {}, "extract_files": None,
            "engine": None,
            "reset_baseline": {"valuation_params": {"1": 9.0}},
            "valuation_params": None,
        }
    }
    save_sessions_to_disk(sessions, "s1", store_path, lambda sess, eng: {})
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["sessions"]["s1"]["valuation_params"] == {"1": 9.0}

test_save_calls_machine_overrides_callback_when_engine_present(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    engine = SimpleNamespace(data=SimpleNamespace(valuation_params=None))
    callback_calls = []
    sessions = {
        "s1": {
            "id": "s1", "file_path": "", "filename": "", "custom_name": None,
            "is_snapshot": False, "metadata": {}, "uploaded_at": "",
            "parameters": None, "pending_edits": {}, "value_aux_overrides": {},
            "machine_overrides": {"M1": {}}, "extract_files": None,
            "engine": engine, "reset_baseline": None, "valuation_params": None,
        }
    }
    def mo_callback(sess, eng):
        callback_calls.append((sess, eng))
        return {"M1": {"oee": 0.9}}
    save_sessions_to_disk(sessions, "s1", store_path, mo_callback)
    assert len(callback_calls) == 1
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["sessions"]["s1"]["machine_overrides"] == {"M1": {"oee": 0.9}}

test_save_uses_stored_machine_overrides_when_no_engine(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    sessions = {
        "s1": {
            "id": "s1", "file_path": "", "filename": "", "custom_name": None,
            "is_snapshot": False, "metadata": {}, "uploaded_at": "",
            "parameters": None, "pending_edits": {}, "value_aux_overrides": {},
            "machine_overrides": {"M1": {"oee": 0.85}}, "extract_files": None,
            "engine": None, "reset_baseline": None, "valuation_params": None,
        }
    }
    callback_calls = []
    save_sessions_to_disk(sessions, "s1", store_path, lambda s, e: callback_calls.append(1) or {})
    # callback should NOT be called when engine is None
    assert callback_calls == []
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["sessions"]["s1"]["machine_overrides"] == {"M1": {"oee": 0.85}}
```

### `load_sessions_from_disk` tests

```
test_load_returns_empty_when_file_does_not_exist(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    sessions, active = load_sessions_from_disk(store_path)
    assert sessions == {}
    assert active is None

test_load_restores_all_session_fields(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    store = {
        "active_session_id": "s1",
        "sessions": {
            "s1": {
                "id": "s1", "file_path": "/f.xlsm", "filename": "f.xlsm",
                "custom_name": "My Session", "is_snapshot": False,
                "extract_files": None,
                "metadata": {"planning_month": "2025-12"},
                "uploaded_at": "2026-04-21T00:00:00",
                "parameters": {"planning_month": "2025-12"},
                "pending_edits": {"key": {"original": 1.0, "new_value": 2.0}},
                "value_aux_overrides": {"vk": {"original": 1.0, "new_value": 3.0}},
                "machine_overrides": {"M1": {"oee": 0.9}},
                "valuation_params": {"1": 5.0},
            }
        },
    }
    store_path.write_text(json.dumps(store), encoding="utf-8")
    sessions, active = load_sessions_from_disk(store_path)
    assert active == "s1"
    assert "s1" in sessions
    sess = sessions["s1"]
    assert sess["engine"] is None
    assert sess["undo_stack"] == []
    assert sess["redo_stack"] == []
    assert sess["custom_name"] == "My Session"
    assert sess["pending_edits"] == {"key": {"original": 1.0, "new_value": 2.0}}
    assert sess["valuation_params"] == {"1": 5.0}

test_load_falls_back_to_first_session_when_saved_active_missing(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    store = {
        "active_session_id": "nonexistent",
        "sessions": {
            "s1": {
                "id": "s1", "file_path": "", "filename": "", "custom_name": None,
                "is_snapshot": False, "extract_files": None, "metadata": {},
                "uploaded_at": "", "parameters": None, "pending_edits": {},
                "value_aux_overrides": {}, "machine_overrides": {},
                "valuation_params": None,
            }
        },
    }
    store_path.write_text(json.dumps(store), encoding="utf-8")
    sessions, active = load_sessions_from_disk(store_path)
    assert active == "s1"

test_load_returns_none_active_when_sessions_empty(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    store = {"active_session_id": None, "sessions": {}}
    store_path.write_text(json.dumps(store), encoding="utf-8")
    sessions, active = load_sessions_from_disk(store_path)
    assert sessions == {}
    assert active is None

test_load_corrupt_file_returns_empty_and_renames(tmp_path)
    store_path = tmp_path / "sessions_store.json"
    store_path.write_text("not valid json", encoding="utf-8")
    sessions, active = load_sessions_from_disk(store_path)
    assert sessions == {}
    assert active is None
    # Original file should be gone (renamed to .corrupt-*)
    assert not store_path.exists()
    corrupt_files = list(tmp_path.glob("sessions_store.json.corrupt-*"))
    assert len(corrupt_files) == 1
```

---

## Verification

```powershell
python -m py_compile tests/test_errors.py tests/test_serializers.py tests/test_session_store.py
pytest tests/test_errors.py tests/test_serializers.py tests/test_session_store.py -v
pytest -v --ignore=tests/browser
```

All previously passing tests must still pass. New tests must all pass.

Coverage targets:
- `ui/errors.py` ≥ 90%
- `ui/serializers.py` ≥ 90%
- `ui/session_store.py` ≥ 90%

## Commit

Branch: `test/utility-coverage` from main.

```
test: add unit tests for errors, serializers, and session_store utilities
```

One commit, three new files.

## Stop conditions

- Any previously passing test fails → stop, report.
- Any import error → stop, report.
- Coverage of any of the three files stays below 85% → stop and report which
  lines remain uncovered and why.
