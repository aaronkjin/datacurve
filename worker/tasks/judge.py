"""LLM judge for Celery task qa.run_judge"""

from __future__ import annotations

import json
import logging
from typing import Any

import openai
from pydantic import ValidationError

from core.blob_store import LocalFsBlobStore
from core.config import settings
from core.models import (
    JudgeFlag,
    JudgeOutput,
    JudgeResult,
    JudgeScores,
    QA,
)
from db.models import EventRow, TraceRow
from db.session import get_sync_session
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)
blob_store = LocalFsBlobStore()

RUBRIC_TEXT = """# LLM Judge Rubric

## Scoring Dimensions

Each dimension is scored **0.0–5.0** (float, one decimal place).

### 1. Root-Cause Identification (`root_cause_identification`)
- **5**: Developer correctly identifies the exact root cause with evidence (logs, stack traces, code references).
- **4**: Root cause is correct; evidence is present but incomplete.
- **3**: Root cause is approximately correct; some confusion or misdirection.
- **2**: Partially correct; significant time spent on wrong hypotheses.
- **1**: Root cause not clearly identified; fix may be coincidental.
- **0**: No evidence of root-cause analysis.

### 2. Plan Quality (`plan_quality`)
- **5**: Clear hypothesis-driven plan; systematic test-iterate loop; well-structured approach.
- **4**: Good plan with minor gaps in systematicity.
- **3**: Reasonable approach but reactive rather than planned.
- **2**: Ad-hoc debugging; no clear plan evident.
- **1**: Chaotic process; random changes.
- **0**: No discernible plan or methodology.

### 3. Experiment & Iterate Loop (`experiment_iterate_loop`)
- **5**: Each change is tested; results inform next step; clear feedback loop.
- **4**: Good iteration with occasional untested changes.
- **3**: Some iteration; gaps in testing intermediate states.
- **2**: Minimal iteration; large jumps between states.
- **1**: No meaningful iteration; single attempt.
- **0**: No experimentation visible.

### 4. Use of Signals — Tests & Logs (`use_of_signals_tests_logs`)
- **5**: Tests, logs, stack traces, and error messages are consistently used to guide decisions.
- **4**: Signals are mostly used; occasional missed signals.
- **3**: Some signal usage; important signals sometimes ignored.
- **2**: Minimal use of available signals.
- **1**: Signals largely ignored.
- **0**: No signal usage evident.

### 5. Minimality of Fix (`minimality_of_fix`)
- **5**: Fix is precisely targeted; no unrelated changes; minimal diff.
- **4**: Fix is targeted with minor unnecessary changes.
- **3**: Fix includes some unrelated cleanup or refactoring.
- **2**: Significant unrelated changes mixed with the fix.
- **1**: Overly broad changes; hard to isolate the actual fix.
- **0**: Changes are mostly unrelated to the bug.

### 6. Clarity (`clarity`)
- **5**: Reasoning is clear, well-documented, and directly grounded in code/evidence.
- **4**: Mostly clear; minor gaps in explanation.
- **3**: Understandable but could be clearer; some leaps in logic.
- **2**: Reasoning is hard to follow; significant gaps.
- **1**: Very unclear reasoning.
- **0**: No reasoning provided.

## Overall Score

`overall` = weighted average of all 6 dimensions (equal weight).
Rounded to one decimal place.

## Flags

Set zero or more flags from this fixed set when clearly warranted:
- `hallucination_risk` — Judge suspects developer reasoning contains fabricated information.
- `missing_steps` — Significant debugging steps appear to be missing from the trace.
- `unsafe_suggestion` — Fix introduces potential security or reliability concerns.
- `incomplete_fix` — Fix may not fully resolve the reported bug.
- `exemplary_trace` — Trace is exceptionally high quality and suitable as a training example.

## Required JSON Output Shape

Return EXACTLY this JSON structure with no additional keys:

{
  "scores": {
    "root_cause_identification": 0.0,
    "plan_quality": 0.0,
    "experiment_iterate_loop": 0.0,
    "use_of_signals_tests_logs": 0.0,
    "minimality_of_fix": 0.0,
    "clarity": 0.0
  },
  "overall": 0.0,
  "rationale": "Free-text explanation of scores (1-3 paragraphs).",
  "flags": []
}

All scores must be floats in range [0.0, 5.0]. The `overall` field should be the average of the 6 scores, rounded to one decimal place. The `flags` array may be empty or contain values from the fixed set above."""

SYSTEM_PROMPT = f"""You are an expert code reviewer evaluating a developer's bug-fix trace.

Your task is to evaluate the trace using the rubric below and return a strictly valid JSON response.

{RUBRIC_TEXT}

IMPORTANT:
- Return ONLY valid JSON matching the exact output shape above.
- Do not include any text before or after the JSON.
- All score values must be floats between 0.0 and 5.0.
- The overall score should be the average of all 6 dimension scores, rounded to 1 decimal place.
- Only set flags when clearly warranted based on the evidence."""


