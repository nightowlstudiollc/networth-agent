"""Read balances from the Net Worth Google Sheet.

Rows are identified by the stable slug in column H; balance is in column B.
Rows without an ID in column H are skipped (subtotal, blank, section header).
"""

from __future__ import annotations

import re
from typing import Protocol


class SheetClient(Protocol):
    def get_values(self, spreadsheet_id: str, range_: str) -> list[list]: ...

    def batch_update_values(
        self, spreadsheet_id: str, value_ranges: list[dict]
    ) -> dict: ...


_STRIP_RE = re.compile(r"[^\d.\-]")


def _parse_balance(raw) -> float | None:
    """Parse a cell value into a float.

    Handles: numeric, '$ 1,234.56', '$ (25.99)' (accounting-style negative),
    '$ -   ' (zero), '' (None).
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s == "":
        return None
    # Dash alone (possibly surrounded by $ and spaces) = zero
    core = s.replace("$", "").replace(" ", "")
    if core in ("-", ""):
        return 0.0
    is_negative = "(" in s and ")" in s
    cleaned = _STRIP_RE.sub("", s)
    if cleaned in ("", "-"):
        return 0.0
    value = float(cleaned)
    return -abs(value) if is_negative else value


def read_balances_from_sheet(
    client: SheetClient,
    spreadsheet_id: str,
    tab_name: str,
) -> dict[str, float]:
    """Read all balances from the sheet, keyed by column-H ID."""
    rows = client.get_values(spreadsheet_id, f"{tab_name}!A1:H200")
    result: dict[str, float] = {}
    for i, row in enumerate(rows):
        if i == 0:
            continue  # header
        if len(row) < 8:
            continue
        account_id = (row[7] or "").strip()
        if not account_id:
            continue
        balance = _parse_balance(row[1])
        if balance is None:
            continue
        result[account_id] = balance
    return result


CHECKMARK = "✔️"


def write_balances_to_sheet(
    client: SheetClient,
    spreadsheet_id: str,
    tab_name: str,
    balances: dict[str, float],
) -> None:
    """Write balances into column B and a checkmark into column C.

    Rows are resolved by matching the `balances` keys against column H.
    Raises KeyError if any id is missing from the sheet — callers should
    filter unmapped ids before calling.
    """
    if not balances:
        return
    rows = client.get_values(spreadsheet_id, f"{tab_name}!A:H")
    id_to_row: dict[str, int] = {}
    for i, row in enumerate(rows):
        if i == 0 or len(row) < 8:
            continue
        rid = (row[7] or "").strip()
        if rid:
            id_to_row[rid] = i + 1  # sheet rows are 1-indexed

    value_ranges = []
    for rid, value in balances.items():
        if rid not in id_to_row:
            raise KeyError(rid)
        row_num = id_to_row[rid]
        value_ranges.append(
            {
                "range": f"{tab_name}!B{row_num}:C{row_num}",
                "values": [[value, CHECKMARK]],
            }
        )
    client.batch_update_values(spreadsheet_id, value_ranges)
