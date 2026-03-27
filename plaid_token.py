#!/usr/bin/env python3
"""Manage Plaid OAuth tokens for MCP access."""

import json
import os
import stat
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

TOKEN_FILE = Path(__file__).parent / ".plaid_token.json"
OAUTH_URL = "https://production.plaid.com/oauth/token"


def get_credentials():
    """Load Plaid credentials from environment."""
    load_dotenv()
    client_id = os.getenv("PLAID_CLIENT_ID")
    secret = os.getenv("PLAID_SECRET")
    if not client_id or not secret:
        raise ValueError("PLAID_CLIENT_ID and PLAID_SECRET must be set")
    return client_id, secret


def fetch_new_token():
    """Get a new OAuth token from Plaid."""
    client_id, secret = get_credentials()
    resp = requests.post(
        OAUTH_URL,
        json={
            "client_id": client_id,
            "client_secret": secret,
            "grant_type": "client_credentials",
            "scope": "mcp:dashboard",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "access_token" not in data:
        raise ValueError(f"Token request failed: {data}")

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + data.get("expires_in", 900) - 60,
    }


def refresh_token(refresh_tok):
    """Refresh an expired token.

    Note: Plaid's OAuth API uses "secret" for refresh requests but
    "client_secret" for initial token requests. This is per Plaid docs.
    """
    client_id, secret = get_credentials()
    resp = requests.post(
        OAUTH_URL,
        json={
            "client_id": client_id,
            "secret": secret,  # Plaid uses "secret" for refresh
            "refresh_token": refresh_tok,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "access_token" not in data:
        raise ValueError(f"Token refresh failed: {data}")

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_tok),
        "expires_at": time.time() + data.get("expires_in", 900) - 60,
    }


def write_token_file(token_data):
    """Write token to file with restrictive permissions (600)."""
    TOKEN_FILE.touch(mode=stat.S_IRUSR | stat.S_IWUSR, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token_data))


def get_valid_token():
    """Get a valid token, refreshing or fetching new if needed."""
    # Try to load cached token
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if cached.get("expires_at", 0) > time.time():
                return cached["access_token"]
            # Try refresh
            if cached.get("refresh_token"):
                try:
                    new_token = refresh_token(cached["refresh_token"])
                    write_token_file(new_token)
                    return new_token["access_token"]
                except Exception as e:
                    # Log but continue to fetch new token
                    print(f"Token refresh failed: {e}", file=sys.stderr)
        except Exception as e:
            # Log but continue to fetch new token
            print(f"Failed to load cached token: {e}", file=sys.stderr)

    # Fetch new token
    new_token = fetch_new_token()
    write_token_file(new_token)
    return new_token["access_token"]


def get_token_with_expiry():
    """Get valid token with expiry timestamp for proxy use.

    Returns:
        tuple: (access_token, expires_at) where expires_at is Unix timestamp
    """
    # Try to load cached token
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if cached.get("expires_at", 0) > time.time():
                return cached["access_token"], cached["expires_at"]
            # Try refresh
            if cached.get("refresh_token"):
                try:
                    new_token = refresh_token(cached["refresh_token"])
                    write_token_file(new_token)
                    return new_token["access_token"], new_token["expires_at"]
                except Exception as e:
                    print(f"Token refresh failed: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Failed to load cached token: {e}", file=sys.stderr)

    # Fetch new token
    new_token = fetch_new_token()
    write_token_file(new_token)
    return new_token["access_token"], new_token["expires_at"]


def main():
    """Print current valid token."""
    try:
        token = get_valid_token()
        print(token)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
