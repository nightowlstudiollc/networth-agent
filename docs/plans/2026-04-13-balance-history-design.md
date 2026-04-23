# Balance History — Design

**Date:** 2026-04-13
**Status:** Approved for implementation
**Author:** Brainstormed with Claude Code

## Problem

The Net Worth spreadsheet shows a "right now" view of every account, but provides
no usable history. Column D nominally holds "last week's totals" but is set by a
manual "Prep for New Week" step that is easy to forget — meaning the displayed
"Δ vs last week" can silently span multiple weeks. The user recently saw a
+$32,505 weekly delta that turned out to be ~2 weeks of cumulative change, and
had to reconstruct the breakdown manually.

We need to know, for every weekly snapshot:

1. **What** changed (which accounts moved)
2. **By how much** (per-account delta)
3. **Why** (market vs flow for investments; manual annotation for everything else)

We also need to be able to query history beyond "this week vs last week" —
trends, top movers over arbitrary windows, per-security holdings history.

## Non-goals

- Plaid transaction ingestion (the "what spending caused this credit-card move?"
  question). Schema leaves room; deferred to a future phase.
- Multi-machine concurrent writes. Single-user, single-machine model with a
  staleness check is sufficient.
- Web UI, charts, projections, goal tracking, retirement planning.
- Migrating to an off-the-shelf product (Empower, Maybe Finance, Tiller).
  Considered and rejected: keeping the custom-script-feeding-my-own-sheet model
  matches the project's monetizable / open-source vision.

## Cost analysis

Plaid bills two ways:

| API | Pricing model | Current usage |
|---|---|---|
| `accounts/balance/get` | Per-call, $0.10/item | Hit weekly per the existing rate limit |
| `investments/holdings/get` | Subscription (~$0.35/item/month) | Hit on every `--force` for Merrill + SoFi (already paid for) |
| `transactions/sync` | Subscription (~$0.30/item/month) | **Already paid for on every item** — currently unused |

Conclusion: storing balances + holdings per snapshot costs **zero additional
Plaid dollars**. The data is already being fetched on every weekly run and
discarded. Adding transactions later would also cost zero additional dollars.

## Architecture

```
┌─────────────────────┐    ┌──────────────────────────────────────┐
│  plaid_balance.py   │    │       balance_history.py (CLI)       │
│  --force            │    │  snapshot | diff | trend | annotate  │
│  (updates sheet)    │    │                                      │
└─────────────────────┘    └────────────┬────────────┬────────────┘
                                        │            │
                                        │ writes     │ reads
                                        ▼            ▼
   ┌───────────────────────────────────────────────┐
   │  history.db  (SQLite, lives in repo dir)      │
   │   ├── accounts        (id → label/type/inst)  │
   │   ├── snapshots       (one row per capture)   │
   │   ├── balances        (account × snapshot)    │
   │   ├── holdings        (security × snapshot)   │
   │   ├── securities      (security_id → name)    │
   │   └── notes           (id × week → text)      │
   └───────────┬─────────────────────────┬─────────┘
               │                         │
               │ post-write upload       │ on weekly run
               ▼                         ▼
   ┌────────────────────┐   ┌──────────────────────────┐
   │  Google Drive      │   │  History tab             │
   │  history.db backup │   │  (regenerated in sheet)  │
   └────────────────────┘   └──────────────────────────┘
```

**Single source of truth:** `history.db`. CLI and sheet History tab are both
read-only views over it. Writes happen in exactly one place: the
`balance_history.py snapshot` command, which is run at the end of the weekly
workflow after both automated and manual balances are in the sheet.

**No bidirectional sync.** Sheet History tab is regenerated from the DB; manual
annotations enter via CLI. Eliminates whole categories of conflict bugs.

**Drive backup runs after every successful write.** Local DB is canonical; Drive
holds the latest snapshot for recovery and "lives in my Google account"
property. We never read from Drive in normal operation — only an explicit
`restore-from-drive` command pulls it back.

## Storage choice

SQLite file in the repo directory (`history.db`, gitignored), with a copy
pushed to Google Drive after every successful write. Considered and rejected:

