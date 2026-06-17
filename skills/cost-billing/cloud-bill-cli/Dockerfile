# moo-cloud-bill, containerized for an ECS Fargate scheduled task (daily `push`).
#
# This image is GENERIC — it bakes NO secrets and NO customer config:
#   - config   → env vars (MCB_BUCKET, MCB_PREFIX, MCB_REPORT_NAME, MCB_REGION,
#                MCB_ACUTE_BASE, MCB_REPORTING_CURRENCY) set in the task definition
#   - API key  → MOOLABS_API_KEY injected from Secrets Manager by the task definition
#   - AWS auth → the ECS task role (no SSO, no stored keys; tokens never expire)
# See AWS_SCHEDULING.md for the full setup runbook.
FROM python:3.12-slim

WORKDIR /app

# Install the package (+ boto3/httpx/pyyaml) from the src layout.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Drop root — the task only needs to read S3 and POST to Acute.
RUN useradd --create-home --uid 10001 app
USER app

# `push` reads the CUR, aggregates to daily, and POSTs to Acute, then exits 0
# (non-zero on any failed day so the schedule surfaces it). The column map falls
# back to the CUR 2.0 defaults when no override file is present.
ENTRYPOINT ["moo-cloud-bill"]
CMD ["push"]
