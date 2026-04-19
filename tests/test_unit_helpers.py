import pytest

from ui.parsers import (
    format_purchased_and_produced,
    parse_purchased_and_produced,
    valuation_params_from_config,
)


@pytest.mark.no_fixture
def test_parse_purchased_and_produced_ignores_invalid_entries():
    raw = 'MAT-001:0.25, invalid, MAT-002:not-a-number, :0.5, MAT-003:1'

    assert parse_purchased_and_produced(raw) == {
        'MAT-001': 0.25,
        'MAT-003': 1.0,
    }


@pytest.mark.no_fixture
def test_format_purchased_and_produced_sorts_materials():
    pap = {'MAT-003': 1.0, 'MAT-001': 0.25}

    assert format_purchased_and_produced(pap) == 'MAT-001:0.25, MAT-003:1.0'


@pytest.mark.no_fixture
def test_valuation_params_from_config_casts_numeric_values():
    params = valuation_params_from_config({
        '1': '10.5',
        '2': 20,
        '3': '',
        '4': None,
        '5': '300.25',
        '6': '400',
        '7': '45.9',
        '8': 60.1,
    })

    assert params.direct_fte_cost_per_month == 10.5
    assert params.indirect_fte_cost_per_month == 20.0
    assert params.overhead_cost_per_month == 0.0
    assert params.sga_cost_per_month == 0.0
    assert params.depreciation_per_year == 300.25
    assert params.net_book_value == 400.0
    assert params.days_sales_outstanding == 45
    assert params.days_payable_outstanding == 60