- **DB lives in Drive directly** — every read would require download, every
  write upload. SQLite is single-writer; mid-upload corruption is a real risk.
- **Cloud SQL (Postgres)** — overkill at this data volume; ~$10–25/mo for an
  ACID database we use ~weekly.
- **Sheet-only history (extra tabs/columns)** — not queryable, doesn't scale,
  no holdings detail.

Local-first SQLite gives us millisecond queries, no network on the read path,
and Drive backup for recovery. If we ever need true multi-machine concurrency,
graduating to Cloud SQL is a connection-string change; the schema is portable.

## Storage size

| Component | Per year | Decade |
|---|---|---|
| Balances (~30 accounts × 52 weeks × ~50 bytes) | ~80 KB | ~800 KB |
| Holdings (~50 securities × 7 inv accounts × 52 weeks × ~80 bytes) | ~150 KB | ~1.5 MB |
| Snapshots, securities, accounts, notes, indexes | ~250 KB | ~2.5 MB |
| **Total** | **~500 KB** | **~5 MB** |

Trivial for SQLite, trivial for Drive. Do not bother compressing on upload.

## Schema

```sql
-- Stable account registry. One row per ID in column H of the sheet.
CREATE TABLE accounts (
    id            TEXT PRIMARY KEY,        -- e.g. "ml-retirement-andrew"
    label         TEXT NOT NULL,           -- e.g. "ML Retirement"
    type          TEXT NOT NULL,           -- "asset" | "liability"
    institution   TEXT,                    -- nullable for manual accounts
    is_manual     INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT NOT NULL,           -- ISO date
    retired_at    TEXT                     -- ISO date if no longer tracked
);

-- One row per fetch run. The "when" of every other table.
CREATE TABLE snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at   TEXT NOT NULL,           -- ISO timestamp UTC
    week_of       TEXT NOT NULL,           -- ISO date, Monday of that week
    source        TEXT NOT NULL,           -- "weekly" | "manual" | "backfill"
    notes         TEXT
);
CREATE INDEX idx_snapshots_week ON snapshots(week_of);

-- Account balance at a point in time.
CREATE TABLE balances (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id),
    account_id    TEXT    NOT NULL REFERENCES accounts(id),
    balance       REAL    NOT NULL,        -- signed (liabilities negative)
    PRIMARY KEY (snapshot_id, account_id)
);

-- Per-security holdings (only populated for investment accounts).
CREATE TABLE holdings (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id),
    account_id    TEXT    NOT NULL REFERENCES accounts(id),
    security_id   TEXT    NOT NULL REFERENCES securities(id),
    quantity      REAL    NOT NULL,
    price         REAL    NOT NULL,
    value         REAL    NOT NULL,        -- Plaid's institution_value (signed)
    PRIMARY KEY (snapshot_id, account_id, security_id)
);

-- Security registry. Stable across snapshots; updated when new tickers appear.
CREATE TABLE securities (
    id            TEXT PRIMARY KEY,        -- Plaid's security_id (opaque)
    ticker        TEXT,                    -- e.g. "VTV", may be NULL for cash positions
    name          TEXT NOT NULL,
    type          TEXT                     -- "equity" | "etf" | "mutual_fund" | "cash" | etc.
);

-- Manual "why" annotations, scoped to (account, week).
-- annotate() uses INSERT OR REPLACE semantics: calling it twice for the same
-- (account_id, week_of) silently overwrites the prior note. --delete removes
-- the row entirely. No history of past annotations is kept.
CREATE TABLE notes (
    account_id    TEXT NOT NULL REFERENCES accounts(id),
    week_of       TEXT NOT NULL,           -- ISO date, Monday — matches snapshots.week_of
    note          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (account_id, week_of)
);

-- Small key/value state table. Survives fresh clone via restore-from-drive,
-- so the Drive staleness check works correctly on new machines.
CREATE TABLE sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Known keys:
--   last_drive_push_iso  -- ISO timestamp of Drive modifiedTime after our
--                           last successful upload from any machine
--   last_drive_push_host -- hostname that last pushed (forensics)
```

### Key schema decisions

