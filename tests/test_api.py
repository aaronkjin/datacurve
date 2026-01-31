"""Tests for the Ingestion API using httpx AsyncClient + SQLite in-memory"""

from __future__ import annotations

# Must set DATABASE_URL before importing any project modules that trigger
# db/session.py module-level engine creation
import os
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import sqlite3
import uuid

# Register UUID adapter so SQLite can bind uuid.UUID objects as strings
sqlite3.register_adapter(uuid.UUID, str)

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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

# SQLite requires INTEGER (not BIGINT) for autoincrement primary keys
EventRow.id.property.columns[0].type = Integer()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, tmp_path):
    from api.main import app
    from db.session import get_session_dep
    from api.routes.blobs import get_blob_store
    from core.blob_store import LocalFsBlobStore

    async def override_session():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    blob_store = LocalFsBlobStore(root=tmp_path / "blobs")

    app.dependency_overrides[get_session_dep] = override_session
    app.dependency_overrides[get_blob_store] = lambda: blob_store

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trace_body() -> dict:
    return {
        "repo": {
            "repo_id": "test-repo",
            "commit_base": "abc123",
        },
        "task": {
            "bug_report": {
                "title": "Bug title",
                "description": "Bug description",
            },
        },
        "developer": {
            "developer_id": "dev-1",
        },
        "environment": {
            "ide": {"name": "vscode"},
        },
    }


def _event(seq: int, event_id: str | None = None) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "seq": seq,
        "ts_ms": 1700000000000 + seq,
        "type": "thought",
        "actor": {"kind": "human"},
        "payload": {
            "content_blob_id": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
            "kind": "hypothesis",
        },
    }


# ---------------------------------------------------------------------------
# Tests: POST /traces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_trace_returns_201(client: AsyncClient):
    resp = await client.post("/traces", json=_trace_body())
    assert resp.status_code == 201
    data = resp.json()
    assert "trace_id" in data
    assert data["status"] == "collecting"
    assert "created_at_ms" in data


@pytest.mark.asyncio
async def test_create_trace_missing_field_returns_400(client: AsyncClient):
    body = _trace_body()
    del body["repo"]
    resp = await client.post("/traces", json=body)
    assert resp.status_code == 400
    data = resp.json()
    assert "errors" in data


# ---------------------------------------------------------------------------
# Tests: POST /traces/{id}/events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_events_returns_202(client: AsyncClient):
    resp = await client.post("/traces", json=_trace_body())
    trace_id = resp.json()["trace_id"]

    events_body = {"events": [_event(1), _event(2)]}
    resp = await client.post(f"/traces/{trace_id}/events", json=events_body)
    assert resp.status_code == 202
    data = resp.json()
    assert data["accepted"] == 2
    assert data["seq_high"] == 2


@pytest.mark.asyncio
async def test_append_events_duplicate_event_id_returns_409(client: AsyncClient):
    resp = await client.post("/traces", json=_trace_body())
    trace_id = resp.json()["trace_id"]

    eid = str(uuid.uuid4())
    events_body = {"events": [_event(1, event_id=eid)]}
    resp = await client.post(f"/traces/{trace_id}/events", json=events_body)
    assert resp.status_code == 202

    # Send same event_id again
    events_body2 = {"events": [_event(2, event_id=eid)]}
    resp = await client.post(f"/traces/{trace_id}/events", json=events_body2)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_append_events_non_monotonic_seq_returns_400(client: AsyncClient):
    resp = await client.post("/traces", json=_trace_body())
    trace_id = resp.json()["trace_id"]

    events_body = {"events": [_event(1)]}
    await client.post(f"/traces/{trace_id}/events", json=events_body)

    # seq=1 again — not monotonically increasing
    events_body2 = {"events": [_event(1)]}
    resp = await client.post(f"/traces/{trace_id}/events", json=events_body2)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_append_events_nonexistent_trace_returns_404(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    events_body = {"events": [_event(1)]}
    resp = await client.post(f"/traces/{fake_id}/events", json=events_body)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /traces/{id}/finalize
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_returns_200(client: AsyncClient):
    resp = await client.post("/traces", json=_trace_body())
    trace_id = resp.json()["trace_id"]

    finalize_body = {"final_state": {"commit_head": "def456"}}
    resp = await client.post(f"/traces/{trace_id}/finalize", json=finalize_body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "finalizing"
    assert "qa_job_id" in data


@pytest.mark.asyncio
async def test_double_finalize_returns_409(client: AsyncClient):
    resp = await client.post("/traces", json=_trace_body())
    trace_id = resp.json()["trace_id"]

    finalize_body = {"final_state": {"commit_head": "def456"}}
    resp = await client.post(f"/traces/{trace_id}/finalize", json=finalize_body)
    assert resp.status_code == 200

    resp = await client.post(f"/traces/{trace_id}/finalize", json=finalize_body)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Tests: GET /traces/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_trace_returns_full_trace(client: AsyncClient):
    resp = await client.post("/traces", json=_trace_body())
    trace_id = resp.json()["trace_id"]

    events_body = {"events": [_event(1)]}
    await client.post(f"/traces/{trace_id}/events", json=events_body)

    resp = await client.get(f"/traces/{trace_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trace_id"] == trace_id
    assert len(data["events"]) == 1
    assert data["repo"]["repo_id"] == "test-repo"


@pytest.mark.asyncio
async def test_get_trace_404_for_missing(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/traces/{fake_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /blobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blob_upload_returns_201(client: AsyncClient):
    resp = await client.post(
        "/blobs",
        files={"file": ("test.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["blob_id"].startswith("sha256:")
    assert data["byte_length"] == 11


@pytest.mark.asyncio
async def test_blob_dedup_same_content(client: AsyncClient):
    content = b"duplicate content"
    resp1 = await client.post(
        "/blobs",
        files={"file": ("a.txt", content, "text/plain")},
    )
    resp2 = await client.post(
        "/blobs",
        files={"file": ("b.txt", content, "text/plain")},
    )
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["blob_id"] == resp2.json()["blob_id"]
