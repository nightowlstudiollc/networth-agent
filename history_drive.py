"""Google Drive backup for history.db with staleness check.

DriveClient is a Protocol so tests can mock it. The production adapter
(GoogleDriveAdapter) wraps google-api-python-client.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlite_utils.db import Database, NotFoundError


class DriveClient(Protocol):
    def find_file(self, folder_id: str, name: str) -> dict | None: ...

    def upload_file(self, folder_id: str, name: str, path: str) -> dict: ...

    def download_file(self, file_id: str) -> bytes: ...


FILENAME = "history.db"


def _get_sync_value(db: Database, key: str) -> str | None:
    """Return sync_state[key].value, or None if the key is absent.

    Only swallows NotFoundError — real errors (corruption, schema mismatch,
    permission issues) propagate so they surface rather than being silently
    treated as "no prior sync."
    """
    try:
        row = db["sync_state"].get(key)
        return row["value"]
    except NotFoundError:
        return None


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp; Drive's trailing 'Z' needs translation."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def upload_db_to_drive(
    db: Database,
    local_path: str,
    drive_client: DriveClient,
    drive_folder_id: str,
    force: bool = False,
) -> dict:
    """Upload history.db to Drive after verifying no remote drift.

    Returns {status: "uploaded", drive_id, modifiedTime} on success, or
    {status: "blocked_stale_local", drive_modified_time, last_push} when
    remote is newer than our last push and force=False.

    Staleness comparison parses modifiedTime into datetime before
    comparing — string comparison would be fragile across fractional-
    second precision differences.
    """
    remote = drive_client.find_file(drive_folder_id, FILENAME)
    last_push = _get_sync_value(db, "last_drive_push_iso")

    if remote and not force:
        is_stale = last_push is None or (
            _parse_iso(remote["modifiedTime"]) > _parse_iso(last_push)
        )
        if is_stale:
            return {
                "status": "blocked_stale_local",
                "drive_modified_time": remote["modifiedTime"],
                "last_push": last_push,
            }

    result = drive_client.upload_file(drive_folder_id, FILENAME, local_path)
    db["sync_state"].insert(
        {"key": "last_drive_push_iso", "value": result["modifiedTime"]},
        replace=True,
    )
    db["sync_state"].insert(
        {"key": "last_drive_push_host", "value": socket.gethostname()},
        replace=True,
    )
    return {
        "status": "uploaded",
        "drive_id": result["id"],
        "modifiedTime": result["modifiedTime"],
    }


def restore_db_from_drive(
    local_path: str,
    drive_client: DriveClient,
    drive_folder_id: str,
    force: bool = False,
) -> None:
    """Download history.db from Drive to local_path.

    Refuses if local_path already exists unless force=True. Raises
    FileNotFoundError if no Drive file to pull. Raises sqlite3.DatabaseError
    if the downloaded bytes don't pass an integrity check (the file is
    discarded; existing local DB, if any, is not touched).

    Writes to a temp file in the same directory, runs PRAGMA
    integrity_check, then atomically renames into place. A process kill
    mid-write can't corrupt the existing local DB.
    """
    p = Path(local_path)
    if p.exists() and not force:
        raise FileExistsError(
            f"{local_path} already exists. Pass force=True to overwrite."
        )
    remote = drive_client.find_file(drive_folder_id, FILENAME)
    if remote is None:
        raise FileNotFoundError(
            f"No {FILENAME} found in Drive folder {drive_folder_id}"
        )
    content = drive_client.download_file(remote["id"])

    # Write to temp in the same directory (so os.replace is atomic on POSIX),
    # integrity-check, then atomic rename.
    parent = p.parent if str(p.parent) else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{p.name}.", suffix=".download", dir=str(parent)
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        _verify_sqlite_integrity(tmp_name)
        os.replace(tmp_name, str(p))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _verify_sqlite_integrity(path: str) -> None:
    """Raise sqlite3.DatabaseError if the file at path is not a valid SQLite DB."""
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"integrity_check failed for {path}: {result}")
    finally:
        conn.close()


class GoogleDriveAdapter:
    """Implements DriveClient over googleapiclient's drive v3 service.

    Construct the service separately and inject — makes testing and auth
    scope selection explicit. See google_drive_client.build_drive_adapter
    for the production entry point.
    """

    def __init__(self, service):
        self.service = service

    def find_file(self, folder_id: str, name: str) -> dict | None:
        # Escape backslashes and single quotes per Drive query syntax.
        # Prevents malformed queries or injection via filenames with '.
        safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
        safe_folder = folder_id.replace("\\", "\\\\").replace("'", "\\'")
        q = f"'{safe_folder}' in parents and name='{safe_name}' " f"and trashed=false"
        # Server-side sort + pageSize=1 is authoritative; avoids relying on
        # client-side string ordering of modifiedTime.
        resp = (
            self.service.files()
            .list(
                q=q,
                fields="files(id, modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=1,
            )
            .execute()
        )
        files = resp.get("files", [])
        return files[0] if files else None

    def upload_file(self, folder_id: str, name: str, path: str) -> dict:
        from googleapiclient.http import MediaFileUpload

        existing = self.find_file(folder_id, name)
        media = MediaFileUpload(path, mimetype="application/x-sqlite3")
        if existing:
            return (
                self.service.files()
                .update(
                    fileId=existing["id"],
                    media_body=media,
                    fields="id, modifiedTime",
                )
                .execute()
            )
        body = {"name": name, "parents": [folder_id]}
        return (
            self.service.files()
            .create(
                body=body,
                media_body=media,
                fields="id, modifiedTime",
            )
            .execute()
        )

    def download_file(self, file_id: str) -> bytes:
        import io

        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()
