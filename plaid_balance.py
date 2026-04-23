#!/usr/bin/env python3
"""Fetch Plaid account balances."""

import json
import os
import sys
import time
from pathlib import Path

import plaid
from dotenv import load_dotenv
from plaid.api import plaid_api
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.investments_holdings_get_request import (
    InvestmentsHoldingsGetRequest,
)

load_dotenv()

# Environment configuration
PLAID_ENV = os.getenv("PLAID_ENV", "production")

if PLAID_ENV == "sandbox":
    host = plaid.Environment.Sandbox
    PLAID_SECRET = os.getenv("PLAID_SANDBOX_SECRET")
    ITEMS_FILE = Path(__file__).parent / ".plaid_items_sandbox.json"
elif PLAID_ENV == "development":
    host = plaid.Environment.Development
    PLAID_SECRET = os.getenv("PLAID_SECRET")
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"
else:
    host = plaid.Environment.Production
    PLAID_SECRET = os.getenv("PLAID_SECRET")
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"

# Rate-limiting: prevent accidental real-time fetches during dev sessions.
# accounts/balance/get is billed at $0.10/item; accounts/get is free (cached).
LAST_FETCH_FILE = Path(__file__).parent / ".plaid_last_realtime_fetch"
MIN_FETCH_INTERVAL_HOURS = 23

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")

if not PLAID_CLIENT_ID or not PLAID_SECRET:
    raise ValueError(
        "PLAID_CLIENT_ID and PLAID_SECRET (or PLAID_SANDBOX_SECRET) required"
    )

# Configure Plaid client
configuration = plaid.Configuration(
    host=host,
    api_key={
        "clientId": PLAID_CLIENT_ID,
        "secret": PLAID_SECRET,
    },
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)


def aggregate_balances_by_id(
    accounts: list[dict],
    holdings: list[dict],
    yaml_accounts: list[dict],
) -> dict[str, float]:
    """Aggregate Plaid accounts + holdings into per-sheet-row balances.

    Liabilities (per yaml `type`) are returned negative. Multiple Plaid
    accounts sharing a yaml `id` sum into that id. Unmapped Plaid accounts
    are skipped.
    """
    by_inst_mask = {
        (a["institution"], a["mask"]): (a["id"], a["type"]) for a in yaml_accounts
    }
    account_id_to_yaml = {}
    result: dict[str, float] = {}

    for acc in accounts:
        key = (acc.get("institution"), acc.get("mask"))
        mapped = by_inst_mask.get(key)
        if not mapped:
            continue
        yaml_id, yaml_type = mapped
        if acc.get("account_id"):
            account_id_to_yaml[acc["account_id"]] = (yaml_id, yaml_type)
        bal = acc.get("balance")
        if bal is None:
            continue
        if yaml_type == "liability":
            bal = -bal
        result[yaml_id] = result.get(yaml_id, 0.0) + bal

    for h in holdings:
        mapped = account_id_to_yaml.get(h.get("account_id"))
        if not mapped:
            continue
        yaml_id, _ = mapped
        result[yaml_id] = result.get(yaml_id, 0.0) + (h.get("value") or 0.0)

    return result


def load_items() -> dict:
    """Load saved items from file."""
    if ITEMS_FILE.exists():
        content = ITEMS_FILE.read_text().strip()
        if content:
            return json.loads(content)
    return {}


def hours_since_last_realtime_fetch() -> float | None:
    """Hours since last real-time fetch, or None if never."""
    if not LAST_FETCH_FILE.exists():
        return None
    try:
        ts = float(LAST_FETCH_FILE.read_text().strip())
        return (time.time() - ts) / 3600
    except (ValueError, OSError):
        return None


def record_realtime_fetch() -> None:
    """Stamp the current time as the last successful real-time fetch."""
    LAST_FETCH_FILE.write_text(str(time.time()))


def get_investment_holdings(access_token: str, institution_name: str) -> tuple:
    """Fetch investment holdings for an item with investments product.

    Returns:
        tuple: (holdings_list, securities_dict, error_or_none)
            - holdings_list: list of holding dicts
            - securities_dict: map of security_id to security info
            - error_or_none: error message if failed, None if success
    """
    try:
        req = InvestmentsHoldingsGetRequest(access_token=access_token)
        response = client.investments_holdings_get(req)
        data = response.to_dict()

        holdings = data.get("holdings", [])
        securities = {s["security_id"]: s for s in data.get("securities", [])}

        return holdings, securities, None
    except plaid.ApiException as e:
        error_body = json.loads(e.body) if e.body else {}
        error_code = error_body.get("error_code", "UNKNOWN")
        error_msg = error_body.get("error_message", str(e))

        if error_code == "ADDITIONAL_CONSENT_REQUIRED":
            return [], {}, f"{institution_name}: Investments consent needed"
        elif error_code == "PRODUCTS_NOT_SUPPORTED":
            return [], {}, None  # Silent - just skip investments for this item
        else:
            return [], {}, f"{institution_name}: {error_code} - {error_msg}"


