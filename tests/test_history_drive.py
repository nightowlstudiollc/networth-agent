"""Tests for history_drive.upload_db_to_drive and restore_db_from_drive."""

from unittest.mock import MagicMock
import pytest


def test_upload_first_run_creates_file(db, tmp_path):
    from history_drive import upload_db_to_drive

    local = tmp_path / "history.db"
    local.write_bytes(b"fake")

    fake = MagicMock()
    fake.find_file.return_value = None
    fake.upload_file.return_value = {
        "id": "drive-id-1",
        "modifiedTime": "2026-04-13T18:00:00Z",
    }

    result = upload_db_to_drive(
        db,
        local_path=str(local),
        drive_client=fake,
        drive_folder_id="folder-1",
    )
    assert result["status"] == "uploaded"
    fake.upload_file.assert_called_once()
    row = db["sync_state"].get("last_drive_push_iso")
    assert row["value"] == "2026-04-13T18:00:00Z"


def test_upload_blocks_when_drive_is_newer(db, tmp_path):
    from history_drive import upload_db_to_drive

    local = tmp_path / "history.db"
    local.write_bytes(b"fake")
    db["sync_state"].insert(
        {
            "key": "last_drive_push_iso",
            "value": "2026-04-06T18:00:00Z",
        }
    )
    fake = MagicMock()
    fake.find_file.return_value = {
        "id": "d",
        "modifiedTime": "2026-04-10T18:00:00Z",
    }
    result = upload_db_to_drive(
        db,
        local_path=str(local),
        drive_client=fake,
        drive_folder_id="folder-1",
    )
    assert result["status"] == "blocked_stale_local"
    fake.upload_file.assert_not_called()


def test_upload_proceeds_when_drive_matches(db, tmp_path):
    from history_drive import upload_db_to_drive

    local = tmp_path / "history.db"
    local.write_bytes(b"fake")
    db["sync_state"].insert(
        {
            "key": "last_drive_push_iso",
            "value": "2026-04-06T18:00:00Z",
        }
    )
    fake = MagicMock()
    fake.find_file.return_value = {
        "id": "d",
        "modifiedTime": "2026-04-06T18:00:00Z",
    }
    fake.upload_file.return_value = {
        "id": "d",
        "modifiedTime": "2026-04-13T18:00:00Z",
    }
    result = upload_db_to_drive(
        db,
        local_path=str(local),
        drive_client=fake,
        drive_folder_id="folder-1",
    )
    assert result["status"] == "uploaded"
    row = db["sync_state"].get("last_drive_push_iso")
    assert row["value"] == "2026-04-13T18:00:00Z"


def test_upload_force_bypasses_staleness_check(db, tmp_path):
    from history_drive import upload_db_to_drive

    local = tmp_path / "history.db"
    local.write_bytes(b"fake")
    db["sync_state"].insert(
        {
            "key": "last_drive_push_iso",
            "value": "2026-04-06T18:00:00Z",
        }
    )
    fake = MagicMock()
    fake.find_file.return_value = {
        "id": "d",
        "modifiedTime": "2026-04-10T18:00:00Z",
    }
    fake.upload_file.return_value = {
        "id": "d",
        "modifiedTime": "2026-04-13T18:00:00Z",
    }
    result = upload_db_to_drive(
        db,
        local_path=str(local),
        drive_client=fake,
        drive_folder_id="folder-1",
        force=True,
    )
    assert result["status"] == "uploaded"


def test_restore_refuses_if_local_exists(tmp_path):
    from history_drive import restore_db_from_drive

    local = tmp_path / "history.db"
    local.write_bytes(b"existing")
    fake = MagicMock()
    with pytest.raises(FileExistsError):
        restore_db_from_drive(
            local_path=str(local),
            drive_client=fake,
            drive_folder_id="folder-1",
        )


def test_restore_force_overwrites(tmp_path):
    """Restore overwrites existing local DB with integrity-verified bytes."""
    import sqlite3

    from history_drive import restore_db_from_drive

    # Build a valid SQLite file in memory, then read its bytes as the
    # "downloaded" payload. This exercises the integrity check path.
    valid_db_path = tmp_path / "valid.db"
    conn = sqlite3.connect(str(valid_db_path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    valid_bytes = valid_db_path.read_bytes()

    local = tmp_path / "history.db"
    local.write_bytes(b"old content")

    fake = MagicMock()
    fake.download_file.return_value = valid_bytes
    fake.find_file.return_value = {"id": "d"}
    restore_db_from_drive(
        local_path=str(local),
        drive_client=fake,
        drive_folder_id="folder-1",
        force=True,
    )
    # Restored file should have the valid content — not a bytes equality
    # check (atomic rename may change exact bytes), but table should exist.
    conn = sqlite3.connect(str(local))
    assert conn.execute("SELECT x FROM t").fetchone() == (1,)
    conn.close()


def test_restore_rejects_corrupt_download(tmp_path):
    """Invalid SQLite bytes fail integrity check; local DB is unchanged."""
    import sqlite3

    from history_drive import restore_db_from_drive

    local = tmp_path / "history.db"
    local.write_bytes(b"existing")

    fake = MagicMock()
    fake.download_file.return_value = b"not a sqlite file"
    fake.find_file.return_value = {"id": "d"}
    with pytest.raises(sqlite3.DatabaseError):
        restore_db_from_drive(
            local_path=str(local),
            drive_client=fake,
            drive_folder_id="folder-1",
            force=True,
        )
    # Existing local DB untouched.
    assert local.read_bytes() == b"existing"
