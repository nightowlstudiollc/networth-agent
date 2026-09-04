#!/usr/bin/env python3
"""Report Plaid consent expiration status and item health.

Phase 1 (visibility) of issue #8: surfaces upcoming consent expirations
and ITEM_LOGIN_REQUIRED errors before they silently break balance
fetches. Run standalone (e.g. from a daily cron/launchd job) or via
`--check` for a monitoring-friendly non-zero exit on anything urgent.
"""

import os
import sys

import op_bootstrap

op_bootstrap.bootstrap("PLAID_CLIENT_ID")

import json  # noqa: E402 (deferred: must run after venv+creds bootstrap)
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import plaid  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from plaid.api import plaid_api  # noqa: E402
from plaid.model.item_get_request import ItemGetRequest  # noqa: E402

load_dotenv()

# Environment configuration (mirrors plaid_balance.py / plaid_accounts.py)
PLAID_ENV = os.getenv("PLAID_ENV", "production")

if PLAID_ENV == "sandbox":
    _host = plaid.Environment.Sandbox
    PLAID_SECRET = os.getenv("PLAID_SANDBOX_SECRET")
    ITEMS_FILE = Path(__file__).parent / ".plaid_items_sandbox.json"
elif PLAID_ENV == "development":
    _host = plaid.Environment.Development
    PLAID_SECRET = os.getenv("PLAID_SECRET")
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"
else:
    _host = plaid.Environment.Production
    PLAID_SECRET = os.getenv("PLAID_SECRET")
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")

# Thresholds per issue #8's Phase 1 spec.
WARNING_DAYS = 30
CRITICAL_DAYS = 7

_client: plaid_api.PlaidApi | None = None


def _get_client() -> plaid_api.PlaidApi:
    """Return the Plaid API client, initializing it on first use."""
    global _client
    if _client is not None:
        return _client
    if not PLAID_CLIENT_ID or not PLAID_SECRET:
        raise ValueError(
            "PLAID_CLIENT_ID and PLAID_SECRET (or PLAID_SANDBOX_SECRET) required"
        )
    configuration = plaid.Configuration(
        host=_host,
        api_key={
            "clientId": PLAID_CLIENT_ID,
            "secret": PLAID_SECRET,
        },
    )
    api_client = plaid.ApiClient(configuration)
    _client = plaid_api.PlaidApi(api_client)
    return _client


def load_items() -> dict:
    """Load saved items from file."""
    if ITEMS_FILE.exists():
        content = ITEMS_FILE.read_text().strip()
        if content:
            return json.loads(content)
    return {}


