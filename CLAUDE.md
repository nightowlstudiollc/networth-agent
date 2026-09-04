# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**This file is deliberately static.** It covers the repo model and invariants.
Process and operational detail live in **`IMPLEMENTATION.md`** — read that too,
and put updates there rather than here.

Anything naming a real institution, balance, mask, or account belongs in
`NOTES.md` (gitignored, local only). Never in a tracked file: everything
tracked syncs to the public repo.

## Repo Model — Read This First

This is the **private development repo** (`nightowlstudiollc/financial-agent`). All
development happens here. There is a companion **public repo**
(`nightowlstudiollc/networth-agent`) that contains the same code but no personal data.

**Never commit personal configuration to this repo.** The following files are
gitignored and must stay that way — they live only on local disk:

- `accounts.yaml` — account-to-spreadsheet mapping with real institution names and masks
- `config.yaml` — Zillow URL, Google service account path, Drive folder ID
- `.mcp.json` — MCP server config with local paths
- `.claude/secrets.op` — 1Password secret references

When code changes are ready to publish, sync to the public repo:

```bash
./sync-to-public.sh --push --message "feat: description of change"
```

Do not push directly to the public repo. Do not suggest committing the files
listed above. If a task requires editing those files, edit them on disk — do not
stage or commit them.

---

## Project Overview

Financial agent that automates net worth tracking by pulling balances from financial
institutions and updating a Google Sheet.

## Cost-aware balance fetching

`plaid_balance.py` has two modes controlled by CLI flags:

| Flag | Endpoint | Cost | When to use |
| ------ | ---------- | ------ | ------------- |
| `--force` | `accounts/balance/get` | $0.10/item | **Biweekly spreadsheet update only (1st & 15th)** |
| `--cached` | `accounts/get` | Free | Dev, debug, inspection, any non-update session |
| *(no flag)* | `accounts/balance/get` | $0.10/item | Allowed once per 23 h; exits with error if rate limit is active |

**Rule:** use `--force` only when writing balances to the spreadsheet. Use `--cached` for everything else — inspecting data, troubleshooting code, testing changes. Never run a real-time fetch just to look at output.

```bash
# Check rate-limit status without fetching
python plaid_balance.py --check
```

**Retry one institution instead of all of them.** A bare `--force` bills every
linked item ($0.10 × 13 ≈ $1.30). When a single institution fails — a re-auth,
or a transient `INSTITUTION_NOT_RESPONDING` — scope the retry:

```bash
python plaid_balance.py --force --only "Institution Name"
```

That costs $0.10. Only the fetched rows are written; every other row keeps its
current value. A scoped run deliberately does not stamp the rate-limit clock,
so a cheap retry never blocks the next full sweep. An unknown name exits 2 and
lists the valid institutions rather than silently fetching nothing.

`investments/holdings/get` is **subscription-billed** (~$0.35/item/month,
unlimited calls), not per-call. Fetching holdings on a `--cached` run or
during `snapshot` costs nothing extra — do not "optimize" it away.

## Balance history (`balance_history.py`)

A local SQLite database (`history.db`, gitignored) stores biweekly snapshots
(1st & 15th of each month) of all balances plus Plaid holdings. Backed up to
Drive after each snapshot.

**Biweekly flow (1st & 15th):**

```
plaid_balance.py --force  →  enter manual balances in sheet  →
balance_history.py snapshot  →  balance_history.py diff
```

`--force` ends with a nudge reminding the user to run `snapshot` after entering
manual balances. Do not call `snapshot` yourself automatically — the user enters
manual rows between those two steps.

**New sheet rows need an `accounts.yaml` entry first.** A row whose column-H ID
has no entry there fails the balances foreign key. `snapshot` now checks this
before fetching or writing anything and names the offending IDs; add each under
`manual_accounts` (`id`, `label`, `type`) and re-run.

**Subcommands:** `snapshot`, `diff`, `snapshots`, `annotate`,
`restore-from-backup`. See `README.md` for usage examples.

Per-account prior-cycle balances are not recoverable — the sheet never
saved them. Snapshot 1 (whenever the user first ran `snapshot`) is the
inception point; cycles accumulate from there.

`history.db` is local-only. If the file is missing, run `restore-from-backup` —
do not re-initialize by running `snapshot` on an empty DB (that would lose
prior history).

## Working Integrations

| Source | Method | Script |
| -------- | -------- | -------- |
| Google Sheets | Sheets v4 API (service account) | `google_sheets_client.py` |
| Plaid (banks/cards) | Direct API | `plaid_balance.py` |
| Mercury | Direct API | `mercury_balance.py` |
| Coinbase | Advanced Trade API | `coinbase_balance.py` |
| Zillow | Web scraping | `zillow_balance.py` |

The balance scripts write to the sheet themselves via `google_sheets_client.py`.
No MCP server sits in the automation path.

## Manual Accounts

Some institutions cannot be automated (no Plaid coverage, proprietary auth, or
BNPL providers Plaid does not list). Those balances are typed into the sheet by
hand each cycle.

The current per-institution status — which are automated, which are manual and
why, and which need a Link attempt to confirm — is tracked in `NOTES.md`
(gitignored, local only). See `IMPLEMENTATION.md` for the general automation
decision process.

## Environment Setup

**1Password secrets** (loaded automatically via `.claude/secrets.op`):

- `COINBASE_API_KEY`, `COINBASE_API_SECRET` - Coinbase Advanced Trade API
- `MERCURY_API_TOKEN` - Mercury Banking API
- `PLAID_CLIENT_ID`, `PLAID_SECRET` - Plaid API (production)
- `PLAID_SANDBOX_SECRET` - Plaid API (sandbox testing)

