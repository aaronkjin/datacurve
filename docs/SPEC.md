# Technical Specification (Frozen Contract)

> This document is the single source of truth for API contracts, DB schema, blob-store
> interface, job queue interface, and trace materialization rules. **Do not deviate from
> these contracts without updating this file first.**

---

## 1. API Endpoints

All endpoints accept and return `application/json`. Auth: Bearer token via `Authorization` header (simple shared secret for MVP).

### POST /traces

Create a new trace.

**Request body:**
```json
{
  "repo": {
    "repo_id": "string",
    "remote_url": "string|null",
    "default_branch": "string|null",
    "commit_base": "git_sha"
  },
  "task": {
    "task_id": "string|null",
    "bug_report": {
      "title": "string",
      "description": "string",
      "repro_steps": "string|null",
      "expected": "string|null",
      "actual": "string|null",
      "links": ["string"]
    },
    "labels": ["string"]
  },
  "developer": {
    "developer_id": "string",
    "experience_level": "junior|mid|senior|unknown",
    "consent_flags": {
      "store_raw_code": true,
      "store_terminal_output": true,
      "allow_llm_judge": true
    }
  },
  "environment": {
    "os": "string|null",
    "ide": { "name": "string", "version": "string|null" },
    "language": ["string"],
    "containerized": true,
    "timezone": "string|null"
  }
}
```

**Response:** `201 Created`
```json
{
  "trace_id": "uuid",
  "created_at_ms": 1234567890,
  "status": "collecting"
}
```

### POST /traces/{trace_id}/events

Append events to an existing trace.

**Request body:**
```json
{
  "events": [
    {
      "event_id": "uuid",
      "seq": 1,
      "ts_ms": 1234567890,
      "type": "file_edit|file_snapshot|terminal_command|terminal_output|test_run|debug_action|navigation|thought|commit|pr_metadata|error",
      "actor": { "kind": "human|tool|ide", "id": "string|null" },
      "context": {
        "workspace_root": "string|null",
        "branch": "string|null",
        "commit_head": "git_sha|null",
        "correlation_id": "uuid|null",
        "parent_event_id": "uuid|null"
      },
      "payload": {}
    }
  ]
}
```

**Constraints:**
- `seq` must be monotonically increasing per trace.
- Duplicate `event_id` within a trace is rejected with `409 Conflict`.
- Maximum 100 events per request.

**Response:** `202 Accepted`
```json
{
  "accepted": 5,
  "seq_high": 10
}
```

### POST /traces/{trace_id}/finalize

Mark trace as complete and trigger QA pipeline.

**Request body:**
```json
{
  "final_state": {
    "commit_head": "git_sha|null",
    "pr": {
      "title": "string|null",
      "description": "string|null",
      "diff_blob_id": "sha256:...|null"
    }
  }
}
```

**Response:** `200 OK`
```json
{
  "trace_id": "uuid",
  "status": "finalizing",
  "qa_job_id": "uuid"
}
```

### GET /traces/{trace_id}

Retrieve a full or metadata-only trace.

**Query params:**
- `include_events` (bool, default true)
- `include_qa` (bool, default true)

**Response:** `200 OK` — Full trace JSON per schema in PLAN.md.

### POST /blobs

Upload a blob (binary or text).

**Request:** `multipart/form-data` with field `file`.

**Response:** `201 Created`
```json
{
  "blob_id": "sha256:abc123...",
  "byte_length": 4096,
  "storage_uri": "file:///data/blobs/sha256/abc123..."
}
```

---

## 2. Database Tables

All tables use PostgreSQL. Primary keys are UUIDs unless noted.

### traces
| Column | Type | Constraints |
|---|---|---|
| trace_id | UUID | PK, default gen_random_uuid() |
| trace_version | VARCHAR(10) | NOT NULL, default '1.0' |
| status | VARCHAR(20) | NOT NULL, default 'collecting'. Enum: collecting, finalizing, complete, failed |
| repo_json | JSONB | NOT NULL |
| task_json | JSONB | NOT NULL |
| developer_json | JSONB | NOT NULL |
| environment_json | JSONB | NOT NULL |
| ingestion_json | JSONB | nullable |
| final_state_json | JSONB | nullable |
| qa_json | JSONB | nullable |
| created_at_ms | BIGINT | NOT NULL |
| finalized_at_ms | BIGINT | nullable |

### events
| Column | Type | Constraints |
|---|---|---|
| id | BIGSERIAL | PK |
| trace_id | UUID | FK -> traces.trace_id, NOT NULL |
| event_id | UUID | NOT NULL |
| seq | INTEGER | NOT NULL |
| ts_ms | BIGINT | NOT NULL |
| type | VARCHAR(30) | NOT NULL |
| actor_json | JSONB | NOT NULL |
| context_json | JSONB | nullable |
| payload_json | JSONB | NOT NULL |

**Unique constraint:** `(trace_id, event_id)` — enforces idempotency.
**Index:** `(trace_id, seq)` — enforces ordering queries.

### blobs
| Column | Type | Constraints |
|---|---|---|
| blob_id | VARCHAR(80) | PK (sha256:hex) |
| content_type | VARCHAR(50) | NOT NULL |
| byte_length | BIGINT | NOT NULL |
| storage_uri | TEXT | NOT NULL |
| redaction_json | JSONB | nullable |
| created_at_ms | BIGINT | NOT NULL |

---

## 3. Blob Store Interface

```python
class BlobStore(Protocol):
    def put_bytes(self, data: bytes, content_type: str) -> str:
        """Store bytes, return blob_id (sha256:hex)."""
        ...

    def get_bytes(self, blob_id: str) -> bytes:
        """Retrieve bytes by blob_id."""
        ...

    def get_uri(self, blob_id: str) -> str:
        """Return storage URI for a blob_id."""
        ...

    def exists(self, blob_id: str) -> bool:
        """Check if blob already stored (content-addressed dedup)."""
        ...
```

MVP implementation: `LocalFsBlobStore` writing to `/data/blobs/` volume.

---

## 4. Job Queue Interface

Queue: Redis-backed Celery (broker + result backend).

### Tasks

| Task name | Trigger | Input | Output |
|---|---|---|---|
| `qa.run_tests` | On finalize | `trace_id` | Updates `qa.tests` in trace |
| `qa.run_judge` | After `run_tests` completes | `trace_id` | Updates `qa.judge` in trace |
| `qa.finalize_qa` | After `run_judge` completes | `trace_id` | Sets trace status to `complete` |

Each task is idempotent. Failed tasks set trace status to `failed` and store error in `qa_json`.

---

## 5. Trace Materialization Rules

When a trace reaches status `complete`, the materializer produces a single JSON document:

1. Read `traces` row + all `events` rows (ordered by `seq`).
2. Assemble top-level schema per PLAN.md.
3. Blob references remain as `blob_id` strings (not inlined) by default.
4. Write materialized JSON as a new blob; store its `blob_id` in `final_state_json.materialized_blob_id`.
5. The materialized trace must pass JSON Schema validation (schema version = `trace_version`).

---

## 6. Event Payload Schemas

See PLAN.md "Core event payloads" section. The following types are supported:

- `file_edit` — patch-based edit
- `file_snapshot` — full file content at checkpoint
- `terminal_command` — command executed
- `terminal_output` — stdout/stderr chunk
- `test_run` — normalized test execution result
- `thought` — developer reasoning note
- `debug_action` — debugger step/breakpoint (future)
- `navigation` — file/symbol navigation (future)
- `commit` — git commit event
- `pr_metadata` — PR creation/update
- `error` — error/exception event
