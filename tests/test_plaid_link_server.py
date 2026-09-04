"""Tests for plaid_link_server pure/mockable helper functions.

These tests exercise load_items, save_items, get_plaid_user, and
save_plaid_user without requiring Plaid credentials or Flask requests.
"""

import json
import os
import signal
from unittest.mock import MagicMock

import pytest

import plaid_link_server as pls


# ---------------------------------------------------------------------------
# load_items / save_items
# ---------------------------------------------------------------------------


def test_load_items_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pls, "ITEMS_FILE", tmp_path / "items.json")
    assert pls.load_items() == {}


def test_load_items_returns_empty_dict_for_empty_file(tmp_path, monkeypatch):
    f = tmp_path / "items.json"
    f.write_text("")
    monkeypatch.setattr(pls, "ITEMS_FILE", f)
    assert pls.load_items() == {}


def test_load_items_returns_data_from_file(tmp_path, monkeypatch):
    f = tmp_path / "items.json"
    data = {"item-1": {"access_token": "tok", "institution_name": "Acme Bank"}}
    f.write_text(json.dumps(data))
    monkeypatch.setattr(pls, "ITEMS_FILE", f)
    assert pls.load_items() == data


def test_save_items_round_trips_through_load(tmp_path, monkeypatch):
    f = tmp_path / "items.json"
    monkeypatch.setattr(pls, "ITEMS_FILE", f)
    items = {"item-abc": {"institution_name": "Test Bank", "access_token": "x"}}
    pls.save_items(items)
    assert pls.load_items() == items


def test_save_items_creates_file_with_restricted_permissions(tmp_path, monkeypatch):
    f = tmp_path / "items.json"
    monkeypatch.setattr(pls, "ITEMS_FILE", f)
    pls.save_items({"k": "v"})
    mode = f.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_items_narrows_permissions_on_preexisting_wide_file(
    tmp_path, monkeypatch
):
    """A pre-existing world-readable items file must not stay world-readable.

    Issue #167: touch(mode=0o600, exist_ok=True) is a no-op on an existing
    file, so a file created at a wider mode by another tool kept that mode
    while holding Plaid access tokens.
    """
    f = tmp_path / "items.json"
    f.write_text("{}")
    f.chmod(0o644)
    monkeypatch.setattr(pls, "ITEMS_FILE", f)
    pls.save_items({"item-abc": {"access_token": "secret"}})
    assert f.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# get_plaid_user / save_plaid_user
# ---------------------------------------------------------------------------


def test_get_plaid_user_creates_new_user_when_no_files_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(pls, "PLAID_USER_FILE", tmp_path / ".plaid_user.json")
    monkeypatch.setattr(pls, "CLIENT_USER_ID_FILE", tmp_path / ".plaid_client_user_id")
    user = pls.get_plaid_user()
    assert "client_user_id" in user
    assert user["plaid_user_id"] is None
    assert user["phone_number"] is None


