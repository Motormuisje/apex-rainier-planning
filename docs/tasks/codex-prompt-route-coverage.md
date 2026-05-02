# Codex prompt — Route coverage sprint (Layer 2 gap fill)

## Context

Apex Rainier is a Flask S&OP planning tool. Layer 2 route tests already exist
for workflow, edits, machines, and sessions. The following route modules have
no tests yet:

| File | Coverage | Endpoints |
|---|---|---|
| `ui/routes/read.py` | 15% | GET /api/results, /api/value_results, /api/dashboard, /api/capacity, /api/inventory, /api/inventory_quality |
| `ui/routes/config.py` | 15% | GET+POST /api/config/folders, GET /api/config, POST /api/config/master-file, POST /api/config/settings, POST /api/config/reset_vp_params |
| `ui/routes/exports.py` | 18% | GET /api/export, POST /api/export_db, GET /api/mom |
| `ui/routes/license.py` | 30% | GET /api/license/status, POST /api/license/activate |
| `ui/routes/pap.py` | 26% | GET /api/pap, POST /api/pap, DELETE /api/pap/<material> |
| `ui/routes/scenarios.py` | 9% | GET /api/scenarios, POST /api/scenarios/save, POST /api/scenarios/load, DELETE /api/scenarios/<id>, POST /api/scenarios/compare, GET /api/scenarios/compare/export |

## Task

Write route tests for the six modules above. One new test file per module.
Place them in `tests/` alongside the existing route test files.

## Rules

- Read **all six** route files fully before writing any code.
- Read `tests/conftest.py` fully before writing any code.
- Read `tests/test_routes_workflow.py`, `tests/test_routes_edits.py`, and
  `tests/test_routes_sessions.py` as pattern references.
- Write tests in the same style: blueprint-injection fixtures (no module-level
  `ui.app` imports), `SimpleNamespace` app wrappers, function-scoped fixtures.
- Each fixture creates the minimal Flask app needed for that blueprint only.
  Do not create one giant fixture for everything.
- Tests must be runnable without `SOP_GOLDEN_FIXTURE` where possible. For
  routes that serve engine results (`/api/results`, `/api/dashboard`, etc.),
  use a pre-built engine from `golden_fixture_path` (session-scoped, like
  `edit_route_app` does).
- Do not test implementation details. Test observable HTTP behavior: status
  code, response shape, key fields present. Do not assert exact floats.
- One happy-path test per endpoint minimum. Add a 404/400 test where the
  route explicitly handles missing session or bad input.
- Skip `POST /api/config/master-file` — it requires a real `.xlsm` file upload
  which complicates fixture setup. Note the skip in a comment.
- Skip `GET /api/export` and `POST /api/export_db` — they produce binary
  `.xlsx` outputs requiring openpyxl teardown. Note the skip in a comment.
- Skip `GET /api/scenarios/compare/export` for the same reason.
- Do NOT modify production code. If a route is untestable without production
  changes, note it in a comment and skip it.
- Mark tests that require `golden_fixture_path` (i.e. a real engine) with
  `@pytest.mark.no_fixture` if and only if they do NOT need the fixture.
  Tests that DO need the fixture must NOT have this marker.

## Blueprint factory signatures — read these before writing fixtures

Each route module has a `create_<name>_blueprint(...)` factory. Read the
actual signature from the source file — do not guess the arguments.

For `read.py`: the factory takes dependencies including a `get_active`
callable and a `global_config` dict. Study `tests/test_routes_sessions.py`
`session_route_app` fixture and `ui/app.py` to understand how `get_active`
is wired.

For `pap.py`: the PAP blueprint modifies engine results in-place. Inspect
`ui/routes/pap.py` to understand what engine state it reads and writes.

For `scenarios.py`: scenarios are saved to and loaded from disk. The factory
takes a `scenarios_dir` callable or path. Inspect `ui/app.py` to see how it
is registered, then use `tmp_path` to provide an isolated directory.

For `license.py`: the factory takes a `license_manager` object. Use a
`SimpleNamespace` or minimal mock — read `modules/license_manager.py` to see
which methods the routes call.

## Deliverables

- `tests/test_routes_read.py`
- `tests/test_routes_config.py`
- `tests/test_routes_exports.py`
- `tests/test_routes_license.py`
- `tests/test_routes_pap.py`
- `tests/test_routes_scenarios.py`

## Verification

Run after each file (not just at the end):

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/test_routes_<name>.py -v
```

Run the full suite before committing:

```powershell
pytest -v
```

All previously passing tests must still pass.

## Commit

One commit per file:

```
test: add read route tests
test: add config route tests
test: add exports route tests
test: add license route tests
test: add pap route tests
test: add scenario route tests
```

Branch: `test/routes-remaining` from main.

## Stop conditions

- A route factory signature requires a dependency that is deeply entangled
  with `ui/app.py` globals and cannot be mocked without touching production
  code → skip that route, note it, continue with the rest.
- A test would require calling `engine.run()` more than once per test session
  to be meaningful → share the engine via a session-scoped fixture.
- Any test that would require modifying production code to pass → skip it.

## Final check

After all six files:

```powershell
pytest --cov=ui --cov=modules --cov-report=term --ignore=tests/browser -q
```

Report the new overall coverage % and per-file deltas for the six modules.
