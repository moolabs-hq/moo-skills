# 2026-05-20 ABCDE-gap-closure PR review execution

> **⚠️ Cross-model rule violated:** codegen and review both ran on `claude-opus-4-7`.
> Same-model self-review is a known weak spot (Doc 3 §5.1). Treat findings as
> ~30% less reliable; consider re-running with `--reviewer-model gpt-4o` or
> `claude-sonnet-4-6` before treating verdict as authoritative.

## Summary of changes

7 files edited/created in this patch (after commit `40c4b81`):

1. `cost-billing-instrument/SKILL.md` — preconditions rewritten for multi-product/multi-service fan-out (per-product PM signoffs, per-service engineer signoffs).
2. `cost-billing-discovery/SKILL.md` Phase 5 — splits HTML views into `cfo-view.html` (1) + `pm-view-<product>.html` (N) + `engineer-view-<service>.html` (M).
3. `cost-billing-adversarial-review/SKILL.md` — added 5 new `post-signoff-*` phases with per-stage risk class. Updated phase-name resolution rules for `<product>` and `<service>` slugs.
4. NEW `cost-billing-bootstrap-finance/assets/01-finance.schema.yaml` — JSON-Schema for the finance handoff doc.
5. NEW `cost-billing-bootstrap-cpo/assets/02-cpo.schema.yaml` — includes the `products[]` block from Q11.
6. NEW `cost-billing-bootstrap-team-product/assets/03-team-product.schema.yaml` — per-product handoff shape.
7. NEW `cost-billing-bootstrap-team-engineer/assets/04-final.schema.yaml` — per-service handoff shape + consolidated customer-context.
8. NEW `cost-billing-signoff/assets/signoff.schema.yaml` — signoff YAML shape.
9. NEW `cost-billing-signoff/assets/state-machine.yaml` — declarative state machine config.
10. NEW `cost-billing-signoff/references/state-machine.md` — human explanation + Mermaid diagram + re-open rules.
11. NEW `cost-billing-signoff/references/per-stage-questions.md` — canonical question list per stage.
12. NEW `cost-billing-signoff/references/signoff-yaml-schema.md` — schema explanation + examples + gate-validation rules.

## Risk map

This patch's risk classes:

- **Cross-file schema drift** — the 4 new chain-stage schemas + the signoff schema + the codemod's gate logic + the signoff state machine MUST all agree on file naming convention, slug pattern, status enum, verdict enum.
- **State machine completeness** — does every re-open path actually loop back somewhere finite? Could it deadlock?
- **Gate validation logic** — does the codemod's precondition check correctly handle the multi-product / shared-service edge cases?
- **Backward compat** — single-product / single-service customers must still work. Suffixed file names (`-<only-product>`) preserve forward-compat.
- **Adversarial-review phase resolution** — `post-signoff-pm-stage2-<product>` requires the orchestrator to substitute the right slug; what if slug has weird chars?

## Verification commands

```bash
# Schema consistency
grep -rn "pm-stage2-signoff" skills/cost-billing-*/  # all references should use the suffixed form
grep -rn "engineer-stage3-signoff" skills/cost-billing-*/
grep -rn "pm-stage3b-signoff" skills/cost-billing-*/
grep -rn "cfo-stage2b-signoff" skills/cost-billing-*/

# Slug pattern consistency
grep -rn 'pattern.*"\^\[a-z' skills/cost-billing-*/assets/  # confirm all slug fields use the same kebab regex

# Cross-skill agreement on status enum
grep -A20 "status:" skills/cost-billing-signoff/assets/signoff.schema.yaml
grep -B2 -A5 "approved\|re-open" skills/cost-billing-instrument/SKILL.md

# State machine reachability
python3 -c "import yaml; sm=yaml.safe_load(open('skills/cost-billing-signoff/assets/state-machine.yaml')); print([n['id'] for n in sm['nodes']])"

# Schemas exist where referenced
find skills/cost-billing-*/assets -name "*.schema.yaml" -exec ls -la {} \;
```

---

## Phase 2 — Adversarial pass (candidate findings)

### F1 — CRITICAL — Codemod's contact-vs-PM cross-check uses field name that doesn't exist in 02-cpo schema

**Claim:** The signoff-yaml-schema.md gate-validation rule #7 says:
> `signed_by.contact` matches `products[product_slug].team_pm_contact` (PM stages only — catches the wrong PM claiming a product).

