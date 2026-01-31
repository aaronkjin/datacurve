#!/usr/bin/env python3
"""E2E demo script

Usage:
    # Terminal 1: Start services
    docker-compose up --build

    # Terminal 2: Run demo (after services are healthy)
    python scripts/demo_e2e.py

Environment variables:
    API_URL - Base URL for the API (default: http://localhost:8000)
"""

from __future__ import annotations

import io
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_URL = os.getenv("API_URL", "http://localhost:8000")
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 180
STARTUP_RETRY_SECONDS = 30
STARTUP_RETRY_INTERVAL = 2

# ---------------------------------------------------------------------------
# ANSI colors for terminal output
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log_info(msg: str) -> None:
    print(f"[{timestamp()}] {msg}")


def log_success(msg: str) -> None:
    print(f"[{timestamp()}] {GREEN}✓ {msg}{RESET}")


def log_fail(msg: str) -> None:
    print(f"[{timestamp()}] {RED}✗ {msg}{RESET}")


def log_warning(msg: str) -> None:
    print(f"[{timestamp()}] {YELLOW}⚠ {msg}{RESET}")


def log_status(msg: str) -> None:
    print(f"[{timestamp()}] {CYAN}Status: {msg}{RESET}")


# ---------------------------------------------------------------------------
# Sample data for demo
# ---------------------------------------------------------------------------

SAMPLE_PATCH = """\
--- a/src/utils/calculator.py
+++ b/src/utils/calculator.py
@@ -10,7 +10,7 @@ class Calculator:
     def divide(self, a: float, b: float) -> float:
-        return a / b
+        if b == 0:
+            raise ValueError("Cannot divide by zero")
+        return a / b
"""

SAMPLE_THOUGHT = """\
Looking at the stack trace, the ZeroDivisionError is raised in calculator.py line 11.

Hypothesis: The divide() method doesn't handle the case where b=0.

Plan:
1. Add a check for b == 0 before division
2. Raise a descriptive ValueError instead of letting Python raise ZeroDivisionError
3. Run tests to verify the fix handles edge cases
"""

SAMPLE_TERMINAL_OUTPUT = """\
============================= test session starts ==============================
platform linux -- Python 3.11.0, pytest-7.4.0, pluggy-1.3.0
rootdir: /workspace
collected 15 items

tests/test_calculator.py::test_add PASSED
tests/test_calculator.py::test_subtract PASSED  
tests/test_calculator.py::test_multiply PASSED
tests/test_calculator.py::test_divide PASSED
tests/test_calculator.py::test_divide_by_zero PASSED

============================= 5 passed in 0.12s ================================
"""


def get_sample_trace_create() -> dict[str, Any]:
    return {
        "repo": {
            "repo_id": "demo-org/calculator-app",
            "remote_url": "https://github.com/demo-org/calculator-app.git",
            "default_branch": "main",
            "commit_base": "a1b2c3d4e5f6789012345678901234567890abcd",
        },
        "task": {
            "task_id": "BUG-1234",
            "bug_report": {
                "title": "ZeroDivisionError when dividing by zero",
                "description": "The calculator crashes with ZeroDivisionError when a user attempts to divide by zero. Expected behavior is a graceful error message.",
                "repro_steps": "1. Open calculator\n2. Enter 10 / 0\n3. Click equals\n4. App crashes",
                "expected": "Display error message: 'Cannot divide by zero'",
                "actual": "Application crashes with ZeroDivisionError",
                "links": ["https://github.com/demo-org/calculator-app/issues/1234"],
            },
            "labels": ["bug", "crash", "high-priority"],
        },
        "developer": {
            "developer_id": "dev-demo-123",
            "experience_level": "senior",
            "consent_flags": {
                "store_raw_code": True,
                "store_terminal_output": True,
                "allow_llm_judge": True,
            },
        },
        "environment": {
            "os": "Linux",
            "ide": {"name": "VSCode", "version": "1.85.0"},
            "language": ["python"],
            "containerized": True,
            "timezone": "UTC",
        },
    }


def get_sample_final_state() -> dict[str, Any]:
    return {
        "final_state": {
            "commit_head": "b2c3d4e5f6789012345678901234567890abcdef",
            "pr": {
                "title": "Fix ZeroDivisionError in calculator divide method",
                "description": "Added validation to check for zero divisor before performing division. Raises ValueError with descriptive message instead of crashing.",
                "diff_blob_id": None,
            },
        }
    }


