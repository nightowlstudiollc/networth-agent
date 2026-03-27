#!/usr/bin/env python3
"""Fetch Mercury account balances."""

import os
import sys

import requests
from dotenv import load_dotenv

MERCURY_API_URL = "https://api.mercury.com/api/v1/accounts"


def get_mercury_balances() -> dict:
    """Fetch all Mercury account balances.

    Returns:
        dict with 'total_usd' and 'accounts' list
    """
    load_dotenv()

    token = os.getenv("MERCURY_API_TOKEN")
    if not token:
        raise ValueError("MERCURY_API_TOKEN environment variable not set")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.get(MERCURY_API_URL, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    accounts = data.get("accounts", [])

    total_usd = 0.0
    holdings = []

    for account in accounts:
        # Only include active Mercury accounts (not external/recipient)
        if account.get("status") != "active":
            continue
        if account.get("type") != "mercury":
            continue

        available = float(account.get("availableBalance", 0))
        current = float(account.get("currentBalance", 0))

        holdings.append(
            {
                "name": account.get("name", "Unknown"),
                "nickname": account.get("nickname"),
                "account_number": account.get("accountNumber", "")[-4:],
                "available_balance": available,
                "current_balance": current,
                "kind": account.get("kind"),
            }
        )
        total_usd += available

    return {
        "total_usd": total_usd,
        "accounts": holdings,
    }


def main():
    """Print Mercury balances."""
    try:
        result = get_mercury_balances()
        print("Mercury Balances:")
        print("-" * 50)
        for acct in result["accounts"]:
            name = acct["nickname"] or acct["name"]
            print(f"  {name:30} ${acct['available_balance']:>12,.2f}")
        print("-" * 50)
        print(f"  {'TOTAL':30} ${result['total_usd']:>12,.2f}")
    except requests.exceptions.HTTPError as e:
        msg = f"HTTP Error: {e.response.status_code} - {e.response.text}"
        print(msg, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