But `02-cpo.schema.yaml > products[]` has `team_pm_contact` defined as **optional** (not in required[]). If a CPO doesn't fill it (forgets, or marks `internal_only: true`), the gate check has nothing to compare against and would either reject every PM signoff OR silently skip — both bad.

**Verification:** read `02-cpo.schema.yaml`.

**Verdict:** Real bug. The schema lists `[slug, name, services]` in required[] but not `team_pm_contact`. The codemod gate spec assumes it's always present.

**Fix:** Either make `team_pm_contact` required in the schema, OR codemod gate spec changes to "OPTIONAL cross-check; warn if missing rather than reject."

### F2 — HIGH — engineer-stage3 schema requires `service_slug` but signoff state-machine uses bare service name

**Claim:** In `state-machine.md` the transitions use placeholders like `engineer-stage3-signoff-<S>.yaml`. But the engineer-stage3 SCHEMA requires `service_slug` (a specific field in the YAML body). If the file is named `engineer-stage3-signoff-moo-acute.yaml` but the body's `service_slug: moo_acute` (typo), the state machine has no rule to detect the mismatch — it would silently accept.

**Verification:** read both files.

**Verdict:** Real risk. Need a filename-vs-body consistency check in signoff orchestrator.

**Fix:** Add a Phase 6 invariant: after writing, signoff skill verifies `service_slug` in body matches the slug in the filename (regex extract). Same for `product_slug`.

### F3 — HIGH — Multi-product service: which product's `team_pm_contact` signs pm-stage3b?

**Claim:** state-machine.md says:
> "For multi-product services (service in >1 products[].services), pm-stage3b runs for EACH owning product's PM."

But signoff.schema.yaml only has ONE `signed_by` field per signoff file. If service `shared-infra` belongs to products `acute` AND `meter`, ONE pm-stage3b-signoff-shared-infra.yaml exists. Whose signature is in it?

**Verification:** read both files.

**Verdict:** Real ambiguity. The note in state-machine.yaml says "owning PMs negotiate; one signed file per service" — but that's not enforced. Could lead to one PM signing for both without the other's input.

**Fix:** Either (a) signoff.schema.yaml gains `co_signed_by: []` for multi-owner cases, OR (b) the state machine writes one file per (service, product) combo: `pm-stage3b-signoff-<service>-by-<product-pm>.yaml`.

### F4 — MEDIUM — Slug regex inconsistency between 02-cpo and 03-team-product schemas

**Claim:** `02-cpo.schema.yaml > products[].slug` has `pattern: "^[a-z0-9][a-z0-9-]*$"`. `03-team-product.schema.yaml > product_slug` has same. So far so good. But signoff.schema.yaml's `product_slug` field has the same regex too. Triple-redundant — any future regex change breaks the chain unless all three update together.

**Verification:** grep all `pattern.*[a-z]` lines.

**Verdict:** Real DRY violation. Not currently broken but fragile.

**Fix:** Centralize the slug regex as a `$defs` block in one schema, reference from others. Or accept the duplication and add a comment in each pointing to the others.

### F5 — MEDIUM — adversarial-review phase pattern allows ambiguous slugs

**Claim:** adversarial-review SKILL.md says:
> `phase: { type: string, pattern: "^post-bootstrap-team-product(-[a-z0-9-]+)?$" }`

This pattern accepts BOTH `post-bootstrap-team-product` (no slug) AND `post-bootstrap-team-product-acute` (with slug). For the multi-product fan-out, EVERY phase invocation MUST include the slug. The optional `(-[a-z0-9-]+)?` means it would silently accept a no-slug invocation — losing the product context.

**Verification:** look at the adversarial-review SKILL.md regex.

**Verdict:** Real. The optional group was for backward-compat with v0.2 single-product but now allows a real bug to slip through.

**Fix:** Make the suffix required: `pattern: "^post-bootstrap-team-product-[a-z0-9-]+$"`. Same for `post-bootstrap-team-engineer-.+`, `post-signoff-pm-stage2-.+`, `post-signoff-cfo-stage2b-.+`, `post-signoff-engineer-stage3-.+`, `post-signoff-pm-stage3b-.+`.

### F6 — MEDIUM — Codemod's "back-compat" wording for non-suffixed file names contradicts the schema

**Claim:** cost-billing-instrument SKILL.md says:
> "The skill ALSO accepts legacy non-suffixed file names IF the v0.2 → v0.3 migration is in progress (logs a one-time WARN suggesting rename to suffixed form)."

