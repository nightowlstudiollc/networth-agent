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
