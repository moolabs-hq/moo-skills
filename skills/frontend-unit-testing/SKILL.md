---
name: frontend-unit-testing
description: Definitive guide for unit-testing any new frontend feature, enhancement, or bug fix in the Moolabs codebase using Vitest + @testing-library/react. Covers what to test (pure utils, hook/store state transitions, error/fallback behavior) vs skip (presentation JSX, thin API wrappers); the agent-edition completion gate (scoped tests during iteration, full `npm run test:unit` before declaring done); coverage thresholds via `npm run test:unit:ci`; failing-test-first for bug fixes; refactor regression workflow. See also the bundled roadmap.md (point-in-time inventory) and usage.md (run commands). Use when the user is writing, fixing, or auditing frontend unit tests, asks "how should I test this React component", "do I need a test for this", "vitest setup", or runs the FE unit suite.
---

# Frontend Unit Testing Blueprint (Agent Edition)

> The definitive guide for how any new feature, enhancement or bug fix should approach unit testing. Framework: **Vitest** + **@testing-library/react** (already configured).
>
> This document is written for an autonomous coding agent. It assumes the agent enumerates testable units and holds intermediate reasoning in working memory rather than externalising them to PR descriptions or scratch files. Human-oriented ceremony ("before opening PR", pre-commit hooks, watch mode for a human-driven inner loop) is reframed in terms of the agent's own completion gate.

---

## 1. Philosophy: Backend Principles → Frontend

| Backend Concept | Frontend Equivalent |
|-----------------|---------------------|
| Controller layer (thin, no unit tests needed) | **React component JSX** — rendering/orchestration, tested via E2E |
| Model layer (business logic, test thoroughly) | **Custom hooks with logic** (`useXxx`) — state machines, transformations |
| Helpers, validators, mappers (must be tested) | **Utils, helpers, transformers** — pure functions |
| DAO layer (tested via model) | **API service hooks** (`useAxios` wrappers) — thin, no unit tests |
| Infra wrappers  | `**useAxios**`**, Supabase client, Mixpanel** — tested once, trusted |
| Zustand stores  | **Model layer** — business logic that computes derived state |
| Ports & Adapters | **Context providers** = ports; **concrete implementations** = adapters |


---

## 2. What to Test and What Not To

### MUST unit test (has business logic / transformations)

* Pure utility functions (helpers, validators, mappers, transformers)
* Custom hooks with business logic (state machines, computed values, orchestration)
* Zustand stores with derived state or actions containing logic
* Routing logic, backward compatibility layers, adapter layers

### DO NOT unit test (no business logic)

* Component JSX rendering — E2E (Playwright) covers this
* API service files that are thin `useAxios` wrappers with no transformation
* Type-only files (`types.ts`, `dtos/`), constants with no computation
* Context providers that only pass through config
* Barrel exports (`index.ts` re-exports)

### Decision Flowchart

```
Is there business logic, transformation, or branching?
  ├── YES → Does it live in a pure function (no React state)?
  │     ├── YES → Write unit test (Wave 1 style, zero mocks)
  │     └── NO → Is it a hook or store?
  │           ├── YES → Write unit test with renderHook / store.getState()
  │           └── NO → It's component orchestration → E2E covers it
  └── NO → Skip unit test
```


---

## 3. TDD Process for New Features

### Red → Green → Refactor


1. **Define the behavior first** — before writing any implementation, write the `describe`/`it` blocks expressing what the feature should do
2. **Watch it fail (Red)** — confirms the test isn't a false positive
3. **Write minimum code to pass (Green)** — resist the urge to over-engineer
4. **Refactor** — extract utils, simplify, rely on tests for safety net

### Practical Steps

```
1. Parse the task. Enumerate which units will contain business logic (hold in working memory, not scratch files):
   - Will you need a util/helper? → Plan its test file
   - Will you need a hook with state logic? → Plan its test file
   - Will you need a Zustand store action? → Plan its test file
2. Write behavior specs FIRST (describe/it blocks with assertions)
3. Run tests — they should fail (Red)
4. Implement the code
5. Run tests — they should pass (Green)
6. Refactor code and tests for clarity
```

### For Bug Fixes


1. **Reproduce the bug as a failing test** — this is your regression guard
2. Fix the code
3. Watch the test pass
4. The test stays forever as a regression test

### For Modifications to Existing Code

> "Never touch a piece of code without first thinking about tests."


1. If the file has tests → run them first, ensure green
2. If the file has NO tests → write tests for the existing behavior BEFORE modifying
3. Make your changes
4. Update/add tests for the new behavior


---

## 4. Test Conventions

### File Naming & Location

Tests are **colocated** with source code in `__tests__/` directories:

```
feature/
  hooks.ts
  utils.ts
  __tests__/
    hooks.test.ts
    utils.test.ts
```

