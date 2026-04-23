#!/usr/bin/env python
"""CLI for balance history queries and capture.

See docs/plans/2026-04-13-balance-history-design.md for the design.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import click
import sqlite_utils

from history import (
    init_schema,
    sync_accounts_from_yaml,
    write_snapshot,
    monday_of,
    resolve_holdings_account_ids,
)
from history_sheet import read_balances_from_sheet
from history_drive import upload_db_to_drive
from rich.console import Console
from rich.table import Table

console = Console()


DB_PATH_ENV = "HISTORY_DB_PATH"
YAML_ENV = "ACCOUNTS_YAML_PATH"


def _db_path() -> str:
    return os.environ.get(DB_PATH_ENV, "history.db")


def _yaml_path() -> str:
    return os.environ.get(YAML_ENV, "accounts.yaml")


def _open_db():
    db = sqlite_utils.Database(_db_path())
    db.conn.execute("PRAGMA foreign_keys = ON")
    init_schema(db)
    return db


def make_sheet_client():
    """Return an object with a get_values(spreadsheet_id, range_) method."""
    from google_sheets_client import SheetsClient

    return SheetsClient(read_only=True)


def load_plaid_items() -> dict:
    import json

    p = Path(".plaid_items.json")
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def fetch_all_holdings_for_snapshot(items: dict) -> list[dict]:
    """Thin wrapper around plaid_balance.fetch_all_holdings for testability."""
    from plaid_balance import fetch_all_holdings

    return fetch_all_holdings(items)


@click.group()
def cli():
    """Balance history — capture and query weekly net-worth snapshots."""
    pass


@cli.command()
@click.option(
    "--source",
    default="weekly",
    type=click.Choice(["weekly", "manual"]),
)
def snapshot(source):
    """Capture a snapshot of current sheet balances + Plaid holdings."""
    import yaml

    db = _open_db()

    yaml_path = _yaml_path()
    sync_accounts_from_yaml(db, yaml_path, today=datetime.now().strftime("%Y-%m-%d"))

    # Load yaml to get spreadsheet_id + account mapping for holdings resolution
    with open(yaml_path) as f:
        yaml_data = yaml.safe_load(f)
    spreadsheet_id = yaml_data["spreadsheet_id"]
    yaml_accounts = yaml_data.get("accounts", [])

    # Read balances from sheet
    sheet_client = make_sheet_client()
    balances = read_balances_from_sheet(sheet_client, spreadsheet_id, "Net Worth")
    click.echo(f"Read {len(balances)} balances from sheet.")

    # Fetch fresh holdings from Plaid
    items = load_plaid_items()
    raw_holdings = fetch_all_holdings_for_snapshot(items)
    holdings = resolve_holdings_account_ids(raw_holdings, yaml_accounts)
    click.echo(f"Fetched {len(holdings)} holdings from Plaid.")

    # Write snapshot
    now = datetime.now(timezone.utc)
    snapshot_id = write_snapshot(
        db,
        captured_at=now.isoformat(),
        week_of=monday_of(now),
        source=source,
        balances=balances,
        holdings=holdings,
    )
    click.echo(f"Snapshot {snapshot_id} written for week {monday_of(now)}.")

    # Drive backup (optional — only if config exists)
    _maybe_backup_to_drive(db)


def _maybe_backup_to_drive(db):
    """Try Drive backup; log failures but don't abort."""
    try:
        from google_drive_client import (
            build_drive_adapter,
            load_drive_folder_id,
        )
    except ImportError:
        return
    try:
        adapter = build_drive_adapter()
        folder_id = load_drive_folder_id()
        result = upload_db_to_drive(db, _db_path(), adapter, folder_id)
        click.echo(f"Drive backup: {result['status']}")
    except Exception as e:
        click.echo(f"Drive backup failed (non-fatal): {e}", err=True)


