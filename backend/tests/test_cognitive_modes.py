"""Unit tests for cognitive phase classification."""
import pytest

from cognitive.modes import (
    STUCK_REPEAT_THRESHOLD,
    call_kind_for_trace_strip,
    compute_cognitive_for_run,
    is_session_envelope_row,
    tool_arg_fingerprint,
)


def test_one_shot_single_llm():
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 50,
            "parent_span_id": None,
            "metadata": {},
        }
    ]
    phases, stuck, waiting, mf, fp, alerts, ct = compute_cognitive_for_run(steps)
    assert phases[1] == "thinking"
    assert stuck[1] is False
    assert waiting[1] is False
    assert ct and ct[0].get("kind") == "llm"


def test_runaway_stuck_tool_repeat():
    md = {"tool_name": "fetch", "tool_args": '{"u":1}'}
    steps = []
    for i in range(STUCK_REPEAT_THRESHOLD):
        steps.append(
            {
                "id": i + 1,
                "timestamp": f"2025-01-01T00:00:0{i}Z",
                "span_type": "tool",
                "provider": "tool",
                "model": "fetch",
                "latency_ms": 10,
                "parent_span_id": None,
                "metadata": md,
                "cost_usd": 0.0,
            }
        )
    phases, stuck, waiting, mf, fp, alerts, _ct = compute_cognitive_for_run(steps)
    assert all(phases[i + 1] == "executing" for i in range(STUCK_REPEAT_THRESHOLD))
    assert all(stuck[i + 1] for i in range(STUCK_REPEAT_THRESHOLD))
    assert alerts and alerts[0]["count"] == STUCK_REPEAT_THRESHOLD


def test_tool_arg_fingerprint_stable():
    a = tool_arg_fingerprint({"metadata": {"tool_name": "t", "tool_args": '{"x":1}'}, "model": "t"})
    b = tool_arg_fingerprint({"metadata": {"tool_name": "t", "tool_args": '{"x":1}'}, "model": "t"})
    assert a == b


def test_call_kind_agent_span():
    assert call_kind_for_trace_strip({"span_type": "agent", "provider": "openai", "model": "x"}) == "agent"


def test_infer_tool_from_metadata_without_span_type():
    from cognitive.modes import infer_is_tool_span

    assert infer_is_tool_span(
        {
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "metadata": {"tool_name": "search", "tool_args": "{}"},
        }
    )


def test_infer_tool_false_for_empty_tool_calls_json_string():
    from cognitive.modes import infer_is_tool_span

    assert not infer_is_tool_span(
        {
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "metadata": {"tool_calls": "[]"},
        }
    )


def test_bespoke_span_type_llm_call_name_is_planning():
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "RunnableSequence",
            "span_name": "llm_call",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 12,
            "metadata": {},
            "parent_span_id": None,
        }
    ]
    phases, _, _, mf, _, _, ct = compute_cognitive_for_run(steps)
    assert phases[1] == "thinking"
    assert "thinking" in mf
    assert ct[0]["kind"] == "llm"


def test_tracer_llm_prompt_json_without_span_type_is_planning():
    """Tracer flush: prompt has step+fingerprint JSON; span_type may be missing on ingest."""
    import json

    prompt = json.dumps({"step": "think", "fingerprint": {}, "had_tool_call": False})
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "weird_client_value",
            "span_name": "x",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 20,
            "prompt": prompt,
            "metadata": {},
            "parent_span_id": None,
        },
    ]
    phases, _, _, mf, _, _, _ = compute_cognitive_for_run(steps)
    assert phases[1] == "thinking"
    assert "thinking" in mf


def test_token_usage_implies_llm_even_with_custom_span_type():
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "custom_step",
            "provider": "openai",
            "model": "gpt-4o",
            "input_tokens": 10,
            "output_tokens": 2,
            "latency_ms": 50,
            "metadata": {},
            "parent_span_id": None,
        }
    ]
    phases, _, _, _, _, _, ct = compute_cognitive_for_run(steps)
    assert phases[1] == "thinking"
    assert ct[0]["kind"] == "llm"


def test_all_llm_rows_tool_calls_then_text():
    """LLM-only trace: tool_calls blob then assistant text → acting then observing."""
    tc = '[{"id":"c1","type":"function","function":{"name":"read","arguments":"{}"}}]'
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 100,
            "response": tc,
            "metadata": {},
            "parent_span_id": None,
        },
        {
            "id": 2,
            "timestamp": "2025-01-01T00:00:01Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 50,
            "response": "Here is the summary based on the file.",
            "metadata": {},
            "parent_span_id": None,
        },
    ]
    phases, stuck, waiting, mf, _, _, ct = compute_cognitive_for_run(steps)
    assert phases[1] == "calling"
    assert phases[2] == "synthesizing"
    assert stuck[1] is False and stuck[2] is False
    assert mf.get("calling", 0) > 0 and mf.get("synthesizing", 0) > 0
    assert [x["kind"] for x in ct] == ["llm", "llm"]


