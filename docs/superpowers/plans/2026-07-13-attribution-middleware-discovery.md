# Attribution Middleware Discovery - Implementation Plan

> **Execution note:** This is the WS5 kickoff plan required by the frozen
> attribution signal-plane plan. Implement in strict RED-GREEN-REFACTOR order.
> Static discovery must report uncertainty; it must never invent a customer
> identity, route, feature, or async propagation guarantee.

**Goal:** Add a customer-portable skill that inventories authenticated ingress,
proposes safe customer/feature resolver inputs, reports async propagation gaps,
and detects middleware drift without requiring Datadog or any other APM vendor.

**Output:** `<repo>/.moolabs/attribution/instrumentation-map.yaml`, validated by
`skills/attribution-middleware-discovery/assets/instrumentation-map.schema.yaml`.

**Supported v1 ingress adapters:** FastAPI/Starlette, Express/Hono/Next.js App
Router, and Go `net/http`/chi. Unsupported or dynamic constructs remain explicit
`unknown`/`unresolved` findings; they are never silently counted as covered.

## Load-bearing contracts

1. **Read-only scan.** The scanner reads source and writes only the requested
   output path. It never imports or executes customer code.
2. **Deterministic evidence.** Sort all services, routes, evidence locations,
   async hops, and findings. Re-running against unchanged source is byte-identical
   when `--generated-at` is fixed.
3. **Service scope.** Reuse `cost-billing/discovery/scripts/repo_scan.py` for
   manifest/service discovery. `--service` restricts by exact repo-relative path
   or unique basename and fails on missing/ambiguous matches.
4. **Runtime files only.** Exclude tests, fixtures, examples, generated output,
   vendored dependencies, build output, migrations, and SDK source from route and
   resolver evidence.
5. **Exact source evidence.** Each detected ingress or async hop includes
   repo-relative `file` and 1-based `line`. Dynamic paths/mounts are retained with
   low confidence and `path_template: null`.
6. **Auth scope is honest.** Report `global`, `router`, `handler`, or `unknown`.
   Proximity alone may lower uncertainty but cannot prove authentication.
7. **Customer identity is trusted or unresolved.** A resolver may use a verified
   auth/request-context value that is a non-empty Moolabs UUID, or a non-empty
   external key explicitly destined for the ACUTE crosswalk. Raw inbound
   `X-Moolabs-*`, `X-Customer-*`, or equivalent identity headers are rejected as
   resolver evidence unless the code shows verification before context binding.
8. **Feature maps are proposals.** Static route-to-feature suggestions carry
   `high|medium|low` confidence and require engineer signoff. Route names are not
   financial facts.
9. **Async propagation is evidence-based.** Mark each boundary
   `verified|missing|unknown`. Naming, tracing libraries, or a queue dependency
   alone cannot prove thread propagation.
10. **No fake middleware for non-ingress services.** Worker-only services are
    marked `no-middleware-inherits-thread-id`; their async receivers still need a
    verified propagation finding.
11. **Coverage is projected, not measured.** Report discovered route totals and
    statically covered/unknown counts as `discovery_projection`; do not call it
    runtime or financial coverage.
12. **Drift is non-mutating and warn-first.** Route-registering services without
    middleware produce findings and exit 0 by default. Exit 1 only when a checked-in
    policy explicitly sets `enforcement: block`. Schema/CLI/internal errors exit 2.
13. **Signoff is engineering-owned.** The instrumentation map gets an immutable
    SHA-256 artifact signoff through `cost-billing-signoff`; no CFO/PM inventory
    stages are fabricated. Block approval derives the exact repo-relative map
    path and source commit from a clean scanner `source_revision`; dirty or
    unversioned maps are rejected.

## Task 1 - Schema and failing contract fixtures

- [x] Add `assets/instrumentation-map.schema.yaml` with strict enums, nullable
      unresolved fields, evidence locations, resolver safety state, async status,
      projected coverage, findings, and source fingerprint.
- [x] Add fixture repos for each supported adapter, dynamic paths, raw-header
      poisoning, worker-only services, and generated/test exclusions.
- [x] Add failing tests for deterministic ordering, exact line evidence, schema
      validation, unsupported syntax, and source-tree non-mutation.

**RED command:**

```bash
python3 -m unittest discover -s skills/attribution-middleware-discovery/scripts -p 'test_*.py' -v
```

## Task 2 - Shared service discovery and file policy

- [x] Load the existing `repo_scan.py` scanner from the sibling discovery skill;
      do not copy its manifest/framework tables.
