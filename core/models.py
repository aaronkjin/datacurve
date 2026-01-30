"""Pydantic models for trace/event schema"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ExperienceLevel(str, Enum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    unknown = "unknown"


class TraceStatus(str, Enum):
    collecting = "collecting"
    finalizing = "finalizing"
    complete = "complete"
    failed = "failed"


class IngestionMode(str, Enum):
    batch = "batch"
    incremental = "incremental"


class ActorKind(str, Enum):
    human = "human"
    tool = "tool"
    ide = "ide"


class EventType(str, Enum):
    file_edit = "file_edit"
    file_snapshot = "file_snapshot"
    terminal_command = "terminal_command"
    terminal_output = "terminal_output"
    test_run = "test_run"
    debug_action = "debug_action"
    navigation = "navigation"
    thought = "thought"
    commit = "commit"
    pr_metadata = "pr_metadata"
    error = "error"


class EditKind(str, Enum):
    patch = "patch"
    replace_range = "replace_range"
    keystroke_batch = "keystroke_batch"


class SnapshotReason(str, Enum):
    pre_test = "pre_test"
    post_test = "post_test"
    manual_checkpoint = "manual_checkpoint"


class Shell(str, Enum):
    bash = "bash"
    zsh = "zsh"
    pwsh = "pwsh"
    cmd = "cmd"


class Stream(str, Enum):
    stdout = "stdout"
    stderr = "stderr"


class TestRunner(str, Enum):
    pytest = "pytest"
    go_test = "go test"
    npm_test = "npm test"
    make_test = "make test"
    custom = "custom"


class ThoughtKind(str, Enum):
    hypothesis = "hypothesis"
    plan = "plan"
    interpretation = "interpretation"
    decision = "decision"
    postmortem = "postmortem"


class JudgeFlag(str, Enum):
    hallucination_risk = "hallucination_risk"
    missing_steps = "missing_steps"
    unsafe_suggestion = "unsafe_suggestion"
    incomplete_fix = "incomplete_fix"
    exemplary_trace = "exemplary_trace"


class RedactionRule(str, Enum):
    secret_scan = "secret_scan"
    pii_mask = "pii_mask"
    truncate_large = "truncate_large"


# ---------------------------------------------------------------------------
# Trace metadata sub-models
# ---------------------------------------------------------------------------

class RepoFingerprint(BaseModel):
    tree_hash: str | None = None
    dependencies_lock_hash: str | None = None


class Repo(BaseModel):
    repo_id: str
    remote_url: str | None = None
    default_branch: str | None = None
    commit_base: str
    repo_fingerprint: RepoFingerprint | None = None


class BugReport(BaseModel):
    title: str
    description: str
    repro_steps: str | None = None
    expected: str | None = None
    actual: str | None = None
    links: list[str] = Field(default_factory=list)


class Task(BaseModel):
    task_id: str | None = None
    bug_report: BugReport
    labels: list[str] = Field(default_factory=list)


class ConsentFlags(BaseModel):
    store_raw_code: bool = True
    store_terminal_output: bool = True
    allow_llm_judge: bool = True


class Developer(BaseModel):
    developer_id: str
    experience_level: ExperienceLevel = ExperienceLevel.unknown
    consent_flags: ConsentFlags = Field(default_factory=ConsentFlags)


class IDE(BaseModel):
    name: str
    version: str | None = None


class Environment(BaseModel):
    os: str | None = None
    ide: IDE
    language: list[str] = Field(default_factory=list)
    containerized: bool = False
    timezone: str | None = None


class Ingestion(BaseModel):
    mode: IngestionMode = IngestionMode.incremental
    client_session_id: str
    seq_last: int = 0
    dedupe_policy: str = "event_id"
    clock_skew_ms_est: int = 0


# ---------------------------------------------------------------------------
# Blob reference
# ---------------------------------------------------------------------------

class BlobRedaction(BaseModel):
    applied: bool = False
    rules: list[RedactionRule] = Field(default_factory=list)


class BlobRef(BaseModel):
    blob_id: str = Field(..., pattern=r"^sha256:[a-f0-9]+$")
    content_type: str
    byte_length: int = Field(..., ge=0)
    storage_uri: str
    redaction: BlobRedaction | None = None


# ---------------------------------------------------------------------------
# Event actor & context
# ---------------------------------------------------------------------------

class Actor(BaseModel):
    kind: ActorKind
    id: str | None = None


class EventContext(BaseModel):
    workspace_root: str | None = None
    branch: str | None = None
    commit_head: str | None = None
    correlation_id: str | None = None
    parent_event_id: str | None = None


# ---------------------------------------------------------------------------
# Event payloads
# ---------------------------------------------------------------------------

class SelectionRange(BaseModel):
    start: list[int] = Field(..., min_length=2, max_length=2)
    end: list[int] = Field(..., min_length=2, max_length=2)


class FileEditPayload(BaseModel):
    file_path: str
    edit_kind: EditKind
    patch_format: str = "unified_diff"
    patch_blob_id: str
    pre_hash: str | None = None
    post_hash: str | None = None
    selection: SelectionRange | None = None
    reason_ref: str | None = None


class FileSnapshotPayload(BaseModel):
    file_path: str
    content_blob_id: str
    snapshot_reason: SnapshotReason


class TerminalCommandPayload(BaseModel):
    cwd: str
    command: str
    shell: Shell = Shell.bash
    env_hash: str | None = None


class TerminalOutputPayload(BaseModel):
    stream: Stream
    chunk_blob_id: str
    is_truncated: bool = False


class TestRunPayload(BaseModel):
    command: str
    runner: TestRunner
    exit_code: int
    duration_ms: int = Field(..., ge=0)
    passed: bool
    report_blob_id: str | None = None


class ThoughtPayload(BaseModel):
    content_blob_id: str
    kind: ThoughtKind
    links_to: list[str] = Field(default_factory=list)


class CommitPayload(BaseModel):
    commit_sha: str
    message: str
    parent_shas: list[str] = Field(default_factory=list)


class PRMetadataPayload(BaseModel):
    title: str | None = None
    description: str | None = None
    diff_blob_id: str | None = None
    pr_url: str | None = None


class ErrorPayload(BaseModel):
    error_type: str
    message: str
    stacktrace_blob_id: str | None = None


class DebugActionPayload(BaseModel):
    action: str
    details: dict | None = None


class NavigationPayload(BaseModel):
    file_path: str
    symbol: str | None = None
    line: int | None = None


# ---------------------------------------------------------------------------
# Payload type mapping
# ---------------------------------------------------------------------------

PAYLOAD_TYPE_MAP: dict[EventType, type[BaseModel]] = {
    EventType.file_edit: FileEditPayload,
    EventType.file_snapshot: FileSnapshotPayload,
    EventType.terminal_command: TerminalCommandPayload,
    EventType.terminal_output: TerminalOutputPayload,
    EventType.test_run: TestRunPayload,
    EventType.thought: ThoughtPayload,
    EventType.commit: CommitPayload,
    EventType.pr_metadata: PRMetadataPayload,
    EventType.error: ErrorPayload,
    EventType.debug_action: DebugActionPayload,
    EventType.navigation: NavigationPayload,
}

# Discriminated union type for payloads
EventPayload = Annotated[
    Union[
        FileEditPayload,
        FileSnapshotPayload,
        TerminalCommandPayload,
        TerminalOutputPayload,
        TestRunPayload,
        ThoughtPayload,
        CommitPayload,
        PRMetadataPayload,
        ErrorPayload,
        DebugActionPayload,
        NavigationPayload,
    ],
    Field(discriminator=None),
]


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    seq: int = Field(..., ge=1)
    ts_ms: int = Field(..., ge=0)
    type: EventType
    actor: Actor
    context: EventContext | None = None
    payload: dict  # validated dynamically via PAYLOAD_TYPE_MAP

    # Parse and validate payload dict against the typed schema for this event type
    def validated_payload(self) -> BaseModel:
        model_cls = PAYLOAD_TYPE_MAP[self.type]
        return model_cls.model_validate(self.payload)


# Request body for POST /traces/{trace_id}/events
class EventBatch(BaseModel):
    events: list[Event] = Field(..., min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# Final state
# ---------------------------------------------------------------------------

class PRFinalState(BaseModel):
    title: str | None = None
    description: str | None = None
    diff_blob_id: str | None = None


class FinalState(BaseModel):
    commit_head: str | None = None
    pr: PRFinalState | None = None


# ---------------------------------------------------------------------------
# QA models
# ---------------------------------------------------------------------------

class TestInvocation(BaseModel):
    invocation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts_ms: int = Field(..., ge=0)
    command: str
    exit_code: int
    duration_ms: int = Field(..., ge=0)
    passed: bool
    report_blob_id: str | None = None
    stdout_blob_id: str | None = None
    stderr_blob_id: str | None = None


class QATests(BaseModel):
    runner: str
    container_image: str | None = None
    invocations: list[TestInvocation] = Field(default_factory=list)
    final_passed: bool = False


class JudgeScores(BaseModel):
    root_cause_identification: float = Field(..., ge=0.0, le=5.0)
    plan_quality: float = Field(..., ge=0.0, le=5.0)
    experiment_iterate_loop: float = Field(..., ge=0.0, le=5.0)
    use_of_signals_tests_logs: float = Field(..., ge=0.0, le=5.0)
    minimality_of_fix: float = Field(..., ge=0.0, le=5.0)
    clarity: float = Field(..., ge=0.0, le=5.0)


class JudgeResult(BaseModel):
    model: str
    rubric_version: str = "1.0"
    scores: JudgeScores
    overall: float = Field(..., ge=0.0, le=5.0)
    rationale_blob_id: str | None = None
    flags: list[JudgeFlag] = Field(default_factory=list)

    @field_validator("overall")
    @classmethod
    def validate_overall(cls, v: float, info) -> float:
        return round(v, 1)


# Raw JSON output shape from the LLM judge
class JudgeOutput(BaseModel):
    scores: JudgeScores
    overall: float = Field(..., ge=0.0, le=5.0)
    rationale: str = Field(..., min_length=1)
    flags: list[JudgeFlag] = Field(default_factory=list)

    @field_validator("overall")
    @classmethod
    def validate_overall(cls, v: float) -> float:
        return round(v, 1)


class QA(BaseModel):
    schema_valid: bool = True
    tests: QATests | None = None
    judge: JudgeResult | None = None


# ---------------------------------------------------------------------------
# Top-level Trace
# ---------------------------------------------------------------------------

# Request body for POST /traces
class TraceCreate(BaseModel):
    repo: Repo
    task: Task
    developer: Developer
    environment: Environment


# Request body for POST /traces/{trace_id}/finalize
class FinalizeRequest(BaseModel):
    final_state: FinalState


# Full assembled trace document
class Trace(BaseModel):
    trace_version: str = "1.0"
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at_ms: int = Field(..., ge=0)
    finalized_at_ms: int | None = None
    status: TraceStatus = TraceStatus.collecting
    repo: Repo
    task: Task
    developer: Developer
    environment: Environment
    ingestion: Ingestion | None = None
    artifacts: dict | None = None
    events: list[Event] = Field(default_factory=list)
    final_state: FinalState | None = None
    qa: QA | None = None


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class TraceCreateResponse(BaseModel):
    trace_id: str
    created_at_ms: int
    status: TraceStatus = TraceStatus.collecting


class EventsAcceptedResponse(BaseModel):
    accepted: int
    seq_high: int


class FinalizeResponse(BaseModel):
    trace_id: str
    status: TraceStatus = TraceStatus.finalizing
    qa_job_id: str


class BlobUploadResponse(BaseModel):
    blob_id: str
    byte_length: int
    storage_uri: str
