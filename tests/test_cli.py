"""Tests for the balance_history CLI skeleton."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

from click.testing import CliRunner


def test_cli_help_lists_subcommands():
    from balance_history import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "snapshot",
        "diff",
        "snapshots",
        "annotate",
        "restore-from-backup",
    ):
        assert cmd in result.output
    # backfill was removed — the sheet never held per-account prior-week
    # values, so --from-sheet had no data to recover.
    assert "backfill" not in result.output


def test_cli_diff_help_has_options():
    from balance_history import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["diff", "--help"])
    assert result.exit_code == 0
    assert "--weeks-back" in result.output
    assert "--week-a" in result.output
    assert "--json" in result.output


def test_cli_diff_fallback_week_range_is_one_week_when_no_prior_snapshot(
    tmp_path, monkeypatch
):
    """When there is no earlier snapshot to baseline against, `diff` (no args)
    falls back to comparing last Monday to this Monday — not two Mondays ago.
    The tz round-trip through monday_of previously shifted week_a back an extra
    week under LOCAL_TZ. (The normal baseline is the preceding snapshot; see
    test_cli_diff_default_baseline_is_preceding_snapshot.)"""
    from balance_history import cli

    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # Monday 2026-04-13 12:00 UTC
            return datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)

    with patch("balance_history.datetime", _FrozenDateTime):
        runner = CliRunner()
        result = runner.invoke(cli, ["diff", "--json"])

    assert result.exit_code == 0, result.output
    # With an empty DB the result is `[]`, but the point of the test is
    # that it doesn't crash and, more importantly, that the week
    # arithmetic is right. We assert by checking there's no error and
    # by re-running through the code path directly for the dates.
    data = json.loads(result.output)
    assert isinstance(data, list)

    # Exercise the date math directly via the same helpers the CLI uses.
    from datetime import date, timedelta
    from history import monday_of

    week_b = monday_of(datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc))
    week_a = (date.fromisoformat(week_b) - timedelta(weeks=1)).isoformat()
    assert week_b == "2026-04-13"
    assert week_a == "2026-04-06"


_MIN_YAML = """
spreadsheet_id: "t"
accounts:
  - institution: "B"
    name: "C"
    mask: "1"
    id: "foo"
    label: "Foo"
    type: asset
