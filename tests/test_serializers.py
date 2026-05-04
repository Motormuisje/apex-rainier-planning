from datetime import datetime
from types import SimpleNamespace

import pytest

from modules.models import LineType
from ui.serializers import (
    json_safe,
    moq_warnings_payload,
    planning_value_payload,
    row_payload,
    value_results_payload,
)


pytestmark = pytest.mark.no_fixture


class FakeScalar:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class BadScalar:
    def item(self):
        raise RuntimeError("not scalarizable")

    def __str__(self):
        return "fallback-value"


class FakeRow:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


def test_json_safe_normalizes_nested_payloads():
    when = datetime(2026, 4, 22, 7, 0, 0)

    payload = json_safe({
        1: [
            None,
            True,
            float("nan"),
            float("inf"),
            when,
            LineType.DEMAND_FORECAST,
            FakeScalar(12.5),
            BadScalar(),
        ],
    })

    assert payload == {
        "1": [
            None,
            True,
            None,
            None,
            "2026-04-22T07:00:00",
            LineType.DEMAND_FORECAST.value,
            12.5,
            "fallback-value",
        ],
    }


def test_row_payload_converts_to_json_safe_dict():
    payload = row_payload(FakeRow({
        "material_number": "MAT-1",
        "values": {"2025-12": float("-inf")},
    }))

    assert payload == {
        "material_number": "MAT-1",
        "values": {"2025-12": None},
    }


def test_payload_builders_return_frontend_shapes():
    planning_row = FakeRow({
        "material_number": "MAT-1",
        "line_type": LineType.DEMAND_FORECAST.value,
        "values": {"2025-12": 10.0},
    })
    consolidation_row = FakeRow({
        "material_number": "ZZZZZZ_REVENUE",
        "line_type": LineType.CONSOLIDATION.value,
        "values": {"2025-12": 100.0},
    })
    engine = SimpleNamespace(
        data=SimpleNamespace(periods=["2025-12"]),
        results={LineType.DEMAND_FORECAST.value: [planning_row]},
        value_results={LineType.CONSOLIDATION.value: [consolidation_row]},
        all_purch_raw_needs={"MAT-RAW": {"2025-12": 3.0}},
    )

    assert moq_warnings_payload(engine) == {"moq_raw_needs": {"MAT-RAW": {"2025-12": 3.0}}}

    value_payload = value_results_payload(engine)
    assert set(value_payload) == {"value_results", "consolidation"}
    assert value_payload["consolidation"][0]["material_number"] == "ZZZZZZ_REVENUE"

    planning_payload = planning_value_payload(engine)
    assert planning_payload["periods"] == ["2025-12"]
    assert planning_payload["results"][LineType.DEMAND_FORECAST.value][0]["material_number"] == "MAT-1"
    assert planning_payload["value_results"][LineType.CONSOLIDATION.value][0]["values"] == {"2025-12": 100.0}
    assert planning_payload["moq_raw_needs"] == {"MAT-RAW": {"2025-12": 3.0}}
