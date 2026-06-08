# Dogfood Retro — cost-billing suite on `moo-arc` (2026-06-07 → 2026-06-08)

## What this is

A record of running the cost-billing skill suite
(`/cost-billing-discovery` → `/cost-billing-instrument` →
`/cost-billing-adversarial-review`) against **`services/moo-arc`** — the
moolabs monorepo's FastAPI/Python accounts-receivable / collections service
(LangGraph agents, `structlog`, brownfield OTel, pydantic-settings v2 with a
module-level `settings` instance). Goal: validate the v0.3 skills end-to-end on
a real service and fix whatever the run surfaced.

This service is a good stress test because:
- LLM spend flows through an **`LLMPort` abstraction** (no direct vendor SDK
  call at the agent sites) → the cost is a **consolidation point**, not a
  per-agent vendor call.
- Config extends a **project base class** (`class Settings(CommonSettings)`)
  and is exposed as a **module-level instance**, not `get_settings()`.
- Centralized infra (`infrastructure/terraform/...`), not per-service IaC.

## TL;DR

Running the scripts on a real service surfaced **5 skill defects** + **1
over-correction** (reverted) + **1 execution-process issue**:

| # | Defect | Status |
|---|--------|--------|
| 1 | `env_loader_scan` picks a test/smoke file over the real config | ⚠️ **partially fixed** (test-skip done; detection+mode root open) |
| 2 | `task_planner` crashes on unquoted-ISO `generated_at` | ✅ fixed + tested |
| 3 | `task_planner` marks consolidated LLM-agent sites `sibling-pair` (double-count) | ✅ fixed (discovery guidance) |
| 4 | `task_planner` slug resolution returns `None` (no `product_slug`) | ✅ fixed + tested |
| 5 | helper template missing lazy-import + secret-scrub (and Python-only) | ✅ fixed across py/ts/go |
| — | `env_loader_scan` terraform over-detection "fix" | ↩️ **reverted** (was wrong — see below) |
| — | execution hand-authored artifacts the skill has templates for | 🔁 **open process issue** |

All deterministic fixes are on moo-skills branch
`fix/cost-billing-v03-rough-edges` (cost-billing test suite: **168 passing**).

---

## Pipeline run order (r5 — the clean run against the fixed skills)

Run on a fresh branch off `origin/master` (`moolabs/instrument-moo-arc-r5-…`).
"Phase" numbers match the SKILL.md workflow.

| # | Phase | What was run | Output | Issue surfaced |
|---|-------|--------------|--------|----------------|
| 1 | Discovery 1 | `repo_scan.py` | `repo-profile.yaml` (python; fastapi+aiohttp; brownfield OTel; `existing_moolabs_sdk: none`) | — |
| 2 | Discovery 2 | doc-tree (light) | — | — |
| 3 | Discovery 3 | `catalog_match.py` | code-graph: **0 cost-call sites** | Not a bug — LLM cost is behind `LLMPort`; the cost is a consolidation at `llm_helpers.py`, not a catalog vendor call. Worth documenting so reviewers don't read "0" as a miss. |
| 4 | Discovery 4/5 | inventory build (**agent-driven**; `refund_test.py` / `inventory_build.py` are aspirational/not-on-disk) | `cost-/usage-events-inventory.yaml`, `output-input-map.yaml` | **#3** — agents came out `sibling-pair`; corrected to `usage-only` per the new consolidation rule |
| 5 | Discovery 6 | `env_loader_scan.py` | `env-routing-inventory.yaml` | **#1** — see below |
| 6 | Discovery 7 | `slug_inventory.py` | `slug-inventory.yaml` (4 products: acute, arc, bff, meter) | — |
| 7 | Instrument 1.5 | `sdk_snapshot.py` (snapshot reused) | `sdk-surface-snapshot.yaml` (v0.3.0-rc1, `unified_ingest_present: true`) | — |
| 8 | Instrument 1.6 | attribution discovery | `attribution-bindings.yaml` (`customer_id: self.tenant_id`, `request_id: get_correlation_id()`) | — |
| 9 | Instrument 1.7 | `config_wire.py` | `config-wiring-plan.yaml` (mode `stub`; `deployment_stubs`) | **#1 manifests** — raw env_loader output produced over-broad surfaces; scoped by hand at instrument layer |
| 10 | Instrument 2c | `task_planner.py` | `tasks.yaml` (8 inserts: 7 `usage-only` + 1 `cost-only`, all slug consts resolved; `env_wire_tasks: 1`) | **#2 / #3 / #4 manifest here** (all fixed → clean output) |
| 11 | Instrument 2 | render: helper from `python-moolabs-client.py.j2` ✅; stub / slugs / `.env` / terraform **hand-authored** ❌ | `moolabs_client.py` (+ stub, slugs, env-wiring files) | **Template-bypass** (see Process issue) |
| 12 | Instrument 2d | 8 code inserts | — | **Not completed** (paused here) |
| 13 | Instrument 3 | PR emission | — | Not reached |
| 14 | Skill R | adversarial review | — | Not reached |

