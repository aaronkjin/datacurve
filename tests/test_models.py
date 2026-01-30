"""Tests for core/models.py, core/validation.py, and core/redaction.py.

Covers serialization round-trips, validation error cases, and redaction hooks.
"""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from core.models import (
    Actor,
    ActorKind,
    BlobRedaction,
    BlobRef,
    BugReport,
    CommitPayload,
    ConsentFlags,
    Developer,
    EditKind,
    Environment,
    ErrorPayload,
    Event,
    EventBatch,
    EventContext,
    EventType,
    ExperienceLevel,
    FileEditPayload,
    FileSnapshotPayload,
    FinalizeRequest,
    FinalState,
    IDE,
    Ingestion,
    JudgeFlag,
    JudgeOutput,
    JudgeResult,
    JudgeScores,
    NavigationPayload,
    PRFinalState,
    PRMetadataPayload,
    QA,
    QATests,
    RedactionRule,
    Repo,
    SnapshotReason,
    Task,
    TerminalCommandPayload,
    TerminalOutputPayload,
    TestInvocation,
    TestRunPayload,
    TestRunner,
    ThoughtKind,
    ThoughtPayload,
    Trace,
    TraceCreate,
    TraceCreateResponse,
    TraceStatus,
)
from core.validation import (
    TraceValidationError,
    validate_event_batch,
    validate_event_seq_monotonic,
    validate_finalize,
    validate_trace_create,
)
from core.redaction import (
    apply_redaction,
    pii_mask,
    secret_scan,
    truncate_large,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_trace_create_dict() -> dict:
    return {
        "repo": {
            "repo_id": "my-repo",
            "commit_base": "abc123",
        },
        "task": {
            "bug_report": {
                "title": "Button is broken",
                "description": "Clicking the button does nothing.",
            },
        },
        "developer": {
            "developer_id": "dev-1",
        },
        "environment": {
            "ide": {"name": "vscode"},
        },
    }


def _make_event_dict(seq: int = 1, event_type: str = "file_edit", payload: dict | None = None) -> dict:
    if payload is None:
        payload = {
            "file_path": "src/main.py",
            "edit_kind": "patch",
            "patch_blob_id": "sha256:aabbccdd",
        }
    return {
        "event_id": str(uuid.uuid4()),
        "seq": seq,
        "ts_ms": 1700000000000,
        "type": event_type,
        "actor": {"kind": "human"},
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Model serialization round-trip tests
# ---------------------------------------------------------------------------

class TestTraceCreateRoundTrip:
    def test_minimal_trace_create(self):
        data = _make_trace_create_dict()
        tc = TraceCreate.model_validate(data)
        assert tc.repo.repo_id == "my-repo"
        assert tc.developer.experience_level == ExperienceLevel.unknown
        assert tc.developer.consent_flags.store_raw_code is True

        # Round-trip
        dumped = json.loads(tc.model_dump_json())
        tc2 = TraceCreate.model_validate(dumped)
        assert tc2.repo.commit_base == tc.repo.commit_base

    def test_full_trace_create(self):
        data = _make_trace_create_dict()
        data["repo"]["remote_url"] = "https://github.com/org/repo"
        data["repo"]["default_branch"] = "main"
        data["repo"]["repo_fingerprint"] = {"tree_hash": "abc"}
        data["task"]["task_id"] = "TASK-1"
        data["task"]["labels"] = ["bug", "critical"]
        data["task"]["bug_report"]["repro_steps"] = "Click the button"
        data["task"]["bug_report"]["links"] = ["https://issue.tracker/1"]
        data["developer"]["experience_level"] = "senior"
        data["environment"]["os"] = "linux"
        data["environment"]["language"] = ["python", "typescript"]
        data["environment"]["containerized"] = True
        data["environment"]["timezone"] = "UTC"

        tc = TraceCreate.model_validate(data)
        assert tc.developer.experience_level == ExperienceLevel.senior
        assert tc.environment.language == ["python", "typescript"]


class TestEventRoundTrip:
    def test_file_edit_event(self):
        data = _make_event_dict()
        event = Event.model_validate(data)
        assert event.type == EventType.file_edit

        payload = event.validated_payload()
        assert isinstance(payload, FileEditPayload)
        assert payload.file_path == "src/main.py"

        # Round-trip
        dumped = json.loads(event.model_dump_json())
        event2 = Event.model_validate(dumped)
        assert event2.seq == event.seq

    def test_terminal_command_event(self):
        data = _make_event_dict(seq=2, event_type="terminal_command", payload={
            "cwd": "/home/user/repo",
            "command": "pytest tests/",
            "shell": "bash",
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, TerminalCommandPayload)
        assert payload.command == "pytest tests/"

    def test_terminal_output_event(self):
        data = _make_event_dict(seq=3, event_type="terminal_output", payload={
            "stream": "stdout",
            "chunk_blob_id": "sha256:1234",
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, TerminalOutputPayload)
        assert payload.is_truncated is False

    def test_test_run_event(self):
        data = _make_event_dict(seq=4, event_type="test_run", payload={
            "command": "pytest",
            "runner": "pytest",
            "exit_code": 0,
            "duration_ms": 5000,
            "passed": True,
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, TestRunPayload)
        assert payload.passed is True

    def test_thought_event(self):
        data = _make_event_dict(seq=5, event_type="thought", payload={
            "content_blob_id": "sha256:abcd",
            "kind": "hypothesis",
            "links_to": [],
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, ThoughtPayload)
        assert payload.kind == ThoughtKind.hypothesis

    def test_file_snapshot_event(self):
        data = _make_event_dict(seq=6, event_type="file_snapshot", payload={
            "file_path": "src/main.py",
            "content_blob_id": "sha256:1111",
            "snapshot_reason": "pre_test",
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, FileSnapshotPayload)
        assert payload.snapshot_reason == SnapshotReason.pre_test

    def test_commit_event(self):
        data = _make_event_dict(seq=7, event_type="commit", payload={
            "commit_sha": "abc123",
            "message": "fix: resolve button click handler",
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, CommitPayload)

    def test_pr_metadata_event(self):
        data = _make_event_dict(seq=8, event_type="pr_metadata", payload={
            "title": "Fix button",
            "description": "Fixed the click handler",
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, PRMetadataPayload)

    def test_error_event(self):
        data = _make_event_dict(seq=9, event_type="error", payload={
            "error_type": "TypeError",
            "message": "Cannot read property 'click' of undefined",
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, ErrorPayload)

    def test_navigation_event(self):
        data = _make_event_dict(seq=10, event_type="navigation", payload={
            "file_path": "src/main.py",
            "symbol": "handle_click",
            "line": 42,
        })
        event = Event.model_validate(data)
        payload = event.validated_payload()
        assert isinstance(payload, NavigationPayload)


class TestEventBatchRoundTrip:
    def test_batch_round_trip(self):
        events = [_make_event_dict(seq=i) for i in range(1, 4)]
        batch = EventBatch.model_validate({"events": events})
        assert len(batch.events) == 3

        dumped = json.loads(batch.model_dump_json())
        batch2 = EventBatch.model_validate(dumped)
        assert len(batch2.events) == 3


class TestTraceRoundTrip:
    def test_full_trace(self):
        data = {
            "trace_version": "1.0",
            "trace_id": str(uuid.uuid4()),
            "created_at_ms": 1700000000000,
            "status": "complete",
            "repo": {"repo_id": "r1", "commit_base": "abc"},
            "task": {"bug_report": {"title": "Bug", "description": "Desc"}},
            "developer": {"developer_id": "d1"},
            "environment": {"ide": {"name": "vscode"}},
            "events": [_make_event_dict(seq=1)],
            "qa": {
                "schema_valid": True,
                "tests": {
                    "runner": "pytest",
                    "invocations": [{
                        "ts_ms": 1700000000000,
                        "command": "pytest",
                        "exit_code": 0,
                        "duration_ms": 3000,
                        "passed": True,
                    }],
                    "final_passed": True,
                },
                "judge": {
                    "model": "gpt-4",
                    "scores": {
                        "root_cause_identification": 4.0,
                        "plan_quality": 3.5,
                        "experiment_iterate_loop": 4.0,
                        "use_of_signals_tests_logs": 3.0,
                        "minimality_of_fix": 5.0,
                        "clarity": 4.5,
                    },
                    "overall": 4.0,
                },
            },
        }
        trace = Trace.model_validate(data)
        assert trace.status == TraceStatus.complete
        assert trace.qa is not None
        assert trace.qa.judge is not None
        assert trace.qa.judge.scores.minimality_of_fix == 5.0

        # Round-trip
        dumped = json.loads(trace.model_dump_json())
        trace2 = Trace.model_validate(dumped)
        assert trace2.trace_id == trace.trace_id


class TestBlobRef:
    def test_valid_blob_ref(self):
        ref = BlobRef(
            blob_id="sha256:aabbccdd1122",
            content_type="text/plain",
            byte_length=1024,
            storage_uri="file:///data/blobs/sha256/aabbccdd1122",
        )
        assert ref.byte_length == 1024

    def test_invalid_blob_id_pattern(self):
        with pytest.raises(ValidationError):
            BlobRef(
                blob_id="md5:invalid",
                content_type="text/plain",
                byte_length=0,
                storage_uri="file:///x",
            )


class TestJudgeOutput:
    def test_valid_judge_output(self):
        output = JudgeOutput.model_validate({
            "scores": {
                "root_cause_identification": 4.0,
                "plan_quality": 3.5,
                "experiment_iterate_loop": 4.0,
                "use_of_signals_tests_logs": 3.0,
                "minimality_of_fix": 5.0,
                "clarity": 4.5,
            },
            "overall": 4.0,
            "rationale": "Good debugging session.",
            "flags": ["exemplary_trace"],
        })
        assert output.overall == 4.0
        assert JudgeFlag.exemplary_trace in output.flags

    def test_overall_rounded(self):
        output = JudgeOutput.model_validate({
            "scores": {
                "root_cause_identification": 4.0,
                "plan_quality": 3.5,
                "experiment_iterate_loop": 4.0,
                "use_of_signals_tests_logs": 3.0,
                "minimality_of_fix": 5.0,
                "clarity": 4.5,
            },
            "overall": 3.99,
            "rationale": "Rounded.",
            "flags": [],
        })
        assert output.overall == 4.0

    def test_score_out_of_range(self):
        with pytest.raises(ValidationError):
            JudgeOutput.model_validate({
                "scores": {
                    "root_cause_identification": 6.0,
                    "plan_quality": 3.5,
                    "experiment_iterate_loop": 4.0,
                    "use_of_signals_tests_logs": 3.0,
                    "minimality_of_fix": 5.0,
                    "clarity": 4.5,
                },
                "overall": 4.0,
                "rationale": "Bad score.",
                "flags": [],
            })

    def test_invalid_flag(self):
        with pytest.raises(ValidationError):
            JudgeOutput.model_validate({
                "scores": {
                    "root_cause_identification": 4.0,
                    "plan_quality": 3.5,
                    "experiment_iterate_loop": 4.0,
                    "use_of_signals_tests_logs": 3.0,
                    "minimality_of_fix": 5.0,
                    "clarity": 4.5,
                },
                "overall": 4.0,
                "rationale": "Unknown flag.",
                "flags": ["nonexistent_flag"],
            })


# ---------------------------------------------------------------------------
# Validation error cases
# ---------------------------------------------------------------------------

class TestValidateTraceCreate:
    def test_valid(self):
        tc = validate_trace_create(_make_trace_create_dict())
        assert tc.repo.repo_id == "my-repo"

    def test_missing_repo(self):
        data = _make_trace_create_dict()
        del data["repo"]
        with pytest.raises(TraceValidationError) as exc_info:
            validate_trace_create(data)
        body = exc_info.value.to_response_body()
        assert any("repo" in e["field"] for e in body["errors"])

    def test_missing_bug_report_title(self):
        data = _make_trace_create_dict()
        del data["task"]["bug_report"]["title"]
        with pytest.raises(TraceValidationError) as exc_info:
            validate_trace_create(data)
        body = exc_info.value.to_response_body()
        assert any("title" in e["field"] for e in body["errors"])

    def test_invalid_experience_level(self):
        data = _make_trace_create_dict()
        data["developer"]["experience_level"] = "godlike"
        with pytest.raises(TraceValidationError):
            validate_trace_create(data)


class TestValidateEventBatch:
    def test_valid_batch(self):
        data = {"events": [_make_event_dict(seq=1)]}
        batch = validate_event_batch(data)
        assert batch.events[0].type == EventType.file_edit

    def test_empty_events(self):
        with pytest.raises(TraceValidationError):
            validate_event_batch({"events": []})

    def test_too_many_events(self):
        events = [_make_event_dict(seq=i) for i in range(1, 102)]
        with pytest.raises(TraceValidationError):
            validate_event_batch({"events": events})

    def test_invalid_event_type(self):
        data = {"events": [_make_event_dict(seq=1, event_type="unknown_type")]}
        with pytest.raises(TraceValidationError):
            validate_event_batch(data)

    def test_missing_payload_field(self):
        data = {"events": [_make_event_dict(seq=1, event_type="file_edit", payload={
            "file_path": "x.py",
            # missing edit_kind and patch_blob_id
        })]}
        with pytest.raises(TraceValidationError) as exc_info:
            validate_event_batch(data)
        body = exc_info.value.to_response_body()
        assert any("payload" in e["field"] for e in body["errors"])

    def test_negative_seq(self):
        data = {"events": [_make_event_dict(seq=0)]}
        with pytest.raises(TraceValidationError):
            validate_event_batch(data)

    def test_payload_validated_per_type(self):
        """Terminal command with wrong payload shape."""
        data = {"events": [_make_event_dict(seq=1, event_type="terminal_command", payload={
            "file_path": "wrong field",
        })]}
        with pytest.raises(TraceValidationError) as exc_info:
            validate_event_batch(data)
        body = exc_info.value.to_response_body()
        assert any("payload" in e["field"] for e in body["errors"])


class TestValidateFinalize:
    def test_valid(self):
        req = validate_finalize({"final_state": {"commit_head": "abc123"}})
        assert req.final_state.commit_head == "abc123"

    def test_empty_final_state(self):
        req = validate_finalize({"final_state": {}})
        assert req.final_state.commit_head is None

    def test_missing_final_state(self):
        with pytest.raises(TraceValidationError):
            validate_finalize({})


class TestSeqMonotonic:
    def test_valid_sequence(self):
        events = [Event.model_validate(_make_event_dict(seq=i)) for i in range(1, 4)]
        validate_event_seq_monotonic(events)  # no error

    def test_valid_sequence_with_offset(self):
        events = [Event.model_validate(_make_event_dict(seq=i)) for i in range(5, 8)]
        validate_event_seq_monotonic(events, current_high=4)

    def test_non_monotonic(self):
        events = [
            Event.model_validate(_make_event_dict(seq=1)),
            Event.model_validate(_make_event_dict(seq=1)),  # duplicate
        ]
        with pytest.raises(TraceValidationError):
            validate_event_seq_monotonic(events)

    def test_seq_not_greater_than_current_high(self):
        events = [Event.model_validate(_make_event_dict(seq=3))]
        with pytest.raises(TraceValidationError):
            validate_event_seq_monotonic(events, current_high=5)


# ---------------------------------------------------------------------------
# Redaction tests
# ---------------------------------------------------------------------------

class TestSecretScan:
    def test_detects_api_key(self):
        text = 'api_key = "sk-1234567890abcdef"'
        result, modified = secret_scan(text)
        assert modified is True
        assert "sk-1234567890abcdef" not in result
        assert "[SECRET_REDACTED]" in result

    def test_detects_github_token(self):
        text = "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        result, modified = secret_scan(text)
        assert modified is True
        assert "ghp_" not in result

    def test_detects_aws_key(self):
        text = "aws_key=AKIAIOSFODNN7EXAMPLE"
        result, modified = secret_scan(text)
        assert modified is True

    def test_no_secrets(self):
        text = "hello world, this is normal code"
        result, modified = secret_scan(text)
        assert modified is False
        assert result == text


class TestPIIMask:
    def test_masks_email(self):
        text = "Contact: user@example.com for help"
        result, modified = pii_mask(text)
        assert modified is True
        assert "user@example.com" not in result
        assert "[EMAIL_REDACTED]" in result

    def test_masks_phone(self):
        text = "Call 555-123-4567 now"
        result, modified = pii_mask(text)
        assert modified is True
        assert "555-123-4567" not in result

    def test_no_pii(self):
        text = "just some code here"
        result, modified = pii_mask(text)
        assert modified is False


class TestTruncate:
    def test_no_truncation_needed(self):
        data = b"short"
        result, truncated = truncate_large(data, max_bytes=100)
        assert truncated is False
        assert result == data

    def test_truncation(self):
        data = b"x" * 200
        result, truncated = truncate_large(data, max_bytes=100)
        assert truncated is True
        assert len(result) == 100


class TestApplyRedaction:
    def test_all_rules(self):
        text = 'api_key="secret123456" user@example.com'
        result = apply_redaction(text.encode("utf-8"))
        assert result.was_modified is True
        assert RedactionRule.secret_scan in result.rules_applied
        assert RedactionRule.pii_mask in result.rules_applied

    def test_binary_skips_text_rules(self):
        data = bytes(range(256))  # non-UTF8
        result = apply_redaction(data, rules=[RedactionRule.secret_scan])
        assert result.was_modified is False

    def test_truncation_rule(self):
        data = b"x" * 2_000_000
        result = apply_redaction(data, rules=[RedactionRule.truncate_large])
        assert result.was_truncated is True
        assert len(result.content) == 1_048_576

    def test_no_modification(self):
        data = b"clean text no secrets"
        result = apply_redaction(data)
        assert result.was_modified is False
        assert result.rules_applied == []
