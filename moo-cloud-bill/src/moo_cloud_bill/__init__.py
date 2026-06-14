"""moo-cloud-bill — customer CLI that configures an AWS Legacy CUR and pushes it
to Moolabs Acute (`/api/v1/cloud-billing/import`) for cost attribution.

Deterministic, no LLM at runtime. See tasks/prd-cloud-bill-cur-report.md.
"""

__version__ = "0.1.0"
