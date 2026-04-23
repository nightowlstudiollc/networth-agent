"""Tests for plaid_balance.fetch_all_holdings."""

from unittest.mock import patch, MagicMock


def test_fetch_holdings_shape_and_keys():
    import plaid_balance

    # Patch get_investment_holdings directly — the real code path — rather
    # than the lower-level Plaid client. This way the test exercises the
    # tuple unpacking + dict-building in fetch_all_holdings without
    # depending on get_investment_holdings' internal implementation.
    fake_holdings = [
        {
            "account_id": "acct-1",
            "security_id": "sec-1",
            "quantity": 10,
            "institution_price": 5.0,
            "institution_value": 50.0,
            "iso_currency_code": "USD",
        }
    ]
    fake_securities = {
        "sec-1": {
            "security_id": "sec-1",
            "name": "Test Fund",
            "ticker_symbol": "TST",
            "type": "mutual fund",
        }
    }
    fake_accounts_resp = MagicMock()
    fake_accounts_resp.to_dict.return_value = {
        "accounts": [
            {
                "account_id": "acct-1",
                "name": "Brokerage",
                "mask": "1234",
            }
        ]
    }

    with patch.object(plaid_balance, "client") as mock_client, patch.object(
        plaid_balance, "get_investment_holdings"
    ) as mock_get:
        mock_client.accounts_get.return_value = fake_accounts_resp
        mock_get.return_value = (fake_holdings, fake_securities, None)

        items = {
            "item-1": {
                "access_token": "tok",
                "institution_name": "TestBroker",
                "products": ["investments"],
            }
        }
        result = plaid_balance.fetch_all_holdings(items)

    assert len(result) == 1
    h = result[0]
    assert h["institution"] == "TestBroker"
    assert h["account_id"] == "acct-1"
    assert h["account_mask"] == "1234"
    assert h["security_id"] == "sec-1"
    assert h["quantity"] == 10
    assert h["price"] == 5.0
    assert h["value"] == 50.0
    assert h["ticker"] == "TST"
    assert h["name"] == "Test Fund"


def test_fetch_holdings_skips_items_without_investments():
    import plaid_balance

    items = {
        "item-1": {
            "access_token": "tok",
            "institution_name": "BankOnly",
            "products": ["transactions"],
        }
    }
    with patch.object(plaid_balance, "client"):
        result = plaid_balance.fetch_all_holdings(items)
    assert result == []
