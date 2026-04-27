# Companion Guide: MiniObserve, Step by Step

## 1) The core problem

When an agent feels slow, expensive, or wrong, the hardest part is not fixing it; it is seeing what actually happened.

Without observability, you usually have:
- partial logs from different processes,
- missing tool arguments/results,
- no reliable run grouping,
- no clear breakdown of where time and cost went.

MiniObserve solves this by treating agent execution as a sequence of structured events that can be stored, queried, and visualized together.

## 2) The conceptual model

MiniObserve is built around one simple concept:

- A **run** is composed of **spans**.

A span is one meaningful step in the run:
- an LLM call,
- a tool call,
- or an agent/router step.

If each span is recorded in order with enough metadata, the full run can be reconstructed and inspected in the dashboard.

## 3) Basics you must send

For a useful baseline, each span should include:
- what generated it (`model`, `provider`),
- what was asked (`prompt` or `request.messages`),
- what happened (`response`, `error`),
- basic performance (`latency_ms`, tokens when available).

To tie multiple spans into one run, reuse the same:
- `run_id`

`run_id` is the primary run-level correlation key. If two spans share a `run_id`, they belong to the same run.

## 4) Building structure: from list to execution tree

A flat list is useful; a tree is better.

To encode parent/child relationships without first knowing server row IDs, use:
- `client_span_id`
- `parent_client_span_id`

This allows you to submit an ordered batch and still preserve structure:
- parent spans first,
- children later with `parent_client_span_id` pointing to the parent `client_span_id`.

For cross-service correlation during batch ingest, you can also set:
- `X-MiniObserve-Run-Id` header

## 5) Integration choices (same data model)

MiniObserve supports two integration styles:

1. **Raw HTTP** (`POST /api/log`, `POST /api/logs`, `PATCH /api/log`)
2. **Python SDK** (`miniobserve.init`, `@observe`, `Tracer`, `traced_agent_session`)

Important: the dashboard renders stored rows, not client type. UI differences come from payload shape, not HTTP vs SDK.

If you send SDK-like span data over HTTP, you get SDK-like visualization.

## 6) Local onboarding (frictionless path)

For first local run, MiniObserve supports a default key path:
- `sk-local-default-key`

In implicit local default mode:
- no key header maps to app `default`,
- `Authorization: Bearer sk-local-default-key` also maps to `default`,
- wrong Bearer returns `401`.

This keeps local setup friction low while still making auth behavior explicit.

## 7) What “full observability” adds

Once basic logging works, MiniObserve adds richer understanding:
- cognitive **phase** per span (`thinking`, `calling`, `synthesizing`, `executing`, `unclassified`) plus `cognitive_stuck` / `cognitive_waiting` flags (heuristics from span order and tool/LLM shape, not model intent),
- run-level **`stuck_alerts`** and approximate **`mode_fractions`** for loop signals,
- optional **`metadata.trace_lane`** for step labels (LangChain handler fills this from graph metadata; tuple-shaped LangGraph lanes are stored as short names),
- token/cost breakdowns,
- replayable prompts/responses/tool metadata.

Conceptually: same span model, more semantics attached.

## 8) Practical rollout path

Use this sequence to avoid overengineering:

1. Send one span with `POST /api/log`.
2. Add stable `run_id` across a run.
3. Move to `POST /api/logs` with parent/child IDs.
4. Add tool metadata and request/response structure.
5. Adopt SDK helper patterns where useful.

This progression gives value at each step without requiring a full instrumentation rewrite.

## 9) Where to copy from

Use existing repo examples as source of truth:

- HTTP example (minimal + canonical batch): [`examples/log_with_key.py`](examples/log_with_key.py)
- SDK agent loop example: [miniobserve-demo](https://github.com/miniobserve/miniobserve-demo) — [`run.py`](https://github.com/miniobserve/miniobserve-demo/blob/main/run.py), [`agent/runner.py`](https://github.com/miniobserve/miniobserve-demo/blob/main/agent/runner.py)
- SDK and API reference details: [`README.md`](README.md)
- Agent-focused integration contract: [`AGENTS.md`](AGENTS.md)

If unsure which field shape to use, copy the canonical HTTP batch example first, then adapt.
