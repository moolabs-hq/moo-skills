#!/usr/bin/env python3
"""Render-smoke for the 6 callsite codemod templates under the REAL StrictUndefined
jinja env — across usage-only / cost-only / sibling-pair, with the entry shapes the
discovery inventory ACTUALLY produces.

Why this exists (dogfood 2026-06-08, findings E + F): the suite's Phase-7 render-
smoke used a *tolerant* Environment and a fixture where idempotency_anchor /
cost_micros_source / cost_kind were ALWAYS populated. The real codemod renders
under StrictUndefined, and real usage-only inventory entries have NO
idempotency_anchor (discovery sets it only on cost-only entries). So every
usage-only / sibling-pair insert raised UndefinedError at render time — 7/8
moo-arc inserts — and no test caught it. This test renders under StrictUndefined
with the realistic (sparse) entry shapes and py_compiles the Python output.
"""
from __future__ import annotations

import py_compile
import tempfile
import unittest
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    _HAVE_JINJA = True
except ImportError:  # pragma: no cover
    _HAVE_JINJA = False

_TPL_DIR = Path(__file__).resolve().parents[1] / "assets" / "codemod-templates"
_PY_TEMPLATES = ["python-fastapi.j2", "python-django.j2", "python-flask.j2"]
_TS_TEMPLATES = ["typescript-express.j2", "typescript-nestjs.j2", "typescript-nextjs.j2"]


def _sources(request_id="get_correlation_id()"):
    # request_id source matches the attribution_imports fixture (get_correlation_id)
    # so the rendered insert imports AND uses it — the real F821 scenario.
    return {"request_id": request_id, "customer_id": "req.state.cid",
            "consumer_agent": None, "feature_key": None,
            "entity_id": "req.state.email_id"}


def _usage_entry():
    """A usage-only entry AS DISCOVERY ACTUALLY PRODUCES IT: NO idempotency_anchor
    (discovery only sets that on cost-only entries) — the omission that triggered E."""
    return {
        "pattern": "usage-only",
        "event_type": "email.composed",
        "workflow_id": "arc.dunning.email-composed",
        "refund_unit": {"unit": "completion", "derivation": 1},
        "slugs_import_path": "app.slugs_arc",
        "event_type_const": "EVENT_TYPE_EMAIL_COMPOSED",
        "meter_slug_const": "METER_SLUG_ARC_DUNNING_EMAIL_COMPOSED",
        "feature_key_const": "FEATURE_KEY_DUNNING",
        # binding-import for the request_id source (get_correlation_id) — the
        # codemod must emit it or the insert NameErrors (dogfood ruff F821).
        "attribution_imports": ["from app.monitoring.logging import get_correlation_id"],
        "helper_import_path": "app.services.moolabs_client",
        "emission_guard": None,
    }


def _cost_entry():
    return {
        "pattern": "cost-only",
        "event_type": "llmport.call",
        "workflow_id": "arc.shared.llmport-call",
        "idempotency_anchor": {"handler": "call_llm_json", "confidence": 0.8},
        "cost_kind": "llm-tokens",
        "cost_micros_source": "resp.usage.cost_micros",
        "slugs_import_path": "app.slugs_arc",
        "event_type_const": "EVENT_TYPE_LLMPORT_CALL",
        "span_type_const": "SPAN_TYPE_LLM_TOKENS",
        "feature_key_const": "FEATURE_KEY_SHARED",
        "attribution_imports": ["from app.monitoring.logging import get_correlation_id"],
        "helper_import_path": "app.services.moolabs_client",
        "emission_guard": None,
    }


def _sibling_entry():
    """sibling-pair: usage + cost in one call. Like usage-only, real inventory
    entries carry NO idempotency_anchor here either."""
    e = _usage_entry()
    e.update(pattern="sibling-pair", cost_kind="llm-tokens",
             cost_micros_source="resp.usage.cost_micros",
             span_type_const="SPAN_TYPE_LLM_TOKENS")
    return e


