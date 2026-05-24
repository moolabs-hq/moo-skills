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

## Example: Engineer stage 3 for service=moo-acute with re-open-pm

```yaml
$schema: https://moolabs.com/schemas/cost-billing-signoff/0.1.0
stage: engineer-stage3
service_slug: moo-acute
status: re-open-pm
generated_at: 2026-05-20T21:00:00Z
signed_by:
  role: team-engineer
  name: Dan Engineer
  signed_at: 2026-05-20T21:00:00Z
adversarial_review:
  phase: post-signoff-engineer-stage3-moo-acute
  verdict: clean-with-accepted-risks
  reviewer_model: claude-sonnet-4-6
  ran_at: 2026-05-20T20:58:00Z
  findings_total: 1
  findings_resolved: 1
edits_to_inventory:
  - entry_workflow_id: moo-acute.attribution.attribute-cost
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
5. **Body-slug ↔ filename match:** for PM/engineer stages, `product_slug` / `service_slug` in the YAML body matches the slug in the filename. Catches typos like body `service_slug: moo_acute` (underscore) vs filename `engineer-stage3-signoff-moo-acute.yaml` (hyphen). **(F2 fix.)**
6. `status` == `approved`.
7. `adversarial_review.verdict` ∈ `{clean, clean-with-accepted-risks}`.
8. **PM contact cross-check:** for PM stages, `signed_by.contact` matches `products[product_slug].team_pm_contact` IFF the product has `team_pm_contact` set (not internal-only). Catches wrong-PM-claiming-a-product. If team_pm_contact is unset, WARN (not reject) so internal-only products don't deadlock. **(F1 fix.)**
9. **Multi-owner pm-stage3b co-signing:** for `pm-stage3b-signoff-<service>.yaml` where `service` belongs to ≥2 products, the signoff must include `co_signed_by[]` with one entry per owning PM. Each entry has the same shape as `signed_by`. Codemod rejects if any owning product's PM is missing from `co_signed_by[]` OR `signed_by`. **(F3 fix.)**
10. `signed_at` is after `generated_at` (catches backdating).

Any failure → refuse-to-run with a precise message naming the failing field + file.

## v0.2 → v0.3 migration

**No back-compat.** v0.3's signoff schema requires fields v0.2 didn't have (`product_slug`, `service_slug`, schema-versioned `adversarial_review` block). Customers with in-flight v0.2 chains must restart from `/cost-billing-bootstrap-finance`. The codemod refuses any signoff file lacking the v0.3 `$schema` URL. **(F6 fix — clean break.)**
