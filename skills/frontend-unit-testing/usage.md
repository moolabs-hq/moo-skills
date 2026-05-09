# Frontend Unit Testing — Usage (Agent Edition)

> How an autonomous coding agent runs and leverages the frontend unit test suite. Framework: **Vitest** + **@testing-library/react**.

**Run commands**
- Full unit suite: `npm run test:unit`
- Coverage (CI-style): `npm run test:unit:ci`
- Changed-files only: `npm run test:unit:changed`
- Watch mode (`npm run test:unit:watch`) is a human inner-loop tool — skip it. Re-run targeted commands on demand instead.

**When the agent runs which command**
- **While iterating on a specific unit**: `npm run test:unit:changed`, or `vitest run <path>` scoped to the file under change.
- **Before declaring the task complete**: full `npm run test:unit`. A passing scoped run is not sufficient evidence that the change is safe.
- **CI**: `npm run test:unit:ci` with coverage thresholds enforced.

A scoped pass is a working-loop checkpoint, not a completion gate.

**How to leverage effectively**
- For rewrites: add tests only for stable business logic boundaries, not volatile UI plumbing.
- For bug fixes: write failing test first, then fix.
- For refactors: run existing tests first, refactor, re-run to guard regressions.
- Prefer testing:
  - pure utils,
  - state transitions in hooks/stores,
  - error/fallback behavior.
- Avoid over-testing:
  - presentation JSX,
  - thin API wrappers with no transformation.

**Recommended workflow**
1. Change code.
2. Add/update nearest unit tests.
3. Run scoped tests (`test:unit:changed` or a path-filtered invocation).
4. Run full `npm run test:unit` before handing work back.
5. Inspect `tests/unit/coverage` from `test:unit:ci` for blind spots.