---

## Issues found (detail)

### #1 — `env_loader_scan` selects the wrong file as `app_config` ⚠️ partial

- **Symptom:** `app_config.file` resolved to `test_accounts_optimizations.py`
  (a test file), then after the test-skip fix to
  `scripts/smoke_dunning_e2e_dev.py` (a smoke script) — never to the real
  `app/config.py`.
- **How surfaced:** ran `env_loader_scan.py`; inspected
  `env-routing-inventory.yaml > services[0].app_config.file`.
- **Root cause (two layers):**
  1. Any file with `os.getenv` could win because **the real config never
     matches the pydantic pattern** — `app/config.py` is
     `class Settings(CommonSettings)` and imports `from pydantic import Field`,
     but the catalog signals require `BaseSettings` directly
     (`class \w+\(BaseSettings\)` / `from pydantic_settings import … BaseSettings`).
  2. Even once detected, `config_wire` would route a pydantic match to
     **modify** mode (`get_settings()` accessor), which **breaks moo-arc**
     because it has a module-level `settings` instance, not `get_settings()`.
- **Fix applied:** test/smoke files are skipped from `app_config` candidacy,
  broadened to **all languages** (py `test_*`/`conftest`/`_test.py`; ts/js
  `*.test.*`/`*.spec.*`; go `*_test.go`; + `tests`/`__tests__`/`spec`/`e2e`
  dirs).
- **Still open (root):** (a) broaden pydantic detection to match a `Settings`
  class extending a *project* base; (b) make `config_wire` route to **stub**
  when the detected config lacks `get_settings()`. Until both land, detection
  lands on a non-config file. **Cosmetic for moo-arc** (stub mode ignores the
  file), but a **real bug for any customer in modify mode** (would edit the
  wrong file).

### #2 — `task_planner` datetime crash ✅ fixed

- **Symptom:** `TypeError: 'str' object cannot be interpreted as an integer`
  at `emit_tasks_yaml` (`st.generated_at.replace(...)`).
- **Root cause:** `yaml.safe_load` parses an unquoted ISO timestamp to a
  `datetime`, which has no `str.replace`. Any slug-inventory with an unquoted
  `generated_at` crashes the planner.
- **Fix:** `str(st.generated_at)` before `.replace()`.

### #3 — consolidated LLM-agent sites marked `sibling-pair` ✅ fixed

- **Symptom:** the LLM-driven agent usage events (email-composed,
  inbound.classified, ptp.extracted, dispute.processed) came out
  `sibling-pair`, which would emit a cost lane at **each** agent **and** at the
  shared `llm_helpers` consolidation site → **double-count + empty cost lanes**.
- **Root cause:** `task_planner` faithfully honors the per-entry `pattern`, so
  the bug is upstream — **the inventory-build marked them `sibling-pair`**. The
  cost is consolidated once at `llm_helpers.py` (`arc.shared.llmport-call`), so
  the agents should be `usage-only`.
- **Fix:** added a **cost-consolidation rule** to discovery `SKILL.md` Phase 4:
  a usage event whose cost is a shared/consolidated cost emitted elsewhere is
  `usage-only`; the shared cost is `cost-only` at its single site. Detection
  signal: linked cost's `file` differs from the usage's, and the same cost
  feeds ≥2 usage events. Language-agnostic.

### #4 — slug resolution returns `None` ✅ fixed

- **Symptom:** every insert had `event_type_const: None` / `slugs_import_path:
  None` → callsites would fall back to inline literals, defeating Phase 1.8.
  (Verified: was **0/8** inserts resolved → after fix **8/8**.)