> **Retired 2026-07-01:** the `plaid-dashboard` MCP server and its local token
> proxy were removed. Nothing in the automation used it, and Plaid's upstream
> OAuth began returning 404. `plaid_mcp_proxy.py` / `plaid_token.py` remain on
> disk for manual revival. `PLAID_MCP_TOKEN` is no longer needed.

**Google service account** (paths live in `config.yaml`):

- `service_account_path` - Path to the Google service account JSON. Read by
  `google_sheets_client.py`; the sheet must be shared with that account's email.
- `drive_folder_id` - Google Drive folder ID for `history.db` backups.

A Sheets MCP server is optional and used only for interactive work in a Claude
Code session. `.mcp.example.json` has an `mcp-google-sheets` entry to copy; the
same two values are passed to it as `SERVICE_ACCOUNT_PATH` and `DRIVE_FOLDER_ID`.

## Commands

```bash
# Activate Python environment
source .venv/bin/activate

# Fetch balances (automated accounts)
python plaid_balance.py --force   # Real-time fetch — use for spreadsheet updates only ($0.10/item)
python plaid_balance.py --cached  # Cached fetch — use for dev/debug (free)
python plaid_balance.py --check   # Show time since last real-time fetch, no API call
python mercury_balance.py         # Mercury banking (direct API, not Plaid-billed)
python zillow_balance.py          # Home value (Zestimate)

# Manual reference (not used in spreadsheet automation)
python coinbase_balance.py   # Coinbase Advanced Trade only

# Plaid Link server (for connecting bank accounts)
PLAID_ENV=sandbox python plaid_link_server.py   # Test with sandbox
PLAID_ENV=production python plaid_link_server.py # Production (needs approval)

# List connected Plaid accounts
PLAID_ENV=sandbox python plaid_accounts.py

# Check Plaid consent expirations (warns at 30 days, critical at 7 days)
python plaid_status.py                # Report using cached consent data
python plaid_status.py --check-login   # Live check + refresh consent dates (slower)
python plaid_status.py --check         # Exit non-zero if anything is critical/expired (for cron)

# Sync code changes to the public repo
./sync-to-public.sh                              # Dry run — preview changes
./sync-to-public.sh --push --message "feat: …"  # Publish

# Install dependencies (if needed)
uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
```

## Project Structure

```
.claude/secrets.op     # 1Password secret references — gitignored, local only
.claude/pre-launch.sh  # Pre-launch hook (config-backup restore)
.mcp.json              # MCP server config — gitignored, local only (copy from .mcp.example.json)
accounts.yaml          # Account mapping — gitignored, local only (copy from accounts.example.yaml)
config.yaml            # Runtime config — gitignored, local only (copy from config.example.yaml)
plaid_balance.py       # Plaid balance fetcher (banks/cards/loans/investments)
coinbase_balance.py    # Coinbase balance fetcher
mercury_balance.py     # Mercury balance fetcher
zillow_balance.py      # Zillow Zestimate fetcher
plaid_token.py         # Plaid OAuth token manager (dormant — see Retired note)
plaid_mcp_proxy.py     # Local proxy for plaid-dashboard MCP (dormant — retired)
plaid_link_server.py   # Flask server for Plaid Link flow
plaid_accounts.py      # Display connected Plaid accounts
plaid_status.py         # Consent expiration / ITEM_LOGIN_REQUIRED monitoring
sync-to-public.sh      # One-way sync to nightowlstudiollc/networth-agent
static/link.html       # Plaid Link UI
requirements.txt       # Python dependencies
.venv/                 # Python virtual environment (not committed)
```

## Net Worth Spreadsheet

Spreadsheet ID is in `accounts.yaml` (key: `spreadsheet_id`).

Automated accounts update column B with balance, column C with a checkmark (✔️).
Do NOT write to column E (contains formulas).

## Plaid Account Mapping

**Read `accounts.yaml` for the full account-to-row mapping.** This file is gitignored
and contains personal account details. The public template is `accounts.example.yaml`.

Key fields in each account entry:

- `institution`: Plaid institution name (use exact name returned by `plaid_accounts.py`)
- `name`: Account name as returned by Plaid
- `mask`: Last 4 digits of account number
- `id`: Stable slug matching column H of the sheet — used to resolve the row at write-time
- `type`: `asset` or `liability`

**Row resolution:** Do **not** store row numbers — read column H of the Net
Worth sheet and match `id` to find the current row. Multiple Plaid accounts may
share an `id` (their balances are summed onto the same sheet row).

**Sign convention:**

- Assets: positive numbers
- Liabilities: NEGATIVE numbers (Plaid returns them as negative, use as-is)

Some investment accounts use the `investments` product to fetch holdings.

## OAuth Consent Expiration

Some institutions' OAuth panes ask the user to choose a connection lifetime
(e.g. 6 months, 1 year, or indefinite). Picking a bounded lifetime means the
item silently stops returning balances when consent expires.

**When re-linking, choose the longest available option.** Consent duration can
usually also be changed later in the institution's own security settings, which
is why `plaid_status.py --check-login` re-reads `consent_expiration_time` from
`/item/get` and writes the current value back to `.plaid_items.json`. A cached
link-time value can drift from reality, so a plain `plaid_status.py` run reports
the last known value, not necessarily the live one.

Institution-specific consent notes live in `NOTES.md`.

## Plaid Limitations

See `plaid_failures.md` for detailed connection failure history.
Manual accounts listed above require manual balance entry in the spreadsheet.
