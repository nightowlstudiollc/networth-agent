"""Tests for history_sheet.read_balances_from_sheet."""

from unittest.mock import MagicMock


def test_read_balances_maps_ids_to_column_b():
    from history_sheet import read_balances_from_sheet

    client = MagicMock()
    client.get_values.return_value = [
        ["Assets", "Balance", "", "", "", "", "", "ID"],
        ["Coinbase", 100.00, "✔️", "", "", "", "", "coinbase"],
        ["Mercury", 250.00, "✔️", "", "", "", "", "mercury-checking"],
        ["Subtotal", 350.00, "", "", "", "", "", ""],  # no ID
        [],
        ["Liabilities"],
        ["Example Card", -600.00, "✔️", "", "", "", "", "example-card"],
    ]

    result = read_balances_from_sheet(client, "ssid", "Net Worth")
    assert result == {
        "coinbase": 100.00,
        "mercury-checking": 250.00,
        "example-card": -600.00,
    }


def test_read_balances_skips_rows_without_id():
    from history_sheet import read_balances_from_sheet

    client = MagicMock()
    client.get_values.return_value = [
        ["header"],
        ["Subtotal", 1000, "", "", "", "", "", ""],
    ]
    assert read_balances_from_sheet(client, "s", "Net Worth") == {}


def test_read_balances_parses_string_currency():
    """'$ (25.99)' must parse to -25.99; '$ 1,234.56' to 1234.56."""
    from history_sheet import read_balances_from_sheet

    client = MagicMock()
    client.get_values.return_value = [
        ["header"],
        ["Card", " $ (25.99)", "✔️", "", "", "", "", "card-1"],
        ["Chk", " $ 1,234.56 ", "✔️", "", "", "", "", "chk-1"],
    ]
    result = read_balances_from_sheet(client, "s", "Net Worth")
    assert result == {"card-1": -25.99, "chk-1": 1234.56}


def test_read_balances_treats_dash_as_zero():
    from history_sheet import read_balances_from_sheet

    client = MagicMock()
    client.get_values.return_value = [
        ["header"],
        ["Empty", " $ -   ", "", "", "", "", "", "empty-1"],
    ]
    result = read_balances_from_sheet(client, "s", "Net Worth")
    assert result == {"empty-1": 0.0}


def test_read_balances_reads_full_column_h_without_row_cap():
    """Must read A:H (open-ended) so ids past row 200 are not silently dropped."""
    from history_sheet import read_balances_from_sheet

    client = MagicMock()
    client.get_values.return_value = []
    read_balances_from_sheet(client, "s", "Net Worth")
    (_, range_) = client.get_values.call_args.args
    assert "200" not in range_
    assert range_.endswith("H") or range_.endswith(":H")
