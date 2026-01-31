#!/usr/bin/env python3
"""E2E demo script using info from a real repo

Sample demo repo of choice: pallets/itsdangerous

Usage:
    # Terminal 1: Start services
    docker-compose up --build

    # Terminal 2: Run demo (after services are healthy)
    python scripts/demo_itsdangerous.py

Environment variables:
    API_URL - Base URL for the API (default: http://localhost:8000)
"""

from __future__ import annotations

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
# itsdangerous-specific sample data
# ---------------------------------------------------------------------------

# Realistic bug: TimestampSigner doesn't handle timezone-aware datetime properly
SAMPLE_PATCH = """\
--- a/src/itsdangerous/timed.py
+++ b/src/itsdangerous/timed.py
@@ -45,8 +45,12 @@ class TimestampSigner(Signer):
     def get_timestamp(self) -> int:
-        return int(time.time())
+        \"\"\"Returns the current timestamp as an integer.
+        
+        Uses time.time() which returns UTC timestamp regardless of local timezone.
+        \"\"\"
+        return int(time.time())
 
     def timestamp_to_datetime(self, ts: int) -> datetime:
-        return datetime.utcfromtimestamp(ts)
+        \"\"\"Convert a timestamp to a timezone-aware UTC datetime.
+        
+        Note: datetime.utcfromtimestamp() is deprecated in Python 3.12+.
+        Using datetime.fromtimestamp() with UTC timezone instead.
+        \"\"\"
+        return datetime.fromtimestamp(ts, tz=timezone.utc)
"""

SAMPLE_THOUGHT = """\
Investigating issue #287: DeprecationWarning for datetime.utcfromtimestamp()

After reviewing the stack trace and Python 3.12 release notes, I've identified the root cause:

**Root Cause Analysis:**
1. `datetime.utcfromtimestamp()` is deprecated in Python 3.12
2. The `TimestampSigner.timestamp_to_datetime()` method uses this deprecated function
3. Users on Python 3.12+ see DeprecationWarning when validating signed timestamps

**Hypothesis:**
Replace `datetime.utcfromtimestamp(ts)` with `datetime.fromtimestamp(ts, tz=timezone.utc)`
to create timezone-aware datetime objects and eliminate the deprecation warning.

**Plan:**
1. Update `timestamp_to_datetime()` in `src/itsdangerous/timed.py`
2. Add docstrings explaining the timezone handling
3. Run the test suite to verify backward compatibility
4. Check that signed tokens from before the fix still validate correctly

**Risk Assessment:**
- Low risk: The new approach returns timezone-aware datetime instead of naive
- Mitigation: Existing code comparing with naive datetime may need updates
- Tests should catch any regressions
"""

SAMPLE_TERMINAL_OUTPUT = """\
============================= test session starts ==============================
platform linux -- Python 3.12.1, pytest-8.0.0, pluggy-1.4.0
rootdir: /workspace/itsdangerous
configfile: pyproject.toml
plugins: cov-4.1.0
collected 89 items

tests/test_itsdangerous.py::test_signer_sign_unsign PASSED           [  1%]
tests/test_itsdangerous.py::test_signer_no_separator PASSED          [  2%]
tests/test_itsdangerous.py::test_signer_sep_contains_sep PASSED      [  3%]
tests/test_itsdangerous.py::test_timestamp_signer PASSED             [  4%]
tests/test_itsdangerous.py::test_timestamp_signer_expired PASSED     [  5%]
tests/test_itsdangerous.py::test_timestamp_to_datetime PASSED        [  6%]
tests/test_itsdangerous.py::test_serializer PASSED                   [  7%]
tests/test_itsdangerous.py::test_url_safe_serializer PASSED          [  8%]
...
tests/test_timed.py::test_timezone_aware_datetime PASSED             [ 95%]
tests/test_timed.py::test_backward_compatibility PASSED              [ 96%]
tests/test_timed.py::test_no_deprecation_warning PASSED              [ 97%]
tests/test_timed.py::test_timestamp_roundtrip PASSED                 [ 98%]
tests/test_timed.py::test_max_age_validation PASSED                  [100%]

============================= 89 passed in 1.24s ===============================
"""


