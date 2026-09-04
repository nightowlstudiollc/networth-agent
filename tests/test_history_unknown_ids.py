"""Tests for history.find_unregistered_ids — pre-flight check for snapshot.

Adding a row to the sheet with a new ID in column H, without a matching entry
in accounts.yaml, made `snapshot` die on `sqlite3.IntegrityError: FOREIGN KEY
constraint failed` from deep inside write_snapshot. The traceback named neither
the offending ID nor the fix. This check surfaces it up front instead.
"""

from history import find_unregistered_ids


def _register(db, ids):
    db["accounts"].insert_all(
        [
            {
                "id": aid,
                "label": aid,
                "type": "asset",
                "institution": None,
                "is_manual": 1,
                "first_seen": "2026-01-01",
                "retired_at": None,
            }
            for aid in ids
        ]
    )


def test_returns_empty_when_every_id_is_registered(db):
    _register(db, ["coinbase", "zillow-home"])

    assert find_unregistered_ids(db, {"coinbase": 1.0, "zillow-home": 2.0}) == []


def test_reports_an_id_missing_from_the_accounts_table(db):
    _register(db, ["coinbase"])

    assert find_unregistered_ids(
        db, {"coinbase": 1.0, "unknown-liability": -19.39}
    ) == ["unknown-liability"]


def test_reports_every_missing_id_sorted(db):
    _register(db, ["coinbase"])

    result = find_unregistered_ids(db, {"zzz": 1.0, "aaa": 2.0, "coinbase": 3.0})

    assert result == ["aaa", "zzz"]


def test_empty_balances_is_not_an_error(db):
    _register(db, ["coinbase"])

    assert find_unregistered_ids(db, {}) == []


def test_retired_accounts_still_count_as_registered(db):
    """A retired account keeps its row, so its FK is still satisfiable.

    Flagging it would send the user to re-add an account they deliberately
    removed from accounts.yaml.
    """
    _register(db, ["retired-account"])
    db["accounts"].update("retired-account", {"retired_at": "2026-09-03"})

    assert find_unregistered_ids(db, {"retired-account": 0.0}) == []
