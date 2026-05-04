# Codex prompt — Fix silent snapshot failure

## The bug

`POST /api/sessions/snapshot` in `ui/routes/sessions.py:31-92` silently
swallows a `copy.deepcopy` failure and creates a snapshot with `engine=None`.
The route returns `{"success": true, "session": {"calculated": false}}`, which
the UI treats as a successful save. The user believes their state is saved; it
is not.

The specific lines (around line 44-47):

```python
try:
    engine_copy = copy.deepcopy(sess.get('engine')) if sess.get('engine') is not None else None
except Exception:
    engine_copy = None   # ← bug: silently continues with a broken snapshot
```

The session is then saved to disk and added to the sidebar with a "Pending"
badge instead of "Ready", giving no indication that the deepcopy failed.

## The fix

Change the `except Exception` branch to return a 500 error instead of
continuing with a broken snapshot. Do not save a partial session to disk when
deepcopy fails.

The corrected block should look like this:

```python
engine_copy = None
if sess.get('engine') is not None:
    try:
        engine_copy = copy.deepcopy(sess['engine'])
    except Exception as exc:
        return jsonify({'success': False, 'error': f'Could not copy session state: {exc}'}), 500
```

This is a one-block change inside `snapshot_session()`. Nothing else in the
function changes.

## Scope

- **One file changed:** `ui/routes/sessions.py` — the `snapshot_session` route only.
- **No production behavior changes** when deepcopy succeeds (the happy path is unchanged).
- **No state-sync points affected** — this route creates a new session from
  scratch; it does not modify the active session or `_global_config`.

## Tests

Add one test to `tests/test_routes_sessions.py` covering the deepcopy failure
path. The existing `session_route_app` fixture is already set up for session
route tests — read it before writing.

The new test should:
1. Create a session whose engine raises on deepcopy (use `unittest.mock.patch`
   on `ui.routes.sessions.copy.deepcopy` to raise `RuntimeError("deepcopy failed")`).
2. POST to `/api/sessions/snapshot` with a valid name.
3. Assert the response is 500.
4. Assert `success` is False in the response body.
5. Assert the bad session was NOT added to `session_route_app.sessions`.

Do not change any other existing test.

## Verification

```powershell
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest tests/test_routes_sessions.py -v
pytest -v --ignore=tests/browser
```

All previously passing tests must still pass. The new test must pass.

## Commit

Branch: `fix/snapshot-deepcopy-failure` from main.

```
fix: return 500 on snapshot deepcopy failure instead of saving broken session
```

One commit, two files changed (`ui/routes/sessions.py` and
`tests/test_routes_sessions.py`).