- **Root cause:** inventory entries carry no `product_slug` →
  `entry.get("product_slug","")` → `""` → `index.get("")` misses.
- **Fix:** `_default_product_slug()` — when an entry has no `product_slug`,
  fall back to the sole product in the slug index (single-product case);
  empty string for multi-product (requires explicit per-entry slug).

### #5 — helper template missing review fixes; Python-only ✅ fixed (py/ts/go)

- **Symptom:** the rendered helper had a **top-level `from moolabs import
  Moolabs`** (module fails to import without the SDK installed — breaks pytest
  collection / pre-push) and logged raw `str(err)` (**no secret scrub**). These
  were fixes made by hand in #531/#532 that had **never been folded back into
  the skill template**.
- **Second catch:** the first fix was applied **only to the Python template** —
  the suite serves TS and Go customers too.
- **Fix:**
  - **Python:** lazy `from moolabs import Moolabs` (`TYPE_CHECKING` + inside
    `get_client`) + `_scrub_secrets` on the rail.
  - **TypeScript:** `import type { Moolabs }` + dynamic `await import('moolabs')`
    in `getClient` + `scrubSecrets()` on the rail.
  - **Go:** `scrubSecrets()` (regexp) on the panic + log path. *(Lazy-import is
    N/A in Go — imports are compile-time; the customer must add the dep to
    build. Documented, not "fixed".)*

### (reverted) — `env_loader_scan` terraform over-detection ↩️

- **What happened:** the raw scan reported ~30 terraform surfaces (every
  `variables.tf` across a large centralized infra tree). First attempt: skip
  `modules/` + `accounts/`.
- **Why reverted:** that **broke `test_scan_repo_level_finds_centralized_terraform`**
  and was **wrong** — the repo-level scan is *designed* to find all centralized
  terraform (including the legitimate `modules/secrets`). The "30 surfaces" is a
  scale artifact of a large infra, not a scanner bug.
- **Correct resolution:** **scoping to the service's real deployment surface is
  the instrument layer's job** (config_wire / the execution agent), not an
  `env_loader` dir-skip. Left a comment in the scanner; the agent scopes at
  instrument time.

---

## Process issue (open) — execution hand-authored artifacts the skill templates for

While building, the run rendered the **helper** from its template
(`python-moolabs-client.py.j2`) ✅, but **hand-authored** the rest — even though
the skill **ships templates for them**:

| Artifact | Hand-authored as | Should render from |
|----------|------------------|--------------------|
| stub Settings | `moolabs_settings.py` (heredoc) | `python-moolabs-settings.py.j2` |
| slug constants | `slugs_arc.py` (python loop) | `slugs-python.j2` |
| `.env.example` wiring | `printf >>` append | `dotenv-moolabs.env.j2` |
| terraform stub | downgraded to a "PR checklist" | `terraform-moolabs.tf.j2` |

`docker-compose.yml` and `Dockerfile` have **no** templates → agent-authored is
correct for those.

**Why it matters:** the suite's value is template-driven, auditable emission.
Hand-authoring around existing templates produces equivalent output by luck, not
by the skill — exactly what dogfooding should catch. **Suggested skill change:**
make the execution step (Phase 2d) explicitly enumerate-and-render every
template referenced by `env_wire_tasks` / `slugs_emit_tasks` /
`config-wiring-plan.stub_emit_path`, so the agent can't silently substitute.
There is no deterministic driver that renders these today — the SKILL.md
delegates file emission to "the execution agent" in prose, which is easy to
shortcut.

---

## Fixes committed (moo-skills, branch `fix/cost-billing-v03-rough-edges`)

1. `task_planner.py` — #2 datetime + #4 slug default
2. `python-moolabs-client.py.j2` — #5 lazy import + scrub (Python)
3. `env_loader_scan.py` — #1 multi-language test-file skip (+ terraform skip, reverted next)
4. `env_loader_scan.py` — revert the over-aggressive terraform dir-skip
5. `typescript-moolabs-client.ts.j2` + `go-moolabs-client.go.j2` — #5 scrub (+ TS lazy)
6. discovery `SKILL.md` — #3 cost-consolidation → usage-only rule

Validation: `task_planner` tests 20 ✓, `env_loader_scan` tests 47 ✓, full
cost-billing sweep **168 ✓**; all three helper templates render (Python also
`py_compile`s).

---

## Still open

