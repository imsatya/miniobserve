"""Tests for run-level aggregation helpers."""

from utils.run_utils import aggregate_runs


def test_aggregate_runs_prefers_session_latency_over_sum():
    rows = [
        {
            "id": 1,
            "run_id": "abc123",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "span_type": "agent",
            "span_name": "router",
            "parent_span_id": None,
            "latency_ms": 7790,
            "metadata": {"agent_span_name": "agent/hierarchical_supervisor", "span_type": "agent"},
            "cost_usd": 0.0,
            "error": None,
        },
        {
            "id": 2,
            "run_id": "abc123",
            "timestamp": "2026-01-01T00:00:01+00:00",
            "span_type": "llm",
            "span_name": "llm_call",
            "parent_span_id": 1,
            "latency_ms": 1200,
            "metadata": {"span_type": "llm"},
            "cost_usd": 0.0,
            "error": None,
        },
        {
            "id": 3,
            "run_id": "abc123",
            "timestamp": "2026-01-01T00:00:02+00:00",
            "span_type": "tool",
            "span_name": "tool_call",
            "parent_span_id": 1,
            "latency_ms": 900,
            "metadata": {"span_type": "tool", "tool_name": "search"},
            "cost_usd": 0.0,
            "error": None,
        },
    ]
    runs = aggregate_runs(rows)
    assert len(runs) == 1
    assert runs[0]["run_key"] == "abc123"
    assert runs[0]["total_latency_ms"] == 7790.0