@unittest.skipUnless(_HAVE_JINJA, "jinja2 not installed")
class CallsiteRenderSmoke(unittest.TestCase):
    """Renders every callsite template under the REAL StrictUndefined env against
    the sparse entry shapes; Python output must py_compile."""

    @classmethod
    def setUpClass(cls):
        # MUST mirror the codemod's real env: StrictUndefined (an absent key is a
        # render error, not silently empty). A tolerant env hid E/F.
        cls.env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                              undefined=StrictUndefined, keep_trailing_newline=True)

    def _render(self, template, entry):
        return self.env.get_template(template).render(
            entry=entry, attribution_sources=_sources())

    def _assert_py_compiles(self, rendered, label):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(rendered)
            path = fh.name
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            self.fail(f"{label} did not py_compile:\n{rendered}\n--- {exc}")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_python_usage_only_renders_and_compiles(self):
        for tpl in _PY_TEMPLATES:
            with self.subTest(tpl=tpl):
                out = self._render(tpl, _usage_entry())
                self._assert_py_compiles(out, f"{tpl} usage-only")

    def test_python_cost_only_renders_and_compiles(self):
        for tpl in _PY_TEMPLATES:
            with self.subTest(tpl=tpl):
                out = self._render(tpl, _cost_entry())
                self._assert_py_compiles(out, f"{tpl} cost-only")

    def test_absent_event_type_falls_back_to_workflow_id(self):
        # round-3 CRITICAL: the cost-events schema has NO event_type property, so
        # a cost-only / sibling-pair entry with no event_type (and no resolved
        # event_type_const) would raise UndefinedError on `entry.event_type` under
        # StrictUndefined. Producer guarantees the key (None); templates fall back
        # to workflow_id. With NO const to short-circuit, this exercises the deref.
        for pattern in ("cost-only", "sibling-pair"):
            entry = {
                "pattern": pattern, "workflow_id": "api.completion.openai-chat",
                "event_type": None,            # producer-guaranteed key, absent value
                "event_type_const": None, "meter_slug_const": None,
                "feature_key_const": None, "span_type_const": None,
                "slugs_import_path": "app.slugs", "cost_kind": "llm-tokens",
                "cost_micros_source": "r.cm",
                "refund_unit": {"unit": "event", "derivation": 1},
                "attribution_imports": [], "helper_import_path": "app.services.moolabs_client",
        "emission_guard": None,
            }
            for tpl in _PY_TEMPLATES:
                with self.subTest(tpl=tpl, pattern=pattern):
                    out = self._render(tpl, entry)
                    self._assert_py_compiles(out, f"{tpl} {pattern} no-event_type")
                    self.assertNotIn('"None"', out)          # no bareword-None literal
                    self.assertIn("api.completion.openai-chat", out)  # workflow_id fallback
            # TS got the identical event_type fallback fix — render-check it too
            # (can't py_compile TS here; assert no crash + no 'None' literal).
            for tpl in _TS_TEMPLATES:
                with self.subTest(tpl=tpl, pattern=pattern):
                    out = self._render(tpl, entry)
                    self.assertNotIn("'None'", out)
                    self.assertIn("api.completion.openai-chat", out)

    def test_python_sibling_pair_renders_and_compiles(self):
        for tpl in _PY_TEMPLATES:
            with self.subTest(tpl=tpl):
                out = self._render(tpl, _sibling_entry())
                self._assert_py_compiles(out, f"{tpl} sibling-pair")

    def test_entity_id_is_metered_entity_and_refuses_when_unbound(self):
        # P/entity_id: entity_id must be the METERED ENTITY (the dedup grain), NOT the
        # per-request correlation id (which double-counts on retry). Bound -> emit
        # uses the entity + keeps correlation in meta. UNBOUND -> the codemod REFUSES
        # to emit (loud comment, no billable call), never silently bills on the
        # correlation id.
        for tpl in _PY_TEMPLATES:
            with self.subTest(tpl=tpl):
                out = self._render(tpl, _usage_entry())          # entity_id bound in _sources
                self.assertIn("entity_id=str(req.state.email_id)", out)   # the metered entity
                self.assertIn('"correlation_id":', out)                   # correlation kept in meta
                entity_line = next(l for l in out.splitlines() if "entity_id=str(" in l)
                self.assertNotIn("get_correlation_id", entity_line)       # NOT the correlation id
                # unbound -> refuse, no billable emit
                unbound = _sources()
                unbound["entity_id"] = None
                out2 = self.env.get_template(tpl).render(entry=_usage_entry(),
                                                         attribution_sources=unbound)
                self.assertNotIn("emit_usage_event_safe(", out2)
                self.assertIn("NOT EMITTED", out2)

    def test_emission_guard_gates_the_emit(self):
        # O: a CFO emission_guard must GATE the emit (bill only when not blocked).
        # py_compile can't catch an UNGUARDED emit (it compiles fine — that's why
        # it shipped), so assert the GATING: guard present -> `if <guard>:` precedes
        # the INDENTED emit; absent -> no wrapper, emit at column 0.
        guard = "result.get('blocked') is not True"
        for tpl in _PY_TEMPLATES:
            with self.subTest(tpl=tpl):
                e = _usage_entry(); e["emission_guard"] = guard
                out = self._render(tpl, e)
                self._assert_py_compiles(out, f"{tpl} guarded")
                lines = out.splitlines()
                gi = next(i for i, ln in enumerate(lines) if ln.strip() == f"if {guard}:")
                ei = next(i for i, ln in enumerate(lines) if "emit_usage_event_safe(" in ln)
                self.assertLess(gi, ei, "guard must precede the emit")
                self.assertTrue(lines[ei].startswith("    emit_usage_event_safe("),
                                "emit must be indented under the guard")
                # absent -> plain, no wrapper, emit at column 0
                e2 = _usage_entry(); e2["emission_guard"] = None
                out2 = self._render(tpl, e2)
                self.assertNotIn("if result.get('blocked')", out2)
                self.assertIn("\nemit_usage_event_safe(", "\n" + out2)

    def test_emission_guard_gates_typescript_emit(self):
        # TS form: `if (<guard>) { <emit> }` (render-only — no tsc here).
        guard = "!result.blocked"
        for tpl in _TS_TEMPLATES:
            with self.subTest(tpl=tpl):
                e = _usage_entry(); e["emission_guard"] = guard
                out = self._render(tpl, e)
                self.assertIn(f"if ({guard}) {{", out)
                gi = out.index(f"if ({guard})")
                ei = out.index("await emitUsageEventSafe({")
                self.assertLess(gi, ei, "guard must precede the emit")

    def test_usage_only_preserves_idempotency_review_surface(self):
        # The E guard must not SILENTLY drop the idempotency review prompt for
        # usage-only/sibling-pair (which never carry a derived anchor) — usage
        # events can double-count on retry, so the review surface is preserved via
        # the {% else %} generic prompt (advisor follow-up).
        for tpl in _PY_TEMPLATES:
            for entry in (_usage_entry(), _sibling_entry()):
                with self.subTest(tpl=tpl, pattern=entry["pattern"]):
                    out = self._render(tpl, entry)
                    # P (sharpened): the no-anchor prompt now asks the real dedup
                    # question — is entity_id retry-stable (a per-request id
                    # double-counts) — not just "ensure unique".
                    self.assertIn("REVIEW idempotency", out)
                    self.assertIn("STABLE across a retry", out)

    def test_typescript_all_patterns_render(self):
        # Can't compile TS here; assert StrictUndefined render succeeds + the
        # emit call appears (no UndefinedError on the missing idempotency_anchor).
        for tpl in _TS_TEMPLATES:
            for entry in (_usage_entry(), _cost_entry(), _sibling_entry()):
                with self.subTest(tpl=tpl, pattern=entry["pattern"]):
                    out = self._render(tpl, entry)
                    self.assertIn("emit", out)

    def test_unresolved_consts_do_not_emit_bareword_none(self):
        # F regression at the consume side: a const that is None must not appear
        # as a Python `None` identifier in an import (would be a SyntaxError).
        entry = _usage_entry()
        entry["event_type_const"] = None        # unresolved
        entry["meter_slug_const"] = None
        entry["feature_key_const"] = None
        out = self._render("python-fastapi.j2", entry)
        self.assertNotIn("import None", out)
        self.assertNotIn("    None,", out)
        self._assert_py_compiles(out, "fastapi usage-only with None consts")


