"""Balance history: schema, snapshot writing, queries.

Single source of truth for the balance-history SQLite database. See
docs/plans/2026-04-13-balance-history-design.md for the full design.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml
from sqlite_utils.db import Database

LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def init_schema(db: Database) -> None:
    """Create all tables and indexes. Idempotent.

    Foreign keys are enforced at the connection level (PRAGMA foreign_keys=ON
    is set by the test fixture and by production open helpers).

    See docs/plans/2026-04-13-balance-history-design.md for field meanings.
    """
    db["accounts"].create(
        {
            "id": str,
            "label": str,
            "type": str,
            "institution": str,
            "is_manual": int,
            "first_seen": str,
            "retired_at": str,
        },
        pk="id",
        if_not_exists=True,
        not_null={"id", "label", "type", "first_seen"},
        defaults={"is_manual": 0},
    )

    db["snapshots"].create(
        {
            "id": int,
            "captured_at": str,
            "week_of": str,
            "source": str,
            "notes": str,
        },
        pk="id",
        if_not_exists=True,
        not_null={"captured_at", "week_of", "source"},
    )
    db["snapshots"].create_index(
        ["week_of"], if_not_exists=True, index_name="idx_snapshots_week"
    )

    db["securities"].create(
        {
            "id": str,
            "ticker": str,
            "name": str,
            "type": str,
        },
        pk="id",
        if_not_exists=True,
        not_null={"id", "name"},
    )

    db["balances"].create(
        {
            "snapshot_id": int,
            "account_id": str,
            "balance": float,
        },
        pk=("snapshot_id", "account_id"),
        if_not_exists=True,
        foreign_keys=[
            ("snapshot_id", "snapshots", "id"),
            ("account_id", "accounts", "id"),
        ],
    )

    db["holdings"].create(
        {
            "snapshot_id": int,
            "account_id": str,
            "security_id": str,
            "quantity": float,
            "price": float,
            "value": float,
        },
        pk=("snapshot_id", "account_id", "security_id"),
        if_not_exists=True,
        foreign_keys=[
            ("snapshot_id", "snapshots", "id"),
            ("account_id", "accounts", "id"),
            ("security_id", "securities", "id"),
        ],
    )

    db["notes"].create(
        {
            "account_id": str,
            "week_of": str,
            "note": str,
            "created_at": str,
        },
        pk=("account_id", "week_of"),
        if_not_exists=True,
        foreign_keys=[("account_id", "accounts", "id")],
    )

    db["sync_state"].create(
        {
            "key": str,
            "value": str,
        },
        pk="key",
        if_not_exists=True,
        not_null={"key", "value"},
    )


def sync_accounts_from_yaml(db: Database, yaml_path: str, today: str) -> None:
    """Upsert the accounts table from accounts.yaml.

    New IDs get first_seen=today. IDs missing from yaml get retired_at=today.
    IDs that reappear after being retired have retired_at cleared.

    Args:
        db: sqlite-utils Database.
        yaml_path: path to accounts.yaml.
        today: ISO date string (e.g. "2026-04-13"). Injected for testability.
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    yaml_ids = set()
    rows_to_upsert = []

    for entry in data.get("accounts", []) or []:
        yaml_ids.add(entry["id"])
        rows_to_upsert.append(
            {
                "id": entry["id"],
                "label": entry["label"],
                "type": entry["type"],
                "institution": entry.get("institution"),
                "is_manual": 0,
            }
        )

    for entry in data.get("manual_accounts", []) or []:
        yaml_ids.add(entry["id"])
        rows_to_upsert.append(
            {
                "id": entry["id"],
                "label": entry["label"],
                "type": entry["type"],
                "institution": None,
                "is_manual": 1,
            }
        )

    # yaml may list the same id twice (joint accounts split across multiple
    # Plaid entries; their balances are summed onto one sheet row). The
    # accounts registry only cares about id/label/type/institution, so drop
    # duplicates within this run — keep the first occurrence.
    seen_ids: set[str] = set()
    existing = {row["id"]: row for row in db["accounts"].rows}
    for r in rows_to_upsert:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        if r["id"] in existing:
            db["accounts"].update(
                r["id"],
                {
                    "label": r["label"],
                    "type": r["type"],
                    "institution": r["institution"],
                    "is_manual": r["is_manual"],
                    "retired_at": None,
                },
            )
        else:
            new_row = {**r, "first_seen": today, "retired_at": None}
            db["accounts"].insert(new_row)

    for aid, row in existing.items():
        if aid not in yaml_ids and row["retired_at"] is None:
            db["accounts"].update(aid, {"retired_at": today})


