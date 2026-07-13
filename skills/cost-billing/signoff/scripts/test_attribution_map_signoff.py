from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "attribution_map_signoff.py"
SCHEMA_PATH = HERE.parent / "assets" / "signoff.schema.yaml"
STATE_MACHINE_PATH = HERE.parent / "assets" / "state-machine.yaml"
SKILL_PATH = HERE.parent / "SKILL.md"
REFERENCE_PATH = HERE.parent / "references" / "signoff-yaml-schema.md"
FIXED_TIME = "2026-07-13T12:00:00Z"


def _load_module():
    spec = importlib.util.spec_from_file_location("attribution_map_signoff", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load attribution_map_signoff")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AttributionMapSignoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "app.py").write_text("print('source')\n", encoding="utf-8")
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
        self.source_commit = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.map_path = self.repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
        self.map_path.parent.mkdir(parents=True)
        self._write_map()

    def _write_map(self) -> None:
        self.map_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_at": FIXED_TIME,
                    "source_revision": {"git_commit": self.source_commit, "state": "clean"},
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
            ),
            encoding="utf-8",
        )

    def _build(self, **overrides):
        arguments = {
            "repo": self.repo,
            "operator": "A. Engineer",
            "codegen_model": "codegen-a",
            "reviewer_model": "reviewer-b",
            "review_evidence": "review://ws5",
            "review_verdict": "clean",
            "findings_resolved": 0,
            "findings_rejected_as_false_positive": 0,
            "generated_at": FIXED_TIME,
        }
        arguments.update(overrides)
        return self.module.build_signoff(self.map_path, **arguments)

    def test_builds_engineer_owned_immutable_artifact_signoff(self) -> None:
        signoff = self._build()

        expected_digest = hashlib.sha256(self.map_path.read_bytes()).hexdigest()
        self.assertEqual(signoff["stage"], "engineer-attribution-map")
        self.assertEqual(signoff["status"], "approved")
        self.assertEqual(signoff["signed_by"]["role"], "team-engineer")
        self.assertEqual(signoff["artifact"]["sha256"], expected_digest)
        self.assertEqual(signoff["artifact"]["source_commit"], self.source_commit)
        self.assertTrue(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_digest_verification_rejects_mutated_map(self) -> None:
        signoff = self._build()
        self._write_map()
        self.map_path.write_text(self.map_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        self.assertFalse(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_accepted_risk_verdict_requires_explicit_risks(self) -> None:
        with self.assertRaisesRegex(ValueError, "accepted risk"):
            self._build(review_verdict="clean-with-accepted-risks", accepted_risks=[])

    def test_verification_rejects_missing_or_blocked_adversarial_review(self) -> None:
        signoff = self._build()
        del signoff["adversarial_review"]
        self.assertFalse(self.module.verify_signoff(self.repo, self.map_path, signoff))

        signoff = self._build()
        signoff["adversarial_review"]["verdict"] = "blocked"
        self.assertFalse(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_verification_reconciles_accepted_risks_with_review_counts(self) -> None:
        signoff = self._build(
            review_verdict="clean-with-accepted-risks",
            accepted_risks=["The unclassified queue is tracked in an incident."],
        )
        signoff["adversarial_review"]["findings_human_accepted"] = 0

        self.assertFalse(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_build_records_every_review_finding_outcome(self) -> None:
        signoff = self._build(
            review_verdict="clean-with-accepted-risks",
            accepted_risks=["A dynamic route remains unknown."],
            findings_resolved=9,
            findings_rejected_as_false_positive=2,
        )

        review = signoff["adversarial_review"]
        self.assertEqual(review["findings_human_accepted"], 1)
        self.assertEqual(review["findings_resolved"], 9)
        self.assertEqual(review["findings_rejected_as_false_positive"], 2)
        self.assertEqual(review["findings_total"], 12)
        self.assertTrue(self.module.verify_signoff(self.repo, self.map_path, signoff))

    def test_build_rejects_invalid_review_finding_counts(self) -> None:
        for field, value in (
            ("findings_resolved", -1),
            ("findings_resolved", True),
            ("findings_rejected_as_false_positive", -1),
            ("findings_rejected_as_false_positive", False),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "finding counts"):
                    self._build(**{field: value})

    def test_documentation_states_the_verifier_contract(self) -> None:
        documented_contract = (
            SKILL_PATH.read_text(encoding="utf-8")
            + REFERENCE_PATH.read_text(encoding="utf-8")
        )
        self.assertIn("missing or blocked review", documented_contract)
        self.assertIn("accepted-risk list and review counts", documented_contract)

    def test_schema_declares_attribution_map_stage_and_artifact(self) -> None:
        schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertIn("engineer-attribution-map", schema["properties"]["stage"]["enum"])
        self.assertIn("artifact", schema["properties"])
        serialized = yaml.safe_dump(schema, sort_keys=True)
        self.assertIn("post-signoff-engineer-attribution-map", serialized)
        self.assertIn("codegen_model", serialized)
        self.assertIn("review_evidence", serialized)

    def test_state_machine_has_engineer_only_artifact_branch(self) -> None:
        state_machine = yaml.safe_load(STATE_MACHINE_PATH.read_text(encoding="utf-8"))
        branch = state_machine["artifact_branches"]["attribution_instrumentation_map"]
        self.assertEqual(branch["owner"], "team-engineer")
        self.assertEqual(branch["stage"], "engineer-attribution-map")
        self.assertNotIn("cfo", yaml.safe_dump(branch))
        self.assertNotIn("team-product", yaml.safe_dump(branch))
        self.assertIn("sha256", branch["approval_requires"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
