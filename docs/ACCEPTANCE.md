# Acceptance Criteria & E2E Demo Checklist

> This document defines what "done" looks like for the MVP. Every item must pass
> before the system is considered complete.

---

## E2E Demo Flow

The demo script (`scripts/demo_e2e.py`) performs these steps in order:

1. **Start services:** `docker-compose up` brings up API, worker, Postgres, Redis.
2. **Create a trace:** `POST /traces` with sample repo + bug report metadata. Verify `201` and valid `trace_id`.
3. **Upload blobs:** `POST /blobs` with a sample patch file. Verify `201` and `blob_id` returned.
4. **Append events:** `POST /traces/{trace_id}/events` with a batch of 5+ events covering:
   - At least one `file_edit` event (with patch blob reference)
   - At least one `terminal_command` + `terminal_output` pair
   - At least one `test_run` event
   - At least one `thought` event
   - Verify `202` and correct `seq_high`.
5. **Finalize trace:** `POST /traces/{trace_id}/finalize` with final state. Verify `200` and `qa_job_id`.
6. **Wait for QA:** Poll `GET /traces/{trace_id}` until `status == "complete"` (timeout: 120s).
7. **Validate final trace:**
   - `qa.schema_valid == true`
   - `qa.tests.final_passed` is a boolean (true or false is acceptable for demo)
   - `qa.judge.scores` contains all 6 rubric dimensions
   - `qa.judge.overall` is a float 0.0–5.0
   - All blob references in events resolve to existing blobs

---

## Pass/Fail Checklist

### Infrastructure
- [ ] `docker-compose up` starts all services without error
- [ ] API is reachable at `http://localhost:8000`
- [ ] Postgres is initialized with correct schema (tables: traces, events, blobs)
- [ ] Redis is reachable by worker
- [ ] Worker connects and processes tasks

### Ingestion API
- [ ] `POST /traces` returns `201` with valid UUID
- [ ] `POST /traces/{id}/events` accepts batch of events, returns `202`
- [ ] Duplicate `event_id` is rejected with `409`
- [ ] Out-of-order `seq` is rejected with `422`
- [ ] `POST /traces/{id}/finalize` returns `200` and enqueues QA job
- [ ] `GET /traces/{id}` returns full trace JSON
- [ ] `POST /blobs` stores file and returns content-addressed `blob_id`
- [ ] Uploading identical content returns same `blob_id` (dedup)

### QA Pipeline
- [ ] Schema validation runs on finalize and result stored in `qa.schema_valid`
- [ ] Dockerized test runner executes and captures stdout/stderr as blobs
- [ ] Test results written to `qa.tests` with pass/fail status
- [ ] LLM judge runs with rubric prompt and returns structured scores
- [ ] Judge scores stored in `qa.judge.scores` (6 dimensions)
- [ ] Judge rationale stored as blob with `rationale_blob_id`
- [ ] Trace status transitions: `collecting` -> `finalizing` -> `complete`
- [ ] Failed QA sets status to `failed` with error details

### Data Integrity
- [ ] Events are returned ordered by `seq`
- [ ] Blob content matches `blob_id` hash
- [ ] Materialized trace JSON is valid per schema
- [ ] Redaction stubs are present (secret scan, PII mask, truncate)

### Demo Script
- [ ] `scripts/demo_e2e.py` runs end-to-end without manual intervention
- [ ] Script prints clear pass/fail for each step
- [ ] Script exits 0 on full success, non-zero on any failure
