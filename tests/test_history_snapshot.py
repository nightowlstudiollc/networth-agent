"""Tests for history.write_snapshot."""

import pytest


def test_write_snapshot_inserts_snapshot_row(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    sid = write_snapshot(
        db,
        captured_at="2026-04-13T18:00:00Z",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1000.0, "test-brokerage": 50000.0},
        holdings=[],
    )
    assert sid is not None
    row = db["snapshots"].get(sid)
    assert row["week_of"] == "2026-04-13"
    assert row["source"] == "weekly"


def test_write_snapshot_inserts_balance_rows(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    sid = write_snapshot(
        db,
        captured_at="t",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1000.0, "test-brokerage": 50000.0},
        holdings=[],
    )
    rows = {
        r["account_id"]: r["balance"]
        for r in db["balances"].rows_where("snapshot_id = ?", [sid])
    }
    assert rows == {"test-checking": 1000.0, "test-brokerage": 50000.0}


def test_snapshot_inserts_holdings_and_securities(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    holdings = [
        {
            "institution": "TestBroker",
            "account_id": "plaid-acct-1",
            "account_name": "Brokerage",
            "account_mask": "5678",
            "security_id": "sec-1",
            "ticker": "VTV",
            "name": "Vanguard Value ETF",
            "type": "etf",
            "quantity": 10,
            "price": 200,
            "value": 2000,
            "history_account_id": "test-brokerage",
        }
    ]
    sid = write_snapshot(
        db,
        captured_at="t",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-brokerage": 2000.0},
        holdings=holdings,
    )

    sec = db["securities"].get("sec-1")
    assert sec["ticker"] == "VTV"
    assert sec["name"] == "Vanguard Value ETF"

    hrow = list(db["holdings"].rows_where("snapshot_id = ?", [sid]))[0]
    assert hrow["security_id"] == "sec-1"
    assert hrow["quantity"] == 10
    assert hrow["price"] == 200
    assert hrow["value"] == 2000
    assert hrow["account_id"] == "test-brokerage"


def test_snapshot_replaces_weekly_same_week(db, sample_accounts_yaml):
    """Re-running weekly for the same week must not stack snapshots, and
    the balance rows from the first call must be gone."""
    from history import write_snapshot, sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1000.0},
        holdings=[],
    )
    sid2 = write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1500.0},
        holdings=[],
    )

    weekly = list(
        db["snapshots"].rows_where(
            "week_of = ? AND source = ?", ["2026-04-13", "weekly"]
        )
    )
    assert len(weekly) == 1
    assert weekly[0]["id"] == sid2
    assert weekly[0]["captured_at"] == "t2"

    # Total balance rows across all snapshots == exactly 1 (the new one).
    all_balances = list(db["balances"].rows)
    assert len(all_balances) == 1
    assert all_balances[0]["balance"] == 1500.0
    assert all_balances[0]["snapshot_id"] == sid2


def test_snapshot_stacks_manual_same_week(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-04-13",
        source="manual",
        balances={"test-checking": 100.0},
        holdings=[],
    )
    write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-04-13",
        source="manual",
        balances={"test-checking": 200.0},
        holdings=[],
    )
    manual = list(
        db["snapshots"].rows_where(
            "week_of = ? AND source = ?", ["2026-04-13", "manual"]
        )
    )
    assert len(manual) == 2


def test_write_snapshot_rolls_back_on_error(db, sample_accounts_yaml):
    """FK violation raises; transaction rolls back fully (no partial state)."""
    import sqlite3

    from history import write_snapshot, sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    with pytest.raises(sqlite3.IntegrityError):
        write_snapshot(
            db,
            captured_at="t",
            week_of="2026-04-13",
            source="weekly",
            balances={"nonexistent-id": 42},
            holdings=[],
        )
    assert list(db["snapshots"].rows) == []


def test_snapshot_aggregates_duplicate_holdings(db, sample_accounts_yaml):
    """Two Plaid holdings for the same (history_account_id, security_id) —
    e.g. a joint brokerage split across two Plaid account_ids — must be
    summed rather than colliding on the holdings PK."""
    from history import write_snapshot, sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    holdings = [
        {
            "security_id": "sec-1",
            "ticker": "VOO",
            "name": "Vanguard 500",
            "type": "etf",
            "quantity": 10,
            "price": 500,
            "value": 5000,
            "history_account_id": "test-brokerage",
        },
        {
            "security_id": "sec-1",
            "ticker": "VOO",
            "name": "Vanguard 500",
            "type": "etf",
            "quantity": 4,
            "price": 500,
            "value": 2000,
            "history_account_id": "test-brokerage",
        },
    ]
    sid = write_snapshot(
        db,
        captured_at="t",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-brokerage": 7000.0},
        holdings=holdings,
    )
    rows = list(db["holdings"].rows_where("snapshot_id = ?", [sid]))
    assert len(rows) == 1
    assert rows[0]["quantity"] == 14
    assert rows[0]["value"] == 7000
