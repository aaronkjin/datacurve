"""JSON schema validation + error reporting utils for trace ingestion"""

from __future__ import annotations

from pydantic import ValidationError

from core.models import (
    Event,
    EventBatch,
    EventType,
    FinalizeRequest,
    PAYLOAD_TYPE_MAP,
    TraceCreate,
)


# Structured validation error for API responses
class ValidationErrorDetail:
    def __init__(self, field: str, message: str, value: object = None):
        self.field = field
        self.message = message
        self.value = value

    def to_dict(self) -> dict:
        d: dict = {"field": self.field, "message": self.message}
        if self.value is not None:
            d["value"] = repr(self.value)
        return d


# Raised when trace data fails validation with actionable error details
class TraceValidationError(Exception):
    def __init__(self, errors: list[ValidationErrorDetail]):
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s)")

    def to_response_body(self) -> dict:
        return {
            "detail": "Validation failed",
            "errors": [e.to_dict() for e in self.errors],
        }


# Convert Pydantic ValidationError to our structured error format
def _pydantic_errors_to_details(exc: ValidationError) -> list[ValidationErrorDetail]:
    details: list[ValidationErrorDetail] = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        details.append(ValidationErrorDetail(
            field=field,
            message=err["msg"],
            value=err.get("input"),
        ))
    return details


# Validate a POST /traces request body
def validate_trace_create(data: dict) -> TraceCreate:
    try:
        return TraceCreate.model_validate(data)
    except ValidationError as exc:
        raise TraceValidationError(_pydantic_errors_to_details(exc)) from exc


# Validate a POST /traces/{trace_id}/events request body
def validate_event_batch(data: dict) -> EventBatch:
    # Phase 1: envelope validation
    try:
        batch = EventBatch.model_validate(data)
    except ValidationError as exc:
        raise TraceValidationError(_pydantic_errors_to_details(exc)) from exc

    # Phase 2: payload validation per event type
    errors: list[ValidationErrorDetail] = []
    for i, event in enumerate(batch.events):
        try:
            event.validated_payload()
        except ValidationError as exc:
            for err in exc.errors():
                field = f"events.{i}.payload." + ".".join(str(loc) for loc in err["loc"])
                errors.append(ValidationErrorDetail(
                    field=field,
                    message=err["msg"],
                    value=err.get("input"),
                ))
        except KeyError:
            errors.append(ValidationErrorDetail(
                field=f"events.{i}.type",
                message=f"Unknown event type: {event.type}",
                value=event.type,
            ))

    if errors:
        raise TraceValidationError(errors)

    return batch


# Validate a POST /traces/{trace_id}/finalize request body
def validate_finalize(data: dict) -> FinalizeRequest:
    try:
        return FinalizeRequest.model_validate(data)
    except ValidationError as exc:
        raise TraceValidationError(_pydantic_errors_to_details(exc)) from exc


# Ensure event seq values are strictly monotonically increasing
def validate_event_seq_monotonic(events: list[Event], current_high: int = 0) -> None:
    errors: list[ValidationErrorDetail] = []
    prev = current_high
    for i, event in enumerate(events):
        if event.seq <= prev:
            errors.append(ValidationErrorDetail(
                field=f"events.{i}.seq",
                message=f"seq must be > {prev} (monotonically increasing), got {event.seq}",
                value=event.seq,
            ))
        prev = event.seq

    if errors:
        raise TraceValidationError(errors)
