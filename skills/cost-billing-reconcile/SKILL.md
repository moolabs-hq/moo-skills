---
name: cost-billing-reconcile
description: >-
  Engineering-internal Moolabs reconciliation validation harness — measures WAPE and Coverage per cloud service, per attribution algorithm, per customer pattern, against real customer cloud bills. Normalizes bill-grain data to FOCUS half-open intervals [ChargePeriodStart, ChargePeriodEnd) in UTC; buckets event-grain cost_events per cloud granularity (AWS hourly, GCP hourly, Azure daily); runs moo-acute's 12-algorithm attribution ladder; weights WAPE by cost; categorizes unexplained deltas into 8 known failure patterns (timezone skew, period boundary, Azure daily-only, CUR refinalization, tag dark window, Bedrock 4-token splitting, cross-region premium, untagged AI). Runs as 3-phase dogfood: Moolabs own bill (~1-2 wks), 3-5 friendly customers NDA-gated (~4-6 wks), permanent CI gate on attribution_engine.py PRs. Engineering-internal v1 (§10 #9). Skill C in the suite — the empirical validator. Triggers on "WAPE coverage validation", "Skill C", "reconcile against cloud bill", "validate moo-acute".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  source: docs/grooming/2026-05-19-cost-billing-discovery-requirements.md §4.6
  consumers:
    - moo-acute engineering (CI gate on attribution_engine.py)
    - customer go-live decision (per-customer validation report)
---

# /cost-billing-reconcile — Skill C: Reconciliation validation harness

You are an empirical validator for moo-acute's 12-algorithm attribution ladder. You run real customer bills through the algorithms, measure error against ground truth, and surface where the algorithms work and where they don't. **Customer-facing exposure is engineering-internal only in v1.**

## Trigger

```
/cost-billing-reconcile --phase 1                                      # Moolabs's own bill
/cost-billing-reconcile --phase 2 --customer <slug>                     # friendly customer
/cost-billing-reconcile --phase 3                                       # CI mode
/cost-billing-reconcile --phase 2 --customer acme --report only         # don't re-run, just report
```

Naturally:

```
Run Skill C on our own bill (Phase 1)
Validate algorithm ladder against the pilot customer's corpus
Dogfood the attribution against our last quarter's AWS bill
Permanent CI: re-run reconciliation on this attribution_engine.py PR
```

## Read first (shared/)

