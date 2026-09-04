"""Tests for plaid_balance.aggregate_balances_by_id.

Validates that (accounts, holdings) → {id: value} produces the per-row
balances used to update the sheet. Liabilities are negative.
"""


def _yaml_accounts():
    return [
        {
            "institution": "Example Bank",
            "mask": "3001",
            "id": "example-checking",
            "type": "asset",
        },
        {
            "institution": "Example Bank",
            "mask": "3002",
            "id": "example-visa",
            "type": "liability",
        },
        {
            "institution": "Example Brokerage",
            "mask": "3003",
            "id": "example-investment",
            "type": "asset",
        },
        {
            "institution": "Example Brokerage",
            "mask": "3004",
            "id": "example-investment",
            "type": "asset",
        },
        {
            "institution": "Example Brokerage",
            "mask": "1111",
            "id": "example-brokerage",
            "type": "asset",
        },
        {
            "institution": "Example Brokerage",
            "mask": "2222",
            "id": "example-brokerage",
            "type": "asset",
        },
    ]


def test_assets_pass_through_as_positive():
    from plaid_balance import aggregate_balances_by_id

    accounts = [
        {
            "institution": "Example Bank",
            "mask": "3001",
            "type": "depository",
            "balance": 150.00,
        },
    ]
    result = aggregate_balances_by_id(accounts, [], _yaml_accounts())
    assert result["example-checking"] == 150.00


def test_liabilities_are_written_as_negative():
    from plaid_balance import aggregate_balances_by_id

    accounts = [
        {
            "institution": "Example Bank",
            "mask": "3002",
            "type": "credit",
            "balance": 2500.00,
        },
    ]
    result = aggregate_balances_by_id(accounts, [], _yaml_accounts())
    assert result["example-visa"] == -2500.00


def test_multiple_accounts_share_id_sum_into_same_row():
    from plaid_balance import aggregate_balances_by_id

    # Both 3003 and 3004 map to example-investment; their holdings must sum.
    accounts = [
        {
            "institution": "Example Brokerage",
            "mask": "3003",
            "type": "depository",
            "balance": 0.0,
            "account_id": "A1",
        },
        {
            "institution": "Example Brokerage",
            "mask": "3004",
            "type": "depository",
            "balance": 0.0,
            "account_id": "A2",
        },
    ]
    holdings = [
        {"institution": "Example Brokerage", "account_id": "A1", "value": 100000.00},
        {"institution": "Example Brokerage", "account_id": "A2", "value": 25.00},
    ]
    result = aggregate_balances_by_id(accounts, holdings, _yaml_accounts())
    assert result["example-investment"] == 100025.00


def test_unmapped_accounts_are_skipped():
    from plaid_balance import aggregate_balances_by_id

    accounts = [
        {
            "institution": "Unknown",
            "mask": "9999",
            "type": "depository",
            "balance": 100.0,
        },
    ]
    result = aggregate_balances_by_id(accounts, [], _yaml_accounts())
    assert result == {}


def test_none_balance_is_skipped_not_written_as_zero():
    """Plaid returning no balance must not become an authoritative 0.00
    in the sheet — the row should be omitted so the prior value stays."""
    from plaid_balance import aggregate_balances_by_id

    accounts = [
        {
            "institution": "Example Bank",
            "mask": "3001",
            "type": "depository",
            "balance": None,
        },
    ]
    assert aggregate_balances_by_id(accounts, [], _yaml_accounts()) == {}


def test_cash_balance_and_holdings_both_contribute():
    """Brokerage account with cash + investments: B = cash + holdings."""
    from plaid_balance import aggregate_balances_by_id

    accounts = [
        {
            "institution": "Example Brokerage",
            "mask": "1111",
            "type": "investment",
            "balance": 500.0,
            "account_id": "RET_A",
        },
    ]
    holdings = [
        {"institution": "Example Brokerage", "account_id": "RET_A", "value": 100000.0},
    ]
    result = aggregate_balances_by_id(accounts, holdings, _yaml_accounts())
    assert result["example-brokerage"] == 100500.0
