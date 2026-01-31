"""Blob upload endpoint"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.blob_store import LocalFsBlobStore
from core.models import BlobUploadResponse
from db.models import BlobRow
from db.session import get_session_dep

router = APIRouter(prefix="/blobs", tags=["blobs"])

_blob_store: LocalFsBlobStore | None = None


def get_blob_store() -> LocalFsBlobStore:
    global _blob_store
    if _blob_store is None:
        _blob_store = LocalFsBlobStore()
    return _blob_store


@router.post("", status_code=201, response_model=BlobUploadResponse)
async def upload_blob(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session_dep),
    blob_store: LocalFsBlobStore = Depends(get_blob_store),
) -> BlobUploadResponse:
    data = await file.read()
    content_type = file.content_type or "application/octet-stream"

    blob_id = blob_store.put_bytes(data, content_type)
    storage_uri = blob_store.get_uri(blob_id)

    # Dedup: if BlobRow already exists, return existing
    result = await session.execute(
        select(BlobRow).where(BlobRow.blob_id == blob_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return BlobUploadResponse(
            blob_id=existing.blob_id,
            byte_length=existing.byte_length,
            storage_uri=existing.storage_uri,
        )

    row = BlobRow(
        blob_id=blob_id,
        content_type=content_type,
        byte_length=len(data),
        storage_uri=storage_uri,
        created_at_ms=int(time.time() * 1000),
    )
    session.add(row)
    await session.flush()

    return BlobUploadResponse(
        blob_id=blob_id,
        byte_length=len(data),
        storage_uri=storage_uri,
    )
