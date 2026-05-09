---
name: backend-api-testing
description: Definitive guide for end-to-end backend API testing at the HTTP boundary using Python + pytest + httpx / FastAPI TestClient. BDD principles applied without Behave/Cucumber/Gherkin — tests are plain pytest functions. Agent-edition: scenarios held in working memory, completion gates instead of human ceremony. Covers happy path, validation failures, auth failures, not-found, conflict, server errors, response-schema validation, and side-effect verification (DB, events). Use when writing, fixing, or auditing backend API/integration tests, asks "how do I test this endpoint end-to-end", "FastAPI TestClient setup", "API contract test for X", or works on the backend integration suite.
---

# Backend API Testing Blueprint (Agent Edition)

> The definitive guide for how any backend API — new, modified, or bugfixed — should be tested end-to-end at the HTTP boundary. Primary stack: **Python + pytest + httpx / FastAPI TestClient**. No Behave, Cucumber, or Gherkin — BDD *principles* are applied, but tests are plain pytest functions.
>
> This document is written for an autonomous coding agent. It assumes the agent holds scenarios, intermediate reasoning, and progress in working memory rather than externalising them to PR descriptions or scratch files.

---

## 1. Purpose — What API Tests Are For

API tests verify **behavior at the HTTP boundary**. They exercise routing, validation, auth, persistence, serialization, and response contracts — the things unit tests intentionally skip.

Goals, in priority order:

1. **Reduce fear of change.** A green API suite means the contract still holds, even if internals were rewritten.
2. **Capture regression coverage automatically.** Anything that would otherwise require a human tester to click through once should be captured as an automated scenario.
3. **Document the API.** A well-named scenario reads like a spec. It tells the reader what the endpoint *does*, not what it *is*.
4. **Catch integration bugs** — the kind unit tests can't see because each unit passes in isolation.
5. **Verify workflows.** Multi-step orchestration (`create → update → publish`) lives here, not in unit tests.

### What API tests are NOT for

- **Behavior inside a single pure function.** That's a unit test.
- **Load / performance testing.** Separate tooling.
- **UI behavior.** Separate tooling (E2E / Playwright).
- **Verifying every branch of every validator.** Those are unit tests on the validator itself. The API test verifies that the validator is *wired up* — one happy path + one failure path is usually enough per validator.

---

## 2. Applying BDD Principles Without a BDD Framework

Borrow the **thinking model** from BDD and drop the tooling:

| BDD principle | How the agent applies it in pytest |
|---------------|---------------------------|
| Scenarios written as user-observable behavior | Test function names describe what the *caller* achieves, not what the endpoint is called |
| Scenarios enumerated before implementation | Agent reasons through the full scenario set in-context before writing the first `test_` function. No externalisation required — the scenarios live in the agent's working memory until they are translated directly into tests. |
| Declarative, not imperative | Test body reads as "Given X, when I call the API, I expect Y" — not "set var, call helper, mock Z, assert internal state" |
| Edge cases come from adopting a QA mindset | Agent deliberately enumerates failure modes, auth/scope paths, idempotency, concurrency, and multi-use-case branches before coding |
| One dataset can back multiple assertions | Use fixtures + parametrization to share setup across related scenarios |
| Scenarios run against a real app surface | Tests hit the full FastAPI app via `TestClient` or `httpx.AsyncClient` — no mocking of handlers |

**What we don't do:**

- No `.feature` files. No Gherkin. No step definition registries.
- No separate "QA authored" vs "dev authored" code paths. One pytest suite.
- No product-facing DSL.

---

## 3. The Process

For any new endpoint, endpoint change, or API bug fix:

1. **Think through the full scenario set first.** Before touching test code, enumerate every behavior the endpoint must exhibit. Hold this list in working memory. It must cover happy paths, validation failures, auth, scopes, idempotency/concurrency (if claimed), and distinct use cases when one endpoint serves multiple. Example shape:
   ```
   POST /invoices
   - creates an invoice for a valid customer with line items → 201 + invoice payload
   - rejects a missing customer_id → 400 + field error
   - rejects line items with negative quantity → 400 + field error
   - requires authentication → 401
   - requires the "billing:write" scope → 403
   - is idempotent on the idempotency-key header → second call returns the first response, does not double-insert
   - persists the invoice with today's created_at in UTC
   ```
2. **Translate each scenario into one `test_` function.** Name it after the scenario.
3. **Red → Green → Refactor.** Run before writing the endpoint; watch it fail; implement; watch it pass.
4. **When the same endpoint serves multiple use cases, treat each as a separate behavior.** Example: a generic `/assets` endpoint serving both `video` and `document` types needs distinct test scenarios for each — do not rely on the video tests to cover documents.
5. **Completion criteria.** Every enumerated scenario has a test. All tests pass. No scenario was silently dropped during implementation. Before declaring the task done, cross-check the enumerated list against the implemented tests.

