# Repo detection — manifest-first, extension-fallback

Per `v1-decisions-log.md` §6.4b #19c, language detection is manifest-first; falls back to file-extension dominance only when no manifest is found.

## Per-language manifest signatures

| Language | Manifest files (any match → language detected) |
|---|---|
| Python | `pyproject.toml`, `requirements.txt`, `setup.py`, `setup.cfg`, `Pipfile` |
| TypeScript | `package.json` AND `tsconfig.json` (both must be present for TS; only `package.json` → JavaScript) |
| JavaScript | `package.json` alone |
| Go | `go.mod`, `go.sum` |
| Rust | `Cargo.toml` |
| Java | `pom.xml`, `build.gradle`, `build.gradle.kts` |

## Framework detection (Python)

Read the manifest's dependencies (works for `pyproject.toml` PEP 631 `[project.dependencies]`, `[tool.poetry.dependencies]`, and `requirements.txt`):

| Framework | Detection package |
|---|---|
| fastapi | `fastapi`, `uvicorn`, `starlette` |
| django | `django`, `djangorestframework` |
| flask | `flask`, `flask-restful` |
| tornado | `tornado` |
| litestar | `litestar` |
| aiohttp | `aiohttp` |

## Framework detection (TypeScript / JavaScript)

Read `package.json` dependencies + devDependencies:

| Framework | Detection package |
|---|---|
| express | `express` |
| nestjs | `@nestjs/core`, `@nestjs/common` |
| nextjs | `next` |
| fastify | `fastify` |
| koa | `koa` |
| hono | `hono` |

## Framework detection (Go)

Read `go.mod` `require` blocks:

| Framework | Detection module |
|---|---|
| net-http-stdlib | (default if go.mod exists with no other framework match) |
| gin | `github.com/gin-gonic/gin` |
| echo | `github.com/labstack/echo` |
| fiber | `github.com/gofiber/fiber` |
| chi | `github.com/go-chi/chi` |

## Existing instrumentation detection

Detect existing OTel / vendor SDKs to drive the brownfield-vs-greenfield codemod branch (per `v1-decisions-log.md` §6.4b #19g):

| Instrumentation | Detection package(s) |
|---|---|
| OpenTelemetry API | `opentelemetry-api`, `@opentelemetry/api` |
| OpenTelemetry SDK | `opentelemetry-sdk`, `@opentelemetry/sdk-node` |
| OpenLLMetry | `openllmetry-sdk`, `@traceloop/node-server-sdk` |
| Helicone | `helicone`, `@helicone/helicone` |
| Langfuse | `langfuse` |
| Datadog APM | `ddtrace`, `dd-trace` |
| Sentry | `sentry-sdk`, `@sentry/node` |

**Brownfield path** (existing OTel detected): `/cost-billing-instrument` extends existing spans with `moolabs.*` attributes — does NOT wrap or duplicate. Idempotent re-runs are safe.

**Greenfield path** (no instrumentation): `/cost-billing-instrument` introduces both the Moolabs SDK init AND OTel auto-instrumentation, framework-appropriate (e.g., `opentelemetry-instrumentation-fastapi`).

## Existing Moolabs SDK detection

If the customer has `moolabs` (Python) or `@moolabs/sdk` (TypeScript) already installed, the customer has done some integration. Behavior per `v1-decisions-log.md` §6.4b #19d:

1. `/cost-billing-discovery` records this in `repo-profile.yaml` and surfaces a note.
2. The integrator (running discovery) is asked: "Upgrade in place" or "Fresh re-instrument"?
3. "Upgrade in place" → Skill 2 codemod skips files where existing `client.usage.*` calls are detected; only inserts where SDK is missing.
4. "Fresh re-instrument" → Skill 2 codemod replaces existing inserts with current best-practice templates.

## Repo type classification

`repo_scan.py` outputs one of:

| `repo_type` | Definition |
|---|---|
| `polyrepo` | Exactly 1 service detected, at the repo root. |
| `monorepo` | 2–3 services, multiple manifests under sub-paths. |
| `microservices` | 4+ services with manifests. |
| `unknown` | No manifests, no dominant language by extension. |

The codemod's PR-chunking strategy (max 30 files / PR, per `v1-decisions-log.md`) is service-aware — chunks by service path.

## Multi-language services

A single service may have multiple languages (e.g., a Python backend with a TypeScript frontend in one directory). The repo_scan emits ALL detected languages in `service.languages`. The codemod runs per-language passes:

```
services/api/   languages: [python, typescript]
                manifests: [pyproject.toml, package.json, tsconfig.json]
                frameworks_detected: [fastapi, express]
```

Each language scan produces its own per-language inventory entries; outputs are merged at the service level.
