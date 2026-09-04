"""Tests for plaid_status: consent expiration classification and reporting.

These tests exercise the pure/mockable helper functions (days_until,
classify_severity, build_item_statuses, format_report, has_urgent_items)
without requiring Plaid credentials or live network calls.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import plaid
import pytest

import plaid_status as ps


# ---------------------------------------------------------------------------
# days_until
# ---------------------------------------------------------------------------


def test_days_until_future_date():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expiration = (now + timedelta(days=10)).isoformat()
    assert ps.days_until(expiration, now=now) == pytest.approx(10.0)


def test_days_until_past_date_is_negative():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expiration = (now - timedelta(days=5)).isoformat()
    assert ps.days_until(expiration, now=now) == pytest.approx(-5.0)


def test_days_until_handles_naive_datetime_as_utc():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # No timezone info, like some ISO strings might have
    expiration = "2026-01-11T00:00:00"
    assert ps.days_until(expiration, now=now) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# classify_severity
# ---------------------------------------------------------------------------


def test_classify_severity_none_is_unknown():
    assert ps.classify_severity(None) == "unknown"


def test_classify_severity_negative_is_expired():
    assert ps.classify_severity(-1.0) == "expired"


def test_classify_severity_zero_is_expired():
    assert ps.classify_severity(0.0) == "expired"


def test_classify_severity_within_critical_window():
    assert ps.classify_severity(7.0) == "critical"
    assert ps.classify_severity(0.5) == "critical"


def test_classify_severity_just_past_critical_is_warning():
    assert ps.classify_severity(7.01) == "warning"


def test_classify_severity_within_warning_window():
    assert ps.classify_severity(30.0) == "warning"


def test_classify_severity_beyond_warning_is_ok():
    assert ps.classify_severity(30.01) == "ok"
    assert ps.classify_severity(400.0) == "ok"


# ---------------------------------------------------------------------------
# check_item_login
# ---------------------------------------------------------------------------


def test_check_item_login_healthy_item(monkeypatch):
    client = MagicMock()
    response = MagicMock()
    response.to_dict.return_value = {"item": {"error": None}}
    client.item_get.return_value = response
    monkeypatch.setattr(ps, "_get_client", lambda: client)

    check = ps.check_item_login("tok")
    assert check.login_required is False
    assert check.error_code is None
    assert check.error_message is None


def test_check_item_login_detects_login_required(monkeypatch):
    client = MagicMock()
    response = MagicMock()
    response.to_dict.return_value = {
        "item": {
            "error": {
                "error_code": "ITEM_LOGIN_REQUIRED",
                "error_message": "the login details of this item are incorrect",
            }
        }
    }
    client.item_get.return_value = response
    monkeypatch.setattr(ps, "_get_client", lambda: client)

    check = ps.check_item_login("tok")
    assert check.login_required is True
    assert check.error_code == "ITEM_LOGIN_REQUIRED"
    assert "incorrect" in check.error_message


def test_check_item_login_other_error_not_flagged_as_login(monkeypatch):
    client = MagicMock()
    response = MagicMock()
    response.to_dict.return_value = {
        "item": {
            "error": {
                "error_code": "RATE_LIMIT_EXCEEDED",
                "error_message": "too many requests",
            }
        }
    }
    client.item_get.return_value = response
    monkeypatch.setattr(ps, "_get_client", lambda: client)

    check = ps.check_item_login("tok")
    assert check.login_required is False
    assert check.error_code == "RATE_LIMIT_EXCEEDED"


def test_check_item_login_handles_api_exception(monkeypatch):
    client = MagicMock()
    err = plaid.ApiException(status=400)
    err.body = (
        '{"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "login required"}'
    )
    client.item_get.side_effect = err
    monkeypatch.setattr(ps, "_get_client", lambda: client)

    check = ps.check_item_login("tok")
    assert check.login_required is True
    assert check.error_code == "ITEM_LOGIN_REQUIRED"
    # A failed call must not be mistaken for "Chase says no expiry".
    assert check.consent_expiration_known is False


# ---------------------------------------------------------------------------
# build_item_statuses
# ---------------------------------------------------------------------------


def test_build_item_statuses_empty_items():
    assert ps.build_item_statuses({}) == []


def test_build_item_statuses_computes_days_and_severity():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = {
        "item-1": {
            "institution_name": "Bank Alpha",
            "consent_expiration": (now + timedelta(days=5)).isoformat(),
        }
    }
    statuses = ps.build_item_statuses(items, now=now)
    assert len(statuses) == 1
    s = statuses[0]
    assert s.item_id == "item-1"
    assert s.institution_name == "Bank Alpha"
    assert s.severity == "critical"
    assert s.days_remaining == pytest.approx(5.0)


def test_build_item_statuses_missing_consent_expiration_is_unknown():
    items = {"item-1": {"institution_name": "Acme Bank"}}
    statuses = ps.build_item_statuses(items)
    assert statuses[0].severity == "unknown"
    assert statuses[0].days_remaining is None


def test_build_item_statuses_malformed_consent_expiration_is_unknown():
    items = {
        "item-1": {
            "institution_name": "Acme Bank",
            "consent_expiration": "not-a-date",
        }
    }
    statuses = ps.build_item_statuses(items)
    assert statuses[0].severity == "unknown"
    assert statuses[0].days_remaining is None


def test_build_item_statuses_check_login_escalates_severity(monkeypatch):
    """An item with plenty of consent lifetime left but a live
    ITEM_LOGIN_REQUIRED error must still surface as critical."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = {
        "item-1": {
            "institution_name": "Bank Alpha",
            "consent_expiration": (now + timedelta(days=300)).isoformat(),
            "access_token": "tok",
        }
    }

    def fake_check_login(access_token):
        assert access_token == "tok"
        return ps.ItemCheck(
            login_required=True,
            error_code="ITEM_LOGIN_REQUIRED",
            error_message="re-auth needed",
        )

    monkeypatch.setattr(ps, "check_item_login", fake_check_login)
    statuses = ps.build_item_statuses(items, check_login=True, now=now)
    s = statuses[0]
    assert s.severity == "critical"
    assert s.login_required is True
    assert s.error_code == "ITEM_LOGIN_REQUIRED"