def get_trace_create() -> dict[str, Any]:
    # Return trace creation data for pallets/itsdangerous
    return {
        "repo": {
            "repo_id": "pallets/itsdangerous",
            "remote_url": "https://github.com/pallets/itsdangerous.git",
            "default_branch": "main",
            "commit_base": "672971d66a2ef9f85151e53283113f33d642dabd",
            "repo_fingerprint": {
                "tree_hash": "ef4287f82d8234404b58c7b29d38197e1f38e207",
                "dependencies_lock_hash": None,
            },
        },
        "task": {
            "task_id": "itsdangerous-287",
            "bug_report": {
                "title": "DeprecationWarning: datetime.utcfromtimestamp() is deprecated in Python 3.12",
                "description": (
                    "When using TimestampSigner on Python 3.12+, a DeprecationWarning is raised:\n\n"
                    "```\n"
                    "DeprecationWarning: datetime.utcfromtimestamp() is deprecated and scheduled "
                    "for removal in a future version. Use datetime.fromtimestamp(timestamp, tz=timezone.utc) instead.\n"
                    "```\n\n"
                    "This affects Flask applications using session cookies with itsdangerous on Python 3.12."
                ),
                "repro_steps": (
                    "1. Install Python 3.12+\n"
                    "2. Install itsdangerous\n"
                    "3. Create a TimestampSigner and sign some data\n"
                    "4. Unsign the data (triggers timestamp_to_datetime)\n"
                    "5. Observe DeprecationWarning in stderr"
                ),
                "expected": "No deprecation warnings when using TimestampSigner",
                "actual": "DeprecationWarning raised on every unsign() call",
                "links": [
                    "https://github.com/pallets/itsdangerous/issues/287",
                    "https://docs.python.org/3.12/whatsnew/3.12.html#deprecated",
                ],
            },
            "labels": ["bug", "python3.12", "deprecation", "timed"],
        },
        "developer": {
            "developer_id": "contributor-pallets-42",
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


def get_final_state() -> dict[str, Any]:
    # Return finalize data for the bug fix
    return {
        "final_state": {
            "commit_head": "a1b2c3d4e5f6789012345678901234567890abcdef",
            "pr": {
                "title": "Fix DeprecationWarning for datetime.utcfromtimestamp() on Python 3.12+",
                "description": (
                    "## Summary\n"
                    "Replace deprecated `datetime.utcfromtimestamp()` with "
                    "`datetime.fromtimestamp(ts, tz=timezone.utc)` to fix DeprecationWarning "
                    "on Python 3.12+.\n\n"
                    "## Changes\n"
                    "- Updated `TimestampSigner.timestamp_to_datetime()` to use timezone-aware datetime\n"
                    "- Added docstrings explaining timezone handling\n"
                    "- Returns `datetime` with `tzinfo=timezone.utc` instead of naive datetime\n\n"
                    "## Testing\n"
                    "- All existing tests pass\n"
                    "- Added test to verify no deprecation warning\n"
                    "- Verified backward compatibility with existing signed tokens\n\n"
                    "Fixes #287"
                ),
                "diff_blob_id": None,
            },
        }
    }


# ---------------------------------------------------------------------------
# Demo step implementations
# ---------------------------------------------------------------------------

class DemoRunner:
    # Runs the E2E demo flow for itsdangerous

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)
        self.trace_id: str | None = None
        self.blob_ids: dict[str, str] = {}
        self.event_ids: list[str] = []
        self.qa_job_id: str | None = None
        self.failed = False

    def run(self) -> bool:
        # Run the full E2E demo. Returns True on success, False on failure
        log_info(f"{BOLD}Starting itsdangerous E2E demo...{RESET}")
        log_info(f"API URL: {self.base_url}")
        log_info(f"Repository: {CYAN}pallets/itsdangerous{RESET}")
        log_info(f"Bug: DeprecationWarning for datetime.utcfromtimestamp() on Python 3.12+")
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
            log_fail(f"{BOLD}itsdangerous E2E demo failed!{RESET}")
            return False
        else:
            log_success(f"{BOLD}All checks passed!{RESET}")
            return True

    def step_health_check(self) -> bool:
        # Wait for API to be healthy
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
        # Create a new trace via POST /traces
        log_info("Creating trace for pallets/itsdangerous...")

        payload = get_trace_create()
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
        # Upload sample blobs via POST /blobs
        log_info("Uploading blobs...")

        blobs_to_upload = [
            ("patch", SAMPLE_PATCH, "text/plain"),
            ("thought", SAMPLE_THOUGHT, "text/plain"),
            ("terminal_output", SAMPLE_TERMINAL_OUTPUT, "text/plain"),
        ]

        for name, content, content_type in blobs_to_upload:
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
        # Append a batch of events via POST /traces/{trace_id}/events
        log_info("Appending events...")

        if not self.trace_id:
            log_fail("No trace_id available")
            return False

        now_ms = int(time.time() * 1000)

        # Create realistic events for the itsdangerous bug fix
        events = [
            # Event 1: Developer analyzes the deprecation warning
            {
                "event_id": str(uuid.uuid4()),
                "seq": 1,
                "ts_ms": now_ms,
                "type": "thought",
                "actor": {"kind": "human", "id": "contributor-pallets-42"},
                "context": {
                    "workspace_root": "/workspace/itsdangerous",
                    "branch": "fix/deprecation-warning-287",
                },
                "payload": {
                    "content_blob_id": self.blob_ids["thought"],
                    "kind": "hypothesis",
                    "links_to": [],
                },
            },
            # Event 2: Developer edits timed.py to fix the deprecation
            {
                "event_id": str(uuid.uuid4()),
                "seq": 2,
                "ts_ms": now_ms + 60000,  # 1 minute later
                "type": "file_edit",
                "actor": {"kind": "human", "id": "contributor-pallets-42"},
                "context": {
                    "workspace_root": "/workspace/itsdangerous",
                    "branch": "fix/deprecation-warning-287",
                    "commit_head": "672971d66a2ef9f85151e53283113f33d642dabd",
                },
                "payload": {
                    "file_path": "src/itsdangerous/timed.py",
                    "edit_kind": "patch",
                    "patch_format": "unified_diff",
                    "patch_blob_id": self.blob_ids["patch"],
                    "pre_hash": "sha256:abc123def456",
                    "post_hash": "sha256:789xyz012345",
                },
            },
            # Event 3: Developer runs pytest
            {
                "event_id": str(uuid.uuid4()),
                "seq": 3,
                "ts_ms": now_ms + 120000,  # 2 minutes later
                "type": "terminal_command",
                "actor": {"kind": "human", "id": "contributor-pallets-42"},
                "context": {
                    "workspace_root": "/workspace/itsdangerous",
                    "branch": "fix/deprecation-warning-287",
                },
                "payload": {
                    "cwd": "/workspace/itsdangerous",
                    "command": "pytest tests/ -v --tb=short",
                    "shell": "bash",
                },
            },
            # Event 4: Terminal output from pytest
            {
                "event_id": str(uuid.uuid4()),
                "seq": 4,
                "ts_ms": now_ms + 125000,  # A few seconds later
                "type": "terminal_output",
                "actor": {"kind": "tool", "id": "pytest"},
                "context": {
                    "workspace_root": "/workspace/itsdangerous",
                    "branch": "fix/deprecation-warning-287",
                },
                "payload": {
                    "stream": "stdout",
                    "chunk_blob_id": self.blob_ids["terminal_output"],
                    "is_truncated": False,
                },
            },
            # Event 5: Test run summary
            {
                "event_id": str(uuid.uuid4()),
                "seq": 5,
                "ts_ms": now_ms + 126000,
                "type": "test_run",
                "actor": {"kind": "tool", "id": "pytest"},
                "context": {
                    "workspace_root": "/workspace/itsdangerous",
                    "branch": "fix/deprecation-warning-287",
                },
                "payload": {
                    "command": "pytest tests/ -v --tb=short",
                    "runner": "pytest",
                    "exit_code": 0,
                    "duration_ms": 1240,
                    "passed": True,
                    "report_blob_id": None,
                },
            },
            # Event 6: Developer commits the fix
            {
                "event_id": str(uuid.uuid4()),
                "seq": 6,
                "ts_ms": now_ms + 180000,  # 3 minutes later
                "type": "commit",
                "actor": {"kind": "human", "id": "contributor-pallets-42"},
                "context": {
                    "workspace_root": "/workspace/itsdangerous",
                    "branch": "fix/deprecation-warning-287",
                },
                "payload": {
                    "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcdef",
                    "message": "Fix DeprecationWarning for datetime.utcfromtimestamp() on Python 3.12+",
                    "parent_shas": ["672971d66a2ef9f85151e53283113f33d642dabd"],
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

        log_success(f"Appended {accepted} events (seq_high={seq_high})")
        return True

    def step_finalize_trace(self) -> bool:
        # Finalize the trace via POST /traces/{trace_id}/finalize
        log_info("Finalizing trace...")

        if not self.trace_id:
            log_fail("No trace_id available")
            return False

        payload = get_final_state()
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
        # Poll until trace status is 'complete' or 'failed'
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
        # Validate the final trace meets all acceptance criteria
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
        print(f"{BOLD}=== Judge Scores for pallets/itsdangerous ==={RESET}")
        print(f"Bug: DeprecationWarning for datetime.utcfromtimestamp()")
        print()
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
    # Run E2E demo
    runner = DemoRunner(API_URL)
    success = runner.run()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