@cli.command()
@click.option("--weeks-back", type=int, default=1)
@click.option("--week-a", type=str, default=None)
@click.option("--week-b", type=str, default=None)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit JSON instead of a table.",
)
def diff(weeks_back, week_a, week_b, as_json):
    """Show per-account delta between two weekly snapshots."""
    from datetime import date, timedelta
    from history import LOCAL_TZ, weekly_diff

    db = _open_db()
    # Resolve weeks. User-supplied dates are interpreted in LOCAL_TZ at noon
    # (safely inside the day) so monday_of's tz conversion can't spill into
    # the previous day. Default week_a is computed at the date level from
    # the already-normalized week_b Monday string — no tz round-trip.
    if not week_b:
        week_b = monday_of(datetime.now(timezone.utc))
    else:
        week_b = monday_of(
            datetime.fromisoformat(week_b).replace(hour=12, tzinfo=LOCAL_TZ)
        )
    if not week_a:
        week_a = (date.fromisoformat(week_b) - timedelta(weeks=weeks_back)).isoformat()
    else:
        week_a = monday_of(
            datetime.fromisoformat(week_a).replace(hour=12, tzinfo=LOCAL_TZ)
        )

    rows = weekly_diff(db, week_a, week_b)
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)

    if as_json:
        import json as _json

        click.echo(_json.dumps(rows, default=str, indent=2))
        return

    table = Table(title=f"Δ  {week_a}  →  {week_b}")
    for col in ("Label", "Old", "New", "Δ", "Market", "Flow", "Note"):
        table.add_column(col)
    total_delta = 0.0
    for r in rows:
        total_delta += r["delta"]
        if r["delta"] > 0:
            delta_str = f"[green]+${r['delta']:,.2f}[/green]"
        elif r["delta"] < 0:
            delta_str = f"[red]${r['delta']:,.2f}[/red]"
        else:
            delta_str = "—"
        market = f"${r['market']:,.0f}" if r["market"] is not None else ""
        flow = f"${r['flow']:,.0f}" if r["flow"] is not None else ""
        table.add_row(
            r["label"],
            f"${r['old']:,.2f}",
            f"${r['new']:,.2f}",
            delta_str,
            market,
            flow,
            r["note"] or "",
        )
    table.add_section()
    if total_delta > 0:
        total_str = f"[green]+${total_delta:,.2f}[/green]"
    elif total_delta < 0:
        total_str = f"[red]${total_delta:,.2f}[/red]"
    else:
        total_str = "—"
    table.add_row("[bold]Net change[/bold]", "", "", total_str, "", "", "")
    console.print(table)


@cli.command()
@click.option("--limit", type=int, default=10)
def snapshots(limit):
    """List recent snapshots."""
    from history import list_snapshots

    db = _open_db()
    rows = list_snapshots(db, limit)
    table = Table(title="Recent snapshots")
    for col in ("ID", "Week of", "Captured at", "Source"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), r["week_of"], r["captured_at"], r["source"])
    console.print(table)


@cli.command()
@click.argument("account_id")
@click.argument("week_of")
@click.argument("note", required=False)
@click.option("--delete", is_flag=True)
def annotate(account_id, week_of, note, delete):
    """Add, replace, or delete a note for an (account, week)."""
    from history import upsert_note, delete_note

    # --delete is destructive; silently ignoring a note text alongside it
    # would hide the user's intent. Require one or the other, never both.
    if delete and note:
        raise click.UsageError(
            "Pass either a note argument OR --delete, not both. "
            "--delete removes the existing note; a note argument replaces it."
        )

    db = _open_db()
    if delete:
        delete_note(db, account_id, week_of)
        click.echo(f"Deleted note for {account_id} @ {week_of}")
        return
    if not note:
        raise click.UsageError("Provide a note argument or pass --delete")
    upsert_note(db, account_id, week_of, note)
    click.echo(f"Set note for {account_id} @ {week_of}: {note}")


@cli.command(name="restore-from-drive")
@click.option("--force", is_flag=True)
def restore_from_drive(force):
    """Download history.db from Drive. Refuses existing local DB without --force."""
    from history_drive import restore_db_from_drive
    from google_drive_client import build_drive_adapter, load_drive_folder_id

    adapter = build_drive_adapter()
    folder_id = load_drive_folder_id()
    try:
        restore_db_from_drive(
            local_path=_db_path(),
            drive_client=adapter,
            drive_folder_id=folder_id,
            force=force,
        )
        click.echo(f"Restored history.db to {_db_path()}")
    except FileExistsError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Pass --force to overwrite.", err=True)
        raise click.Abort()


if __name__ == "__main__":
    cli()