@celery_app.task(name="qa.run_judge", bind=True, max_retries=0)
def run_judge(self, trace_id: str) -> dict:
    # Execute LLM judge and store results in trace qa_json
    try:
        return _run_judge_impl(trace_id)
    except Exception as exc:
        logger.exception("run_judge failed for trace %s", trace_id)
        _mark_failed(trace_id, str(exc))
        raise


def _run_judge_impl(trace_id: str) -> dict:
    # Load trace and events
    with get_sync_session() as session:
        row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
        if row is None:
            raise ValueError(f"Trace not found: {trace_id}")

        # Read trace data while in session
        task_json = row.task_json or {}
        final_state_json = row.final_state_json or {}
        qa_json_data = row.qa_json or {}

        # Query events ordered by seq
        events = (
            session.query(EventRow)
            .filter_by(trace_id=trace_id)
            .order_by(EventRow.seq)
            .all()
        )

        # Extract event data while in session
        events_data = [
            {
                "seq": e.seq,
                "ts_ms": e.ts_ms,
                "type": e.type,
                "payload_json": e.payload_json,
            }
            for e in events
        ]

    # Build judge packet
    judge_packet = _build_judge_packet(
        task_json=task_json,
        events_data=events_data,
        final_state_json=final_state_json,
        qa_json=qa_json_data,
    )

    # Call LLM
    judge_output = _call_llm_judge(judge_packet)

    # Store rationale as blob
    rationale_blob_id = blob_store.put_bytes(
        judge_output.rationale.encode("utf-8"),
        "text/plain",
    )

    # Build JudgeResult
    judge_result = JudgeResult(
        model=settings.JUDGE_MODEL,
        rubric_version="1.0",
        scores=judge_output.scores,
        overall=judge_output.overall,
        rationale_blob_id=rationale_blob_id,
        flags=judge_output.flags,
    )

    # Update trace qa_json
    with get_sync_session() as session:
        row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
        if row is None:
            raise ValueError(f"Trace not found: {trace_id}")

        existing_qa = QA.model_validate(row.qa_json) if row.qa_json else QA()
        existing_qa.judge = judge_result
        row.qa_json = existing_qa.model_dump()

    # Chain to finalize_qa task
    celery_app.send_task("qa.finalize_qa", args=[trace_id])

    return {
        "trace_id": trace_id,
        "overall": judge_result.overall,
        "flags": [f.value for f in judge_result.flags],
    }


def _build_judge_packet(
    task_json: dict,
    events_data: list[dict],
    final_state_json: dict,
    qa_json: dict,
) -> str:
    # Build a concise judge packet from trace data
    sections: list[str] = []

    # 1. Bug report summary
    bug_report = task_json.get("bug_report", {})
    sections.append("## Bug Report")
    sections.append(f"**Title:** {bug_report.get('title', 'N/A')}")
    sections.append(f"**Description:** {bug_report.get('description', 'N/A')}")
    if bug_report.get("repro_steps"):
        sections.append(f"**Repro Steps:** {bug_report['repro_steps']}")
    if bug_report.get("expected"):
        sections.append(f"**Expected:** {bug_report['expected']}")
    if bug_report.get("actual"):
        sections.append(f"**Actual:** {bug_report['actual']}")
    sections.append("")

    # 2. Key events from trace
    sections.append("## Developer Actions (ordered by sequence)")
    for event in events_data:
        event_summary = _summarize_event(event)
        if event_summary:
            sections.append(event_summary)
    sections.append("")

    # 3. Final diff summary
    sections.append("## Final State")
    if final_state_json:
        commit_head = final_state_json.get("commit_head")
        if commit_head:
            sections.append(f"**Final commit:** {commit_head}")
        pr = final_state_json.get("pr", {})
        if pr:
            if pr.get("title"):
                sections.append(f"**PR Title:** {pr['title']}")
            if pr.get("description"):
                sections.append(f"**PR Description:** {pr['description']}")
            if pr.get("diff_blob_id"):
                sections.append(f"**Diff blob:** {pr['diff_blob_id']}")
    else:
        sections.append("No final state recorded.")
    sections.append("")

    # 4. Test results
    sections.append("## Test Results")
    tests = qa_json.get("tests")
    if tests:
        sections.append(f"**Runner:** {tests.get('runner', 'N/A')}")
        sections.append(f"**Final passed:** {tests.get('final_passed', False)}")
        invocations = tests.get("invocations", [])
        for i, inv in enumerate(invocations[:5]):  # Limit to 5 invocations
            sections.append(
                f"- Invocation {i + 1}: command=`{inv.get('command', 'N/A')}`, "
                f"exit_code={inv.get('exit_code', 'N/A')}, "
                f"passed={inv.get('passed', False)}, "
                f"duration_ms={inv.get('duration_ms', 'N/A')}"
            )
    else:
        sections.append("No test results recorded.")

    return "\n".join(sections)


