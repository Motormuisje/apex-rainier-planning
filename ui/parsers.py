"""Small parsing and formatting helpers for UI request/config values."""

from modules.models import ValuationParameters


def parse_purchased_and_produced(value) -> dict:
    pap = {}
    for entry in str(value or '').split(','):
        parts = entry.strip().split(':')
        if len(parts) != 2:
            continue
        mat = parts[0].strip()
        if not mat:
            continue
        try:
            pap[mat] = float(parts[1].strip())
        except ValueError:
            pass
    return pap


def format_purchased_and_produced(value: dict) -> str:
    return ', '.join(f'{mat}:{frac}' for mat, frac in sorted((value or {}).items()))


def valuation_params_from_config(value) -> ValuationParameters:
    vp = value or {}
    return ValuationParameters(
        direct_fte_cost_per_month=float(vp.get('1', 0) or 0),
        indirect_fte_cost_per_month=float(vp.get('2', 0) or 0),
        overhead_cost_per_month=float(vp.get('3', 0) or 0),
        sga_cost_per_month=float(vp.get('4', 0) or 0),
        depreciation_per_year=float(vp.get('5', 0) or 0),
        net_book_value=float(vp.get('6', 0) or 0),
        days_sales_outstanding=int(float(vp.get('7', 0) or 0)),
        days_payable_outstanding=int(float(vp.get('8', 0) or 0)),
    )
