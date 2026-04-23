"""Tests for sync_accounts_from_yaml."""


def test_sync_accounts_inserts_new_ids(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    ids = {row["id"] for row in db["accounts"].rows}
    assert ids == {"test-checking", "test-brokerage", "manual-asset"}


def test_sync_accounts_marks_manual_flag(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    by_id = {row["id"]: row for row in db["accounts"].rows}
    assert by_id["test-checking"]["is_manual"] == 0
    assert by_id["manual-asset"]["is_manual"] == 1


def test_sync_accounts_sets_first_seen_on_insert(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    for row in db["accounts"].rows:
        assert row["first_seen"] == "2026-04-13"
        assert row["retired_at"] is None


def test_sync_preserves_first_seen_on_reinsert(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-01-01")
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    by_id = {row["id"]: row for row in db["accounts"].rows}
    assert by_id["test-checking"]["first_seen"] == "2026-01-01"


def test_sync_accounts_retires_missing_ids(db, sample_accounts_yaml, tmp_path):
    from history import sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-01-01")
    # New yaml without test-brokerage
    new_yaml = tmp_path / "new_accounts.yaml"
    new_yaml.write_text(
        """
spreadsheet_id: "test"
accounts:
  - institution: "TestBank"
    name: "Checking"
    mask: "1234"
    id: "test-checking"
    label: "Test Checking"
    type: asset
"""
    )
    sync_accounts_from_yaml(db, str(new_yaml), today="2026-04-13")
    by_id = {row["id"]: row for row in db["accounts"].rows}
    assert by_id["test-brokerage"]["retired_at"] == "2026-04-13"
    assert by_id["test-checking"]["retired_at"] is None


def test_sync_accepts_duplicate_ids_in_yaml(db, tmp_path):
    """accounts.yaml is allowed to list the same id twice (joint accounts
    split across multiple Plaid entries; balances are summed onto one sheet
    row). The sync must dedupe by id rather than raising UNIQUE."""
    from history import sync_accounts_from_yaml

    dup = tmp_path / "dup.yaml"
    dup.write_text(
        """
spreadsheet_id: "t"
accounts:
  - institution: "TestBank"
    name: "Joint A"
    mask: "1111"
    id: "joint"
    label: "Joint"
    type: asset
  - institution: "TestBank"
    name: "Joint B"
    mask: "2222"
    id: "joint"
    label: "Joint"
    type: asset
manual_accounts: []
"""
    )
    sync_accounts_from_yaml(db, str(dup), today="2026-04-14")
    assert db["accounts"].count == 1
    assert db["accounts"].get("joint")["label"] == "Joint"


def test_sync_unretires_on_reappear(db, sample_accounts_yaml, tmp_path):
    """Adding back an account that was retired clears retired_at."""
    from history import sync_accounts_from_yaml

    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-01-01")
    # Retire one via a minimal yaml
    minimal = tmp_path / "min.yaml"
    minimal.write_text(
        """
spreadsheet_id: "test"
accounts: []
"""
    )
    sync_accounts_from_yaml(db, str(minimal), today="2026-02-01")
    assert db["accounts"].get("test-checking")["retired_at"] == "2026-02-01"
    # Add back via original
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-03-01")
    assert db["accounts"].get("test-checking")["retired_at"] is None
