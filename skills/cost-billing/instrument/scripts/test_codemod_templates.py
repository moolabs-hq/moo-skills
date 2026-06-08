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


def _sources(request_id="req.state.rid"):
    return {"request_id": request_id, "customer_id": "req.state.cid",
            "consumer_agent": None, "feature_key": None}


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

    def test_usage_only_preserves_idempotency_review_surface(self):
        # The E guard must not SILENTLY drop the idempotency review prompt for
        # usage-only/sibling-pair (which never carry a derived anchor) — usage
        # events can double-count on retry, so the review surface is preserved via
        # the {% else %} generic prompt (advisor follow-up).
        for tpl in _PY_TEMPLATES:
            for entry in (_usage_entry(), _sibling_entry()):
                with self.subTest(tpl=tpl, pattern=entry["pattern"]):
                    out = self._render(tpl, entry)
                    self.assertIn("REVIEW: idempotency", out)

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