- `anchor-taxonomy.md` — FOCUS half-open interval, attribution-key cascade, WAPE/Coverage gate.
- `v1-decisions-log.md` — engineering-internal v1 exposure (#9); uniform 10%/80% gate (#14 still open).
- `gaps-tracker.md` §6.3 — Skill C-specific open questions.

## Required inputs

Skill C is data-hungry. It refuses to run without:

| Input | Source | Purpose |
|---|---|---|
| Real customer corpus: CUR / Billing Export / Cost Management for ≥1 billing month | From `/cost-billing-cloud-bill`'s configured exports | Bill-grain ground truth |
| Customer's actual monthly invoice totals | Customer-provided (NDA-gated) | Ground-truth absolute |
| Event-grain telemetry for the same period | ACUTE Tier 2/4 ingest | Algorithm inputs |
| Customer's tag schema | From `/cost-billing-cloud-bill` tag audit | Attribution-key context |
| The current `attribution_engine.py` snapshot | `../moolabs/services/moo-acute/app/services/attribution_engine.py` | Code under test |

If any input is missing, refuse with a precise message naming which input + how to obtain it.

## Workflow — 4 stages

### Stage 1: Normalize the corpus to FOCUS

Run `scripts/focus_normalize.py`. Per requirements §4.6:

- Convert each bill-grain row to FOCUS half-open interval `[ChargePeriodStart, ChargePeriodEnd)` in UTC.
- Bucket event-grain `cost_events` to matching intervals per cloud:
  - AWS: hourly bucket.
  - GCP: hourly bucket.
  - Azure: daily bucket (interpolation honestly flagged).
- Reconcile timezone metadata: most clouds emit UTC; verify no naive timestamps slipped in.

Output: `.moolabs/reconcile/<customer>/normalized.parquet` — events + bill rows aligned on the same time grid.

### Stage 2: Run the 12-algorithm ladder

Run `scripts/algorithm_ladder.py` against the normalized data. The ladder lives at `../moolabs/services/moo-acute/app/services/attribution_engine.py` (verified path).

Per row, record:

```yaml
- bill_row_id: aws-cur-2026-01-15T08:00Z-AmazonBedrock-1234
  cost_usd: 4.20
  algorithm_fired: attribution_engine.tier_2.request_id_match
  algorithm_confidence: 1.00
  match_grade: financial
  events_matched: [evt-9120, evt-9121, evt-9122]
  unmatched_cost_usd: 0.00
```

```yaml
- bill_row_id: aws-cur-2026-01-15T08:00Z-AmazonS3-5678
  cost_usd: 0.42
  algorithm_fired: attribution_engine.tier_5.tag_only_proportional
  algorithm_confidence: 0.40
  match_grade: best_effort
  events_matched: []
  unmatched_cost_usd: 0.42
  fallback_reason: "no event-grain match; tag-based proportional split applied"
```

### Stage 3: Measure WAPE + Coverage

Run `scripts/wape_coverage.py`. Per requirements §4.6:

- **WAPE** per cloud service, weighted by cost.
- **Coverage** per period = non-fallback rows / total rows.
- **Per-algorithm empirical accuracy** vs. claimed confidence — flag any algorithm whose empirical match rate is more than 15% below its claimed confidence.

Output `.moolabs/reconcile/<customer>/wape-coverage-report.yaml`:

```yaml
report_version: 0.1.0
customer: <slug or "self">
period: 2026-01-01T00:00:00Z..2026-02-01T00:00:00Z
total_cost_usd: 42180.45
overall_wape: 0.082         # 8.2% — passes uniform 10% gate
overall_coverage: 0.84      # 84% — passes uniform 80% gate

per_service:
  AmazonBedrock:
    cost_usd: 12340.10
    wape: 0.043
    coverage: 0.97
    grade: financial
  AmazonS3:
    cost_usd: 8920.30
    wape: 0.18              # 18% — FAILS uniform gate
    coverage: 0.61          # 61% — FAILS uniform gate
    grade: best_effort
    flagged_failure_patterns: [tag_dark_window, untagged_ai_infrastructure]
  Microsoft.MachineLearningServices:
    cost_usd: 1240.50
    wape: 0.12
    coverage: 0.71
    grade: best_effort
    flagged_failure_patterns: [azure_daily_only]

per_algorithm:
  - id: tier_2.request_id_match
    claimed_confidence: 1.00
    empirical_match_rate: 1.00
    rows_fired: 8420
    delta: 0.00              # matches claim
  - id: tier_5.tag_only_proportional
    claimed_confidence: 0.40
    empirical_match_rate: 0.61
    rows_fired: 1240
    delta: +0.21              # exceeds claim — possible over-confidence in algorithm metadata; flag

overall_verdict: passes_uniform_gate_with_per_service_concerns
```

### Stage 4: Categorize unexplained deltas

Run `scripts/failure_pattern_classifier.py`. Per requirements §4.6, classify unexplained delta cost into these 8 patterns:

| Pattern | Symptom |
|---|---|
| `timezone_clock_skew` | Event timestamps off by hours; bill rows in window N but events fall in N±1. |
| `billing_period_boundary` | Costs spike around month-end; some cost gets refinalized into the next period. |
| `azure_daily_only_granularity` | Per-hour event volume can't be inferred from daily bill rows. |
| `cur_mid_month_refinalization` | AWS CUR rows added/changed > 24h after the period closed. |
| `tag_dark_window` | Tags activated mid-period; rows pre-activation have empty tag fields. |
| `bedrock_4_token_splitting` | Bedrock charges split into 4 line items per invocation; events match only the primary line. |
| `cross_region_routing_premium` | Cross-region traffic charged with regional premium; events lack region attribution. |
| `untagged_ai_infrastructure` | AI workload running on shared infrastructure (e.g., GPU pool) without per-feature tags. |

Each pattern has a per-cloud detection signature. Output gets appended to `wape-coverage-report.yaml` under `unexplained_delta_categorization`.

## The three-phase dogfood

### Phase 1 — Moolabs's own bill

- **Effort:** ~1–2 engineer-weeks per requirements §4.6.
- **Principle:** "fail-here-fail-everywhere" — if the algorithm ladder is wrong on Moolabs's own AWS/GCP bills, it will be wrong on customers'.
- **Success criterion (v1 default, per `gaps-tracker.md` §6.3 #16):** All services within uniform 10%/80% gate. Per-service overrides logged.
- **Output:** Public-to-engineering report; not yet customer-shareable.

### Phase 2 — 3–5 friendly customers

- **Effort:** ~4–6 engineer-weeks per requirements §4.6.
- **Mix required:** ≥1 each AWS-heavy / GCP / Azure / polyglot / Bedrock-heavy.
- **NDA-gated.** Phase 2 cannot start without the customer NDA template (gap §6.3 #10, **OPEN — legal**).
- **Local-only run model** (per `v1-decisions-log.md` §6.3 #11) — if a customer refuses to share corpus, Skill C runs **inside the customer's environment** and only aggregate metrics return:
  - Per-service WAPE.
  - Per-service Coverage.
  - Algorithm firing rates.
  - Failure-pattern counts.
  - **NEVER** ship raw bill rows or raw event rows out.
- **Output:** Per-customer validation report consumed by go-live decision. v1 customer-facing exposure = soft-launch with caveats (the per-service confidence map is shared); hard block is v2 (per `gaps-tracker.md` §6.3 #17).

### Phase 3 — Permanent CI gate

- **Trigger:** Every PR to `../moolabs/services/moo-acute/app/services/attribution_engine.py`.
- **Action:** Re-run reconciliation against the **accumulated corpus** (Phase 1 + Phase 2 customers who consented).
- **CI behavior (v1 default):** Block merge if overall WAPE > 10% OR overall Coverage < 80% on accumulated corpus. Per-service regressions log as warnings.
- **Runtime cost (gap §6.3 #13, **OPEN**):** v1 = no sampling (full re-run); add sampling strategy if runtime > 30 min on PR.

## Outputs

| File | Used by |
|---|---|
| `.moolabs/reconcile/<customer>/normalized.parquet` | algorithm runs |
| `.moolabs/reconcile/<customer>/wape-coverage-report.yaml` | go-live decision; engineering review |
| `.moolabs/reconcile/<customer>/per-algorithm-delta.yaml` | algorithm-tuning input |
| `.moolabs/reconcile/<customer>/failure-patterns.yaml` | engineering follow-ups per pattern |
| `.moolabs/reconcile/accumulated-corpus.yaml` | the CI gate input |

## Algorithm versioning (gap §6.3 #15)

When `attribution_engine.py` ships a change, **v1 default = forward-only**:
- New events attribute via the new algorithm.
- Historical events retain their original attribution.
- Customer release notes flag the algorithm change.

Customers do NOT get retroactive re-attribution v1. Margin-report stability matters more than historical correctness at this stage.

Post-GA decision needed on whether to re-attribute history; tracked in `gaps-tracker.md` §6.3 #15.

## What this skill MUST NOT do

- **Never** ship raw bill rows or raw event rows from a customer's environment unless explicitly authorized (NDA-gated v1).
- **Never** claim financial-grade attribution on Azure-daily or Bedrock-pre-Apr-8-2026 — communicate the cap honestly.
- **Never** retroactively re-attribute historical events on an algorithm change (v1; revisit at GA).
- **Never** be reviewed by `/cost-billing-adversarial-review` — Skill C IS the validation skill (per `gaps-tracker.md` §6.5 #27).
- **Never** block a Phase 1 (Moolabs's own bill) failure with customer-facing warnings — Phase 1 is internal.

## Degraded modes

| Condition | Behavior |
|---|---|
| Customer refuses to share corpus | Run locally in customer's environment; aggregate-metrics-only return per `gaps-tracker.md` §6.3 #11. |
| WAPE/Coverage gate fails on a service for a customer's first month | Communicate the per-service confidence map; soft-launch with caveats (v1); customer decides go-live. |
| CI runtime exceeds 30 min on accumulated corpus | Sampling strategy = stratified per-cloud / per-service; document sampling bias in CI output. |
| Phase 1 fails on a service | Block Phase 2 for that service; engineering must fix the algorithm first. |

## Reference files

- `references/12-algorithm-ladder.md` — links to `attribution_engine.py`; per-tier description.
- `references/wape-coverage-gate.md` — math + v1 uniform threshold + per-service-override path.
- `references/focus-half-open-interval.md` — why `[start, end)` and not `[start, end]`.
- `references/three-phase-dogfood.md` — phase-by-phase ops plan.
- `references/local-only-metrics.md` — exactly which metrics leave a customer's environment in local-only mode.
- `references/failure-patterns.md` — per-pattern detection signature.

## Scripts

- `scripts/focus_normalize.py` — Stage 1 normalization.
- `scripts/algorithm_ladder.py` — Stage 2 invoke moo-acute's ladder.
- `scripts/wape_coverage.py` — Stage 3 metrics.
- `scripts/failure_pattern_classifier.py` — Stage 4 categorization.
- `scripts/local_only_runner.py` — runs Skill C in customer environment; emits aggregate-only output.

## Assets

- `assets/failure-patterns.yaml` — the 8 known patterns + detection rules.
- `assets/wape-coverage-report.schema.yaml` — the JSON-Schema for reports.