def test_build_item_statuses_check_login_does_not_downgrade_expired(monkeypatch):
    """An item already "expired" by consent date must stay "expired" even
    if the live login check also flags ITEM_LOGIN_REQUIRED — "expired" is
    more urgent than "critical" and should never be downgraded."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = {
        "item-1": {
            "institution_name": "Bank Alpha",
            "consent_expiration": (now - timedelta(days=5)).isoformat(),
            "access_token": "tok",
        }
    }

    def fake_check_login(access_token):
        return ps.ItemCheck(
            login_required=True,
            error_code="ITEM_LOGIN_REQUIRED",
            error_message="re-auth needed",
        )

    monkeypatch.setattr(ps, "check_item_login", fake_check_login)
    statuses = ps.build_item_statuses(items, check_login=True, now=now)
    s = statuses[0]
    assert s.severity == "expired"
    assert s.login_required is True


def test_build_item_statuses_check_login_skips_items_without_access_token(
    monkeypatch,
):
    items = {"item-1": {"institution_name": "Acme Bank"}}

    def fake_check_login(access_token):
        raise AssertionError("should not be called without an access_token")

    monkeypatch.setattr(ps, "check_item_login", fake_check_login)
    statuses = ps.build_item_statuses(items, check_login=True)
    assert statuses[0].login_required is False


# ---------------------------------------------------------------------------
# save_items
# ---------------------------------------------------------------------------


def test_save_items_round_trips_through_load(tmp_path, monkeypatch):
    f = tmp_path / "items.json"
    monkeypatch.setattr(ps, "ITEMS_FILE", f)
    items = {"item-1": {"institution_name": "Chase", "access_token": "tok"}}
    ps.save_items(items)
    assert ps.load_items() == items


def test_save_items_creates_file_with_restricted_permissions(tmp_path, monkeypatch):
    """The file holds Plaid access tokens, so it must never be group/world
    readable — not even briefly at creation."""
    f = tmp_path / "items.json"
    monkeypatch.setattr(ps, "ITEMS_FILE", f)
    ps.save_items({"k": "v"})
    assert f.stat().st_mode & 0o777 == 0o600


def test_save_items_truncates_previous_longer_content(tmp_path, monkeypatch):
    """O_TRUNC must clear leftover bytes when the new payload is shorter."""
    f = tmp_path / "items.json"
    monkeypatch.setattr(ps, "ITEMS_FILE", f)
    ps.save_items({f"item-{i}": {"institution_name": "X" * 40} for i in range(20)})
    ps.save_items({"item-1": {"institution_name": "Chase"}})
    assert ps.load_items() == {"item-1": {"institution_name": "Chase"}}


# ---------------------------------------------------------------------------
# check_item_login: live consent_expiration_time capture
# ---------------------------------------------------------------------------


def test_check_item_login_returns_live_consent_expiration(monkeypatch):
    """Chase users can change consent duration in the Chase Security Center
    after linking, so /item/get is the only source of truth for it."""
    client = MagicMock()
    response = MagicMock()
    response.to_dict.return_value = {
        "item": {
            "error": None,
            "consent_expiration_time": datetime(2027, 3, 1, tzinfo=timezone.utc),
        }
    }
    client.item_get.return_value = response
    monkeypatch.setattr(ps, "_get_client", lambda: client)

    check = ps.check_item_login("tok")
    assert check.consent_expiration_known is True
    assert check.consent_expiration == "2027-03-01T00:00:00+00:00"


def test_check_item_login_passes_through_string_consent_expiration(monkeypatch):
    """to_dict() may hand back an already-serialized string."""
    client = MagicMock()
    response = MagicMock()
    response.to_dict.return_value = {
        "item": {"error": None, "consent_expiration_time": "2027-03-01T00:00:00+00:00"}
    }
    client.item_get.return_value = response
    monkeypatch.setattr(ps, "_get_client", lambda: client)

    check = ps.check_item_login("tok")
    assert check.consent_expiration_known is True
    assert check.consent_expiration == "2027-03-01T00:00:00+00:00"


def test_check_item_login_absent_consent_expiration_is_known_none(monkeypatch):
    """A successful call where Plaid reports no expiry is authoritative:
    the item genuinely has no expiration."""
    client = MagicMock()
    response = MagicMock()
    response.to_dict.return_value = {"item": {"error": None}}
    client.item_get.return_value = response
    monkeypatch.setattr(ps, "_get_client", lambda: client)

    check = ps.check_item_login("tok")
    assert check.consent_expiration_known is True
    assert check.consent_expiration is None


# ---------------------------------------------------------------------------
# build_item_statuses: live consent refresh
# ---------------------------------------------------------------------------


def test_check_login_prefers_live_consent_over_stale_cache(monkeypatch):
    """The whole point of issue: a cached "no expiry" must not mask a real
    expiration set later via the Chase Security Center."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = {
        "item-1": {
            "institution_name": "Chase",
            "consent_expiration": None,
            "access_token": "tok",
        }
    }
    live = (now + timedelta(days=3)).isoformat()

    def fake_check_login(access_token):
        return ps.ItemCheck(consent_expiration=live, consent_expiration_known=True)

    monkeypatch.setattr(ps, "check_item_login", fake_check_login)
    statuses = ps.build_item_statuses(items, check_login=True, now=now)
    s = statuses[0]
    assert s.consent_expiration == live
    assert s.severity == "critical"
    assert s.days_remaining == pytest.approx(3.0)


