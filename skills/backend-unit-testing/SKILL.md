---
name: backend-unit-testing
description: Definitive guide for backend unit testing across any backend language, with Python + pytest as the primary stack. Agent-edition: enumerate testable units in working memory rather than externalising; scoped tests during iteration, full suite before completion; mock external dependencies (DB, external APIs) but not internal logic; if a test needs >3 mocks the code likely needs refactoring; cover happy/empty/null/boundary/invalid/error paths; do not unit-test thin controllers or DAO proxies — those go to integration. Use when writing, fixing, or auditing backend unit tests, asks "how should I unit test this service", "do I need pytest for this", "mocking strategy for X", or works on the backend test suite.
---

# Backend Unit Testing Blueprint (Agent Edition)

> The definitive guide for how any backend feature, enhancement, or bug fix should approach unit testing. Primary stack: **Python + pytest** (examples below). The principles apply equally to Go, Java, Kotlin, or any other backend language — adapt the mechanics, keep the rules.
>
> This document is written for an autonomous coding agent. It assumes the agent holds behavior enumerations, intermediate reasoning, and progress in working memory rather than externalising them to PR descriptions or scratch files. Human-oriented ceremony (local vs CI, pre-commit hooks, "before opening PR") is reframed in terms of the agent's own completion gate.

---

## 1. Purpose — What Unit Tests Are For

Unit tests exist to serve these goals, in priority order:

1. **Aid debugging.** Find the exact line where a bug lives — not "somewhere in the system". Replace print-statement debugging with a failing test.
2. **Enforce better structure.** If code is hard to test, the code is wrong. Tests are a design pressure, not a quality gate bolted on at the end.
3. **Make the code future-proof.** Changes should feel safe. A green suite is the safety net for every refactor.
4. **Aid refactoring.** The same pinpoint debuggability during a rewrite, six months later.
5. **Catch edge cases the author missed.** Writing tests forces the author to enumerate inputs the happy path ignores.
6. **Write more complete specs.** TDD surfaces unanswered product questions before any code is written.
7. **Point to code smells.** "This is hard to test" ≈ "this code is badly structured".

### What unit tests are NOT for

- **Chasing a code coverage number.**  Never write a test to bump a number.
- **Testing workflows or stateful chains.** `A → B → C` flows, multi-module orchestration, channel-level delivery — that's integration / API test territory.
- **Finding behavioral bugs.** If the developer's assumption was wrong, unit tests built on that assumption will pass anyway. Manual QA owns this.

---

## 2. What's a "Unit"?

A unit is a **behavior**, not an API endpoint or a class method.

> "I need to add amounts in two different currencies and get a result, given exchange rates." → behavior
> "I need a `convertCurrency` method on the `Money` class." → implementation detail

Both produce the same code. Only the first is TDD. The test is the first consumer of the code — start from what the caller wants, not what the class is called.

A unit can be:

- A single function (functional style).
- A method, class, module, or package (OO style — whichever is the smallest boundary that has meaningful behavior).
- In Go, each package is self-contained and testable. In Python, think module + its public callables.

---

## 3. What to Test / What Not to Test

> **AI-era recalibration.** Much of the original philosophy was calibrated to the cost of a human typing tests. Historically, many "don't test" rules were really "not worth the minutes". That calculus no longer holds — agents write tests in seconds, so any real observable behavior is fair game. Going forward, the "don't test" bar is justified by one of three things only: **brittleness** (the test couples to implementation and breaks on refactors), **noise** (nothing meaningful to assert), or **duplication** (the same behavior is already covered at a better-layered test). Never skip a test because "it would take too long". Every "DO NOT" below is tagged with the reason it still holds.

### MUST unit test (has business logic or transformations)

- **Helpers, validators, mappers, serializers** — every exposed function.
- **Model layer** — domain logic, state transitions, invariants. The heart of the test suite.
- **Pure functions** — zero collaborators needed. The highest-ROI tests.
- **Adapters / ports implementations** — their contract with the port.
- **Controller-layer transformations** — request → domain mapping, response shaping, conditional routing based on the request shape, header/cookie parsing. Previously skipped as "thin orchestration"; in the AI era, extract or test in place.
- **DAO-layer transformations** — row → domain object, domain → insert payload, query filter composition, JSON / JSONB column serialization, pagination math, cursor encoding. Previously skipped for human-cost reasons; now write them. Test the transforms as pure functions where possible, or against an in-memory DB when a session is required.
- **Application / orchestration glue that contains logic** — small state machines, conditional dispatch, retry policy, fan-out/fan-in shaping. If there's branching or transformation, test it.

