"""Tests for history_sheet.write_balances_to_sheet."""

from unittest.mock import MagicMock

import pytest


def _client_with_sheet():
    """SheetClient whose column H matches the Net Worth layout."""
    client = MagicMock()
    client.get_values.return_value = [
        ["Assets", "Balance", "", "", "", "", "", "ID"],
        ["Coinbase", 24.42, "", "", "", "", "", "coinbase"],
        ["Home Value (Zillow)", 549600, "", "", "", "", "", "zillow-home"],
        ["Mercury", 296.48, "", "", "", "", "", "mercury-checking"],
        ["Subtotal", 870.90, "", "", "", "", "", ""],
        [],
        ["Liabilities"],
        ["Amex Bonvoy", -638.08, "", "", "", "", "", "amex-bonvoy"],
    ]
    return client


def test_write_balances_resolves_rows_by_id_column():
    from history_sheet import write_balances_to_sheet

    client = _client_with_sheet()
    write_balances_to_sheet(
        client,
        "ssid",
        "Net Worth",
        {"zillow-home": 555600, "mercury-checking": 338.96},
    )

    client.batch_update_values.assert_called_once()
    spreadsheet_id, value_ranges = client.batch_update_values.call_args.args
    assert spreadsheet_id == "ssid"

    by_range = {r["range"]: r["values"] for r in value_ranges}
    # zillow-home is row 3 in _client_with_sheet; mercury-checking is row 4.
    assert by_range["Net Worth!B3:C3"] == [[555600, "✔️"]]
    assert by_range["Net Worth!B4:C4"] == [[338.96, "✔️"]]


def test_write_balances_writes_negative_values_for_liabilities():
    from history_sheet import write_balances_to_sheet

    client = _client_with_sheet()
    write_balances_to_sheet(client, "s", "Net Worth", {"amex-bonvoy": -281.55})
    value_ranges = client.batch_update_values.call_args.args[1]
    assert value_ranges == [{"range": "Net Worth!B8:C8", "values": [[-281.55, "✔️"]]}]


def test_write_balances_raises_on_unknown_id():
    from history_sheet import write_balances_to_sheet

    client = _client_with_sheet()
    with pytest.raises(KeyError, match="no-such-id"):
        write_balances_to_sheet(client, "s", "Net Worth", {"no-such-id": 1.0})


def test_write_balances_no_op_when_empty():
    from history_sheet import write_balances_to_sheet

    client = _client_with_sheet()
    write_balances_to_sheet(client, "s", "Net Worth", {})
    client.batch_update_values.assert_not_called()
    # Reading col H is also unnecessary when nothing to write.
    client.get_values.assert_not_called()


def test_write_balances_reads_full_column_h_without_row_cap():
    """Must read A:H (open-ended) so ids past row 200 resolve correctly."""
    from history_sheet import write_balances_to_sheet

    client = _client_with_sheet()
    write_balances_to_sheet(client, "s", "Net Worth", {"coinbase": 1.0})
    (_, range_) = client.get_values.call_args.args
    assert "200" not in range_
    assert range_.endswith("H") or range_.endswith(":H")


def test_write_balances_batches_all_writes_into_single_call():
    from history_sheet import write_balances_to_sheet

    client = _client_with_sheet()
    write_balances_to_sheet(
        client,
        "s",
        "Net Worth",
        {"coinbase": 10, "amex-bonvoy": -20, "zillow-home": 500000},
    )
    assert client.batch_update_values.call_count == 1
    value_ranges = client.batch_update_values.call_args.args[1]
    assert len(value_ranges) == 3
