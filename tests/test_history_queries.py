def test_weekly_diff_basic(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")

    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-04-06",
        source="weekly",
        balances={"test-checking": 1000.0, "test-brokerage": 50000.0},
        holdings=[],
    )
    write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1500.0, "test-brokerage": 51000.0},
        holdings=[],
    )

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    by_id = {r["id"]: r for r in result}
    assert by_id["test-checking"]["delta"] == 500
    assert by_id["test-brokerage"]["delta"] == 1000


def test_weekly_diff_returns_zero_for_unchanged(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")

    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-04-06",
        source="weekly",
        balances={"test-checking": 1000.0},
        holdings=[],
    )
    write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1000.0},
        holdings=[],
    )

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    assert result[0]["delta"] == 0


def test_weekly_diff_includes_holdings_decomposition(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")

    h_old = [
        {
            "security_id": "s1",
            "ticker": "X",
            "name": "X",
            "type": "etf",
            "quantity": 100,
            "price": 50,
            "value": 5000,
            "history_account_id": "test-brokerage",
        }
    ]
    h_new = [
        {
            "security_id": "s1",
            "ticker": "X",
            "name": "X",
            "type": "etf",
            "quantity": 120,
            "price": 55,
            "value": 6600,
            "history_account_id": "test-brokerage",
        }
    ]

    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-04-06",
        source="weekly",
        balances={"test-brokerage": 5000.0},
        holdings=h_old,
    )
    write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-brokerage": 6600.0},
        holdings=h_new,
    )

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    b = [r for r in result if r["id"] == "test-brokerage"][0]
    assert b["delta"] == 1600
    assert b["market"] == 500  # 100 × (55-50)
    assert b["flow"] == 1100  # (120-100) × 55


def test_weekly_diff_attaches_note_from_notes_table(db, sample_accounts_yaml):
    from history import (
        sync_accounts_from_yaml,
        write_snapshot,
        weekly_diff,
        upsert_note,
    )

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")
    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-04-06",
        source="weekly",
        balances={"test-checking": 1000.0},
        holdings=[],
    )
    write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1500.0},
        holdings=[],
    )
    upsert_note(db, "test-checking", "2026-04-13", "paycheck")

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    b = [r for r in result if r["id"] == "test-checking"][0]
    assert b["note"] == "paycheck"


def test_weekly_diff_zero_balance_is_not_conflated_with_null(db, sample_accounts_yaml):
    """A genuine $0.00 old balance must not be treated the same as NULL."""
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")
    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-04-06",
        source="weekly",
        balances={"test-checking": 0.0},
        holdings=[],
    )
    write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 500.0},
        holdings=[],
    )

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    by_id = {r["id"]: r for r in result}
    assert by_id["test-checking"]["old"] == 0.0
    assert by_id["test-checking"]["new"] == 500.0
    assert by_id["test-checking"]["delta"] == 500.0


def test_delete_note_removes_existing_note(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, upsert_note, delete_note

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")
    upsert_note(db, "test-checking", "2026-04-06", "some note")
    assert db["notes"].count == 1

    delete_note(db, "test-checking", "2026-04-06")
    assert db["notes"].count == 0


def test_preceding_snapshot_week_returns_latest_before(db, sample_accounts_yaml):
    """The diff baseline follows the actual snapshot cadence: pick the most
    recent snapshot strictly before the target week, whatever the gap."""
    from history import (
        sync_accounts_from_yaml,
        write_snapshot,
        preceding_snapshot_week,
    )

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-06-01")
    for wk in ("2026-06-01", "2026-06-15", "2026-06-29"):
        write_snapshot(
            db,
            captured_at=wk,
            week_of=wk,
            source="weekly",
            balances={"test-checking": 1.0},
            holdings=[],
        )

    # Exact match to an existing week returns the one before it.
    assert preceding_snapshot_week(db, "2026-06-29") == "2026-06-15"
    # A target week with no snapshot returns the nearest earlier snapshot,
    # not a fixed 7-day-back week that may not exist.
    assert preceding_snapshot_week(db, "2026-07-13") == "2026-06-29"
    # Nothing earlier than the first snapshot.
    assert preceding_snapshot_week(db, "2026-06-01") is None


def test_list_snapshots_returns_most_recent_first(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, list_snapshots

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")
    write_snapshot(
        db,
        captured_at="2026-04-06T18:00:00Z",
        week_of="2026-04-06",
        source="weekly",
        balances={"test-checking": 1.0},
        holdings=[],
    )
    write_snapshot(
        db,
        captured_at="2026-04-13T18:00:00Z",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 2.0},
        holdings=[],
    )
    rows = list_snapshots(db, limit=10)
    assert len(rows) == 2
    assert rows[0]["week_of"] == "2026-04-13"
    assert rows[1]["week_of"] == "2026-04-06"