def save_items(items: dict) -> None:
    """Persist items back to disk with owner-only permissions.

    The file holds Plaid access tokens. os.open with mode 0600 creates the
    file already restricted, rather than touch-then-write, which leaves a
    brief window where a new file sits at the default umask.
    """
    fd = os.open(ITEMS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(items, f, indent=2)


@dataclass
class ItemStatus:
    """Consent/health status for a single connected Plaid item."""

    item_id: str
    institution_name: str
    consent_expiration: str | None
    days_remaining: float | None
    severity: str  # "ok" | "warning" | "critical" | "expired" | "unknown"
    login_required: bool = False
    error_code: str | None = None
    error_message: str | None = None


def days_until(expiration_iso: str, now: datetime | None = None) -> float:
    """Return days remaining until an ISO-8601 expiration timestamp.

    Negative values mean the expiration is already in the past.
    """
    now = now or datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(expiration_iso)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    delta = expires_at - now
    return delta.total_seconds() / 86400


def classify_severity(days_remaining: float | None) -> str:
    """Classify remaining consent lifetime per issue #8's Phase 1 thresholds.

    - No expiration data at all: "unknown"
    - Already expired (<= 0 days): "expired"
    - Within CRITICAL_DAYS: "critical"
    - Within WARNING_DAYS: "warning"
    - Otherwise: "ok"
    """
    if days_remaining is None:
        return "unknown"
    if days_remaining <= 0:
        return "expired"
    if days_remaining <= CRITICAL_DAYS:
        return "critical"
    if days_remaining <= WARNING_DAYS:
        return "warning"
    return "ok"


@dataclass
class ItemCheck:
    """Result of a live /item/get health + consent check.

    ``consent_expiration_known`` distinguishes "Plaid told us there is no
    expiration" from "we never found out". Only the former may overwrite a
    cached expiration date.
    """

    login_required: bool = False
    error_code: str | None = None
    error_message: str | None = None
    consent_expiration: str | None = None
    consent_expiration_known: bool = False


def _serialize_consent_expiration(value: object) -> str | None:
    """Normalize Plaid's consent_expiration_time to an ISO-8601 string.

    to_dict() may return either a datetime or an already-serialized string
    depending on the response path.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def check_item_login(access_token: str) -> ItemCheck:
    """Check an item's live status and read its authoritative consent expiry.

    A single /item/get call yields both, so the consent refresh is free once
    we're already making the call.
    """
    try:
        req = ItemGetRequest(access_token=access_token)
        response = _get_client().item_get(req)
        item = response.to_dict()["item"]
        # The call succeeded, so whatever Plaid reports is authoritative —
        # including the absence of an expiration.
        consent_expiration = _serialize_consent_expiration(
            item.get("consent_expiration_time")
        )
        error = item.get("error")
        if error:
            error_code = error.get("error_code")
            return ItemCheck(
                login_required=error_code == "ITEM_LOGIN_REQUIRED",
                error_code=error_code,
                error_message=error.get("error_message"),
                consent_expiration=consent_expiration,
                consent_expiration_known=True,
            )
        return ItemCheck(
            consent_expiration=consent_expiration,
            consent_expiration_known=True,
        )
    except plaid.ApiException as e:
        error_body = json.loads(e.body) if e.body else {}
        error_code = error_body.get("error_code", "UNKNOWN")
        # The call failed, so we learned nothing about consent. Leaving
        # consent_expiration_known False stops the caller from mistaking
        # this for "Plaid says there is no expiration".
        return ItemCheck(
            login_required=error_code == "ITEM_LOGIN_REQUIRED",
            error_code=error_code,
            error_message=error_body.get("error_message", str(e)),
        )


def build_item_statuses(
    items: dict, check_login: bool = False, now: datetime | None = None
) -> list[ItemStatus]:
    """Build ItemStatus entries for every connected item.

    Side effect: with check_login=True, refreshed consent expirations are
    written back into ``items`` in place. The caller is responsible for
    persisting them (see main()).

    Args:
        items: Loaded items dict (item_id -> item data), as from load_items().
            Mutated in place when check_login refreshes a consent date.
        check_login: If True, make a live Plaid call per item to detect
            ITEM_LOGIN_REQUIRED. Off by default since it's a network call
            per item (accounts/get-class calls are free, but this still adds
            real latency and API surface for what's meant to be a quick
            local status check).
        now: Override "current time" for testing.
    """
    statuses = []
    for item_id, item_data in items.items():
        institution_name = item_data.get("institution_name", "Unknown")
        consent_exp = item_data.get("consent_expiration")

        login_required = False
        error_code = None
        error_message = None

        if check_login:
            access_token = item_data.get("access_token")
            if access_token:
                check = check_item_login(access_token)
                login_required = check.login_required
                error_code = check.error_code
                error_message = check.error_message
                if check.consent_expiration_known:
                    # Live value wins: a user can change consent duration
                    # after linking (e.g. Chase's Security Center), which
                    # would leave the cached value silently stale.
                    consent_exp = check.consent_expiration
                    item_data["consent_expiration"] = consent_exp

        days_remaining = None
        if consent_exp:
            try:
                days_remaining = days_until(consent_exp, now=now)
            except ValueError:
                days_remaining = None

        severity = classify_severity(days_remaining)

        # Escalate to at least "critical" — but never downgrade an item
        # already classified as "expired" by consent date, which is more
        # urgent.
        if login_required and (
            _SEVERITY_ORDER.get(severity, 99) > _SEVERITY_ORDER["critical"]
        ):
            severity = "critical"

        statuses.append(
            ItemStatus(
                item_id=item_id,
                institution_name=institution_name,
                consent_expiration=consent_exp,
                days_remaining=days_remaining,
                severity=severity,
                login_required=login_required,
                error_code=error_code,
                error_message=error_message,
            )
        )
    return statuses


_SEVERITY_ORDER = {"expired": 0, "critical": 1, "warning": 2, "unknown": 3, "ok": 4}


def format_report(statuses: list[ItemStatus]) -> str:
    """Format a human-readable status report, most urgent items first."""
    lines = []
    lines.append("=" * 70)
    lines.append("PLAID CONSENT STATUS")
    lines.append(f"Environment: {PLAID_ENV}")
    lines.append(f"Checked: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 70)

    if not statuses:
        lines.append("\nNo connected items found. Run plaid_link_server.py first.")
        return "\n".join(lines)

    ordered = sorted(statuses, key=lambda s: _SEVERITY_ORDER[s.severity])

    labels = {
        "expired": "EXPIRED",
        "critical": "CRITICAL",
        "warning": "WARNING",
        "unknown": "UNKNOWN",
        "ok": "OK",
    }

    for s in ordered:
        lines.append(f"\n{'-' * 70}")
        lines.append(f"{labels[s.severity]:8} {s.institution_name}")
        lines.append(f"{'-' * 70}")
        lines.append(f"  Item ID: {s.item_id}")
        if s.consent_expiration:
            lines.append(f"  Consent Expiration: {s.consent_expiration}")
            if s.days_remaining is not None:
                if s.days_remaining < 0:
                    lines.append(
                        f"  Days remaining: EXPIRED "
                        f"{abs(s.days_remaining):.1f} days ago"
                    )
                else:
                    lines.append(f"  Days remaining: {s.days_remaining:.1f}")
        else:
            lines.append(
                "  Consent Expiration: unknown (no data) — cannot warn before "
                "this consent lapses"
            )
            lines.append(
                "    Plaid reports no expiration for this item. Re-run with "
                "--check-login to ask Plaid directly."
            )
        if s.login_required:
            lines.append(
                f"  Login required: {s.error_code} - {s.error_message} "
                "(run plaid_link_server.py to re-link)"
            )
        elif s.error_code:
            lines.append(f"  Error: {s.error_code} - {s.error_message}")

    lines.append(f"\n{'=' * 70}")
    critical_count = sum(1 for s in statuses if s.severity in ("critical", "expired"))
    warning_count = sum(1 for s in statuses if s.severity == "warning")
    unknown_count = sum(1 for s in statuses if s.severity == "unknown")
    summary = (
        f"SUMMARY: {len(statuses)} item(s) — "
        f"{critical_count} critical/expired, {warning_count} warning"
    )
    # Without this clause a report where most items have no expiration date
    # reads identically to a fully healthy one (issue #165). An item we
    # cannot check is not an item that is fine.
    if unknown_count:
        summary += f", {unknown_count} unknown"
    lines.append(summary)
    if unknown_count:
        lines.append(
            f"  {unknown_count} item(s) have no consent expiration data, so no "
            "expiry warning can fire for them."
        )
        lines.append(
            "  Re-run with --check-login to refresh expirations from Plaid."
        )
    lines.append("=" * 70)

    return "\n".join(lines)


def has_urgent_items(statuses: list[ItemStatus]) -> bool:
    """True if any item is expired, critical, or needs re-login."""
    return any(
        s.severity in ("critical", "expired") or s.login_required for s in statuses
    )


def main() -> None:
    """Print the Plaid consent status report."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Report Plaid consent expiration status"
    )
    parser.add_argument(
        "--check-login",
        action="store_true",
        help="Make a live Plaid call per item to detect ITEM_LOGIN_REQUIRED "
        "(slower; off by default)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any item is critical, expired, or needs "
        "re-login (for use in cron/monitoring)",
    )
    args = parser.parse_args()

    items = load_items()
    before = {k: v.get("consent_expiration") for k, v in items.items()}
    statuses = build_item_statuses(items, check_login=args.check_login)

    # build_item_statuses refreshes consent dates in place when --check-login
    # made a live call. Persist them so subsequent runs without --check-login
    # report the current value rather than the link-time one.
    after = {k: v.get("consent_expiration") for k, v in items.items()}
    if after != before:
        save_items(items)
        for item_id, new_value in after.items():
            if new_value != before.get(item_id):
                name = items[item_id].get("institution_name", "Unknown")
                print(
                    f"Refreshed consent expiration for {name}: "
                    f"{before.get(item_id)} -> {new_value}"
                )

    print(format_report(statuses))

    if args.check and has_urgent_items(statuses):
        sys.exit(1)


if __name__ == "__main__":
    main()
