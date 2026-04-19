# Task: QA Layer 1 — Test Infrastructure Sprint

## Goal

Install three pieces of QA infrastructure that run automatically or on demand without manual test orchestration:

1. A pre-commit hook that runs pytest and ruff before every commit
2. GitHub Actions CI that runs a fixture-free test subset on every PR
3. Code coverage measurement with a baseline report showing current gaps

At the end of this sprint, the project has automated quality enforcement that catches regressions before they land on main. No new behavior is added to production code.

## Scope

In scope:

- Install and configure `pre-commit` framework locally
- Write `.pre-commit-config.yaml` with pytest and ruff hooks
- Update `requirements-dev.txt` with new dev dependencies
- Create `.github/workflows/ci.yml` for GitHub Actions
- Create at least two new fixture-free unit tests so CI has something to run
- Install and configure `pytest-cov`
- Produce a coverage baseline report
- Document all of the above in `tests/README.md`
- One observations entry at the end summarizing what was added

Out of scope:

- Writing large numbers of new tests to increase coverage
- Refactoring existing code to make it more testable
- Flask route tests (that is Layer 2)
- Playwright or browser-based tests
- Any work on `_apply_volume_change` or cascade helpers

## Rules

Standard rules from `AGENTS.md` apply plus these specific ones:

- One segment per branch. Three branches total, one per segment.
- Conventional commit messages: `chore:` for config files, `test:` for new tests, `docs:` for documentation.
- Stop at every checkpoint marked in this document. Do not continue to the next segment without explicit approval.
- If pre-commit hook infrastructure conflicts with the user's Windows/PowerShell/venv setup in ways not anticipated here, stop and report rather than forcing a workaround.
- If GitHub Actions setup requires any secret or credential, stop and report — the user must handle that themselves.
- Never commit the golden fixture file. Never print the contents of the fixture file to logs.

---

## Segment 1 — Pre-commit hook

**Branch:** `chore/add-pre-commit-hook`

### Purpose

Install the pre-commit framework, configure it to run pytest and ruff on every commit, and verify it actually blocks a bad commit.

### Steps

1. Add to `requirements-dev.txt`:
   - `pre-commit>=3.5.0`
   - `ruff>=0.1.0`
   - Leave existing entries alone
2. Install the new deps: `pip install -r requirements-dev.txt`
3. Create `.pre-commit-config.yaml` in repo root with two hooks:
   - Ruff: use the official `astral-sh/ruff-pre-commit` hook, version pinned
   - Pytest: a local hook that runs `pytest -x --tb=short`, skipping tests that require `SOP_GOLDEN_FIXTURE` if it is not set
4. Configure ruff in `pyproject.toml` or `ruff.toml` to only flag critical rules (rule sets E and F), so the existing codebase doesn't suddenly fail the hook. Do not run `ruff format`. Do not enable additional rule sets.
5. Install the hook: `pre-commit install`
6. Verify it works by running `pre-commit run --all-files` — this should pass or only flag things we consciously accept.

### Windows/venv notes (critical)

- The hook runs in a subshell. On Windows this subshell may not inherit user-scope environment variables set via `setx` or the System Properties dialog.
- For the pytest hook specifically, verify that `SOP_GOLDEN_FIXTURE` is found inside the hook environment. If it is not, tests will skip silently (per our conftest) and the hook will pass without running meaningful tests. That is a failure mode we must detect, not accept.
- Test procedure: create a temporary test file that deliberately fails (e.g., `assert False`), try to commit it, verify the hook blocks the commit with a clear failure message. Then delete the file.
- If the hook passes despite the failing test, `SOP_GOLDEN_FIXTURE` is not propagating. In that case, add to the hook config a line that skips all pytest tests requiring fixtures, and make the hook run *only* fixture-free tests. Document this workaround clearly.

### Stop conditions

- `pre-commit install` fails with an unclear error
- The hook runs but does not propagate `SOP_GOLDEN_FIXTURE` and no clean workaround is available
- Ruff flags more than five existing files on the minimal rule set — if so, we're configuring ruff too strictly; dial it back further
- Any test newly fails after pre-commit is installed that was previously passing

### Verification

- Test a known-bad commit gets blocked
- Test a known-good commit passes through
- Run `pytest -v --tb=no -q` manually to confirm tests still pass outside the hook

### Commit

- `chore: add pre-commit hook for pytest and ruff`
- Body explains what ruff ruleset is enabled and how `SOP_GOLDEN_FIXTURE` is handled in the hook

### Push

- `git push -u origin chore/add-pre-commit-hook`

---

## 🛑 CHECKPOINT 1

**Stop here. Report:**

- Pre-commit hook install status
- Ruff ruleset used and number of files that triggered warnings at minimum ruleset
- Pytest hook strategy used (fixture-dependent tests included / excluded, and why)
- Proof the hook blocks a deliberately failing commit — show the output
- Branch pushed, awaiting PR and merge by the user

**Wait for user approval before starting Segment 2.** The user will open the PR, review, merge, and explicitly say "go to segment 2" before you continue.

---

## Segment 2 — GitHub Actions CI

**Branch:** `chore/add-github-actions-ci` (created from main after segment 1 is merged)

### Purpose

Set up automatic CI that runs on every pull request, using a fixture-free test subset. This is Path B from earlier strategy discussions: no attempt to shim the golden fixture into CI, instead carve out a subset of unit tests that don't need it.

### Steps

1. Identify two or more helpers in `ui/state_snapshot.py` or `ui/parsers.py` that can be tested without loading the full planning engine
2. Write unit tests in a new file `tests/test_unit_helpers.py`:
   - At least two tests
   - No dependency on `SOP_GOLDEN_FIXTURE`
   - No dependency on a PlanningEngine instance
   - Pure input/output or state-manipulation tests
