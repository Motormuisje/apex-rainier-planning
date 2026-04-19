# Tests

## Setup (one-time per machine)

Test data is never committed to this repo. Point the test suite at a local
fixture with an environment variable.

### 1. Put a golden MS_RECONC workbook outside the repo

```powershell
$fixtures = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures"
New-Item -ItemType Directory -Force -Path $fixtures
# drop your golden .xlsm here, e.g. golden_MS_RECONC.xlsm
```

### 2. Set the env var (user-scope, persists across reboots)

```powershell
[System.Environment]::SetEnvironmentVariable(
    'SOP_GOLDEN_FIXTURE',
    "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm",
    'User'
)
```

Close and reopen PowerShell, then verify:

```powershell
echo $env:SOP_GOLDEN_FIXTURE
Test-Path $env:SOP_GOLDEN_FIXTURE   # should print True
```

### 3. Install dev deps

```powershell
pip install -r requirements-dev.txt
```

### 4. Freeze the baseline (one-time, or after an intentional change)

```powershell
python tests/generate_baseline.py
```

This writes `golden_baseline.json` next to the `.xlsm` in
`%LOCALAPPDATA%\SOPPlanningEngine\fixtures\`. The baseline is also never
committed — it contains client-derived numbers.

## Running tests

```powershell
pytest -v
```

All tests auto-skip if `SOP_GOLDEN_FIXTURE` isn't set, so a fresh checkout
on a machine without the fixture won't produce false failures.

## Coverage

Install the dev dependencies and run coverage with the golden fixture
available:

```powershell
pip install -r requirements-dev.txt
$env:SOP_GOLDEN_FIXTURE = "$env:LOCALAPPDATA\SOPPlanningEngine\fixtures\golden_MS_RECONC.xlsm"
pytest --cov=ui --cov=modules --cov-report=term-missing --cov-report=html
```

The terminal output shows the coverage baseline. The HTML report is written
to `htmlcov/`, which is local output and is not committed.

## What the golden test catches

- Any change to the set of line types produced
- Any change to the set of materials per line type
- Any numeric change in any (line_type, material, period) value

If the test fails after a code change, that means the pipeline output
shifted. Before regenerating the baseline, look at the first reported diffs
and decide: is this a bug, or an intended change? If intended, regenerate
and explain the reason in the commit message. Never regenerate blindly.
