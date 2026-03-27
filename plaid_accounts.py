#!/usr/bin/env python3
"""Fetch and display Plaid account information."""

import json
import os
from datetime import datetime
from pathlib import Path

import plaid
from dotenv import load_dotenv
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.item_get_request import ItemGetRequest

load_dotenv()

# Plaid configuration
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_ENV = os.getenv("PLAID_ENV", "production")

if PLAID_ENV == "sandbox":
    PLAID_SECRET = os.getenv("PLAID_SANDBOX_SECRET")
    host = plaid.Environment.Sandbox
    ITEMS_FILE = Path(__file__).parent / ".plaid_items_sandbox.json"
elif PLAID_ENV == "development":
    PLAID_SECRET = os.getenv("PLAID_SECRET")
    host = plaid.Environment.Development
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"
else:
    PLAID_SECRET = os.getenv("PLAID_SECRET")
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"
    host = plaid.Environment.Production

# Validate required configuration
if not PLAID_CLIENT_ID or not PLAID_SECRET:
    raise ValueError(
        "PLAID_CLIENT_ID and PLAID_SECRET (or PLAID_SANDBOX_SECRET) required"
    )

configuration = plaid.Configuration(
    host=host,
    api_key={
        "clientId": PLAID_CLIENT_ID,
        "secret": PLAID_SECRET,
    },
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)


def load_items():
    """Load saved items from file."""
    if ITEMS_FILE.exists():
        content = ITEMS_FILE.read_text().strip()
        if content:
            return json.loads(content)
    return {}


def get_item_details(access_token):
    """Get item status and details."""
    req = ItemGetRequest(access_token=access_token)
    response = client.item_get(req)
    return response.to_dict()


def get_accounts(access_token):
    """Get accounts for an item."""
    req = AccountsGetRequest(access_token=access_token)
    response = client.accounts_get(req)
    return response.to_dict()


def format_currency(amount, currency="USD"):
    """Format amount as currency."""
    if amount is None:
        return "N/A"
    return f"${amount:,.2f} {currency}"


def main():
    """Fetch and display all connected accounts."""
    items = load_items()

    if not items:
        print("No connected items found.")
        print("Run plaid_link_server.py and connect accounts first.")
        return

    print("=" * 70)
    print("PLAID CONNECTED ACCOUNTS")
    print(f"Environment: {PLAID_ENV}")
    print(f"Retrieved: {datetime.now().isoformat()}")
    print("=" * 70)

    for item_id, item_data in items.items():
        access_token = item_data["access_token"]
        institution = item_data.get("institution_name", "Unknown")

        print(f"\n{'─' * 70}")
        print(f"INSTITUTION: {institution}")
        print(f"{'─' * 70}")

        # Get item details
        try:
            item_response = get_item_details(access_token)
            item = item_response.get("item", {})
            status = item_response.get("status", {})

            print(f"\nItem ID: {item_id}")
            print(f"Institution ID: {item.get('institution_id')}")
            print(f"Products: {', '.join(item.get('products', []))}")
            billed = ", ".join(item.get("billed_products", []))
            print(f"Billed Products: {billed}")

            # Consent/Auth info
            consent_exp = item.get("consent_expiration_time")
            if consent_exp:
                print(f"Consent Expiration: {consent_exp}")
            else:
                print("Consent Expiration: None (no expiration)")

            # Error status
            error = item.get("error")
            if error:
                err_code = error.get("error_code")
                err_msg = error.get("error_message")
                print(f"Error: {err_code} - {err_msg}")
            else:
                print("Status: Healthy (no errors)")

            # Last update times
            if status:
                txn_status = status.get("transactions")
                if txn_status:
                    last_update = txn_status.get("last_successful_update")
                    print(f"Last Transaction Update: {last_update}")

        except plaid.ApiException as e:
            print(f"Error fetching item details: {e}")

        # Get accounts
        try:
            accounts_response = get_accounts(access_token)
            accounts = accounts_response.get("accounts", [])

            print(f"\nAccounts ({len(accounts)}):")
            print("-" * 50)

            for acc in accounts:
                name = acc.get("name")
                official = acc.get("official_name", "N/A")
                print(f"\n  {name} ({official})")
                print(f"    Type: {acc.get('type')} / {acc.get('subtype')}")
                print(f"    Account ID: {acc.get('account_id')}")
                print(f"    Mask: ****{acc.get('mask')}")

                balances = acc.get("balances", {})
                current = format_currency(balances.get("current"))
                available = format_currency(balances.get("available"))
                print(f"    Current Balance: {current}")
                print(f"    Available Balance: {available}")

                if balances.get("limit"):
                    limit = format_currency(balances.get("limit"))
                    print(f"    Credit Limit: {limit}")

        except plaid.ApiException as e:
            print(f"Error fetching accounts: {e}")

    print(f"\n{'=' * 70}")
    print("AUTHENTICATION SUMMARY")
    print("=" * 70)
    print(
        """
Authentication Method: OAuth 2.0 via Plaid Link
- Access tokens are persistent until user revokes or item expires
- Sandbox items never expire
- Production items may have consent expiration (institution-dependent)
- Some institutions require periodic re-authentication (90-180 days)

To refresh credentials: Use Plaid Link in update mode
To revoke access: Call /item/remove API endpoint
"""
    )


if __name__ == "__main__":
    main()
