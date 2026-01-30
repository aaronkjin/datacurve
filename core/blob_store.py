"""Blob store interface and local filesystem implementation per docs/SPEC.md."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from core.config import settings


class BlobStore(Protocol):
    """Protocol matching docs/SPEC.md §3 Blob Store Interface."""

    def put_bytes(self, data: bytes, content_type: str) -> str:
        """Store bytes, return blob_id (sha256:hex)."""
        ...

    def get_bytes(self, blob_id: str) -> bytes:
        """Retrieve bytes by blob_id."""
        ...

    def get_uri(self, blob_id: str) -> str:
        """Return storage URI for a blob_id."""
        ...

    def exists(self, blob_id: str) -> bool:
        """Check if blob already stored (content-addressed dedup)."""
        ...


class LocalFsBlobStore:
    """Content-addressed local filesystem blob store.

    Layout: {root}/sha256/{first2chars}/{full_hash}
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else Path(settings.BLOB_STORE_PATH)

    def _hash_hex(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _blob_path(self, hex_hash: str) -> Path:
        return self._root / "sha256" / hex_hash[:2] / hex_hash

    def _parse_blob_id(self, blob_id: str) -> str:
        """Extract hex hash from blob_id (sha256:hex)."""
        prefix = "sha256:"
        if not blob_id.startswith(prefix):
            raise ValueError(f"Invalid blob_id format: {blob_id}")
        return blob_id[len(prefix):]

    def put_bytes(self, data: bytes, content_type: str) -> str:
        hex_hash = self._hash_hex(data)
        blob_id = f"sha256:{hex_hash}"
        path = self._blob_path(hex_hash)

        # Content-addressed dedup: skip write if file already exists
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        return blob_id

    def get_bytes(self, blob_id: str) -> bytes:
        hex_hash = self._parse_blob_id(blob_id)
        path = self._blob_path(hex_hash)
        if not path.exists():
            raise FileNotFoundError(f"Blob not found: {blob_id}")
        return path.read_bytes()

    def get_uri(self, blob_id: str) -> str:
        hex_hash = self._parse_blob_id(blob_id)
        path = self._blob_path(hex_hash)
        return f"file://{path}"

    def exists(self, blob_id: str) -> bool:
        hex_hash = self._parse_blob_id(blob_id)
        return self._blob_path(hex_hash).exists()
