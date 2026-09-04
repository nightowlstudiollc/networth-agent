"""Tests for plaid_token_watcher pure and mockable functions."""

import json

import plaid_token_watcher as ptw


# ---------------------------------------------------------------------------
# format_time
# ---------------------------------------------------------------------------


def test_format_time_negative_is_expired():
    assert ptw.format_time(-1) == "EXPIRED"


def test_format_time_zero():
    assert ptw.format_time(0) == "00:00"


def test_format_time_under_one_minute():
    assert ptw.format_time(45) == "00:45"


def test_format_time_exactly_one_minute():
    assert ptw.format_time(60) == "01:00"


def test_format_time_mixed():
    assert ptw.format_time(90) == "01:30"


def test_format_time_large():
    assert ptw.format_time(3661) == "61:01"


# ---------------------------------------------------------------------------
# get_color
# ---------------------------------------------------------------------------


def test_get_color_expired_is_red():
    assert ptw.get_color(-1) == ptw.RED


def test_get_color_zero_is_red():
    assert ptw.get_color(0) == ptw.RED


def test_get_color_under_two_minutes_is_red():
    assert ptw.get_color(119) == ptw.RED


def test_get_color_two_minutes_is_yellow():
    assert ptw.get_color(120) == ptw.YELLOW


def test_get_color_under_five_minutes_is_yellow():
    assert ptw.get_color(299) == ptw.YELLOW


def test_get_color_five_minutes_is_green():
    assert ptw.get_color(300) == ptw.GREEN


def test_get_color_well_above_threshold_is_green():
    assert ptw.get_color(3600) == ptw.GREEN


# ---------------------------------------------------------------------------
# get_token_status
# ---------------------------------------------------------------------------


def test_get_token_status_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ptw, "TOKEN_FILE", tmp_path / "nonexistent.json")
    remaining, has_refresh = ptw.get_token_status()
    assert remaining == -1
    assert has_refresh is False


def test_get_token_status_invalid_json(tmp_path, monkeypatch):
    token_file = tmp_path / ".plaid_token.json"
    token_file.write_text("not-json")
    monkeypatch.setattr(ptw, "TOKEN_FILE", token_file)
    remaining, has_refresh = ptw.get_token_status()
    assert remaining == -1
    assert has_refresh is False


def test_get_token_status_with_expiry_and_refresh(tmp_path, monkeypatch):
    fake_now = 1000.0
    expires_at = fake_now + 600.0
    token_file = tmp_path / ".plaid_token.json"
    token_file.write_text(
        json.dumps({"expires_at": expires_at, "refresh_token": "tok_abc"})
    )
    monkeypatch.setattr(ptw, "TOKEN_FILE", token_file)
    monkeypatch.setattr(ptw.time, "time", lambda: fake_now)
    remaining, has_refresh = ptw.get_token_status()
    assert remaining == 600.0
    assert has_refresh is True


def test_get_token_status_no_refresh_token(tmp_path, monkeypatch):
    fake_now = 1000.0
    expires_at = fake_now + 120.0
    token_file = tmp_path / ".plaid_token.json"
    token_file.write_text(json.dumps({"expires_at": expires_at}))
    monkeypatch.setattr(ptw, "TOKEN_FILE", token_file)
    monkeypatch.setattr(ptw.time, "time", lambda: fake_now)
    remaining, has_refresh = ptw.get_token_status()
    assert remaining == 120.0
    assert has_refresh is False


def test_get_token_status_expired_token(tmp_path, monkeypatch):
    fake_now = 2000.0
    expires_at = fake_now - 60.0
    token_file = tmp_path / ".plaid_token.json"
    token_file.write_text(json.dumps({"expires_at": expires_at}))
    monkeypatch.setattr(ptw, "TOKEN_FILE", token_file)
    monkeypatch.setattr(ptw.time, "time", lambda: fake_now)
    remaining, has_refresh = ptw.get_token_status()
    assert remaining == -60.0
    assert has_refresh is False
