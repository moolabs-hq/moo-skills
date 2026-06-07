FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV LOG_LEVEL=info

# Codemod emits a CHECKLIST comment here — NEVER bake the secret into ENV.
# MOOLABS_API_KEY must be injected at runtime via the orchestrator
# (ECS task secrets / k8s envFrom: secretRef / docker run -e).

COPY . .
CMD ["uvicorn", "app.main:app"]