def monday_of(dt: datetime) -> str:
    """Return ISO date (YYYY-MM-DD) for Monday of dt's local-TZ week.

    dt may be in any timezone. It is converted to LOCAL_TZ first, so week
    boundaries always align with the user's local calendar — a run at UTC
    Monday 02:00 that is Sunday 19:00 locally buckets to the *previous*
    Monday, not the current-UTC Monday.

    Monday is weekday 0; Sunday is weekday 6.
    """
    local = dt.astimezone(LOCAL_TZ)
    days_since_monday = local.weekday()
    monday_local = local - timedelta(days=days_since_monday)
    return monday_local.strftime("%Y-%m-%d")


def decompose_security(
    qty_old: float,
    price_old: float,
    qty_new: float,
    price_new: float,
) -> dict:
    """Decompose a security's value change into market vs flow.

    market = qty_old * (price_new - price_old)
    flow   = (qty_new - qty_old) * price_new

    These sum to (value_new - value_old) exactly (algebraic identity).
    """
    value_old = qty_old * price_old
    value_new = qty_new * price_new
    market = qty_old * (price_new - price_old)
    flow = (qty_new - qty_old) * price_new
    return {
        "market": market,
        "flow": flow,
        "value_old": value_old,
        "value_new": value_new,
    }


def write_snapshot(
    db: Database,
    captured_at: str,
    week_of: str,
    source: str,
    balances: dict,
    holdings: list,
) -> int:
    """Write a snapshot atomically.

    If source=='weekly' and a snapshot already exists for this week_of,
    delete the old snapshot's balances + holdings + the snapshot row,
    then insert the new one. All in one transaction — a crash mid-delete
    cannot leave a week with no snapshot.

    For source=='manual', multiple snapshots per week are permitted.

    balances: {history-side account_id: signed balance}
    holdings: list of dicts with keys security_id, ticker, name, type,
              quantity, price, value, history_account_id
    """
    # Use raw sqlite3 for atomic writes. sqlite-utils' insert_all calls
    # conn.commit() internally on each batch, which defeats any outer
    # transaction wrapper and leaves partial state after an FK violation.
    # Raw executes through the connection give us a single transaction.
    conn = db.conn
    try:
        conn.execute("BEGIN IMMEDIATE")

        if source == "weekly":
            sids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM snapshots "
                    "WHERE week_of = ? AND source = 'weekly'",
                    [week_of],
                ).fetchall()
            ]
            for sid in sids:
                conn.execute("DELETE FROM balances WHERE snapshot_id = ?", [sid])
                conn.execute("DELETE FROM holdings WHERE snapshot_id = ?", [sid])
                conn.execute("DELETE FROM snapshots WHERE id = ?", [sid])

        cur = conn.execute(
            "INSERT INTO snapshots " "(captured_at, week_of, source) VALUES (?, ?, ?)",
            [captured_at, week_of, source],
        )
        snapshot_id = cur.lastrowid

        seen_securities = set()
        for h in holdings:
            sec_id = h["security_id"]
            if sec_id in seen_securities:
                continue
            seen_securities.add(sec_id)
            conn.execute(
                "INSERT OR REPLACE INTO securities "
                "(id, ticker, name, type) VALUES (?, ?, ?, ?)",
                [
                    sec_id,
                    h.get("ticker"),
                    h.get("name", "Unknown"),
                    h.get("type"),
                ],
            )

        for aid, bal in balances.items():
            conn.execute(
                "INSERT INTO balances (snapshot_id, account_id, balance) "
                "VALUES (?, ?, ?)",
                [snapshot_id, aid, bal],
            )

        # Joint accounts: two Plaid account_ids can map to the same
        # history_account_id and both hold the same security. Sum
        # quantity + value so the decomposition math sees the merged
        # position. Price is per-share and identical across duplicates.
        aggregated: dict[tuple[str, str], dict] = {}
        for h in holdings:
            key = (h["history_account_id"], h["security_id"])
            qty = h.get("quantity", 0) or 0
            price = h.get("price", 0) or 0
            value = h.get("value", 0) or 0
            if key in aggregated:
                aggregated[key]["quantity"] += qty
                aggregated[key]["value"] += value
            else:
                aggregated[key] = {
                    "quantity": qty,
                    "price": price,
                    "value": value,
                }

        for (aid, sec_id), agg in aggregated.items():
            conn.execute(
                "INSERT INTO holdings "
                "(snapshot_id, account_id, security_id, "
                " quantity, price, value) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    snapshot_id,
                    aid,
                    sec_id,
                    agg["quantity"],
                    agg["price"],
                    agg["value"],
                ],
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return snapshot_id


