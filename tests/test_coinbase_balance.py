"""Tests for coinbase_balance total-value computation.

The original implementation summed only ``available_balance``, which excludes
funds held by open orders. On a portfolio whose value sits mostly in ``hold``
that under-reported the total by an order of magnitude (~$2.42 vs ~$26.45) and
led to the incorrect conclusion that the Advanced Trade API could not see the
full position. These tests pin the corrected behavior: total = available + hold.
"""

from decimal import Decimal

from coinbase_balance import compute_total_usd


def _acct(currency, available, hold):
    """Build an account dict shaped like the Advanced Trade API response."""
    return {
        "currency": currency,
        "name": f"{currency} Wallet",
        "available_balance": {"currency": currency, "value": available},
        "hold": {"currency": currency, "value": hold},
    }


def test_held_funds_are_included():
    """Funds in `hold` count toward the total, not just `available_balance`."""
    accounts = [_acct("DOGE", "0.08864084", "250.2")]
    prices = {"DOGE": Decimal("0.08947")}

    result = compute_total_usd(accounts, prices)

    # 250.28864084 * 0.08947 ≈ 22.39
    assert round(result["total_usd"], 2) == 22.39


def test_excluding_hold_would_undercount():
    """Regression guard for the original bug.

    An account holding everything in `hold` with a zero available balance must
    not report as zero — that is precisely the failure mode being fixed.
    """
    accounts = [_acct("PEPE", "0", "425162")]
    prices = {"PEPE": Decimal("0.00000386")}

    result = compute_total_usd(accounts, prices)

    assert result["total_usd"] > 0
    assert round(result["total_usd"], 2) == 1.64


def test_fiat_and_stablecoin_valued_at_one_to_one():
    """USD and USDC are not priced through a product ticker."""
    accounts = [_acct("USD", "10", "0"), _acct("USDC", "5", "0")]

    result = compute_total_usd(accounts, prices={})

    assert round(result["total_usd"], 2) == 15.00


def test_zero_balance_accounts_are_omitted_from_holdings():
    """Accounts with no value at all do not clutter the holdings list."""
    accounts = [_acct("ETH", "0", "0"), _acct("USD", "3", "0")]

    result = compute_total_usd(accounts, prices={})

    assert [h["currency"] for h in result["holdings"]] == ["USD"]
    assert round(result["total_usd"], 2) == 3.00


def test_missing_price_does_not_crash_and_contributes_zero():
    """An un-priceable asset is reported but valued at zero, not fatal."""
    accounts = [_acct("OBSCURE", "100", "0"), _acct("USD", "7", "0")]

    result = compute_total_usd(accounts, prices={})

    assert round(result["total_usd"], 2) == 7.00
    obscure = [h for h in result["holdings"] if h["currency"] == "OBSCURE"]
    assert obscure and obscure[0]["usd_value"] == 0.0


def test_holdings_sorted_by_value_descending():
    accounts = [
        _acct("USD", "1", "0"),
        _acct("DOGE", "0", "250.2"),
        _acct("USDC", "5", "0"),
    ]
    prices = {"DOGE": Decimal("0.08947")}

    result = compute_total_usd(accounts, prices)

    values = [h["usd_value"] for h in result["holdings"]]
    assert values == sorted(values, reverse=True)


def test_real_portfolio_totals_match_observed_values():
    """End-to-end shape check against the live 2026-09-03 portfolio."""
    accounts = [
        _acct("PEPE", "0.05910391", "425162"),
        _acct("DOGE", "0.08864084", "250.2"),
        _acct("ETH", "0.000958872895589668", "0"),
        _acct("USDC", "0.008051", "0"),
        _acct("USD", "0.0018159104875", "0"),
    ]
    prices = {
        "PEPE": Decimal("0.00000386"),
        "DOGE": Decimal("0.08947"),
        "ETH": Decimal("2505.64"),
    }

    result = compute_total_usd(accounts, prices)

    assert round(result["total_usd"], 2) == 26.45