def test_get_plaid_user_returns_existing_data_from_file(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_user.json"
    data = {
        "client_user_id": "uid-123",
        "plaid_user_id": "plaid-456",
        "phone_number": None,
    }
    f.write_text(json.dumps(data))
    monkeypatch.setattr(pls, "PLAID_USER_FILE", f)
    monkeypatch.setattr(pls, "CLIENT_USER_ID_FILE", tmp_path / ".plaid_client_user_id")
    result = pls.get_plaid_user()
    assert result["client_user_id"] == "uid-123"
    assert result["plaid_user_id"] == "plaid-456"


def test_get_plaid_user_migrates_legacy_client_user_id(tmp_path, monkeypatch):
    legacy = tmp_path / ".plaid_client_user_id"
    legacy.write_text("legacy-uid-789\n")
    monkeypatch.setattr(pls, "PLAID_USER_FILE", tmp_path / ".plaid_user.json")
    monkeypatch.setattr(pls, "CLIENT_USER_ID_FILE", legacy)
    user = pls.get_plaid_user()
    assert user["client_user_id"] == "legacy-uid-789"


def test_get_plaid_user_ignores_corrupt_json_and_creates_new(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_user.json"
    f.write_text("not-valid-json{")
    monkeypatch.setattr(pls, "PLAID_USER_FILE", f)
    monkeypatch.setattr(pls, "CLIENT_USER_ID_FILE", tmp_path / ".plaid_client_user_id")
    user = pls.get_plaid_user()
    assert "client_user_id" in user


def test_save_plaid_user_persists_data(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_user.json"
    monkeypatch.setattr(pls, "PLAID_USER_FILE", f)
    monkeypatch.setattr(pls, "CLIENT_USER_ID_FILE", tmp_path / ".plaid_client_user_id")
    data = {
        "client_user_id": "u1",
        "plaid_user_id": None,
        "phone_number": "+15550001234",
    }
    pls.save_plaid_user(data)
    stored = json.loads(f.read_text())
    assert stored["phone_number"] == "+15550001234"


def test_get_client_user_id_returns_string(tmp_path, monkeypatch):
    monkeypatch.setattr(pls, "PLAID_USER_FILE", tmp_path / ".plaid_user.json")
    monkeypatch.setattr(pls, "CLIENT_USER_ID_FILE", tmp_path / ".plaid_client_user_id")
    uid = pls.get_client_user_id()
    assert isinstance(uid, str)
    assert len(uid) > 0


# ---------------------------------------------------------------------------
# create_update_link_token: re-auth vs add-products
# ---------------------------------------------------------------------------


def _isolate_user_files(monkeypatch, tmp_path):
    """Redirect the persistent user-id files into tmp_path.

    The endpoint reaches get_plaid_user() via build_link_request_base(), which
    reads (and can create) these files. Without this, tests would touch the
    real .plaid_user.json in the project root and leave a stray file in CI.
    """
    monkeypatch.setattr(pls, "PLAID_USER_FILE", tmp_path / ".plaid_user.json")
    monkeypatch.setattr(pls, "CLIENT_USER_ID_FILE", tmp_path / ".plaid_client_user_id")


def _mock_update_link_env(monkeypatch, tmp_path, item):
    """Stub out network/user deps and capture the LinkTokenCreateRequest.

    Returns a dict whose "req" key is populated with the request object
    passed to link_token_create when the endpoint runs.
    """
    _isolate_user_files(monkeypatch, tmp_path)
    monkeypatch.setattr(pls, "load_items", lambda: {"the-item": item})
    # ensure_plaid_user() would hit Plaid's /user/create; skip it.
    monkeypatch.setattr(pls, "ensure_plaid_user", lambda: None)

    captured = {}

    def _link_token_create(req):
        captured["req"] = req
        resp = MagicMock()
        resp.to_dict.return_value = {"link_token": "link-test-123"}
        return resp

    client = MagicMock()
    client.link_token_create.side_effect = _link_token_create
    monkeypatch.setattr(pls, "_get_client", lambda: client)
    return captured


def test_create_update_link_token_reauth_omits_consented_products(
    monkeypatch, tmp_path
):
    """A pure re-auth (empty additional_products) must not request any
    additional consented products. Forcing an unsupported product there is
    what broke Bank of America re-authentication (INVALID_FIELD)."""
    captured = _mock_update_link_env(
        monkeypatch,
        tmp_path,
        {"access_token": "tok", "institution_name": "Bank of America"},
    )

    resp = pls.app.test_client().post(
        "/api/create_update_link_token",
        json={"item_id": "the-item", "additional_products": []},
    )

    assert resp.status_code == 200
    req_dict = captured["req"].to_dict()
    assert "additional_consented_products" not in req_dict
    assert req_dict.get("access_token") == "tok"
    assert "update" in req_dict


def test_create_update_link_token_add_products_sets_consented(monkeypatch, tmp_path):
    """Adding products (non-empty list) still populates
    additional_consented_products with the requested product."""
    captured = _mock_update_link_env(
        monkeypatch,
        tmp_path,
        {"access_token": "tok2", "institution_name": "SoFi"},
    )

    resp = pls.app.test_client().post(
        "/api/create_update_link_token",
        json={"item_id": "the-item", "additional_products": ["investments"]},
    )

    assert resp.status_code == 200
    req_dict = captured["req"].to_dict()
    # to_dict() serializes the Products enum to its plain string value.
    assert req_dict["additional_consented_products"] == ["investments"]


def test_create_update_link_token_defaults_to_reauth(monkeypatch, tmp_path):
    """When additional_products is omitted entirely, the endpoint must default
    to a pure re-auth (no consented products) rather than forcing a product
    onto the item — otherwise the default would resurrect the BoA bug."""
    captured = _mock_update_link_env(
        monkeypatch,
        tmp_path,
        {"access_token": "tok", "institution_name": "Bank of America"},
    )

    resp = pls.app.test_client().post(
        "/api/create_update_link_token",
        json={"item_id": "the-item"},
    )

    assert resp.status_code == 200
    assert "additional_consented_products" not in captured["req"].to_dict()


def test_create_update_link_token_propagates_plaid_error(monkeypatch, tmp_path):
    """A Plaid rejection (e.g. INVALID_FIELD for an unsupported product) is
    surfaced to the client as a 400 with the parsed error body."""
    _isolate_user_files(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pls,
        "load_items",
        lambda: {"the-item": {"access_token": "tok", "institution_name": "BoA"}},
    )
    monkeypatch.setattr(pls, "ensure_plaid_user", lambda: None)

    err = pls.plaid.ApiException(status=400)
    err.body = json.dumps(
        {
            "error_code": "INVALID_FIELD",
            "error_message": "Update mode: investments not supported by BoA",
        }
    )
    client = MagicMock()
    client.link_token_create.side_effect = err
    monkeypatch.setattr(pls, "_get_client", lambda: client)

    resp = pls.app.test_client().post(
        "/api/create_update_link_token",
        json={"item_id": "the-item", "additional_products": ["investments"]},
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"]["error_code"] == "INVALID_FIELD"


# ---------------------------------------------------------------------------
# PID-file lifecycle: write_pid_file / cleanup_pid_file / kill_stale_process
# ---------------------------------------------------------------------------


def test_write_pid_file_writes_current_pid(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_link_server.pid"
    monkeypatch.setattr(pls, "PID_FILE", f)
    pls.write_pid_file()
    assert f.read_text().strip() == str(os.getpid())


def test_cleanup_pid_file_removes_existing_file(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_link_server.pid"
    f.write_text("12345")
    monkeypatch.setattr(pls, "PID_FILE", f)
    pls.cleanup_pid_file()
    assert not f.exists()


def test_cleanup_pid_file_noop_when_missing(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_link_server.pid"
    monkeypatch.setattr(pls, "PID_FILE", f)
    pls.cleanup_pid_file()  # Should not raise
    assert not f.exists()


def test_kill_stale_process_noop_when_no_pid_file(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_link_server.pid"
    monkeypatch.setattr(pls, "PID_FILE", f)
    killed = {}
    monkeypatch.setattr(pls.os, "kill", lambda pid, sig: killed.setdefault("pid", pid))
    pls.kill_stale_process()
    assert "pid" not in killed


def test_kill_stale_process_sends_sigterm_to_stale_pid(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_link_server.pid"
    f.write_text("54321")
    monkeypatch.setattr(pls, "PID_FILE", f)
    monkeypatch.setattr(pls.time, "sleep", lambda _: None)
    killed = {}

    def fake_kill(pid, sig):
        killed["pid"] = pid
        killed["sig"] = sig

    monkeypatch.setattr(pls.os, "kill", fake_kill)
    pls.kill_stale_process()
    assert killed == {"pid": 54321, "sig": signal.SIGTERM}
    assert not f.exists()  # PID file cleaned up after killing stale process


def test_kill_stale_process_handles_dead_process(tmp_path, monkeypatch):
    """If the PID no longer exists, os.kill raises ProcessLookupError; the
    stale PID file should still be cleaned up."""
    f = tmp_path / ".plaid_link_server.pid"
    f.write_text("99999")
    monkeypatch.setattr(pls, "PID_FILE", f)

    def fake_kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(pls.os, "kill", fake_kill)
    pls.kill_stale_process()
    assert not f.exists()


def test_kill_stale_process_handles_permission_error(tmp_path, monkeypatch):
    """A recycled PID now owned by a process we can't signal should not
    crash startup — just warn and clean up the stale PID file."""
    f = tmp_path / ".plaid_link_server.pid"
    f.write_text("11111")
    monkeypatch.setattr(pls, "PID_FILE", f)

    def fake_kill(pid, sig):
        raise PermissionError()

    monkeypatch.setattr(pls.os, "kill", fake_kill)
    pls.kill_stale_process()
    assert not f.exists()


def test_kill_stale_process_handles_invalid_pid_file(tmp_path, monkeypatch):
    """A corrupt PID file (non-integer contents) should be cleaned up rather
    than crashing the next launch."""
    f = tmp_path / ".plaid_link_server.pid"
    f.write_text("not-a-pid")
    monkeypatch.setattr(pls, "PID_FILE", f)
    pls.kill_stale_process()
    assert not f.exists()


def test_handle_shutdown_signal_cleans_up_and_exits(tmp_path, monkeypatch):
    f = tmp_path / ".plaid_link_server.pid"
    f.write_text(str(os.getpid()))
    monkeypatch.setattr(pls, "PID_FILE", f)
    with pytest.raises(SystemExit) as exc_info:
        pls._handle_shutdown_signal(signal.SIGTERM, None)
    assert exc_info.value.code == 0
    assert not f.exists()


# ---------------------------------------------------------------------------
# Werkzeug debug-reloader guard: is_reloader_child / install_process_handlers
# ---------------------------------------------------------------------------


def _track_handler_installation(monkeypatch, tmp_path):
    """Stub the side effects of install_process_handlers() and record them.

    Returns a dict populated with "killed"/"wrote" flags and the signals that
    were installed, so tests can assert on parent-vs-child behavior without
    touching the real PID file or the process's signal dispositions.
    """
    monkeypatch.setattr(pls, "PID_FILE", tmp_path / ".plaid_link_server.pid")
    calls = {"killed": False, "wrote": False, "signals": []}
    monkeypatch.setattr(
        pls, "kill_stale_process", lambda: calls.__setitem__("killed", True)
    )
    monkeypatch.setattr(pls, "write_pid_file", lambda: calls.__setitem__("wrote", True))
    monkeypatch.setattr(
        pls.signal, "signal", lambda sig, handler: calls["signals"].append(sig)
    )
    return calls


def test_is_reloader_child_true_only_for_werkzeug_run_main(monkeypatch):
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    assert pls.is_reloader_child() is False
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    assert pls.is_reloader_child() is True


def test_install_process_handlers_claims_pid_and_signals_in_parent(
    monkeypatch, tmp_path
):
    """The parent process owns the PID file and both shutdown signals."""
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    calls = _track_handler_installation(monkeypatch, tmp_path)

    pls.install_process_handlers()

    assert calls["killed"] is True
    assert calls["wrote"] is True
    assert set(calls["signals"]) == {signal.SIGINT, signal.SIGTERM}


def test_install_process_handlers_noop_in_reloader_child(monkeypatch, tmp_path):
    """The reloader child must not overwrite the parent's PID file, and must
    leave SIGINT to Werkzeug so its graceful shutdown is not bypassed."""
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    calls = _track_handler_installation(monkeypatch, tmp_path)

    pls.install_process_handlers()

    assert calls["killed"] is False
    assert calls["wrote"] is False
    assert calls["signals"] == []


def test_reloader_child_does_not_clobber_parent_pid_file(monkeypatch, tmp_path):
    """End-to-end on the real helpers: the parent's PID survives the child's
    startup. Without the guard the child's PID would overwrite it, so the next
    launch's kill_stale_process() would signal the wrong process."""
    f = tmp_path / ".plaid_link_server.pid"
    monkeypatch.setattr(pls, "PID_FILE", f)
    monkeypatch.setattr(pls.signal, "signal", lambda sig, handler: None)
    monkeypatch.setattr(pls, "kill_stale_process", lambda: None)

    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    pls.install_process_handlers()
    parent_pid = f.read_text().strip()

    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    pls.install_process_handlers()

    assert f.read_text().strip() == parent_pid


# ---------------------------------------------------------------------------
# update_item_products: consent expiration refresh
# ---------------------------------------------------------------------------


def _item_get_response(consent_expiration_time):
    """Build a mock item_get response with the given consent expiry."""
    item = {"products": ["transactions"]}
    if consent_expiration_time is not None:
        item["consent_expiration_time"] = consent_expiration_time
    response = MagicMock()
    response.__getitem__ = lambda self, key: {"item": item}[key]
    return response


def test_update_item_products_clears_stale_consent_expiration(tmp_path, monkeypatch):
    """A successful item_get reporting no expiration is authoritative and
    must clear a stale cached date, not leave it in place."""
    f = tmp_path / "items.json"
    monkeypatch.setattr(pls, "ITEMS_FILE", f)
    pls.save_items(
        {
            "item-1": {
                "access_token": "tok",
                "institution_name": "Chase",
                "consent_expiration": "2026-09-01T00:00:00+00:00",
                "products": ["transactions"],
            }
        }
    )

    client = MagicMock()
    client.item_get.return_value = _item_get_response(None)
    monkeypatch.setattr(pls, "_get_client", lambda: client)

    with pls.app.test_client() as c:
        resp = c.post("/api/update_item_products", json={"item_id": "item-1"})
    assert resp.status_code == 200
    assert pls.load_items()["item-1"]["consent_expiration"] is None


def test_update_item_products_stores_new_consent_expiration(tmp_path, monkeypatch):
    """A live expiration date overwrites whatever was cached."""
    from datetime import datetime, timezone

    f = tmp_path / "items.json"
    monkeypatch.setattr(pls, "ITEMS_FILE", f)
    pls.save_items(
        {
            "item-1": {
                "access_token": "tok",
                "institution_name": "Chase",
                "consent_expiration": None,
                "products": ["transactions"],
            }
        }
    )

    expiry = datetime(2027, 2, 1, tzinfo=timezone.utc)
    client = MagicMock()
    client.item_get.return_value = _item_get_response(expiry)
    monkeypatch.setattr(pls, "_get_client", lambda: client)

    with pls.app.test_client() as c:
        resp = c.post("/api/update_item_products", json={"item_id": "item-1"})
    assert resp.status_code == 200
    assert pls.load_items()["item-1"]["consent_expiration"] == expiry.isoformat()
