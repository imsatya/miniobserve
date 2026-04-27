"""MiniObserve LangChain callback handler (requires ``langchain-core``)."""
import os
from uuid import uuid4

import pytest

pytest.importorskip("langchain_core")

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tools import tool

from miniobserve import Tracer
from miniobserve.integrations.langchain_callback import (
    MiniObserveCallbackHandler,
    _agent_name_from_langgraph_metadata,
    _normalize_trace_lane_for_storage,
    _trace_lane_from_langchain,
    miniobserve_langchain_callbacks,
)


@pytest.fixture
def tracer_off():
    os.environ["MINIOBSERVE_URL"] = "off"
    return Tracer()


def test_normalize_trace_lane_pregel_tuple():
    assert _normalize_trace_lane_for_storage("('__pregel_pull', 'research_expert')") == "research_expert"
    assert _normalize_trace_lane_for_storage('("__pregel_pull", "supervisor")') == "supervisor"
    assert _normalize_trace_lane_for_storage("('__pregel_pull', 'agent')") is None
    assert _normalize_trace_lane_for_storage("plain_lane") == "plain_lane"


def test_miniobserve_langchain_callbacks_factory(tracer_off):
    lst = miniobserve_langchain_callbacks(tracer_off, root_parent_span_id="abc")
    assert len(lst) == 1
    assert isinstance(lst[0], MiniObserveCallbackHandler)


def test_chat_model_emits_llm_span(tracer_off):
    with tracer_off.span("agent", "agent-root") as root:
        root.name = "agent/t"
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        m = FakeListChatModel(responses=["ok"])
        m.invoke([HumanMessage(content="hi")], config={"callbacks": [cb]})

    llm = [s for s in tracer_off.spans if s.span_type == "llm"]
    assert len(llm) == 1
    assert llm[0].parent_span_id == root.span_id
    assert llm[0].request_messages


def test_non_chat_llm_start_end(tracer_off):
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        m = FakeListLLM(responses=["x"])
        m.invoke("prompt", config={"callbacks": [cb]})

    llm = [s for s in tracer_off.spans if s.span_type == "llm"]
    assert len(llm) == 1
    assert llm[0].assistant_preview


@tool
def adder(a: int, b: int) -> int:
    """Return a + b."""
    return a + b


def test_tool_invoke_emits_tool_span(tracer_off):
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        adder.invoke({"a": 2, "b": 3}, config={"callbacks": [cb]})

    tools = [s for s in tracer_off.spans if s.span_type == "tool"]
    assert len(tools) == 1
    assert tools[0].tool_name == "adder"
    assert tools[0].tool_args == {"a": 2, "b": 3}
    assert tools[0].parent_span_id == root.span_id


def _minimal_chat_llm_result(text: str = "ok") -> LLMResult:
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content=text))]])


def test_usage_metadata_populates_tokens_and_cache_read(tracer_off):
    """LangChain chat models often expose counts only on AIMessage.usage_metadata."""
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid = uuid4()
        cb.on_chat_model_start({}, [[HumanMessage(content="hi")]], run_id=rid, parent_run_id=None)
        msg = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 7,
                "total_tokens": 107,
                "input_token_details": {"cache_read": 30},
            },
        )
        cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=rid)

    llm = [s for s in tracer_off.spans if s.span_type == "llm"][0]
    assert llm.input_tokens == 100
    assert llm.output_tokens == 7
    assert llm.cache_read_tokens == 30


def test_fallback_parent_chain_when_parent_run_id_is_none(tracer_off):
    """LangChain often omits parent_run_id; handler links LLM → tool → LLM via completion order."""
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid_llm1 = uuid4()
        rid_tool = uuid4()
        rid_llm2 = uuid4()
        cb.on_chat_model_start(
            {},
            [[HumanMessage(content="hi")]],
            run_id=rid_llm1,
            parent_run_id=None,
        )
        cb.on_llm_end(_minimal_chat_llm_result(), run_id=rid_llm1)
        cb.on_tool_start({"name": "handoff"}, "{}", run_id=rid_tool, parent_run_id=None)
        cb.on_tool_end("{}", run_id=rid_tool)
        cb.on_chat_model_start(
            {},
            [[HumanMessage(content="next")]],
            run_id=rid_llm2,
            parent_run_id=None,
        )
        cb.on_llm_end(_minimal_chat_llm_result("done"), run_id=rid_llm2)

    llms = [s for s in tracer_off.spans if s.span_type == "llm"]
    tools = [s for s in tracer_off.spans if s.span_type == "tool"]
    assert len(llms) == 2
    assert len(tools) == 1
    assert llms[0].parent_span_id == root.span_id
    assert tools[0].parent_span_id == llms[0].span_id
    assert llms[1].parent_span_id == tools[0].span_id


def test_llm_with_unmapped_parent_run_id_uses_root_not_stale_tool(tracer_off):
    """Explicit parent_run_id unknown to the handler → root; must not chain from stale tool."""
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid_tool = uuid4()
        rid_llm = uuid4()
        unknown_parent = uuid4()
        cb.on_tool_start({"name": "t"}, "{}", run_id=rid_tool, parent_run_id=None)
        cb.on_tool_end("x", run_id=rid_tool)
        cb.on_chat_model_start(
            {},
            [[HumanMessage(content="h")]],
            run_id=rid_llm,
            parent_run_id=unknown_parent,
        )
        cb.on_llm_end(_minimal_chat_llm_result(), run_id=rid_llm)

    llms = [s for s in tracer_off.spans if s.span_type == "llm"]
    assert len(llms) == 1
    assert llms[0].parent_span_id == root.span_id