def resolve_holdings_account_ids(holdings: list, yaml_accounts: list) -> list:
    """Add `history_account_id` to each holding.

    Matches on (institution, mask) against yaml_accounts entries. Holdings
    with no match are dropped with a stderr warning — not raised — because
    Plaid occasionally reports accounts we haven't mapped yet.
    """
    import sys

    mapping = {(a["institution"], a["mask"]): a["id"] for a in yaml_accounts}
    out = []
    for h in holdings:
        key = (h.get("institution"), h.get("account_mask"))
        if key not in mapping:
            print(
                f"Warning: dropping holding for unmapped " f"({key[0]}, ...{key[1]})",
                file=sys.stderr,
            )
            continue
        resolved = dict(h)
        resolved["history_account_id"] = mapping[key]
        out.append(resolved)
    return out


def upsert_note(db: Database, account_id: str, week_of: str, note: str) -> None:
    """Insert or replace a note for (account, week)."""
    db["notes"].insert(
        {
            "account_id": account_id,
            "week_of": week_of,
            "note": note,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        replace=True,
    )


def delete_note(db: Database, account_id: str, week_of: str) -> None:
    db["notes"].delete((account_id, week_of))


def list_snapshots(db: Database, limit: int = 10) -> list[dict]:
    """Return recent snapshots, most-recent first."""
    return list(
        db.query(
            "SELECT * FROM snapshots ORDER BY captured_at DESC LIMIT ?",
            [limit],
        )
    )


def weekly_diff(db: Database, week_a: str, week_b: str) -> list[dict]:
    """Return per-account deltas between two weekly snapshots.

    Each row: {id, label, type, old, new, delta, market, flow, note}
    market/flow are only populated for accounts with holdings at
    both weeks.
    """
    sql = """
        SELECT a.id, a.label, a.type,
               b1.balance AS old_balance,
               b2.balance AS new_balance
        FROM accounts a
        LEFT JOIN balances b1 ON b1.account_id = a.id AND b1.snapshot_id =
            (SELECT id FROM snapshots
             WHERE week_of = ? AND source='weekly' ORDER BY captured_at DESC LIMIT 1)
        LEFT JOIN balances b2 ON b2.account_id = a.id AND b2.snapshot_id =
            (SELECT id FROM snapshots
             WHERE week_of = ? AND source='weekly' ORDER BY captured_at DESC LIMIT 1)
        WHERE b1.balance IS NOT NULL OR b2.balance IS NOT NULL
    """
    rows = list(db.query(sql, [week_a, week_b]))
    # Batch-load notes for week_b once instead of N queries in the loop.
    notes_by_id = {
        nr["account_id"]: nr["note"]
        for nr in db["notes"].rows_where("week_of = ?", [week_b])
    }
    out = []
    for r in rows:
        old = r["old_balance"] or 0
        new = r["new_balance"] or 0
        item = {
            "id": r["id"],
            "label": r["label"],
            "type": r["type"],
            "old": old,
            "new": new,
            "delta": new - old,
            "market": None,
            "flow": None,
            "note": notes_by_id.get(r["id"]),
        }
        market, flow = _decompose_account(db, r["id"], week_a, week_b)
        item["market"] = market
        item["flow"] = flow
        out.append(item)
    return out


def _decompose_account(db, account_id, week_a, week_b):
    """Sum market/flow across securities held in either week."""
    sql = """
        SELECT h.security_id,
               COALESCE(ho.quantity, 0) AS qo,
               COALESCE(ho.price, 0) AS po,
               COALESCE(hn.quantity, 0) AS qn,
               COALESCE(hn.price, 0) AS pn
        FROM (
            SELECT security_id FROM holdings
            WHERE account_id = ? AND snapshot_id IN
                (SELECT id FROM snapshots
                 WHERE week_of IN (?, ?) AND source='weekly')
            GROUP BY security_id
        ) h
        LEFT JOIN holdings ho ON ho.security_id = h.security_id
            AND ho.account_id = ?
            AND ho.snapshot_id = (SELECT id FROM snapshots
                WHERE week_of = ? AND source='weekly' ORDER BY captured_at DESC LIMIT 1)
        LEFT JOIN holdings hn ON hn.security_id = h.security_id
            AND hn.account_id = ?
            AND hn.snapshot_id = (SELECT id FROM snapshots
                WHERE week_of = ? AND source='weekly' ORDER BY captured_at DESC LIMIT 1)
    """
    rows = list(
        db.query(
            sql,
            [
                account_id,
                week_a,
                week_b,
                account_id,
                week_a,
                account_id,
                week_b,
            ],
        )
    )
    if not rows:
        return None, None
    market_total = 0.0
    flow_total = 0.0
    for r in rows:
        d = decompose_security(r["qo"], r["po"], r["qn"], r["pn"])
        market_total += d["market"]
        flow_total += d["flow"]
    return market_total, flow_total
