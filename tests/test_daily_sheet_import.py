from modules.portfolio.services.daily_sheet_import import _parse_amount, _parse_date


def test_parse_date_variants():
    assert _parse_date("2024-05-31") == "2024-05-31"
    assert _parse_date("31/05/2024") == "2024-05-31"
    assert _parse_date("May 2024") == "2024-05-01"


def test_parse_amount_variants():
    assert _parse_amount("₹12,34,567") == 1234567.0
    assert _parse_amount("USD 123.45") == 123.45
    assert _parse_amount("—") is None
