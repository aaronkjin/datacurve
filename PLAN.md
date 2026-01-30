# Project Plan

## Background & Context (Given)

Welcome! The goal of this project is to simulate a core loop of what you'll do here: design
and build a system to capture a novel, high-fidelity dataset for pushing frontier AI coding
capabilities.
The future of AI assistants for developers lies in understanding not just what code was
changed, but why and how a developer arrived at a solution. This project is your chance to
build a prototype for capturing that entire process.
Imagine a researcher from a partner AI lab has given us a brief: "We want to improve our
model’s ability to autonomously fix bugs in large repositories. We need data that captures
how skilled human developers do this. The data should include anything that could be useful
to us, such as their code edits, terminal commands, and their 'chain-of-thought' reasoning."
We want to flush out this ambiguous request and turn it into a concrete, functioning data
collection pipeline.

## Clarifying Questions

1. What exactly is the unit of data: “one PR”, “one debugging session,” or “one bug-fix
   attempt”?
   a. Why it matters: Determines trace boundaries, schema cardinalities, QA
   triggers, and what “success” means.
   b. Assumption (for MVP): One trace corresponds to one-bug fix session
   culminating in a PR (or PR-like final state). A session can have multiple
   attempts and test runs.
2. How should we treat and store raw code and outputs (privacy/IP/PII)?
   a. Why it matters: Storage costs, redaction pipeline, what we can send to an LLM
   judge, compliance.
   b. Assumption (for MVP): We store full file snapshots and terminal outputs in
   secure storage but support configurable redaction:
   i. “Raw mode” for internal/consented repos.

```
ii. “Redacted mode” (default) that strips secrets/keys, large blobs, and
optionally stores patches only.
```

3. How important is timing fidelity (milliseconds vs. seconds) and event completeness
   (keystroke-level vs. save-level)?
   a. Why it matters: Schema granularity, ingestion rate, replay fidelity, and client
   complexity.
   b. Assumption (for MVP): Event-level timeline with millisecond timestamps but
   edit events are “meaningful edits” (e.g. on save/debounce window) rather than
   per-keystroke. The schema still supports high-frequency edits if the IDE
   provides them.
4. What downstream use is primary: training data for supervised fine-tuning, RL-style
   trajectories, or evaluation?
   a. Why it matters: How we normalize traces, what labels we compute, and how
   we package reasoning.
   b. Assumption (for MVP): Dual use:
   i. Trajectory-style (ordered events + outcomes) for agent training.
   ii. Evaluation-ready (tests pass/fail + judge rubric scores).
5. What does the researcher want as “chain-of-thought”: verbatim private thoughts,
   structured rationales, or lightweight annotations?
   a. Why it matters: Safety, privacy, and whether we collect sensitive internal
   reasoning vs. “explanations.”
   b. Assumption (for MVP): Collect developer-authored “thought events” as text;
   do not require verbatim private chain-of-thought. We treat “reasoning” as an
   explanation artifact aligned to actions (edit/test), which is consistent with best
   practice of tying rationale to edits.

# Proposed Data Schema

Our design goals:
● Event-sourced: A single ordered event log reconstructs the session timeline.
● Incremental ingestion-friendly: Append events over time; finalize later.
● Replay + training ready: References to code state (snapshots/patches) and command
outputs.
● Scalable storage: Large blobs are content-addressed and stored out-of-line.

