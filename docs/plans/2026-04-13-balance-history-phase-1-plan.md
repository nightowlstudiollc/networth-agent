# Balance History — Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Build the foundation for weekly balance history — snapshot capture
into SQLite, Drive backup with staleness check, and a CLI (`diff`, `snapshot`,
`snapshots`, `annotate`, `backfill`, `restore-from-drive`) that answers "what
changed, by how much, and why" for the previous week.

**Architecture:** Four new Python modules — `history.py` (schema, snapshot
writer, queries, decomposition), `history_sheet.py` (read balances from Google
Sheet by column-H ID), `history_drive.py` (Drive upload with staleness guard +
restore), `balance_history.py` (Click-based CLI). All four are thin glue over
existing libraries. Single source of truth: `history.db` in the repo directory,
gitignored, backed up to Drive after every successful snapshot.

**Tech stack:** `sqlite-utils` (Simon Willison, schema + queries),
`click` (CLI), `rich` (colored tables), `plotext` (sparklines — Phase 3),
`python-dateutil` (week math), `google-api-python-client` (already present,
for Drive upload), `plaid-python` (already present, for holdings fetch),
`pytest` + `pytest-mock` (tests).

**Design reference:** `docs/plans/2026-04-13-balance-history-design.md`.
Read it before starting. This plan assumes familiarity with the schema and
capture flow described there.

---

## Testing philosophy

Every module is test-driven:

1. Write a failing test that specifies the behavior.
2. Run the test — confirm it fails for the right reason (usually
   `AttributeError`/`ImportError`).
3. Implement the minimal code to make it pass.
4. Run the test — confirm it passes.
5. Commit both together.

Tests use an in-memory SQLite DB (via pytest fixture). External dependencies
(Plaid client, Google Sheets MCP, Drive API, `accounts.yaml`) are mocked. One
test file per production module.

Do NOT batch multiple tasks into one commit. Each task's failing test and its
implementation are **one commit** — but separate tasks get separate commits.
This keeps history bisectable.

---

## Task ordering and dependencies

Tasks are ordered so each can complete before the next starts:

```
Group A (Setup) ──► Group B (DB core) ──► Group C (Capture) ──► Group E (CLI)
                        │                      │                      ▲
                        └────► Group D (Drive) ┘                      │
                                                                      │
                                              Group F (Integration) ──┘
```

---

## Group A — Setup

### Task 1: Add new dependencies to requirements.txt

**Files:**

- Modify: `requirements.txt`

**Step 1: Add libraries**

Append to `requirements.txt`:

```
sqlite-utils>=3.36
click>=8.1
rich>=13.7
plotext>=5.2
python-dateutil>=2.8
```

**Step 2: Install into the venv**

Run: `source .venv/bin/activate && uv pip install -r requirements.txt`
Expected: all five install cleanly, `pip show sqlite-utils` succeeds.

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "build: add sqlite-utils, click, rich, plotext, python-dateutil

For balance-history Phase 1."
```

---

### Task 2: Add pytest dev dependencies

**Files:**

- Create: `requirements-dev.txt`

**Step 1: Create dev-requirements file**

```
# requirements-dev.txt — test/dev dependencies, not needed in production
-r requirements.txt
pytest>=8.0
pytest-mock>=3.12
pytest-cov>=4.1
freezegun>=1.4
```

**Step 2: Install**

Run: `source .venv/bin/activate && uv pip install -r requirements-dev.txt`
Expected: all install, `pytest --version` succeeds.

**Step 3: Commit**

```bash
git add requirements-dev.txt
git commit -m "build: add pytest/pytest-mock/pytest-cov/freezegun for tests

Separate dev deps from production requirements.txt."
```

---

### Task 3: Create tests/ structure with conftest.py

**Files:**

- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `pytest.ini`

**Step 1: Write conftest.py with core fixtures**

```python
# tests/conftest.py
"""Shared pytest fixtures for balance-history tests."""
import pytest
import sqlite_utils


@pytest.fixture
def db():
    """In-memory SQLite DB with the full balance-history schema applied."""
    from history import init_schema
    conn = sqlite_utils.Database(memory=True)
    init_schema(conn)
    return conn


@pytest.fixture
def sample_accounts_yaml(tmp_path):
    """Write a minimal accounts.yaml to tmp_path and return the path."""
    path = tmp_path / "accounts.yaml"
    path.write_text("""
spreadsheet_id: "test-sheet-id"
accounts:
  - institution: "TestBank"
    name: "Checking"
    mask: "1234"
    id: "test-checking"
    label: "Test Checking"
    type: asset
  - institution: "TestBroker"
    name: "Brokerage"
    mask: "5678"
    id: "test-brokerage"
    label: "Test Brokerage"
    type: asset
manual_accounts:
  - id: "manual-asset"
    label: "Manual Asset"
    type: asset
""")
    return path


@pytest.fixture
def frozen_monday(freezer):
    """Freeze time to Monday 2026-04-13 10:00 PDT for deterministic week_of."""
    freezer.move_to("2026-04-13 10:00:00-07:00")
    return "2026-04-13"
```

**Step 2: Write pytest.ini**

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --strict-markers --tb=short
```

**Step 3: Verify test collection works**

Run: `source .venv/bin/activate && pytest --collect-only`
Expected: "collected 0 items" (no tests yet, but pytest finds the config).

**Step 4: Commit**

```bash
git add tests/__init__.py tests/conftest.py pytest.ini
git commit -m "test: scaffold tests/ dir with conftest and pytest config

Fixtures for in-memory DB and sample accounts.yaml."
```

---

### Task 4: Update .gitignore for history.db and sync state

**Files:**

- Modify: `.gitignore`

**Step 1: Append new ignore rules**

Add to `.gitignore` (append near the bottom, after the Python section):

```
# Balance history (local DB; backed up to Drive)
history.db
history.db-journal
history.db-wal
history.db-shm

# Git worktrees (isolated workspaces for feature branches)
.worktrees/
```

**Step 2: Verify**

Run: `git check-ignore -v history.db .worktrees`
Expected: both lines show which `.gitignore` rule matches.

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "build: gitignore history.db (SQLite) and .worktrees/

History DB is local state, backed up to Drive. Worktrees are isolated
checkouts that must never enter git."
```

---

## Group B — Database layer

### Task 5: Create history.py with schema initialization

**Files:**

- Create: `history.py`
- Create: `tests/test_history_db.py`

**Step 1: Write the failing test**

```python
# tests/test_history_db.py
"""Tests for history.py schema initialization."""
import sqlite_utils


def test_init_schema_creates_all_tables():
    from history import init_schema
    db = sqlite_utils.Database(memory=True)
    init_schema(db)
    table_names = set(db.table_names())
    assert table_names == {
        "accounts", "snapshots", "balances",
        "holdings", "securities", "notes", "sync_state"
    }


def test_init_schema_is_idempotent():
    """Running init_schema twice must not error."""
    from history import init_schema
    db = sqlite_utils.Database(memory=True)
    init_schema(db)
    init_schema(db)  # second call
    assert "accounts" in db.table_names()


def test_snapshots_has_week_of_index():
    from history import init_schema
    db = sqlite_utils.Database(memory=True)
    init_schema(db)
    indexes = {i.name for i in db["snapshots"].indexes}
    assert "idx_snapshots_week" in indexes
```

**Step 2: Verify tests fail**

Run: `pytest tests/test_history_db.py -v`
Expected: 3 tests fail with `ImportError: cannot import name 'init_schema' from 'history'` (or `ModuleNotFoundError`).

**Step 3: Implement history.py init_schema**

```python
# history.py
"""Balance history: schema, snapshot writing, queries.

Single source of truth for the balance-history SQLite database. See
docs/plans/2026-04-13-balance-history-design.md for the full design.
"""
from __future__ import annotations

import sqlite_utils
from sqlite_utils.db import Database


def init_schema(db: Database) -> None:
    """Create all tables and indexes. Idempotent.

    See docs/plans/2026-04-13-balance-history-design.md for field meanings.
    """
    db["accounts"].create({
        "id": str,
        "label": str,
        "type": str,
        "institution": str,
        "is_manual": int,
        "first_seen": str,
        "retired_at": str,
    }, pk="id", if_not_exists=True, not_null={"id", "label", "type", "first_seen"},
        defaults={"is_manual": 0})

    db["snapshots"].create({
        "id": int,
        "captured_at": str,
        "week_of": str,
        "source": str,
        "notes": str,
    }, pk="id", if_not_exists=True, not_null={"captured_at", "week_of", "source"})
    db["snapshots"].create_index(["week_of"], if_not_exists=True, index_name="idx_snapshots_week")

    db["balances"].create({
        "snapshot_id": int,
        "account_id": str,
        "balance": float,
    }, pk=("snapshot_id", "account_id"), if_not_exists=True,
        foreign_keys=[("snapshot_id", "snapshots", "id"),
                      ("account_id", "accounts", "id")])

    db["securities"].create({
        "id": str,
        "ticker": str,
        "name": str,
        "type": str,
    }, pk="id", if_not_exists=True, not_null={"id", "name"})

    db["holdings"].create({
        "snapshot_id": int,
        "account_id": str,
        "security_id": str,
        "quantity": float,
        "price": float,
        "value": float,
    }, pk=("snapshot_id", "account_id", "security_id"), if_not_exists=True,
        foreign_keys=[("snapshot_id", "snapshots", "id"),
                      ("account_id", "accounts", "id"),
                      ("security_id", "securities", "id")])

    db["notes"].create({
        "account_id": str,
        "week_of": str,
        "note": str,
        "created_at": str,
    }, pk=("account_id", "week_of"), if_not_exists=True,
        foreign_keys=[("account_id", "accounts", "id")])

    db["sync_state"].create({
        "key": str,
        "value": str,
    }, pk="key", if_not_exists=True, not_null={"key", "value"})
```

**Step 4: Verify tests pass**

Run: `pytest tests/test_history_db.py -v`
Expected: 3 passed.

**Step 5: Commit**

```bash
git add history.py tests/test_history_db.py
git commit -m "feat(history): schema initialization

Seven tables: accounts, snapshots, balances, holdings, securities, notes,
sync_state. Idempotent; can be run on an existing DB without error."
```

---

### Task 6: Account registry sync from accounts.yaml

**Files:**

- Modify: `history.py`
- Create: `tests/test_history_accounts.py`

**Step 1: Write failing tests**

```python
# tests/test_history_accounts.py
import yaml


