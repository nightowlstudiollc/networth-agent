"""Tests for GoogleDriveAdapter.find_file (lightweight — upload and
download use real Drive APIs and are integration-tested manually)."""

from unittest.mock import MagicMock


def test_adapter_find_file_returns_first_match():
    from history_drive import GoogleDriveAdapter

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "fid-1", "modifiedTime": "2026-04-13T00:00:00Z"}]
    }
    adapter = GoogleDriveAdapter(service=mock_service)
    result = adapter.find_file("folder-1", "history.db")
    assert result == {
        "id": "fid-1",
        "modifiedTime": "2026-04-13T00:00:00Z",
    }


def test_adapter_find_file_returns_none_when_absent():
    from history_drive import GoogleDriveAdapter

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": []}
    adapter = GoogleDriveAdapter(service=mock_service)
    assert adapter.find_file("folder-1", "history.db") is None


def test_adapter_find_file_passes_orderby_desc_to_drive():
    """Server-side orderBy ensures we get the newest match. We trust
    Drive to sort correctly; the adapter just passes the parameters."""
    from history_drive import GoogleDriveAdapter

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "newest", "modifiedTime": "2026-04-13T00:00:00Z"}]
    }
    adapter = GoogleDriveAdapter(service=mock_service)
    result = adapter.find_file("folder-1", "history.db")

    assert result["id"] == "newest"
    # Verify orderBy was passed — the server-side sort is the whole point.
    _, kwargs = mock_service.files().list.call_args
    assert kwargs.get("orderBy") == "modifiedTime desc"
    assert kwargs.get("pageSize") == 1


def test_adapter_find_file_escapes_filename_with_quote():
    """Filename containing ' must be escaped to avoid query breakage."""
    from history_drive import GoogleDriveAdapter

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": []}
    adapter = GoogleDriveAdapter(service=mock_service)
    adapter.find_file("folder-1", "it's.db")

    _, kwargs = mock_service.files().list.call_args
    assert "\\'" in kwargs["q"]
    # The literal unescaped ' between t and s must NOT appear in the query
    # (it would end the string literal prematurely).
    assert "name='it\\'s.db'" in kwargs["q"]


def test_adapter_upload_creates_new_file_when_absent(tmp_path):
    from history_drive import GoogleDriveAdapter

    fake_db = tmp_path / "x.db"
    fake_db.write_bytes(b"fake")

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.return_value = {
        "id": "new-id",
        "modifiedTime": "2026-04-13T00:00:00Z",
    }
    adapter = GoogleDriveAdapter(service=mock_service)
    result = adapter.upload_file("folder-1", "history.db", str(fake_db))

    assert result["id"] == "new-id"
    mock_service.files().create.assert_called()
    mock_service.files().update.assert_not_called()


def test_adapter_upload_updates_existing_file(tmp_path):
    from history_drive import GoogleDriveAdapter

    fake_db = tmp_path / "x.db"
    fake_db.write_bytes(b"fake")

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "existing", "modifiedTime": "2026-04-06T00:00:00Z"}]
    }
    mock_service.files().update().execute.return_value = {
        "id": "existing",
        "modifiedTime": "2026-04-13T00:00:00Z",
    }
    adapter = GoogleDriveAdapter(service=mock_service)
    result = adapter.upload_file("folder-1", "history.db", str(fake_db))

    assert result["id"] == "existing"
    mock_service.files().update.assert_called()
