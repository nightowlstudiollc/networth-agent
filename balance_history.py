#!/usr/bin/env python3
"""CLI for balance history queries and capture.

Design notes live in docs/plans/ in the private repo (not published).
"""
from __future__ import annotations

import os

import op_bootstrap

op_bootstrap.bootstrap("PLAID_CLIENT_ID")

import sqlite3  # noqa: E402 (deferred: must run after venv+creds bootstrap)
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import click  # noqa: E402
import sqlite_utils  # noqa: E402

from history import (  # noqa: E402
    init_schema,
    sync_accounts_from_yaml,
    write_snapshot,
    monday_of,
    find_unregistered_ids,
    resolve_holdings_account_ids,
)
from history_sheet import read_balances_from_sheet  # noqa: E402
from history_backup import backup_db_local, restore_db_local  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()


def _fmt_dollars(value: float, decimals: int = 2) -> str:
    if value < 0:
        return f"-${abs(value):,.{decimals}f}"
    return f"${value:,.{decimals}f}"


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
    """Fetch all Plaid investment holdings for items with the investments product.

    Thin wrapper around plaid_balance.fetch_all_holdings for testability.

    Cost model: uses investments/holdings/get, which is subscription-billed
    (included in the monthly Investments add-on), NOT the per-call
    accounts/balance/get endpoint that costs $0.10/item. Safe to call on
    every snapshot run without incurring per-item charges.
    """
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
    with open(yaml_path) as f:
        yaml_data = yaml.safe_load(f)
    spreadsheet_id = yaml_data["spreadsheet_id"]
    yaml_accounts = yaml_data.get("accounts", [])

    sync_accounts_from_yaml(
        db,
        yaml_path,
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        yaml_data=yaml_data,
    )

    # Read balances from sheet
    sheet_client = make_sheet_client()
    balances = read_balances_from_sheet(sheet_client, spreadsheet_id, "Net Worth")
    click.echo(f"Read {len(balances)} balances from sheet.")

    # Fail before touching Plaid or writing anything. An ID on the sheet with
    # no accounts row violates the balances FK, and sqlite's error names
    # neither the ID nor the fix.
    unregistered = find_unregistered_ids(db, balances)
    if unregistered:
        raise click.ClickException(
            "Sheet rows have IDs not registered in accounts.yaml: "
            + ", ".join(unregistered)
            + "\nAdd each one under manual_accounts (id, label, type) in "
            "accounts.yaml, then re-run. No snapshot was written."
        )

    # Fetch fresh holdings from Plaid (subscription-billed, not per-call)
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

    # Local backup (optional — only if local_backup_dir configured)
    _maybe_backup_local()


def _maybe_backup_local():
    """Copy history.db to local_backup_dir; log failures but don't abort."""
    try:
        dest = backup_db_local(_db_path())
        click.echo(f"Backup: copied to {dest}")
    except FileNotFoundError:
        return
    except Exception as e:
        click.echo(f"Backup failed (non-fatal): {e}", err=True)


@cli.command()
@click.option("--weeks-back", type=int, default=None)
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
    from history import LOCAL_TZ, preceding_snapshot_week, weekly_diff

    db = _open_db()
    # Resolve weeks. User-supplied dates are interpreted in LOCAL_TZ at noon
    # (safely inside the day) so monday_of's tz conversion can't spill into
    # the previous day. Default week_a is computed at the date level from
    # the already-normalized week_b Monday string — no tz round-trip.
    if not week_b:
        week_b = monday_of(datetime.now(timezone.utc))
    else:
        _dt_b = datetime.fromisoformat(week_b)
        if _dt_b.tzinfo is not None:
            raise click.BadParameter(
                "Use a plain date (YYYY-MM-DD), not a timezone-aware string.",
                param_hint="'--week-b'",
            )
        week_b = monday_of(_dt_b.replace(hour=12, tzinfo=LOCAL_TZ))
    if not week_a:
        if weeks_back is not None:
            # Explicit override: fixed number of weeks before week_b.
            week_a = (
                date.fromisoformat(week_b) - timedelta(weeks=weeks_back)
            ).isoformat()
        else:
            # Default: follow the real snapshot cadence (weekly, biweekly, …)
            # by baselining against the immediately-preceding snapshot. Only
            # if there is none do we fall back to the prior week.
            week_a = (
                preceding_snapshot_week(db, week_b)
                or (date.fromisoformat(week_b) - timedelta(weeks=1)).isoformat()
            )
    else:
        _dt_a = datetime.fromisoformat(week_a)
        if _dt_a.tzinfo is not None:
            raise click.BadParameter(
                "Use a plain date (YYYY-MM-DD), not a timezone-aware string.",
                param_hint="'--week-a'",
            )
        week_a = monday_of(_dt_a.replace(hour=12, tzinfo=LOCAL_TZ))

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
            delta_str = f"[red]{_fmt_dollars(r['delta'])}[/red]"
        else:
            delta_str = "—"
        market = (
            _fmt_dollars(r["market"], decimals=0) if r["market"] is not None else ""
        )
        flow = _fmt_dollars(r["flow"], decimals=0) if r["flow"] is not None else ""
        table.add_row(
            r["label"],
            _fmt_dollars(r["old"]),
            _fmt_dollars(r["new"]),
            delta_str,
            market,
            flow,
            r["note"] or "",
        )
    table.add_section()
    if total_delta > 0:
        total_str = f"[green]+${total_delta:,.2f}[/green]"
    elif total_delta < 0:
        total_str = f"[red]{_fmt_dollars(total_delta)}[/red]"
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


@cli.command(name="restore-from-backup")
@click.option("--force", is_flag=True)
def restore_from_backup(force):
    """Copy history.db from local backup. Refuses if local DB exists without --force."""
    try:
        restore_db_local(_db_path(), force=force)
        click.echo(f"Restored history.db to {_db_path()}")
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()
    except FileExistsError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Pass --force to overwrite.", err=True)
        raise click.Abort()
    except sqlite3.DatabaseError as e:
        click.echo(f"Error: backup failed integrity check: {e}", err=True)
        raise click.Abort()


if __name__ == "__main__":
    cli()
