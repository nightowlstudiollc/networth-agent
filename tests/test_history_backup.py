"""Tests for history_backup: local backup and restore of history.db."""

import sqlite3
from pathlib import Path

import pytest

from history_backup import backup_db_local, restore_db_local


def _make_valid_db(path: Path) -> Path:
    """Create a minimal valid SQLite DB file."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def config_with_backup(tmp_path, monkeypatch):
    """Set up a config.yaml pointing local_backup_dir at a temp dir."""
    backup_dir = tmp_path / "backup"
    config = tmp_path / "config.yaml"
    config.write_text(f"local_backup_dir: '{backup_dir}'\n")
    monkeypatch.chdir(tmp_path)
    return backup_dir


def test_backup_creates_dir_and_copies(config_with_backup, tmp_path):
    db = _make_valid_db(tmp_path / "history.db")
    dest = backup_db_local(str(db))
    assert Path(dest).exists()
    assert Path(dest).read_bytes() == db.read_bytes()


def test_backup_overwrites_existing(config_with_backup, tmp_path):
    backup_dir = config_with_backup
    backup_dir.mkdir(parents=True)
    (backup_dir / "history.db").write_bytes(b"old")
    db = _make_valid_db(tmp_path / "history.db")
    backup_db_local(str(db))
    assert (backup_dir / "history.db").read_bytes() == db.read_bytes()


def test_backup_raises_when_not_configured(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("local_backup_dir: ''\n")
    monkeypatch.chdir(tmp_path)
    _make_valid_db(tmp_path / "history.db")
    with pytest.raises(FileNotFoundError, match="not configured"):
        backup_db_local(str(tmp_path / "history.db"))


def test_backup_raises_when_no_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_valid_db(tmp_path / "history.db")
    with pytest.raises(FileNotFoundError, match="config.yaml not found"):
        backup_db_local(str(tmp_path / "history.db"))


def test_backup_raises_when_db_missing(config_with_backup, tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        backup_db_local(str(tmp_path / "nonexistent.db"))


def test_restore_copies_to_local(config_with_backup, tmp_path):
    backup_dir = config_with_backup
    backup_dir.mkdir(parents=True)
    src = _make_valid_db(backup_dir / "history.db")
    dest = tmp_path / "restored.db"
    restore_db_local(str(dest))
    assert dest.read_bytes() == src.read_bytes()


def test_restore_refuses_existing_without_force(config_with_backup, tmp_path):
    backup_dir = config_with_backup
    backup_dir.mkdir(parents=True)
    _make_valid_db(backup_dir / "history.db")
    local = tmp_path / "restored.db"
    local.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        restore_db_local(str(local))


def test_restore_force_overwrites(config_with_backup, tmp_path):
    backup_dir = config_with_backup
    backup_dir.mkdir(parents=True)
    _make_valid_db(backup_dir / "history.db")
    local = tmp_path / "restored.db"
    local.write_bytes(b"old data")
    restore_db_local(str(local), force=True)
    conn = sqlite3.connect(str(local))
    assert conn.execute("SELECT x FROM t").fetchone() == (1,)
    conn.close()


def test_restore_rejects_corrupt_backup(config_with_backup, tmp_path):
    backup_dir = config_with_backup
    backup_dir.mkdir(parents=True)
    (backup_dir / "history.db").write_bytes(b"not a database")
    dest = tmp_path / "restored.db"
    with pytest.raises(sqlite3.DatabaseError):
        restore_db_local(str(dest))
    assert not dest.exists()


def test_restore_rejects_empty_backup(config_with_backup, tmp_path):
    backup_dir = config_with_backup
    backup_dir.mkdir(parents=True)
    (backup_dir / "history.db").write_bytes(b"")
    dest = tmp_path / "restored.db"
    with pytest.raises(sqlite3.DatabaseError, match="empty file"):
        restore_db_local(str(dest))


def test_restore_raises_when_no_backup_file(config_with_backup, tmp_path):
    backup_dir = config_with_backup
    backup_dir.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="No backup found"):
        restore_db_local(str(tmp_path / "doesnotexist.db"))