3. Mark these tests with a pytest marker `@pytest.mark.no_fixture` — define this marker in `conftest.py` or `pytest.ini`
4. Verify they pass locally: `pytest tests/test_unit_helpers.py -v`
5. Verify they pass independently of `SOP_GOLDEN_FIXTURE`:
   ```powershell
   $env:SOP_GOLDEN_FIXTURE = $null
   pytest tests/test_unit_helpers.py -v
   ```
   All new tests should pass; fixture-dependent tests in other files should skip
6. Create `.github/workflows/ci.yml`:
   - Trigger on pull_request to main
   - Use ubuntu-latest, Python 3.10
   - Install from `requirements.txt` and `requirements-dev.txt`
   - Run only fixture-free tests: `pytest -m no_fixture -v`
   - Also run `ruff check` with the same config as the pre-commit hook
7. Commit the workflow, the new test file, and the pytest marker config

### Test selection decision

We use a pytest marker, not a separate test directory or filename convention, because:
- Markers are the idiomatic pytest approach
- It allows fixture-free tests to live alongside fixture-dependent tests in the same file if that makes sense later
- CI can filter by marker; local runs ignore it

### Stop conditions

- The two planned unit tests turn out to require the planning engine (no genuinely fixture-free tests are writeable) — if so, stop and report; the strategy needs reconsidering
- GitHub Actions does not support something the workflow needs
- A dependency fails to install on ubuntu-latest

### Verification

- Locally, without fixture: `pytest -m no_fixture -v` passes
- Locally, with fixture: `pytest -v` still shows 7 + new tests green
- Push to the branch, verify in GitHub that the Actions run completes green

### Commit

- `chore: add GitHub Actions CI with fixture-free test subset`
- Body notes the Path B strategy and that the new unit tests are intentionally minimal

### Push

- `git push -u origin chore/add-github-actions-ci`

---

## 🛑 CHECKPOINT 2

**Stop here. Report:**

- Which helpers got unit tests and why those were chosen
- The output of `pytest -m no_fixture -v` (should show only the new tests)
- The output of `pytest -v` (should show all tests green, including the new ones)
- A link to the GitHub Actions run on the pushed branch and its status
- Branch pushed, awaiting PR and merge

**Wait for user approval before starting Segment 3.**

---

## Segment 3 — Coverage measurement

**Branch:** `chore/add-coverage-reporting` (created from main after segment 2 is merged)

### Purpose

Install `pytest-cov`, configure it for this project, produce a baseline coverage report, and document what the report shows. This segment is measurement-only — no new tests to increase coverage.

### Steps

1. Add `pytest-cov>=4.0.0` to `requirements-dev.txt`
2. Install: `pip install -r requirements-dev.txt`
3. Create a `.coveragerc` or add `[tool.coverage]` section to `pyproject.toml`:
   - Source: `ui/`, `modules/`
   - Omit: `tests/`, `.venv/`, `ui/static/`, any generated file patterns
   - Exclude from reporting: pragma `no cover` lines, `if __name__ == "__main__"`, `if TYPE_CHECKING`
4. Run a full coverage pass:
   ```powershell
   $env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
   pytest --cov=ui --cov=modules --cov-report=term-missing --cov-report=html
   ```
5. The HTML report goes to `htmlcov/`. Do NOT commit this directory — add `htmlcov/` to `.gitignore` if not already there
6. Capture the terminal summary output and save it to `docs/tasks/qa-coverage-baseline.md`:
   - Overall coverage percentage
   - Per-module coverage percentages
   - The top five files with lowest coverage
   - Brief commentary on whether the gaps are expected (for example, routes are poorly covered because we have no Flask route tests yet — that's Layer 2 work)
7. Add a short section to `tests/README.md` explaining how to run coverage locally

### Stop conditions

- Coverage run fails for non-obvious reasons
- Overall coverage is surprisingly high (above 70%) or surprisingly low (below 15%) — if so, stop and report; either the config is wrong or there's something worth understanding before continuing

### Expected result

- Overall coverage likely in the 25-45% range given that we have 7 tests against a 10k+ line codebase
- `ui/app.py` likely has decent coverage via blueprint registration and `_apply_volume_change`
- `modules/` likely has good coverage via the golden pipeline
- Route files likely have 0% coverage (no Flask tests yet)
- Cascade helpers likely have low coverage

### Commit

- `chore: add coverage reporting with baseline`
- Body summarizes overall coverage number and biggest gap

### Push

- `git push -u origin chore/add-coverage-reporting`

---

## 🛑 CHECKPOINT 3

**Stop here. Report:**

- Overall coverage percentage
- Per-module breakdown (2-3 sentences)
- Top 5 files with lowest coverage and whether the gaps are expected or surprising
- Content of `docs/tasks/qa-coverage-baseline.md`
- Branch pushed, awaiting PR and merge

**After this checkpoint, the sprint is complete.** If there is time and energy, the user may elect to continue with Layer 2 (Flask route tests), but that is a separate decision.

---

## Final deliverable

At the end of this sprint, the following has landed on main through three separate PRs:

1. `.pre-commit-config.yaml` and updated `requirements-dev.txt` (segment 1)
2. `.github/workflows/ci.yml`, `tests/test_unit_helpers.py`, pytest marker config (segment 2)
3. `.coveragerc` or equivalent, `docs/tasks/qa-coverage-baseline.md`, updated `tests/README.md` (segment 3)

Plus one observations entry noting what was added, dated today, reviewing any remaining QA gaps for future consideration.
