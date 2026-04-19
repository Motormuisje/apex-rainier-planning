# QA Coverage Baseline

Date: 2026-04-19

Command:

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest --cov=ui --cov=modules --cov-report=term-missing --cov-report=html
```

Result: 10 tests passed.

## Summary

- Overall coverage: 48% (5416 statements, 2804 missed)
- `ui`: 36% (2473 statements, 1586 missed)
- `modules`: 59% (2943 statements, 1218 missed)

The result is inside the expected 15-70% range for the first measurement
pass. The engine modules get meaningful coverage from the golden pipeline
tests, while the UI layer is mostly untested beyond import/registration
paths and the state-model tests.

## Top Five Lowest-Coverage Files

| File | Coverage | Notes |
| --- | ---: | --- |
| `ui/errors.py` | 7% | Expected gap; error-classification branches need targeted tests. |
| `modules/inventory_quality_engine.py` | 9% | Expected gap; optional quality overlay is not exercised by current golden tests. |
| `ui/routes/scenarios.py` | 9% | Expected gap; Flask route behavior is Layer 2 work. |
| `ui/routes/workflow.py` | 11% | Expected gap; upload/calculate routes need Flask client tests. |
| `ui/routes/edits.py` | 12% | Expected gap; edit endpoints and cascade route behavior need Layer 2 tests. |

## Per-Area Notes

The `modules` package is substantially covered by the full golden pipeline:
core engines such as BOM, forecast, inventory, and value planning are high.
Lower module coverage is concentrated in optional or orchestration-heavy
areas such as inventory quality, MoM comparison, database export, and parts
of `planning_engine.py` that are not reached by the current fixture path.

The `ui` package has lower coverage because most route files are not called
through a Flask test client yet. That is expected for Layer 1. Future Layer 2
tests should target route behavior, session switching, edit import/export,
scenario flows, and workflow upload/calculate paths.

The HTML report was generated in `htmlcov/` and is intentionally ignored.

## Post Layer 2 Sprint

Date: 2026-04-19

Command:

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest --cov=ui --cov=modules --cov-report=term
```

Result: 18 tests passed.

Overall coverage increased from 48% to 51% (5416 statements, 2641 missed).
The gain came from Flask route tests for workflow, edit, machine reset,
pending-edit persistence, and session management routes.

Notable route-file changes:

- `ui/routes/workflow.py`: 11% -> 51%
- `ui/routes/edits.py`: 12% -> 17%
- `ui/routes/edit_state.py`: 28% -> 64%
- `ui/routes/machines.py`: 49% -> 56%
- `ui/routes/sessions.py`: 20% -> 49%

The remaining low-coverage route modules are expected gaps for future route
test sprints: scenarios, config, read, PAP, exports, and license routes.
`POST /api/sessions/snapshot` was intentionally skipped after route testing
exposed a production bug where engine deepcopy failure is swallowed and the
snapshot is returned as successful but uncalculated.
