# Adversarial PR Review — framework-capability tree (PR #11)
Date: 2026-06-08
Operator: claude-opus-4-8

## PRs in scope
| PR | Branch | Base | Head SHA | Status |
|---|---|---|---|---|
| #11 | spec/framework-capability-tree-impl | main | dd29723 | in-progress |

Supersedes PR #10 (built on it; reuses its transitive resolver + render driver + clobber guard). Close PR #10 after merge.

## Codebase profile
- Python 3.10+ stdlib unittest; Jinja2 + PyYAML soft deps (try/except ImportError).
- Test: `bash skills/cost-billing/scripts/test-suite.sh` (123 baseline; Phase 8 auto-discovers test_*.py; Phase 7 renders templates). Scoped: per-script `python3 <test_file>.py`.
- **NO CI** (verified: .github/workflows absent). Exit gate uses "no checks configured (verified)".
- Config style: the suite DETECTS customer config frameworks (pydantic/dynaconf/django/environs/decouple/dotenv; zod/convict/process-env/env-var; viper/envconfig/koanf/os.Getenv).
- shared/ SHIPS to customers (install.sh → cost-billing-shared). Node files must carry NO moolabs provenance.
- Overriding principle (memory: dogfood-generalization, corrected 4x): NO modeling on moo-arc's shape (app/ layout, env_file, CommonSettings, python_common). This PR's whole point is de-moolabs-ing.

## PR #11 — framework-capability tree

### Original intention
Config detection = 10 flat regex patterns in env-loader-patterns.yaml + a transitive pydantic detector (PR #10). Emit paths HARDCODED to moo-arc's `app/services/` (config_wire._STUB_EMIT_PATHS, task_planner._slugs_import_path_for, render_artifacts._SLUGS_DIR). Detection + emission scattered across 3 axes.

### New intention
A single language→framework tree (one YAML node per framework) drives detection (regex|code), wiring (modify|stub), and emit (path/import DERIVED from the customer's detected config location). One winner per service; a thin dispatcher runs only that node's scripts. The `app/services/` hardcodes are deleted; env-loader-patterns.yaml retired.

### Success criteria (1-8 — see reviewer brief in this file's commit + the dispatch)
### Codebase-specific challenges (path derivation / file corruption / stub_anchor multi-service / registry perf / moved-resolver safety / node false-positives / dangling catalog refs — see dispatch)

### Phase 1f self-review
- Round 1: sharpened challenge "path derivation correctness" → split into (repo-wide granularity scan_root math) + (flat/nested layouts) + (clobber). Added criterion 8 (no moolabs modeling in shipped node YAMLs).
- Round 2: added the stub_anchor multi-service fallback question (is the legacy `app/services/moolabs` fallback a RE-INTRODUCED hardcode, or acceptable documented fallback?) — this is the sharpest residual concern. Deferred to Phase 2.

### Risk map
- env_loader_scan `_service_entry`: service-relative path = str(result.file relative to scan_root). MEDIUM — repo-wide granularity (scan_root = shared_config_path) + flat config-at-service-root edge.
- task_planner `stub_anchor`: returns None for multi-service → slugs fall back to legacy `_slugs_import_path_for` (app/services/moolabs). MEDIUM — a documented-fallback-vs-regression judgment.
- registry `load_registry`: called in env_loader_scan + config_wire + task_planner. LOW-MEDIUM — redundant per-call reload (perf, not correctness).
- new framework nodes (django SECRET_KEY|INSTALLED_APPS|DATABASES; etc.): LOW-MEDIUM — false-positive on a non-config file / collision with another node.
- strategies `_PY_INDEX_CACHE` module-global: LOW — cross-scan staleness in the test suite.

### CI status: no checks configured (verified).

## Rounds (each found a DISTINCT real bug — not a recurring one — all fixed + verified)
| R | Confirmed findings | Fix commit | Streak |
|---|---|---|---|
| 1 | 2 IMP (F1 repo-wide emit/import mismatch → ImportError; F2 multi-service re-introduced app/services/moolabs hardcode) + 4 MINOR | f700723 | 0 |
| 2 | 1 IMP (slugs emit anchor-derived for all langs but import python-only → TS/Go broken) + 1 MINOR + 2 NIT | 7daf0e6 | 0 |
| 3 | 1 IMP (TS stub ts_alias relative ./ import unresolvable from fixed client location) + 1 MINOR + 1 NIT | d7cc6bb | 0 |
| 4 | 0 CRIT/IMP, 1 MINOR (Go bare-dir import, pre-existing + P0-gated) + 2 NIT | (none — LOW only) | 1 |
| 5 | 1 IMP (unquoted file: → ` #` path silent YAML truncation → wrong codemod target) + 3 MINOR | 4a1c4af | 0 |

Theme of R1-3: emit/import path-consistency across artifacts × languages (CLOSED — all 3 import rules now location-independent; slugs emit/import gates aligned). R5: a different axis (YAML quoting of the source `file` field). Each finding is distinct (not the same bug recurring), each fixed + regression-tested, suite green every round (123, FAIL 0). CI: none configured (verified).

Operator spot-checks: R1 (read _service_entry repo-wide path math), R2 (read both slugs gates), R3 (TS stub @/ resolves to emit — empirical MATCH), R4 (Go P0-gated in SKILL), R5 (file round-trip 'v #2/config.py' intact).

Accepted residue (LOW, not blocking): Go bare-dir config import (pre-existing, gated behind the P0/in-progress Go adapter — no compilable Go ships); ts_alias @/ assumes standard tsconfig; repo-wide no-config fallback import (degenerate unrecognized path); PermissionError on unreadable signed-yaml + KeyError on root-less service dict (both CLI-unreachable).

## Status: at the 5-round safety-valve threshold — SURFACED to user.
Streak 1 (R4) then reset by R5's IMP. All CRIT/IMP found across 5 rounds are FIXED + verified. Findings are distinct + narrowing (R4 clean, R5 one quoting sibling), not a stuck loop. Awaiting user decision: continue to the 2-consecutive-LOW exit gate, or accept now.

## Final summary
- Merge status: NOT MERGED — awaiting explicit user permission + PR #10 close confirmation.
