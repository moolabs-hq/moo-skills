# SDK is blocking by design — why Option B for v1

Per `cost-billing-shared/sdk-surface-reference.md` and Doc 3 §6.1, the Moolabs SDK transport is synchronous. There is no async variant in v1.

| SDK | Transport | Median round-trip |
|---|---|---|
| `moolabs` (Python) | `urllib3.PoolManager` | ~35ms |
| `moolabs` (TypeScript / Node) | `fetch` | ~35ms |
| `moolabs` (Go) | `net/http` | ~35ms |

## The choice for v1 (per `v1-decisions-log.md` #4)

**Option B — blocking insert + PR documents the latency.**

The codemod inserts a synchronous call inline (v0.3.0-rc1 ergonomic method):

```python
client.usage.ingest_event(args)
```

It does NOT background-wrap (Option A). The PR description includes:

> The Moolabs SDK is blocking by design (~35ms typical round-trip). This codemod chose Option B (blocking + documented) per the v1 default. Hot-path callers experiencing p99 latency concerns can replace with a background-wrap pattern (e.g., `asyncio.create_task(...)` or `setTimeout(() => ..., 0)`). The codemod will NOT background-wrap automatically.

## Why blocking by default

1. **Failure visibility.** A blocking call that fails throws; the customer's logging captures it. A background call's failure is silent unless instrumented.
2. **Customer ergonomics.** The customer's engineer can swap to background-wrap on a per-handler basis, where they know the trade-offs. The codemod can't make that decision per handler without per-call latency budget data the customer hasn't provided.
3. **Code review clarity.** A blocking call is one line. A background-wrapped call needs a fire-and-forget pattern that varies by framework — harder to review consistently.
4. **Conservatism.** It's easier to convert a blocking call to background later than the reverse. Background-by-default would hide failures during the first weeks of integration when failures matter most.

## When to override (`/cost-billing-instrument --pattern usage-only --background`)

If the customer has explicitly told you "hot paths must be non-blocking", pass `--background`. The codemod emits the per-framework background pattern:

| Framework | Pattern |
|---|---|
| FastAPI / Starlette | `asyncio.create_task(client.usage.ingest_event(args))` (handler must be `async def`) |
| Django (sync) | `threading.Thread(target=..., daemon=True).start()` |
| Django (async) | Same as FastAPI |
| Flask | `concurrent.futures.ThreadPoolExecutor().submit(...)` |
| Express | `setImmediate(() => client.usage.ingestEvent(args))` |
| NestJS | Inject a `Logger`-style provider that uses `setImmediate` internally |

**Caveat:** background-wrap means a process exit could lose the in-flight events. For production, recommend flushing on shutdown (e.g., FastAPI `lifespan`). The codemod does NOT emit shutdown-flush — it's customer-policy.

## Revisit trigger

Per `v1-decisions-log.md` #4, this decision revisits if 3+ customers report needing async by default. The reviewer model (`/cost-billing-adversarial-review`) does not flag blocking inserts as a concern in v1.
