"""Trace endpoints: create, append events, finalize, get"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    Event,
    EventsAcceptedResponse,
    FinalizeResponse,
    Trace,
    TraceCreateResponse,
    TraceStatus,
)
from core.validation import (
    validate_event_batch,
    validate_event_seq_monotonic,
    validate_finalize,
    validate_trace_create,
)
from db.models import EventRow, TraceRow
from db.session import get_session_dep

router = APIRouter(prefix="/traces", tags=["traces"])


@router.post("", status_code=201, response_model=TraceCreateResponse)
async def create_trace(
    body: dict,
    session: AsyncSession = Depends(get_session_dep),
) -> TraceCreateResponse:
    trace_create = validate_trace_create(body)
    now_ms = int(time.time() * 1000)
    trace_id = str(uuid.uuid4())

    row = TraceRow(
        trace_id=trace_id,
        status="collecting",
        repo_json=trace_create.repo.model_dump(),
        task_json=trace_create.task.model_dump(),
        developer_json=trace_create.developer.model_dump(),
        environment_json=trace_create.environment.model_dump(),
        created_at_ms=now_ms,
    )
    session.add(row)
    await session.flush()

    return TraceCreateResponse(
        trace_id=trace_id,
        created_at_ms=now_ms,
        status=TraceStatus.collecting,
    )


@router.post("/{trace_id}/events", status_code=202, response_model=EventsAcceptedResponse)
async def append_events(
    trace_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session_dep),
) -> EventsAcceptedResponse:
    batch = validate_event_batch(body)

    # Check trace exists and is collecting
    result = await session.execute(
        select(TraceRow).where(TraceRow.trace_id == trace_id)
    )
    trace_row = result.scalar_one_or_none()
    if trace_row is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    if trace_row.status != "collecting":
        raise HTTPException(status_code=409, detail=f"Trace status is '{trace_row.status}', expected 'collecting'")

    # Get current max seq for this trace
    seq_result = await session.execute(
        select(func.coalesce(func.max(EventRow.seq), 0)).where(
            EventRow.trace_id == trace_id
        )
    )
    current_high: int = seq_result.scalar_one()

    # Validate monotonic seq
    validate_event_seq_monotonic(batch.events, current_high)

    # Check for duplicate event_ids
    event_ids = [e.event_id for e in batch.events]
    existing_result = await session.execute(
        select(EventRow.event_id).where(
            EventRow.trace_id == trace_id,
            EventRow.event_id.in_(event_ids),
        )
    )
    existing_event_ids = {str(row[0]) for row in existing_result.all()}
    if existing_event_ids:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate event_id(s): {', '.join(existing_event_ids)}",
        )

    # Insert events
    for event in batch.events:
        row = EventRow(
            trace_id=trace_id,
            event_id=event.event_id,
            seq=event.seq,
            ts_ms=event.ts_ms,
            type=event.type.value,
            actor_json=event.actor.model_dump(),
            context_json=event.context.model_dump() if event.context else None,
            payload_json=event.payload,
        )
        session.add(row)

    await session.flush()

    seq_high = max(e.seq for e in batch.events)
    return EventsAcceptedResponse(accepted=len(batch.events), seq_high=seq_high)


@router.post("/{trace_id}/finalize", status_code=200, response_model=FinalizeResponse)
async def finalize_trace(
    trace_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session_dep),
) -> FinalizeResponse:
    finalize_req = validate_finalize(body)

    result = await session.execute(
        select(TraceRow).where(TraceRow.trace_id == trace_id)
    )
    trace_row = result.scalar_one_or_none()
    if trace_row is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    if trace_row.status != "collecting":
        raise HTTPException(status_code=409, detail=f"Trace status is '{trace_row.status}', expected 'collecting'")

    now_ms = int(time.time() * 1000)
    trace_row.status = "finalizing"
    trace_row.final_state_json = finalize_req.final_state.model_dump()
    trace_row.finalized_at_ms = now_ms

    qa_job_id = str(uuid.uuid4())
    # TODO: celery_app.send_task("qa.run_tests", args=[trace_id], task_id=qa_job_id)

    await session.flush()

    return FinalizeResponse(
        trace_id=trace_id,
        status=TraceStatus.finalizing,
        qa_job_id=qa_job_id,
    )


@router.get("/{trace_id}", response_model=Trace)
async def get_trace(
    trace_id: str,
    include_events: bool = Query(True),
    include_qa: bool = Query(True),
    session: AsyncSession = Depends(get_session_dep),
) -> Trace:
    result = await session.execute(
        select(TraceRow).where(TraceRow.trace_id == trace_id)
    )
    trace_row = result.scalar_one_or_none()
    if trace_row is None:
        raise HTTPException(status_code=404, detail="Trace not found")

    events: list[Event] = []
    if include_events:
        ev_result = await session.execute(
            select(EventRow)
            .where(EventRow.trace_id == trace_id)
            .order_by(EventRow.seq)
        )
        for ev_row in ev_result.scalars().all():
            events.append(
                Event(
                    event_id=str(ev_row.event_id),
                    seq=ev_row.seq,
                    ts_ms=ev_row.ts_ms,
                    type=ev_row.type,
                    actor=ev_row.actor_json,
                    context=ev_row.context_json,
                    payload=ev_row.payload_json,
                )
            )

    from core.models import FinalState, QA, Repo, Task, Developer, Environment

    trace = Trace(
        trace_version=trace_row.trace_version,
        trace_id=str(trace_row.trace_id),
        created_at_ms=trace_row.created_at_ms,
        finalized_at_ms=trace_row.finalized_at_ms,
        status=trace_row.status,
        repo=Repo.model_validate(trace_row.repo_json),
        task=Task.model_validate(trace_row.task_json),
        developer=Developer.model_validate(trace_row.developer_json),
        environment=Environment.model_validate(trace_row.environment_json),
        events=events,
        final_state=FinalState.model_validate(trace_row.final_state_json) if trace_row.final_state_json else None,
        qa=QA.model_validate(trace_row.qa_json) if include_qa and trace_row.qa_json else None,
    )
    return trace