### DO NOT unit test

- **Pure pass-through controllers** — `parse → call model → return result` with no transformation. *(Reason: noise + duplication — API tests cover the wire, the model test covers the logic.)*
- **Pure pass-through DAO methods** — a single SQL call with no transformation on the way in or out. *(Reason: noise + duplication. A transform that parses a JSONB column or encodes a cursor is NOT pass-through and should be tested per the MUST list above.)*
- **Code with zero business logic** — pass-through DTOs, constants, `__init__` glue, barrel exports. *(Reason: noise.)*
- **Private / internal functions by default** — see §5.4. *(Reason: brittleness. This is the one area where AI-era cost doesn't rescue the case, because the failure mode is churn-in-every-refactor-PR, not effort.)*
- **The channel itself** — don't assert that `A` calls `B` calls `C`. Test `A`'s output and `C`'s behavior given `C`'s input. The glue is integration-test territory. *(Reason: brittleness + duplication.)*
- **Real databases** — speed and cross-test contamination. Use in-memory or fakes. *(Reason: noise + parallelism.)*

### Decision flowchart

```
Is there business logic, transformation, branching, or a state machine?
  ├── YES → Is it in a pure function (no I/O, no DB)?
  │     ├── YES → Unit test it. Zero mocks.
  │     └── NO → What layer is it in?
  │           ├── Model           → Unit test with a fake / in-memory DAO injected.
  │           ├── Helper/Validator/Mapper → Unit test the public function.
  │           ├── Controller      → Test the transformation (extract to a mapper if possible,
  │           │                      or test in place with a fake Model).
  │           ├── DAO             → Test the transform (row↔domain, query composition,
  │           │                      JSON parse) as a pure function OR against in-memory DB.
  │           └── App/orchestration → Test the branching/state logic with fakes for collaborators.
  └── NO → Is this pure pass-through (no transformation, no branching)?
            ├── YES → Skip. The layered tests above cover it.
            └── NO  → Re-read the YES branch — you probably do have logic to test.
```

---

## 4. TDD Process

### Red → Green → Refactor

1. **State the requirement as a falsifiable sentence.** e.g. "`double(x)` returns `x * 2`."
2. **Write the test.** Describe the behavior in the `it` / test name.
3. **Run it and watch it fail (Red).** Proves it isn't a false positive.
4. **Write the minimum code to pass (Green).** Resist over-engineering.
5. **Refactor.** The green suite is your safety net.

### Practical loop

```
1. Parse the task. Enumerate the units that will contain business logic (hold in working memory, not scratch files):
   - New helper / validator / mapper?  → plan its test file.
   - New model method?                 → plan its test file.
   - New state transition?             → plan its test file.
2. Write behavior specs FIRST (test names + assertions, no impl).
3. Run — everything red.
4. Implement.
5. Run — everything green.
6. Refactor. Re-run. Hand back.
```

### For bug fixes

1. **Reproduce the bug as a failing test.** This is the regression guard.
2. Fix the code.
3. Watch it pass.
4. The test stays forever.

### For modifications to existing code

> "Never touch a piece of code without first thinking about tests."

1. File has tests → run them, confirm green.
2. File has no tests → **write tests for existing behavior before modifying**. This pins down what's already there.
3. Make the change.
4. Update / add tests for new behavior.

### Non-negotiables

- **Never leave a piece of code with a broken test.** Ever.
- **"It would take too long" is not a reason to skip a test.** Generation cost is effectively zero — the only reasons to skip are brittleness, noise, or duplication (see §3).
- When tempted to instrument a running system with print statements or ad-hoc scripts to diagnose behavior, **write a unit test instead.** If the test exists only to pin a private detail you were debugging, delete it after the diagnosis — don't leave brittle private tests behind for future maintainers.

---

## 5. Core Rules

### 5.1 Test the behavior, not the implementation

- As long as the **signature stays the same, tests should not need to change.** If a refactor breaks a test without changing behavior, the test was coupled to implementation — that's the bug.
- Describe packages by behavior, not structure: `pricing`, not `price_utils_v2`.

### 5.2 Pure functions are the north star

- Black-box: input → output. No side effects.
- **Never mutate a data structure passed as a parameter.** If you need a modified version, copy internally. Callers that rely on side-effects-on-args are the #1 source of test fragility — and the #1 reason tests "randomly" break when unrelated code changes.
- If a test needs to inspect a passed-in DS after the call to confirm behavior, the function is impure. Refactor.

### 5.3 Don't pass request/response objects below the Controller layer

- Controller receives the HTTP request → **parses it into a domain input** → passes the domain input to the Model.
- The Model must not know about `FastAPI Request`, `Flask request`, `http.Request`, or any transport artifact.
- If the Model signature depends on the shape of an incoming HTTP body, every upstream change (new field, renamed field, middleware rewrite) cascades into every model test. This is the most common cause of "suite goes for a toss" rewrites.
- The cure is an explicit domain model at the Controller/Model boundary. Tests of the Model don't care what HTTP looked like.

### 5.4 Don't test private functions (usually)

- **Rule:** test through the public API.
- **Why:** tests on private functions couple to implementation and churn on every refactor. In the AI era, the *effort* argument against them is gone — but the brittleness argument is unchanged. A test that forces a PR to rewrite five private-function tests every time internals move is still noise in review, even if an agent did the rewriting.
- **Preferred escape hatch:** if a private function contains meaningful logic you'd refactor to a public helper anyway, **refactor it to a public helper** in its own module and test that. Public helpers are cheap.
- **Acceptable fallback:** if extracting would be disproportionate churn, or the logic is genuinely internal and unlikely to be refactored, go ahead and test the private function. Mark it clearly as a debugging aid and ensure the same behavior is also reachable via a public-API test somewhere.
- **Do not** write private-function tests as your default path to coverage — refactor first.

### 5.5 Test isolation — tests run in parallel, no shared state

- **The unit of isolation is the test, not the system under test.**
- Any two tests must be runnable in any order, concurrently, with no cross-talk.
- The reason for keeping real DBs out of unit tests is **not** to "isolate the module from the DB". It is:
  1. Cross-test contamination — one test's writes leak into another's reads.
  2. Speed — a real DB is orders of magnitude slower than an in-memory fake.
- In-memory DB implementations (or fakes) fix both.

### 5.6 Loose coupling

- Pub-sub / queue / channel-style: test the **receiver** against a known input, and the **publisher** against a known expected output. Do not test the channel itself — Kafka, RMQ, Go channels, asyncio.Queue are all tested upstream.

---

## 6. Mocking Policy

> "Mocking is a code smell."

### Hierarchy (prefer top, avoid bottom)

1. **No mocks.** Test pure functions directly. Gold standard.
2. **Dependency injection (DI).** Pass collaborators as constructor params or function args. Tests pass in a fake, prod passes in the real thing.
3. **Nullable infrastructure — `createNull()` / `Fake` classes.** Program each infra class with a factory method that returns a variant disabled from talking to the external world. `LoginClient.createNull().get_user_info("...")` returns a canned value. Tests get a real instance of the real class, with real methods, real signatures — just disconnected from the network. This is **not** a mock; it's a sibling implementation.
4. **In-memory / fake DB.** For the Model layer. Swap the DAO with a fake that implements the same interface and stores rows in a dict. Test the Model's logic against it. (DAO's own tests can use the same fake or — only if truly necessary — a real in-memory DB like SQLite.)
5. **Framework mocks (`unittest.mock.patch`, `monkeypatch`) as last resort.** Only for truly external surfaces you cannot control (system clock, random, subprocess, third-party SDK with no seam). Do **not** use them to mock internal collaborators.

