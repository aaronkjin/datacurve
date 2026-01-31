"""Tests for QA worker task qa.run_judge via OpenAI API calls"""

from __future__ import annotations

import os
import uuid

# Set sync DB URL to SQLite before importing project modules
os.environ["DATABASE_URL_SYNC"] = "sqlite://"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import sqlite3

# Register UUID adapter so SQLite can bind uuid.UUID objects as strings
sqlite3.register_adapter(uuid.UUID, str)

import time
from unittest.mock import patch

import pytest
from sqlalchemy import JSON, Integer, String, create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, BlobRow, EventRow, TraceRow

# ---------------------------------------------------------------------------
# Patch PostgreSQL-specific column types for SQLite compatibility
# ---------------------------------------------------------------------------

_JSONB_COLUMNS = [
    TraceRow.repo_json, TraceRow.task_json, TraceRow.developer_json,
    TraceRow.environment_json, TraceRow.ingestion_json,
    TraceRow.final_state_json, TraceRow.qa_json,
    EventRow.actor_json, EventRow.context_json, EventRow.payload_json,
    BlobRow.redaction_json,
]

for _col in _JSONB_COLUMNS:
    _col.property.columns[0].type = JSON()

for _col in [TraceRow.trace_id, EventRow.trace_id, EventRow.event_id]:
    _col.property.columns[0].type = String(36)

EventRow.id.property.columns[0].type = Integer()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sync_engine():
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def sync_session_factory(sync_engine):
    return sessionmaker(sync_engine, expire_on_commit=False)


@pytest.fixture
def sync_session(sync_session_factory):
    session = sync_session_factory()
    yield session
    session.close()


@pytest.fixture
def trace_id_with_events(sync_session: Session) -> str:
    # Create a trace in 'finalizing' state with events and test results
    tid = str(uuid.uuid4())
    row = TraceRow(
        trace_id=tid,
        status="finalizing",
        repo_json={"repo_id": "test-repo", "commit_base": "abc123"},
        task_json={
            "bug_report": {
                "title": "Login button not working on mobile",
                "description": "Users cannot click the login button on mobile devices. The button appears but does not respond to touch events.",
                "repro_steps": "1. Open app on mobile device\n2. Navigate to login page\n3. Tap login button\n4. Nothing happens",
                "expected": "Login modal should appear when button is tapped",
                "actual": "No response to touch events on the login button",
            },
            "labels": ["bug", "mobile", "high-priority"],
        },
        developer_json={"developer_id": "dev-1", "experience_level": "senior", "consent_flags": {}},
        environment_json={"ide": {"name": "vscode"}, "language": ["typescript", "react"], "containerized": False},
        final_state_json={
            "commit_head": "def456",
            "pr": {
                "title": "Fix mobile login button touch events",
                "description": "Added onTouchStart handler alongside onClick for mobile compatibility",
            },
        },
        qa_json={
            "schema_valid": True,
            "tests": {
                "runner": "jest",
                "container_image": "node:18-slim",
                "invocations": [
                    {
                        "invocation_id": str(uuid.uuid4()),
                        "ts_ms": int(time.time() * 1000),
                        "command": "npm test",
                        "exit_code": 0,
                        "duration_ms": 5000,
                        "passed": True,
                    }
                ],
                "final_passed": True,
            },
        },
        created_at_ms=int(time.time() * 1000),
        finalized_at_ms=int(time.time() * 1000),
    )
    sync_session.add(row)
    sync_session.commit()

    # Add realistic events showing debugging process
    events = [
        EventRow(
            trace_id=tid,
            event_id=str(uuid.uuid4()),
            seq=1,
            ts_ms=int(time.time() * 1000),
            type="thought",
            actor_json={"kind": "human", "id": "dev-1"},
            payload_json={
                "content_blob_id": "",
                "kind": "hypothesis",
                "links_to": [],
            },
        ),
        EventRow(
            trace_id=tid,
            event_id=str(uuid.uuid4()),
            seq=2,
            ts_ms=int(time.time() * 1000) + 1000,
            type="file_edit",
            actor_json={"kind": "human", "id": "dev-1"},
            payload_json={
                "file_path": "src/components/LoginButton.tsx",
                "edit_kind": "patch",
                "patch_blob_id": "sha256:abc123",
            },
        ),
        EventRow(
            trace_id=tid,
            event_id=str(uuid.uuid4()),
            seq=3,
            ts_ms=int(time.time() * 1000) + 2000,
            type="test_run",
            actor_json={"kind": "ide", "id": None},
            payload_json={
                "command": "npm test",
                "runner": "npm test",
                "exit_code": 0,
                "duration_ms": 3000,
                "passed": True,
            },
        ),
    ]
    for event in events:
        sync_session.add(event)
    sync_session.commit()

    return tid


def _patch_judge_sync_session(sync_session_factory):
    # Return a context manager patch for get_sync_session for judge module
    from contextlib import contextmanager

    @contextmanager
    def _test_sync_session():
        session = sync_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return patch("worker.tasks.judge.get_sync_session", _test_sync_session)


# ---------------------------------------------------------------------------
# Tests: run_judge with real OpenAI API
# ---------------------------------------------------------------------------

