"""Implicit OSS local default API key (sk-local-default-key)."""
import os

# Force SQLite before main/state are imported so state.db is not supabase.
os.environ["MINIOBSERVE_BACKEND"] = "sqlite"

import pytest
from fastapi.testclient import TestClient

import auth.auth as auth
from main import app


@pytest.fixture(autouse=True)
def _reset_auth_cache(monkeypatch):
    """Tests toggle env; auth has no global key cache after refactor."""
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def implicit_local_env(monkeypatch):
    monkeypatch.setenv("MINIOBSERVE_BACKEND", "sqlite")
    monkeypatch.setenv("MINIOBSERVE_API_KEYS", "")
    monkeypatch.delenv("MINIOBSERVE_API_KEY_PEPPER", raising=False)
    monkeypatch.delenv("MINIOBSERVE_ENV", raising=False)
    monkeypatch.delenv("MINIOBSERVE_DISABLE_LOCAL_DEFAULT_KEY", raising=False)


def test_implicit_default_no_header_ok(client, implicit_local_env):
    assert auth.implicit_local_default_key_enabled()
    r = client.post(
        "/api/log",
        json={"model": "m", "provider": "openai", "prompt": "p", "response": "r", "input_tokens": 1, "output_tokens": 1},
    )
    assert r.status_code == 200, r.text


def test_implicit_default_correct_bearer_ok(client, implicit_local_env):
    r = client.post(
        "/api/log",
        headers={"Authorization": f"Bearer {auth.LOCAL_DEFAULT_API_KEY}"},
        json={"model": "m", "provider": "openai", "prompt": "p", "response": "r", "input_tokens": 1, "output_tokens": 1},
    )
    assert r.status_code == 200, r.text


def test_implicit_default_wrong_bearer_401(client, implicit_local_env):
    r = client.post(
        "/api/log",
        headers={"Authorization": "Bearer not-a-real-local-key"},
        json={"model": "m", "provider": "openai", "prompt": "p", "response": "r", "input_tokens": 1, "output_tokens": 1},
    )
    assert r.status_code == 401, r.text


def test_disable_implicit_wrong_bearer_still_default(client, monkeypatch):
    monkeypatch.setenv("MINIOBSERVE_BACKEND", "sqlite")
    monkeypatch.setenv("MINIOBSERVE_API_KEYS", "")
    monkeypatch.delenv("MINIOBSERVE_API_KEY_PEPPER", raising=False)
    monkeypatch.setenv("MINIOBSERVE_DISABLE_LOCAL_DEFAULT_KEY", "1")
    assert not auth.implicit_local_default_key_enabled()
    r = client.post(
        "/api/log",
        headers={"Authorization": "Bearer not-a-real-local-key"},
        json={"model": "m", "provider": "openai", "prompt": "p", "response": "r", "input_tokens": 1, "output_tokens": 1},
    )
    assert r.status_code == 200, r.text


def test_explicit_env_same_as_singleton_behaves_like_implicit(client, monkeypatch):
    monkeypatch.setenv("MINIOBSERVE_BACKEND", "sqlite")
    monkeypatch.setenv("MINIOBSERVE_API_KEYS", f"{auth.LOCAL_DEFAULT_API_KEY}:default")
    monkeypatch.delenv("MINIOBSERVE_API_KEY_PEPPER", raising=False)
    assert not auth.implicit_local_default_key_enabled()
    assert auth.singleton_local_default_map(auth.effective_key_map())
    r = client.post(
        "/api/log",
        json={"model": "m", "provider": "openai", "prompt": "p", "response": "r", "input_tokens": 1, "output_tokens": 1},
    )
    assert r.status_code == 200, r.text
    r2 = client.post(
        "/api/log",
        headers={"Authorization": "Bearer wrong"},
        json={"model": "m2", "provider": "openai", "prompt": "p", "response": "r", "input_tokens": 1, "output_tokens": 1},
    )
    assert r2.status_code == 401, r2.text
