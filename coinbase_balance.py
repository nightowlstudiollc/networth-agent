#!/usr/bin/env python3
"""Fetch Coinbase account balances using the Advanced Trade API."""

import os
import sys
from decimal import Decimal
from pathlib import Path

from coinbase.rest import RESTClient
from dotenv import load_dotenv


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


def get_coinbase_balances() -> dict:
    """Fetch all Coinbase account balances with USD values.

    Returns:
        dict with 'total_usd' and 'accounts' list
    """
    client = get_client()
    accounts = client.get_accounts()

    total_usd = Decimal("0")
    holdings = []
    prices_cache = {}

    for account in accounts.accounts:
        balance_info = getattr(account, "available_balance", None) or {}
        available = Decimal(balance_info.get("value", "0"))
        currency = balance_info.get("currency", "")

        if available > 0:
            # Get USD value
            if currency in ("USD", "USDC"):
                usd_value = available
            else:
                # Fetch price if not cached
                if currency not in prices_cache:
                    try:
                        product_id = f"{currency}-USD"
                        product = client.get_product(product_id)
                        prices_cache[currency] = Decimal(product.price)
                    except Exception as e:
                        print(
                            f"Warning: {currency} price fetch failed: {e}",
                            file=sys.stderr,
                        )
                        prices_cache[currency] = Decimal("0")

                price = prices_cache.get(currency, Decimal("0"))
                usd_value = available * price

            holdings.append(
                {
                    "currency": currency,
                    "balance": float(available),
                    "usd_value": float(usd_value),
                    "name": account.name,
                }
            )
            total_usd += usd_value

    # Sort by USD value descending
    holdings.sort(key=lambda x: x["usd_value"], reverse=True)

    return {
        "total_usd": float(total_usd),
        "accounts": holdings,
    }


def main():
    """Print Coinbase balances."""
    try:
        result = get_coinbase_balances()
        print("Coinbase Balances:")
        print("-" * 50)
        for acct in result["accounts"]:
            cur = acct["currency"]
            bal = acct["balance"]
            usd = acct["usd_value"]
            print(f"  {cur:8} {bal:>18.8f}  ${usd:>10.2f}")
        print("-" * 50)
        print(f"  {'TOTAL':8} {' ':>18}  ${result['total_usd']:>10.2f}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