### Heuristic

> If a test needs more than 3 mocks, the code under test needs refactoring.

Refactor options:
- Extract pure logic into helpers (testable without mocks).
- Accept dependencies as constructor params, not imported globals.
- Introduce a port (interface) and an adapter (implementation). The port gets a fake in tests.

### Dependency ownership

- **Most objects should construct their own dependencies by default** and accept overrides as optional constructor params. Do not pass dependencies around through every function signature — that pollutes every caller and every test.
- Tests override dependencies at construction: `PricingModel(dao=FakePricingDao())`.

---

## 7. Layer-by-Layer Testing

Canonical layering: **Controller → Model → DAO**, with **Helpers, Validators, Mappers** as cross-cutting pure modules.

### Helpers, Validators, Mappers

- **Every exposed function gets a test.** These are pure. Zero mocks.
- Highest-ROI tests in the codebase.

```python
# pricing/helpers.py
def apply_discount(amount: Decimal, percent: Decimal) -> Decimal:
    ...

# tests/pricing/test_helpers.py
def test_apply_discount_standard_case():
    assert apply_discount(Decimal("100"), Decimal("10")) == Decimal("90")

def test_apply_discount_rejects_negative_percent():
    with pytest.raises(ValueError):
        apply_discount(Decimal("100"), Decimal("-5"))
```

