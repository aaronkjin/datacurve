"""Run tests in a Docker container for Celery task qa.run_tests"""

from __future__ import annotations

import logging
import time

import docker
from docker.errors import ContainerError, DockerException, ImageNotFound

from core.blob_store import LocalFsBlobStore
from core.config import settings
from core.models import QA, QATests, TestInvocation
from db.models import TraceRow
from db.session import get_sync_session
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)
blob_store = LocalFsBlobStore()


@celery_app.task(name="qa.run_tests", bind=True, max_retries=0)
def run_tests(self, trace_id: str) -> dict:
    try:
        return _run_tests_impl(trace_id)
    except Exception as exc:
        logger.exception("run_tests failed for trace %s", trace_id)
        _mark_failed(trace_id, str(exc))
        raise


def _run_tests_impl(trace_id: str) -> dict:
    with get_sync_session() as session:
        row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
        if row is None:
            raise ValueError(f"Trace not found: {trace_id}")

        # Read trace metadata for context
        final_state = row.final_state_json or {}
        repo = row.repo_json or {}

    # Run tests in Docker container
    image = settings.TEST_BASE_IMAGE
    command = settings.TEST_COMMAND
    timeout = settings.TEST_TIMEOUT_SECONDS
    memory_limit = settings.TEST_MEMORY_LIMIT

    client = docker.from_env()

    start_ms = int(time.time() * 1000)
    exit_code = 1
    stdout_bytes = b""
    stderr_bytes = b""

    try:
        container = client.containers.run(
            image=image,
            command=command,
            detach=True,
            mem_limit=memory_limit,
            network_mode="none",
            read_only=True,
            tmpfs={"/tmp": "size=64M"},
        )

        # Wait for completion with timeout
        result = container.wait(timeout=timeout)
        exit_code = result.get("StatusCode", 1)

        stdout_bytes = container.logs(stdout=True, stderr=False)
        stderr_bytes = container.logs(stdout=False, stderr=True)

        container.remove(force=True)

    except Exception as exc:
        # Handle timeout, image not found, and other Docker errors
        if isinstance(exc, (ContainerError, ImageNotFound, DockerException)):
            stderr_bytes = str(exc).encode("utf-8")
        else:
            stderr_bytes = f"Docker error: {exc}".encode("utf-8")
        exit_code = 1

    end_ms = int(time.time() * 1000)
    duration_ms = end_ms - start_ms
    passed = exit_code == 0

    # Store stdout/stderr as blobs
    stdout_blob_id = blob_store.put_bytes(stdout_bytes, "text/plain") if stdout_bytes else None
    stderr_blob_id = blob_store.put_bytes(stderr_bytes, "text/plain") if stderr_bytes else None

    # Build QA test result
    invocation = TestInvocation(
        ts_ms=start_ms,
        command=command,
        exit_code=exit_code,
        duration_ms=duration_ms,
        passed=passed,
        stdout_blob_id=stdout_blob_id,
        stderr_blob_id=stderr_blob_id,
    )

    qa_tests = QATests(
        runner=command,
        container_image=image,
        invocations=[invocation],
        final_passed=passed,
    )

    # Update trace qa_json in DB
    with get_sync_session() as session:
        row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
        if row is None:
            raise ValueError(f"Trace not found: {trace_id}")

        existing_qa = QA.model_validate(row.qa_json) if row.qa_json else QA()
        existing_qa.tests = qa_tests
        row.qa_json = existing_qa.model_dump()

    # Chain to judge task
    celery_app.send_task("qa.run_judge", args=[trace_id])

    return {"trace_id": trace_id, "passed": passed}


def _mark_failed(trace_id: str, error_msg: str) -> None:
    try:
        with get_sync_session() as session:
            row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
            if row is None:
                return
            row.status = "failed"
            existing_qa = QA.model_validate(row.qa_json) if row.qa_json else QA()
            existing_qa.schema_valid = False
            row.qa_json = {
                **existing_qa.model_dump(),
                "error": error_msg,
            }
    except Exception:
        logger.exception("Failed to mark trace %s as failed", trace_id)