def test_check_login_refreshes_items_dict_in_place(monkeypatch):
    """The refreshed value must be written back so it can be persisted."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = {
        "item-1": {
            "institution_name": "Chase",
            "consent_expiration": None,
            "access_token": "tok",
        }
    }
    live = (now + timedelta(days=200)).isoformat()

    monkeypatch.setattr(
        ps,
        "check_item_login",
        lambda tok: ps.ItemCheck(
            consent_expiration=live, consent_expiration_known=True
        ),
    )
    ps.build_item_statuses(items, check_login=True, now=now)
    assert items["item-1"]["consent_expiration"] == live


def test_check_login_clears_consent_when_plaid_reports_none(monkeypatch):
    """A successful call reporting no expiry must clear a stale cached date,
    not leave it in place. This is the bug in plaid_link_server's refresh."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = {
        "item-1": {
            "institution_name": "Chase",
            "consent_expiration": (now + timedelta(days=10)).isoformat(),
            "access_token": "tok",
        }
    }

    monkeypatch.setattr(
        ps,
        "check_item_login",
        lambda tok: ps.ItemCheck(
            consent_expiration=None, consent_expiration_known=True
        ),
    )
    statuses = ps.build_item_statuses(items, check_login=True, now=now)
    assert statuses[0].consent_expiration is None
    assert statuses[0].severity == "unknown"
    assert items["item-1"]["consent_expiration"] is None


def test_failed_check_does_not_clear_cached_consent(monkeypatch):
    """An API error yields consent_expiration_known=False. Treating that as
    "no expiry" would silently discard a real expiration date."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cached = (now + timedelta(days=5)).isoformat()
    items = {
        "item-1": {
            "institution_name": "Chase",
            "consent_expiration": cached,
            "access_token": "tok",
        }
    }

    monkeypatch.setattr(
        ps,
        "check_item_login",
        lambda tok: ps.ItemCheck(
            login_required=True,
            error_code="ITEM_LOGIN_REQUIRED",
            error_message="re-auth needed",
            consent_expiration_known=False,
        ),
    )
    statuses = ps.build_item_statuses(items, check_login=True, now=now)
    assert statuses[0].consent_expiration == cached
    assert statuses[0].severity == "critical"
    assert items["item-1"]["consent_expiration"] == cached


def test_without_check_login_cached_consent_is_used_unchanged(monkeypatch):
    """No live call means no refresh; the cached value stands."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cached = (now + timedelta(days=100)).isoformat()
    items = {"item-1": {"institution_name": "Chase", "consent_expiration": cached}}

    def fake_check_login(access_token):
        raise AssertionError("must not make a live call without check_login")

    monkeypatch.setattr(ps, "check_item_login", fake_check_login)
    statuses = ps.build_item_statuses(items, now=now)
    assert statuses[0].consent_expiration == cached


