#!/usr/bin/env python3
"""Contract tests for the attribution middleware discovery CLIs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
FIXTURES = SKILL / "fixtures"
DISCOVER = SCRIPTS / "discover.py"
DRIFT = SCRIPTS / "drift_lint.py"
SIGNOFF = SKILL.parent / "cost-billing" / "signoff" / "scripts" / "attribution_map_signoff.py"
FIXED_TIME = "2026-07-13T00:00:00Z"


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCAN_MODULE = _load_script("attribution_scan", SCRIPTS / "attribution_scan.py")
DRIFT_MODULE = _load_script("attribution_drift_lint", DRIFT)


def _copy_fixture(name: str, target: Path) -> None:
    for source in (FIXTURES / name).rglob("*"):
        relative = source.relative_to(FIXTURES / name)
        destination = target / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class DiscoveryContractTests(unittest.TestCase):
    maxDiff = None

    def _discover(self, repo: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DISCOVER), "--repo", str(repo), "--output", str(output),
             "--generated-at", FIXED_TIME, *extra],
            text=True,
            capture_output=True,
            check=False,
        )

    def _load(self, path: Path) -> dict:
        try:
            import yaml
        except ImportError:  # Generated documents may use JSON, a YAML subset.
            return json.loads(path.read_text(encoding="utf-8"))
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_generates_deterministic_validated_map_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            _copy_fixture("mixed", repo)
            before = _tree_digest(repo)
            first = Path(directory) / "first.yaml"
            second = Path(directory) / "second.yaml"

            first_run = self._discover(repo, first)
            second_run = self._discover(repo, second)

            self.assertEqual(first_run.returncode, 0, first_run.stderr)
            self.assertEqual(second_run.returncode, 0, second_run.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(before, _tree_digest(repo))

            result = self._load(first)
            self.assertEqual(result["generated_at"], FIXED_TIME)
            self.assertEqual(result["scanner_version"], "1.0.0")
            self.assertEqual(set(result["source_fingerprint"]), {"algorithm", "value"})
            self.assertEqual([service["service_path"] for service in result["services"]],
                             ["go-api", "python-api", "web-api", "worker"])
            self.assertTrue((SKILL / "assets" / "instrumentation-map.schema.yaml").is_file())

    def test_detects_supported_ingress_with_exact_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("mixed", repo)
            output = repo / "map.yaml"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            routes = [route for service in result["services"] for route in service["routes"]]
            observed = {(route["framework"], route["method"], route["path_template"],
                         route["evidence"]["file"], route["evidence"]["line"])
                        for route in routes}
            self.assertIn(("fastapi", "GET", "/v1/items", "python-api/app.py", 9), observed)
            self.assertIn(("express", "POST", "/orders", "web-api/server.ts", 5), observed)
            self.assertIn(("nextjs-app-router", "GET", "/users/{id}",
                           "web-api/app/users/[id]/route.ts", 3), observed)
            self.assertIn(("chi", "GET", "/health", "go-api/main.go", 8), observed)

    def test_marks_dynamic_paths_unknown_and_rejects_raw_identity_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("mixed", repo)
            output = repo / "map.yaml"
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            web = next(service for service in result["services"] if service["service_path"] == "web-api")
            self.assertEqual(
                {(route["method"], route["path_template"]) for route in web["routes"]},
                {("POST", "/orders"), ("DELETE", None), ("GET", "/users/{id}")},
            )
            dynamic = next(route for route in web["routes"] if route["method"] == "DELETE")
            self.assertIsNone(dynamic["path_template"])
            self.assertEqual(dynamic["confidence"], "low")
            self.assertEqual(web["resolver"]["state"], "unresolved")
            self.assertTrue(any(finding["code"] == "raw_identity_header" and finding["severity"] == "high"
                                for finding in web["findings"]))

    def test_excludes_test_generated_and_vendor_files_and_marks_worker_only(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("mixed", repo)
            output = repo / "map.yaml"
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            all_files = [route["evidence"]["file"] for service in result["services"] for route in service["routes"]]
            self.assertNotIn("python-api/tests/test_routes.py", all_files)
            self.assertNotIn("web-api/generated/routes.ts", all_files)
            self.assertNotIn("go-api/vendor/routes.go", all_files)
            worker = next(service for service in result["services"] if service["service_path"] == "worker")
            self.assertEqual(worker["ingress_state"], "no-middleware-inherits-thread-id")
            self.assertEqual(worker["routes"], [])
            self.assertEqual(
                worker["resolver"],
                {
                    "state": "not-required",
                    "identity_kind": None,
                    "expression": None,
                    "template": None,
                    "evidence": None,
                },
            )

    def test_schema_restricts_not_required_resolvers_to_worker_ingress(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("mixed", repo)
            output = repo / "map.yaml"
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            worker = next(
                service
                for service in result["services"]
                if service["service_path"] == "worker"
            )
            worker["resolver"]["expression"] = "claims.customer_id"
            with self.assertRaises(SCAN_MODULE.DiscoveryError):
                SCAN_MODULE.validate_map(result)

            worker["resolver"]["expression"] = None
            for field, value in (
                ("frameworks", ["fastapi"]),
                ("middleware_detected", True),
                (
                    "mounts",
                    [
                        {
                            "framework": "fastapi",
                            "target": "api",
                            "prefix": "/api",
                            "confidence": "high",
                            "evidence": {"file": "worker/tasks.py", "line": 1},
                        }
                    ],
                ),
            ):
                original = worker[field]
                worker[field] = value
                with self.subTest(field=field), self.assertRaises(
                    SCAN_MODULE.DiscoveryError
                ):
                    SCAN_MODULE.validate_map(result)
                worker[field] = original

            ingress = next(
                service
                for service in result["services"]
                if service["ingress_state"] == "http-ingress"
            )
            ingress["resolver"] = {
                "state": "not-required",
                "identity_kind": None,
                "expression": None,
                "template": None,
                "evidence": None,
            }
            with self.assertRaises(SCAN_MODULE.DiscoveryError):
                SCAN_MODULE.validate_map(result)

    def test_excludes_generated_source_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example/go\n\nrequire github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                'package main\nimport "github.com/go-chi/chi/v5"\n'
                'func routes(r chi.Router) { r.Get("/owned", h) }\n',
                encoding="utf-8",
            )
            (repo / "api.gen.go").write_text(
                'package main\nfunc generated(r chi.Router) { r.Get("/generated", h) }\n',
                encoding="utf-8",
            )
            (repo / "router_test.go").write_text(
                'package main\nfunc testRoute(r chi.Router) { r.Get("/test", h) }\n',
                encoding="utf-8",
            )
            (repo / "test").mkdir()
            (repo / "test" / "routes.go").write_text(
                'package test\nfunc helper(r chi.Router) { r.Get("/test-helper", h) }\n',
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual([route["path_template"] for route in routes], ["/owned"])

    def test_go_routes_require_a_proven_chi_receiver(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example/go\n\nrequire github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                'package main\nimport "github.com/go-chi/chi/v5"\nfunc routes() {\n'
                '  r := chi.NewRouter()\n'
                '  r.Get("/owned", h)\n'
                '  token := headers.Get("Authorization")\n'
                '  service.Delete(ctx, id)\n'
                '  _, _ = token, service\n'
                '}\n',
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                [(route["method"], route["path_template"]) for route in routes],
                [("GET", "/owned")],
            )

    def test_go_chi_tokens_without_an_import_remain_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text("module example/go\n", encoding="utf-8")
            (repo / "main.go").write_text(
                'package main\nfunc routes() {\n'
                '  r := chi.NewRouter()\n'
                '  r.Use(AttributionMiddleware)\n'
                '  r.Get("/false-positive", h)\n'
                '}\n',
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertEqual(service["routes"], [])
            self.assertFalse(service["middleware_detected"])
            self.assertEqual(
                result["discovery_projection"],
                {
                    "routes_discovered": 0,
                    "routes_statically_covered": 0,
                    "routes_unknown": 0,
                },
            )

    def test_scanner_version_is_visible_and_participates_in_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n",
                encoding="utf-8",
            )
            services = [{"service_path": "."}]

            baseline = SCAN_MODULE._fingerprint(repo, services)
            with mock.patch.object(SCAN_MODULE, "SCANNER_VERSION", "1.0.1"):
                changed = SCAN_MODULE._fingerprint(repo, services)

            self.assertNotEqual(changed, baseline)

    def test_service_selector_accepts_exact_path_and_rejects_ambiguous_basenames(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("ambiguous", repo)
            output = repo / "map.yaml"
            selected = self._discover(repo, output, "--service", "apps/api")
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual([service["service_path"] for service in self._load(output)["services"]], ["apps/api"])
            ambiguous = self._discover(repo, output, "--service", "api")
            self.assertEqual(ambiguous.returncode, 2)
            self.assertIn("ambiguous", ambiguous.stderr.lower())

    def test_drift_is_warn_by_default_and_blocks_only_with_opt_in_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("drift", repo)
            app = repo / "service" / "app.py"
            app.write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "@app.middleware('http')\n"
                "async def attribution_context(request, call_next):\n"
                "    return await call_next(request)\n"
                "def resolver(auth=Depends(verify_jwt)):\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/old', dependencies=[Depends(require_auth), Depends(resolver)])\n"
                "def old():\n"
                "    return {}\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-qm", "fixture"],
                check=True,
            )
            baseline = repo / "baseline.yaml"
            initial = self._discover(repo, baseline)
            self.assertEqual(initial.returncode, 0, initial.stderr)
            policy = repo / ".moolabs" / "attribution-policy.yaml"
            policy.parent.mkdir()
            policy.write_text("enforcement: block\n", encoding="utf-8")
            unsigned = subprocess.run([sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline),
                                       "--generated-at", FIXED_TIME], text=True, capture_output=True, check=False)
            self.assertEqual(unsigned.returncode, 2)
            self.assertIn("signoff", unsigned.stderr.lower())

            signoff = policy.parent / "attribution" / "instrumentation-map-signoff.yaml"
            signoff.parent.mkdir()
            created = subprocess.run(
                [
                    sys.executable,
                    str(SIGNOFF),
                    "create",
                    str(baseline),
                    "--repo",
                    str(repo),
                    "--output",
                    str(signoff),
                    "--operator",
                    "A. Engineer",
                    "--codegen-model",
                    "scanner-codegen",
                    "--reviewer-model",
                    "independent-reviewer",
                    "--review-evidence",
                    "review://scanner-contract-test",
                    "--review-verdict",
                    "clean",
                    "--findings-resolved",
                    "0",
                    "--findings-rejected-as-false-positive",
                    "0",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            policy.unlink()
            app.write_text(app.read_text(encoding="utf-8") + '\n@app.get("/new")\ndef new():\n    return {}\n', encoding="utf-8")
            warn = subprocess.run([sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline),
                                   "--generated-at", FIXED_TIME], text=True, capture_output=True, check=False)
            self.assertEqual(warn.returncode, 0, warn.stderr)
            self.assertIn("route_added", warn.stdout)

            policy.write_text("enforcement: block\n", encoding="utf-8")
            block = subprocess.run([sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline),
                                    "--generated-at", FIXED_TIME], text=True, capture_output=True, check=False)
            self.assertEqual(block.returncode, 1, block.stderr)

            signoff_payload = json.loads(signoff.read_text(encoding="utf-8"))
            signoff_payload["artifact"]["path"] = "different-baseline.yaml"
            signoff.write_text(json.dumps(signoff_payload), encoding="utf-8")
            wrong_artifact = subprocess.run(
                [sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline),
                 "--generated-at", FIXED_TIME],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(wrong_artifact.returncode, 2)
            self.assertIn("exact baseline map", wrong_artifact.stderr)

            policy.write_text("enforcement: nope\n", encoding="utf-8")
            malformed = subprocess.run([sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline)],
                                       text=True, capture_output=True, check=False)
            self.assertEqual(malformed.returncode, 2)

    def test_generic_framework_middleware_is_not_attribution_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            python = repo / "python"
            python.mkdir()
            (python / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (python / "app.py").write_text(
                'from fastapi import FastAPI\napp = FastAPI()\n@app.middleware("http")\n'
                'async def request_logging(request, call_next):\n    return await call_next(request)\n'
                '@app.get("/python")\ndef route():\n    return {}\n', encoding="utf-8")
            web = repo / "web"
            web.mkdir()
            (web / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
            (web / "server.ts").write_text(
                'const app = express();\napp.use(express.json());\napp.get("/web", handler);\n', encoding="utf-8")
            go = repo / "go"
            go.mkdir()
            (go / "go.mod").write_text(
                'module example/go\n\nrequire github.com/go-chi/chi v1.0.0\n', encoding="utf-8")
            (go / "main.go").write_text(
                'package main\nimport "github.com/go-chi/chi"\n'
                'func main() {\n r := chi.NewRouter()\n r.Use(cors.Handler)\n r.Get("/go", h)\n}\n',
                encoding="utf-8")
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 0)
            self.assertTrue(all(not service["middleware_detected"] for service in result["services"]))
            self.assertTrue(all(any(f["code"] == "middleware_missing" for f in service["findings"])
                                for service in result["services"]))

    def test_route_id_survives_line_only_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("drift", repo)
            baseline = repo.parent / "baseline.yaml"
            initial = self._discover(repo, baseline)
            self.assertEqual(initial.returncode, 0, initial.stderr)
            app = repo / "service" / "app.py"
            app.write_text("# relocated\n" + app.read_text(encoding="utf-8"), encoding="utf-8")

            drift = subprocess.run([sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline),
                                    "--generated-at", FIXED_TIME], text=True, capture_output=True, check=False)
            self.assertEqual(drift.returncode, 0, drift.stderr)
            findings = self._load_text_json(drift.stdout)["findings"]
            self.assertEqual([finding["code"] for finding in findings], ["source_fingerprint_changed"])

    def test_root_next_app_router_and_net_http_handlefunc_are_honest(self):
        with tempfile.TemporaryDirectory() as directory:
            next_repo = Path(directory) / "next"
            route = next_repo / "app" / "api" / "users" / "[id]" / "route.ts"
            route.parent.mkdir(parents=True)
            (next_repo / "package.json").write_text('{"dependencies":{"next":"1"}}', encoding="utf-8")
            route.write_text("export async function GET() { return Response.json({}); }\n", encoding="utf-8")
            next_output = Path(directory) / "next-map.json"
            next_run = self._discover(next_repo, next_output)
            self.assertEqual(next_run.returncode, 0, next_run.stderr)
            next_route = self._load(next_output)["services"][0]["routes"][0]
            self.assertEqual(next_route["path_template"], "/api/users/{id}")

            go_repo = Path(directory) / "go"
            go_repo.mkdir()
            (go_repo / "go.mod").write_text("module example/go\n", encoding="utf-8")
            (go_repo / "main.go").write_text(
                'package main\nimport "net/http"\nfunc main(){ http.HandleFunc("/all", h) }\n', encoding="utf-8")
            go_output = Path(directory) / "go-map.json"
            go_run = self._discover(go_repo, go_output)
            self.assertEqual(go_run.returncode, 0, go_run.stderr)
            self.assertIsNone(self._load(go_output)["services"][0]["routes"][0]["method"])

    def test_service_discovery_excludes_workspace_copies_and_root_container(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text('{"workspaces":["services/*"]}', encoding="utf-8")
            service = repo / "services" / "api"
            service.mkdir(parents=True)
            (service / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
            (service / "app.js").write_text('app.get("/active", h);\n', encoding="utf-8")
            stale = repo / "worktrees" / "old" / "api"
            stale.mkdir(parents=True)
            (stale / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
            (stale / "app.js").write_text('app.get("/stale", h);\n', encoding="utf-8")
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual([s["service_path"] for s in self._load(output)["services"]], ["services/api"])

    def test_records_mount_evidence_without_inventing_dynamic_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("advanced", repo)
            output = repo.parent / "map.json"
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            python = next(s for s in self._load(output)["services"] if s["service_path"] == "python")
            self.assertIn(
                {"framework": "fastapi", "target": "router", "prefix": "/v1", "confidence": "high",
                 "evidence": {"file": "python/app.py", "line": 6}},
                python["mounts"],
            )

    def test_uuid_comment_does_not_promote_unvalidated_customer_context(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                'from fastapi import FastAPI\napp = FastAPI()\n# UUID is expected from JWT\n'
                'customer = claims.customer_id\n@app.get("/x")\ndef x(): return {}\n', encoding="utf-8")
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(self._load(output)["services"][0]["resolver"]["state"], "unresolved")

    def test_resolver_requires_exact_validated_candidate_and_verified_auth_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "@app.get('/trusted')\n"
                "def trusted(claims=Depends(verify_jwt)):\n"
                "    customer = claims.customer_id\n"
                "    parsed = UUID(customer)\n"
                "    UUID(other_customer)\n"
                "    return parsed\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            resolver = self._load(output)["services"][0]["resolver"]
            self.assertEqual(resolver["state"], "proposed")
            self.assertEqual(resolver["expression"], "claims.customer_id")
            self.assertEqual(resolver["identity_kind"], "moolabs_uuid")

            (repo / "app.py").write_text(
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "@app.get('/mismatch')\n"
                "def mismatch(claims=Depends(verify_jwt)):\n"
                "    customer = claims.customer_id\n"
                "    UUID(other_customer)\n"
                "    return customer\n",
                encoding="utf-8",
            )
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(self._load(output)["services"][0]["resolver"]["state"], "unresolved")

            (repo / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def helper(request):\n"
                "    raw = request.headers.get('X-Customer-ID')\n"
                "    customer = raw\n"
                "    return UUID(customer)\n"
                "@app.get('/raw')\n"
                "def raw_route(): return {}\n",
                encoding="utf-8",
            )
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["resolver"]["state"], "unresolved")
            self.assertTrue(any(item["code"] == "raw_identity_header" for item in service["findings"]))

    def test_resolver_ignores_unused_helpers_but_keeps_reachable_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            app = repo / "app.py"
            app.write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def unused(auth=Depends(verify_jwt)):\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

            app.write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(customer=Depends(resolve_customer)): return {}\n",
                encoding="utf-8",
            )

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            resolver = self._load(output)["services"][0]["resolver"]
            self.assertEqual(resolver["state"], "proposed")
            self.assertEqual(resolver["expression"], "auth.customer_id")

    def test_resolver_rejects_auth_like_dependency_without_verifier_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def fake_auth_context():\n"
                "    return {'customer_id': '00000000-0000-0000-0000-000000000000'}\n"
                "@app.get('/orders')\n"
                "def orders(auth=Depends(fake_auth_context)):\n"
                "    return uuid.UUID(auth.customer_id)\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

    def test_resolver_preserves_explicit_verified_auth_dependencies(self):
        verifier_names = ("verify_jwt", "verify_customer_token")
        for verifier_name in verifier_names:
            with self.subTest(verifier_name=verifier_name), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
                (repo / "app.py").write_text(
                    "import uuid\n"
                    "from fastapi import Depends, FastAPI\n"
                    "app = FastAPI()\n"
                    "@app.get('/orders')\n"
                    f"def orders(auth=Depends({verifier_name})):\n"
                    "    return uuid.UUID(auth.customer_id)\n",
                    encoding="utf-8",
                )
                output = repo.parent / "map.json"

                run = self._discover(repo, output)

                self.assertEqual(run.returncode, 0, run.stderr)
                resolver = self._load(output)["services"][0]["resolver"]
                self.assertEqual(resolver["state"], "proposed")
                self.assertEqual(resolver["expression"], "auth.customer_id")

    def test_resolver_rejects_overwritten_verified_auth_field(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    auth.customer_id = '00000000-0000-0000-0000-000000000000'\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(user=Depends(require_auth), customer=Depends(resolve_customer)):\n"
                "    return {'customer': str(customer)}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

    def test_resolver_rejects_verified_auth_field_overwritten_on_one_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(replace, auth=Depends(verify_jwt)):\n"
                "    if replace:\n"
                "        auth.customer_id = '00000000-0000-0000-0000-000000000000'\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(user=Depends(require_auth), customer=Depends(resolve_customer)):\n"
                "    return {'customer': str(customer)}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

    def test_resolver_rejects_verified_auth_field_overwritten_through_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    claims = auth\n"
                "    claims.customer_id = '00000000-0000-0000-0000-000000000000'\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(user=Depends(require_auth), customer=Depends(resolve_customer)):\n"
                "    return {'customer': str(customer)}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

    def test_resolver_rejects_alias_created_after_verified_field_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    auth.customer_id = '00000000-0000-0000-0000-000000000000'\n"
                "    claims = auth\n"
                "    return uuid.UUID(claims.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(user=Depends(require_auth), customer=Depends(resolve_customer)):\n"
                "    return {'customer': str(customer)}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

    def test_resolver_preserves_trust_after_unrelated_auth_field_write(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    auth.display_name = 'masked'\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(user=Depends(require_auth), customer=Depends(resolve_customer)):\n"
                "    return {'customer': str(customer)}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "proposed",
            )

    @staticmethod
    def _verified_auth_mutation_source(mutation: str) -> str:
        return (
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def resolve_customer(auth=Depends(verify_jwt)):\n"
            f"    {mutation}\n"
            "    return uuid.UUID(auth.customer_id)\n"
            "@app.get('/orders')\n"
            "def orders(user=Depends(require_auth), customer=Depends(resolve_customer)):\n"
            "    return {'customer': str(customer)}\n"
        )

    def test_resolver_rejects_verified_auth_field_overwritten_with_setattr(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                self._verified_auth_mutation_source(
                    "setattr(auth, 'customer_id', "
                    "'00000000-0000-0000-0000-000000000000')"
                ),
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

    def test_resolver_rejects_verified_auth_field_removed_with_delattr(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                self._verified_auth_mutation_source(
                    "delattr(auth, 'customer_id')"
                ),
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["resolver"]["state"],
                "unresolved",
            )

    def _discover_python_source(self, source: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(source, encoding="utf-8")
            output = repo.parent / "map.json"

            run = self._discover(repo, output)

            self.assertEqual(run.returncode, 0, run.stderr)
            return self._load(output)["services"][0]

    def test_resolver_accepts_keyword_verified_identity_source(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def trusted(request):\n"
            "    request.state.customer_id = verify_signed_customer_identity(\n"
            "        token=request.headers.get('X-Customer-ID'))\n"
            "    uuid.UUID(request.state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(trusted)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(
            [
                finding["code"]
                for finding in service["findings"]
                if finding["code"]
                in {"raw_identity_header", "verified_identity_header"}
            ],
            ["verified_identity_header"],
        )

    def test_resolver_preserves_each_completed_verified_context_binding(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def trusted(request):\n"
            "    request.state.customer_id = verify_signed_customer_identity(request.headers.get('X-Customer-ID'))\n"
            "    request.state.customer_id = verify_signed_customer_identity(request.headers.get('X-Moolabs-Customer'))\n"
            "    uuid.UUID(request.state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(trusted)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(
            [
                (finding["code"], finding["evidence"]["line"])
                for finding in service["findings"]
                if finding["code"]
                in {"raw_identity_header", "verified_identity_header"}
            ],
            [
                ("verified_identity_header", 5),
                ("verified_identity_header", 6),
            ],
        )

    def test_resolver_tracks_verified_request_context_alias(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def trusted(request):\n"
            "    state = request.state\n"
            "    state.customer_id = verify_signed_customer_identity(request.headers.get('X-Customer-ID'))\n"
            "    uuid.UUID(request.state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(trusted)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(
            service["resolver"]["expression"],
            "request.state.customer_id",
        )

    def test_resolver_does_not_trust_raw_request_context_alias(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def poisoned(request):\n"
            "    state = request.state\n"
            "    state.customer_id = request.headers.get('X-Customer-ID')\n"
            "    uuid.UUID(state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(poisoned)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "unresolved")
        self.assertTrue(
            any(
                finding["code"] == "raw_identity_header"
                for finding in service["findings"]
            )
        )

    def test_resolver_accepts_verified_request_context_setattr(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def trusted(request):\n"
            "    setattr(request.state, 'customer_id', verify_signed_customer_identity(\n"
            "        request.headers.get('X-Customer-ID')))\n"
            "    uuid.UUID(request.state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(trusted)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(
            [
                finding["code"]
                for finding in service["findings"]
                if finding["code"]
                in {"raw_identity_header", "verified_identity_header"}
            ],
            ["verified_identity_header"],
        )

    def test_request_object_alias_mutation_revokes_canonical_verified_context(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def trusted(request):\n"
            "    request.state.customer_id = verify_signed_customer_identity(\n"
            "        request.headers.get('X-Customer-ID'))\n"
            "    uuid.UUID(request.state.customer_id)\n"
            "    req = request\n"
            "    req.state.customer_id = load_untrusted_identity()\n"
            "    uuid.UUID(request.state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(trusted)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "unresolved")
        self.assertEqual(
            [
                finding["code"]
                for finding in service["findings"]
                if finding["code"]
                in {"raw_identity_header", "verified_identity_header"}
            ],
            ["verified_identity_header"],
        )

    def test_dynamic_sensitive_setattr_fails_closed_but_unrelated_receiver_does_not(self):
        cases = {
            "trusted-root": (
                "    field = choose_customer_feature_or_tenant_field()\n"
                "    setattr(auth, field, load_untrusted_identity())\n",
                "unresolved",
            ),
            "unrelated-root": (
                "    field = choose_customer_feature_or_tenant_field()\n"
                "    setattr(profile, field, load_untrusted_identity())\n",
                "proposed",
            ),
        }
        for name, (mutation, expected_state) in cases.items():
            with self.subTest(name=name):
                service = self._discover_python_source(
                    "import uuid\n"
                    "from fastapi import Depends, FastAPI\n"
                    "app = FastAPI()\n"
                    "def resolve_customer(profile, auth=Depends(verify_jwt)):\n"
                    f"{mutation}"
                    "    return uuid.UUID(auth.customer_id)\n"
                    "@app.get('/orders')\n"
                    "def orders(customer=Depends(resolve_customer)): return {}\n"
                )

                self.assertEqual(service["resolver"]["state"], expected_state)

    def test_verified_rebind_restores_field_after_dynamic_sensitive_setattr(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def resolve_customer(request, auth=Depends(verify_jwt)):\n"
            "    field = choose_customer_feature_or_tenant_field()\n"
            "    setattr(auth, field, load_untrusted_identity())\n"
            "    auth.customer_id = verify_signed_customer_identity(\n"
            "        request.headers.get('X-Customer-ID'))\n"
            "    return uuid.UUID(auth.customer_id)\n"
            "@app.get('/orders')\n"
            "def orders(customer=Depends(resolve_customer)): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(service["resolver"]["expression"], "auth.customer_id")

    def test_dynamic_sensitive_setattr_revokes_stale_local_field_provenance(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def resolve_customer(auth=Depends(verify_jwt)):\n"
            "    customer_id = auth.customer_id\n"
            "    field = choose_customer_feature_or_tenant_field()\n"
            "    setattr(auth, field, load_untrusted_identity())\n"
            "    return uuid.UUID(customer_id)\n"
            "@app.get('/orders')\n"
            "def orders(customer=Depends(resolve_customer)): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "unresolved")

    def test_object_delattr_revokes_verified_auth_field(self):
        service = self._discover_python_source(
            self._verified_auth_mutation_source(
                "object.__delattr__(auth, 'customer_id')"
            )
        )

        self.assertEqual(service["resolver"]["state"], "unresolved")

    def test_keyword_only_depends_parameter_is_a_trusted_root(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def resolve_customer(*, auth=Depends(verify_jwt)):\n"
            "    return uuid.UUID(auth.customer_id)\n"
            "@app.get('/orders')\n"
            "def orders(customer=Depends(resolve_customer)): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(service["resolver"]["expression"], "auth.customer_id")

    def test_app_router_and_mount_dependencies_are_ingress_reachable(self):
        cases = {
            "app": (
                "app = FastAPI(dependencies=[Depends(resolve_customer)])\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n"
            ),
            "router": (
                "router = APIRouter(dependencies=[Depends(resolve_customer)])\n"
                "@router.get('/orders')\n"
                "def orders(): return {}\n"
                "app = FastAPI()\n"
                "app.include_router(router)\n"
            ),
            "mount": (
                "router = APIRouter()\n"
                "@router.get('/orders')\n"
                "def orders(): return {}\n"
                "app = FastAPI()\n"
                "app.include_router(\n"
                "    router, dependencies=[Depends(resolve_customer)])\n"
            ),
        }
        for name, registration in cases.items():
            with self.subTest(name=name):
                service = self._discover_python_source(
                    "import uuid\n"
                    "from fastapi import APIRouter, Depends, FastAPI\n"
                    "def resolve_customer(*, auth=Depends(verify_jwt)):\n"
                    "    return uuid.UUID(auth.customer_id)\n"
                    f"{registration}"
                )

                self.assertEqual(service["resolver"]["state"], "proposed")
                self.assertEqual(
                    service["resolver"]["expression"],
                    "auth.customer_id",
                )

    def test_fastapi_keyword_route_arguments_preserve_paths_and_endpoint_reachability(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "def resolve_customer(*, auth=Depends(verify_jwt)):\n"
            "    return uuid.UUID(auth.customer_id)\n"
            "def registered(customer=Depends(resolve_customer)): return {}\n"
            "app = FastAPI()\n"
            "app.add_middleware(AttributionMiddleware)\n"
            "@app.get(path='/decorated')\n"
            "def decorated(): return {}\n"
            "app.add_api_route(\n"
            "    path='/registered', endpoint=registered, methods=['POST'])\n"
            "dynamic_path = load_route_path()\n"
            "@app.get(path=dynamic_path)\n"
            "def dynamic(): return {}\n"
        )

        self.assertEqual(
            {
                (route["method"], route["path_template"], route["confidence"])
                for route in service["routes"]
            },
            {
                ("GET", "/decorated", "high"),
                ("POST", "/registered", "high"),
                ("GET", None, "low"),
            },
        )
        self.assertEqual(service["resolver"]["state"], "proposed")

    def test_try_with_always_raising_except_preserves_verified_fallthrough(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def trusted(request):\n"
            "    try:\n"
            "        request.state.customer_id = verify_signed_customer_identity(\n"
            "            request.headers.get('X-Customer-ID'))\n"
            "    except ValueError:\n"
            "        raise\n"
            "    return uuid.UUID(request.state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(trusted)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(
            service["resolver"]["expression"],
            "request.state.customer_id",
        )

    def test_try_with_fallthrough_untrusted_except_remains_unresolved(self):
        service = self._discover_python_source(
            "import uuid\n"
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def poisoned(request):\n"
            "    try:\n"
            "        request.state.customer_id = verify_signed_customer_identity(\n"
            "            request.headers.get('X-Customer-ID'))\n"
            "    except ValueError:\n"
            "        request.state.customer_id = load_untrusted_identity()\n"
            "    return uuid.UUID(request.state.customer_id)\n"
            "@app.get('/orders', dependencies=[Depends(poisoned)])\n"
            "def orders(): return {}\n"
        )

        self.assertEqual(service["resolver"]["state"], "unresolved")

    def test_bound_dunder_mutations_fail_closed_for_aliases_and_stale_provenance(self):
        cases = {
            "stale-local": (
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    customer_id = auth.customer_id\n"
                "    auth.__setattr__('customer_id', load_untrusted_identity())\n"
                "    return uuid.UUID(customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(customer=Depends(resolve_customer)): return {}\n"
            ),
            "alias-dynamic": (
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    claims = auth\n"
                "    field = choose_customer_feature_or_tenant_field()\n"
                "    claims.__setattr__(field, load_untrusted_identity())\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(customer=Depends(resolve_customer)): return {}\n"
            ),
            "auth-delete": (
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(auth=Depends(verify_jwt)):\n"
                "    auth.__delattr__('customer_id')\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(customer=Depends(resolve_customer)): return {}\n"
            ),
            "request-state-delete": (
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(request):\n"
                "    request.state.customer_id = verify_signed_customer_identity(\n"
                "        request.headers.get('X-Customer-ID'))\n"
                "    request.state.__delattr__('customer_id')\n"
                "    return uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders', dependencies=[Depends(resolve_customer)])\n"
                "def orders(): return {}\n"
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                service = self._discover_python_source(source)

                self.assertEqual(service["resolver"]["state"], "unresolved")

    def test_bound_dunder_verified_setter_and_unrelated_receiver_controls(self):
        cases = {
            "verified-request-state": (
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(request):\n"
                "    request.state.__setattr__(\n"
                "        'customer_id', verify_signed_customer_identity(\n"
                "            request.headers.get('X-Customer-ID')))\n"
                "    return uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders', dependencies=[Depends(resolve_customer)])\n"
                "def orders(): return {}\n"
            ),
            "unrelated-receiver": (
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def resolve_customer(profile, auth=Depends(verify_jwt)):\n"
                "    field = choose_customer_feature_or_tenant_field()\n"
                "    profile.__setattr__(field, load_untrusted_identity())\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(customer=Depends(resolve_customer)): return {}\n"
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                service = self._discover_python_source(source)

                self.assertEqual(service["resolver"]["state"], "proposed")

    def test_finally_overwrite_reaches_return_break_and_continue_paths(self):
        terminal_blocks = {
            "normal": (
                "    try:\n"
                "        metrics.started = True\n"
                "    finally:\n"
                "        request.state.customer_id = load_untrusted_identity()\n"
                "    return uuid.UUID(request.state.customer_id)\n"
            ),
            "return": (
                "    try:\n"
                "        return uuid.UUID(request.state.customer_id)\n"
                "    finally:\n"
                "        request.state.customer_id = load_untrusted_identity()\n"
            ),
            "break": (
                "    for item in items:\n"
                "        try:\n"
                "            break\n"
                "        finally:\n"
                "            request.state.customer_id = load_untrusted_identity()\n"
                "    return uuid.UUID(request.state.customer_id)\n"
            ),
            "continue": (
                "    for item in items:\n"
                "        try:\n"
                "            continue\n"
                "        finally:\n"
                "            request.state.customer_id = load_untrusted_identity()\n"
                "    return uuid.UUID(request.state.customer_id)\n"
            ),
        }
        for name, terminal_block in terminal_blocks.items():
            with self.subTest(name=name):
                service = self._discover_python_source(
                    "import uuid\n"
                    "from fastapi import Depends, FastAPI\n"
                    "app = FastAPI()\n"
                    "def resolve_customer(request, items=()):\n"
                    "    request.state.customer_id = verify_signed_customer_identity(\n"
                    "        request.headers.get('X-Customer-ID'))\n"
                    f"{terminal_block}"
                    "@app.get('/orders', dependencies=[Depends(resolve_customer)])\n"
                    "def orders(): return {}\n"
                )

                self.assertEqual(service["resolver"]["state"], "unresolved")

    def test_finally_preserves_verified_paths_and_can_override_raise(self):
        cases = {
            "return-cleanup": (
                "    try:\n"
                "        return uuid.UUID(request.state.customer_id)\n"
                "    finally:\n"
                "        metrics.closed = True\n"
            ),
            "normal-cleanup": (
                "    try:\n"
                "        metrics.started = True\n"
                "    finally:\n"
                "        metrics.closed = True\n"
                "    return uuid.UUID(request.state.customer_id)\n"
            ),
            "raise-overridden-by-return": (
                "    try:\n"
                "        raise RuntimeError('retry')\n"
                "    finally:\n"
                "        return uuid.UUID(request.state.customer_id)\n"
            ),
        }
        for name, control_flow in cases.items():
            with self.subTest(name=name):
                service = self._discover_python_source(
                    "import uuid\n"
                    "from fastapi import Depends, FastAPI\n"
                    "app = FastAPI()\n"
                    "def resolve_customer(request):\n"
                    "    request.state.customer_id = verify_signed_customer_identity(\n"
                    "        request.headers.get('X-Customer-ID'))\n"
                    f"{control_flow}"
                    "@app.get('/orders', dependencies=[Depends(resolve_customer)])\n"
                    "def orders(): return {}\n"
                )

                self.assertEqual(service["resolver"]["state"], "proposed")

    def test_fastapi_and_starlette_mounts_compose_child_route_evidence(self):
        parent_declarations = {
            "fastapi-positional": (
                "parent = FastAPI()\n"
                "parent.add_middleware(AttributionMiddleware)\n"
                "parent.mount('/api', child)\n"
            ),
            "starlette-keyword": (
                "parent = Starlette()\n"
                "parent.add_middleware(AttributionMiddleware)\n"
                "parent.mount(path='/api', app=child)\n"
            ),
        }
        for name, parent_declaration in parent_declarations.items():
            with self.subTest(name=name):
                service = self._discover_python_source(
                    "import uuid\n"
                    "from fastapi import Depends, FastAPI\n"
                    "from starlette.applications import Starlette\n"
                    "def resolve_customer(*, auth=Depends(verify_jwt)):\n"
                    "    return uuid.UUID(auth.customer_id)\n"
                    "child = FastAPI(dependencies=[\n"
                    "    Depends(require_auth), Depends(resolve_customer)])\n"
                    "@child.get('/orders')\n"
                    "def orders(): return {}\n"
                    f"{parent_declaration}"
                )

                self.assertEqual(service["resolver"]["state"], "proposed")
                self.assertEqual(
                    [
                        (
                            route["path_template"],
                            route["auth_scope"],
                            route["confidence"],
                        )
                        for route in service["routes"]
                    ],
                    [("/api/orders", "global", "high")],
                )
                self.assertEqual(
                    [
                        (mount["target"], mount["prefix"], mount["confidence"])
                        for mount in service["mounts"]
                    ],
                    [("child", "/api", "high")],
                )
                self.assertNotIn(
                    "middleware_missing",
                    {finding["code"] for finding in service["findings"]},
                )

    def test_dynamic_and_unsupported_python_mounts_are_explicitly_unresolved(self):
        cases = {
            "dynamic-prefix": (
                "child = FastAPI()\n"
                "@child.get('/orders')\n"
                "def orders(): return {}\n"
                "prefix = load_mount_prefix()\n"
                "parent.mount(prefix, child)\n",
                True,
            ),
            "unsupported-target": (
                "@parent.get('/health')\n"
                "def health(auth=Depends(verify_jwt)):\n"
                "    return uuid.UUID(auth.customer_id)\n"
                "parent.mount('/external', create_external_asgi_app())\n",
                False,
            ),
        }
        for name, (registration, has_child_route) in cases.items():
            with self.subTest(name=name):
                service = self._discover_python_source(
                    "import uuid\n"
                    "from fastapi import Depends, FastAPI\n"
                    "parent = FastAPI()\n"
                    "parent.add_middleware(AttributionMiddleware)\n"
                    f"{registration}"
                )

                self.assertEqual(
                    [(mount["prefix"], mount["confidence"]) for mount in service["mounts"]],
                    [(None, "low")],
                )
                self.assertIn(
                    ("mount_unresolved", "high"),
                    {
                        (finding["code"], finding["severity"])
                        for finding in service["findings"]
                    },
                )
                if has_child_route:
                    self.assertEqual(
                        [
                            (route["path_template"], route["confidence"])
                            for route in service["routes"]
                        ],
                        [(None, "low")],
                    )

    def test_fastapi_api_route_methods_are_expanded_or_explicitly_unknown(self):
        service = self._discover_python_source(
            "from fastapi import APIRouter, FastAPI\n"
            "app = FastAPI()\n"
            "app.add_middleware(AttributionMiddleware)\n"
            "router = APIRouter(prefix='/v1')\n"
            "app.include_router(router)\n"
            "@app.api_route(path='/multi', methods=['GET', 'POST'])\n"
            "def multi(): return {}\n"
            "@router.api_route(path='/single', methods=('PATCH',))\n"
            "def single(): return {}\n"
            "dynamic_methods = load_methods()\n"
            "@app.api_route(path='/dynamic', methods=dynamic_methods)\n"
            "def dynamic(): return {}\n"
            "@app.api_route(path='/absent')\n"
            "def absent(): return {}\n"
            "@app.api_route(path='/unsupported', methods=['TRACE'])\n"
            "def unsupported(): return {}\n"
        )

        self.assertEqual(
            {
                (route["method"], route["path_template"], route["confidence"])
                for route in service["routes"]
            },
            {
                ("GET", "/multi", "high"),
                ("POST", "/multi", "high"),
                ("PATCH", "/v1/single", "high"),
                (None, "/dynamic", "high"),
                (None, "/absent", "high"),
                (None, "/unsupported", "high"),
            },
        )

    def test_resolver_accepts_verified_tenant_crosswalk(self):
        service = self._discover_python_source(
            "from fastapi import Depends, FastAPI\n"
            "app = FastAPI()\n"
            "def resolve_tenant_key(auth=Depends(verify_jwt)):\n"
            "    return lookup_tenant(auth.tenant_key)\n"
            "@app.get('/orders')\n"
            "def orders(tenant=Depends(resolve_tenant_key)): return {'tenant': tenant}\n"
        )

        self.assertEqual(service["resolver"]["state"], "proposed")
        self.assertEqual(
            service["resolver"]["identity_kind"],
            "external_key_crosswalk",
        )
        self.assertEqual(service["resolver"]["expression"], "auth.tenant_key")

    def test_resolver_conservatively_merges_raw_and_trusted_python_branches(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def branch(request, use_claims, claims=Depends(verify_jwt)):\n"
                "    if use_claims:\n"
                "        customer = request.headers.get('X-Customer-ID')\n"
                "    else:\n"
                "        customer = claims.customer_id\n"
                "    uuid.UUID(customer)\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["resolver"]["state"], "unresolved")
            self.assertTrue(
                any(
                    item["code"] == "raw_identity_header"
                    and item["evidence"]["line"] == 6
                    for item in service["findings"]
                )
            )

    def test_auth_and_attribution_middleware_require_attached_runtime_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "other = FastAPI()\n"
                "# app.add_middleware(AuthenticationMiddleware)\n"
                "dead = 'app.add_middleware(AttributionMiddleware)'\n"
                "if False:\n"
                "    app.add_middleware(AuthenticationMiddleware)\n"
                "    app.add_middleware(AttributionMiddleware)\n"
                "other.add_middleware(AuthenticationMiddleware)\n"
                "@app.get('/live')\n"
                "def live(): return {}\n"
                "@other.get('/other')\n"
                "def other_route(): return {}\n"
                "@other.middleware('http')\n"
                "async def attribution_context(request, call_next):\n"
                "    return await call_next(request)\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            scopes = {route["path_template"]: route["auth_scope"] for route in service["routes"]}
            self.assertEqual(scopes, {"/live": "unknown", "/other": "global"})
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 1)

            (repo / "app.js").write_text(
                "const app = express();\n"
                "const dead = 'app.use(AttributionMiddleware)';\n"
                "// app.use(authMiddleware);\n"
                "app.get('/js', handler);\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
            (repo / "requirements.txt").unlink()
            (repo / "app.py").unlink()
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            self.assertFalse(result["services"][0]["middleware_detected"])
            self.assertEqual(result["services"][0]["routes"][0]["auth_scope"], "unknown")

            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 0)

            (repo / "go.mod").write_text(
                "module example/go\n\nrequire github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                "package main\n"
                'import "github.com/go-chi/chi/v5"\n'
                "func main() {\n"
                "  r := chi.NewRouter()\n"
                "  dead := \"r.Use(AttributionMiddleware)\"\n"
                "  // r.Use(AuthMiddleware)\n"
                "  r.Get(\"/go\", handler)\n"
                "  _ = dead\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "app.js").unlink()
            (repo / "package.json").unlink()
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            self.assertFalse(result["services"][0]["middleware_detected"])
            self.assertEqual(result["services"][0]["routes"][0]["auth_scope"], "unknown")

    def test_js_comments_are_lexically_ignored_and_multiline_calls_keep_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("adversarial/js-comments-multiline", repo)
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertTrue(service["middleware_detected"])
            self.assertEqual(
                [
                    (route["method"], route["path_template"], route["evidence"])
                    for route in service["routes"]
                ],
                [("GET", "/literal//path", {"file": "server.ts", "line": 17})],
            )
            self.assertEqual(
                result["discovery_projection"]["routes_statically_covered"],
                1,
            )

    def test_go_multiline_routes_and_middleware_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("adversarial/go-multiline", repo)
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertTrue(service["middleware_detected"])
            self.assertEqual(
                [
                    (route["framework"], route["method"], route["path_template"], route["evidence"])
                    for route in service["routes"]
                ],
                [("chi", "GET", "/multiline", {"file": "main.go", "line": 10})],
            )
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 1)

    def test_async_verification_requires_propagation_inside_boundary_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("adversarial/async-boundary", repo)
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            hops = self._load(output)["services"][0]["async_hops"]
            self.assertEqual(
                [
                    (hop["kind"], hop["propagation"], hop["evidence"])
                    for hop in hops
                ],
                [
                    ("publish", "missing", {"file": "server.ts", "line": 8}),
                    ("publish", "verified", {"file": "server.ts", "line": 12}),
                ],
            )

    def test_excludes_copy_and_sdk_trees_without_hiding_app_services(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("adversarial/path-filtering", repo)
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            services = self._load(output)["services"]
            self.assertEqual(
                [service["service_path"] for service in services],
                ["services/app-api", "services/sdk-gateway"],
            )
            self.assertEqual(
                {
                    route["path_template"]
                    for service in services
                    for route in service["routes"]
                },
                {"/active", "/gateway"},
            )

    def test_flask_python_is_unknown_and_never_classified_as_starlette(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("adversarial/flask-unsupported", repo)
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["frameworks"], ["flask"])
            self.assertEqual(service["ingress_state"], "unknown")
            self.assertEqual(service["routes"], [])
            self.assertFalse(service["middleware_detected"])

    def test_python_import_alias_mounts_compose_prefixes_and_parent_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("adversarial/python-alias-mount", repo)
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertEqual(
                [route["path_template"] for route in service["routes"]],
                ["/v1/api/users", "/v2/admin/stats"],
            )
            self.assertTrue(service["middleware_detected"])
            self.assertEqual(
                result["discovery_projection"],
                {
                    "routes_discovered": 2,
                    "routes_statically_covered": 2,
                    "routes_unknown": 0,
                },
            )
            self.assertEqual(
                [(mount["target"], mount["prefix"], mount["confidence"]) for mount in service["mounts"]],
                [
                    ("admin_routes.router", "/v2", "high"),
                    ("api_router", "/v1", "high"),
                ],
            )

    def test_source_revision_is_clean_only_for_head_clean_relevant_sources(self):
        def initialize_git(repo: Path) -> str:
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "scanner@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Scanner Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "app.py", "requirements.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
            return subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean_repo = root / "clean"
            clean_repo.mkdir()
            _copy_fixture("adversarial/source-revision", clean_repo)
            commit = initialize_git(clean_repo)
            output = clean_repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"

            first = self._discover(clean_repo, output)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_bytes = output.read_bytes()
            revision = self._load(output)["source_revision"]
            self.assertEqual(revision, {"state": "clean", "git_commit": commit})

            second = self._discover(clean_repo, output)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(output.read_bytes(), first_bytes)
            self.assertEqual(self._load(output)["source_revision"], revision)

            (clean_repo / "app.py").write_text(
                (clean_repo / "app.py").read_text(encoding="utf-8") + "\n# modified\n",
                encoding="utf-8",
            )
            modified = self._discover(clean_repo, output)
            self.assertEqual(modified.returncode, 0, modified.stderr)
            self.assertEqual(
                self._load(output)["source_revision"],
                {"state": "dirty", "git_commit": None},
            )

            untracked_repo = root / "untracked"
            untracked_repo.mkdir()
            _copy_fixture("adversarial/source-revision", untracked_repo)
            initialize_git(untracked_repo)
            (untracked_repo / "extra.py").write_text("value = 1\n", encoding="utf-8")
            untracked_output = root / "untracked-map.json"
            untracked = self._discover(untracked_repo, untracked_output)
            self.assertEqual(untracked.returncode, 0, untracked.stderr)
            self.assertEqual(
                self._load(untracked_output)["source_revision"],
                {"state": "dirty", "git_commit": None},
            )

            plain_repo = root / "plain"
            plain_repo.mkdir()
            _copy_fixture("adversarial/source-revision", plain_repo)
            plain_output = root / "plain-map.json"
            plain = self._discover(plain_repo, plain_output)
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(
                self._load(plain_output)["source_revision"],
                {"state": "unversioned", "git_commit": None},
            )

            malformed = self._load(output)
            malformed["source_revision"] = {"state": "clean", "git_commit": None}
            with self.assertRaises(SCAN_MODULE.DiscoveryError):
                SCAN_MODULE.validate_map(malformed)

    def test_next_export_const_and_unresolved_cross_file_mount_are_honest(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text('{"dependencies":{"next":"1","express":"1"}}', encoding="utf-8")
            next_route = repo / "app" / "api" / "items" / "route.ts"
            next_route.parent.mkdir(parents=True)
            next_route.write_text(
                "const handler = async () => Response.json({});\nexport const GET = handler;\n",
                encoding="utf-8",
            )
            (repo / "server.ts").write_text(
                "const app = express();\napp.use('/v1', router);\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "const router = express.Router();\nrouter.get('/users', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            by_framework = {(route["framework"], route["path_template"]): route for route in service["routes"]}
            self.assertIn(("nextjs-app-router", "/api/items"), by_framework)
            unresolved = next(route for route in service["routes"] if route["framework"] == "express")
            self.assertIsNone(unresolved["path_template"])
            self.assertEqual(unresolved["confidence"], "low")
            mount = service["mounts"][0]
            self.assertIsNone(mount["prefix"])
            self.assertEqual(mount["confidence"], "low")

    def test_excludes_common_generated_and_test_directory_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
            (repo / "server.ts").write_text("const app=express(); app.get('/live', h);\n", encoding="utf-8")
            for dirname in ("__tests__", "generated-src", "test-utils", "tests-integration", "__generated__", "generated_sources"):
                target = repo / dirname
                target.mkdir()
                (target / "routes.ts").write_text("const app=express(); app.get('/excluded', h);\n", encoding="utf-8")
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            paths = [route["path_template"] for route in self._load(output)["services"][0]["routes"]]
            self.assertEqual(paths, ["/live"])

    def test_zero_route_and_unsupported_http_services_are_unknown_not_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            unsupported = repo / "django-api"
            unsupported.mkdir(parents=True)
            (unsupported / "requirements.txt").write_text("django\n", encoding="utf-8")
            (unsupported / "views.py").write_text("def health(request): return None\n", encoding="utf-8")
            zero = repo / "express-api"
            zero.mkdir()
            (zero / "package.json").write_text('{"dependencies":{"express":"1"}}', encoding="utf-8")
            (zero / "server.ts").write_text("const app = express();\n", encoding="utf-8")
            fastify = repo / "fastify-api"
            fastify.mkdir()
            (fastify / "package.json").write_text('{"dependencies":{"fastify":"1"}}', encoding="utf-8")
            (fastify / "server.ts").write_text(
                "const app = Fastify();\napp.get('/must-stay-unknown', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            states = {service["service_path"]: service["ingress_state"] for service in self._load(output)["services"]}
            self.assertEqual(
                states,
                {
                    "django-api": "unknown",
                    "express-api": "unknown",
                    "fastify-api": "unknown",
                },
            )
            fastify_service = next(
                service
                for service in self._load(output)["services"]
                if service["service_path"] == "fastify-api"
            )
            self.assertEqual(fastify_service["routes"], [])

    def test_json_schema_rejects_malformed_maps_and_drift_baselines_exit_two(self):
        malformed = {
            "schema_version": "1.0",
            "generated_at": FIXED_TIME,
            "source_fingerprint": {"algorithm": "sha256", "value": "0" * 64},
            "discovery_projection": {"routes_discovered": "one", "routes_statically_covered": 0, "routes_unknown": 1},
            "services": [],
            "findings": [],
        }
        with self.assertRaises(SCAN_MODULE.DiscoveryError):
            SCAN_MODULE.validate_map(malformed)

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text("from fastapi import FastAPI\napp=FastAPI()\n", encoding="utf-8")
            baseline = repo / "baseline.json"
            baseline.write_text(json.dumps(malformed), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(run.returncode, 2)
            self.assertIn("schema", run.stderr.lower())

    def test_drift_compares_all_contract_blocks_and_reports_duplicates(self):
        route = {
            "route_id": "route-1", "framework": "fastapi", "method": "GET", "path_template": "/x",
            "confidence": "high", "auth_scope": "handler", "evidence": {"file": "app.py", "line": 1},
            "feature_proposal": {"slug": "x", "confidence": "high", "requires_engineer_signoff": True},
        }
        service = {
            "service_path": ".", "frameworks": ["fastapi"], "ingress_state": "http-ingress",
            "middleware_detected": True, "routes": [route], "mounts": [],
            "resolver": {"state": "proposed", "identity_kind": "moolabs_uuid", "expression": "claims.customer_id",
                         "template": "validate", "evidence": {"file": "app.py", "line": 2}},
            "async_hops": [{"kind": "publish", "propagation": "verified", "evidence": {"file": "app.py", "line": 3}}],
            "findings": [],
        }
        baseline = {
            "schema_version": "1.0", "generated_at": FIXED_TIME,
            "source_fingerprint": {"algorithm": "sha256", "value": "1" * 64},
            "discovery_projection": {"routes_discovered": 1, "routes_statically_covered": 1, "routes_unknown": 0},
            "services": [service], "findings": [],
        }
        current = json.loads(json.dumps(baseline))
        current["source_fingerprint"]["value"] = "2" * 64
        current["discovery_projection"]["routes_statically_covered"] = 0
        current["services"][0]["resolver"]["expression"] = "auth.account_id"
        current["services"][0]["routes"][0]["auth_scope"] = "unknown"
        current["services"][0]["async_hops"][0]["propagation"] = "missing"
        current["services"][0]["findings"] = [
            {"code": "middleware_missing", "severity": "warning", "message": "missing", "evidence": None}
        ]
        current["services"][0]["routes"].append(json.loads(json.dumps(current["services"][0]["routes"][0])))

        codes = {finding["code"] for finding in DRIFT_MODULE.compare(baseline, current)}
        self.assertTrue({
            "source_fingerprint_changed", "resolver_changed", "auth_changed", "async_propagation_changed",
            "findings_changed", "projected_coverage_changed", "duplicate_route_id",
        }.issubset(codes))

    def test_resolves_router_prefixes_and_records_trusted_and_async_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("advanced", repo)
            output = repo / "map.yaml"
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            python = next(service for service in result["services"] if service["service_path"] == "python")
            self.assertIn("/v1/api/users", [route["path_template"] for route in python["routes"]])
            self.assertEqual(python["resolver"]["state"], "unresolved")
            self.assertIsNone(python["resolver"]["identity_kind"])
            self.assertIn("verified", [hop["propagation"] for hop in python["async_hops"]])
            web = next(service for service in result["services"] if service["service_path"] == "web")
            self.assertIn(("hono", "GET", "/v1/hono"),
                          {(route["framework"], route["method"], route["path_template"]) for route in web["routes"]})
            go = next(service for service in result["services"] if service["service_path"] == "go")
            net_http = next(route for route in go["routes"] if route["path_template"] == "/net")
            self.assertIsNone(net_http["method"])

    def test_drift_ignores_generated_time_and_reports_middleware_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("drift", repo)
            baseline = repo / "baseline.yaml"
            initial = self._discover(repo, baseline)
            self.assertEqual(initial.returncode, 0, initial.stderr)

            unchanged = subprocess.run([sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline),
                                        "--generated-at", "2099-01-01T00:00:00Z"], text=True, capture_output=True, check=False)
            self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
            self.assertEqual(self._load_text_json(unchanged.stdout)["findings"], [])

            app = repo / "service" / "app.py"
            app.write_text(app.read_text(encoding="utf-8").replace('@app.middleware("http")\n', ""), encoding="utf-8")
            removed = subprocess.run([sys.executable, str(DRIFT), "--repo", str(repo), "--baseline", str(baseline)],
                                      text=True, capture_output=True, check=False)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertIn("middleware_removed", removed.stdout)

    def test_reports_only_explicit_auth_scope_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("auth", repo)
            output = repo / "map.yaml"
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            scopes = {route["path_template"]: route.get("auth_scope") for route in routes}
            self.assertEqual(scopes, {"/global": "global", "/handler": "handler", "/router": "router", "/unknown": "global"})

    def test_reports_feature_slug_collisions_for_engineer_review(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _copy_fixture("collision", repo)
            output = repo / "map.yaml"
            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            findings = self._load(output)["services"][0]["findings"]
            self.assertTrue(any(finding["code"] == "feature_slug_collision" for finding in findings))

    def test_repo_scan_manifest_inputs_change_fingerprint_and_dirty_revision(self):
        manifest_contents = {
            "pyproject.toml": "[project]\ndependencies = [\"fastapi>=1\"]\n",
            "requirements.txt": "fastapi\n",
            "setup.py": "from setuptools import setup\nsetup()\n",
            "setup.cfg": "[metadata]\nname = scanner-fixture\n",
            "Pipfile": "[packages]\nfastapi = \"*\"\n",
            "package.json": '{"dependencies":{"express":"1"}}\n',
            "tsconfig.json": '{"compilerOptions":{"strict":true}}\n',
            "go.mod": "module example.test/scanner\n\nrequire github.com/go-chi/chi/v5 v5.0.0\n",
            "go.sum": "github.com/go-chi/chi/v5 v5.0.0 h1:fixture\n",
            "Cargo.toml": "[package]\nname = \"scanner-fixture\"\nversion = \"0.1.0\"\n",
            "pom.xml": "<project/>\n",
            "build.gradle": "plugins {}\n",
            "build.gradle.kts": "plugins {}\n",
        }
        advertised = {
            name
            for names in SCAN_MODULE._load_repo_scan().MANIFESTS.values()
            for name in names
        }
        self.assertEqual(set(manifest_contents), advertised)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            for name, content in manifest_contents.items():
                (repo / name).write_text(content, encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/x')\ndef x(): return {}\n",
                encoding="utf-8",
            )
            (repo / "server.ts").write_text(
                "const app = express();\napp.get('/js', handler);\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                'package main\nfunc main() { r := chi.NewRouter(); r.Get("/go", handler) }\n',
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                 "user.email=test@example.com", "commit", "-qm", "fixture"],
                check=True,
            )
            output = root / "map.json"
            baseline_run = self._discover(repo, output)
            self.assertEqual(baseline_run.returncode, 0, baseline_run.stderr)
            baseline = self._load(output)
            self.assertEqual(baseline["source_revision"]["state"], "clean")

            for name, content in manifest_contents.items():
                with self.subTest(manifest=name):
                    (repo / name).write_text(content + "\n", encoding="utf-8")
                    run = self._discover(repo, output)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    changed = self._load(output)
                    self.assertNotEqual(changed["source_fingerprint"], baseline["source_fingerprint"])
                    self.assertEqual(
                        changed["source_revision"],
                        {"state": "dirty", "git_commit": None},
                    )
                    (repo / name).write_text(content, encoding="utf-8")

    def test_root_repo_scan_manifest_dirties_nested_service_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            service = repo / "services" / "api"
            service.mkdir(parents=True)
            root_manifest = repo / "package.json"
            root_manifest.write_text('{"private":true,"workspaces":["services/*"]}\n', encoding="utf-8")
            (service / "package.json").write_text(
                '{"dependencies":{"express":"1"}}\n', encoding="utf-8"
            )
            (service / "server.ts").write_text(
                "const app = express();\napp.get('/x', handler);\n", encoding="utf-8"
            )
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                 "user.email=test@example.com", "commit", "-qm", "fixture"],
                check=True,
            )
            output = root / "map.json"
            baseline_run = self._discover(repo, output)
            self.assertEqual(baseline_run.returncode, 0, baseline_run.stderr)
            baseline = self._load(output)

            root_manifest.write_text(
                '{"private":true,"workspaces":["services/*"],"description":"changed"}\n',
                encoding="utf-8",
            )
            changed_run = self._discover(repo, output)
            self.assertEqual(changed_run.returncode, 0, changed_run.stderr)
            changed = self._load(output)
            self.assertNotEqual(changed["source_fingerprint"], baseline["source_fingerprint"])
            self.assertEqual(changed["source_revision"], {"state": "dirty", "git_commit": None})

    def test_js_and_go_resolver_provenance_is_explicitly_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            web = repo / "services" / "web"
            web.mkdir(parents=True)
            (web / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (web / "server.ts").write_text(
                "const app = express();\n"
                "app.get('/x', requireAuth, (req) => validateUuid(req.auth.customerId));\n",
                encoding="utf-8",
            )
            go = repo / "services" / "go"
            go.mkdir(parents=True)
            (go / "go.mod").write_text(
                "module example.test/go\n\nrequire github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (go / "main.go").write_text(
                'package main\nfunc main() { r := chi.NewRouter(); r.Get("/x", handler) }\n',
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            services = self._load(output)["services"]
            self.assertEqual([service["resolver"]["state"] for service in services], ["unresolved", "unresolved"])
            for service in services:
                self.assertTrue(any(
                    finding["code"] == "resolver_provenance_unsupported"
                    for finding in service["findings"]
                ))
            self.assertIn(
                "Python-only",
                (SKILL / "SKILL.md").read_text(encoding="utf-8"),
            )

    def test_fastapi_auth_covers_handlers_annotated_add_route_and_router_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from typing import Annotated\n"
                "from fastapi import APIRouter, Depends, FastAPI\n"
                "app = FastAPI()\n"
                "router = APIRouter()\n"
                "def added(user=Depends(require_auth)): return {}\n"
                "app.add_api_route('/added', added, methods=['GET'])\n"
                "app.include_router(router, prefix='/v1', dependencies=[Depends(require_auth)])\n"
                "@router.get('/router')\n"
                "def nested(): return {}\n"
                "@app.get('/handler')\n"
                "def handler(user=Depends(require_auth)): return {}\n"
                "@app.get('/annotated')\n"
                "def annotated(user: Annotated[object, Depends(require_auth)]): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            scopes = {
                route["path_template"]: route["auth_scope"]
                for route in self._load(output)["services"][0]["routes"]
            }
            self.assertEqual(
                scopes,
                {
                    "/added": "handler",
                    "/annotated": "handler",
                    "/handler": "handler",
                    "/v1/router": "router",
                },
            )

    def test_nested_express_and_chi_mounts_compose_or_remain_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            web = repo / "services" / "web"
            web.mkdir(parents=True)
            (web / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (web / "server.ts").write_text(
                "const app = express();\n"
                "const api = express.Router();\n"
                "const admin = express.Router();\n"
                "const dynamic = express.Router();\n"
                "app.use('/v1', api);\n"
                "api.use('/admin', admin);\n"
                "app.use(prefix, dynamic);\n"
                "admin.get('/stats', handler);\n"
                "dynamic.get('/ambiguous', handler);\n",
                encoding="utf-8",
            )
            go = repo / "services" / "go"
            go.mkdir(parents=True)
            (go / "go.mod").write_text(
                "module example.test/go\n\nrequire github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (go / "main.go").write_text(
                "package main\n"
                'import "github.com/go-chi/chi/v5"\n'
                "func main() {\n"
                "  root := chi.NewRouter()\n"
                "  api := chi.NewRouter()\n"
                "  admin := chi.NewRouter()\n"
                "  dynamic := chi.NewRouter()\n"
                "  root.Mount(\"/v1\", api)\n"
                "  api.Mount(\"/admin\", admin)\n"
                "  root.Mount(prefix, dynamic)\n"
                "  admin.Get(\"/stats\", handler)\n"
                "  dynamic.Get(\"/ambiguous\", handler)\n"
                "  root.Route(\"/inline\", func(r chi.Router) {\n"
                "    r.Route(\"/nested\", func(r chi.Router) {\n"
                "      r.Get(\"/ok\", handler)\n"
                "    })\n"
                "  })\n"
                "  root.Route(dynamicPrefix, func(r chi.Router) {\n"
                "    r.Get(\"/inline-ambiguous\", handler)\n"
                "  })\n"
                "}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            services = {service["service_path"]: service for service in self._load(output)["services"]}
            for service_path in ("services/web", "services/go"):
                paths = [route["path_template"] for route in services[service_path]["routes"]]
                self.assertIn("/v1/admin/stats", paths)
                self.assertIn(None, paths)
            go_paths = [route["path_template"] for route in services["services/go"]["routes"]]
            self.assertIn("/inline/nested/ok", go_paths)
            self.assertNotIn("/inline-ambiguous", go_paths)

    def test_arbitrary_send_calls_are_not_verified_async_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (repo / "server.ts").write_text(
                "const app = express();\n"
                "app.get('/x', handler);\n"
                "res.send(injectThreadId({ thread_id }));\n"
                "mailer.send(injectThreadId({ thread_id }));\n"
                "send(injectThreadId({ thread_id }));\n"
                "producer.send(injectThreadId({ thread_id }));\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["async_hops"],
                [{
                    "kind": "send",
                    "propagation": "verified",
                    "evidence": {"file": "server.ts", "line": 6},
                }],
            )

    def test_async_propagation_requires_a_concrete_operation_call(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            python = repo / "services" / "python"
            python.mkdir(parents=True)
            (python / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (python / "app.py").write_text(
                "producer.send(payload, note='do not inject thread_id')\n"
                "producer.send({'thread_id': thread_id, 'value': inject_thread_id_payload})\n"
                "producer.send(inject_thread_id({'thread_id': thread_id}))\n",
                encoding="utf-8",
            )
            typescript = repo / "services" / "typescript"
            typescript.mkdir(parents=True)
            (typescript / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (typescript / "server.ts").write_text(
                "producer.send(payload, { note: 'do not inject thread_id' });\n"
                "producer.send({ thread_id, value: injectThreadIdPayload });\n"
                "producer.send(injectThreadId({ thread_id }));\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            services = {
                service["service_path"]: service
                for service in self._load(output)["services"]
            }
            for service_path in ("services/python", "services/typescript"):
                self.assertEqual(
                    [hop["propagation"] for hop in services[service_path]["async_hops"]],
                    ["missing", "missing", "verified"],
                )

    def test_raw_header_is_downgraded_only_for_verified_context_binding_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def trusted(request):\n"
                "    raw_customer = request.headers.get('X-Customer-ID')\n"
                "    verified_customer = verify_signed_customer_identity(raw_customer)\n"
                "    request.state.customer_id = verified_customer\n"
                "def poisoned(request):\n"
                "    raw_tenant = request.headers.get('X-Tenant-ID')\n"
                "    verify_signed_customer_identity(raw_tenant)\n"
                "    request.state.tenant_id = raw_tenant\n"
                "def dead_chain(request):\n"
                "    raw_account = request.headers.get('X-Moolabs-Customer')\n"
                "    if False:\n"
                "        verified_account = verify_signed_customer_identity(raw_account)\n"
                "        request.state.account_id = verified_account\n"
                "@app.get('/x')\n"
                "def x(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            findings = self._load(output)["services"][0]["findings"]
            observed = [
                (item["code"], item["severity"], item["evidence"]["line"])
                for item in findings
                if item["evidence"] is not None
            ]
            self.assertIn(("verified_identity_header", "info", 4), observed)
            self.assertIn(("raw_identity_header", "high", 8), observed)
            self.assertIn(("raw_identity_header", "high", 12), observed)

    def test_js_route_receiver_requires_framework_construction_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (repo / "server.ts").write_text(
                "const app = new Map();\n"
                "app.get('/cache-key');\n"
                "const router = { get() {} };\n"
                "router.get('/also-not-a-route');\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["routes"], [])
            self.assertFalse(
                any(item["code"] == "middleware_missing" for item in service["findings"])
            )

    def test_split_file_chi_middleware_coverage_is_unresolved_not_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example.test/go\n\n"
                "require github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                "package main\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func main() {\n"
                "  r := chi.NewRouter()\n"
                "  r.Use(AttributionMiddleware)\n"
                "  register(r)\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "routes.go").write_text(
                "package main\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func register(r chi.Router) { r.Get(\"/orders\", handler) }\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            codes = {item["code"] for item in service["findings"]}
            self.assertNotIn("middleware_missing", codes)
            self.assertNotIn("middleware_coverage_unresolved", codes)
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 1)
            self.assertEqual(result["discovery_projection"]["routes_unknown"], 0)

    def test_chi_cross_file_call_discovery_ignores_string_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example.test/go\n\n"
                "require github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                "package main\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func main() {\n"
                "  r := chi.NewRouter()\n"
                "  r.Use(AttributionMiddleware)\n"
                "  dead := \"register(r)\"\n"
                "  _ = dead\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "routes.go").write_text(
                "package main\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func register(r chi.Router) { r.Get(\"/orders\", handler) }\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertTrue(
                any(item["code"] == "middleware_missing" for item in service["findings"])
            )
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 0)
            self.assertEqual(result["discovery_projection"]["routes_unknown"], 1)

    def test_unrelated_split_file_chi_router_stays_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example.test/go\n\n"
                "require github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "admin.go").write_text(
                "package main\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func admin() {\n"
                "  r := chi.NewRouter()\n"
                "  r.Use(AttributionMiddleware)\n"
                "  registerAdmin(r)\n"
                "}\n"
                "func registerAdmin(r chi.Router) { r.Get(\"/admin\", adminHandler) }\n",
                encoding="utf-8",
            )
            (repo / "public.go").write_text(
                "package main\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func public() {\n"
                "  r := chi.NewRouter()\n"
                "  r.Get(\"/public\", publicHandler)\n"
                "}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertTrue(
                any(item["code"] == "middleware_missing" for item in service["findings"])
            )
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 1)
            self.assertEqual(result["discovery_projection"]["routes_unknown"], 1)

    def test_package_qualified_multi_arg_chi_call_covers_only_called_package(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example.test/go\n\n"
                "require github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                "package main\n"
                "import (\n"
                '  "github.com/go-chi/chi/v5"\n'
                '  routes "example.test/go/internal/routes"\n'
                ")\n"
                "func main() {\n"
                "  r := chi.NewRouter()\n"
                "  r.Use(AttributionMiddleware)\n"
                "  routes.Register(r, database)\n"
                "}\n",
                encoding="utf-8",
            )
            routes = repo / "internal" / "routes"
            routes.mkdir(parents=True)
            (routes / "routes.go").write_text(
                "package routes\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func Register(r chi.Router, db *Database) {\n"
                "  r.Get(\"/orders\", ordersHandler)\n"
                "}\n",
                encoding="utf-8",
            )
            unrelated = repo / "internal" / "unrelated"
            unrelated.mkdir(parents=True)
            (unrelated / "routes.go").write_text(
                "package unrelated\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func Register(r chi.Router, db *Database) {\n"
                "  r.Get(\"/unrelated\", unrelatedHandler)\n"
                "}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertTrue(
                any(item["code"] == "middleware_missing" for item in service["findings"])
            )
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 1)
            self.assertEqual(result["discovery_projection"]["routes_unknown"], 1)

    def test_chi_coverage_tracks_attributed_router_argument_position(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example.test/go\n\n"
                "require github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                "package main\n"
                "import \"github.com/go-chi/chi/v5\"\n"
                "func main() {\n"
                "  public := chi.NewRouter()\n"
                "  attributed := chi.NewRouter()\n"
                "  attributed.Use(AttributionMiddleware)\n"
                "  register(public, attributed)\n"
                "}\n"
                "func register(publicRouter chi.Router, privateRouter chi.Router) {\n"
                "  publicRouter.Get(\"/public\", publicHandler)\n"
                "  privateRouter.Get(\"/private\", privateHandler)\n"
                "}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertTrue(
                any(item["code"] == "middleware_missing" for item in service["findings"])
            )
            self.assertEqual(result["discovery_projection"]["routes_statically_covered"], 1)
            self.assertEqual(result["discovery_projection"]["routes_unknown"], 1)

    def test_imported_exported_express_receiver_preserves_route_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (repo / "app.ts").write_text(
                "export const app = express();\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "import { app } from './app';\n"
                "app.get('/orders', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                [
                    (
                        route["framework"],
                        route["method"],
                        route["path_template"],
                        route["evidence"],
                    )
                    for route in routes
                ],
                [("express", "GET", "/orders", {"file": "routes.ts", "line": 2})],
            )

    def test_express_and_hono_middleware_coverage_respects_registration_order(self):
        for dependency, constructor in (
            ("express", "express()"),
            ("hono", "new Hono()"),
        ):
            with self.subTest(dependency=dependency), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                (repo / "package.json").write_text(
                    json.dumps({"dependencies": {dependency: "1"}}),
                    encoding="utf-8",
                )
                (repo / "app.ts").write_text(
                    f"const app = {constructor};\n"
                    "app.get('/before', beforeHandler);\n"
                    "app.use(attributionMiddleware);\n"
                    "app.get('/after', afterHandler);\n",
                    encoding="utf-8",
                )
                output = repo.parent / f"{dependency}-map.json"

                run = self._discover(repo, output)
                self.assertEqual(run.returncode, 0, run.stderr)
                result = self._load(output)
                service = result["services"][0]
                self.assertEqual(
                    result["discovery_projection"],
                    {
                        "routes_discovered": 2,
                        "routes_statically_covered": 1,
                        "routes_unknown": 1,
                    },
                )
                self.assertTrue(
                    any(
                        finding["code"] == "middleware_missing"
                        for finding in service["findings"]
                    )
                )

    def test_js_ambiguous_extensionless_import_is_unresolved_and_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1","hono":"1"}}',
                encoding="utf-8",
            )
            (repo / "app.js").write_text(
                "export const app = express();\n",
                encoding="utf-8",
            )
            (repo / "app.ts").write_text(
                "export const app = new Hono();\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "import { app } from './app';\n"
                "app.get('/orders', handler);\n",
                encoding="utf-8",
            )
            first = Path(directory) / "seed-1.json"
            second = Path(directory) / "seed-6.json"

            runs = [
                subprocess.run(
                    [
                        sys.executable,
                        str(DISCOVER),
                        "--repo",
                        str(repo),
                        "--output",
                        str(output),
                        "--generated-at",
                        FIXED_TIME,
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    env={**os.environ, "PYTHONHASHSEED": seed},
                )
                for seed, output in (("1", first), ("6", second))
            ]

            for run in runs:
                self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            service = self._load(first)["services"][0]
            self.assertEqual(service["routes"], [])
            self.assertEqual(service["ingress_state"], "unknown")

    def test_imported_js_receiver_ignores_lexically_shadowed_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (repo / "app.ts").write_text(
                "export const app = express();\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "import { app } from './app';\n"
                "function parameterShadow(app) {\n"
                "  app.get('/parameter-shadow', fakeHandler);\n"
                "}\n"
                "function localShadow() {\n"
                "  const app = createTestDouble();\n"
                "  app.get('/local-shadow', fakeHandler);\n"
                "}\n"
                "app.get('/orders', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                [
                    (route["framework"], route["path_template"], route["evidence"])
                    for route in routes
                ],
                [("express", "/orders", {"file": "routes.ts", "line": 9})],
            )

    def test_default_import_resolves_declared_default_export_express_receiver(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (repo / "app.ts").write_text(
                "const app = express();\n"
                "export default app;\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "import api from './app';\n"
                "api.get('/orders', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                [
                    (
                        route["framework"],
                        route["method"],
                        route["path_template"],
                        route["evidence"],
                    )
                    for route in routes
                ],
                [("express", "GET", "/orders", {"file": "routes.ts", "line": 2})],
            )

    def test_default_import_resolves_inline_default_export_hono_receiver(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"hono":"1"}}', encoding="utf-8"
            )
            (repo / "app.ts").write_text(
                "export default new Hono();\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "import api from './app';\n"
                "api.get('/orders', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                [
                    (
                        route["framework"],
                        route["method"],
                        route["path_template"],
                        route["evidence"],
                    )
                    for route in routes
                ],
                [("hono", "GET", "/orders", {"file": "routes.ts", "line": 2})],
            )

    def test_combined_default_and_named_js_imports_resolve_both_receivers(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1","hono":"1"}}',
                encoding="utf-8",
            )
            (repo / "app.ts").write_text(
                "const app = express();\n"
                "export const admin = new Hono();\n"
                "export default app;\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "import api, { admin as adminApi } from './app';\n"
                "api.get('/orders', handler);\n"
                "adminApi.get('/admin', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                {
                    (
                        route["framework"],
                        route["method"],
                        route["path_template"],
                        route["evidence"]["line"],
                    )
                    for route in routes
                },
                {
                    ("express", "GET", "/orders", 2),
                    ("hono", "GET", "/admin", 3),
                },
            )

    def test_inline_verified_header_binding_is_trusted_resolver_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def trusted(request):\n"
                "    request.state.customer_id = verify_signed_customer_identity(\n"
                "        request.headers.get('X-Customer-ID')\n"
                "    )\n"
                "    uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders', dependencies=[Depends(trusted)])\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            header_findings = [
                item for item in service["findings"]
                if item["code"] in {"raw_identity_header", "verified_identity_header"}
            ]
            self.assertEqual(
                [(item["code"], item["evidence"]["line"]) for item in header_findings],
                [("verified_identity_header", 6)],
            )
            self.assertEqual(service["resolver"]["state"], "proposed")
            self.assertEqual(service["resolver"]["identity_kind"], "moolabs_uuid")
            self.assertEqual(
                service["resolver"]["expression"],
                "request.state.customer_id",
            )

    def test_verified_header_finding_revokes_an_overwritten_verified_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def poisoned(request):\n"
                "    raw_customer = request.headers.get('X-Customer-ID')\n"
                "    verified_customer = verify_signed_customer_identity(raw_customer)\n"
                "    verified_customer = load_untrusted_identity()\n"
                "    request.state.customer_id = verified_customer\n"
                "    uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            header_findings = [
                item for item in service["findings"]
                if item["code"] in {"raw_identity_header", "verified_identity_header"}
            ]
            self.assertEqual(
                [(item["code"], item["evidence"]["line"]) for item in header_findings],
                [("raw_identity_header", 5)],
            )
            self.assertEqual(service["resolver"]["state"], "unresolved")

    def test_raw_context_overwrite_revokes_verified_resolver_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def poisoned(request):\n"
                "    request.state.customer_id = verify_signed_customer_identity(\n"
                "        request.headers.get('X-Customer-ID')\n"
                "    )\n"
                "    request.state.customer_id = request.headers.get('X-Customer-ID'); uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["resolver"]["state"], "unresolved")
            self.assertTrue(
                any(
                    item["code"] == "raw_identity_header"
                    and item["evidence"]["line"] == 8
                    for item in service["findings"]
                )
            )

    def test_augmented_context_write_revokes_verified_resolver_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def poisoned(request):\n"
                "    request.state.customer_id = verify_signed_customer_identity(\n"
                "        request.headers.get('X-Customer-ID')\n"
                "    )\n"
                "    request.state.customer_id += '-unverified'\n"
                "    uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["resolver"]["state"], "unresolved")

    def test_destructured_context_write_revokes_verified_resolver_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def poisoned(request):\n"
                "    request.state.customer_id = verify_signed_customer_identity(\n"
                "        request.headers.get('X-Customer-ID')\n"
                "    )\n"
                "    request.state.customer_id, ignored = load_untrusted_identity()\n"
                "    uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["resolver"]["state"], "unresolved")
            self.assertTrue(
                any(
                    item["code"] == "verified_identity_header"
                    and item["evidence"]["line"] == 6
                    for item in service["findings"]
                )
            )

    def test_destructured_context_write_uses_pre_assignment_verified_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "def trusted(request):\n"
                "    raw_customer = request.headers.get('X-Customer-ID')\n"
                "    verified_customer = verify_signed_customer_identity(raw_customer)\n"
                "    verified_customer, request.state.customer_id = None, verified_customer\n"
                "    uuid.UUID(request.state.customer_id)\n"
                "@app.get('/orders', dependencies=[Depends(trusted)])\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["resolver"]["state"], "proposed")
            self.assertEqual(
                service["resolver"]["expression"],
                "request.state.customer_id",
            )
            self.assertTrue(
                any(
                    item["code"] == "verified_identity_header"
                    and item["evidence"]["line"] == 5
                    for item in service["findings"]
                )
            )

    def test_client_component_fetch_is_not_a_trusted_async_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"next":"1"}}', encoding="utf-8"
            )
            client = repo / "app" / "orders" / "page.tsx"
            client.parent.mkdir(parents=True)
            client.write_text(
                "'use client';\n"
                "export async function loadOrders() {\n"
                "  return fetch('/api/orders');\n"
                "}\n",
                encoding="utf-8",
            )
            (repo / "server.ts").write_text(
                "export async function syncOrders() {\n"
                "  return fetch('https://backend.example/orders');\n"
                "}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                self._load(output)["services"][0]["async_hops"],
                [{
                    "kind": "fetch",
                    "propagation": "missing",
                    "evidence": {"file": "server.ts", "line": 2},
                }],
            )

    def test_default_output_rejects_parent_symlink_and_replaces_target_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            escaped_repo = root / "escaped-repo"
            escaped_repo.mkdir()
            _copy_fixture("adversarial/source-revision", escaped_repo)
            outside = root / "outside"
            outside.mkdir()
            (escaped_repo / ".moolabs").symlink_to(outside, target_is_directory=True)

            escaped = subprocess.run(
                [sys.executable, str(DISCOVER), "--repo", str(escaped_repo),
                 "--generated-at", FIXED_TIME],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(escaped.returncode, 2, escaped.stderr)
            self.assertFalse((outside / "attribution" / "instrumentation-map.yaml").exists())

            safe_repo = root / "safe-repo"
            safe_repo.mkdir()
            _copy_fixture("adversarial/source-revision", safe_repo)
            destination = safe_repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
            destination.parent.mkdir(parents=True)
            sentinel = root / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            destination.symlink_to(sentinel)

            replaced = subprocess.run(
                [sys.executable, str(DISCOVER), "--repo", str(safe_repo),
                 "--generated-at", FIXED_TIME],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            self.assertFalse(destination.is_symlink())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")

    def test_python_parse_failures_are_reported_as_deterministic_unknown_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "@app.get('/broken')\n"
                "def broken(:\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            first = self._discover(repo, output)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_bytes = output.read_bytes()
            service = self._load(output)["services"][0]
            self.assertEqual(service["ingress_state"], "unknown")
            self.assertEqual(service["routes"], [])
            parse_findings = [
                finding for finding in service["findings"]
                if finding["code"] == "python_parse_error"
            ]
            self.assertEqual(
                parse_findings,
                [{
                    "code": "python_parse_error",
                    "severity": "warning",
                    "message": "Python source could not be parsed; discovery for this file is unknown",
                    "evidence": {"file": "app.py", "line": 4},
                }],
            )
            second = self._discover(repo, output)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(output.read_bytes(), first_bytes)

    def test_receiver_identity_is_lexically_scoped_across_python_js_and_go(self):
        cases = {
            "python": (
                "requirements.txt",
                "fastapi\n",
                "app.py",
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "app.add_middleware(AttributionMiddleware)\n"
                "@app.get('/covered')\n"
                "def covered(): return {}\n"
                "def build_shadowed_app():\n"
                "    app = FastAPI()\n"
                "    @app.get('/shadowed')\n"
                "    def shadowed(): return {}\n"
                "    return app\n",
            ),
            "javascript": (
                "package.json",
                '{"dependencies":{"express":"1"}}',
                "server.ts",
                "const app = express();\n"
                "app.use(attributionMiddleware);\n"
                "app.get('/covered', handler);\n"
                "function buildShadowedApp() {\n"
                "  const app = express();\n"
                "  app.get('/shadowed', handler);\n"
                "  return app;\n"
                "}\n",
            ),
            "go": (
                "go.mod",
                "module example.test/scopes\n\n"
                "require github.com/go-chi/chi/v5 v5.0.0\n",
                "main.go",
                "package main\n"
                'import "github.com/go-chi/chi/v5"\n'
                "func coveredRoutes() {\n"
                "  r := chi.NewRouter()\n"
                "  r.Use(AttributionMiddleware)\n"
                '  r.Get("/covered", coveredHandler)\n'
                "}\n"
                "func shadowedRoutes() {\n"
                "  r := chi.NewRouter()\n"
                "  if false {\n"
                "    r.Use(AttributionMiddleware)\n"
                "  }\n"
                '  r.Get("/shadowed", shadowedHandler)\n'
                "}\n",
            ),
        }

        for language, (manifest, manifest_text, source, source_text) in cases.items():
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / language
                repo.mkdir()
                (repo / manifest).write_text(manifest_text, encoding="utf-8")
                (repo / source).write_text(source_text, encoding="utf-8")
                output = repo.parent / "map.json"

                run = self._discover(repo, output)
                self.assertEqual(run.returncode, 0, run.stderr)
                result = self._load(output)
                service = result["services"][0]
                self.assertEqual(
                    {route["path_template"] for route in service["routes"]},
                    {"/covered", "/shadowed"},
                )
                self.assertEqual(
                    result["discovery_projection"]["routes_statically_covered"],
                    1,
                )
                self.assertEqual(
                    result["discovery_projection"]["routes_unknown"],
                    1,
                )
                self.assertTrue(
                    any(
                        finding["code"] == "middleware_missing"
                        for finding in service["findings"]
                    )
                )

    def test_resolver_candidate_is_revoked_after_later_untrusted_context_write(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import uuid\n"
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def poisoned(request):\n"
                "    request.state.customer_id = verify_signed_customer_identity(\n"
                "        request.headers.get('X-Customer-ID')\n"
                "    )\n"
                "    uuid.UUID(request.state.customer_id)\n"
                "    request.state.customer_id = load_untrusted_identity()\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            service = self._load(output)["services"][0]
            self.assertEqual(service["resolver"]["state"], "unresolved")
            self.assertTrue(
                any(
                    finding["code"] == "verified_identity_header"
                    and finding["evidence"]["line"] == 6
                    for finding in service["findings"]
                )
            )

    def test_multiline_raw_identity_header_read_emits_mandatory_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "def raw_identity(request):\n"
                "    customer_id = request.headers.get(\n"
                "        'X-Customer-ID'\n"
                "    )\n"
                "    return customer_id\n"
                "@app.get('/orders')\n"
                "def orders(): return {}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            findings = self._load(output)["services"][0]["findings"]
            self.assertIn(
                ("raw_identity_header", 4),
                {
                    (finding["code"], finding["evidence"]["line"])
                    for finding in findings
                    if finding["evidence"] is not None
                },
            )

    def test_raw_identity_headers_ignore_comments_and_string_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            python = repo / "services" / "python"
            javascript = repo / "services" / "javascript"
            golang = repo / "services" / "go"
            python.mkdir(parents=True)
            javascript.mkdir(parents=True)
            golang.mkdir(parents=True)
            (python / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (python / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "# request.headers.get('X-Customer-ID')\n"
                "'request.headers.get(\\\"X-Tenant-ID\\\")'\n"
                "@app.get('/python')\n"
                "def route(request):\n"
                "    return request.headers.get(\n"
                "        'X-Customer-ID'\n"
                "    )\n",
                encoding="utf-8",
            )
            (javascript / "package.json").write_text(
                '{"dependencies":{"express":"1"}}',
                encoding="utf-8",
            )
            (javascript / "app.ts").write_text(
                "const app = express();\n"
                "// req.headers.get('X-Customer-ID')\n"
                "const example = `req.headers.get('X-Tenant-ID')`;\n"
                "app.get('/javascript', (req) => req.headers.get(\n"
                "  'X-Customer-ID'\n"
                "));\n",
                encoding="utf-8",
            )
            (golang / "go.mod").write_text("module example.test/go\n", encoding="utf-8")
            (golang / "main.go").write_text(
                'package main\n'
                '// r.Header.Get("X-Customer-ID")\n'
                'const example = `r.Header.Get("X-Tenant-ID")`\n'
                'func handler(r *http.Request) {\n'
                '  _ = r.Header.Get(\n'
                '    "X-Customer-ID",\n'
                '  )\n'
                '}\n'
                'func main() { http.HandleFunc("/go", handler) }\n',
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            services = {
                service["service_path"]: service
                for service in self._load(output)["services"]
            }
            observed = {
                service_path: [
                    finding["evidence"]["line"]
                    for finding in service["findings"]
                    if finding["code"] == "raw_identity_header"
                ]
                for service_path, service in services.items()
            }
            self.assertEqual(
                observed,
                {
                    "services/go": [5],
                    "services/javascript": [4],
                    "services/python": [7],
                },
            )

    def test_js_export_clause_provenance_carries_receiver_middleware_state(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"express":"1"}}', encoding="utf-8"
            )
            (repo / "app.ts").write_text(
                "const app = express();\n"
                "app.use(attributionMiddleware);\n"
                "app.use(requireAuth);\n"
                "export { app };\n",
                encoding="utf-8",
            )
            (repo / "routes.ts").write_text(
                "import { app } from './app';\n"
                "app.get('/orders', handler);\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            service = result["services"][0]
            self.assertEqual(
                [
                    (
                        route["framework"],
                        route["path_template"],
                        route["auth_scope"],
                    )
                    for route in service["routes"]
                ],
                [("express", "/orders", "global")],
            )
            self.assertEqual(
                result["discovery_projection"]["routes_statically_covered"], 1
            )
            self.assertFalse(
                any(
                    finding["code"] == "middleware_missing"
                    for finding in service["findings"]
                )
            )

    def test_next_route_groups_optional_catchalls_and_alias_exports_are_honest(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "package.json").write_text(
                '{"dependencies":{"next":"1"}}', encoding="utf-8"
            )
            optional = repo / "app" / "(marketing)" / "docs" / "[[...slug]]" / "route.ts"
            optional.parent.mkdir(parents=True)
            optional.write_text(
                "export async function GET() { return Response.json({}); }\n",
                encoding="utf-8",
            )
            aliased = repo / "app" / "api" / "[...parts]" / "route.ts"
            aliased.parent.mkdir(parents=True)
            aliased.write_text(
                "const handler = () => Response.json({});\n"
                "export { handler as POST };\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                {
                    (route["method"], route["path_template"])
                    for route in routes
                },
                {
                    ("GET", "/docs/{...slug?}"),
                    ("POST", "/api/{...parts}"),
                },
            )

    def test_aliased_chi_and_python_route_calls_require_import_provenance(self):
        with self.subTest(language="go"), tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "go.mod").write_text(
                "module example.test/aliases\n\n"
                "require github.com/go-chi/chi/v5 v5.0.0\n",
                encoding="utf-8",
            )
            (repo / "main.go").write_text(
                "package main\n"
                'import web "github.com/go-chi/chi/v5"\n'
                "func main() {\n"
                "  router := web.NewRouter()\n"
                "  router.Use(AttributionMiddleware)\n"
                '  router.Get("/orders", ordersHandler)\n'
                "}\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = self._load(output)
            self.assertEqual(
                [
                    (route["framework"], route["path_template"])
                    for route in result["services"][0]["routes"]
                ],
                [("chi", "/orders")],
            )
            self.assertEqual(
                result["discovery_projection"]["routes_statically_covered"], 1
            )

        with self.subTest(language="python"), tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "from fastapi import FastAPI\n"
                "from internal.routing import Route\n"
                "from starlette.routing import Route as StarletteRoute\n"
                "fake = Route('/fake', fake_handler)\n"
                "real = StarletteRoute('/real', real_handler, methods=['GET'])\n",
                encoding="utf-8",
            )
            output = repo.parent / "map.json"

            run = self._discover(repo, output)
            self.assertEqual(run.returncode, 0, run.stderr)
            routes = self._load(output)["services"][0]["routes"]
            self.assertEqual(
                [
                    (route["framework"], route["method"], route["path_template"])
                    for route in routes
                ],
                [("starlette", "GET", "/real")],
            )

    @staticmethod
    def _load_text_json(text: str) -> dict:
        return json.loads(text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