- **Snapshots are the unit of "when".** Every other table joins through
  `snapshot_id`. Lets us add per-snapshot metadata later (e.g. "which Plaid
  items were unreachable") without schema migration.
- **`week_of` denormalized** onto snapshots so weekly comparisons don't have to
  re-derive Mondays. Notes also use `week_of` so a note attaches to a week, not
  a specific snapshot ID — survives if you ever re-snapshot.
- **`balances.balance` is signed** matching the sheet convention. No separate
  `is_liability` needed; the sign tells us.
- **`holdings.value` denormalized** because Plaid sometimes reports
  `institution_value` directly with a different sign convention than
  `quantity × price` (Merrill quirk). Trust Plaid's value when present;
  decomposition uses qty × price for attribution.
- **`securities` keyed by Plaid's `security_id`.** Lets us query "every snapshot
  where I held VTV" cheaply.
- **No transactions table yet** — but the snapshot/account model leaves obvious
  room: a `transactions` table would foreign-key to `accounts(id)` and have its
  own time field, no schema change needed elsewhere.

## Capture flow

### Weekly workflow

Snapshot capture is a **separate, explicit step** run at the end of the weekly
workflow — not bundled into `plaid_balance.py --force`. This is because not
all accounts are updated by Plaid: Fidelity (brokerage + 401K), Coinbase,
Apple Card, Affirm/Klarna, Synchrony, and a few others are hand-typed into the
sheet by the user after the Plaid fetch. If we captured the snapshot during
`--force`, manual values would still be last week's stale data.

The full weekly workflow:

```bash
# 1. Prep for New Week (sheet script, copies column B → column D)
# 2. Update automated rows via Plaid (existing):
python plaid_balance.py --force

# 3. User manually updates Coinbase, Fidelity, Apple Card, etc.

# 4. Capture the snapshot once everything is in the sheet:
python balance_history.py snapshot
```

Step 4 is the only path that writes to the history DB. It does not depend on
step 2 having run in the same session — it's always self-sufficient.

### What `snapshot` does

```python
def snapshot(source="weekly"):
    # Read every balance from column B of the sheet, matched by ID in column H
    balances = read_all_balances_from_sheet()

    # Fetch fresh investment holdings from Plaid (free — on monthly subscription)
    holdings = fetch_investment_holdings_from_plaid()

    with db.transaction():  # all writes below are atomic
        upsert_account_registry_from_yaml()     # sync accounts table
        upsert_securities_registry(holdings)    # sync securities table
        # If a snapshot already exists for this (source, week_of), the
        # delete-then-insert of its balances/holdings happens INSIDE this
        # same transaction, so a crash mid-delete cannot leave a week
        # with no snapshot.
        snapshot_id = db["snapshots"].insert(
            captured_at=now_utc(),
            week_of=monday_of(now_local()),     # see "Week boundaries" below
            source=source,
        )
        replace_week_balances(snapshot_id, balances)
        replace_week_holdings(snapshot_id, holdings)

    backup_to_drive()
```

### Week boundaries

`week_of` is computed in **the user's local timezone** (read from the system;
currently `America/Los_Angeles`). This keeps weeks aligned with the user's
lived experience of "this week" — a Sunday-night run still buckets into the
week the user thinks of as "ending today." The TZ is logged on every snapshot
for forensics, and explicit in the design so a future DST transition or
machine move doesn't silently shift week boundaries.

**Self-sufficient for holdings.** Fetching holdings from Plaid at snapshot time
costs nothing (the Investments product is a monthly subscription — unlimited
calls). This means `snapshot` doesn't depend on `--force` having stashed
holdings data anywhere. It also means holdings reflect prices at *snapshot*
time, which is what we want for decomposition math later.

**Reads balances, doesn't fetch them.** The sheet is the truth for balances —
including Plaid-sourced balances, which `--force` wrote there moments before.
Reading from the sheet means we capture whatever the user actually sees,
including any hand-corrections they made between the Plaid fetch and the
snapshot call.

### End-of-run nudge

`plaid_balance.py --force` ends with a reminder when manual-account rows exist:

```
✔ Updated 21 automated rows in the spreadsheet.
ℹ 12 manual rows still need attention: coinbase, fidelity-brokerage, ...
→ After entering manual balances, run:
    python balance_history.py snapshot
```

