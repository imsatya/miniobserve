# MiniObserve: LangGraph `invoke` integration (reference)

Standalone guidelines and a **single optional Python module** (`miniobserve_langgraph_client.py`) for teams using **LangGraph** (or graphs built with LangChain runnables) with MiniObserve's Python **`Tracer`** and **`MiniObserveCallbackHandler`**.

This document does **not** assume any particular application repository. Copy the `.py` file into your project and adjust imports as needed.

## Prerequisites

- MiniObserve **backend** running (see upstream `AGENTS.md`: `uvicorn`, default URL `http://localhost:7823` unless overridden).
- Python: **`miniobserve[langchain]`** and **`langgraph`** installed (the module imports `langgraph.types.Command` for compact tool logging).
- Ingest sanity check: upstream **`miniobserve hello`** or **`hello_first_integration`** once HTTP and auth work.

## Integration guidelines

### 1. Use LangChain callbacks on `invoke` / `ainvoke`

For **LangGraph** `compiled.invoke(...)` / `ainvoke(...)`, pass **`RunnableConfig`** callbacks so internal **LLM** and **tool** steps become spans. A single span around `invoke` without callbacks usually yields one coarse row and hides handoffs and tools (see upstream **LangGraph / LangChain** in `AGENTS.md`).

### 2. Nest spans under an agent root

When using **`traced_agent_session`**, pass **`root_parent_span_id=<root span_id>`** into the callback handler (same contract as **`miniobserve_langchain_callbacks(...)`** in the SDK) so LLM/tool rows nest under the agent root. Upstream documents **`parent_run_id`** mapping and fallbacks in `AGENTS.md`.

### 3. Keep tool log payloads small

Handoff and graph-control tools may return **LangGraph `Command`** (or other large objects). The server stores **tool** **`response`** / **`metadata.tool_result`** as sent. Prefer **truncation**, a **short fixed string**, or a **small JSON summary** for ingest—**no extra LLM** just to shape logs (upstream **Keep tool log payloads small** and **Do not** in `AGENTS.md`).

The accompanying module **`miniobserve_langgraph_client.py`** subclasses **`MiniObserveCallbackHandler`** and replaces **`on_tool_end`** logging so **`Command`** becomes a **small JSON** summary (`goto`, truncated `graph`, `has_update`). Graph execution is unchanged; only observability rows are affected.

### 4. Short-lived CLIs and flush

Set **`MINIOBSERVE_TRACER_BLOCKING_FLUSH=1`** (or rely on upstream atexit join) so **`POST /api/logs`** completes before the process exits. See upstream **Tracer: missing runs after a CLI exits**.

The module's **`configure_miniobserve_env()`** uses **`os.environ.setdefault`** for local OSS **`MINIOBSERVE_API_KEY`** and blocking flush—**does not override** variables already set (safe for production if env is configured first).

### 5. Dashboard vs no HTTP

If **`MINIOBSERVE_URL`** is **`stdout`**, **`off`**, etc., nothing is sent to the server. **`print_miniobserve_ingest_footer(tracer)`** prints whether HTTP ingest is active and the **`run_id`** so operators can distinguish misconfiguration from ingest failure.

## Accompanying module

| File | Purpose |
|------|--------|
| **`miniobserve_langgraph_client.py`** | Optional template: **`configure_miniobserve_env`**, **`miniobserve_invoke_config`**, **`print_miniobserve_ingest_footer`**, **`CompactToolLogMiniObserveCallbackHandler`**, re-export **`traced_agent_session`**. |

### Minimal usage

```python
from miniobserve_langgraph_client import (
    configure_miniobserve_env,
    miniobserve_invoke_config,
    print_miniobserve_ingest_footer,
    traced_agent_session,
)

configure_miniobserve_env()
with traced_agent_session(mode="task", objective="user goal") as (tracer, root):
    result = compiled_graph.invoke(
        {"messages": [...]},
        config=miniobserve_invoke_config(tracer, root),
    )
print_miniobserve_ingest_footer(tracer)
```

Replace `mode`, `objective`, `compiled_graph`, and message shape with your app. For **`ainvoke`**, pass the same **`config`** into the async call.

## Caveats (read before adopting)

- **Subclass internals** — The compact handler uses the parent's **`_pending_tool`** / **`_exit_tool`**. The footer uses **`getattr(tracer, "_remote", None)`**. Those are **not** guaranteed public SDK APIs; upstream may later offer a supported flag or helper.
- **Single callback list** — **`miniobserve_invoke_config`** returns one handler. Merge with your existing **`callbacks`** if needed.
- **LangGraph import** — **`Command`** handling is optional at runtime (import inside a `try`); non-LangGraph apps still benefit from truncation of long string tool outputs.
- **Production** — Set **`MINIOBSERVE_URL`** and **`MINIOBSERVE_API_KEY`** (and app name) explicitly for non-local deployments.

## Suggested `AGENTS.md` link line

> **Optional LangGraph `invoke` template (compact `Command` tool logs, ingest footer):** see the companion document **LangGraph `invoke` integration (reference)** and the Python module **`miniobserve_langgraph_client.py`** distributed alongside it.

Point the link to wherever you host this document and the `.py` file (docs site, gist, or release asset).
