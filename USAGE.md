# Using MiniObserve (short)

Assumes MiniObserve is **already running as a managed service**. You only connect your app and use the dashboard.

## 1. What you need from the operator

- **Base URL** — e.g. `https://miniobserve.example.com` (same origin as the API and dashboard).
- **API key** — if the service uses key auth, you get a secret key scoped to your app.
- **App name** — the label for your project (often implied by the key; match what the operator gave you).

Open the base URL in a browser to use the dashboard. Sign in with your API key when prompted (key is sent in headers, not stored on the server beyond the session).

## 2. Send logs from Python

Install the SDK (from this repo or your package index, if published):

```bash
pip install -e ./sdk
```

Point at the **hosted** URL and pass the **API key** in `init`:

```python
import miniobserve

miniobserve.init(
    server_url="https://miniobserve.example.com",
    api_key="your-api-key",
    app_name="my-app",
)
obs = miniobserve.MiniObserve()
obs.log(
    model="gpt-4o",
    provider="openai",
    prompt="...",
    response="...",
    input_tokens=0,
    output_tokens=0,
    latency_ms=0,
)
```

Or wrap LLM calls with `@miniobserve.observe(provider="openai", model="gpt-4o")` (see README).

## 3. Auth rules (HTTP or any client)

Send the API key in a **header only** — never in the JSON body:

- `Authorization: Bearer <key>` or `X-Api-Key: <key>`

POST log payloads to `{base_url}/api/log`. Wrong or missing key → 401.

## 4. Tracing and grouping (correlation headers)

MiniObserve does **not** implement a full distributed trace graph. Instead, each `POST /api/log` can carry **correlation fields** so related LLM calls share an id you can **see in one place** and **match in the logs list**.

### Special headers

Send any header whose name starts with **`X-MiniObserve-`** (case-insensitive). The server copies them into the stored row’s **`metadata`** object:

- The part after `X-MiniObserve-` becomes the metadata key: **hyphens → underscores**, lowercased.  
  Examples:
  - `X-MiniObserve-Run-Id: job-abc-123` → `metadata.run_id = "job-abc-123"`
  - `X-MiniObserve-Iteration: 3` → `metadata.iteration = "3"`

You can add **any** suffix you need (`Session`, `User-Id`, `Experiment`, …) using the same pattern.

### Overriding from the JSON body

If the request body includes a `metadata` object, **body values win** for the same key after headers are merged (headers are applied first, then body `metadata`).

### How “grouping” works in the UI

- The dashboard **logs table** has a **Run** column. It shows **`run_id`** (or `runId`) from `metadata` — typically populated via `X-MiniObserve-Run-Id`.
- **Grouping is by convention:** use the **same** `Run-Id` value on every log line that belongs to one workflow, job, or user session. You can then scan or search the list and see the same run id repeated; MiniObserve does not collapse rows into a single grouped row, but the shared id ties them together.

### Structured logs (optional)

For JSON `LogEntry` payloads you can also set top-level **`run_id`**, **`span_name`**, and **`parent_span_id`** on the log row (separate from `X-MiniObserve-*`). See **[README.md](README.md)** for the full schema.

### `span_name` conventions (recommended)

Use **`span_name`** as a short semantic label so the **Runs** tab timeline and waterfall stay readable:

| Value (examples) | Meaning |
|------------------|---------|
| `llm_call` | Chat completion / generation step |
| `retrieval` | RAG / vector / search |
| `tool_call` | Tool or function execution |
| `router` | Routing / planning |
| `embedding` | Embedding-only call |

These are not enforced; any string works. The dashboard uses them for step titles and subtle color hints.

### Optional hierarchy: `parent_span_id`

Set **`parent_span_id`** to the numeric **`id`** of another stored log row (same app) to mark a **child** step under a parent. The Runs timeline shows children **indented** under their parent when both rows are loaded for the same run.

## 5. CLI (optional)

Configure the same base URL (and key) your app uses — e.g. set `MINIOBSERVE_URL` and `MINIOBSERVE_API_KEY` to match the managed service, then: `miniobserve logs`, `miniobserve stats`, `miniobserve dashboard`.

---

Self-hosting the server, env vars, and raw HTTP examples: **[README.md](README.md)**.
