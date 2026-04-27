"""Client span id + batch ingest resolution (SQLite)."""
import json
import os
import uuid

# SQLite-only: conftest loads backend/.env which may set MINIOBSERVE_BACKEND=supabase.
os.environ["MINIOBSERVE_BACKEND"] = "sqlite"

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(autouse=True)
def _local_mode_no_api_keys(monkeypatch):
    """Match CI/local runs where .env may set MINIOBSERVE_API_KEYS."""
    monkeypatch.setenv("MINIOBSERVE_API_KEYS", "")
    monkeypatch.delenv("MINIOBSERVE_API_KEY_PEPPER", raising=False)


@pytest.fixture
def client():
    return TestClient(app)


def test_post_log_resolves_parent_client_span_id(client):
    tid = uuid.uuid4().hex[:12]
    r1 = client.post(
        "/api/log",
        json={
            "run_id": tid,
            "model": "parent-m",
            "provider": "openai",
            "prompt": "p",
            "response": "r",
            "client_span_id": "root",
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )
    assert r1.status_code == 200, r1.text
    root_id = r1.json()["id"]

    r2 = client.post(
        "/api/log",
        json={
            "run_id": tid,
            "model": "child-m",
            "provider": "openai",
            "prompt": "p2",
            "response": "r2",
            "client_span_id": "child",
            "parent_client_span_id": "root",
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )
    assert r2.status_code == 200, r2.text
    cid = r2.json()["id"]

    detail = client.get("/api/run-logs", params={"run_key": tid})
    assert detail.status_code == 200
    steps = {s["id"]: s for s in detail.json()["steps"]}
    assert steps[cid].get("parent_span_id") == root_id


def test_post_logs_batch_id_map_and_parent(client):
    tid = uuid.uuid4().hex[:12]
    r = client.post(
        "/api/logs",
        json={
            "logs": [
                {
                    "run_id": tid,
                    "model": "a",
                    "provider": "openai",
                    "prompt": "p0",
                    "response": "r0",
                    "client_span_id": "s0",
                    "input_tokens": 2,
                    "output_tokens": 1,
                },
                {
                    "run_id": tid,
                    "model": "b",
                    "provider": "openai",
                    "prompt": "p1",
                    "response": "r1",
                    "client_span_id": "s1",
                    "parent_client_span_id": "s0",
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert len(data["results"]) == 2
    id0 = data["results"][0]["id"]
    id1 = data["results"][1]["id"]
    assert data["id_map"]["s0"] == id0
    assert data["id_map"]["s1"] == id1

    detail = client.get("/api/run-logs", params={"run_key": tid})
    steps = {s["id"]: s for s in detail.json()["steps"]}
    assert steps[id1].get("parent_span_id") == id0


def test_post_log_promotes_request_messages_and_canonical_prompt_for_tracer_shape(client):
    """Tracer sends chat under request.messages and step JSON in prompt; ingest should store both usefully."""
    tid = uuid.uuid4().hex[:12]
    step_json = json.dumps(
        {"step": "turn1", "fingerprint": {"n": 1}, "had_tool_call": False}
    )
    r = client.post(
        "/api/log",
        json={
            "run_id": tid,
            "model": "gpt-4",
            "provider": "openai",
            "prompt": step_json,
            "response": "assistant says hi",
            "request": {"messages": [{"role": "user", "content": "hello world"}]},
            "metadata": {"span_type": "llm", "prompt_fingerprint": {"n": 1}},
            "input_tokens": 3,
            "output_tokens": 2,
        },
    )
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    detail = client.get("/api/run-logs", params={"run_key": tid})
    assert detail.status_code == 200
    steps = {s["id"]: s for s in detail.json()["steps"]}
    row = steps[lid]
    msgs = row.get("messages")
    assert msgs == [{"role": "user", "content": "hello world"}]
    assert row.get("prompt") == "hello world"


def test_post_log_started_at_ended_at_stored_in_metadata(client):
    tid = uuid.uuid4().hex[:12]
    r = client.post(
        "/api/log",
        json={
            "run_id": tid,
            "model": "m",
            "provider": "openai",
            "prompt": "p",
            "response": "r",
            "input_tokens": 1,
            "output_tokens": 1,
            "metadata": {"k": "v"},
            "started_at": "2026-01-15T10:00:00Z",
            "ended_at": "2026-01-15T10:00:01Z",
        },
    )
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    detail = client.get("/api/run-logs", params={"run_key": tid})
    row = next(s for s in detail.json()["steps"] if s["id"] == lid)
    md = row.get("metadata") or {}
    assert md.get("k") == "v"
    assert md.get("started_at") == "2026-01-15T10:00:00Z"
    assert md.get("ended_at") == "2026-01-15T10:00:01Z"


def test_patch_started_at_merges_existing_metadata(client):
    tid = uuid.uuid4().hex[:12]
    r0 = client.post(
        "/api/log",
        json={
            "run_id": tid,
            "model": "m",
            "provider": "openai",
            "prompt": "p",
            "response": "r",
            "input_tokens": 1,
            "output_tokens": 1,
            "metadata": {"keep": "yes"},
        },
    )
    lid = r0.json()["id"]
    r1 = client.patch(
        "/api/log",
        json={"id": lid, "started_at": "2026-02-01T12:00:00Z"},
    )
    assert r1.status_code == 200, r1.text
    detail = client.get("/api/run-logs", params={"run_key": tid})
    row = next(s for s in detail.json()["steps"] if s["id"] == lid)
    md = row.get("metadata") or {}
    assert md.get("keep") == "yes"
    assert md.get("started_at") == "2026-02-01T12:00:00Z"


def test_post_log_promotes_metadata_span_type_to_column(client):
    tid = uuid.uuid4().hex[:12]
    r = client.post(
        "/api/log",
        json={
            "run_id": tid,
            "model": "gpt",
            "provider": "openai",
            "prompt": '{"step": "my-step", "fingerprint": {}, "had_tool_call": false}',
            "response": "assistant text",
            "input_tokens": 3,
            "output_tokens": 1,
            "metadata": {"span_type": "llm", "agent_span_name": "my-step"},
        },
    )
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    detail = client.get("/api/run-logs", params={"run_key": tid})
    row = next(s for s in detail.json()["steps"] if s["id"] == lid)
    assert row.get("span_type") == "llm"


def test_patch_metadata_span_type_promotes_column(client):
    tid = uuid.uuid4().hex[:12]
    r0 = client.post(
        "/api/log",
        json={
            "run_id": tid,
            "model": "m",
            "provider": "openai",
            "prompt": "p",
            "response": "r",
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )
    lid = r0.json()["id"]
    r1 = client.patch(
        "/api/log",
        json={"id": lid, "metadata": {"span_type": "tool", "tool_name": "add"}},
    )
    assert r1.status_code == 200, r1.text
    detail = client.get("/api/run-logs", params={"run_key": tid})
    row = next(s for s in detail.json()["steps"] if s["id"] == lid)
    assert row.get("span_type") == "tool"


def test_run_step_trace_display_label_prefers_agent_span_name():
    from utils.run_utils import run_step_trace_display_label

    row = {
        "span_name": "llm_call",
        "model": "gpt-4",
        "provider": "openai",
        "prompt": '{"step": "from-json", "fingerprint": {}, "had_tool_call": false}',
        "response": "assistant visible",
        "metadata": {"span_type": "llm", "agent_span_name": "human-step"},
    }
    assert run_step_trace_display_label(row) == "human-step"
