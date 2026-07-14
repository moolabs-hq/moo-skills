# Signoff YAML schema — human-readable explanation

Authoritative schema: `assets/signoff.schema.yaml`. This doc explains the shape + conventions.

## File naming convention

| Stage | Filename | Cardinality |
|---|---|---|
| `cfo-stage1` | `cfo-stage1-signoff.yaml` | 1 (org-wide) |
| `pm-stage2-<product>` | `pm-stage2-signoff-<product-slug>.yaml` | N (one per CPO-declared product) |
| `cfo-stage2b-<product>` | `cfo-stage2b-signoff-<product-slug>.yaml` | N |
| `engineer-stage3-<service>` | `engineer-stage3-signoff-<service-slug>.yaml` | M (one per engineer's `--service`) |
| `pm-stage3b-<service>` | `pm-stage3b-signoff-<service-slug>.yaml` | M |
| (Holistic R) | `holistic-r-review.md` | 1 (org-wide; produced by `/cost-billing-adversarial-review`, not signoff skill) |

All files live in `.moolabs/inventory/reviews/`.

## Required fields

Every signoff YAML must have:

- `$schema` — the JSON-Schema URL (`https://moolabs.com/schemas/cost-billing-signoff/0.1.0`).
- `stage` — one of the enum values (`cfo-stage1` / `pm-stage2` / `cfo-stage2b` / `engineer-stage3` / `pm-stage3b`).
- `status` — `approved` OR a `re-open-*` value OR `escalate`.
- `generated_at` — ISO-8601 UTC.
- `signed_by` — object with `role`, `name`, `signed_at` (mandatory) + optional `contact`, `signed_method`, `machine_fingerprint`.
- `adversarial_review` — the Skill R Phase 4 output: `phase`, `verdict`, `reviewer_model`, `ran_at`, counts.

## Required-when-applicable fields

- `product_slug` — REQUIRED for `pm-stage2` and `cfo-stage2b` stages.
- `service_slug` — REQUIRED for `engineer-stage3` and `pm-stage3b` stages.
- `notes` — REQUIRED if any R finding has outcome=`risk-accepted` (signer must explain).

## Optional fields

- `edits_to_inventory[]` — per-entry overrides this signer requested (drives next-revision diff).
- `findings_from_persona[]` — concerns the human flagged BEFORE R ran.
- `findings_resolution[]` — per-R-finding outcome (accepted / risk-accepted / rejected).

## Example: CFO stage 1 (single-product back-compat)

```yaml
$schema: https://moolabs.com/schemas/cost-billing-signoff/0.1.0
stage: cfo-stage1
status: approved
generated_at: 2026-05-20T19:00:00Z
signed_by:
  role: cfo
  name: Jane Smith
  contact: jane@acme.com
  signed_at: 2026-05-20T19:00:00Z
  signed_method: interactive-cli
adversarial_review:
  phase: post-signoff-cfo-stage1
  verdict: clean-with-accepted-risks
  reviewer_model: claude-sonnet-4-6  # different from codegen model
  ran_at: 2026-05-20T18:55:00Z
  findings_total: 3
  findings_human_accepted: 1
  findings_resolved: 2
  findings_rejected_as_false_positive: 0
edits_to_inventory:
  - entry_workflow_id: api.completion.completion-delivered
    change: change-price
    old_value: "0.015"
    new_value: "0.02"
    rationale: "Tier 2 GPU costs rose since last quarter; adjusting unit price up."
findings_resolution:
  - finding_id: r-001
    outcome: accepted
    rationale: "R correctly flagged: my projected revenue arithmetic was wrong."
  - finding_id: r-002
    outcome: risk-accepted
    rationale: "R flagged inconsistent pricing between completion and chat features; accepting because they're priced differently for strategic reasons (chat is a loss-leader)."
  - finding_id: r-003
    outcome: rejected
    rationale: "R thought api.healthcheck was billable; it's actually exempt per ops policy."
notes: |
  Risk-accepted finding r-002 about loss-leader pricing — see Q3 2026 board deck for rationale.
```

## Example: PM stage 2 for product=acute

```yaml
$schema: https://moolabs.com/schemas/cost-billing-signoff/0.1.0
stage: pm-stage2
product_slug: acute
status: approved
generated_at: 2026-05-20T20:30:00Z
signed_by:
  role: team-product
  name: Alice Chen
  contact: alice@acme.com   # MUST match 02-cpo.signed.yaml > products[slug=acute].team_pm_contact
  signed_at: 2026-05-20T20:30:00Z
  signed_method: interactive-cli
adversarial_review:
  phase: post-signoff-pm-stage2-acute
  verdict: clean
  reviewer_model: gpt-4o
  ran_at: 2026-05-20T20:25:00Z
  findings_total: 0
notes: ""
```

## Example: Engineer stage 3 for service=<your-service> with re-open-pm

```yaml
$schema: https://moolabs.com/schemas/cost-billing-signoff/0.1.0
stage: engineer-stage3
service_slug: <your-service>
status: re-open-pm
generated_at: 2026-05-20T21:00:00Z
signed_by:
  role: team-engineer
  name: Dan Engineer
  signed_at: 2026-05-20T21:00:00Z
adversarial_review:
  phase: post-signoff-engineer-stage3-<your-service>
  verdict: clean-with-accepted-risks
  reviewer_model: claude-sonnet-4-6
  ran_at: 2026-05-20T20:58:00Z
  findings_total: 1
  findings_resolved: 1
edits_to_inventory:
  - entry_workflow_id: analytics.attribution.attribute-cost
    change: change-idempotency-anchor
    old_value: "{handler}.{customer_id}.{epoch}"
    new_value: "{handler}.{request_id}"
    rationale: "Original anchor broke under concurrent requests from same customer; request_id is per-request unique."
findings_resolution:
  - finding_id: r-101
    outcome: accepted
    rationale: "R correctly identified that the streaming handler doesn't emit on partial-stream-collapse; fixed in this signoff's edits."
notes: |
  Reopening to PM Alice: the api.attribution.bulk-attribute handler maps to feature
  'bulk-attribute' but PM's pm-stage2-signoff-acute.yaml says this feature is
  priced per-attribution. Reality: the handler emits ONE event per BATCH, not per
  attribution. Need PM to either accept per-batch pricing OR add per-attribution
  derivation (currently impossible without restructuring the handler).
```

## How the gate (`/cost-billing-instrument`) validates

The codemod loads each signoff file and verifies:
1. `$schema` matches the expected URL.
2. `stage` matches the expected stage for the filename.
3. `product_slug` (if applicable) is in `02-cpo.signed.yaml > products[].slug`.
4. `service_slug` (if applicable) appears under at least one `products[].services`.
5. **Body-slug ↔ filename match:** for PM/engineer stages, `product_slug` / `service_slug` in the YAML body matches the slug in the filename. Catches typos like body `service_slug: moo_acute` (underscore) vs filename `engineer-stage3-signoff-<your-service>.yaml` (hyphen). **(F2 fix.)**
6. `status` == `approved`.
7. `adversarial_review.verdict` ∈ `{clean, clean-with-accepted-risks}`.
8. **PM contact cross-check:** for PM stages, `signed_by.contact` matches `products[product_slug].team_pm_contact` IFF the product has `team_pm_contact` set (not internal-only). Catches wrong-PM-claiming-a-product. If team_pm_contact is unset, WARN (not reject) so internal-only products don't deadlock. **(F1 fix.)**
9. **Multi-owner pm-stage3b co-signing:** for `pm-stage3b-signoff-<service>.yaml` where `service` belongs to ≥2 products, the signoff must include `co_signed_by[]` with one entry per owning PM. Each entry has the same shape as `signed_by`. Codemod rejects if any owning product's PM is missing from `co_signed_by[]` OR `signed_by`. **(F3 fix.)**
10. `signed_at` is after `generated_at` (catches backdating).

Any failure → refuse-to-run with a precise message naming the failing field + file.

## Example: attribution instrumentation-map artifact

This engineering-only branch is independent of CFO/PM inventory signoff. Its
digest approves one exact scanner output and customer-repo commit:

```yaml
$schema: https://moolabs.com/schemas/cost-billing-signoff/0.1.0
stage: engineer-attribution-map
status: approved
generated_at: 2026-07-13T12:00:00Z
signed_by:
  role: team-engineer
  name: Dan Engineer
  signed_at: 2026-07-13T12:00:00Z
  signed_method: agent-mediated
adversarial_review:
  phase: post-signoff-engineer-attribution-map
  verdict: clean
  codegen_model: implementation-model
  reviewer_model: independent-reviewer
  review_evidence: review://ws5/independent-review-123
  ran_at: 2026-07-13T11:55:00Z
  findings_total: 0
  findings_human_accepted: 0
  findings_resolved: 0
  findings_rejected_as_false_positive: 0
  cross_model_violated: false
artifact:
  kind: attribution-instrumentation-map
  path: .moolabs/attribution/instrumentation-map.yaml
  sha256: <64 lowercase hex characters>
  source_commit: <customer repo commit>
  accepted_risks: []
```

The helper derives `artifact.path` from the map's exact location under `--repo`
and recomputes `sha256` over the current map bytes. Reformatting the map changes
those bytes and invalidates the signoff. It copies `source_commit`
only from a scanner map whose `source_revision.state` is `clean`; the commit
must be a full lowercase 40-character SHA-1 or 64-character SHA-256 Git object
ID that exists in that repository. Creation and verification also recompute the
scanner's live source revision: the bound commit must remain the current `HEAD`
with clean relevant source. Tests, generated output, vendored code, and other
scanner-ignored paths do not make that revision dirty. Dirty relevant source, a
later commit, and unversioned repositories are ineligible for block approval. A
changed map/path/revision, non-engineer signer, missing or blocked review,
identical normalized codegen and reviewer models, malformed review evidence,
or an inconsistent accepted-risk list and review counts invalidates approval. The
helper derives `cross_model_violated` from the two model IDs and never accepts a
self-review. Review counts are non-negative integers (booleans are rejected),
their outcomes sum to `findings_total`, and accepted risks match
`findings_human_accepted`. The creation command requires explicit resolved and
false-positive counts, derives the accepted count from `--accepted-risk`, and
computes `findings_total`; a review that fixed findings must not be recorded as
zero-finding clean.

Ingress `unresolved` resolvers cannot be accepted as risk and always block
creation and verification. Worker-only services with
`ingress_state: no-middleware-inherits-thread-id` instead use a resolver with
`state: not-required` and null identity, expression, template, and evidence;
that state is valid only when `frameworks`, `routes`, and `mounts` are empty and
`middleware_detected` is false.

For JavaScript/TypeScript or Go HTTP ingress, the scanner intentionally emits
`unresolved` plus `resolver_provenance_unsupported`. The engineer must inspect
the auth/context path and edit the resolver in the map itself to `proposed`,
including `identity_kind`, the concrete resolver `expression`, an implementation
`template`, and file/line `evidence`. Independent review records the unsupported
scanner finding as resolved, then the helper signs the exact edited map bytes.
Changing that proposal or reformatting the map invalidates the digest and
requires another review and signoff.

`attribution_map_signoff.py` writes this document as JSON-form YAML and reads
that same portable representation during verification. JSON is valid YAML, so
the `.yaml` filename and downstream YAML consumers remain compatible while the
helper requires no third-party Python package.

`review_evidence` must be a concrete `review://` URI, an HTTP(S) URL, or a
structured uppercase evidence ID such as `WS5-REVIEW-123`. Free-form review
notes are not evidence identifiers and fail both create and verify.

## v0.2 → v0.3 migration

**No back-compat.** v0.3's signoff schema requires fields v0.2 didn't have (`product_slug`, `service_slug`, schema-versioned `adversarial_review` block). Customers with in-flight v0.2 chains must restart from `/cost-billing-bootstrap-finance`. The codemod refuses any signoff file lacking the v0.3 `$schema` URL. **(F6 fix — clean break.)**
