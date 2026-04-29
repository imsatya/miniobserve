# MiniObserve for coding agents

Short contract for wiring apps to MiniObserve. Deep docs: [README.md](README.md), deploy: [HOSTING.md](HOSTING.md), HTTP schema: `http://<host>:7823/docs`.

## SDK install — pick the right extra based on the LLM provider in the app

**Check which LLM provider the app uses before installing:**

- OpenAI (`ChatOpenAI`, `openai`): `pip install "miniobserve[openai]"`
- Anthropic (`ChatAnthropic`, `anthropic`): `pip install "miniobserve[anthropic]"`
- Other / provider already installed: `pip install "miniobserve[langchain]"`
- No LangChain: `pip install miniobserve`

**Do not default to `miniobserve[langchain]`** — it does not include the provider packages.

---

## Default local install (zero config)

1. From repo: `cd backend && pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 7823`.
2. Base URL: `http://localhost:7823` (or set `MINIOBSERVE_URL` on the client).
3. **Hello-world ingest check:** with the backend up and `MINIOBSERVE_URL` **not** set to `stdout` / `off`, run `miniobserve hello` (after `pip install miniobserve`) or `python examples/hello_first_integration.py` from the repo. You should see a success message with a `run_id`, and the dashboard should show a new row (model **`integration-check`**, span **`hello_first_integration`**, prompt starting with “Hello — first”). If ingest fails, the command prints HTTP / auth hints.

### Local default API key (use on every client)

For OSS local development, **treat `sk-local-default-key` as the normal client secret** and send it on **every** request:

- **Python / SDK:** set `MINIOBSERVE_API_KEY=sk-local-default-key` in the environment (or pass the same value into `MiniObserve(..., api_key=...)` / `Tracer(..., api_key=...)`).
- **HTTP / curl / other languages:** `Authorization: Bearer sk-local-default-key` or header `X-Api-Key: sk-local-default-key`.
- **Dashboard:** click **Default — sk-local-default-key** on the login screen so the browser stores that key (same as typing it yourself). Prefer this over an empty key so your setup matches production-style “always send a credential.”

The literal string is always **`sk-local-default-key`** (matches `LOCAL_DEFAULT_API_KEY` in [`backend/auth.py`](backend/auth.py)). The CLI `miniobserve hello` sends it automatically when `MINIOBSERVE_API_KEY` is unset and the URL is `localhost` / `127.0.0.1`.

When **`MINIOBSERVE_API_KEYS` is unset** in `backend/.env`, **`MINIOBSERVE_API_KEY_PEPPER` is unset**, **`MINIOBSERVE_ENV` is not `production`**, and **`MINIOBSERVE_DISABLE_LOCAL_DEFAULT_KEY` is not set**, the server still accepts the mapping below (implicit mode). **Agents should use the explicit key above anyway** so behavior matches hosts that require a header.

| Header | Result |
|--------|--------|
| No `Authorization` / `X-Api-Key` | App **`default`** (dashboard **Continue** without typing a key — prefer **Default** or `MINIOBSERVE_API_KEY` so clients always send a credential). |
| `Authorization: Bearer sk-local-default-key` | App **`default`**. |
| Any other Bearer value | **`401`** (invalid key). |

To turn off the implicit localhost default key and require an explicit key mapping, set **`MINIOBSERVE_DISABLE_LOCAL_DEFAULT_KEY=1`** (see [backend/.env.example](backend/.env.example)).

For explicit keys in env, use **`MINIOBSERVE_API_KEYS=secret:app_name`** (see README). You should still set **`MINIOBSERVE_API_KEYS=sk-local-default-key:default`** (or equivalent) when you want the server’s key table to list the local default explicitly; clients keep sending **`sk-local-default-key`** as the Bearer secret (the part before `:` is never sent in the JSON body).

## Where to look (copy-paste starting points)

Open these files in the repo—do not infer payloads only from the tables below.

