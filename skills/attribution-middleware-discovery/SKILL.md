---
name: attribution-middleware-discovery
description: Use when onboarding attribution middleware to a customer repository, inventorying HTTP ingress and async propagation, generating instrumentation-map.yaml, or checking route and middleware drift.
---

# Attribution Middleware Discovery

## Purpose

Build an evidence-backed static map of where attribution middleware belongs.
The map is an engineering proposal, not runtime telemetry or a financial fact.
It works from source and manifests; Datadog, another APM, and production log
access are optional inputs, not prerequisites.

## Hard Rules

1. Run the bundled scanner. Do not reconstruct the map from memory or prose.
2. Never execute or import customer code. The scanner is read-only except for
   its requested output file.
3. Count only explicit Moolabs/attribution middleware. Logging, CORS, auth,
   tracing, JSON parsing, or any generic framework middleware is not coverage.
4. Reject raw inbound customer/tenant identity headers as resolver evidence.
   A resolver must read verified auth/request context and produce either a
   non-empty Moolabs UUID or a non-empty external key destined for the ACUTE
   crosswalk.
5. Preserve uncertainty. Dynamic paths, mounts, auth scopes, and async hops are
   `unknown`/`unresolved` unless concrete source evidence proves them.
6. Exclude tests, fixtures, examples, generated code, vendored code, SDKs,
   archived trees, and local worktree copies.
7. Worker-only services do not need request middleware or an ingress resolver.
   Their resolver state is `not-required`, and the signed map must contain no
   HTTP framework, route, mount, or middleware shape for that service. They
   inherit a verified thread ID at the queue/stream boundary; missing extraction
   remains a gap.
8. Call coverage `discovery_projection`. Never present it as measured runtime,
   customer, cloud-cost, or financial coverage.

## Generate The Map

Locate `scripts/discover.py` relative to this file, then run:

```bash
python3 scripts/discover.py \
  --repo /path/to/customer-repo \
  --output /path/to/customer-repo/.moolabs/attribution/instrumentation-map.yaml
```

For repositories containing archived copies or ambiguous service names, scope
the scan with an exact repo-relative path:

```bash
python3 scripts/discover.py --repo /path/to/repo --service services/api
```

The scanner reuses `cost-billing-discovery` service detection. Install both
skills; a missing sibling scanner is a setup error, not permission to guess.

## Review The Output

Review these in order, one engineering decision at a time:

| Field | Required interpretation |
|---|---|
| `routes[].evidence` | Exact runtime source declaration; confirm dynamic registrations separately. |
| `mounts[]` | Mount evidence only; a dynamic prefix remains unresolved. |
| `auth_scope` | `global`, `router`, `handler`, or honest `unknown`. |
| `resolver` | HTTP ingress is `proposed` only with explicit reachable validation/crosswalk evidence; unresolved ingress blocks signoff. Worker-only services use `not-required`. |
| `feature_proposal` | Draft slug requiring engineer/product confirmation, never an automatic financial label. |
| `async_hops` | `verified`, `missing`, or `unknown` from concrete inject/extract/bind evidence. |
| `middleware_detected` | Static presence of attribution middleware, not proof of ordering or runtime execution. |
| `discovery_projection` | Route inventory projection only. |
| `scanner_version` | Scanner release that generated the map; it is also domain-bound into `source_fingerprint`. |

Any `raw_identity_header`, `middleware_missing`, feature collision, unknown auth
scope, or missing async propagation must be resolved or explicitly accepted
before rollout. An unresolved HTTP ingress resolver cannot be accepted and
blocks signoff; a worker-only `not-required` resolver is not a gap.

Resolver provenance proposals are Python-only in v1. JavaScript/TypeScript and
Go ingress remain supported, but scanner-generated resolver fields stay
`unresolved` and carry `resolver_provenance_unsupported`; do not treat those
services as resolver coverage. To sign one, an engineer must inspect executable
auth/context code, edit the resolver in the exact map to `proposed` with a
concrete expression, template, identity kind, and file/line evidence, then run
independent review and sign those exact edited map bytes. Never accept the
generated unresolved state as risk.

## Signoff

Use `/cost-billing-signoff --attribution-map` after an independent adversarial
review. The resulting
`.moolabs/attribution/instrumentation-map-signoff.yaml` must bind the exact map
SHA-256 and customer source commit. Any map-byte or commit change requires a new
review and signoff.

## Drift

Warn-only is the default:

```bash
python3 scripts/drift_lint.py \
  --repo /path/to/customer-repo \
  --baseline /path/to/customer-repo/.moolabs/attribution/instrumentation-map.yaml
```

Blocking is opt-in through `.moolabs/attribution-policy.yaml`:

```yaml
enforcement: block
```

Block mode is valid only when the engineer signoff approves the exact baseline
digest. Drift never edits source, the baseline, policy, or signoff. Exit codes:
`0` clean/warn, `1` opted-in blocking findings, `2` invalid setup or contract.

## Do Not

- Do not infer auth from route proximity, imports, naming, or an APM library.
- Do not turn a header/API-key guess into customer identity.
- Do not count a service as covered because it has any middleware.
- Do not fabricate effective paths across unresolved dynamic mounts.
- Do not put request middleware on workers with no HTTP ingress.
- Do not enable blocking drift before exact artifact signoff.
