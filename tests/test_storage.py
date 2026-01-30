"""Tests for storage layer: blob store + DB models"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import JSON, Integer, String, create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from core.blob_store import LocalFsBlobStore
from db.models import Base, BlobRow, EventRow, TraceRow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def blob_store(tmp_path):
    # Blob store rooted in a temp dir
    return LocalFsBlobStore(root=tmp_path)


@pytest.fixture()
def db_session():
    # Synchronous SQLite in-memory session for DB model tests
    for table in Base.metadata.tables.values():
        for col in table.columns:
            col_type = type(col.type)
            if col_type.__name__ == "JSONB":
                col.type = JSON()
            elif col_type.__name__ == "UUID":
                col.type = String(36)
            elif col_type.__name__ == "BigInteger" and col.primary_key:
                col.type = Integer()

    engine = create_engine("sqlite:///:memory:")

    # Enable FK enforcement in SQLite
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Blob store tests
# ---------------------------------------------------------------------------


class TestLocalFsBlobStore:
    def test_put_get_roundtrip(self, blob_store: LocalFsBlobStore):
        data = b"hello world"
        blob_id = blob_store.put_bytes(data, "text/plain")

        assert blob_id.startswith("sha256:")
        assert blob_store.get_bytes(blob_id) == data

    def test_dedup_same_content(self, blob_store: LocalFsBlobStore):
        data = b"duplicate content"
        id1 = blob_store.put_bytes(data, "text/plain")
        id2 = blob_store.put_bytes(data, "application/octet-stream")
        assert id1 == id2

    def test_different_content_different_ids(self, blob_store: LocalFsBlobStore):
        id1 = blob_store.put_bytes(b"aaa", "text/plain")
        id2 = blob_store.put_bytes(b"bbb", "text/plain")
        assert id1 != id2

    def test_get_uri(self, blob_store: LocalFsBlobStore):
        blob_id = blob_store.put_bytes(b"uri test", "text/plain")
        uri = blob_store.get_uri(blob_id)
        assert uri.startswith("file://")
        assert "sha256" in uri

    def test_exists_true(self, blob_store: LocalFsBlobStore):
        blob_id = blob_store.put_bytes(b"exists", "text/plain")
        assert blob_store.exists(blob_id) is True

    def test_exists_false(self, blob_store: LocalFsBlobStore):
        assert blob_store.exists("sha256:0000000000000000000000000000000000000000000000000000000000000000") is False

    def test_get_missing_raises(self, blob_store: LocalFsBlobStore):
        with pytest.raises(FileNotFoundError):
            blob_store.get_bytes("sha256:0000000000000000000000000000000000000000000000000000000000000000")

    def test_storage_layout(self, blob_store: LocalFsBlobStore, tmp_path):
        # Verify files land in {root}/sha256/{first2}/{fullhash}
        import hashlib

        data = b"layout check"
        hex_hash = hashlib.sha256(data).hexdigest()
        blob_store.put_bytes(data, "text/plain")

        expected = tmp_path / "sha256" / hex_hash[:2] / hex_hash
        assert expected.exists()


# ---------------------------------------------------------------------------
# DB model tests
# ---------------------------------------------------------------------------


class TestTraceRow:
    def test_create_trace(self, db_session: Session):
        trace_id = uuid.uuid4()
        row = TraceRow(
            trace_id=str(trace_id),
            status="collecting",
            repo_json={"repo_id": "r1", "commit_base": "abc123"},
            task_json={"bug_report": {"title": "t", "description": "d"}},
            developer_json={"developer_id": "dev1"},
            environment_json={"ide": {"name": "vscode"}},
            created_at_ms=1000000,
        )
        db_session.add(row)
        db_session.commit()

        fetched = db_session.get(TraceRow, str(trace_id))
        assert fetched is not None
        assert fetched.status == "collecting"
        assert fetched.repo_json["repo_id"] == "r1"
        assert fetched.finalized_at_ms is None


class TestEventRow:
    def _make_trace(self, db_session: Session) -> str:
        trace_id = str(uuid.uuid4())
        db_session.add(
            TraceRow(
                trace_id=trace_id,
                repo_json={"repo_id": "r1", "commit_base": "abc"},
                task_json={"bug_report": {"title": "t", "description": "d"}},
                developer_json={"developer_id": "d1"},
                environment_json={"ide": {"name": "vim"}},
                created_at_ms=100,
            )
        )
        db_session.commit()
        return trace_id

    def test_insert_event(self, db_session: Session):
        trace_id = self._make_trace(db_session)
        event_id = str(uuid.uuid4())
        row = EventRow(
            trace_id=trace_id,
            event_id=event_id,
            seq=1,
            ts_ms=200,
            type="file_edit",
            actor_json={"kind": "human"},
            payload_json={"file_path": "a.py"},
        )
        db_session.add(row)
        db_session.commit()

        result = db_session.query(EventRow).filter_by(event_id=event_id).one()
        assert result.seq == 1
        assert result.type == "file_edit"

    def test_duplicate_event_id_rejected(self, db_session: Session):
        trace_id = self._make_trace(db_session)
        event_id = str(uuid.uuid4())
        base = dict(
            trace_id=trace_id,
            event_id=event_id,
            seq=1,
            ts_ms=200,
            type="thought",
            actor_json={"kind": "human"},
            payload_json={},
        )
        db_session.add(EventRow(**base))
        db_session.commit()

        db_session.add(EventRow(**{**base, "seq": 2}))
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestBlobRow:
    def test_blob_roundtrip(self, db_session: Session):
        blob_id = "sha256:abcd1234"
        row = BlobRow(
            blob_id=blob_id,
            content_type="text/plain",
            byte_length=42,
            storage_uri="file:///data/blobs/sha256/ab/abcd1234",
            created_at_ms=300,
        )
        db_session.add(row)
        db_session.commit()

        fetched = db_session.get(BlobRow, blob_id)
        assert fetched is not None
        assert fetched.byte_length == 42
        assert fetched.redaction_json is None