def test_trace_lane_from_langchain_metadata_priority():
    """``langgraph_node`` maps to agent_name, not trace_lane; next metadata key is the lane."""
    assert _trace_lane_from_langchain(None, {"langgraph_node": "sup", "ls_run_name": "other"}) == "other"
    assert _agent_name_from_langgraph_metadata({"langgraph_node": "sup", "ls_run_name": "other"}) == "sup"


def test_agent_name_generic_node_prefers_ls_run_name():
    assert (
        _agent_name_from_langgraph_metadata(
            {"langgraph_node": "agent", "ls_run_name": "research_expert", "ls_name": "ignored"}
        )
        == "research_expert"
    )


def test_agent_name_generic_node_ls_name_fallback():
    assert _agent_name_from_langgraph_metadata({"langgraph_node": "tools", "ls_name": "math_expert"}) == "math_expert"


def test_agent_name_generic_node_run_name_fallback():
    assert _agent_name_from_langgraph_metadata({"langgraph_node": "__start__", "run_name": "pipe_a"}) == "pipe_a"


def test_trace_lane_from_langchain_tags_fallback():
    assert _trace_lane_from_langchain(["my_node"], None) == "my_node"


def test_trace_lane_skips_uuid_tags():
    u = "550e8400-e29b-41d4-a716-446655440000"
    assert _trace_lane_from_langchain([u, "real_lane"], None) == "real_lane"


def test_trace_lane_long_metadata_truncated():
    long = "x" * 200
    out = _trace_lane_from_langchain(None, {"ls_run_name": long})
    assert out is not None
    assert len(out) == 128


def test_agent_name_long_langgraph_node_truncated():
    long = "y" * 200
    out = _agent_name_from_langgraph_metadata({"langgraph_node": long})
    assert out is not None
    assert len(out) == 128


def test_chat_model_start_sets_agent_name_from_langgraph_node(tracer_off):
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid = uuid4()
        cb.on_chat_model_start(
            {},
            [[HumanMessage(content="hi")]],
            run_id=rid,
            parent_run_id=None,
            metadata={"langgraph_node": "math_expert"},
        )
        cb.on_llm_end(_minimal_chat_llm_result(), run_id=rid)

    llm = [s for s in tracer_off.spans if s.span_type == "llm"][0]
    assert llm.agent_name == "math_expert"
    assert llm.trace_lane is None


def test_tool_start_sets_trace_lane_from_tags(tracer_off):
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid = uuid4()
        cb.on_tool_start(
            {"name": "handoff"},
            "{}",
            run_id=rid,
            parent_run_id=None,
            tags=["transfer_node"],
        )
        cb.on_tool_end("{}", run_id=rid)

    tool = [s for s in tracer_off.spans if s.span_type == "tool"][0]
    assert tool.trace_lane == "transfer_node"


def test_log_body_generic_langgraph_node_uses_ls_run_name_for_agent_name(tracer_off):
    """Inner react graph reports langgraph_node=tools; outer subgraph name is on ls_run_name."""
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid = uuid4()
        cb.on_tool_start(
            {"name": "t"},
            "{}",
            run_id=rid,
            parent_run_id=None,
            metadata={"langgraph_node": "tools", "ls_run_name": "research_expert"},
        )
        cb.on_tool_end("ok", run_id=rid)

    tool = [s for s in tracer_off.spans if s.span_type == "tool"][0]
    body = tracer_off._span_to_log_body(tool)
    assert body["metadata"].get("agent_name") == "research_expert"
    # ``_trace_lane_from_langchain`` also picks ``ls_run_name`` when path keys are absent.
    assert body["metadata"].get("trace_lane") == "research_expert"


def test_log_body_generic_langgraph_node_only_no_agent_name(tracer_off):
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid = uuid4()
        cb.on_tool_start(
            {"name": "t"},
            "{}",
            run_id=rid,
            parent_run_id=None,
            metadata={"langgraph_node": "tools"},
        )
        cb.on_tool_end("ok", run_id=rid)

    tool = [s for s in tracer_off.spans if s.span_type == "tool"][0]
    body = tracer_off._span_to_log_body(tool)
    assert body["metadata"].get("agent_name") is None
    assert body["metadata"].get("trace_lane") is None


def test_log_body_trace_lane_when_only_ls_run_name(tracer_off):
    with tracer_off.span("agent", "agent-root") as root:
        cb = MiniObserveCallbackHandler(tracer_off, root_parent_span_id=root.span_id)
        rid = uuid4()
        cb.on_tool_start(
            {"name": "t"},
            "{}",
            run_id=rid,
            parent_run_id=None,
            metadata={"ls_run_name": "my_lane"},
        )
        cb.on_tool_end("ok", run_id=rid)

    tool = [s for s in tracer_off.spans if s.span_type == "tool"][0]
    body = tracer_off._span_to_log_body(tool)
    assert body["metadata"].get("trace_lane") == "my_lane"
    assert body["metadata"].get("agent_name") is None