def test_run_judge_success_real_api(sync_session_factory, trace_id_with_events, tmp_path):
    # Test successful judge execution with real OpenAI API call
    from core.blob_store import LocalFsBlobStore
    blob_store = LocalFsBlobStore(root=tmp_path / "blobs")

    with (
        _patch_judge_sync_session(sync_session_factory),
        patch("worker.tasks.judge.blob_store", blob_store),
        patch("worker.tasks.judge.celery_app") as mock_celery,
    ):
        from worker.tasks.judge import _run_judge_impl
        result = _run_judge_impl(trace_id_with_events)

    # Verify result structure
    assert result["trace_id"] == trace_id_with_events
    assert isinstance(result["overall"], float)
    assert 0.0 <= result["overall"] <= 5.0
    assert isinstance(result["flags"], list)

    # Verify qa_json.judge was updated
    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id_with_events).first()
    assert row.qa_json is not None
    assert row.qa_json["judge"] is not None
    
    judge = row.qa_json["judge"]
    assert judge["model"] == "gpt-5.2"
    assert judge["rubric_version"] == "1.0"
    
    # Verify all scores are present and in valid range
    scores = judge["scores"]
    assert "root_cause_identification" in scores
    assert "plan_quality" in scores
    assert "experiment_iterate_loop" in scores
    assert "use_of_signals_tests_logs" in scores
    assert "minimality_of_fix" in scores
    assert "clarity" in scores
    
    for score_name, score_value in scores.items():
        assert 0.0 <= score_value <= 5.0, f"Score {score_name} out of range: {score_value}"
    
    # Verify overall is in valid range
    assert 0.0 <= judge["overall"] <= 5.0
    
    # Verify rationale was stored as blob
    assert judge["rationale_blob_id"] is not None
    assert judge["rationale_blob_id"].startswith("sha256:")
    
    rationale = blob_store.get_bytes(judge["rationale_blob_id"]).decode("utf-8")
    assert len(rationale) > 0  # Should have some explanation
    
    # Verify flags is a list (may be empty or have valid flags)
    assert isinstance(judge["flags"], list)
    valid_flags = ["hallucination_risk", "missing_steps", "unsafe_suggestion", "incomplete_fix", "exemplary_trace"]
    for flag in judge["flags"]:
        assert flag in valid_flags, f"Invalid flag: {flag}"
    
    session.close()

    # Verify finalize_qa task was dispatched
    mock_celery.send_task.assert_called_once_with("qa.finalize_qa", args=[trace_id_with_events])


def test_run_judge_trace_not_found(sync_session_factory, tmp_path):
    # Test that non-existent trace raises error
    fake_trace_id = str(uuid.uuid4())

    from core.blob_store import LocalFsBlobStore
    blob_store = LocalFsBlobStore(root=tmp_path / "blobs")

    with (
        _patch_judge_sync_session(sync_session_factory),
        patch("worker.tasks.judge.blob_store", blob_store),
        patch("worker.tasks.judge.celery_app"),
    ):
        from worker.tasks.judge import _run_judge_impl

        with pytest.raises(ValueError, match="Trace not found"):
            _run_judge_impl(fake_trace_id)


# ---------------------------------------------------------------------------
# Tests: build_judge_packet (no API calls needed)
# ---------------------------------------------------------------------------

def test_build_judge_packet_includes_bug_report():
    # Test that judge packet includes bug report details
    from worker.tasks.judge import _build_judge_packet

    packet = _build_judge_packet(
        task_json={
            "bug_report": {
                "title": "Test Bug",
                "description": "Bug description",
                "repro_steps": "Step 1",
                "expected": "Expected behavior",
                "actual": "Actual behavior",
            }
        },
        events_data=[],
        final_state_json={},
        qa_json={},
    )

    assert "Test Bug" in packet
    assert "Bug description" in packet
    assert "Step 1" in packet
    assert "Expected behavior" in packet
    assert "Actual behavior" in packet


def test_build_judge_packet_includes_events():
    # Test that judge packet includes event summaries
    from worker.tasks.judge import _build_judge_packet

    packet = _build_judge_packet(
        task_json={"bug_report": {"title": "Bug", "description": "Desc"}},
        events_data=[
            {
                "seq": 1,
                "ts_ms": 1000,
                "type": "file_edit",
                "payload_json": {"file_path": "test.py", "edit_kind": "patch"},
            },
            {
                "seq": 2,
                "ts_ms": 2000,
                "type": "test_run",
                "payload_json": {"command": "pytest", "exit_code": 0, "passed": True, "duration_ms": 100},
            },
        ],
        final_state_json={},
        qa_json={},
    )

    assert "file_edit" in packet
    assert "test.py" in packet
    assert "test_run" in packet
    assert "pytest" in packet


def test_build_judge_packet_includes_test_results():
    # Test that judge packet includes test results
    from worker.tasks.judge import _build_judge_packet

    packet = _build_judge_packet(
        task_json={"bug_report": {"title": "Bug", "description": "Desc"}},
        events_data=[],
        final_state_json={},
        qa_json={
            "tests": {
                "runner": "pytest",
                "final_passed": True,
                "invocations": [
                    {"command": "pytest", "exit_code": 0, "passed": True, "duration_ms": 5000}
                ],
            }
        },
    )

    assert "Test Results" in packet
    assert "pytest" in packet
    assert "final_passed" in packet.lower() or "Final passed" in packet


def test_build_judge_packet_includes_final_state():
    # Test that judge packet includes final state details
    from worker.tasks.judge import _build_judge_packet

    packet = _build_judge_packet(
        task_json={"bug_report": {"title": "Bug", "description": "Desc"}},
        events_data=[],
        final_state_json={
            "commit_head": "abc123def456",
            "pr": {
                "title": "Fix the bug",
                "description": "This PR fixes the issue by...",
            },
        },
        qa_json={},
    )

    assert "Final State" in packet
    assert "abc123def456" in packet
    assert "Fix the bug" in packet