def test_sync_accounts_inserts_new_ids(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    ids = {row["id"] for row in db["accounts"].rows}
    assert ids == {"test-checking", "test-brokerage", "manual-asset"}


def test_sync_accounts_marks_manual_flag(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    by_id = {row["id"]: row for row in db["accounts"].rows}
    assert by_id["test-checking"]["is_manual"] == 0
    assert by_id["manual-asset"]["is_manual"] == 1


def test_sync_accounts_sets_first_seen_on_insert(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    for row in db["accounts"].rows:
        assert row["first_seen"] == "2026-04-13"
        assert row["retired_at"] is None


def test_sync_accounts_preserves_first_seen_on_reinsert(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-01-01")
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")
    by_id = {row["id"]: row for row in db["accounts"].rows}
    assert by_id["test-checking"]["first_seen"] == "2026-01-01"


def test_sync_accounts_retires_missing_ids(db, sample_accounts_yaml, tmp_path):
    from history import sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-01-01")
    # Write a new yaml without test-brokerage
    new_yaml = tmp_path / "new_accounts.yaml"
    new_yaml.write_text("""
spreadsheet_id: "test"
accounts:
  - institution: "TestBank"
    name: "Checking"
    mask: "1234"
    id: "test-checking"
    label: "Test Checking"
    type: asset
""")
    sync_accounts_from_yaml(db, str(new_yaml), today="2026-04-13")
    by_id = {row["id"]: row for row in db["accounts"].rows}
    assert by_id["test-brokerage"]["retired_at"] == "2026-04-13"
    assert by_id["test-checking"]["retired_at"] is None


def test_sync_accounts_unretires_if_reappears(db, sample_accounts_yaml, tmp_path):
    """Adding back an account that was retired clears retired_at."""
    from history import sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-01-01")
    # Retire one
    minimal = tmp_path / "min.yaml"
    minimal.write_text("""
spreadsheet_id: "test"
accounts: []
""")
    sync_accounts_from_yaml(db, str(minimal), today="2026-02-01")
    assert db["accounts"].get("test-checking")["retired_at"] == "2026-02-01"
    # Add back via original
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-03-01")
    assert db["accounts"].get("test-checking")["retired_at"] is None
```

**Step 2: Verify tests fail**

Run: `pytest tests/test_history_accounts.py -v`
Expected: 6 fails (`ImportError` or `AttributeError`).

**Step 3: Implement sync_accounts_from_yaml**

Add to `history.py`:

```python
import yaml


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

    for entry in data.get("accounts", []):
        yaml_ids.add(entry["id"])
        rows_to_upsert.append({
            "id": entry["id"],
            "label": entry["label"],
            "type": entry["type"],
            "institution": entry.get("institution"),
            "is_manual": 0,
        })

    for entry in data.get("manual_accounts", []):
        yaml_ids.add(entry["id"])
        rows_to_upsert.append({
            "id": entry["id"],
            "label": entry["label"],
            "type": entry["type"],
            "institution": None,
            "is_manual": 1,
        })

    # Upsert: preserve first_seen; clear retired_at.
    existing = {row["id"]: row for row in db["accounts"].rows}
    for r in rows_to_upsert:
        if r["id"] in existing:
            db["accounts"].update(r["id"], {
                "label": r["label"],
                "type": r["type"],
                "institution": r["institution"],
                "is_manual": r["is_manual"],
                "retired_at": None,
            })
        else:
            db["accounts"].insert({**r, "first_seen": today, "retired_at": None})

    # Retire missing IDs that aren't already retired.
    for aid, row in existing.items():
        if aid not in yaml_ids and row["retired_at"] is None:
            db["accounts"].update(aid, {"retired_at": today})
```

**Step 4: Verify tests pass**

Run: `pytest tests/test_history_accounts.py -v`
Expected: 6 passed.

**Step 5: Commit**

```bash
git add history.py tests/test_history_accounts.py
git commit -m "feat(history): sync accounts table from accounts.yaml

Upserts accounts + manual_accounts blocks. Handles first_seen preservation,
retirement of missing IDs, and un-retirement when an ID reappears."
```

---

### Task 7: Week math helper

**Files:**

- Modify: `history.py`
- Create: `tests/test_history_week.py`

**Step 1: Write failing tests**

```python
# tests/test_history_week.py
from datetime import datetime
from zoneinfo import ZoneInfo


def test_monday_of_returns_same_day_on_monday():
    from history import monday_of
    mon = datetime(2026, 4, 13, 15, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert monday_of(mon) == "2026-04-13"


def test_monday_of_returns_previous_monday_for_mid_week():
    from history import monday_of
    wed = datetime(2026, 4, 15, 15, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert monday_of(wed) == "2026-04-13"


def test_monday_of_returns_previous_monday_for_sunday():
    from history import monday_of
    sun = datetime(2026, 4, 19, 23, 59, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert monday_of(sun) == "2026-04-13"


def test_monday_of_honors_local_tz_not_utc():
    """A timestamp that is Sunday in UTC but Monday locally should bucket to
    that local-Monday."""
    from history import monday_of
    # 2026-04-13 02:00 UTC = 2026-04-12 19:00 PDT (Sunday)
    sun_local = datetime(2026, 4, 13, 2, 0, tzinfo=ZoneInfo("UTC"))
    assert monday_of(sun_local) == "2026-04-06"
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_week.py -v`
Expected: 4 fails.

**Step 3: Implement monday_of**

Add to `history.py`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def monday_of(dt: datetime) -> str:
    """Return ISO date string (YYYY-MM-DD) for the Monday of dt's local week.

    dt may be in any timezone. It is converted to LOCAL_TZ before the Monday
    calculation, so week boundaries always align with the user's local calendar.

    Monday is weekday 0; Sunday is weekday 6.
    """
    local = dt.astimezone(LOCAL_TZ)
    days_since_monday = local.weekday()
    monday_local = local - timedelta(days=days_since_monday)
    return monday_local.strftime("%Y-%m-%d")
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_week.py -v`
Expected: 4 passed.

**Step 5: Commit**

```bash
git add history.py tests/test_history_week.py
git commit -m "feat(history): monday_of helper anchored to local TZ

Weeks always bucket by user's local-TZ Monday. Documents and tests the
UTC-vs-local-Sunday edge case explicitly."
```

---

### Task 8: Market-vs-flow decomposition

**Files:**

- Modify: `history.py`
- Create: `tests/test_history_decompose.py`

**Step 1: Write failing tests**

```python
# tests/test_history_decompose.py
from history import decompose_security

# decompose_security takes (qty_old, price_old, qty_new, price_new) and
# returns a dict {"market": float, "flow": float, "value_old": float,
# "value_new": float}.


def test_no_change():
    d = decompose_security(100, 50, 100, 50)
    assert d == {"market": 0, "flow": 0, "value_old": 5000, "value_new": 5000}


def test_pure_market_gain():
    d = decompose_security(100, 50, 100, 55)
    assert d["market"] == 500
    assert d["flow"] == 0
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_pure_flow_add():
    d = decompose_security(100, 50, 120, 50)
    assert d["market"] == 0
    assert d["flow"] == 1000
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_mixed_market_and_flow():
    d = decompose_security(100, 50, 120, 55)
    assert d["market"] == 500         # 100 × (55 - 50)
    assert d["flow"] == 1100          # (120 - 100) × 55
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_newly_held():
    """qty_old=0 means entirely flow."""
    d = decompose_security(0, 0, 10, 100)
    assert d["market"] == 0
    assert d["flow"] == 1000
    assert d["value_old"] == 0
    assert d["value_new"] == 1000


def test_fully_sold():
    """qty_new=0 with price change: market captures the old shares' price
    change, flow captures the sale."""
    d = decompose_security(100, 50, 0, 55)
    assert d["market"] == 500         # 100 × (55 - 50)
    assert d["flow"] == -5500         # (0 - 100) × 55
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]


def test_sale_at_same_price():
    d = decompose_security(100, 50, 60, 50)
    assert d["market"] == 0
    assert d["flow"] == -2000         # (60 - 100) × 50
    assert d["value_new"] - d["value_old"] == d["market"] + d["flow"]
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_decompose.py -v`
Expected: 7 fails.

**Step 3: Implement**

Add to `history.py`:

```python
def decompose_security(qty_old: float, price_old: float,
                       qty_new: float, price_new: float) -> dict:
    """Decompose a security's value change into market-move vs flow.

    market = qty_old × (price_new − price_old)
    flow   = (qty_new − qty_old) × price_new

    These sum to value_new − value_old exactly (algebraic identity).
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
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_decompose.py -v`
Expected: 7 passed.

**Step 5: Commit**

```bash
git add history.py tests/test_history_decompose.py
git commit -m "feat(history): market-vs-flow decomposition

Per-security attribution formula. All 7 edge cases covered:
no-change, pure market, pure flow, mixed, newly-held, fully-sold,
sale-at-same-price."
```

---

## Group C — Capture layer

### Task 9: Sheet reader — extract balances by ID

**Files:**

- Create: `history_sheet.py`
- Create: `tests/test_history_sheet.py`

**Step 1: Write failing tests**

```python
# tests/test_history_sheet.py
from unittest.mock import MagicMock


def test_read_balances_from_sheet_maps_ids_to_column_b():
    """Given column H IDs and column B balances, returns {id: balance}."""
    from history_sheet import read_balances_from_sheet

    # Mock the Google Sheets MCP client call
    fake_client = MagicMock()
    fake_client.get_values.return_value = [
        ["Assets", "Balance", "", "", "", "", "", "ID"],
        ["Coinbase", 24.42, "✔️", "", "", "", "", "coinbase"],
        ["Mercury", 296.48, "✔️", "", "", "", "", "mercury-checking"],
        ["Subtotal", 320.90, "", "", "", "", "", ""],  # no ID → skipped
        [],                                              # empty row → skipped
        ["Liabilities"],                                 # section header → skipped
        ["Amex Bonvoy", -638.08, "✔️", "", "", "", "", "amex-bonvoy"],
    ]

    result = read_balances_from_sheet(fake_client, "spreadsheet-id", "Net Worth")

    assert result == {
        "coinbase": 24.42,
        "mercury-checking": 296.48,
        "amex-bonvoy": -638.08,
    }


def test_read_balances_skips_rows_without_id():
    """Row with empty column H is skipped (not an error)."""
    from history_sheet import read_balances_from_sheet
    fake_client = MagicMock()
    fake_client.get_values.return_value = [
        ["header"],
        ["Subtotal", 1000, "", "", "", "", "", ""],  # col H empty
    ]
    assert read_balances_from_sheet(fake_client, "s", "Net Worth") == {}


def test_read_balances_parses_string_currency_as_float():
    """Values like ' $ (25.99)' must parse to -25.99."""
    from history_sheet import read_balances_from_sheet
    fake_client = MagicMock()
    fake_client.get_values.return_value = [
        ["header"],
        ["Card", " $ (25.99)", "✔️", "", "", "", "", "card-1"],
        ["Checking", " $ 1,234.56 ", "✔️", "", "", "", "", "checking-1"],
    ]
    result = read_balances_from_sheet(fake_client, "s", "Net Worth")
    assert result == {"card-1": -25.99, "checking-1": 1234.56}


def test_read_balances_treats_dash_as_zero():
    """Sheet often displays $0 as ' $ -   '. Treat that as 0.0."""
    from history_sheet import read_balances_from_sheet
    fake_client = MagicMock()
    fake_client.get_values.return_value = [
        ["header"],
        ["Empty", " $ -   ", "", "", "", "", "", "empty-1"],
    ]
    assert read_balances_from_sheet(fake_client, "s", "Net Worth") == {"empty-1": 0.0}
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_sheet.py -v`
Expected: 4 fails.

**Step 3: Implement history_sheet.py**

```python
# history_sheet.py
"""Read balances from the Net Worth Google Sheet.

The sheet is queried via the google-sheets MCP client. Rows are identified
by the stable slug in column H; balance is in column B.
"""
from __future__ import annotations

import re
from typing import Protocol


class SheetClient(Protocol):
    def get_values(self, spreadsheet_id: str, range_: str) -> list[list]: ...


_CURRENCY_RE = re.compile(r"[^\d.\-]")


def _parse_balance(raw) -> float | None:
    """Parse a cell value into a float balance.

    Handles: numeric types (int/float), ' $ 1,234.56 ', ' $ (25.99)' (parens
    mean negative, accounting convention), ' $ -   ' (zero), '' (None).
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s == "" or s == "$-" or s.replace(" ", "").replace("$", "") == "-":
        return 0.0
    is_negative = "(" in s and ")" in s
    cleaned = _CURRENCY_RE.sub("", s)
    if cleaned in ("", "-"):
        return 0.0
    value = float(cleaned)
    return -abs(value) if is_negative else value


def read_balances_from_sheet(
    client: SheetClient, spreadsheet_id: str, tab_name: str,
) -> dict[str, float]:
    """Read all balances from the sheet, keyed by column-H ID.

    Rows without an ID in column H are skipped (subtotal, blank, section header).
    """
    rows = client.get_values(spreadsheet_id, f"{tab_name}!A1:H200")
    result: dict[str, float] = {}
    for i, row in enumerate(rows):
        if i == 0:
            continue  # header row
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
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_sheet.py -v`
Expected: 4 passed.

**Step 5: Commit**

```bash
git add history_sheet.py tests/test_history_sheet.py
git commit -m "feat(history): read balances from Google Sheet by column-H ID

Handles currency formatting: parens mean negative, dash means zero,
plain numerics pass through."
```

---

### Task 10: Refactor Plaid holdings fetch into callable

**Files:**

- Modify: `plaid_balance.py` (extract existing logic into a callable that can be reused)
- Create: `tests/test_plaid_holdings.py`

**Step 1: Write failing test**

```python
# tests/test_plaid_holdings.py
"""Test the extracted holdings-fetch callable in plaid_balance.py."""


def test_fetch_holdings_by_account_id_structure():
    """fetch_all_holdings returns list of dicts with required keys."""
    # This test uses mocks to avoid real Plaid calls.
    from unittest.mock import patch, MagicMock
    import plaid_balance

    fake_response = MagicMock()
    fake_response.to_dict.return_value = {
        "holdings": [{
            "account_id": "acct-1", "security_id": "sec-1",
            "quantity": 10, "institution_price": 5.0,
            "institution_value": 50.0, "iso_currency_code": "USD",
        }],
        "securities": [{
            "security_id": "sec-1", "name": "Test Fund",
            "ticker_symbol": "TST", "type": "mutual fund",
        }],
    }

    fake_accounts_resp = MagicMock()
    fake_accounts_resp.to_dict.return_value = {
        "accounts": [{"account_id": "acct-1", "name": "Brokerage", "mask": "1234"}]
    }

    with patch.object(plaid_balance, "client") as mock_client:
        mock_client.investments_holdings_get.return_value = fake_response
        mock_client.accounts_get.return_value = fake_accounts_resp

        items = {"item-1": {
            "access_token": "tok",
            "institution_name": "TestBroker",
            "products": ["investments"],
        }}
        result = plaid_balance.fetch_all_holdings(items)

    assert len(result) == 1
    h = result[0]
    assert h["institution"] == "TestBroker"
    assert h["account_id"] == "acct-1"
    assert h["account_mask"] == "1234"
    assert h["security_id"] == "sec-1"
    assert h["quantity"] == 10
    assert h["price"] == 5.0
    assert h["value"] == 50.0
    assert h["ticker"] == "TST"
    assert h["name"] == "Test Fund"


def test_fetch_holdings_skips_items_without_investments_product():
    """Items without 'investments' in their products list return empty."""
    from unittest.mock import patch
    import plaid_balance

    items = {"item-1": {
        "access_token": "tok",
        "institution_name": "BankOnly",
        "products": ["transactions"],  # no investments
    }}
    with patch.object(plaid_balance, "client"):
        result = plaid_balance.fetch_all_holdings(items)
    assert result == []
```

**Step 2: Verify fail**

Run: `pytest tests/test_plaid_holdings.py -v`
Expected: fails with `AttributeError: fetch_all_holdings`.

**Step 3: Implement fetch_all_holdings**

Add to `plaid_balance.py` (re-using `get_investment_holdings` + `client` already defined):

```python
# Append near the bottom of plaid_balance.py, after existing helpers.
from plaid.model.accounts_get_request import AccountsGetRequest


def fetch_all_holdings(items: dict) -> list[dict]:
    """Fetch holdings for every item with the investments product enabled.

    Returns a flat list of dicts with stable keys for consumption by history.py:
    institution, account_id, account_name, account_mask, security_id,
    ticker, name, type, quantity, price, value, currency.

    Does not touch accounts/balance/get — no per-call billing. Only hits
    investments/holdings/get which is on the monthly Investments subscription.
    """
    all_holdings: list[dict] = []
    for _item_id, item in items.items():
        if "investments" not in item.get("products", []):
            continue
        access_token = item["access_token"]
        institution = item.get("institution_name", "Unknown")

        # Map account_id -> (name, mask) for this item.
        acct_resp = client.accounts_get(AccountsGetRequest(access_token=access_token))
        acct_map = {a["account_id"]: (a.get("name", ""), a.get("mask", ""))
                    for a in acct_resp.to_dict()["accounts"]}

        holdings, securities, err = get_investment_holdings(access_token, institution)
        if err:
            continue
        for h in holdings:
            sec = securities.get(h.get("security_id"), {})
            value = h.get("institution_value")
            if value is None:
                qty = h.get("quantity", 0)
                price = h.get("institution_price", 0)
                value = (qty * price) if (qty and price) else 0
            aid = h.get("account_id")
            name, mask = acct_map.get(aid, ("", ""))
            all_holdings.append({
                "institution": institution,
                "account_id": aid,
                "account_name": name,
                "account_mask": mask,
                "security_id": h.get("security_id"),
                "ticker": sec.get("ticker_symbol"),
                "name": sec.get("name", "Unknown"),
                "type": sec.get("type"),
                "quantity": h.get("quantity"),
                "price": h.get("institution_price"),
                "value": value,
                "currency": h.get("iso_currency_code", "USD"),
            })
    return all_holdings
```

**Step 4: Verify pass**

Run: `pytest tests/test_plaid_holdings.py -v`
Expected: 2 passed.

**Step 5: Commit**

```bash
git add plaid_balance.py tests/test_plaid_holdings.py
git commit -m "feat(plaid): extract fetch_all_holdings callable

Reusable wrapper returning a flat list of holdings dicts with stable keys.
Called by history.py during snapshot capture — no new Plaid charges."
```

---

### Task 11: Snapshot writer

**Files:**

- Modify: `history.py`
- Create: `tests/test_history_snapshot.py`

**Step 1: Write failing tests**

```python
# tests/test_history_snapshot.py
import pytest


def test_write_snapshot_inserts_snapshot_row(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")

    snapshot_id = write_snapshot(
        db,
        captured_at="2026-04-13T18:00:00Z",
        week_of="2026-04-13",
        source="weekly",
        balances={"test-checking": 1000.0, "test-brokerage": 50000.0},
        holdings=[],
    )
    assert snapshot_id is not None
    row = db["snapshots"].get(snapshot_id)
    assert row["week_of"] == "2026-04-13"
    assert row["source"] == "weekly"


def test_write_snapshot_inserts_balance_rows(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")

    sid = write_snapshot(db, captured_at="t", week_of="2026-04-13",
                         source="weekly",
                         balances={"test-checking": 1000.0, "test-brokerage": 50000.0},
                         holdings=[])

    balance_rows = {r["account_id"]: r["balance"]
                    for r in db["balances"].rows_where("snapshot_id = ?", [sid])}
    assert balance_rows == {"test-checking": 1000.0, "test-brokerage": 50000.0}


def test_write_snapshot_inserts_holdings_and_securities(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")

    holdings = [{
        "institution": "TestBroker",
        "account_id": "plaid-acct-1",
        "account_name": "Brokerage",
        "account_mask": "5678",
        "security_id": "sec-1",
        "ticker": "VTV",
        "name": "Vanguard Value ETF",
        "type": "etf",
        "quantity": 10,
        "price": 200,
        "value": 2000,
    }]
    # Important: test-brokerage is the history-side ID matching account_mask 5678.
    # For this test, assume we pre-populate the mask→id mapping.
    # The writer signature accepts holdings already keyed to history-side account_ids:
    for h in holdings:
        h["history_account_id"] = "test-brokerage"

    sid = write_snapshot(db, captured_at="t", week_of="2026-04-13",
                         source="weekly",
                         balances={"test-brokerage": 2000.0},
                         holdings=holdings)

    # Security inserted into securities table
    sec = db["securities"].get("sec-1")
    assert sec["ticker"] == "VTV"
    assert sec["name"] == "Vanguard Value ETF"

    # Holding row inserted
    hrow = list(db["holdings"].rows_where("snapshot_id = ?", [sid]))[0]
    assert hrow["security_id"] == "sec-1"
    assert hrow["quantity"] == 10
    assert hrow["price"] == 200
    assert hrow["value"] == 2000
    assert hrow["account_id"] == "test-brokerage"


def test_write_snapshot_replaces_existing_weekly_for_same_week(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")

    # First write
    sid1 = write_snapshot(db, captured_at="t1", week_of="2026-04-13",
                          source="weekly",
                          balances={"test-checking": 1000.0}, holdings=[])

    # Re-run for same week — must replace, not stack
    sid2 = write_snapshot(db, captured_at="t2", week_of="2026-04-13",
                          source="weekly",
                          balances={"test-checking": 1500.0}, holdings=[])

    weekly_snapshots = list(db["snapshots"].rows_where(
        "week_of = ? AND source = ?", ["2026-04-13", "weekly"]))
    assert len(weekly_snapshots) == 1
    assert weekly_snapshots[0]["id"] == sid2

    # Old balance rows must be gone
    assert list(db["balances"].rows_where("snapshot_id = ?", [sid1])) == []
    new_balances = list(db["balances"].rows_where("snapshot_id = ?", [sid2]))
    assert len(new_balances) == 1
    assert new_balances[0]["balance"] == 1500.0


def test_write_snapshot_allows_multiple_manual_snapshots_same_week(db, sample_accounts_yaml):
    from history import write_snapshot, sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")

    write_snapshot(db, captured_at="t1", week_of="2026-04-13", source="manual",
                   balances={"test-checking": 100.0}, holdings=[])
    write_snapshot(db, captured_at="t2", week_of="2026-04-13", source="manual",
                   balances={"test-checking": 200.0}, holdings=[])
    manual_snapshots = list(db["snapshots"].rows_where(
        "week_of = ? AND source = ?", ["2026-04-13", "manual"]))
    assert len(manual_snapshots) == 2


def test_write_snapshot_rolls_back_on_balance_error(db, sample_accounts_yaml):
    """If inserting balances fails (e.g. unknown account_id), no partial state."""
    from history import write_snapshot, sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-13")

    with pytest.raises(Exception):
        write_snapshot(db, captured_at="t", week_of="2026-04-13", source="weekly",
                       balances={"nonexistent-id": 42}, holdings=[])

    # No snapshot row should have been inserted
    assert list(db["snapshots"].rows) == []
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_snapshot.py -v`
Expected: 6 fails.

**Step 3: Implement write_snapshot**

Add to `history.py`:

```python
def write_snapshot(
    db: Database,
    captured_at: str,
    week_of: str,
    source: str,
    balances: dict[str, float],
    holdings: list[dict],
) -> int:
    """Write a snapshot atomically.

    If source=="weekly" and a snapshot already exists for this week_of, delete
    the old snapshot's balances + holdings rows and the snapshot row itself,
    then insert the new one. All in one transaction.

    For source in {"manual", "backfill"}, multiple snapshots per week are
    permitted — nothing is deleted.

    balances: {history-side account_id: signed balance}
    holdings: list of dicts with keys:
        security_id, ticker, name, type, quantity, price, value,
        history_account_id  (the history-side account ID, pre-resolved)
    """
    with db.conn:  # transaction
        if source == "weekly":
            existing = list(db["snapshots"].rows_where(
                "week_of = ? AND source = ?", [week_of, "weekly"]))
            for old in existing:
                sid = old["id"]
                db.execute("DELETE FROM balances WHERE snapshot_id = ?", [sid])
                db.execute("DELETE FROM holdings WHERE snapshot_id = ?", [sid])
                db.execute("DELETE FROM snapshots WHERE id = ?", [sid])

        # Insert snapshot row
        snapshot_id = db["snapshots"].insert({
            "captured_at": captured_at,
            "week_of": week_of,
            "source": source,
        }).last_pk

        # Upsert securities (idempotent)
        seen_securities = set()
        for h in holdings:
            sid_sec = h["security_id"]
            if sid_sec in seen_securities:
                continue
            seen_securities.add(sid_sec)
            db["securities"].insert({
                "id": sid_sec,
                "ticker": h.get("ticker"),
                "name": h.get("name", "Unknown"),
                "type": h.get("type"),
            }, replace=True)

        # Insert balance rows (FK enforced)
        balance_rows = [{
            "snapshot_id": snapshot_id,
            "account_id": aid,
            "balance": bal,
        } for aid, bal in balances.items()]
        db["balances"].insert_all(balance_rows)

        # Insert holdings rows
        holding_rows = [{
            "snapshot_id": snapshot_id,
            "account_id": h["history_account_id"],
            "security_id": h["security_id"],
            "quantity": h.get("quantity", 0),
            "price": h.get("price", 0),
            "value": h.get("value", 0),
        } for h in holdings]
        if holding_rows:
            db["holdings"].insert_all(holding_rows)

    return snapshot_id
```

**Note:** Enable foreign keys. Add at top of `history.py`:

```python
def init_schema(db: Database) -> None:
    db.conn.execute("PRAGMA foreign_keys = ON")
    # ... rest of existing init_schema body
```

Also update `conftest.py` `db` fixture to enable foreign keys:

```python
@pytest.fixture
def db():
    from history import init_schema
    conn = sqlite_utils.Database(memory=True)
    conn.conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_snapshot.py -v`
Expected: 6 passed.

**Step 5: Commit**

```bash
git add history.py tests/test_history_snapshot.py tests/conftest.py
git commit -m "feat(history): snapshot writer with idempotent weekly replace

All writes in one transaction. source=weekly is upsert-by-week; manual
and backfill stack. Rollback on FK violation keeps DB consistent."
```

---

### Task 12: Holdings account-id resolver

**Files:**

- Modify: `history.py`
- Create: `tests/test_history_holdings_resolver.py`

**Step 1: Write failing tests**

Holdings come from Plaid keyed by `(institution, account_mask)`. We need to
resolve them to history-side IDs by matching against `accounts.yaml`.

```python
# tests/test_history_holdings_resolver.py
def test_resolve_holdings_to_history_ids_by_institution_and_mask():
    from history import resolve_holdings_account_ids

    yaml_accounts = [
        {"institution": "TestBroker", "mask": "5678", "id": "test-brokerage"},
        {"institution": "OtherBroker", "mask": "9999", "id": "other"},
    ]
    holdings = [
        {"institution": "TestBroker", "account_mask": "5678",
         "security_id": "s1", "quantity": 1, "price": 10, "value": 10,
         "name": "X", "ticker": "X", "type": "etf"},
    ]

    result = resolve_holdings_account_ids(holdings, yaml_accounts)
    assert result[0]["history_account_id"] == "test-brokerage"


def test_resolve_holdings_drops_unmapped():
    """Holdings whose (institution, mask) has no matching yaml entry are
    dropped with a warning, not an error."""
    from history import resolve_holdings_account_ids
    yaml_accounts = [{"institution": "Known", "mask": "1111", "id": "known"}]
    holdings = [
        {"institution": "Unknown", "account_mask": "9999",
         "security_id": "s", "quantity": 1, "price": 1, "value": 1,
         "name": "", "ticker": "", "type": ""},
    ]
    result = resolve_holdings_account_ids(holdings, yaml_accounts)
    assert result == []


def test_resolve_holdings_aggregates_multiple_plaid_accounts_sharing_id():
    """Two Plaid accounts with different masks but the same yaml id both
    resolve to that id (no filtering by uniqueness)."""
    from history import resolve_holdings_account_ids
    yaml_accounts = [
        {"institution": "Merrill", "mask": "2299", "id": "ml-retirement-andrew"},
        {"institution": "Merrill", "mask": "9817", "id": "ml-retirement-andrew"},
    ]
    holdings = [
        {"institution": "Merrill", "account_mask": "2299",
         "security_id": "s1", "quantity": 1, "price": 100, "value": 100,
         "name": "", "ticker": "", "type": ""},
        {"institution": "Merrill", "account_mask": "9817",
         "security_id": "s2", "quantity": 1, "price": 50, "value": 50,
         "name": "", "ticker": "", "type": ""},
    ]
    result = resolve_holdings_account_ids(holdings, yaml_accounts)
    assert len(result) == 2
    assert all(h["history_account_id"] == "ml-retirement-andrew" for h in result)
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_holdings_resolver.py -v`
Expected: 3 fails.

**Step 3: Implement**

Add to `history.py`:

```python
def resolve_holdings_account_ids(
    holdings: list[dict], yaml_accounts: list[dict],
) -> list[dict]:
    """Add `history_account_id` to each holding by matching (institution, mask).

    Holdings that don't match anything in yaml_accounts are dropped (printed
    warning, not raised). yaml_accounts is the `accounts:` list from
    accounts.yaml (each entry must have `institution`, `mask`, `id`).
    """
    mapping = {(a["institution"], a["mask"]): a["id"] for a in yaml_accounts}
    out = []
    for h in holdings:
        key = (h.get("institution"), h.get("account_mask"))
        if key not in mapping:
            print(f"Warning: dropping holding for unmapped ({key[0]}, ...{key[1]})")
            continue
        resolved = dict(h)
        resolved["history_account_id"] = mapping[key]
        out.append(resolved)
    return out
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_holdings_resolver.py -v`
Expected: 3 passed.

**Step 5: Commit**

```bash
git add history.py tests/test_history_holdings_resolver.py
git commit -m "feat(history): resolve Plaid holdings to history account IDs

Matches on (institution, mask). Unmapped holdings are dropped with a
warning. Multiple Plaid accounts sharing an ID resolve correctly."
```

---

## Group D — Drive backup

### Task 13: Drive backup — upload with staleness check

**Files:**

- Create: `history_drive.py`
- Create: `tests/test_history_drive.py`

**Step 1: Write failing tests**

```python
# tests/test_history_drive.py
from unittest.mock import MagicMock
import pytest


def test_upload_first_run_creates_file(db):
    from history_drive import upload_db_to_drive
    fake = MagicMock()
    # No existing Drive file
    fake.find_file.return_value = None
    fake.upload_file.return_value = {"id": "drive-id-1", "modifiedTime": "2026-04-13T18:00:00Z"}

    result = upload_db_to_drive(db, local_path="/tmp/history.db",
                                drive_client=fake, drive_folder_id="folder-1")

    assert result["status"] == "uploaded"
    fake.upload_file.assert_called_once()
    # sync_state must record the modifiedTime
    state = dict(db["sync_state"].rows_where(None))
    assert db["sync_state"].get("last_drive_push_iso")["value"] == "2026-04-13T18:00:00Z"


def test_upload_blocks_when_drive_is_newer(db):
    """If Drive's modifiedTime > our last push, abort."""
    from history_drive import upload_db_to_drive
    db["sync_state"].insert({"key": "last_drive_push_iso", "value": "2026-04-06T18:00:00Z"})

    fake = MagicMock()
    fake.find_file.return_value = {"id": "d", "modifiedTime": "2026-04-10T18:00:00Z"}

    result = upload_db_to_drive(db, local_path="/tmp/history.db",
                                drive_client=fake, drive_folder_id="folder-1")
    assert result["status"] == "blocked_stale_local"
    fake.upload_file.assert_not_called()


def test_upload_proceeds_when_drive_matches(db):
    from history_drive import upload_db_to_drive
    db["sync_state"].insert({"key": "last_drive_push_iso", "value": "2026-04-06T18:00:00Z"})

    fake = MagicMock()
    fake.find_file.return_value = {"id": "d", "modifiedTime": "2026-04-06T18:00:00Z"}
    fake.upload_file.return_value = {"id": "d", "modifiedTime": "2026-04-13T18:00:00Z"}

    result = upload_db_to_drive(db, local_path="/tmp/history.db",
                                drive_client=fake, drive_folder_id="folder-1")
    assert result["status"] == "uploaded"
    assert db["sync_state"].get("last_drive_push_iso")["value"] == "2026-04-13T18:00:00Z"


def test_upload_force_bypasses_staleness_check(db):
    from history_drive import upload_db_to_drive
    db["sync_state"].insert({"key": "last_drive_push_iso", "value": "2026-04-06T18:00:00Z"})

    fake = MagicMock()
    fake.find_file.return_value = {"id": "d", "modifiedTime": "2026-04-10T18:00:00Z"}
    fake.upload_file.return_value = {"id": "d", "modifiedTime": "2026-04-13T18:00:00Z"}

    result = upload_db_to_drive(db, local_path="/tmp/history.db",
                                drive_client=fake, drive_folder_id="folder-1",
                                force=True)
    assert result["status"] == "uploaded"


def test_restore_refuses_if_local_exists(tmp_path):
    from history_drive import restore_db_from_drive
    local = tmp_path / "history.db"
    local.write_bytes(b"existing")

    fake = MagicMock()
    with pytest.raises(FileExistsError):
        restore_db_from_drive(local_path=str(local), drive_client=fake,
                              drive_folder_id="folder-1")


def test_restore_force_overwrites(tmp_path):
    from history_drive import restore_db_from_drive
    local = tmp_path / "history.db"
    local.write_bytes(b"old content")

    fake = MagicMock()
    fake.download_file.return_value = b"new content"
    fake.find_file.return_value = {"id": "d"}

    restore_db_from_drive(local_path=str(local), drive_client=fake,
                          drive_folder_id="folder-1", force=True)
    assert local.read_bytes() == b"new content"
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_drive.py -v`
Expected: 6 fails.

**Step 3: Implement history_drive.py**

```python
# history_drive.py
"""Google Drive backup for history.db with staleness check.

The DriveClient is an adapter over google-api-python-client. It's kept as a
Protocol here so tests can mock it freely.
"""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Protocol

from sqlite_utils.db import Database


class DriveClient(Protocol):
    def find_file(self, folder_id: str, name: str) -> dict | None: ...
    def upload_file(self, folder_id: str, name: str, path: str) -> dict: ...
    def download_file(self, file_id: str) -> bytes: ...


FILENAME = "history.db"


def upload_db_to_drive(
    db: Database,
    local_path: str,
    drive_client: DriveClient,
    drive_folder_id: str,
    force: bool = False,
) -> dict:
    """Upload history.db to Drive after verifying no remote drift.

    Returns {status, drive_id, modifiedTime} or {status: "blocked_stale_local"}.
    """
    remote = drive_client.find_file(drive_folder_id, FILENAME)
    last_push_row = db["sync_state"].get("last_drive_push_iso") \
        if _key_exists(db, "last_drive_push_iso") else None
    last_push = last_push_row["value"] if last_push_row else None

    if remote and not force:
        if last_push is None or remote["modifiedTime"] > last_push:
            return {"status": "blocked_stale_local",
                    "drive_modified_time": remote["modifiedTime"],
                    "last_push": last_push}

    result = drive_client.upload_file(drive_folder_id, FILENAME, local_path)
    db["sync_state"].insert({"key": "last_drive_push_iso",
                             "value": result["modifiedTime"]}, replace=True)
    db["sync_state"].insert({"key": "last_drive_push_host",
                             "value": socket.gethostname()}, replace=True)
    return {"status": "uploaded", "drive_id": result["id"],
            "modifiedTime": result["modifiedTime"]}


def restore_db_from_drive(
    local_path: str,
    drive_client: DriveClient,
    drive_folder_id: str,
    force: bool = False,
) -> None:
    """Download history.db from Drive to local_path.

    Refuses if local_path already exists unless force=True.
    """
    p = Path(local_path)
    if p.exists() and not force:
        raise FileExistsError(
            f"{local_path} already exists. Pass force=True to overwrite.")

    remote = drive_client.find_file(drive_folder_id, FILENAME)
    if remote is None:
        raise FileNotFoundError(f"No {FILENAME} found in Drive folder {drive_folder_id}")

    content = drive_client.download_file(remote["id"])
    p.write_bytes(content)


def _key_exists(db: Database, key: str) -> bool:
    try:
        db["sync_state"].get(key)
        return True
    except Exception:
        return False
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_drive.py -v`
Expected: 6 passed.

**Step 5: Commit**

```bash
git add history_drive.py tests/test_history_drive.py
git commit -m "feat(history): Drive upload + restore with staleness check

Blocks upload when Drive's modifiedTime is newer than our last push,
unless --force. Restore refuses existing local DB without --force.
Sync state stored inside history.db so it survives fresh clone."
```

---

### Task 14: Drive client adapter (google-api-python-client wrapper)

**Files:**

- Modify: `history_drive.py` (add `GoogleDriveAdapter` class)
- Create: `tests/test_history_drive_adapter.py`

**Step 1: Write failing test**

```python
# tests/test_history_drive_adapter.py
from unittest.mock import MagicMock, patch


def test_adapter_find_file_queries_by_name_and_parent():
    from history_drive import GoogleDriveAdapter
    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "fid-1", "modifiedTime": "2026-04-13T00:00:00Z"}]
    }
    adapter = GoogleDriveAdapter(service=mock_service)
    result = adapter.find_file("folder-1", "history.db")
    assert result == {"id": "fid-1", "modifiedTime": "2026-04-13T00:00:00Z"}


def test_adapter_find_file_returns_none_when_absent():
    from history_drive import GoogleDriveAdapter
    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": []}
    adapter = GoogleDriveAdapter(service=mock_service)
    assert adapter.find_file("folder-1", "history.db") is None
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_drive_adapter.py -v`
Expected: fails (`GoogleDriveAdapter` not found).

**Step 3: Implement adapter**

Add to `history_drive.py`:

```python
class GoogleDriveAdapter:
    """Thin adapter over googleapiclient's drive service.

    Requires a `service` built via:
        from googleapiclient.discovery import build
        service = build("drive", "v3", credentials=credentials)
    """
    def __init__(self, service):
        self.service = service

    def find_file(self, folder_id: str, name: str) -> dict | None:
        q = f"'{folder_id}' in parents and name='{name}' and trashed=false"
        resp = self.service.files().list(
            q=q, fields="files(id, modifiedTime)", pageSize=10
        ).execute()
        files = resp.get("files", [])
        if not files:
            return None
        # If multiple exist (shouldn't), pick the most recently modified.
        files.sort(key=lambda f: f["modifiedTime"], reverse=True)
        return files[0]

    def upload_file(self, folder_id: str, name: str, path: str) -> dict:
        from googleapiclient.http import MediaFileUpload
        existing = self.find_file(folder_id, name)
        media = MediaFileUpload(path, mimetype="application/x-sqlite3")
        if existing:
            resp = self.service.files().update(
                fileId=existing["id"], media_body=media,
                fields="id, modifiedTime"
            ).execute()
        else:
            body = {"name": name, "parents": [folder_id]}
            resp = self.service.files().create(
                body=body, media_body=media, fields="id, modifiedTime"
            ).execute()
        return resp

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
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_drive_adapter.py -v`
Expected: 2 passed.

**Step 5: Commit**

```bash
git add history_drive.py tests/test_history_drive_adapter.py
git commit -m "feat(history): GoogleDriveAdapter wrapping google-api-python-client

Implements the DriveClient protocol for real Drive I/O. Only tested for
the find_file paths; upload/download are integration surfaces that
require a real service account and are covered by manual QA."
```

---

## Group E — CLI

### Task 15: CLI skeleton with `click`

**Files:**

- Create: `balance_history.py`
- Create: `tests/test_cli.py`

**Step 1: Write failing test**

```python
# tests/test_cli.py
from click.testing import CliRunner


def test_cli_help_lists_subcommands():
    from balance_history import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("snapshot", "diff", "snapshots", "annotate",
                "backfill", "restore-from-drive"):
        assert cmd in result.output
```

**Step 2: Verify fail**

Run: `pytest tests/test_cli.py -v`
Expected: fail (`ImportError: balance_history`).

**Step 3: Implement CLI skeleton**

```python
#!/usr/bin/env python
# balance_history.py
"""CLI for balance history queries and capture.

See docs/plans/2026-04-13-balance-history-design.md for design.
"""
from __future__ import annotations

import click


@click.group()
def cli():
    """Balance history — capture and query weekly net-worth snapshots."""
    pass


@cli.command()
@click.option("--source", default="weekly",
              type=click.Choice(["weekly", "manual"]))
def snapshot(source):
    """Capture a snapshot of current sheet balances + Plaid holdings."""
    click.echo(f"snapshot: not yet implemented (source={source})")


@cli.command()
@click.option("--weeks-back", type=int, default=1)
@click.option("--week-a", type=str, default=None)
@click.option("--week-b", type=str, default=None)
def diff(weeks_back, week_a, week_b):
    """Show per-account delta between two weeks."""
    click.echo("diff: not yet implemented")


@cli.command()
@click.option("--limit", type=int, default=10)
def snapshots(limit):
    """List recent snapshots."""
    click.echo("snapshots: not yet implemented")


@cli.command()
@click.argument("account_id")
@click.argument("week_of")
@click.argument("note", required=False)
@click.option("--delete", is_flag=True)
def annotate(account_id, week_of, note, delete):
    """Add or delete a note for an (account, week)."""
    click.echo("annotate: not yet implemented")


@cli.command()
@click.option("--week", type=str, required=True)
@click.option("--from-sheet", is_flag=True, required=True)
def backfill(week, from_sheet):
    """Backfill a past week from the sheet's current values."""
    click.echo("backfill: not yet implemented")


@cli.command(name="restore-from-drive")
@click.option("--force", is_flag=True)
def restore_from_drive(force):
    """Download history.db from Drive. Refuses to overwrite without --force."""
    click.echo("restore-from-drive: not yet implemented")


if __name__ == "__main__":
    cli()
```

**Step 4: Verify pass**

Run: `pytest tests/test_cli.py -v`
Expected: 1 passed.

Also verify: `python balance_history.py --help` shows all six subcommands.

**Step 5: Commit**

```bash
git add balance_history.py tests/test_cli.py
git commit -m "feat(cli): balance_history.py skeleton with click

All six subcommands registered with stub implementations. Help output
lists them. Each will be wired up in subsequent tasks."
```

---

### Task 16: Wire `snapshot` command

**Files:**

- Modify: `balance_history.py` (implement `snapshot` command body)
- Modify: `tests/test_cli.py`
- Create: `tests/test_cli_snapshot.py`

**Step 1: Write failing test**

```python
# tests/test_cli_snapshot.py
from unittest.mock import patch, MagicMock
from click.testing import CliRunner


def test_snapshot_command_calls_write_snapshot(tmp_path, monkeypatch):
    """Integration-ish: mocks at the edges, verifies the full call flow."""
    from balance_history import cli

    # Set up a temp DB path and accounts.yaml
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))

    # Minimal accounts.yaml
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text("""
spreadsheet_id: "test"
accounts:
  - institution: "TestBank"
    name: "Checking"
    mask: "1234"
    id: "test-checking"
    label: "Test Checking"
    type: asset
manual_accounts: []
""")
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    # Mock the sheet-reader and holdings-fetcher
    with patch("balance_history.read_balances_from_sheet") as mock_sheet, \
         patch("balance_history.fetch_all_holdings_for_snapshot") as mock_holdings, \
         patch("balance_history.upload_db_to_drive") as mock_drive, \
         patch("balance_history.make_sheet_client") as mock_sheet_client, \
         patch("balance_history.load_plaid_items") as mock_items:
        mock_sheet.return_value = {"test-checking": 1234.56}
        mock_holdings.return_value = []
        mock_drive.return_value = {"status": "uploaded"}
        mock_items.return_value = {}
        mock_sheet_client.return_value = MagicMock()

        runner = CliRunner()
        result = runner.invoke(cli, ["snapshot"])

    assert result.exit_code == 0, result.output
    assert db_path.exists()
    # Snapshot written
    import sqlite_utils
    db = sqlite_utils.Database(str(db_path))
    assert db["snapshots"].count == 1
    assert db["balances"].count == 1
```

**Step 2: Verify fail**

Run: `pytest tests/test_cli_snapshot.py -v`
Expected: fail.

**Step 3: Implement `snapshot` fully**

Rewrite `balance_history.py`'s `snapshot` command and add helpers:

```python
# balance_history.py (additions)
import os
from datetime import datetime, timezone
from pathlib import Path

import sqlite_utils

from history import (
    init_schema, sync_accounts_from_yaml, write_snapshot,
    monday_of, resolve_holdings_account_ids,
)
from history_sheet import read_balances_from_sheet
from history_drive import upload_db_to_drive, GoogleDriveAdapter


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
    """Return an object with a get_values(spreadsheet_id, range_) method.

    In production this will be a tiny wrapper around mcp-google-sheets or
    the google-api-python-client. For now, import-time resolution.
    """
    from google_sheets_client import SheetsClient  # implemented later
    return SheetsClient()


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


@cli.command()
@click.option("--source", default="weekly",
              type=click.Choice(["weekly", "manual"]))
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
        from google_drive_client import build_drive_adapter, load_drive_folder_id
    except ImportError:
        return
    try:
        adapter = build_drive_adapter()
        folder_id = load_drive_folder_id()
        result = upload_db_to_drive(db, _db_path(), adapter, folder_id)
        click.echo(f"Drive backup: {result['status']}")
    except Exception as e:
        click.echo(f"Drive backup failed (non-fatal): {e}", err=True)
```

**Note:** `google_sheets_client` and `google_drive_client` are small adapter
modules that will be stubbed in the next task. The test mocks them so this task
can proceed without their real implementation existing yet.

**Step 4: Verify pass**

Run: `pytest tests/test_cli_snapshot.py -v`
Expected: 1 passed.

**Step 5: Commit**

```bash
git add balance_history.py tests/test_cli_snapshot.py
git commit -m "feat(cli): implement snapshot command end-to-end

Syncs accounts, reads sheet balances, fetches Plaid holdings, resolves
account IDs, writes snapshot, backs up to Drive (best-effort). Drive
failures are non-fatal — DB write stays local-consistent."
```

---

### Task 17: Sheet + Drive client adapter stubs

**Files:**

- Create: `google_sheets_client.py`
- Create: `google_drive_client.py`

**Step 1: Implement sheet client**

```python
# google_sheets_client.py
"""Adapter: Google Sheets access for history.py.

Reads config.yaml for the service account path; uses google-api-python-client
for sheets reads. Wraps the Sheets v4 values().get() endpoint.
"""
from __future__ import annotations

import yaml
from pathlib import Path
from googleapiclient.discovery import build
from google.oauth2 import service_account


def _load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


class SheetsClient:
    def __init__(self):
        cfg = _load_config()
        creds = service_account.Credentials.from_service_account_file(
            cfg["service_account_path"],
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        self.service = build("sheets", "v4", credentials=creds)

    def get_values(self, spreadsheet_id: str, range_: str) -> list[list]:
        resp = self.service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_
        ).execute()
        return resp.get("values", [])
```

**Step 2: Implement drive client**

```python
# google_drive_client.py
"""Adapter: Google Drive access for history_drive.py."""
from __future__ import annotations

import yaml
from pathlib import Path
from googleapiclient.discovery import build
from google.oauth2 import service_account

from history_drive import GoogleDriveAdapter


def _load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text())


def load_drive_folder_id() -> str:
    return _load_config()["drive_folder_id"]


def build_drive_adapter() -> GoogleDriveAdapter:
    cfg = _load_config()
    creds = service_account.Credentials.from_service_account_file(
        cfg["service_account_path"],
        scopes=["https://www.googleapis.com/auth/drive"])
    service = build("drive", "v3", credentials=creds)
    return GoogleDriveAdapter(service=service)
```

**Step 3: Manual smoke test**

Run: `source .venv/bin/activate && python balance_history.py --help`
Expected: help listing all subcommands, no import errors.

**Step 4: Commit**

```bash
git add google_sheets_client.py google_drive_client.py
git commit -m "feat(clients): minimal Google Sheets + Drive adapters

Loads service account from config.yaml. Read-only Sheets scope; full
Drive scope for the upload path."
```

---

### Task 18: Wire `diff` command

**Files:**

- Modify: `balance_history.py`
- Modify: `history.py` (add `weekly_diff` query)
- Create: `tests/test_history_queries.py`
- Modify: `tests/test_cli.py`

**Step 1: Write failing tests**

```python
# tests/test_history_queries.py
def test_weekly_diff_basic(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")

    write_snapshot(db, captured_at="t1", week_of="2026-04-06", source="weekly",
                   balances={"test-checking": 1000.0, "test-brokerage": 50000.0},
                   holdings=[])
    write_snapshot(db, captured_at="t2", week_of="2026-04-13", source="weekly",
                   balances={"test-checking": 1500.0, "test-brokerage": 51000.0},
                   holdings=[])

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    by_id = {r["id"]: r for r in result}
    assert by_id["test-checking"]["delta"] == 500
    assert by_id["test-brokerage"]["delta"] == 1000


def test_weekly_diff_returns_zero_for_unchanged(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")

    write_snapshot(db, captured_at="t1", week_of="2026-04-06", source="weekly",
                   balances={"test-checking": 1000.0}, holdings=[])
    write_snapshot(db, captured_at="t2", week_of="2026-04-13", source="weekly",
                   balances={"test-checking": 1000.0}, holdings=[])

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    assert result[0]["delta"] == 0


def test_weekly_diff_includes_holdings_decomposition(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")

    h_old = [{"security_id": "s1", "ticker": "X", "name": "X", "type": "etf",
              "quantity": 100, "price": 50, "value": 5000,
              "history_account_id": "test-brokerage"}]
    h_new = [{"security_id": "s1", "ticker": "X", "name": "X", "type": "etf",
              "quantity": 120, "price": 55, "value": 6600,
              "history_account_id": "test-brokerage"}]

    write_snapshot(db, captured_at="t1", week_of="2026-04-06", source="weekly",
                   balances={"test-brokerage": 5000.0}, holdings=h_old)
    write_snapshot(db, captured_at="t2", week_of="2026-04-13", source="weekly",
                   balances={"test-brokerage": 6600.0}, holdings=h_new)

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    b = [r for r in result if r["id"] == "test-brokerage"][0]
    assert b["delta"] == 1600
    assert b["market"] == 500       # 100 × (55-50)
    assert b["flow"] == 1100        # (120-100) × 55


def test_weekly_diff_attaches_note_from_notes_table(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, weekly_diff, upsert_note
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")
    write_snapshot(db, captured_at="t1", week_of="2026-04-06", source="weekly",
                   balances={"test-checking": 1000.0}, holdings=[])
    write_snapshot(db, captured_at="t2", week_of="2026-04-13", source="weekly",
                   balances={"test-checking": 1500.0}, holdings=[])
    upsert_note(db, "test-checking", "2026-04-13", "paycheck")

    result = weekly_diff(db, "2026-04-06", "2026-04-13")
    b = [r for r in result if r["id"] == "test-checking"][0]
    assert b["note"] == "paycheck"
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_queries.py -v`
Expected: 4 fails.

**Step 3: Implement `weekly_diff` and `upsert_note`**

Add to `history.py`:

```python
from datetime import datetime, timezone


def upsert_note(db: Database, account_id: str, week_of: str, note: str) -> None:
    """Insert or replace a note for (account, week)."""
    db["notes"].insert({
        "account_id": account_id,
        "week_of": week_of,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, replace=True)


def delete_note(db: Database, account_id: str, week_of: str) -> None:
    db["notes"].delete((account_id, week_of))


def weekly_diff(db: Database, week_a: str, week_b: str) -> list[dict]:
    """Return per-account deltas between two weekly snapshots.

    Each row: {id, label, type, old, new, delta, market, flow, note}
    market/flow are only populated for accounts with holdings at both weeks.
    """
    sql = """
        SELECT a.id, a.label, a.type,
               b1.balance AS old_balance,
               b2.balance AS new_balance
        FROM accounts a
        LEFT JOIN balances b1 ON b1.account_id = a.id AND b1.snapshot_id =
            (SELECT id FROM snapshots WHERE week_of = ? AND source='weekly' LIMIT 1)
        LEFT JOIN balances b2 ON b2.account_id = a.id AND b2.snapshot_id =
            (SELECT id FROM snapshots WHERE week_of = ? AND source='weekly' LIMIT 1)
        WHERE b1.balance IS NOT NULL OR b2.balance IS NOT NULL
    """
    rows = list(db.query(sql, [week_a, week_b]))
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
            "note": None,
        }
        market, flow = _decompose_account(db, r["id"], week_a, week_b)
        item["market"] = market
        item["flow"] = flow
        note_row = db["notes"].rows_where("account_id = ? AND week_of = ?",
                                          [r["id"], week_b])
        for nr in note_row:
            item["note"] = nr["note"]
        out.append(item)
    return out


def _decompose_account(db, account_id, week_a, week_b):
    """Sum market/flow across securities held in either week for this account."""
    sql = """
        SELECT h1.security_id,
               COALESCE(h1.quantity, 0) AS qo,
               COALESCE(h1.price, 0) AS po,
               COALESCE(h2.quantity, 0) AS qn,
               COALESCE(h2.price, 0) AS pn
        FROM (
            SELECT * FROM holdings
            WHERE account_id = ? AND snapshot_id =
                (SELECT id FROM snapshots WHERE week_of = ? AND source='weekly' LIMIT 1)
        ) h1
        FULL OUTER JOIN (
            SELECT * FROM holdings
            WHERE account_id = ? AND snapshot_id =
                (SELECT id FROM snapshots WHERE week_of = ? AND source='weekly' LIMIT 1)
        ) h2 ON h1.security_id = h2.security_id
    """
    # SQLite doesn't support FULL OUTER JOIN natively — use UNION workaround:
    sql = """
        SELECT h.security_id,
               COALESCE(ho.quantity, 0) AS qo, COALESCE(ho.price, 0) AS po,
               COALESCE(hn.quantity, 0) AS qn, COALESCE(hn.price, 0) AS pn
        FROM (
            SELECT security_id FROM holdings WHERE account_id = ? AND snapshot_id IN
                (SELECT id FROM snapshots WHERE week_of IN (?, ?) AND source='weekly')
            GROUP BY security_id
        ) h
        LEFT JOIN holdings ho ON ho.security_id = h.security_id AND ho.account_id = ?
            AND ho.snapshot_id = (SELECT id FROM snapshots WHERE week_of = ? AND source='weekly' LIMIT 1)
        LEFT JOIN holdings hn ON hn.security_id = h.security_id AND hn.account_id = ?
            AND hn.snapshot_id = (SELECT id FROM snapshots WHERE week_of = ? AND source='weekly' LIMIT 1)
    """
    rows = list(db.query(sql, [account_id, week_a, week_b,
                               account_id, week_a, account_id, week_b]))
    if not rows:
        return None, None
    market_total = 0.0
    flow_total = 0.0
    for r in rows:
        d = decompose_security(r["qo"], r["po"], r["qn"], r["pn"])
        market_total += d["market"]
        flow_total += d["flow"]
    return market_total, flow_total
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_queries.py -v`
Expected: 4 passed.

**Step 5: Wire `diff` command in `balance_history.py`**

Replace stub `diff`:

```python
from rich.console import Console
from rich.table import Table

console = Console()


@cli.command()
@click.option("--weeks-back", type=int, default=1)
@click.option("--week-a", type=str, default=None)
@click.option("--week-b", type=str, default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def diff(weeks_back, week_a, week_b, as_json):
    """Show per-account delta between two weekly snapshots."""
    from datetime import datetime, timezone, timedelta
    from history import weekly_diff
    db = _open_db()
    # Resolve weeks
    if not week_b:
        week_b = monday_of(datetime.now(timezone.utc))
    if not week_a:
        week_a_dt = datetime.fromisoformat(week_b) - timedelta(weeks=weeks_back)
        week_a = monday_of(week_a_dt.replace(tzinfo=timezone.utc))

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
        delta_str = f"[green]+${r['delta']:,.2f}[/green]" if r["delta"] > 0 \
            else f"[red]${r['delta']:,.2f}[/red]" if r["delta"] < 0 \
            else "—"
        market = f"${r['market']:,.0f}" if r["market"] else ""
        flow = f"${r['flow']:,.0f}" if r["flow"] else ""
        table.add_row(r["label"], f"${r['old']:,.2f}", f"${r['new']:,.2f}",
                      delta_str, market, flow, r["note"] or "")
    table.add_section()
    total_str = f"[green]+${total_delta:,.2f}[/green]" if total_delta > 0 \
        else f"[red]${total_delta:,.2f}[/red]"
    table.add_row("[bold]Net change[/bold]", "", "", total_str, "", "", "")
    console.print(table)
```

**Step 6: Update test_cli.py to cover diff help**

```python
# tests/test_cli.py (add)
def test_cli_diff_help_has_options():
    from balance_history import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["diff", "--help"])
    assert result.exit_code == 0
    assert "--weeks-back" in result.output
    assert "--week-a" in result.output
    assert "--json" in result.output
```

**Step 7: Verify pass**

Run: `pytest tests/test_history_queries.py tests/test_cli.py -v`
Expected: all pass.

**Step 8: Commit**

```bash
git add history.py balance_history.py tests/test_history_queries.py tests/test_cli.py
git commit -m "feat(cli): diff command with market-vs-flow decomposition

Queries weekly_diff across any two weeks. Sorts by absolute delta.
Rich-rendered table with colored deltas; --json for scripting."
```

---

### Task 19: Wire `snapshots` command (list)

**Files:**

- Modify: `balance_history.py`
- Modify: `history.py` (add `list_snapshots`)
- Modify: `tests/test_history_queries.py` (add test)

**Step 1: Write failing test**

```python
# Append to tests/test_history_queries.py
def test_list_snapshots_returns_most_recent_first(db, sample_accounts_yaml):
    from history import sync_accounts_from_yaml, write_snapshot, list_snapshots
    sync_accounts_from_yaml(db, str(sample_accounts_yaml), today="2026-04-06")
    write_snapshot(db, captured_at="2026-04-06T18:00:00Z", week_of="2026-04-06",
                   source="weekly", balances={"test-checking": 1.0}, holdings=[])
    write_snapshot(db, captured_at="2026-04-13T18:00:00Z", week_of="2026-04-13",
                   source="weekly", balances={"test-checking": 2.0}, holdings=[])
    rows = list_snapshots(db, limit=10)
    assert len(rows) == 2
    assert rows[0]["week_of"] == "2026-04-13"
    assert rows[1]["week_of"] == "2026-04-06"
```

**Step 2: Verify fail**

Run: `pytest tests/test_history_queries.py::test_list_snapshots_returns_most_recent_first -v`
Expected: fail.

**Step 3: Implement**

Add to `history.py`:

```python
def list_snapshots(db: Database, limit: int = 10) -> list[dict]:
    return list(db.query(
        "SELECT * FROM snapshots ORDER BY captured_at DESC LIMIT ?", [limit]))
```

Update `balance_history.py` `snapshots` command:

```python
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
```

**Step 4: Verify pass**

Run: `pytest tests/test_history_queries.py -v`
Expected: all pass.

**Step 5: Commit**

```bash
git add history.py balance_history.py tests/test_history_queries.py
git commit -m "feat(cli): snapshots command lists recent captures"
```

---

### Task 20: Wire `annotate` command

**Files:**

- Modify: `balance_history.py`
- Add to: `tests/test_cli.py`

**Step 1: Write failing test**

```python
# tests/test_cli.py (append)
def test_annotate_creates_note(tmp_path, monkeypatch):
    from balance_history import cli
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text("""
spreadsheet_id: "t"
accounts:
  - institution: "B"
    name: "C"
    mask: "1"
    id: "foo"
    label: "Foo"
    type: asset
manual_accounts: []
""")
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    # First, init DB with accounts synced
    import balance_history
    db = balance_history._open_db()
    from history import sync_accounts_from_yaml
    sync_accounts_from_yaml(db, str(accounts_yaml), today="2026-04-13")

    runner = CliRunner()
    result = runner.invoke(cli, ["annotate", "foo", "2026-04-13", "test note"])
    assert result.exit_code == 0

    import sqlite_utils
    db2 = sqlite_utils.Database(str(db_path))
    note = db2["notes"].get(("foo", "2026-04-13"))
    assert note["note"] == "test note"


def test_annotate_delete_removes_note(tmp_path, monkeypatch):
    from balance_history import cli
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text("""
spreadsheet_id: "t"
accounts:
  - institution: "B"
    name: "C"
    mask: "1"
    id: "foo"
    label: "Foo"
    type: asset
manual_accounts: []
""")
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))
    import balance_history
    db = balance_history._open_db()
    from history import sync_accounts_from_yaml, upsert_note
    sync_accounts_from_yaml(db, str(accounts_yaml), today="2026-04-13")
    upsert_note(db, "foo", "2026-04-13", "to be deleted")
    db.conn.commit()

    runner = CliRunner()
    result = runner.invoke(cli, ["annotate", "foo", "2026-04-13", "--delete"])
    assert result.exit_code == 0

    import sqlite_utils
    db2 = sqlite_utils.Database(str(db_path))
    assert db2["notes"].count == 0
```

**Step 2: Verify fail**

Run: `pytest tests/test_cli.py::test_annotate_creates_note tests/test_cli.py::test_annotate_delete_removes_note -v`
Expected: 2 fails.

**Step 3: Implement `annotate`**

Replace stub in `balance_history.py`:

```python
@cli.command()
@click.argument("account_id")
@click.argument("week_of")
@click.argument("note", required=False)
@click.option("--delete", is_flag=True)
def annotate(account_id, week_of, note, delete):
    """Add, replace, or delete a note for an (account, week)."""
    from history import upsert_note, delete_note
    db = _open_db()
    if delete:
        delete_note(db, account_id, week_of)
        click.echo(f"Deleted note for {account_id} @ {week_of}")
        return
    if not note:
        raise click.UsageError("Provide a note argument or pass --delete")
    upsert_note(db, account_id, week_of, note)
    click.echo(f"Set note for {account_id} @ {week_of}: {note}")
```

**Step 4: Verify pass**

Run: `pytest tests/test_cli.py -v`
Expected: all pass.

**Step 5: Commit**

```bash
git add balance_history.py tests/test_cli.py
git commit -m "feat(cli): annotate command (INSERT OR REPLACE + --delete)"
```

---

### Task 21: Wire `backfill` command with interactive warning

**Files:**

- Modify: `balance_history.py`
- Add: `tests/test_cli_backfill.py`

**Step 1: Write failing test**

```python
# tests/test_cli_backfill.py
from unittest.mock import patch, MagicMock
from click.testing import CliRunner


def test_backfill_prompts_user_and_aborts_on_no(tmp_path, monkeypatch):
    from balance_history import cli
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text("""
spreadsheet_id: "t"
accounts: []
manual_accounts: []
""")
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    runner = CliRunner()
    # "n" answers the confirmation prompt
    result = runner.invoke(cli, ["backfill", "--week", "2026-04-06", "--from-sheet"],
                           input="n\n")
    assert "column D" in result.output.lower() or "prep for new week" in result.output.lower()
    assert "abort" in result.output.lower() or result.exit_code != 0


def test_backfill_writes_snapshot_with_source_backfill(tmp_path, monkeypatch):
    from balance_history import cli
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("HISTORY_DB_PATH", str(db_path))
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text("""
spreadsheet_id: "t"
accounts:
  - institution: "B"
    name: "C"
    mask: "1"
    id: "foo"
    label: "Foo"
    type: asset
manual_accounts: []
""")
    monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(accounts_yaml))

    with patch("balance_history.read_balances_from_sheet") as mock_sheet, \
         patch("balance_history.make_sheet_client") as mock_client:
        mock_sheet.return_value = {"foo": 100.0}
        mock_client.return_value = MagicMock()

        runner = CliRunner()
        result = runner.invoke(cli, ["backfill", "--week", "2026-04-06", "--from-sheet"],
                               input="y\n")

    assert result.exit_code == 0, result.output
    import sqlite_utils
    db = sqlite_utils.Database(str(db_path))
    snapshot = list(db["snapshots"].rows)[0]
    assert snapshot["source"] == "backfill"
    assert snapshot["week_of"] == "2026-04-06"
```

**Step 2: Verify fail**

Run: `pytest tests/test_cli_backfill.py -v`
Expected: 2 fails.

**Step 3: Implement `backfill`**

```python
@cli.command()
@click.option("--week", type=str, required=True)
@click.option("--from-sheet", is_flag=True, required=True)
def backfill(week, from_sheet):
    """Backfill a past week from the sheet's current values."""
    import yaml
    from datetime import datetime, timezone
    from history import sync_accounts_from_yaml, write_snapshot

    click.echo(
        "⚠ Column D of the sheet holds whatever 'Prep for New Week' last captured —\n"
        "  possibly older than one week. Verify column D is accurate for this backfill\n"
        f"  week ({week}) before continuing. (Check Last Modified in F1.)\n"
    )
    if not click.confirm("Continue?", default=False):
        click.echo("Aborted.")
        raise click.Abort()

    db = _open_db()
    yaml_path = _yaml_path()
    sync_accounts_from_yaml(db, yaml_path, today=datetime.now().strftime("%Y-%m-%d"))

    with open(yaml_path) as f:
        yaml_data = yaml.safe_load(f)
    spreadsheet_id = yaml_data["spreadsheet_id"]

    sheet_client = make_sheet_client()
    balances = read_balances_from_sheet(sheet_client, spreadsheet_id, "Net Worth")

    snapshot_id = write_snapshot(
        db,
        captured_at=datetime.now(timezone.utc).isoformat(),
        week_of=week,
        source="backfill",
        balances=balances,
        holdings=[],
    )
    click.echo(f"Backfill snapshot {snapshot_id} written for week {week}.")
```

**Step 4: Verify pass**

Run: `pytest tests/test_cli_backfill.py -v`
Expected: 2 passed.

**Step 5: Commit**

```bash
git add balance_history.py tests/test_cli_backfill.py
git commit -m "feat(cli): backfill command with column-D warning

Requires interactive y/N confirmation before writing a backfill snapshot,
since column D is the stale surface that motivated this whole design."
```

---

### Task 22: Wire `restore-from-drive` command

**Files:**

- Modify: `balance_history.py`

**Step 1: Implement command body**

```python
@cli.command(name="restore-from-drive")
@click.option("--force", is_flag=True)
def restore_from_drive(force):
    """Download history.db from Drive. Refuses existing local DB without --force."""
    from history_drive import restore_db_from_drive
    from google_drive_client import build_drive_adapter, load_drive_folder_id

    adapter = build_drive_adapter()
    folder_id = load_drive_folder_id()
    try:
        restore_db_from_drive(local_path=_db_path(), drive_client=adapter,
                              drive_folder_id=folder_id, force=force)
        click.echo(f"Restored history.db to {_db_path()}")
    except FileExistsError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Pass --force to overwrite.", err=True)
        raise click.Abort()
```

**Step 2: Manual smoke test**

Run: `python balance_history.py restore-from-drive --help`
Expected: help text with --force option.

**Step 3: Commit**

```bash
git add balance_history.py
git commit -m "feat(cli): restore-from-drive command"
```

---

## Group F — Integration

### Task 23: Add end-of-run nudge to plaid_balance.py --force

**Files:**

- Modify: `plaid_balance.py`

**Step 1: Write failing test**

Not strictly testable (output-only feature), so smoke-test manually.

**Step 2: Implement nudge**

At the end of `plaid_balance.py` main flow after `--force`, add:

```python
if args.force:
    # ... existing Plaid fetch and sheet update ...

    # End-of-run nudge: remind user to snapshot after manual entries
    try:
        import yaml
        cfg = yaml.safe_load(open("accounts.yaml"))
        manual_ids = [m["id"] for m in cfg.get("manual_accounts", [])]
        n_manual = len(manual_ids)
    except Exception:
        n_manual = 0

    print()
    print(f"✔ Updated automated rows in the spreadsheet.")
    if n_manual:
        print(f"ℹ {n_manual} manual rows may still need attention: "
              + ", ".join(manual_ids[:5])
              + ("..." if n_manual > 5 else ""))
    print("→ After entering any manual balances, run:")
    print("    python balance_history.py snapshot")
```

**Step 3: Smoke test**

Run: `python plaid_balance.py --check`
Expected: no errors (nudge only fires on --force).

**Step 4: Commit**

```bash
git add plaid_balance.py
git commit -m "feat(plaid): end-of-run nudge to snapshot after manual entries

Reminds the user that manual accounts still need attention and that the
history DB only updates via balance_history.py snapshot (not --force)."
```

---

### Task 24: README + CLAUDE.md updates

**Files:**

- Modify: `README.md`
- Modify: `CLAUDE.md`

**Step 1: Update README with new workflow**

Add a new section after "Commands" describing the weekly workflow including
`balance_history.py snapshot` and `balance_history.py diff`. Include a brief
example showing what `diff` output looks like.

**Step 2: Update CLAUDE.md**

Add a short section explaining:

- `balance_history.py` exists for history queries
- The weekly workflow: `--force` → enter manual balances → `snapshot` → `diff`
- `history.db` is gitignored and backed up to Drive

**Step 3: Smoke test**

Run: `markdownlint README.md CLAUDE.md`
Expected: clean, or auto-fixed.

**Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document balance_history.py weekly workflow

Adds 'History & deltas' section to README and updates CLAUDE.md with the
Plaid fetch → manual entry → snapshot → diff sequence."
```

---

### Task 25: Backfill last 1–2 weeks from the current sheet state

**Files:**

- None (data-only operation; commands executed interactively)

**Step 1: Backfill last week from column D**

Run:

```bash
python balance_history.py backfill --week 2026-04-06 --from-sheet
# Respond "n" the first time to confirm the warning fires.
# Then run again and respond "y".
```

**Step 2: Take a weekly snapshot for this week**

Run:

```bash
python balance_history.py snapshot
```

**Step 3: Verify `diff` works**

Run:

```bash
python balance_history.py diff
```

Expected: a table showing per-account deltas between 2026-04-06 (backfill)
and 2026-04-13 (weekly).

**Step 4: Verify Drive backup**

Run:

```bash
ls -la history.db
# Manually check the Drive folder for history.db
```

**Step 5: Commit nothing**

This task is data-only; `history.db` is gitignored. No commit needed.

---

## Finishing up

After Task 25:

1. Run the full test suite: `pytest -v` — expect all pass, no warnings.
2. Run the local pre-commit checks: `pre-commit run --config ~/.config/pre-commit/config.yaml --all-files`.
3. Push the branch: `git push -u origin <branch>`.
4. Let pre-push review run. Address any blocking findings.
5. Open PR with summary of all Phase 1 changes.
6. Stop. Do not merge without explicit `merge-lock auth <PR#> "ok"`.

**Out of scope for Phase 1:**

- History tab in the sheet (Phase 2).
- `trend`, `top-movers`, `holdings` commands (Phase 3).
- `--json` flags on commands other than `diff` (Phase 3).
- `plotext` sparkline output (Phase 3).
- Transactions ingestion (future phase).

---

## Acceptance criteria for Phase 1

- [ ] `pytest -v` passes with all tests green.
- [ ] `python balance_history.py --help` lists all six subcommands.
- [ ] `python balance_history.py snapshot` writes a row to `snapshots`,
      multiple rows to `balances`, and (if Plaid investments are connected)
      rows to `holdings` + `securities`.
- [ ] Running `snapshot` twice in the same week replaces the first snapshot,
      doesn't stack.
- [ ] `python balance_history.py diff` shows last-week-vs-this-week deltas with
      color, and market/flow columns for investment accounts.
- [ ] `python balance_history.py annotate <id> <week> "text"` persists a note
      that `diff` subsequently displays.
- [ ] `python balance_history.py backfill --week X --from-sheet` prompts for
      confirmation and writes a `source=backfill` snapshot on y.
- [ ] Drive upload succeeds (or is gracefully skipped if config missing).
- [ ] Staleness check blocks upload when Drive is newer than `sync_state`.
- [ ] `python balance_history.py restore-from-drive` refuses to overwrite
      without `--force`.
- [ ] `plaid_balance.py --force` ends with the snapshot-reminder nudge.
