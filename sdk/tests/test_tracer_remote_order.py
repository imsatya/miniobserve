"""Tracer batch ordering: all spans must be sent with parents before children."""

from typing import Optional

from miniobserve.tracer import Tracer


def _assert_parent_before_child(order: list, parent_id: Optional[str], child) -> None:
    if not parent_id:
        return
    parent_ids = {s.span_id for s in order}
    assert parent_id in parent_ids
    pi = next(i for i, s in enumerate(order) if s.span_id == parent_id)
    ci = next(i for i, s in enumerate(order) if s.span_id == child.span_id)
    assert pi < ci


def test_remote_order_keeps_all_agent_spans_and_roots_first():
    t = Tracer()
    with t.span("agent", "outer") as outer:
        with t.span("agent", "inner", parent_id=outer.span_id) as inner:
            with t.span("llm", "step-a", parent_id=inner.span_id) as llm:
                llm.model = "m"
                llm.provider = "openai"
    ordered = t._ordered_spans_for_remote()
    assert len(ordered) == 3
    names = {s.name for s in ordered}
    assert names == {"outer", "inner", "step-a"}
    _assert_parent_before_child(ordered, None, outer)
    _assert_parent_before_child(ordered, outer.span_id, inner)
    _assert_parent_before_child(ordered, inner.span_id, llm)


def test_remote_order_single_root_agent_before_llm():
    t = Tracer()
    with t.span("agent", "root") as root:
        with t.span("llm", "only-llm", parent_id=root.span_id) as llm:
            llm.model = "m"
            llm.provider = "openai"
    ordered = t._ordered_spans_for_remote()
    assert len(ordered) == 2
    assert ordered[0].span_type == "agent" and ordered[0].name == "root"
    assert ordered[1].span_type == "llm"
    _assert_parent_before_child(ordered, root.span_id, llm)