# ---------------------------------------------------------------------------
# has_urgent_items
# ---------------------------------------------------------------------------


def test_has_urgent_items_false_when_all_ok():
    statuses = [
        ps.ItemStatus("i1", "Bank A", "2099-01-01", 999.0, "ok"),
        ps.ItemStatus("i2", "Bank B", "2099-01-01", 999.0, "warning"),
    ]
    assert ps.has_urgent_items(statuses) is False


def test_has_urgent_items_true_for_critical():
    statuses = [ps.ItemStatus("i1", "Bank A", "2020-01-01", 3.0, "critical")]
    assert ps.has_urgent_items(statuses) is True


def test_has_urgent_items_true_for_expired():
    statuses = [ps.ItemStatus("i1", "Bank A", "2020-01-01", -3.0, "expired")]
    assert ps.has_urgent_items(statuses) is True


def test_has_urgent_items_true_for_login_required_even_if_severity_ok():
    statuses = [
        ps.ItemStatus("i1", "Bank A", "2099-01-01", 999.0, "ok", login_required=True)
    ]
    assert ps.has_urgent_items(statuses) is True


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_empty_items_mentions_link_server():
    report = ps.format_report([])
    assert "No connected items found" in report
    assert "plaid_link_server.py" in report


def test_format_report_orders_most_urgent_first():
    statuses = [
        ps.ItemStatus("i1", "OK Bank", "2099-01-01", 999.0, "ok"),
        ps.ItemStatus("i2", "Expired Bank", "2020-01-01", -3.0, "expired"),
        ps.ItemStatus("i3", "Warning Bank", "2026-02-01", 25.0, "warning"),
    ]
    report = ps.format_report(statuses)
    expired_pos = report.index("Expired Bank")
    warning_pos = report.index("Warning Bank")
    ok_pos = report.index("OK Bank")
    assert expired_pos < warning_pos < ok_pos


def test_format_report_includes_summary_counts():
    statuses = [
        ps.ItemStatus("i1", "Bank A", "2020-01-01", -1.0, "expired"),
        ps.ItemStatus("i2", "Bank B", "2026-02-01", 25.0, "warning"),
        ps.ItemStatus("i3", "Bank C", "2099-01-01", 999.0, "ok"),
    ]
    report = ps.format_report(statuses)
    assert "1 critical/expired, 1 warning" in report


def test_format_report_shows_login_required_note():
    statuses = [
        ps.ItemStatus(
            "i1",
            "Bank A",
            "2099-01-01",
            999.0,
            "critical",
            login_required=True,
            error_code="ITEM_LOGIN_REQUIRED",
            error_message="re-auth needed",
        )
    ]
    report = ps.format_report(statuses)
    assert "Login required" in report
    assert "ITEM_LOGIN_REQUIRED" in report
    assert "plaid_link_server.py to re-link" in report


def test_format_report_summary_counts_unknown_items():
    """Items with no expiration data must be counted, not hidden.

    Issue #165: a summary reading "0 critical/expired, 0 warning" looks
    healthy even when most items have no expiration date to check, so no
    warning could ever fire for them.
    """
    statuses = [
        ps.ItemStatus("i1", "Bank A", None, None, "unknown"),
        ps.ItemStatus("i2", "Bank B", None, None, "unknown"),
        ps.ItemStatus("i3", "Bank C", "2099-01-01", 999.0, "ok"),
    ]
    report = ps.format_report(statuses)
    assert "2 unknown" in report


def test_format_report_summary_omits_unknown_when_all_known():
    """No unknown items means no unknown clause cluttering the summary."""
    statuses = [
        ps.ItemStatus("i1", "Bank A", "2099-01-01", 999.0, "ok"),
        ps.ItemStatus("i2", "Bank B", "2026-02-01", 25.0, "warning"),
    ]
    report = ps.format_report(statuses)
    assert "unknown" not in report


def test_format_report_unknown_items_get_remediation_hint():
    """An unknown item must say how to find out, not just that it is unknown."""
    statuses = [ps.ItemStatus("i1", "Bank A", None, None, "unknown")]
    report = ps.format_report(statuses)
    assert "--check-login" in report
