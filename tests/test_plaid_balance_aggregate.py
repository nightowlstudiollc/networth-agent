"""Tests for plaid_balance.aggregate_balances_by_id.

Validates that (accounts, holdings) → {id: value} produces the per-row
balances used to update the sheet. Liabilities are negative.
"""


def _yaml_accounts():
    return [
        {
            "institution": "Bank of America",
            "mask": "0696",
            "id": "ml-banking",
            "type": "asset",
        },
        {
            "institution": "Bank of America",
            "mask": "7942",
            "id": "boa-atmos-visa",
            "type": "liability",
        },
        {
            "institution": "Merrill",
            "mask": "0229",
            "id": "ml-investment",
            "type": "asset",
        },
        {
            "institution": "Merrill",
            "mask": "1H40",
            "id": "ml-investment",
            "type": "asset",
        },
        {
            "institution": "Merrill",
            "mask": "2299",
            "id": "ml-retirement-andrew",
            "type": "asset",
        },
        {
            "institution": "Merrill",
            "mask": "9817",
            "id": "ml-retirement-andrew",
            "type": "asset",
        },
    ]


def test_assets_pass_through_as_positive():
    from plaid_balance import aggregate_balances_by_id

    accounts = [
        {
            "institution": "Bank of America",
            "mask": "0696",
            "type": "depository",
            "balance": 25.46,
        },
    ]
    result = aggregate_balances_by_id(accounts, [], _yaml_accounts())
    assert result["ml-banking"] == 25.46


def test_liabilities_are_written_as_negative():
    from plaid_balance import aggregate_balances_by_id

    accounts = [
        {
            "institution": "Bank of America",
            "mask": "7942",
            "type": "credit",
            "balance": 4989.64,
        },
    ]
    result = aggregate_balances_by_id(accounts, [], _yaml_accounts())
    assert result["boa-atmos-visa"] == -4989.64


def test_multiple_accounts_share_id_sum_into_same_row():
    from plaid_balance import aggregate_balances_by_id

    # Both 0229 and 1H40 map to ml-investment; their holdings must sum.
    accounts = [
        {
            "institution": "Merrill",
            "mask": "0229",
            "type": "depository",
            "balance": 0.0,
            "account_id": "A1",
        },
        {
            "institution": "Merrill",
            "mask": "1H40",
            "type": "depository",
            "balance": 0.0,
            "account_id": "A2",
        },
    ]
    holdings = [
        {"institution": "Merrill", "account_id": "A1", "value": 109601.00},
        {"institution": "Merrill", "account_id": "A2", "value": 25.00},
    ]
    result = aggregate_balances_by_id(accounts, holdings, _yaml_accounts())
    assert result["ml-investment"] == 109626.00


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
            "institution": "Bank of America",
            "mask": "0696",
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
            "institution": "Merrill",
            "mask": "2299",
            "type": "investment",
            "balance": 500.0,
            "account_id": "RET_A",
        },
    ]
    holdings = [
        {"institution": "Merrill", "account_id": "RET_A", "value": 680000.0},
    ]
    result = aggregate_balances_by_id(accounts, holdings, _yaml_accounts())
    assert result["ml-retirement-andrew"] == 680500.0