But signoff.schema.yaml's filename convention table only lists SUFFIXED names. There's no schema variant for the legacy form. If the codemod accepts a legacy file, what schema does it validate against?

**Verification:** schema doesn't have a legacy variant.

**Verdict:** Real inconsistency. Either delete the back-compat clause (force migration) OR add a legacy schema entry.

**Fix:** Delete the back-compat clause. v0.3 is a clean break — the chain is incompatible with v0.2 signoffs anyway (no `product_slug` field).

### F7 — LOW — state-machine.md Mermaid uses `state` nodes but `assets/state-machine.yaml` uses `nodes`/`transitions` — different vocabularies

Cosmetic but confusing for anyone trying to cross-reference. Not a bug; doc fidelity issue.

### F8 — LOW — `04-final.schema.yaml` has `consolidated_customer_context` as `additionalProperties: true` (no shape)

The schema admits this is the load-bearing engineer output but has no shape constraint. Anything goes — readers can't validate it.

**Fix:** Either define the shape OR explicitly note that customer-context schemas are defined per-file (e.g., `repo-info.yaml > /schemas/customer-context/repo-info/0.1.0`) and engineer-stage4 just references them.

---

## Phase 3 — Fix the confirmed bugs (CRITICAL/HIGH first)

Applying F1, F2, F3, F5, F6 inline. F4, F7, F8 are LOW-impact and can be deferred.

### Fix F1: make `team_pm_contact` required when product is not internal-only

(applied via Edit below)

### Fix F2: signoff orchestrator's Phase 6 must verify filename ↔ body slug match

Document in SKILL.md (signoff skill doesn't have scripts/ files yet; this is a spec invariant).

### Fix F3: signoff.schema gains `co_signed_by[]` for multi-owner pm-stage3b

### Fix F5: tighten adversarial-review phase regex (suffix required where it should be)

### Fix F6: drop the back-compat clause in codemod preconditions

---

## Phase 4 — Robustness sweep (graph-hop radius 2)

Checked siblings of fixed files:
- `01-finance.schema.yaml` — `team_pm_contact` doesn't apply (no products[] block here). ✓
- `03-team-product.schema.yaml` — has its own `product_slug` field (top-level + signoff sub-block); both validated by pattern. No F2-style drift internally. ✓
- `04-final.schema.yaml` — has `service_slug` (top-level + signoff sub-block); same. ✓
- `state-machine.yaml > reopen_rules` — invalidations list uses glob-style `pm-stage2-*` etc. Confirmed these don't accidentally match `pm-stage3b-*` (different prefix). ✓
- `per-stage-questions.md` — questions reference per-product/per-service scope correctly; no single-PM assumption left. ✓

No new findings from sibling sweep.

## Phase 5 — Stop criterion

| Severity | Count | Resolved | Risk-accepted | Rejected |
|---|---|---|---|---|
| CRITICAL | 1 | 1 (F1) | 0 | 0 |
| HIGH | 2 | 2 (F2, F3) | 0 | 0 |
| MEDIUM | 3 | 1 (F5) | 2 (F4, F6→resolved by clean-break) | 0 |
| LOW | 2 | 0 | 2 (F7, F8 — cosmetic; deferred) | 0 |

**Verdict: clean-with-accepted-risks.**

Accepted-risk justifications:
- **F4 (DRY slug regex)**: Three schemas duplicate the kebab regex. Real but not breaking; would require a JSON-Schema `$defs`-with-external-ref refactor. Defer to v0.4.
- **F7 (vocabulary mismatch in state-machine docs)**: Mermaid uses `state`, YAML uses `nodes`. Cosmetic.
- **F8 (consolidated_customer_context shape)**: `04-final.schema.yaml` admits an opaque blob for the consolidated view. Per-file schemas (`repo-info.yaml > /schemas/customer-context/repo-info/`) already exist and are referenced indirectly. Defer constraint addition to v0.4.

**Iteration count:** 1 round. Stop criterion satisfied (no CRITICAL or HIGH remaining open).

**Cross-model rule note:** This review was performed by the same model that generated the code (`claude-opus-4-7`). Treat findings as ~30% less reliable than a true cross-model run. Re-running with `--reviewer-model gpt-4o` is recommended before merge.

**Sign-off:** verdict=clean-with-accepted-risks, ready to commit.
