"""JSON-safe payload builders for the Flask UI."""

from datetime import datetime
from enum import Enum

from modules.models import LineType


def moq_warnings_payload(engine) -> dict:
    """Build moq_raw_needs dict for frontend MOQ warning rendering."""
    return {'moq_raw_needs': getattr(engine, 'all_purch_raw_needs', {}) or {}}


def value_results_payload(engine) -> dict:
    value_results = {
        lt: [row_payload(row) for row in rows]
        for lt, rows in (getattr(engine, 'value_results', {}) or {}).items()
    }
    return {
        'value_results': value_results,
        'consolidation': value_results.get(LineType.CONSOLIDATION.value, []),
    }


def planning_value_payload(engine) -> dict:
    return {
        'periods': list(getattr(engine.data, 'periods', []) or []),
        'results': {
            lt: [row_payload(row) for row in rows]
            for lt, rows in (getattr(engine, 'results', {}) or {}).items()
        },
        **value_results_payload(engine),
        **moq_warnings_payload(engine),
    }


def json_safe(value):
    """Convert row payloads to plain JSON types.

    Some test doubles and optional model fields can contain objects such as
    MagicMock. API responses should stay serializable even when an optional
    display-only field has an unexpected object value.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            return None
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(json_safe(k)): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, Enum):
        return json_safe(value.value)
    try:
        item = value.item
    except AttributeError:
        item = None
    if callable(item):
        try:
            return json_safe(item())
        except Exception:
            pass
    return str(value)


def row_payload(row) -> dict:
    return json_safe(row.to_dict())