### For bug fixes

1. **Reproduce the bug as a failing API test.** Same rule as unit tests.
2. Fix the code.
3. Watch it pass.
4. The scenario stays as a regression.

### For modifications to an existing endpoint

- Run the existing API tests → must be green.
- If the endpoint has no API tests → **enumerate and write scenarios for existing behavior before changing anything**.
- Change the code.
- Add scenarios for the new behavior; update any scenarios whose *contract* genuinely changed. A test that breaks from a pure internal refactor was testing the wrong thing.

---

## 4. Test Conventions

### Naming: behavior-first, scenario-shaped

Each test function is one scenario. The name reads like an English sentence about what the API does.

```python
# GOOD — reads like a scenario
class TestCreateInvoice:
    def test_creates_invoice_for_valid_customer_with_line_items(self, client): ...
    def test_rejects_missing_customer_id_with_400(self, client): ...
    def test_returns_cached_response_when_idempotency_key_repeats(self, client): ...
    def test_requires_authentication(self, client): ...
    def test_requires_billing_write_scope(self, client): ...

# BAD — describes endpoint mechanics
class TestInvoicePost:
    def test_post(self, client): ...
    def test_post_400(self, client): ...
    def test_post_auth(self, client): ...
```

### Structure: Given-When-Then as comments (or blank-line blocks)

```python
def test_creates_invoice_for_valid_customer_with_line_items(client, billing_token):
    # Given — a customer exists and we have line items
    customer = fixtures.create_customer(tier="gold")
    payload = {
        "customer_id": customer.id,
        "line_items": [{"sku": "SKU-1", "quantity": 2, "unit_price": "50.00"}],
    }

    # When — we call the API
    response = client.post(
        "/invoices",
        json=payload,
        headers={"Authorization": f"Bearer {billing_token}"},
    )

    # Then — the invoice is created and returned
    assert response.status_code == 201
    body = response.json()
    assert body["customer_id"] == customer.id
    assert body["total"] == "100.00"
    assert body["status"] == "draft"
```

### Declarative, not imperative

A scenario body says **what** is being verified, not **how** every collaborator is wired.

```python
# GOOD — declarative
def test_publishing_an_invoice_notifies_the_customer(client, billing_token, spy_email):
    invoice = fixtures.create_invoice(status="draft")

    response = client.post(
        f"/invoices/{invoice.id}/publish",
        headers={"Authorization": f"Bearer {billing_token}"},
    )

    assert response.status_code == 200
    assert spy_email.sent_to(invoice.customer.email, subject_contains="Invoice")

# BAD — imperative / implementation-centric
def test_publish(client, mock_email_client, mock_pdf_renderer, mock_audit_log):
    mock_pdf_renderer.return_value = b"..."
    mock_email_client.send.return_value = {"id": "m-1"}
    ...
    assert mock_email_client.send.call_count == 1
    assert mock_pdf_renderer.render.call_args[0][0].id == "inv-1"
```

### One dataset backs multiple scenarios where possible

If the same fixture row covers multiple assertions, reuse it via a pytest fixture. Don't duplicate setup across five tests just to re-verify a separate aspect each time.

```python
@pytest.fixture
def published_invoice(client, billing_token):
    inv = fixtures.create_invoice(status="draft")
    client.post(f"/invoices/{inv.id}/publish", headers=auth(billing_token))
    return inv

def test_published_invoice_has_published_at_timestamp(published_invoice): ...
def test_published_invoice_cannot_be_edited(client, published_invoice, billing_token): ...
def test_published_invoice_is_listed_in_outbound(client, published_invoice, billing_token): ...
```

### Parametrize for families of similar scenarios

Validation edge cases are a natural fit:

```python
@pytest.mark.parametrize(
    "bad_payload,expected_field,expected_error",
    [
        ({"customer_id": None}, "customer_id", "required"),
        ({"line_items": []}, "line_items", "min_length"),
        ({"line_items": [{"quantity": -1}]}, "line_items.0.quantity", "positive"),
    ],
)
def test_rejects_invalid_payload(client, billing_token, bad_payload, expected_field, expected_error):
    response = client.post("/invoices", json=bad_payload, headers=auth(billing_token))
    assert response.status_code == 400
    assert_field_error(response, expected_field, expected_error)
```

Do **not** hide scenarios with genuinely different intent behind one parametrized test. If "rejects missing customer" and "rejects unauthorised user" are different behaviors, they get different tests.

---

## 5. Project Structure

