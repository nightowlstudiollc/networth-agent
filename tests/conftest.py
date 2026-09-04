"""Shared pytest fixtures for balance-history tests."""

import pytest
import sqlite_utils


@pytest.fixture
def db():
    """In-memory SQLite DB with the full balance-history schema applied.

    Foreign keys are enabled here so tests catch FK violations that
    production code would also see.
    """
    from history import init_schema

    conn = sqlite_utils.Database(memory=True)
    conn.conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


@pytest.fixture
def sample_accounts_yaml(tmp_path):
    """Write a minimal accounts.yaml to tmp_path and return the path."""
    path = tmp_path / "accounts.yaml"
    path.write_text(
        """
spreadsheet_id: "test-sheet-id"
accounts:
  - institution: "TestBank"
    name: "Checking"
    mask: "1234"
    id: "test-checking"
    label: "Test Checking"
    type: asset
  - institution: "TestBroker"
    name: "Brokerage"
    mask: "5678"
    id: "test-brokerage"
    label: "Test Brokerage"
    type: asset
manual_accounts:
  - id: "manual-asset"
    label: "Manual Asset"
    type: asset
"""
    )
    return path
