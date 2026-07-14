from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "drift_lint.py"
FIXED_TIME = "2026-07-13T12:00:00Z"


def _load_module():
    sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location("drift_contract", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load drift_lint")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _route() -> dict:
    return {
        "route_id": "route-1",
        "framework": "fastapi",
        "method": "GET",
        "path_template": "/items",
        "confidence": "high",
        "auth_scope": "handler",
        "evidence": {"file": "app.py", "line": 1},
        "feature_proposal": {
            "slug": "items",
            "confidence": "high",
            "requires_engineer_signoff": True,
        },
    }


def _map(commit: str, state: str = "clean") -> dict:
    return {
        "schema_version": "1.0",
        "scanner_version": "1.0.0",
        "generated_at": FIXED_TIME,
        "source_revision": {"git_commit": commit if state != "unversioned" else None, "state": state},
        "source_fingerprint": {"algorithm": "sha256", "value": "1" * 64},
        "discovery_projection": {
            "routes_discovered": 1,
            "routes_statically_covered": 1,
            "routes_unknown": 0,
        },
        "services": [
            {
                "service_path": ".",
                "frameworks": ["fastapi"],
                "ingress_state": "http-ingress",
                "middleware_detected": True,
                "routes": [_route()],
                "mounts": [],
                "resolver": {
                    "state": "unresolved",
                    "identity_kind": None,
                    "expression": None,
                    "template": None,
                    "evidence": None,
                },
                "async_hops": [],
                "findings": [],
            }
        ],
        "findings": [],
    }


class DriftContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()

    def test_feature_proposal_change_is_drift(self) -> None:
        baseline = _map("a" * 40)
        current = copy.deepcopy(baseline)
        current["services"][0]["routes"][0]["feature_proposal"]["slug"] = "renamed"

        codes = [finding["code"] for finding in self.module.compare(baseline, current)]

        self.assertIn("feature_proposal_changed", codes)

    def test_policy_accepts_only_one_top_level_enforcement_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            policy = repo / ".moolabs" / "attribution-policy.yaml"
            policy.parent.mkdir()
            for value in ("warn", "block"):
                with self.subTest(value=value):
                    policy.write_text(f"enforcement: {value}\n", encoding="utf-8")
                    self.assertEqual(self.module._policy(repo), value)

            invalid_documents = {
                "duplicate": "enforcement: warn\nenforcement: block\n",
                "nested": "policy:\n  enforcement: block\n",
                "extra": "enforcement: warn\nmode: strict\n",
                "malformed": "enforcement block\n",
                "sequence": "- enforcement: block\n",
            }
            for name, document in invalid_documents.items():
                with self.subTest(name=name):
                    policy.write_text(document, encoding="utf-8")
                    with self.assertRaises(self.module.DiscoveryError):
                        self.module._policy(repo)

    def test_block_gate_rejects_non_clean_map_source_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            source = repo / "app.py"
            source.write_text("print('source')\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
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
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            baseline_path = repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
            baseline_path.parent.mkdir(parents=True)
            signoff_path = baseline_path.with_name("instrumentation-map-signoff.yaml")

            for state in ("dirty", "unversioned"):
                with self.subTest(state=state):
                    baseline = _map(commit, state)
                    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
                    signoff_path.write_text(
                        json.dumps(self._signoff(baseline_path, commit, baseline_path.relative_to(repo).as_posix())),
                        encoding="utf-8",
                    )
                    with self.assertRaises(self.module.DiscoveryError):
                        self.module._require_block_signoff(repo, baseline_path, baseline)

    def test_block_gate_rejects_boolean_review_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "app.py").write_text("print('source')\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
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
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            baseline = _map(commit)
            baseline_path = repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
            baseline_path.parent.mkdir(parents=True)
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            signoff = self._signoff(
                baseline_path,
                commit,
                baseline_path.relative_to(repo).as_posix(),
            )
            signoff["adversarial_review"]["findings_total"] = False
            baseline_path.with_name("instrumentation-map-signoff.yaml").write_text(
                json.dumps(signoff),
                encoding="utf-8",
            )

            with self.assertRaises(self.module.DiscoveryError):
                self.module._require_block_signoff(repo, baseline_path, baseline)

    def test_block_gate_rejects_invalid_evidence_and_incomplete_schema_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "app.py").write_text("print('source')\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                 "user.email=test@example.com", "commit", "-qm", "source"],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            baseline = _map(commit)
            baseline_path = repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
            baseline_path.parent.mkdir(parents=True)
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            signoff_path = baseline_path.with_name("instrumentation-map-signoff.yaml")

            for mutation in ("invalid-evidence", "extra-field"):
                with self.subTest(mutation=mutation):
                    signoff = self._signoff(
                        baseline_path,
                        commit,
                        baseline_path.relative_to(repo).as_posix(),
                    )
                    if mutation == "invalid-evidence":
                        signoff["adversarial_review"]["review_evidence"] = "review completed"
                    else:
                        signoff["artifact"]["unexpected"] = True
                    signoff_path.write_text(json.dumps(signoff), encoding="utf-8")
                    with self.assertRaises(self.module.DiscoveryError):
                        self.module._require_block_signoff(repo, baseline_path, baseline)

    @staticmethod
    def _signoff(map_path: Path, commit: str, artifact_path: str) -> dict:
        return {
            "$schema": "https://moolabs.com/schemas/cost-billing-signoff/0.1.0",
            "stage": "engineer-attribution-map",
            "status": "approved",
            "generated_at": FIXED_TIME,
            "signed_by": {
                "role": "team-engineer",
                "name": "A. Engineer",
                "signed_at": FIXED_TIME,
            },
            "adversarial_review": {
                "phase": "post-signoff-engineer-attribution-map",
                "verdict": "clean",
                "codegen_model": "codegen-a",
                "reviewer_model": "reviewer-b",
                "review_evidence": "review://finding-10",
                "ran_at": FIXED_TIME,
                "findings_total": 0,
                "findings_human_accepted": 0,
                "findings_resolved": 0,
                "findings_rejected_as_false_positive": 0,
                "cross_model_violated": False,
            },
            "artifact": {
                "kind": "attribution-instrumentation-map",
                "path": artifact_path,
                "sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
                "source_commit": commit,
                "accepted_risks": [],
            },
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