def get_plaid_balances(realtime: bool = True) -> dict:
    """Fetch all Plaid account balances.

    Args:
        realtime: If True (default), use accounts/balance/get for a live pull
            (billed at $0.10/item). If False, use accounts/get for Plaid's
            cached data (free). Cached data is typically a few hours stale —
            fine for dev/debug work, not appropriate for spreadsheet updates.

    Returns:
        dict with:
            - 'total_assets': sum of depository/investment balances
            - 'total_liabilities': sum of credit/loan balances (positive)
            - 'net_total': total_assets - total_liabilities
            - 'accounts': list of account details
            - 'holdings': list of investment holdings
            - 'errors': list of any errors encountered
            - 'realtime': bool indicating which endpoint was used
    """
    items = load_items()

    if not items:
        return {
            "total_assets": 0.0,
            "total_liabilities": 0.0,
            "net_total": 0.0,
            "accounts": [],
            "holdings": [],
            "errors": ["No items found. Run plaid_link_server.py first."],
            "realtime": realtime,
        }

    all_accounts = []
    all_holdings = []
    errors = []
    total_assets = 0.0
    total_liabilities = 0.0

    for _item_id, item_data in items.items():
        access_token = item_data.get("access_token")
        institution_name = item_data.get("institution_name", "Unknown")
        products = item_data.get("products", [])

        if not access_token:
            errors.append(f"{institution_name}: Missing access token")
            continue

        # Fetch investment holdings if investments product is enabled
        investment_account_ids = set()
        if "investments" in products:
            holdings, securities, inv_error = get_investment_holdings(
                access_token, institution_name
            )
            if inv_error:
                errors.append(inv_error)
            for h in holdings:
                security = securities.get(h.get("security_id"), {})
                account_id = h.get("account_id")
                investment_account_ids.add(account_id)

                # Calculate holding value
                value = h.get("institution_value")
                if value is None:
                    quantity = h.get("quantity", 0)
                    price = h.get("institution_price", 0)
                    value = quantity * price if quantity and price else 0

                holding_info = {
                    "institution": institution_name,
                    "account_id": account_id,
                    "security_id": h.get("security_id"),
                    "name": security.get("name", "Unknown"),
                    "ticker": security.get("ticker_symbol"),
                    "type": security.get("type"),
                    "quantity": h.get("quantity"),
                    "price": h.get("institution_price"),
                    "value": value,
                    "currency": h.get("iso_currency_code", "USD"),
                }
                all_holdings.append(holding_info)
                total_assets += value

        try:
            # accounts/balance/get: real-time pull, billed $0.10/item.
            # accounts/get: Plaid-cached, free. Use for dev/debug.
            if realtime:
                req = AccountsBalanceGetRequest(access_token=access_token)
                response = client.accounts_balance_get(req)
            else:
                req = AccountsGetRequest(access_token=access_token)
                response = client.accounts_get(req)
            accounts = response.to_dict().get("accounts", [])

            for acc in accounts:
                account_id = acc.get("account_id")
                balances = acc.get("balances", {})
                current = balances.get("current")
                available = balances.get("available")
                acc_type = acc.get("type")
                subtype = acc.get("subtype")

                # Skip investment accounts if we already fetched holdings
                # for them (to avoid double-counting)
                if account_id in investment_account_ids:
                    # Still add account info but don't count balance
                    account_info = {
                        "institution": institution_name,
                        "name": acc.get("name"),
                        "official_name": acc.get("official_name"),
                        "type": acc_type,
                        "subtype": subtype,
                        "mask": acc.get("mask"),
                        "account_id": account_id,
                        "balance": 0,  # Balance is in holdings
                        "balance_from_holdings": True,
                        "currency": balances.get("iso_currency_code", "USD"),
                    }
                    all_accounts.append(account_info)
                    continue

                # Determine balance to use
                # For credit cards: current is the amount owed
                # For depository: available is more useful (excludes holds)
                if acc_type == "credit":
                    balance = current if current is not None else 0.0
                elif acc_type == "loan":
                    balance = current if current is not None else 0.0
                else:
                    if available is not None:
                        balance = available
                    else:
                        balance = current or 0.0

                account_info = {
                    "institution": institution_name,
                    "name": acc.get("name"),
                    "official_name": acc.get("official_name"),
                    "type": acc_type,
                    "subtype": subtype,
                    "mask": acc.get("mask"),
                    "account_id": account_id,
                    "balance": balance,
                    "currency": balances.get("iso_currency_code", "USD"),
                }
                all_accounts.append(account_info)

                # Categorize as asset or liability
                if acc_type in ("depository", "investment", "brokerage"):
                    total_assets += balance
                elif acc_type in ("credit", "loan"):
                    total_liabilities += balance

        except plaid.ApiException as e:
            error_body = json.loads(e.body) if e.body else {}
            error_code = error_body.get("error_code", "UNKNOWN")
            error_msg = error_body.get("error_message", str(e))

            if error_code == "ITEM_LOGIN_REQUIRED":
                errors.append(
                    f"{institution_name}: Re-authentication required. "
                    "Run plaid_link_server.py to re-link."
                )
            else:
                err = f"{institution_name}: {error_code} - {error_msg}"
                errors.append(err)

    return {
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "net_total": total_assets - total_liabilities,
        "accounts": all_accounts,
        "holdings": all_holdings,
        "errors": errors,
        "realtime": realtime,
    }


