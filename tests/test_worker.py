"""Tests for QA worker tasks (test_runner + finalize_qa) via mocked Docker"""

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
from unittest.mock import MagicMock, patch

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
def trace_id(sync_session: Session) -> str:
    # Create a trace in 'finalizing' state and return its ID
    tid = str(uuid.uuid4())
    row = TraceRow(
        trace_id=tid,
        status="finalizing",
        repo_json={"repo_id": "test-repo", "commit_base": "abc123"},
        task_json={"bug_report": {"title": "Bug", "description": "Desc"}, "labels": []},
        developer_json={"developer_id": "dev-1", "experience_level": "unknown", "consent_flags": {}},
        environment_json={"ide": {"name": "vscode"}, "language": [], "containerized": False},
        final_state_json={"commit_head": "def456"},
        created_at_ms=int(time.time() * 1000),
        finalized_at_ms=int(time.time() * 1000),
    )
    sync_session.add(row)
    sync_session.commit()
    return tid


def _patch_sync_session(sync_session_factory):
    # Return a context manager patch for get_sync_session that uses our test factory
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

    return patch("worker.tasks.test_runner.get_sync_session", _test_sync_session)


def _patch_finalize_sync_session(sync_session_factory):
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

    return patch("worker.tasks.finalize_qa.get_sync_session", _test_sync_session)


# ---------------------------------------------------------------------------
# Mock Docker helpers
# ---------------------------------------------------------------------------

def _make_mock_container(exit_code: int = 0, stdout: bytes = b"PASSED", stderr: bytes = b""):
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}
    container.logs.side_effect = lambda stdout=True, stderr=True: stdout if stdout else stderr
    # More precise side_effect
    def logs_fn(stdout=True, stderr=True):
        if stdout and not stderr:
            return b"PASSED"
        if stderr and not stdout:
            return b""
        return b"PASSED"
    container.logs.side_effect = None
    container.logs = MagicMock(side_effect=lambda stdout=True, stderr=False: (
        b"PASSED" if stdout else b""
    ))
    return container


def _make_mock_docker_client(container=None, raise_on_run=None):
    client = MagicMock()
    if raise_on_run:
        client.containers.run.side_effect = raise_on_run
    else:
        client.containers.run.return_value = container or _make_mock_container()
    return client


# ---------------------------------------------------------------------------
# Tests: run_tests
# ---------------------------------------------------------------------------

def test_run_tests_success(sync_session_factory, trace_id, tmp_path):
    # run_tests loads trace, calls Docker, stores blobs, updates qa_json
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs = MagicMock(side_effect=lambda stdout=True, stderr=False: (
        b"All tests passed" if stdout else b""
    ))
    container.remove = MagicMock()

    mock_client = MagicMock()
    mock_client.containers.run.return_value = container

    from core.blob_store import LocalFsBlobStore
    blob_store = LocalFsBlobStore(root=tmp_path / "blobs")

    with (
        _patch_sync_session(sync_session_factory),
        patch("worker.tasks.test_runner.docker") as mock_docker,
        patch("worker.tasks.test_runner.blob_store", blob_store),
        patch("worker.tasks.test_runner.celery_app") as mock_celery,
    ):
        mock_docker.from_env.return_value = mock_client

        from worker.tasks.test_runner import _run_tests_impl
        result = _run_tests_impl(trace_id)

    assert result["trace_id"] == trace_id
    assert result["passed"] is True

    # Verify Docker was called with expected args
    mock_client.containers.run.assert_called_once()
    call_kwargs = mock_client.containers.run.call_args
    assert call_kwargs.kwargs["network_mode"] == "none"
    assert call_kwargs.kwargs["read_only"] is True
    assert call_kwargs.kwargs["mem_limit"] == "512m"

    # Verify qa_json was updated
    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
    assert row.qa_json is not None
    assert row.qa_json["tests"]["final_passed"] is True
    assert len(row.qa_json["tests"]["invocations"]) == 1
    inv = row.qa_json["tests"]["invocations"][0]
    assert inv["exit_code"] == 0
    assert inv["stdout_blob_id"] is not None
    session.close()

    # Verify judge task was dispatched
    mock_celery.send_task.assert_called_once_with("qa.run_judge", args=[trace_id])


