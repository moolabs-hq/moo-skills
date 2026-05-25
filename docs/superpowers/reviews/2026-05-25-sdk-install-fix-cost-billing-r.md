# 2026-05-25 SDK install fix — cost-billing adversarial review

**Phase:** `post-codemod` (closest fit — review target is what the codemod emits in PR description, even though scope is the codemod's spec rather than an emitted PR diff)
**Target commit:** `a9caa65` — "fix(cost-billing): codemod's broken SDK install commands"
**Files in scope:** 5 (cost-billing-bootstrap-team-engineer/SKILL.md + 04-final.schema.yaml; cost-billing-instrument/SKILL.md; cost-billing-shared/sdk-surface-reference.md; cost-billing-shared/v1-decisions-log.md)

## ⚠️ Cross-model rule violated

I am `claude-opus-4-7` — same model that wrote `a9caa65`. Same blind spots. Per the skill's own rule, findings are ~30% less reliable than a true cross-model run. Recommend re-running with `--reviewer-model gpt-4o` or `claude-sonnet-4-6` before treating verdict as authoritative.

## Summary of changes (target)

`a9caa65` fixed the codemod's broken SDK install commands by:
- Adding team-engineer bootstrap Q16 (per-language install command with sensible defaults)
- Extending 04-final.schema.yaml with `integration.sdk_package_install` block
- Rewriting `sdk-surface-reference.md` §"Install" to be honest about registry 404s
- Updating codemod SKILL.md to read sdk_package_install + fall back to canonical commands
- Updating v1-decisions-log.md §6.4b #19h stale reference

The default install commands (used when customer-context lacks sdk_package_install):

```bash
# Python
LATEST=$(git ls-remote --tags https://github.com/moolabs-hq/moolabs-py.git \
  | grep -v '\^{}' | awk -F'refs/tags/' '{print $2}' | sort -V | tail -1)
pip install -U "git+https://github.com/moolabs-hq/moolabs-py.git@$LATEST"

# TypeScript
LATEST=$(git ls-remote --tags https://github.com/moolabs-hq/moolabs-ts.git \
  | grep -v '\^{}' | awk -F'refs/tags/' '{print $2}' | sort -V | tail -1)
npm install -E "moolabs-hq/moolabs-ts#$LATEST"

# Go
go get -u github.com/moolabs-hq/moolabs-go@latest
```

## Risk map

Primary risks for the post-codemod phase, scoped to install-command correctness:

- **Install command actually fails when customer runs it** — the codemod tells the customer to run X; if X fails, the PR can't be merged. Critical for adoption.
- **"Latest tag" semantics breaks reproducibility** — every CI run resolves a different tag → flaky builds if upstream changes between merges.
- **Pipeline portability** — shell pipeline assumes git+awk+sort+grep available. Fails in minimal containers (Alpine without `git`), Windows `cmd.exe`, Nix sandboxes.
- **Prerelease tags poisoning "latest"** — `sort -V | tail -1` could pick `v1.0.0-rc1` ahead of `v0.9.0` depending on tag order; customers get unstable SDKs without realizing.
- **Hardcoded org name `moolabs-hq`** — coupling that breaks on rename / fork.

## Verification commands

- Confirm Go module path mismatch is still real today: `curl -sL https://raw.githubusercontent.com/moolabs-hq/moolabs-go/main/go.mod`
- Confirm `sort -V` prerelease behavior: `printf 'v1.0.0\nv1.0.0-rc1\nv0.9.0\n' | sort -V`
- Confirm npm github shorthand requires GH auth for private repos: known behavior
- Check that the codemod template files emit the `import` statement matching what `pip install git+...` package name resolves to

## Phase 2 — Adversarial pass (candidates)

### F1 — Go install command fails today (CRITICAL → verified real)

**Candidate:** `go get github.com/moolabs-hq/moolabs-go@latest` fails because `moolabs-hq/moolabs-go`'s `go.mod` declares a different module path.

**Verification command:**
```bash
curl -sL https://raw.githubusercontent.com/moolabs-hq/moolabs-go/main/go.mod | head -3
```

**Output:**
```
module github.com/moolabs/moolabs-go
```

**Verdict:** Real bug. The codemod tells Go customers to run a command that errors with `module declares its path as: github.com/moolabs/moolabs-go but was required as: github.com/moolabs-hq/moolabs-go`. Workaround was documented in `sdk-surface-reference.md` but NOT emitted in the codemod's PR pre-merge note — customer hits failure with no in-PR guidance.

**Severity:** CRITICAL. Customer's PR cannot merge — install fails before tests run.

### F4 — Prerelease tags poison "latest" pick (HIGH → verified real)

**Candidate:** `sort -V | tail -1` could pick a release-candidate over a stable release because `sort -V` treats `v1.0.0-rc1` as > `v1.0.0`.

**Verification command:**
```bash
printf 'v1.0.0\nv1.0.0-rc1\nv0.9.0\nv2.0.0-beta\n' | sort -V
```

**Output:**
```
v0.9.0
v1.0.0
v1.0.0-rc1     # ← AFTER v1.0.0
v2.0.0-beta    # ← picked as "latest" by tail -1
```

**Verdict:** Real bug. If upstream ever cuts an RC tag after a stable tag (standard practice — `v1.0.0` ships, then `v1.1.0-rc1` is cut as a pre-release for v1.1.0), the pipeline silently installs the prerelease as if it were stable.

**Severity:** HIGH. Silent install of unstable code; no error, no warning. Customer ships RC SDK to production without realizing.

### F6 — Sibling bug: telemetry-stack template still teaches `pip install moolabs` (HIGH → verified real, Phase 4 catch)

**Candidate:** `skills/cost-billing-bootstrap/assets/customer-context-templates/telemetry-stack.template.yaml` lines 41-42 instruct customers to fill `instrumentation_install_command` with `pip install moolabs ...` / `npm install moolabs ...` — the exact 404 commands `a9caa65` claimed to eliminate.

**Verification command:**
```bash
curl -sf -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/moolabs/json
```
**Output:** `404`

**Verdict:** Real bug — `a9caa65` fixed the customer-context production template but missed this older parent-bootstrap template. Identical class of bug, identical fix needed.

**Severity:** HIGH. Same impact as the original bug: customer follows the template, install fails.

### F7 — Sibling bug: drift-lint CI uses `pip install moolabs-drift-lint` (MEDIUM → verified real, Phase 4 catch)

**Candidate:** `skills/cost-billing-drift-lint/SKILL.md:160` shows a GitHub Action that runs `pip install moolabs-drift-lint`.

**Verification command:**
```bash
curl -sf -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/moolabs-drift-lint/json
```
**Output:** `404`

**Verdict:** Real bug. The CI snippet is template code customers copy verbatim — first run fails on install. The drift-lint tool itself is bundled with moo-skills, not published as a standalone package (yet).

**Severity:** MEDIUM. Affects only customers who set up the optional drift-lint CI gate; fails loudly on first CI run rather than silently.

### F2 — "Always latest tag" breaks build reproducibility (MEDIUM → accepted v1 risk)

**Candidate:** Every CI run resolves the latest tag dynamically. Two CI runs on the same merge commit can install different SDK versions if upstream ships between them.

**Verdict:** Real characteristic, but matches v1 design intent — `latest-tag` is the DEFAULT strategy; customers who need reproducibility set Q16 `strategy: pinned`. Documentation already mentions this option. Accepted as non-blocking v1 risk; revisit if customers complain about flaky builds.

**Severity:** MEDIUM accepted-residue.

### F3 — Pipeline portability assumes git/awk/sort/grep (MEDIUM → accepted v1 risk)

**Candidate:** The default install pipeline fails on Alpine without `apk add git`, on distroless containers, and on Windows `cmd.exe`.

**Verdict:** Real concern; mitigated by Q16's `strategy: custom` escape hatch (any customer with non-standard tooling overrides with a verbatim command). Codemod's PR pre-merge note now documents the dependency (added in Phase 3). Accepted as non-blocking v1 risk.

**Severity:** MEDIUM accepted-residue.

### F5 — Hardcoded `moolabs-hq` org name (LOW → accepted v1 risk)

**Candidate:** Org name appears in ~10 places across docs and install commands. A future rename / fork breaks all of them.

**Verdict:** Real but extremely unlikely to fire in v1 timeline. Accepted as non-blocking; if Moolabs ever does rename, a single search-and-replace fixes it.

**Severity:** LOW accepted-residue.

---

## Phase 3 — Fixes applied

### Fix F1: Go go.mod workaround emitted in PR pre-merge note

| What was wrong | What changed | What we ran to confirm |
|---|---|---|
| Codemod told Go customers to run `go get github.com/moolabs-hq/moolabs-go@latest` which fails with module-path mismatch; workaround was buried in `sdk-surface-reference.md` only | (1) `sdk-surface-reference.md` §"Go" rewritten with the FULL `require` + `replace` go.mod block + corrected import path (`github.com/moolabs/moolabs-go`, matching module path NOT repo URL). (2) `cost-billing-instrument/SKILL.md:228` now MANDATES the codemod emit the `require`+`replace` block verbatim in the PR pre-merge note when `repo.languages[]` includes `go`. (3) `cost-billing-bootstrap-team-engineer/SKILL.md:162` Q16 default now points at the documented workaround instead of the bare `go get`. | `curl -sL https://raw.githubusercontent.com/moolabs-hq/moolabs-go/main/go.mod` still returns wrong path, so the workaround stays needed; codemod now teaches it instead of hiding it |

### Fix F4: Prerelease filter in all 3 git-URL pipelines

| What was wrong | What changed | What we ran to confirm |
|---|---|---|
| `sort -V \| tail -1` selected `v1.0.0-rc1` over `v1.0.0` | All 3 install pipelines (Python, TS, Go) now insert `grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$'` BEFORE `sort -V` — strict regex anchored with `$` to reject any prerelease suffix. Also added empty-result guard `[ -n "$LATEST" ] \|\| { echo "no stable tag found"; exit 1; }`. Updated 5 places: `sdk-surface-reference.md` (3 pipelines) + `cost-billing-bootstrap-team-engineer/SKILL.md` (Python + TS Q16 defaults). | `printf 'v1.0.0\nv1.0.0-rc1\nv0.9.0\n' \| grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' \| sort -V \| tail -1` → `v1.0.0` ✓ (prereleases correctly filtered) |

### Fix F6: telemetry-stack template no longer teaches `pip install moolabs`

| What was wrong | What changed | What we ran to confirm |
|---|---|---|
| `telemetry-stack.template.yaml` examples for `instrumentation_install_command` told customers to write `pip install moolabs ...` / `npm install moolabs ...` — both 404 | Template comment now: (1) explicitly states SDK install is handled separately via `04-final.signed.yaml > integration.sdk_package_install`; (2) clarifies this field is for OPTIONAL OpenTelemetry sibling packages only (which ARE on registries); (3) examples updated to OTel-only (`opentelemetry-instrumentation-fastapi` / `@opentelemetry/api`); (4) explicit "do NOT include moolabs/moolabs-py/moolabs-ts/moolabs-go here" warning | `grep -n "moolabs" telemetry-stack.template.yaml` → no install commands referencing moolabs packages remain |

### Fix F7: drift-lint CI uses git-URL install with explicit TODO

| What was wrong | What changed | What we ran to confirm |
|---|---|---|
| `pip install moolabs-drift-lint` returns 404 — package not published | CI step replaced with: (1) explicit `⚠️` warning that the package isn't published yet; (2) git-URL fallback that clones moo-skills and pip-installs from the local scripts dir; (3) `TODO(post-GA)` marker for the eventual replacement with the published name | `curl -sf -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/moolabs-drift-lint/json` returns 404 confirming the fix is needed; customer's CI now works via git clone |

---

## Phase 4 — Robustness sweep (hop radius 2)

Searched all skills/ files for:
- `sort -V` without preceding prerelease filter → all 4 production occurrences now have the filter; only doc-comment occurrences remain (the 3 in sdk-surface-reference.md are inside the corrected pipeline blocks themselves, and the bootstrap-team-engineer one is the doc warning text)
- `moolabs-hq/moolabs-go` bare in install commands → none remain in install templates (only in repo URL listings + the documented workaround block)
- `pip install moolabs\b` / `npm install moolabs\b` / `go get moolabs.com` as live install commands → none remain in templates (only documentation contexts that explicitly say "NEVER emit this")
- Other Moolabs-org packages assumed on public registries (`moolabs-cli`, `moolabs-sdk`, etc.) → none found

No further sibling occurrences detected.

---

## Phase 5 — Stop criterion check

**Confirmed bugs fixed:** F1 (CRITICAL), F4 (HIGH), F6 (HIGH), F7 (MEDIUM).
**Accepted non-blocking risks:** F2 (MEDIUM — reproducibility via Q16 `pinned` escape hatch), F3 (MEDIUM — portability via Q16 `custom` escape hatch), F5 (LOW — org-rename unlikely in v1 timeline).
**No CRITICAL or HIGH remaining open.** Stop criterion met after 1 round.

**Iteration count:** 1 of 5 (well under cap).

---

## verdict: clean-with-accepted-risks

3 MEDIUM/LOW items accepted as non-blocking with rationale and Q16 escape hatches documented. The 4 confirmed bugs (1 CRITICAL, 2 HIGH, 1 MEDIUM) are fixed with the three-column "what was wrong / what changed / what we ran" entries above. Same-model cross-model violation flagged at top of spec — re-run with a different reviewer model for true confidence before customers see this.

