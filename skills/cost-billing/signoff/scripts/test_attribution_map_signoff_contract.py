from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "attribution_map_signoff.py"
SCHEMA_PATH = HERE.parent / "assets" / "signoff.schema.yaml"
FIXED_TIME = "2026-07-13T12:00:00Z"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "attribution_map_signoff_contract", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load attribution_map_signoff")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AttributionMapSignoffContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        source = self.repo / "app.py"
        source.write_text("print('source')\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "app.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "source",
            ],
            check=True,
        )
        self.commit = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.map_path = (
            self.repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
        )
        self.map_path.parent.mkdir(parents=True)
        self._write_map("clean", self.commit)

    def _write_map(self, state: str, commit: str | None) -> None:
        self.map_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "scanner_version": "1.0.0",
                    "generated_at": FIXED_TIME,
                    "source_revision": {"git_commit": commit, "state": state},
                    "source_fingerprint": {"algorithm": "sha256", "value": "1" * 64},
                    "discovery_projection": {
                        "routes_discovered": 0,
                        "routes_statically_covered": 0,
                        "routes_unknown": 0,
                    },
                    "services": [],
                    "findings": [],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _build(self, **overrides):
        arguments = {
            "repo": self.repo,
            "operator": "A. Engineer",
            "codegen_model": "codegen-a",
            "reviewer_model": "reviewer-b",
            "review_evidence": "review://ws5/finding-10",
            "review_verdict": "clean",
            "findings_resolved": 0,
            "findings_rejected_as_false_positive": 0,
            "generated_at": FIXED_TIME,
        }
        arguments.update(overrides)
        return self.module.build_signoff(self.map_path, **arguments)

    def test_create_binds_repo_relative_path_and_clean_map_revision(self) -> None:
        signoff = self._build()

        self.assertEqual(
            signoff["artifact"]["path"],
            ".moolabs/attribution/instrumentation-map.yaml",
        )
        self.assertEqual(signoff["artifact"]["source_commit"], self.commit)
        self.assertTrue(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_create_rejects_dirty_unversioned_and_missing_commits(self) -> None:
        cases = (("dirty", self.commit), ("unversioned", None), ("clean", "f" * 40))
        for state, commit in cases:
            with self.subTest(state=state, commit=commit):
                self._write_map(state, commit)
                with self.assertRaises(ValueError):
                    self._build()

    def test_create_requires_cross_model_review_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "distinct"):
            self._build(reviewer_model=" CODEGEN-A ")
        with self.assertRaisesRegex(ValueError, "evidence"):
            self._build(review_evidence="  ")

    def test_create_fails_closed_on_incomplete_proposed_resolver(self) -> None:
        document = json.loads(self.map_path.read_text(encoding="utf-8"))
        document["services"] = [
            {
                "service_path": ".",
                "frameworks": ["fastapi"],
                "ingress_state": "http-ingress",
                "middleware_detected": True,
                "routes": [],
                "mounts": [],
                "resolver": {
                    "state": "proposed",
                    "identity_kind": None,
                    "expression": None,
                    "template": None,
                    "evidence": None,
                },
                "async_hops": [],
                "findings": [],
            }
        ]
        self.map_path.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "resolver"):
            self._build()

    def test_create_rejects_partial_route_contract(self) -> None:
        document = json.loads(self.map_path.read_text(encoding="utf-8"))
        document["discovery_projection"] = {
            "routes_discovered": 1,
            "routes_statically_covered": 1,
            "routes_unknown": 0,
        }
        document["services"] = [
            {
                "service_path": ".",
                "frameworks": ["fastapi"],
                "ingress_state": "http-ingress",
                "middleware_detected": True,
                "routes": [
                    {
                        "method": "GET",
                        "path_template": "/v1/items",
                        "auth_scope": "handler",
                    }
                ],
                "mounts": [],
                "resolver": {
                    "state": "proposed",
                    "identity_kind": "moolabs_uuid",
                    "expression": "request.state.customer_id",
                    "template": "validate before binding",
                    "evidence": {"file": "app.py", "line": 1},
                },
                "async_hops": [],
                "findings": [],
            }
        ]
        self.map_path.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "route"):
            self._build()

    def test_create_rejects_malformed_mount_contract(self) -> None:
        valid_mount = {
            "framework": "fastapi",
            "target": "api_router",
            "prefix": "/v1",
            "confidence": "high",
            "evidence": {"file": "app.py", "line": 2},
        }
        invalid_mounts = {
            "partial": {"framework": "fastapi"},
            "extra-field": {**valid_mount, "unexpected": True},
            "blank-framework": {**valid_mount, "framework": "  "},
            "blank-target": {**valid_mount, "target": ""},
            "bad-prefix-type": {**valid_mount, "prefix": 42},
            "bad-confidence": {**valid_mount, "confidence": "certain"},
            "bad-evidence": {**valid_mount, "evidence": {"file": "app.py"}},
        }

        for name, mount in invalid_mounts.items():
            with self.subTest(name=name):
                self._write_map("clean", self.commit)
                document = json.loads(self.map_path.read_text(encoding="utf-8"))
                document["services"] = [
                    {
                        "service_path": ".",
                        "frameworks": ["fastapi"],
                        "ingress_state": "http-ingress",
                        "middleware_detected": True,
                        "routes": [],
                        "mounts": [mount],
                        "resolver": {
                            "state": "proposed",
                            "identity_kind": "moolabs_uuid",
                            "expression": "request.state.customer_id",
                            "template": "validate before binding",
                            "evidence": {"file": "app.py", "line": 1},
                        },
                        "async_hops": [],
                        "findings": [],
                    }
                ]
                self.map_path.write_text(json.dumps(document), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "mount"):
                    self._build()

    def test_create_rejects_malformed_async_hop_contract(self) -> None:
        valid_hop = {
            "kind": "http",
            "propagation": "verified",
            "evidence": {"file": "app.py", "line": 3},
        }
        invalid_hops = {
            "partial": {"propagation": "verified"},
            "extra-field": {**valid_hop, "unexpected": True},
            "blank-kind": {**valid_hop, "kind": "  "},
            "bad-propagation": {**valid_hop, "propagation": "complete"},
            "bad-evidence": {**valid_hop, "evidence": None},
        }

        for name, hop in invalid_hops.items():
            with self.subTest(name=name):
                self._write_map("clean", self.commit)
                document = json.loads(self.map_path.read_text(encoding="utf-8"))
                document["services"] = [
                    {
                        "service_path": ".",
                        "frameworks": ["fastapi"],
                        "ingress_state": "http-ingress",
                        "middleware_detected": True,
                        "routes": [],
                        "mounts": [],
                        "resolver": {
                            "state": "proposed",
                            "identity_kind": "moolabs_uuid",
                            "expression": "request.state.customer_id",
                            "template": "validate before binding",
                            "evidence": {"file": "app.py", "line": 1},
                        },
                        "async_hops": [hop],
                        "findings": [],
                    }
                ]
                self.map_path.write_text(json.dumps(document), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "async hop"):
                    self._build()

    def test_cli_requires_explicit_review_outcome_counts(self) -> None:
        parser = self.module._build_parser()
        required = [
            "create",
            str(self.map_path),
            "--repo",
            str(self.repo),
            "--output",
            str(self.repo / "signoff.yaml"),
            "--operator",
            "A. Engineer",
            "--codegen-model",
            "codegen-a",
            "--reviewer-model",
            "reviewer-b",
            "--review-evidence",
            "review://ws5/finding-10",
            "--review-verdict",
            "clean",
        ]

        with self.assertRaises(SystemExit):
            parser.parse_args(required)

        parsed = parser.parse_args(
            required
            + [
                "--findings-resolved",
                "9",
                "--findings-rejected-as-false-positive",
                "2",
            ]
        )
        self.assertEqual(parsed.findings_resolved, 9)
        self.assertEqual(parsed.findings_rejected_as_false_positive, 2)

    def test_create_and_verify_require_structured_review_evidence(self) -> None:
        for evidence in ("review completed", "looks-good", "https://"):
            with self.subTest(evidence=evidence):
                with self.assertRaisesRegex(ValueError, "evidence"):
                    self._build(review_evidence=evidence)

        for evidence in (
            "review://ws5/finding-10",
            "https://reviews.example.test/ws5/10",
            "WS5-REVIEW-10",
        ):
            with self.subTest(evidence=evidence):
                signoff = self._build(review_evidence=evidence)
                self.assertTrue(
                    self.module.verify_signoff(self.repo, self.map_path, signoff)
                )

        signoff = self._build()
        signoff["adversarial_review"]["review_evidence"] = "arbitrary non-empty text"
        self.assertFalse(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_verify_requires_the_complete_attribution_signoff_shape(self) -> None:
        mutations = {
            "extra-top-level": lambda value: value.__setitem__("unexpected", True),
            "extra-signer-field": lambda value: value["signed_by"].__setitem__(
                "unexpected", True
            ),
            "extra-review-field": lambda value: value["adversarial_review"].__setitem__(
                "unexpected", True
            ),
            "extra-artifact-field": lambda value: value["artifact"].__setitem__(
                "unexpected", True
            ),
            "blank-signer-name": lambda value: value["signed_by"].__setitem__(
                "name", "  "
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                signoff = self._build()
                mutate(signoff)
                self.assertFalse(
                    self.module.verify_signoff(self.repo, self.map_path, signoff)
                )

    def test_verify_derives_cross_model_violation(self) -> None:
        signoff = self._build()
        signoff["adversarial_review"]["reviewer_model"] = "CODEGEN-A"
        signoff["adversarial_review"]["cross_model_violated"] = False

        self.assertFalse(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_verify_rejects_changed_map_revision_and_artifact_location(self) -> None:
        signoff = self._build()
        changed = copy.deepcopy(signoff)
        changed["artifact"]["source_commit"] = "f" * 40
        self.assertFalse(self.module.verify_signoff(self.repo, self.map_path, changed))

        relocated = self.repo / "other-map.yaml"
        relocated.write_bytes(self.map_path.read_bytes())
        self.assertFalse(self.module.verify_signoff(self.repo, relocated, signoff))

    def test_verify_manually_validates_required_fields_types_timestamps_and_counts(
        self,
    ) -> None:
        mutations = {
            "missing-generated-at": lambda value: value.pop("generated_at"),
            "bad-generated-at": lambda value: value.__setitem__(
                "generated_at", "yesterday"
            ),
            "non-rfc3339-generated-at": lambda value: value.__setitem__(
                "generated_at", "2026-07-13 12:00:00+00:00"
            ),
            "signed-name-type": lambda value: value["signed_by"].__setitem__(
                "name", 42
            ),
            "bad-signed-at": lambda value: value["signed_by"].__setitem__(
                "signed_at", "later"
            ),
            "missing-review-count": lambda value: value["adversarial_review"].pop(
                "findings_total"
            ),
            "boolean-review-count": lambda value: value[
                "adversarial_review"
            ].__setitem__("findings_total", False),
            "negative-review-count": lambda value: value[
                "adversarial_review"
            ].__setitem__("findings_total", -1),
            "bad-review-time": lambda value: value["adversarial_review"].__setitem__(
                "ran_at", "noon"
            ),
            "review-evidence-type": lambda value: value[
                "adversarial_review"
            ].__setitem__("review_evidence", []),
            "artifact-risks-type": lambda value: value["artifact"].__setitem__(
                "accepted_risks", "none"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                signoff = self._build()
                mutate(signoff)
                self.assertFalse(
                    self.module.verify_signoff(self.repo, self.map_path, signoff)
                )

    def test_schema_requires_codegen_model_and_review_evidence(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        attribution_branch = schema.split(
            "stage: { const: engineer-attribution-map }", 1
        )[1]
        self.assertIn("- codegen_model", attribution_branch)
        self.assertIn("- review_evidence", attribution_branch)
        self.assertIn(
            "review_evidence:",
            attribution_branch,
        )
        self.assertIn("review://", attribution_branch)
        self.assertIn("https?://", attribution_branch)
        self.assertIn(
            'source_commit: { type: string, pattern: "^(?:[a-f0-9]{40}|[a-f0-9]{64})$" }',
            schema,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
