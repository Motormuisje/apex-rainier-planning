"""Golden pipeline test.

Runs the full PlanningEngine on a fixed input and compares every line type /
material / period value against a frozen baseline. A diff here means the
cascade, a formula, or a data-loading path changed behavior — which may be
intentional (regenerate the baseline) or a regression (investigate).

If this test fails, do NOT just regenerate the baseline. Read the diff first.
"""

import json
from pathlib import Path

import pytest


def _engine_to_comparable_dict(engine) -> dict:
    """Convert engine.results to a pure-data dict we can compare and JSON-dump.

    Keyed by line_type -> material_number -> period -> value. Deterministic order.
    """
    out: dict = {}
    for line_type, rows in engine.results.items():
        per_line: dict = {}
        for row in rows:
            # Round to avoid float noise; 6 decimals is well below any
            # business-relevant precision in S&OP planning.
            per_line[row.material_number] = {
                period: round(value, 6)
                for period, value in sorted(row.values.items())
            }
        out[line_type] = dict(sorted(per_line.items()))
    return dict(sorted(out.items()))


def test_baseline_exists(baseline_path):
    """Guard test: fail loudly if the baseline hasn't been generated yet."""
    if not baseline_path.exists():
        pytest.fail(
            f"Baseline not found at {baseline_path}.\n"
            f"Run: python tests/generate_baseline.py"
        )


def test_line_types_match_baseline(planning_engine_result, baseline_path):
    """The set of line types produced must match the baseline exactly."""
    if not baseline_path.exists():
        pytest.skip("baseline not generated yet")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = _engine_to_comparable_dict(planning_engine_result)
    missing = set(baseline) - set(current)
    extra = set(current) - set(baseline)
    assert not missing, f"Line types missing from current run: {sorted(missing)}"
    assert not extra, f"Unexpected new line types in current run: {sorted(extra)}"


def test_materials_per_line_type_match_baseline(planning_engine_result, baseline_path):
    """Within each line type, the set of materials must match."""
    if not baseline_path.exists():
        pytest.skip("baseline not generated yet")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = _engine_to_comparable_dict(planning_engine_result)

    mismatches = []
    for line_type, baseline_mats in baseline.items():
        current_mats = current.get(line_type, {})
        missing = set(baseline_mats) - set(current_mats)
        extra = set(current_mats) - set(baseline_mats)
        if missing or extra:
            mismatches.append((line_type, sorted(missing), sorted(extra)))

    assert not mismatches, (
        "Material sets differ per line_type:\n"
        + "\n".join(
            f"  {lt}: missing={m[:5]}{'…' if len(m) > 5 else ''} "
            f"extra={e[:5]}{'…' if len(e) > 5 else ''}"
            for lt, m, e in mismatches
        )
    )


def test_values_match_baseline(planning_engine_result, baseline_path):
    """Every (line_type, material, period) value must match the baseline.

    Reports up to 10 concrete diffs so you can diagnose at a glance.
    """
    if not baseline_path.exists():
        pytest.skip("baseline not generated yet")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = _engine_to_comparable_dict(planning_engine_result)

    diffs: list = []
    for line_type, base_mats in baseline.items():
        cur_mats = current.get(line_type, {})
        for material, base_periods in base_mats.items():
            cur_periods = cur_mats.get(material, {})
            for period, base_value in base_periods.items():
                cur_value = cur_periods.get(period)
                if cur_value != base_value:
                    diffs.append((line_type, material, period, base_value, cur_value))
                    if len(diffs) >= 10:
                        break
            if len(diffs) >= 10:
                break
        if len(diffs) >= 10:
            break

    if diffs:
        msg = ["Pipeline output diverged from baseline. First diffs:"]
        for lt, mat, per, b, c in diffs:
            msg.append(f"  {lt} | {mat} | {per}: baseline={b} current={c}")
        msg.append("")
        msg.append(
            "If this change is intentional, regenerate the baseline with "
            "`python tests/generate_baseline.py` and commit the intent in "
            "the commit message."
        )
        pytest.fail("\n".join(msg))