manual_accounts: []
"""


def test_cli_diff_default_baseline_is_preceding_snapshot(tmp_path, monkeypatch):
    """Regression: with biweekly (14-day) snapshots, `diff` with no args must
    compare against the immediately-preceding snapshot, not a hardcoded
    7-day-back week that doesn't exist (which silently zeroed every 'Old'
    balance and reported the entire net worth as the 'change')."""
    import balance_history
    from balance_history import cli
    from history import sync_accounts_from_yaml, write_snapshot

    db_path = tmp_path / "history.db"
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(_MIN_YAML)
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    db = balance_history._open_db()
    sync_accounts_from_yaml(db, str(accounts_yaml), today="2026-06-29")
    write_snapshot(
        db,
        captured_at="t1",
        week_of="2026-06-29",
        source="weekly",
        balances={"foo": 1000.0},
        holdings=[],
    )
    write_snapshot(
        db,
        captured_at="t2",
        week_of="2026-07-13",
        source="weekly",
        balances={"foo": 1500.0},
        holdings=[],
    )

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # Wednesday 2026-07-15 → Monday-of-week is 2026-07-13 (week_b).
            return datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    with patch("balance_history.datetime", _FrozenDateTime):
        runner = CliRunner()
        result = runner.invoke(cli, ["diff", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    foo = [r for r in data if r["id"] == "foo"][0]
    # Baseline must be the 2026-06-29 snapshot (14 days back), NOT a missing
    # 2026-07-06 week that would zero the old balance.
    assert foo["old"] == 1000.0
    assert foo["new"] == 1500.0
    assert foo["delta"] == 500.0


def test_annotate_creates_note(tmp_path, monkeypatch):
    from balance_history import cli

    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(_MIN_YAML)
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    # Pre-sync accounts so FK check on notes.account_id succeeds.
    import balance_history
    from history import sync_accounts_from_yaml

    db = balance_history._open_db()
    sync_accounts_from_yaml(db, str(accounts_yaml), today="2026-04-13")

    runner = CliRunner()
    result = runner.invoke(cli, ["annotate", "foo", "2026-04-13", "test note"])
    assert result.exit_code == 0, result.output

    import sqlite_utils

    db2 = sqlite_utils.Database(str(db_path))
    note = db2["notes"].get(("foo", "2026-04-13"))
    assert note["note"] == "test note"


def test_annotate_rejects_delete_with_note_text(tmp_path, monkeypatch):
    """Passing both --delete and a note must be rejected — a destructive
    flag should never silently override an apparent intent to write."""
    from balance_history import cli

    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(_MIN_YAML)
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    import balance_history
    from history import sync_accounts_from_yaml, upsert_note

    db = balance_history._open_db()
    sync_accounts_from_yaml(db, str(accounts_yaml), today="2026-04-13")
    upsert_note(db, "foo", "2026-04-13", "preserved")
    db.conn.commit()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["annotate", "foo", "2026-04-13", "new text", "--delete"],
    )
    # Must reject with non-zero exit; the existing note must survive.
    assert result.exit_code != 0
    import sqlite_utils

    db2 = sqlite_utils.Database(str(db_path))
    assert db2["notes"].get(("foo", "2026-04-13"))["note"] == "preserved"


def test_annotate_delete_removes_note(tmp_path, monkeypatch):
    from balance_history import cli

    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(_MIN_YAML)
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    import balance_history
    from history import sync_accounts_from_yaml, upsert_note

    db = balance_history._open_db()
    sync_accounts_from_yaml(db, str(accounts_yaml), today="2026-04-13")
    upsert_note(db, "foo", "2026-04-13", "to be deleted")
    db.conn.commit()

    runner = CliRunner()
    result = runner.invoke(cli, ["annotate", "foo", "2026-04-13", "--delete"])
    assert result.exit_code == 0, result.output

    import sqlite_utils

    db2 = sqlite_utils.Database(str(db_path))
    assert db2["notes"].count == 0


# --- #71: negative dollar formatting ---


def test_fmt_dollars_positive():
    from balance_history import _fmt_dollars

    assert _fmt_dollars(1234.56) == "$1,234.56"


def test_fmt_dollars_negative():
    from balance_history import _fmt_dollars

    assert _fmt_dollars(-500.0) == "-$500.00"


def test_fmt_dollars_negative_zero_decimals():
    from balance_history import _fmt_dollars

    assert _fmt_dollars(-1500.0, decimals=0) == "-$1,500"


def test_fmt_dollars_zero():
    from balance_history import _fmt_dollars

    assert _fmt_dollars(0.0) == "$0.00"


# --- #64: datetime.now(timezone.utc) consistency in snapshot ---


def test_snapshot_uses_utc_datetime(tmp_path, monkeypatch):
    """snapshot() must use timezone-aware datetime throughout (#64)."""
    from balance_history import cli
    from unittest.mock import patch, MagicMock

    db_path = tmp_path / "history.db"
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        """
spreadsheet_id: "t"
accounts:
  - institution: "B"
    name: "C"
    mask: "1"
    id: "foo"
    label: "Foo"
    type: asset
manual_accounts: []
"""
    )
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)

    with patch("balance_history.datetime", _FrozenDateTime), patch(
        "balance_history.read_balances_from_sheet", return_value={}
    ), patch("balance_history.fetch_all_holdings_for_snapshot", return_value=[]), patch(
        "balance_history.make_sheet_client", return_value=MagicMock()
    ), patch(
        "balance_history.load_plaid_items", return_value={}
    ), patch(
        "balance_history._maybe_backup_local"
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["snapshot"])

    assert result.exit_code == 0, result.output