### Describe/It Naming: Behavior-First (BDD)

Describe **what the user/system achieves**, not what function is called.

```typescript
// GOOD — describes BEHAVIOR
describe("agentRouting", () => {
  describe("when agent is NODE_CONFIG_GENERATOR", () => {
    it("routes to the new /ai/conversation/start endpoint", () => { ... })
    it("builds status params with messageId", () => { ... })
  })

  describe("when agent is a legacy type", () => {
    it("routes to the old /ai/workflow/conversation endpoint", () => { ... })
  })
})

// BAD — describes IMPLEMENTATION
describe("isNewApiAgent", () => {
  it("returns true for NODE_CONFIG_GENERATOR", () => { ... })
  it("checks the NEW_API_AGENTS set", () => { ... })
})
```

### Test Structure: Arrange → Act → Assert

```typescript
describe("What behavior are you testing?", () => {
  it("What should it do?", () => {
    // Arrange — set up input
    const input = buildTestInput({ currency: "USD", amount: 100 })

    // Act — call the behavior
    const actual = convertCurrency(input, { targetCurrency: "EUR", rate: 0.85 })

    // Assert — verify expected output
    expect(actual).toEqual({ currency: "EUR", amount: 85 })
  })
})
```

### Edge Case Coverage Checklist

For every unit, consider these scenarios:

* **Happy path** — standard input, expected output
* **Empty/null input** — `null`, `undefined`, `""`, `[]`, `{}`
* **Boundary values** — 0, -1, MAX_INT, empty string vs whitespace
* **Invalid input** — wrong types, malformed data
* **Multiple items** — single vs many (arrays with 0, 1, N items)
* **Error conditions** — what happens when dependencies fail?


---

## 5. Mocking Policy

> "Mocking is a code smell." — from the team testing strategy

### Hierarchy (prefer top, avoid bottom)


1. **No mocks** — test pure functions directly. This is the gold standard.
2. **Dependency injection** — pass collaborators as hook/function params (`onApply`, `onClose`, etc.)
3. **Nullable infrastructure** — `createNull()` pattern for API clients in tests
4. `**vi.mock()**` **as last resort** — only for Next.js framework modules (router, navigation) already mocked in `vitest.setup.ts`

### Examples

```typescript
// TIER 1: No mocks — pure function
it("creates label from field type", () => {
  expect(createLabelFromFieldType("multi_select")).toBe("Multi select")
  expect(createLabelFromFieldType(undefined)).toBe("String")
})

// TIER 2: Dependency injection via hook params
it("calls onApplyAIValues with settings", () => {
  const onApply = vi.fn()
  const { result } = renderHook(() =>
    useNodeConfigSync({ onApplyAIValues: onApply })
  )
  result.current.applyAIChanges([{ field_name: "to", field_value: "x" }])
  expect(onApply).toHaveBeenCalledWith([{ field_name: "to", field_value: "x" }])
})

// TIER 4: Framework mock (already in vitest.setup.ts, acceptable)
// vi.mock("next/navigation") — mocked globally, not per-test
```

### If you need more than 3 mocks in a single test file, the code under test likely needs refactoring:

* Extract pure logic into utility functions (testable without mocks)
* Accept dependencies as params instead of importing them directly
* Use the Ports & Adapters pattern — your hook should accept an adapter interface, not a concrete implementation


---

## 6. Fixture Builders

### Pattern

```typescript
// Create builders with sensible defaults + overrides
const createMockNodeConfig = (overrides?: Partial<WorkflowBlockDto>): WorkflowBlockDto => ({
  id: "node-1",
  typeId: "filter-emails",
  variableName: "Filter Emails",
  // ... sensible defaults
  ...overrides
})
```

### Where to Put Them

| Scope | Location |
|-------|----------|
| Used in 1 test file | Inline in the test file |
| Used across 2+ test files in same feature | `feature/__tests__/builders.ts` |
| Used across multiple features | `tests/unit/builders/` |

### Planned Shared Builders

```
tests/
  unit/
    builders/
      workflowBuilder.ts       # WorkflowBlockDto, WorkflowDto
      nodeConfigBuilder.ts      # Node config fixtures
      formFieldBuilder.ts       # WorkflowSettingsSchemaObject
  integration/                  # Existing
  e2e/                          # Existing
```


---

## 7. Testing Each Code Layer

### Pure Utility Functions

The highest-ROI tests. Zero mocks, pure input → output.

```typescript
// utils/__tests__/inputTypeNormalizer.test.ts
describe("inputTypeNormalizer", () => {
  describe("shouldAddInputTypes", () => {
    it("skips string type fields", () => {
      const field = buildField({ type: "string" })
      expect(shouldAddInputTypes(field, mockNodeDef)).toBe(false)
    })

    it("allows select type fields", () => {
      const field = buildField({ type: "select" })
      expect(shouldAddInputTypes(field, mockNodeDef)).toBe(true)
    })

    it("skips explicitly excluded field names", () => {
      const field = buildField({ name: "data_manipulation-filter_data-filtering_operator" })
      expect(shouldAddInputTypes(field, mockNodeDef)).toBe(false)
    })
  })
})
```