### Controller layer

- **Pure pass-through controllers** (parse → call model → return result, no transformation) → no unit test. API tests cover the wire; model tests cover the logic.
- **Controllers with transformation** — request → domain mapping, response shaping, header / cookie parsing, conditional routing based on the request shape, multi-model composition with branching — **do get unit tests** on that transformation logic. Preferred shape:
  1. Extract the transformation into a mapper / helper module and unit-test that module (best — tested as a pure function, zero mocks).
  2. If extraction is disproportionate churn, test the controller in place with a fake Model injected.
- **Every Model call from a controller should have a corresponding Model unit test** somewhere in the suite.

### Model layer

- **The centerpiece.** Exhaustively tested.
- Interacts with ideally **one DAO**. If a model talks to multiple DAOs, examine whether one of them should be collapsed into a helper or moved to another model.
- Inject a **fake DAO** or **in-memory DB** at construction. Assert every behavior, state transition, and error branch.

```python
# pricing/model.py
class PricingModel:
    def __init__(self, dao: PricingDao = None):
        self.dao = dao or PricingDao()

    def calculate_invoice(self, customer_id: str) -> Invoice:
        ...

# tests/pricing/test_model.py
def test_calculate_invoice_applies_loyalty_tier():
    fake_dao = FakePricingDao(customer_tier={"c1": "gold"})
    model = PricingModel(dao=fake_dao)

    invoice = model.calculate_invoice("c1")

    assert invoice.discount_applied == Decimal("15")
```

### DAO layer

- **Pure pass-through DAO methods** (a single SQL call with no transformation in or out) → no unit test. Model tests against a fake DAO cover the callers.
- **DAO transformations — test them.** Historically this layer was skipped because maintaining DAO tests alongside schema changes was a human-time drain. That's no longer true. Write tests for:
  - Row → domain object conversion (especially when denormalising JOINs, null handling, or JSON / JSONB column parsing).
  - Domain → insert / update payload construction (audit fields, defaults, type coercion, timezone handling).
  - Query filter composition — the piece that turns `{tier: "gold", active: True}` into a WHERE clause.
  - Pagination math — cursor encoding/decoding, page-size bounds, offset safety.
  - Bulk-operation chunking / batching logic.
- **Preferred shape:** pull the transformation into a pure function (`dao/transforms.py`) and unit-test as pure input → output. Zero mocks, zero DB.
- **Fallback:** if the transformation requires an ORM session or a driver-specific feature, test against an **in-memory DB** (SQLite for simple cases, in-memory Postgres fake where fidelity matters). **Never a real network DB** — parallelism and cross-test contamination are the reason, not effort.
- **Still don't** assert on the literal SQL string emitted. Assert on the result shape or on rows read back from the in-memory DB.

### Infrastructure wrappers

- **Wrap external libraries / SDKs** in your own thin adapter class. This lets you:
  - Swap the adapter with `createNull()` in tests.
  - Limit the blast radius when the external API changes.
- Optional but strongly recommended for anything talking to a third party (payment gateway, auth provider, search index, queue, cache, LLM provider).

### A-Frame architecture (reference: James Shore, "Testing Without Mocks")

- The **Application** layer wires together the **Logic** layer and the **Infrastructure** layer. It receives return values from one and passes them to the other.
- **Application-layer plumbing does not need unit tests** — each side has its own.
- **Logic layer** is pure, heavily tested with zero mocks.
- **Infrastructure layer** is tested via nullable / `createNull()` factories.
- Start with a skeletal end-to-end implementation, hardcoding any infra values, and factor concepts into Logic classes as the Application layer grows messy.

---

## 8. Test Conventions

### File layout

Mirror `src/` under `tests/`. Colocate test helpers next to the tests that use them.

```
src/pricing/
  helpers.py
  model.py
  dao.py

tests/pricing/
  test_helpers.py
  test_model.py
  conftest.py            # shared fixtures for this package
  builders.py            # fixture builders scoped to pricing tests
```

For small projects, colocated `__tests__/` folders next to the source (as in the frontend convention) are also fine — pick one and be consistent.

### Test naming: behavior-first

Name the test after **what the unit achieves**, not the function it calls.