JSON
Top-level schema (conceptual):
{
"trace_version": "1.0",
"trace_id": "uuid",
"created_at_ms": 0 ,
"finalized_at_ms": 0 ,
"repo": {
"repo_id": "string",
"remote_url": "string|null",
"default_branch": "string|null",
"commit_base": "git_sha",
"repo_fingerprint": {
"tree_hash": "string|null",
"dependencies_lock_hash": "string|null"
}
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

},
"ingestion": {
"mode": "batch|incremental",
"client_session_id": "string",
"seq_last": 0 ,
"dedupe_policy": "event_id",
"clock_skew_ms_est": 0
},
"artifacts": {
"blobs": [
{
"blob_id": "sha256:...",
"content_type":
"text/plain|application/json|application/gzip|application/octet-stream",
"byte_length": 0 ,
"storage_uri": "s3://...|file://...|db://...",
"redaction": {
"applied": true,
"rules": ["secret_scan", "pii_mask", "truncate_large"]
}
}
]
},
"events": [
{
"event_id": "uuid",
"seq": 1 ,
"ts_ms": 0 ,
"type":
"file_edit|file_snapshot|terminal_command|terminal_output|test_run|debug_action|navigation
|thought|commit|pr_metadata|error",
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
],
"final_state": {
"commit_head": "git_sha|null",
"pr": {
"title": "string|null",

"description": "string|null",
"diff_blob_id": "sha256:...|null"
}
},
"qa": {
"schema_valid": true,
"tests": {
"runner": "string",
"container_image": "string|null",
"invocations": [
{
"invocation_id": "uuid",
"ts_ms": 0 ,
"command": "string",
"exit_code": 0 ,
"duration_ms": 0 ,
"passed": true,
"report_blob_id": "sha256:...|null",
"stdout_blob_id": "sha256:...|null",
"stderr_blob_id": "sha256:...|null"
}
],
"final_passed": true
},
"judge": {
"model": "string",
"rubric_version": "1.0",
"scores": {
"root_cause_identification": 0. 0 ,
"plan_quality": 0. 0 ,
"experiment_iterate_loop": 0. 0 ,
"use_of_signals_tests_logs": 0. 0 ,
"minimality_of_fix": 0. 0 ,
"clarity": 0. 0
},
"overall": 0. 0 ,
"rationale_blob_id": "sha256:...|null",
"flags": ["hallucination_risk", "missing_steps", "unsafe_suggestion"]
}
}
}
Core event payloads:

1. file_edit: Captures a meaningful edit as a patch and anchors it to a file + optional
   pre/post state.

JSON
JSON
JSON
JSON
{
"file_path": "string",
"edit_kind": "patch|replace_range|keystroke_batch",
"patch_format": "unified_diff",
"patch_blob_id": "sha256:...",
"pre_hash": "sha256:...|null",
"post_hash": "sha256:...|null",
"selection": { "start": [line, col], "end": [line, col] },
"reason_ref": "uuid|null"
}
Justification:
a. Patches keep traces smaller than full snapshots while still allowing replay/diff
training.
b. pre_hash / post_hash support integrity and dedupe.

2. file_snapshot: Stores full content at key checkpoints (e.g. before/after test runs).
   {
   "file_path": "string",
   "content_blob_id": "sha256:...",
   "snapshot_reason": "pre_test|post_test|manual_checkpoint"
   }
   Justification:
   a. Snapshots at checkpoints allow deterministic reconstruction without storing
   every keystroke.
3. terminal_command + terminal_output: Commands and outputs are separate so we
   can stream output incrementally.
   { "cwd": "string", "command": "string", "shell": "bash|zsh|pwsh|cmd", "env_hash":
   "string|null" }
   { "stream": "stdout|stderr", "chunk_blob_id": "sha256:...", "is_truncated": false }
4. test_run: A normalized wrapper event for “ran tests” regardless of runner.

JSON
JSON
{
"command": "string",
"runner": "pytest|go test|npm test|make test|custom",
"exit_code": 0 ,
"duration_ms": 0 ,
"passed": true,
"report_blob_id": "sha256:...|null"
}
Justification:
a. Tests as first-class events are central for bug-fix traces and deterministic QA.

5. thought: A developer-authored rationale note (or IDE-collected note) tied to a point
   in time.
   {
   "content_blob_id": "sha256:...",
   "kind": "hypothesis|plan|interpretation|decision|postmortem",
   "links_to": ["event_id1", "event_id2"]
   }
   Justification:
   a. Keeps reasoning aligned to actions (edit/test) rather than a monolithic essay,
   which improves training signal.

## High-Level Technical Plan

Constraints to consider:
● Backend service accepts JSON in our format, persists traces, supports incremental
ingestion (bonus).
● Runs QA pipeline: Docker test suite + LLM judge; writes results back into the trace.
MVP stack:
● API: Python FastAPI
● DB: Postgres (or SQLite for local-only MVP), with a clean storage interface
● Blob store: Local filesystem volume in Docker Compose (MVP), S3-compatible if
considering for the future
**●** Async jobs: Celery + Redis (or a lightweight background worker queue)
**●** QA runner: Docker-inDocker or host Docker socket; run tests in isolated container

**●** LLM judge: Pluggable client; store request/response artifacts as blobs
System components:

1. Ingestion API service
   a. Responsibilities:
   i. Create trace (metadata + base repo/task info)
   ii. Append events (incremental ingestion)
   iii. Finalize trace (mark complete, trigger QA)
   iv. Fetch trace (for debugging/demo)
   b. Planned endpoints:
   i. POST /traces: returns the trace_id
   ii. POST /traces/{trace_id}/events: Accepts {events: [...]}; enforces required
   fields, monotonic seq, idempotency by event_id
   iii. POST /traces/{trace_id}/finalize: Triggers QA
   iv. GET /traces/{trace_id}: Returns full assembled trace (or metadata-only
   option)
   c. Storage model:
   i. Table traces (metadata, status, created/finalized timestamps)
   ii. Table events (trace_id, seq, ts_ms, type, payload_jsonb, event_id
   unique)
   iii. Table blobs (blob_id, storage_uri, content_type, byte_len,
   redaction_json)
   d. Key implementation details:
   i. Idempotency: Reject duplicates by (trace_id, event_id) unique
   constraint.
   ii. Ordering: Accept out-of-order timestamps but require increasing seq
   per trace (client controls seq).
   iii. Size management: Any large string fields are uploaded/accepted as
   blobs (API can accept inline and convert).
2. QA worker service
   Triggered on finalize; performs:
   a. Schema validation: Ensure required top-level fields exist; validate event types.
   b. Repo validation (tests):
   i. Checkout base commit (or used submitted final state)
   ii. Apply final patch/checkout head (depending on trace content)
   iii. Run docker build / docker run test command in isolated container.
   iv. Persist stdout/stderr/report as blobs; write qa.tests.\*
   c. LLM judge:

```
i. Build a compact “judge packet”:
```

1. Bug report summary
2. Key event excerpts: thought events, test outcomes, final diff
   summary
3. Optionally, small relevant file snippets (redacted)
   ii. Call judge model with rubric prompt
   iii. Parse JSON output into qa.judge.scores + overall + rationale blob
   d. Update trace: Write QA results into DB and/or materialize final JSON
   document
   Notes:
   ● Deterministic test evaluation in Docker is central to QA reliability.
   ● LLM-as-judge should be treated as a heuristic signal; store rationale for audit.
4. Trace materializer (optional but useful)
   Produce a single “final JSON”:
   a. Reads traces + events + blobs + qa
   b. Embeds blobs by reference (default) or inline (for small data)
   c. Writes final_trace.json to blob store and stores its blob_id in final_state
   Task breakdown (for coding agents):
5. Subagent 1 (schema and validation):
   a. Define Pydantic models for:
   i. Trace metadata
   ii. Event union types + payload schemas
   b. Implement JSON schema validation + error reporting
   c. Implement redaction hooks (stub rules; enforce max sizes)
6. Subagent 2 (storage layer):
   a. DB models + migrations:
   i. Traces, events, blobs
   b. Blob store abstraction:
   i. put_bytes() -> blob_id
   ii. get_uri(blob_id)
   c. Dedupe by content hash
7. Subagent 3 (ingestion API):
   a. Build FastAPI routes:
   i. Create trace
   ii. Append events (batch)
   iii. Finalize
   iv. Get trace

```
b. Idempotency + seq enforcement
c. Basic auth token or per-trace ingest key (simple)
```

4. Subagent 4 (QA test runner):
   a. Implement dockerized test execution:
   i. Choose a sample repo in the demo
   ii. Run command, capture outputs
   b. Persist results back to qa.tests
   c. Hardening:
   i. Timeouts
   ii. Resource limits
   iii. No-network mode, if feasible
5. Subagent 5 (LLM judge):
   a. Define rubric + prompt template
   b. Implement judge client interface:
   i. judge(trace_packet) -> {scores, overall, rationale}
   c. Store judge rationale as blob
   d. Parsing + failure handling (fallback score + flag)
6. Subagent 6 (end-to-end demo + compose):
   a. docker-compose.yml with:
   i. api, worker, postgres, redis, (optional) minio
   b. One-command startup
   c. Provide a script that:
   i. Creates a trace
   ii. Appends a small event set
   iii. Finalizes
   iv. Fetches final JSON
   LLM judge rubric (what the judge should evaluate):
   Scores (0-5) and brief definitions:
7. Root-cause identification: Did they correctly locate the bug cause?
8. Plan quality: Clear hypothesis → test → iterate loop
9. Use of signals: Logs, stack traces, tests meaningfully used
10. Minimality: Fix is targeted, avoids unrelated refactors
11. Correctness confidence: Reasoning supports why fix works
12. Clarity: Reasoning is interpretable and grounded
    Output format required from judge: strict JSON with numeric scores + short rationale + flags.
    Model choice:

```
● Implement as configurable via env var (JUDGE_MODEL), defaulting a strong
frontier reasoning model available. The key is deterministic-ish scoring (temperature
set to 0) and structured JSON output.
```

## Scope & Tradeoffs

### MVP:

● Accept trace creation + event ingestion (batch and/or incremental)
● Persist to DB + blob store
● Finalize triggers QA:
○ Dockerized test run and results stored back into trace
○ LLM judge scoring + rationale stored back into trace
○ One-command docker-compose up + one E2E demo path
Trade-offs for robustness:
● Edit granularity: Save-level/patch-level edits vs keystroke-level (de-scoped for MVP)
● Privacy: Implement basic secret scanning + truncation; full PII detection and
advanced anonymization de-scoped
● Replay UI: No replay viewer; instead provide a deterministic JSON artifact + minimal
viewer endpoint
● Scalability: No Kafka; single API + worker is sufficient for our current scale (but, for
the future, should evolve later)
● Multi-repo generalized runners: MVP targets one demo repo/test command;
extensible config structure provided
Nice-to-haves (but explicitly descoped):
● Eye gaze/screen recording capture (high cost/low necessity for MVP)
● Fully automated extraction of “reasoning” from arbitrary IDE context
● Advanced event compression/session stitching across multiple machines
● Multi-judge consensus and calibration set

## Accompanying Research

[Datacurve Take-Home] Research - High-Fidelity Coding Telemetry Pipelines: Recent Develop...
(https://docs.google.com/document/d/1bqx7GxoV722GcN1BxKMTYL8md0i7iHHS/edit)