_THIS_DIR = Path(__file__).resolve().parent
import sys as _sys
_sys.path.insert(0, str(_THIS_DIR))


@unittest.skipUnless(_HAVE_JINJA, "jinja2 not installed")
class EndToEndPipeline(unittest.TestCase):
    """The check the dogfood measured at 0/8: build_tasks -> emit tasks.yaml ->
    reload -> render each insert under StrictUndefined -> py_compile, against a
    moo-arc-shaped inventory (usage-only with NO idempotency_anchor; cost-only
    with cost_dimension, no cost_micros_source, prose derivation; attribution
    bindings missing consumer_agent). This single test exercises E + F + G + H +
    the attribution sibling together — the coverage that was missing."""

    def _render_contract(self, env, template, ins):
        # Mirror the REAL Phase 2d render contract: the subagent substitutes the
        # insert's `entry` block + `attribution_sources` into the template — NO
        # hand-merge. `entry.pattern` must therefore be present IN the entry block
        # (build_tasks guarantees it). If a test had to inject pattern by hand,
        # the producer would be wrong and the real codemod would 0/8 (advisor catch).
        self.assertIn("pattern", ins["entry"],
                      "entry block must carry pattern for the Phase 2d render contract")
        return env.get_template(template.split("/")[-1]).render(
            entry=ins["entry"], attribution_sources=ins["attribution_sources"])

    def test_moo_arc_shape_renders_and_compiles_end_to_end(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        import task_planner as tp
        env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                          undefined=StrictUndefined, keep_trailing_newline=True)
        usage_inv = {"entries": [{
            "file": "app/agents/email.py", "line": 40,
            "workflow_id": "arc.dunning.email-composed", "event_type": "email.composed",
            "product_slug": "arc",
            "refund_unit": {"unit": "completion",
                            "derivation": "1 apply_remittance completion (post-success)"}}]}
        cost_inv = {"entries": [{
            "file": "app/llm_helpers.py", "line": 209,
            "workflow_id": "arc.shared.llmport-call",
            # NO event_type — the cost-events schema has no such property; a
            # cost entry carries workflow_id. Over-populating event_type here is
            # exactly what hid the round-3 CRITICAL.
            "classification": "cost-only", "product_slug": "arc",
            "cost_dimension": "llm_tokens"}]}
        signed = {"service_slug": "moo-arc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            tasks = tp.build_tasks(
                cost_inv, usage_inv, {"edges": []}, {"capabilities": {}}, signed,
                {"language": "python", "framework": "fastapi"},
                attribution_defaults={"customer_id": "self.tenant_id",
                                      "request_id": "get_correlation_id()"},
                attribution_overrides=[], slug_inventory=None)
        self.assertTrue(tasks, "moo-arc shape should yield tasks")
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "tasks.yaml"
            tp.emit_tasks_yaml(tasks, dest)
            reloaded = yaml.safe_load(dest.read_text())

        rendered_count = 0
        for t in reloaded["tasks"]:
            for ins in t["inserts"]:
                out = self._render_contract(env, t["template"], ins)
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
                    fh.write(out)
                    path = fh.name
                try:
                    py_compile.compile(path, doraise=True)
                    rendered_count += 1
                except py_compile.PyCompileError as exc:
                    self.fail(f"insert {ins['entry']['workflow_id']} "
                              f"({ins['pattern']}) did not compile:\n{out}\n--- {exc}")
                finally:
                    Path(path).unlink(missing_ok=True)
        self.assertGreaterEqual(rendered_count, 2, "expected both inserts to render")
        # G surfaced: the cost-only entry with no cost_micros_source is flagged.
        flagged = any(i["entry"].get("cost_value_missing")
                      for t in reloaded["tasks"] for i in t["inserts"])
        self.assertTrue(flagged, "cost_value_missing must be flagged for the LLMPort entry")

    def test_helper_and_stub_e501_clean_at_generous_realistic_slug(self):
        # rounds 10-11: the helper/stub docstrings interpolate variable-length
        # customer values (service_slug, generated_at, chain file path). The
        # COMMITTED bound (see SKILL.md): the codemod's own static content never
        # causes E501, and the Python helper + stub are ruff-clean at a
        # GENEROUS-REALISTIC shape — a 21-char service_slug (covers ~all real
        # slugs), the longest REAL chain stage ("team-engineer"), the real
        # 04-final-<slug>.signed.yaml path convention, and a microsecond ISO ts.
        # ACCEPTED RESIDUE (not asserted): the slugs-module CONST line
        # (LONG_NAME: str = "...") is an unwrappable assignment whose name is the
        # customer's own slug — a pathologically long slug overflows it (and the
        # customer's own code) regardless; and the .ts/.go/.tf templates can't be
        # linted here (no eslint/golangci). Import lines are formatter-resolved
        # (ruff format wraps them). The earlier "worst_case" fixture was DISHONEST
        # — its 8-char stage + non-convention path passed while a real slug
        # overflowed; this fixture matches the bound it claims to guard.
        import shutil
        import subprocess
        if shutil.which("ruff") is None:
            self.skipTest("ruff not installed")
        env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                          undefined=StrictUndefined, keep_trailing_newline=True)
        slug = "acme-billing-platform"  # 21 chars — generous realistic
        gates = [{"stage": "team-engineer",
                  "file": f".moolabs/chain/04-final-{slug}.signed.yaml",
                  "sha256": "fedcba0987654321"}]
        ts = "2026-06-08T00:00:00.123456+00:00"
        renders = {
            "python-moolabs-client.py.j2": {
                "service_slug": slug, "signoff_chain_hashes": gates,
                "sdk_pinned_version": "v0.3.0-rc1", "telemetry": {"mode": "brownfield"},
                "env_config": {"mode": "modify", "settings_import_path": "app.config",
                               "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
                               "stub_emit_path": None},
                "generated_at": ts},
            "python-moolabs-settings.py.j2": {"service_slug": slug, "generated_at": ts,
                "env_config": {"mode": "stub", "settings_import_path": "app.config",
                               "stub_emit_path": "app/moolabs_settings.py", "api_key_accessor": "x"}},
        }
        for tpl, ctx in renders.items():
            with self.subTest(template=tpl):
                out = env.get_template(tpl).render(**ctx)
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
                    fh.write(out)
                    path = fh.name
                try:
                    r = subprocess.run(["ruff", "check", "--select", "E501", "--isolated", path],
                                       capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, f"{tpl} has E501 at the committed bound:\n{r.stdout}")
                finally:
                    Path(path).unlink(missing_ok=True)

    def test_callsite_helper_import_resolves_for_non_app_layout(self):
        # P0 GATE (verbatim-dogfood): the portability leak moo-arc STRUCTURALLY
        # could not catch — its package root is `app`, so the old hardcoded
        # `from app.services.moolabs_client import ...` resolved by coincidence; a
        # customer rooted at src/<pkg>/ got ModuleNotFoundError. With the helper
        # import derived from the service's stub anchor, a NON-app anchor must
        # produce `from <pkg>.services.moolabs_client import ...` — never app.services.
        import io
        import contextlib
        import task_planner as tp
        anchor = ("services/svc/src/mypkg/services/moolabs_settings.py",
                  "mypkg.services.moolabs_settings")  # a src/<pkg>/ layout
        usage_inv = {"entries": [{"file": "services/svc/src/mypkg/api.py", "line": 5,
                                  "workflow_id": "svc.checkout", "event_type": "svc.checkout",
                                  "product_slug": "svc"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        with contextlib.redirect_stderr(io.StringIO()):
            tasks = tp.build_tasks(
                {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}}, signed,
                {"language": "python", "framework": "fastapi"},
                attribution_defaults={"customer_id": "self.tenant_id", "request_id": "r"},
                attribution_overrides=[], slug_inventory=None, anchor=anchor)
        ins = tasks[0].inserts[0]
        self.assertEqual(ins.entry["helper_import_path"], "mypkg.services.moolabs_client")
        env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                          undefined=StrictUndefined, keep_trailing_newline=True)
        out = env.get_template("python-fastapi.j2").render(
            entry=ins.entry, attribution_sources=ins.attribution_sources)
        self.assertIn("from mypkg.services.moolabs_client import", out)
        self.assertNotIn("from app.services.moolabs_client import", out)  # leak gone

    def _build_one(self, entry):
        import io
        import contextlib
        import task_planner as tp
        signed = {"service_slug": "svc", "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        with contextlib.redirect_stderr(io.StringIO()):
            tasks = tp.build_tasks(
                {"entries": []}, {"entries": [entry]}, {"edges": []}, {"capabilities": {}}, signed,
                {"language": "python", "framework": "fastapi"},
                attribution_defaults={"customer_id": "self.tenant_id", "request_id": "r"},
                attribution_overrides=[], slug_inventory=None)
        return tasks[0].inserts[0]

    def test_entity_id_capture_proposed_candidate_still_refuses(self):
        # THE blocking assertion: a discovery-PROPOSED entity_id candidate is NOT a
        # confirmed binding. The planner reads only the confirmed `entity_id`; a
        # candidate-only entry must still flow entity_id=None -> the template REFUSES.
        # (If a candidate ever satisfied the gate, the refuse-don't-fallback invariant
        # would be defeated through the back door.)
        base = {"file": "svc/api.py", "line": 5, "workflow_id": "svc.x",
                "event_type": "svc.x", "product_slug": "svc"}
        env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                          undefined=StrictUndefined, keep_trailing_newline=True)
        # proposed-but-unconfirmed -> refuse
        ins = self._build_one({**base, "entity_id_candidate": ["email_id"]})
        self.assertIsNone(ins.attribution_sources.get("entity_id"))
        out = env.get_template("python-fastapi.j2").render(
            entry=ins.entry, attribution_sources=ins.attribution_sources)
        self.assertNotIn("emit_usage_event_safe(", out)
        self.assertIn("NOT EMITTED", out)
        # CONFIRMED -> the per-entry entity flows + emits
        ins2 = self._build_one({**base, "entity_id": "self.email_id"})
        self.assertEqual(ins2.attribution_sources.get("entity_id"), "self.email_id")
        out2 = env.get_template("python-fastapi.j2").render(
            entry=ins2.entry, attribution_sources=ins2.attribution_sources)
        self.assertIn("entity_id=str(self.email_id)", out2)

    def test_anchor_without_confidence_renders(self):
        # round-4 CRITICAL: the schema marks idempotency_anchor.confidence OPTIONAL;
        # the `is defined and entry.idempotency_anchor` guard checks the PARENT, so a
        # conformant anchor {handler, path_param} (no confidence) crashed the REVIEW
        # comment deref under StrictUndefined. The producer now guarantees the
        # sub-key. This goes build_tasks->emit->reload->render->py_compile on a
        # sibling-pair cost entry whose anchor omits confidence.
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        import task_planner as tp
        import io
        import contextlib
        env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                          undefined=StrictUndefined, keep_trailing_newline=True)
        cost_inv = {"entries": [{
            "file": "app/x.py", "line": 5, "workflow_id": "a.b.c",
            "classification": "sibling-pair", "product_slug": "p",
            "cost_dimension": "llm_tokens", "cost_micros_source": "r.cm",
            "idempotency_anchor": {"handler": "h", "path_param": "customer_id"}}]}  # NO confidence
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        with contextlib.redirect_stderr(io.StringIO()):
            tasks = tp.build_tasks(
                cost_inv, {"entries": []}, {"edges": []}, {"capabilities": {}}, signed,
                {"language": "python", "framework": "fastapi"},
                attribution_defaults={"customer_id": "c", "request_id": "r"},
                attribution_overrides=[], slug_inventory=None)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "tasks.yaml"
            tp.emit_tasks_yaml(tasks, dest)
            reloaded = yaml.safe_load(dest.read_text())
        ins = reloaded["tasks"][0]["inserts"][0]
        self.assertIn("confidence", ins["entry"]["idempotency_anchor"])
        out = self._render_contract(env, reloaded["tasks"][0]["template"], ins)
        self.assertIn("REVIEW: idempotency anchor (confidence=", out)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(out)
            path = fh.name
        try:
            py_compile.compile(path, doraise=True)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
