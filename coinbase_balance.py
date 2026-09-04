#!/usr/bin/env python3
"""Fetch Coinbase account balances using the Advanced Trade API."""

import os
import sys

import op_bootstrap

op_bootstrap.bootstrap("COINBASE_API_KEY")

from decimal import (
    Decimal,
)  # noqa: E402 (deferred: must run after venv+creds bootstrap)
from pathlib import Path  # noqa: E402

from coinbase.rest import RESTClient  # noqa: E402
from dotenv import load_dotenv  # noqa: E402


def get_client() -> RESTClient:
    """Initialize Coinbase client from environment."""
    load_dotenv()

    key_file = os.getenv("COINBASE_KEY_FILE")
    if key_file and Path(key_file).exists():
        return RESTClient(key_file=key_file)

    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    if not api_key or not api_secret:
        raise ValueError(
            "Set COINBASE_KEY_FILE or COINBASE_API_KEY and COINBASE_API_SECRET"
        )
    return RESTClient(api_key=api_key, api_secret=api_secret)


PAR_CURRENCIES = ("USD", "USDC")


def _field(account: dict, key: str) -> dict:
    """Read a balance sub-object from an account, tolerating None."""
    return account.get(key) or {}


def compute_total_usd(accounts: list[dict], prices: dict) -> dict:
    """Value a list of Coinbase accounts in USD.

    The quantity that matters is ``available_balance + hold``. Funds backing an
    open order sit in ``hold`` and are excluded from ``available_balance``;
    summing only the latter under-reports a portfolio with resting orders and
    can read as near-zero while real value is held.

    ``prices`` maps a currency to its USD price. USD and USDC are valued 1:1
    and need no entry. A currency with no price contributes zero rather than
    raising, so one delisted or illiquid asset cannot fail the whole run.

    Returns a dict with ``total_usd`` and a ``holdings`` list sorted by value.
    """
    total_usd = Decimal("0")
    holdings = []

    for account in accounts:
        available_info = _field(account, "available_balance")
        currency = available_info.get("currency") or account.get("currency", "")
        available = Decimal(available_info.get("value", "0") or "0")
        held = Decimal(_field(account, "hold").get("value", "0") or "0")
        quantity = available + held

        if quantity <= 0:
            continue

        if currency in PAR_CURRENCIES:
            usd_value = quantity
        else:
            usd_value = quantity * prices.get(currency, Decimal("0"))

        holdings.append(
            {
                "currency": currency,
                "balance": float(quantity),
                "available": float(available),
                "hold": float(held),
                "usd_value": float(usd_value),
                "name": account.get("name", ""),
            }
        )
        total_usd += usd_value

    holdings.sort(key=lambda x: x["usd_value"], reverse=True)

    return {
        "total_usd": float(total_usd),
        "holdings": holdings,
    }


def _fetch_prices(client, currencies: set) -> dict:
    """Look up USD spot prices, skipping par currencies."""
    prices = {}
    for currency in sorted(currencies - set(PAR_CURRENCIES)):
        try:
            product = client.get_product(f"{currency}-USD")
            prices[currency] = Decimal(product.price)
        except Exception as e:
            print(f"Warning: {currency} price fetch failed: {e}", file=sys.stderr)
    return prices


def get_coinbase_balances() -> dict:
    """Fetch all Coinbase account balances with USD values.

    Returns:
        dict with 'total_usd' and 'holdings' list
    """
    client = get_client()
    response = client.get_accounts()

    accounts = []
    for account in response.accounts:
        accounts.append(
            {
                "currency": getattr(account, "currency", ""),
                "name": getattr(account, "name", ""),
                "available_balance": getattr(account, "available_balance", None) or {},
                "hold": getattr(account, "hold", None) or {},
            }
        )

    currencies = {a["currency"] for a in accounts if a["currency"]}
    return compute_total_usd(accounts, _fetch_prices(client, currencies))


def _write_to_sheet(total_usd: float) -> bool:
    """Write the total to column B of the coinbase row.

    Returns True on success, False when accounts.yaml is absent or has no
    spreadsheet_id (e.g. tests, CI) — the balances still print in that case.

    Everything else propagates: auth, a missing coinbase row, network errors,
    and an ImportError from the sheet-client imports below, which are
    deliberately deferred so this module stays importable without them.
    main() catches those and exits non-zero, since a silent no-op write would
    leave a stale balance on the sheet.
    """
    try:
        import yaml

        with open(Path(__file__).parent / "accounts.yaml") as f:
            cfg = yaml.safe_load(f)
    except (FileNotFoundError, ImportError):
        return False
    spreadsheet_id = cfg.get("spreadsheet_id")
    if not spreadsheet_id:
        return False

    from google_sheets_client import SheetsClient
    from history_sheet import write_balances_to_sheet

    client = SheetsClient()
    write_balances_to_sheet(
        client, spreadsheet_id, "Net Worth", {"coinbase": total_usd}
    )
    return True


def main():
    """Print Coinbase balances and write the total to the sheet."""
    write = "--no-write" not in sys.argv
    try:
        result = get_coinbase_balances()
        print("Coinbase Balances:")
        print("-" * 62)
        for acct in result["holdings"]:
            cur = acct["currency"]
            bal = acct["balance"]
            usd = acct["usd_value"]
            held = " (incl. held)" if acct["hold"] else ""
            print(f"  {cur:8} {bal:>18.8f}  ${usd:>10.2f}{held}")
        print("-" * 62)
        print(f"  {'TOTAL':8} {' ':>18}  ${result['total_usd']:>10.2f}")

        if not write:
            return
        if _write_to_sheet(result["total_usd"]):
            print("\n✔ Wrote coinbase to the spreadsheet.")
        else:
            print(
                "\nℹ accounts.yaml not configured — skipped sheet write. "
                f"Enter {result['total_usd']:.2f} in column B of the "
                "coinbase row manually."
            )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