def main():
    """Print Plaid balances."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch Plaid account balances.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Cost control:\n"
            "  accounts/balance/get (--force or first run) costs $0.10/item.\n"
            "  accounts/get         (--cached)             is free.\n\n"
            "Typical usage:\n"
            "  Weekly spreadsheet update : python plaid_balance.py --force\n"
            "  Dev / debug session       : python plaid_balance.py --cached\n"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--force",
        action="store_true",
        help=(
            "Force a real-time fetch (accounts/balance/get), bypassing the "
            f"{MIN_FETCH_INTERVAL_HOURS}-hour rate limit. "
            "Use for the weekly spreadsheet update."
        ),
    )
    mode.add_argument(
        "--cached",
        action="store_true",
        help=(
            "Use Plaid cached balances (accounts/get). Free, no rate limit. "
            "Data may be a few hours stale — fine for dev/debug work."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Show time since last real-time fetch and exit.",
    )
    args = parser.parse_args()

    # --check: status only, no fetch
    if args.check:
        hours = hours_since_last_realtime_fetch()
        if hours is None:
            print("No real-time fetch on record.")
        else:
            print(f"Last real-time fetch: {hours:.1f}h ago.")
            if hours < MIN_FETCH_INTERVAL_HOURS:
                remaining = MIN_FETCH_INTERVAL_HOURS - hours
                print(
                    f"Rate limit active — {remaining:.1f}h until next "
                    "allowed fetch (use --force to override)."
                )
            else:
                print("Rate limit clear.")
        sys.exit(0)

    # Determine endpoint
    if args.cached:
        realtime = False
    elif args.force:
        realtime = True
    else:
        # Default: enforce rate limit to prevent accidental billable calls
        # during dev/debug Claude Code sessions.
        hours = hours_since_last_realtime_fetch()
        if hours is not None and hours < MIN_FETCH_INTERVAL_HOURS:
            remaining = MIN_FETCH_INTERVAL_HOURS - hours
            print(
                f"Rate limit: last real-time fetch was {hours:.1f}h ago "
                f"({remaining:.1f}h until next allowed).",
                file=sys.stderr,
            )
            print(
                "  --force   Run now anyway (weekly spreadsheet update)\n"
                "  --cached  Use free cached data (dev/debug)",
                file=sys.stderr,
            )
            sys.exit(2)
        realtime = True  # First run, or rate limit has cleared

    result = get_plaid_balances(realtime=realtime)

    # Stamp successful real-time fetch
    if realtime and not result["errors"]:
        record_realtime_fetch()

    fetch_label = "real-time" if realtime else "cached"

    if result["errors"]:
        print("Errors:", file=sys.stderr)
        for err in result["errors"]:
            print(f"  - {err}", file=sys.stderr)
        print()

    if not result["accounts"]:
        sys.exit(1)

    # Group accounts by institution
    by_institution = {}
    for acc in result["accounts"]:
        inst = acc["institution"]
        if inst not in by_institution:
            by_institution[inst] = []
        by_institution[inst].append(acc)

    print(f"Plaid Balances (env: {PLAID_ENV}, {fetch_label})")
    print("=" * 60)

    for institution, accounts in sorted(by_institution.items()):
        print(f"\n{institution}")
        print("-" * 40)
        for acc in accounts:
            name = acc["name"]
            mask = f" (...{acc['mask']})" if acc["mask"] else ""
            balance = acc["balance"]
            acc_type = acc["type"]

            # Show liabilities as negative
            if acc_type in ("credit", "loan"):
                display_balance = -balance
                prefix = ""
            else:
                display_balance = balance
                prefix = " "

            print(f"  {name}{mask:12}")
            sub = acc["subtype"]
            bal_str = f"{prefix}${display_balance:>12,.2f}"
            print(f"    {acc_type}/{sub:20} {bal_str}")

    # Print investment holdings if any
    if result.get("holdings"):
        print("\nInvestment Holdings")
        print("-" * 60)
        holdings_by_account = {}
        for h in result["holdings"]:
            key = (h["institution"], h["account_id"])
            if key not in holdings_by_account:
                holdings_by_account[key] = []
            holdings_by_account[key].append(h)

        for (inst, _acc_id), holdings in sorted(holdings_by_account.items()):
            total = sum(h["value"] for h in holdings)
            print(f"\n  {inst} (${total:,.2f} total)")
            for h in sorted(holdings, key=lambda x: -x["value"]):
                ticker = h["ticker"] or h["name"][:20]
                qty = h["quantity"]
                price = h["price"]
                val = h["value"]
                line = f"    {ticker:20} {qty:>10,.2f} @ ${price:>8,.2f}"
                print(f"{line} = ${val:>12,.2f}")

    print("\n" + "=" * 60)
    print(f"  Total Assets:      ${result['total_assets']:>12,.2f}")
    print(f"  Total Liabilities: ${result['total_liabilities']:>12,.2f}")
    print(f"  Net Total:         ${result['net_total']:>12,.2f}")

    # After a billable real-time fetch, push balances to the sheet and
    # nudge the user to enter the remaining manual rows before snapshotting.
    # If the write fails, the fetched values above are the only record —
    # surface that clearly so the user can paste them in manually.
    if args.force:
        import yaml

        with open("accounts.yaml") as f:
            cfg = yaml.safe_load(f)
        yaml_accounts = cfg.get("accounts", [])
        manual_ids = [m["id"] for m in cfg.get("manual_accounts", [])]
        spreadsheet_id = cfg.get("spreadsheet_id")
        if not spreadsheet_id:
            print(
                "\n⚠ accounts.yaml missing spreadsheet_id — balances above "
                "will not be written. Fix the config and paste manually.",
                file=sys.stderr,
            )
            return

        balances_by_id = aggregate_balances_by_id(
            result["accounts"], result.get("holdings", []), yaml_accounts
        )

        from google_sheets_client import SheetsClient
        from history_sheet import write_balances_to_sheet

        try:
            sheet_client = SheetsClient()
            write_balances_to_sheet(
                sheet_client, spreadsheet_id, "Net Worth", balances_by_id
            )
        except Exception as e:
            print(
                f"\n⚠ Sheet write failed after billable fetch: {e}\n"
                "  The balances printed above are the only record — "
                "paste them into column B manually if needed.",
                file=sys.stderr,
            )
            return

        print()
        print(f"✔ Wrote {len(balances_by_id)} automated rows to the spreadsheet.")
        n_manual = len(manual_ids)
        if n_manual:
            preview = ", ".join(manual_ids[:5]) + ("..." if n_manual > 5 else "")
            print(f"ℹ {n_manual} manual rows may still need attention: {preview}")
        print("→ After entering any manual balances, run:")
        print("    python balance_history.py snapshot")


def fetch_all_holdings(items: dict) -> list[dict]:
    """Fetch holdings for every item with the investments product.

    Returns a flat list of dicts with stable keys for consumption by
    history.py. Does not hit accounts/balance/get — no per-call billing.
    investments/holdings/get is on the monthly Investments subscription.

    Errors from accounts_get or get_investment_holdings are logged to
    stderr and the item is skipped, rather than aborting the whole fetch.
    """
    import sys

    all_holdings: list[dict] = []
    for _item_id, item in items.items():
        if "investments" not in item.get("products", []):
            continue
        access_token = item["access_token"]
        institution = item.get("institution_name", "Unknown")

        try:
            acct_req = AccountsGetRequest(access_token=access_token)
            acct_resp = client.accounts_get(acct_req)
            acct_map = {
                a["account_id"]: (a.get("name", ""), a.get("mask", ""))
                for a in acct_resp.to_dict()["accounts"]
            }
        except Exception as e:
            print(
                f"Warning: accounts_get failed for {institution}: {e}",
                file=sys.stderr,
            )
            acct_map = {}

        res = get_investment_holdings(access_token, institution)
        holdings, securities, err = res
        if err:
            print(
                f"Warning: holdings fetch failed for {institution}: {err}",
                file=sys.stderr,
            )
            continue
        for h in holdings:
            sec = securities.get(h.get("security_id"), {})
            value = h.get("institution_value")
            if value is None:
                qty = h.get("quantity") or 0
                price = h.get("institution_price") or 0
                value = qty * price
            aid = h.get("account_id")
            name, mask = acct_map.get(aid, ("", ""))
            all_holdings.append(
                {
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
                }
            )
    return all_holdings


if __name__ == "__main__":
    main()
