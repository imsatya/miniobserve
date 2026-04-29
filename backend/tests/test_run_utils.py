"""Tests for run-level aggregation helpers."""

from utils.run_utils import aggregate_runs, decision_observability_for_run


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


def test_decision_observability_skipped_and_missing_expected_paths():
    steps = [
        {
            "id": 10,
            "parent_span_id": None,
            "span_name": "router",
            "latency_ms": 10,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
            "metadata": {
                "decision": {
                    "type": "workflow_routing",
                    "available": ["route:billing", "route:security"],
                    "chosen": "route:billing",
                    "expected_downstream": ["tool:refund_policy", "tool:security_escalation"],
                }
            },
        },
        {
            "id": 11,
            "parent_span_id": 10,
            "span_name": "tool_call",
            "latency_ms": 25,
            "cost_usd": 0.1,
            "input_tokens": 3,
            "output_tokens": 2,
            "error": None,
            "metadata": {"tool_name": "refund_policy"},
        },
    ]
    out = decision_observability_for_run(steps)
    assert len(out["decisions"]) == 1
    d = out["decisions"][0]
    assert d["chosen"] == ["route:billing"]
    assert d["skipped"] == ["route:security"]
    assert d["missing_expected"] == ["tool:security_escalation"]
    assert d["impact"]["computed"]["descendant_span_count"] == 1
    assert out["integrity_alerts"][0]["kind"] == "missing_expected_path"


def test_decision_observability_prefers_canonical_workflow_node_matching():
    steps = [
        {
            "id": 1,
            "parent_span_id": None,
            "metadata": {
                "decision": {
                    "type": "workflow_routing",
                    "chosen": ["route:budget"],
                    "expected_downstream": ["route:final"],
                }
            },
        },
        {
            "id": 2,
            "parent_span_id": 1,
            "span_name": "llm_call",
            "metadata": {"workflow_node": "route:final"},
            "latency_ms": 10,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
        },
    ]
    out = decision_observability_for_run(steps)
    d = out["decisions"][0]
    assert d["missing_expected"] == []
    assert d["matching_mode"] == "canonical"


def test_decision_observability_marks_fallback_matching_mode_for_legacy_labels():
    steps = [
        {
            "id": 11,
            "parent_span_id": None,
            "metadata": {
                "decision": {
                    "type": "workflow_routing",
                    "chosen": ["route:budget"],
                    "expected_downstream": ["workflow:final_router"],
                }
            },
        },
        {
            "id": 12,
            "parent_span_id": 11,
            "span_name": "final_router",
            "metadata": {},
            "latency_ms": 5,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
        },
    ]
    out = decision_observability_for_run(steps)
    d = out["decisions"][0]
    assert d["missing_expected"] == []
    assert d["matching_mode"] == "fallback"


def test_decision_observability_marks_mixed_matching_mode():
    steps = [
        {
            "id": 21,
            "parent_span_id": None,
            "metadata": {
                "decision": {
                    "type": "workflow_routing",
                    "expected_downstream": ["route:final", "workflow:legacy_done"],
                }
            },
        },
        {
            "id": 22,
            "parent_span_id": 21,
            "metadata": {"workflow_node": "route:final"},
            "span_name": "llm_call",
            "latency_ms": 1,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
        },
        {
            "id": 23,
            "parent_span_id": 21,
            "metadata": {},
            "span_name": "legacy_done",
            "latency_ms": 1,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
        },
    ]
    out = decision_observability_for_run(steps)
    d = out["decisions"][0]
    assert d["missing_expected"] == []
    assert d["matching_mode"] == "mixed"
