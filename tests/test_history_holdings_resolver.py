"""Tests for resolve_holdings_account_ids."""


def test_resolve_by_institution_and_mask():
    from history import resolve_holdings_account_ids

    yaml_accounts = [
        {
            "institution": "TestBroker",
            "mask": "5678",
            "id": "test-brokerage",
        },
        {"institution": "OtherBroker", "mask": "9999", "id": "other"},
    ]
    holdings = [
        {
            "institution": "TestBroker",
            "account_mask": "5678",
            "security_id": "s1",
            "quantity": 1,
            "price": 10,
            "value": 10,
            "name": "X",
            "ticker": "X",
            "type": "etf",
        }
    ]
    result = resolve_holdings_account_ids(holdings, yaml_accounts)
    assert result[0]["history_account_id"] == "test-brokerage"


def test_resolve_drops_unmapped():
    """(institution, mask) with no yaml match is dropped, not raised."""
    from history import resolve_holdings_account_ids

    yaml_accounts = [{"institution": "Known", "mask": "1111", "id": "known"}]
    holdings = [
        {
            "institution": "Unknown",
            "account_mask": "9999",
            "security_id": "s",
            "quantity": 1,
            "price": 1,
            "value": 1,
            "name": "",
            "ticker": "",
            "type": "",
        }
    ]
    result = resolve_holdings_account_ids(holdings, yaml_accounts)
    assert result == []


def test_resolve_aggregates_multiple_plaid_sharing_id():
    """Two Plaid accounts with different masks but same yaml id both
    resolve to that id."""
    from history import resolve_holdings_account_ids

    yaml_accounts = [
        {
            "institution": "Merrill",
            "mask": "2299",
            "id": "ml-retirement-andrew",
        },
        {
            "institution": "Merrill",
            "mask": "9817",
            "id": "ml-retirement-andrew",
        },
    ]
    holdings = [
        {
            "institution": "Merrill",
            "account_mask": "2299",
            "security_id": "s1",
            "quantity": 1,
            "price": 100,
            "value": 100,
            "name": "",
            "ticker": "",
            "type": "",
        },
        {
            "institution": "Merrill",
            "account_mask": "9817",
            "security_id": "s2",
            "quantity": 1,
            "price": 50,
            "value": 50,
            "name": "",
            "ticker": "",
            "type": "",
        },
    ]
    result = resolve_holdings_account_ids(holdings, yaml_accounts)
    assert len(result) == 2
    for h in result:
        assert h["history_account_id"] == "ml-retirement-andrew"
