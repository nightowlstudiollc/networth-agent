# networth-agent

> Replace `networth-agent` with your preferred repo name throughout this file and in
> `sync-to-public.sh` if you fork and rename this project.

An AI-assisted personal finance tool that automatically pulls account balances from
banks, brokerages, credit cards, and other sources and writes them to a Google Sheet.
Claude Code acts as the orchestration layer — reading account mappings, fetching
balances, and updating the sheet without manual intervention.

## What it does

- Pulls live balances from connected institutions via [Plaid](https://plaid.com)
- Fetches your home's Zestimate from Zillow
- Pulls Mercury and Coinbase balances via direct APIs
- Writes everything to a configured Google Sheet via the Sheets v4 API

## Requirements

- Python 3.11+
- A [Plaid](https://plaid.com) account (free tier covers personal use)
- A Google Cloud service account with Sheets API access
- [1Password CLI](https://developer.1password.com/docs/cli/) (optional but recommended for secrets)
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## Integrations

| Source | Method | Notes |
| -------- | -------- | ------- |
| Banks, credit cards, loans | Plaid API | ~10,000 supported institutions |
| Investment accounts | Plaid Investments API | Brokerages and 401k providers |
| Mercury | Direct API | Requires Mercury API token |
| Coinbase | Advanced Trade API | Automated; balances include funds held by open orders |
| Home value | Zillow scraping | Zestimate only; fragile by nature |
| Google Sheets | Sheets v4 API | Service account; `google_sheets_client.py` |

### Institutions Plaid may not cover

Plaid's coverage is broad but not universal, and it changes over time. Common
gaps:

| Category | Notes |
| -------- | ----- |
| Proprietary-auth card issuers | Some issuers do not work with aggregators at all |
| Buy-now-pay-later | Largely absent from Plaid |
| Partial enumeration | An item may connect but expose only some of your accounts there |
| Wrong entity | Plaid may list a co-branded card rather than the main account |

Anything Plaid cannot reach is entered in the sheet by hand. Check coverage for
your own institutions with Plaid's free `institutions/search` endpoint — a past
"not supported" is not durable. See `plaid_failures.md` for the failure
categories and how to re-check.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/nightowlstudiollc/networth-agent.git
cd networth-agent
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

### 2. Configure accounts

```bash
cp accounts.example.yaml accounts.yaml
cp config.example.yaml config.yaml
cp .mcp.example.json .mcp.json
```

Edit `accounts.yaml` with your institution→ID mapping. Each account references a
stable slug (e.g. `mercury-checking`) that must also appear in column H of your
spreadsheet — rows are resolved at write-time by matching the ID, so balances
land on the right row even when the sheet is reordered.
Run `python plaid_accounts.py` after linking to see exact Plaid account names.

Edit `config.yaml` with your Google service account path, Drive folder ID, and Zillow URL.

### 3. Set up secrets

Copy `.claude/secrets.op.template` to `.claude/secrets.op` and fill in your
1Password vault references. Or set the environment variables directly:

```bash
export PLAID_CLIENT_ID=your_client_id
export PLAID_SECRET=your_production_secret
export MERCURY_API_TOKEN=your_mercury_token
export COINBASE_API_KEY=your_coinbase_key
export COINBASE_API_SECRET=your_coinbase_secret
```

### 4. Link bank accounts via Plaid

```bash
PLAID_ENV=sandbox python plaid_link_server.py  # Test first
PLAID_ENV=production python plaid_link_server.py
```

Open `http://localhost:8080` and connect your institutions.

### 5. Configure Google Sheets access

The balance scripts write to the sheet through the Sheets v4 API using a Google
service account — no MCP server is involved. Set `service_account_path` and the
spreadsheet ID in `config.yaml`, and share the sheet with the service account's
email address (found in the JSON key file) so it can write.

### 6. (Optional) Install the Google Sheets MCP server

Nothing in the automation needs it. A Sheets MCP server is useful for interactive
work — browsing or editing the sheet from a Claude Code session without writing a
script. `.mcp.example.json` has a working `mcp-google-sheets` entry to copy.

### 7. (Optional) Install the Coinbase MCP server

`coinbase_balance.py` talks to the Advanced Trade API directly and does not need
an MCP server. The Coinbase MCP is useful for interactive work — inspecting
balances, prices, and portfolios from a Claude Code session without writing a
script.

```bash
claude mcp add coinbase --transport http https://agents.coinbase.com/mcp
```

Then authenticate: run `/mcp` in Claude Code, select **coinbase**, choose
**Authenticate**, and complete the Coinbase sign-in flow.

This registers the server in your user-scope config, not in the project's
`.mcp.json` (which is gitignored and local-only).

Creating a CDP API key is a separate step, only needed for the direct-API path
used by `coinbase_balance.py`:

1. Go to the [CDP Portal API Keys page](https://portal.cdp.coinbase.com/api-keys/secret).
2. Click **Create API Key** and name it.
3. Under **Advanced Settings > Coinbase App & Advanced Trade**, select your portfolio.
4. Download the JSON key file — the secret is shown only at creation time.

Grant only the permissions you need. Coinbase's own walkthrough enables **Trade**
and **Transfer**; this project only reads balances, so neither is required.
Read-only keys cannot place orders or move funds, which is the safer default for
an automated net-worth tracker.

### 8. Run

```bash
source .venv/bin/activate
python plaid_balance.py
python mercury_balance.py
python zillow_balance.py
python coinbase_balance.py
```

With Claude Code, these run automatically when you ask it to update the spreadsheet.

## History & deltas

`balance_history.py` captures weekly snapshots into a local SQLite database
(`history.db`, gitignored) and backs them up to Google Drive. This is what lets
you see week-over-week changes instead of just the latest balance.

### Weekly workflow

```bash
# 1. Fetch automated balances and write them to the sheet.
python plaid_balance.py --force
python mercury_balance.py
python zillow_balance.py
python coinbase_balance.py

# If one institution fails (re-auth, or a transient outage), retry just that
# one — a bare --force re-bills every linked item at $0.10 each.
python plaid_balance.py --force --only "Institution Name"

# 2. Enter any manual balances (whatever Plaid cannot reach) in the sheet.

# 3. Snapshot the sheet + Plaid holdings into history.db.
#    `snapshot` needs PLAID_CLIENT_ID + PLAID_SECRET; if you use 1Password
#    CLI, wrap it: `op run --env-file=.claude/secrets.op -- python ...`
python balance_history.py snapshot

# 4. See what changed week-over-week.
python balance_history.py diff
```

### Commands

```bash
python balance_history.py snapshot               # Capture this week
python balance_history.py diff                   # Preceding snapshot vs. this week (follows cadence)
python balance_history.py diff --weeks-back 4    # Override: 4 weeks ago vs. now
python balance_history.py snapshots              # List recent captures
python balance_history.py annotate <id> <week> "note"   # Add context
python balance_history.py restore-from-backup    # Recover db from local backup
```

Example `diff` output:

```text
                  Δ  2026-04-06  →  2026-04-13
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┓
┃ Label           ┃        Old ┃        New ┃        Δ ┃ Market ┃  Flow ┃ Note     ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━━━━┩
│ Brokerage       │ $50,000.00 │ $51,600.00 │ +$1,600  │ +$500  │ +$1,100│          │
│ Checking        │  $1,000.00 │  $1,500.00 │   +$500  │        │        │ paycheck │
├─────────────────┼────────────┼────────────┼──────────┼────────┼───────┼──────────┤
│ Net change      │            │            │ +$2,100  │        │        │          │
└─────────────────┴────────────┴────────────┴──────────┴────────┴───────┴──────────┘
```

For investment accounts with Plaid holdings, `market` and `flow` columns decompose
the delta into price movement (what the market did) vs. contributions/withdrawals
(what you did).

`history.db` is gitignored and copied to `local_backup_dir` (configured in
`config.yaml`) after each snapshot. Use a cloud-synced folder (e.g. Google Drive
mount) for automatic offsite backup. Use `restore-from-backup` to recover.

## Architecture

```
Claude Code
  ├── plaid_balance.py    → direct Plaid API for real-time balances
  ├── mercury_balance.py  → Mercury Banking API
  ├── zillow_balance.py   → Zillow Zestimate scraper
  └── coinbase_balance.py → Coinbase Advanced Trade API

Each script writes its balances to the sheet itself, through
google_sheets_client.py (Sheets v4 API + service account).
```

## Account mapping

`accounts.yaml` (gitignored — copy from `accounts.example.yaml`) maps each Plaid
account to a stable slug, which must also appear in column H of your spreadsheet:

```yaml
spreadsheet_id: "your_spreadsheet_id"

accounts:
  - institution: "Chase"
    name: "CREDIT CARD"
    mask: "1234"
    id: "chase-sapphire-reserve"
    label: "Sapphire Reserve"
    type: liability
```

Multiple Plaid accounts can share an `id` — their balances are summed before
being written to that row (useful for joint accounts split across products).

Run `python plaid_accounts.py` to see the exact institution and account names
that Plaid returns for your linked accounts.

## Contributing

This is a personal-use tool shared as a reference implementation. Issues and PRs
welcome, but the scope is intentionally narrow.

## License

MIT
