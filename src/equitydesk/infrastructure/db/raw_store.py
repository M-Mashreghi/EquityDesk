"""Filesystem content-addressed store for raw source payloads."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from equitydesk.application.ports import NotFoundError
from equitydesk.domain.provenance import ProvenanceError, compute_content_hash


class FilesystemRawStore:
    """Stores bytes at `<root>/<hash[:2]>/<hash>`; writes are atomic and idempotent."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, ref: str) -> Path:
        if len(ref) != 64 or any(c not in "0123456789abcdef" for c in ref):
            raise ValueError(f"invalid raw content reference {ref!r}")
        return self.root / ref[:2] / ref

    def put(self, content: bytes) -> str:
        ref = compute_content_hash(content)
        path = self._path(ref)
        if path.exists():
            return ref
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return ref

    def get(self, ref: str) -> bytes:
        path = self._path(ref)
        if not path.exists():
            raise NotFoundError(f"raw content {ref}")
        content = path.read_bytes()
        if compute_content_hash(content) != ref:
            raise ProvenanceError(f"raw content {ref} is corrupted")
        return content

    def exists(self, ref: str) -> bool:
        return self._path(ref).exists()
