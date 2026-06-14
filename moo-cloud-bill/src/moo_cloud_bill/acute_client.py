"""HTTP client for Acute's cloud-billing push API.

Contract (moo-acute router): POST /api/v1/cloud-billing/import and
/resource-map; auth is ``Authorization: Bearer <key>``; tenant is derived
server-side so it is NEVER in the body. Idempotency is server-side per-period
supersession, so re-POSTing a day is safe; we retry only idempotent 5xx.

The transport is injectable so tests run without httpx/network.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

IMPORT_PATH = "/api/v1/cloud-billing/import"
RESOURCE_MAP_PATH = "/api/v1/cloud-billing/resource-map"


@dataclass(frozen=True)
class Result:
    status_code: int
    body: dict

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def _httpx_post(url: str, headers: dict, json_body: dict):
    import httpx

    resp = httpx.post(url, headers=headers, json=json_body, timeout=30.0)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return resp.status_code, body


class AcuteClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        post=None,
        max_retries: int = 3,
        backoff: float = 0.5,
        sleep=time.sleep,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._post = post or _httpx_post
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep

    def _headers(self) -> dict:
        # Bearer = external/customer trust domain (auth.py). Never logged.
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def _send(self, path: str, body: dict) -> Result:
        # Hard raise, not assert: asserts are stripped under `python -O`, which
        # would silently disable this contract guard.
        if "tenant_id" in body:
            raise ValueError("tenant_id must never be sent; Acute derives it from the Bearer key")
        url = self._base + path
        attempt = 0
        while True:
            status, resp_body = self._post(url, self._headers(), body)
            if status < 500 or attempt >= self._max_retries:
                return Result(status_code=status, body=resp_body or {})
            attempt += 1
            self._sleep(self._backoff * attempt)

    def import_batch(self, batch) -> Result:
        return self._send(IMPORT_PATH, batch.to_body())

    def upsert_resource_map(self, body: dict) -> Result:
        return self._send(RESOURCE_MAP_PATH, body)
