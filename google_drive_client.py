"""Adapter: Google Drive access for history_drive.py."""

from __future__ import annotations

from pathlib import Path

import yaml
from google.oauth2 import service_account
from googleapiclient.discovery import build

from history_drive import GoogleDriveAdapter


def _load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


def load_drive_folder_id() -> str:
    cfg = _load_config()
    gs = cfg.get("google_sheets", cfg)
    return gs["drive_folder_id"]


def build_drive_adapter() -> GoogleDriveAdapter:
    cfg = _load_config()
    gs = cfg.get("google_sheets", cfg)
    creds = service_account.Credentials.from_service_account_file(
        gs["service_account_path"],
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    service = build("drive", "v3", credentials=creds)
    return GoogleDriveAdapter(service=service)
