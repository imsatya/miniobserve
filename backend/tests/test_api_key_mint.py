"""DB-backed API key hash + mint round-trip (isolated SQLite file)."""
import sys

import pytest


@pytest.fixture
def fresh_sqlite_db(tmp_path, monkeypatch):
    for m in ("db.sqlite", "auth.api_keys"):
        sys.modules.pop(m, None)
    db_path = tmp_path / "mint_test.db"
    monkeypatch.setenv("MINIOBSERVE_DB", str(db_path))
    monkeypatch.setenv("MINIOBSERVE_BACKEND", "sqlite")
    monkeypatch.setenv("MINIOBSERVE_API_KEY_PEPPER", "unit-test-pepper-do-not-reuse")
    import db.sqlite as db_sqlite

    db_sqlite.init()
    yield db_path


def test_insert_resolve_roundtrip(fresh_sqlite_db):
    import auth.api_keys as api_key_credentials

    raw = "sk_mo_unit_test_key_example"
    api_key_credentials.insert_credential(raw, "mytenant", label="lab", source="admin")
    assert api_key_credentials.resolve_app_from_presented_key(raw) == "mytenant"
    assert api_key_credentials.resolve_app_from_presented_key("sk_mo_wrong") is None


def test_mint_raw_key_format(fresh_sqlite_db):
    import auth.api_keys as api_key_credentials

    k = api_key_credentials.mint_raw_api_key()
    assert k.startswith("sk_mo_")
    assert len(k) > 20