def test_run_tests_docker_timeout(sync_session_factory, trace_id, tmp_path):
    # Docker timeout is handled and trace status set to failed
    import docker.errors

    mock_client = MagicMock()
    # Simulate container that times out
    container = MagicMock()
    container.wait.side_effect = Exception("Connection timed out")
    container.logs = MagicMock(return_value=b"")
    container.remove = MagicMock()
    mock_client.containers.run.return_value = container

    from core.blob_store import LocalFsBlobStore
    blob_store = LocalFsBlobStore(root=tmp_path / "blobs")

    with (
        _patch_sync_session(sync_session_factory),
        patch("worker.tasks.test_runner.docker") as mock_docker,
        patch("worker.tasks.test_runner.blob_store", blob_store),
        patch("worker.tasks.test_runner.celery_app") as mock_celery,
    ):
        mock_docker.from_env.return_value = mock_client

        from worker.tasks.test_runner import _run_tests_impl
        result = _run_tests_impl(trace_id)

    assert result["passed"] is False

    # Verify qa_json was updated with failure
    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
    assert row.qa_json is not None
    assert row.qa_json["tests"]["final_passed"] is False
    session.close()


def test_run_tests_docker_error_marks_failed(sync_session_factory, trace_id, tmp_path):
    # Docker connection error marks trace as failed
    from docker.errors import DockerException

    mock_client = MagicMock()
    mock_client.containers.run.side_effect = DockerException("Cannot connect to Docker daemon")

    from core.blob_store import LocalFsBlobStore
    blob_store = LocalFsBlobStore(root=tmp_path / "blobs")

    with (
        _patch_sync_session(sync_session_factory),
        patch("worker.tasks.test_runner.docker") as mock_docker,
        patch("worker.tasks.test_runner.blob_store", blob_store),
        patch("worker.tasks.test_runner.celery_app") as mock_celery,
    ):
        mock_docker.from_env.return_value = mock_client

        from worker.tasks.test_runner import _run_tests_impl
        result = _run_tests_impl(trace_id)

    assert result["passed"] is False

    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
    assert row.qa_json is not None
    assert row.qa_json["tests"]["final_passed"] is False
    inv = row.qa_json["tests"]["invocations"][0]
    assert inv["stderr_blob_id"] is not None
    session.close()


# ---------------------------------------------------------------------------
# Tests: finalize_qa
# ---------------------------------------------------------------------------

# finalize_qa sets status to complete when qa.tests and qa.judge are present
def test_finalize_qa_sets_complete(sync_session_factory, trace_id):
    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
    
    # First populate qa_json with both tests and judge
    row.qa_json = {
        "schema_valid": True,
        "tests": {
            "runner": "pytest",
            "container_image": "python:3.11-slim",
            "invocations": [],
            "final_passed": True,
        },
        "judge": {
            "model": "test-model",
            "rubric_version": "1.0",
            "scores": {
                "root_cause_identification": 4.0,
                "plan_quality": 4.0,
                "experiment_iterate_loop": 3.5,
                "use_of_signals_tests_logs": 4.0,
                "minimality_of_fix": 3.0,
                "clarity": 4.5,
            },
            "overall": 3.8,
            "rationale_blob_id": None,
            "flags": [],
        },
    }
    session.commit()
    session.close()

    with _patch_finalize_sync_session(sync_session_factory):
        from worker.tasks.finalize_qa import _finalize_qa_impl
        result = _finalize_qa_impl(trace_id)

    assert result["status"] == "complete"

    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
    assert row.status == "complete"
    session.close()


def test_finalize_qa_missing_judge_raises(sync_session_factory, trace_id):
    # finalize_qa raises when qa.judge is missing
    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
    row.qa_json = {
        "schema_valid": True,
        "tests": {
            "runner": "pytest",
            "container_image": "python:3.11-slim",
            "invocations": [],
            "final_passed": True,
        },
        "judge": None,
    }
    session.commit()
    session.close()

    with _patch_finalize_sync_session(sync_session_factory):
        from worker.tasks.finalize_qa import _finalize_qa_impl
        with pytest.raises(ValueError, match="missing qa.judge"):
            _finalize_qa_impl(trace_id)


def test_finalize_qa_missing_tests_raises(sync_session_factory, trace_id):
    # finalize_qa raises when qa.tests is missing
    session = sync_session_factory()
    row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
    row.qa_json = {
        "schema_valid": True,
        "tests": None,
        "judge": None,
    }
    session.commit()
    session.close()

    with _patch_finalize_sync_session(sync_session_factory):
        from worker.tasks.finalize_qa import _finalize_qa_impl
        with pytest.raises(ValueError, match="missing qa.tests"):
            _finalize_qa_impl(trace_id)