```python
# GOOD — describes behavior
class TestCurrencyConversion:
    def test_converts_usd_to_eur_at_given_rate(self): ...
    def test_raises_when_rate_is_missing_for_target_currency(self): ...
    def test_preserves_precision_to_two_decimal_places(self): ...

# BAD — describes implementation
class TestConvertFunction:
    def test_convert_returns_float(self): ...
    def test_convert_calls_rate_lookup(self): ...
```

### AAA structure

```python
def test_converts_usd_to_eur_at_given_rate():
    # Arrange
    amount = Money(Decimal("100"), "USD")
    rate = ExchangeRate("USD", "EUR", Decimal("0.85"))

    # Act
    result = convert(amount, rate)

    # Assert
    assert result == Money(Decimal("85"), "EUR")
```

### Edge case checklist

For every unit, consciously consider:

- **Happy path** — standard input, expected output.
- **Empty / null input** — `None`, `""`, `[]`, `{}`.
- **Boundary values** — `0`, `-1`, `MAX_INT`, empty string vs whitespace, off-by-one.
- **Invalid input** — wrong types, malformed data.
- **Cardinality** — single vs many (0, 1, N items).
- **Error conditions** — dependency raises, timeout, partial failure.

### Fixture builders

Prefer builder functions over sprawling setup:

```python
def build_customer(**overrides) -> Customer:
    defaults = dict(
        id="c-1",
        tier="standard",
        country="US",
        created_at=datetime(2026, 1, 1),
    )
    return Customer(**{**defaults, **overrides})

def test_gold_tier_gets_discount():
    customer = build_customer(tier="gold")
    ...
```

- Builder used in 1 file → inline.
- Builder used across a package → `tests/<package>/builders.py`.
- Builder used cross-package → `tests/builders/`.

### Parametrization

Use `pytest.mark.parametrize` for behavior that has the same shape across many inputs — it keeps tests honest about what's really being asserted.

```python
@pytest.mark.parametrize(
    "amount,percent,expected",
    [
        (Decimal("100"), Decimal("10"), Decimal("90")),
        (Decimal("100"), Decimal("0"),  Decimal("100")),
        (Decimal("0"),   Decimal("10"), Decimal("0")),
    ],
)
def test_apply_discount(amount, percent, expected):
    assert apply_discount(amount, percent) == expected
```

Do **not** use parametrization to hide three genuinely different behaviors behind one test function. If the assertions diverge, split.

---

## 9. pytest Configuration

### Structure

```
pyproject.toml          # [tool.pytest.ini_options]
tests/
  conftest.py           # repo-wide fixtures
  <package>/
    conftest.py         # package-scoped fixtures
    test_*.py
    builders.py
```

### Recommended settings

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = [
    "-ra",                        # short summary of all outcomes
    "--strict-markers",
    "--strict-config",
    "-p no:cacheprovider",        # CI cleanliness
]
markers = [
    "slow: marks slow tests (deselect with -m 'not slow')",
]
```

### Coverage thresholds (75–80% target)

Coverage is a **guardrail**, not a goal. Include only files that should have business logic; exclude glue.

```toml
[tool.coverage.run]
source = ["src"]
omit = [
    "*/__init__.py",
    "*/types.py",
    "*/constants.py",
    "*/dto/*",
    "*/migrations/*",
]

