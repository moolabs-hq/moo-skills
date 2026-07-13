#!/usr/bin/env python3
"""Compare unsafe naive attribution guesses with the scanner's honest contract."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from attribution_scan import discover


FIXED_TIME = "2026-07-13T12:00:00Z"


def _write_case(root: Path, name: str, manifest: str, filename: str, source: str) -> Path:
    repo = root / name
    repo.mkdir()
    (repo / ("requirements.txt" if filename.endswith(".py") else "package.json")).write_text(
        manifest,
        encoding="utf-8",
    )
    (repo / filename).write_text(source, encoding="utf-8")
    return repo


def _naive_raw_header(source: str) -> dict[str, Any]:
    match = re.search(r"headers(?:\.get)?\([^\n]*[Xx]-Customer-ID", source)
    return {
        "customer_resolver": "request.headers[X-Customer-ID]" if match else None,
        "trusted": match is not None,
    }


def _naive_dynamic_route(source: str) -> dict[str, Any]:
    match = re.search(r"app\.get\(([^,]+),", source)
    expression = match.group(1).strip() if match else None
    return {"path_template": expression, "covered": expression is not None}


def run() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        dynamic_source = (
            "const express = require('express');\n"
            "const app = express();\n"
            "const routePath = process.env.ROUTE_PATH;\n"
            "app.get(routePath, (_req, res) => res.send('ok'));\n"
        )
        dynamic_repo = _write_case(
            root,
            "dynamic-route",
            '{"dependencies":{"express":"1"}}',
            "server.js",
            dynamic_source,
        )
        dynamic_map = discover(dynamic_repo, FIXED_TIME)
        dynamic_route = dynamic_map["services"][0]["routes"][0]

        raw_source = (
            "from fastapi import FastAPI, Request\n"
            "app = FastAPI()\n"
            "@app.get('/customers')\n"
            "def customers(request: Request):\n"
            "    return request.headers.get('X-Customer-ID')\n"
        )
        raw_repo = _write_case(
            root,
            "raw-header",
            "fastapi\n",
            "app.py",
            raw_source,
        )
        raw_map = discover(raw_repo, FIXED_TIME)
        raw_service = raw_map["services"][0]

        cases = [
            {
                "case": "dynamic-route",
                "naive": _naive_dynamic_route(dynamic_source),
                "naive_unsafe": _naive_dynamic_route(dynamic_source)["covered"],
                "scanner": {
                    "confidence": dynamic_route["confidence"],
                    "path_template": dynamic_route["path_template"],
                },
                "scanner_contract_honest": (
                    dynamic_route["path_template"] is None
                    and dynamic_route["confidence"] == "low"
                    and dynamic_route["feature_proposal"]["requires_engineer_signoff"] is True
                ),
            },
            {
                "case": "raw-header",
                "naive": _naive_raw_header(raw_source),
                "naive_unsafe": _naive_raw_header(raw_source)["trusted"],
                "scanner": {
                    "finding_codes": sorted(item["code"] for item in raw_service["findings"]),
                    "resolver_state": raw_service["resolver"]["state"],
                },
                "scanner_contract_honest": (
                    raw_service["resolver"]["state"] == "unresolved"
                    and any(item["code"] == "raw_identity_header" for item in raw_service["findings"])
                ),
            },
        ]
        return {"cases": cases, "generated_at": FIXED_TIME}


def main() -> int:
    report = run()
    if not all(
        case["naive_unsafe"] and case["scanner_contract_honest"]
        for case in report["cases"]
    ):
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
