# Cost-billing env-routing + slugs — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the discovery side (env_loader_scan.py + slug_inventory.py) and the bootstrap-team-engineer Q14b question for the v0.3 env-routing + event-slug-constants migration. After this plan ships, two new inventory files (env-routing-inventory.yaml + slug-inventory.yaml) appear in customer-context, ready for Phase B/C to consume.

**Architecture:** Two new standalone Python scripts in `skills/cost-billing/discovery/scripts/`, one new recognition catalog in `skills/cost-billing/shared/assets/`, one new question in `bootstrap-team-engineer/SKILL.md`, and stdlib-unittest tests for each script following the existing `test_*.py` pattern. No new dependencies. Each script is a single-file CLI with argparse — matches the convention established by `repo_scan.py` / `catalog_match.py` / `context_classifier.py` / `billing_gate.py`. Hand-rolled YAML emit matches `sdk_snapshot.py` (no PyYAML runtime dep).

**Tech Stack:** Python 3.10+ (existing); stdlib `unittest` for tests (existing); stdlib `re` + `ast` + `tempfile` for scanning; bash test driver `test-suite.sh` Phase 8 auto-discovers `test_*.py` (existing).

**Spec:** `docs/superpowers/specs/2026-06-06-cost-billing-env-routing-and-slugs-design.md`