Claude can also orchestrate this automatically: when asked to do the weekly
update, run `--force`, prompt the user for manual balances, write them, then
run `snapshot`.

### Idempotency

At most one `source="weekly"` snapshot per `week_of`. A re-run within the same
week **replaces** the previous weekly snapshot (delete old rows in
`balances`/`holdings`, insert new). This means if the user realizes a manual
balance was wrong, they fix the cell and re-run `snapshot` — history corrects
cleanly. Manual `source="manual"` snapshots can stack freely.

### Registry sync

**Accounts.** Before writing balances, `accounts` table is upserted from
`accounts.yaml` + the `manual_accounts:` block. New IDs get
`first_seen=today`. IDs that were present last week but missing now get
`retired_at=today` (we keep their history; they just stop appearing in new
snapshots).

**Securities.** Same pattern. New `security_id` from Plaid → insert. Existing
→ no-op. We never delete securities (they may reappear).

### Failure handling

The whole snapshot write is one SQLite transaction. If Drive backup fails, log
and continue — the local DB still has the data, and the next successful
snapshot will push the cumulative file. If the Plaid holdings fetch fails,
abort without writing (rather than writing an incomplete snapshot with balances
but no holdings detail). If the sheet read fails, abort.

### Backfill

A separate command seeds the DB with recent history retroactively:

```bash
python balance_history.py backfill --week 2026-04-06 --from-sheet
```

Reads the sheet's current state and writes a snapshot dated to a past Monday.
Limited to recovering 1–2 weeks (current column B + column D last-week
values); longer history is lost. `source="backfill"` so it's distinguishable
from organic captures. Does not fetch holdings (the sheet doesn't carry
per-security history), so backfilled snapshots have balance rows but empty
holdings rows — acceptable, since decomposition only matters when both
endpoints of a comparison have holdings.

**Important caveat:** column D is exactly the "stale last-week" surface that
originally motivated this design. A `backfill --week X --from-sheet` run
against a drifted column D will happily write a snapshot with bad values
under `source="backfill"` and no warning. The command should print an
explicit caution before running:

```
⚠ Column D holds whatever "Prep for New Week" last captured — possibly
  older than one week. Verify column D is accurate for 2026-04-06 before
  continuing. (Check Last Modified in F1.)
Continue? [y/N]
```

Skipping this warning risks seeding the DB with a lie that then corrupts
every subsequent delta calculation.

## Drive backup

**Default:** write-only push. Every successful weekly run uploads `history.db`
to Drive, overwriting the previous copy. The script never reads from Drive in
normal operation.

**Read-back is one explicit, opt-in command:**

```bash
python balance_history.py restore-from-drive          # refuses if local DB exists
python balance_history.py restore-from-drive --force  # overwrites local
```

For two scenarios: disaster recovery (laptop dies, fresh clone, no local DB),
or "I ran it on another machine and want to pull that work down."

**Staleness detection on the write path.** Before every Drive upload:

1. Read Drive file's `modifiedTime` metadata.
2. Compare against the timestamp this machine wrote during its previous
   successful upload. **Stored inside the SQLite DB itself** in a small
   `sync_state` table (key/value: `last_drive_push_iso`). Keeping this state in
   the DB (rather than a sibling dotfile) means it survives a fresh clone —
   since `restore-from-drive` downloads the DB with its sync state already
   populated, the next push correctly sees itself as a continuation of the
   prior machine's history rather than a "first run" that silently overwrites.
