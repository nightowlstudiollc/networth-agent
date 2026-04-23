"""Tests for decompose_security (market-vs-flow attribution)."""

from history import decompose_security


def test_no_change():
    d = decompose_security(100, 50, 100, 50)
    assert d == {
        "market": 0,
        "flow": 0,
        "value_old": 5000,
        "value_new": 5000,
    }


def test_pure_market_gain():
    d = decompose_security(100, 50, 100, 55)
    assert d["market"] == 500
    assert d["flow"] == 0
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_pure_flow_add():
    d = decompose_security(100, 50, 120, 50)
    assert d["market"] == 0
    assert d["flow"] == 1000
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_mixed_market_and_flow():
    d = decompose_security(100, 50, 120, 55)
    assert d["market"] == 500
    assert d["flow"] == 1100
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_newly_held():
    """qty_old=0 means entirely flow."""
    d = decompose_security(0, 0, 10, 100)
    assert d["market"] == 0
    assert d["flow"] == 1000
    assert d["value_old"] == 0
    assert d["value_new"] == 1000


def test_fully_sold_with_price_change():
    d = decompose_security(100, 50, 0, 55)
    assert d["market"] == 500
    assert d["flow"] == -5500
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_sale_at_same_price():
    d = decompose_security(100, 50, 60, 50)
    assert d["market"] == 0
    assert d["flow"] == -2000
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]