```
tests/
  api/
    conftest.py              # app fixture, auth fixtures, spy servers
    fixtures/
      customers.py           # factory helpers (create_customer, etc.)
      invoices.py
    invoices/
      test_create_invoice.py
      test_publish_invoice.py
      test_list_invoices.py
    auth/
      test_login.py
      test_scopes.py
  unit/                      # unit tests (see backend-unit-testing-blueprint.md)
```

Mirror the route or resource hierarchy — one directory per resource, one file per endpoint (or per high-level behavior if an endpoint is large).

### Core fixtures (`tests/api/conftest.py`)

- **`app`** — the FastAPI instance with test configuration applied (in-memory DB, fake external adapters).
- **`client`** — `TestClient(app)` or `httpx.AsyncClient(app=app)`.
- **`db`** — an in-memory or truncated-between-tests database handle.
- **Auth fixtures** — `billing_token`, `admin_token`, etc., returning valid JWTs signed by the test signing key.
- **Spy fixtures** — `spy_email`, `spy_queue`, `spy_external_api` — see §6.

Every test starts from a clean database. Use a fixture with `autouse=True` at the package level that truncates / resets state before each test.

---

## 6. Handling External Dependencies

API tests must exercise the **real** application handlers, routes, middleware, and database. They must **not** hit real external services.

### Spy Server pattern

For any outbound call — email provider, payment gateway, third-party SDK, webhook delivery — wrap the dependency behind a thin adapter in production code (see the Unit Testing Blueprint §7 *Infrastructure wrappers*). In tests, swap the adapter for a **spy**: a real implementation that records every call and returns a canned response.

```python
class SpyEmailAdapter:
    def __init__(self):
        self.sent: list[SentEmail] = []

    def send(self, to: str, subject: str, body: str) -> str:
        self.sent.append(SentEmail(to=to, subject=subject, body=body))
        return f"msg-{len(self.sent)}"

    def sent_to(self, address: str, subject_contains: str | None = None) -> bool:
        return any(
            m.to == address and (subject_contains is None or subject_contains in m.subject)
            for m in self.sent
        )
```

- **Spies over mocks.** Spies are real classes with real signatures — they record and return, they don't pretend.
- **Asserted at the boundary, not the implementation.** Tests ask "did we send an email to this customer?", not "was `EmailClient.send` called with args X, Y, Z?".
- **Reset per test.** Each test gets a fresh spy. No shared state.

### Database

- **In-memory or containerised Postgres** — one per test session, truncated between tests.
- Never a shared dev or staging DB.
- A transactional rollback strategy (wrap each test in a transaction, roll back at teardown) is acceptable as long as parallelism is accounted for. Prefer truncation for simplicity.

### Time, randomness, IDs

- Freeze time with a fixture (`freezegun` or a clock adapter) when assertions depend on timestamps.
- Seed random / UUID generators if any assertion depends on their values. Otherwise, assert shape / format, not exact values.

---

## 7. What to Assert

Assert the **contract**, not the internals.

| Do assert | Don't assert |
|-----------|-------------|
| HTTP status code | Which handler function was called |
| Response body shape and values | That a specific method on a service was invoked |
| Response headers relevant to the contract (`Location`, `ETag`, cache headers) | SQL that was executed |
| Persistent side effects observable via another API call (`GET` after `POST`) | Internal state of a service object |
| Outbound side effects via spies (email sent, webhook enqueued) | Call counts on internal helpers |
| Error codes and error field paths | Log messages (unless the log is itself the contract) |

### Round-trip assertions

When possible, verify a write by reading it back through the API:

```python
def test_created_invoice_is_retrievable(client, billing_token):
    created = client.post("/invoices", json=valid_payload, headers=auth(billing_token)).json()

    fetched = client.get(f"/invoices/{created['id']}", headers=auth(billing_token)).json()

    assert fetched == created
```

This keeps the test honest about the public contract and avoids reaching into the DB.

---

## 8. Running Tests

### Commands

```
pytest tests/api                       # full API suite
pytest tests/api/invoices              # one resource
pytest tests/api -k "idempotency"      # filter
pytest tests/api -x                    # stop on first failure
pytest tests/api -n auto               # parallel (pytest-xdist)
pytest tests/api --cov=src             # coverage
```

### When to run

- **While iterating on a specific endpoint**: `pytest tests/api/<resource>` — fast feedback loop.
- **Before declaring the task complete**: full `tests/api` suite + `tests/unit`. A passing targeted run is not sufficient evidence that the change is safe.
- **CI**: full suite with coverage, parallelised.

### Completion gate

Before handing work back:

- All API tests pass.
- Coverage threshold holds (see §9).
- The enumerated scenario set from §3 has exactly one corresponding test per scenario.