# ---------------------------------------------------------------------------
# Demo step implementations
# ---------------------------------------------------------------------------

class DemoRunner:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)
        self.trace_id: str | None = None
        self.blob_ids: dict[str, str] = {}
        self.event_ids: list[str] = []
        self.qa_job_id: str | None = None
        self.failed = False

    def run(self) -> bool:
        log_info(f"{BOLD}Starting E2E demo...{RESET}")
        log_info(f"API URL: {self.base_url}")
        print()

        steps = [
            ("Health check", self.step_health_check),
            ("Create trace", self.step_create_trace),
            ("Upload blobs", self.step_upload_blobs),
            ("Append events", self.step_append_events),
            ("Finalize trace", self.step_finalize_trace),
            ("Poll for completion", self.step_poll_completion),
            ("Validate final trace", self.step_validate_trace),
        ]

        for step_name, step_fn in steps:
            try:
                success = step_fn()
                if not success:
                    self.failed = True
                    log_fail(f"Step failed: {step_name}")
                    break
            except Exception as e:
                self.failed = True
                log_fail(f"Step failed: {step_name} — {e}")
                break

        print()
        if self.failed:
            log_fail(f"{BOLD}E2E demo failed!{RESET}")
            return False
        else:
            log_success(f"{BOLD}All checks passed!{RESET}")
            return True

    def step_health_check(self) -> bool:
        log_info("Checking API health...")
        
        start_time = time.time()
        while time.time() - start_time < STARTUP_RETRY_SECONDS:
            try:
                resp = self.client.get(f"{self.base_url}/health")
                if resp.status_code == 200:
                    log_success("Health check passed")
                    return True
            except httpx.ConnectError:
                pass
            except Exception as e:
                log_warning(f"Health check error: {e}")
            
            time.sleep(STARTUP_RETRY_INTERVAL)
        
        log_fail(f"API not healthy after {STARTUP_RETRY_SECONDS}s")
        return False

    def step_create_trace(self) -> bool:
        log_info("Creating trace...")

        payload = get_sample_trace_create()
        resp = self.client.post(f"{self.base_url}/traces", json=payload)

        if resp.status_code != 201:
            log_fail(f"Expected 201, got {resp.status_code}: {resp.text[:200]}")
            return False

        data = resp.json()
        self.trace_id = data.get("trace_id")

        if not self.trace_id:
            log_fail("Response missing trace_id")
            return False

        log_success(f"Created trace: {self.trace_id}")
        return True

    def step_upload_blobs(self) -> bool:
        log_info("Uploading blobs...")

        blobs_to_upload = [
            ("patch", SAMPLE_PATCH, "text/plain"),
            ("thought", SAMPLE_THOUGHT, "text/plain"),
            ("terminal_output", SAMPLE_TERMINAL_OUTPUT, "text/plain"),
        ]

        for name, content, content_type in blobs_to_upload:
            # Create a file-like object for multipart upload
            files = {"file": (f"{name}.txt", content.encode(), content_type)}
            resp = self.client.post(f"{self.base_url}/blobs", files=files)

            if resp.status_code != 201:
                log_fail(f"Blob upload failed for {name}: {resp.status_code} — {resp.text[:200]}")
                return False

            data = resp.json()
            blob_id = data.get("blob_id")

            if not blob_id or not blob_id.startswith("sha256:"):
                log_fail(f"Invalid blob_id for {name}: {blob_id}")
                return False

            self.blob_ids[name] = blob_id

        log_success(f"Uploaded {len(self.blob_ids)} blobs")
        return True

    def step_append_events(self) -> bool:
        log_info("Appending events...")

        if not self.trace_id:
            log_fail("No trace_id available")
            return False

        now_ms = int(time.time() * 1000)
        
        # Create sample events covering different types
        events = [
            # Event 1: thought (hypothesis/reasoning)
            {
                "event_id": str(uuid.uuid4()),
                "seq": 1,
                "ts_ms": now_ms,
                "type": "thought",
                "actor": {"kind": "human", "id": "dev-demo-123"},
                "context": {
                    "workspace_root": "/workspace",
                    "branch": "fix/divide-by-zero",
                },
                "payload": {
                    "content_blob_id": self.blob_ids["thought"],
                    "kind": "hypothesis",
                    "links_to": [],
                },
            },
            # Event 2: file_edit (with patch blob reference)
            {
                "event_id": str(uuid.uuid4()),
                "seq": 2,
                "ts_ms": now_ms + 1000,
                "type": "file_edit",
                "actor": {"kind": "human", "id": "dev-demo-123"},
                "context": {
                    "workspace_root": "/workspace",
                    "branch": "fix/divide-by-zero",
                    "commit_head": "a1b2c3d4e5f6789012345678901234567890abcd",
                },
                "payload": {
                    "file_path": "src/utils/calculator.py",
                    "edit_kind": "patch",
                    "patch_format": "unified_diff",
                    "patch_blob_id": self.blob_ids["patch"],
                    "pre_hash": "sha256:abcdef1234567890",
                    "post_hash": "sha256:0987654321fedcba",
                },
            },
            # Event 3: terminal_command
            {
                "event_id": str(uuid.uuid4()),
                "seq": 3,
                "ts_ms": now_ms + 2000,
                "type": "terminal_command",
                "actor": {"kind": "human", "id": "dev-demo-123"},
                "context": {
                    "workspace_root": "/workspace",
                    "branch": "fix/divide-by-zero",
                },
                "payload": {
                    "cwd": "/workspace",
                    "command": "pytest tests/test_calculator.py -v",
                    "shell": "bash",
                },
            },
            # Event 4: terminal_output
            {
                "event_id": str(uuid.uuid4()),
                "seq": 4,
                "ts_ms": now_ms + 5000,
                "type": "terminal_output",
                "actor": {"kind": "tool", "id": "pytest"},
                "context": {
                    "workspace_root": "/workspace",
                    "branch": "fix/divide-by-zero",
                },
                "payload": {
                    "stream": "stdout",
                    "chunk_blob_id": self.blob_ids["terminal_output"],
                    "is_truncated": False,
                },
            },
            # Event 5: test_run
            {
                "event_id": str(uuid.uuid4()),
                "seq": 5,
                "ts_ms": now_ms + 5500,
                "type": "test_run",
                "actor": {"kind": "tool", "id": "pytest"},
                "context": {
                    "workspace_root": "/workspace",
                    "branch": "fix/divide-by-zero",
                },
                "payload": {
                    "command": "pytest tests/test_calculator.py -v",
                    "runner": "pytest",
                    "exit_code": 0,
                    "duration_ms": 120,
                    "passed": True,
                    "report_blob_id": None,
                },
            },
        ]

        self.event_ids = [e["event_id"] for e in events]

        resp = self.client.post(
            f"{self.base_url}/traces/{self.trace_id}/events",
            json={"events": events},
        )

        if resp.status_code != 202:
            log_fail(f"Expected 202, got {resp.status_code}: {resp.text[:200]}")
            return False

        data = resp.json()
        accepted = data.get("accepted", 0)
        seq_high = data.get("seq_high", 0)

        if accepted != len(events):
            log_fail(f"Expected {len(events)} accepted, got {accepted}")
            return False

        if seq_high != 5:
            log_fail(f"Expected seq_high=5, got {seq_high}")
            return False

        log_success(f"Appended {accepted} events (seq_high={seq_high})")
        return True

    def step_finalize_trace(self) -> bool:
        log_info("Finalizing trace...")

        if not self.trace_id:
            log_fail("No trace_id available")
            return False

        payload = get_sample_final_state()
        resp = self.client.post(
            f"{self.base_url}/traces/{self.trace_id}/finalize",
            json=payload,
        )

        if resp.status_code != 200:
            log_fail(f"Expected 200, got {resp.status_code}: {resp.text[:200]}")
            return False

        data = resp.json()
        self.qa_job_id = data.get("qa_job_id")
        status = data.get("status")

        if not self.qa_job_id:
            log_fail("Response missing qa_job_id")
            return False

        if status != "finalizing":
            log_fail(f"Expected status 'finalizing', got '{status}'")
            return False

        log_success(f"Finalized trace, qa_job_id: {self.qa_job_id}")
        log_info("Waiting for QA pipeline...")
        return True

    def step_poll_completion(self) -> bool:
        if not self.trace_id:
            log_fail("No trace_id available")
            return False

        start_time = time.time()
        last_status = None

        while time.time() - start_time < POLL_TIMEOUT_SECONDS:
            resp = self.client.get(f"{self.base_url}/traces/{self.trace_id}")

            if resp.status_code != 200:
                log_warning(f"Poll error: {resp.status_code}")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            data = resp.json()
            status = data.get("status")

            if status != last_status:
                log_status(status)
                last_status = status

            if status == "complete":
                log_success("QA complete!")
                return True
            elif status == "failed":
                qa = data.get("qa", {})
                error = qa.get("error", "Unknown error")
                log_fail(f"QA pipeline failed: {error}")
                return False

            time.sleep(POLL_INTERVAL_SECONDS)

        log_fail(f"Timeout after {POLL_TIMEOUT_SECONDS}s — status: {last_status}")
        return False

    def step_validate_trace(self) -> bool:
        log_info("Validating final trace...")

        if not self.trace_id:
            log_fail("No trace_id available")
            return False

        resp = self.client.get(f"{self.base_url}/traces/{self.trace_id}")

        if resp.status_code != 200:
            log_fail(f"Failed to fetch trace: {resp.status_code}")
            return False

        data = resp.json()
        
        # Validate status
        status = data.get("status")
        if status != "complete":
            log_fail(f"Expected status 'complete', got '{status}'")
            return False

        # Validate QA section
        qa = data.get("qa")
        if not qa:
            log_fail("Missing qa section")
            return False

        # Check schema_valid
        schema_valid = qa.get("schema_valid")
        if schema_valid is not True:
            log_warning(f"qa.schema_valid = {schema_valid} (expected true)")
            # Continue validation — this is a warning, not failure

        # Check tests
        tests = qa.get("tests")
        if not tests:
            log_fail("Missing qa.tests")
            return False

        final_passed = tests.get("final_passed")
        if not isinstance(final_passed, bool):
            log_fail(f"qa.tests.final_passed is not a boolean: {final_passed}")
            return False

        # Check judge
        judge = qa.get("judge")
        if not judge:
            log_fail("Missing qa.judge")
            return False

        # Check all 6 score dimensions
        scores = judge.get("scores")
        if not scores:
            log_fail("Missing qa.judge.scores")
            return False

        required_dimensions = [
            "root_cause_identification",
            "plan_quality",
            "experiment_iterate_loop",
            "use_of_signals_tests_logs",
            "minimality_of_fix",
            "clarity",
        ]

        for dim in required_dimensions:
            if dim not in scores:
                log_fail(f"Missing score dimension: {dim}")
                return False
            score = scores[dim]
            if not isinstance(score, (int, float)) or score < 0.0 or score > 5.0:
                log_fail(f"Invalid score for {dim}: {score}")
                return False

        # Check overall
        overall = judge.get("overall")
        if not isinstance(overall, (int, float)) or overall < 0.0 or overall > 5.0:
            log_fail(f"Invalid overall score: {overall}")
            return False

        # Check rationale_blob_id
        rationale_blob_id = judge.get("rationale_blob_id")
        if not rationale_blob_id:
            log_fail("Missing qa.judge.rationale_blob_id")
            return False

        # Print scores
        print()
        print(f"{BOLD}=== Judge Scores ==={RESET}")
        print(f"Root Cause Identification: {scores['root_cause_identification']}")
        print(f"Plan Quality: {scores['plan_quality']}")
        print(f"Experiment & Iterate: {scores['experiment_iterate_loop']}")
        print(f"Use of Signals: {scores['use_of_signals_tests_logs']}")
        print(f"Minimality of Fix: {scores['minimality_of_fix']}")
        print(f"Clarity: {scores['clarity']}")
        print("---")
        print(f"{BOLD}Overall: {overall} / 5.0{RESET}")
        
        # Print flags if any
        flags = judge.get("flags", [])
        if flags:
            print(f"Flags: {', '.join(flags)}")
        print()

        log_success("All validation checks passed")
        return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> int:
    runner = DemoRunner(API_URL)
    success = runner.run()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
