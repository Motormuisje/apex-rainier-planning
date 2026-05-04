# Test Coverage Diff - 2026-04-22

## Purpose

This change raises the next layer of automated test coverage without changing
production code. It focuses on low-risk, high-signal areas identified by
`pytest --cov`: UI error helpers, serializers, session persistence, export
routes, workflow upload error handling, and the database export transformer.

No engine formulas, route contracts, session/config state fields, or UI
refactor moves were changed.

## Files Changed

### Added

- `tests/test_database_exporter.py`
  - Covers `modules.database_exporter.DatabaseExporter`.
  - Tests flat DB export row generation for included line types.
  - Verifies the special `04. Inventory` `Pre-month` row from starting stock.
  - Verifies deduplication keeps the last duplicate row.
  - Verifies empty output for empty input, non-exported line types, and no valid
    period columns.
  - Verifies period-column detection sorts valid `YYYY-MM` columns and ignores
    invalid labels.

- `tests/test_errors.py`
  - Covers `ui.errors.classify_upload_exception`.
  - Tests `BadZipFile`, `FileNotFoundError`, `PermissionError`, disk-full
    `OSError`, `MemoryError`, `KeyError`, `ValueError`, and fallback unknown
    exception mapping.

- `tests/test_serializers.py`
  - Covers `ui.serializers.json_safe`, `row_payload`,
    `moq_warnings_payload`, `value_results_payload`, and
    `planning_value_payload`.
  - Exercises nested structures, NaN/Inf normalization, datetimes, enums,
    scalar-like objects with `.item()`, fallback string conversion, and frontend
    payload shapes.

- `tests/test_session_store.py`
  - Covers `ui.session_store.save_sessions_to_disk` and
    `load_sessions_from_disk`.
  - Verifies engine objects are not serialized.
  - Verifies machine overrides are sourced from the injected callback when an
    engine exists.
  - Verifies valuation params are persisted from live engine data, or from the
    reset baseline when no engine exists.
  - Verifies active-session restore, fallback to first loaded session, missing
    store behavior, and corrupt JSON quarantine behavior.

### Updated

- `tests/test_routes_exports.py`
  - Replaced two skipped placeholder tests with real no-fixture route tests.
  - Added fake export engine and fake cycle manager.
  - Covers:
    - `/api/export` requires an engine.
    - `/api/export` writes a workbook, passes previous-cycle DataFrame through,
      and calls edit highlighting.
    - `/api/export_db` requires an engine.
    - `/api/export_db` returns 400 when `DatabaseExporter` produces no rows.
    - `/api/export_db` writes an `.xlsx` with sanitized filename.

- `tests/test_routes_workflow.py`
  - Added a small no-fixture workflow blueprint fixture.
  - Covers:
    - `/api/upload` with no file.
    - Single-file upload where loader output is missing required data.
    - Multi-file upload without a configured master file.

## Coverage Movement

Before this layer, total coverage was 63%.

After this layer:

- Total coverage: 65%
- `ui/errors.py`: 7% -> 100%
- `ui/serializers.py`: 49% -> 95%
- `ui/session_store.py`: 39% -> 93%
- `ui/routes/exports.py`: 29% -> 85%
- `modules/database_exporter.py`: 19% -> 91%

The final HTML report was regenerated at `htmlcov/index.html`.

## Verification Run

Commands run successfully:

```powershell
python -m py_compile tests\test_database_exporter.py tests\test_errors.py tests\test_routes_exports.py tests\test_routes_workflow.py tests\test_serializers.py tests\test_session_store.py
python -m pytest tests\test_database_exporter.py -v
python -m pytest tests\test_errors.py tests\test_serializers.py tests\test_session_store.py tests\test_routes_exports.py tests\test_routes_workflow.py -v -m no_fixture
python -m pytest --cov=ui --cov=modules --cov-report=term-missing --cov-report=html
python main.py --test
```

Final full coverage run:

```text
142 passed, 2 skipped
TOTAL 65%
```

`python main.py --test` also passed.

## Notes For Code Checking Agent

- This is intentionally test-only. There should be no production source edits
  in `modules/` or `ui/`.
- The new export route tests monkeypatch `ui.routes.exports.DatabaseExporter`
  for route behavior. The real `DatabaseExporter` is covered separately in
  `tests/test_database_exporter.py`.
- The fake workbook written by `FakeExportEngine.to_excel_with_values` is just
  bytes in a temp directory; it is enough for Flask `send_file` behavior and
  avoids client-data fixtures.
- The session-store tests write only to pytest `tmp_path` and verify the
  corrupt-store rename using a glob, because the exact timestamp is generated
  inside production code.
- The new workflow upload tests monkeypatch `modules.data_loader.DataLoader`
  only for the missing-required-data path. They do not touch `PlanningEngine`
  or calculation logic.
- Existing untracked docs/report files were present before this note and were
  left alone. This handoff file is the only new documentation file from this
  request.

## Remaining Coverage Hotspots

Useful next areas if continuing:

- `modules/cycle_manager.py` at 36%: test snapshot save/load/metadata/clear,
  including corrupt parquet or metadata.
- `modules/mom_comparison_engine.py` at 60%: test empty inputs, no overlapping
  periods, `calculate()`, and `create_scatter_data()`.
- `ui/routes/sessions.py` at 60% and `ui/routes/scenarios.py` at 57%: add edge
  route tests for snapshot/switch/delete and scenario compare/export failures.
- `ui/state_snapshot.py` at 59%: add unit tests around machine override
  extraction, manual-edit detection, and restore edge cases.
- `ui/routes/workflow.py` at 57%: add multi-file upload branch tests for wrong
  filename keyword, empty file field, loader exception, and successful multi-file
  session creation.