- **#1 root** — pydantic detection for project-base `Settings` + `config_wire`
  stub-when-no-`get_settings()`. (Cosmetic for moo-arc; real for modify-mode.)
- **Process issue** — render stub/slugs/.env/terraform from the shipped
  templates instead of hand-authoring; add an execution-step enumerator.
- The moo-arc **instrumentation build itself** was paused mid-way: framework +
  env-wiring committed locally (not pushed); the **8 code inserts** and the
  **adversarial review** were not completed.

---

## Process lessons (for the suite)

1. **Run the scripts on a real, awkward service.** Every defect here came from
   moo-arc's non-textbook shape (LLMPort consolidation, project-base config,
   module-level settings, centralized infra). Toy fixtures wouldn't surface them.
2. **A shallow fix moves the symptom.** The test-file skip (#1) just moved the
   misdetection from a test file to a smoke script — the root (detection + mode)
   was the real fix.
3. **Cross-language by default.** #1 and #5 were first fixed Python-only; the
   suite serves py/ts/go. Every fix needs the language matrix considered.
4. **Templates exist — use them.** The execution agent must render the shipped
   templates, not hand-author equivalents, or the dogfood signal is lost.

---

## Addendum — PR #11 (framework-capability-tree) discovery re-run, 2026-06-08

Ran the full chain **raw** against moo-arc after PR #11 merged to `main`. **Good news:**
PR #11 absorbed Dogfood #4/#5 — `slug_inventory` buckets per product correctly and the
consolidation double-count check is clean (7 `usage-only` + 1 `cost-only`). Bootstrap
Stages 1–4 **reused cleanly** (only finance **Q7** PII blocklist was missing → filled:
debtor email/phone/address, payment+bank, LLM prompt/response). The 3 inventories + slug
constants are correct. **But PR #11's discovery scripts have 3 install/detection bugs the
skill-folder fix should address (in priority order):**

### A. Install-layout `sys.path` bug — breaks `import strategies` for EVERY installed user (HIGH)
- **Where:** `discovery/scripts/env_loader_scan.py:34` (+ any script importing `strategies` / `framework_registry`).
- **Code:** `sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts")); import strategies  # noqa: E402`
- **Root cause:** `parents[2]/"shared"/"scripts"` is the **source** layout (`cost-billing/{discovery,shared}/scripts/`). `install.sh` flattens to sibling **`cost-billing-discovery/` + `cost-billing-shared/`** under `…/skills/`, so at runtime `parents[2]` = `…/skills/` and `skills/shared/scripts` doesn't exist (it's `skills/cost-billing-shared/scripts`). → `ModuleNotFoundError: No module named 'strategies'`. Works in the source/test tree; fails on every real install.
- **Repro:** `python3 ~/.claude-moolabs/skills/cost-billing-discovery/scripts/env_loader_scan.py --signed-yaml x --repo-root .` → ModuleNotFoundError. (Manual workaround: `PYTHONPATH=…/cost-billing-shared/scripts`.)
- **Fix:** make the insert layout-robust — insert whichever of `parents[2]/"shared"/"scripts"` OR `parents[2]/"cost-billing-shared"/"scripts"` exists; or have `install.sh` vendor `strategies.py`+`framework_registry.py` next to each importing script. Minimal: the two-path `if .exists()` try.

### B. Transitive-base detector misses a project-base Settings subclass — the #1 fix STILL doesn't fire (HIGH)
- **Where:** `shared/scripts/strategies.py` — `_first_transitive_settings_class()` / the pydantic-project-base node.
- **Symptom:** for `services/moo-arc/app/config.py` (`class Settings(CommonSettings)`), env_loader returns `pattern: unrecognized`, `app_config.file: None`, `node_id: ""`.
- **Root cause:** `CommonSettings` is `from python_common.config import Settings as CommonSettings` — it lives in a **different workspace package** (`packages/python-common/`), not under `services/moo-arc/`. The resolver's `search_roots` don't span monorepo workspace packages, so it can't follow the import to discover `CommonSettings → pydantic BaseSettings`; the chain breaks at the package boundary → unrecognized.
- **Expected for moo-arc:** `pattern: python-pydantic-settings-v2`, `app_config.file: services/moo-arc/app/config.py`, `wiring.mode: stub` (module-level `settings = Settings()`, NO `get_settings()` → stub).
- **Fix:** (1) extend `search_roots` to resolve workspace-package imports (`python_common.config` → `packages/python-common/.../config.py` via the uv/npm/go workspace members); OR (2) add a fallback node: a `*Settings*`/`*Config*` class in a `config.py`/`settings.py` that does `from pydantic import …` and extends an unresolvable base → classify as pydantic-settings **stub** mode. moo-arc MUST land on `stub` regardless (no `get_settings()`).

