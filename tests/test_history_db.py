"""Tests for history.py schema initialization."""

import sqlite_utils


def test_init_schema_creates_all_tables():
    from history import init_schema

    db = sqlite_utils.Database(memory=True)
    init_schema(db)
    table_names = set(db.table_names())
    assert table_names == {
        "accounts",
        "snapshots",
        "balances",
        "holdings",
        "securities",
        "notes",
        "sync_state",
    }


def test_init_schema_is_idempotent():
    """Running init_schema twice must not error."""
    from history import init_schema

    db = sqlite_utils.Database(memory=True)
    init_schema(db)
    init_schema(db)
    assert "accounts" in db.table_names()


def test_snapshots_has_week_of_index():
    from history import init_schema

    db = sqlite_utils.Database(memory=True)
    init_schema(db)
    indexes = {i.name for i in db["snapshots"].indexes}
    assert "idx_snapshots_week" in indexes
