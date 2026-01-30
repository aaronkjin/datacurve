"""SQLAlchemy ORM models — matching docs/SPEC.md table definitions."""

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TraceRow(Base):
    __tablename__ = "traces"

    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trace_version: Mapped[str] = mapped_column(String(10), nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="collecting")
    repo_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    task_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    developer_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    environment_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ingestion_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    final_state_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    qa_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finalized_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    events: Mapped[list["EventRow"]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("trace_id", "event_id", name="uq_trace_event"),
        Index("ix_trace_seq", "trace_id", "seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("traces.trace_id"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    trace: Mapped["TraceRow"] = relationship(back_populates="events")


class BlobRow(Base):
    __tablename__ = "blobs"

    blob_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    redaction_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