### C. `catalog_match` whole-repo scan + no `.direnv` skip → crash (MEDIUM)
- **Where:** `discovery/scripts/catalog_match.py` (file-walk).
- **Symptom:** `catalog_match.py . --catalog …` crashes `IsADirectoryError: [Errno 21] Is a directory: 'services/moo-meter/.direnv/flake-inputs/…/gen_cs_glue_version.py'`. Scoped (`catalog_match.py services/moo-arc --catalog …`) works → `0 cost-call sites` (correct: moo-arc LLM cost is behind `LLMPort`).
- **Root cause:** (1) walks the whole positional path (`.`), doesn't honor `--service`; (2) skip-set omits `.direnv` (Nix) and likely `.venv`/`node_modules`/`build`; (3) opens matched paths with no `is_file()` guard, so a directory named `*.py` (Nix store artifact) raises IsADirectoryError.
- **Fix:** add `.direnv`, `.venv`, `node_modules`, `dist`, `build`, `.git`, `__pycache__`, `.terraform` to the walk skip-set; guard `if not path.is_file(): continue`; and have the discovery driver pass the service path (or honor `--service`) instead of `.`.

### D. 30 over-detected terraform surfaces (LOW — known)
- env_loader still emits ~30 centralized-infra terraform surfaces (see the "#1b reverted" note above). Repo-level scan is comprehensive by design; scoping to the service's real surface stays the instrument layer's job. Re-noted only because it reappears in PR #11's raw output.

**Net:** under PR #11 the discovery *inventories* (core deliverable) are correct; the
*env-routing* (Phase 6) is unusable until **A + B** are fixed, which blocks instrument's
env-wiring (Phase 1.7). A and C are packaging/walk bugs (quick); **B is the real detection
gap** — the transitive base-resolution needs cross-package search or a stub fallback.

---

## Resolution status (2026-06-08, after the fixes)

| # | Status | Resolution |
|---|--------|-----------|
| **A** | ✅ **FIXED** (PR #12, merged) | `_locate_shared_base()` in env_loader_scan / config_wire / task_planner walks up and accepts `shared` OR `cost-billing-shared`, so the import resolves in BOTH the source monorepo and the installed sibling layout. `test_install_portability.py` (subprocess, sibling-dir sim) guards it. |
| **B** | ✅ **RESOLVED — it was a SYMPTOM of A**, not a separate detection gap | The transitive resolver's src-layout rglob fallback (`_resolve_module_files` / `_py_file_index`) DOES span monorepo workspace packages. Verified post-A: the real CLI path now returns `python-pydantic-settings-subclass` for `services/moo-arc/app/config.py`, with `python_common.config` resolving to `packages/python-common/src/python_common/config.py` and `wiring.mode: stub` (no `get_settings()`). B only manifested because A corrupted path resolution (the PYTHONPATH workaround). **Residual (doc, not code):** `--repo-root` MUST be the WORKSPACE ROOT (where `packages/` lives), not a service dir — else the fallback can't span workspaces. Documented in env_loader_scan `--repo-root` help + discovery SKILL.md Phase 6. |
| **C** | ✅ **FIXED** | `catalog_match._IGNORE_DIRS` gains `.direnv`, `.terraform`, `.tox`, `.mypy_cache`; a robust `if not py.is_file(): continue` guard in `scan_repo` defends against ANY directory named `*.py` (Nix store artifacts). Regression tests in `test_catalog_match.py::ScanRepoRobustness`. `--service` scoping remains optional (pass the service path as the positional arg). |
| **D** | accepted (by design) | Repo-level terraform scan is comprehensive on purpose; scoping to the service's real surface stays the instrument layer's job. No code change. |

**Revised net:** with A fixed, env-routing (Phase 6) **works** for moo-arc — the doc's
"unusable until A+B" conclusion is superseded. The PII/PHI blocklist also moved to a
3-way ownership split (regime=Finance / categories=CPO / field-paths=Engineer) after a
separate role-assignment finding in the same dogfood session.
