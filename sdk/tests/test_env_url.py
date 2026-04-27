"""resolve_miniobserve_http_base_url precedence."""

import pytest

from miniobserve.env_url import resolve_miniobserve_http_base_url


@pytest.fixture(autouse=True)
def clear_url_env(monkeypatch):
    for k in (
        "MINIOBSERVE_URL",
        "MINIOBSERVE_DASHBOARD_ORIGIN",
    ):
        monkeypatch.delenv(k, raising=False)


def test_default_localhost_when_unset(monkeypatch):
    assert resolve_miniobserve_http_base_url() == "http://localhost:7823"


def test_dashboard_origin_when_url_absent(monkeypatch):
    monkeypatch.setenv("MINIOBSERVE_DASHBOARD_ORIGIN", "https://tunnel.example")
    assert resolve_miniobserve_http_base_url() == "https://tunnel.example"


def test_minobserve_url_wins_over_dashboard(monkeypatch):
    monkeypatch.setenv("MINIOBSERVE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("MINIOBSERVE_DASHBOARD_ORIGIN", "https://ignored")
    assert resolve_miniobserve_http_base_url() == "http://127.0.0.1:9999"


def test_explicit_empty_disables(monkeypatch):
    monkeypatch.setenv("MINIOBSERVE_URL", "")
    monkeypatch.setenv("MINIOBSERVE_DASHBOARD_ORIGIN", "https://tunnel.example")
    assert resolve_miniobserve_http_base_url() is None
