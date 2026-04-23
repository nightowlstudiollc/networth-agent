from unittest.mock import patch, MagicMock
from click.testing import CliRunner


def test_snapshot_command_calls_write_snapshot(tmp_path, monkeypatch):
    """Integration-ish: mocks at the edges, verifies the full call flow."""
    from balance_history import cli

    # Set up a temp DB path and accounts.yaml
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))

    # Minimal accounts.yaml
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        """
spreadsheet_id: "test"
accounts:
  - institution: "TestBank"
    name: "Checking"
    mask: "1234"
    id: "test-checking"
    label: "Test Checking"
    type: asset
manual_accounts: []
"""
    )
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    # Mock the sheet-reader and holdings-fetcher
    with patch("balance_history.read_balances_from_sheet") as mock_sheet, patch(
        "balance_history.fetch_all_holdings_for_snapshot"
    ) as mock_holdings, patch(
        "balance_history.upload_db_to_drive"
    ) as mock_drive, patch(
        "balance_history.make_sheet_client"
    ) as mock_sheet_client, patch(
        "balance_history.load_plaid_items"
    ) as mock_items:
        mock_sheet.return_value = {"test-checking": 1234.56}
        mock_holdings.return_value = []
        mock_drive.return_value = {"status": "uploaded"}
        mock_items.return_value = {}
        mock_sheet_client.return_value = MagicMock()

        runner = CliRunner()
        result = runner.invoke(cli, ["snapshot"])

    assert result.exit_code == 0, result.output
    assert db_path.exists()
    # Snapshot written
    import sqlite_utils

    db = sqlite_utils.Database(str(db_path))
    assert db["snapshots"].count == 1
    assert db["balances"].count == 1