**Branch:** `spec/cost-billing-env-routing-design` (off `main` at PR #2's merge commit).

**Out of scope for Phase A** (separate plans):
- Phase B: instrument env-wire emission (helper template rewrites + config_wire.py)
- Phase C: instrument slugs emission (slugs templates + framework callsite updates)
- Phase D: e2e fixture + adversarial-review tuning

---

## File Structure (Phase A)

**Create:**
- `skills/cost-billing/shared/assets/env-loader-patterns.yaml` — recognition catalog (10 patterns across Python/TS/Go)
- `skills/cost-billing/discovery/scripts/env_loader_scan.py` — env-routing scanner
- `skills/cost-billing/discovery/scripts/test_env_loader_scan.py` — unit tests
- `skills/cost-billing/discovery/scripts/slug_inventory.py` — slug inventory builder
- `skills/cost-billing/discovery/scripts/test_slug_inventory.py` — unit tests

**Modify:**
- `skills/cost-billing/bootstrap-team-engineer/SKILL.md` — add Q14b
- `skills/cost-billing/bootstrap-team-engineer/assets/04-final.schema.yaml` — add `env_loader_granularity` field
- `skills/cost-billing/discovery/SKILL.md` — document new phases

**Verify (no changes):**
- `skills/cost-billing/scripts/test-suite.sh` — Phase 8 auto-discovers the new `test_*.py` files; no change needed.

---

## Task 1: Branch + baseline verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm branch + clean tree**

```bash
git branch --show-current
git status -sb | head -3
```

Expected: branch `spec/cost-billing-env-routing-design`; tree clean except possibly `__pycache__/` dirs (ignored).

- [ ] **Step 2: Confirm smoke baseline is green**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 60    FAIL: 0`. If anything is failing, stop and investigate before proceeding.

- [ ] **Step 3: Confirm Python version**

```bash
python3 --version
```

Expected: Python 3.10 or later (existing scripts use 3.10+ syntax like `str | None` union types).

---

## Task 2: Create env-loader-patterns.yaml catalog

**Files:**
- Create: `skills/cost-billing/shared/assets/env-loader-patterns.yaml`

- [ ] **Step 1: Create the catalog file**

```yaml
# Env-loader pattern catalog — drives env_loader_scan.py recognition.
# One entry per recognized pattern. Each pattern is checked in order of priority
# (higher first); the first high-confidence match wins per service.
#
# Fields:
#   id              — unique pattern id (used in env-routing-inventory.yaml output)
#   language        — python | typescript | go
#   detection_signal— human-readable description of what triggers the match
#   import_signals  — list of import-statement regexes (any-match → +0.3 confidence)
#   structural_signals — list of structural-pattern regexes (any-match → +0.4 confidence)
#   wire_target     — what env_loader_scan flags for instrument's Phase 1.7 to emit
#   priority        — higher means checked first when multiple patterns hit
#
# Confidence band rules (computed by env_loader_scan):
#   - import + structural hit → confidence: high (0.9+)
#   - only structural hit     → confidence: medium (0.5-0.8)
#   - only import hit         → confidence: low (0.3-0.5)
#   - no hit                  → pattern not detected for this service
#   - low confidence triggers stub_required: true downstream

patterns:
  # ── Python (4 patterns) ────────────────────────────────────────────────
  - id: python-pydantic-settings-v2
    language: python
    detection_signal: "pydantic-settings BaseSettings subclass"
    import_signals:
      - "from\\s+pydantic_settings\\s+import\\s+.*BaseSettings"
    structural_signals:
      - "class\\s+\\w+\\(BaseSettings\\)"
    wire_target:
      kind: add_pydantic_settings_field
      field_template: "moolabs_api_key: SecretStr = Field(..., env=\"MOOLABS_API_KEY\")"
    priority: 100

  - id: python-pydantic-v1-settings
    language: python
    detection_signal: "pydantic v1 BaseSettings subclass"
    import_signals:
      - "from\\s+pydantic\\s+import\\s+.*BaseSettings"
    structural_signals:
      - "class\\s+\\w+\\(BaseSettings\\)"
    wire_target:
      kind: add_pydantic_settings_field
      field_template: "moolabs_api_key: SecretStr = Field(..., env=\"MOOLABS_API_KEY\")"
    priority: 90

  - id: python-decouple
    language: python
    detection_signal: "python-decouple config() reads"
    import_signals:
      - "from\\s+decouple\\s+import\\s+config"
    structural_signals:
      - "config\\(['\"][A-Z_]+['\"]"
    wire_target:
      kind: add_decouple_line
      line_template: "MOOLABS_API_KEY = config('MOOLABS_API_KEY')"
    priority: 80

  - id: python-dotenv-os-getenv
    language: python
    detection_signal: "python-dotenv + os.getenv pattern"
    import_signals:
      - "from\\s+dotenv\\s+import\\s+load_dotenv"
      - "load_dotenv\\("
    structural_signals:
      - "os\\.(getenv|environ\\.get|environ\\[)"
    wire_target:
      kind: add_os_getenv_line
      line_template: "MOOLABS_API_KEY = os.getenv(\"MOOLABS_API_KEY\")"
    priority: 70

  # ── TypeScript (3 patterns) ────────────────────────────────────────────
  - id: ts-zod-env-schema
    language: typescript
    detection_signal: "zod env-validation schema in env.ts / config.ts"
    import_signals:
      - "import\\s+\\{[^}]*\\bz\\b[^}]*\\}\\s+from\\s+['\"]zod['\"]"
      - "from\\s+['\"]zod['\"]"
    structural_signals:
      - "z\\.object\\(\\s*\\{"
    wire_target:
      kind: add_zod_field
      field_template: "MOOLABS_API_KEY: z.string().min(1)"
    priority: 100

  - id: ts-process-env-direct
    language: typescript
    detection_signal: "multiple process.env.X reads in a config module"
    import_signals: []
    structural_signals:
      - "process\\.env\\.[A-Z_]+"
    wire_target:
      kind: add_process_env_line
      line_template: "export const MOOLABS_API_KEY = process.env.MOOLABS_API_KEY ?? \"\";"
    priority: 70

  - id: ts-env-var-library
    language: typescript
    detection_signal: "env-var library .get() pattern"
    import_signals:
      - "import\\s+.*env-var"
      - "from\\s+['\"]env-var['\"]"
    structural_signals:
      - "env\\.get\\(['\"]"
    wire_target:
      kind: add_env_var_line
      line_template: "export const MOOLABS_API_KEY = env.get(\"MOOLABS_API_KEY\").required().asString();"
    priority: 90

  # ── Go (3 patterns) ────────────────────────────────────────────────────
  - id: go-viper
    language: go
    detection_signal: "viper.SetEnvPrefix / viper.AutomaticEnv usage"
    import_signals:
      - "spf13/viper"
    structural_signals:
      - "viper\\.(SetEnvPrefix|AutomaticEnv|BindEnv)"
    wire_target:
      kind: add_viper_bindenv
      line_template: "viper.BindEnv(\"moolabs_api_key\", \"MOOLABS_API_KEY\")"
    priority: 100

  - id: go-envconfig
    language: go
    detection_signal: "kelseyhightower/envconfig struct tags"
    import_signals:
      - "kelseyhightower/envconfig"
    structural_signals:
      - "envconfig:\"[A-Z_]+\""
    wire_target:
      kind: add_envconfig_field
      field_template: "MoolabsAPIKey string `envconfig:\"MOOLABS_API_KEY\" required:\"true\"`"
    priority: 90

  - id: go-os-getenv
    language: go
    detection_signal: "raw os.Getenv reads in a config.go"
    import_signals: []
    structural_signals:
      - "os\\.Getenv\\(['\"]"
    wire_target:
      kind: add_os_getenv_line
      line_template: "MoolabsAPIKey := os.Getenv(\"MOOLABS_API_KEY\")"
    priority: 70
```

- [ ] **Step 2: Verify YAML parses (catches typos / indent errors immediately)**

```bash
python3 -c "import yaml; d = yaml.safe_load(open('skills/cost-billing/shared/assets/env-loader-patterns.yaml')); print(f'OK: {len(d[\"patterns\"])} patterns')"
```

Expected: `OK: 10 patterns`

- [ ] **Step 3: Confirm smoke still passes (Phase 2 parses the new YAML)**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 60    FAIL: 0` (the YAML-parse phase auto-discovers the new asset).

- [ ] **Step 4: Commit**

```bash
git add skills/cost-billing/shared/assets/env-loader-patterns.yaml
git commit -m "feat(cost-billing/shared): add env-loader-patterns recognition catalog

10-pattern catalog driving env_loader_scan.py:
- Python: pydantic-settings v2, pydantic v1 BaseSettings, python-decouple, dotenv+os.getenv
- TypeScript: zod env schema, process.env direct, env-var library
- Go: viper, kelseyhightower/envconfig, raw os.Getenv

Each pattern declares its detection signals (import + structural regexes),
wire_target (what instrument's Phase 1.7 emits when the pattern matches),
and priority (higher = checked first when multiple match).

Confidence bands computed by the scanner:
  import + structural  → high  (0.9+)
  structural only      → medium (0.5-0.8)
  import only          → low    (0.3-0.5)
  none                 → not detected

Low-confidence and not-detected both trigger stub_required: true
downstream (Phase 1.7 emits a stub Settings class instead of in-place
modification)."
```

---

## Task 3: env_loader_scan.py — skeleton + Python pattern detection

**Files:**
- Create: `skills/cost-billing/discovery/scripts/env_loader_scan.py`
- Create: `skills/cost-billing/discovery/scripts/test_env_loader_scan.py`

- [ ] **Step 1: Write failing tests first (TDD red)**

Create `skills/cost-billing/discovery/scripts/test_env_loader_scan.py`:

```python
#!/usr/bin/env python3
"""Unit tests for env_loader_scan.py (Phase 1.7-scan).

Stdlib unittest; runs in the bash smoke suite's Phase 8. Fixtures are
generated in-process via tempfile.TemporaryDirectory — no checked-in
fixture directory.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env_loader_scan as els  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "skills" / "cost-billing" / "shared" / "assets" / "env-loader-patterns.yaml"


class CatalogLoad(unittest.TestCase):
    def test_catalog_has_ten_patterns(self):
        catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.assertEqual(len(catalog), 10)

    def test_catalog_groups_by_language(self):
        catalog = els.load_pattern_catalog(CATALOG_PATH)
        by_lang = els.group_patterns_by_language(catalog)
        self.assertEqual(len(by_lang["python"]), 4)
        self.assertEqual(len(by_lang["typescript"]), 3)
        self.assertEqual(len(by_lang["go"]), 3)


class PythonPydanticSettingsV2(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_pydantic_settings_v2_high_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from pydantic_settings import BaseSettings\n"
                "from pydantic import Field\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
                "    redis_url: str = Field(..., env='REDIS_URL')\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")
            self.assertEqual(result.confidence, "high")

    def test_pydantic_settings_v2_finds_insertion_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
                "    redis_url: str\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            # Insertion line is the last field of the class — line 5 here (1-indexed).
            self.assertEqual(result.line_to_insert, 5)


class PythonPydanticV1Settings(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_pydantic_v1_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "settings.py"
            cfg.write_text(
                "from pydantic import BaseSettings\n"
                "\n"
                "class Config(BaseSettings):\n"
                "    api_url: str\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertEqual(result.pattern_id, "python-pydantic-v1-settings")


class PythonDecouple(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_decouple(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from decouple import config\n"
                "\n"
                "DATABASE_URL = config('DATABASE_URL')\n"
                "REDIS_URL = config('REDIS_URL')\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertEqual(result.pattern_id, "python-decouple")


class PythonDotenvOsGetenv(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_dotenv_os_getenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "import os\n"
                "from dotenv import load_dotenv\n"
                "\n"
                "load_dotenv()\n"
                "DATABASE_URL = os.getenv('DATABASE_URL', '')\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertEqual(result.pattern_id, "python-dotenv-os-getenv")


class UnrecognizedFileReturnsNone(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_random_python_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "random.py"
            f.write_text("def hello():\n    return 42\n")
            result = els.scan_file(f, self.python_patterns)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests — must FAIL (env_loader_scan.py doesn't exist yet)**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'env_loader_scan'` — confirms test file is correctly wired and we have RED.

- [ ] **Step 3: Write the minimal implementation to make Python-pattern tests pass**

Create `skills/cost-billing/discovery/scripts/env_loader_scan.py`:

```python
#!/usr/bin/env python3
"""Phase 1.7-scan — env-loader pattern detection for /cost-billing-discovery.

Scans each service's source tree for recognized env-loading patterns
(pydantic-settings, dotenv, viper, etc.). Produces
.moolabs/customer-context/env-routing-inventory.yaml describing the
recognized pattern (or stub_required=true when none found) and the
deployment-surface insertion points (Terraform, k8s, docker-compose,
.env.example).

Pattern catalog lives at shared/assets/env-loader-patterns.yaml.

Usage:
    python env_loader_scan.py \\
        --signed-yaml .moolabs/chain/04-final.signed.yaml \\
        --customer-context-dir .moolabs/customer-context \\
        --catalog skills/cost-billing/shared/assets/env-loader-patterns.yaml \\
        [--repo-root .]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Catalog loading
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    id: str
    language: str
    detection_signal: str
    import_signals: list[str]
    structural_signals: list[str]
    wire_target: dict[str, str]
    priority: int


def load_pattern_catalog(path: Path) -> list[Pattern]:
    """Load env-loader-patterns.yaml. Uses PyYAML when available, falls back
    to a minimal hand-rolled parser otherwise (matches sdk_snapshot.py's
    no-runtime-dep approach for the codemod environment).
    """
    try:
        import yaml
        data = yaml.safe_load(path.read_text())
    except ImportError:
        data = _hand_rolled_yaml_load(path)

    out: list[Pattern] = []
    for entry in data.get("patterns", []):
        out.append(Pattern(
            id=entry["id"],
            language=entry["language"],
            detection_signal=entry["detection_signal"],
            import_signals=list(entry.get("import_signals", []) or []),
            structural_signals=list(entry.get("structural_signals", []) or []),
            wire_target=entry["wire_target"],
            priority=int(entry.get("priority", 50)),
        ))
    return out


def group_patterns_by_language(patterns: list[Pattern]) -> dict[str, list[Pattern]]:
    """Group patterns by language, sorted by priority descending within each."""
    by_lang: dict[str, list[Pattern]] = {"python": [], "typescript": [], "go": []}
    for p in patterns:
        by_lang.setdefault(p.language, []).append(p)
    for lang in by_lang:
        by_lang[lang].sort(key=lambda p: -p.priority)
    return by_lang


def _hand_rolled_yaml_load(path: Path) -> dict:
    """Minimal YAML reader for the pattern catalog when PyYAML is absent.
    Only supports the catalog's known shape; not a general YAML parser.
    """
    # Phase A leans on PyYAML being available in the test environment (smoke
    # already imports yaml). This fallback is here so the script can run in
    # a customer's codemod environment that lacks PyYAML — Phase B's instrument
    # side will revisit this.
    raise NotImplementedError(
        "PyYAML required for env_loader_scan.py in Phase A. "
        "Install pyyaml or run from the smoke environment."
    )


# ──────────────────────────────────────────────────────────────────────
# Per-file scan
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    """Result of scanning one file against the relevant patterns."""
    pattern_id: str
    file: str
    line_to_insert: int
    confidence: str  # "high" | "medium" | "low"
    confidence_score: float  # 0.0 to 1.0
    evidence: list[str] = field(default_factory=list)
    wire_target: dict[str, str] = field(default_factory=dict)


# Confidence-band thresholds. Matches catalog comment.
_HIGH_THRESHOLD = 0.85
_MEDIUM_THRESHOLD = 0.50
_LOW_THRESHOLD = 0.30


def _band(score: float) -> str:
    if score >= _HIGH_THRESHOLD:
        return "high"
    if score >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _signal_score(text: str, regexes: list[str]) -> tuple[float, list[str]]:
    """Return (signal_strength, matched_evidence_lines). 1.0 if any regex
    matches, 0.0 if none. Evidence is the first matching line for each regex.
    """
    if not regexes:
        return 0.0, []
    evidence: list[str] = []
    matched = False
    for rx in regexes:
        for i, line in enumerate(text.splitlines(), start=1):
            if re.search(rx, line):
                evidence.append(f"line {i}: {line.strip()[:120]}")
                matched = True
                break
    return (1.0 if matched else 0.0), evidence


def _python_insert_line(text: str, class_pattern: str) -> int:
    """For Python class-based patterns: return the last field line of the
    first matching class (1-indexed). For non-class patterns: return the
    last non-blank, non-import line + 1.
    """
    lines = text.splitlines()

    # Try to find the class block
    in_class = False
    class_re = re.compile(class_pattern)
    last_field_line = 0
    class_start = 0
    for i, line in enumerate(lines, start=1):
        if not in_class and class_re.search(line):
            in_class = True
            class_start = i
            last_field_line = i
            continue
        if in_class:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped == "":
                continue
            # Dedented back to module level → end of class
            if indent == 0 and stripped:
                break
            # A class body line — track as last field if it looks like a field
            if ":" in stripped or "=" in stripped:
                last_field_line = i

    if last_field_line > class_start:
        return last_field_line

    # Fallback for non-class patterns: insert after the last non-blank line
    for i in range(len(lines), 0, -1):
        if lines[i - 1].strip():
            return i
    return 1


def scan_file(path: Path, patterns: list[Pattern]) -> ScanResult | None:
    """Scan a single file against the patterns. Return the highest-confidence
    match, or None if no pattern reaches the LOW threshold."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None

    best: ScanResult | None = None
    best_score: float = 0.0

    for p in patterns:
        import_score, import_evidence = _signal_score(text, p.import_signals)
        struct_score, struct_evidence = _signal_score(text, p.structural_signals)

        # Combine: structural weighted higher than imports.
        # Both → 0.95 (high); structural only → 0.65 (medium);
        # import only → 0.35 (low); neither → 0.0
        if import_score > 0 and struct_score > 0:
            score = 0.95
        elif struct_score > 0:
            score = 0.65
        elif import_score > 0:
            score = 0.35
        else:
            score = 0.0

        if score < _LOW_THRESHOLD:
            continue

        if score > best_score:
            best_score = score
            # For class-based Python patterns, derive insertion line from the class
            line_to_insert = 1
            if p.structural_signals:
                line_to_insert = _python_insert_line(text, p.structural_signals[0])
            best = ScanResult(
                pattern_id=p.id,
                file=str(path),
                line_to_insert=line_to_insert,
                confidence=_band(score),
                confidence_score=score,
                evidence=import_evidence + struct_evidence,
                wire_target=p.wire_target,
            )

    return best


# ──────────────────────────────────────────────────────────────────────
# CLI (skeleton — fleshed out in later tasks)
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signed-yaml", default=".moolabs/chain/04-final.signed.yaml")
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--catalog", required=True, help="path to env-loader-patterns.yaml")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    # Phase A Task 3 ships catalog load + per-file scan only. Granularity,
    # deployment surfaces, and YAML emit land in later tasks.
    catalog = load_pattern_catalog(Path(args.catalog))
    print(f"loaded {len(catalog)} patterns", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -5
```

Expected: `OK` with the test count (~7 tests passing).

- [ ] **Step 5: Confirm smoke still 60/60**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 61    FAIL: 0` (count goes up by 1 because the new `test_env_loader_scan.py` is auto-discovered by Phase 8).

- [ ] **Step 6: Commit**

```bash
git add skills/cost-billing/discovery/scripts/env_loader_scan.py \
        skills/cost-billing/discovery/scripts/test_env_loader_scan.py
git commit -m "feat(cost-billing/discovery): env_loader_scan.py skeleton + Python pattern detection

Scanner module + 4 Python-pattern recognizers:
- python-pydantic-settings-v2 (BaseSettings from pydantic_settings)
- python-pydantic-v1-settings (BaseSettings from pydantic)
- python-decouple (config() reads)
- python-dotenv-os-getenv (load_dotenv + os.getenv)

Confidence bands: import + structural → high (0.95);
structural only → medium (0.65); import only → low (0.35);
neither → no match.

scan_file() returns the highest-confidence match per file.
_python_insert_line() finds the last field of the matched class for
pydantic-style patterns (Phase 1.7 will use this for AST insertion).

Stdlib unittest; auto-discovered by test-suite.sh Phase 8.
PyYAML used for catalog load (smoke already has it; codemod-env fallback
deferred to Phase B)."
```

---

## Task 4: env_loader_scan.py — TypeScript pattern detection

**Files:**
- Modify: `skills/cost-billing/discovery/scripts/test_env_loader_scan.py` — add TS tests
- Modify: `skills/cost-billing/discovery/scripts/env_loader_scan.py` — add TS-aware insert-line helper

- [ ] **Step 1: Add failing TS tests**

Append to `test_env_loader_scan.py` (above the `if __name__ == "__main__"` block):

```python
class TypeScriptZodEnv(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_detects_zod_env_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "env.ts"
            f.write_text(
                "import { z } from 'zod';\n"
                "\n"
                "export const envSchema = z.object({\n"
                "  DATABASE_URL: z.string(),\n"
                "  REDIS_URL: z.string(),\n"
                "});\n"
            )
            result = els.scan_file(f, self.ts_patterns)
            self.assertEqual(result.pattern_id, "ts-zod-env-schema")
            self.assertEqual(result.confidence, "high")


class TypeScriptProcessEnvDirect(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_detects_process_env_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ts"
            f.write_text(
                "export const DATABASE_URL = process.env.DATABASE_URL ?? '';\n"
                "export const REDIS_URL = process.env.REDIS_URL ?? '';\n"
                "export const API_PORT = process.env.API_PORT ?? '8080';\n"
            )
            result = els.scan_file(f, self.ts_patterns)
            self.assertEqual(result.pattern_id, "ts-process-env-direct")
            # Only structural hit (no import) → medium confidence.
            self.assertEqual(result.confidence, "medium")


class TypeScriptEnvVarLibrary(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_detects_env_var_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ts"
            f.write_text(
                "import * as env from 'env-var';\n"
                "\n"
                "export const DATABASE_URL = env.get('DATABASE_URL').required().asString();\n"
            )
            result = els.scan_file(f, self.ts_patterns)
            self.assertEqual(result.pattern_id, "ts-env-var-library")


class TypeScriptInsertLineHeuristic(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_zod_schema_insert_line_is_inside_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "env.ts"
            f.write_text(
                "import { z } from 'zod';\n"          # line 1
                "\n"                                   # line 2
                "export const envSchema = z.object({\n"  # line 3
                "  DATABASE_URL: z.string(),\n"       # line 4
                "  REDIS_URL: z.string(),\n"          # line 5
                "});\n"                               # line 6
            )
            result = els.scan_file(f, self.ts_patterns)
            # Insertion line should be inside the object — line 5 (the last
            # field before the closing brace).
            self.assertEqual(result.line_to_insert, 5)
```

- [ ] **Step 2: Run tests — TS tests must FAIL (insert-line heuristic is Python-shaped)**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -10
```

Expected: TS pattern tests pass (regex match works) BUT `test_zod_schema_insert_line_is_inside_object` FAILS — the Python-class insert-line heuristic doesn't understand TS object syntax.

- [ ] **Step 3: Add a TS-aware insert-line helper to env_loader_scan.py**

In `env_loader_scan.py`, ADD a new helper above `scan_file()`:

```python
def _ts_insert_line(text: str, opening_pattern: str) -> int:
    """For TS object/schema patterns: return the last entry line of the
    first matched balanced-brace block (1-indexed). Falls back to last
    non-blank line + 1 when no balanced match.
    """
    lines = text.splitlines()
    opening_re = re.compile(opening_pattern)
    for i, line in enumerate(lines, start=1):
        if opening_re.search(line):
            # Found the opener; now find the closing brace and the last
            # content line inside.
            depth = line.count("{") - line.count("}")
            if depth <= 0:
                continue
            last_content_line = i
            for j in range(i + 1, len(lines) + 1):
                inner = lines[j - 1]
                inner_stripped = inner.strip()
                if inner_stripped and not inner_stripped.startswith("//"):
                    last_content_line = j
                depth += inner.count("{") - inner.count("}")
                if depth <= 0:
                    # We've closed the block — return the line BEFORE the closer.
                    # If the closer is on a line by itself, last_content_line
                    # already points at the last content line.
                    if inner_stripped in {"}", "};", "})", "});", "}))", "}));"}:
                        return last_content_line
                    return j
            return last_content_line
    # Fallback: last non-blank line
    for i in range(len(lines), 0, -1):
        if lines[i - 1].strip():
            return i
    return 1
```

- [ ] **Step 4: Update `scan_file()` to dispatch by language to the right insert-line helper**

In `env_loader_scan.py`, MODIFY the `scan_file()` function — replace the existing `line_to_insert = ...` block with:

```python
            line_to_insert = 1
            if p.structural_signals:
                if p.language == "python":
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
                elif p.language == "typescript":
                    line_to_insert = _ts_insert_line(text, p.structural_signals[0])
                else:
                    # Go inserts handled in Task 5
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
```

- [ ] **Step 5: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -3
```

Expected: `OK` with all tests (Python + TS) passing.

- [ ] **Step 6: Confirm smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 61    FAIL: 0`.

- [ ] **Step 7: Commit**

```bash
git add skills/cost-billing/discovery/scripts/env_loader_scan.py \
        skills/cost-billing/discovery/scripts/test_env_loader_scan.py
git commit -m "feat(cost-billing/discovery): env_loader_scan TypeScript pattern detection

3 TS recognizers:
- ts-zod-env-schema (z.object schema, import zod)
- ts-process-env-direct (multiple process.env.X reads, no import signal)
- ts-env-var-library (env.get() pattern with env-var import)

_ts_insert_line() heuristic: finds the last content line inside the
first balanced-brace block opened by the structural pattern. Handles
the zod-schema case where the insertion point is INSIDE the object
literal, not at the end of the file.

scan_file() now dispatches by language to the right insert-line helper.
process-env-direct intentionally yields medium-confidence (no import
signal in the catalog) to nudge the operator toward a stub-merge UX
later in Phase 1.7."
```

---

## Task 5: env_loader_scan.py — Go pattern detection

**Files:**
- Modify: `skills/cost-billing/discovery/scripts/test_env_loader_scan.py` — add Go tests
- Modify: `skills/cost-billing/discovery/scripts/env_loader_scan.py` — add Go-aware insert-line helper

- [ ] **Step 1: Add failing Go tests**

Append to `test_env_loader_scan.py`:

```python
class GoViper(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_detects_viper(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"
                "\n"
                "import \"github.com/spf13/viper\"\n"
                "\n"
                "func Init() {\n"
                "    viper.SetEnvPrefix(\"APP\")\n"
                "    viper.AutomaticEnv()\n"
                "    viper.BindEnv(\"database_url\")\n"
                "}\n"
            )
            result = els.scan_file(f, self.go_patterns)
            self.assertEqual(result.pattern_id, "go-viper")
            self.assertEqual(result.confidence, "high")


class GoEnvconfig(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_detects_envconfig(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"
                "\n"
                "import \"github.com/kelseyhightower/envconfig\"\n"
                "\n"
                "type Config struct {\n"
                "    DatabaseURL string `envconfig:\"DATABASE_URL\" required:\"true\"`\n"
                "    RedisURL    string `envconfig:\"REDIS_URL\"`\n"
                "}\n"
            )
            result = els.scan_file(f, self.go_patterns)
            self.assertEqual(result.pattern_id, "go-envconfig")


class GoOsGetenv(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_detects_os_getenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"
                "\n"
                "import \"os\"\n"
                "\n"
                "var DatabaseURL = os.Getenv(\"DATABASE_URL\")\n"
                "var RedisURL = os.Getenv(\"REDIS_URL\")\n"
            )
            result = els.scan_file(f, self.go_patterns)
            self.assertEqual(result.pattern_id, "go-os-getenv")


class GoEnvconfigInsertLine(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_envconfig_insert_line_is_inside_struct(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"                                                     # 1
                "\n"                                                                    # 2
                "import \"github.com/kelseyhightower/envconfig\"\n"                     # 3
                "\n"                                                                    # 4
                "type Config struct {\n"                                                # 5
                "    DatabaseURL string `envconfig:\"DATABASE_URL\"`\n"                # 6
                "    RedisURL    string `envconfig:\"REDIS_URL\"`\n"                   # 7
                "}\n"                                                                   # 8
            )
            result = els.scan_file(f, self.go_patterns)
            # Insertion point: last field of the struct → line 7.
            self.assertEqual(result.line_to_insert, 7)
```

- [ ] **Step 2: Run tests — Go insert-line test must FAIL**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -10
```

Expected: pattern-detection tests pass; insert-line test fails because we currently fall through to `_python_insert_line` for Go.

- [ ] **Step 3: Add a Go-aware insert-line helper**

In `env_loader_scan.py`, add above `scan_file()`:

```python
def _go_insert_line(text: str, opening_pattern: str) -> int:
    """For Go struct/func patterns: find the last content line inside the
    first matched balanced-brace block (1-indexed). Same shape as
    _ts_insert_line but tuned for Go's `type X struct { ... }` and
    `func X() { ... }` layouts.
    """
    # Go's syntax is brace-balanced like TS — the helper is structurally
    # identical. Reuse _ts_insert_line; named separately so future Go-only
    # tuning (e.g. struct-tag awareness) has a place to land.
    return _ts_insert_line(text, opening_pattern)
```

- [ ] **Step 4: Update `scan_file()` dispatch for Go**

In `env_loader_scan.py`, MODIFY the dispatch block inside `scan_file()`:

```python
            line_to_insert = 1
            if p.structural_signals:
                if p.language == "python":
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
                elif p.language == "typescript":
                    line_to_insert = _ts_insert_line(text, p.structural_signals[0])
                elif p.language == "go":
                    line_to_insert = _go_insert_line(text, p.structural_signals[0])
                else:
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
```

- [ ] **Step 5: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 6: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/discovery/scripts/env_loader_scan.py \
        skills/cost-billing/discovery/scripts/test_env_loader_scan.py
git commit -m "feat(cost-billing/discovery): env_loader_scan Go pattern detection

3 Go recognizers:
- go-viper (spf13/viper SetEnvPrefix/AutomaticEnv/BindEnv)
- go-envconfig (kelseyhightower/envconfig struct tags)
- go-os-getenv (raw os.Getenv reads)

_go_insert_line() delegates to _ts_insert_line — Go and TS share
brace-balanced syntax, so the heuristic is identical. Named separately
to give future Go-specific tuning (struct-tag awareness, build-tag
handling) a place to land."
```

---

## Task 6: env_loader_scan.py — service-level scan + conflict resolution

**Files:**
- Modify: `skills/cost-billing/discovery/scripts/test_env_loader_scan.py`
- Modify: `skills/cost-billing/discovery/scripts/env_loader_scan.py`

- [ ] **Step 1: Add failing tests for service-level scan**

Append to `test_env_loader_scan.py`:

```python
class ServiceScan(unittest.TestCase):
    """Scan a service directory (multiple files) and return the best match."""

    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)

    def test_scan_service_finds_pydantic_settings_in_config_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "payments-api"
            (svc / "app").mkdir(parents=True)
            (svc / "app" / "main.py").write_text("from app.config import Settings\n")
            (svc / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
            )
            result = els.scan_service(svc, "python", self.catalog)
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")
            self.assertTrue(result.file.endswith("config.py"))

    def test_scan_service_picks_highest_confidence_when_multiple_match(self):
        """If a service has BOTH pydantic-settings AND a dotenv+os.getenv
        config file, pick the higher-priority pydantic one."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "payments-api"
            (svc / "app").mkdir(parents=True)
            # The "real" config — pydantic-settings, high confidence
            (svc / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
            )
            # A legacy helper using dotenv + os.getenv (lower priority)
            (svc / "app" / "legacy_env.py").write_text(
                "from dotenv import load_dotenv\n"
                "import os\n"
                "load_dotenv()\n"
                "DB = os.getenv('DB')\n"
            )
            result = els.scan_service(svc, "python", self.catalog)
            # Pydantic-settings priority=100 vs dotenv priority=70 → pydantic wins
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")

    def test_scan_service_returns_none_when_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "no-config"
            svc.mkdir(parents=True)
            (svc / "main.py").write_text("def main(): pass\n")
            result = els.scan_service(svc, "python", self.catalog)
            self.assertIsNone(result)

    def test_scan_service_skips_irrelevant_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "py-svc"
            svc.mkdir(parents=True)
            # A Go config file in a Python service — should be skipped when
            # we ask for language=python.
            (svc / "config.go").write_text(
                "import \"github.com/spf13/viper\"\n"
                "viper.AutomaticEnv()\n"
            )
            result = els.scan_service(svc, "python", self.catalog)
            self.assertIsNone(result)
```

- [ ] **Step 2: Run — must FAIL (no scan_service yet)**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -10
```

Expected: `AttributeError: module 'env_loader_scan' has no attribute 'scan_service'`.

- [ ] **Step 3: Add `scan_service()` to env_loader_scan.py**

In `env_loader_scan.py`, add below `scan_file()`:

```python
# ──────────────────────────────────────────────────────────────────────
# Service-level scan
# ──────────────────────────────────────────────────────────────────────

_EXTENSION_BY_LANGUAGE = {
    "python": (".py",),
    "typescript": (".ts", ".tsx", ".mts"),
    "go": (".go",),
}

# Skip directories that never contain config (saves walk time + avoids
# false positives in vendored dependencies).
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "vendor", "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
})


def scan_service(
    service_root: Path,
    language: str,
    catalog: list[Pattern],
) -> ScanResult | None:
    """Walk a service directory and return the best env-loader-pattern match
    found, or None if no file passes the LOW threshold.

    Conflict resolution: the highest-priority pattern wins. If two files
    match the SAME pattern, the deepest match (most-specific path) wins —
    `app/config.py` beats `app/legacy/old_config.py`.
    """
    by_lang = group_patterns_by_language(catalog)
    patterns = by_lang.get(language, [])
    if not patterns:
        return None

    extensions = _EXTENSION_BY_LANGUAGE.get(language, ())
    if not extensions:
        return None

    candidates: list[ScanResult] = []
    for path in service_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in extensions:
            continue
        hit = scan_file(path, patterns)
        if hit is not None:
            candidates.append(hit)

    if not candidates:
        return None

    # Sort by: confidence_score desc, then by priority of the matched
    # pattern desc, then by path depth asc (shallower path = more canonical
    # config location).
    priority_by_id = {p.id: p.priority for p in catalog}

    def sort_key(r: ScanResult) -> tuple:
        depth = r.file.count("/")
        return (
            -r.confidence_score,
            -priority_by_id.get(r.pattern_id, 0),
            depth,
        )

    candidates.sort(key=sort_key)
    return candidates[0]
```

- [ ] **Step 4: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/discovery/scripts/env_loader_scan.py \
        skills/cost-billing/discovery/scripts/test_env_loader_scan.py
git commit -m "feat(cost-billing/discovery): env_loader_scan service-level walker

scan_service() walks a service tree, filters by language extension,
skips well-known noise directories (node_modules, __pycache__, vendor,
etc.), and returns the highest-confidence per-file match.

Conflict resolution when multiple patterns match: confidence_score desc
→ catalog priority desc → path-depth asc (shallower path wins as the
canonical config location). pydantic-settings (priority 100) wins over
dotenv+os.getenv (priority 70) when both appear in the same service.

_SKIP_DIRS prevents false positives in vendored dependencies. Phase B
deployment-surface scanning is intentionally separate from this — that
walker has different skip rules (must include infra/ which may sit at
repo root)."
```

---

## Task 7: env_loader_scan.py — deployment-surface scan

**Files:**
- Modify: `skills/cost-billing/discovery/scripts/test_env_loader_scan.py`
- Modify: `skills/cost-billing/discovery/scripts/env_loader_scan.py`

- [ ] **Step 1: Add failing tests**

Append to `test_env_loader_scan.py`:

```python
class DeploymentSurfaceScan(unittest.TestCase):
    def test_detects_terraform_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf_dir = Path(tmp) / "infra" / "terraform" / "payments-api"
            tf_dir.mkdir(parents=True)
            (tf_dir / "variables.tf").write_text(
                'variable "database_url" { type = string }\n'
            )
            (tf_dir / "main.tf").write_text("# main\n")
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            terraform_hits = [s for s in surfaces if s.kind == "terraform"]
            self.assertEqual(len(terraform_hits), 1)
            self.assertTrue(terraform_hits[0].path.endswith("variables.tf"))
            self.assertEqual(terraform_hits[0].insert_kind, "variable_block_append")

    def test_detects_k8s_deployment_with_envfrom(self):
        with tempfile.TemporaryDirectory() as tmp:
            k8s = Path(tmp) / "infra" / "k8s" / "payments-api"
            k8s.mkdir(parents=True)
            (k8s / "deployment.yaml").write_text(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: payments-api\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "      - name: app\n"
                "        envFrom:\n"
                "        - secretRef:\n            name: payments-secrets\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            k8s_hits = [s for s in surfaces if s.kind == "k8s"]
            self.assertEqual(len(k8s_hits), 1)
            self.assertEqual(k8s_hits[0].insert_kind, "secret_ref_checklist")

    def test_detects_docker_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docker-compose.yml").write_text(
                "services:\n  app:\n    image: app:latest\n    environment:\n"
                "      - DATABASE_URL=postgres://...\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            compose_hits = [s for s in surfaces if s.kind == "docker-compose"]
            self.assertEqual(len(compose_hits), 1)

    def test_detects_dotenv_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "payments-api"
            svc.mkdir(parents=True)
            (svc / ".env.example").write_text("DATABASE_URL=\nREDIS_URL=\n")
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            dotenv_hits = [s for s in surfaces if s.kind == "dotenv_example"]
            self.assertEqual(len(dotenv_hits), 1)
            self.assertEqual(dotenv_hits[0].insert_kind, "line_append")

    def test_dockerfile_with_env_lines_emits_checklist_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Dockerfile").write_text(
                "FROM python:3.11\n"
                "ENV DATABASE_URL=postgres://baked-in\n"  # security smell
                "COPY . /app\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            docker_hits = [s for s in surfaces if s.kind == "dockerfile"]
            self.assertEqual(len(docker_hits), 1)
            self.assertEqual(docker_hits[0].insert_kind, "checklist_only")
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -5
```

Expected: `AttributeError: module 'env_loader_scan' has no attribute 'scan_deployment_surfaces'`.

- [ ] **Step 3: Implement scan_deployment_surfaces**

In `env_loader_scan.py`, add below `scan_service()`:

```python
# ──────────────────────────────────────────────────────────────────────
# Deployment-surface scan
# ──────────────────────────────────────────────────────────────────────

@dataclass
class DeploymentSurface:
    kind: str          # "terraform" | "k8s" | "docker-compose" | "dotenv_example" | "dockerfile"
    path: str          # repo-relative path
    insert_kind: str   # "variable_block_append" | "secret_ref_checklist" |
                       # "environment_block_append" | "line_append" | "checklist_only"


# Per-surface skip dirs are MORE permissive than _SKIP_DIRS — we explicitly
# want to scan infra/, deployment/, k8s/, etc.
_SURFACE_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", "vendor"})


def scan_deployment_surfaces(repo_root: Path) -> list[DeploymentSurface]:
    """Walk the repo for deployment-surface insertion points. Each detected
    surface becomes one entry; the instrument side decides per-entry whether
    to emit a stub file, append to an existing file, or emit a CHECKLIST
    comment.

    Recognition rules (all non-destructive — no file modification here):
      - Terraform: any `variable "..." {}` block in a *.tf file
      - k8s: Deployment / StatefulSet / DaemonSet manifests with envFrom: secretRef
      - docker-compose: `services.<X>.environment:` block in compose yaml
      - .env.example / .env.sample: presence
      - Dockerfile: ENV lines (security smell — checklist only)
    """
    out: list[DeploymentSurface] = []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SURFACE_SKIP_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(repo_root))

        # Terraform
        if path.suffix == ".tf":
            text = path.read_text(errors="ignore")
            if re.search(r'variable\s+"[^"]+"\s*\{', text):
                out.append(DeploymentSurface(
                    kind="terraform",
                    path=rel,
                    insert_kind="variable_block_append",
                ))
            continue

        # Kubernetes manifests
        if path.suffix in (".yaml", ".yml"):
            text = path.read_text(errors="ignore")
            if re.search(r'^\s*kind:\s*(Deployment|StatefulSet|DaemonSet)\b',
                         text, re.MULTILINE):
                out.append(DeploymentSurface(
                    kind="k8s",
                    path=rel,
                    insert_kind="secret_ref_checklist",
                ))
                continue
            # docker-compose detection by filename
            if path.name in {"docker-compose.yml", "docker-compose.yaml",
                             "compose.yml", "compose.yaml"}:
                if re.search(r'^\s*environment:\s*$', text, re.MULTILINE) or \
                   re.search(r'^\s*environment:\s*\[', text, re.MULTILINE):
                    out.append(DeploymentSurface(
                        kind="docker-compose",
                        path=rel,
                        insert_kind="environment_block_append",
                    ))
            continue

        # .env.example / .env.sample
        if path.name in {".env.example", ".env.sample"}:
            out.append(DeploymentSurface(
                kind="dotenv_example",
                path=rel,
                insert_kind="line_append",
            ))
            continue

        # Dockerfile
        if path.name == "Dockerfile" or path.name.startswith("Dockerfile."):
            text = path.read_text(errors="ignore")
            if re.search(r'^\s*ENV\s+\w+', text, re.MULTILINE):
                out.append(DeploymentSurface(
                    kind="dockerfile",
                    path=rel,
                    insert_kind="checklist_only",
                ))

    return out
```

- [ ] **Step 4: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/discovery/scripts/env_loader_scan.py \
        skills/cost-billing/discovery/scripts/test_env_loader_scan.py
git commit -m "feat(cost-billing/discovery): env_loader_scan deployment-surface scan

scan_deployment_surfaces() walks the repo for the 5 surfaces the spec
calls out:

  - terraform        → variable_block_append (append to existing .tf)
  - k8s              → secret_ref_checklist  (manifest edits are risky;
                                              checklist only)
  - docker-compose   → environment_block_append
  - .env.example     → line_append
  - Dockerfile       → checklist_only (ENV lines smell of baked-in
                                       secrets; never auto-edit)

Permissive skip set (only .git, node_modules, __pycache__, vendor) —
infra/ and deployment/ trees are explicitly in scope. Each surface entry
carries its repo-relative path + the insertion kind that Phase 1.7's
config_wire.py will dispatch on."
```

---

## Task 8: env_loader_scan.py — granularity handling + YAML emit + main()

**Files:**
- Modify: `skills/cost-billing/discovery/scripts/test_env_loader_scan.py`
- Modify: `skills/cost-billing/discovery/scripts/env_loader_scan.py`

- [ ] **Step 1: Add failing tests for granularity + YAML emit**

Append to `test_env_loader_scan.py`:

```python
class GranularityHandling(unittest.TestCase):
    def test_per_service_emits_one_entry_per_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "services" / "svc-a" / "app").mkdir(parents=True)
            (repo / "services" / "svc-a" / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    x: str\n"
            )
            (repo / "services" / "svc-b" / "app").mkdir(parents=True)
            (repo / "services" / "svc-b" / "app" / "config.py").write_text(
                "from pydantic import BaseSettings\n"
                "class S(BaseSettings):\n    x: str\n"
            )
            inventory = els.build_inventory(
                repo_root=repo,
                services=[
                    {"slug": "svc-a", "root": "services/svc-a", "language": "python"},
                    {"slug": "svc-b", "root": "services/svc-b", "language": "python"},
                ],
                catalog=els.load_pattern_catalog(CATALOG_PATH),
                granularity="per-service",
                granularity_source="declared",
                shared_config_path=None,
            )
            self.assertEqual(len(inventory["services"]), 2)
            slugs = {s["service_slug"] for s in inventory["services"]}
            self.assertEqual(slugs, {"svc-a", "svc-b"})

    def test_unrecognized_pattern_yields_stub_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "services" / "weird" / "app").mkdir(parents=True)
            (repo / "services" / "weird" / "app" / "main.py").write_text(
                "def main(): pass\n"
            )
            inventory = els.build_inventory(
                repo_root=repo,
                services=[{"slug": "weird", "root": "services/weird", "language": "python"}],
                catalog=els.load_pattern_catalog(CATALOG_PATH),
                granularity="per-service",
                granularity_source="declared",
                shared_config_path=None,
            )
            entry = inventory["services"][0]
            self.assertEqual(entry["app_config"]["pattern"], "unrecognized")
            self.assertTrue(entry["app_config"]["stub_required"])

    def test_repo_wide_uses_shared_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            shared = repo / "packages" / "config"
            shared.mkdir(parents=True)
            (shared / "settings.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    x: str\n"
            )
            inventory = els.build_inventory(
                repo_root=repo,
                services=[
                    {"slug": "svc-a", "root": "services/svc-a", "language": "python"},
                    {"slug": "svc-b", "root": "services/svc-b", "language": "python"},
                ],
                catalog=els.load_pattern_catalog(CATALOG_PATH),
                granularity="repo-wide",
                granularity_source="declared",
                shared_config_path="packages/config",
            )
            # Both services share the same wire target — the shared file.
            self.assertEqual(len(inventory["services"]), 2)
            for entry in inventory["services"]:
                self.assertTrue(entry["app_config"]["file"].endswith("settings.py"))
                self.assertEqual(entry["app_config"]["pattern"], "python-pydantic-settings-v2")


class InventoryYamlEmit(unittest.TestCase):
    def test_emit_yaml_has_top_level_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            out = repo / "env-routing-inventory.yaml"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "granularity": "per-service",
                "granularity_source": "declared",
                "services": [],
            }
            els.emit_inventory_yaml(inventory, out)
            content = out.read_text()
            self.assertIn("generated_at:", content)
            self.assertIn("granularity: per-service", content)
            self.assertIn("granularity_source: declared", content)
            self.assertIn("services: []", content)
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -5
```

Expected: `AttributeError: module 'env_loader_scan' has no attribute 'build_inventory'`.

- [ ] **Step 3: Add build_inventory + emit_inventory_yaml + flesh out main()**

In `env_loader_scan.py`, REPLACE the `main()` function and add the helpers above it:

```python
# ──────────────────────────────────────────────────────────────────────
# Inventory build
# ──────────────────────────────────────────────────────────────────────

def _service_entry(
    repo_root: Path,
    service: dict,
    scan_root: Path,
    catalog: list[Pattern],
) -> dict:
    """Build one services[] entry from a scan_service result + deployment
    surfaces under the service's path."""
    language = service.get("language", "python")
    result = scan_service(scan_root, language, catalog)

    if result is None:
        app_config = {
            "pattern": "unrecognized",
            "confidence": "none",
            "evidence": [],
            "stub_required": True,
        }
    else:
        rel_file = str(Path(result.file).relative_to(repo_root)) \
            if Path(result.file).is_relative_to(repo_root) else result.file
        app_config = {
            "pattern": result.pattern_id,
            "file": rel_file,
            "line_to_insert": result.line_to_insert,
            "confidence": result.confidence,
            "confidence_score": round(result.confidence_score, 2),
            "evidence": result.evidence,
            "stub_required": result.confidence == "low",
            "wire_target": result.wire_target,
        }

    # Deployment surfaces scoped to the SERVICE's path (not the whole repo).
    service_path = repo_root / service["root"]
    surfaces = scan_deployment_surfaces(service_path) if service_path.exists() else []

    return {
        "service_slug": service["slug"],
        "app_config": app_config,
        "deployment_surfaces": [
            {"kind": s.kind, "path": s.path, "insert_kind": s.insert_kind}
            for s in surfaces
        ],
    }


def build_inventory(
    repo_root: Path,
    services: list[dict],
    catalog: list[Pattern],
    granularity: str,
    granularity_source: str,
    shared_config_path: str | None,
) -> dict:
    """Build the env-routing-inventory dict that will be YAML-emitted.

    Granularity behavior:
      - per-service:  scan each service's root independently
      - repo-wide:    scan ONLY shared_config_path; every service entry
                      points at the same file
      - hybrid:       per-service for services not in the shared set;
                      shared_config_path for the rest (out of scope for
                      Phase A — falls back to per-service)
      - TBD:          per-service best-effort with granularity_source flag
    """
    if granularity == "repo-wide" and shared_config_path:
        scan_root = repo_root / shared_config_path
        service_entries: list[dict] = []
        for svc in services:
            entry = _service_entry(repo_root, svc, scan_root, catalog)
            service_entries.append(entry)
    else:
        # per-service (or TBD / hybrid → per-service for Phase A)
        service_entries = []
        for svc in services:
            svc_root = repo_root / svc["root"]
            entry = _service_entry(repo_root, svc, svc_root, catalog)
            service_entries.append(entry)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "granularity": granularity,
        "granularity_source": granularity_source,
        "services": service_entries,
    }


# ──────────────────────────────────────────────────────────────────────
# YAML emit (hand-rolled, matches sdk_snapshot.py convention)
# ──────────────────────────────────────────────────────────────────────

def emit_inventory_yaml(inventory: dict, dest: Path) -> None:
    """Hand-rolled YAML emit for env-routing-inventory.yaml. Avoids PyYAML
    runtime dep for the customer codemod environment."""
    lines: list[str] = []
    lines.append(f"generated_at: {inventory['generated_at']}")
    lines.append(f"granularity: {inventory['granularity']}")
    lines.append(f"granularity_source: {inventory['granularity_source']}")
    if not inventory["services"]:
        lines.append("services: []")
    else:
        lines.append("services:")
        for svc in inventory["services"]:
            lines.append(f"  - service_slug: {svc['service_slug']}")
            ac = svc["app_config"]
            lines.append(f"    app_config:")
            lines.append(f"      pattern: {ac['pattern']}")
            if ac.get("file"):
                lines.append(f"      file: {ac['file']}")
                lines.append(f"      line_to_insert: {ac['line_to_insert']}")
                lines.append(f"      confidence: {ac['confidence']}")
                lines.append(f"      confidence_score: {ac['confidence_score']}")
            else:
                lines.append(f"      confidence: {ac['confidence']}")
            lines.append(f"      stub_required: {str(ac['stub_required']).lower()}")
            if ac.get("evidence"):
                lines.append(f"      evidence:")
                for e in ac["evidence"]:
                    # Escape quotes inside the evidence string
                    e_safe = e.replace('"', '\\"')
                    lines.append(f'        - "{e_safe}"')
            if ac.get("wire_target"):
                lines.append(f"      wire_target:")
                for k, v in ac["wire_target"].items():
                    v_str = str(v).replace('"', '\\"')
                    lines.append(f'        {k}: "{v_str}"')

            if svc["deployment_surfaces"]:
                lines.append(f"    deployment_surfaces:")
                for s in svc["deployment_surfaces"]:
                    lines.append(f"      - kind: {s['kind']}")
                    lines.append(f"        path: {s['path']}")
                    lines.append(f"        insert_kind: {s['insert_kind']}")
            else:
                lines.append(f"    deployment_surfaces: []")

    dest.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────────────
# Signed-yaml parser (read services + env_loader_granularity)
# ──────────────────────────────────────────────────────────────────────

def parse_services_and_granularity(signed_yaml_path: Path) -> tuple[list[dict], str, str, str | None]:
    """Read `04-final.signed.yaml` and return:
      (services, env_loader_granularity, granularity_source, shared_config_path)

    Defaults: granularity="TBD", source="default-fallback" if absent.
    """
    if not signed_yaml_path.exists():
        return [], "TBD", "default-fallback", None
    try:
        import yaml
        data = yaml.safe_load(signed_yaml_path.read_text()) or {}
    except ImportError:
        # Phase A leans on PyYAML; documented in the module docstring.
        return [], "TBD", "default-fallback", None

    integration = data.get("integration") or {}
    services_raw = integration.get("services") or []
    services: list[dict] = []
    for s in services_raw:
        services.append({
            "slug": s.get("slug") or s.get("service_slug") or "",
            "root": s.get("root") or s.get("path") or "",
            "language": s.get("language") or "python",
        })

    granularity = integration.get("env_loader_granularity")
    if granularity:
        return services, granularity, "declared", integration.get("shared_config_path")
    return services, "TBD", "default-fallback", None


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signed-yaml", default=".moolabs/chain/04-final.signed.yaml")
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--catalog", required=True, help="path to env-loader-patterns.yaml")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    catalog = load_pattern_catalog(Path(args.catalog))
    services, granularity, granularity_source, shared_config_path = \
        parse_services_and_granularity(Path(args.signed_yaml))

    if not services:
        print(
            "WARNING: no services found in 04-final.signed.yaml. "
            "Inventory will have an empty services list.",
            file=sys.stderr,
        )

    inventory = build_inventory(
        repo_root=repo_root,
        services=services,
        catalog=catalog,
        granularity=granularity,
        granularity_source=granularity_source,
        shared_config_path=shared_config_path,
    )

    out_dir = Path(args.customer_context_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "env-routing-inventory.yaml"
    emit_inventory_yaml(inventory, out_path)
    print(f"wrote {out_path}", file=sys.stderr)

    # No exit code 2 / refuse-to-run in Phase A — the stub_required flag
    # downstream handles the unrecognized-pattern case. The codemod
    # adversarial review surfaces low-confidence entries.
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -3
```

Expected: `OK` with all ~20 tests passing.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/discovery/scripts/env_loader_scan.py \
        skills/cost-billing/discovery/scripts/test_env_loader_scan.py
git commit -m "feat(cost-billing/discovery): env_loader_scan inventory build + YAML emit + CLI

- build_inventory(): orchestrates per-service or repo-wide scan based on
  the bootstrap-declared granularity; produces the inventory dict.
- _service_entry(): glues scan_service + scan_deployment_surfaces into
  one entry per service; flags unrecognized patterns as stub_required.
- emit_inventory_yaml(): hand-rolled YAML emit matching sdk_snapshot.py
  convention (no PyYAML runtime dep for codemod env).
- parse_services_and_granularity(): reads services + env_loader_granularity
  + shared_config_path from 04-final.signed.yaml; defaults to TBD with
  granularity_source=default-fallback when missing.
- main(): wires everything together. Writes
  .moolabs/customer-context/env-routing-inventory.yaml.

Phase A intentionally does NOT refuse-to-run on unrecognized pattern —
the stub_required flag downstream handles it. Adversarial review (Skill R)
will surface low-confidence entries for engineer attention."
```

---

## Task 9: bootstrap-team-engineer — Q14b + schema field

**Files:**
- Modify: `skills/cost-billing/bootstrap-team-engineer/SKILL.md`
- Modify: `skills/cost-billing/bootstrap-team-engineer/assets/04-final.schema.yaml`

- [ ] **Step 1: Locate Q14 in bootstrap-team-engineer/SKILL.md**

```bash
grep -n "Q14\|env_loader\|SDK key location" skills/cost-billing/bootstrap-team-engineer/SKILL.md | head -10
```

Expected: a line like `### Q14 — SDK key location` at some line number. Note the line number; Q14b goes right after Q14's section.

- [ ] **Step 2: Read Q14's section to find where it ends**

```bash
awk '/^### Q14 /,/^### Q1[5-9]/' skills/cost-billing/bootstrap-team-engineer/SKILL.md | head -40
```

Look for the line that introduces Q15 — Q14b will be inserted right before it.

- [ ] **Step 3: Insert Q14b**

Use the Edit tool to insert a new `### Q14b — Env-loader granularity` section directly before the `### Q15` heading. The new section reads:

```markdown
### Q14b — Env-loader granularity (NEW v0.3 env-routing migration)

> "How is env-loading wired in your repo? This determines whether the codemod
> integrates MOOLABS_API_KEY into one shared config file or into each service's
> own config file.
>
> - **Per-service** — each service has its own config code (e.g. one
>   pydantic-settings class per service)
> - **Repo-wide** — shared config package every service imports from
>   (e.g. `packages/config/`)
> - **Hybrid** — some services share, others have their own. (I'll ask you to
>   name which.)
> - **TBD** — let the scanner detect best-effort"

If **Repo-wide** or **Hybrid**, follow up:

> "What's the path to your shared config package, relative to repo root?
> (e.g. `packages/config`)"

If **Hybrid**, also follow up:

> "Which services use the shared package, and which have their own config?
> List service slugs per group."

The answer is written to `04-final.signed.yaml` as:

```yaml
integration:
  env_loader_granularity: per-service   # or repo-wide / hybrid / TBD
  shared_config_path: packages/config   # only when granularity != per-service
  hybrid_shared_services:               # only when granularity == hybrid
    - billing-api
    - notifications-svc
```

The Phase 1 discovery scan (`env_loader_scan.py`) reads these fields to decide
whether to scan each service independently or only the shared path.
```

- [ ] **Step 4: Add the schema field to 04-final.schema.yaml**

```bash
grep -n "integration:\|env_loader\|sdk_package_install\|services:" skills/cost-billing/bootstrap-team-engineer/assets/04-final.schema.yaml | head -10
```

Find the `integration:` block. Add three new fields under it:

Use the Edit tool to insert (after the `services:` field definition, or near other top-level `integration.*` fields):

```yaml
      env_loader_granularity:
        type: string
        enum: [per-service, repo-wide, hybrid, TBD]
        description: |
          From Q14b. Determines whether env_loader_scan.py scans each service
          independently (per-service) or only a shared config path (repo-wide).
          Defaults to TBD when absent; the scanner runs best-effort and the
          inventory carries granularity_source: default-fallback.
      shared_config_path:
        type: string
        description: |
          Repo-relative path to the shared config package. Required when
          env_loader_granularity is repo-wide or hybrid. Example: packages/config
      hybrid_shared_services:
        type: array
        items: { type: string }
        description: |
          List of service slugs that USE the shared_config_path when
          env_loader_granularity is hybrid. Services not in this list are
          scanned per-service.
```

- [ ] **Step 5: Verify the schema YAML parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('skills/cost-billing/bootstrap-team-engineer/assets/04-final.schema.yaml')); print('OK')"
```

Expected: `OK`.

- [ ] **Step 6: Confirm smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 61    FAIL: 0`.

- [ ] **Step 7: Commit**

```bash
git add skills/cost-billing/bootstrap-team-engineer/SKILL.md \
        skills/cost-billing/bootstrap-team-engineer/assets/04-final.schema.yaml
git commit -m "feat(cost-billing/bootstrap-team-engineer): Q14b env-loader granularity

New bootstrap question driving the v0.3 env-routing migration:

  Per-service   — each service owns its env-loading code
  Repo-wide     — one shared config package every service imports from
  Hybrid        — some services share, others have their own
  TBD           — scanner detects best-effort

Two follow-up questions when not per-service:
  - shared_config_path (path to the shared package)
  - hybrid_shared_services (which slugs use the shared path)

Answers flow to 04-final.signed.yaml under three new fields in the
integration block. Schema updated to allow them; enum-constrained on
env_loader_granularity. env_loader_scan.py reads these and dispatches
to per-service or repo-wide scanning."
```

---

## Task 10: slug_inventory.py — skeleton + EVENT_TYPE/METER_SLUG/FEATURE_KEY derivation

**Files:**
- Create: `skills/cost-billing/discovery/scripts/slug_inventory.py`
- Create: `skills/cost-billing/discovery/scripts/test_slug_inventory.py`

- [ ] **Step 1: Write failing tests**

Create `skills/cost-billing/discovery/scripts/test_slug_inventory.py`:

```python
#!/usr/bin/env python3
"""Unit tests for slug_inventory.py (Phase 1.7-slugs).

Stdlib unittest; runs in the bash smoke suite's Phase 8.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slug_inventory as si  # noqa: E402


class WorkflowIdToConstantName(unittest.TestCase):
    def test_dotted_workflow_id(self):
        self.assertEqual(si.to_constant_name("checkout.recommendation.delivered"),
                         "CHECKOUT_RECOMMENDATION_DELIVERED")

    def test_hyphenated_value(self):
        self.assertEqual(si.to_constant_name("llm-tokens"), "LLM_TOKENS")

    def test_mixed_separators(self):
        self.assertEqual(si.to_constant_name("foo.bar-baz_qux"),
                         "FOO_BAR_BAZ_QUX")

    def test_already_upper_snake(self):
        self.assertEqual(si.to_constant_name("ALREADY_GOOD"), "ALREADY_GOOD")

    def test_strips_leading_trailing_punctuation(self):
        self.assertEqual(si.to_constant_name(".leading.trailing."),
                         "LEADING_TRAILING")


class DeriveEventTypes(unittest.TestCase):
    def test_from_cost_events_inventory(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "checkout.recommendation.delivered",
                 "event_type": "checkout.recommendation.delivered",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        self.assertIn("billing", by_product)
        event_types = by_product["billing"]["EVENT_TYPE"]
        names = {e["name"] for e in event_types}
        self.assertIn("CHECKOUT_RECOMMENDATION_DELIVERED", names)


class DeriveMeterSlugs(unittest.TestCase):
    def test_meter_slug_from_workflow_id(self):
        cost_inv = {"entries": []}
        usage_inv = {
            "entries": [
                {"workflow_id": "seat.assigned", "event_type": "seat.assigned",
                 "product_slug": "billing"},
            ],
        }
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        meter_slugs = by_product["billing"]["METER_SLUG"]
        values = {e["value"] for e in meter_slugs}
        self.assertIn("seat.assigned", values)


class DeriveFeatureKeys(unittest.TestCase):
    def test_feature_key_is_second_dotted_segment(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "checkout.recommendation.delivered",
                 "event_type": "checkout.recommendation.delivered",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        feature_keys = by_product["billing"]["FEATURE_KEY"]
        # checkout.recommendation.delivered → feature_key = "recommendation"
        # (second segment of dotted workflow_id)
        values = {e["value"] for e in feature_keys}
        self.assertIn("recommendation", values)

    def test_single_segment_workflow_id_uses_whole_value_as_feature_key(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "seat", "event_type": "seat",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        feature_keys = by_product["billing"]["FEATURE_KEY"]
        values = {e["value"] for e in feature_keys}
        self.assertIn("seat", values)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py 2>&1 | tail -3
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement slug_inventory.py**

Create `skills/cost-billing/discovery/scripts/slug_inventory.py`:

```python
#!/usr/bin/env python3
"""Phase 1.7-slugs — per-product event-slug constants inventory.

Reads cost-events-inventory.yaml + usage-events-inventory.yaml +
output-input-map.yaml + provider-catalog.starter.yaml + CPO bootstrap's
product list. Derives the constants Phase B+C will emit as a slugs
module per product.

Categories per product:
  EVENT_TYPE   — per-feature canonical event identifiers
  METER_SLUG   — per-feature billing routing keys
  FEATURE_KEY  — per-feature short identifiers
  PROVIDER     — recognized vendor identifiers (from provider-catalog)
  SPAN_TYPE    — canonical span-kind identifiers (from cost_kind values)

Output: .moolabs/customer-context/slug-inventory.yaml

Usage:
    python slug_inventory.py \\
        --cost-events .moolabs/inventory/cost-events-inventory.yaml \\
        --usage-events .moolabs/inventory/usage-events-inventory.yaml \\
        --output-input-map .moolabs/inventory/output-input-map.yaml \\
        --provider-catalog skills/cost-billing/discovery/assets/provider-catalog.starter.yaml \\
        --customer-context-dir .moolabs/customer-context
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Naming convention
# ──────────────────────────────────────────────────────────────────────

_NAME_SEPARATORS = re.compile(r"[.\-_]+")


def to_constant_name(value: str) -> str:
    """Convert a slug value to UPPER_SNAKE_CASE.

    Examples:
        "checkout.recommendation.delivered" -> "CHECKOUT_RECOMMENDATION_DELIVERED"
        "llm-tokens"                        -> "LLM_TOKENS"
        "foo.bar-baz_qux"                   -> "FOO_BAR_BAZ_QUX"
        ".leading.trailing."                -> "LEADING_TRAILING"
    """
    parts = _NAME_SEPARATORS.split(value)
    parts = [p for p in parts if p]  # strip empties from leading/trailing/internal dups
    return "_".join(parts).upper()


# ──────────────────────────────────────────────────────────────────────
# Derivation per product
# ──────────────────────────────────────────────────────────────────────

def _feature_key_for(workflow_id: str) -> str:
    """Derive a feature_key from a dotted workflow_id. Convention:

    - Multi-segment (a.b.c.d):  use the SECOND segment ('b')
    - Two-segment   (a.b):      use the SECOND segment ('b')
    - Single-segment (a):       use the whole value ('a')

    Matches the framework callsite template's existing inline derivation
    (`entry.workflow_id.split('.')[1] if count('.') >= 1 else workflow_id`).
    """
    parts = workflow_id.split(".")
    if len(parts) >= 2:
        return parts[1]
    return workflow_id


def derive_per_product_constants(
    cost_inv: dict,
    usage_inv: dict,
    omap: dict,  # noqa: ARG001 — kept for future cross-edge derivations
    provider_catalog: dict | None,
) -> dict[str, dict[str, list[dict]]]:
    """Return {product_slug: {CATEGORY: [{name, value}, ...]}}.

    Each (CATEGORY, name) is unique within a product. Duplicate detection
    is the caller's responsibility (see check_duplicates()).
    """
    by_product: dict[str, dict[str, list[dict]]] = {}

    def _ensure(product: str) -> dict[str, list[dict]]:
        return by_product.setdefault(product, {
            "EVENT_TYPE": [],
            "METER_SLUG": [],
            "FEATURE_KEY": [],
            "PROVIDER": [],
            "SPAN_TYPE": [],
        })

    def _add_unique(bucket: list[dict], name: str, value: str) -> None:
        if not any(e["name"] == name for e in bucket):
            bucket.append({"name": name, "value": value})

    # EVENT_TYPE, METER_SLUG, FEATURE_KEY from cost-events + usage-events
    for source in (cost_inv, usage_inv):
        for entry in source.get("entries", []) or []:
            product = entry.get("product_slug") or "default"
            bucket = _ensure(product)

            event_type = entry.get("event_type") or entry.get("workflow_id")
            if event_type:
                _add_unique(bucket["EVENT_TYPE"],
                            to_constant_name(event_type), event_type)

            workflow_id = entry.get("workflow_id")
            if workflow_id:
                _add_unique(bucket["METER_SLUG"],
                            to_constant_name(workflow_id), workflow_id)
                fk_value = _feature_key_for(workflow_id)
                _add_unique(bucket["FEATURE_KEY"],
                            to_constant_name(fk_value), fk_value)

    # PROVIDER and SPAN_TYPE come from later tasks
    return by_product


# ──────────────────────────────────────────────────────────────────────
# CLI (skeleton — fleshed out in later tasks)
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost-events", required=False, default=".moolabs/inventory/cost-events-inventory.yaml")
    ap.add_argument("--usage-events", required=False, default=".moolabs/inventory/usage-events-inventory.yaml")
    ap.add_argument("--output-input-map", required=False, default=".moolabs/inventory/output-input-map.yaml")
    ap.add_argument("--provider-catalog", required=False)
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    args = ap.parse_args(argv)

    print(
        "Phase A Task 10 skeleton — derives EVENT_TYPE / METER_SLUG / FEATURE_KEY only. "
        "PROVIDER / SPAN_TYPE / per-product split / YAML emit land in later tasks.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/discovery/scripts/slug_inventory.py \
        skills/cost-billing/discovery/scripts/test_slug_inventory.py
git commit -m "feat(cost-billing/discovery): slug_inventory.py skeleton + workflow-derived constants

slug_inventory.py module + tests covering the three categories derived
from cost-events-inventory.yaml + usage-events-inventory.yaml:

  - EVENT_TYPE  — workflow_id / event_type as the canonical name
  - METER_SLUG  — workflow_id as the routing key
  - FEATURE_KEY — second dotted segment of workflow_id (matches the
                  framework callsite template's existing inline derivation)

to_constant_name(): UPPER_SNAKE_CASE conversion handling all three
separators (. - _), stripping leading/trailing empty segments,
preserving internal underscores.

_add_unique(): per-product de-dup by NAME (not value). PROVIDER and
SPAN_TYPE + per-product split + YAML emit land in later tasks."
```

---

## Task 11: slug_inventory.py — PROVIDER + SPAN_TYPE derivation

**Files:**
- Modify: `skills/cost-billing/discovery/scripts/test_slug_inventory.py`
- Modify: `skills/cost-billing/discovery/scripts/slug_inventory.py`

- [ ] **Step 1: Add failing tests**

Append to `test_slug_inventory.py`:

```python
class DeriveProviders(unittest.TestCase):
    def test_providers_from_catalog(self):
        cost_inv = {"entries": []}
        usage_inv = {"entries": []}
        omap = {"edges": []}
        provider_catalog = {
            "providers": [
                {"slug": "openai", "name": "OpenAI"},
                {"slug": "anthropic", "name": "Anthropic"},
                {"slug": "stripe", "name": "Stripe"},
            ],
        }
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog
        )
        # PROVIDER constants are global across products — every product
        # gets the same enum from the catalog.
        # For an empty inventory we expect at least one "default" product
        # entry carrying the providers.
        self.assertIn("default", by_product)
        providers = by_product["default"]["PROVIDER"]
        names = {e["name"] for e in providers}
        self.assertEqual(names, {"OPENAI", "ANTHROPIC", "STRIPE"})


class DeriveSpanTypes(unittest.TestCase):
    def test_span_types_from_cost_kind(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "x", "event_type": "x", "cost_kind": "llm-tokens",
                 "product_slug": "billing"},
                {"workflow_id": "y", "event_type": "y", "cost_kind": "gpu-seconds",
                 "product_slug": "billing"},
                {"workflow_id": "z", "event_type": "z", "cost_kind": "llm-tokens",
                 "product_slug": "billing"},  # duplicate cost_kind — de-duped
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        span_types = by_product["billing"]["SPAN_TYPE"]
        names = {e["name"] for e in span_types}
        self.assertEqual(names, {"LLM_TOKENS", "GPU_SECONDS"})
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py 2>&1 | tail -10
```

Expected: tests for PROVIDER / SPAN_TYPE fail (the derivation isn't there yet).

- [ ] **Step 3: Extend derive_per_product_constants**

In `slug_inventory.py`, MODIFY the `derive_per_product_constants()` function. Replace the comment `# PROVIDER and SPAN_TYPE come from later tasks` and everything before `return by_product` with:

```python
    # SPAN_TYPE from cost_kind values in cost-events-inventory
    for entry in cost_inv.get("entries", []) or []:
        product = entry.get("product_slug") or "default"
        bucket = _ensure(product)
        cost_kind = entry.get("cost_kind")
        if cost_kind:
            _add_unique(bucket["SPAN_TYPE"],
                        to_constant_name(cost_kind), cost_kind)

    # PROVIDER from provider-catalog (global — every product gets the same
    # enum). When inventories are empty, we still ensure a "default" bucket
    # so the providers are surfaced somewhere.
    if provider_catalog:
        providers = provider_catalog.get("providers") or []
        if not by_product and providers:
            _ensure("default")
        for product in list(by_product.keys()):
            bucket = by_product[product]
            for p in providers:
                slug = p.get("slug")
                if slug:
                    _add_unique(bucket["PROVIDER"],
                                to_constant_name(slug), slug)

    return by_product
```

- [ ] **Step 4: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/discovery/scripts/slug_inventory.py \
        skills/cost-billing/discovery/scripts/test_slug_inventory.py
git commit -m "feat(cost-billing/discovery): slug_inventory PROVIDER + SPAN_TYPE derivation

- SPAN_TYPE: derived from cost_kind values in cost-events-inventory.
  De-duped by NAME within each product. (Future v2: a curated
  span-type-catalog.yaml for canonical values across all products.)

- PROVIDER: derived from provider-catalog.starter.yaml. Global across
  products (every product gets the same enum). When inventories are
  empty, ensures a 'default' product bucket so providers are still
  surfaced for downstream Phase C consumers.

UPPER_SNAKE_CASE naming via to_constant_name() (same as the other
categories)."
```

---

## Task 12: slug_inventory.py — duplicate detection + YAML emit + main()

**Files:**
- Modify: `skills/cost-billing/discovery/scripts/test_slug_inventory.py`
- Modify: `skills/cost-billing/discovery/scripts/slug_inventory.py`

- [ ] **Step 1: Add failing tests**

Append to `test_slug_inventory.py`:

```python
class DuplicateDetection(unittest.TestCase):
    def test_duplicate_name_in_same_category_raises(self):
        # Two cost-event entries with workflow_ids that collapse to the
        # same UPPER_SNAKE_CASE name → CRITICAL: refuse-to-run.
        cost_inv = {
            "entries": [
                {"workflow_id": "checkout.recommendation",
                 "event_type": "checkout.recommendation",
                 "product_slug": "billing"},
                {"workflow_id": "checkout-recommendation",  # same canonical name
                 "event_type": "checkout-recommendation",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        errors = si.check_duplicates(by_product)
        # The EVENT_TYPE bucket gets CHECKOUT_RECOMMENDATION twice from
        # two different value strings — that's a name collision.
        # NOTE: _add_unique() dedupes by name, so only one entry exists;
        # check_duplicates() catches the SOURCE collision by comparing
        # raw inventory entries directly. See the implementation.
        self.assertTrue(any("CHECKOUT_RECOMMENDATION" in e for e in errors),
                        f"Expected name collision in errors: {errors}")


class YamlEmit(unittest.TestCase):
    def test_emit_yaml_contains_per_product_blocks(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "slug-inventory.yaml"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "products": [
                    {
                        "product_slug": "billing",
                        "constants": {
                            "EVENT_TYPE": [
                                {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                            ],
                            "METER_SLUG": [
                                {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                            ],
                            "FEATURE_KEY": [
                                {"name": "ASSIGNED", "value": "assigned"},
                            ],
                            "PROVIDER": [],
                            "SPAN_TYPE": [],
                        },
                    },
                ],
            }
            si.emit_slug_inventory_yaml(inventory, out)
            content = out.read_text()
            self.assertIn("product_slug: billing", content)
            self.assertIn("EVENT_TYPE:", content)
            self.assertIn("name: SEAT_ASSIGNED", content)
            self.assertIn('value: "seat.assigned"', content)
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py 2>&1 | tail -10
```

Expected: tests fail on `check_duplicates` and `emit_slug_inventory_yaml` AttributeErrors.

- [ ] **Step 3: Implement check_duplicates + emit + flesh out main()**

In `slug_inventory.py`, REPLACE the `main()` function with the helpers + new main:

```python
# ──────────────────────────────────────────────────────────────────────
# Duplicate detection
# ──────────────────────────────────────────────────────────────────────

def check_duplicates(by_product: dict[str, dict[str, list[dict]]]) -> list[str]:
    """Detect (product, category, name) entries where multiple source values
    collapse to the same canonical NAME. Returns a list of error strings.
    Empty list = clean.

    Note: _add_unique() in derive_per_product_constants() already drops
    duplicates by NAME — so we need to re-scan the per-category buckets
    looking for the case where TWO DIFFERENT source values would generate
    the same NAME. We detect by re-running to_constant_name on each value
    in the bucket and counting collisions.
    """
    errors: list[str] = []
    for product, categories in by_product.items():
        for category, entries in categories.items():
            # Build {name: [values]} from the bucket
            by_name: dict[str, list[str]] = {}
            for e in entries:
                by_name.setdefault(e["name"], []).append(e["value"])
            for name, values in by_name.items():
                if len(set(values)) > 1:
                    errors.append(
                        f"duplicate slug name {name} in product {product} "
                        f"category {category}: values={values}"
                    )
    return errors


# ──────────────────────────────────────────────────────────────────────
# YAML emit (hand-rolled)
# ──────────────────────────────────────────────────────────────────────

def emit_slug_inventory_yaml(inventory: dict, dest: Path) -> None:
    """Hand-rolled YAML emit for slug-inventory.yaml."""
    lines: list[str] = []
    lines.append(f"generated_at: {inventory['generated_at']}")
    if not inventory.get("products"):
        lines.append("products: []")
    else:
        lines.append("products:")
        for product in inventory["products"]:
            lines.append(f"  - product_slug: {product['product_slug']}")
            lines.append(f"    constants:")
            for category in ("EVENT_TYPE", "METER_SLUG", "FEATURE_KEY",
                             "PROVIDER", "SPAN_TYPE"):
                entries = product["constants"].get(category, [])
                if not entries:
                    lines.append(f"      {category}: []")
                    continue
                lines.append(f"      {category}:")
                for e in entries:
                    lines.append(f"        - name: {e['name']}")
                    # Quote the value to handle dots/hyphens cleanly.
                    v = str(e["value"]).replace('"', '\\"')
                    lines.append(f'          value: "{v}"')

    dest.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────────────
# I/O helpers (read inventories)
# ──────────────────────────────────────────────────────────────────────

def _read_yaml_safe(path: Path) -> dict:
    """Read a YAML file via PyYAML. Returns {} if missing or unreadable."""
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except ImportError:
        return {}


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost-events", default=".moolabs/inventory/cost-events-inventory.yaml")
    ap.add_argument("--usage-events", default=".moolabs/inventory/usage-events-inventory.yaml")
    ap.add_argument("--output-input-map", default=".moolabs/inventory/output-input-map.yaml")
    ap.add_argument("--provider-catalog", default="skills/cost-billing/discovery/assets/provider-catalog.starter.yaml")
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    args = ap.parse_args(argv)

    cost_inv = _read_yaml_safe(Path(args.cost_events))
    usage_inv = _read_yaml_safe(Path(args.usage_events))
    omap = _read_yaml_safe(Path(args.output_input_map))
    provider_catalog = _read_yaml_safe(Path(args.provider_catalog))

    by_product = derive_per_product_constants(
        cost_inv, usage_inv, omap, provider_catalog
    )

    errors = check_duplicates(by_product)
    if errors:
        print(
            "CRITICAL: slug-name collisions detected — refusing to run:",
            file=sys.stderr,
        )
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 2

    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "products": [
            {"product_slug": slug, "constants": cats}
            for slug, cats in sorted(by_product.items())
        ],
    }

    out_dir = Path(args.customer_context_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "slug-inventory.yaml"
    emit_slug_inventory_yaml(inventory, out_path)
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests — must PASS**

```bash
python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/discovery/scripts/slug_inventory.py \
        skills/cost-billing/discovery/scripts/test_slug_inventory.py
git commit -m "feat(cost-billing/discovery): slug_inventory dup detection + YAML emit + CLI

- check_duplicates(): refuses-to-run when two DIFFERENT source values
  in the same product/category collapse to the same UPPER_SNAKE_CASE
  name. Exit code 2 with a CRITICAL message naming every collision.

- emit_slug_inventory_yaml(): hand-rolled per-product / per-category
  YAML emit; quoted values (handles dots/hyphens cleanly); empty
  categories rendered as [].

- main(): reads four inventories (cost-events, usage-events,
  output-input-map, provider-catalog), derives per-product constants,
  refuses on duplicate collisions, otherwise writes
  .moolabs/customer-context/slug-inventory.yaml.

Phase A complete for slug_inventory.py."
```

---

## Task 13: discovery/SKILL.md — document new phases

**Files:**
- Modify: `skills/cost-billing/discovery/SKILL.md`

- [ ] **Step 1: Locate the "Workflow — 5 phases" section**

```bash
grep -n "Workflow.*phases\|### Phase " skills/cost-billing/discovery/SKILL.md | head -10
```

Note the phases listed there.

- [ ] **Step 2: Read the existing Phase 5 (last phase) section to find where to insert Phase 6 / 7**

```bash
awk '/^### Phase 5/,/^---/' skills/cost-billing/discovery/SKILL.md | head -30
```

- [ ] **Step 3: Add two new phase sections after the existing last phase**

Use the Edit tool to insert (right after the `### Phase 5` section's content ends, before the next `---` separator):

```markdown
### Phase 6: Env-loader scan (NEW v0.3 env-routing migration)

Driven by `scripts/env_loader_scan.py`. Walks each declared service and
detects the customer's env-loading pattern (pydantic-settings v2, pydantic
v1 BaseSettings, python-decouple, dotenv+os.getenv for Python; zod env
schema, process.env direct, env-var library for TypeScript; viper,
kelseyhightower/envconfig, raw os.Getenv for Go). The recognition catalog
lives at `cost-billing-shared/assets/env-loader-patterns.yaml`.

Granularity is declared by the engineer in bootstrap-team-engineer Q14b:
- `per-service` — scan each service independently
- `repo-wide` — scan only the declared `shared_config_path`; every service
  entry points at the same file
- `hybrid` — per-service for declared independents, repo-wide for the rest
- `TBD` — best-effort; the inventory carries `granularity_source:
  default-fallback` for adversarial-review visibility

The scanner also walks each service's deployment surfaces (Terraform
`variable {}` blocks, k8s Deployment/StatefulSet manifests with
`envFrom: secretRef`, docker-compose `environment:` blocks, `.env.example`
files, Dockerfile `ENV` lines). Each detected surface becomes an entry
in the per-service `deployment_surfaces` list with an `insert_kind` that
the instrument-side Phase 1.7 dispatches on.

Output: `.moolabs/customer-context/env-routing-inventory.yaml`. Unrecognized
patterns and low-confidence matches both flag `stub_required: true`, which
instrument's Phase 1.7 turns into a generated stub Settings class instead
of an in-place modification.

### Phase 7: Slug inventory (NEW v0.3 event-slug constants)

Driven by `scripts/slug_inventory.py`. Reads
`cost-events-inventory.yaml` + `usage-events-inventory.yaml` +
`output-input-map.yaml` + `provider-catalog.starter.yaml` + the CPO
bootstrap's product list. Derives per-product canonical slug constants
across five categories:

- `EVENT_TYPE` — workflow_id / event_type as the canonical name
- `METER_SLUG` — workflow_id as the routing key
- `FEATURE_KEY` — second dotted segment of workflow_id (matches the
  framework callsite template's existing inline derivation)
- `PROVIDER` — vendor identifiers from provider-catalog (global enum,
  same per product)
- `SPAN_TYPE` — canonical span-kind from cost_kind values (per-product,
  de-duped by name)

Naming convention: `UPPER_SNAKE_CASE` via
`slug.value.replace(".", "_").replace("-", "_").upper()`.

Duplicate-detection: when two different source values in the same
(product, category) collapse to the same canonical name (e.g. one entry
has `checkout.recommendation` and another has `checkout-recommendation`,
both yielding `CHECKOUT_RECOMMENDATION`), the script refuses-to-run with
exit code 2 and a CRITICAL message naming every collision. The engineer
fixes the source data.

Output: `.moolabs/customer-context/slug-inventory.yaml`. Phase B (instrument)
consumes both inventories: env-routing-inventory drives the helper template's
`_resolve_api_key()` rewrite + per-service env-wiring tasks; slug-inventory
drives the per-product slugs module emission + the framework callsite
template's import-instead-of-literal updates.
```

- [ ] **Step 4: Add a brief note to the existing "Phase 5" or the head of the workflow about the new phases**

This is optional but recommended for discoverability. Find the workflow header (something like `## Workflow — 5 phases`) and bump the count to 7 plus a one-line summary.

```bash
grep -n "^## Workflow" skills/cost-billing/discovery/SKILL.md
```

Update the header line:

```markdown
## Workflow — 7 phases (Phase 6/7 added for v0.3 env-routing + slugs)
```

- [ ] **Step 5: Confirm smoke (SKILL.md frontmatter unchanged so Phase 1 still passes)**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 62    FAIL: 0` (count from prior tasks + the new test_slug_inventory.py auto-discovered).

- [ ] **Step 6: Commit**

```bash
git add skills/cost-billing/discovery/SKILL.md
git commit -m "docs(cost-billing/discovery): document Phase 6/7 (env-loader + slugs)

Two new sections in the Workflow — phases:

Phase 6: env_loader_scan.py — scans each declared service for the
customer's env-loading pattern (10 patterns across Python/TS/Go) plus
deployment surfaces (Terraform/k8s/docker-compose/.env.example/Dockerfile).
Granularity declared by bootstrap-team-engineer Q14b. Unrecognized or
low-confidence patterns flag stub_required: true for Phase 1.7.

Phase 7: slug_inventory.py — derives per-product UPPER_SNAKE_CASE
constants for EVENT_TYPE, METER_SLUG, FEATURE_KEY, PROVIDER, SPAN_TYPE
from the existing inventories + provider-catalog. Duplicate-name
detection refuses-to-run on collisions.

Both outputs land in .moolabs/customer-context/ alongside the other
inventories. Phase B (instrument) consumes both."
```

---

## Task 14: Final smoke + push branch

**Files:** none (operational)

- [ ] **Step 1: Full smoke run**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -10
```

Expected: `PASS: 62    FAIL: 0`. The count is original 60 + 2 new test files auto-discovered by Phase 8.

- [ ] **Step 2: Verify both new inventory CLIs are executable end-to-end**

```bash
# Test env_loader_scan.py against an empty signed yaml (should warn but exit 0)
mkdir -p /tmp/els-test/.moolabs/customer-context
python3 skills/cost-billing/discovery/scripts/env_loader_scan.py \
    --signed-yaml /dev/null \
    --customer-context-dir /tmp/els-test/.moolabs/customer-context \
    --catalog skills/cost-billing/shared/assets/env-loader-patterns.yaml \
    --repo-root /tmp/els-test 2>&1 | tail -3
echo "---"
cat /tmp/els-test/.moolabs/customer-context/env-routing-inventory.yaml
```

Expected: warning about no services found + an empty `services: []` inventory written. Exit code 0.

```bash
# Test slug_inventory.py against an empty repo (no inventories)
mkdir -p /tmp/si-test/.moolabs/customer-context
python3 skills/cost-billing/discovery/scripts/slug_inventory.py \
    --customer-context-dir /tmp/si-test/.moolabs/customer-context \
    --cost-events /dev/null \
    --usage-events /dev/null \
    --output-input-map /dev/null \
    --provider-catalog /dev/null 2>&1 | tail -3
echo "---"
cat /tmp/si-test/.moolabs/customer-context/slug-inventory.yaml
```

Expected: empty inventory written with `products: []`. Exit code 0.

- [ ] **Step 3: Review the commit log on this branch**

```bash
git log --oneline main..HEAD
```

Expected: ~13-14 commits, one per task, in conventional-commits style.

- [ ] **Step 4: Push the branch**

```bash
source ../moolabs/.envrc && git push -u origin spec/cost-billing-env-routing-design
```

Expected: branch tracking set up; commits pushed.

- [ ] **Step 5: (Optional) Open a draft PR**

```bash
source ../moolabs/.envrc && gh pr create --base main --head spec/cost-billing-env-routing-design \
    --draft \
    --title "feat(cost-billing): env-routing + event-slug constants — Phase A (discovery)" \
    --body "## Summary

Phase A of the cost-billing env-routing + event-slug constants migration
(spec: \`docs/superpowers/specs/2026-06-06-cost-billing-env-routing-and-slugs-design.md\`).

Discovery side only — produces two new inventory files in customer-context
that Phase B (instrument env-wire) and Phase C (instrument slugs) will
consume. Ships independently from B/C/D.

### What landed

- \`shared/assets/env-loader-patterns.yaml\` — 10-pattern recognition
  catalog across Python/TS/Go.
- \`discovery/scripts/env_loader_scan.py\` — service-level env-loader
  scanner + deployment-surface scanner + YAML emit.
- \`discovery/scripts/slug_inventory.py\` — per-product slug-constant
  derivation across 5 categories with collision-refuse.
- \`bootstrap-team-engineer\` Q14b — env-loader granularity declaration.
- \`discovery/SKILL.md\` — Phase 6 / 7 documentation.

### Test plan

- [x] Smoke suite green: 62/62
- [x] Both new \`test_*.py\` files auto-discovered by Phase 8
- [x] Empty-repo CLI smoke test: both scripts emit empty inventories without crashing
- [ ] Real-customer fixture: deferred to Phase D
- [ ] Phase B (instrument env-wire) follow-up plan
- [ ] Phase C (instrument slugs) follow-up plan"
```

Expected: PR URL printed. The PR stays draft until Phase B/C land and the discovery → instrument flow is e2e-verified.

---

## Spec coverage check

Mapping each requirement section of the spec to its implementing task(s):

| Spec section | Implementing task(s) |
|---|---|
| Architecture — Discovery side / two new inventory files | Tasks 3-8 (env-routing-inventory), Tasks 10-12 (slug-inventory) |
| Architecture — Bootstrap-team-engineer / one new question | Task 9 |
| Architecture — Shared assets / one new catalog | Task 2 |
| Discovery — env_loader_scan.py pattern catalog | Task 2 |
| Discovery — env_loader_scan.py per-language detection (Python/TS/Go) | Tasks 3, 4, 5 |
| Discovery — env_loader_scan.py deployment-surface scan | Task 7 |
| Discovery — env_loader_scan.py granularity behavior | Task 8 |
| Discovery — env_loader_scan.py output schema | Task 8 |
| Discovery — slug_inventory.py naming convention | Task 10 |
| Discovery — slug_inventory.py per-product output | Task 10 + 11 (PROVIDER/SPAN_TYPE) + 12 (emit) |
| Discovery — slug_inventory.py collision handling | Task 12 |
| Bootstrap-team-engineer Q14b | Task 9 |
| Data flow diagram | Task 13 (documented in SKILL.md) |
| Error handling — bootstrap question never answered → TBD/default-fallback | Task 8 (parse_services_and_granularity) |
| Error handling — slug NAME collision → CRITICAL refuse-to-run | Task 12 (check_duplicates) |
| Error handling — multiple conflicting patterns → highest-confidence wins | Task 6 (scan_service sort key) |
| Testing — Unit tests for env_loader_scan + slug_inventory | Tasks 3-8, 10-12 (TDD within each) |
| Out of scope — instrument env-wire (Phase B), instrument slugs (Phase C), e2e fixture (Phase D) | Not in this plan; queued as follow-up plans |

All sections covered. No gaps.

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-06-env-routing-and-slugs-phase-a.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