### Custom Hooks

Use `renderHook` from `@testing-library/react`. Test the behavior, not React internals.

```typescript
describe("useFormValidation", () => {
  it("returns validation errors for empty required fields", () => {
    const { result } = renderHook(() =>
      useFormValidation({ fields: mockFields, values: {} })
    )
    expect(result.current.errors).toContainEqual({
      field: "email",
      message: "Required"
    })
  })

  it("clears errors when valid values are provided", () => {
    const { result, rerender } = renderHook(
      ({ values }) => useFormValidation({ fields: mockFields, values }),
      { initialProps: { values: {} } }
    )

    rerender({ values: { email: "test@example.com" } })
    expect(result.current.errors).toEqual([])
  })
})
```

### Zustand Stores

Test directly without React rendering. Call actions, assert state.

```typescript
describe("InterfaceFormStore", () => {
  beforeEach(() => {
    useInterfaceFormStore.setState(useInterfaceFormStore.getInitialState())
  })

  it("sets form values without overwriting unrelated fields", () => {
    const { setFieldValue } = useInterfaceFormStore.getState()
    setFieldValue("email", "test@example.com")
    setFieldValue("name", "John")

    const { formValues } = useInterfaceFormStore.getState()
    expect(formValues).toEqual({
      email: "test@example.com",
      name: "John"
    })
  })
})
```


---

## 8. Vitest Configuration

### Coverage Thresholds (75-80% target)

```typescript
// vitest.config.ts — coverage section
coverage: {
  provider: "v8",
  reporter: ["text", "html", "lcov"],
  reportsDirectory: "./coverage",
  include: [
    "components/**/utils/**/*.ts",
    "components/**/hooks/**/*.ts",
    "components/**/hooks.ts",
    "app/**/helpers/**/*.ts",
    "app/**/utils/**/*.ts",
    "app/**/hooks/**/*.ts",
    "app/**/hooks.ts",
    "app/**/store/**/*.ts",
    "hooks/**/*.ts",
    "utils/**/*.ts",
    "lib/**/*.ts"
  ],
  exclude: [
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/types.ts",
    "**/constants.ts",
    "**/index.ts",
    "**/*ApiService.ts",
    "**/dtos/**"
  ],
  thresholds: {
    statements: 75,
    branches: 70,
    functions: 75,
    lines: 75
  }
}
```

### Scripts

```json
{
  "test:unit": "vitest run",
  "test:unit:watch": "vitest",
  "test:unit:ci": "vitest run --coverage --reporter=dot",
  "test:unit:changed": "vitest run --changed"
}
```

### When the agent runs which command

- **While iterating on a specific unit**: `test:unit:changed` or a path-filtered `vitest run <path>` — fast feedback loop.
- **Before declaring the task complete**: full `test:unit`. A passing targeted run is not sufficient evidence that the change is safe.
- **CI**: `test:unit:ci` with coverage thresholds enforced.

`test:unit:watch` is a human inner-loop tool — skip it. The agent re-runs targeted commands on demand instead of sitting in a watcher.


---

## 9. What NOT to Do


1. **Don't test for code coverage numbers** — test for behavior coverage. 80% is a guardrail, not a goal.
2. **Don't test component rendering** — E2E (Playwright) handles that.
3. **Don't mock everything** — if you need 10 mocks, refactor the code.
4. **Don't test implementation** — test what the function does, not how.
5. **Don't test thin wrappers** — `*ApiService.ts` with no transformation logic.
6. **Don't write tests for the sake of tests** — zero business logic = skip.
7. **Don't test private/internal functions** — refactor to make them public helpers if they need testing.
8. **Don't test the channel** — don't assert that A calls B calls C. Test A's output and C's behavior given C's input. Integration between them is integration test territory.


---

## 10. Pre-Completion Checklist

Before declaring any change that adds or modifies business logic as done, the agent must self-verify:

- [ ] Enumerated which units contain testable business logic
- [ ] Pure utility functions have unit tests (zero mocks)
- [ ] Hooks with state/transformation logic have unit tests
- [ ] Zustand store actions with logic have unit tests
- [ ] Tests describe BEHAVIOR, not implementation
- [ ] Edge cases covered (null, empty, boundary, error)
- [ ] No more than 3 mocks per test file (refactor if more needed)
- [ ] Test file colocated in `__tests__/` directory
- [ ] Full `vitest run` passes (not just the targeted subset)
- [ ] Bug fixes include a regression test that fails without the fix