[tool.coverage.report]
fail_under = 75
show_missing = true
skip_covered = true
```

### Scripts

```
pytest                         # full suite
pytest -x                      # stop at first failure
pytest -k "discount"           # filter by name
pytest tests/pricing           # scope to a package
pytest --cov --cov-report=html # coverage + HTML report
pytest -n auto                 # parallel (requires pytest-xdist)
```

### When the agent runs which command

- **While iterating on a specific unit**: `pytest tests/<package>` — fast feedback loop.
- **Before declaring the task complete**: full `pytest` with coverage. A passing targeted run is not sufficient evidence that the change is safe.
- **CI**: `pytest --cov` with `fail_under=75`.

A scoped pass is a working-loop checkpoint, not a completion gate. Always run the full suite before handing work back.

---

## 10. What NOT to Do

1. **Don't chase coverage numbers.** Code with zero business logic should not be tested to pad the number. (AI-era note: writing more tests is cheap, but empty-assertion tests are still noise in review.)
2. **Don't test pure pass-through controllers** — the parse-call-return ones with no transformation. API tests cover the wire. **But do test controllers that map, shape, or route on the request shape** — extract to a mapper where you can, test in place where you can't.
3. **Don't mock everything.** If you need >3 mocks, refactor.
4. **Don't test implementation details.** Method names, call counts, private attributes — all off-limits.
5. **Don't test thin pass-through wrappers** (no transformation, no branching). A wrapper that transforms, defaults, or branches is not thin — test it.
6. **Don't write tests for the sake of tests.** Zero behavior to assert → skip. The bar is "does this test pin down a real behavior?", not "did writing it take long?".
7. **Prefer refactoring private functions into public helpers over testing them privately.** Test privates only when extraction is disproportionate churn — and mark them as debugging aids.
8. **Don't test the channel.** Test receiver on input; test publisher on output; never the transport in between.
9. **Don't mutate arguments and then assert on them.** That's a design smell surfaced by the test. Fix the code, not the test.
10. **Don't pass HTTP request/response objects below the Controller.** Parse into a domain type first.
11. **Don't hit a real DB.** Use an in-memory implementation or a fake.
12. **Don't share state between tests.** Every test must set up and tear down its own world.
13. **Don't confuse "cheap to write" with "worth writing".** An agent can generate twenty tests in a minute. Twenty tests that assert nothing meaningful still pollute the suite. Every test must pin down a behavior a reader of the code would care about.

---

## 11. Pre-Completion Checklist

Before declaring any change that adds or modifies backend business logic as done, the agent must self-verify:

- [ ] Enumerated which units contain testable behavior — Helpers, Validators, Mappers, Model, **Controller transforms, DAO transforms, orchestration branches**.
- [ ] Pure functions have unit tests (zero mocks).
- [ ] Model methods have tests against a fake / in-memory DAO.
- [ ] Controller mapping / shaping / conditional logic has tests — extracted to a mapper where practical, tested in place otherwise.
- [ ] DAO transforms (row↔domain, query composition, JSON parsing, pagination) have tests — pure-function or in-memory DB; never a real DB.
- [ ] Pure pass-through controllers and pass-through DAO methods are **not** tested (would be duplication / noise).
- [ ] Tests describe **behavior**, not implementation.
- [ ] Edge cases covered (null, empty, boundary, invalid input, error conditions).
- [ ] ≤3 mocks per test file. If more, refactor.
- [ ] No private-function tests added as a default path — refactored to public helpers, or explicitly marked as debugging aids.
- [ ] No request/response object passed below the Controller layer.
- [ ] No mutation of passed-in arguments.
- [ ] Every test pins down a behavior a future reader would care about — no noise tests, no coverage-padding tests.
- [ ] Full `pytest` suite passes, including coverage threshold (not just the targeted subset).
- [ ] Bug fixes include a regression test that failed before the fix.

---

## 12. Pending Approval — Industry Practices Not Yet Adopted

The following are common industry practices that either conflict with or extend beyond the source principles. **They are NOT currently part of this blueprint.** Each requires explicit sign-off before being adopted.

| # | Practice | Status | Note |
|---|----------|--------|------|
| 1 | **Property-based testing** (Hypothesis in Python, QuickCheck-style) | Not adopted | Generates hundreds of inputs per test. Powerful for pure functions; adds tooling and mental load. |
| 2 | **Mutation testing** (`mutmut`, `cosmic-ray`) | Not adopted | Measures test quality by mutating source and checking if tests catch it. Conflicts with "no chasing metrics" stance; could be a targeted tool. |
| 3 | **Snapshot / golden-file testing** | Not adopted | Often tests implementation rather than behavior. Flagged per §10.4. |
| 4 | **TestContainers / real-Postgres for unit tests** | Not adopted | Conflicts with "no real DB in unit tests". Suitable for integration, not unit. |
| 5 | **Mocking internal collaborators with `unittest.mock.patch`** as a default | Not adopted | Conflicts with "mocking is a code smell" — DI / `createNull()` / fakes are preferred. `patch` stays as last resort only. |
| 6 | **Testing private functions as a routine practice** | Not adopted | Source doc explicitly recommends against. Escape hatch exists for rare cases (§5.4). |
| 7 | **100% coverage mandate** | Not adopted | Source doc explicitly caps at 80–90% for diminishing returns. |
| 8 | **Given-When-Then naming** inside unit tests (BDD-style) | Not adopted | Reserved for Behave / API tests. Unit tests use AAA + behavior-first naming. |
| 9 | **Shared test database with transaction rollback per test** | Not adopted | Attractive for speed but creates cross-test coupling risk. In-memory fakes preferred. |

To adopt any of these, explicitly approve and this document will be updated.
