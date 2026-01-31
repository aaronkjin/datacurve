# Datacurve

Coding data telemetry pipeline for training LLMs. Captures developer debugging sessions (code edits, terminal commands, reasoning) and evaluates them via automated test execution and LLM-based judging.

---

## Table of Contents

1. [Project Plan](#project-plan)
2. [Quick Start](#quick-start)
3. [System Architecture](#system-architecture)
4. [API Reference](#api-reference)
5. [Configuration](#configuration)
6. [Development](#development)

---

## Project Plan

Check out my project plan [here](https://docs.google.com/document/d/1VtB2qBSLW4lU0BYttS_3e7Kj_Hc2zXg4mBYodu1rs6Q/edit?usp=sharing).

### Background & Context

The goal of this project is to build a system to capture a novel, high-fidelity dataset for pushing frontier AI coding capabilities. The future of AI assistants for developers lies in understanding not just _what_ code was changed, but _why_ and _how_ a developer arrived at a solution.

Research Brief: "We want to improve our model's ability to autonomously fix bugs in large repositories. We need data that captures how skilled human developers do this. The data should include anything that could be useful to us, such as their code edits, terminal commands, and their 'chain-of-thought' reasoning."

### Key Design Decisions

| Question         | Decision                                                           |
| ---------------- | ------------------------------------------------------------------ |
| Unit of data     | One trace = one bug-fix session culminating in a PR                |
| Privacy/storage  | Full snapshots with configurable redaction (raw vs. redacted mode) |
| Timing fidelity  | Event-level with millisecond timestamps, save-level edits          |
| Downstream use   | Dual: trajectory-style for training + evaluation-ready with scores |
| Chain-of-thought | Developer-authored "thought events" aligned to actions             |

### Data Schema Design Goals

- Event-sourced: A single ordered event log reconstructs the session timeline
- Incremental ingestion-friendly: Append events over time; finalize later
- Replay + training ready: References to code state (snapshots/patches) and command outputs
- Scalable storage: Large blobs are content-addressed and stored out-of-line

### MVP Scope

- Accept trace creation + event ingestion (batch and/or incremental)
- Persist to DB + blob store
- Finalize triggers QA:
  - Dockerized test run with results stored back into trace
  - LLM judge scoring + rationale stored back into trace
- One-command `docker-compose up` + E2E demo path

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+
- OpenAI API key

### Setup

1. Clone and configure:

   ```bash
   git clone # this repo
   cd datacurve
   cp .env.example .env
   # edit .env and add your OPENAI_API_KEY
   ```

2. Start all services:

   ```bash
   docker-compose up --build
   ```

3. Run the E2E demo (in another terminal):

   ```bash
   # install deps locally
   pip install -e .

   # run basic demo
   python scripts/demo_e2e.py

   # or run the itsdangerous real-repo demo
   python scripts/demo_itsdangerous.py
   ```

4. See final judge scores after E2E flow, for example:

   ```
   [2026-01-31 10:00:00] Starting E2E demo...
   [2026-01-31 10:00:01] ✓ Health check passed
   [2026-01-31 10:00:01] ✓ Created trace: abc123-def456-...
   [2026-01-31 10:00:02] ✓ Uploaded 3 blobs
   [2026-01-31 10:00:02] ✓ Appended 5 events (seq_high=5)
   [2026-01-31 10:00:03] ✓ Finalized trace, qa_job_id: xyz789-...
   [2026-01-31 10:00:03] Waiting for QA pipeline...
   [2026-01-31 10:00:18] Status: complete
   [2026-01-31 10:00:18] ✓ QA complete!

   === Judge Scores ===
   Root Cause Identification: 4.5
   Plan Quality: 4.0
   ...
   Overall: 4.2 / 5.0

   [2026-01-31 10:00:18] ✓ All checks passed!
   ```

---

## System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Client (IDE Plugin)                         │
│                    Creates traces, uploads blobs, appends events         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           API Service (FastAPI)                          │
│                                                                          │
│  POST /traces          - Create new trace                                │
│  POST /traces/{id}/events - Append events to trace                       │
│  POST /traces/{id}/finalize - Finalize and trigger QA                    │
│  GET  /traces/{id}     - Fetch trace with QA results                     │
│  POST /blobs           - Upload content-addressed blob                   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                    ▼                ▼                ▼
             ┌──────────┐     ┌──────────┐     ┌──────────┐
             │ PostgreSQL│     │   Redis  │     │  Blob    │
             │  (traces, │     │  (task   │     │  Store   │
             │  events)  │     │  queue)  │     │ (files)  │
             └──────────┘     └────┬─────┘     └──────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Worker Service (Celery)                         │
│                                                                          │
│  qa.run_tests    - Execute tests in Docker container                     │
│  qa.run_judge    - Call LLM judge with rubric prompt                     │
│  qa.finalize_qa  - Mark trace as complete                                │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Details

| Component  | Technology       | Purpose                                       |
| ---------- | ---------------- | --------------------------------------------- |
| API        | FastAPI          | HTTP endpoints for trace/event/blob ingestion |
| Worker     | Celery           | Async task processing for QA pipeline         |
| Database   | PostgreSQL       | Persistent storage for traces and events      |
| Queue      | Redis            | Task queue and result backend                 |
| Blob Store | Local filesystem | Content-addressed storage for large data      |

### Data Flow

1. Trace Creation: Client creates trace with repo/task/developer metadata
2. Blob Upload: Large content (patches, logs) uploaded as content-addressed blobs
3. Event Ingestion: Events appended with monotonic sequence numbers
4. Finalize: Client finalizes trace → triggers QA pipeline
5. QA Pipeline:
   - `qa.run_tests`: Executes tests in isolated Docker container
   - `qa.run_judge`: LLM evaluates trace against rubric
   - `qa.finalize_qa`: Sets trace status to `complete`
6. Retrieval: Client fetches final trace with QA scores

### Trace Status Flow

```
collecting → finalizing → complete
                ↓
              failed
```

---

## API Reference

### Endpoints

| Endpoint                      | Method | Description                         |
| ----------------------------- | ------ | ----------------------------------- |
| `/health`                     | GET    | Health check                        |
| `/traces`                     | POST   | Create a new trace                  |
| `/traces/{trace_id}/events`   | POST   | Append events to trace              |
| `/traces/{trace_id}/finalize` | POST   | Finalize trace, trigger QA          |
| `/traces/{trace_id}`          | GET    | Fetch full trace with QA results    |
| `/blobs`                      | POST   | Upload a blob (multipart/form-data) |

### Event Types

| Type               | Description                      |
| ------------------ | -------------------------------- |
| `file_edit`        | Code change with patch reference |
| `file_snapshot`    | Full file content at checkpoint  |
| `terminal_command` | Shell command executed           |
| `terminal_output`  | stdout/stderr chunk              |
| `test_run`         | Test execution result            |
| `thought`          | Developer reasoning/hypothesis   |
| `commit`           | Git commit event                 |
| `pr_metadata`      | Pull request info                |
| `error`            | Error/exception event            |

### Judge Scoring Dimensions

The LLM judge evaluates traces on 6 dimensions (0.0–5.0 each):

1. Root Cause Identification — Did developer correctly identify the bug cause?
2. Plan Quality — Clear hypothesis → test → iterate loop?
3. Experiment & Iterate Loop — Each change tested with feedback?
4. Use of Signals (Tests & Logs) — Signals used to guide decisions?
5. Minimality of Fix — Fix targeted with minimal unrelated changes?
6. Clarity — Reasoning clear and grounded in evidence?

---

## Configuration

### Environment Variables

| Variable               | Description                  | Default                    |
| ---------------------- | ---------------------------- | -------------------------- |
| `OPENAI_API_KEY`       | OpenAI API key for LLM judge | (required)                 |
| `JUDGE_MODEL`          | Model to use for judging     | `gpt-5.2`                  |
| `DATABASE_URL`         | Async PostgreSQL connection  | `postgresql+asyncpg://...` |
| `DATABASE_URL_SYNC`    | Sync PostgreSQL connection   | `postgresql://...`         |
| `REDIS_URL`            | Redis connection for Celery  | `redis://localhost:6379/0` |
| `BLOB_STORE_PATH`      | Path for blob storage        | `/data/blobs`              |
| `TEST_TIMEOUT_SECONDS` | Max time for test execution  | `120`                      |
| `TEST_MEMORY_LIMIT`    | Docker memory limit          | `512m`                     |
| `TEST_BASE_IMAGE`      | Docker image for tests       | `python:3.11-slim`         |

### Docker Compose Services

| Service    | Port | Description                 |
| ---------- | ---- | --------------------------- |
| `api`      | 8000 | FastAPI ingestion service   |
| `worker`   | —    | Celery worker (QA pipeline) |
| `postgres` | 5432 | PostgreSQL database         |
| `redis`    | 6379 | Redis queue                 |

---

## Development

### Running Tests

```bash
# run all tests
pytest

# run with coverage
pytest --cov=.

# run specific test file
pytest tests/test_api.py -v
```

### Local Development (without Docker)

```bash
# create virtual environment
python -m venv venv
source venv/bin/activate

# install dependencies
pip install -e ".[dev]"

# start PostgreSQL and Redis (via Docker)
docker-compose up postgres redis -d

# run API
uvicorn api.main:app --reload

# run worker (separate terminal)
celery -A worker.celery_app worker --loglevel=info
```

### Adding New Event Types

1. Add enum value to `EventType` in `core/models.py`
2. Create payload model (e.g., `NewEventPayload`)
3. Add to `PAYLOAD_TYPE_MAP`
4. Update validation in `core/validation.py`

---

## License

Internal use only.

## Developer

Aaron Jin  
[GitHub Profile](https://github.com/aaronkjin)
