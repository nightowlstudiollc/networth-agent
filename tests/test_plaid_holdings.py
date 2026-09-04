"""Tests for plaid_balance investment holdings and get_plaid_balances cash handling."""

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

    mock_client = MagicMock()
    mock_client.accounts_get.return_value = fake_accounts_resp

    with patch.object(
        plaid_balance, "_get_client", return_value=mock_client
    ), patch.object(plaid_balance, "get_investment_holdings") as mock_get:
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
    result = plaid_balance.fetch_all_holdings(items)
    assert result == []


def test_get_plaid_balances_investment_cash_residual_preserved():
    """Settlement cash not represented in holdings must not be silently dropped.

    When balances.current exceeds the sum of holdings values, the difference
    is residual cash (e.g. money-market settlement fund).  It must appear in
    the returned account's balance so aggregate_balances_by_id captures it.
    """
    import plaid_balance

    fake_holdings = [
        {
            "account_id": "acct-brokerage",
            "security_id": "sec-1",
            "quantity": 100,
            "institution_price": 950.0,
            "institution_value": 95000.0,
            "iso_currency_code": "USD",
        }
    ]
    fake_securities = {
        "sec-1": {
            "name": "Index Fund",
            "ticker_symbol": "IDXF",
            "type": "mutual fund",
        }
    }

    fake_accounts_resp = MagicMock()
    fake_accounts_resp.to_dict.return_value = {
        "accounts": [
            {
                "account_id": "acct-brokerage",
                "name": "Brokerage Account",
                "official_name": "Brokerage Account",
                "type": "investment",
                "subtype": "brokerage",
                "mask": "1234",
                "balances": {
                    "current": 100000.0,  # 95000 holdings + 5000 cash
                    "available": None,
                    "iso_currency_code": "USD",
                },
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.accounts_get.return_value = fake_accounts_resp

    items = {
        "item-1": {
            "access_token": "tok",
            "institution_name": "TestBroker",
            "products": ["investments"],
        }
    }

    with (
        patch.object(plaid_balance, "_get_client", return_value=mock_client),
        patch.object(
            plaid_balance,
            "get_investment_holdings",
            return_value=(fake_holdings, fake_securities, None),
        ),
        patch.object(plaid_balance, "load_items", return_value=items),
    ):
        result = plaid_balance.get_plaid_balances(realtime=False)

    accounts = result["accounts"]
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc["balance_from_holdings"] is True
    # Cash residual (100000 - 95000) must be preserved, not zeroed out
    assert acc["balance"] == 5000.0

    # total_assets must include both holdings (95000) and cash residual (5000)
    assert result["total_assets"] == 100000.0


def test_get_plaid_balances_investment_no_cash_residual():
    """Investment account where holdings cover full balance has zero cash residual."""
    import plaid_balance

    fake_holdings = [
        {
            "account_id": "acct-brokerage",
            "security_id": "sec-1",
            "quantity": 100,
            "institution_price": 1000.0,
            "institution_value": 100000.0,
            "iso_currency_code": "USD",
        }
    ]
    fake_securities = {
        "sec-1": {
            "name": "Index Fund",
            "ticker_symbol": "IDXF",
            "type": "mutual fund",
        }
    }

    fake_accounts_resp = MagicMock()
    fake_accounts_resp.to_dict.return_value = {
        "accounts": [
            {
                "account_id": "acct-brokerage",
                "name": "Brokerage Account",
                "official_name": "Brokerage Account",
                "type": "investment",
                "subtype": "brokerage",
                "mask": "1234",
                "balances": {
                    "current": 100000.0,
                    "available": None,
                    "iso_currency_code": "USD",
                },
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.accounts_get.return_value = fake_accounts_resp

    items = {
        "item-1": {
            "access_token": "tok",
            "institution_name": "TestBroker",
            "products": ["investments"],
        }
    }

    with (
        patch.object(plaid_balance, "_get_client", return_value=mock_client),
        patch.object(
            plaid_balance,
            "get_investment_holdings",
            return_value=(fake_holdings, fake_securities, None),
        ),
        patch.object(plaid_balance, "load_items", return_value=items),
    ):
        result = plaid_balance.get_plaid_balances(realtime=False)

    accounts = result["accounts"]
    assert len(accounts) == 1
    # Holdings fully cover balance — no residual cash
    assert accounts[0]["balance"] == 0.0
    assert result["total_assets"] == 100000.0