def _summarize_event(event: dict) -> str | None:
    # Create a concise summary of an event for the judge packet
    seq = event.get("seq", 0)
    ts_ms = event.get("ts_ms", 0)
    event_type = event.get("type", "unknown")
    payload = event.get("payload_json", {})

    prefix = f"[seq={seq}, ts={ts_ms}] "

    if event_type == "file_edit":
        file_path = payload.get("file_path", "N/A")
        edit_kind = payload.get("edit_kind", "N/A")
        return f"{prefix}**file_edit**: `{file_path}` ({edit_kind})"

    elif event_type == "thought":
        kind = payload.get("kind", "N/A")
        content_blob_id = payload.get("content_blob_id", "")
        # Try to fetch thought content if blob exists
        thought_content = _fetch_blob_content_preview(content_blob_id)
        if thought_content:
            return f"{prefix}**thought** ({kind}): {thought_content}"
        return f"{prefix}**thought** ({kind}): [blob: {content_blob_id}]"

    elif event_type == "test_run":
        command = payload.get("command", "N/A")
        exit_code = payload.get("exit_code", "N/A")
        passed = payload.get("passed", False)
        duration_ms = payload.get("duration_ms", 0)
        return (
            f"{prefix}**test_run**: `{command}` "
            f"(exit_code={exit_code}, passed={passed}, duration={duration_ms}ms)"
        )

    elif event_type == "terminal_command":
        command = payload.get("command", "N/A")
        cwd = payload.get("cwd", "N/A")
        return f"{prefix}**terminal_command**: `{command}` (cwd: {cwd})"

    elif event_type == "terminal_output":
        stream = payload.get("stream", "N/A")
        is_truncated = payload.get("is_truncated", False)
        chunk_blob_id = payload.get("chunk_blob_id", "")
        output_preview = _fetch_blob_content_preview(chunk_blob_id, max_chars=200)
        if output_preview:
            return f"{prefix}**terminal_output** ({stream}): {output_preview}"
        return f"{prefix}**terminal_output** ({stream}): [truncated={is_truncated}]"

    elif event_type == "commit":
        commit_sha = payload.get("commit_sha", "N/A")
        message = payload.get("message", "")[:100]
        return f"{prefix}**commit**: {commit_sha[:12]} - {message}"

    elif event_type == "error":
        error_type = payload.get("error_type", "N/A")
        message = payload.get("message", "")[:100]
        return f"{prefix}**error**: {error_type}: {message}"

    # Skip other event types (navigation, debug_action, etc.)
    return None


def _fetch_blob_content_preview(blob_id: str, max_chars: int = 500) -> str | None:
    # Try to fetch blob content and return a preview
    if not blob_id:
        return None
    try:
        content = blob_store.get_bytes(blob_id)
        text = content.decode("utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + "..."
        return text
    except Exception:
        return None


def _call_llm_judge(judge_packet: str) -> JudgeOutput:
    # Call the LLM judge and parse the response
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

    response = client.chat.completions.create(
        model=settings.JUDGE_MODEL,
        max_completion_tokens=2000,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"Please evaluate the following bug-fix trace:\n\n{judge_packet}",
            }
        ],
    )

    # Extract text content from response
    response_text = response.choices[0].message.content or ""

    # Parse JSON from response
    try:
        # Try to find JSON in response (handle potential markdown code blocks)
        json_str = response_text.strip()
        if json_str.startswith("```"):
            # Extract from code block
            lines = json_str.split("\n")
            # Find start and end of code block
            start_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("```") and i == 0:
                    start_idx = 1
                    continue
            end_idx = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].startswith("```"):
                    end_idx = i
                    break
            json_str = "\n".join(lines[start_idx:end_idx])

        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\nResponse: {response_text[:500]}")

    # Validate against JudgeOutput model
    try:
        judge_output = JudgeOutput.model_validate(parsed)
    except ValidationError as e:
        raise ValueError(f"LLM response failed validation: {e}\nParsed: {parsed}")

    return judge_output


def _mark_failed(trace_id: str, error_msg: str) -> None:
    # Set trace status to failed and store error in qa_json
    try:
        with get_sync_session() as session:
            row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
            if row is None:
                return
            row.status = "failed"
            existing_qa = QA.model_validate(row.qa_json) if row.qa_json else QA()
            row.qa_json = {
                **existing_qa.model_dump(),
                "error": error_msg,
            }
    except Exception:
        logger.exception("Failed to mark trace %s as failed", trace_id)
