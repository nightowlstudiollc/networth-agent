#!/usr/bin/env python3
"""Fetch home value (Zestimate) from Zillow."""

import json
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Zillow URL for the property.
# Set via ZILLOW_URL environment variable, or config.yaml (zillow.url).
# To find your URL: navigate to your property on
# zillow.com and copy the full URL.
ZILLOW_URL = os.getenv("ZILLOW_URL", "")

# Load from config.yaml if env var not set
if not ZILLOW_URL:
    try:
        import yaml

        config_path = Path(__file__).parent / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                _cfg = yaml.safe_load(f)
            ZILLOW_URL = _cfg.get("zillow", {}).get("url", "")
    except ImportError:
        pass  # yaml not installed; must use ZILLOW_URL env var

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": (
        '"Chromium";v="134", "Google Chrome";v="134",' ' "Not:A-Brand";v="24"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def get_zestimate(zillow_url: str) -> dict:
    """Fetch Zestimate from a Zillow property page.

    Args:
        zillow_url: Full Zillow URL for the property

    Returns:
        Dict with zestimate, rent_zestimate, address, and last_updated
    """
    if not zillow_url:
        raise ValueError(
            "No Zillow URL configured. Set ZILLOW_URL env var or add "
            "zillow.url to config.yaml (copy from config.example.yaml)."
        )

    resp = requests.get(zillow_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    result = {
        "url": zillow_url,
        "zestimate": None,
        "rent_zestimate": None,
        "address": None,
    }

    # Try to find the Next.js data blob with property info
    json_match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html,
        re.DOTALL,
    )

    if json_match:
        try:
            data = json.loads(json_match.group(1))
            props = data.get("props", {}).get("pageProps", {})

            property_data = None

            # Path 1: legacy initialData.property / aboveTheFold
            initial = props.get("initialData", {})
            if "property" in initial:
                property_data = initial["property"]
            elif "aboveTheFold" in initial:
                property_data = initial["aboveTheFold"]

            # Path 2: gdpClientCache (GraphQL cache since ~2026)
            if not property_data:
                comp = props.get("componentProps", {})
                cache_raw = comp.get("gdpClientCache")
                if isinstance(cache_raw, str) and cache_raw:
                    cache = json.loads(cache_raw)
                elif isinstance(cache_raw, dict):
                    cache = cache_raw
                else:
                    cache = {}
                for entry in cache.values():
                    try:
                        if isinstance(entry, str):
                            obj = json.loads(entry)
                        else:
                            obj = entry
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(obj, dict) and "property" in obj:
                        property_data = obj["property"]
                        break

            if property_data:
                result["zestimate"] = property_data.get("zestimate")
                result["rent_zestimate"] = property_data.get("rentZestimate")
                result["address"] = property_data.get("address", {})

                if not result["zestimate"]:
                    result["zestimate"] = property_data.get("price")

        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback: regex for Zestimate value in HTML
    if not result["zestimate"]:
        zest_match = re.search(
            r"\$[\d,]+(?=\s*(?:Zestimate|</span>.*?Zestimate))",
            html,
            re.IGNORECASE,
        )
        if zest_match:
            val = zest_match.group().replace("$", "").replace(",", "")
            result["zestimate"] = int(val)

    return result


def format_currency(amount: int | float | None) -> str:
    """Format amount as currency."""
    if amount is None:
        return "N/A"
    return f"${amount:,.0f}"


def _write_to_sheet(zestimate: int | float) -> bool:
    """Write the Zestimate to column B of the zillow-home row.

    Returns True on success, False if config is unavailable (e.g. tests, CI
    without accounts.yaml). Any other failure (auth, missing row, network)
    is raised so the caller surfaces it — the scrape already succeeded.
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
        client, spreadsheet_id, "Net Worth", {"zillow-home": zestimate}
    )
    return True


def main():
    """Fetch and display Zestimate."""
    url = sys.argv[1] if len(sys.argv) > 1 else ZILLOW_URL

    if not url:
        print("Usage: python zillow_balance.py <zillow_url>")
        print(
            "Or set ZILLOW_URL environment variable,"
            " or configure zillow.url in config.yaml"
        )
        sys.exit(1)

    try:
        data = get_zestimate(url)

        addr = data.get("address")
        if isinstance(addr, dict):
            addr_str = addr.get("streetAddress", "Unknown")
            city = addr.get("city", "")
            state = addr.get("state", "")
            if city and state:
                addr_str = f"{addr_str}, {city}, {state}"
        else:
            addr_str = addr or "Unknown"

        print(f"Address: {addr_str}")
        print(f"Zestimate: {format_currency(data['zestimate'])}")
        if data["rent_zestimate"]:
            rent = format_currency(data["rent_zestimate"])
            print(f"Rent Zestimate: {rent}/mo")

        if data["zestimate"]:
            print(f"\nValue: {data['zestimate']}")
            if _write_to_sheet(data["zestimate"]):
                print("✔ Wrote zillow-home to the spreadsheet.")
            else:
                print(
                    "ℹ accounts.yaml not configured — skipped sheet write. "
                    f"Enter {data['zestimate']} in column B of the "
                    "zillow-home row manually."
                )

    except requests.RequestException as e:
        print(f"Error fetching Zillow page: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