def test_flat_timeline_llm_tool_llm():
    """LLM → tool → text LLM: planning, acting, observing."""
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt",
            "latency_ms": 10,
            "metadata": {},
            "parent_span_id": None,
        },
        {
            "id": 2,
            "timestamp": "2025-01-01T00:00:01Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "search",
            "latency_ms": 5,
            "metadata": {"tool_name": "search", "tool_args": "{}"},
            "parent_span_id": None,
        },
        {
            "id": 3,
            "timestamp": "2025-01-01T00:00:02Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt",
            "latency_ms": 10,
            "metadata": {},
            "parent_span_id": None,
        },
    ]
    phases, _, _, mf, _, _, ct = compute_cognitive_for_run(steps)
    assert phases[1] == "thinking"
    assert phases[2] == "executing"
    assert phases[3] == "synthesizing"
    assert "synthesizing" in mf
    assert [x["kind"] for x in ct] == ["llm", "tool", "llm"]


def test_session_router_skips_cognitive_and_strips():
    """Root router span wraps the run; it must not get a phase or dilute fractions."""
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "agent",
            "span_name": "router",
            "parent_span_id": None,
            "latency_ms": 9000,
            "metadata": {"agent_span_name": "agent/normal", "span_type": "agent"},
        },
        {
            "id": 2,
            "timestamp": "2025-01-01T00:00:01Z",
            "span_type": "llm",
            "span_name": "llm_call",
            "parent_span_id": 1,
            "latency_ms": 100,
            "metadata": {"span_type": "llm"},
        },
        {
            "id": 3,
            "timestamp": "2025-01-01T00:00:02Z",
            "span_type": "tool",
            "span_name": "tool_call",
            "parent_span_id": 1,
            "latency_ms": 50,
            "metadata": {"tool_name": "search", "span_type": "tool"},
        },
    ]
    assert is_session_envelope_row(steps[0]) is True
    assert is_session_envelope_row(steps[1]) is False

    phases, _, _, mf, fp, _, ct = compute_cognitive_for_run(steps)
    assert phases[1] == ""
    assert phases[2] == "thinking"
    assert phases[3] == "executing"
    assert sum(x["fraction"] for x in fp) == pytest.approx(1.0)
    assert sum(x["fraction"] for x in ct) == pytest.approx(1.0)
    assert [x["kind"] for x in ct] == ["llm", "tool"]
    assert ct[0]["fraction"] == pytest.approx(100 / 150, rel=1e-5)
    assert ct[1]["fraction"] == pytest.approx(50 / 150, rel=1e-5)
    assert mf.get("thinking", 0) == pytest.approx(100 / 150, rel=1e-3)
    assert mf.get("executing", 0) == pytest.approx(50 / 150, rel=1e-3)


def test_span_type_unknown_string_is_planning_not_cognitive_unknown():
    """Ingest defaults use the literal 'unknown' for model/provider; some clients echo that as span_type."""
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "unknown",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 40,
            "metadata": {},
            "parent_span_id": None,
        },
    ]
    phases, _, _, mf, _, _, ct = compute_cognitive_for_run(steps)
    assert phases[1] == "thinking"
    assert "thinking" in mf
    assert ct and ct[0].get("kind") == "llm"


def test_tool_then_llm_tool_calls_is_calling():
    tc = '[{"type":"function","function":{"name":"x","arguments":"{}"}}]'
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "tool",
            "provider": "tool",
            "model": "search",
            "latency_ms": 5,
            "metadata": {"tool_name": "search"},
        },
        {
            "id": 2,
            "timestamp": "2025-01-01T00:00:01Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 30,
            "response": tc,
            "metadata": {},
        },
    ]
    phases, _, _, _, _, _, _ = compute_cognitive_for_run(steps)
    assert phases[1] == "executing"
    assert phases[2] == "calling"




def test_tracer_had_tool_call_flag_is_calling():
    """Tracer prompt JSON with had_tool_call=true → calling phase (no response blob needed)."""
    import json as _json
    prompt = _json.dumps({"step": "dispatch", "fingerprint": {}, "had_tool_call": True})
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "claude-3-5-sonnet",
            "latency_ms": 30,
            "prompt": prompt,
            "metadata": {},
            "parent_span_id": None,
        },
    ]
    phases, _, _, mf, _, _, _ = compute_cognitive_for_run(steps)
    assert phases[1] == "calling"
    assert "calling" in mf


def test_callback_handler_tool_call_response_format_is_calling():
    """MiniObserveCallbackHandler writes 'tool_call: [{name, args}]' — must be detected as calling."""
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 677,
            "response": 'tool_call: [{"name": "transfer_to_math_agent", "args": {}}]',
            "metadata": {},
            "parent_span_id": None,
        },
    ]
    phases, _, _, mf, _, _, _ = compute_cognitive_for_run(steps)
    assert phases[1] == "calling"
    assert "calling" in mf


def test_callback_handler_tool_call_after_tool_is_calling():
    """Callback-format tool call emission after a tool ran must be calling, not synthesizing."""
    tc = 'tool_call: [{"name": "add", "args": {"a": 17, "b": 25}}]'
    steps = [
        {
            "id": 1,
            "timestamp": "2025-01-01T00:00:00Z",
            "span_type": "tool",
            "provider": "tool",
            "model": "search",
            "latency_ms": 1,
            "metadata": {"tool_name": "search"},
        },
        {
            "id": 2,
            "timestamp": "2025-01-01T00:00:01Z",
            "span_type": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "latency_ms": 445,
            "response": tc,
            "metadata": {},
        },
    ]
    phases, _, _, _, _, _, _ = compute_cognitive_for_run(steps)
    assert phases[1] == "executing"
    assert phases[2] == "calling"
