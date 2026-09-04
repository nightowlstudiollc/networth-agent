"""Adapter: Google Sheets access for history.py.

Reads config.yaml for the service account path; uses google-api-python-client
for sheets reads and writes. Wraps the Sheets v4 values() endpoints.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from google.oauth2 import service_account
from googleapiclient.discovery import build


def _load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


class SheetsClient:
    def __init__(self, read_only: bool = False):
        cfg = _load_config()
        gs = cfg.get("google_sheets", cfg)
        scope = (
            "https://www.googleapis.com/auth/spreadsheets.readonly"
            if read_only
            else "https://www.googleapis.com/auth/spreadsheets"
        )
        creds = service_account.Credentials.from_service_account_file(
            gs["service_account_path"],
            scopes=[scope],
        )
        self.service = build("sheets", "v4", credentials=creds)

    def get_values(self, spreadsheet_id: str, range_: str) -> list[list]:
        resp = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_)
            .execute()
        )
        return resp.get("values", [])

    def batch_update_values(
        self, spreadsheet_id: str, value_ranges: list[dict]
    ) -> dict:
        # USER_ENTERED matches manual typing: leading "=" is parsed as a
        # formula. All current callers write numbers and the ✔️ literal,
        # neither of which is formula-like; do not pass user-controlled
        # strings here without sanitizing.
        body = {"valueInputOption": "USER_ENTERED", "data": value_ranges}
        return (
            self.service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )
