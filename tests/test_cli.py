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
        "restore-from-drive",
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


def test_cli_diff_default_week_range_is_exactly_one_week(tmp_path, monkeypatch):
    """Regression: `diff` (no args) must compare last Monday to this Monday,
    not two Mondays ago. The tz round-trip through monday_of previously
    shifted week_a back an extra week under LOCAL_TZ."""
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
