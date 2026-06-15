"""`verify` — confirm the setup actually works: (1) Acute is reachable and the
Moolabs API key authenticates, (2) report whether the CUR has delivered data yet.

A real `push` can't be validated right after `configure` because AWS doesn't
deliver the first CUR for ~24-48h — but the Acute connection + auth CAN be tested
immediately, which is the part most likely to be misconfigured.
"""
from __future__ import annotations

from .. import aws
from ..acute_client import AcuteClient
from .push import _list_cur_object_keys


def run_verify(config, api_key, *, clients=None, acute_client=None, out=print) -> int:
    # 1) Acute reachability + auth (read-only GET, no CUR data required).
    client = acute_client or AcuteClient(config.acute_base, api_key)
    res = client.list_imports("aws")
    if res.status_code == 0:
        out(f"✗ Cannot reach Acute at {config.acute_base}: {res.body.get('error')}")
        out("  Check the Acute base URL (ACUTE_BASE / --acute-base).")
        return 1
    if res.status_code in (401, 403):
        out(f"✗ Acute auth failed ({res.status_code}) — check your Moolabs API key (`moo-cloud-bill init`).")
        return 1
    if not res.ok:
        out(f"✗ Acute returned {res.status_code}: {res.body}")
        return 1
    n = len(res.body) if isinstance(res.body, list) else "?"
    out(f"✓ Acute reachable + authenticated at {config.acute_base} ({n} existing import batch(es)).")

    # 2) CUR data presence (needs the configured bucket + AWS creds).
    if not (config.bucket and config.report_name):
        out("• No CUR configured yet — run `moo-cloud-bill configure` first.")
        return 0
    try:
        clients = clients or aws.make_clients(profile=config.aws_profile, region=config.region)
        keys = _list_cur_object_keys(clients["s3"], config.bucket, config.prefix, config.report_name)
    except Exception as exc:
        out(f"• Could not check CUR data: {aws.as_friendly_error(exc) or exc}")
        return 0
    if keys:
        out(f"✓ CUR has data ({len(keys)} CSV file(s)) — run `moo-cloud-bill push` to send it.")
    else:
        out("• CUR not delivered yet (first delivery ~24-48h after creation); `push` will have data once it lands.")
    return 0
