# Project Instructions

## Rules

- Do not change API contracts without updating `docs/SPEC.md`.
- Prefer small PR-style diffs; add tests when possible.
- All services must run via `docker-compose up`.
- Follow the schema + endpoints in `PLAN.md` and `docs/SPEC.md`.
- The trace/event schema defined in `docs/SPEC.md` is the source of truth for all Pydantic models.

## Architecture

- **api/**: FastAPI ingestion service (endpoints defined in `docs/SPEC.md`)
- **worker/**: Celery worker for QA pipeline (test runner + LLM judge)
- **core/**: Shared code — Pydantic models, storage interfaces, blob store, materializer
- **db/**: Database migrations (Alembic)
- **scripts/**: E2E demo and utility scripts
- **docs/**: Frozen contracts (`SPEC.md`, `ACCEPTANCE.md`, `RUBRIC.md`)

## Stack

- Python 3.11+, FastAPI, Celery, Redis, PostgreSQL
- Docker Compose for all services
- Local filesystem blob store (MVP), S3-compatible interface for future

## Testing

- Run tests with `pytest` from project root
- E2E demo: `python scripts/demo_e2e.py`
