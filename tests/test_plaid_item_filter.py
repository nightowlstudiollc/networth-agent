"""Tests for plaid_balance.filter_items — scoping a fetch to named institutions.

A full --force sweep bills accounts/balance/get at $0.10 per item. Recovering a
single failed institution (a re-auth, or a transient INSTITUTION_NOT_RESPONDING)
previously meant re-running every item: 13 x $0.10 instead of $0.10. These tests
pin the matching rules used to scope that retry.
"""

import pytest

from plaid_balance import UnknownInstitutionError, filter_items


def _items():
    return {
        "item-1": {"institution_name": "Chase", "access_token": "t1"},
        "item-2": {"institution_name": "Chase", "access_token": "t2"},
        "item-3": {"institution_name": "Bank Alpha", "access_token": "t3"},
        "item-4": {"institution_name": "Lender Beta", "access_token": "t4"},
    }


def test_no_filter_returns_everything():
    """An empty selection is 'all items', not 'no items'."""
    assert filter_items(_items(), None) == _items()
    assert filter_items(_items(), []) == _items()


def test_single_institution_selects_only_that_item():
    result = filter_items(_items(), ["Bank Alpha"])
    assert list(result) == ["item-3"]


def test_match_is_case_insensitive():
    """Typing an institution name by hand should not require exact casing."""
    result = filter_items(_items(), ["bank alpha"])
    assert list(result) == ["item-3"]


def test_surrounding_whitespace_is_ignored():
    result = filter_items(_items(), ["  Bank Alpha  "])
    assert list(result) == ["item-3"]


def test_duplicate_institution_returns_all_matching_items():
    """Chase has two items; scoping to Chase must not silently drop one.

    Returning only the first would under-fetch and write a stale balance.
    """
    result = filter_items(_items(), ["Chase"])
    assert sorted(result) == ["item-1", "item-2"]


def test_multiple_institutions_are_unioned():
    result = filter_items(_items(), ["Bank Alpha", "Lender Beta"])
    assert sorted(result) == ["item-3", "item-4"]


def test_unknown_institution_raises_rather_than_fetching_nothing():
    """A typo must fail loudly.

    Silently matching nothing would exit 0 having fetched no balances, which
    reads as success and leaves the sheet stale.
    """
    with pytest.raises(UnknownInstitutionError) as exc:
        filter_items(_items(), ["Bank Alpho"])
    assert "Bank Alpho" in str(exc.value)


def test_unknown_institution_error_lists_valid_names():
    """The error should be actionable without going to read the items file."""
    with pytest.raises(UnknownInstitutionError) as exc:
        filter_items(_items(), ["Nope"])
    message = str(exc.value)
    assert "Chase" in message
    assert "Bank Alpha" in message
    # Duplicate institutions appear once in the suggestion list.
    assert message.count("Chase") == 1


def test_one_bad_name_among_good_ones_still_raises():
    """Partial success would fetch some items and bill for them silently."""
    with pytest.raises(UnknownInstitutionError):
        filter_items(_items(), ["Chase", "Nope"])


def test_items_missing_institution_name_do_not_crash():
    items = {"item-x": {"access_token": "t"}}
    assert filter_items(items, None) == items
    with pytest.raises(UnknownInstitutionError):
        filter_items(items, ["Chase"])