3. If Drive's `modifiedTime` is newer than what we last pushed → another
   machine has written since. Abort upload, print error, instruct user to
   `restore-from-drive` (pull the other machine's work) or pass `--force-push`
   (overwrite remote, last-write-wins).
4. If matches → safe, upload, then update `sync_state.last_drive_push_iso` to
   the new `modifiedTime` returned by the Drive API.
5. If no Drive file exists → safe, upload (first run).

**Format on Drive.** Plain `history.db` (the SQLite file). Not zipped — at
<1 MB it's not worth the cost, and a plain file means the user can `sqlite3` it
directly after downloading via the Drive web UI in an emergency.

**Versioning.** Google Drive keeps native version history for ~30 days on
uploaded files. We don't roll our own. If a write goes catastrophically wrong,
the previous version is one click away in the Drive UI.

## Query layer

Thin Python module (`history.py`) wraps SQLite reads. CLI (`balance_history.py`)
is a `click` front-end calling into it. Both the sheet-tab renderer and the CLI
go through the same query functions — single source of truth for what a delta
calculation means.

### Core functions

```python
weekly_diff(week_a, week_b) -> List[AccountDelta]
    # Per-account balance change between two snapshots, with holdings
    # decomposition for investment accounts.

trend(account_id, since=None, weeks=None) -> List[(week_of, balance)]
    # Time series for one account. For investment accounts, also returns
    # per-security trends.

top_movers(weeks_back=1, n=10, kind="all") -> List[AccountDelta]
    # Largest absolute deltas in the most recent N-week window.

holdings_diff(account_id, week_a, week_b) -> List[SecurityDelta]
    # Per-security: shares_old, shares_new, price_old, price_new,
    # value_old, value_new, market_change, flow_change.

list_snapshots(limit=10) -> List[Snapshot]

annotate(account_id, week_of, note) -> None
```

### Investment decomposition formula

For each security held at both snapshots:

```
market_change = qty_old × (price_new − price_old)
flow_change   = (qty_new − qty_old) × price_new
```

These sum to `value_new − value_old` exactly (algebraic identity).

- Newly held: entire value is `flow_change`.
- Fully sold: `market_change = qty_old × (price_new − price_old)`,
  `flow_change = −qty_old × price_new`. Sum = `−value_old`.
- For a whole account, market and flow each sum across securities. Cash
  positions (constant $1.00 price) naturally have zero market_change.

## CLI surface

```bash
# Capture (writes)
balance_history.py snapshot                      # read sheet + Plaid holdings, store snapshot
balance_history.py snapshot --source manual      # ad-hoc snapshot (doesn't replace weekly)
balance_history.py backfill --week 2026-04-06 --from-sheet

# Read
balance_history.py diff                          # last week vs this week
balance_history.py diff --weeks-back 4           # 4 weeks ago vs this week
balance_history.py diff --week-a 2026-03-30 --week-b 2026-04-13

balance_history.py trend ml-retirement-andrew    # full history sparkline
balance_history.py trend ml-retirement-andrew --weeks 12

balance_history.py top-movers                    # default: last 1 week, 10 results
balance_history.py top-movers --weeks-back 4 --n 5 --kind investment

balance_history.py holdings ml-retirement-andrew         # latest snapshot
balance_history.py holdings ml-retirement-andrew --diff  # vs last week

balance_history.py snapshots                     # list recent snapshots

# Annotations
balance_history.py annotate usaa-checking 2026-04-13 "Oracle paycheck + RSPP"
balance_history.py annotate usaa-checking 2026-04-13 --delete

# Drive
balance_history.py restore-from-drive [--force]
```

**Output style.** Default is human-friendly tables with deltas color-coded
green/red via `rich`. `--json` flag on every read command for piping.

**Date arguments.** Always `YYYY-MM-DD`. The CLI snaps any date to the Monday
of its week.

## History tab in the sheet

Second tab in the same Google Sheet, named `History`. Regenerated from scratch
on every weekly run (no incremental updates — full rewrite, since SQLite is
canonical and the sheet is a view).

### Layout (12-week rolling window)

| ID | Label | Type | 2026-04-13 | Δ | 2026-04-06 | … | 2026-01-26 | Note (this week) |
|---|---|---|---|---|---|---|---|---|
| ml-retirement-andrew | ML Retirement | asset | 681,128 | +9,032 | 672,096 | … | 640,200 | market: +$8,200, flow: +$832 |
| usaa-checking | USAA Checking | asset | 43,580 | +3,025 | 40,554 | … | 32,100 | Oracle paycheck + RSPP |
| rocket-mortgage | Rocket Mortgage | liability | -258,628 | 0 | -258,628 | … | -259,400 | |
| **Net Worth** | | | **1,160,824** | **+14,636** | **1,146,188** | … | … | |

### Columns

- A: ID (column H of main sheet — for cross-reference)
- B: Label
- C: Type (asset/liability)
- D: This week's balance
- E: Δ vs last week (color-coded)
- F–O: Last 11 weeks of balances (12 weeks total visible)
- P: Note for the most recent week (from `notes` table; auto-populated for
  investment accounts with market/flow split)

A second hidden block underneath: same layout but rolling 52-week. Useful for
long-trend scanning without leaving the sheet.

A small "Last regenerated" cell at the top tells you when the data is from.

**No formulas.** All values static. You can sort/filter freely without breaking
anything; a re-run overwrites everything.

## Library leverage

Pure-Python deps, all installable via `uv pip install`. Total install ~5 MB.

| Library | Use | Why |
|---|---|---|
| `sqlite-utils` | DB schema, inserts, queries, migrations | Simon Willison. Schema as Python dicts, `db["table"].insert_all(...)`, automatic indexes, JSON support, built-in CSV/JSON export. |
| `rich` | CLI tables, color, progress | Industry standard. Native delta coloring, nice tables, spinners during fetch. |
| `plotext` | Terminal sparklines and trend charts | Renders ASCII line/bar plots. No matplotlib dependency. |
| `click` | CLI argument parsing | Cleaner than argparse for nested subcommands. |
| `python-dateutil` | "Monday of week N", relative dates | One-liners instead of stdlib gymnastics. |

Estimated project-specific code: ~360 lines total (history.py ~150, CLI ~100,
sheet renderer ~80, snapshot wiring in plaid_balance.py ~30).

## Phased delivery

Three PRs, each shippable independently. You can stop after any phase.

### Phase 1 — Capture + CLI diff (the foundation)

- `history.py` module: schema, snapshot writer, account/security registry sync,
  sheet-reader helper.
- `balance_history.py` CLI: `snapshot`, `diff`, `snapshots`, `annotate`,
  `restore-from-drive`, `backfill`.
- Add end-of-run nudge to `plaid_balance.py --force` reminding the user to run
  `balance_history.py snapshot` after entering manual balances.
- Drive backup + staleness check.
- Tests: schema creation, sheet-read matches column H IDs, idempotent
  re-snapshot for same week (replaces cleanly), decomposition math, staleness
  guard, backfill from sheet.

**Stop point:** After the weekly update (Plaid + manual entries), run
`balance_history.py snapshot` then `balance_history.py diff` to see exactly
what changed since last week, with investment market-vs-flow decomposition.
Solves the original "+$32K, why?" problem.

### Phase 2 — History tab in the sheet

- `history_sheet.py` renderer.
- Wire it into `--force` so the History tab regenerates after each snapshot.
- Handle tab creation if missing.
- Format dates, color deltas, populate Notes column.

**Stop point:** History visible every time the sheet is opened; no terminal
needed.

### Phase 3 — Trends + visualizations

- `trend`, `top-movers`, `holdings` CLI subcommands.
- `plotext` sparkline output for trends.
- `--json` flag across read commands for scripting.

**Stop point:** Full feature set. Easy to add more queries later — always just
add a function to `history.py`.

### Out of scope (for now)

- Plaid transactions ingestion. Schema leaves room; phase 4 if/when wanted.
- Multi-machine sync beyond the staleness check.
- Web UI / charts beyond terminal + sheet.
- Goal tracking, projections, retirement planning.

## Risks

- **Plaid `institution_value` quirks.** Plaid occasionally returns
  `institution_value` that doesn't equal `quantity × price` (Merrill reporting
  quirk). The schema stores both — totals use `institution_value`, attribution
  uses `qty × price`.
- **Backfill horizon.** Backfill-from-sheet recovers at most 2 weeks (current
  column B + column D last-week). Longer history is genuinely lost; accept.
- **Drive `modifiedTime` granularity** is seconds. Two runs in the same second
  would race the staleness check. Not a real concern at weekly cadence.
- **Sheet renderer fragility.** A user manually editing the History tab between
  runs would have edits silently overwritten. Mitigate by labeling the tab
  clearly: "Auto-generated, do not edit. Source: history.db at <timestamp>."
