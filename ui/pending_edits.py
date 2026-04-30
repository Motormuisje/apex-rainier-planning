"""Pending edit key helpers."""


def pending_edit_key(line_type, material_number, aux_column, period) -> str:
    return (
        f"{str(line_type)}||"
        f"{str(material_number)}||"
        f"{str(aux_column or '').strip()}||"
        f"{str(period)}"
    )


def canonical_pending_edit_key(key: str) -> str:
    parts = str(key or '').split('||')
    if len(parts) != 4:
        return str(key or '').strip()
    line_type, material_number, aux_column, period = parts
    return pending_edit_key(line_type, material_number, aux_column, period)
