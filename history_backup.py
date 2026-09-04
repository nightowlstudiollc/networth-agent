"""Local file-system backup for history.db."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import yaml


def _load_backup_dir() -> str:
    """Return local_backup_dir from config.yaml, or raise FileNotFoundError."""
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        raise FileNotFoundError("config.yaml not found")
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    backup_dir = cfg.get("local_backup_dir", "")
    if not backup_dir:
        raise FileNotFoundError("local_backup_dir not configured in config.yaml")
    return backup_dir


def backup_db_local(db_path: str) -> str:
    """Copy db_path into local_backup_dir. Returns destination path.

    Checkpoints WAL before copying so the backup is a single self-contained
    file. Uses atomic write (copy to temp, then rename) to avoid destroying
    the previous backup on crash or full disk.

    Raises FileNotFoundError if backup dir is not configured or db_path
    does not exist.
    """
    backup_dir = _load_backup_dir()
    if not Path(db_path).exists():
        raise FileNotFoundError(f"{db_path} does not exist")
    _checkpoint_wal(db_path)
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, "history.db")
    fd, tmp = tempfile.mkstemp(dir=backup_dir, suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(db_path, tmp)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def restore_db_local(db_path: str, *, force: bool = False) -> None:
    """Copy history.db from local_backup_dir to db_path.

    Uses atomic write to avoid corrupting existing db_path on interruption.

    Raises FileExistsError if db_path exists and force is False.
    Raises FileNotFoundError if backup file does not exist.
    Raises sqlite3.DatabaseError if backup file fails integrity check.
    """
    backup_dir = _load_backup_dir()
    src = os.path.join(backup_dir, "history.db")
    if not Path(src).exists():
        raise FileNotFoundError(f"No backup found at {src}")
    if Path(db_path).exists() and not force:
        raise FileExistsError(f"{db_path} already exists; pass --force to overwrite")
    _verify_sqlite_integrity(src)
    dest_dir = str(Path(db_path).parent)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, db_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _checkpoint_wal(db_path: str) -> None:
    """Flush WAL journal into the main database file."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _verify_sqlite_integrity(path: str) -> None:
    """Raise sqlite3.DatabaseError if file is empty or not a valid SQLite DB."""
    if Path(path).stat().st_size == 0:
        raise sqlite3.DatabaseError(f"empty file: {path}")
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"integrity_check failed for {path}: {result}")
    finally:
        conn.close()
