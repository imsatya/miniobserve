#!/usr/bin/env python3
"""
LangChain + MiniObserve: use callbacks so internal LLM/tool steps become spans.

Requires: pip install -e "./sdk[langchain]" (from repo root).

This is the recommended pattern for LangGraph as well: pass the same ``callbacks``
into ``compiled.invoke(..., config=RunnableConfig(callbacks=[...]))``.

Set MINIOBSERVE_URL / MINIOBSERVE_API_KEY like other examples; use MINIOBSERVE_URL=off
for stdout-only tracing.
"""
from __future__ import annotations

import os

os.environ.setdefault("MINIOBSERVE_URL", "off")

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from miniobserve import Tracer
from miniobserve.integrations.langchain_callback import MiniObserveCallbackHandler


@tool
def double(n: int) -> int:
    """Return n * 2."""
    return n * 2


def main() -> None:
    tracer = Tracer()
    with tracer.span("agent", "agent-root") as root:
        root.name = "agent/langchain-callback-demo"
        cb = MiniObserveCallbackHandler(tracer, root_parent_span_id=root.span_id)
        cfg = {"callbacks": [cb]}
        model = FakeListChatModel(responses=["Planning to call the tool."])
        model.invoke([HumanMessage(content="Use double on 21")], config=cfg)
        double.invoke({"n": 21}, config=cfg)
    tracer.summary()


if __name__ == "__main__":
    main()