1. **HTTP (stdlib, no SDK):** [examples/log_with_key.py](examples/log_with_key.py) — copy `post_log` (single `POST /api/log`), `post_logs_batch` (`POST /api/logs` + `X-MiniObserve-Run-Id` header), and `canonical_agent_run_logs` (full agent-shaped batch with `client_span_id` / `parent_client_span_id`). From repo root: `python examples/log_with_key.py` (backend must be running; env `MINIOBSERVE_URL`, `MINIOBSERVE_API_KEY` default to `sk-local-default-key`).

   **Fastest “did wiring work?”** — [examples/hello_first_integration.py](examples/hello_first_integration.py) or CLI `miniobserve hello` (same HTTP path as `post_log`; one obvious row in the UI).

2. **Python SDK (agent loop):** standalone **[miniobserve-demo](https://github.com/miniobserve/miniobserve-demo)** — [`run.py`](https://github.com/miniobserve/miniobserve-demo/blob/main/run.py), [`agent/runner.py`](https://github.com/miniobserve/miniobserve-demo/blob/main/agent/runner.py): `traced_agent_session`, `Tracer.run_llm`, `Tracer.run_tool` with the SDK installed via `pip` (see that repo’s `README.md`).

3. **Python SDK (snippets):** [README.md](README.md) — section **Python SDK** (`init`, `@observe`, `Tracer`, `traced_agent_session`).

4. **LangChain / LangGraph:** [examples/langchain_callback_invoke.py](examples/langchain_callback_invoke.py) — `MiniObserveCallbackHandler` + `config={"callbacks": [...]}`. Install the right extra for your provider:
   - OpenAI: `pip install "miniobserve[openai]"` (installs `langchain-core` + `langchain-openai`)
   - Anthropic: `pip install "miniobserve[anthropic]"` (installs `langchain-core` + `langchain-anthropic`)
   - Other / already installed: `pip install "miniobserve[langchain]"` (installs `langchain-core` only)

   See [LangGraph / LangChain](#langgraph--langchain) below.

## LangGraph / LangChain

- **One span around `graph.invoke` only** (no LangChain callbacks): MiniObserve sees whatever you emit manually—often **one** LLM-shaped row. The dashboard’s per-step “tool calls” line and `Tracer.summary()` **tool counts** come from **stored spans** (`span_type: tool` and/or OpenAI-style `tool_calls` JSON on LLM responses). Internal LangGraph tool or handoff nodes are **invisible** unless something maps them into those spans.
- **Recommended:** pass [`MiniObserveCallbackHandler`](sdk/miniobserve/integrations/langchain_callback.py) via LangChain’s `callbacks` on `invoke` / `ainvoke` / `RunnableConfig` (same mechanism LangGraph uses). Optionally set **`MINIOBSERVE_TRACER_DIAG=1`** (or `MINIOBSERVE_DEBUG`) to print a hint when an LLM span reports tool calls but **no** `tool` spans were emitted.
- **Trace lanes (no manual wiring):** the handler copies LangChain’s callback **`tags`** / **`metadata`** into **`metadata.trace_lane`** on each **`llm`** and **`tool`** span when it finds known keys (e.g. LangGraph-style `langgraph_node`, LangSmith-style `ls_run_name`) or a usable tag—so you get readable step labels in the dashboard **without** inventing graph names in application code. Override only when you need a custom label (`Tracer.run_llm(..., trace_lane=...)` or HTTP `metadata.trace_lane` / `mo_trace_lane`).
- **Sanity check:** run **`miniobserve hello`** once to confirm HTTP + auth before relying on graph callbacks (see step 3 under default local install).
- **Tree / `parent_span_id`:** pass **`root_parent_span_id=<agent root span_id>`** (e.g. from `miniobserve_langchain_callbacks(tracer, root_parent_span_id=root.span_id)` inside `traced_agent_session`) so top-level LLM/tool rows nest under the agent. The handler maps LangChain **`parent_run_id`** when that run was opened by the same handler; when **`parent_run_id` is missing**, it falls back to the **last completed LLM** (for tools) and **last completed tool** (for the next LLM) so `parent_client_span_id` is still sent—see [examples/log_with_key.py](examples/log_with_key.py) for batch shape and **Runs and UI parity** below.
- **Tool end output:** [`MiniObserveCallbackHandler`](sdk/miniobserve/integrations/langchain_callback.py) records whatever LangChain passes as tool output. For **graph handoffs**, wrap or adjust the tool so the value logged to callbacks is already **short** (truncate, constant status line, or a small dict)—see [Keep tool log payloads small](#keep-tool-log-payloads-small). No second model pass is required; avoid dumping full `Command` / `State` / message-list reprs into MiniObserve.
- **Timing semantics (important):** treat **`latency_ms`** as **actual execution time** for that span, not dispatch/queue time. In LangGraph/LangChain flows where callbacks can fire before work starts, prefer emitting explicit **`started_at`** / **`ended_at`** (or separate wait spans) so timelines do not show false overlap. Copy wiring from [examples/langchain_callback_invoke.py](examples/langchain_callback_invoke.py), [miniobserve-demo `agent/runner.py`](https://github.com/miniobserve/miniobserve-demo/blob/main/agent/runner.py) (manual `Tracer` loop), and canonical batch shape in [examples/log_with_key.py](examples/log_with_key.py).

## Stable HTTP API

| Method | Path | Purpose |
|--------|------|-----------|
| POST | `/api/log` | One span |
| POST | `/api/logs` | Batch `{ "logs": [ ... ] }` (parents before children); optional header `X-MiniObserve-Run-Id` |
| PATCH | `/api/log` | Update span (e.g. after async token fill) |
| GET | `/api/health` | Smoke check |

Auth: **`Authorization: Bearer <secret>`** or **`X-Api-Key: <secret>`** only. Never put the secret in the JSON body. Never send `secret:app_name` as the Bearer value—only the part before `:` when using `MINIOBSERVE_API_KEYS`.

### Span fields the server cannot infer (client / SDK must send)

MiniObserve stores one row per span; it does **not** run inside your process to measure wall time or call vendor LLMs. Treat the contract as follows:

| Concept | JSON field | Who sets it | Notes |
|--------|------------|-------------|--------|
| Wall time for the span | **`latency_ms`** | Client | Same idea as “duration_ms”: elapsed time between span start and end **on the client** (or use the Python **`Tracer`**, which fills this from `Span` timers). The server does not compute this from `timestamp` alone. |
| Token usage | **`input_tokens`**, **`output_tokens`**, **`cached_input_tokens`** (and `total_tokens`) | Client | Forward the provider `usage` object (or SDK-normalized counts). If omitted, cost estimation and cache UI have nothing authoritative. Optional: send a JSON completion blob in **`response`** and ingest may read OpenAI-style cached-token hints—still your responsibility to attach usage when possible. |
| Cost | **`cost_usd`** | Client optional | Backend may **estimate** missing/zero `cost_usd` from model + tokens ([`backend/ingest/cost.py`](backend/ingest/cost.py)). |
| Tool output | **`response`** on **`span_type: "tool"`** rows | Client | Put the tool return payload in **`response`** (truncated in examples to a few KB). Do **not** rely on inferring tool output from the **next** LLM row’s `messages` / prompt—emit a dedicated **tool** span (or use [`Observer.log_tool`](sdk/miniobserve/observer.py) / `Tracer.run_tool`). **`metadata.tool_result`** is also set for tool spans from the Python **`Tracer`** (parity with `log_tool`). |
| Span bounds (optional) | **`started_at`**, **`ended_at`** | Client optional | ISO8601 strings accepted on **POST/PATCH**; stored inside **`metadata`** for timelines / validation (no separate DB columns). PATCH merges these keys into existing `metadata`. |
| Trace lane label (UI) | **`metadata.trace_lane`** (alias **`metadata.mo_trace_lane`**) | **LangChain handler** (auto) or client override | Short string shown before the primary label in the trace pipeline and run lists. With **`MiniObserveCallbackHandler`**, values come from Runnable **`tags`** / **`metadata`** (LangGraph node keys, LangSmith run name, etc.); LangGraph-style `__pregel_pull` tuples are normalized to a short node name before storage. For HTTP-only or custom stacks, set **`metadata.trace_lane`** yourself, or use **`Tracer.run_llm(..., trace_lane=...)`**. **No** prompt-body heuristics on the server. |
| Span type column | **`span_type`** (top-level on each log) | Client / **Tracer** | Ingest copies **`metadata.span_type`** into the **`span_type` column** when the top-level field is omitted (Python **`Tracer`** batch shape). Prefer sending top-level **`span_type`** for the clearest dashboard colors and cognitive classification. |
| Human step title (UI) | **`metadata.agent_span_name`**, LLM **`prompt` JSON `step`** | Python **`Tracer`** (and compatible HTTP) | The OSS dashboard uses these for the **main activity step label** before assistant **`response`** snippets. Tracer intentionally uses generic top-level **`span_name`** values (`llm_call`, `tool_call`, `router`); meaningful names live in **metadata** and **`prompt`** JSON—see [`Tracer._span_to_log_body`](sdk/miniobserve/tracer.py). |

### Deterministic decision metadata (optional)

To expose chosen/skipped/missing paths without extra model calls, emit a namespaced block:

```json
{
  "metadata": {
    "decision": {
      "type": "workflow_routing",
      "chosen": ["route:billing"],
      "available": ["route:billing", "route:security"],
      "selection_signals": {"priority": "speed"},
      "expected_downstream": ["tool:refund_policy", "tool:security_escalation"],
      "impact": {"status": "ok"}
    }
  }
}
```

MiniObserve computes deterministic fields from stored spans:
- `skipped = available - chosen`
- `missing_expected = expected_downstream - observed_descendants`
- downstream rollups: latency/tokens/cost/errors over descendants.

ID conventions for reliable matching: `tool:<name>`, `route:<name>`, `agent:<name>`, `workflow:<name>`.

### Decision observability: required wiring (read carefully)

MiniObserve computes `missing_expected` from the stored span tree.  
If you only set custom metadata keys (for example `metadata.miniobserve_client_span_id`) and do not set linkage fields, checks will be wrong.

**Required on payload rows:**
- `client_span_id`
- `parent_client_span_id` (for children)

**Required for decision semantics:**
- `metadata.decision` with `type`, `chosen`; optional `available`, `expected_downstream`, `selection_signals`, `impact`
- Canonical orchestration IDs on route/workflow spans:
  - preferred: `metadata.workflow_node` (example: `route:final`)
  - optional alias: `metadata.route_id` (normalized to `route:<value>`)

**Important semantics:**
- Emit decision metadata on the span that actually makes the decision (router/tool-select step).
- If you emit a pre-run heuristic/intent checkpoint, mark `metadata.decision.stage = "pre"`.
- Keep decision-span `prompt` minimal (`"route_decision"`). Do not add extra LLM calls just to generate decision text.

#### Good (HTTP shape)

```json
{
  "run_id": "r1",
  "span_type": "llm",
  "span_name": "router_decision",
  "prompt": "route_decision",
  "response": "",
  "client_span_id": "decision-1",
  "metadata": {
    "decision": {
      "type": "workflow_routing",
      "chosen": ["route:math"],
      "available": ["route:math", "route:research"],
      "expected_downstream": ["tool:add"],
      "selection_signals": {"detected_math": true}
    }
  }
}
```

```json
{
  "run_id": "r1",
  "span_type": "llm",
  "span_name": "llm_call",
  "client_span_id": "node-final-1",
  "parent_client_span_id": "decision-1",
  "metadata": { "workflow_node": "route:final" }
}
```

```json
{
  "run_id": "r1",
  "span_type": "tool",
  "span_name": "tool_call",
  "prompt": "{\"tool\":\"add\",\"args\":{\"a\":13,\"b\":14}}",
  "response": "27",
  "client_span_id": "tool-add-1",
  "parent_client_span_id": "decision-1",
  "metadata": { "tool_name": "add" }
}
```

#### Bad (common mistake)

```json
{
  "metadata": {
    "miniobserve_client_span_id": "decision-1"
  }
}
```

This is metadata only; it does **not** create lineage.

#### Good (Tracer helper style)

```python
tracer.run_tool(
    name="tool-step",
    parent_id=parent_span_id,
    tool_name="add",
    tool_args={"a": 13, "b": 14},
    fn=lambda: "27",
    extra_metadata={
        "decision": {
            "type": "tool_select",
            "chosen": ["tool:add"],
            "available": ["tool:add", "tool:search"],
            "expected_downstream": ["tool:add"],
        }
    },
)
```

This adds observability rows only (no extra model inference cost).

OpenAPI **`/docs`** on the running server includes a short summary of this contract in the app **description** ([`backend/main.py`](backend/main.py)).

### Keep tool log payloads small

For **`span_type: tool`** rows, whatever you put in **`response`** (and **`metadata.tool_result`** when using the Python **`Tracer`**) is stored and shown in the UI. **Handoff and graph-control tools** (e.g. LangGraph `transfer_to_*`, `transfer_to_math_expert`) often return a huge `repr(...)` (`Command(...)`, full state). You do **not** need—and should not need—**any extra LLM call** to “clean up” logs for MiniObserve. In ordinary client code: **truncate** a string to a cap (e.g. first 500–2000 chars), **return a fixed short line** from a thin wrapper around the tool, or **log a tiny structured dict** you build yourself (`{"handoff": "math_expert"}`). Pick one deterministic approach; avoid shipping the entire runtime object string.

### LLM spans: fields worth sending every time

Rich **`prompt`** / **`request.messages`** alone do not populate cost or duration. For each **`span_type: llm`** (or equivalent) row, also send when your stack has them:

- **`latency_ms`** — wall time for that model call.
- **`input_tokens`**, **`output_tokens`**, **`cached_input_tokens`** — from the provider completion payload (or PATCH them in after streaming).
- **`response`** — assistant text and/or tool-calls JSON, if you want the log detail view to match the real model output.

### Dashboard: step titles and `cognitive_mode`

- **Trace / Runs activity column:** prefers **`metadata.agent_span_name`** and Tracer-style **`prompt` JSON** (`step`), then an assistant **`response`** snippet, then model and fallbacks (see [`frontend/src/runUi.js`](frontend/src/runUi.js) `stepPrimaryLabel` / `traceStepDisplayLabel`).
- **`cognitive_mode`** stores a **behavioral phase** computed at ingest (framework-agnostic): **`thinking`** (LLM call that did not emit tool calls — reasoning before any action), **`calling`** (LLM call that emitted tool calls — detected from `had_tool_call` in Tracer prompt JSON or OpenAI-style `response` blob), **`synthesizing`** (LLM call after tools ran that did not emit further tool calls — synthesis/summarisation), **`executing`** (tool span or child-agent wrapper doing real work), or **`unclassified`** (fell through all heuristics). Session envelope spans (root `span_type=agent` with no parent) are excluded from phase classification entirely. **`cognitive_stuck`** and **`cognitive_waiting`** are separate boolean flags (repeat tool fingerprint; slow tool vs median). Phases are heuristics based on span order and metadata, not model intent. Treat **`stuck_alerts`** on the run summary as the most actionable loop signal; **mode_fractions** are approximate. See [`backend/cognitive/modes.py`](backend/cognitive/modes.py) for classifier details.

## Runs and UI parity

- Use the same **`run_id`** on every span in one run.
- For trees without server ids, use **`client_span_id`** and **`parent_client_span_id`** in each log object; order batch entries parent-before-child.

The dashboard shows **stored rows**, not “Python vs HTTP.” For agent-shaped runs that match the Python **`Tracer`** flush shape, mirror **`Tracer._span_to_log_body`** in [sdk/miniobserve/tracer.py](sdk/miniobserve/tracer.py). Runnable HTTP reference: [examples/log_with_key.py](examples/log_with_key.py) (sections 1–2 minimal smoke; **section 3** canonical `POST /api/logs` batch).

## Client environment (SDK and HTTP)

| Variable | Role |
|----------|------|
| `MINIOBSERVE_URL` | Backend base URL (SDK / Tracer). If **set** to empty / `stdout` / `off`, HTTP ingest is disabled. If **unset**, defaults to `http://localhost:7823` unless `MINIOBSERVE_DASHBOARD_ORIGIN` is set (use that so clients post to the same origin you open in the browser — tunnels, custom host). |
| `MINIOBSERVE_DASHBOARD_ORIGIN` | Optional. When `MINIOBSERVE_URL` is **not** present in the environment, this value is used as the ingest base URL (no trailing slash). Set it to the exact dashboard origin (e.g. `https://abc.ngrok-free.app`) so traces land in the same server the UI uses. |
| `MINIOBSERVE_API_KEY` | Bearer secret sent on every SDK/CLI request. **For local OSS, set to `sk-local-default-key`** (same as dashboard **Default** button). Unset on localhost still works for `miniobserve hello` only (it injects the local default for `localhost` / `127.0.0.1`); other clients should set this explicitly. |
| `MINIOBSERVE_APP_NAME` | Usually `default` for local implicit key. |
| `MINIOBSERVE_TRACER_DIAG` | `1` — stderr hint when LLM spans report tool calls but no `tool` spans (coarse LangGraph / LangChain integration). |
| `MINIOBSERVE_TRACER_BLOCKING_FLUSH` | **`1`** recommended for **short-lived scripts and CLIs**: run the HTTP batch **inside** `Tracer.summary()` before returning (no background thread). If unset, flush is async but the SDK **registers an atexit handler** to join pending flush threads (~20s cap) so one-shot processes usually still deliver—use blocking if you call `os._exit`, hard-kill the process, or need the run visible **before** `summary()` returns. |
| Others | Retries, background flush, debug—see README “Client / SDK” table. |

### Tracer: missing runs after a CLI exits

`Tracer.summary()` defaults to a **non-blocking** HTTP flush so long agent loops are not stalled on network I/O. **Short-lived** programs (single `invoke`, a CLI that exits immediately) used to lose traces when the process died before the daemon flush thread finished.

**Mitigations:** (1) set **`MINIOBSERVE_TRACER_BLOCKING_FLUSH=1`** so flush completes before `summary()` returns; (2) rely on the SDK **atexit join** of pending flush threads (since recent versions); (3) avoid `os._exit(0)` / SIGKILL before exit hooks run.

Python: `pip install miniobserve` (or `pip install "miniobserve[openai]"` / `"miniobserve[anthropic]"` for LangChain provider support) then `miniobserve.init(...)`, `@observe`, or `Tracer` / `traced_agent_session` (README). Demo agent loop (standalone install): **[miniobserve-demo](https://github.com/miniobserve/miniobserve-demo)**.

**`Tracer` constructor** — only these keyword arguments exist (do not invent others):
```python
Tracer(
    run_id=None,      # optional: custom run id string
    server_url=None,  # optional: overrides MINIOBSERVE_URL
    app_name=None,    # optional: overrides MINIOBSERVE_APP_NAME
    api_key=None,     # optional: overrides MINIOBSERVE_API_KEY
)
```

## Troubleshooting (short)

1. Client URL must match the dashboard origin.
2. Key only in headers; wrong Bearer → `401`. For local OSS use **`sk-local-default-key`** (dashboard **Default** or `MINIOBSERVE_API_KEY`).
3. Backend terminal should show `[ingest]` lines when logs arrive.
4. `200` with `{"ok":true,...}` is success; `401` invalid/missing key when keys are required; `503` often DB.
5. Supabase: use **service_role** on the server, not anon (README).
6. **Tracer run missing right after a script exits:** set **`MINIOBSERVE_TRACER_BLOCKING_FLUSH=1`**, or ensure normal process exit (atexit joins async flush). See [Tracer: missing runs after a CLI exits](#tracer-missing-runs-after-a-cli-exits).

## Working on this repo

Backend dev: `cd backend && uvicorn main:app --port 7823`. After changing backend ingest or cognitive logic, restart the server; cognitive pipeline changes may need `python3 backfill_cognitive.py` (see [.cursor/rules/miniobserve-backend-restart.mdc](.cursor/rules/miniobserve-backend-restart.mdc)).

## Do not

- Log secrets inside `prompt` / `response` / `metadata`.
- Send **multi‑thousand‑character reprs** of LangGraph / LangChain internal objects (e.g. full `Command(...)`, entire checkpoint state) as tool **`response`** / **`metadata.tool_result`**—**truncate or replace in your code** with a short fixed or structured value (see [Keep tool log payloads small](#keep-tool-log-payloads-small)); do not rely on another LLM call just to shape logs.
- Hardcode real production API keys in examples or commits.
- Rely on undocumented endpoints; use `/api/log` and `/api/logs` unless you extend the server.
