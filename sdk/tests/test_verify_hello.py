"""Integration hello helper (no network when URL is off)."""

from miniobserve.verify import LOCAL_DEFAULT_API_KEY, _auth_headers, send_integration_hello


def test_auth_headers_localhost_uses_default_key_when_env_unset(monkeypatch):
    monkeypatch.delenv("MINIOBSERVE_API_KEY", raising=False)
    h = _auth_headers(base_url="http://localhost:7823")
    assert h["Authorization"] == f"Bearer {LOCAL_DEFAULT_API_KEY}"


def test_send_integration_hello_refuses_stdout_mode(monkeypatch):
    monkeypatch.setenv("MINIOBSERVE_URL", "off")
    ok, msg, tid, lid = send_integration_hello()
    assert ok is False
    assert "stdout" in msg.lower() or "off" in msg.lower()
    assert tid is None
    assert lid is None
