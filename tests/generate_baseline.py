"""Generate / regenerate the golden baseline.

Run this ONCE to freeze current pipeline output as the expected baseline:

    python tests/generate_baseline.py

Re-run only when:
  - A deliberate engine change makes the diff expected, AND
  - You have manually verified the new numbers are correct.

When you regenerate, the commit message must explain WHY the numbers changed.
Never regenerate just to make a failing test pass without that review.
"""

import json
import os
import sys
from pathlib import Path

# Make `modules` importable when running this as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.planning_engine import PlanningEngine  # noqa: E402


def _engine_to_comparable_dict(engine) -> dict:
    out: dict = {}
    for line_type, rows in engine.results.items():
        per_line: dict = {}
        for row in rows:
            per_line[row.material_number] = {
                period: round(value, 6)
                for period, value in sorted(row.values.items())
            }
        out[line_type] = dict(sorted(per_line.items()))
    return dict(sorted(out.items()))


def main() -> int:
    fixture = os.environ.get("SOP_GOLDEN_FIXTURE")
    if not fixture:
        print("ERROR: set SOP_GOLDEN_FIXTURE to your golden .xlsm path.", file=sys.stderr)
        return 1
    fixture_path = Path(fixture)
    if not fixture_path.exists():
        print(f"ERROR: {fixture_path} does not exist.", file=sys.stderr)
        return 1

    print(f"Running pipeline on {fixture_path.name}...")
    engine = PlanningEngine(
        str(fixture_path),
        planning_month="2025-12",
        months_actuals=11,
        months_forecast=12,
    )
    engine.run()

    data = _engine_to_comparable_dict(engine)
    baseline_path = fixture_path.parent / "golden_baseline.json"
    baseline_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    line_types = len(data)
    materials = sum(len(v) for v in data.values())
    print(f"Baseline written: {baseline_path}")
    print(f"  line_types: {line_types}")
    print(f"  total (line_type, material) pairs: {materials}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