---

## 9. Coverage

API tests contribute to coverage too, but coverage here is a **scenario-completeness proxy**, not a line-count proxy.

- Re-verify scenario completeness explicitly before concluding work — walk the enumerated list against the test functions present in the file.
- Line coverage on route handlers, controllers, and serializers should be close to 100% — there's little code there that isn't exercised by a real request.
- Coverage on Models / Helpers / Validators should come primarily from unit tests, not API tests.

---

## 10. What NOT to Do

1. **Don't mock internal collaborators.** If you need to mock the service your controller calls to test the controller, your scenario coverage is wrong — the API test should exercise the real service stack against an in-memory DB.
2. **Don't assert on internal method calls.** Contract only.
3. **Don't hit external services** (email, payment, third-party APIs). Use spy adapters.
4. **Don't share state between tests.** Reset the DB and all spies per test.
5. **Don't duplicate unit-test work** in API tests. Validator edge cases are unit tests; the API test verifies the validator is *wired up* with one success + one failure.
6. **Don't test UI or workflow orchestration across services here.** That's E2E.
7. **Don't skip the scenario-enumeration phase.** Jumping straight to code produces whatever happened to be implemented, not what was required. Enumerate first, code second — even if the enumeration lives only in working memory.
8. **Don't treat "endpoint serves multiple use cases" as "one set of tests covers all".** Each use case is a distinct set of scenarios.
9. **Don't assert on log lines or internal metrics** unless the log / metric is itself a documented product contract.
10. **Don't rely on test execution order.** Each test must pass standalone and in any order.

---

## 11. Pre-Completion Checklist

Before declaring any change that adds or modifies an API endpoint as done, the agent must self-verify:

- [ ] Scenarios were enumerated before implementation; every enumerated scenario maps to a `test_` function named to read like the scenario.
- [ ] Tests use the real app stack — no handler-level mocking.
- [ ] External dependencies go through spy adapters; no network calls leave the test process.
- [ ] Database resets between tests; no shared state.
- [ ] Validation edge cases are covered (happy path + key failure paths per validator, not exhaustively — those are unit tests).
- [ ] Auth / scope scenarios are covered for every non-public endpoint.
- [ ] Idempotency / concurrency scenarios are covered where the endpoint claims to support them.
- [ ] Multi-use endpoints have distinct scenarios per use case.
- [ ] Bug fixes include a regression scenario that failed before the fix.
- [ ] `pytest tests/api` passes in full (not just the targeted subset).

---

## 12. Pending Approval — Industry Practices Not Yet Adopted

The following are common practices that either conflict with the source principles or extend significantly beyond them. **They are NOT currently part of this blueprint.** Each requires explicit sign-off before being adopted.

| # | Practice | Status | Note |
|---|----------|--------|------|
| 1 | **Behave / Cucumber / Gherkin `.feature` files** | Not adopted | Source doc referenced Behave, but the team has dropped the tooling. BDD principles are applied via plain pytest. |
| 2 | **Contract testing** (Pact, Spring Cloud Contract) | Not adopted | Useful when multiple services consume the API; adds schema / broker infrastructure. Worth revisiting when we have external consumers. |
| 3 | **Schema-based assertion** (OpenAPI / JSON Schema validation in every test) | Not adopted | Adds value when the spec is source-of-truth; adds boilerplate otherwise. |
| 4 | **Snapshot testing for response bodies** | Not adopted | Encourages "accept whatever the code returns" rather than explicit assertions. Conflicts with declarative scenario philosophy. |
| 5 | **VCR.py / request recording and replay** | Not adopted | Record-replay masks behavior drift in external services. Spy adapters are preferred. |
| 6 | **TestContainers for real Postgres** in API tests | Partially considered | Acceptable as the database layer, but requires buy-in on run time and CI resources. In-memory Postgres (or SQLite for simple cases) is the current default. |
| 7 | **Load / performance testing frameworks** (Locust, k6) | Not adopted | Separate concern — out of scope for this document. |
| 8 | **Property-based API testing** (Hypothesis with Schemathesis) | Not adopted | Fuzzes the API against its OpenAPI spec. Powerful but introduces non-determinism into CI. |
| 9 | **Mutation testing on API code paths** | Not adopted | Same concern as unit-level mutation testing — conflicts with "don't chase metrics" stance. |
| 10 | **Dedicated QA-authored scenarios in a separate DSL** | Not adopted | Source doc originally proposed QA writing Gherkin scenarios that devs implement. Without Behave we collapse this into a single enumeration phase held in the agent's working memory and translated directly into pytest functions. |

To adopt any of these, the human owner must explicitly approve and this document will be updated.
