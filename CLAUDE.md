# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## IMPORTANT NOTE

*NEVER* use cached balances from previous runs. *ALWAYS* fetch current balances.

## Working Integrations

| Source | Method | Script |
|--------|--------|--------|
| Google Sheets | MCP (mcp-google-sheets) | - |
| Plaid Dashboard | MCP (plaid-dashboard) | - |
| Plaid (banks/cards) | Direct API | `plaid_balance.py` |
| Mercury | Direct API | `mercury_balance.py` |
| Zillow | Web scraping | `zillow_balance.py` |

## Manual Accounts

| Source | Reason |
|--------|--------|
| Coinbase | Plaid only supports Base Wallet, not main Coinbase |
| Fidelity | Not supported by Plaid |
| American Express | 2FA incompatibility with Plaid |
| Apple Card | Apple doesn't work with aggregators |
| Rocket Loans (personal) | Not available separately in Plaid (only mortgage is exposed) |
| Synchrony/CareCredit | Not available in Plaid |
| Affirm/Klarna | BNPL not available in Plaid |

## Environment Setup

**1Password secrets** (loaded automatically via `.claude/secrets.op`):

- `COINBASE_API_KEY`, `COINBASE_API_SECRET` - Coinbase Advanced Trade API
- `MERCURY_API_TOKEN` - Mercury Banking API
- `PLAID_CLIENT_ID`, `PLAID_SECRET` - Plaid API (production)
- `PLAID_SANDBOX_SECRET` - Plaid API (sandbox testing)

**Plaid MCP token** (fetched automatically via `.claude/pre-launch.sh`):

- `PLAID_MCP_TOKEN` - OAuth token for Plaid Dashboard MCP

**Google Sheets MCP** (configured in `.mcp.json`, generated from `config.yaml`):

- `SERVICE_ACCOUNT_PATH` - Path to Google service account JSON
- `DRIVE_FOLDER_ID` - Google Drive folder ID

## Commands

```bash
# Activate Python environment
source .venv/bin/activate

# Fetch balances (automated accounts)
python plaid_balance.py      # Plaid bank/credit/loan/investment accounts
python mercury_balance.py    # Mercury banking (also via Plaid)
python zillow_balance.py     # Home value (Zestimate)

# Manual reference (not used in spreadsheet automation)
python coinbase_balance.py   # Coinbase Advanced Trade only

# Plaid Link server (for connecting bank accounts)
PLAID_ENV=sandbox python plaid_link_server.py   # Test with sandbox
PLAID_ENV=production python plaid_link_server.py # Production (needs approval)

# List connected Plaid accounts
PLAID_ENV=sandbox python plaid_accounts.py

# Sync code changes to the public repo
./sync-to-public.sh                              # Dry run — preview changes
./sync-to-public.sh --push --message "feat: …"  # Publish

# Install dependencies (if needed)
uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
```

## Project Structure

```
.claude/secrets.op     # 1Password secret references — gitignored, local only
.claude/pre-launch.sh  # Pre-launch hook for Plaid OAuth token
.mcp.json              # MCP server config — gitignored, local only (copy from .mcp.example.json)
accounts.yaml          # Account mapping — gitignored, local only (copy from accounts.example.yaml)
config.yaml            # Runtime config — gitignored, local only (copy from config.example.yaml)
plaid_balance.py       # Plaid balance fetcher (banks/cards/loans/investments)
coinbase_balance.py    # Coinbase balance fetcher
mercury_balance.py     # Mercury balance fetcher
zillow_balance.py      # Zillow Zestimate fetcher
plaid_token.py         # Plaid OAuth token manager
plaid_mcp_proxy.py     # Local proxy for transparent Plaid token refresh
plaid_link_server.py   # Flask server for Plaid Link flow
plaid_accounts.py      # Display connected Plaid accounts
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
- `row`: Spreadsheet row number to update
- `type`: `asset` or `liability`

**Sign convention:**

- Assets: positive numbers
- Liabilities: NEGATIVE numbers (Plaid returns them as negative, use as-is)

**IMPORTANT:** Always fetch fresh balances before updating. Never use cached data.

Some investment accounts use the `investments` product to fetch holdings.

## Plaid Limitations

See `plaid_failures.md` for detailed connection failure history.
Manual accounts listed above require manual balance entry in the spreadsheet.