- [x] Implement exact/unique `--service` selection.
- [x] Implement one runtime-file iterator shared by all language adapters.
- [x] Test monorepo roots, nested manifests, ignored paths, symlinks, and stable
      repo-relative locations.

## Task 3 - Ingress adapters

- [x] FastAPI/Starlette: decorators, `add_api_route`, `include_router` prefixes,
      Starlette `Route`, dependency/auth sites, and middleware registration.
- [x] Express/Hono: method routes, `use`/mount prefixes, router middleware,
      dynamic paths, and attribution middleware registration.
- [x] Next.js App Router: `app/**/route.{ts,tsx,js,jsx}` exported HTTP methods,
      route templates for static/dynamic segments, and auth call sites.
- [x] Go: `http.Handle`, `HandleFunc`, chi method routes/groups/mounts, `Use`, and
      dynamic pattern uncertainty.
- [x] Merge duplicate declarations only when framework, method, effective path,
      and handler evidence agree.

## Task 4 - Resolver, feature, and async evidence

- [x] Detect verified Python request-context/auth-claim candidates and produce a
      resolver template with non-empty and identity-kind guards. JavaScript,
      TypeScript, and Go resolver provenance stays explicitly unsupported in v1.
- [x] Reject unverified raw identity headers with a high-severity finding.
- [x] Draft normalized route-to-feature slugs with confidence and collision
      findings; preserve a stable `route_id` for human overrides.
- [x] Inventory HTTP client, queue publish/consume, task dispatch, and Kafka-like
      boundaries; mark propagation verified only from concrete inject/extract or
      thread-id binding evidence.
- [x] Mark non-ingress services without recommending request middleware.

## Task 5 - Generator and drift CLI

- [x] Add `scripts/discover.py` with `--repo`, `--service`, `--output`, and
      test-only `--generated-at`.
- [x] Validate generated output before replacing the destination atomically.
- [x] Add `scripts/drift_lint.py` to compare current discovery with a signed
      baseline and emit stable machine-readable findings.
- [x] Support `.moolabs/attribution-policy.yaml` with only
      one top-level `enforcement: warn|block`; reject duplicates, nesting,
      extra keys, and malformed syntax. Absent policy means `warn`.
- [x] Test exit codes 0/1/2, malformed policy, route additions, middleware
      removals, feature-proposal changes, generated-at-only differences, and
      zero source mutation.

## Task 6 - Skill and engineering signoff

- [x] Add concise `SKILL.md` covering when to run discovery, how to inspect
      uncertainty, and the signoff/drift sequence.
- [x] Extend signoff schema/state/docs with an `engineer-attribution-map` branch
      containing exact artifact path, SHA-256, clean map source revision,
      independent model IDs/evidence, explicit resolved/rejected review counts,
      derived accepted/total counts, and approved risk notes.
- [x] Require drift `block` mode to verify the exact approved map bytes/path and
      an existing clean source commit.
- [x] Add executable deterministic `scripts/pressure_harness.py` comparing an
      intentionally unsafe naive heuristic with scanner output for raw identity
      headers and dynamic routes; assert unresolved/low-confidence scanner
      results and run it twice in `test_pressure_harness.py` for byte stability.

## Task 7 - Packaging and real-repo acceptance

- [x] Register the skill in `.claude-plugin/plugin.json`.
- [x] Include it in engineering/all install and package paths, install output,
      next-step guidance, and pruning allowlists.
- [x] Update the suite smoke test to execute the new unit tests and installer
      portability checks under Bash 3.2 and current Bash.
- [x] Scan `/Users/anuragsingh/moolabs-root` into `/tmp`; validate schema,
      deterministic output (ignoring only generated time), known FastAPI/Next/Go
      ingress evidence, and no customer-repo modifications.
- [x] Inject a fixture route without middleware and prove warn/default then
      block/opt-in behavior.

## Verification gate

```bash
python3 -m unittest discover -s skills/attribution-middleware-discovery/scripts -p 'test_*.py' -v
python3 -m unittest discover -s skills/cost-billing/signoff/scripts -p 'test_*.py' -v
python3 skills/attribution-middleware-discovery/scripts/pressure_harness.py
bash skills/cost-billing/scripts/test-suite.sh
git diff --check
```

Before merge, create and verify the attribution-map signoff with a distinct
`codegen_model`, `reviewer_model`, and format-valid `review_evidence` ID/URL. Runtime
acceptance remains static by design: WS5 ships no service and has no cloud deploy.
